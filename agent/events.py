"""Task 4：时间线的**事件词汇表** —— 也就是 `docs/执行事实契约-2026-09-18.md` 的落地处。

```
一条事件：{"n", "at", "kind", "who", "say", "data"}
```

## 一、为什么这个文件就是那份契约

契约说：**「这一步到底发生了什么」，而不是「我认为它成了」**；病根不是字段不够，
是**判断和执行是同一方**（`datewhirl.py:175` 把「什么都没看见」判成「做完了」）。
它把「发生了什么」拆成七格，并且**规定了每一格由谁填**：

| 格 | 谁填 | 在这个形状里落在哪 |
|---|---|---|
| `step_no` | 脚本 | `data["step_no"]` |
| `action` | 脚本 | `data["action"]`（送出了什么 + 目标） |
| `receipt` | **cdp 二进制** | `data["receipt"]`（回执**原文**，脚本只转抄） |
| `sig_before` | 脚本 | `data["sig_before"]`（url + 正文指纹 + 可见元素计数） |
| `sig_after` | 脚本 | `data["sig_after"]`（同上，动作之后） |
| `expect` | **运营** | `data["expect"]`（从菜单里选、填人话；或 `UNDECLARED`） |
| `verdict` | **收到上面那些的那一方** | `data["verdict"]`（**永远不是脚本的**） |

**这一版一个都不填**（契约 §五：只把七格如实记下来；算的那一方是下一步）。
这个文件管的是**形状**：七格落得下、落得对、落不下的地方**响**（抛），不静默。

⚠️ **不是「先拼个时间线、字段随手定」**（契约 §三）。反过来做会得到
「给旧的 `step` 字段换个名字」—— 三层（Action / State / Business Truth）就白分了。

## 二、三条硬规矩（都在下面机械挡掉了）

1. **字段名里不许出现判断词**（契约 §二③）。不许有 `success` / `changed` / `done`
   这种格子。今天那个 `step="success"` 就是这么来的：**脚本自起的名字，后台照着当真话读**。
   挡的是 `data` 的**键**（不是 `kind` 的值 —— 事件词汇表由服务那一侧定，
   `done` / `failed` 说的是「图走到哪了」，不是「成没成」）。
2. **「看不见」是一等值**（契约 §二②）：`sig_after = None` **加一句 `why`**，
   **不许**被折算成「没变化」。所以 `None` 一路原样留着（连 `json.dumps` 都不许把它吃掉），
   而写了 `None` 却不说是为什么 —— **抛**。
3. **谁填哪一格**（契约 §二①）：脚本只填前五格。时间线上脚本的声音就是 `who="agent"`，
   它的 `data` 里**不许**出现 `verdict`（判断）与 `expect`（那是运营写的）——
   脚本替运营声明期望，正是「执行的那一方在当裁判」换个地方又长出来。

## 三、为什么错误一律「抛」而不是「忽略」

`say` 空着、`who` 是第四个值、`data` 里塞了字节 —— 这些都不是「数据不干净」，
是**还没想清**。静默修正会把「没人说得清这一步发生了什么」留在时间线上，
而那正是这一片要治的病（Global Constraints：没有静默的路径）。
抛出来的每一条消息都说清**该怎么办**（用哪一格、为什么）。
"""

from __future__ import annotations

import copy
import datetime
import json
import re
import threading

__all__ = ["WHO", "MAX_EVENTS", "CELLS", "UNDECLARED", "JUDGMENT_WORDS", "Timeline"]

#: 时间线上说话的**三方**（页面靠它决定气泡长相：它就是「聊天」那一半）。
#: `agent` = 它自己（探路的步、模型的话）；`system` = 服务/系统；`you` = 人。
WHO = ("agent", "system", "you")

#: 事件上限：超了**丢最旧的**（内存里的东西，随 job 一起活在进程里）。
#: 一次探路 30 步 → 几百条短字符串（设计注 §3.3 算过这笔账）——
#: 2000 条够装下「一轮 + 它前面那一轮」，再多也不是人看得过来的量。
MAX_EVENTS = 2000

#: 契约那七格（顺序照契约 §二那张表）—— **名字由这个模块定下来**，
#: 后一个任务照这个名字填，别在别处再起一套。
CELLS = ("step_no", "action", "receipt", "sig_before", "sig_after", "expect", "verdict")

