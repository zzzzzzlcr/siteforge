"""Task 4：契约检查器（lint）—— 把手拼 JS 变成**机器可打回的约束**（规格 §5.2）。

## 它为什么存在

用户 2026-09-16 定的原则：**动页面一律走 cdp 工具**（`form` / `click` / `scroll`），
**不要手拼 JS**。而这份 py 是 LLM 写出来的 —— 原则写在提示词里只是**请求**，
所以这里把它变成**机器打回**：产物里出现下面四条写模式，图里的 `lint` 节点就
回 `draft`，**带着行号**让它重写（`Violation.line` / `.snippet` 就是为这个带的）。

## 判据表（规格 §5.2，按**预检 S1** 修正后的四条）

| code | 违规 | 为什么 |
|---|---|---|
| `js-fill` | `dispatchEvent(new Event('input'` / `'change'` 这类**合成事件** | 合成事件站点（React 那种）常不认：人是填了，站没收到 |
| `js-native-setter` | `Object.getOwnPropertyDescriptor(...'value')` | 手拼 native setter，绕过网站自己的输入逻辑 |
| `js-click` | `.click()` 形式的 JS 点击 | 不是拟人手势 |
| `js-value-assign` | `e.value = ` 形式的直接赋值 | 绕过 cdp 的拟人键入手势 |

## ⚠️ 为什么**没有**「裸 `document.querySelector`」那条（S1，2026-09-17 裁定）

原来的表里有一条「裸 `document.querySelector` 出现在**写**路径」。**删掉了**，两条理由：

1. **「读路径 vs 写路径」在静态上不可判定** —— 想看出一段 `querySelector` 的结果
   最后是不是被写回了，本质上要做数据流分析，而 JS 是拼在字符串里发出去的。
2. **`querySelector` 只是查找**，读也用它、写也用它。拿它当违规会**误伤合法的读** ——
   而产物里的 `cdp eval` **读页面**（页签名、当前 URL、shadow DOM 里的正文）
   本来就要用它（见 `fixtures/reference_site.py` 的 `_PAGE_TEXT_JS`）。

→ 判据盯的是**写动作本身**（上面四条），不是**查找方式**。
`test_lookup_alone_is_not_a_violation` 是这条裁定的回归钉子 ——
谁再把那条加回表里，测试会红。

## 怎么扫

**整份源码逐行扫**（不用 AST 挑字符串）。为什么不挑：

- JS 是**字符串**，可以拼在多行、多个字面量里（参考产物的 `_PAGE_TEXT_JS` 就是
  一串相邻字符串），按 AST 挑字符串反而更容易漏；
- 判据要**能指着行说**（回灌给 agent 的时候它认的是行号），逐行扫天然对得上；
- 代价是**注释与 docstring 里的样例文案也会被算进去**。可接受：重写一句话就行，
  而「产物里出现这串字」本身也不该出现在一份正经的产物里。

## 已知边界（**不是** lint 该自己决定的事）

只打回**上表这四条**写模式。同族的写法（比如 `e.checked = true`、`setAttribute('value', ...)`）
暂不在表内 —— **表是契约，扩表是改规格**，得回规格/计划那边定，不是 lint 顺手加一条。
真遇到漏检，报上去，别在这里自作主张。

**第二族**（判据与产物形状，码都是 `judge-*`）见下面 `JUDGE_RULES` 那一块：
2026-09-29 晚按交接 §6 把探针（`docs/probes/2026-09-28-georgiapower/artifact_lint.py`）
那套 A~I 接进了**生成期的出口** —— 命中就不许出产物。五条新规则各自的现场（哪一次真站实测、
证据在哪个文件）与**判据边界**（什么形状不挂）写在它自己的 docstring 里。
⚠️ 那一族**同样是契约**：`JUDGE_RULES` 里没有的不报（测试的变异验证就钉在这一行上）。

给非技术人员看的文字（`Violation.message`）必须是**人话**，且**说清改用哪条 cdp 命令**
（D16；光说「不许这样」会让 agent 原地打转）。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Optional, Pattern

__all__ = ["Violation", "Rule", "RULES", "check", "check_unverified", "no_basis_slots"]


@dataclass(frozen=True)
class Violation:
    """一处违规。`line` 是 1-based 行号，`snippet` 是那一行的原文（截断）。

    回灌给 agent 时靠这两个字段「**指着行说**」—— 只说「你手拼 JS 了」它找不到地方。
    """

    line: int
    code: str
    message: str
    snippet: str

    def __str__(self) -> str:  # 日志/调试用；给人看的正文是 message
        return "第 %d 行：%s" % (self.line, self.message)


@dataclass(frozen=True)
class Rule:
    """一条写模式判据。`message` 是要回灌给 agent 的人话（D16）。"""

    code: str
    pattern: Pattern[str]
    message: str


#: 判据表 —— **只**这四条写模式（规格 §5.2 + 预检 S1）。
#: 顺序 = 表的顺序；同一行命中多条时按这个顺序报。
#:
#: 正则都刻意写在**写动作**上，而不是「出现了某个名字」上：
#: `.click()` 要**空括号**（`self.cdp.click(selector)` 是合规的 cdp 手势）；
#: `.value =` 用 `(?!=)` 排掉 `===` / `==`（那是**读**出来比一比，不是写）。
RULES: tuple[Rule, ...] = (
    Rule(
        code="js-fill",
        # 表里举的是 `'input'` / `'change'`，但这一条的理由（「合成事件站点常不认」）
        # 对**任何**合成事件都成立 —— 所以按 `new *Event(` 这**一族**匹配，
        # 免得 `dispatchEvent(new Event('blur'))` 从「举例」的缝里漏过去。
        pattern=re.compile(r"dispatchEvent\s*\(\s*new\s+[\w$]*Event\s*\("),
        message=("这段在用 JavaScript 假装「输入框变了」。真网站常常不认这种假通知"
                 "（人是填了，站没收到）—— 改用 cdp 的 form 命令来填。"),
    ),
    Rule(
        code="js-native-setter",
        # 在 cdp-first 的产物里，去拿属性的描述符只有**一个**用处：
        # 抠出原生的 setter 绕过框架的受控输入。读页面不需要它。
        pattern=re.compile(r"Object\s*\.\s*getOwnPropertyDescriptor\s*\("),
        message=("这段绕过网站自己的输入逻辑，把值直接塞进输入框（网站不知道你填过）"
                 "—— 改用 cdp 的 form 命令来填。"),
    ),
    Rule(
        code="js-click",
        # 空括号：`self.cdp.click(selector)` 是**合规**的 cdp 手势，不许误伤。
        pattern=re.compile(r"\.\s*click\s*\(\s*\)"),
        message=("这段用 JavaScript 直接点了一下，不是人手点的那种点法，网站可能不认"
                 "—— 改用 cdp 的 click 命令来点。"),
    ),
    Rule(
        code="js-value-assign",
        # `(?!=)` 排掉 `==` / `===`（读出来比一比，是读不是写）。
        pattern=re.compile(r"\.\s*value\s*=(?!=)"),
        message=("这段在直接改输入框里的值，网站收不到「有人输入」这件事"
                 "—— 改用 cdp 的 form 命令来填。"),
    ),
)

# ──────────────────────────────────────────────────────────────────────────
# 第二族：**判据与产物形状**（`STATES[*].when` / `FILLS` / 产物里那几格骨架）——
# 要读字面量，正则扫不出来。
#
# ★ 2026-09-29（用户裁定）：先挂 **C / D / H** 三条。
# ★★ 2026-09-29 晚（交接《骨架还缺什么》§6 原文：「`artifact_lint.py` 那套检查**属于生成期
#   或 CI，不是事后扫**。把它接进生成器的出口，产出前跑一遍，命中就不许出产物」）：
#   把探针那一套补成 **A / E / F / G / I** 五条 —— 字母与探针
#   （`docs/probes/2026-09-28-georgiapower/artifact_lint.py`）一一对应，
#   现场、证据文件、**判据边界**写在每条规则自己的 docstring 里。
#
# 为什么会有这一族：上面四条盯的是「**怎么写**」，而 2026-09-28 一天里修掉的六个站，
# 死因多半在「**认页那一段字**」与「**产物缺了骨架的哪一格**」——判据错了 / 缺一格，
# 产物每一步都「做成了」而整单没成（georgiapower：真做 2 步、跳过 23 步，日志每步写着点上了）。
#
# ⚠️ 从探针那套里**收窄**过的两条（A 与 I，理由写在各自 docstring 里）：探针是**事后扫**、
# 报告长了有人读；这里是**生成期的闸**，命中就不许出产物 ⇒ 凡是本骨架自己那份合法写法会被
# 打回的，都必须收窄到「真的会出事的那个形状」，否则闸一挂上，**每一份正常产物都出不去**。
#
# ⚠️ **仍然不挂的**：
#   · **B 跨元素拼接**：机械判据要**逐元素正文**，那只在生成期拿得到（`cdp observe` 的
#     `node_texts`，2026-09-29 起随模型一起来）——生成期已经在换/丢那段判据了
#     （`browser_agent._when_for`）；放到这一层只能写成「长串 + 空格」那种启发式，
#     实测它会把**修好的**产物也标出来（51 份里 185 处）。
JUDGE_RULES: tuple[str, ...] = (
    "judge-transient", "judge-fill-value", "judge-url-only",
    "judge-strict-unguarded", "judge-ungated-click", "judge-no-follow-tab",
    "judge-source-autoname", "judge-fragile-selectors",
    #: ★ 2026-10-15（修站那一刀④，spec §3.1）：I 那一格的**另一种判决** —— 产品里那根
    #: 脆地址**在探路那一刻根本没有更稳的可依**（卡上没锚、也没有一条不脆的采信候选）。
    #: 与上面那一条**分开**报，是因为两族的出路完全不同：一个改稿能修（补一条稳的），
    #: 一个只能删/请人/接受 —— `tools/fam_report.py` 与 `docs/根因库.md` 要分别计数。
    #: ⚠️ 这一条只在**传了 `observed`** 时才可能报（没有冻结的观察就没有「出处」可判）。
    "judge-fragile-no-basis",
)

#: 瞬时/校验类文案（C）。与 `docs/probes/2026-09-28-georgiapower/artifact_lint.py` 的
#: `TRANSIENT` 同一套词 —— 两处对不上就会出现「体检说有问题、闸说没有」那种不一致。
TRANSIENT_RE = re.compile(
    r"complete this field|please enter|is not valid|invalid|required|"
    r"error|loading|please wait|try again|verification", re.I)

#: A：产物把 `strict: True` 发给 cdp 的 form —— 与它**成对**的那条退路见 `_rule_a`。
STRICT_TRUE_RE = re.compile(r"['\"]?strict['\"]?\s*[:=]\s*True")
#: A 的退路原话：骨架里 `except TypeError:` 那一支（`agent/template.py` 的 `_perform`）。
STRICT_GUARD_MARK = "except TypeError"
#: F：点完跟新 tab 那个方法（定义与调用都带这一串 —— `_follow_new_tab(`）。
FOLLOW_NEW_TAB_MARK = "_follow_new_tab("
#: G：账本给「页面上这个框」起的**占位名**（`field` / `field-2` …）—— 它们是探索期的临时名，
#: **本来就不该出现在资料库里** ⇒ 不当「键名对不上」报（逐字照抄探针 `artifact_lint.py:36-38`
#: 的原话，不是这里新定的口径）。
PLACEHOLDER_FILL = re.compile(r"^field(-\d+)?$")

#: 骨架是一份 `string.Template`：`$states` 这种**占位符不是合法 py**，直接 `ast.parse`
#: 会 `SyntaxError` ⇒ 解析前先把它们换成 `None`。要读的那张 `_ALIASES` 表里**没有**占位符，
#: 所以这一步只是让整份源文本可解析，不会改动要读的那一格。
SKELETON_PLACEHOLDER = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
#: G：「人写的列名」与「机器起的名」在形状上的分界 —— **名字里有数字串**就不像列名了
#: （`textfield_173838` / `input-174` / `#__BVID__408`；运营那边是 `username` / `postal_code`）。
AUTONAME = re.compile(r"\d")

#: snippet 的截断长度：够看出是哪一行，又不至于把整行 JS 灌进提示词。
SNIPPET_CHARS = 120


def _line_of(src: str, needle: str) -> int:
    """这一串字在源码里的行号（找不到给 0）。回灌给 agent 时要「指着行说」。"""
    for lineno, raw in enumerate(str(src or "").splitlines(), start=1):
        if needle in raw:
            return lineno
    return 0

def _line_at(src: str, index: int) -> int:
    """源码里第 `index` 个字符落在哪一行（1-based）—— 正则命中的位置用它，比再找一遍串准。"""
    return str(src or "").count("\n", 0, index) + 1


def _line_of_any(src: str, *needles: str) -> int:
    """第一个**找得到**的那条串的行号（都找不到给 0）。

    为什么不是 `_line_of` 一把：选择器在源码里会被**转义**（`\\x01` 那种文字前缀）或折断，
    整条串搜不到 ⇒ 退回它的尾巴、再退回它所属的那一组 —— 回灌给 agent 时要能指着行说，
    「第 0 行」等于没说。
    """
    for needle in needles:
        line = _line_of(src, str(needle or ""))
        if line:
            return line
    return 0


def _line_text(src: str, lineno: int) -> str:
    """第 `lineno` 行的原文（越界给空串）—— 拼 snippet 用。"""
    lines = str(src or "").splitlines()
    return lines[lineno - 1] if 1 <= lineno <= len(lines) else ""


def _selectors(target) -> list[str]:
    """这份 target 里**写得出来**的选择器（去空）。空列表 = 这一格没有选择器可依。"""
    return [str(s) for s in ((target or {}).get("selectors") or []) if str(s or "").strip()]


# ── 出处三级：这根地址是不是**量到的**（★ 2026-10-15 修站那一刀，spec §3.2）────
#
# 为什么要有这一层：判据要的是「给它补一条稳的地址」，而**那个答案可能根本不存在**
# （探路那一刻这个元素身上没名字、旁边没字）。今天闸不管有没有答案，一律要 —— 于是模型
# 只能编；编完形状一查是「稳的」，就放过去了（实测 job-d1232badf5f6：`header a` 就是这么过的，
# 它大概率命中不止一个 `<a>`，**比被换掉的那根更坏**）。
#
# 这一层只做一件事：把待检查的地址拆成「断言」，逐条回**冻结的原始观察**里找出处。
# ⚠️ **只判出处，不判对错** —— 对不对由自测和人说了算（与 `fix.check_patch` 同一条立场）。

OBSERVED = "observed"
DERIVED = "derived"
UNSUPPORTED = "unsupported"

_ATTR_IN_SEL = re.compile(r'\[\s*([A-Za-z_:][-A-Za-z0-9_:]*)\s*=\s*["\']?([^"\'\]]*)["\']?\s*\]')
_ID_IN_SEL = re.compile(r'#([A-Za-z_][-A-Za-z0-9_]*)')
_CLASS_IN_SEL = re.compile(r'\.([A-Za-z_][-A-Za-z0-9_]*)')
#: 标签名只在**它自己就是一个简单选择器**时才算一句主张（`header a` 那一族）——
#: 所以要求后面紧跟组合子或到头：被 `[属性]` / `#id` / `.cls` / `:伪类` 限定的都不算
#: （`input[name=…]` 里的 `input`、`body:nth-of-type(1)` 里的 `body`，身份是属性/位置给的）。
_TAG_IN_SEL = re.compile(r'(?:^|[\s>+~])([a-zA-Z][a-zA-Z0-9]*)(?=$|[\s>+~])')
#: 剥 `[属性]` / `#id` 时留下的占位符：**不能**用空白 —— 那会把 `input[name=x]` 剥成 `input `，
#: 标签又变回「裸的」。用一个既不是字母也不是组合子的字符，把「这一处被限定过」留在原地。
_QUALIFIED = "."
#: 位置：`:nth-of-type(…)` / `:nth-child(…)` —— **不算断言**（spec §3.2），但也不算「没读懂」
#: （与 `template._HOPS` 同一族的词）。**其余伪类一律进 `unparsed`**。
_POSITIONAL_IN_SEL = re.compile(r':(?:nth-of-type|nth-child)\([^()]*\)', re.I)
#: 顶层组合子（切「简单选择器」用；括号里与引号里的分隔符不算，见 `_segments`）。
_SEPARATOR_IN_SEL = re.compile(r'[\s>+~]+')
#: 一段简单选择器开头的标签名（`input[name=…]` ⇒ `input`）。
_SEG_TAG = re.compile(r'^([a-zA-Z][a-zA-Z0-9]*)')
_TEXT_ANCHOR = re.compile(r'^(?:text|label|nearby_text)\s*=\s*(.+)$', re.S)


def _norm_text(value) -> str:
    """比文字用的一把尺子：去首尾空白、压掉连续空白、撇号归一（与 `template` 那边同一个理由：
    同一个字的两种排版写法算同一个字）。"""
    text = str(value or "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _norm_ident(value) -> str:
    """比**标识**（id / class / 属性值）用的一把尺子：去首尾 + 压掉连续空白。

    ⚠️ **不 casefold、不撇号归一** —— 这一把与 `_norm_text` 是**两把尺子**，别并（spec §3.4）：
      · 文字锚（页面上**印出来**的那句话）⇒ `_norm_text`（同一个字的两种排版算同一个字）；
      · 标识（**代码里的名字**：id / class / 属性值）⇒ 这一把 —— HTML 里它们**大小写敏感**，
        而重放那边是 `Element.matches()`（实测：判据说「每一条都回原始观察里对上了」，
        真站上 `matches("#postcode")` 在 `#Postcode` 上找不到）。
      · 标签名**不在此列**：HTML 的标签名本来就不分大小写，那两处照旧 `casefold()`。
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _segments(text: str) -> list:
    """按**顶层**组合子（空白 / `>` / `+` / `~`）切出几段「简单选择器」。

    括号里与引号里的分隔符不算：`input[placeholder="Where do you live?"]` 是**一段**，不是三段
    （否则属性值里的字会被当成标签读出来）。切它是为了让「带锚的简单选择器挂在哪个标签上」
    这句能问得出来（I2，见 `_anchor_tags`）。
    """
    out: list = []
    buf: list = []
    depth, quote = 0, ""
    for ch in str(text or ""):
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and (ch.isspace() or ch in ">+~"):
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _leftover(text: str) -> str:
    """把**读得懂**的东西全换成空白之后剩下的那段 —— 它就是「判据没读懂的构造」（空串 = 全读懂）。

    读得懂的：`[属性=值]`、`#id`、`.class`、光标签、顶层组合子，加上**位置**
    （`:nth-of-type(…)` / `:nth-child(…)` —— 它不算断言，但也不是「没读懂」，spec §3.2）。
    剩下的（`input[data-x]` 那种属性存在性、`[name^="post"]` 那种非 `=` 运算符、`:has(…)`、
    `*`、逗号……）不是「零断言」，是**没读懂** —— 两者的处置完全不同，见 `assertions_of`。
    """
    rest = _ATTR_IN_SEL.sub(" ", str(text or ""))
    rest = _ID_IN_SEL.sub(" ", rest)
    rest = _CLASS_IN_SEL.sub(" ", rest)
    rest = _POSITIONAL_IN_SEL.sub(" ", rest)
    rest = _TAG_IN_SEL.sub(" ", rest)
    rest = _SEPARATOR_IN_SEL.sub(" ", rest)
    return re.sub(r"\s+", " ", rest).strip()


