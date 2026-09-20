"""Task 4 ③④：**榜单（哪个站坏了）→ 原因（为什么）** —— 服务那一半。

## 这一份钉的是那条链的**两头**，和它们各自的**空**

这一片从头到尾治的是同一个形状（本仓最贵的一次实测：无效 key 回 404，
诊断页把它显示成「近 2 天没有失败 ✓」—— **一个查不到的站被标成健康的**）。
Task 4 往这条链上加了两个新读口，于是**多出来四个「空」**，每一个都得分开：

| 口 | 「真量了、真没有」 | 「量不到」 |
|---|---|---|
| `/rank` | `200` + `failed_total:0`（那句 `say` 说清是量到的） | **非 2xx**（503 没配 token / 502 连不上 / 502 后端回码） |
| `/diag/{单号}` | `200` + `diag: []` + **一句 `note`**（「这一单还没有原因行」） | **非 2xx**（同上） |

⚠️ `/diag` 那个「空」与别处**不一样**：它不是「这个站没有失败」那一族的误会，
而是这一屏**现在没有这一单的原因**（成因：新功能 / 老单 / 那台机器没发上来 ——
**三种在数据上长得一样，分不出**）。所以它**不是错误**，但**也不许留白**。

## 为什么这一份是个独立的文件

Task 13 那份（`test_service_failures.py`）钉的是「这个站失败了哪几趟」；
这一份钉的是它**上面**（哪个站坏了）与**下面**（为什么坏）那两跳。
两跳的路由、载荷、空语义都不一样，混在一个文件里会让「哪一条钉的是哪一跳」看不出来。
（复用的只有那些**桩**：`Recorder` / `envelope` / `StubWindow` —— 从原处 import，不抄一遍。）

⚠️ 这一份**不打真网络、不开浏览器、不打真模型**（`failures_reader` 是注入的口子）。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import graph  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import (  # noqa: E402
    FAKE_TOKEN, MEASURED_DIAG, RANK_FIXTURE, RANK_KEY_CLEAN, RANK_KEY_LONG,
    RANK_KEY_NO_CONFIG, Recorder, envelope,
)
from test_service_input import StubWindow  # noqa: E402

#: 那个站（FMR 那边榜单上的键 —— 与 `POST /run` 的 `site` **不是一回事**）。
SITE_KEY = "www.gowizard.com/auto-warranty/"
#: 那一单（就是 ④ 那条链要走的键）。
TASK_ID = "26033398"


def _client(*, fmr_client, raise_server_exceptions=False):
    """一个 app：**桩掉 FMR 那一层**（不打真网络），别的都按既有兜底来。"""
    return TestClient(service.create_app(failures_reader=fmr_client, window=StubWindow()),
                      raise_server_exceptions=raise_server_exceptions)


def _rank(client, **params):
    return client.get(service.RANK_PATH, params=params)


def _diag(client, task_id):
    return client.get(service.DIAG_PATH % task_id)


def _no_code(payload) -> None:
    """**端出去的那份正文里，一个码都不许有**（与 `test_service_failures._no_code` 同一条纪律）。

    ⚠️ 这一份的清单**多了两样**，是 Task 4 新引进来的面：
      · `created_at` —— 时刻那一格在服务这层就换成「失败于 9-20 14:32」那种人话了；
      · `stuck` / `unknown` —— `exit` 那一格同理（`unknown` 尤其要紧：它要显示成
        「没报上来」，**码本身不许上屏**）。
    """
    body = json.dumps(payload, ensure_ascii=False)
    for code in ("site_specific", "created_at", "formLog", "formLogRank", "failDiag",
                 "formStep", "no_success", "config_id", "has_script",
                 '"exit"', "unknown"):
        assert code not in body, "码漏进给运营看的那份正文里了（%s）：\n%s" % (code, body)


# ── ③ 榜单 ────────────────────────────────────────────────────────────────


def test_the_rank_comes_back_as_human_rows():
    """每一行是**人话** + 一个站点键（页面拿它去查失败单 —— 那是 `formLog` 认的那个键）。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    r = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)))
    assert r.status_code == 200, r.text
    got = r.json()
    assert [row["site"] for row in got["rank"]] == [r["site"] for r in RANK_FIXTURE["rank"]], \
        got["rank"]
    assert got["rank"][0]["say"] == "失败 3 次 · 配置 66（启用） · 有 py 脚本", got["rank"][0]
    _no_code(got)


