"""服务侧 /night：排单 / 拿掉 / 读账单 —— 只碰 tmp 里的 `runtime/auto/night/`。

钉三件：① 排进去的是**队列文件**（不是开一趟 job）；② 不合格在门口 400（国家不猜）；
③ 账单读的是**同一个目录**（宿主 cron 写的与面板看的是同一份）。
"""
from __future__ import annotations

import json
import pathlib
import sys

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import night, service  # noqa: E402


def _client(tmp_path):
    app = TestClient(service.create_app(json_backup_dir=str(tmp_path / "backups")),
                     raise_server_exceptions=False)
    svc = app.app.state.service
    svc._auto_root = str(tmp_path)
    return app, svc


def _order(**over):
    body = {"url": "https://new.site/quote", "goal": "走到报价页",
            "success_text": "Thank you.", "mode": "build", "country": "gb"}
    body.update(over)
    return body


def test_an_order_goes_into_the_queue_not_into_a_job(tmp_path):
    app, _ = _client(tmp_path)
    got = app.post(service.NIGHT_ORDER_PATH, json=_order()).json()
    assert got["ok"] is True and got["id"], got
    assert night.queue_path(tmp_path).exists(), "队列文件没落盘"
    assert app.get("/runs").json()["runs"] == [], "排单不该开 job"

    seen = app.get(service.NIGHT_PATH).json()
    assert len(seen["orders"]) == 1
    assert seen["orders"][0]["env"]["proxy_country"] == "GB"


def test_an_order_without_a_country_is_refused_at_the_door(tmp_path):
    app, _ = _client(tmp_path)
    res = app.post(service.NIGHT_ORDER_PATH, json=_order(country=""))
    assert res.status_code == 400, res.text
    assert "国家" in res.json()["detail"]
    assert not night.queue_path(tmp_path).exists(), "被拒的单不许落盘"


def test_an_order_can_be_taken_back_out(tmp_path):
    app, _ = _client(tmp_path)
    oid = app.post(service.NIGHT_ORDER_PATH, json=_order()).json()["id"]
    assert app.post(service.NIGHT_REMOVE_PATH, json={"id": oid}).json()["ok"] is True
    assert app.get(service.NIGHT_PATH).json()["orders"] == []
    assert app.post(service.NIGHT_REMOVE_PATH, json={"id": "nope"}).status_code == 404


def test_the_panel_reads_the_same_morning_note_the_host_writes(tmp_path):
    app, _ = _client(tmp_path)
    oid = app.post(service.NIGHT_ORDER_PATH, json=_order()).json()["id"]
    when = app.get(service.NIGHT_PATH).json()["date"]
    day = pathlib.Path(tmp_path) / "runtime" / "auto" / "night" / when
    day.mkdir(parents=True)
    (day / "morning.md").write_text("# 夜间批量 · 测试\n\n## 已推送（1）\n", encoding="utf-8")
    got = app.get(service.NIGHT_PATH).json()
    assert "已推送" in got["report"], got["report"]
    assert json.loads(night.queue_path(tmp_path).read_text(encoding="utf-8"))["orders"][0]["id"] == oid
