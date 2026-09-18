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

**这一版七格一格都没填 —— 而原因不是「契约要求别填」，是「没有填的人」。**
契约 §五那句的**字面**是「只把七格**如实记下来**」（记 ≠ 不记）：它要求的是**别替它算**，
不是**别记**。这一版记不下来的原因是**来源**：

- `receipt`：**没有任何一方在写它**（契约 §一自己写着今天的 CDP 回执「没上报」）；
- `sig_before` / `sig_after`：**没有人在算**「那一页的原始签」；
- `verdict`：**裁判不存在**（契约 §五：这一版不判 Business Truth）；
- `expect`：**运营写它的入口不存在**，而且已声明那一半的机器形状还没定（契约 §四那张菜单）。

**落不下的不是格子，是填格子的人** —— 格子已经在形状里等他们了。
这个文件管的是**形状**：七格落得下、落得对、落不下的地方**响**（抛），不静默。

⚠️ **不是「先拼个时间线、字段随手定」**（契约 §三）。反过来做会得到
「给旧的 `step` 字段换个名字」—— 三层（Action / State / Business Truth）就白分了。

## 二、五条硬规矩（都在下面机械挡掉了）

**这五条里有四条判的都是同一件事 —— 「谁在说」**（契约 §二：承重的不是「有哪几格」，是「谁填」）。

1. **字段名里不许出现判断词**（契约 §二③）。不许有 `success` / `changed` / `done`
   这种格子。今天那个 `step="success"` 就是这么来的：**脚本自起的名字，后台照着当真话读**。
   挡的是 `data` 里**每一层**的**键**（顶层挡得住、`{"result": {"ok": true}}` 挡不住 =
   等于没挡），**凭据是「谁填」**：`receipt`（cdp 逐字转抄）/ `verdict`（裁判）/ `expect`（运营）
   那三格里可以有判断词（过滤转抄来的原文 = 伪造笔录）；**脚本自己填的四格一个字都不许**。
2. **「看不见」是一等值**（契约 §二②）：`sig_after = None` **加一句 `why`**，
   **不许**被折算成「没变化」。所以 `None` 一路原样留着（连 `json.dumps` 都不许把它吃掉），
   而写了 `None` 却不说是为什么 —— **抛**。
   ⚠️ 这条管的是**任何一格**（不只签名那两格；修复轮 2 的 M3）：`nan` 那条路被闸挡住了，
   裸 `None` 那条路就不许还开着 —— 两条路到了线上是同一个形状（`null`）。
3. **谁填哪一格**（契约 §二①）：脚本只填前五格。时间线上脚本的声音就是 `who="agent"`，
   它的 `data` 里**不许**出现 `verdict`（判断）与 `expect`（那是运营写的）——
   脚本替运营声明期望，正是「执行的那一方在当裁判」换个地方又长出来。
4. **`kind` 是一张封闭的词表**（`KINDS`，见下）：没有的词不认。
5. **词表按嗓子分**（`VOICE_KINDS`）：脚本只说得出口 `step`，人只说得出口 `human_said`，
   状态机那些词**只有服务那一方**能说。关上「新词」那半扇门、却留着一张能说 `done` 的嘴，
   等于没关 —— 这条与规矩 1 是同一个病的两半。

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

__all__ = ["WHO", "KINDS", "MAX_EVENTS", "CELLS", "UNDECLARED", "JUDGMENT_WORDS",
           "safe_value", "Timeline"]


#: 线上装不下的那些码位（**孤立代理对**）换成什么。U+FFFD 是「这里有一个字节读不出来」
#: 的通用写法 —— 换成 `?` 会让人以为页面本来就写着一个问号。
_UNWRITABLE = "�"


