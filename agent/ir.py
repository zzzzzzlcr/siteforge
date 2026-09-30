"""**IR 层**：把「一次成功的探路」变成一份**能编译的东西**，并在编译之前**校验它完整**。

为什么要单独有这一层（用户 2026-09-30 的诊断，原话）：

> 探路能走通，但「把一次成功探路压缩成可重复运行的 py」这一步不稳定 …… 核心瓶颈已经不在
> CDP，也不在 Agent 会不会操作页面，而是在 **Explore → Py 编译层** …… 我建议你立刻把链路
> 拆成 3 个明确阶段：Explore（只负责走通）→ IR / Trace Normalize（变成稳定中间表示）→
> Py Emit（只负责从 IR 生成 py）…… 只要你把「生成不出来」都塞进这 5 类，问题会瞬间清晰很多。

这一层在仓库里**原本是隐形的**：`Journey.states()` / `Journey.fills()` 算出来的东西**直接**
交给 `graph._draft` 去渲（`template.render`）。于是失败全叫「生成脚本失败」——而它们其实分别
卡在**不同的层**：

| 层 | 判据（这一层能不能过） | 谁算的 |
|---|---|---|
| `explore` | 这一趟**走到成功**了吗（`reached_success`）—— 没走到就没有 IR 可谈 | `graph._explore_reached_success` |
| `ir` | 账本→IR 这一步能不能**表达**（每组有判据、每组有可重放的步、没有「只有地址」的空门、`goto` 不许落在组中间） | **本模块** |
| `emit` | IR 完整的前提下渲得出来吗（契约检查 / 形态 / 成功判据在不在） | `template.render` + `lint` |
| `selftest` | 产物在真站上按扰动序列跑得过吗（**跑不起来 ≠ 跑不过**，两种分开报） | `selftest.run` |

⚠️ **本模块只做校验，不动 IR 本身** —— 「发现问题」与「修 IR」是两件事：这一段只回答
「这份 IR 能不能编译」，修不修、怎么修由上游（`browser_agent` 那几处）负责。
⚠️ **判下来是「不完整」时要说清是哪一层的哪一条**（`layer` + `code`），不要把机器码丢给运营：
`say` 那一格才是上屏的（人话），`code` 只用来归类与统计。

## 五类归因（用户点名要的那五类）

`code` 的前缀就是那五类之一（写在这儿，免得日后各处各叫）：

- `trace_incomplete` —— ① 轨迹不完整（某一步没记下来 / 没走到成功）
- `trace_ambiguous` —— ② 轨迹有歧义（同一动作多个候选 / 成功原因不确定 / 靠临时推理过的）
- `ir_unexpressible` —— ③ IR 无法表达（循环 / 随机题 / 动态分支 / 空的判据）
- `emit_bug` —— ④ Emitter Bug（IR 完整、稿出不来 / 语法 / 参数 / 模板）
- `selftest_failed` —— ⑤ py 能生成、重放失败（**含「自测没跑成」这一档，单独一个 code**）

（五类里前四类归本模块与 `emit` 那两处；`selftest` 那两档在 `selftest.py` / `graph` 那侧报。）
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 允许出现在产物里的动作（与骨架的契约同一张表 —— `template.render` 只认这五种）。
REPLAYABLE_ACTIONS = ("click", "form", "scroll", "goto", "wait")


@dataclass
class Problem:
    """IR 不完整的一条：**哪一层、哪一类、说给人听的那句话**。"""

    layer: str          # explore / ir / emit / selftest
    code: str           # 上面那五类之一（+ 更细的后缀）
    say: str            # 人话：哪一组、为什么、人能做什么
    name: str = ""      # 哪一组（有的话）
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"layer": self.layer, "code": self.code, "say": self.say,
                "name": self.name, **({"extra": self.extra} if self.extra else {})}


def _stable(url) -> str:
    """判「是不是同一个地址」用的**稳定前缀**：去掉 fragment / query、去掉尾斜杠、小写。

    与 `browser_agent._stable_url` 的前两步同一口径（那一步还丢「像轮换码的末段」——
    这里不必：它只用来数「同一个地址上有几个状态」）。
    """
    return str(url or "").split("#")[0].split("?")[0].rstrip("/").lower()


def validate(journey, values: dict | None = None) -> list:
    """这份 IR 能不能编译 ⇒ `[Problem]`（空列表 = 可以）。

    ⚠️ **只在 IR 这一层判**：这里不看探路成没成（那是 `explore` 层，`graph` 有它自己的判据），
    也不看产物渲得出来渲不出来（那是 `emit` 层）。越界判会让「卡在第几层」这件事又糊掉。
    """
    values = dict(values or {})
    out: list = []
    if journey is None:
        return [Problem("ir", "ir_unexpressible.no_ledger",
                        "这一趟没有账本（探路那一层就没留下东西）—— 没有账本就没有「怎么走」。")]

    try:
        states = journey.states()
    except Exception as exc:                      # noqa: BLE001 —— 读不出来也要**归类**，不许炸给上层
        return [Problem("ir", "ir_unexpressible.states_raised",
                        "账本读不成 IR（`states()` 自己抛了：%s：%s）—— 这是 IR 这一层的代码问题，"
                        "不是站点的问题。" % (type(exc).__name__, exc))]

    if not states:
        out.append(Problem("ir", "trace_incomplete.no_steps",
                           "账本里**一步可重放的都没有**（全是「看一眼」这类感知动作）—— "
                           "没有可重放步骤就没有产物可谈。"))

    #: 每个**稳定地址**上有几个状态 —— 下面那条 `bare_gate` 要看它（★ 2026-09-30 收窄）。
    _urls: dict = {}
    for _st in states:
        _u = _stable((_st.get("when") or {}).get("url_contains"))
        if _u:
            _urls[_u] = _urls.get(_u, 0) + 1

    for st in states:
        name = str(st.get("name") or "")
        when = st.get("when") or {}
        steps = list(st.get("steps") or [])
        has_text = bool(when.get("text_contains"))
        has_url = bool(when.get("url_contains"))

        # ① 一组里一步都没有 —— `states()` 已经滤掉了空组，这里只是兜底（读账的人要能看见）。
        if not steps:
            out.append(Problem("ir", "trace_incomplete.empty_group",
                               "「%s」这一组一步都没有。" % name, name))
            continue

        # ② **只有地址**的门 —— ★ 2026-09-30 **收窄**（`zekear` 现场逮到的第二个误判）：
        #    **只在同一个地址上还有别的组时**才算病（那时它分不出是哪一屏 ✗）；
        #    而这个地址上**独一份**时，地址前缀本来就够用（`/user/signIn` 就是它自己那一页）
        #    ⇒ 报它是误判，而它会拦住一份本来对的产物。
        #    ⚠️ 根子多半还是「记下这一组的那一眼没读到正文」（页面还在加载）。
        if when and not has_text and _urls.get(_stable(when.get("url_contains")), 0) > 1:
            out.append(Problem("ir", "ir_unexpressible.bare_gate",
                               "「%s」这一组**只有地址**当门（`%s`），而这个地址上还有别的组 —— "
                               "地址前缀在**每一屏**上都成立，等于没有门、分不出是哪一屏。"
                               "多半是记下这一组的那一眼**没读到正文**（页面还在加载）。"
                               % (name, when.get("url_contains") or ""), name))
        if when and not has_url and not has_text:
            out.append(Problem("ir", "ir_unexpressible.no_criterion",
                               "「%s」这一组没有任何判据（地址与正文都没有）。" % name, name))

        # ③ 动作必须都在允许的几种里（骨架只认那五种；别的到了产物里会被当成「写错了」）。
        for i, step in enumerate(steps, 1):
            act = str(step.get("action") or "")
            if act not in REPLAYABLE_ACTIONS:
                out.append(Problem("ir", "ir_unexpressible.bad_action",
                                   "「%s」第 %d 步的动作是 `%s` —— 产物只认 %s 这几种。"
                                   % (name, i, act, "、".join(REPLAYABLE_ACTIONS)), name,
                                   {"step": i, "action": act}))
            # ④ `goto` **夹在中间**（前面有步、后面也有步）**而且真的换了页**：
            #    前面那几步是按这一组那一页记的，后面那几步按**新页**记的 ⇒ 重放时一页上做一半、
            #    另一半在别的页上做（2026-09-30 现场：`zekear` 那趟自测「页面上没找到「Buy now」」
            #    就是这个形状）。
            #    ⚠️ ★ 2026-09-30 **两次收窄**（都是现场逮到的误判，两处都很常见）：
            #      · **同址的 `goto` 是重载**（真站实测：`goto https://…/user/signUp` 之后再填表
            #        —— 页面没换）⇒ 放行；
            #      · **`goto` 在组的第一步**是合法形状（「从这一页出发，去那一页做事」：
            #        `center-2` 的 `[goto 商品页, 点 Buy now]` 就是这样，且它是对的）⇒ 放行。
            #    只剩**真的夹在中间**（前面有重放步）才报。
            if act == "goto":
                after = [s for s in steps[i:] if str(s.get("action") or "") != "scroll"]
                before = [s for s in steps[:i - 1]
                          if str(s.get("action") or "") not in ("scroll", "wait")]
                where = str(step.get("url") or "")
                own = str(when.get("url_contains") or "")
                if after and before and not (own and where.startswith(own)):
                    out.append(Problem("ir", "trace_ambiguous.goto_in_the_middle",
                                       "「%s」第 %d 步是 `goto`（去「%s」），而它**前后都有别的动作** —— "
                                       "重放时前面的步在这一页上做、后面的步在新页上做，"
                                       "而日志看不出区别。这一组该在 `goto` 那儿断开。"
                                       % (name, i, where[:60]), name,
                                       {"step": i, "goto": where}))
    return out


def referenced_fills(states: list) -> set:
    """产物里**被步骤引用到的**那些 fill 名（`form` 步的 `fill` 那一格）。"""
    out = set()
    for st in states or ():
        for step in (st.get("steps") or ()):
            name = str((step or {}).get("fill") or "").strip()
            if name:
                out.add(name)
    return out


def missing_fills(states: list, fills: dict) -> set:
    """被步骤引用、却**不在 `FILLS` 里**的那些名字 —— 产物自我矛盾的正身。

    ★ 2026-09-30（用户问「FILLS 没有不是有兜底的随机码」时当场量到的）：现场那一趟

        账本 `journey.fills()`      : email / password / field      ← 三格都在
        交出去那一版 `FILLS`        : field                          ← 少了两格
        而步骤里还**引用**着        : email / password / field
        ⇒ 跑起来报「这一步要填「email」，FILLS 里没有它」并停

    ⚠️ **兜底管不了这一格**：别名 → 拆名 → 随机池那条链，覆盖的是「**资料里**没有那一列」；
    而这里缺的是「**产物自己的声明表**里没有这一格」—— 那种时候产物连「填什么、什么类型」
    都没有，只能报「产物写错了」（拿一个凭空的随机值去填，正是「自信地错」）。
    能补就补（账本里有 ⇒ `graph` 那一侧补回去），补不了才拦。
    """
    return set(referenced_fills(states)) - set((fills or {}).keys())


def layer_of(problems: list) -> str:
    """这一批问题的**最靠前的那一层**（给「卡在第几层」那一格用）。空列表 ⇒ 空串。"""
    order = ("explore", "ir", "emit", "selftest")
    got = {p.layer for p in problems}
    return next((one for one in order if one in got), "")


def say(problems: list) -> str:
    """一段人话（上屏用）：先说是哪一层卡住，再逐条摆。**机器码一个都不上屏**。"""
    if not problems:
        return ""
    where = {"explore": "探路那一层", "ir": "把账本变成「可编译的步骤表」那一层",
             "emit": "按步骤表生成 py 那一层", "selftest": "在真站上跑自测那一层"}
    lines = ["先别写 py：**卡在「%s」** —— 这一趟的账本还编译不出来。"
             % where.get(layer_of(problems), "IR")]
    for p in problems:
        lines.append("  · %s" % p.say)
    return "\n".join(lines)
