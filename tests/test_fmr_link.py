"""`POST /api/quest/formConfig/link` 的客户端契约（后端 2026-10-22 `df4938d`）。"""
from __future__ import annotations

import json

import pytest

from agent import fmr
from test_fmr import FAKE_TOKEN, PostRecorder

WRITE = "WRITE-TOKEN-LINK-1"


def linker(rec):
    """写口要的是 write_token —— 与读 token 分开传。"""
    return fmr.FmrClient(token=FAKE_TOKEN, write_token=WRITE, poster=rec)


def test_link_posts_json_to_the_link_route_with_the_write_token():
    rec = PostRecorder({"status": 200, "msg": "success",
                        "data": {"site": "a.test/x", "config_id": 101, "created": True}})
    got = linker(rec).form_config_link(
        "a.test/x", 101, operator="claude (classify)", reason="testid: step-content-item-")
    assert got == {"site": "a.test/x", "config_id": 101, "created": True}
    assert rec.urls[0] == "https://fmr.3tkj.cn/api/quest/formConfig/link", rec.urls[0]
    assert rec.headers[0].get("X-Api-Token") == WRITE, rec.headers[0]
    sent = json.loads(rec.sent[0].decode("utf-8"))
    assert sent == {"site": "a.test/x", "config_id": 101,
                    "operator": "claude (classify)",
                    "reason": "testid: step-content-item-"}, sent


def test_link_reads_body_status_404_instead_of_treating_http_200_as_success():
    rec = PostRecorder({"status": 404, "msg": "config not found: 999"})
    with pytest.raises(fmr.FmrRefused) as got:
        linker(rec).form_config_link("a.test/x", 999, operator="who")
    assert got.value.status == 404


def test_link_refuses_locally_without_operator_or_config_id():
    rec = PostRecorder({"status": 200, "msg": "success", "data": {}})
    with pytest.raises(fmr.FmrUnmeasured):
        linker(rec).form_config_link("a.test/x", 101, operator="")
    with pytest.raises(fmr.FmrUnmeasured):
        linker(rec).form_config_link("a.test/x", 0, operator="who")
    assert rec.sent == [], "本地能查出来的错不该发请求"


def test_link_without_write_token_sends_nothing():
    rec = PostRecorder({"status": 200, "msg": "success", "data": {}})
    with pytest.raises(fmr.FmrNoToken):
        fmr.FmrClient(token=FAKE_TOKEN, write_token="", poster=rec).form_config_link(
            "a.test/x", 101, operator="who")
    assert rec.sent == []