def safe_value(value) -> tuple:
    """把一个值里**写不出去**的码位换成 `\\ufffd`，返回 `(换过的值, 换了几个)`。

    ⚠️ **它为什么必须存在**（2026-09-18，Task 4 收口复审实测出来的洞，就在这个模块里）：
    闸（`_facts`）的刻度管的是 **`data`**，而**事件外壳**（`kind` / `say`）没有用同一把尺子
    —— `Timeline.add("step", "\\ud800")` **从正门收下**，随后 `/live` 直接 500：
    **整条时间线一条都读不出来**（不是那一条事件坏掉）。一个孤立代理对过得了
    `json.dumps`，却过不了最后那次 `.encode("utf-8")` —— 把它变成 500 的是**这一层**。

    ⚠️ 为什么是「换掉 + 数出来」而不是「抛」：这些字节**来自外面**
    （CDP 的回执、页面上的一段字、运营写的期望）——抛掉整条事件等于**把这一步的记录丢了**，
    而丢记录正是这份契约要治的病。换掉之后**读得到那条事件**，而且知道「有几个字节没能原样留下」。

    ⚠️ 它**不替 `_facts` 那道闸干活**：`data` 里出现这种值仍然由那道闸**当场拒**
    （那是写的人的编程错误）。这里的用途只有两个：**转抄外面来的东西**时（回执 / 页面上的字）
    与**事件外壳**（`Service.narrate` 的 `say`）。
    """
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            pass
        else:
            return value, 0
        chars, replaced = [], 0
        for char in value:
            try:
                char.encode("utf-8")
            except UnicodeEncodeError:
                chars.append(_UNWRITABLE)
                replaced += 1
            else:
                chars.append(char)
        return "".join(chars), replaced
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            fixed_key, key_count = safe_value(key)          # 键也要换（它同样是「外面来的」）
            fixed, count = safe_value(item)
            total += count + key_count
            out[fixed_key] = fixed
        return out, total
    if isinstance(value, (list, tuple)):
        total = 0
        out = []
        for item in value:
            fixed, count = safe_value(item)
            total += count
            out.append(fixed)
        return out, total
    return value, 0

#: 时间线上说话的**三方**（页面靠它决定气泡长相：它就是「聊天」那一半）。
#: `agent` = 它自己（探路的步、模型的话）；`system` = 服务/系统；`you` = 人。
WHO = ("agent", "system", "you")

#: **事件的词表**（计划 Task 4 目录表那九行的全部产出 + 这一版补的两个）——
#: **封闭**：不在表里的 `kind` 一律拒收。
#:
#: 为什么要关这扇门（复审 2026-09-18 点名）：`datewhirl` 那个病的形状正是
#: **「脚本自起的名字，后台照着当真话读」**（`step="success"`）。一个自称「事件词汇表」
#: 的模块要是谁递什么名字都收，那它就只是「一个字符串字段」。加一个词的门槛很低的 ——
#: 往这张表里加一行、并想清它说的是**哪一件事实**（不是「它感觉怎么样」）。
KINDS = (
    # ── 一个 job 的一生（目录表第 3、4、5、6 行）──
    "submitted",        # 收到了（`start()`）
    "queued",           # 排队等窗口（前面还有 run）
    "running",          # 在跑
    "failed",           # 跑挂了（**跑挂 ≠ 跑成**）
    "done",             # 图走到了 END（**不是**「成了」—— 成没成看 `end_note` 原话）
    "cap_hit",          # 撞上限了（revision / lint / selftest）
    # ── 窗口那一支（目录表第 1、2 行）──
    "window_died",      # 窗口没了（从「不是没了」翻成「没了」的那一翻）
    "window_reopened",  # 窗口重开了（探路重跑 / 从断点接着跑，两句不同的话）
    # ── 别的（目录表第 7、8、9 行 + 这一版补的）──
    "recovered",        # 服务重启过，这个 job 是从 checkpoint 捡回来的
    "human_said",       # 人说的话 / 人打过回（`who="you"`）
    "shot_missing",     # 这一轮没留下图（配一句「为什么没有」）
    "state_unreadable", # 跑完一步之后读不回自己的状态（**读**那一侧的静默路，Task 4 补）
    "step",             # 「第 N 步」：契约七格主要落在这类事件上（探路的 `on_step` 是 Task 5）
)

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

#: 判断词**允许出现**的那三格 —— 凭据是「**谁填**」，不是「它在不在格子里」。
#:
#: 契约 §二③ 的边界（2026-09-18 裁定，**修复轮 2 改准了**）：管的是**契约自己的格子名**，
#: **不管逐字转抄进来的原文**。而「谁填」决定那一格里放的是不是别人的原话：
#:   - `receipt` —— **cdp 二进制**填，脚本逐字**转抄**：替它过滤 = **伪造笔录**；
#:   - `verdict` / `expect` —— **裁判 / 运营**填：判断**就该**待在这两格里。
#: ⚠️ 另外四格（`step_no` / `action` / `sig_before` / `sig_after`）**是脚本自己填的** ——
#: 它在那儿写一个 `success` / `ok` 不是别人的原话，是**脚本自起的名字**，
#: 正是规矩 ① 要防的那个病换到了一个格子里。**这四格里一个字都不许有。**
JUDGMENT_MAY_APPEAR = ("receipt", "verdict", "expect")

