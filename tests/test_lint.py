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
import hashlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import lint, template  # noqa: E402

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


# ── 第二族：判据（C/D/H，2026-09-29 用户裁定）────────────────────────────────
#
# 为什么有这一族：上面四条盯「**怎么写**」，这一族盯「**认页那一段字**」——
# 2026-09-28 一天里修掉的六个站，死因多半在前者之外（判据错了，产物每步都「做成了」
# 而整单没成：georgiapower 真做 2 步、跳过 23 步）。


def _artifact(states, fills=None):
    return template.render("lint-sample", "Thank you", states, fills or [],
                           {"generated_at": "2026-09-29T00:00:00+08:00"})


def _codes(src):
    return sorted({v.code for v in lint.check(src)})


def test_a_transient_error_string_as_a_judge_is_refused():
    """C：判据是**瞬时文案**（校验报错那种）⇒ 只有那一刻才出现，重放时必然整组跳过。"""
    src = _artifact([{"name": "why", "when": {"url_contains": "/a",
                                               "text_contains": ["Please enter a valid email address"]},
                      "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}])
    assert "judge-transient" in _codes(src), lint.check(src)


def test_a_judge_containing_the_value_we_are_going_to_fill_is_refused():
    """D：判据里含**要填进去的值** ⇒ 与随机池自相矛盾（真站：`123 Peachtree St NE … 123`）。"""
    src = _artifact(
        [{"name": "addr", "when": {"url_contains": "/a",
                                   "text_contains": ["123 Peachtree Street Northeast is your address"]},
          "steps": [{"action": "form", "fill": "street", "target": {"selectors": ["#street"]}}]}],
        [{"name": "street", "target": {"selectors": ["#street"]},
          "fallback": ["123 Peachtree Street Northeast"]}])
    assert "judge-fill-value" in _codes(src), lint.check(src)


def test_a_url_only_judge_is_refused_unless_it_says_why():
    """H：只有地址判据 ⇒ SPA 上等于没判据（11 组的 url 一模一样）。

    ⚠️ 但**带了 `text_why` 的不报** —— 那是「查过了，只有地址稳」的记录（有损但**说了**），
    与「没钉出来」是两件事。这一条钉的就是这个区别。
    """
    bare = _artifact([{"name": "spa", "when": {"url_contains": "/go"},
                       "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}])
    assert "judge-url-only" in _codes(bare), lint.check(bare)
    told = _artifact([{"name": "spa", "when": {"url_contains": "/go", "text_why": "查过了：只有地址稳"},
                       "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}])
    assert "judge-url-only" not in _codes(told), lint.check(told)


def test_a_bare_when_is_not_a_violation():
    """E **不挂**（用户 2026-09-29 明确）：旁枝起点**有意**不设门
    （`browser_agent._drop_incidental_start_when`）—— 拿它当闸会把每一份正常产物都打回。"""
    src = _artifact([{"name": "start", "when": None,
                      "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}])
    assert lint.check(src) == [], lint.check(src)


def test_each_judge_rule_is_load_bearing(monkeypatch):
    """变异验证：摘掉一条 ⇒ 对应样例**必须不再**被打回（与 `RULES` 那族同一条规矩）。"""
    src = _artifact([{"name": "why", "when": {"url_contains": "/a",
                                              "text_contains": ["Please enter a valid email address"]},
                      "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}])
    assert "judge-transient" in _codes(src)
    monkeypatch.setattr(lint, "JUDGE_RULES", ("judge-fill-value", "judge-url-only"))
    assert "judge-transient" not in _codes(src), "摘掉了它却还在报 —— 说明报它的不是这一条"


# ── 2026-09-29 晚：探针那套 A/E/F/G/I 接进**生成期**（交接《骨架还缺什么》§6）────────
#
# 探针（`docs/probes/2026-09-28-georgiapower/artifact_lint.py`）是**事后扫**：见到形状就报，
# 报告长了有人读。这一段测的是**生成期的闸** —— 命中就不许出产物，所以凡是「本骨架自己那份
# **合法**写法也会被打回」的规则，都必须**收窄**到真的会出事的那个形状（A 与 I 两条就是
# 收窄过的，理由逐字写在 `agent/lint.py` 各自 docstring 里）。
# ⇒ 这一段里每条规则都是**一对**：一条「该挂」的正例 + 一条「不该挂」的反例。


def test_a_strict_form_with_no_way_out_is_refused():
    """A：把**严格闸**发出去、却不写「这一版部署不认这个开关」的退路。

    真站两次：带 `--strict` 时 mapleporchdaily **每一步 form 都失败**（选择器命中数都是 1，
    却一律 `element not found` —— `forms-222/sites/mapleporchdaily.v2.py:1584-1587` 逐字记着）；
    部署的 `common.py` 不认识那两个参数时，这一下 `TypeError` 会把整份产物炸掉，
    而自测会把它读成「走完了 N 步、0 跳过」。
    """
    src = ("def _fill(self, selector, value):\n"
           "    return self.cdp.form(selector, value=value, strict=True)\n")
    assert _codes(src) == ["judge-strict-unguarded"], lint.check(src)


def test_the_skeletons_own_strict_form_is_not_flagged():
    """A 的**反例**：本骨架那份写法（`strict` 与那条退路**成对**）不挂。

    ⚠️ 探针原来那句是「出现 `"strict": true` 就报」（`artifact_lint.py:109-110`）——
    照抄过来会把本骨架自己渲染的**每一份**产物都打回，连参考产物一起，
    也就是这个闸永远红。所以判据收窄成「成对出现」。
    """
    src = REFERENCE.read_text(encoding="utf-8")
    assert '"strict": True' in src and "except TypeError" in src, "反例本身要真是那个形状"
    assert "judge-strict-unguarded" not in _codes(src), lint.check(src)


def test_an_ungated_group_clicking_by_words_is_refused():
    """E：无门的那一组里，有一步点击**只按页面上的文字**认元素。

    落到别的页上时，它照样找得到「碰巧写着同一句话」的那个东西点下去，而日志里看不出
    异常（交接 §6 / 探针 E 条：「无条件执行：广告落到别的页会乱点」）。
    """
    src = _artifact([{"name": "start", "when": None,
                      "steps": [{"action": "click",
                                 "target": {"text": "Continue", "role": "button",
                                            "selectors": []}}]}])
    assert "judge-ungated-click" in _codes(src), lint.check(src)


def test_an_ungated_group_addressed_by_selectors_is_not_flagged():
    """E 的**反例**（用户 2026-09-29 明确过的那一向）：旁枝起点**有意**不设门
    （`browser_agent._drop_incidental_start_when`）—— 它的步骤按**选择器**认元素，
    落到别的页上最坏是当场「没找到」（出声，不静默），不是乱点 ⇒ 不许打回。
    """
    src = _artifact([{"name": "start", "when": None,
                      "steps": [{"action": "click",
                                 "target": {"text": "Continue", "role": "button",
                                            "selectors": ["#continue"]}}]}])
    assert lint.check(src) == [], lint.check(src)


#: F 的样例：有一步点击、产物里却没有骨架那个跟新 tab 的方法
#: （**故意**手写一份最小产物 —— 从当前骨架渲染出来的**一定**带着它，那正是这条要盯的东西）。
NO_FOLLOW_SRC = (
    "STATES = [{'name': 'go',\n"
    "           'when': {'url_contains': '/a', 'text_contains': ['Go']},\n"
    "           'steps': [{'action': 'click', 'target': {'selectors': ['#cta']}}]}]\n"
)


def test_clicks_without_following_a_new_tab_is_refused():
    """F：点 CTA 会开新 tab，而这份产物没有「点完跟一下新开的那一页」。

    真站（`docs/缺陷单-2026-09-28-产物缺跟随新tab.md`，georgiapower）：新 tab **2.26 秒**
    才出现、先以空地址出现、4.3 秒才有地址 —— 那一下之后脚本读的还是旧页，
    25 步里 23 步被静默跳过，而每一步日志照样写着「点上了」。
    """
    assert _codes(NO_FOLLOW_SRC) == ["judge-no-follow-tab"], lint.check(NO_FOLLOW_SRC)


def test_no_click_means_no_new_tab_to_follow():
    """F 的**反例**：一次点击都没有的产物不挂 —— 没有「新开的那一页」要跟，报它是噪音。"""
    src = ("STATES = [{'name': 'go', 'when': {'url_contains': '/a', 'text_contains': ['Go']},\n"
           "           'steps': [{'action': 'scroll', 'pixels': '400'}]}]\n")
    assert lint.check(src) == [], lint.check(src)


#: G / I 的样例共用这一组状态：有判据（不碰 H）、点击带选择器（不碰 E/F/I）。
ONE_GATED_STATE = {"name": "go", "when": {"url_contains": "/a", "text_contains": ["Go"]},
                   "steps": [{"action": "click", "target": {"selectors": ["#a"]}}]}


def test_a_machine_made_source_name_is_refused():
    """G：`FILLS[*].source` **认不出语义**、形状又是**机器起的名**。

    真站（2026-09-17）：gowizard 那个 ZIP 框的标签是个不透明的 MUI id ⇒ 名字成了
    `textfield_173838`，运营的 `zip`/`postcode` 一个都对不上 ⇒ 取不到资料、掉进随机池，
    美国站被填了英国邮编（`docs/生成脚本-取值缺陷-2026-09-17.md`）。
    """
    src = _artifact([ONE_GATED_STATE],
                    [{"name": "postcode", "source": "textfield_173838",
                      "target": {"selectors": ["input#postcode"]},
                      "fallback": [{"random": "postcode"}]}])
    assert "judge-source-autoname" in _codes(src), lint.check(src)


def test_source_names_the_skeleton_recognises_are_not_flagged():
    """G 的**反例**：语义名 / 别名表里的名字 / 人写的列名形状（`username`）都不挂。

    ⚠️ 这一条同时也是这条规则的**局限**，写在明处：`phone` vs `phone_number` 那种
    「语义名 vs 资料库列名」的错位，它**抓不到** —— 仓里**没有**资料库键的真相源
    （探针那张 `LIB_KEYS` 是 09-28 从线上 `formMessage` 手抄的，抄进来就是第二份真相源）。
    那种错位归运行期的 `template._ALIASES` 管（09-28 mapleporchdaily 就是它兜的）。
    `state2` 那种「语义词 + 后缀」也故意放过（同一句 `_kind_by_words` 的边界）；
    真要收紧它，那是改判据 —— 连同这条用例一起改，别顺手改 lint。

    ⚠️ `address1` 那一格是**另一种钉子**：它带数字、又不在语义词表里，只有靠
    `_alias_names()` 真读到了骨架那张 `_ALIASES` 才不挂 —— 而骨架是一份 `string.Template`
    （`STATES = $states` 那种占位符**不是合法 py**）⇒ 这张表要解析成功必须先做占位符替换。
    谁把那段替换弄坏（表变成空集），这一格立刻红。
    """
    for source in ("zip", "username", "email", "phone", "postcode", "age", "task_id",
                   "state2", "address1"):
        src = _artifact([ONE_GATED_STATE],
                        [{"name": "x", "source": source, "target": {"selectors": ["#x"]},
                          "fallback": [{"random": "postcode"}]}])
        assert "judge-source-autoname" not in _codes(src), (source, lint.check(src))


def test_a_source_named_after_the_fields_own_label_is_not_a_machine_name():
    """★ 2026-10-12（真事 `job-7c0cb8a45c25` · nitrouscompetitions.com/auth）：**标签蛇形化**出来的
    名字不是「框架自造名」—— 规则 G 要抓的是 `textfield_173838` 那种**与页面文字无关**的机器名。

    那单的两格：`address_line_1`（标签「Address line 1」）与
    `i_confirm_i_am_18_or_over_and_i_accept_t`（标签「I confirm I am 18 or over…」，被截断）
    —— 它们只因为**标签里本来就有数字**（1 / 18）就中了「带数字串 = 机器起的名」那一格，
    连打三轮把整单卡停。而运营的 form-file 拿 `address_line_1` 当列名**完全合理**
    （规则自己的文档就写着：这类「语义名 vs 资料列名」的错位它判不了）。

    ⚠️ 真阳性**一个都不许松**：名字与标签对不上的照旧报（下一格就是）。
    """
    for label, source in (("Address line 1", "address_line_1"),
                          ("I confirm I am 18 or over and I accept t",
                           "i_confirm_i_am_18_or_over_and_i_accept_t")):
        src = _artifact([ONE_GATED_STATE],
                        [{"name": source, "source": source, "label": label,
                          "target": {"selectors": ["#x"]}, "fallback": []}])
        assert "judge-source-autoname" not in _codes(src), (source, lint.check(src))
    #: 反向：标签与名字对不上（`textfield_173838` 那种）照旧报
    src = _artifact([ONE_GATED_STATE],
                    [{"name": "postcode", "source": "textfield_173838", "label": "ZIP code",
                      "target": {"selectors": ["input#postcode"]},
                      "fallback": [{"random": "postcode"}]}])
    assert "judge-source-autoname" in _codes(src), lint.check(src)


#: I 的三族脆地址（判据与骨架 `template._GENERATED_ID` / `_RENDER_INDEX_ID` / `_HOPS_LOW`
#: 是**同一份**）：框架渲染序号 id、以 `-数字` 结尾的自造 id、跳数 > 3 的 nth 链。
FRAGILE_ONLY = [
    "#__BVID__408",
    "#input-174",
    "#a > div:nth-of-type(1) > div:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(2)",
]


@pytest.mark.parametrize("selector", FRAGILE_ONLY)
def test_a_target_with_only_a_fragile_address_is_refused(selector):
    """I：这一格**只有一根随时会漂的地址** —— 重渲一次就换号（真站实测 `#input-167` →
    `#input-163`、`#combobox-input-182` → `#combobox-input-168`；9 跳那条长链**换个分辨率全挂**）
    ⇒ 这一步「页面上没找到」、整组步骤被静默跳过。
    """
    src = _artifact([{"name": "go", "when": {"url_contains": "/a", "text_contains": ["Go"]},
                      "steps": [{"action": "click", "target": {"selectors": [selector]}}]}])
    assert _codes(src) == ["judge-fragile-selectors"], lint.check(src)


def test_a_stable_candidate_beside_a_fragile_one_is_not_flagged():
    """I 的**反例**：还有一条稳的候选时不挂 —— 骨架**故意**把脆的排在最后当兜底
    （`template._selector_rank`：「页面真没给别的地址时，它是唯一够得着的路」，
    实测有一格 `telephoneNumber` 就只有它一个候选）。

    探针原来那句是「出现就报」（`artifact_lint.py:103-108`），照抄会把**留着兜底**的产物
    一起打回 ⇒ 判据收窄成「一条稳的都没有」。
    """
    src = _artifact([{"name": "go", "when": {"url_contains": "/a", "text_contains": ["Go"]},
                      "steps": [{"action": "click",
                                 "target": {"selectors": ["a.btn-primary", "#input-174"]}}]}])
    assert lint.check(src) == [], lint.check(src)


def test_judge_rule_table_is_the_mounted_nine():
    """挂进生成期出口的判据规则就这**九条**（C/D/H + A/E/F/G/I）——
    多一条 / 少一条都要有人来解释（与上面 `RULES` 那张表同一条规矩）。

    ★ 2026-10-15（修站那一刀④，spec §3.1）：I 那一格的**另一种判决**单列一条
    （`judge-fragile-no-basis` = 无据可依）—— 与 `judge-fragile-selectors`
    （有据但没用上）**分开**：两族的出路完全不同（一个改稿能修，一个只能删/请人/接受），
    `tools/fam_report.py` / `docs/根因库.md` 要分别计数。
    """
    assert list(lint.JUDGE_RULES) == [
        "judge-transient", "judge-fill-value", "judge-url-only",
        "judge-strict-unguarded", "judge-ungated-click", "judge-no-follow-tab",
        "judge-source-autoname", "judge-fragile-selectors", "judge-fragile-no-basis",
    ]


E_UNGATED_SRC = _artifact([{"name": "start", "when": None,
                            "steps": [{"action": "click",
                                       "target": {"text": "Continue", "role": "button",
                                                  "selectors": []}}]}])
G_AUTONAME_SRC = _artifact([ONE_GATED_STATE],
                           [{"name": "postcode", "source": "textfield_173838",
                             "target": {"selectors": ["input#postcode"]},
                             "fallback": [{"random": "postcode"}]}])
I_FRAGILE_SRC = _artifact([{"name": "go", "when": {"url_contains": "/a",
                                                  "text_contains": ["Go"]},
                            "steps": [{"action": "click",
                                       "target": {"selectors": ["#input-174"]}}]}])

NEW_JUDGE_CASES = [
    pytest.param("judge-strict-unguarded",
                 "def _fill(self, selector, value):\n"
                 "    return self.cdp.form(selector, value=value, strict=True)\n",
                 id="A-strict-unguarded"),
    pytest.param("judge-ungated-click", E_UNGATED_SRC, id="E-ungated-click"),
    pytest.param("judge-no-follow-tab", NO_FOLLOW_SRC, id="F-no-follow-tab"),
    pytest.param("judge-source-autoname", G_AUTONAME_SRC, id="G-source-autoname"),
    pytest.param("judge-fragile-selectors", I_FRAGILE_SRC, id="I-fragile-selectors"),
]


@pytest.mark.parametrize("code, src", NEW_JUDGE_CASES)
def test_each_new_judge_rule_is_load_bearing(monkeypatch, code, src):
    """变异验证（与上面那两条同一条规矩）：把这条规则从 `JUDGE_RULES` 里摘掉 ⇒
    它对应的样例**必须不再**被打回。摘不掉 = 报它的不是这条规则（用例在骗人）。
    """
    assert code in _codes(src), "这条样例本该命中 %s：%r" % (code, lint.check(src))
    monkeypatch.setattr(lint, "JUDGE_RULES",
                        tuple(name for name in lint.JUDGE_RULES if name != code))
    assert code not in _codes(src), "摘掉 %s 之后仍被打回 —— 说明报它的不是这条规则" % code


# ─── ★ 2026-09-30：有文字/标签锚的格**不算**「只剩一根漂的线」（zekear 那趟的现场）──────

def test_a_fragile_chain_with_a_text_anchor_is_not_a_violation():
    """那个「i agree」勾选框：页面上**没有 name / id**，探路只留下一条 9 跳 nth 链 ——
    而它**有 label**。打回它只会让 draft ↔ lint 来回打转（每一圈一次模型调用），
    产物本来就能按 label 找得到 ⇒ **有文字锚的不挂**。
    反面（正控）：同样脆、**没有**任何文字锚的 ⇒ 照旧要挂。
    """
    from agent import lint as lint_mod

    frag = ("div#customer-register > form:nth-of-type(1) > div:nth-of-type(3) > "
            "label:nth-of-type(1) > span:nth-of-type(1) > input:nth-of-type(1)")

    def say(target):
        states = [{"name": "signup-2",
                   "steps": [{"action": "form", "fill": "field", "target": target}]}]
        return [v.code for v in lint_mod._rule_i(states, {}, "x = 1\n")]

    assert say({"selectors": [frag], "label": "i agree"}) == []
    assert say({"selectors": [frag], "text": "i agree"}) == []
    assert say({"selectors": [frag]}) == ["judge-fragile-selectors"]


# ─── ★ 2026-10-13：「身上没字、标签在隔壁」的控件也算**有锚**（compareinsulation 那排圆点）──

def test_a_fragile_click_with_a_nearby_label_and_a_role_is_not_a_violation():
    """「身上没字、标签在隔壁」的控件也算**有锚**（compareinsulation 那排圆点的形状）。

    现场形状（2026-10-13，`job-37d8e6bfa080` 那一族）：一排 `role=radio` 的 16×16 圆点，
    `text` 空、标签（`Gas boiler` / `Electric` …）**只写在旁边**（`nearby_text`）。
    重放那一跳（产物 `_relocate` → `_intent_score`）**本来就认 `nearby_text`** ——
    缺的只是这里不认，于是「只有一根漂的线」照旧打回、draft ↔ lint 空转。

    ⚠️ **代表地址选哪一族**要跟闸对齐：那排圆点的 id 是 `#option-<时间戳>`（`-数字` 族），
    而按 2026-10-13 的裁定它**不进这道硬闸**（`_fragile` 的 docstring 写着：只留
    「框架渲染序号 id」与「>3 跳 nth 链」两族）。所以这里请 `#__BVID__408` 当代表 ——
    形状同族（无名 + 标签在旁 + role），地址是闸真会拦的那一族。

    尺度（用户当面定）：`nearby_text` **且** `role` 才算锚 —— 仓里实测过 nearby_text
    可能是**邻居的字**（隔壁 State 下拉、邻格的 `Address line 2`），role 是那道分辨器；
    缺一个就照旧打回（下面两条正控钉着）。
    """
    from agent import lint as lint_mod

    frag = "#__BVID__408"                 # 闸真认的那一族（Vue 渲染序号）

    def say(target):
        states = [{"name": "quiz-2",
                   "steps": [{"action": "click", "target": target}]}]
        return [v.code for v in lint_mod._rule_i(states, {}, "x = 1\n")]

    strip = {"selectors": [frag], "text": None, "role": "radio", "near": None}
    assert say(dict(strip, nearby_text=["Electric", "Gas boiler"])) == []
    # 正控①：只有裸 nearby_text、**没有 role** ⇒ 照旧打回（尺度不许悄悄放宽）
    assert say(dict(strip, role=None,
                    nearby_text=["Electric", "Gas boiler"])) == ["judge-fragile-selectors"]
    # 正控②：四样全空 ⇒ 照旧打回
    assert say(strip) == ["judge-fragile-selectors"]


# ── 出处三级（2026-10-15 修站那一刀，spec §3.2）─────────────────────────────

_CARD = {"label": "Postcode", "hint": "postcode", "placeholder": "SW1A 1AA",
         "nearby_text": ["Where do you live?"], "role": "textbox",
         "selectors": ['input[name="postcode"]', '#postcode-field',
                       'body:nth-of-type(1) > div:nth-of-type(2) > input:nth-of-type(1)']}


def test_observed_when_the_attribute_and_value_are_both_in_the_card():
    level, _ = lint.address_basis('input[name="postcode"]', _CARD, _CARD["selectors"])
    assert level == lint.OBSERVED


def test_unsupported_when_the_attribute_was_never_observed():
    """评审给的反例：文字能证明文字存在，**不能**证明一个没观察到的属性存在。"""
    card = {"nearby_text": ["Choose your preferred plan"]}
    level, why = lint.address_basis('input[name="preferred_plan"]', card, [])
    assert level == lint.UNSUPPORTED
    assert "name" in why or "属性" in why


def test_text_anchor_is_observed_because_replay_uses_it():
    card = {"label": "Continue", "role": "button"}
    level, _ = lint.address_basis("text=Continue", card, [])
    assert level == lint.OBSERVED


def test_derived_when_the_value_is_observed_but_the_attribute_is_a_guess():
    """`hint` = `el.name || el.id`（cdp 那边合并了）⇒ `[name=X]` 与 `#X` 都只是二选一。"""
    card = {"hint": "textField-173862"}
    level, why = lint.address_basis('input[name="textField-173862"]', card, [])
    assert level == lint.DERIVED
    assert "二选一" in why or "推" in why
    assert lint.address_basis("#textField-173862", card, [])[0] == lint.DERIVED


def test_position_only_selector_has_no_assertions():
    """纯位置不主张任何身份 —— 它不进出处判定（脆不脆是 `_fragile` 的活）。"""
    card = {"selectors": ["body:nth-of-type(1) > header:nth-of-type(1) > a:nth-of-type(1)"]}
    level, _ = lint.address_basis(
        "body:nth-of-type(1) > header:nth-of-type(1) > a:nth-of-type(1)", card,
        card["selectors"])
    assert level == lint.OBSERVED
    assert lint.assertions_of("body:nth-of-type(1) > a:nth-of-type(1)") == []


def test_empty_card_makes_anything_unsupported():
    assert lint.address_basis("header a", {}, [])[0] == lint.UNSUPPORTED


# ── 修复轮 1（2026-10-15 评审：1 Critical + 2 Important）────────────────────

def test_unreadable_constructs_are_never_observed():
    """★ C1（评审实测的 fail-open）：判据**读不懂**的构造不许静默变成「零断言」。

    这几种都**看着稳**（`_fragile` 只认自造 id 与长 nth 链，拦不到）、都**主张了身份**，
    只是判据一个都不认识。以前它们吐空断言，而 `address_basis` 那边「零断言 ⇒ 只说了位置」
    的前提是「零断言 ⟺ 纯位置」—— 前提被踩穿，于是对着一张**空卡**也判 `OBSERVED`，
    还配一句「没主张任何身份」的假人话。读不懂 = **不算量到过**。
    """
    for sel in ('input[data-x]',            # 属性存在性：没有 `=`
                'input[name^="post"]',      # 非 `=` 运算符
                'a[href*=signin]',
                '[name~=postcode]',
                'div:has(> a)',             # 位置以外的伪类
                '*',
                ''):
        level, why = lint.address_basis(sel, {}, [])
        assert level == lint.UNSUPPORTED, sel
        assert level != lint.OBSERVED, sel
        assert why.strip(), sel


def test_pseudo_positional_ones_are_still_not_assertions():
    """C1 的反向钉子：`:nth-of-type(…)` / `:nth-child(…)` 是**位置**，照旧不算断言（也别卷进 unparsed）。"""
    assert lint.assertions_of("body:nth-of-type(1) > a:nth-of-type(1)") == []
    assert lint.assertions_of("li:nth-child(2n+1)") == []
    assert "unparsed" not in [k for k, _ in lint.assertions_of("li:nth-child(2n+1)")]


def test_id_and_attribute_id_are_the_same_thing():
    """★ I1（spec §3.2）：`#id` 与 `[id=id]` 是**同一件事** —— 两个方向都要能对上。"""
    assert lint.address_basis(
        "#postcode-field", {}, ['[id="postcode-field"]'])[0] == lint.OBSERVED
    assert lint.address_basis(
        'input[id="postcode-field"]', {}, ["#postcode-field"])[0] == lint.OBSERVED
    assert ('id', 'postcode-field') in lint.assertions_of('[id="postcode-field"]')


def test_the_tag_must_match_the_observed_anchor():
    """★ I2：锚对上还不够 —— **标签也要对得上**（重放是整串 `Element.matches()`）。

    `div[name="postcode"]` 对观测原文 `input[name="postcode"]`：页面上真有
    `div[name=postcode]` 时就会**静默点到另一个元素**（比被换掉的那根更坏）。
    判法要**窄**：只在观测侧确实写出了标签时比 —— `[name=…]` 这种没带标签的锚没有可比的。
    """
    obs = ['input[name="postcode"]']
    assert lint.address_basis('div[name="postcode"]', {}, obs)[0] == lint.UNSUPPORTED
    assert lint.address_basis('input[name="postcode"]', {}, obs)[0] == lint.OBSERVED
    assert lint.address_basis('div[name="postcode"]', {}, ['[name="postcode"]'])[0] == lint.OBSERVED


def test_a_hash_or_dot_inside_a_value_is_not_an_id_or_a_class():
    """同族的 fail-open（自审补的）：`a[href="#top"]` 里的 `#top` 是**值**，不是 id。

    解析器自己编出 `("id","top")` 的话，它甚至会给**观测侧**凭空添一条证据 ——
    于是编的地址又有了「出处」。与 C1 同一条立场：判据不许自己造事实。
    """
    assert ('id', 'top') not in lint.assertions_of('a[href="#top"]')
    assert ('class', 'b') not in lint.assertions_of('[data-x="a.b"]')
    assert lint.address_basis("#top", {}, ['a[href="#top"]'])[0] == lint.UNSUPPORTED


# ── ★ 2026-10-15 修站那一刀 · Task 4：判据先验出处，再判脆不脆（spec §3.1）─────

def _src_with(state_name, selector_line, header=True):
    return (
        "STATES = [\n"
        "  { 'name': '%s',\n"
        "    'steps': [ { 'action': 'click',\n"
        "                 'target': { 'role': 'a', 'near': 'header',\n"
        "                             'selectors': [ %s ] } } ] } ]\n"
        % (state_name, selector_line)
    ) + ("FILLS = {}\n" if header else "")


_FRAGILE = "'body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)'"
_OBS = {"states": [{"name": "auth", "steps": [
    {"action": "click", "target": {"role": "a", "near": "header", "text": None,
                                   "selectors": ["body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"]}}]}],
    "fills": {}}


def test_no_basis_when_the_frozen_observation_has_nothing():
    """无据可依那一支：不许再喊「补一条稳的」（那句只能逼出编的）。"""
    src = _src_with("auth", _FRAGILE)
    got = [v for v in lint.check(src, observed=_OBS) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1
    assert "补一条" not in got[0].message
    assert "删掉" in got[0].message and "请人" in got[0].message and "接受" in got[0].message


def test_fabricated_selector_does_not_escape_the_judge():
    """★ 三步回归：把编的那根塞进候选，**仍然**不算原始观察证据（spec §6）。"""
    src = _src_with("auth", "%s, 'header a'" % _FRAGILE)
    got = [v for v in lint.check(src, observed=_OBS) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1, "编了一根『看着稳的』就逃出判据 —— 这正是这一刀要堵的路"


def test_a_card_with_a_stable_selector_is_not_called_no_basis():
    """★ 修复轮 1（Minor #1）：卡上有一根**验过唯一**的地址 ⇒ 不许说「量不到更稳的地址」。

    卡上那几根是 `tools/cdp` 的 `address()` **验过唯一**才记下来的，判「有没有答案」时漏看它
    就会**对外说假话**：卡上明明有 `a[href='/comp']`，人话却说这一步「量不到更稳的地址」，
    模型也因此拿不到那条正路（该说的是「把卡上那根拿来用」——`_with_evidence_say` 把卡摆出来）。

    ⚠️ 这一格在 `find_observed` 「地址相等」那版下够不着（卡根本取不到）；Ruling 13 落地后立刻可达。
    """
    src = _src_with("auth", _FRAGILE)
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": ["a[href='/comp']"]}}]}], "fills": {}}
    got = lint.check(src, observed=obs)
    assert [v.code for v in got if v.code == "judge-fragile-no-basis"] == [], \
        [v.message for v in got]
    one = [v for v in got if v.code == "judge-fragile-selectors"]
    assert len(one) == 1
    assert "a[href='/comp']" in one[0].message        # 卡摆到模型眼前了


def test_a_card_with_only_the_fragile_chain_still_has_no_basis():
    """★ 修复轮 1 的**不许放松**那一半：卡上没有不脆的候选 ⇒ 照旧「无据可依」。

    上一条与本条是同一次改动的两面（卡上的候选**只有不脆的**才算答案）——
    nitrous 那族（卡上只有那根脆链、产物那根与观测原文逐字相同）与空卡那族都不得被顺带放宽。
    """
    # ① nitrous 那族：卡上只有那根脆链
    got = [v for v in lint.check(_src_with("auth", _FRAGILE), observed=_OBS)
           if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1 and "量不到更稳的地址" in got[0].message
    # ② 空卡那族（原件里那一步本来就没有 target / 找不到这一步）
    empty = {"states": [{"name": "auth", "steps": [{"action": "click", "target": {}}]}], "fills": {}}
    gone = [v for v in lint.check(_src_with("auth", _FRAGILE), observed=empty)
            if v.code == "judge-fragile-no-basis"]
    assert len(gone) == 1 and "它身上没有名字" in gone[0].message


def test_the_no_basis_message_carries_the_values_not_only_the_keys():
    """★ 修复轮 1（Minor #3）：卡上有东西时，「量到的只有 …」要把**值**带上。

    只说键名（会印成「量到的只有 `role`、`selectors`」）模型拿不到值，也就没法照它改 ——
    这一刀治的正是「说了等于没说」。⚠️ **空卡那一支的人话一个字没动**（上一条钉着）。
    """
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "link", "selectors": [_FRAGILE.strip("'")]}}]}],
        "fills": {}}
    got = [v for v in lint.check(_src_with("auth", _FRAGILE), observed=obs)
           if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1
    assert "`role=link`" in got[0].message, got[0].message
    assert "selectors=%s" % _FRAGILE.strip("'") in got[0].message, got[0].message


@pytest.mark.parametrize("card,needle", [
    pytest.param({"label": "Competitions", "selectors": ["#get-started"]},
                 "`label=Competitions`", id="有锚"),
    pytest.param({"role": "a", "selectors": ["a[href='/comp']"]},
                 "`selectors=a[href='/comp']`", id="只有一根不脆的地址"),
])
def test_no_basis_with_a_card_that_has_something_says_to_use_it(card, needle):
    """★ Ruling 14：卡上**有货**、而这一版的地址一条都不是量到的 ⇒ 人话不许打脸。

    病（我自报、控制方裁的那条）：卡上有锚 + 一根不脆的地址，产物那几根却**全被判「不是量到的」
    剔掉**（落第二格 `not kept`）⇒ 人话是「这一步探路那一刻**量不到更稳的地址** —— 量到的只有
    `selectors=#get-started」—— **前半句被自己后半句打脸**，而且三条正路里没有最该说的那条
    「**把卡上那根拿来用**」。

    ⚠️ 路由**不动**：这一格仍然报 `judge-fragile-no-basis`（spec §3.1 第二格 ——
    「你这一版的地址无据可依」这个读法本来就对），这一条换的只是**话**。
    ⚠️ 「卡上有没有货」用的就是第一格那把尺子（`_card_has_nothing`），不另造口径 ——
    所以 nitrous 那族（卡上只有那根脆链）**不会**被引到这句「先把卡上那根拿来用」上。
    """
    src = _src_with("auth", "'a#made-up'")          # 编的 id ⇒ 候选一条不剩
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": dict(card, near="header")}]}], "fills": {}}
    got = [v for v in lint.check(src, observed=obs) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1, [v.message for v in lint.check(src, observed=obs)]
    said = got[0].message
    assert "量不到更稳的地址" not in said, said       # 前半句不许再打自己的后半句
    assert "先把卡上那根拿来用" in said, said
    assert needle in said, said                     # 卡上的值摆到眼前（能照它改）
    #: 三条正路仍在 —— 它们是「实在没有能用的」时候的兜底
    assert "删掉" in said and "请人" in said and "接受" in said


def test_no_basis_with_a_card_that_has_nothing_keeps_todays_wording():
    """★ Ruling 14 的另一半：卡上**什么都没有** ⇒ 今天那两句**一个字都不许动**。

    「什么都没有」用的是与第一格同一把尺子（`_card_has_nothing`：没锚、卡上也没有不脆的候选）。
    那时说的「量不到更稳的地址」是**真话**（真没有更稳的），「补一条稳的」也**没有正确答案**
    （只能逼出编的）⇒ 两句原话都逐字节钉在这里：
      · 空卡（连 selectors 都没有）⇒ 「它身上没有名字、旁边没有字、也没有一条验过唯一的候选」；
      · 只有一根脆链（nitrous 那族）⇒ 「量到的只有 `role=a`、`selectors=<那根链>`」——
        这一支里列的全是**不能用**的东西（role + 那根脆链），所以也不打脸；
        它还是 `Minor #3` 刚把**值**带上的那一句，不许被新话挤掉。
    """
    head = ("组「auth」第 1 步：这一步探路那一刻**量不到更稳的地址** —— %s。"
            "**别编一根**：编出来的地址没人验过，而它会让「这一步还能不能重放」这件事"
            "在屏幕上看起来是好的。三条正路，任选其一："
            "① 这一步若只是辅助动作（把某个东西滚进视口那类）⇒ **删掉它**；"
            "② **请人给一根**（在闸上说一句，人正看着那一页）；"
            "③ 如实**接受**（在闸上说「接受地址 auth#0」—— 只有人能接受，且地址或证据一变就失效）。")
    empty = {"states": [{"name": "auth", "steps": [{"action": "click", "target": {}}]}], "fills": {}}
    for obs, said in ((empty, "它身上没有名字、旁边没有字、也没有一条验过唯一的候选"),
                      (_OBS, "量到的只有 `role=a`、`selectors=%s`" % _FRAGILE.strip("'"))):
        got = [v for v in lint.check(_src_with("auth", _FRAGILE), observed=obs)
               if v.code == "judge-fragile-no-basis"]
        assert len(got) == 1
        assert got[0].message == head % said, got[0].message


def test_the_field_wording_says_to_use_the_card_when_the_card_has_something():
    """★ Ruling 14 的**字段那一支**：同一条病（打脸 + 没说出「把卡上那根拿来用」），
    同一条改法、同一把「有没有货」的尺子 —— 而**两条正路**照旧（字段这一支没有「接受」）。
    """
    frozen = {"states": [], "fills": {"postcode": {"target": {
        "label": "Postcode", "hint": "postcode", "selectors": ['input[name="postcode"]']}}}}
    src = _FILL_SRC.replace(_FRAGILE, "'#made-up'")     # 编的 id ⇒ 候选一条不剩
    got = [v for v in lint.check(src, observed=frozen) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1, [v.message for v in lint.check(src, observed=frozen)]
    said = got[0].message
    assert said.startswith("字段「postcode」："), said
    assert "量不到更稳的地址" not in said, said
    assert "先把卡上那根拿来用" in said, said
    assert 'input[name="postcode"]' in said, said
    assert "接受地址" not in said, said       # 字段这一支没有那个口子，不许写做不到的话
    #: 卡上什么都没有那一支照旧（不因为这次改动被带上新话）
    gone = [v for v in lint.check(_FILL_SRC, observed={"states": [], "fills": {}})
            if v.code == "judge-fragile-no-basis"]
    assert len(gone) == 1 and "它身上没有名字" in gone[0].message, gone


# ── ★ 2026-10-15 修复轮 3（Ruling 16）：**逐字相等就是量到的** ────────────────────
# 病：产物用的**就是探路那一刻量到的那一根**，只因为判据自己的解析器读不懂 `:has(`
# 就被判「无据可依」—— **误阻断**，而且落在交付过的真产物上（47 份里 18 条）。
# 错在我们这一侧：「我读不懂」是判据的局限，不该记到产物头上（与「量到了不许当成量不到」同一条纪律）。

_HAS_CARD = {"selectors": ["label:has(#mco-5e266a84)"]}


def test_an_address_that_is_the_observed_one_verbatim_is_observed():
    """★ Ruling 16：逐字相同 ⇒ `OBSERVED`（排在 `unparsed` 判定**之前**）。"""
    level, why = lint.address_basis("label:has(#mco-5e266a84)", _HAS_CARD,
                                    _HAS_CARD["selectors"])
    assert level == lint.OBSERVED, why
    assert "这就是探路那一刻量到的那一根" in why, why


def test_the_verbatim_match_only_norms_whitespace():
    """⚠️ 归一**只有**压空白 + 去首尾 —— 归一越多，「逐字」这个保证越弱。"""
    card, text = _HAS_CARD, _HAS_CARD["selectors"]
    assert lint.address_basis("  label:has(#mco-5e266a84)\n", card, text)[0] == lint.OBSERVED
    #: 大小写不一样 ⇒ **不算**逐字（不做 casefold）
    assert lint.address_basis("LABEL:has(#mco-5e266a84)", card, text)[0] == lint.UNSUPPORTED
    #: 值不一样 ⇒ 不算逐字 ⇒ 照旧走「读不懂 = 不算量到过」那一套
    assert lint.address_basis("label:has(#mco-other)", card, text)[0] == lint.UNSUPPORTED


def test_the_observed_address_itself_is_never_reported():
    """★ Ruling 16 的正面：卡上只有一根**判据读不懂**的地址、而产物用的就是它 ⇒ **不报**。

    （`_src_with` 造的是碎片，会另外命中 `judge-no-follow-tab` —— 那是另一条规则，与本条无关；
    这里钉的是**判据那一族一条都不许出**。）
    """
    src = _src_with("auth", "'label:has(#mco-5e266a84)'")
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": ["label:has(#mco-5e266a84)"]}}]}], "fills": {}}
    codes = [v.code for v in lint.check(src, observed=obs) if v.code.startswith("judge-fragile")]
    assert codes == [], codes


def test_an_invented_address_beside_the_same_card_is_still_reported():
    """★ Ruling 16 的**反向钉**：同一张卡，产物用一根**编的**地址 ⇒ 照旧报。

    （逐字相等这条捷径**只认卡里有的那一根**；编的 `a#made-up` 不在卡里 ⇒ 走 unparsed/断言那一套。）
    """
    src = _src_with("auth", "'a#made-up'")
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": ["label:has(#mco-5e266a84)"]}}]}], "fills": {}}
    got = [v for v in lint.check(src, observed=obs) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1, [v.message for v in lint.check(src, observed=obs)]


def test_an_address_that_touches_the_card_is_let_through():
    """★ Ruling 13 的兑现点：模型按 spec §3.3 换成一根**沾着卡的**地址 ⇒ 判据**放行**。

    病：`find_observed` 旧版第①步还要求地址相等 ⇒ 地址一被改写就取不到卡 ⇒ 卡为空 ⇒
    `judge-fragile-no-basis` ⇒ `draft ↔ lint` 两趟 `lint_cap`（**spec 自己要躲的那个死循环**）。
    这里钉的是「沾着卡的地址」这一条正路**真的走得通**（不是靠放宽验地址 —— 见下一条：
    编出来的那根照样 `UNSUPPORTED`）。
    """
    src = _src_with("auth", "'a#get-started'")
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": ["#get-started"]}}]}], "fills": {}}
    assert [v.code for v in lint.check(src, observed=obs) if v.code.startswith("judge-fragile")] == [], \
        [v.message for v in lint.check(src, observed=obs)]
    #: 反过来：**没沾卡的**改写（卡上没有的东西）照旧算没量到过 —— 放行不是放宽
    bad = _src_with("auth", "'a#made-up'")
    got = [v for v in lint.check(bad, observed=obs) if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1, [v.message for v in lint.check(bad, observed=obs)]


def test_with_evidence_the_old_message_stays():
    """有据但没用上那一支：还是老码、还是「补一条稳的」，但**把卡摆出来**。"""
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header", "label": "Competitions",
                                       "selectors": [_FRAGILE.strip("'")]}}]}], "fills": {}}
    src = _src_with("auth", _FRAGILE)
    got = [v for v in lint.check(src, observed=obs) if v.code == "judge-fragile-selectors"]
    assert len(got) == 1
    assert "Competitions" in got[0].message           # 卡摆到眼前了


def _dump(src, **kw):
    """整表快照：`(行号, 码, message, snippet)` —— 一条一条逐字节，别只挑一条看。"""
    return [(v.line, v.code, v.message, v.snippet) for v in lint.check(src, **kw)]


def test_without_observed_behaviour_is_byte_identical():
    """不传 `observed` ⇒ 与今天**逐字节一致**（老 job / 老调用点一点不受影响）。

    ⚠️ **钉法**（任务书的断言与实测冲突，这里如实改成能钉住同一件事的写法）：
    任务书写的是 `[v.code for v in lint.check(src)] == ["judge-fragile-selectors"]`，
    而这份样例 src 是一段**碎片**（有 `click`、没有 `_follow_new_tab(`）⇒ 它本来还会命中
    `judge-no-follow-tab`（那是**另一条规则**的事），逐字相等永远钉不住。
    但**整表相等是可钉的**，而那是本 Task 的硬验收 ⇒ 改成整表比（`_dump`）：
    `check(src)` 与 `check(src, observed=None)` 的 dump 完全相等，且整表里没有新码。
    """
    src = _src_with("auth", _FRAGILE)
    plain, frozen = _dump(src), _dump(src, observed=None)
    assert plain == frozen, "不传 observed 与传 observed=None 必须是同一份输出（逐字节）"
    assert [one[:2] for one in plain] == [(5, "judge-fragile-selectors"),
                                          (5, "judge-no-follow-tab")]
    assert "judge-fragile-no-basis" not in [one[1] for one in plain]
    #: 那一条违规本身：行号 / snippet / **message 一个字不动**（老话照旧，不含新那半句）
    got = [one for one in plain if one[1] == "judge-fragile-selectors"][0]
    assert (got[0], got[3]) == (5, _FRAGILE.strip("'"))
    assert got[2] == (
        "组「auth」第 1 步**只有一根随时会漂的地址**（框架自造 id / 超长"
        "nth-of-type 链）：同一格重渲一次就换号（真站实测 `#input-167` →"
        "`#input-163`），换了分辨率的长链还会整条挂 —— 到那时这一步就是"
        "「页面上没找到」，整组步骤被静默跳过。给它补一条**稳的**地址"
        "（`input[name=…]` / `input[aria-label=…]` 这类）。")


def test_the_same_fragile_selector_in_two_states_is_reported_twice():
    """同一根地址在两处 ⇒ 报两条，**一处被接受不许顺带放行另一处**（Review Focus ③）。

    ⚠️ **钉在哪一层**（任务书的断言与实测冲突，这里如实说明）：任务书写的是
    `len(lint.check(src, observed=obs) …) == 2`，而这一条在 `check` 那一层**永远不成立** ——
    `_line_of_any` 对**同一根地址**永远给「它第一次出现的行」（这份 src 里两处都落在第 2 行），
    而 `check` 有一条**既有**的去重规矩：「同一行同一条规则只报一次」
    （实测：`_rule_i` 层两条、`check` 层一条）。那条规矩是本刀**不许动**的老行为。
    ⇒ 所以在**判据自己**那一层钉（与上面那两条「有锚不挂」的用例同一层）：
    去重按 `(组名, 步序)`、**不按地址** —— 同一根地址在两处各自成案，
    所以「接受了一处」在结构上不可能顺带放行另一处。

    ⚠️ 任务书这一段的末尾还调了 `lint.check(src, observed=obs, accepted=[…])` ——
    **`accepted=` 是 Task 6 的参数**（本 Task 的 ③ 那条自己写着「这一刀先不加」），
    那半条用例随 Task 6 一起落。这里钉的是它的前提。
    """
    src = (
        "STATES = [\n"
        "  { 'name': 'a', 'steps': [ { 'action': 'click', 'target': { 'selectors': [%s] } } ] },\n"
        "  { 'name': 'b', 'steps': [ { 'action': 'click', 'target': { 'selectors': [%s] } } ] } ]\n"
        "FILLS = {}\n" % (_FRAGILE, _FRAGILE)
    )
    states = [{"name": "a", "steps": [{"action": "click", "target": {"selectors": [_FRAGILE.strip("'")]}}]},
              {"name": "b", "steps": [{"action": "click", "target": {"selectors": [_FRAGILE.strip("'")]}}]}]
    obs = {"states": states, "fills": {}}
    got = [v for v in lint._rule_i(states, {}, src, obs)
           if v.code == "judge-fragile-no-basis"]
    assert len(got) == 2
    assert "组「a」第 1 步" in got[0].message and "组「b」第 1 步" in got[1].message
    #: 顺带钉住 `check` 那一层的**既有**行为没被这一刀改掉（同一行同一条只报一次）
    assert len([v for v in lint.check(src, observed=obs)
                if v.code == "judge-fragile-no-basis"]) == 1


def test_steps_without_a_target_are_never_reported():
    """`goto` / `observe` 那些步没有 target —— 判据不许在它们身上报出处问题（Review Focus ④）。"""
    src = ("STATES = [ { 'name': 'a', 'steps': [ { 'action': 'goto', 'target': { 'url': 'https://x.test/' } } ] } ]\n"
           "FILLS = {}\n")
    assert [v.code for v in lint.check(src, observed={"states": [], "fills": {}}) ] == []


def test_the_new_code_walks_the_same_switchboard(monkeypatch):
    """新码同样走 `JUDGE_RULES` 那张表（与那五条同一条规矩：表里没有的不报）。

    ⚠️ 它进不了 `test_each_new_judge_rule_is_load_bearing` 那张参数表：那一族跑的是
    `lint.check(src)`（**不传** `observed`），而这一支只在有冻结观察时才存在。
    """
    src = _src_with("auth", _FRAGILE)
    assert "judge-fragile-no-basis" in [v.code for v in lint.check(src, observed=_OBS)]
    monkeypatch.setattr(lint, "JUDGE_RULES",
                        tuple(n for n in lint.JUDGE_RULES if n != "judge-fragile-no-basis"))
    assert "judge-fragile-no-basis" not in [v.code for v in lint.check(src, observed=_OBS)]


# ── 同一个判据的**字段那一支**（`FILLS`）：卡从**冻结那份**的 `fills` 里取 ───────────

_FILL_SRC = ("STATES = []\n"
             "FILLS = { 'postcode': { 'name': 'postcode',\n"
             "                        'target': { 'selectors': [ %s ] } } }\n" % _FRAGILE)


def test_a_fill_with_no_basis_gets_the_same_verdict_but_field_wording():
    """字段那一支的「无据可依」：判决与 states 那支一致，**话按字段说**，而且
    ①不说「补一条稳的」（那句只能逼出编的）；②**不许**教人「接受地址 X#N」——
    接受那个口子（`no_basis_slots` / `_accept_from_note`）是按 `(组名, 步序)` 认的，
    **字段这一支没有对应的名字可认**：写一句做不到的话，正是这一刀要治的病。
    """
    frozen_card = {"fills": {"postcode": {"target": {"selectors": [_FRAGILE.strip("'")]}}}}
    got = [v for v in lint.check(_FILL_SRC, observed=dict(frozen_card, states=[]))
           if v.code == "judge-fragile-no-basis"]
    assert len(got) == 1
    assert got[0].message.startswith("字段「postcode」："), got[0].message
    assert "补一条" not in got[0].message
    assert "接受地址" not in got[0].message
    assert "删掉" in got[0].message and "请人" in got[0].message
    # 冻结那份里**根本没有这一格** ⇒ 取不到卡 ⇒ 照样本判决（fail-closed）
    gone = [v for v in lint.check(_FILL_SRC, observed={"states": [], "fills": {}})
            if v.code == "judge-fragile-no-basis"]
    assert len(gone) == 1 and "它身上没有名字" in gone[0].message, gone
    # 不传 observed ⇒ 老路：还是老码、还是老话（与 states 那一支同一条规矩）
    old = [v for v in lint.check(_FILL_SRC) if v.code == "judge-fragile-selectors"]
    assert len(old) == 1 and "字段「postcode」" in old[0].message and "补一条" in old[0].message


# ── Task 5：Derived 的标注通道 —— 放行，但按「未证实」记（spec §3.3a）─────────────
#
# 为什么不报 violation：报了它就停在 `draft ↔ lint` 环里、**永远到不了自测** ——
# 而 Derived 的验证恰恰落在自测那一跳。所以这一格只**记一笔**，让它带着标注往下走。


def test_unverified_lists_derived_only_steps():
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"hint": "textField-173862", "role": "a"}}]}], "fills": {}}
    src = _src_with("auth", "'input[name=\"textField-173862\"]'")
    got = lint.check_unverified(src, obs)
    assert len(got) == 1
    assert "textField-173862" in got[0] and "二选一" in got[0]


def test_unverified_is_empty_without_observed():
    assert lint.check_unverified(_src_with("auth", "'input#x'"), None) == []


def test_unverified_does_not_misread_the_verbatim_observed_selector():
    """★ Ruling 16：与观测原文**逐字相同**的那一根是**量到的**（`OBSERVED`）。

    `check_unverified` 必须走**同一个** `address_basis`（不另判一遍）—— 另判一遍的话，
    「把观测原文原样抄回来」那一根会被说成「推出来的」（它的值确实等于 `hint`）。
    """
    src = _src_with("auth", "'#textField-173862'")
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"hint": "textField-173862",
                                       "selectors": ["#textField-173862"]}}]}], "fills": {}}
    assert lint.check_unverified(src, obs) == []


def test_unverified_is_reported_beside_a_fabricated_sibling():
    """「全靠 Derived 撑着」的口径**就是** `_rule_i` 那一支：`UNSUPPORTED` 的**不算采信**
    （那边压根不把它放进 `kept`）⇒ 旁边还挂着一根编的，照样是「全靠推的撑着」、照样要记一笔。

    实测（Task 5，对着计划里那版只判 `all(one == DERIVED for one in levels)` 的实现跑）：
    这一格 `lint.check` **一根地址违规都不报**（`_rule_i` 放行了），而那版回的是 `[]`
    —— **放了却不记**，正是 §3.3a 要治的那件事。
    """
    src = _src_with("auth", "'input[name=\"textField-173862\"]', 'div[data-junk=\"1\"]'")
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"hint": "textField-173862"}}]}], "fills": {}}
    # 脆弱那两条一条都不响（`_src_with` 那份小 src 会撞上 `judge-no-follow-tab`，与地址无关）
    assert [v.code for v in lint.check(src, observed=obs)
            if v.code.startswith("judge-fragile")] == []
    got = lint.check_unverified(src, obs)
    assert len(got) == 1 and "textField-173862" in got[0]