def assertions_of(selector) -> list:
    """一条选择器 → 它**断言了哪些事实**（`(种类, 值)` 的列表，保序去重）。

    只有这几类算断言（位置**不算** —— `:nth-of-type` / 结构组合子不主张任何身份，
    脆不脆是 `_fragile` 的活，两件事别混）：
      · `("attr", "name=email")` —— 属性名与值
      · `("id", "postcode-field")` —— `#id` 与 `[id=id]` **归一成同一件事**（spec §3.2）
      · `("class", "btn")`
      · `("tag", "input")`
      · `("text", "Sign in")` —— 文字锚（产物重放认的那一套，见 `template._relocate`）
      · `("unparsed", 原文)` —— **判据读不懂的构造**（见下）

    ⚠️ 标签名只在**它自己就是一个简单选择器**时才算（`header a` 那一族）：`input[name=…]` 里的
    `input` 被属性限定、`body:nth-of-type(1)` 里的 `body` 被位置限定 —— 那两处的身份主张是
    属性与位置，不是标签（spec §3.2：位置不算断言）。光秃秃的标签不同：`header a` 里那两个字
    就是它说的**全部内容**，那是一次结构性改写、没人验过它命中几个。

    ⚠️ **读不懂的构造不许静默变成「零断言」**（★ 2026-10-15 评审实测的 fail-open）：
    `input[data-x]`（属性存在性、没有 `=`）、`input[name^="post"]`（非 `=` 运算符）、
    `a[href*=signin]`、`[name~=postcode]`、`div:has(> a)`、`*`、逗号、空串 —— 判据一个都不认识。
    它们以前**吐空**，而 `address_basis` 那边「零断言 ⇒ 只说了位置」的前提是
    **「零断言 ⟺ 纯位置」** ⇒ 于是这些「看着稳、主张了身份、只是没人读懂」的地址对着一张
    **空卡**也能判 `OBSERVED`，还配一句假人话（fail-open 方向被反 + 人话在编）。
    所以这里一律以 `("unparsed", 原文)` 落进断言，`address_basis` 见到就 `UNSUPPORTED`：
    **读不懂 = 不算量到过**。
    """
    text = str(selector or "").strip()
    out: list = []
    if not text:
        return [("unparsed", "")]
    m = _TEXT_ANCHOR.match(text)
    if m:
        return [("text", _norm_text(m.group(1)))]
    for attr, value in _ATTR_IN_SEL.findall(text):
        name = attr.lower()
        # `#id` 与 `[id=id]` 是同一件事（spec §3.2）—— 归一成同一个 kind，两种写法才比得上
        if name == "id":
            out.append(("id", _norm_ident(value)))
        else:
            out.append(("attr", "%s=%s" % (name, _norm_ident(value))))
    # `#id` / `.cls` 只在**括号外**那一层找：`a[href="#top"]` 里的 `#top` 是**值**，不是 id。
    # （不这样遮一下，解析器会自己编出 `("id","top")` —— 它甚至会给观测侧凭空添一条证据。）
    outside = _ATTR_IN_SEL.sub(" ", text)
    for ident in _ID_IN_SEL.findall(outside):
        out.append(("id", _norm_ident(ident)))
    for cls in _CLASS_IN_SEL.findall(outside):
        out.append(("class", _norm_ident(cls)))
    for tag in _TAG_IN_SEL.findall(
            _ATTR_IN_SEL.sub(_QUALIFIED, _ID_IN_SEL.sub(_QUALIFIED, text))):
        out.append(("tag", _norm_text(tag)))
    if _leftover(text):
        out.append(("unparsed", _norm_text(text)))
    seen, uniq = set(), []
    for one in out:
        if one not in seen:
            seen.add(one)
            uniq.append(one)
    return uniq


def _anchor_tags(text: str) -> dict:
    """这条地址里**带锚的简单选择器**各自挂在哪个标签上（`div[name=…]` ⇒ `{("attr","name=…"): "div"}`）。

    只认**它自己那一段**里的标签（`form input[name=…]` 认 `input`，段的切法见 `_segments`）；
    那一段没写出标签（`[name=…]` / `#id` / `.cls`）就不记 —— 没写标签就没有可比的。
    """
    out: dict = {}
    for seg in _segments(text):
        tag = _SEG_TAG.match(seg)
        if not tag:
            continue
        for one in assertions_of(seg):
            if one[0] in ("attr", "id", "class"):
                out.setdefault(one, _norm_text(tag.group(1)))
    return out


def _observed_anchor_tags(observed_selector_text) -> dict:
    """观测原文里**带锚的简单选择器**各自挂在哪些标签上：`input[name=…]` ⇒ `{("attr","name=…"): {"input"}}`。

    I2（★ 2026-10-15 评审）：锚对上了还不够 —— **标签也要对得上**。标签本身不单独算断言
    （它只是「范围限定」，见 `assertions_of`），但重放那边是 `Element.matches(原始串)`：
    `div[name="postcode"]` 对着一张观测原文是 `input[name="postcode"]` 的卡，页面上真有
    `div[name=postcode]` 时就会**静默点到另一个元素**。

    ⚠️ 判法要**窄**：只在**观测侧确实写出了一个标签**时比（`[name=…]` 这种没带标签的锚没有可比的，
    「提案比观测更泛」不算错）；**也绝不**把「观测原文里出现过的标签」整体倒进 facts ——
    那会把 `header a` 放回来（观测链里 `header`/`a` 都在），正好把这个 Task 要堵的路重新打开。
    """
    out: dict = {}
    for one in (observed_selector_text or []):
        for anchor, tag in _anchor_tags(str(one or "")).items():
            out.setdefault(anchor, set()).add(tag)
    return out


def _observed_facts_and_hint(card, observed_selector_text) -> tuple:
    """原始观察里**能当事实用**的那些（同一套拆法：把当时那几条选择器拆开，再加上卡上那几样）。

    ⚠️ 返回的是**二元组** `(facts, hint)`，不是光一个集合 —— `hint` 要单独拎出来降一档用
    （它把 `name` 与 `id` 合并了，见下面那段）。名字里带 `_and_hint` 就是为了不让读的人
    以为它只回一个集合（那会让 `DERIVED` 那两条分支永远走不到，而测试全绿）。

    ⚠️ 标签那件事（I2）**不在**这个二元组里：它是「锚挂在哪个标签上」的**旁注**，不是一条事实
    （混进 `facts` 会让 `header a` 变 `OBSERVED`）—— 单独走 `_observed_anchor_tags`。
    """
    facts: set = set()
    for one in (observed_selector_text or []):
        facts.update(assertions_of(one))
    card = card or {}
    for key, kind in (("label", "text"), ("aria_label", "text"),
                      ("text", "text"), ("placeholder", "text")):
        if _norm_text(card.get(key)):
            facts.add((kind, _norm_text(card.get(key))))
    for one in (card.get("nearby_text") or []):
        if _norm_text(one):
            facts.add(("text", _norm_text(one)))
    #: `hint` 是 **`el.name || el.id`**（cdp 合并了，见下面那段）—— 两个属性都有可能，
    #: 所以它同时记成 `id` 与 `attr name=` 两条**线索**（级别由 `address_basis` 降一档）。
    #: ★ 这一格用**标识**那把尺子（`_norm_ident`，不 casefold）：`hint` 是**代码里的名字**、不是
    #: 页面上印出来的字，而它比的是**断言的值**（`address_basis` 的 DERIVED 支：`value == hint`，
    #: `value` 出自 `assertions_of`）—— 那边已经是 `_norm_ident` 了，这边再 casefold 就是
    #: **一次比较两把尺子**：逐字节相同的一对反而对不上（任何 `hint` 带大写的 job 被凭空打回），
    #: 而只差大小写的反倒照样派生（M3 要堵的就是这一条）。两个方向都由这把尺子一起收掉。
    hint = _norm_ident(card.get("hint"))
    return facts, hint