#: 「有没有变化」那两格（契约 §二②点名的那两格）。⚠️ 闸现在管的是**所有格**（见下），
#: 这个元组只留着说明契约点的是哪两格 —— 别再拿它当判据的射程。
SIG_CELLS = ("sig_before", "sig_after")

#: 「看不见」要配的那句话叫什么（`why`）。它可以是**一句人话**（这条事件里所有 `None` 共用），
#: 也可以是**按字段点名**的字典（`{"sig_after": "窗口没答", "visible": "接口没回这个数"}`）——
#: 后者用在「同一件事里有两格以上看不见」的时候，免得读的人分不出那句话说的是哪一格。
WHY_KEY = "why"

#: 写了 `None` **不用**解释的那些格：`verdict=None` = 「**还没人判过**」，
#: 是那一格定义好的一等值（与「量不出来」不是一回事）。
NONE_WITHOUT_WHY = ("verdict",)


def _require_why(why, cells: list) -> None:
    """**任何一格写了 `None`** 都要配一句解释（规矩 2 的扩展版，修复轮 2 的 M3）。

    为什么从「签名那两格」扩到**所有格**（复审 2026-09-18 实测出来的不对称）：
    `nan` 那条路被闸挡住了（写的人当场挨一句报错），而**裸 `None` 那条路一直开着、连一句解释
    都不要** —— 两条路到了线上是**同一个形状**（`null`）。闸拿掉的是响的那条、留下的是静的那条；
    而闸的理由（「`null` 是『看不见』那个一等值，不许被混同」）在别的格里**同样成立**
    （`visible=null` 与「没量过这个数」也是同一个形状）。
    """
    if isinstance(why, dict):
        missing = [c for c in cells if not str(why.get(c) or "").strip()]
        if not missing:
            return
        raise ValueError(
            "这些格写了 `None`（「看不见」），但 `why` 里没有它们的解释：%s —— "
            "`why` 写成字典时**要逐格点名**（比如 {\"sig_after\": \"窗口没答\"}）。"
            "「看不见」是一等值，把「看不见」与「没变化 / 没量过」分开的**只有**那句话。"
            % "、".join("`%s`" % c for c in missing))
    if not str(why or "").strip():
        raise ValueError(
            "这些格写了 `None`（「看不见」）：%s —— 得配一句 `%s`（人话），"
            "或者一个**按字段点名**的字典。契约 §二②：「看不见」是一等值，"
            "不许被折算成「没变化」；把两者分开的只有那句话。"
            "（`goldenagesouls.py` 的 `read()` 读不到返回 `None` 是今天唯一做对的那个。）"
            % ("、".join("`%s`" % c for c in cells), WHY_KEY))

#: 词表**按嗓子**分（规矩 1 的另一半，修复轮 2 加）—— 与 `JUDGMENT_MAY_APPEAR` 同一个道理：
#: 承重的不是「有哪几格 / 有哪几个词」，是**谁在说**。
#:   - 脚本（`who="agent"`）只报**它自己干的事**（`step`）；
#:   - 人（`who="you"`）只说**人自己的话**（`human_said`）；
#:   - 状态机那些词（`done` / `failed` / `running` / `cap_hit` …）**只有服务那一方**能说。
#: 为什么这条要紧：`datewhirl` 的病是「脚本自起的名字被后台照着当真话读」——
#: 关上「新词」那半扇门、却留着一张嘴能说 `done`，那半扇门等于没关。
VOICE_KINDS = {"agent": ("step",), "you": ("human_said",)}

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