def test_unverified_covers_the_field_side_too():
    """`FILLS` 那一支是**同一条判决的另一半**（`_rule_i` 字段那格本来就写着「由
    `check_unverified` 记一笔」）：只记 states 的话，字段那一支就是**放了却不记**。

    实测（Task 5，对着计划里那版只走 states 的实现跑）：`lint.check` 回空、那版也回 `[]`。
    """
    src = ("STATES = []\n"
           "FILLS = { 'postcode': { 'name': 'postcode',\n"
           "  'target': { 'selectors': ['input[name=\"textField-173862\"]'] } } }\n")
    obs = {"states": [], "fills": {"postcode": {"target": {"hint": "textField-173862"}}}}
    assert lint.check(src, observed=obs) == []
    got = lint.check_unverified(src, obs)
    assert len(got) == 1 and got[0].startswith("字段「postcode」：") \
        and "textField-173862" in got[0]


def test_unverified_does_not_crash_on_a_source_whose_tables_are_unreadable():
    """坏 py / 动态构造 ⇒ **不猜**（与 `_judge_violations` 那条同一规矩：读不出来就不报）。

    `STATES` 写成标量时 `_judge_violations` 把它归一成空表（判据**不炸**）——
    `check_unverified` 必须同一条待遇：它是 `_lint` 每一趟都要走的一步，
    在这里抛 `TypeError` 就是**整趟运行死在读不懂的产物上**（而那正是判据
    「不猜」那条纪律要躲的事：读不懂 = 不说，不是崩）。
    """
    obs = {"states": [], "fills": {}}
    assert lint.check("STATES = 5\nFILLS = {}\n", observed=obs) == []      # 今天就不炸
    assert lint.check_unverified("STATES = 5\nFILLS = {}\n", obs) == []
    assert lint.check_unverified("STATES = []\nFILLS = [1]\n", obs) == []