def _verbatim(selector, observed_selector_text) -> bool:
    """这一根地址与观测原文里某一条**逐字相同**吗（★ Ruling 16 的「第 0 步」）。

    归一**只有**两下：压掉连续空白、去首尾 —— **别做别的归一**（归一越多，「逐字」这个保证越弱）。
    大小写 / 撇号那一套是 `_norm_text` 的活（比**文字**用的），在**地址**这一格**不算逐字**。
    """
    want = re.sub(r"\s+", " ", str(selector or "")).strip()
    if not want:
        return False
    said = observed_selector_text if isinstance(observed_selector_text, (list, tuple)) \
        else [observed_selector_text]
    return any(want == re.sub(r"\s+", " ", str(one or "")).strip() for one in said)


def address_basis(selector, card, observed_selector_text) -> tuple:
    """这一根地址的**出处** → `(级别, 人话)`。

    ★ **第 0 步（Ruling 16）：逐字相等就是量到的** —— 与观测原文某一条逐字相同 ⇒ `OBSERVED`。
    它排在下面 `unparsed` 判定**之前**：产物用的**就是探路那一刻量到的那一根**时，
    判据自己读不懂 `:has(` 那类构造**不是产物的错**（「我读不懂」是判据的局限 ——
    真语料 47 份里 18 条正是这么被误阻断的，与「量到了不许当成量不到」同一条纪律）。

    三级（spec §3.2）：
      · `OBSERVED`    —— 每一条断言都能在原始观察里找到**同一个属性名 + 同一个值**；
      · `DERIVED`     —— 值观察过，但**属性名**只在同一来源的合法形式里二选一（今天只有 `hint` 这一族）；
      · `UNSUPPORTED` —— 有任何一条断言在原始观察里找不到依据。

    另有两条**直接 `UNSUPPORTED`** 的，它们不是「找不到依据」，是「根本没读懂 / 对不上」：
      · 任何一条 `("unparsed", …)`（★ C1：读不懂的构造不算量到过，见 `assertions_of`）；
      · 锚在原始观察里对上了、但**标签对不上**（★ I2：`div[name=…]` 对观测 `input[name=…]`，
        重放会静默点到另一个元素，见 `_observed_anchor_tags`）。

    ⚠️ **文字只证明文字存在**：`nearby_text` / `text` / `label` 只能支持**文字锚**那一类断言
    （产物重放本来就认它），**不能**支持任何属性断言 —— 评审给的反例正是这一条：
    `nearby_text: "Choose your preferred plan"` ⇒ `input[name="preferred_plan"]`
    里的 `name` 从没观察过，值也是编的。
    ⚠️ 返回的「人话」是给**模型和人**读的（进 `Violation.message`）——说清缺哪一条依据。
    """
    #: ★ 第 0 步（见 docstring）。⚠️ **不重新打开洞**：编的地址（`header a` 那类）**不在卡里**
    #: ⇒ 逐字不等 ⇒ 照走 unparsed/断言那一套 ⇒ 仍被拦；nitrous 那根脆链**在**卡里 ⇒ 逐字命中、
    #: 进 `kept` ⇒ 仍落**第一格**（卡上没锚、也没有不脆的候选）⇒ 照旧 `judge-fragile-no-basis`；
    #: 「把卡上那根原样抄回来」算过 —— 那**正是对的**（用探路量到的那根地址），卡又来自冻结那份。
    if _verbatim(selector, observed_selector_text):
        return OBSERVED, "**这就是探路那一刻量到的那一根**（与观测原文逐字相同）。"
    asserts = assertions_of(selector)
    if not asserts:
        return OBSERVED, "这条地址**只说了位置、没主张任何身份**（位置不算断言）。"
    # 读不懂的构造**一律不算量到过** —— 这一条要在看 `facts` 之前（观测侧写了同一串也一样：
    # 判据没法拿一个它读不懂的构造去比）。fail-closed 的方向就落在这一句上。
    unreadable = [v for k, v in asserts if k == "unparsed"]
    if unreadable:
        return UNSUPPORTED, ("这条地址里有**判据读不懂的构造**：%s。读不懂就**不算量到过** —— "
                             "判据只认 `[属性=值]` / `#id`（或 `[id=值]`，两者是同一件事）/ "
                             "`.class` / 光标签 / 文字锚这几样（`:nth-of-type(…)` / `:nth-child(…)` "
                             "是位置、不算断言，那几个照写）。"
                             % "、".join("`%s`" % (v or "（空地址）") for v in unreadable[:4]))
    facts, hint = _observed_facts_and_hint(card, observed_selector_text)
    obs_tags = _observed_anchor_tags(observed_selector_text)
    cand_tags = _anchor_tags(str(selector or "").strip())
    unknown, mistag, derived = [], [], []
    for one in asserts:
        if one in facts:
            # 锚对上了、**标签还要对得上**（I2，见 `_observed_anchor_tags`）：重放是整串
            # `Element.matches()`，标签换一个就会静默点到另一个元素。两边都写了标签才比。
            want, got = obs_tags.get(one), cand_tags.get(one)
            if want and got and got not in want:
                mistag.append((one, got, sorted(want)))
            continue
        # `hint` 那条线索**同时**管两样形状：`("id", <值>)` 与 `("attr", "name=<值>")`
        # （`hint` = `el.name || el.id`，两个属性都有可能）—— 两条都要能对上。
        # ⚠️ 属性断言的值是 `assertions_of` 拼出来的 `属性名=值`，比之前先把 `name=` 摘掉：
        # 不摘的话这一支只有 `#X` 那一半走得通，`[name=X]` 那一半会掉进「没依据」。
        value = str(one[1])
        if one[0] == "attr" and value.startswith("name="):
            value = value[len("name="):]
        # `hint` 那一族：值对得上 ⇒ 降一档（属性名二选一），不是「没依据」
        if hint and value == hint and one[0] in ("id", "attr"):
            if one[0] == "attr" and not str(one[1]).startswith("name="):
                unknown.append(one)
            else:
                derived.append(one)
            continue
        if one[1] == hint and one[0] == "id":
            derived.append(one)
            continue
        unknown.append(one)
    if unknown:
        return UNSUPPORTED, ("这条地址里有**原始观察里没有的东西**：%s。"
                             "探路那一刻量到的只有：%s。"
                             % ("、".join("`%s`" % v for _, v in unknown[:4]),
                                "、".join("`%s`" % f for f in sorted(
                                    "%s:%s" % (k, v) for k, v in facts)[:6]) or "**什么都没有**"))
    if mistag:
        return UNSUPPORTED, ("这条地址里的**元素类型对不上原始观察**：%s。"
                             "重放是按整串 `Element.matches()` 找元素的 —— 标签换一个就会"
                             "**静默点到另一个元素**；照观测到的那个标签写，或者把标签去掉、"
                             "只留那个沾着卡的锚。"
                             % "、".join("`%s`（写的是 `%s`、观察里是 %s）"
                                         % (v, got, " / ".join("`%s`" % w for w in want))
                                         for v, got, want in mistag[:4]))
    if derived:
        return DERIVED, ("这条地址里的值是观察到的，但**属性名是二选一**（%s）："
                         "工具当时把 `name` 与 `id` 合并成一个 `hint` 报了（cdp 那边 `el.name || el.id`），"
                         "所以 `[name=…]` 与 `#…` 哪个是真的**没人验过**。"
                         % "、".join("`%s`" % v for _, v in derived[:4]))
    return OBSERVED, "每一条都回原始观察里对上了。"


def _alias_names() -> set[str]:
    """骨架自己那张别名表（`_ALIASES`）里的名字 —— 键与值都算。

    ⚠️ **不在这里另抄一份表**：别名表在这个仓里**只有一份**，它就在骨架源码里
    （`agent/template.py` 的 `SKELETON` 里那一格；产物里那份是它的渲染副本）。
    解析不出来（骨架被人改散了）⇒ **空集** —— 那是「这条规则没有真相源可依」，
    不是「键名都对上了」（有损可以说清，但不许编）。
    """
    from agent import template                 # 局部 import：避免与 template 循环引用

    try:
        tree = ast.parse(SKELETON_PLACEHOLDER.sub("None", template.SKELETON.template))
    except SyntaxError:
        return set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_ALIASES" for t in node.targets):
            continue
        try:
            table = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            return set()
        names: set[str] = set()
        for key, values in (table or {}).items():
            names.add(str(key))
            names.update(str(v) for v in (values or ()))
        return names
    return set()


def _rule_a(src: str) -> list[Violation]:
    """A 严格闸：`strict` 发出去了，却没有「这一版部署**不认**这个开关」的退路。

    两次真站实测（两次都长在这一条上）：
      · **mapleporchdaily**（2026-09-28）：逐字在 `forms-222/sites/mapleporchdaily.v2.py:1584-1587`
        ——「带 `--strict` 时**每一步 form 都失败**（4 个选择器、命中数都是 1，一律
        `scroll mouse wheel failed: element not found`；不带就全成）」。那份手改的产物把
        strict 关掉、并把理由写在了那一行上；
      · **版本不齐那一头**：部署的 `common.py` 不认识 `--strict` / `--expect-label` 时，
        这一下 `TypeError` 会把**整个产物**炸掉 —— 而 `run()` 的 except 当时不落 trace，
        于是自测把「产物死了」读成「走完了 10 步、0 跳过」（骨架里那段注释就为它写的，
        `agent/template.py` 的 `_perform` → `except TypeError`）。这是本项目最贵的那类谎。
    ⇒ 判据：**`strict: True` 与它的退路必须成对出现**。

    判据的边界（什么形状**不挂**）—— 这是**从探针收窄**的那一条（探针 `artifact_lint.py:109-110`
    写的是「源码里出现 `"strict": true` 就报」，那会把本骨架自己渲染的**每一份**产物都打回，
    连 `fixtures/reference_site.py` 一起 ⇒ 挂上去这个闸永远红）：
      · 本骨架那份写法（`"strict": True` 与 `except TypeError` 成对）**不挂**；
      · 压根没发 `strict` 的产物**不挂**（它没碰这件事）。
    ⚠️ **这一条没抓到的那一半**（写在明处，不装作抓到了）：mapleporchdaily 那种
    「严格闸在**这一站**上每一步都失败」取决于真页面（4 个选择器命中数都是 1、却一律
    element not found），**产物源码里看不出来** —— 那不是静态能判的事，得在真站上量
    （自测那一步就是量它的地方）。能静态钉住的只有「发出去却没退路」这一半。
    """
    out: list[Violation] = []
    hit = STRICT_TRUE_RE.search(src)
    if hit is None or STRICT_GUARD_MARK in src:
        return out
    line = _line_at(src, hit.start())
    out.append(Violation(
        line=line, code="judge-strict-unguarded",
        message=("这份产物把**严格闸**发给了填表命令，却没写「这一版部署不认这个开关」的退路："
                 "那一下会把整份脚本炸掉（自测会把它读成「走完了 N 步、0 跳过」）；"
                 "而真站上还有另一种样子 —— 带严格闸时**每一步都填不进**"
                 "（mapleporchdaily 实测：选择器命中数都是 1，却一律「element not found」）。"
                 "照骨架那份写法补上退路（抓到不认识这个开关时退回不消歧那条老路、"
                 "并**大声说**）；或者本站量过它不适用时，把结论与理由写在那一行上。"),
        snippet=_snippet(_line_text(src, line))))
    return out


