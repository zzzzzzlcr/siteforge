"""`tools/case_report.py` 的钉子 —— 案例怎么数、局部修复覆盖怎么算。

## 为什么单独钉

用户 2026-11-03 裁定的三项里，这一份是**唯一**把「经验有没有在积累、局部修复到底覆盖多少」
算出来的地方。数字错了，方向就错了：该治工具层的时候继续改稿，或者把「没拿到步号」
误读成「已经局部修了」。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOL = ROOT / "tools" / "case_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("case_report", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(*, family="selector", step=3, scope=None, local=False,
         delivered=False, selftest=False, diff="--- a\n+++ b\n"):
    return {"site": "s", "fingerprint": "点名:X", "family": family,
            "failed_step": step, "note": "x",
            "repair": {"diff": diff, "changed_scope": scope or {},
                       "local": local},
            "verification": {"lint": True, "selftest": selftest,
                             "delivered": delivered}}


def test_only_delivered_and_selftested_cases_can_be_fed_back(tmp_path):
    """未交付 / 没过自测 / 没 diff ⇒ 都不算可回灌经验。"""
    mod = _load()
    data = mod.survey([
        _row(delivered=True, selftest=True),
        _row(delivered=True, selftest=False),
        _row(delivered=False, selftest=True),
        _row(delivered=True, selftest=True, diff=""),
    ])
    assert data["total"] == 4
    assert data["delivered"] == 3
    assert data["hinted"] == 1, data


def test_local_repair_coverage_splits_the_three_buckets():
    """没步号 / 有步号没锚 / 有锚 —— 三种必须分开数（用户点名的那三档）。"""
    mod = _load()
    data = mod.survey([
        _row(step=None),
        _row(step=3, scope={}),
        _row(step=3, scope={"state": "q1", "index": 0, "global": 4}, local=True),
        _row(step=4, scope={"state": "q2", "index": 1, "global": 9}, local=False),
    ])
    assert data["local"] == {"no_step": 1, "step_no_scope": 1,
                             "scope": 2, "scope_local": 1}, data


def test_bad_lines_are_skipped_not_counted_as_cases(tmp_path):
    """坏行跳过；它不许被读成「一个 unknown 案例」。"""
    mod = _load()
    path = tmp_path / "ledger.jsonl"
    path.write_text("{not json}\n" + json.dumps(_row()) + "\n", encoding="utf-8")
    rows = mod.read_rows(path)
    assert len(rows) == 1
    assert mod.survey(rows)["total"] == 1


def test_a_missing_ledger_is_not_reported_as_zero_failures(tmp_path):
    """读不到账本 ⇒ 明说「没读到」，不许读成「一切正常」。"""
    mod = _load()
    data = mod.survey(mod.read_rows(tmp_path / "nope.jsonl"))
    assert data["total"] == 0
    assert "不是「没有失败」" in mod.render(data)



def test_legacy_rows_are_flagged_instead_of_read_as_clean_cases():
    """2026-11-03 之前的行没有族/步号/验证结果 ⇒ 明说，别读成「4 个 unknown 案例」。"""
    mod = _load()
    data = mod.survey([{"site": "s", "fingerprint": "x", "at": "2026-10-01"}])
    assert data["legacy"] == 1, data
    assert "旧格式" in mod.render(data), mod.render(data)