# ── ★ 2026-10-15 修站那一刀 · Task 6：**接受只能由人给**（spec §3.5）─────────────
#
# 判据这一侧只有三件事：① 人接受过（且四元组没变）⇒ **这一步一条违规都不剩**；
# ② 四元组一变 ⇒ **回到报**；③ 把「现在有哪几处等着被接受」写下来（`no_basis_slots`）——
# `_accept_from_note` 认的就是这一份（Ruling 2：没人写它，那句约定**永远空转**）。
#
#: 判据 F（`judge-no-follow-tab`）要的骨架标记：与地址毫无关系，但手写的小 src 不带它就会
#: 多出一条违规 ⇒ 那就没法断言「**整张表**」（★ 修复轮 1：只按码过滤的断法看不见
#: 「接受生效没生效」的另一半 —— 实测第一格上 no-basis 没了、`judge-fragile-selectors` 接着报）。
_FOLLOW = "def _follow_new_tab(self):\n    pass\n"


def test_no_basis_slots_says_which_address_the_gate_can_accept():
    """`no_basis_slots` = 「现在有哪几处等着被接受」，每条绑死四元组（spec §3.5）。

    ⚠️ 只有 `states` 那一支有它：`FILLS` 那一格的把手是**键**、不是 (组名, 步序)，
    `_fill_no_basis_say` 因此**只写两条正路**（那条「接受」写上去就是一句做不到的话）。
    """
    src = _src_with("auth", _FRAGILE)
    [slot] = lint.no_basis_slots(src, _OBS)
    assert (slot["state"], slot["index"]) == ("auth", 0)
    assert slot["selector"] == _FRAGILE.strip("'")
    assert isinstance(slot["card"], str) and slot["card"], slot     # 卡指纹（要进 state 的那份）
    #: 没有冻结那份 ⇒ **没人能接受**（不猜：连绑哪一根都不知道）
    assert lint.no_basis_slots(src, None) == []
    #: 已经接受过的那一格**不再等**（人再说一遍也只是原地覆盖）
    assert lint.no_basis_slots(src, _OBS, accepted=[slot]) == []


