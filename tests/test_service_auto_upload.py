"""T4：auto 那条路「**两遍内过了**」⇒ 自动上传（谁传的用字面 `auto` 签）。

打的是 `service._auto_upload`（那一段只碰 `job.brief` / `job.auto_state` / `job.say` / `job.lock`，
所以用一个假的 job 就够）；上传那两步是**别人已经写好的**（`py_upload_prepare` / `py_upload_commit`
—— 回读不一致时的自动回滚就在 commit 里），这儿只验「**什么时候**去传、以谁的名字、不传的时候说没说」。
"""
import pathlib
import sys
import threading

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service, state  # noqa: E402


class Snap:
    """快照那两格（`_auto_upload` 只看 `values`）。"""

    def __init__(self, values):
        self.values = values


class FakeJob:
    """假的 job —— 只备 `_auto_upload` 会碰的几格（不手拼真 Job，免得跟着它的字段跑）。"""

    def __init__(self, brief=None, auto_state="", job_id="j-1"):
        self.brief = dict(brief or {})
        self.auto_state = auto_state
        self.job_id = job_id
        self.say = ""
        self.lock = threading.Lock()


def _svc(tmp_path):
    app = TestClient(service.create_app(json_backup_dir=str(tmp_path / "backups")),
                     raise_server_exceptions=False)
    return app.app.state.service


def _wire(svc, calls, *, delivered=True, prepare_raises=None):
    """把「读快照」与上传那两步换成桩（真那两步会打网络：用例里绝不发请求）。"""
    svc._snapshot = lambda job_id: Snap(
        {"end_reason": "delivered" if delivered else "human_stop"})

    def prepare(job_id, operator):
        calls.append(("prepare", job_id, operator))
        if prepare_raises is not None:
            raise prepare_raises
        return {"ticket": "t-1"}

    def commit(job_id, ticket):
        calls.append(("commit", job_id, ticket))
        return {"say": "传好了"}

    svc.py_upload_prepare = prepare
    svc.py_upload_commit = commit


def test_the_success_end_reason_is_the_one_state_declares():
    """钉住那个字面值（`state.END_DELIVERED`）—— 它挪了这条就要红，不是悄悄不传了。"""
    assert state.END_DELIVERED == "delivered"



def test_the_operator_is_literally_auto(tmp_path):
    """用户拍的：自动上传以字面 `auto` 签（后端审计里与人传的分得开）。"""
    assert _svc(tmp_path).AUTO_OPERATOR == "auto"

def test_delivered_auto_uploads_with_its_own_name(tmp_path):
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls)
    job = FakeJob(brief={"auto": True})
    svc._auto_upload(job)
    assert calls == [("prepare", "j-1", "auto"), ("commit", "j-1", "t-1")], calls
    assert "自动传上去了" in job.say and "auto" in job.say


def test_a_human_run_never_uploads_itself(tmp_path):
    """人按的那条路：上传**永远**是人的动作（一个字节都不发）。"""
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls)
    svc._auto_upload(FakeJob(brief={}))
    assert calls == [], "人那条路上自己传了 —— 那是替人做主"


def test_needs_human_never_uploads(tmp_path):
    """两遍没通 / 做不成 ⇒ **没修好**，什么都不传。"""
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls)
    svc._auto_upload(FakeJob(brief={"auto": True}, auto_state="needs_human"))
    assert calls == [], calls


def test_a_disabled_row_is_not_uploaded_and_says_so(tmp_path):
    """用户拍的「**停用行不许自动上传**」—— 而且要说出是这个原因。"""
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls)
    job = FakeJob(brief={"auto": True, "allow_disabled": True})
    svc._auto_upload(job)
    assert calls == [], calls
    assert "停用行" in job.say, job.say


@pytest.mark.parametrize("reason", ["human_stop", "window_gone", "no_success_text", ""])
def test_anything_that_is_not_delivered_is_not_uploaded(tmp_path, reason):
    """不是「真落盘了」那个结局 ⇒ 一个字节都不发。"""
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls, delivered=False)
    svc._snapshot = lambda job_id: Snap({"end_reason": reason})
    svc._auto_upload(FakeJob(brief={"auto": True}))
    assert calls == [], (reason, calls)


def test_a_failed_upload_is_told_not_raised(tmp_path):
    """预检/上传自己会抛（票据、鉴权那些）：**不抛出去**（那是旁路），要在 job 上说出来。"""
    calls = []
    svc = _svc(tmp_path)
    _wire(svc, calls, prepare_raises=HTTPException(status_code=400, detail="没说谁确认的"))
    job = FakeJob(brief={"auto": True})
    svc._auto_upload(job)
    assert calls == [("prepare", "j-1", "auto")], calls
    assert "没成" in job.say and "没说谁确认的" in job.say, job.say