def test_the_rank_answer_carries_the_three_numbers_the_page_needs():
    """★ 「摆了 4 个站」与「今天失败 18 次」**可以同时为真** —— 那一句里三个数都得在。

    ⚠️ 判据比的是**带着重号**的那个数（`**18**`），不是裸的 `"18"`/`"2"` ——
    裸的那种会被同一句里的日期 `2026-09-20` 保证为真（复审 F8 实测：两条这样的断言**空转**）。
    差额那一支在 `test_fmr.py::test_the_rank_says_when_it_did_not_show_everything`。
    """
    rec = Recorder(envelope(RANK_FIXTURE))
    got = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))).json()
    assert "2026-09-20" in got["say"], got["say"]
    for token in ("**18**", "**8**"):
        assert token in got["say"], "%s 不在那句里：%r" % (token, got["say"])
    assert "下面摆了 4 个站" in got["say"], got["say"]
    assert got["date"] == "2026-09-20", got
    assert got["limit"] == fmr.DEFAULT_RANK_LIMIT, got


def test_the_rank_row_carries_the_count_the_page_reconciles_with():
    """★★ 复审 F2：★ **`fail_count` 必须真的端出去** —— 它是页面那条对账的**唯一输入**。

    上一版这里只留 `site`/`say`，于是页面的 `at(row, "fail_count", null)` **恒为 null** ⇒
    那条对账**永不触发**（复审量：0 条用例、变异体全绿、真数据 8/8 走不到）
    —— **一条永远不触发的对账比没有对账更坏**（它看起来像有守）。

    ⚠️ 这一条钉的是**服务这一侧**（页面那一侧是
    `test_console_js.py::test_the_reconciliation_speaks_in_both_directions`）——
    两半都得有，缺一半那条链就是断的（上一版缺的正是这一半）。
    """
    rec = Recorder(envelope(RANK_FIXTURE))
    got = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))).json()
    want = {r["site"]: r["fail"] for r in RANK_FIXTURE["rank"]}
    assert [(r["site"], r["fail_count"]) for r in got["rank"]] == list(want.items()), got["rank"]
    #: ⚠️ 后端的 `fail` 读不出来时给 `None` —— **不给 0**（0 是合法读数，
    #: 拿它顶替「没给」就是把「对不了账」写成「对上了」）。
    hurt = dict(RANK_FIXTURE)
    hurt["rank"] = [dict(RANK_FIXTURE["rank"][0], fail="不知道")]
    rec2 = Recorder(envelope(hurt))
    got2 = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec2))).json()
    assert got2["rank"][0]["fail_count"] is None, got2["rank"][0]
    #: 而那一行的人话也要**照实说它没给**（不是画一个 0）。
    assert "它没给" in got2["rank"][0]["say"], got2["rank"][0]


def test_a_day_with_no_failures_is_a_200_that_says_it_was_measured():
    """**真量了、真没有** → `200` + 那句说清「这是量到的」（**不是**空屏、也不是错误）。"""
    day = {"date": "2026-09-20", "failed_total": 0, "unattributed": 0, "rank": []}
    rec = Recorder(envelope(day))
    r = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["rank"] == [], got
    #: ⚠️ 比的是**带着重号**的那个 0（复审 F8：裸的 `"0" in say` 会被同一句里的
    #: 日期 `2026-09-20` 保证为真 —— 那条断言是空转的）。
    assert "量到" in got["say"], got["say"]
    assert "**0**" in got["say"], got["say"]


def test_a_rank_that_cannot_be_measured_is_not_a_200_with_an_empty_list():
    """★ 三种「量不到」都是**非 2xx**（与 `/failures` 同一条路、同一个判据）。

    正控在上一段：**同一个空列表**在真量过的那一次是 `200` ——
    两条一起看，才说得上「这一条量的是码，不是空不空」。
    """
    no_token = _rank(_client(fmr_client=fmr.FmrClient(token="")))
    assert no_token.status_code == service.FAILURE_STATUS_NO_TOKEN, no_token.text
    #: 那句话要点到**那个环境变量名**上（运维照着它去配，而不是去查网络）。
    assert fmr.TOKEN_ENV in no_token.json()["detail"], no_token.json()
    assert "rank" not in no_token.json(), "「量不到」居然给了一份榜单：%r" % no_token.json()
    _no_code(no_token.json())

    def boom(url, headers):
        raise RuntimeError("connection refused")

    offline = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=boom)))
    assert offline.status_code == service.FAILURE_STATUS_UNMEASURED, offline.text
    assert fmr.UNMEASURED_SAY in offline.json()["detail"], offline.json()


@pytest.mark.parametrize("code", [400, 401, 404])
def test_a_backend_code_on_the_rank_is_a_502_not_an_empty_day(code):
    """后端自己在 body 里回码（这一族的正常形状）—— 一样进 502，**不是**「今天没有站在失败」。"""
    rec = Recorder(envelope(None, status=code, msg="backend said no"))
    r = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)))
    assert r.status_code == service.FAILURE_STATUS_UNMEASURED, r.text
    assert "backend said no" in r.json()["detail"], r.json()


