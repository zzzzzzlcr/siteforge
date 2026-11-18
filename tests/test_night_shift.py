"""夜间批量：门口校验 / 载荷 / 账单 / 上传核对 —— 都不碰网络（网络那半在 `agent/night_run.py`）。

钉住的是「免费就该拦下」的那几件：国家缺失、build 没判据、fix 可继承；
以及「载荷只带服务认的字段、auto 恒为真」、账单分档顺序、交付后**后台 sha256** 才算推了。
"""
from __future__ import annotations

import hashlib
import pathlib
import importlib.util
import json
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import night, night_run  # noqa: E402


def test_a_build_order_needs_a_country_and_a_success_criterion():
    ok = {"mode": "build", "url": "https://new.site/quote", "goal": "走到报价页",
          "success_urls": ["/thank-you"], "env": {"proxy_country": "gb"}}
    assert night.problems(ok) == []
    assert night.country_of(ok) == "GB"

    said = " ".join(night.problems(dict(ok, env={})))
    assert "国家" in said, said

    said = " ".join(night.problems(dict(ok, success_urls=[])))
    assert "成功判据" in said, said


def test_a_fix_order_may_inherit_the_old_scripts_criterion():
    """fix 两格可空 —— 服务会搬旧脚本自己声明的那条（这里是**故意**不拦）。"""
    order = {"mode": "fix", "url": "a.com/x", "evidence": "旧脚本停在 thank-you 前",
             "env": {"proxy_country": "US"}}
    assert night.problems(order) == []
    assert night.problems(dict(order, evidence=""))[0].startswith("没有 goal")


def test_the_payload_is_auto_and_keeps_the_country():
    order = {"id": "o1", "url": "a.com/x", "goal": "走通", "mode": "fix",
             "success_text": "Thank you", "env": {"proxy_country": "us"}}
    got = night.payload(order)
    assert got["auto"] is True
    assert got["env"]["proxy_country"] == "US"
    assert "id" not in got, "本脚本自己的账不许发给服务"


def test_the_morning_note_groups_in_the_reading_order():
    rows = [{"id": "o3", "site": "c.com", "bucket": "needs_human", "say": "窗口没了"},
            {"id": "o1", "site": "a.com", "bucket": "pushed", "job_id": "job-1", "say": "已传"},
            {"id": "o2", "site": "b.com", "bucket": "not_generated", "say": "没出 py"}]
    text = night.render(rows, "2026-10-29")
    assert text.index("已推送") < text.index("需要人") < text.index("没生成")
    assert "o1" in text and "job-1" in text and "窗口没了" in text


def test_a_delivered_py_is_only_pushed_when_the_backend_sha_matches(tmp_path, monkeypatch):
    """账单最上面那一段的判据：**后台 sha256 与盘上一致**才算推了（服务也是这么校验的）。"""
    from agent import fmr

    py = tmp_path / "sites" / "a.py"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"print('ok')\n")
    want = hashlib.sha256(py.read_bytes()).hexdigest()

    class FakeFmr:
        def __init__(self, *a, **k):
            pass

        def form_script(self, key):
            return {"sha256": want}

    monkeypatch.setattr(fmr, "FmrClient", FakeFmr)
    view = {"site": "a.com/x", "result": {"end_reason": "delivered", "py_path": str(py)}}
    got, _said = night_run.upload_state(tmp_path, {"allow_disabled": False}, view)
    assert got is True

    py.write_bytes(b"print('changed')\n")
    got, said = night_run.upload_state(tmp_path, {"allow_disabled": False}, view)
    assert got is False and "不是这一份" in said

    got, said = night_run.upload_state(tmp_path, {"allow_disabled": True}, view)
    assert got is False and "不自动上传" in said


def test_settled_and_interrupted_look_across_all_dates(tmp_path):
    """supervisor 常驻后「换了一天」不许把跑过的单再跑一遍；跑到一半的不重跑。"""
    day1 = night.night_dir(tmp_path) / "2026-10-28"
    day2 = night.night_dir(tmp_path) / "2026-10-29"
    day1.mkdir(parents=True)
    day2.mkdir(parents=True)

    def put(day, rows):
        (day / "results.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    put(day1, [{"id": "a", "bucket": "pushed"},
               {"id": "b", "bucket": "timeout"},
               {"id": "c", "bucket": "started"}])
    put(day2, [{"id": "d", "bucket": "started"},
               {"id": "d", "bucket": "failed"}])

    assert night.settled(tmp_path) == {"a", "b", "d"}
    assert night.interrupted(tmp_path) == {"c"}


def test_the_supervisor_config_runs_the_watch_worker(tmp_path):
    """配置必须是**这个仓库**的绝对路径 + `--watch`（不是系统那套 supervisord）。"""
    spec = importlib.util.spec_from_file_location(
        "night_supervisor", ROOT / "tools" / "night-supervisor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "runtime" / "auto" / "night" / "supervisor"
    text = mod.config_text(tmp_path, "/usr/bin/python3", tmp_path / "tools" / "night-shift.py",
                           home=home)
    assert "--watch" in text and "--root %s" % tmp_path in text
    assert str(home) in text