def test_a_step_that_has_something_to_use_is_not_a_slot_to_accept():
    """口径与 `_rule_i` **同一支**：卡上有一根验过唯一的地址时，这一格**根本不报**无据可依
    （`test_a_card_with_a_stable_selector_is_not_called_no_basis` 钉着那一半）——
    这里钉的是同一条判决的另一半：它也**不进待接受清单**（否则人会去接受一处没人问过的东西）。
    """
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": ["a[href='/comp']"]}}]}], "fills": {}}
    src = _src_with("auth", _FRAGILE)
    assert [v.code for v in lint.check(src, observed=obs)
            if v.code == "judge-fragile-no-basis"] == []          # 那一半：不报
    assert lint.no_basis_slots(src, obs) == []                     # 这一半：不等接受


def test_accepted_step_is_not_reported():
    """人接受过 ⇒ **这一步一条违规都不剩**（spec §3.5）。

    ⚠️ 任务书那一行原样写的是 `"card": "x"` —— 实测**对不上**（Task 6 报告里那条最小复现）：
    判据绑的是 `fix.card_fingerprint(card)`，这一格的指纹是
    `('body:nth-of-type(1) > … > a:nth-of-type(1)',)`（**元组**，`fix.py:1190`），
    手写一个 `"x"` 永远不命中 ⇒ 这条用例**无论实现怎么写都不可能绿**。
    照真相改：那一格走**真生产者** `no_basis_slots`（`_no_basis_say` 报的 handle 就是它写的）。

    ⚠️ 断言是**整张表**（★ 修复轮 1）：按码滤成 `judge-fragile-no-basis` 的写法看不见
    「接受生效没生效」的另一半 —— 实测这一格（空卡 + 产物那根脆链是 `OBSERVED` +
    target 没有语义锚）上 no-basis 那条没了、**`judge-fragile-selectors` 接着报**。
    """
    src = _src_with("auth", _FRAGILE) + _FOLLOW
    assert [v.code for v in lint.check(src, observed=_OBS)] == ["judge-fragile-no-basis"]
    [slot] = lint.no_basis_slots(src, _OBS)
    assert lint.check(src, observed=_OBS, accepted=[slot]) == []