def test_a_rank_answer_of_the_wrong_shape_is_a_502_not_an_empty_day():
    """★ 后端回了 `200` 可 `data` 是个**空数组** ⇒ 那是**形状事故**，不是「今天没有站在失败」。

    正控就是上面那条 `failed_total:0` 的：**同一个键**（都是空），
    形状对的那一次是 `200`、形状不对的这一次是 `502` ——
    这正是 `fetch_rank(shape=dict)` 那条闸要的东西。
    """
    rec = Recorder(envelope([]))
    r = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)))
    assert r.status_code == service.FAILURE_STATUS_UNMEASURED, r.text
    assert fmr.UNMEASURED_SAY in r.json()["detail"], r.json()


def test_a_limit_that_is_not_a_number_is_refused_at_the_door():
    """那一格是**外面来的字** ⇒ 400，而且**一个请求都没发出去**（与 `/failures` 同一条闸）。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    r = _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), limit="abc")
    assert r.status_code == 400, r.text
    assert rec.urls == [], "读不出来的 limit 居然发了请求：%r" % (rec.urls,)


def test_the_day_and_the_limit_travel_through_the_route():
    """那两格**原样透传**（服务不替调用方挑日子、也不改条数）。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    _rank(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)),
          date="2026-09-19", limit="7")
    from urllib.parse import parse_qs, urlparse
    params = {k: v[0] for k, v in parse_qs(urlparse(rec.urls[0]).query).items()}
    assert params == {"date": "2026-09-19", "limit": "7"}, params


# ── ④ 原因 ────────────────────────────────────────────────────────────────


def test_the_diag_comes_back_as_a_head_and_the_raw_lines():
    """一条原因 = **一句抬头（人话）** + **那几行日志（原样）**。

    ⚠️ 断言写成**整行相等**（不是「那句话在里面」）：只比 `in` 的话，
    「把整条记录 `JSON.stringify` 出来、人话也在里面」那种改法照绿。
    """
    rec = Recorder(envelope(MEASURED_DIAG))
    r = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["task_id"] == TASK_ID, got
    assert got["diag"][0]["say"] == "失败于 9-20 14:32 · 机器 worker-07 · 结束方式：卡住", got
    #: ★ 那几行**原样**（一个字节都不加工）—— 它就是这条链要给人看的东西。
    assert got["diag"][0]["lines"] == MEASURED_DIAG[0]["lines"], got
    assert got["note"] == "", "有原因的时候不该有话要说：%r" % got
    _no_code(got)


def test_the_diag_asks_by_task_id_only_never_by_the_site_key():
    """★ brief §2 R1 的可判形状：那一跳**只发单号**，这一层也**没有**站点键这个入口。

    拿站点键去查原因会**静默地**查到 0 行（榜单那个键实测脏过）——
    所以路由上根本没有 `site` 这一格，想拼也拼不出来。
    """
    rec = Recorder(envelope(MEASURED_DIAG))
    _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID)
    from urllib.parse import parse_qs, urlparse
    path, query = urlparse(rec.urls[0]).path, parse_qs(urlparse(rec.urls[0]).query)
    assert path == "/api/quest/failDiag", rec.urls[0]
    assert {k: v[0] for k, v in query.items()} == {"task_id": TASK_ID}, query
    #: 路由的形状也钉一下：`/diag/{单号}` **没有**第二格（`site` 拼不进去）。
    assert service.DIAG_PATH == "/diag/%s", service.DIAG_PATH


def test_a_task_with_no_reason_yet_is_said_out_loud_not_left_blank():
    """★★ 这一份的**头号判据**（brief §2 R2 / §6.3）：**没有静默的路径**。

    单号查得到、可**还没有原因行**是很常见的（新功能 / 老单 / 那台机器没发上来）。
    这一条要求：`200`（**不是错误**）+ 空的 `diag` + **一句 `note`**（**不是留白**）。
    """
    rec = Recorder(envelope([]))
    r = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["diag"] == [], got
    assert got["note"] == fmr.NO_DIAG_SAY, got
    assert "还没有原因行" in got["note"], got
    assert "量到" in got["note"], "没把「量不到」与「还没有」分开：%r" % got["note"]
    _no_code(got)


def test_the_note_is_empty_when_there_is_a_reason():
    """正控：`note` **不是**一句天天摆着的开场白 —— 有原因时它是空的。

    没有这一条的话，「`note` 永远写着那句话」也能让上一条绿 ——
    而那种实现会让**有原因**的那些单也看到一句「还没有原因」。
    """
    rec = Recorder(envelope(MEASURED_DIAG))
    got = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID).json()
    assert got["note"] == "", got


def test_an_exit_nobody_reported_is_said_as_not_reported_on_the_screen():
    """★ brief §2 R3：`exit=unknown` 显示成**「没报上来」**，**不许**编成那三种之一。

    这条同时量两下：那句话上屏；以及**码本身**（`unknown`）没上屏。
    """
    row = dict(MEASURED_DIAG[0], exit="unknown")
    rec = Recorder(envelope([row]))
    got = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID).json()
    assert "没报上来" in got["diag"][0]["say"], got
    _no_code(got)


