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

给非技术人员看的文字（`Violation.message`）必须是**人话**，且**说清改用哪条 cdp 命令**
（D16；光说「不许这样」会让 agent 原地打转）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

__all__ = ["Violation", "Rule", "RULES", "check"]


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
# 第二族：**判据**（`STATES[*].when` / `FILLS`）—— 要读字面量，正则扫不出来。
#
# ★ 2026-09-29（用户裁定）：挂 **C / D / H** 三条，**E 不挂**。
#   为什么会有这一族：上面四条盯的是「**怎么写**」，而 2026-09-28 一天里修掉的六个站，
#   死因多半在「**认页那一段字**」——判据错了，产物每一步都「做成了」而整单没成
#   （georgiapower：真做 2 步、跳过 23 步，日志里每步都写着点上了）。
#
# 三条各自的现场：
#   · C 瞬时文案（`Please enter a valid email address…`）—— 只有空提交/非法值那一刻才出现；
#   · D 判据含**要填进去的值**（`123 Peachtree St NE Atlanta, GA, USA 123`）—— 与随机池自相矛盾；
#   · H 只有地址判据 —— SPA 上 URL 全程不变 ⇒ 等于没判据（georgiapower 11 组的 url 全一样）。
#
# ⚠️ **不挂的**（挂上去就是把好产物拦下来，`test_lint.py` 那条「负例比正例重要」说得很清楚）：
#   · **E `when=None`**：旁枝起点**有意**不设门（`browser_agent._drop_incidental_start_when`），
#     那是设计不是错 —— 拿它当闸会把每一份正常产物都打回；
#   · **B 跨元素拼接**：机械判据要**逐元素正文**，那只在生成期拿得到（`cdp observe` 的
#     `node_texts`，2026-09-29 起随模型一起来）——生成期已经在换/丢那段判据了
#     （`browser_agent._when_for`）；放到这一层只能写成「长串 + 空格」那种启发式，
#     实测它会把**修好的**产物也标出来（51 份里 185 处）。
JUDGE_RULES: tuple[str, ...] = ("judge-transient", "judge-fill-value", "judge-url-only")

#: 瞬时/校验类文案（C）。与 `docs/probes/2026-09-28-georgiapower/artifact_lint.py` 的
#: `TRANSIENT` 同一套词 —— 两处对不上就会出现「体检说有问题、闸说没有」那种不一致。
TRANSIENT_RE = re.compile(
    r"complete this field|please enter|is not valid|invalid|required|"
    r"error|loading|please wait|try again|verification", re.I)

#: snippet 的截断长度：够看出是哪一行，又不至于把整行 JS 灌进提示词。
SNIPPET_CHARS = 120


def _line_of(src: str, needle: str) -> int:
    """这一串字在源码里的行号（找不到给 0）。回灌给 agent 时要「指着行说」。"""
    for lineno, raw in enumerate(str(src or "").splitlines(), start=1):
        if needle in raw:
            return lineno
    return 0


def _judge_violations(src: str) -> list[Violation]:
    """判据那三条（C/D/H）。读不出来（坏 py / 动态构造）⇒ 空列表（**不猜**）。"""
    from agent import fix                      # 局部 import：避免与 fix 循环引用

    out: list[Violation] = []
    states, fills = fix._literals(src)
    if not isinstance(states, list):
        return out
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
            continue                            # `when=None` 是**有意**的（E 不挂）
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
    #: 表就是开关：`JUDGE_RULES` 里没有的不报 —— 变异验证（摘掉一条 ⇒ 对应用例不再红）
    #: 靠的就是这一行，与上面那族 `RULES` 同一条规矩。
    return [one for one in out if one.code in JUDGE_RULES]


def _snippet(raw: str) -> str:
    """那一行的原文（只削掉行尾空白；太长就截断 —— 回灌提示词用的）。"""
    text = raw.rstrip()
    if len(text) > SNIPPET_CHARS:
        text = text[:SNIPPET_CHARS - 1] + "…"
    return text


def check(src: str) -> list[Violation]:
    """扫一份产物源码，返回违规列表（按行号升序；同一行同一条规则只报一次）。

    **空列表 = 通过**。负例（合法产物零违规）比正例重要：一个什么都抓的 lint
    和没有 lint 一样没用，而且更坏 —— 它看起来在工作。
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
    # 第二族：判据那三条（C / D / H）。同一行同一条只报一次 —— 与上面同一条规矩。
    for one in _judge_violations(src):
        key = (one.line, one.code)
        if key in seen:
            continue
        seen.add(key)
        violations.append(one)
    return sorted(violations, key=lambda v: (v.line, v.code))
