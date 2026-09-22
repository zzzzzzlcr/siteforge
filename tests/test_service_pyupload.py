"""py 上传：预检不写、二次确认、写前防覆盖、写后回读、不一致就回滚（2026-09-22）。

用户问的那一格：「现在面板能上传？就是测通后得新脚本/生成得新脚本」——
这一层就是那句话的接线（面板上那三下打的就是这三个端点）。
"""
from __future__ import annotations

import pathlib
import sys
import types

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr, pywrite, service  # noqa: E402

KEY = "qualify.example.io"
URL = "https://qualify.example.io/quiz"
JOB = "job-py-1"
SOURCE = ("#!/usr/bin/env python3\n"
          "def main():\n"
          "    return 0\n")
ORIGINAL = ("#!/usr/bin/env python3\n"
            "def main():\n"
            "    return 1\n")


class Backend:
    """只替掉后端那两下（读脚本 / 传脚本），其余仍走真 service。"""

    configured = True
    write_token = "write-token-for-test"
    base = "https://fmr.example"

    def __init__(self, *, content: str = ORIGINAL, store_instead: str = None):
        self.content = content
        self.store_instead = store_instead
        self.writes = []

    def form_script(self, site, *, allow_disabled: bool = False):
        assert site == KEY, site
        return {"type": "py", "source": self.content, "sha256": pywrite.sha256_text(self.content)}

    def update_form_script(self, key, source, *, operator):
        self.writes.append((key, source, operator))
        self.content = self.store_instead if self.store_instead is not None else source
        return fmr.FormWriteResult(fmr.WRITE_OK, 200, "success", "上传好了")


def _app(tmp_path, backend, monkeypatch, *, delivered=True, source=SOURCE):
    app = TestClient(service.create_app(
        failures_reader=backend, json_ws_url="ws://browser/x",
        json_rerun=lambda config, **kw: {"summary": {"status": "success"}, "facts": {}},
        json_backup_dir=str(tmp_path / "backups")), raise_server_exceptions=False)
    svc = app.app.state.service
    path = tmp_path / "forms" / "sites" / "qualify.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    end = {"delivered": delivered, "end_reason": "delivered" if delivered else "stopped",
           "py_path": str(path), "site": "qualify", "say": "", "selftest": {}, "visits": []}
    monkeypatch.setattr(svc, "_view", lambda job_id: {
        "job_id": job_id, "status": service.DONE, "delivered": bool(delivered),
        "say": "", "error": None, "result": end, "gate": None, "site": "qualify"})
    #: ⚠️ 产物那一格走的是仓里**唯一**判它的那一处（`_artifact_state`）—— 这一支的用例
    #: 注入它（判官自己有它自己的用例），否则我们要么去搭真 checkpoint、要么量到别的层。
    #: 这一支没有真 checkpoint（job 是手工塞进登记表的）⇒ 快照那一格也注入成「没有」。
    monkeypatch.setattr(svc, "_snapshot", lambda job_id: None)
    monkeypatch.setattr(svc, "_artifact_state", lambda view, values=None: (
        {"bytes": source.encode("utf-8"), "filename": "qualify.py", "path": str(path),
         "say": "产物拿到了"} if delivered else
        {"bytes": None, "filename": None, "path": None,
         "say": "这一趟**还没有落盘**（没有可上传的产物）—— 先在「自测」那一步过了、"
                "把 py 写进站点目录，再来传。"}))
    svc._jobs[JOB] = types.SimpleNamespace(
        brief={"url": URL, "fix_site": KEY, "fix_site_url": URL})
    return app, svc, path


def _prepare(app, operator="值班员 A"):
    return app.post(service.PYWRITE_PREPARE_PATH.format(job_id=JOB),
                    json={"operator": operator})


def test_a_brand_new_site_can_be_uploaded_too(tmp_path, monkeypatch):
    """★ 2026-09-22 真事（用户：「**能下载 py 了但是好像不能直接上传**」）。

    后端**没有这个键**（新站第一次传）时，那个读口回 404 ⇒ 原来预检直接 502 ⇒
    「新建」这条路被「先读一遍现值」挡住（面板上就表现为「不能上传」）。
    修法：404 = **「后端还没有这一份」**（不是失败）：现值按空算、票照发，
    确认那一下走 `created: true` 那条路**新建**（今天实测过）。

    判据五条：预检 200 ✓；`backend_sha256` 是**空串**（没有可保护的东西）✓；
    那句话**只说新建、不说「后端现在那份 sha」**（说成后者是假话）✓；
    确认之后**真写了**（写的是新那份）✓；全程**一个字节都没在预检那一步写出去**✓。
    """
    class Nova(Backend):
        """后端不认识这个键（**第一次传**）：还没写过之前，读那一下回 404。

        ⚠️ 写**之后**必须能读到新那份 —— 否则量到的就不是「新建」，而是「回读对不上 ⇒
        自动回滚」（我第一版就是这么写的，结果那条安全网当场把我拦下来了 ✓ 它是对的）。
        """
        def __init__(self, **kw):
            super().__init__(**kw)
            self.content = None                     # ← 「后端还没有这一行」

        def form_script(self, site, *, allow_disabled: bool = False):
            assert site == KEY, site
            if self.content is None:
                raise fmr.FmrRefused("后端回 404：这个键不认识", status=404, msg="script not found")
            return {"type": "py", "source": self.content,
                    "sha256": pywrite.sha256_text(self.content)}

    backend = Nova()
    app, _svc, _path = _app(tmp_path, backend, monkeypatch)

    prepared = _prepare(app)
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    #: ⚠️ 空值也有指纹（`sha256("")`）—— 那就是「防覆盖」在预检/确认之间要比的东西：
    #: 两次读都「不认识」⇒ 两次都是这个值 ⇒ 那道闸照旧成立 ✓（不是「没有指纹可比」）。
    assert body["backend_sha256"] == pywrite.sha256_text(""), body
    assert "还不认识这个键" in body["say"] and "新建" in body["say"], body["say"]
    assert "后端现在那份 sha" not in body["say"], body["say"]
    assert backend.writes == [], "预检那一步写了：%r" % backend.writes

    done = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB),
                    json={"ticket": body["ticket"]})
    assert done.status_code == 200, done.text
    assert backend.writes and backend.writes[-1][1] == SOURCE, backend.writes