@pytest.mark.parametrize("bad,word", [("stuck", "卡住"), ("stalled", "原地打转"),
                                      ("no_success", "没成功")])
def test_the_three_real_exits_each_have_their_own_words(bad, word):
    """正控（上面那条的另一半）：三个**真报上来的**值各有各的说法，都不等于「没报上来」。"""
    rec = Recorder(envelope([dict(MEASURED_DIAG[0], exit=bad)]))
    got = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID).json()
    assert word in got["diag"][0]["say"], got
    assert "没报上来" not in got["diag"][0]["say"], got


def test_a_diag_that_cannot_be_measured_is_not_a_200_with_no_reason():
    """★ 「这一单还没有原因」与「这一栏没读到」**必须分得开**（这一屏最怕的形状）。

    后者是**非 2xx**：读的人看到「还没有原因」会以为那台机器没发上来，
    而真因可能是没配 token / 连不上。
    """
    def boom(url, headers):
        raise RuntimeError("connection refused")

    offline = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=boom)), TASK_ID)
    assert offline.status_code == service.FAILURE_STATUS_UNMEASURED, offline.text
    assert fmr.UNMEASURED_SAY in offline.json()["detail"], offline.json()
    assert "diag" not in offline.json(), "「量不到」居然给了一份原因：%r" % offline.json()
    _no_code(offline.json())

    no_token = _diag(_client(fmr_client=fmr.FmrClient(token="")), TASK_ID)
    assert no_token.status_code == service.FAILURE_STATUS_NO_TOKEN, no_token.text


def test_a_missing_task_id_is_refused_for_free_before_anything_is_sent():
    """缺单号 ⇒ 400，而且**一个请求都没发出去**（与 `/failures` 缺 site 同一档）。"""
    rec = Recorder(envelope(MEASURED_DIAG))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get("/diag/%20")
    assert r.status_code == 400, r.text
    assert rec.urls == [], "缺单号居然发了请求：%r" % (rec.urls,)


def test_a_full_page_of_reasons_says_it_may_not_be_the_end():
    """★ 摆满了**要说**（那个接口回的是**裸数组、不带总数**）。

    不说的话，100 这个数看着就像「一共就这些」—— 而这一层**分不出**是不是到底了，
    那就照实说分不出。⚠️ 别把它写成「还有更早的」：这一层**不知道**有没有。
    """
    rows = [dict(MEASURED_DIAG[0], id=i) for i in range(fmr.DIAG_PAGE_SIZE)]
    rec = Recorder(envelope(rows))
    got = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID).json()
    assert "摆满了" in got["say"], got["say"]
    assert "分不出" in got["say"], got["say"]
    assert len(got["diag"]) == fmr.DIAG_PAGE_SIZE, len(got["diag"])


def test_a_short_page_does_not_claim_anything_about_more():
    """正控：**没摆满**时那一句不许出现（否则它就成了一句天天摆着的废话）。"""
    rec = Recorder(envelope(MEASURED_DIAG))
    got = _diag(_client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)), TASK_ID).json()
    assert "摆满了" not in got["say"], got["say"]


# ── 那条链的两头都在 `/health` 上看得见 ────────────────────────────────────


def test_health_reports_the_one_reader_that_serves_all_four_reads():
    """`/health` 那一格说的是**这个进程真正会用的那个客户端**配没配 token ——
    四个读口共用它一个（brief §4：**一处旋钮**，没有「按接口各配一个」）。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    r = _client(fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec)).get("/health")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["failures"]["configured"] is True, got["failures"]
    assert "/api/quest/failDiag" not in json.dumps(got), "接口路径不该出现在这一格里"
    assert FAKE_TOKEN not in json.dumps(got), "token 本身进了 /health"


def test_the_new_routes_are_the_ones_the_page_is_told_about():
    """★ **服务给地址，页面不自己拼**（与 `FAILURES_PATH` 同一个做法）：

    两份手拼的地址迟早漂，而漂了的那一份在页面上看着像「后端没数据」。
    """
    assert service.RANK_PATH == "/rank", service.RANK_PATH
    assert service.DIAG_PATH % "26034602" == "/diag/26034602", service.DIAG_PATH
    page = _client(fmr_client=fmr.FmrClient(token="")).get("/console").text
    assert '"' + service.RANK_PATH + '"' in page, "页面里没有榜单那一跳的地址"
    assert '"' + service.DIAG_PATH.split("%s")[0] + '"' in page, "页面里没有原因那一跳的地址"
    assert service.STEER_WIRED is False, "这一版写死 False（插话通道还没上线）"