#: `expect` 的**一等值**：运营在这一格说「这一步我说不好」（契约 §四）。
#: 它是契约的**诚实阀门** —— 明说不知道，而不是拿 Action Truth 冒充。
#: ⚠️ 它是**一个值**，不是「没写」：没写与「说了不知道」在纸上必须分得开（所以 `None` 不行）。
UNDECLARED = "未声明"

#: 判断词（契约 §二③点名三个：`success` / `changed` / `done`）。
#: 后两个（`ok` / `passed`）是同一个病在本仓库里**已经存在的两个正身**：
#: `journey.steps[].result.ok` 与 `selftest.Report.passed` —— 都是脚本自己给自己下的判。
JUDGMENT_WORDS = ("success", "changed", "done", "ok", "passed")

#: 脚本（`who="agent"`）**不许填**的两格（契约 §二①：脚本只填前五格）。
SCRIPT_MAY_NOT_FILL = ("verdict", "expect")

#: 「有没有变化」那两格。写 `None` = **看不见**，必须配一句 `why`（规矩 2）。
SIG_CELLS = ("sig_before", "sig_after")

_WORDS = re.compile(r"[^0-9A-Za-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _now() -> str:
    """本地 ISO **秒级**时间戳（与 `Job.created_at` 同一个写法）。

    秒级是**有意的**：同一秒里可能来两三件事 —— 所以排序一律用 `n`，不用时间戳。
    """
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _words(key: str) -> list:
    """一个键拆成词：`success_text` → `["success", "text"]`；`resultOk` → `["result", "ok"]`。

    为什么要拆词而不是直接找子串（契约 §二③的判据要**准**）：`tokens` 里就有 `ok`、
    `broken` 里也有 —— 子串匹配会把好好的键误杀，而误杀一次，人就再也不信这条规矩了。
    """
    out: list = []
    for chunk in _WORDS.split(str(key)):
        if not chunk:
            continue
        out.extend(part.lower() for part in _CAMEL.split(chunk) if part)
    return out


def _require_text(value, name: str) -> str:
    """非空字符串（去掉空白之后还得有东西）—— 写不出人话就说明还没想清。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "%s 得是一句**非空**的字符串（拿到的是 %r）。"
            "写不出人话就说明还没想清 —— 这是时间线的第一条纪律："
            "每一句都要是人话（不是错误码、不是选择器、不是 JSON）。" % (name, value))
    return value


def _facts(data) -> dict:
    """`data` → 一个**装得下**、**没有判断词**、**没被折算过**的事实字典。

    五道判据，每一道都对应契约里的一条（顺序即报错的优先级）：
      ① 它得是个 mapping（七格是**有名字的格子**）；
      ② JSON 装得下（**字节不进 JSON**：图走文件名，字节在盘上）；
      ③ 键里不许出现判断词；④ `expect` 不许留空；⑤ 写了 `None` 的签名格要带 `why`。
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            "data 得是一个字典（七格是**有名字的格子**，不是一串值）：拿到的是 %r" % (data,))
    # 深拷一次：时间线**拥有**这些事实 —— 调用方回头改自己那个字典
    # （或者改某个嵌套的小字典）不许改到历史（append-only 的意思就在这儿）。
    facts = copy.deepcopy(data)

    try:
        json.dumps(facts, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "data 里有个值 JSON 装不下（%s）—— **字节不进 JSON**："
            "图走文件名（`pause-<n>.png`），字节在盘上。整份 data 得能原样进 `/live` 的 JSON。"
            % exc)

    for key in facts:
        hit = [w for w in _words(key) if w in JUDGMENT_WORDS]
        if hit:
            raise ValueError(
                "判断词不许当字段名（%r 里的 %r）—— 契约 §二③：字段名里不许出现 "
                "success / changed / done 这种词。判断不是脚本写的格子："
                "「该怎样」用 `expect`（运营写）或 `verdict`（收到事实的那一方写），"
                "「变没变」用 `sig_before` / `sig_after`（`None` 就是「看不见」，见下）。"
                % (key, hit[0]))

    if "expect" in facts and facts["expect"] is None:
        raise ValueError(
            "`expect` 不许留空 —— 运营说不好时要**明写** `%s`（events.UNDECLARED）。"
            "留空的话「他说不好」与「这一格没人碰过」在纸上一模一样，读的人分不出来；"
            "而这一格正是整个契约的诚实阀门（契约 §四）。" % UNDECLARED)

    for cell in SIG_CELLS:
        if cell in facts and facts[cell] is None and not str(facts.get("why") or "").strip():
            raise ValueError(
                "`%s = None` 是「**看不见**」（一等值），得配一句 `why` —— "
                "契约 §二②：「看不见」不许被折算成「没变化」，而把两者分开的**只有**那句话。"
                "（`goldenagesouls.py` 的 `read()` 读不到返回 `None` 是今天唯一做对的那个。）"
                % cell)

    return facts