def test_prepare_never_writes_and_commit_uploads_with_a_readback(tmp_path, monkeypatch):
    backend = Backend()
    app, _svc, path = _app(tmp_path, backend, monkeypatch)
    prepared = _prepare(app)
    assert prepared.status_code == 200, prepared.text
    assert backend.writes == [], "预检阶段就调了生产写口"
    body = prepared.json()
    assert body["local_sha256"] == pywrite.sha256_text(SOURCE), body
    assert body["backend_sha256"] == pywrite.sha256_text(ORIGINAL), body
    assert body["bytes"] == len(SOURCE.encode("utf-8")), body

    done = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB),
                    json={"ticket": body["ticket"]})
    assert done.status_code == 200, done.text
    assert backend.content == SOURCE, "后端那份不是本地这份"
    key, sent, who = backend.writes[0]
    assert (key, sent, who) == (KEY, SOURCE, "值班员 A"), backend.writes[0]
    #: 原件落盘（回滚要拿得回来）—— 扩展名是 .py、内容是**原文**、指纹对得上
    backup = tmp_path / "backups" / (done.json()["backup_id"] + ".py")
    assert backup.is_file() and backup.read_text(encoding="utf-8") == ORIGINAL
    assert "回读" in done.json()["say"], done.json()["say"]


def test_the_ticket_is_one_shot(tmp_path, monkeypatch):
    backend = Backend()
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    assert app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB),
                    json={"ticket": ticket}).status_code == 200
    again = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB), json={"ticket": ticket})
    assert again.status_code == 409, again.text
    assert len(backend.writes) == 1, backend.writes


def test_a_backend_changed_after_the_precheck_is_not_overwritten(tmp_path, monkeypatch):
    backend = Backend()
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    backend.content = ORIGINAL + "# 别人动过\n"
    got = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB), json={"ticket": ticket})
    assert got.status_code == 409, got.text
    assert backend.writes == [], "被改过还硬压上去了"
    assert "没上传" in got.json()["detail"], got.json()


def test_a_readback_mismatch_puts_the_original_back(tmp_path, monkeypatch):
    """★★ 最贵的那一格：写口说成了、**回读却对不上** ⇒ 把原来那份传回去，并如实说回滚成没成。"""
    backend = Backend(store_instead=ORIGINAL + "# 后端自己改的\n")
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    got = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB), json={"ticket": ticket})
    assert got.status_code == 502, got.text
    #: 两次写：一次是传新版，一次是**把原件传回去**
    assert [w[1] for w in backend.writes] == [SOURCE, ORIGINAL], backend.writes
    assert backend.writes[-1][2].endswith("（自动回滚）"), backend.writes[-1]
    assert "回滚" in got.json()["detail"]["say"], got.json()


def test_a_run_that_never_landed_has_nothing_to_upload(tmp_path, monkeypatch):
    backend = Backend()
    app, _svc, _p = _app(tmp_path, backend, monkeypatch, delivered=False)
    got = _prepare(app)
    assert got.status_code == 409, got.text
    assert "还没有落盘" in got.json()["detail"], got.json()
    assert backend.writes == []


def test_the_same_content_is_not_uploaded_twice(tmp_path, monkeypatch):
    backend = Backend(content=SOURCE)          # 后端那份**已经**是本地这份
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    got = _prepare(app)
    assert got.status_code == 409, got.text
    assert "已经是" in got.json()["detail"], got.json()
    assert backend.writes == []


def test_no_write_token_means_nothing_leaves_the_machine(tmp_path, monkeypatch):
    backend = Backend()
    backend.write_token = ""
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    got = _prepare(app)
    assert got.status_code == 503, got.text
    assert "没配写用的 token" in got.json()["detail"], got.json()
    assert backend.writes == []


def test_saved_original_can_be_rolled_back(tmp_path, monkeypatch):
    backend = Backend()
    app, _svc, _p = _app(tmp_path, backend, monkeypatch)
    ticket = _prepare(app).json()["ticket"]
    done = app.post(service.PYWRITE_COMMIT_PATH.format(job_id=JOB),
                    json={"ticket": ticket}).json()
    rolled = app.post(service.PYWRITE_ROLLBACK_PATH.format(job_id=JOB),
                      json={"backup_id": done["backup_id"], "operator": "值班员 B"})
    assert rolled.status_code == 200, rolled.text
    assert backend.content == ORIGINAL
    assert backend.writes[-1][2].endswith("（人工回滚）"), backend.writes[-1]
