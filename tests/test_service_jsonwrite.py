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
