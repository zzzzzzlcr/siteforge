"""Task 5：浏览器 Agent 的工具循环（ReAct over MCP）。

    explore(url, goal, budget) -> Journey
    Journey{steps: [{state, action, target, result, note}], notes: [str]}

## 它为什么是「本项目第一次真的走依赖链」

Task 1 的 spike 证明了模型**肯**调工具（24 跑 0 编造、47 次真 observe），但它自己承认
一件事没验：`observe` 是**只读**的，模型有理由把**三次 observe 并进同一轮** ——
那测到的「连续 ≥3 轮」是**同一件事看了三遍**，不是「做一步 → 看结果 → 再决定」。

`click` / `form` 会**改页面**，天然不能批量：第二眼的页面只可能是「第一眼 + 那一下点击」
的结果。所以这条循环跑起来，模型看到的每一轮都是**上一轮动作造成的**新世界。
`tests/test_browser_agent.py` 里那条真浏览器的端到端钉的就是它。

## spike 量到的三条，在这里各是一个具体的东西（不是调参）

- **C1**（`max_tokens` ≥ 12000）：`MAX_TOKENS` 常量，**显式**传给 `run_tool_loop`。
  给少了最终答案是**空**的，而空答案看起来与「模型说没有」一模一样。
- **C2**（一轮里可能多个 `tool_calls`）：靠 `llm.run_tool_loop` 的逐条派发 + 我们自己的
  dispatch **每条都记账**。只处理 `tool_calls[0]` 的实现会静默丢掉观测 —— 这里丢掉的不只是
  一条结果，还有 Journey 里的**一步**（那一步发生过，账上却没有）。
- **C3**（终止认「没有 `tool_calls`」）：**那道门上的七个工具里没有 `done()`**，
  所以「不调工具」是唯一的正常收尾。`run_tool_loop` 认它，我们据此记 `model_done`。

## 人每一步都在（规格 §6.2/D16 —— 架构约束，不是「以后加 UI」）

`should_pause` 是**注入点**（计划三的 Console 接它）。它在**每一步之前**被问一次，
为真就**在下一步之前**退出：这里有两道闸 ——

1. 每个工具调用**之前**（dispatch 里）
2. 每一轮模型调用**之前**（包装客户端，`_Gate`）

两道都必要：模型可能一轮丢来五个 `click`，只在轮边界问一次 = 那一轮的五步全做完了才停。
停下来的信号是 `_Stop`，它**故意**继承 `BaseException`：`run_tool_loop` 对 dispatch 抛出的
`Exception` 是「记成一次工具失败、接着跑」—— 那对工具自己的错是对的，对「人喊停」是错的。

## Journey 与 `template.render()` 的接口（跨任务，Task 3 定）

`journey.states()` / `journey.fills()` 产出的就是 `render()` 要的 `states` / `fills`
（形状以 `agent/template.py` 的模块 docstring 与 `fixtures/reference_site.py` 为准）。
两件事因此被明确下来：

- **`Journey.steps` 是「agent 干了什么」**（含 observe / diff / screenshot —— 那些是感知动作）
- **`states()` 是「py 要重放什么」**：只留 click / form / scroll / goto / wait，
  且**只留做成了的**（没做成的动作不该在重放里再来一遍）
- `state` 归属 = 那个动作**发生在哪一页上**；换页（url 或正文变了）就换一个状态，
  状态的 `when` 就是**那一页**的页面判据（`url_contains` + `text_contains`）。
  ⚠️ `when` 生成时会**当场验一遍**（`when_holds`）—— 一条当时就不成立的 `when`
  在重放时会让整组步骤被静默跳过（`_applies` 就是这么写的），那是这类产物最贵的错。

⚠️ 有两处**已知有损**，不藏着：① `scroll` 工具是「把元素滚进视口」，而骨架的 scroll 是
像素滚动（没有「滚到某个元素」这一档），重放只能滚一屏 —— 每次都会在 `notes` 里说出来；
② 字段的 `source`（form-file 的键）与 `fallback` 是按**标签文字猜的**，猜不准时宁可给
保守的随机值，也不编一个假的值。
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import llm, tools

#: **C1**：`deepseek-v4-*` 把思考 token 算进 `max_tokens`。给 4000 时最终答案会**静默变空**
#: （spike 实测 1/8，提到 12000 后 5/5 正常）。别往下调 —— 那不是省钱，是把能力削掉。
MAX_TOKENS = 12000

#: 预算的目的是**防跑飞**，不是省钱（规格 §6.5 / P5）。到顶就停，且**明确说出来**。
DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_ROUNDS = 20

#: 骨架认的五个重放动作（其余动作出现在 STATES 里会被当成「产物写错了」）。
REPLAY_ACTIONS = ("click", "form", "scroll", "goto", "wait")

#: 有损映射：scroll 工具是「滚到某个元素」，骨架是「滚这么多像素」。
SCROLL_PIXELS = "400"

#: 一个状态的 `when` 里带多少字的页面文字（够认出「是不是这一页」，又不至于一改就失配）。
WHEN_SNIPPET_CHARS = 48

#: `result["page_text_head"]` 留多少字（给人核对「模型那一眼看到了什么」）。
PAGE_HEAD_CHARS = 200

#: 状态名/字段名的兜底（页面 slug 取不出来时）
FALLBACK_STATE_NAME = "page"
FALLBACK_FILL_NAME = "field"
#: 「还没看过页面」那个状态的名字。它会被**预占**，免得页面 slug 与它撞名。
START_STATE = "start"

_SYSTEM = """你是 siteforge 的探路 agent：在一个**真的浏览器**里把目标站点走一遍，\
把「怎么走」探清楚，后面要照它生成一条能重放的 py 脚本。

