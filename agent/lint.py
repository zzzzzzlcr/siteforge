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


def _rule_g(fills, src: str) -> list[Violation]:
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
        if not AUTONAME.search(source):
            continue                             # 人写的列名形状（`username` 那种）—— 不报
        out.append(Violation(
            line=_line_of(src, source), code="judge-source-autoname",
            message=("字段「%s」要拿去任务资料里取值的那个名字叫 `%s`：它**认不出语义**"
                     "（不是邮编/邮箱/电话那类名字），形状也**不像运营资料的列名**"
                     "（带数字串 = 框架自造的名字）。重放时这一格取不到资料、别名表也兜不住，"
                     "值直接掉进随机池，而日志里写着「填好了」—— 真站代价是整单被站点拒收。"
                     "回到探路那侧把这一格的**语义**认出来（标签 / 例子值 / 题目正文）再重渲。"
                     % (key, source)),
            snippet=_snippet(source)))
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
    """
    t = target or {}
    return bool(str(t.get("label") or "").strip() or str(t.get("text") or "").strip())


def _rule_i(states: list, fills, src: str) -> list[Violation]:
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
            if not sels or not all(_fragile(s) for s in sels):
                continue
            #: 有文字/标签锚 ⇒ 不挂（见 `_has_text_anchor`：那种格不是「只剩一根漂的线」）
            if _has_text_anchor(target):
                continue
            handle = "%s#%d" % (name, index)
            if handle in seen:
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
        if not sels or not all(_fragile(s) for s in sels):
            continue
        if _has_text_anchor(target) or str((fill or {}).get("label") or "").strip():
            continue
        handle = "FILLS:%s" % key
        if handle in seen:
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

def _judge_violations(src: str) -> list[Violation]:
    """判据与产物形状那一族（C/D/H + A/E/F/G/I）。读不出来的那几条（坏 py / 动态构造）⇒ **不猜**。"""
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
    out.extend(_rule_g(fills, src))
    out.extend(_rule_i(states, fills, src))
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
    # 第二族：判据与产物形状那八条（C/D/H + A/E/F/G/I）。同一行同一条只报一次 —— 与上面同一条规矩。
    for one in _judge_violations(src):
        key = (one.line, one.code)
        if key in seen:
            continue
        seen.add(key)
        violations.append(one)
    return sorted(violations, key=lambda v: (v.line, v.code))