def test_acceptance_blocks_the_judge_in_both_no_basis_cells():
    """★ 两格**都**要被接受挡住：同一处接受，落在哪一格都不许再报。

    `judge-fragile-no-basis` 有**两种形状**（spec §3.1 第③步两格）：
      · **第一格** `_card_has_nothing(card, kept)` —— 卡上什么都没有（`_OBS` 那一份）；
      · **第二格** `not kept` —— 卡上**有锚**，可候选**一根采信的都不剩**（编的那根地址）。
    只挡住一处的话：人在闸上点头接受的那一步、落到**另一格**照样被打回 ——
    **接受永远不生效**，而屏幕上看起来一切正常（本刀最忌讳的形状，见控制方那一条）。
    ⚠️ 实现上两格走的是**同一处**（`_no_basis_sel`，合并的那一句）—— 这条用例钉的是
    「合并之后两格都还挡得住」，不是「又加了一句 if」。
    ⚠️ 断言同样是**整张表**（★ 修复轮 1）：第一格那条原来只断言「no-basis 为空」，
    所以 `judge-fragile-selectors` 还挂着也照样绿（就是它把接受吃掉的那一条）。
    """
    cases = {
        "卡上什么都没有": (_src_with("auth", _FRAGILE) + _FOLLOW, _OBS),
        "一根采信的都不剩": (_src_with("auth", "'#made-up'") + _FOLLOW,
                             {"states": [{"name": "auth", "steps": [
                                 {"action": "click",
                                  "target": {"label": "Sign in",
                                             "selectors": ["button[type='submit']"]}}]}],
                              "fills": {}}),
    }
    for said, (src, obs) in cases.items():
        before = [v.code for v in lint.check(src, observed=obs)]
        assert before == ["judge-fragile-no-basis"], (said, before)   # 这一份确实落在那一格上
        [slot] = lint.no_basis_slots(src, obs)
        after = lint.check(src, observed=obs, accepted=[slot])
        assert after == [], (said, [v.code for v in after])           # ★ 这一步一条都不剩


