"""Task 13 ②「服务端点」：`GET /failures` 与 `GET /failures/{单号}/evidence`。

## 这一份钉的两件事

**① 端出去的是人话，不是 FMR 的 JSON。** 「别把 FMR 的 JSON 原样端出去」——
所以每一条断言里都带一句「那一串码不在」：`site_specific` / `failed` / `US` /
`created_at` / `formLog` / `no_success`。少了这一句，「服务转发了一遍后端 JSON」
这种改法照绿。

**② 「量不到」不许被读成「没有失败」**（这一份的重心）。它在**端点这一层**的形状是：

| 情形 | 端点给的 | 页面上那句 |
|---|---|---|
| 真量了、真没有 | **200** + `failures: []` + `say`「没有失败的记录」 | 绿：这一栏是空的是**量出来**的 |
| 没配 token | **503** + `detail` 人话 | 红：「量不到」 |
| 连不上 / 超时 / 正文不是 JSON | **502** + `detail` 人话 | 红：「量不到」 |
| 后端回 401/404/400 | **502** + `detail`（带后端那句） | 红：「量不到」 |

⚠️ **四种情形里只有第一种是 200。** 这一条是「量不到 ≠ 没有失败」在协议上的样子 ——
一个 200 + `[]` 的响应，在屏幕上与「这个站很健康」长得一模一样。

⚠️ 这一份**不打真网络**（`fmr=` 注入桩客户端）、**不开浏览器、不打真模型**。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import urllib.request

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import FAKE_TOKEN, MEASURED_ROWS, MEASURED_STEPS, Recorder, envelope  # noqa: E402
from test_service_input import URL, StubWindow  # noqa: E402

SITE_KEY = "www.gowizard.com/auto-warranty/"
#: 那个站的短名（`POST /run` 的 `site` 一格给的形状）—— 与 FMR 的 key **不是一回事**。
SHORT_SITE = "gowizard"


# ── 桩的那一层 ────────────────────────────────────────────────────────────


class _FakeResp:
    """`urllib.request.urlopen` 那个桩的返回值：只要 `.read()`（真 opener 只用它）。"""

    def __init__(self, body: str):
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(*, fmr_client, raise_server_exceptions=False):
    """一个 app：**桩掉 FMR 那一层**（不打真网络），别的都按既有兜底来。"""
    return TestClient(service.create_app(failures_reader=fmr_client, window=StubWindow()),
                      raise_server_exceptions=raise_server_exceptions)


def _failures(client, **params):
    return client.get("/failures", params=params)


def _no_code(payload) -> None:
    """**端出去的那份正文里，一个码都不许有**（这是「人话」的可判形状）。"""
    body = json.dumps(payload, ensure_ascii=False)
    for code in ("site_specific", "created_at", "formLog", "formStep", "no_success"):
        assert code not in body, "码漏进给运营看的那份正文里了（%s）：\n%s" % (code, body)
    #: `failed` / `US` 这种**短词**单独出现才算漏（在别的词里会有假阳性，所以按词边界查）
    import re
    for code in ("failed", "US"):
        assert not re.search(r'(^|[\s":,·])%s([\s":,·]|$)' % code, body), \
            "码漏进给运营看的那份正文里了（%s）：\n%s" % (code, body)


# ── ① 失败列表 ────────────────────────────────────────────────────────────


def test_the_failure_list_comes_back_as_human_rows():
    """**每一行是人话**（`9-19 10:21 失败 · 美国 · 站点专属`），外加一个单号给页面拿去办事。"""
    rec = Recorder(envelope(MEASURED_ROWS))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
                  site=SITE_KEY)
    assert r.status_code == 200, r.text
    got = r.json()
    assert [row["task_id"] for row in got["failures"]] == ["26034602", "26033398", "26033353"]
    assert got["failures"][0]["say"] == "9-19 10:21 失败 · 美国 · 站点专属", got["failures"][0]
    _no_code(got)


def test_the_answer_says_what_window_it_measured():
    """「量到的东西要说清量的是什么」—— 那句人话里要有**时间窗**与**条数**。

    不说窗口的那种坏：运营以为查的是「所有失败」，实际只是今天那一段。
    """
    rec = Recorder(envelope(MEASURED_ROWS))
    got = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
                    site=SITE_KEY, since="2026-09-19").json()
    assert "9-19" in got["say"] and "3" in got["say"], got["say"]
    assert got["since"].startswith("2026-09-19"), got["since"]


def test_an_empty_list_is_said_as_measured_not_as_a_failure_to_measure():
    """**真量了、真没有** → 200 + 空列表，那句 `say` 说清「这是量出来的」。

    这一条是正控的**正面**：它必须与下面那三种「量不到」**长得不一样**（一个 200、一个不是）。
    """
    rec = Recorder(envelope([]))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), site=SITE_KEY)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["failures"] == []
    assert "没有" in got["say"], got["say"]
    assert "量" in got["say"], got["say"]
    assert fmr.UNMEASURED_SAY not in got["say"], "把「量不到」那句话用在了「量到了」上：%r" % got["say"]


def test_a_full_page_says_so_instead_of_truncating_in_silence():
    """摆满了上限 → **说**「更早的可能还有」（悄悄截断 = 一条静默的路径）。"""
    rec = Recorder(envelope(MEASURED_ROWS))
    got = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
                    site=SITE_KEY, limit=3).json()
    assert got["limit"] == 3, got
    assert "3" in got["say"] and ("更多" in got["say"] or "更早" in got["say"]), got["say"]

    # 正控：没摆满就**不许**说那句（否则这一条只是在量「有一句话」）
    rec2 = Recorder(envelope(MEASURED_ROWS))
    got2 = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec2)),
                     site=SITE_KEY, limit=20).json()
    assert "更早" not in got2["say"] and "更多" not in got2["say"], got2["say"]


# ── ② 量不到：三种，各有各的码，**都不是 200** ─────────────────────────────


def test_no_token_is_a_503_with_human_words_not_an_empty_list():
    """没配 token → **503**（这个部署干不了这件事），人话点名那个环境变量。"""
    r = _failures(_client(fmr_client=fmr.FmrClient(token="", opener=Recorder(envelope([])))),
                  site=SITE_KEY)
    assert r.status_code == 503, r.text
    said = r.json()["detail"]
    assert fmr.TOKEN_ENV in said, said
    assert fmr.UNMEASURED_SAY in said, said
    # ★ 那一份正文里**不许有** `failures` 那一格：有它就是在说「列表是空的」
    assert "failures" not in r.text, r.text


def test_a_network_failure_is_a_502_with_human_words_not_an_empty_list():
    """连不上 → **502** + 「量不到」；正文里**没有** `failures` 那一格。"""
    rec = Recorder(RuntimeError("connection refused"))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), site=SITE_KEY)
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert fmr.UNMEASURED_SAY in said, said
    assert "connection refused" in said, "真因被吞了：%r" % said
    assert "failures" not in r.text, r.text


@pytest.mark.parametrize("code", [400, 401, 404])
def test_a_backend_code_is_a_502_not_an_empty_list(code):
    """后端自己回的码（**「后端说的」**）→ 502 + 它的 `msg`；而不是 200 + `[]`。"""
    rec = Recorder(envelope(None, status=code, msg="backend said no"))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), site=SITE_KEY)
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert str(code) in said and "backend said no" in said, said
    assert fmr.UNMEASURED_SAY in said, said
    assert "failures" not in r.text, r.text


def test_the_site_key_that_does_not_exist_says_so_in_the_error():
    """404 → 那句人话要**指到那串 key 上**（不然读的人只会去查网络）。"""
    rec = Recorder(envelope(None, status=404, msg="not found"))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
                  site="stockmarketjunkie.com")
    assert r.status_code == 502, r.text
    said = r.json()["detail"]
    assert "stockmarketjunkie.com" in said, said
    assert "不认识" in said or "名字" in said, said


# ── ③ 免费的那道闸 + 窗口透传 ──────────────────────────────────────────────


def test_a_missing_site_is_refused_for_free():
    """没说哪个站 → **400**，而且**一个请求都没发出去**（免费的那道闸）。"""
    rec = Recorder(envelope(MEASURED_ROWS))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)))
    assert r.status_code == 400, r.text
    assert "站" in r.json()["detail"], r.json()["detail"]
    assert rec.urls == [], "拦下来了却还是发出去了：%r" % rec.urls


def test_the_window_and_the_limit_travel_through_the_route():
    """`since` / `limit` 一路透传到那个请求上（不多不少）。"""
    rec = Recorder(envelope(MEASURED_ROWS))
    _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
              site=SITE_KEY, since="2026-09-18 00:00:00", limit=7)
    from urllib.parse import parse_qs, urlparse
    params = {k: v[0] for k, v in parse_qs(urlparse(rec.urls[0]).query).items()}
    assert params["since"] == "2026-09-18 00:00:00", params
    assert params["limit"] == "7", params


def test_a_window_this_layer_cannot_read_is_refused_at_the_door():
    """读不出来的 `since` → **400**（不是 200 + 一份**别的窗口**的列表）。

    悄悄改用默认窗口的那种坏：调用方以为查的是昨天，看到的是今天那份，屏幕上什么都不说。
    """
    rec = Recorder(envelope(MEASURED_ROWS))
    r = _failures(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
                  site=SITE_KEY, since="前天")
    assert r.status_code == 400, r.text
    assert "前天" in r.json()["detail"], r.json()["detail"]
    assert rec.urls == [], "读不出来还发出去了：%r" % rec.urls


# ── ④ 那一段人话（`evidence`）──────────────────────────────────────────────


def test_the_evidence_route_gives_the_three_things_the_page_needs():
    """`GET /failures/{单号}/evidence` → 单号的人话 + **该填进「站点网址」的那一串** + evidence。

    ⚠️ 页面拿这三样去填「开一趟」那张表（Task 12 的那张）—— 它**自己不拼** evidence。
    """
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26033398/evidence", params={"site": SITE_KEY})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["task_id"] == "26033398"
    assert got["row_say"] == "9-19 09:58 失败 · 美国 · 站点专属", got["row_say"]
    assert got["url"] == MEASURED_STEPS[0]["url"], got["url"]
    assert "26033398" in got["evidence"], got["evidence"]
    assert "一个状态都没进去" in got["evidence"], got["evidence"]
    #: ⚠️ 「码不上屏」这把尺子在这条路上的**射程要说清**（2026-09-20 追加实测之后收窄的）：
    #: `evidence` 那一格**故意**点名 `step` 那一列的值 ——「只报了「start」就直接
    #: 「no_success」」正是诊断要的那句话。所以那把尺子扫的是**另外三格**。
    _no_code({k: v for k, v in got.items() if k != "evidence"})
    #: 而 evidence 那一格换了一条**更严**的：**只有** `step` 那一列的值许出现 ——
    #: 分类码（`site_specific`）、原始 JSON、国家码一个都不许。
    for junk in ("site_specific", '"task_id"', '{"', " US", "created_at", "formLog"):
        assert junk not in got["evidence"], "码漏进证据里了（%s）：%s" % (junk, got["evidence"])


def test_the_evidence_route_hands_back_the_backends_own_site_key():
    """★ **后端自己那个站键**跟着那一段证据一起端出去（第 ① 件事的接口那一半）。

    病：`_stage_fix_source` 拿的是「站点网址」那一格（人填的入口网址），
    而后端的匹配规则是「**存的 site 必须是请求值的 host+path 前缀**」——
    入口网址常常比存的那个键**浅**（`compareinsulation.io` vs
    `compareinsulation.io/article-1-c`），于是明明有这个站、也回了 404。

    药：这一格给的是**失败记录里那个键**（后端自己存的那个）。

    ⚠️ 两个值**故意差一个斜杠**（行里 `…/auto-warranty`、问的 `…/auto-warranty/`）：
    写得一样的话，「服务把调用方问的那个键回传了一遍」这种改法照绿。
    """
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26033398/evidence", params={"site": SITE_KEY})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["site"] == "www.gowizard.com/auto-warranty", got["site"]
    assert got["fill_site"] is True, got
    _no_code({k: v for k, v in got.items() if k != "evidence"})


def test_a_row_without_a_site_key_gets_none_invented():
    """行里**没有**那一格 ⇒ 交出去空串 + `fill_site: False`（页面据此不塞）。

    ⚠️ 这一条治的是「服务顺手拿**问的那个键**顶替」—— 顶替出来的东西**看起来一模一样**
    （都是个站名），可它不是后端说的那一格。与 `url`/`fill_url` 同一把尺子：
    页面那格填不填，是**服务说**的，不是页面自己推的。
    """
    rows = [{k: v for k, v in MEASURED_ROWS[0].items() if k != "site"}]
    rec = Recorder(envelope(rows), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26034602/evidence", params={"site": SITE_KEY})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["site"] == "", got["site"]
    assert got["fill_site"] is False, got


def test_the_evidence_is_exactly_what_the_backend_said_and_nothing_from_the_caller():
    """★ **调用方一个字都塞不进去**：端出去的那段证据与服务**算出来的那一段逐字相同**。

    为什么值得单独一条：这条路上真正的诱惑是「页面把那行数据回传上来，服务照着拼」、
    或者「服务顺手补一句别的」—— 那样 evidence 里就会出现**不是后端说的**字，
    而那段证据是按「**证据**」用的（人和模型拿它判断哪儿坏了）。
    所以判据写成**逐字相等**（不是「某些串不在里面」）—— 少一个字、多一个字都算改过。
    另外乱加的查询参数也不许冒出来。
    """
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26033398/evidence",
        params={"site": SITE_KEY, "since": "2026-09-19", "type": "car_insurance",
                "country": "ZZ", "say": "INJECTED"})
    assert r.status_code == 200, r.text
    said = r.json()["evidence"]
    assert said == fmr.evidence_text(MEASURED_ROWS[1], MEASURED_STEPS), \
        "端出去的那段证据不是「行 + 步」算出来的那一段：\n%r" % said
    for injected in ("INJECTED", "car_insurance", "ZZ", "2026-09-19"):
        assert injected not in said, "调用方塞进来的字进了证据：%r（%s）" % (injected, said)


def test_the_evidence_route_needs_a_site_too():
    """没给 site → 400，且一个请求都没发（与列表那条同一道免费的闸）。"""
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26033398/evidence")
    assert r.status_code == 400, r.text
    assert rec.urls == [], rec.urls


def test_the_evidence_route_is_502_when_it_cannot_measure():
    """量不到 → 502 + 人话（与前两条同一条纪律）。"""
    rec = Recorder(RuntimeError("boom"))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/26033398/evidence", params={"site": SITE_KEY})
    assert r.status_code == 502, r.text
    assert fmr.UNMEASURED_SAY in r.json()["detail"], r.json()["detail"]
    assert "evidence" not in r.text, r.text


def test_a_task_that_is_not_in_the_window_is_said_out_loud():
    """单号不在那个窗口里 → **非 200 + 人话**（不拿别的单顶替）。"""
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get(
        "/failures/99999999/evidence", params={"site": SITE_KEY})
    assert r.status_code == 502, r.text
    assert "99999999" in r.json()["detail"], r.json()["detail"]


# ── ⑤ 接线那两件事：token 什么时候读、`/health` 报什么 ─────────────────────


def test_the_token_is_read_at_construction_and_the_environment_cannot_change_it_later():
    """★ 构造时读一次环境，**之后环境再变也不影响这个服务** —— 量的是**发出去的那一串**。

    与 `_cdp_bin` / `_shots_dir` / `_selftest_root` 同一条不变量（那三处各栽过一次）：
    名字说 A、量的是 B。这里 A = 「服务现在用的 token」、B = 「环境里现在写着什么」。

    ⚠️ 量的是**行为**（请求头上那一串），不是 `svc._fmr.token` 那个属性 ——
    属性对了而请求时另读一次环境，正是这条不变量要挡的那个写法
    （属性断言看不出「用的时候又读了一眼」）。
    `_default_get` 那条真 opener 走 `urllib.request.urlopen`，这里把它换成一个记账的桩
    （**不打真网络**：它只把请求头记下来，回一份空信封）。
    """
    seen = []

    def spy(req, timeout=None):
        seen.append(dict(getattr(req, "headers", {}) or {}))
        return _FakeResp(json.dumps(envelope([])))

    monkey = pytest.MonkeyPatch()
    monkey.setattr(urllib.request, "urlopen", spy)
    monkey.setenv(fmr.TOKEN_ENV, "first")
    try:
        client = TestClient(service.create_app(window=StubWindow()),   # 不给 `failures_reader`：
                            raise_server_exceptions=False)              # 走**默认那条**（读环境）
        assert client.get("/failures", params={"site": SITE_KEY}).status_code == 200
        monkey.setenv(fmr.TOKEN_ENV, "second")
        assert client.get("/failures", params={"site": SITE_KEY}).status_code == 200
    finally:
        monkey.undo()
    #: ⚠️ 头的名字按**大小写不敏感**读：`urllib` 会把它整理成 `X-api-token`
    #: （`Request` 的 `headers` 是个不区分大小写的字典）—— 按原样读会读到 `None`，
    #: 那样这条断言**永远红**，量的是量具不是服务。
    got = [h.get("X-Api-Token") or h.get("X-api-token") for h in seen]
    assert got == ["first", "first"], \
        "构造之后又去看了一眼环境（第二次发出去的换了一个身份）：%r" % seen


def test_health_says_whether_the_failures_reader_is_configured_and_never_leaks_the_token():
    """`/health` 报**这个服务真正会用的那个客户端**配没配 —— 但**不报 token 本身**。"""
    rec = Recorder(envelope([]))
    client = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))
    got = client.get("/health").json()
    assert FAKE_TOKEN not in json.dumps(got, ensure_ascii=False), "token 进了 /health：%r" % got
    #: 报的是**人话**（运维在那儿读的是它）
    assert "failures" in got and isinstance(got["failures"], dict), got
    assert "失败" in got["failures"]["say"], got["failures"]

    bare = _client(fmr_client=fmr.FmrClient(token="", opener=rec)).get("/health").json()
    assert fmr.TOKEN_ENV in bare["failures"]["say"], bare["failures"]


def test_the_service_builds_a_reader_from_the_environment_by_default():
    """不给 `fmr=` 时自己按环境拼一个（**默认那条路**也要能被量到）。"""
    monkey = pytest.MonkeyPatch()
    monkey.setenv(fmr.TOKEN_ENV, "from-env")
    try:
        svc = service.Service()
        assert svc._fmr.configured is True
        assert svc._fmr.token == "from-env"
        assert svc._fmr.base == fmr.DEFAULT_BASE
    finally:
        monkey.undo()
