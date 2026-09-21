"""JSON 写回：预检不写、二次确认、并发覆盖闸、写后回读与备份。"""
from __future__ import annotations

import copy
import pathlib
import sys

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr, service  # noqa: E402

SITE = "example.test"
ORIGINAL = {"site": SITE, "steps": [{"action": "wait", "min": 1}],
            "success": {"any": [{"body_contains": ["done"]}]}}
PROPOSED = {"site": SITE, "steps": [{"action": "wait", "min": 2}],
            "success": {"any": [{"body_contains": ["done"]}]}}


class Backend:
    configured = True

    def __init__(self):
        self.config = copy.deepcopy(ORIGINAL)
        self.writes = []

    def form_config(self, site):
        assert site == SITE
        return copy.deepcopy(self.config)

    def update_form_config(self, site, config, *, operator=""):
        self.writes.append((site, copy.deepcopy(config), operator))
        self.config = copy.deepcopy(config)
        return fmr.FormWriteResult(fmr.WRITE_OK, 200, "success", "写回去了")


def _app(tmp_path, backend, monkeypatch):
    monkeypatch.setattr(service.configcheck, "load_rules", lambda: {
        "path": "/executor.py", "sha256": "a" * 64, "mtime": "now"})
    monkeypatch.setattr(service.configcheck, "check_config", lambda config, rules: [])
    return TestClient(service.create_app(
        failures_reader=backend, json_ws_url="ws://browser/x",
        json_rerun=lambda config, **kw: {"summary": {"status": "success"}, "facts": {}},
        json_backup_dir=str(tmp_path / "backups")), raise_server_exceptions=False)


def _prepare(app):
    return app.post(service.JSONWRITE_PREPARE_PATH, json={
        "site": SITE, "config": PROPOSED, "operator": "值班员 A"})


def test_prepare_does_not_write_and_commit_round_trips(tmp_path, monkeypatch):
    backend = Backend()
    app = _app(tmp_path, backend, monkeypatch)
    prepared = _prepare(app)
    assert prepared.status_code == 200, prepared.text
    assert backend.writes == [], "预检阶段调用了生产写口"

    done = app.post(service.JSONWRITE_COMMIT_PATH,
                    json={"ticket": prepared.json()["ticket"]})
    assert done.status_code == 200, done.text
    assert backend.config == PROPOSED
    assert backend.writes[0][2] == "值班员 A"
    backup = tmp_path / "backups" / (done.json()["backup_id"] + ".json")
    assert backup.is_file()


def test_ticket_is_one_use(tmp_path, monkeypatch):
    backend = Backend(); app = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    assert app.post(service.JSONWRITE_COMMIT_PATH, json={"ticket": ticket}).status_code == 200
    assert app.post(service.JSONWRITE_COMMIT_PATH, json={"ticket": ticket}).status_code == 409
    assert len(backend.writes) == 1


def test_changed_backend_is_not_overwritten(tmp_path, monkeypatch):
    backend = Backend(); app = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    backend.config["steps"][0]["min"] = 99
    got = app.post(service.JSONWRITE_COMMIT_PATH, json={"ticket": ticket})
    assert got.status_code == 409, got.text
    assert backend.writes == []


def test_saved_original_can_be_rolled_back(tmp_path, monkeypatch):
    backend = Backend(); app = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    done = app.post(service.JSONWRITE_COMMIT_PATH, json={"ticket": ticket}).json()
    rolled = app.post(service.JSONWRITE_ROLLBACK_PATH, json={
        "backup_id": done["backup_id"], "operator": "值班员 B"})
    assert rolled.status_code == 200, rolled.text
    assert backend.config == ORIGINAL
    assert backend.writes[-1][2].endswith("（人工回滚）")


def test_the_rerun_is_told_which_page_to_open(tmp_path, monkeypatch):
    """★★ 复跑**必须**被告知跑哪一页 —— 否则它是在**浏览器自己的控制台页**上跑的。

    【2026-09-21 实测】不给 `entry_url` 那一趟：窗口停在 `https://console.bitbrowser.net/?id=…`，
    执行器记下的每一句（`click: element not found` / `deferred unfilled`）**全是假账**，
    页面事实也量不到 ⇒ 屏幕上看起来只是「这份配置跑不起来」（与 py 那条路今天修的是同一件事：
    生产起脚本之前先 `cdp navi <入口网址>`）。

    ⚠️ 这一条量的是**接线**（服务有没有把它传下去）—— 复跑那根线在用例里是 stub，
    所以只有「传没传」看得见（真跑那条路另有一条：
    `tests/test_jsondiag.py::test_the_executor_own_helper_wins_the_path_race` 那一族）。
    """
    backend = Backend()
    seen: list = []
    monkeypatch.setattr(service.configcheck, "load_rules", lambda: {
        "path": "/executor.py", "sha256": "a" * 64, "mtime": "now"})
    monkeypatch.setattr(service.configcheck, "check_config", lambda config, rules: [])
    app = TestClient(service.create_app(
        failures_reader=backend, json_ws_url="ws://browser/x",
        json_rerun=lambda config, **kw: (seen.append(kw) or
                                        {"summary": {"status": "success"}, "facts": {}}),
        json_backup_dir=str(tmp_path / "backups")), raise_server_exceptions=False)
    app.get(service.JSODIFF_PATH + "?site=" + SITE)          # 只读那一下也要导航
    _prepare(app)
    assert len(seen) >= 2, seen
    assert all(kw.get("entry_url") == "https://%s/" % SITE for kw in seen), seen
    #: 配置里没有 `site` ⇒ **不许编一个网址**（编一个就跑错页，比不跑更坏）
    assert service._json_entry_url({}) == "" and service._json_entry_url({"site": ""}) == ""
    #: 已经带 scheme 的原样用；带尾斜杠的不重复加
    assert service._json_entry_url({"site": "https://x.test/a"}) == "https://x.test/a"
    assert service._json_entry_url({"site": "x.test/a/"}) == "https://x.test/a/"


def test_prepare_requires_explicit_success(tmp_path, monkeypatch):
    backend = Backend()
    monkeypatch.setattr(service.configcheck, "load_rules", lambda: {
        "path": "/executor.py", "sha256": "a" * 64, "mtime": "now"})
    monkeypatch.setattr(service.configcheck, "check_config", lambda config, rules: [])
    app = TestClient(service.create_app(
        failures_reader=backend, json_ws_url="ws://browser/x",
        json_rerun=lambda config, **kw: {"summary": {"status": "failed"}, "facts": {}},
        json_backup_dir=str(tmp_path / "backups")), raise_server_exceptions=False)
    got = _prepare(app)
    assert got.status_code == 409, got.text
    assert backend.writes == []
