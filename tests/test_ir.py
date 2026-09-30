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

def test_a_goto_in_the_middle_of_a_group_is_an_ir_problem():
    """★ `goto` 后面还有动作（`zekear` 那趟自测的真因）：重放第一步就走掉了，后面的步会在别的页上做。"""
    got = ir.validate(_journey_with([_state(steps=(("goto",), ("click",)))]))
    assert [p.code for p in got] == ["trace_ambiguous.goto_in_the_middle"], got
    assert "另一个页面" in got[0].say, got[0].say


def test_a_goto_that_stays_on_the_same_page_is_a_reload_not_a_problem():
    """★ 2026-09-30（用户问「还是说我提的还要改」时逮到的**误判**）：同一个地址上的 `goto`
    是**重载**（真站实测：`goto https://…/user/signUp` 之后再填表 —— 页面没换），后面的动作
    照样在这一页上 ⇒ 报它就是误判，而它会**拦住一整份本来对的产物**。换页的那种照旧要报。
    """
    same = _state(steps=(("goto",), ("form",)))
    same["when"]["url_contains"] = "https://example.test/signup"
    same["steps"][0]["url"] = "https://example.test/signup?step=2"      # ← 同址（带 query）
    assert ir.validate(_journey_with([same])) == []

    other = _state(steps=(("goto",), ("form",)))
    other["when"]["url_contains"] = "https://example.test/product"
    other["steps"][0]["url"] = "https://example.test/user/signIn"       # ← 真换了页
    got = ir.validate(_journey_with([other]))
    assert [p.code for p in got] == ["trace_ambiguous.goto_in_the_middle"], got


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