def test_an_acceptance_whose_quartet_changed_does_not_silence_the_judge():
    """四元组**任一**变了就不算接受 ⇒ 判据回到报，那一格也回到待接受清单（spec §3.5「失效」）。

    ⚠️ 方向不许反：错的这一边是「**人没同意的事被记成同意了**」—— 屏幕上一切正常，
    产物却带着一根没人认过的地址出闸（`_acceptance_holds` 只是那把尺子，见 test_graph.py）。
    """
    src = _src_with("auth", _FRAGILE)
    [slot] = lint.no_basis_slots(src, _OBS)
    assert [v.code for v in lint.check(src, observed=_OBS, accepted=[slot])
            if v.code == "judge-fragile-no-basis"] == []            # 原样那一份 ⇒ 认
    for changed in (dict(slot, selector="header a"),                 # 那根地址被改稿换了
                    dict(slot, card="('原始观察重渲了',)"),            # 证据变了（卡指纹）
                    dict(slot, state="别的组"),                       # 换了别的组
                    dict(slot, index=1)):                            # 步序挪了
        got = [v for v in lint.check(src, observed=_OBS, accepted=[changed])
               if v.code == "judge-fragile-no-basis"]
        assert len(got) == 1, changed
        #: ★ 「失效 = 回到闸上重新问」：那一格要**重新出现在待接受清单里**
        assert len(lint.no_basis_slots(src, _OBS, accepted=[changed])) == 1, changed