规矩（每一条都有理由）：
1. **先看再动**。页面长什么样只能从 observe 拿 —— 不许凭常识猜「这个站大概有个提交按钮」。
2. **改页面一律走工具**：click / form / scroll / goto。工具层没有 eval，手拼 JS 这条路不存在。
3. 做完一个动作，**用 observe（或 diff）确认它到底有没有推进**。click 返回 ok 只代表命令
   下发了，不代表页面动了 —— 这两件事在这套系统里是分开的。
4. 元素在折线（above_fold=false）之下或被挡着（occluded_by 非空）时，先 scroll 再动它。
   对看不见的元素动手不是「没点到」，是**点到了别的东西**。
5. 页面里 honeypots 列出的元素是**陷阱**（人看不见、bot 填了会被标记），别碰。
6. 走通了（或确定走不通）就**别再调工具**，用一段话说明：这条路怎么走、每一步为什么这么做、
   以及**成功时页面上会出现什么文字**。注意：**没有 done 这个工具** —— 你不调工具就是结束。"""


@dataclass
class Budget:
    """跑飞的上限。到顶就停，并如实记下「为什么停」。"""

    max_steps: int = DEFAULT_MAX_STEPS
    max_rounds: int = DEFAULT_MAX_ROUNDS


@dataclass
class Journey:
    """一次探索的账本。

    - `steps`  每步 `{state, action, target, result, note}`（**每个工具调用一步**）
    - `notes`  给人看的一句话串（D16）—— 含模型自己的话、报错、以及**为什么停下**

    计划里只定了这两个字段；下面几个是**加出来的**，都摆在明面上：
    `stop_reason` / `final_answer` / `pages`（状态清单，`states()` 的 `when` 就在里面）。
    """

    steps: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stop_reason: str = "running"
    final_answer: str = ""
    pages: list = field(default_factory=list)

    # ── 给 Task 7 的 draft 节点用：直接喂 template.render() ──

    def states(self) -> list:
        """`[{name, when, steps}]` —— 与 `template.render(states=...)` 同形。

        步骤按**状态**分组（同一个状态里的是连续发生的），没有可重放步骤的状态整组丢掉
        （骨架里空 steps 的状态等于没写，留着只会让人以为那儿本来有东西）。
        """
        order: list[str] = []
        groups: dict[str, list] = {}
        for step in self.steps:
            name = step.get("state") or START_STATE
            if name not in groups:
                groups[name] = []
                order.append(name)
            replay = _replay_step(step)
            if replay is not None:
                groups[name].append(replay)
        whens = {page.get("name"): page.get("when") for page in self.pages}
        return [
            {"name": name, "when": whens.get(name), "steps": groups[name]}
            for name in order if groups[name]
        ]

    def fills(self) -> dict:
        """`{name: {source, kind, label, target, fallback}}` —— 与 `render(fills=...)` 同形。

        只收**真填成功过**的字段：没填成的字段进了 FILLS，重放时就会去填一个
        agent 当时都没找着的东西。
        """
        out: dict = {}
        for step in self.steps:
            info = (step.get("result") or {}).get("fill")
            if not info:
                continue
            out[info["name"]] = {
                "name": info["name"],
                "source": info["source"],
                "kind": info["kind"],
                "label": info["label"],
                "target": step.get("target") or {},
                "fallback": list(info.get("fallback") or []),
            }
        return out


def when_holds(when, model: dict) -> bool:
    """一条 `when` 在**这一份页面模型**上成不成立。

    判据与产物里的 `_applies` **逐条对齐**（url 子串 + 正文里含任一段文字），
    因为「生成时的 `when`」与「重放时的 `when`」不是两件事：一条当时就不成立的 when，
    在重放时会让整组步骤**静默跳过**。
    """
    if not when:
        return True
    if when.get("url_contains") and when["url_contains"] not in (model.get("url") or ""):
        return False
    wants = when.get("text_contains") or []
    if wants:
        text = _norm(model.get("page_text") or "")
        return any(_norm(str(w)) in text for w in wants)
    return True


class _Stop(BaseException):
    """「停下」这个信号（人喊停 / 预算到顶）。

    ⚠️ **故意继承 `BaseException`**：`llm.run_tool_loop` 对 dispatch 抛出的 `Exception`
    是「记成一次工具失败，接着跑」—— 那对工具自己的错是对的（模型该看见它），
    对「人喊停」是错的（那会变成「喊了停还在跑」）。与 KeyboardInterrupt 同一个道理。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def explore(url: str, goal: str, budget: Budget | int | dict | None = None, *,
            should_pause: Callable | None = None,
            client=None, session: "tools.McpSession | None" = None,
            ws_url: str | None = None, host: str | None = None, port: int | None = None,
            binary: str | None = None,
            on_step: Callable[[dict], None] | None = None) -> Journey:
    """在真浏览器里为 `goal` 探 `url` 这条路，返回 `Journey`。

    参数：
      - `url` / `goal`：探哪一页、要摸清什么
      - `budget`：`Budget` / 步数（int）/ dict；不给就用默认上限（防跑飞）
      - `should_pause`：**人的注入点**。`should_pause(journey)` 或 `should_pause()`，
        真 → 在**下一步之前**退出。每一步之前都会被问一次（见模块 docstring 的两道闸）
      - `session`：MCP 会话（测试用桩；不给就按 `ws_url`/`host`/`port` 起一个真的）
      - `client`：LLM 客户端（测试注入；不给就用 `llm.client()`）
      - `on_step`：每步**发生的当下**回调一次（Console 的实时视图靠它）

    出错怎么办：
      - **工具自己报的错**（找不到元素 / 连不上窗口）→ 记进**那一步**，也回给模型，
        循环接着走（模型可能自己换一条路）
      - **门起不来 / 传输断了**（`McpError`）→ **抛**。不吞成一份空 Journey ——
        空的 Journey 与「探完了，什么都没发现」长得一模一样，而这两件事的下一步
        完全相反（一个要去重开窗口，一个要继续往下写 py）
    """
    plan = _as_budget(budget)
    paused = _as_predicate(should_pause)
    journey = Journey()
    pages = _Pages()
    own_session = session is None
    if session is None:
        session = tools.McpSession.open(ws_url=ws_url, host=host, port=port, binary=binary)
    taken = 0
    try:
        specs = tools.tool_specs(session)
        if not specs:
            raise RuntimeError("MCP 门上一个工具都没有 —— 工具循环没法开始")
        inner = client if client is not None else llm.client()
        gate = _Gate(inner, lambda: _stop_or_raise(paused, journey, taken, plan))

        def dispatch(name: str, args: dict) -> Any:
            nonlocal taken
            _stop_or_raise(paused, journey, taken, plan)   # ← 每一步之前（§6.2）
            taken += 1
            step, fill = _describe(name, args, pages, journey)
            t0 = time.time()
            try:
                raw = session.call_tool(name, args)
            except Exception as exc:                       # noqa: BLE001
                step["result"] = {"ok": False, "elapsed_ms": _ms(t0),
                                  "error": f"{type(exc).__name__}: {exc}"}
                step["note"] = _say(name, step["target"], False)
                journey.steps.append(step)
                _emit(on_step, step)
                raise        # 还给 run_tool_loop：模型也必须看见这条错（不吞）
            step["result"] = _summarize(name, args, raw, _ms(t0), fill)
            if name == "goto" and isinstance(raw, dict) and raw.get("url"):
                step["target"] = {"url": raw["url"]}    # 落到哪了（可能与给的不一样）
            step["note"] = _say(name, step["target"], True)
            journey.steps.append(step)
            if name == "observe":
                moved = pages.note_page(raw)
                if moved:
                    journey.notes.append(
                        f"页面变了：现在是「{_title_of(raw)}」（{raw.get('url') or '?'}）")
            if name == "scroll" and not any("滚进视口" in n for n in journey.notes):
                journey.notes.append(
                    f"第 {len(journey.steps)} 步是把「{_label_of(step['target'])}」滚进视口；"
                    "重放时只能滚一屏（骨架的 scroll 是像素滚动，没有「滚到某个元素」这一档）")
            _emit(on_step, step)
            return raw

        rounds = llm.run_tool_loop(
            _SYSTEM, _brief(url, goal, plan), specs, dispatch,
            max_rounds=plan.max_rounds, max_tokens=MAX_TOKENS, _client=gate,
        )
        _wrap_up(journey, rounds, plan)
    except _Stop as stop:
        journey.stop_reason = stop.reason
        journey.notes.append(_stop_note(stop.reason, len(journey.steps)))
    finally:
        journey.pages = [{"name": p["name"], "when": p["when"], "url": p["url"],
                          "title": p["title"]} for p in pages.pages]
        if own_session:
            session.close()
    return journey