def _rule_e(states: list, src: str) -> list[Violation]:
    """E 无门状态：`when=None` 的那一组里，有一步点击**只按页面上的文字**认元素。

    真站依据：交接《骨架还缺什么》§6 与探针 `artifact_lint.py:14`（E 条）——
    「无条件执行：广告落到别的页会乱点」。这一条**没有**一次单独的站点失败在它名下，
    它是从 georgiapower / travelandleisure 那两趟「判据错了 ⇒ 整组静默做错」里推出来的
    同类形状；这一点如实写在这里，不假装它是实测。

    ⚠️ 边界就是这条规则的**全部内容**（用户 2026-09-29 明确过：`when=None` **本身不是错**，
    探针那种「见到 `when=None` 就报」会把每一份正常产物都打回）：
      · **不挂**：旁枝起点那类**有意**不设门的一组（`browser_agent._drop_incidental_start_when`），
        以及参考产物里第三组那种写法 —— 它们的步骤都**按选择器**认元素，落到别的页上
        最坏是当场「没找到」（出声，不静默），不会点错东西；
      · **挂**：无门的一组里，有一步点击只有文字/角色可依（没有选择器）—— 落到别的页上
        它照样能找到「碰巧写着同一句话」的那个东西点下去，而日志里看不出异常。
    ⇒ 钉的是**会乱点的那个形状**，不是「不是起点就不许 `when=None`」——后者会把
    `fixtures/reference_site.py` 的第三组（作者故意不设门）也打回，而它是合法样例。
    （所以不按「第几组」判：第一个组里按文字乱点的，照样挂。）
    """
    out: list[Violation] = []
    for state in states:
        if not isinstance(state, dict) or state.get("when"):
            continue                             # 有门的归 C / D / H 管
        for step in (state.get("steps") or []):
            if not isinstance(step, dict) or (step.get("action") or "") != "click":
                continue
            if _selectors(step.get("target")):
                continue                         # 这一下有选择器可依 ⇒ 最坏是「没找到」
            name = str(state.get("name") or "?")
            out.append(Violation(
                line=_line_of(src, name), code="judge-ungated-click",
                message=("组「%s」**没有判据**（落在哪一页都会做），而它要点的那个元素**只按"
                         "页面上的文字认**、没有地址（选择器）可依 —— 广告/推广落到别的页时，"
                         "它会照样找到「碰巧写着同一句话」的那个东西点下去，而日志里看不出"
                         "任何异常。给这一步补一条**选择器**；或者给这一组补一条**这一屏独有的**"
                         "正文判据。" % name),
                snippet=_snippet('"name": %r' % name)))
            break                                # 一组一条，别刷屏
    return out


def _rule_f(states: list, src: str) -> list[Violation]:
    """F 缺跟新 tab：产物里**有点击**，却没有「点完跟一下新开的那一页」。

    真站实测（`docs/缺陷单-2026-09-28-产物缺跟随新tab.md` 的正题，georgiapower 那趟）：
    CTA 是 `<a target="_blank">`，排程页开在**新 tab** 里（弹窗 **2.26 秒**才出现、
    先以空地址出现、4.3 秒才有地址）—— 而脚本一直在读旧页 ⇒ 25 步里 23 步被静默跳过，
    日志里每一步照样写着「点上了」。修法就是骨架里那个 `_follow_new_tab`
    （`agent/template.py` 里方法名与调用都在，产物这一侧一个字都不用改）。
    ⇒ 判据：产物里出现点击、而这个方法不在 ⇒ 它多半是从**旧骨架**渲染出来的。

    边界（什么形状**不挂**）：产物里**一次点击都没有**的不挂 —— 没有「新开的那一页」要跟，
    报出来是噪音。
    """
    clicks = [(state, step) for state in states if isinstance(state, dict)
              for step in (state.get("steps") or [])
              if isinstance(step, dict) and (step.get("action") or "") == "click"]
    if not clicks or FOLLOW_NEW_TAB_MARK in src:
        return []
    first_state, first_step = clicks[0]
    target = first_step.get("target") or {}
    needles = _selectors(target) + [str(target.get("text") or ""),
                                    str(first_state.get("name") or "")]
    line = _line_of_any(src, *needles) or _line_of(src, "click") or 1
    return [Violation(
        line=line, code="judge-no-follow-tab",
        message=("这份产物里有**点击**，却没有「点完跟一下新开的那一页」（骨架里那个跟新 tab"
                 "的方法不在）—— 点 CTA 开出新 tab 时，目标页在新 tab 里、脚本却一直在读旧页，"
                 "整组步骤会被静默跳过，而每一步日志照样写着「点上了」（真站实测：新 tab"
                 "2.26 秒才出现、先空地址、4.3 秒才有地址）。这份产物多半是从**旧骨架**"
                 "渲染的 —— 用当前骨架重渲一版。"),
        snippet=_snippet(_line_text(src, line)))]


#: 名字尾巴上那个去重后缀（`street-2` / `field-3`）—— 它不是名字的一部分。
_DEDUPE_SUFFIX = re.compile(r"-\d+$")


def _named_after_its_own_label(source: str, fill: dict) -> bool:
    """这个名字是不是**这一格自己的标签**蛇形化来的（含截断与 `-2` 那类去重后缀）。

    ★ 2026-10-12（真事 `job-7c0cb8a45c25` · nitrouscompetitions.com/auth）：
    `Address line 1` → `address_line_1`、`I confirm I am 18 or over…` →
    `i_confirm_i_am_18_or_over_and_i_accept_t` —— 这两格只因为**标签里本来就有数字**
    （`1` / `18`）就中了下面那条「带数字串 = 机器起的名」，连着三轮把整单卡停。

    而 `AUTONAME` 那条要抓的是 `textfield_173838` 那种**与页面文字毫无关系**的机器名；
    标签名不是那一类：运营的 form-file 拿 `address_line_1` 当列名**完全合理**，
    而「语义名 vs 资料列名」那种错位，这条规则的文档自己就写着**判不了**
    （没有资料库键的真相源）。⇒ 判据是**量得到的**：名字与这一格记下来的 `label`
    蛇形化之后对得上（截断与去重后缀都算对得上），就不报。

    ⚠️ 真阳性一个都不松：名字与标签对不上的（`textfield_173838` + 标签「ZIP code」）照旧报。
    """
    from agent import browser_agent as ba          # 局部 import：与 _rule_g 同一条路

    label = str((fill or {}).get("label") or "")
    if not label:
        return False
    slug = ba._snake(label)
    if not slug:
        return False
    stem = _DEDUPE_SUFFIX.sub("", str(source or ""))
    if not stem:
        return False
    #: 两个方向都算：名字是标签的前缀（标签被截断）或标签是名字的前缀（名字被截断）。
    return stem == slug or slug.startswith(stem) or stem.startswith(slug)


def _rule_g(fills, src: str, observed=None) -> list[Violation]:
    """G 资料键名：`FILLS[*].source` **认不出语义**，形状也**不像运营资料的列名**。

    真站实测（两次，都是「键名对不上 ⇒ 静默掉进随机池」这一个病）：
      · `docs/生成脚本-取值缺陷-2026-09-17.md`：`source: 'postcode'` 取不到生产数据里的
        `postal_code` ⇒ 美国站被填了英国邮编 `SW1A 1AA`（截图在 failDiag 里）；
      · `docs/交接-2026-09-29-骨架还缺什么-八条.md` §0 的 mapleporchdaily 那一行：
        `phone` vs `phone_number` ⇒ 被填了英国号、整单提交被拒。

    ⚠️ **真相源：仓里没有**。「运营那份 form-file 有哪些键」这件事，逐字的证据是
    `docs/生成脚本-取值缺陷-2026-09-17.md` 自己写的那句「生产 form-file 的实际键名……
    **我没有那份数据**」；探针里那张 `LIB_KEYS`（`artifact_lint.py:31-34`）是 09-28 从线上
    `formMessage` **手抄**的，那是**第二份真相源**，不许抄进这里（抄了就等于把一张会过期的
    手抄表当规格）。
    ⇒ 退而求其次，只报**两边都说不清**的那种：名字既不是骨架认得出的语义名
    （`browser_agent._kind_by_words` —— 骨架**唯一**一处说「这格是什么」的口子），
    也不在骨架那张别名表（`_ALIASES`）里，而且形状是**机器起的名**（带数字串）。
    ⚠️ **局限，写在明处**：`phone` vs `phone_number` 这种「语义名 vs 资料列名」的错位，
    这条**抓不到**（没有资料库键的真相源）。那件事现在由 `template._ALIASES` 在**运行期**兜
    —— 09-28 那次就是它兜的。这条抓它兜不住的那些：框架自造名（gowizard 那个 ZIP 框的
    `textfield_173838` ⇒ 运营的 `zip`/`postcode` 一个都对不上，用户 2026-09-17 在真窗口上
    问的「是不是资料没对齐」就是它）。

    边界（什么形状**不挂**），四格：
      · 名字里**带着语义词**的（`postcode` / `email` / `phone` / `zip`，以及 `state2` 这种
        「语义词 + 数字」）—— `_kind_by_words` 认得出就不报：这条只判「认不认得出语义」，
        **不追**「这一格在真资料里取不取得到」（那是运行期的事）；
      · 别名表里的名字（`username` / `first_name` …）；
      · 形状像**人写的列名**的（通篇是词、不带数字，比如 `age` / `task_id` / `company_name`）；
      · **探索期占位名**（`field` / `field-2`）—— 理由是探针自己写下的那条：它**本来就不该**
        出现在资料库里，报它是**误诊**。
    ⚠️ 这几格是**量过**的（不是拍的），写在明处免得读的人以为它抓全了：带数字的名字里，
    含 `_RANDOM_HINTS` 那些词的（`zip_2` / `state2` / `email2` / `phone1`）放过，
    不带的（`age-2` / `full_name-2` / `textfield_173838`）报 —— 后者多半也真取不到资料，
    但「`-2` 那种去重后缀该不该写进 `source`」是另一件事，不在这条规则里判（不装作抓到了）。

    ★ 2026-10-15 Task 7（spec §3.1 第二族）：**收场那半句看卡说话**（有卡 / 没卡两支，
    见 `_autoname_say`）—— 老那句「回到探路那侧把这一格的语义认出来」与
    `judge-fragile-selectors` 是**同一条病**（模型打不开页面）。
    ⚠️ `observed`（探路那一刻的原始观察，**冻结的那一份**）在这一条里只用来取**卡**；
    不传 ⇒ 尾句仍是老那一个，与从前**逐字节一致**（老 job / 老调用点不受影响）。
    """
    from agent import browser_agent as ba      # 局部 import：语义词表的真相源在它那儿

    out: list[Violation] = []
    if not isinstance(fills, dict):
        return out
    alias = _alias_names()
    for key, fill in fills.items():
        source = str((fill or {}).get("source") or "")
        if not source or PLACEHOLDER_FILL.match(source):
            continue
        if ba._kind_by_words(source) or source in alias:
            continue
        if _named_after_its_own_label(source, fill):
            continue                             # 标签蛇形化来的名字 —— 不是框架自造名（见那个函数）
        if not AUTONAME.search(source):
            continue                             # 人写的列名形状（`username` 那种）—— 不报
        if observed is None:
            #: ── 没传 observed：老路，尾句**一个字都不改**（老 job / 老调用点照旧）──────
            message = _AUTONAME_HEAD % (key, source) + _AUTONAME_OLD_TAIL
        else:
            from agent import fix              # 局部 import：与 `_observed_card` 同一条理由
            #: ⚠️ 与 `_rule_i` 的 fills 那一支**同一个取卡方式**（`lint.py` 里那一处就在
            #: `judge-fragile-no-basis` 上面）：冻结那份里按 `fills` 的**键**找 ——
            #: 键就是这一格唯一的名字，模型换不掉，而 `FILLS` 这一支没有「步序」那个把手
            #: （`fix.find_observed` 只认 states）。**不许**从 `src` 取卡：`src` 是待检查的
            #: 那一份，拿它当证据就是循环证明（模型编一张卡写进产物，判据再读出来）。
            frozen_fill = ((observed or {}).get("fills") or {}).get(key)
            message = _autoname_say(key, source, fix.card_of((frozen_fill or {}).get("target")))
        out.append(Violation(
            line=_line_of(src, source), code="judge-source-autoname",
            message=message, snippet=_snippet(source)))
    return out


def _fragile(selector) -> bool:
    """这条选择器是不是「换次构建就漂」（★ 与骨架**同一份判据**）。

    三族都定死在 `agent/template.py`：框架渲染序号 id（`_GENERATED_ID`）、以 `-数字` 结尾的
    自造 id（`_RENDER_INDEX_ID`）、跳数 > `_HOPS_LOW` 的 nth 链。**不在这里另写一份模式** ——
    两处对不上就会出现「评级说它脆、闸说它没事」那种不一致，而它正是要躲的东西。
    """
    from agent import template

    text = str(selector or "")
    return bool(template._GENERATED_ID.match(text) or template._RENDER_INDEX_ID.match(text)
                or len(template._HOPS.findall(text)) > template._HOPS_LOW)


def _has_text_anchor(target: dict) -> bool:
    """这一格除了选择器之外，有没有**文字/标签**那根锚。

    ★ 2026-09-30（`zekear` 那趟现场）：那个「i agree」勾选框**页面上没有 name / id**，
    探路只留下一条 9 跳 nth 链 —— 打回一次、改稿那双手也补不出稳的（真没有），于是
    **draft ↔ lint 来回打转**（每一圈一次模型调用）。而它**有 label**（`i agree`）——
    产物本来就能按 label 找得到。
    ⇒ 「一条稳的都没有」这句话里的**稳**，本来就不止 CSS 那一条路（`_selector_rank`
    把脆的排最后，正是因为它只是**最后一条路**）：有文字/标签锚的格不算「只剩一根漂的线」。

    ★ 2026-10-13：「**身上没字、标签在隔壁**」的控件也算有锚 —— `nearby_text` **且** `role`。
    现场是 compareinsulation 那排 `role=radio` 的 16×16 圆点（`text` 空、id 是
    `#option-<时间戳>`、标签只写在旁边）。⚠️ 那一族的 id 按同一天的裁定**不进这道硬闸**
    （`_fragile` 只剩渲染序号 id 与长 nth 链两族）—— 这条收的是**形状同族**的那些：
    只有渲染序号 id / 长链可依、而标签写在隔壁的无名控件。为什么不放宽成「nearby_text 非空就算」：
    仓里实测过它可能是**邻居的字**（隔壁 State 下拉、邻格的 `Address line 2`）——
    `role` 是那道分辨器；重放那一跳（产物 `_relocate` → `_intent_score`）认的也正是
    「role 先筛、标签再分」。缺一个就照旧打回。
    """
    t = target or {}
    if str(t.get("label") or "").strip() or str(t.get("text") or "").strip():
        return True
    near = [x for x in (t.get("nearby_text") or []) if str(x).strip()]
    return bool(near and str(t.get("role") or "").strip())