# ── ★ 2026-10-15 修站那一刀 · Task 7：第二族（认不出语义）也看卡说话 ──────────────
#
# `judge-source-autoname`（`FILLS[*].source` 认不出语义）的老收场话是
# 「**回到探路那侧**把这一格的**语义**认出来（标签 / 例子值 / 题目正文）再重渲」——
# 与 `judge-fragile-selectors` 那一族是**同一条病**：**模型打不开页面**，
# 让它在探路那侧「认一认」就是让它去够一个够不着的东西。
# 改法与那一族**同一把尺子**：有卡 ⇒ 把卡摆到眼前照着判；没卡 ⇒ 如实说「判不出」+ 请人给一句。
#
# ⚠️ **任务书给的样例 src 与实测冲突**（与上面 `test_without_observed_behaviour_is_byte_identical`
# 那条同一条纪律：如实改成能钉住同一件事的写法，而不改要钉的那件事）——
# 任务书用的是 `source: 'address_line_1'`，而它**一次都不会命中这一族**：
# `_rule_g` 的第一格 `ba._kind_by_words(source)` 认得出 `address`/`street`
# （`_RANDOM_HINTS` 里那一行），于是直接 `continue`（实测 `lint.check(src)` == `[]`）。
# ⇒ 这里换成**真站那一个**机器名（gowizard 的 ZIP 框，见 `_rule_g` 的 docstring 与
# `docs/生成脚本-取值缺陷-2026-09-17.md`）：`textfield_173838`。
# 钉的是同一件事（有卡摆卡 / 没卡判不出），只是样例得**先真的命中**。

_AUTONAME_SRC = ("FILLS = {'postcode': {'source': 'textfield_173838',\n"
                 "  'target': { %s } } }\n"
                 "STATES = []\n")


def _autoname_msg(frozen_target, src_target="'selectors': ['input#x']"):
    """跑一遍、取这一族那**一条** message（`FILLS` 的键是这一格唯一的名字）。

    ⚠️ 两个 target 是**两份**：`src_target` 是产物那一格（待检查的那一份）、
    `frozen_target` 是冻结那份里探路那一刻量到的卡 —— 卡只许从后者取。
    """
    src = _AUTONAME_SRC % src_target
    obs = {"states": [], "fills": {"postcode": {"target": frozen_target}}}
    got = [v for v in lint.check(src, observed=obs) if v.code == "judge-source-autoname"]
    assert len(got) == 1, got
    return got[0].message


