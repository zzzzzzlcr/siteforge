"""`agent/steps.py` 的判据：一行 trace 怎么翻成「三层 + 诊断」。

⚠️ 这一版**不编概率**（附件里那张 65%/25%/10% 不做）——本项目没有概率模型，
编出来的数字是假证据。这里每一条原因都必须**指着具体字段**。
"""
import json
from pathlib import Path

from agent import steps


def _row(**kw):
    base = {"step": 1, "action": "click", "target": "订阅按钮",
            "selector_used": "form:has(input[name=\"email\"]) button[type=submit]",
            "fallback_level": 0, "frame_id": "", "ok": True,
            "progress": True, "progress_why": None, "recovery": None,
            "note": "点了「订阅按钮」"}
    base.update(kw)
    return base


def test_skipped_row_is_its_own_act_layer():
    got = steps.layer_of(_row(ok=None, skipped=True, progress=None, note="第 1 步跳过：…"))
    assert got["act"] == "跳过"
    assert "没轮到" in got["biz"]


def test_act_failed_is_not_the_same_as_business_failed():
    """动作没做成 ⇒ 业务层不判「没推进」—— 那会把两件事混成一件。"""
    got = steps.layer_of(_row(ok=False, progress=None,
                              note="页面上没找到「订阅按钮」，这一步没做成"))
    assert got["act"] == "没做成"
    assert "没轮到" in got["biz"]


def test_element_layer_names_the_fallback():
    assert steps.layer_of(_row(fallback_level=0))["elem"] == "账本第一条地址"
    assert "第 2 个" in steps.layer_of(_row(fallback_level=1))["elem"]
    rec = {"primary_selector": "#__BVID__408", "resolved_by": "label+near", "confidence": 0.8}
    got = steps.layer_of(_row(fallback_level=3, recovery=rec))
    assert "恢复" in got["elem"] and "label+near" in got["elem"]
    assert "#__BVID__408" in got["elem"]


def test_element_layer_says_when_it_was_not_recorded():
    assert "没记下" in steps.layer_of(_row(fallback_level=None))["elem"]


def test_business_layer_reads_progress_and_says_when_it_cannot():
    assert steps.layer_of(_row(progress=True))["biz"] == "页面推进了"
    assert steps.layer_of(_row(progress=False))["biz"] == "页面没有变化"
    got = steps.layer_of(_row(progress=None, progress_why="动作前没能留下页面快照，没得比"))
    assert "没算出" in got["biz"] and "快照" in got["biz"]


def test_diagnose_is_evidence_backed_and_never_invents_percentages():
    why = steps.diagnose(_row(ok=True, progress=False, fallback_level=0))
    assert any("页面没有变化" in x for x in why)
    assert not any("%" in x for x in why), "不许编概率"
    why2 = steps.diagnose(_row(ok=False, progress=None, note="页面上没找到「订阅按钮」"))
    assert any("没找到" in x for x in why2)
    why3 = steps.diagnose(_row(ok=None, skipped=True, note="第 1 步跳过：这一页不像…"))
    assert any("跳过" in x for x in why3)


def test_diagnose_uses_the_recovery_record():
    rec = {"primary_selector": "#__BVID__408", "resolved_by": "label", "confidence": 0.5}
    why = steps.diagnose(_row(recovery=rec))
    assert any("失效" in x and "label" in x for x in why)


def test_step_view_carries_everything_the_panel_needs():
    got = steps.step_view(_row(shot_after="7-after.png", recovery=None))
    for key in ("n", "action", "target", "note", "layers", "why", "selector_used",
                "frame_id", "recovery", "shots", "raw"):
        assert key in got, key
    assert got["shots"] == {"before": None, "after": "7-after.png"}
    assert got["n"] == 1


def test_read_trace_survives_a_broken_line(tmp_path: Path):
    p = tmp_path / "x.trace.jsonl"
    p.write_text(json.dumps(_row()) + "\nnot json\n" + json.dumps(_row(step=2)) + "\n",
                 encoding="utf-8")
    rows, broken = steps.read_trace(p)
    assert len(rows) == 2 and broken == 1
    assert [r["step"] for r in rows] == [1, 2]


def test_the_scene_block_becomes_human_sentences():
    """★ 2026-09-23（用户：「最后画面的截图不好分析，运营懵、我们 AI 也懵」）：
    产物在**没做成**那一步录下的**现场**（`scene`）翻成人话 ——
    这几句就是「不用看截图也读得懂」的那一半（视觉模型今天没配）。
    """
    row = _row(ok=False, progress=None, note="页面上没找到「发送」，这一步没做成",
               scene={"ready": "loading", "overlay": "div#onetrust-banner",
                      "at_point": "div.cookie-banner", "texts": ["接受", "更 多", "关闭"]})
    why = steps.diagnose(row)
    assert any("点到的其实是 `div.cookie-banner`" in w for w in why), why
    assert any("同意类容器" in w and "onetrust" in w for w in why), why
    assert any("还没加载完" in w and "loading" in w for w in why), why
    assert any("最显眼" in w and "接受" in w for w in why), why
    # 没有现场时**一个字都不许编**（「没有现场」与「现场一切正常」不是一件事）
    assert not any("点到的其实是" in w for w in steps.diagnose(_row())), steps.diagnose(_row())