def _as_int(value) -> Optional[int]:
    """读成整数；**读不出来给 `None`**（不是 `0` —— 「读不懂」与「第 0 步」在判据里是两件事）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fp_text(card_fp) -> str:
    """指纹 → **能比、能进 state** 的那个字符串（`None` 与「空卡」不当同一件事）。

    ⚠️ 为什么不能写成 `str(card_fp or "")`：空卡的指纹是**空元组** `()`，它**是假的**
    ⇒ `or ""` 把它变成 `""`，而 `no_basis_slots` 存进 state 的是 `str(())` = `"()"` ——
    两边对不上，于是「卡上什么都没有」那一格的接受**永远不会生效**：人点了头、屏幕上说
    「记下了」、判据照报（正是这一刀要治的那种「屏幕上一切正常」）。
    """
    return "" if card_fp is None else str(card_fp)


def _acceptance_holds(accepted, state_name, index, selector, card_fp) -> bool:
    """这一处现在还算被接受吗 —— **四元组逐项比**（spec §3.5「失效」）。

    ⚠️ 四样任一变了就 `False`：那根地址被改稿换了 / 原始观察重渲了（`card_fp` 变）/
    步序挪了 / 换了别的组。**失效 = 回到闸上重新问**，不是沿用上一次那声「行」。
    ⚠️ 它住 `lint`（判据这一侧），不住 `graph`：判据要用它，而 `graph` 已经 import 了
    `lint`，反过来 import 就是循环引用。人在闸上那句话怎么**变成**一条 `accepted`，
    那一半在 `graph._accept_from_note`（唯一入口）。
    ⚠️ 读不懂的那一条（不是 dict / 步序读不成数）**一律不算接受**：这一侧的方向只有一个 ——
    判「不成立」（⇒ 照旧报 ⇒ 回到闸上重新问）。这里**不许抛**（判据每一趟都走它，
    在这儿抛就是整趟运行死在一条手改的旧账上；文件里同一条纪律见 `check_unverified`）。
    """
    want = _as_int(index)
    if want is None:
        return False
    for one in (accepted or []):
        if not isinstance(one, dict):
            continue
        if (str(one.get("state")) != str(state_name)
                or _as_int(one.get("index") or 0) != want):
            continue
        return (str(one.get("selector") or "") == str(selector or "")
                and _fp_text(one.get("card")) == _fp_text(card_fp))
    return False


def _no_basis_sel(card, sels, kept) -> Optional[str]:
    """这一步**是不是**「无据可依」→ 是就回**要绑的那根地址**，不是回 `None`。**纯谓词**。

    这一支**只有一个定义**，两个调用方是同一件事的两半（★ 2026-10-15 Task 6）：
      · `_rule_i`：报 `judge-fragile-no-basis` 之前问一句；
      · `no_basis_slots`：把「现在有哪几处等着被接受、各绑哪一根」写下来（人话里的 handle、
        与 `addr_accepted` 的四元组**从这里出**）。
    两处各写一遍的话，「人接受过的那一步」与「判据还在报的那一步」会**各说各的**
    —— 接受完还在报 / 报着却接受不了 —— 而屏幕上两边看起来都正常。

    ⚠️ **它不看 `accepted`**（★ 2026-10-15 修复轮 1）：原来它把「不是这一格」与「人接受过」
    混成同一个 `None`，于是 `_rule_i` 里那句 `if ... is not None` 在**已接受**这条路上
    **不 `continue`**，控制流掉进下面那几条（`all(one == DERIVED …)` → `not all(_fragile …)`
    → `_has_text_anchor`），最后被 **`judge-fragile-selectors`** 重新拦住（实测：空卡 + 产物那根
    脆链是 `OBSERVED` + 那个 target 没有语义锚 ⇒ 接受完违规表里换成这一条）⇒ 人话里承诺的
    第 ③ 条路对这类形状是**死胡同**（那一步同时从 `addr_candidates` 里消失，再说一遍无效 ⇒
    一路爬到 `lint_cap`）。判定「接受生效」是 `_accepted` 那一支的事（它管**整步跳过**）。
    """
    if not (_card_has_nothing(card, kept) or not kept):
        return None
    return kept[0] if kept else sels[0]


def _accepted(card, sels, kept, accepted, state_name, index) -> bool:
    """这一步无据可依，**而且人接受过、四元组一项没变** ⇒ `True`（★ 2026-10-15 Task 6）。

    ⚠️ `True` 的含义是「**这一步不再被任何一条判据拦**」——调用方一律**整步跳过**，
    不只是不报 `judge-fragile-no-basis` 那一条（理由见 `_no_basis_sel` 里那段实测）。
    ⚠️ 四元组任一变了（地址被改稿换了 / 原始观察重渲了 / 步序挪了 / 换了别的组）⇒ `False`
    ⇒ 照旧报、也照旧进 `no_basis_slots` 那份待接受清单（「失效 = 回到闸上重新问」）。
    """
    sel = _no_basis_sel(card, sels, kept)
    if sel is None:
        return False
    from agent import fix                  # 局部 import：与 `_observed_card` 同一条理由
    return _acceptance_holds(accepted, state_name, index, sel, fix.card_fingerprint(card))


def _rule_i(states: list, fills, src: str, observed=None, accepted=None) -> list[Violation]:
    """I 脆地址：这一格的候选**全是**「随时会漂」的那种，没有一条稳的可依。

    真站实测（缺陷单《产物判据跨元素拼接》§四.1，2026-09-28）：georgiapower 同一天量到的
    对照 —— 探路记的 `#input-167` → 今天实际 `#input-163`、`#combobox-input-182` →
    `#combobox-input-168`（`agent/template.py` 的 `_RENDER_INDEX_ID` 上面那段注释记的正是
    这次实测）；而 `input[name="city"]` / `input[aria-label="Search for Address"]` **全对上了**。
    另有实测：9 跳那条 nth 链**换个分辨率全挂**。

    ⚠️ 边界（从探针**收窄**的第二条）：探针 `artifact_lint.py:103-108` 是「出现就报」，
    而骨架**故意留着**这些脆地址当**最后一条路**（`_selector_rank` 把它们排在最后：
    「页面真没给别的地址时，它是唯一够得着的路 —— 实测有一格 `telephoneNumber` 就只有它一个候选」）。
    ⇒ 判据只在**一条稳的都没有**时会挂：那样这一格就只剩一根随时会漂的线，
    换一版构建 / 换个分辨率 ⇒ 这一步「页面上没找到」⇒ 整组静默跳过。
    有稳候选的不挂（脆的排最后，本来就不会先用它）。

    ★ 2026-10-15（spec §3.1）干了三件事：
      ① **先验出处**：拿 `observed` 里那一步的卡（`fix.find_observed`）把候选分成
         Observed / Derived / Unsupported，**Unsupported 的不算候选** ——
         这一步就是「编一根长得稳的过闸」不再有好处的全部理由；
      ② 全靠 Derived 撑着 ⇒ **不报**（放行但按「未证实」记，`check_unverified` 管那一半）；
      ③ 一根采信的都不剩 ⇒ 报**新码** `judge-fragile-no-basis`，话换成三条正路
         （老话「给它补一条稳的」在那一刻**没有正确答案**，只能逼出编的）。
    ⚠️ `observed` 只该影响**出处那一件事**：去重照旧按 `(组名, 步序)`（与老路同一把尺子）、
    有语义锚照旧不挂 —— 这两条在两支里都留着。
    不传 `observed` ⇒ 走老路（与从前逐字节一致）。

    ★ 2026-10-15 Task 6：`accepted` = **人点头接受过的那几处地址**（spec §3.5）。它只干一件事：
    「这一步**无据可依**、但人接受过、且四元组一项没变」⇒ **整步跳过**（这一条的每一格都不再问）。
    ⚠️ 必须是**整步**：落在无据可依那一格的那一步同时也在下面几条的射程里（真站实测那个形状：
    空卡 + 产物那根脆链是 `OBSERVED` + target 没有语义锚 ⇒ 只「不报 no-basis」的话，它会被
    `judge-fragile-selectors` 接着拦住 ⇒ 那句「接受」等于没说）。见 `_no_basis_sel` 里那段实测。
    ⚠️ 它**不放松**别的任何一格（无据可依这个判断本身一个字没动）；四元组任一变了就回到报。
    ⚠️ **`FILLS` 那一支没有它**：那一格的把手是**键**、不是 (组名, 步序)，没有对应的口子
    （`_fill_no_basis_say` 因此只写两条正路 —— 写上去就是一句做不到的话）。
    不传 `accepted` ⇒ 与从前逐字节一致。
    """
    out: list[Violation] = []
    seen: set = set()
    for state in states:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "?")
        for index, step in enumerate(state.get("steps") or []):
            if not isinstance(step, dict):
                continue
            target = step.get("target") or {}
            sels = _selectors(target)
            if not sels:
                continue
            handle = "%s#%d" % (name, index)
            if handle in seen:
                continue
            if observed is not None:
                card = _observed_card(observed, name, index, target)
                kept, levels = [], []
                for one in sels:
                    level, why = address_basis(
                        one, card or {}, (card or {}).get("selectors") or [])
                    levels.append(level)
                    if level != UNSUPPORTED:
                        kept.append(one)
                #: ★ 第一格（spec §3.1 第③步第一条）：判「有没有答案」**要看卡**，
                #: 不能只看产物那几根。真现场：nitrous 那一步的卡是空的，而产物那根脆链
                #: 与观测原文**逐字相同** ⇒ 它是 `OBSERVED`、是「采信的候选」，
                #: 只按候选判会掉进「有据但没用上」⇒ 报「从这几样里挑一条稳的」，
                #: 可**那几样一样都没有** —— 那句人话自相矛盾，又回到逼人编。
                #: ★ 2026-10-15 Task 6：这一格与下面 ③ 那一格（**卡上有锚、候选全没依据**）
                #: **合成一句** —— 两条身子逐字节相同（同一个 handle、同一句话、同一根地址），
                #: 而「人接受过 ⇒ 不报」必须**两处都问**（漏一处就成了「接受了还在报」，
                #: 屏幕上看不出来）。「算不算无据可依、要绑哪一根」因此**只有一处定义**：
                #: `_no_basis_sel`（`no_basis_slots` 那份待接受清单走的是同一支）。
                #: ★ 修复轮 1：接受**整步跳过**（`continue`）—— 落在这一格的那一步同时也在
                #: 别的判据的射程里（实测：`judge-fragile-selectors` 就在下面几行），
                #: 只「不报 no-basis」等于接受没生效（详见 `_no_basis_sel` 里那段实测）。
                #: ⚠️ 这里**不 `seen.add`**：`seen` 是「同一个 handle 只报一次」的账，
                #: 接受不是「报过」—— 同名重复组里那一步要按它自己的四元组判（与
                #: `no_basis_slots` 同一条时机）。
                if _accepted(card, sels, kept, accepted, name, index):
                    continue
                if _no_basis_sel(card, sels, kept) is not None:
                    seen.add(handle)
                    out.append(Violation(
                        line=_line_of_any(src, sels[0], sels[0][-24:], name),
                        code="judge-fragile-no-basis",
                        message=_no_basis_say(name, index, card),
                        snippet=_snippet(sels[0])))
                    continue
                if all(one == DERIVED for one in levels if one != UNSUPPORTED):
                    continue        # ② 全靠 Derived 撑着 ⇒ 放行（由 check_unverified 记一笔）
                if not all(_fragile(s) for s in kept):
                    continue        # 有 Observed 的不脆候选 ⇒ 过
                if _has_text_anchor(target):
                    continue        # 有语义锚 ⇒ 过（今天就这样，留）
                seen.add(handle)
                out.append(Violation(
                    line=_line_of_any(src, sels[0], sels[0][-24:], name),
                    code="judge-fragile-selectors",
                    message=_with_evidence_say(name, index, card),
                    snippet=_snippet(sels[0])))
                continue
            # ── 没传 observed：老路，一个字都不改 ─────────────────────────────
            if not all(_fragile(s) for s in sels):
                continue
            #: 有文字/标签锚 ⇒ 不挂（见 `_has_text_anchor`：那种格不是「只剩一根漂的线」）
            if _has_text_anchor(target):
                continue
            seen.add(handle)
            out.append(Violation(
                line=_line_of_any(src, sels[0], sels[0][-24:], name), code="judge-fragile-selectors",
                message=("组「%s」第 %d 步**只有一根随时会漂的地址**（框架自造 id / 超长"
                         "nth-of-type 链）：同一格重渲一次就换号（真站实测 `#input-167` →"
                         "`#input-163`），换了分辨率的长链还会整条挂 —— 到那时这一步就是"
                         "「页面上没找到」，整组步骤被静默跳过。给它补一条**稳的**地址"
                         "（`input[name=…]` / `input[aria-label=…]` 这类）。"
                         % (name, index + 1)),
                snippet=_snippet(sels[0])))
    for key, fill in (fills or {}).items():
        target = (fill or {}).get("target") or {}
        sels = _selectors(target)
        if not sels:
            continue
        handle = "FILLS:%s" % key
        if handle in seen:
            continue
        if observed is not None:
            from agent import fix             # 局部 import：与 `_observed_card` 同一条理由
            #: ⚠️ 这里**不许**用 `fix._literals(src)` 取卡 —— `src` 是**待检查的那一份**，
            #: 拿它当证据就是循环证明（那正是这一刀要堵的路）。冻结那份里按 `fills` 的
            #: 键找：**键就是这一格的唯一名字**，模型换不掉（`find_observed` 只认 states，
            #: fills 这一支没有「步序」这个把手）。
            frozen_fill = ((observed or {}).get("fills") or {}).get(key)
            card = fix.card_of((frozen_fill or {}).get("target"))
            kept, levels = [], []
            for one in sels:
                level, why = address_basis(
                    one, card or {}, (card or {}).get("selectors") or [])
                levels.append(level)
                if level != UNSUPPORTED:
                    kept.append(one)
            if _card_has_nothing(card, kept) or not kept:
                #: 与 states 那一支同一条判决、同一格顺序（卡上什么都没有 ⇒ 无据可依）
                seen.add(handle)
                out.append(Violation(
                    line=_line_of_any(src, sels[0], sels[0][-24:], key),
                    code="judge-fragile-no-basis",
                    message=_fill_no_basis_say(key, card),
                    snippet=_snippet(sels[0])))
                continue
            if all(one == DERIVED for one in levels if one != UNSUPPORTED):
                continue        # 全靠 Derived 撑着 ⇒ 放行（由 check_unverified 记一笔）
            if not all(_fragile(s) for s in kept):
                continue        # 有 Observed 的不脆候选 ⇒ 过（脆的排最后，本来就不会先用它）
            if _has_text_anchor(target) or str((fill or {}).get("label") or "").strip():
                continue        # 这一格自己写着名字 ⇒ 过（今天就这样，留）
            seen.add(handle)
            out.append(Violation(
                line=_line_of_any(src, sels[0], sels[0][-24:], key),
                code="judge-fragile-selectors",
                message=_fill_with_evidence_say(key, card),
                snippet=_snippet(sels[0])))
            continue
        # ── 没传 observed：老路，一个字都不改 ─────────────────────────────
        if not all(_fragile(s) for s in sels):
            continue
        if _has_text_anchor(target) or str((fill or {}).get("label") or "").strip():
            continue
        seen.add(handle)
        out.append(Violation(
            line=_line_of_any(src, sels[0], sels[0][-24:], key), code="judge-fragile-selectors",
            message=("字段「%s」**只有一根随时会漂的地址**（框架自造 id / 超长 nth-of-type 链）："
                     "重渲一次就换号（真站实测 `#input-167` → `#input-163`）⇒ 这一格会"
                     "「没找到」而填不进去，日志里只说没填成、看不出是地址漂了。"
                     "给它补一条**稳的**地址（`input[name=…]` / `input[aria-label=…]` 这类）。"
                     % key),
            snippet=_snippet(sels[0])))
    return out


def _observed_card(observed, state_name, index, target):
    """当前这一步 → **原件那张卡**（找不到给 `None`）。薄薄一层，把 `fix` 那边两件事接起来。

    ⚠️ 按 Ruling 8：`find_observed` 的返回值**一律按 falsy 判**（它可能给 `{}` 而不是 `None`
    —— 原件那一步本来就没有 target、而调用方这张卡也没有 selectors 时）。`{}` 与 `None`
    在「有没有证据」这件事上同义，写成 `is None` 会把「这一步压根没有地址」读成「量到了」。
    """
    if observed is None:
        return None
    from agent import fix                      # 局部 import：与 `_judge_violations` 同一条理由
    return fix.find_observed(observed, state_name, index, fix.card_of(target))


#: 卡上**说得出这一格是什么**的那几样（锚）。⚠️ **一份**，两处用：
#: `_card_has_nothing`（判地址有没有可依的）与 `_card_has_semantics`（判语义说得出说不出）。
#: 两处各写一遍的话，一边加了字段、另一边没加，屏幕上就会出现「有卡却说没卡」（或反过来），
#: 而两边各自看起来都正常。
_CARD_ANCHOR_KEYS = ("label", "aria_label", "hint", "placeholder", "nearby_text", "text")


def _card_has_nothing(card, kept) -> bool:
    """这一步**探路那一刻有没有可依的**：卡上没锚、也没有一根不脆的采信候选。

    ⚠️ 判「有没有答案」**要看卡**，不能只看产物那几根 —— 产物那根脆链与观测原文**逐字相同**时
    它是 `OBSERVED`（那是对的），可那**并不意味着「有更好的答案」**（spec §3.1 第③步第一条）。
    卡是 `None`（原件里找不到对应这一步）也走这一支：fail-closed。
    ★ 2026-10-15 修复轮 1（Minor #1）：卡**自己的 `selectors`** 也算「不脆的候选」那一侧 ——
    卡上那几根是 `tools/cdp` 的 `address()` **验过唯一**才记下来的。原来看漏了这一格，
    Ruling 13 一落地它立刻可达，而且会**对外说假话**：卡上明明有一根 `a[href='/comp']`，
    人话却说这一步「量不到更稳的地址」（模型也因此拿不到那条正路 —— 该说的是「把卡上那根
    拿来用」，`_with_evidence_say` 会把卡摆出来）。
    ⚠️ 这一条**不放松**任何东西：卡仍然来自冻结那份（模型注入不进来），被改写的那根地址照样
    要逐条断言过 `address_basis`；nitrous 那族（卡上只有那根脆链）与空卡那族照旧「无据可依」。

    ★ 2026-10-16 第二刀（spec §3.3）：末句多问一句 `assertions_of(s)` ——
    **零断言的地址（`li:nth-child(2n+1)` 那一族，纯位置）不算「有答案」**。
    理由是 `address_basis` 自己那句人话：「只说了位置、没主张任何身份」——
    什么都没主张的东西，证明不了任何事。⚠️ **不动 `_fragile`**：给它补 `:nth-child` 一族会连带把
    `ul li:nth-child(2) a[href="/x"]` 这种**带真锚**的地址也判脆（扩大打击面）。
    """
    if card and any(card.get(k) not in (None, "", [], {}) for k in _CARD_ANCHOR_KEYS):
        return False
    if card and any(not _fragile(s) for s in (card.get("selectors") or [])):
        return False          #: 卡上有一根验过唯一、又不在脆那一族的地址 ⇒ 有答案
    #: ★ 2026-10-16 第二刀（spec §3.3）：「采信的候选」得**说了点什么**才算数。
    #: 位置不是断言（`address_basis` 自己那张嘴就是这么说的：「只说了位置、没主张任何身份」）——
    #: 而原来它把这种零断言的地址**记成「有据」** ⇒ 空卡上成了「有答案」⇒ 判据自己给自己背书。
    #: ⚠️ 只在**这一格**收：卡上有锚 / 卡上有不脆候选那两条前置分支一个字不动
    #: （所以「卡上有锚 + 纯位置地址」照旧不报，重放按锚找回）。
    return not [s for s in (kept or []) if not _fragile(s) and assertions_of(s)]


def _machine_made_name(value) -> bool:
    """这个值（标签 / 提示 / 名字 / 旁边的文字）**本身长着框架自造名那个形状**吗。

    ★ 2026-10-15 修复轮 1（评审 Important）：`_card_has_semantics` 拿它把「根本不是语义证据」
    的那些**逐样滤掉** —— 这一格问的是「这几样说不说得清这一格在问什么」，而
    `label: 'textField-173838'`（页面没给标签时工具拿框架名兜的那个）一个字也说不出。

    ⚠️ 两把尺子都是**现成的**（`AUTONAME` / `ba._kind_by_words`，`_rule_g` 判 `source` 用的
    就是它们），两课也都是这一族**真站上踩过**的（`_named_after_its_own_label` 的现场）：
      · 数字是**语义词**的一部分（`Address line 1` / `Year of birth` / `zip2`）
        ⇒ `_kind_by_words` 认得出 ⇒ **不是**自造名；
      · 数字只是人写那句话里的一个词（`I confirm I am 18 or over…`，**有空格**）⇒ 也不是。
    ⇒ 自造名的形状只剩「**一个词 + 数字**」那一种（`textField-173838` / `textfield_173838` /
    `field-2` / `__BVID__408`）：换一次构建就换号，说不出这一格问什么。
    ⚠️ 判得**窄**：拿捏不准的一律**当语义证据用** —— 这一侧判错的代价是把「够得着」说成
    「够不着」（模型于是只能去请人；可惜，但不是假的）。
    """
    from agent import browser_agent as ba      # 局部 import：与 `_rule_g` 同一条路

    text = str(value or "").strip()
    if not text or not AUTONAME.search(text):
        return False                           # 没有数字 ⇒ 不长这个形状（`zip` / `ZIP code`）
    if ba._kind_by_words(text):
        return False                           # 数字长在语义词里：`Address line 1` 那一族
    return not re.search(r"\s", text)          # 有空格 ⇒ 人写的句子：`…18 or over…` 那一族


def _card_has_semantics(card) -> bool:
    """卡上有没有**说得出这一格是什么**的那几样（`_CARD_ANCHOR_KEYS` 里至少一样有值）。

    ★ 2026-10-15 Task 7（`judge-source-autoname` 那一族的收场话按它分两支）：
    有 ⇒ 「用这几样把这一格的语义判出来，再重渲」；没有 ⇒ 「判不出」，请人给一句。

    ★ 修复轮 1（评审 Important）：**逐样过一遍 `_machine_made_name`** —— 「有值」不等于
    「有语义证据」。真站那张卡（`_AUTONAME_MACHINE_CARD` 那个形状）上唯一有值的那样是
    `label: 'textField-173838'`（页面没给标签、工具拿框架名兜的）：照它「判语义」只能**编**一个
    `source` 出来，而编出来的名字**恰好过得了形状闸、静默放行** —— 正是这一刀要堵的那条路。
    ⇒ 滤完一样都不剩就**当没量到**（fail-closed；失败的方向不许反）。

    ⚠️ **不是** `_card_has_nothing` 那把尺子原样照搬（虽然锚那一半是**同一份表**）：
    那把尺子还认「卡上有一根不脆的候选」也算「有货」—— 那是判**地址**用的。这一格判的是
    **语义**：地址说明这一格**在哪**，不说明它**问什么**（`selectors` / `role` 因此都不算
    —— 说了「有」就等于让模型拿一根地址去猜它填什么，正是这一刀要治的那种「够不着」）。
    ⚠️ `nearby_text` 这类**列表逐条**判：一条语义都判不出就算是没有（一条说得出就算有）。
    ⚠️ 卡取不到时给的是 `{}`（`fix.card_of`），一样落在「没有」那一支：fail-closed ——
    **认不出 ⇒ 当「没量到」**。
    """
    for key in _CARD_ANCHOR_KEYS:
        value = (card or {}).get(key)
        for one in (value if isinstance(value, (list, tuple)) else [value]):
            text = str(one or "").strip()
            if text and not _machine_made_name(text):
                return True                    # 有一样说得出这一格问什么 ⇒ 够得着
    return False


def _card_facts_say(card) -> str:
    """卡上的东西**连值一起**说：`` `role=link` `` / `` `selectors=a[href='/comp']` ``。

    ⚠️ 只说键名（会印成「量到的只有 `role`、`selectors`」）**模型拿不到值，也就没法照它改**
    —— 这一刀治的正是「说了等于没说」（走这条路的人每一句都得是能落地的）。
    有多根时照原样、按顺序列出来（`、` 分隔），不做任何压缩或改写。
    """
    said = []
    for key in sorted(card or {}):
        value = (card or {}).get(key)
        if isinstance(value, (list, tuple)):
            value = "、".join(str(one) for one in value)
        said.append("`%s=%s`" % (key, value))
    return "、".join(said)


def _no_basis_say(name: str, index: int, card) -> str:
    """「无据可依」那一支的人话 —— **不说「补一条稳的」**（那一刻没有正确答案）。

    ★ 2026-10-15 修复轮 2（Ruling 14）：**看卡说话**（口径就是第一格那把 `_card_has_nothing`，
    不另造）——
      · 卡上**有货**（有锚，或有一根不脆的候选）⇒ **不许**先说「量不到更稳的地址」
        （下一句就把它打脸），改成「这一版的地址一条都不是量到的」+ **把卡摆出来** +
        「**先把卡上那根拿来用**」；三条正路退成「实在没有能用的」时的兜底。
      · 卡上**什么都没有** ⇒ 今天那两句**一个字都不动**（都有测试逐字节钉着）——
        那时「量不到更稳的地址」是**真话**（真没有更稳的），「补一条稳的」也**没有正确答案**。
    ⚠️ 路由**不动**：这一格仍然是 `judge-fragile-no-basis`（spec §3.1 第二格 ——
    「你这一版的地址无据可依」这个读法本来就对），换的只是**话**。
    ⚠️ 三条正路的每一条都必须是**真做得到**的（这一刀治的就是「说了做不到的话」）：
    ① 删掉 → `draft` 删一步是它能做的；② 请人 → 人在闸上说的那句话进 feedback；
    ③ 接受 → 落地在 Task 6（`no_basis_slots` + 人在闸上说「接受地址 X#N」），
    四元组任一变即失效。
    """
    if not _card_has_nothing(card, ()):
        return (
            "组「%s」第 %d 步：**这一版的地址一条都不是量到的**（编出来的地址没人验过 —— "
            "它会让「这一步还能不能重放」这件事在屏幕上看起来是好的）。"
            "**探路那一刻量到的是**：%s —— **先把卡上那根拿来用**；"
            "实在没有能用的，三条正路任选其一："
            "① 这一步若只是辅助动作（把某个东西滚进视口那类）⇒ **删掉它**；"
            "② **请人给一根**（在闸上说一句，人正看着那一页）；"
            "③ 如实**接受**（在闸上说「接受地址 %s#%d」—— 只有人能接受，且地址或证据一变就失效）。"
            % (name, index + 1, _card_facts_say(card), name, index))
    #: ── 卡上什么都没有：下面的原话**一个字都没动**（空卡 / 只有一根脆链两种形状各有其词）──
    return (
        "组「%s」第 %d 步：这一步探路那一刻**量不到更稳的地址** —— %s。"
        "**别编一根**：编出来的地址没人验过，而它会让「这一步还能不能重放」这件事"
        "在屏幕上看起来是好的。三条正路，任选其一："
        "① 这一步若只是辅助动作（把某个东西滚进视口那类）⇒ **删掉它**；"
        "② **请人给一根**（在闸上说一句，人正看着那一页）；"
        "③ 如实**接受**（在闸上说「接受地址 %s#%d」—— 只有人能接受，且地址或证据一变就失效）。"
        % (name, index + 1,
           "它身上没有名字、旁边没有字、也没有一条验过唯一的候选"
           if not card else
           "量到的只有 %s" % _card_facts_say(card),
           name, index))


def _with_evidence_say(name: str, index: int, card) -> str:
    """「有据但没用上」那一支 —— 老话 + **把卡摆到模型面前**（spec §3.3）。"""
    said = "、".join("%s=`%s`" % (k, v) for k, v in sorted((card or {}).items()))
    return ("组「%s」第 %d 步**只有一根随时会漂的地址**（框架自造 id / 超长 nth-of-type 链）："
            "同一格重渲一次就换号，换了分辨率的长链还会整条挂 —— 到那时这一步就是"
            "「页面上没找到」，整组步骤被静默跳过。"
            "**探路那一刻量到的是**：%s —— 从这几样里挑一条**稳的**地址"
            "（`input[name=…]` / `input[placeholder=…]` / 文字锚那几类）。"
            % (name, index + 1, said or "**什么都没有**"))


def _fill_no_basis_say(key: str, card) -> str:
    """「无据可依」那一支的**字段版**人话 —— 与 `_no_basis_say` 同一条立场，两处不同：

    · 说的是**字段**（`FILLS` 那一支没有「第 N 步」这个把手，键就是它的名字）；
    · **不写第 ③ 条「接受」** —— 那一条的落地（`no_basis_slots` / `_accept_from_note`）
      是按 `(组名, 步序)` 认的，**字段这一支没有对应的口子**：写一句做不到的话，
      正是这一刀要治的病（老话「给它补一条稳的」就是这么坏的）。
    ★ 2026-10-15 修复轮 2（Ruling 14）：与 `_no_basis_say` **同一个病、同一条改法、同一把
    「有没有货」的尺子**（`_card_has_nothing`）—— 卡上有货就不许说「量不到更稳的地址」
    （下一句会打脸），改成「先把卡上那根拿来用」。两条正路照旧只有两条。
    """
    if not _card_has_nothing(card, ()):
        return (
            "字段「%s」：**这一格的地址一条都不是量到的**（编出来的地址没人验过 —— "
            "它会让「这一格还能不能填上」这件事在屏幕上看起来是好的）。"
            "**探路那一刻量到的是**：%s —— **先把卡上那根拿来用**；"
            "实在没有能用的，两条正路任选其一："
            "① 这一格**本来就不该填**（页面没要求，或它已经在别处填过）⇒ **把这一步删掉**；"
            "② **请人给一根**（在闸上说一句，人正看着那一页）。"
            % (key, _card_facts_say(card)))
    #: ── 卡上什么都没有：下面的原话**一个字都没动** ──────────────────────────────────
    return (
        "字段「%s」：这一格探路那一刻**量不到更稳的地址** —— %s。"
        "**别编一根**：编出来的地址没人验过，而它会让「这一格还能不能填上」这件事"
        "在屏幕上看起来是好的。两条正路，任选其一："
        "① 这一格**本来就不该填**（页面没要求，或它已经在别处填过）⇒ **把这一步删掉**；"
        "② **请人给一根**（在闸上说一句，人正看着那一页）。"
        % (key, "它身上没有名字、旁边没有字、也没有一条验过唯一的候选"
           if not card else "量到的只有 %s" % _card_facts_say(card)))


def _fill_with_evidence_say(key: str, card) -> str:
    """「有据但没用上」那一支的**字段版** —— 老话 + **把卡摆到模型面前**（spec §3.3）。"""
    said = "、".join("%s=`%s`" % (k, v) for k, v in sorted((card or {}).items()))
    return ("字段「%s」**只有一根随时会漂的地址**（框架自造 id / 超长 nth-of-type 链）："
            "重渲一次就换号（真站实测 `#input-167` → `#input-163`）⇒ 这一格会"
            "「没找到」而填不进去，日志里只说没填成、看不出是地址漂了。"
            "**探路那一刻量到的是**：%s —— 从这几样里挑一条**稳的**地址"
            "（`input[name=…]` / `input[placeholder=…]` / 文字锚那几类）。"
            % (key, said or "**什么都没有**"))


#: `judge-source-autoname` 那一句人话的**前半句**（诊断）—— 两支共用，一个字都不许变：
#: 名字认不出语义、形状也不像运营资料的列名，后果是取不到资料、静默掉进随机池。
#: ⚠️ 换的只是**收场那半句**（见 `_autoname_say`）：老那半句在 `_AUTONAME_OLD_TAIL`。
_AUTONAME_HEAD = (
    "字段「%s」要拿去任务资料里取值的那个名字叫 `%s`：它**认不出语义**"
    "（不是邮编/邮箱/电话那类名字），形状也**不像运营资料的列名**"
    "（带数字串 = 框架自造的名字）。重放时这一格取不到资料、别名表也兜不住，"
    "值直接掉进随机池，而日志里写着「填好了」—— 真站代价是整单被站点拒收。")

#: ★ 老那句收场话 —— **只留给「没传 `observed`」那一条路**（逐字节钉在 `test_lint.py` 上）。
#: 它教模型「回到探路那侧把这一格的语义认出来」，而**模型打不开页面**：
#: 那是让它去够一个够不着的东西（与 `judge-fragile-selectors` 是**同一条病**）。
_AUTONAME_OLD_TAIL = "回到探路那侧把这一格的**语义**认出来（标签 / 例子值 / 题目正文）再重渲。"

#: 「判不出」那半句的**共同尾句**（两种形状共用：卡上什么都没量到 / 卡上的东西一样都说不出语义）。
#: ⚠️ 两条正路都是**做得到**的：判据判不出就**别猜**（猜出来的名字恰好过得了形状闸），
#: 请人在闸上说一句 —— 人正看着那一页，他说的那句话进 feedback，改稿那双手照着定 `source`。
_AUTONAME_CANNOT_JUDGE_TAIL = (
    "判据**判不出**它是什么，也就说不出该给它填什么。**别猜一个名字** —— 猜错正是"
    "这一条要治的那个病（取不到资料、静默掉进随机池）。请人在闸上说清"
    "**这一格填什么**（人正看着那一页），照那句话把 `source` 定下来，再重渲。")


def _autoname_say(key: str, source: str, card) -> str:
    """`judge-source-autoname` 那一句人话 —— 诊断那半句照旧，**收场那半句看卡说话**（两支）。

    ★ 2026-10-15 Task 7（spec §3.1 第二族）：老收场话是「回到探路那侧把这一格的语义认出来
    再重渲」，与 `judge-fragile-selectors` 那一族是**同一条病** —— 模型**打不开页面**，
    让它在探路那侧「认一认」就是让它去够一个够不着的东西。改法与那一族**同一把尺子**：
      · **有卡**（`_card_has_semantics`：卡上说得出「这一格是什么」的那几样至少一样有值，
        且不只是一个框架自造的名字）⇒ **把卡摆到眼前**（`_card_facts_say`，与
        `judge-fragile-selectors` 那一族**同一处写法** —— 两处各写一份的话，屏幕上同一个东西
        会长得不一样），照着这几样判语义；
      · **判不出** ⇒ 直说**判不出**，并给那条**做得到**的路：
        **请人在闸上说清这一格填什么**（人正看着那一页，他说的那句话进 feedback）。

    ★ 修复轮 1（评审 Important）：**「判不出」这一支也要把量到的东西摆出来**（`_card_facts_say`）
    —— 卡上明明有一个东西（真站那张卡上就是一句框架自造的名字）时说成「什么都没留下」是**假话**，
    而这一刀治的正是「说了等于没说 / 说了做不到的话」。两种形状分开说：
      · 卡**非空**但一样都说不出语义 ⇒ 「**探路那一刻量到的是**：… —— 可这几样一样都说不出
        这一格在问什么」（✅ 诚实，且模型仍然看得见那条机器名）；
      · 卡**是空的** ⇒ 才说「没有留下标签、提示或旁边的文字」。

    ⚠️ **旧路不经过这一层**：`observed is None`（老 job / 老调用点）⇒ `_AUTONAME_OLD_TAIL`，
    与从前逐字节一致（见 `_rule_g`）。
    ⚠️ 两支里**都不许**出现「回到探路那侧…」那句：模型的处境没变，它够不着就是够不着。
    """
    head = _AUTONAME_HEAD % (key, source)
    if _card_has_semantics(card):
        return head + (
            "**探路那一刻量到的是**：%s —— 用这几样把这一格的**语义**判出来"
            "（这一格到底在问什么），把 `source` 换成对得上的那个名字，再重渲。"
            % _card_facts_say(card))
    if card:
        return head + (
            "**探路那一刻量到的是**：%s —— 可这几样**一样都说不出这一格在问什么**"
            "（地址只说明它在哪；框架自造的名字换一次构建就换号）。"
            % _card_facts_say(card)) + _AUTONAME_CANNOT_JUDGE_TAIL
    return head + ("这一格**没有语义证据**（探路那一刻没有留下标签、提示或旁边的文字）："
                   + _AUTONAME_CANNOT_JUDGE_TAIL)


def _judge_violations(src: str, observed=None, accepted=None) -> list[Violation]:
    """判据与产物形状那一族（C/D/H + A/E/F/G/I）。

    读不出来的那几条（坏 py / 动态构造）⇒ **不猜**。
    ★ 2026-10-15：`observed` = **探路那一刻的原始观察**（只读、且必须是冻结的那一份）——
    它递给 `_rule_i`（I 那一格的出处判定）与 `_rule_g`（G 那一格的卡：有卡摆卡 / 没卡判不出）。
    不传（`None`）⇒ 与从前**逐字节一致**。
    ★ 2026-10-15 Task 6：`accepted` = 人点头接受过的那几处（spec §3.5）——只递给 `_rule_i`。
    """
    from agent import fix                      # 局部 import：避免与 fix 循环引用

    out: list[Violation] = []
    #: A 排在「读不出 STATES 就收工」那道闸**前面**：它**只读源码文本**（不碰 STATES）——
    #: 一份连字面量都读不出来的产物，照样可能把严格闸盲发出去。
    out.extend(_rule_a(src))
    states, fills = fix._literals(src)
    if not isinstance(states, list):
        #: 坏 py / 动态构造 ⇒ 读不出来的那几条（E/F/G/I）**一条都不报**（不猜）。
        states = []
    literals = []
    for one in (fills or {}).values():
        for cand in ((one or {}).get("fallback") or []):
            if isinstance(cand, str) and len(cand) > 3:
                literals.append(cand)
    for state in states:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "?")
        when = state.get("when")
        if not isinstance(when, dict):
            continue                            # `when=None` 本身不违规（边界见 `_rule_e`）
        judged = [str(t) for t in (when.get("text_contains") or [])]
        if not judged and when.get("url_contains") and not when.get("text_why"):
            # ⚠️ 带了 `text_why` 的不报：那是「查过了，只有地址稳」的记录（有损但**说了**），
            # 与「没钉出来」是两件事 —— 判据就在这一格上。
            out.append(Violation(
                line=_line_of(src, str(state.get("name") or "")), code="judge-url-only",
                message=("组「%s」的判据**只有地址**：SPA 上的 URL 全程不变 ⇒ 等于没判据"
                         "（真站实测：同一趟 11 组的 url 一模一样）。补一条**这一屏独有的**"
                         "正文判据（字段标签 / 标题 / 按钮文字）。" % name),
                snippet=_snippet('"name": %r' % name)))
        for text in judged:
            if TRANSIENT_RE.search(text):
                out.append(Violation(
                    line=_line_of(src, text), code="judge-transient",
                    message=("组「%s」的判据是**瞬时文案**（%r）—— 校验报错/加载中那种，"
                             "只在某一刻出现。换成这一屏**稳定**的文字。" % (name, text[:40])),
                    snippet=_snippet(text)))
            for literal in literals:
                if literal in text:
                    out.append(Violation(
                        line=_line_of(src, text), code="judge-fill-value",
                        message=("组「%s」的判据里含**要填进去的值**（%r ← %r）—— "
                                 "填什么由随机池决定，这两格会自相矛盾。"
                                 % (name, text[:40], literal[:30])),
                        snippet=_snippet(text)))
                    break
    #: A / E / F / G / I 五条（2026-09-29 晚，交接 §6：探针那套接进生成期的出口）。
    #: 与上面那三条分开写：这三条读的是**判据那几格**，那五条有的是**产物形状**
    #: （A/F 看骨架那几格在不在、E/G/I 看这一格认不认得出来）—— 各是各的现场。
    out.extend(_rule_a(src))
    out.extend(_rule_e(states, src))
    out.extend(_rule_f(states, src))
    out.extend(_rule_g(fills, src, observed))
    out.extend(_rule_i(states, fills, src, observed, accepted))
    #: 表就是开关：`JUDGE_RULES` 里没有的不报 —— 变异验证（摘掉一条 ⇒ 对应用例不再红）
    #: 靠的就是这一行，与上面那族 `RULES` 同一条规矩。
    return [one for one in out if one.code in JUDGE_RULES]


def _snippet(raw: str) -> str:
    """那一行的原文（只削掉行尾空白；太长就截断 —— 回灌提示词用的）。"""
    text = raw.rstrip()
    if len(text) > SNIPPET_CHARS:
        text = text[:SNIPPET_CHARS - 1] + "…"
    return text


def check(src: str, observed=None, accepted=None) -> list[Violation]:
    """扫一份产物源码，返回违规列表（按行号升序；同一行同一条规则只报一次）。

    **空列表 = 通过**。负例（合法产物零违规）比正例重要：一个什么都抓的 lint
    和没有 lint 一样没用，而且更坏 —— 它看起来在工作。

    ★ 2026-10-15 修站那一刀：`observed` = **探路那一刻的原始观察**（`fix.observed_of` 冻的那份，
    spec §3.6b）。⚠️ 它**只读**，而且**必须是冻结的那一份** —— 不许从 `src` / 当前 spec 里取，
    否则就是循环证明（模型编一根写进表，判据再从表里读到「有出处」）。
    不传（`None`）⇒ 与从前**逐字节一致**。

    ★ 2026-10-15 Task 6：`accepted` = **人点头接受过的那几处地址**（spec §3.5，
    形状与 `no_basis_slots` 每一条相同；只有人能写它 —— `graph._accept_from_note` 是唯一入口）。
    它只让「无据可依、但人接受过、且四元组一项没变」的那一步**不报**；四元组任一变了就回到报。
    不传（`None`）⇒ 与从前**逐字节一致**。
    """
    violations: list[Violation] = []
    seen: set[tuple[int, str]] = set()
    for lineno, raw in enumerate(src.splitlines(), start=1):
        for rule in RULES:
            key = (lineno, rule.code)
            if key in seen or not rule.pattern.search(raw):
                continue
            seen.add(key)
            violations.append(Violation(line=lineno, code=rule.code,
                                        message=rule.message, snippet=_snippet(raw)))
    # 第二族：判据与产物形状那八条（C/D/H + A/E/F/G/I）。同一行同一条只报一次 —— 与上面同一条规矩。
    for one in _judge_violations(src, observed, accepted):
        key = (one.line, one.code)
        if key in seen:
            continue
        seen.add(key)
        violations.append(one)
    return sorted(violations, key=lambda v: (v.line, v.code))


def _derived_only_say(card, sels) -> Optional[tuple]:
    """这一处地址是不是**全靠 Derived 撑着** → 是就给 `(判据那句话, 全靠它撑着的那几根)`。

    ★ 口径**就是** `_rule_i` 里「全靠 Derived 撑着 ⇒ 放行」那一支（`states` 与 `fills`
    两处同一条判决）—— 两边是同一件事的两半，**改一处必须改两处**：
      · `UNSUPPORTED` 那几根**不算采信**（`_rule_i` 压根不把它们放进 `kept`）⇒ 判之前先滤掉；
      · 滤完一根不剩 ⇒ **不是这一支**（那是「一根采信的都没有」，`_rule_i` 报
        `judge-fragile-no-basis`，不归这一笔）；
      · 只要**还有一根 Observed 撑着** ⇒ 也不算（那一步有量到的地址，本来就不靠推的）。
    ⚠️ **没卡**（`not card`）⇒ 不是这一支：「推出来的」的前提是**有据**（有 Observation 可推）。
    ⚠️ 级别一律走 `address_basis`（**不自己另判一遍**）：它的**第 0 步**是「与观测原文
    逐字相同 ⇒ `OBSERVED`」（★ Ruling 16）—— 另判一遍就会把「原样抄回来的那一根」
    误报成「推出来的」。
    """
    if not card:
        return None
    levels, derived = [], []
    for one in sels:
        level, why = address_basis(one, card, card.get("selectors") or [])
        levels.append(level)
        if level == DERIVED:
            derived.append((one, why))
    #: 与 `_rule_i` 那一支**同一个滤法**：`UNSUPPORTED` 的**不算采信**，滤掉再判。
    #: ⚠️ 变量名不与那边同形：那边 `kept` 装的是**地址**，这里装的是**级别**。
    credible = [one for one in levels if one != UNSUPPORTED]
    if not credible:
        return None                 # 一根采信的都不剩 ⇒ `_rule_i` 报无据可依，不归这一笔
    #: ⚠️ 下面这一句**照 `_rule_i` 那一行写**（两处是同一件事的两半，改一处必须改两处）。
    if not all(one == DERIVED for one in levels if one != UNSUPPORTED):
        return None                 # 还有 Observed 撑着 ⇒ 那一步不是「全靠推的」
    return (derived[0][1], [one for one, _ in derived])


def check_unverified(src: str, observed) -> list[str]:
    """这一版里**有据但没验过**的那几处（Derived）→ 一行一句的人话。**不报违规**。

    ★ 2026-10-15（spec §3.3a）：全靠 Derived 撑着的那一步**放行，但不当作「稳」**。
    为什么不报违规：报了它就停在 `draft ↔ lint` 环里、**永远到不了自测** ——
    而 Derived 的验证恰恰落在自测那一跳。所以这里只**记一笔**，让它带着标注往下走。

    ⚠️ 自测跑到了 ⇒ 记「运行验证通过」；**不得改写成 Observed**（一次跑通 ≠ 属性来源已证实）。
    ⚠️ 人话不许编：那几句**出处的话**取自 `address_basis`（判据自己说的那一句），
    这里只加「这一格是哪儿」+「它要往哪儿走」。
    """
    if observed is None:
        return []
    from agent import fix
    states, fills = fix._literals(src)
    #: ⚠️ 坏 py / 动态构造 / 标量：与 `_judge_violations` 那条**同一规矩** —— 读不出来**不猜**。
    #: 这里**不是**新判据，是那一格自己不许崩：`_lint` 每一趟都走这一步，在这里抛
    #: `TypeError` 就是整趟运行死在读不懂的产物上（读不懂 = 不说，不是崩）。
    if not isinstance(states, list):
        states = []
    if not isinstance(fills, dict):
        fills = {}
    out: list = []
    for state in states:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "?")
        for index, step in enumerate(state.get("steps") or []):
            if not isinstance(step, dict):
                continue
            target = step.get("target") or {}
            sels = _selectors(target)
            if not sels:
                continue
            #: ⚠️ 卡一律走 `_observed_card`（= `_rule_i` 那一支的同一个调用）：`find_observed`
            #: 的返回值**按 falsy 判**，而且卡**必须**来自冻结那份 —— 见 `_observed_card`。
            card = _observed_card(observed, name, index, target)
            got = _derived_only_say(card, sels) if card else None
            if not got:
                continue
            why, derived = got
            out.append("组「%s」第 %d 步：地址 %s **放行，但按「未证实」记** —— %s"
                       "自测跑到它才算数（跑到了也只记「运行验证通过」，"
                       "**不得**把它当成量到的）。"
                       % (name, index + 1,
                          "、".join("`%s`" % one for one in derived), why))
    for key, fill in fills.items():
        target = (fill or {}).get("target") or {}
        sels = _selectors(target)
        if not sels:
            continue
        #: ⚠️ 与 `_rule_i` 的 fills 那一支**同一个取卡方式**：`src` 是待检查的那一份，
        #: 拿它当证据就是循环证明 —— 冻结那份里按 `fills` 的**键**找（键是这一格唯一的名字）。
        frozen = ((observed or {}).get("fills") or {}).get(key)
        card = fix.card_of((frozen or {}).get("target"))
        got = _derived_only_say(card, sels) if card else None
        if not got:
            continue
        why, derived = got
        out.append("字段「%s」：地址 %s **放行，但按「未证实」记** —— %s"
                   "自测跑到它才算数（跑到了也只记「运行验证通过」，"
                   "**不得**把它当成量到的）。"
                   % (key, "、".join("`%s`" % one for one in derived), why))
    return out


def no_basis_slots(src: str, observed, accepted=None) -> list[dict]:
    """这一版里**无据可依**的那几处 → `[{"state": 名, "index": 步序, "selector": 那根, "card": 卡指纹}]`。

    为什么要有它（★ Ruling 2）：人在闸上说「接受地址 X#N」时，`_accept_from_note` 得知道
    X#N 指的是**哪一根**，否则只能不认（**不许猜** —— 猜的后果是「人没同意的事被记成同意了」，
    而屏幕上一切正常）。这一格就是那个「指的是哪一根」：`_no_basis_say` 报的那个 handle
    与这里写下来的键**同源**（`(组名, 步序)` 拼的 `%s#%d`），两边不会各说各的。

    ⚠️ 判「算不算无据可依、要绑哪一根」走的是 `_no_basis_sel`、判「人接受过没有」走的是
    `_accepted`（与 `_rule_i` 报那一格**同一支**）—— 各写一遍的话，「人接受过的那一步」
    与「判据还在报的那一步」会对不上，而屏幕上两边看起来都正常。
    ⚠️ 只覆盖 `states` 那一支：`FILLS` 那一格的把手是**键**、`(组名, 步序)` 认不出来
    （`_fill_no_basis_say` 因此只写两条正路 —— 写一句做不到的话正是这一刀要治的病）。
    ⚠️ **卡指纹存字符串**（`fix.card_fingerprint` 本来给的是**元组**，`fix.py:1190`）：
    这一格要进 state、要过 checkpoint（JSON），元组过一趟就成了 list ⇒ 同一张卡前后
    `str()` 不等，人那次点头会**无声失效**（屏幕上还写着「接受过」）。存字符串就只有一种形状。
    ⚠️ 坏 py / 动态构造 ⇒ 与 `_judge_violations` 那条**同一规矩**：读不出来**不猜**（空表）。
    ⚠️ 没给 `observed`（没有冻结那份）⇒ 空表：连绑哪一根都不知道，没人能接受（不编一条出来）。
    """
    if observed is None:
        return []
    from agent import fix
    states, fills = fix._literals(src)
    if not isinstance(states, list):
        states = []
    out: list[dict] = []
    seen: set = set()
    for state in states:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "?")
        for index, step in enumerate(state.get("steps") or []):
            if not isinstance(step, dict):
                continue
            target = step.get("target") or {}
            sels = _selectors(target)
            if not sels:
                continue
            handle = "%s#%d" % (name, index)
            if handle in seen:
                continue
            #: ⚠️ 卡一律走 `_observed_card`（= `_rule_i` 那一支的同一个调用）：`find_observed`
            #: 的返回值**按 falsy 判**，而且卡**必须**来自冻结那份 —— 见 `_observed_card`。
            card = _observed_card(observed, name, index, target)
            #: ⚠️ 与 `_rule_i` 那一支**同一个滤法**（`UNSUPPORTED` 的**不算采信**）：
            #: 那边还要 `levels`（"全靠 Derived 撑着" 那一支要用），所以它自己那个循环留着 ——
            #: 改一处必须改两处。
            kept = []
            for one in sels:
                level, _why = address_basis(
                    one, card or {}, (card or {}).get("selectors") or [])
                if level != UNSUPPORTED:
                    kept.append(one)
            #: ★ 修复轮 1：与 `_rule_i` **同一条判决**（那边是「整步跳过」，这边是「不再等」）——
            #: 已经接受过的那一格不该再出现在待接受清单里（人再说一遍也只是原地覆盖）。
            if _accepted(card, sels, kept, accepted, name, index):
                continue
            sel = _no_basis_sel(card, sels, kept)
            if sel is None:
                continue
            seen.add(handle)
            out.append({"state": name, "index": index, "selector": sel,
                        "card": str(fix.card_fingerprint(card))})
    return out