class Timeline:
    """一趟运行的事件流：**append-only**、有上限、线程安全。

    - **append-only**：只有 `add`，没有改、没有删（历史不许被重写）；
    - **有上限**：超了 `MAX_EVENTS` 丢最旧的，丢了几条 `dropped()` 说得出；
    - **线程安全**：写的人不止一个（探路的回调在工作线程上、服务的闸口在另一个）。

    ⚠️ 生产代码**只有 `Service.narrate` 一个写入口**（谁都不许直接 `add`）——
    人话纪律与七格的形状是靠「只有一条路进来」守住的。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list = []
        #: **单调递增**的号（不是下标）：排序用它、跨重启用它、丢事件时它不回卷。
        self._n = 0
        self._dropped = 0

    # ── 写（唯一一个）─────────────────────────────────────────────
    def add(self, kind: str, say: str, *, who: str = "system", data=None) -> dict:
        """记一条，返回它（副本）。形状不对就**抛**，一条都不留。

        形状不对包括：`who` 不是那三方、`say` 不是人话（空/不是字符串）、`kind` 空着、
        `data` 不是字典、`data` 里有判断词/字节/留空的 `expect`/没带 `why` 的 `None` 签名 ——
        还有「脚本替运营声明期望」。
        """
        kind = _require_text(kind, "kind")
        say = _require_text(say, "say")
        if who not in WHO:
            raise ValueError(
                "who 只认三个值：%s（拿到的是 %r）—— 页面靠它决定气泡长相（那是「聊天」那一半），"
                "多一个值就得让页面猜。" % (" / ".join(WHO), who))
        facts = _facts(data)
        if who == "agent":
            taken = [c for c in SCRIPT_MAY_NOT_FILL if c in facts]
            if taken:
                raise ValueError(
                    "脚本（who=\"agent\"）不许填 %s —— 契约 §二①：脚本只填前五格。"
                    "第七格（verdict）**永远不是它的**；第六格（expect）是运营写的"
                    "（契约 §四：让执行的那一方当裁判，正是这一片要换掉的那个东西）。"
                    % "、".join("`%s`" % c for c in taken))

        with self._lock:
            self._n += 1
            event = {"n": self._n, "at": _now(), "kind": kind, "who": who,
                     "say": say, "data": facts}
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._dropped += len(self._events) - MAX_EVENTS
                del self._events[:len(self._events) - MAX_EVENTS]
            return copy.deepcopy(event)      # 同上：拿回去的那一份也改不动历史

    # ── 读 ────────────────────────────────────────────────────────
    def all(self, limit: int = 500) -> list[dict]:
        """**最后** `limit` 条（旧 → 新）—— 页面要的是「刚刚发生了什么」。

        `limit <= 0` = 一条也不要（**不许**把 `0` 读成「不限」：`[-0:]` 是整个列表）。
        读出来的是**深副本**：一条读不许改历史（浅拷只保住了最外面那一层，
        改一个嵌套的小字典照样能改到存下来的那份）。这点拷贝的代价（几百条
        短字典）比「历史能被读的人改」小得多。
        """
        with self._lock:
            if limit is None or limit <= 0:
                return []
            return [copy.deepcopy(e) for e in self._events[-limit:]]

    def dropped(self) -> int:
        """**超上限丢掉了几条**（要能说出来 —— 悄悄丢就是静默）。"""
        with self._lock:
            return self._dropped

    def __len__(self) -> int:
        """现在留着几条（`/live` 用它判「这一次是不是只回了一部分」）。"""
        with self._lock:
            return len(self._events)
