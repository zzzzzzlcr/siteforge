"""`tools/rootcause.py` 的钉子 —— 根因库怎么数、什么时候喊「该治工具层」。

## 为什么这一条要单独钉

这是那份「同根因 ≤ 2 次」判据的**唯一执行处**（2026-10-13 统计给的，当时没落）。
它错了的代价是**方向错**：该去治工具层的时候继续治站，或者反过来，
把「第 3 个不同的根因」误读成「同一个根因第 3 次」。

## 两处判断

1. **≥ `THRESHOLD`（3）⇒ 该治工具层** —— 判据原话「同根因 ≤ 2 次」，
   真事是遮挡类到第 3 份（10-09 悬浮窗 / 10-10 下拉没收回 / 10-13 吸顶页眉）。
2. **库外的单要点名** —— 盘上有 `缺陷单-*.md` 而表里没有 ⇒ **说出它**。
   不说的话，「这个根因只有 2 次」和「这个根因还有 1 份没进库」长得一模一样，
   而这两件事的处置**相反**（一个继续治站、一个该先补库）。
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

_TOOL = ROOT / "tools" / "rootcause.py"

TABLE = """# 根因库

| 根因 | 缺陷单 | 修在哪 | 治它的那一刀 | 覆盖它的用例 |
|---|---|---|---|---|
%s
"""


def _load():
    spec = importlib.util.spec_from_file_location("rootcause", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rc():
    return _load()


def _row(cause, ticket):
    return "| %s | %s | 骨架 | 某刀 | 某用例 |" % (cause, ticket)


def _build(tmp_path, rows, tickets):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    for t in tickets:
        (tmp_path / "docs" / ("%s.md" % t)).write_text("# 单\n", encoding="utf-8")
    p = tmp_path / "docs" / "根因库.md"
    p.write_text(TABLE % "\n".join(rows), encoding="utf-8")
    return p


def test_the_third_ticket_on_one_root_cause_is_flagged_for_the_tool_layer(rc, tmp_path):
    """★ 第 3 份同一个根因 ⇒ 标「该治工具层」。第 2 份**不**标。"""
    rows = [_row("遮挡", "缺陷单-a"), _row("遮挡", "缺陷单-b"),
            _row("遮挡", "缺陷单-c"),
            _row("时序", "缺陷单-d"), _row("时序", "缺陷单-e")]
    p = _build(tmp_path, rows, ["缺陷单-a", "缺陷单-b", "缺陷单-c", "缺陷单-d", "缺陷单-e"])
    data = rc.survey(tmp_path / "docs", p)

    assert rc.THRESHOLD == 3, "判据原话是「同根因 ≤ 2 次」——阈值就是 3"
    assert [g["cause"] for g in data["flagged"]] == ["遮挡"], data["flagged"]
    assert data["flagged"][0]["n"] == 3
    assert "时序" not in [g["cause"] for g in data["flagged"]], "2 份不该喊"


def test_a_ticket_on_disk_that_is_not_in_the_table_is_named(rc, tmp_path):
    """**库外的单要点名** —— 不许让「还有一份没进库」伪装成「只出现过 2 次」。"""
    rows = [_row("遮挡", "缺陷单-a"), _row("遮挡", "缺陷单-b")]
    p = _build(tmp_path, rows, ["缺陷单-a", "缺陷单-b", "缺陷单-c"])
    data = rc.survey(tmp_path / "docs", p)

    assert data["missing_from_table"] == ["缺陷单-c"], data["missing_from_table"]
    text = rc.render(data)
    assert "缺陷单-c" in text, "要在报告里点名"
    assert "盘上有、库里没有" in text, "而且要说清是哪一边缺的（不是笼统一句「有洞」）"


def test_a_row_that_names_a_ticket_that_is_not_on_disk_is_named_too(rc, tmp_path):
    """反向也要出声：表里写了、盘上没有 —— 那是**库锈了**（单被改名/删了）。"""
    rows = [_row("遮挡", "缺陷单-a"), _row("遮挡", "缺陷单-删掉了")]
    p = _build(tmp_path, rows, ["缺陷单-a"])
    data = rc.survey(tmp_path / "docs", p)

    assert data["not_on_disk"] == ["缺陷单-删掉了"], data["not_on_disk"]
    assert "缺陷单-删掉了" in rc.render(data)


def test_an_empty_library_says_unmeasured_not_zero(rc, tmp_path):
    """一行都没有 ⇒ 说「没量到」，**不许**画成「一切正常、没有根因超标」。"""
    p = _build(tmp_path, [], [])
    data = rc.survey(tmp_path / "docs", p)

    assert data["groups"] == []
    assert "没量到" in rc.render(data) or "一行都没有" in rc.render(data)


def _row_at(cause, ticket, where):
    return "| %s | %s | %s | 某刀 | 某用例 |" % (cause, ticket, where)


def test_a_fix_that_lives_only_in_the_artifact_is_shouted_about(rc, tmp_path):
    """★ 那条 ★ 底下三把刀**寿命不同** —— **只在那一份产物里的要单独喊**。

    真事（`docs/根因库.md` 遮挡那三行）：10-13 的 `_recenter` 在**骨架**里（重出还在、
    所有站都有），而 10-09 关浮层那一刀**只修在 `forms-222/sites/kotzheating.py` 那一份里，
    生成侧从没补** —— 单子自己写着「下次重出会丢」。**前者是治一类，后者是修一个站。**

    第一版只看得出「有刀」，看不出「这把刀下次还在不在」—— 这一条钉的就是那个差别。
    """
    rows = [_row_at("遮挡", "缺陷单-甲", "骨架"),
            _row_at("遮挡", "缺陷单-乙", "**产物**"),
            _row_at("遮挡", "缺陷单-丙", "骨架+生成侧")]
    p = _build(tmp_path, rows, ["缺陷单-甲", "缺陷单-乙", "缺陷单-丙"])
    #: `_build` 用的是老的两格行 —— 这里重写一份五格的
    (tmp_path / "docs" / "根因库.md").write_text(
        TABLE % "\n".join(rows), encoding="utf-8")
    data = rc.survey(tmp_path / "docs", p)
    text = rc.render(data)

    g = data["groups"][0]
    #: 集合里是**拆开的档**（`骨架+生成侧` 拆成两档）—— 那一类到底散在哪几档，一眼看全。
    assert g["where"] == ["产物", "生成侧", "骨架"], g["where"]
    assert g["product_only"] == ["缺陷单-乙"], g["product_only"]
    assert "只在那一份产物里" in text and "缺陷单-乙" in text, text
    assert "**修站**" in text and "不是「修类」" in text, "要说清它是哪一档"


def test_the_where_column_can_be_bold_and_still_reads(rc):
    """`修在哪` 允许带 `**`（表里那几行是加粗的）—— 判定不许被星号带偏。"""
    assert rc.where_of("**产物**") == ["产物"], "加粗不算另一档"
    assert rc.is_product_only("**产物**") is True, "加粗的「产物」也是「只在产物里」"
    assert rc.is_product_only("骨架") is False
    assert rc.is_product_only("骨架+产物") is False, "有骨架就不算「只在产物里」"
    assert rc.is_product_only("cdp") is False


def test_a_where_value_outside_the_four_kinds_is_named(rc, tmp_path):
    """四档之外的写法要点名 —— 这一格漂了，整条库的结论就跟着漂。"""
    rows = [_row_at("遮挡", "缺陷单-甲", "骨架"),
            _row_at("遮挡", "缺陷单-乙", "随手修的")]
    p = _build(tmp_path, rows, ["缺陷单-甲", "缺陷单-乙"])
    (tmp_path / "docs" / "根因库.md").write_text(TABLE % "\n".join(rows), encoding="utf-8")
    data = rc.survey(tmp_path / "docs", p)

    assert data["bad_where"] == ["随手修的"], data["bad_where"]
    assert "不认识的字" in rc.render(data)
