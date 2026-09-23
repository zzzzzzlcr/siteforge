"""`GET /job/{id}/steps`：把**运行**那趟的 trace 端成三层 + 诊断。

⚠️ 这是**加法式**的：既有 `tests/test_service_steps.py` 一个用例都没动，
夹具（`_run_job` / 那几个常量）**照抄**那边的，不另起一套
（本仓最贵的坑之一就是同一件事两套判据）。
"""
import json

from test_service_steps import PERFORMED, SITE, _run_job


def _write_trace(svc, tmp_path, rows, extra_garbage=False):
    root = tmp_path / "selftest"
    run = root / ("%s-20260923-000000" % SITE)
    run.mkdir(parents=True, exist_ok=True)
    svc._selftest_root = str(root)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if extra_garbage:
        body += "\nnot json"
    (run / ("%s.evidence.trace.jsonl" % SITE)).write_text(body + "\n", encoding="utf-8")


def test_the_endpoint_lays_out_the_three_layers(tmp_path, monkeypatch):
    """跳过的 / 动作成功但页面没动的 / 靠恢复救回来的 —— 三层必须分得开。"""
    client, job_id, _, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED})
    _write_trace(client.app.state.service, tmp_path, [
        {"step": 1, "action": "goto", "target": "打开页面", "ok": None, "skipped": True,
         "progress": None, "note": "第 1 步跳过：这一页不像那个状态"},
        {"step": 2, "action": "click", "target": "订阅", "ok": True, "progress": False,
         "fallback_level": 0, "selector_used": "#a", "note": "点了「订阅」；页面没有变化"},
        {"step": 3, "action": "click", "target": "发送", "ok": True, "progress": True,
         "fallback_level": 3, "selector_used": 'form:has(input[name="email"]) button',
         "recovery": {"primary_selector": "#__BVID__408", "resolved_by": "label+near",
                      "confidence": 0.8},
         "note": "点了「发送」；【恢复】声明里的地址全失效"},
    ], extra_garbage=True)

    got = client.get("/job/%s/steps" % job_id).json()

    assert got["broken"] == 1, "读不动的行要被**数出来**，不许静默吞"
    assert [s["n"] for s in got["steps"]] == [1, 2, 3]
    # ① 跳过是第三种状态：不是「没做成」
    assert got["steps"][0]["layers"]["act"] == "跳过"
    assert "没轮到" in got["steps"][0]["layers"]["biz"]
    # ② 动作成了、业务没推进 —— 这一层必须看得见（最容易被读成「全成了」的那一格）
    assert got["steps"][1]["layers"]["act"] == "成了"
    assert got["steps"][1]["layers"]["biz"] == "页面没有变化"
    assert any("页面没有变化" in w for w in got["steps"][1]["why"])
    # ③ 元素层要说得出「靠恢复找到的」，并带上原来那条失效的地址
    elem = got["steps"][2]["layers"]["elem"]
    assert "恢复" in elem and "label+near" in elem and "#__BVID__408" in elem
    assert any("失效" in w for w in got["steps"][2]["why"])
    # ④ 面板要的那几格都在（少一格页面就得自己推，那就是第二套判据）
    for key in ("n", "action", "target", "note", "layers", "why", "selector_used",
                "frame_id", "recovery", "shots", "raw"):
        assert key in got["steps"][2], key
    assert "步" in got["say"]


def test_a_run_without_a_trace_says_so_instead_of_an_empty_list(tmp_path, monkeypatch):
    """「还没跑过」与「跑了、没问题」不是一件事 —— 空数组会让人读成后者。"""
    client, job_id, _, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED})
    client.app.state.service._selftest_root = str(tmp_path / "empty")
    got = client.get("/job/%s/steps" % job_id).json()
    assert got["steps"] == []
    assert "还没跑过" in got["say"]


def test_a_job_that_does_not_exist_is_404(tmp_path, monkeypatch):
    client, _, _, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED})
    r = client.get("/job/nope/steps")
    assert r.status_code == 404