# ─────────────────────── 停止条件（人 / 预算）───────────────────────


def _stop_reason(paused, journey, taken: int, plan: Budget) -> str | None:
    """该不该停下？返回理由或 None。**只看，不做**（做由调用方决定）。"""
    if paused is not None and paused(journey):
        return "paused"
    if taken >= plan.max_steps:
        return "budget_steps"
    return None


def _stop_or_raise(paused, journey, taken: int, plan: Budget) -> None:
    reason = _stop_reason(paused, journey, taken, plan)
    if reason:
        raise _Stop(reason)


def _stop_note(reason: str, steps: int) -> str:
    if reason == "paused":
        return (f"人喊停：在第 {steps + 1} 步之前停下来 —— 这一步**没有做**，页面保持原样"
                "（§6.2：人是每一步都在的旁路，不是最后一关）")
    if reason == "budget_steps":
        return f"预算到顶：走满 {steps} 步就停下（预算是**防跑飞**，不是省钱）"
    if reason == "budget_rounds":
        return (f"预算到顶：问满 {steps} 轮就停下 —— 模型一直在调工具、没有自己收尾"
                "（预算是防跑飞，不是省钱）")
    return f"停下（{reason}）"


def _wrap_up(journey: Journey, rounds: list, plan: Budget) -> None:
    """把每一轮里模型说的话记进 notes，并判定它是怎么结束的。"""
    for record in rounds:
        content = (record.get("content") or "").strip()
        if content:
            journey.notes.append(f"AI 说：{content}")
    last = rounds[-1] if rounds else None
    if last is None:
        journey.stop_reason = "no_rounds"
        journey.notes.append("一轮都没跑起来 —— 模型一次都没回话")
    elif not last.get("tool_calls"):
        # **C3**：没有 tool_calls = 它讲完了（那道门上没有 done()）
        journey.stop_reason = "model_done"
        journey.final_answer = (last.get("content") or "").strip()
    elif len(rounds) >= plan.max_rounds:
        journey.stop_reason = "budget_rounds"
        journey.notes.append(_stop_note("budget_rounds", len(rounds)))
    else:
        journey.stop_reason = "ended"
        journey.notes.append("循环停了，但最后一轮既没有 tool_calls 也没到预算上限")


