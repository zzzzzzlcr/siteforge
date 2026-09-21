"""合规闸的入口：`GET /configcheck?site=<键>` —— 读那份配置，逐格对照执行器认不认。

## 这一条路上的「空」只有一种，但它**最像「没问题」**

| 情形 | 给什么 | 不许被读成 |
|---|---|---|
| 配置读回来了、**合规** | `200` + `errors: []` + 一句「没有要改的」 | —— |
| 配置读回来了、**不合规** | `200` + 逐条人话 | ❌ 量不到（**这是量到了**的结果，不是错误） |
| **配置读不回来**（没配置 / 连不上） | **非 2xx** | ❌ **「这份配置没问题」** |

★ 第三行是这一片的命门：读不回来却被画成「合规」，就是在告诉运营「这份配置是好的」——
与「无效 key 被标成没有失败 ✓」是**同一个形状的下一站**。

⚠️ 这一份**不打真网络**（`failures_reader` 注入桩），但**要读真执行器**：
判据是**现量**的（见 `test_configcheck.py` 那几条），不许在这一层抄一份动作表。
"""
from __future__ import annotations

import pathlib
import sys

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import FAKE_TOKEN, Recorder, envelope  # noqa: E402
from test_service_input import StubWindow  # noqa: E402

SITE_KEY = "cvrefresh.com"

#: **cvrefresh.com 那份配置的逐字复制**（2026-09-21 经 B1 的读口取回，实测）——
#: 与 `test_configcheck.py` / `test_jsondiag.py` 是同一份。
CLEAN = {
    "form_type": "magic_link",
    "site": SITE_KEY,
    "steps": [
        {"action": "wait", "max": 10, "min": 5},
        {"action": "click", "find": {"text": "Refresh my resume"}},
        {"action": "form",
         "field": {"label": "Email", "placeholder": "your@email.com", "type": "email"},
         "value": "{{random.email}}"},
    ],
    "success": {"any": [{"body_contains": ["Check your email"]}]},
}


def _fmr_stub(*bodies):
    """一个**真的** `FmrClient`，只把传输那一格换成桩（判据代码真跑）。"""
    return fmr.FmrClient(token=FAKE_TOKEN, opener=Recorder(*bodies))


def _client(*, fmr_client):
    return TestClient(service.create_app(failures_reader=fmr_client, window=StubWindow()),
                      raise_server_exceptions=False)


def _config(**over):
    got = dict(CLEAN)
    got.update(over)
    return envelope({"site": SITE_KEY, "steps": got})


def test_a_compliant_config_comes_back_clean():
    """★ 合规 ⇒ `200` + 两条清单都是空的 + 一句**说清「没有要改的」**的话。"""
    app = _client(fmr_client=_fmr_stub(_config()))

    r = app.get(service.CONFIGCHECK_PATH, params={"site": SITE_KEY})

    assert r.status_code == 200, r.text
    got = r.json()
    assert got["site"] == SITE_KEY, got
    assert got["errors"] == [], got
    assert got["notes"] == [], got
    assert "没有要改的" in got["say"], got["say"]


def test_an_unclean_config_is_a_200_with_the_list_not_a_failure():
    """★ **不合规 ≠ 量不到**：读回来了、逐格看过了 ⇒ `200` + 那几条。

    把它做成非 2xx 的坏处：屏幕上与「配置读不回来」分不开，
    而两件事的处置完全不同（一个去改配置，一个去查后端/网络）。
    """
    bad = dict(CLEAN, steps=[{"action": "clik", "find": {"text": "x"}}])
    app = _client(fmr_client=_fmr_stub(_config(**{"steps": bad["steps"]})))

    r = app.get(service.CONFIGCHECK_PATH, params={"site": SITE_KEY})

    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got["errors"]) == 1, got
    assert "clik" in got["errors"][0]["say"], got["errors"][0]
    #: 那句抬头要说清「有几处」「别写回」，而不是干说一句「有问题」
    assert "1" in got["say"] and "写回" in got["say"], got["say"]


def test_a_missing_site_key_is_a_free_400():
    """没给站点键 ⇒ **400**（调用方自己的毛病），而且**一个请求都没发出去**。"""
    rec = Recorder(_config())
    app = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.get(service.CONFIGCHECK_PATH)

    assert r.status_code == 400, r.text
    assert rec.urls == [], "没站点键却发了请求：%r" % rec.urls


def test_a_config_that_cannot_be_read_is_never_drawn_as_compliant():
    """★★ **读不回来 ⇒ 非 2xx** —— 这是这一片最贵的那个形状。

    一个 `200 {"errors": []}` 在屏幕上与「这份配置没问题」**一模一样**，
    而它真正的意思是「我根本没读到」。
    """
    def boom(url, headers):
        raise RuntimeError("connection refused")
    app = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=boom))

    r = app.get(service.CONFIGCHECK_PATH, params={"site": SITE_KEY})

    assert not (200 <= r.status_code < 300), r.text
    assert "没有要改的" not in r.text, r.text


def test_the_answer_says_which_executor_version_the_rules_came_from():
    """★ 判据是**现量**的 ⇒ 答案里要带**量的是哪一版**（漂了才看得见）。

    那个执行器在 `/opt/skills/auto-farm-skill/`，**别人在改**。
    没有这个指纹，闸就会在无声中拿旧规矩卡新执行器。
    """
    app = _client(fmr_client=_fmr_stub(_config()))

    got = app.get(service.CONFIGCHECK_PATH, params={"site": SITE_KEY}).json()

    assert got["rules"]["path"].endswith("json_executor.py"), got["rules"]
    assert len(got["rules"]["sha256"]) == 64, got["rules"]


def test_keys_we_did_not_measure_are_reported_as_notes_only():
    """没量到的键 ⇒ 进 `notes`、**不进 `errors`**，而且那句抬头仍然说「都认」。"""
    steps = list(CLEAN["steps"]) + [{"action": "wait", "min": 1, "max": 2, "note": "运营写的"}]
    app = _client(fmr_client=_fmr_stub(_config(**{"steps": steps})))

    got = app.get(service.CONFIGCHECK_PATH, params={"site": SITE_KEY}).json()

    assert got["errors"] == [], got
    assert len(got["notes"]) == 1, got
    assert "note" in got["notes"][0]["say"], got["notes"][0]
    assert "没有要改的" in got["say"], got["say"]
