"""T7：JSON 写回那个键**带目录 / 从失败记录带出来**才算数（不许人手工打了就算）。

判据与 py 上传**共用一处**（`_backend_key`）：人把「站点网址」那一格改了，旧键继续用就会
**静默改错站**（面板上开的是 B 站，服务拿 A 站的键去写、中间没有一处会响）。
⚠️ 两个新格子都没给 ⇒ **老那一趟一个字不变** —— 这一条也要钉住（不许收紧成「必须先给」）。
"""
import pathlib
import sys

import pytest
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402


def _svc(tmp_path):
    app = TestClient(service.create_app(json_backup_dir=str(tmp_path / "backups")),
                     raise_server_exceptions=False)
    return app.app.state.service


def _refused(svc, *args, **kw) -> str:
    """跑一下，把它抛出来的话**原样**拿回来（抛什么不管；这里只看「是不是那道键的 400」）。"""
    try:
        svc.jsonwrite_prepare(*args, **kw)
    except Exception as exc:                      # noqa: BLE001 —— 往下走会碰真后端，什么都可能
        return str(getattr(exc, "detail", "") or exc)
    return ""


def test_a_key_that_no_longer_matches_the_url_it_came_with_is_refused(tmp_path):
    """★ 键只在**还配得上**它跟着来的那串网址时才算数（改了那一格 ⇒ 那道 400）。"""
    said = _refused(_svc(tmp_path), "a.com/x", {"site": "a.com/x"}, "op",
                    url="b.com/y", fix_site_url="a.com/x")
    assert "站点键" in said, said


def test_the_key_that_still_matches_gets_through(tmp_path):
    """跟着来的就是那一串 ⇒ 不再被那道 400 拦（往下会去真后端，这里只量这一道）。"""
    said = _refused(_svc(tmp_path), "a.com/x", {"site": "a.com/x"}, "op",
                    url="a.com/x", fix_site_url="a.com/x")
    assert "站点键" not in said, said


def test_without_the_two_new_cells_nothing_changes(tmp_path):
    """★ 两个新格子都没给 ⇒ 老那一趟照旧（键就是 `site`，不许因为缺格就报「必须填站点键」）。"""
    said = _refused(_svc(tmp_path), "a.com/x", {"site": "a.com/x"}, "op")
    assert "站点键" not in said, said
