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


def test_judge_rule_table_is_the_mounted_eight():
    """挂进生成期出口的判据规则就这**八条**（C/D/H + A/E/F/G/I）——
    多一条 / 少一条都要有人来解释（与上面 `RULES` 那张表同一条规矩）。"""
    assert list(lint.JUDGE_RULES) == [
        "judge-transient", "judge-fill-value", "judge-url-only",
        "judge-strict-unguarded", "judge-ungated-click", "judge-no-follow-tab",
        "judge-source-autoname", "judge-fragile-selectors",
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