def _as_budget(budget) -> Budget:
    if budget is None:
        return Budget()
    if isinstance(budget, Budget):
        return budget
    if isinstance(budget, int):
        return Budget(max_steps=int(budget))
    if isinstance(budget, dict):
        return Budget(**{k: v for k, v in budget.items() if k in ("max_steps", "max_rounds")})
    raise TypeError(f"不认识这种 budget: {budget!r}（给 Budget / 步数 int / dict）")


def _as_predicate(callback) -> Callable[[Journey], bool] | None:
    """把 `should_pause` 收成「收 journey 一个参数」的形状。

    两种写法都收：`should_pause(journey)`（下层能看到进度）与 `should_pause()`（上层只有一个
    flag）。按参数个数判断，**不**靠 try/except TypeError —— 那会把回调里真正的 TypeError
    也读成「它是不带参数的」。
    """
    if callback is None:
        return None
    try:
        params = [p for p in inspect.signature(callback).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        arity = len(params)
    except (TypeError, ValueError):        # 内建函数之类拿不到签名
        arity = 1
    if arity >= 1:
        return lambda journey: bool(callback(journey))
    return lambda journey: bool(callback())


class _Gate:
    """在两个工具轮之间也问一次「该停了吗」——否则模型一轮丢来五个动作时，
    停只能发生在那一轮**做完之后**。"""

    def __init__(self, inner, check: Callable[[], None]):
        self._inner = inner
        self._check = check
        self.chat = _Namespace(completions=_Namespace(create=self._create))

    def _create(self, **kwargs):
        self._check()
        return self._inner.chat.completions.create(**kwargs)


class _Namespace:
    """极简的「只有属性」的壳（用来把 create 挂成 chat.completions.create）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ─────────────────────── 每一步：描述 / 记账 / 人话 ───────────────────────


def _describe(name: str, args: dict, pages: "_Pages", journey: Journey) -> tuple:
    """开一个「步」的骨架：状态归属 + target（**声明式多元描述**）+ 这一步填什么。

    返回 `(step, fill)`。⚠️ fill 是**跟着 step 一起算出来的，但不塞进 step 里** ——
    step 的形状（`{state, action, target, result, note}`）是要给外面看的，
    多塞一个键就会在「报错那一步」上现形（报错也要能看出「本来想填哪个字段」）。
    """
    step = {"state": pages.current_name, "action": name, "target": None, "result": None, "note": ""}
    fill = None
    selector = str(args.get("selector") or "")
    if name == "goto":
        # goto 的 target 就是那个地址。**先记「要打开哪个」**，真打开了之后再盖成
        # 「落到哪了」（很多站会重定向）—— 失败时也还有话说（「打不开 <url>」）。
        step["target"] = {"url": str(args.get("url") or "")}
    if name in ("click", "scroll", "form"):
        element = _find_element(pages, "field" if name == "form" else "action", selector)
        step["target"] = _target_of(element, name, selector)
        if name == "form":
            fill = _fill_info(args, step["target"], element, journey)
    return step, fill


def _find_element(pages: "_Pages", kind: str, selector: str):
    """在上一次观测的模型里找这个选择器对应的元素（找不到就 None）。

    ⚠️ 找的是**上一次观测**——「它当时看到的是哪个元素」这件事只有那份模型说得清。
    找不到不是错：target 退回「只有选择器」的形态，重放的声明式回退链照样能跑。
    """
    model = pages.current_model
    if not model or not selector:
        return None
    pool = (model.get("fields") if kind == "field" else model.get("actions")) or []
    for element in pool:
        if not isinstance(element, dict):
            continue
        if element.get("selector") == selector or selector in (element.get("alternates") or []):
            return element
    return None


def _selectors_of(element, selector: str) -> list:
    out = []
    if element:
        for candidate in [element.get("selector")] + list(element.get("alternates") or []):
            if candidate and candidate not in out:
                out.append(candidate)
    if selector and selector not in out:
        out.append(selector)
    return out


def _target_of(element, action: str, selector: str) -> dict:
    """声明式多元 target（§5.1b）：**不写死单个选择器**，文字 + 角色 + 语境优先。"""
    if action == "form":
        label = ""
        if element:
            label = element.get("label") or element.get("hint") or element.get("placeholder") or ""
        return {"text": None, "label": label or None, "role": None, "near": None,
                "selectors": _selectors_of(element, selector), "above_fold_only": False}
    return {
        "text": (element or {}).get("text") or None,
        "role": (element or {}).get("role") or None,
        "near": (element or {}).get("region") or None,
        "selectors": _selectors_of(element, selector),
        "above_fold_only": False,
    }


def _fill_info(args: dict, target: dict, element, journey: Journey) -> dict:
    """这一步填什么（`args` 是模型真给的），以及重放时值从哪来。"""
    if args.get("check") is not None:
        kind, value = "check", ("true" if args.get("check") else "false")
    elif args.get("select") is not None:
        kind, value = "select", str(args.get("select"))
    else:
        kind, value = "value", str(args.get("value") or "")
    label = target.get("label") or ""
    name = _fill_name(label, element, journey)
    return {"name": name, "source": name, "kind": kind,
            "label": label or name, "value": value,
            "fallback": _fallback(kind, value, label, element)}


def _fill_name(label: str, element, journey: Journey) -> str:
    base = label or (element or {}).get("placeholder") or (element or {}).get("type") or FALLBACK_FILL_NAME
    stem = _snake(base) or FALLBACK_FILL_NAME
    used = {(s.get("result") or {}).get("fill", {}).get("name")
            for s in journey.steps if (s.get("result") or {}).get("fill")}
    if stem not in used:
        return stem
    index = 2
    while f"{stem}-{index}" in used:
        index += 1
    return f"{stem}-{index}"


#: `random` 池子认的几类值（与产物里 `_random_value` 同一套 —— 产物认不出的类型会抛）。
_RANDOM_HINTS = (
    (("password",), "password"),
    (("email", "e-mail"), "email"),
    (("phone", "tel", "mobile"), "phone"),
    (("zip", "postcode", "postal"), "postcode"),
    (("birth", "dob"), "dob"),
    (("first name", "firstname", "given name"), "first_name"),
    (("last name", "lastname", "surname", "family name"), "last_name"),
)


def _fallback(kind: str, value: str, label: str, element) -> list:
    """重放时这个字段填什么：先读 form-file 的键（`source`），没有就用这里。

    ⚠️ 标签猜不出语义时给 `full_name` —— 一个保守的默认值，**不编**具体内容
    （真值要么来自运营的 form-file，要么来自随机池；产物那边的随机化本身是拟人需要）。
    """
    if kind == "check":
        return [value or "true"]
    if kind == "select":
        return [value] if value else []
    blob = " ".join(str(x or "") for x in (
        label, (element or {}).get("label"), (element or {}).get("hint"),
        (element or {}).get("placeholder"), (element or {}).get("type"),
    )).lower()
    for words, random_kind in _RANDOM_HINTS:
        if any(w in blob for w in words):
            return [{"random": random_kind}]
    if (element or {}).get("type") == "password":
        return [{"random": "password"}]
    return [{"random": "full_name"}]


def _summarize(name: str, args: dict, raw: Any, elapsed_ms: int, fill: dict | None) -> dict:
    """这一步的**结果**（进 Journey 的那份）。

    这是一份**摘要**，不是原始返回 —— 原始返回里可能有几百 KB 的 base64（截图），
    整份抄进账本只会让内存和落盘都变得没法读。模型那边拿到的是**原样的**结果。
    """
    out: dict = {"ok": True, "elapsed_ms": elapsed_ms}
    if name == "observe" and isinstance(raw, dict):
        out["url"] = raw.get("url") or ""
        out["title"] = raw.get("title") or ""
        out["page_text_head"] = _norm(raw.get("page_text") or "")[:PAGE_HEAD_CHARS]
        out["actions"] = len(raw.get("actions") or [])
        out["fields"] = len(raw.get("fields") or [])
        out["option_groups"] = len(raw.get("option_groups") or [])
        out["honeypots"] = len(raw.get("honeypots") or [])
        out["diagnostics"] = [d.get("kind") for d in (raw.get("diagnostics") or [])
                              if isinstance(d, dict)]
    elif name == "goto":
        out["url"] = (raw or {}).get("url") if isinstance(raw, dict) else None
    elif name == "diff":
        out["actionable"] = (raw or {}).get("actionable") if isinstance(raw, dict) else None
    elif name == "screenshot":
        out["bytes"] = len(str(raw)) if raw is not None else 0
    elif isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k != "note"})
    if name in ("click", "form", "scroll") and args.get("selector"):
        out.setdefault("selector", args.get("selector"))
    if fill:
        out["fill"] = fill
    return out


def _say(action: str, target, ok: bool) -> str:
    """这一步在**人话**里叫什么（D16：使用者是非技术人员 —— 不是错误码、不是选择器）。"""
    label = _label_of(target)
    if action == "observe":
        return "看了一眼页面"
    if action == "diff":
        return "比了一下：这一步之后页面有没有变"
    if action == "screenshot":
        return "截了一张图"
    if action == "click":
        return f"点了「{label}」" if ok else f"页面上没找到「{label}」，这一步没做成"
    if action == "form":
        return f"填好了「{label}」" if ok else f"没找到「{label}」这个输入框，没填成"
    if action == "scroll":
        return f"把「{label}」滚进了视口" if ok else f"滚不动「{label}」"
    if action == "goto":
        url = (target or {}).get("url") or ""
        return f"打开了 {url}" if ok else f"打不开 {url}"
    return f"做了一下「{action}」" if ok else f"「{action}」没做成"


def _label_of(target) -> str:
    target = target or {}
    for key in ("text", "label", "name"):
        if target.get(key):
            return str(target[key])
    selectors = target.get("selectors") or []
    if selectors:
        # 没有文字名字时把选择器带上 —— 不是给人读的，是排查时对得回页面
        return f"没写名字的元素（{selectors[0]}）"
    return "没写名字的元素"


def _replay_step(step: dict):
    """一个 Journey 步骤 → 产物里的一步（不可重放的返回 None）。"""
    action = step.get("action")
    if action not in REPLAY_ACTIONS:
        return None
    if not (step.get("result") or {}).get("ok"):
        return None                      # 没做成的动作不该在重放里再来一遍
    target = step.get("target") or {}
    note = step.get("note") or ""
    if action == "click":
        return {"action": "click", "note": note, "target": target}
    if action == "form":
        fill = (step.get("result") or {}).get("fill") or {}
        if not fill.get("name"):
            return None                  # 连字段名都不知道，重放时填不了
        return {"action": "form", "fill": fill["name"], "note": note, "target": target}
    if action == "scroll":
        return {"action": "scroll", "pixels": SCROLL_PIXELS, "note": note, "target": target}
    if action == "goto":
        url = (step.get("target") or {}).get("url") or (step.get("result") or {}).get("url") or ""
        if not url:
            return None
        return {"action": "goto", "url": url, "note": note}
    return {"action": action, "note": note}


# ─────────────────────── 页面状态（换页 = 换状态）───────────────────────


class _Pages:
    """把观察到的页面归成状态。

    ⚠️ 判据不只是 URL：**正文变了也算换页**。SPA 与问卷站（blinkist 那类）每步都在同一
    个 URL 上换内容 —— 只认 URL 的话，一个状态的 `when` 会盖住好几个页面，
    而 `when` 是在**进这个状态时**判的（`_applies`），判错了整组步骤被静默跳过。
    """

    def __init__(self):
        self.pages: list = []
        self._used = {START_STATE}
        self._current: dict | None = None

    @property
    def current_name(self) -> str:
        return self._current["name"] if self._current else START_STATE

    @property
    def current_model(self):
        return self._current["model"] if self._current else None

    def note_page(self, model: dict) -> str | None:
        """记一页。返回**上一个状态名**（说明换页了），第一页返回 None。"""
        if not isinstance(model, dict):
            return None
        key = ((model.get("url") or "").split("#")[0],
               _norm(model.get("title") or ""),
               _norm(model.get("page_text") or "")[:400])
        if self._current is not None and self._current["key"] == key:
            return None
        previous = self._current["name"] if self._current else None
        entry = {
            "name": self._unique(_slug(model)),
            "when": _when_for(model),
            "key": key,
            "model": model,
            "url": model.get("url") or "",
            "title": model.get("title") or "",
        }
        self.pages.append(entry)
        self._current = entry
        return previous

    def _unique(self, stem: str) -> str:
        name = stem
        index = 2
        while name in self._used:
            name = f"{stem}-{index}"
            index += 1
        self._used.add(name)
        return name


def _when_for(model: dict) -> dict | None:
    """一个状态的 `when`：**进这个状态时**那一页的判据。

    `url_contains` 用**去掉 query 与 fragment** 的地址：重放时 query 常常不一样
    （utm、step 号、A/B 参数），而 `when` 判太严的后果是**整组步骤被跳过**。
    正文那一段是从这一页的 `page_text` 里**取的原文** —— 它是当时那一页的**子串**，
    所以必然成立（`when_holds` 在生成时就会验一遍）。
    """
    when: dict = {}
    url = (model.get("url") or "").split("#")[0].split("?")[0]
    if url:
        when["url_contains"] = url
    text = _norm(model.get("page_text") or "")
    snippet = _snippet(text)
    if snippet:
        when["text_contains"] = [snippet]
    if not when:
        return None
    return when if when_holds(when, model) else ({"url_contains": url} if url else None)


def _snippet(text: str) -> str:
    head = text[:WHEN_SNIPPET_CHARS]
    if len(text) > WHEN_SNIPPET_CHARS and " " in head:
        head = head.rsplit(" ", 1)[0]           # 别把一个词从中间切断（读起来是半截话）
    return head.strip()


def _slug(model: dict) -> str:
    url = (model.get("url") or "").split("#")[0].split("?")[0].rstrip("/")
    tail = url.rsplit("/", 1)[-1]
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    slug = _snake(stem) or _snake(model.get("title") or "")
    return slug or FALLBACK_STATE_NAME


# ─────────────────────── 小工具 ───────────────────────


def _norm(text: str) -> str:
    """与 `cdp observe` 的 page_text / 产物的 `page_signature()` **同一口径**。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _snake(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(text or "").lower()).strip("_")


def _title_of(model: dict) -> str:
    return (model or {}).get("title") or (model or {}).get("url") or "没标题的页面"


def _brief(url: str, goal: str, plan: Budget) -> str:
    return (f"目标站点：{url}\n要做的事：{goal}\n"
            f"（你最多走 {plan.max_steps} 步、{plan.max_rounds} 轮。"
            f"现在这个浏览器窗口可能停在别的页上，先确认自己在哪。\n"
            f"⚠️ **能回答了就直接停下来说**（不调工具就是结束）—— 一直调工具会把预算耗光，"
            f"那一次你的结论一个字都留不下来。）")


def _emit(on_step, step: dict) -> None:
    if on_step is not None:
        on_step(step)


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)