def _nested_problem(node, *, top: bool, in_foreign_cell: bool, in_reference: bool = False,
                    path: tuple = ()):
    """把事实里**每一层**的键过一遍，返回第一个不对的 `(哪条规矩, 键, 命中词, 路径)`。

    为什么不能只看最外面那一层（复审 2026-09-18 实测）：`{"step_no":3, "result":{"ok":true}}`
    顶层键全是干净的 —— 判**藏在里面**，等于没挡。

    两层规矩，各自有各自的来由：

    1. **判断词不许当键** —— 但**别人填的那三格里不查**（`JUDGMENT_MAY_APPEAR`）：
       `{"receipt": {"ok": false}}` 是照抄 CDP 说了什么（过滤 = 伪造笔录），
       `verdict={"changed": true}` / `expect={"screen_changed": true}` 是判断，
       判断就该待在那两格里。
       ⚠️ 而**脚本自己填的四格**（`step_no` / `action` / `sig_*`）**照查**：
       `{"action": {"clicked_ok": true}}` 里的 `clicked_ok` 不是别人的原话，
       是脚本自起的名字 —— 凭据是「谁填」，不是「它在不在格子里」（修复轮 2 的 M1）。
    2. **七格的名字只在顶层**：`receipt` 是**转抄** —— 转抄来的东西里冒出一个 `verdict`，
       说明有人把「判」塞进收据里了。
       ⚠️ 唯一的例外是 `why` 那一支：它是**按字段点名**的解释（`{"sig_after": "窗口没答"}`）——
       在那儿写格子名**正是它的用途**，不是把判塞进哪里。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key)
            if not in_foreign_cell:
                hit = [w for w in _words(name) if w in JUDGMENT_WORDS]
                if hit:
                    return ("judgment", name, hit[0], path + (name,))
            if not top and not in_reference and name in CELLS:
                return ("cell", name, "", path + (name,))
            deeper = _nested_problem(value, top=False, path=path + (name,),
                                     in_reference=in_reference or (top and name == WHY_KEY),
                                     in_foreign_cell=in_foreign_cell
                                     or (top and name in JUDGMENT_MAY_APPEAR))
            if deeper:
                return deeper
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            deeper = _nested_problem(item, top=False, in_foreign_cell=in_foreign_cell,
                                     in_reference=in_reference,
                                     path=path + ("[%d]" % index,))
            if deeper:
                return deeper
    return None


def _name_the_place(path: tuple) -> str:
    """把路径说成人话：`('action', 'targets', '[0]', 'ok')` → `action.targets[0].ok`。"""
    out = ""
    for part in path:
        out += part if part.startswith("[") else (("." if out else "") + part)
    return out


def _facts(data) -> dict:
    """`data` → 一个**装得下**、**没有判断词**、**没被折算过**的事实字典。

    六道判据，每一道都对应契约里的一条（顺序即报错的优先级）：
      ① 它得是个 mapping（七格是**有名字的格子**）；
      ② **原样**进得了 `/live` 的 JSON（字节、`nan`/`inf` 都不行 —— 见下）；
      ③ 键里不许出现判断词（**每一层**）；④ 七格的名字只在顶层；
      ⑤ `expect` 不许留空；⑥ **任何一格**写了 `None` 都要带 `why`（`verdict` 除外）。
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            "data 得是一个字典（七格是**有名字的格子**，不是一串值）：拿到的是 %r" % (data,))
    # 深拷一次：时间线**拥有**这些事实 —— 调用方回头改自己那个字典
    # （或者改某个嵌套的小字典）不许改到历史（append-only 的意思就在这儿）。
    facts = copy.deepcopy(data)

    # ⚠️ 这道闸的刻度是**线上那一层**，`allow_nan=False` 与 `.encode("utf-8")` 一档都不能少。
    #
    # **因果写对**（修复轮 2 的 M2：上一版把账记在 starlette 头上，实测是错的）：
    # `/live` 的路由标注是 `-> dict`，FastAPI 会把它过一遍 **pydantic 的 JSON-mode 序列化**
    # （`ser_json_inf_nan` 默认把 `nan`/`inf` 写成 **`null`**）—— 把它变成 `null` 的是**这一层**，
    # 不是 starlette（starlette 的 `JSONResponse.render` 用 `allow_nan=False`，那一档是**抛**）。
    # ⇒ 少了这道闸的话，写进去的 `nan` 会**静默变成 `null`**，而 `null` 正是 `sig_after`
    # 表示「**看不见**」的那个一等值：「看不见」与「一个数」被抹成同一个 ——
    # 正是这份契约要治的病（`datewhirl` 把「什么都没看见」判成「做完了」）换了个层次，
    # 这次是**序列化器的默认值**干的。
    # `.encode("utf-8")` 是**同一族的第二格刻度**（修复轮 2 实测）：一个孤立代理对
    # （`"\ud800"`）过得了 `json.dumps`，却过不了最后那次编码 —— `/live` 直接 500。
    # 量具的刻度必须与线上同档，否则「我量过了」是假的。
    try:
        json.dumps(facts, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "data 里有个值**原样**出不了 `/live` 那一层（%s）—— 三条路，都不是「随便塞」：\n"
            "· **字节**（bytes / 一个文件对象）：图走文件名（`pause-<n>.png`），字节在盘上；\n"
            "· **数字**（`nan` / `inf`）：`/live` 的返回值过 pydantic 的 JSON-mode 序列化，"
            "它会把这两个写成 **`null`** —— 而 `null` 是「**看不见**」那个一等值的写法"
            "（`sig_after = None` + 一句 `why`）。要记「量不出来」就明写 `None`（并说为什么），"
            "**不许**用一个会静默变成 `null` 的数；\n"
            "· **编码不了的东西**（比如一个孤立代理对）：`/live` 会**直接 500**，"
            "整条时间线一条都读不出来。" % exc)

    bad = _nested_problem(facts, top=True, in_foreign_cell=False)
    if bad and bad[0] == "judgment":
        raise ValueError(
            "判断词不许当字段名（%r）—— 契约 §二③：字段名里不许出现 "
            "success / changed / done 这种词，**哪一层都不许**（只查最外面那一层 = 没查）。"
            "⚠️ 凭据是**谁填**，不是「它在不在格子里」：`receipt`（cdp 逐字转抄）、"
            "`verdict`（裁判）、`expect`（运营）那三格**可以有**判断词；"
            "而这四格（`step_no` / `action` / `sig_before` / `sig_after`）**是脚本自己填的** —— "
            "它在那儿写的 `ok` / `success` 是**脚本自起的名字**，正是规矩 ① 要防的那个病。"
            "（要记「该怎样」写 `expect`；「变没变」写 `verdict` 或 `sig_*`。）"
            % _name_the_place(bad[3]))
    if bad and bad[0] == "cell":
        raise ValueError(
            "`%s` 是**顶层**那七格之一，不许藏在别的键里面（%s）—— 收据（`receipt`）是**转抄**"
            "（契约 §二：脚本只转抄、也留原文），转抄来的东西里冒出一个格子名，"
            "说明有人把「判」塞进收据里了。七个格子只有顶层那七把椅子。"
            % (bad[1], _name_the_place(bad[3])))

    if "expect" in facts and facts["expect"] is None:
        raise ValueError(
            "`expect` 不许留空 —— 运营说不好时要**明写** `%s`（events.UNDECLARED）。"
            "留空的话「他说不好」与「这一格没人碰过」在纸上一模一样，读的人分不出来；"
            "而这一格正是整个契约的诚实阀门（契约 §四）。" % UNDECLARED)

    silent = [k for k, v in facts.items() if v is None and k not in NONE_WITHOUT_WHY]
    if silent:
        _require_why(facts.get(WHY_KEY), silent)

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

        形状不对包括：`who` 不是那三方、`say` 不是人话（空/不是字符串）、`kind` 不在词表里
        （`KINDS`）、`data` 不是字典、`data` 里有判断词（哪一层都不许）/字节或 `nan`/
        留空的 `expect`/没带 `why` 的 `None` 签名、七格的名字藏在别的键里面 ——
        还有「脚本替运营声明期望」。
        """
        kind = _require_text(kind, "kind")
        if kind not in KINDS:
            raise ValueError(
                "不认得这个 kind：%r —— 事件的词表就写在 `agent/events.py` 的 `KINDS` 里"
                "（第 4 条规矩）。一个自称「事件词汇表」的模块要是谁递什么名字都收，"
                "那它就只是一个字符串字段 —— 而「脚本自起的名字被后台照着当真话读」"
                "正是这一片要治的那个病（`datewhirl.py:175` 的 `step=\"success\"`）。"
                "要加一个词：往 `KINDS` 里加一行，并想清它说的是**哪一件事实**。"
                "现有：%s" % (kind, " / ".join(KINDS)))
        say = _require_text(say, "say")
        if who not in WHO:
            raise ValueError(
                "who 只认三个值：%s（拿到的是 %r）—— 页面靠它决定气泡长相（那是「聊天」那一半），"
                "多一个值就得让页面猜。" % (" / ".join(WHO), who))
        spoken = VOICE_KINDS.get(who)
        if spoken is not None and kind not in spoken:
            raise ValueError(
                "%s 这一方说不出口 %r —— 词表**按嗓子分**（`VOICE_KINDS`）："
                "脚本（`who=\"agent\"`）只报它自己干的事（%s），人（`who=\"you\"`）只说人自己的话（%s）；"
                "状态机那些词（`done` / `failed` / `running` / `cap_hit` …）**只有服务那一方**能说。"
                "为什么：这份契约的承重句是「**承重的不是有哪几格，是谁填**」——"
                "脚本自起的名字被后台照着当真话读（`datewhirl.py:175`）正是要治的那个病，"
                "它换一张嘴说出来，病还是同一个。"
                % (who, kind, " / ".join(VOICE_KINDS["agent"]), " / ".join(VOICE_KINDS["you"])))
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
