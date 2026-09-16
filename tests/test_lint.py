"""Task 4：契约检查器（`agent/lint.py`）的契约测试（规格 §5.2）。

**为什么要一个 lint**：用户 2026-09-16 定的原则是「**动页面一律走 cdp 工具**
（`form` / `click` / `scroll`），不要手拼 JS」。产出的 py 是 LLM 写的，
所以这条原则只能靠**机器打回**来执行 —— 在提示词里写「请勿手拼 JS」不算执行。

**为什么负例比正例重要**（计划 Task 4 Step 1 的 ⚠️）：
只测「抓到违规」会放过「**什么都抓**」的 lint —— 一个把所有产物都打回的 lint
和没有 lint 一样没用（而且更坏：它看起来在工作）。所以这里有一条钉子：
拿参考产物 `fixtures/reference_site.py`（Task 3 渲染出的**合法 cdp-first 产物**）跑
lint，必须**零违规**；还有一条钉住它**不是空的**（否则零违规没有意义）。

**为什么 `document.querySelector` 不是违规**（计划预检 **S1**，2026-09-17 裁定）：
原表里的「裸 `document.querySelector` 出现在**写**路径」被删掉了 ——
**「读路径 vs 写路径」在静态上不可判定**，而 `querySelector` **只是查找**、
读写都用它；拿它当违规会**误伤合法的读**（产物里 `cdp eval` 读页面本来就要用）。
判据盯的是**写动作本身**（四条写模式，见 `RULES`）。
→ `test_read_only_js_is_not_flagged` 与 `test_lookup_alone_is_not_a_violation`
   两条就是钉这个裁定的。

**变异验证**（计划 Task 4）：`test_each_rule_is_load_bearing` 把某条规则从表里摘掉，
断言对应用例**不再**被打回 —— 它测的是「这条规则真的在承重」，
即防「用例因为别的原因红/绿」。
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import lint  # noqa: E402

REFERENCE = ROOT / "fixtures" / "reference_site.py"


# ── 四条写模式，一条一个用例（规格 §5.2 的表）──────────────────────────
#
# 每份样例都是一段**像产物片段**的 Python：JS 写在字符串里、由 `eval` 发出去 ——
# 这正是真实产物里手拼 JS 的样子（不是孤立的一行 JS）。
# `line` 是那行违规的 1-based 行号，**故意每条都不一样**：# 行号写死成常量，
# 才能验出「行号报的是真行号」而不是恰好等于 1。

JS_FILL_SRC = (
    "def _set(self, value):\n"
    '    js = "el.dispatchEvent(new Event(\'input\', {bubbles: true}))"\n'
    "    return self.cdp.eval(js)\n"
)

JS_NATIVE_SETTER_SRC = (
    "def _set(self, value):\n"
    "    js = (\n"
    "        \"var d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');\"\n"
    "        \"d.set.call(el, '\" + value + \"');\"\n"
    "    )\n"
    "    return self.cdp.eval(js)\n"
)

# ⚠️ 这一行**同时**含 `querySelector` 与 `.click()` —— 断言「只有一条违规」，
# 就是断言 `querySelector` 单独出现**不算**违规（S1）。
JS_CLICK_SRC = (
    "def _go(self):\n"
    "    # 手拼点击（违规样例）\n"
    "    js = \"document.querySelector('.cta').click()\"\n"
    "    return self.cdp.eval(js)\n"
)

# 同上：这一行也含 `querySelector`，但违规的是**赋值**（写动作）。
JS_VALUE_ASSIGN_SRC = (
    "def _set(self, value):\n"
    "    # 直接赋值（违规样例）\n"
    "    js = (\n"
    "        \"document.querySelector('#postcode').value = '\" + value + \"';\"\n"
    "    )\n"
    "    return self.cdp.eval(js)\n"
)

CASES = [
    pytest.param("js-fill", JS_FILL_SRC, 2, id="js-fill"),
    pytest.param("js-native-setter", JS_NATIVE_SETTER_SRC, 3, id="js-native-setter"),
    pytest.param("js-click", JS_CLICK_SRC, 3, id="js-click"),
    pytest.param("js-value-assign", JS_VALUE_ASSIGN_SRC, 4, id="js-value-assign"),
]


@pytest.mark.parametrize("code, src, line", CASES)
def test_write_pattern_is_rejected(code, src, line):
    """每条写模式都要被打回，且**指出是哪一行**（回灌给 agent 时要能指着行说）。"""
    violations = lint.check(src)

    assert [v.code for v in violations] == [code], (
        "这一条应该**只有**它自己被报出来（多报 = 误伤，少报 = 漏检）：%r" % (violations,)
    )
    v = violations[0]
    assert v.line == line, "行号要是 1-based 的真行号：%r" % (v,)
    assert v.snippet == src.splitlines()[line - 1], "snippet 要是那一行的原文：%r" % (v,)


@pytest.mark.parametrize("code, src, line", CASES)
def test_message_is_human_words(code, src, line):
    """D16：给非技术人员看的文字必须是**人话**，不是错误码、不是选择器。

    而且要**说清怎么改** —— 光说「不许这样」会让 agent 原地打转。
    """
    v = lint.check(src)[0]

    assert v.message and v.message != code
    assert code not in v.message, "消息里不该出现错误码：%r" % (v.message,)
    assert any("一" <= ch <= "鿿" for ch in v.message), "要是中文人话：%r" % (v.message,)
    assert "cdp" in v.message, "要说清改用哪条 cdp 命令：%r" % (v.message,)
    assert "querySelector" not in v.message, "不许把选择器端给人看：%r" % (v.message,)


@pytest.mark.parametrize("code, src, line", CASES)
def test_each_rule_is_load_bearing(monkeypatch, code, src, line):
    """变异验证：把这条规则从表里摘掉 → 对应用例**必须不再**被打回。

    只测「抓到了」会放过「什么都抓」；这条反过来证明**每条规则都在承重**，
    也证明上面那些用例不是因为别的原因才红的。
    """
    monkeypatch.setattr(lint, "RULES", tuple(r for r in lint.RULES if r.code != code))

    assert lint.check(src) == [], "摘掉 %s 之后这条样例仍被打回 —— 说明报它的不是这条规则" % code


# ── 负例：合法的 cdp-first 产物必须零违规（比正例重要）──────────────────


def _describe(violations):
    return "\n".join("  第 %d 行 [%s] %s\n    %s" % (v.line, v.code, v.message, v.snippet)
                     for v in violations)


def test_reference_artifact_has_zero_violations():
    """参考产物（Task 3 渲染出的**合法**产物）跑 lint 必须零违规。

    这条红了有两个可能，**先怀疑 lint 误报**：
    - lint 太宽 → 改 lint（这一条是本次任务的判据）
    - 产物真写了手拼 JS → 那是**真发现**，报给计划负责人，**不要**放宽规则迁就它，
      也不要改那个 fixture（它被 `tests/test_template.py` 的钉子锁着）
    """
    src = REFERENCE.read_text(encoding="utf-8")
    violations = lint.check(src)

    assert not violations, (
        "参考产物被打回了 —— 要么 lint 误报，要么产物真有手拼 JS（详见测试文件 docstring）：\n"
        + _describe(violations)
    )


def test_reference_artifact_is_not_vacuous():
    """负例**不能是空的**：参考产物里得真有 cdp 写动作与 eval 读 ——

    否则「零违规」只说明这份文件里什么都没有，什么也证明不了。
    """
    src = REFERENCE.read_text(encoding="utf-8")

    for call in ("self.cdp.form(", "self.cdp.click(", "self.cdp.scroll("):
        assert call in src, "参考产物里应该有这类 cdp 写动作：%s" % call
    assert "self.cdp.eval(" in src, "参考产物里应该有 eval（**只读**路径用它）"


# ── S1：只读的 JS 不许被误伤 ───────────────────────────────────────────


READ_ONLY_SRC = (
    "def _probe(self):\n"
    "    # 只读：看页面正文、看输入框里现在是什么（S1：这不算违规）\n"
    "    text = self.cdp.eval(\"return document.querySelector('#hero').innerText;\")\n"
    "    filled = self.cdp.eval(\"return document.querySelector('#postcode').value;\")\n"
    "    count = self._ev(\"return document.querySelectorAll('input').length;\")\n"
    "    roots = self._ev(\"return document.getElementsByTagName('*').length;\")\n"
    "    # 写动作走 cdp —— 与上面那些读**共存**，谁也不该被打回\n"
    "    self.cdp.click('#get-started')\n"
    "    return text, filled, count, roots\n"
)


def test_read_only_js_is_not_flagged():
    """构造一个**只读**的片段 → 零违规（这正是 S1 要防的那类误伤）。

    里面刻意放了 `querySelector` 与 `.value` + `.innerText` 的**读**：
    读路径是产物该有的东西（`eval` 只准用来读），不是违规。
    """
    assert lint.check(READ_ONLY_SRC) == []


def test_lookup_alone_is_not_a_violation():
    """S1 的回归钉子：规则表里**不许**出现以「查找方式」为判据的规则。

    理由逐字见本文件 docstring：读路径 vs 写路径静态不可判定，而 `querySelector`
    只是查找、读写都用它。判据盯**写动作本身**。
    """
    assert not any("querySelector" in r.pattern.pattern for r in lint.RULES), (
        "有人把 querySelector 那条加回来了 —— S1 已经裁定删掉它（见计划预检扫描）"
    )


# ── 接口形状与行为 ─────────────────────────────────────────────────────


def test_interface_shape():
    """`Violation{line, code, message, snippet}` 四个字段 —— Task 7 要拿它们回灌 draft。"""
    assert [f.name for f in dataclasses.fields(lint.Violation)] == ["line", "code", "message", "snippet"]
    v = lint.check(JS_FILL_SRC)[0]
    assert isinstance(v.line, int) and isinstance(v.code, str)
    assert isinstance(v.message, str) and isinstance(v.snippet, str)


def test_rule_table_is_exactly_the_four_write_patterns():
    """表就是规格 §5.2 那四条（按 S1 修正后）—— 多一条/少一条都要有人来解释。"""
    assert [r.code for r in lint.RULES] == ["js-fill", "js-native-setter", "js-click", "js-value-assign"]


def test_comparison_is_not_an_assignment():
    """读一个值来比一比（`==` / `!=`）是**读**，不是写 —— 不许被打回。"""
    src = (
        "def _empty(self, selector):\n"
        "    js = \"return document.querySelector(sel).value === '';\"\n"
        "    return self.cdp.eval(js)\n"
    )
    assert lint.check(src) == []


def test_empty_and_trivial_source_is_clean():
    """空源码、只有一行 cdp 写动作的片段 → 零违规（lint 不许见谁都打回）。"""
    assert lint.check("") == []
    assert lint.check("    self.cdp.form('#postcode', value='SW1A 1AA')\n") == []
    assert lint.check("    self.cdp.scroll('400')\n") == []


def test_violations_are_ordered_and_deduped_per_line():
    """按行号升序；**同一行同一条规则只报一次**（回灌的提示词不该被同一行刷屏）。"""
    src = (
        "def _a(self):\n"                                            # 1
        "    el.value = 'x'\n"                                       # 2 违规
        "    return self.cdp.eval('1')\n"                            # 3 干净
        "    el2.value = 'y'; el3.value = 'z'\n"                     # 4 违规（两次写，同一行）
    )
    violations = lint.check(src)

    assert [v.line for v in violations] == [2, 4]
    assert {v.code for v in violations} == {"js-value-assign"}
