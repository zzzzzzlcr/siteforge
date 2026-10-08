"""IR 那一层（`agent/ir.py`）：**IR 完整才允许 Emit**（用户 2026-09-30 的诊断）。

为什么要这一份：今晚那批事故（`zekear` 自测「页面上没找到「Buy now」」、`getfixapro` 的
「判据分不开页」）**全都不是站点的问题**，而是「账本 → 可编译的步骤表」这一步**算出了编译不了
的东西**，而它以前**直接**交给渲那一层。于是失败全叫「生成脚本失败」，看不出卡在哪一层。

这里钉四件事：
1. 每一条问题都带**层与类**（`layer` / `code`），人话只在 `say` 里（**机器码不上屏**）；
2. 四类可量的形状各钉一条：只有地址的空门 / 组中间夹 `goto` / 产物不认的动作 / 一步可重放的都没有；
3. 干净的 IR ⇒ **一条都不报**（否则每一份正常产物都在喊狼来了）；
4. 接进图里那一下：IR 不完整 ⇒ **在渲之前**停下，`end_note` 说清卡在哪一层、`ir_layer` 记下来。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, ir  # noqa: E402


def _journey_with(states: list) -> browser_agent.Journey:
    """一份「IR 就长这样」的账本（`validate` 只读 `states()`，所以按那个形状造）。"""
    book = browser_agent.Journey()
    book.states = lambda: states  # type: ignore[assignment]
    return book


def _state(name="funnel", *, url="https://example.test/funnel", text="Get Started",
           steps=(("click",),)):
    when = {"url_contains": url}
    if text is not None:
        when["text_contains"] = [text]
    return {"name": name, "when": when,
            "steps": [{"action": a[0]} for a in steps]}

def _two_screens_one_url() -> list:
    """同一个地址上的两屏，其中一屏**只有地址**（`zekear` 那趟现场的形状）。"""
    url = "https://example.test/product"
    bare = _state("product-2", url=url, text=None)
    return [_state("product", url=url, text="Add to cart"), bare]


def test_a_bare_gate_is_an_ir_problem_only_when_the_url_has_other_screens():
    """★ 只有地址的门 —— **判据是「同一个地址上还有没有别的组」**（2026-09-30 收窄）。

    现场（`job-face3bb1d760`）：`signin` 那一组只有地址（`/user/signIn`），而那个地址上
    **独它一份** ⇒ 地址前缀本来就够用（就是它自己那一页）⇒ 拦住它是**误判**，而它拦掉了
    一份本来对的产物。反过来，同一个地址上**还有别的组**时，地址前缀在每一屏上都成立
    ⇒ 那时才真的分不出是哪一屏（这才是那条判据的射程）。
    """
    got = ir.validate(_journey_with(_two_screens_one_url()))
    assert [p.code for p in got] == ["ir_unexpressible.bare_gate"], got
    assert got[0].layer == "ir" and got[0].name == "product-2", got[0]
    assert "没读到正文" in got[0].say, got[0].say

    #: 正控：同一个地址上**独一份** ⇒ 不报（那一趟被误拦的就是这个形状）
    alone = _state("signin", url="https://example.test/user/signIn", text=None)
    assert ir.validate(_journey_with([alone])) == []

def test_a_goto_sandwiched_between_actions_of_two_pages_is_an_ir_problem():
    """★ `goto` **夹在中间**（前面有步、后面也有步）**而且换了页** ⇒ 真病：
    前面那几步按这一页记的、后面那几步按新页记的，重放时一页上做一半、另一半在别的页上做，
    而日志看不出区别（`zekear` 那趟自测「页面上没找到「Buy now」」就是这个形状）。
    """
    bad = _state(steps=(("click",), ("goto",), ("click",)))
    bad["when"]["url_contains"] = "https://example.test/product"
    bad["steps"][1]["url"] = "https://example.test/user/signIn"        # ← 真换了页
    got = ir.validate(_journey_with([bad]))
    assert [p.code for p in got] == ["trace_ambiguous.goto_in_the_middle"], got
    assert "前后都有别的动作" in got[0].say, got[0].say


def test_a_goto_the_group_starts_with_is_a_legal_shape():
    """★ 2026-09-30（现场逮到的第三个误判）：「**从这一页出发、去那一页做事**」是合法形状 ——
    `center-2` 的 `[goto 商品页, 点 Buy now]` 就是这样，而它**是对的**（那一组的前提页就是它
    出发的那一页）。改成拦它 = 一份本来对的产物出不来。
    """
    ok = _state(steps=(("goto",), ("click",)))
    ok["when"]["url_contains"] = "https://example.test/center"
    ok["steps"][0]["url"] = "https://example.test/product"             # ← 换了页，但 goto 在最前
    assert ir.validate(_journey_with([ok])) == []


def test_a_goto_that_stays_on_the_same_page_is_a_reload_not_a_problem():
    """★ 2026-09-30（第二个误判）：同一个地址上的 `goto` 是**重载**（真站实测：
    `goto https://…/user/signUp` 之后再填表 —— 页面没换）⇒ 夹在中间也放行。"""
    same = _state(steps=(("form",), ("goto",), ("form",)))
    same["when"]["url_contains"] = "https://example.test/signup"
    same["steps"][1]["url"] = "https://example.test/signup?step=2"      # ← 同址（带 query）
    assert ir.validate(_journey_with([same])) == []


def test_an_action_the_product_does_not_accept_is_an_ir_problem():
    """产物只认那五种动作（骨架的契约）—— 别的到了产物里会被当成「写错了」。"""
    got = ir.validate(_journey_with([_state(steps=(("eval",),))]))
    assert [p.code for p in got] == ["ir_unexpressible.bad_action"], got
    assert "只认" in got[0].say, got[0].say


def test_an_ir_with_no_replayable_step_is_a_trace_problem():
    """一步可重放的都没有 ⇒ 这是**轨迹**那一类（不是 IR 表达不了）。"""
    got = ir.validate(_journey_with([]))
    assert [p.code for p in got] == ["trace_incomplete.no_steps"], got
    assert got[0].layer == "ir", got[0]


def test_no_ledger_at_all_is_classified_not_raised():
    got = ir.validate(None)
    assert [p.code for p in got] == ["ir_unexpressible.no_ledger"], got


def test_the_human_sentence_names_the_layer_and_never_leaks_the_codes():
    """`say()` 是上屏的那一句：**说清卡在哪一层**，机器码一个都不许露。"""
    got = ir.validate(_journey_with(_two_screens_one_url()))
    said = ir.say(got)
    assert "卡在" in said and "可编译的步骤表" in said, said
    for p in got:
        assert p.code not in said, said


def test_the_draft_refuses_an_incomplete_ir_before_rendering(tmp_path, monkeypatch):
    """接进图里那一下：IR 不完整 ⇒ **渲之前**停下，账上说清是哪一层（`ir_layer`）。

    ⚠️ 要打在**类**上：账本会被 saver 序列化/反序列化，打在实例上的那个 lambda 过不了这一趟
    （实测：那样改完这一趟**照样交付**了 —— 量的还是原来那条路）。
    """
    import test_graph as TG

    monkeypatch.setattr(browser_agent.Journey, "states",
                        lambda self: _two_screens_one_url())   # ← 同址两屏、其中一屏只有地址
    book = TG._journey()
    deps, rec = TG._deps(journey=book)
    app, cfg, _ = TG._build(deps=deps)
    _, out = TG._drive(app, cfg, TG._brief(tmp_path))

    assert out["end_reason"] == "draft_failed", out.get("end_note")
    assert out.get("ir_layer") == "ir", out
    assert "卡在" in out["end_note"] and "只有地址" in out["end_note"], out["end_note"]
    assert rec.selftest == [], "IR 都不完整，不该去跑自测"
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % TG.SITE)).exists()


# ─── ★ 2026-09-30：步骤引用的 fill 必须在 FILLS 里（用户问「FILLS 没有不是有兜底的随机码」）──

def test_a_step_that_fills_something_the_fills_table_does_not_declare_is_caught():
    """产物自我矛盾的正身：`form` 步引用 `email`，而 `FILLS` 里没有它 ⇒ 跑起来当场报
    「产物写错了：这一步要填「email」，FILLS 里没有它」并停。**兜底管不了这一格** ——
    别名/拆名/随机池覆盖的是「**资料里**没有那一列」，不是「产物自己的声明表里没有这一格」。
    """
    states = [{"name": "signup", "when": {"url_contains": "https://x.test/signup"},
               "steps": [{"action": "form", "fill": "email"}, {"action": "form", "fill": "field"}]}]
    assert ir.missing_fills(states, {"field": {"source": "field"}}) == {"email"}
    assert ir.missing_fills(states, {"field": {}, "email": {}}) == set()
    assert ir.referenced_fills(states) == {"email", "field"}


# ─── ★ 2026-09-30（用户裁定「IR 必须先证明完整」）──
# 两条门都用**账本里已经记着的事实**，不猜：
#   ① 这一页观察到的 `required` 格，产物里没有一步在填它；
#   ② 账本里真填成功过的那一格（`journey.fills()`），步骤表里一步都没引用。

def _book(*, states: list, fields: dict | None = None, fills: dict | None = None):
    """带上「这一页见过哪些格 / 账本真填成功过哪些格」的账本（`validate` 只读这三样）。"""
    book = _journey_with(states)
    book.fields_by_state = lambda: dict(fields or {})      # type: ignore[assignment]
    book.fills = lambda: dict(fills or {})                 # type: ignore[assignment]
    return book


def _one_screen():
    return [{"name": "start", "when": {"url_contains": "https://x.test/funnel",
                                       "text_contains": ["Get started"]},
             "steps": [{"action": "click"}]}]


def test_a_required_field_the_product_has_no_step_for_is_an_ir_problem():
    """★ 这一页**见过**一个必填格，而产物里没有一步去填它 ⇒ 编译之前就停下。

    真事（`job-243fd328e088` 的 `fix_notes` 原话）：「「Town/City」认不出是什么 —— **没动它**」、
    「「I have read and agree to the My Disney Experience Terms and Conditions.」认不出是什么
    —— 没动它」⇒ 一趟下来关键格一个没填，产物照样跑、照样交，而日志里「每一步都做成了」。

    ⚠️ 判据只认**账本里观察到的 `required`**（DOM 自己说的），不认猜测：没证据不拦。
    """
    fields = {"start": [{"label": "Town/City", "hint": "town", "type": "text",
                         "required": True, "selector": "#town"}]}
    got = ir.validate(_book(states=_one_screen(), fields=fields))
    assert [p.code for p in got] == ["trace_incomplete.required_field_without_step"], got
    assert got[0].layer == "ir" and got[0].name == "start", got[0]
    assert "Town/City" in got[0].say and "必填格" in got[0].say, got[0].say

    #: 正控一：同一格真有一步在填它（选择器对得上）⇒ 一条都不报
    covered = [dict(_one_screen()[0],
                    steps=[{"action": "form", "fill": "town",
                            "target": {"selectors": ["#town"]}}])]
    assert ir.validate(_book(states=covered, fields=fields)) == []

    #: 正控二：**不是必填**的格 ⇒ 不报（账本里见过、产物没填它，是合法形状）
    optional = {"start": [{"label": "Newsletter", "type": "checkbox", "selector": "#nl"}]}
    assert ir.validate(_book(states=_one_screen(), fields=optional)) == []

    #: 正控三：**循环型**这一条必须关掉（运行期按页面挑格，静态对不上号 ⇒ 全是误杀）
    assert ir.validate(_book(states=_one_screen(), fields=fields), shape="loop") == []


def test_a_required_field_whose_only_handle_is_a_data_label_is_not_blocked():
    """★ 误判（真事，`job-fa794783c63b`）：产物**明明填了** Email，闸却说「一步都没填」。

    这个输入框**没有 DOM 名字**（页面上是 `data-label="@Email"`），于是两条路各记了一个
    选择器 —— 两个都是**量出来的**：

      · 动作那条路（产物照它重放）：`form > section.visible input[data-label="@Email"]`
      · `observe` 那条路（闸把这一行当判据）：`form:has(input[placeholder="Search"]) input[type=email]`

    账本给这次填起的名是 `field-3`（没有名字可起）⇒ 「选择器一模一样」与「名字互相含」
    两条判据**都撞不上**，于是一份本来对的稿被拦在编译之前（那趟的 `end_reason`
    是 `draft_failed`，人话就是这句「账本里见到过 1 个必填格」）。

    下面那几个值**逐字抄自那趟的账本**（`runtime/explore/job-fa794783c63b/attempt-1.jsonl`）。
    """
    fields = {"co-3": [{"label": "Email", "hint": "input-8df0faf4", "type": "email",
                        "required": True,
                        "selector": 'form:has(input[placeholder="Search"]) input[type=email]'}]}
    filled = [{"name": "co-3", "when": {"url_contains": "https://uk.guaranteed.overfifties.co.uk/"},
               "steps": [{"action": "form", "fill": "field-3",
                          "target": {"name": None, "label": None,
                                     "selectors": ['form > section.visible input[data-label="@Email"]']}}]}]
    got = ir.validate(_book(states=filled, fields=fields))
    assert got == [], got

    #: 正控：同一形态，但产物那一步跟这格**哪儿都对不上** ⇒ 照旧拦（宽松不许变成不拦）
    stranger = [dict(filled[0],
                     steps=[{"action": "form", "fill": "field-3",
                             "target": {"name": None, "label": None,
                                        "selectors": ["#newsletter-opt-in"]}}])]
    got = ir.validate(_book(states=stranger, fields=fields))
    assert [p.code for p in got] == ["trace_incomplete.required_field_without_step"], got


def test_a_required_field_the_ledger_did_fill_is_put_back_instead_of_blocking():
    """★ 「**能补就补，补不了才拦**」接到必填格这条闸上（2026-10-07，用户：「探路不是探出来了吗？
    不能自己修正？」）。

    与 `FILLS` 那一格同一个道理（`graph` 里 `missing_fills` → 从账本补回去 → 补不了才拦）：
    账本里**真填过**这一格（`fills()` 里有它），只是产物里没有一步在填 ⇒ **把它补回产物**，
    而不是把一份本来能跑的稿拦在编译之前。补的是**账本里那一格**（选择器照抄，不编）。

    ⚠️ 补不了（账本里也没填过 —— 迪士尼那趟：`Town/City` 认不出来、没动它）⇒ **照旧拦**，
    那趟废稿的教训一个不丢。
    """
    fields = {"co-3": [{"label": "Email", "hint": "input-8df0faf4", "type": "email",
                        "required": True,
                        "selector": 'form:has(input[placeholder="Search"]) input[type=email]'}]}
    states = [{"name": "co-3", "when": {"url_contains": "https://uk.guaranteed.overfifties.co.uk/"},
               "steps": [{"action": "click"}]}]
    fills = {"field-3": {"source": "field-3", "kind": "value", "label": "field-3",
                         "target": {"selectors": ['form > section.visible input[data-label="@Email"]']},
                         "fallback": []}}
    book = _book(states=states, fields=fields, fills=fills)
    fixed, restored, notes = ir.repair_required(states, book)
    assert restored == ["field-3"], (restored, notes)
    assert notes and "field-3" in notes[0], notes
    #: 补的是**账本里那一格**（选择器照抄、值照抄），不是编一个
    step = fixed[0]["steps"][-1]
    assert step["action"] == "form" and step["fill"] == "field-3", step
    assert step["target"]["selectors"] == ['form > section.visible input[data-label="@Email"]'], step
    #: 原样那一份**不许被改**（补出来的是新的一份）
    assert states[0]["steps"] == [{"action": "click"}], states

    #: 补回去之后，这条闸不再报；`validate` 判的就是补完的那一份
    book.states = lambda: fixed  # type: ignore[assignment]
    assert ir.validate(book) == [], ir.validate(book)

    #: 正控：账本里**也没填过**这一格 ⇒ 照旧拦（那才是那趟废稿的形状）
    empty = _book(states=states, fields=fields, fills={})
    fixed2, restored2, _ = ir.repair_required(states, empty)
    assert restored2 == [] and fixed2 == states
    assert [p.code for p in ir.validate(empty)] == ["trace_incomplete.required_field_without_step"]


def test_a_fill_the_ledger_recorded_but_no_step_references_is_an_ir_problem():
    """★ 账本里**真填成功过**的那一格，步骤表里一步都没引用 = 那一步被弄丢了。

    产物里会留一个**没人用的 `FILLS` 条目**（`Journey.fills()` 的 docstring 点名过的
    「死条目」），重放时那一格就**空着** —— 而账本明明白白记着当初填成功过。
    """
    states = [{"name": "start", "when": {"url_contains": "https://x.test/funnel",
                                         "text_contains": ["Get started"]},
               "steps": [{"action": "form", "fill": "email",
                          "target": {"selectors": ["#email"]}}]}]
    fills = {"email": {"source": "email", "target": {"selectors": ["#email"]}},
             "phone": {"source": "phone", "target": {"selectors": ["#phone"]}}}
    got = ir.validate(_book(states=states, fills=fills))
    assert [p.code for p in got] == ["trace_incomplete.dead_fill"], got
    assert "phone" in got[0].say, got[0].say

    #: 正控：只有被引用的那一格 ⇒ 干净；循环型照旧不判这一条。
    assert ir.validate(_book(states=states, fills={"email": fills["email"]})) == []
    assert ir.validate(_book(states=states, fills=fills), shape="loop") == []


# ─── ★ 2026-09-30（用户裁定的第二半）：**闸门够不够用**（充分性）──
# 事实一个来源（探路期 `_gates_that_hold_elsewhere` → `journey.gate_collisions`），
# 但**判它的是 IR 这一层** —— 原来是 `graph` 那一格自己拦，`ir_layer` 空着，
# 面板上看不出「卡在第几层」（那 9 趟就是 `ir_layer=None`）。

def test_a_gate_that_holds_on_another_page_is_an_ir_problem_with_its_layer():
    """★ 「判据分不开页」搬进 IR 层：层 / 类 / 人话三样齐全，人话里点得出是哪一组、在哪页也成立。

    `_applies` 是重放时**唯一的门**：两个状态的判据互相命中 ⇒ 走到那一页时前一组的步骤
    **在那页上真做一遍**（真站：georgiapower 那趟「真做 2 步、跳过 23 步」），日志里看不出来。
    """
    book = _book(states=_one_screen())
    book.gate_collisions = [{"name": "landing", "url": "https://x.test/funnel",
                             "pages": ["https://x.test/thanks"]}]
    got = ir.validate(book)
    assert [p.code for p in got] == ["trace_ambiguous.gate_holds_elsewhere"], got
    assert got[0].layer == "ir" and ir.layer_of(got) == "ir", got[0]
    assert "分不开页" in got[0].say, got[0].say
    #: 说清是哪一组、在哪一页上也成立 —— 只说「判据有问题」人没法动手
    assert "landing" in got[0].say and "thanks" in got[0].say, got[0].say
    #: 人话里**不许**念两遍「先别写 py」（那句由 `say()` 统一给）
    assert ir.say(got).count("先别写 py") == 1, ir.say(got)
    #: 循环型：它靠地址判、运行期自己找 ⇒ 这条不适用
    assert ir.validate(book, shape="loop") == []
    #: 正控：`gate_collisions` 空（默认值）⇒ 一个字都不许多说
    assert ir.validate(_book(states=_one_screen())) == []