def test_autoname_with_card_points_at_the_card():
    """有卡 ⇒ **把卡摆出来**照着判（`_card_facts_say` 连值一起说，与 fragile 那一族同一处写法）。

    ★ 这一格是 gowizard 那个 ZIP 框的形状：机器名 `textfield_173838` 认不出语义，
    而**探路那一刻量到了**「ZIP code」这个标签 —— 判语义要的那几样本来就在手上。
    ⚠️ 卡从**冻结那份**（`observed`）取，不从 `src` 取（`src` 是待检查的那一份，拿它当证据
    就是循环证明）—— 所以下面 src 那一格的 target **故意不写**标签，纸上摆出来的那几样
    只可能来自冻结那份。
    """
    message = _autoname_msg(
        {"label": "ZIP code", "nearby_text": ["What is your ZIP code?"],
         "selectors": ["input#x"]},
        src_target="'selectors': ['input#x']")
    assert "ZIP code" in message                  # 卡摆出来了（连值）
    assert "What is your ZIP code?" in message    # 旁边那行字也摆出来
    assert "回到探路那侧" not in message            # 不再让人去够一个够不着的东西


@pytest.mark.parametrize("key, value", [
    ("label", "ZIP code"),
    ("aria_label", "ZIP code"),
    ("hint", "zip"),
    ("placeholder", "e.g. 06801"),
    ("nearby_text", ["What is your ZIP code?"]),
    ("text", "ZIP code"),
])
def test_every_anchor_on_the_card_counts_as_semantics(key, value):
    """卡上这几样**任何一样**都算「说得出这一格是什么」⇒ 一律走「照着卡判」那一支。

    ⚠️ `selectors` / `role` **不算** —— 地址说明这一格**在哪**，不说明它**问什么**
    （下一个用例钉的就是这一半：卡上只有一根地址 ⇒ 仍然是「判不出」）。
    ⚠️ 这张表与 `_card_has_nothing` 第一格是**同一份**（`lint._CARD_ANCHOR_KEYS`）：
    两处各写一遍的话，一边加了字段、另一边没加，屏幕上就会出现「有卡却说没卡」（或反过来），
    而两边各自看起来都正常。
    """
    message = _autoname_msg({"selectors": ["input#x"], key: value})
    needle = value if isinstance(value, str) else value[0]
    assert needle in message, message
    assert "回到探路那侧" not in message, message


def test_autoname_without_card_says_it_cannot_be_judged():
    """没卡（探路那一刻什么都没留下）⇒ **如实说判不出**，不许再教模型去够一个够不着的东西。

    ⚠️ fail-closed 的方向：**认不出 ⇒ 当「没量到」** —— 卡取不到（冻结那份里没这一格）
    也走这一支（见下）。
    """
    message = _autoname_msg({"selectors": ["input#x"]})
    assert "判不出" in message or "没有语义证据" in message, message
    assert "回到探路那侧" not in message, message
    assert "请人" in message                      # 那条**做得到**的路要给出来
    #: 冻结那份里**根本没有这一格** ⇒ 取不到卡 ⇒ 与空卡同一支（fail-closed，不编一张卡出来）
    bare = [v for v in lint.check(_AUTONAME_SRC % "'selectors': ['input#x']",
                                  observed={"states": [], "fills": {}})
            if v.code == "judge-source-autoname"]
    assert len(bare) == 1 and "判不出" in bare[0].message, bare


#: ★ 修复轮 1（评审 Minor）：老话的**哈希** —— 取一次、钉在带外，来源是**这一刀之前那一版**
#: 的 `lint.py`（`be3daa7`），**不是**从现在的实现里抄的：
#:   `git show be3daa7:agent/lint.py` → 跑 `check(src)` → 那条 `judge-source-autoname` 的 message
#:   → `sha256` = `4c2c0b9f54a2709cdf55f75d09ce4c23b405b50d10fafff575607114891b273c`（205 字节）。
#: 为什么光钉字面量不够：字面量是**从实现里抄的**，实现里打错一个字、字面量跟着抄错 ⇒
#: 这条钉子只在「改了一处没改另一处」时才红（自己不会红）。哈希是外来的，改了就是改了。
_AUTONAME_OLD_BE3DAA7_SHA256 = \
    "4c2c0b9f54a2709cdf55f75d09ce4c23b405b50d10fafff575607114891b273c"


def test_autoname_without_observed_keeps_the_old_wording_verbatim():
    """不传 `observed`（老 job / 老调用点）⇒ 那一句**逐字节还是老话**（本任务书的硬验收）。

    ⚠️ 老那句收场话里就有「回到探路那侧」—— 它**只许留在这条老路上**
    （两条新话都不许出现它）。这一条是**钉子**（改前改后都绿）：谁把老那半句
    顺手换成新话，它立刻红。
    ⚠️ 两条断言各管一件事：**字面量**让人一眼读得懂老话是什么（并钉住 205 字节一个字不差）；
    **哈希**是**带外**的（来自 `be3daa7`，见上面那个常量）—— 只有它能在
    「实现与字面量一起被改错」时还红。两条都在，才既读得懂又真能红。
    """
    old = [v for v in lint.check(_AUTONAME_SRC % "'selectors': ['input#x']")
           if v.code == "judge-source-autoname"]
    assert len(old) == 1, old
    assert old[0].message == (
        "字段「postcode」要拿去任务资料里取值的那个名字叫 `textfield_173838`：它**认不出语义**"
        "（不是邮编/邮箱/电话那类名字），形状也**不像运营资料的列名**"
        "（带数字串 = 框架自造的名字）。重放时这一格取不到资料、别名表也兜不住，"
        "值直接掉进随机池，而日志里写着「填好了」—— 真站代价是整单被站点拒收。"
        "回到探路那侧把这一格的**语义**认出来（标签 / 例子值 / 题目正文）再重渲。")
    assert hashlib.sha256(old[0].message.encode("utf-8")).hexdigest() \
        == _AUTONAME_OLD_BE3DAA7_SHA256, "老话变了 —— 老 job 的 message 与从前不再逐字节一致"
    assert len(old[0].message) == 205, len(old[0].message)


#: ★ 修复轮 1（评审 Important）：**真站那一张机器名卡**的形状 ——
#: 这一族的文档里那个字段（gowizard 的 ZIP 框）探路那一刻能记下来的只有
#: 框架自造的名字与一根机器地址，**一样都说不出这一格问什么**。
#: ⚠️ 这不是角落：它是这一族的**主流形状**（认不出语义的格，多半也就没有像样的标签）。
_AUTONAME_MACHINE_CARD = {"label": "textField-173838",
                          "selectors": ["input#textfield_173838"]}


@pytest.mark.parametrize("card", [
    _AUTONAME_MACHINE_CARD,                                     # 真站那张卡（label = 自造名）
    {"hint": "textfield_173838", "selectors": ["input#textfield_173838"]},
    {"label": "textField-173838"},
    {"placeholder": "field-2"},
])
def test_a_card_carrying_only_machine_names_still_cannot_be_judged(card):
    """★ 修复轮 1（Important）：卡上**只有框架自造的名字**时一样**判不出**，
    而且要说清「**量到了什么**」（`label=textField-173838`），**不准**说成「什么都没留下」。

    ⚠️ 不治的话 fail-closed 就**反了**：这一支会告诉模型「照着这几样把这一格的语义判出来」，
    可那几样里**没有一样**说得出这一格问什么 ⇒ 模型只能**编一个 `source`**，
    而编出来的名字**恰好过得了形状闸、静默放行** —— 正是这一刀存在要堵的那条路
    （「认不出 ⇒ 当没量到」，不是「认不出 ⇒ 当量到了」）。
    """
    message = _autoname_msg(card)
    assert "判不出" in message, message
    assert "请人" in message, message                    # 那条**做得到**的路要在（原来这一支没有）
    assert "没有留下" not in message, message             # 卡上明明有东西 ⇒ 不准说成什么都没留下
    needle = card.get("label") or card.get("hint") or card.get("placeholder")
    assert needle in message, message                    # 量到的是那**一句机器名**，摆出来


def test_a_semantic_name_on_the_card_still_reads_as_semantics():
    """反例（★ 滤法别收太宽）：卡上**有**一样说得出这一格问什么 ⇒ 照旧走「照着判」那一支。

    `hint: 'zip'` 是页面上人给这格起的名字 —— 判据认得出（`_kind_by_words`），
    模型照着它就能把 `source` 定成 `zip`。把它一起滤掉就是把「够得着」说成「够不着」。
    """
    message = _autoname_msg({"hint": "zip", "selectors": ["input#zip"]})
    assert "判不出" not in message, message
    assert "zip" in message, message


def test_one_semantic_anchor_beside_a_machine_name_is_enough():
    """混合：机器名（`label: 'textField-173838'`）+ 语义名（`hint: 'zip'`）⇒ **照着判**。

    ⚠️ 滤法要**逐样**滤，不是「只要卡上有自造名就整张作废」—— 那会把这一格里
    本来够得着的那一样也一起丢掉（模型于是又只能编）。
    """
    message = _autoname_msg(dict(_AUTONAME_MACHINE_CARD, hint="zip"))
    assert "判不出" not in message, message
    assert "zip" in message, message


@pytest.mark.parametrize("label", ["Address line 1",
                                   "I confirm I am 18 or over and I accept t"])
def test_a_human_label_with_a_digit_in_it_is_not_a_machine_name(label):
    """★ 「**带数字 ≠ 自造名**」那两课（`_named_after_its_own_label` 的现场）在**卡**这一侧同样算数。

    `Address line 1`（标签里那个 `1` 是人写的）与那句含 `18` 的勾选说明都是**人写的字** ——
    真站上正是这两格被「带数字串 = 机器起的名」误伤、连打三轮把整单卡停。
    ⚠️ 这条是**反例钉子**：滤法只看「有没有数字」的话，它立刻红。
    """
    message = _autoname_msg({"label": label, "selectors": ["input#x"]})
    assert "判不出" not in message, message
    assert label in message, message
