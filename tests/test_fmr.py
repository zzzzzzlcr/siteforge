"""Task 13 ①「读」：**从 FMR 取失败记录** —— 那个客户端。

## 这一份钉的是一条纪律，不是几个函数

这个仓库反复栽在同一个形状上：**把「量不出来」读成「量出来了」**
（最贵的一次是 `fail-script`：`stockmarketjunkie.com` 是个无效 key，接口回 404，
诊断页把它显示成「近 2 天没有失败 ✓」—— 一个**查不到的站被标成健康的**）。

所以这一份的重心**不是**「能不能取到数」，而是**取不到的时候说了什么**：

| 情形 | 后端那边是什么 | 这一层必须给什么 |
|---|---|---|
| 真量了、真没有失败 | `{"status":200,"data":[]}` | 空列表 + 一句「这段时间里没有失败记录」 |
| **没配 token** | 一个请求都没发出去 | **抛**（人话里点名 token）—— 不是 `[]` |
| **连不上 / 超时** | 一个字节都没回来 | **抛** —— 不是 `[]` |
| **正文不是 JSON** | 回来的是别的什么东西 | **抛** —— 不是 `[]` |
| **后端说 401** | `{"status":401,…}` | **抛**（带上后端那句话）—— 不是 `[]` |
| **后端说 404** | `{"status":404,…}` | **抛**，且人话要**指到 key 上**（404 的语义是「这个站它不认识」） |
| **后端说 400** | `{"status":400,…}` | **抛** |

⚠️ 最后那一列全是「抛」：**唯一能变成空列表的，只有第一种**。
这就是「量不到 ≠ 没有失败」在代码里的样子 —— 它是一条**类型**上的区分
（`FmrUnmeasured` 这个异常 vs 一个空 list），不是一句注释。

## 契约（**线上实测过的，原样用**）

```
GET {base}/api/quest/formLog    X-Api-Token: <token>
    ?site=<config 的 site>&status=failed&since=<ISO8601>&limit=<n>
 → {"status":200,"msg":"success","data":[{task_id, site, status, type, country, created_at}]}
   按时间**倒序**
GET {base}/api/quest/formStep   X-Api-Token: <token>&task_id=<id>
 → {"status":200,"msg":"success","data":[{id, task_id, step, url}]}  按 id **升序**
   信封 {"status","msg","data"}；**HTTP 恒 200**，错码只在 body 的 `status` 里
```

⚠️ 这一份**不打真网络**：`opener` 那个口子是注入的（与 `fix.site_schema` 的 `url_get`
同一个做法），夹具给它一个记下「发了什么」的桩。

⚠️ 这一份**不开浏览器、不打真模型**（Global Constraints）。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402

#: **线上实测的那三行**（`formLog?site=www.gowizard.com/auto-warranty/&since=2026-09-19 00:00:00`）。
#: 一个字节都不改 —— 这一份所有「人话」的断言都拿它们当输入。
MEASURED_ROWS = [
    {"task_id": "26034602", "site": "www.gowizard.com/auto-warranty", "status": "failed",
     "type": "site_specific", "country": "US", "created_at": "2026-09-19T10:21:38+08:00"},
    {"task_id": "26033398", "site": "www.gowizard.com/auto-warranty", "status": "failed",
     "type": "site_specific", "country": "US", "created_at": "2026-09-19T09:58:02+08:00"},
    {"task_id": "26033353", "site": "www.gowizard.com/auto-warranty", "status": "failed",
     "type": "site_specific", "country": "US", "created_at": "2026-09-19T09:55:11+08:00"},
]
#: **线上实测的那两行**（`formStep?task_id=26033398`）—— `start` 那条带全量 utm。
MEASURED_STEPS = [
    {"id": 79794, "task_id": 26033398, "step": "start",
     "url": "https://www.gowizard.com/auto-warranty/?utm_source=fb&utm_campaign=x"},
    {"id": 79795, "task_id": 26033398, "step": "no_success",
     "url": "https://www.gowizard.com/auto-warranty/"},
]
#: 那一串 token 的长相（**不是真的** —— 真的只从环境变量来，永远不进这个文件）。
FAKE_TOKEN = "tok-for-fixture-only"

class Recorder:
    """一个记下「发了什么」的桩 `opener`。用完可以问它 `urls` / `headers`。"""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.urls = []
        self.headers = []

    def __call__(self, url, headers):
        self.urls.append(url)
        self.headers.append(dict(headers or {}))
        if not self.bodies:
            raise AssertionError("桩没给这个请求准备响应：%s" % url)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, str) else json.dumps(body)


def envelope(data, status=200, msg="success"):
    return {"status": status, "msg": msg, "data": data}


def client(rec, token=FAKE_TOKEN, **kw):
    return fmr.FmrClient(token=token, opener=rec, **kw)


def query_of(url):
    """把请求 URL 拆成 `(路径, {参数})` —— 断言里好读。"""
    from urllib.parse import parse_qs, urlparse
    parts = urlparse(url)
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


# ─────────────────────── 人话：一行失败长什么样 ───────────────────────


def test_a_failure_row_becomes_one_human_sentence():
    """**给运营看的那一行**：`9-19 10:21 失败 · 美国 · 站点专属`（任务书里写死了这一句）。

    ⚠️ 同时量「码不在里面」：`site_specific` / `failed` / `US` 一律不许出现 ——
    任务书点名的纪律是「**不是** `type: site_specific` 这种码」。
    """
    said = fmr.failure_say(MEASURED_ROWS[0])
    assert said == "9-19 10:21 失败 · 美国 · 站点专属", said
    for code in ("site_specific", "failed", "US", "created_at", "2026-09-19T"):
        assert code not in said, "码漏进人话里了：%r（%s）" % (said, code)


def test_a_code_this_page_does_not_know_is_said_out_loud_not_swallowed():
    """**不认识的代号要如实说**，不许静默吞掉（「没有静默的路径」）。

    吞掉的样子有两种，都不许：把它整段删掉（那行看着像「这个站没有别的问题」），
    或者原样把 `type: xx` 端出去（那正是任务书禁止的）。
    """
    said = fmr.failure_say({"task_id": "1", "status": "failed", "type": "brand_new_thing",
                            "country": "ZZ", "created_at": "2026-09-19T10:21:38+08:00"})
    assert "9-19 10:21 失败" in said, said
    assert "brand_new_thing" in said, "不认识的类型被悄悄吞了：%r" % said
    assert "这一屏" in said, "不认识的类型没说「这一屏不认识」：%r" % said
    assert "ZZ" in said, "不认识的国家被悄悄吞了：%r" % said


def test_a_time_that_cannot_be_read_says_so_and_does_not_invent_one():
    """时刻读不出来 → **明说**，不许编一个（编出来的时刻是最坏的一种：它像证据）。"""
    said = fmr.failure_say({"task_id": "1", "status": "failed", "type": "site_specific",
                            "country": "US", "created_at": "昨天下午"})
    assert "时刻读不出来" in said, said
    assert "昨天下午" in said, "它原样给的那串字要留着（不然读的人不知道是什么读不出来）：%r" % said


def test_a_missing_cell_is_said_out_loud_too():
    """整格不在（不是「不认识」，是**它没给**）—— 也要说，不许留白。

    ⚠️ **一格一格量**（只抽掉 `country`，别的照给）：整行都抽掉的话，
    「时刻它没给」那句会把「没给」这两个字先满足掉 —— 抽掉国家那一格的改法**照样绿**
    （实测：这条断言原先就是那么写的，变异下没红）。
    """
    row = dict(MEASURED_ROWS[0])
    del row["country"]
    said = fmr.failure_say(row)
    assert said == "9-19 10:21 失败 · 国家它没给 · 站点专属", said
    #: 正控：另外两格**都在**的时候不许说「没给」（否则这一条只是在量「有一句话」）
    full = fmr.failure_say(MEASURED_ROWS[0])
    assert "没给" not in full, full


# ─────────────────────── 查失败列表 ───────────────────────


def test_the_query_is_the_one_that_was_measured():
    """请求本身的形状：**路径 + 那几个参数 + 头里那串 token**（线上实测过的那一份）。

    ⚠️ token **不许进 URL**（URL 会进日志、进 `ps`）—— 与 `fail-script` 里那条同源。
    """
    rec = Recorder(envelope(MEASURED_ROWS))
    got = client(rec).fetch_failures("www.gowizard.com/auto-warranty/",
                                     since="2026-09-19 00:00:00", limit=20)
    assert len(got) == 3
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formLog", path
    assert params["site"] == "www.gowizard.com/auto-warranty/", params
    assert params["status"] == "failed", params
    assert params["since"] == "2026-09-19 00:00:00", params
    assert params["limit"] == "20", params
    assert rec.headers[0].get("X-Api-Token") == FAKE_TOKEN, rec.headers[0]
    assert FAKE_TOKEN not in rec.urls[0], "token 进了 URL：%r" % rec.urls[0]


def test_the_rows_come_back_in_the_order_the_backend_gave_them():
    """① 按时间**倒序**返回 —— **不重排**（重排就是「服务自己编了一个顺序」）。"""
    rec = Recorder(envelope(MEASURED_ROWS))
    got = client(rec).fetch_failures("www.gowizard.com/auto-warranty/")
    assert [r["task_id"] for r in got] == ["26034602", "26033398", "26033353"]


def test_the_default_window_starts_at_today_midnight():
    """★ 契约里那个坑：`since` 默认是**今天 00:00** —— 要查昨天必须**显式给**。

    这一条钉的是「默认值就是那个默认值」：不给 `since` 时，发出去的那串字
    是**今天**的 00:00:00（拿同一个 clock 算，不是写死一个日期）。
    """
    rec = Recorder(envelope([]))
    now = datetime.datetime(2026, 9, 20, 15, 4, 5)
    client(rec, now=lambda: now).fetch_failures("www.gowizard.com/auto-warranty/")
    _, params = query_of(rec.urls[0])
    assert params["since"] == "2026-09-20 00:00:00", params


def test_the_window_goes_out_in_the_shape_that_was_measured():
    """`since` 那串字用**量过的那个形状**（空格分隔），不是文档里那个 `<ISO8601>` 的 `T`。

    三种输入都认（`datetime` / `2026-09-19` / 量过的那一串），出去的**永远是同一种形状**。
    """
    now = datetime.datetime(2026, 9, 20, 15, 4, 5)
    forms = [datetime.datetime(2026, 9, 19), "2026-09-19", "2026-09-19 00:00:00"]
    for given in forms:
        rec = Recorder(envelope([]))
        client(rec, now=lambda: now).fetch_failures("s/", since=given)
        _, params = query_of(rec.urls[0])
        assert params["since"] == "2026-09-19 00:00:00", (given, params)
        assert "T" not in params["since"], params


def test_a_since_this_layer_cannot_read_is_refused_not_guessed():
    """给了一串读不出来的「从什么时候起」→ **拒**（不是悄悄改用默认值）。

    悄悄改用默认值的那种坏：调用方以为查的是昨天，看到的是**今天那份**，
    而屏幕上什么都不说 —— 它就是「量错了对象」。
    """
    rec = Recorder(envelope([]))
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec).fetch_failures("s/", since="前天")
    assert "前天" in str(e.value), str(e.value)
    assert rec.urls == [], "读不出来还发出去了：%r" % rec.urls


# ─────────────────── 量不到 ≠ 没有失败（这一份的重心）───────────────────


def test_a_missing_token_is_said_out_loud_and_never_becomes_an_empty_list():
    """没配 token → **抛**，而且人话里点名 token —— 不是「没有失败」。"""
    rec = Recorder(envelope([]))
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec, token="").fetch_failures("www.gowizard.com/auto-warranty/")
    said = str(e.value)
    assert "FMR_AGENT_TOKEN" in said, said
    assert fmr.UNMEASURED_SAY in said, said
    assert rec.urls == [], "没 token 还是发出去了：%r" % rec.urls
    assert not isinstance(e.value, fmr.FmrRefused), "「没配」不该说成「后端拒了」"


def test_a_network_failure_is_never_read_as_no_failures():
    """连不上 / 超时 → **抛**（`FmrUnreachable`）—— 不是 `[]`。

    ⚠️ 这一条正是这一片存在的理由：`[]` 在屏幕上与「这个站很健康」长得一模一样。
    """
    rec = Recorder(RuntimeError("connection refused"))
    with pytest.raises(fmr.FmrUnreachable) as e:
        client(rec).fetch_failures("www.gowizard.com/auto-warranty/")
    assert fmr.UNMEASURED_SAY in str(e.value), str(e.value)
    assert "connection refused" in str(e.value), "真因被吞了：%r" % str(e.value)


def test_a_body_that_is_not_json_is_unmeasured_not_empty():
    """回来的是别的什么东西（网关的 HTML 错误页…）→ **抛**，不是 `[]`。"""
    rec = Recorder("<html>502 Bad Gateway</html>")
    with pytest.raises(fmr.FmrUnreachable) as e:
        client(rec).fetch_failures("s/")
    assert "不是" in str(e.value), str(e.value)


def test_an_http_error_is_unmeasured_and_carries_the_http_code():
    """契约说 HTTP 恒 200（限速除外）—— 那**例外**也要说出来，别让它以别的面目冒出来。

    `fail-script` 的注释记着同一个坑：不读状态码的话，429 会以 `JSONDecodeError`
    的面目出现，看不出是「被限速了」。
    """
    import urllib.error
    import urllib.request

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(urllib.request, "urlopen", boom)
    try:
        rec = fmr.FmrClient(token=FAKE_TOKEN)          # 走**默认**那条 opener
        with pytest.raises(fmr.FmrUnreachable) as e:
            rec.fetch_failures("s/")
    finally:
        monkey.undo()
    assert "429" in str(e.value), str(e.value)
    assert fmr.UNMEASURED_SAY in str(e.value), str(e.value)


@pytest.mark.parametrize("code", [400, 401, 404])
def test_a_backend_code_is_said_out_loud_and_never_becomes_an_empty_list(code):
    """后端自己回的业务码 → **抛**（`FmrRefused`），带上后端那句 `msg`。

    ⚠️ 这是「后端说的」那一类：**它仍然不是「没有失败」**。
    """
    rec = Recorder(envelope(None, status=code, msg="backend said no"))
    with pytest.raises(fmr.FmrRefused) as e:
        client(rec).fetch_failures("www.gowizard.com/auto-warranty/")
    said = str(e.value)
    assert str(code) in said, said
    assert "backend said no" in said, "后端那句话被吞了：%r" % said
    assert fmr.UNMEASURED_SAY in said, said


def test_the_404_sentence_points_at_the_site_key():
    """★ 404 的语义是「**这个站它不认识**」—— 人话要指到 key 上。

    证据（`fail-script/src/diagnosis/datasource.py` 的注释，实测过一次）：
    无效 key 回 404，而当时的诊断页把它显示成「近 2 天没有失败 ✓」——
    一个**查不到的站被标成健康的**。这一条要求那句话在屏幕上就说得清是 key 的事。
    """
    rec = Recorder(envelope(None, status=404, msg="not found"))
    with pytest.raises(fmr.FmrRefused) as e:
        client(rec).fetch_failures("stockmarketjunkie.com")
    said = str(e.value)
    assert "站" in said and ("key" in said or "名字" in said or "对不上" in said), said
    assert "stockmarketjunkie.com" in said, "没把那串 key 摆出来（人要照着改）：%r" % said


def test_an_empty_list_is_the_only_thing_that_can_be_said_as_no_failures():
    """**唯一**能变成空列表的那一种：真量了、真没有（`status:200` + `data:[]`）。

    正控：同样一条路，把 `status` 改成 401 就必须**抛**（否则这一条只是在量
    「空数组是空数组」—— 那种断言永远绿）。
    """
    rec = Recorder(envelope([]))
    assert client(rec).fetch_failures("s/") == []

    rec2 = Recorder(envelope([], status=401, msg="no"))
    with pytest.raises(fmr.FmrUnmeasured):
        client(rec2).fetch_failures("s/")
    with pytest.raises(fmr.FmrUnmeasured):        # 换成 404 也一样
        client(Recorder(envelope([], status=404, msg="no"))).fetch_failures("s/")


# ─────────────────────────── 逐步记录 + evidence ───────────────────────────


def test_the_steps_query_is_the_one_that_was_measured_and_keep_the_backend_order():
    """② 的形状：`task_id` 那个参数 + **按 id 升序返回、不重排**。"""
    rec = Recorder(envelope(MEASURED_STEPS))
    got = client(rec).fetch_steps("26033398")
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formStep", path
    assert params["task_id"] == "26033398", params
    assert rec.headers[0].get("X-Api-Token") == FAKE_TOKEN, rec.headers[0]
    assert [r["step"] for r in got] == ["start", "no_success"]


#: ★ **七趟真单** —— 从生产日志 `logs/gowizard.log` 里逐条抽出来的
#: （`[URL Report] Sending data: {"task_id":…, "step":…, "url":…}` 那几行，
#: 那是**上报到 FMR 去的那一串**，也就是 `formStep` 读回来的那一列）。
#:
#: ⚠️ 其中 **`26033398`** 与协调者 2026-09-20 在**线上**取到的那一趟**逐字相同** ——
#: 这份日志与那个 `step` 列是同一件事的两个读数。所以这一组**不是「我编的样例」**。
#:
#: ⚠️ 更要紧的一条：**行数不决定形状** —— `26015658` 也是 5 行，终点却是 `no_success`
#: （进过 `auto_warranty`/`auto`），而协调者线上那几趟 5 行的是 `stuck` 收尾。
#: **同一个行数、两种完全不同的病。**
REAL_WALKS = {
    "26004759": ["start", "no_success"],
    "26005216": ["start", "auto_warranty", "auto", "auto-2", "no_success"],
    "26005787": ["start", "auto_warranty", "auto", "auto", "success"],
    "26015658": ["start", "auto_warranty", "auto", "auto", "no_success"],
    "26023712": ["start", "auto_warranty", "auto", "auto", "success"],
    "26032770": ["start", "auto_warranty", "auto", "auto", "success"],
    "26033398": ["start", "no_success"],
}
#: 协调者 2026-09-20 在线上取到的那一趟（5 行、`stuck` 收尾）—— 日志那份里**没有**
#: 这个形状（`stuck` 在本地日志里 0 次），所以这一条照他给的原样写。
LIVE_STUCK_ROWS = [
    {"id": 1, "task_id": 26034602, "step": "start",
     "url": "https://www.gowizard.com/auto-warranty/?utm_source=fb"},
    {"id": 2, "task_id": 26034602, "step": "auto_warranty",
     "url": "https://www.gowizard.com/auto-warranty/"},
    {"id": 3, "task_id": 26034602, "step": "auto", "url": "https://www.gowizard.com/auto/"},
    {"id": 4, "task_id": 26034602, "step": "auto", "url": "https://www.gowizard.com/auto/"},
    {"id": 5, "task_id": 26034602, "step": "stuck",
     "url": "https://www.gowizard.com/auto/?text=Sedan&touchpointId=e8bcf7fa"},
]


def walk(task_id, steps, *, stop_url="https://www.gowizard.com/auto/?text=Sedan"):
    """把 `REAL_WALKS` 里那一串名字拼成 `formStep` 会回的那几行（**终点那行带停下的网址**）。"""
    rows = [{"id": i, "task_id": int(task_id), "step": s,
             "url": "https://www.gowizard.com/auto-warranty/"} for i, s in enumerate(steps, 1)]
    if steps:
        rows[-1]["url"] = stop_url
    return rows


def test_a_two_row_walk_says_no_state_was_ever_entered():
    """★ 2 行那一档（`start → no_success`）：**一个状态都没进去** —— 判据把每一组都吞了。

    真单：`26004759` / `26033398`（后者协调者线上也取到过，逐字相同）。
    """
    said = fmr.where_it_stopped(walk("26004759", REAL_WALKS["26004759"]))
    assert "一个状态都没进去" in said, said
    assert "start" in said and "no_success" in said, said
    assert "判据" in said, "没说清是判据把步骤吞了：%s" % said


def test_a_walk_that_ends_in_stuck_says_where_it_got_stuck_and_where_the_page_was():
    """★ 5 行、`stuck` 收尾那一档：「走到了「auto」这一步，然后卡住了」+ **卡住时页面在哪**。

    这两样（走到哪 / 页面在哪）就是诊断要的第一手事实 —— 协调者 2026-09-20 的追加实测
    点名了它们。用的是他给的那一趟（`26034602`）原样。
    """
    said = fmr.where_it_stopped(LIVE_STUCK_ROWS)
    assert "走到了「auto」这一步" in said, said
    assert "卡住" in said, said
    assert "https://www.gowizard.com/auto/?text=Sedan&touchpointId=e8bcf7fa" in said, \
        "卡住时页面在哪没写出来：%s" % said
    assert "一个状态都没进去" not in said, said


def test_stalled_is_its_own_words_not_lumped_with_stuck():
    """`stalled`（点了页面没动）与 `stuck`（连着几步没做成）是**两种**收摊，各有各的话。

    出处：生产那份产物里 `_rpt("stuck")` 与 `_rpt("stalled")` 是**两个不同的分支**
    （`self.stuck >= STUCK_LIMIT` / `self.stalled >= STUCK_LIMIT`）。
    """
    said = fmr.where_it_stopped(walk("1", ["start", "auto", "stalled"]))
    assert "原地打转" in said, said
    assert "卡住" not in said, said


def test_a_five_row_walk_that_ends_in_no_success_is_not_read_as_stuck():
    """★★ **这一条是这次追加实测纠出来的那个错**（可复算）。

    生产日志里 `26015658` 是**5 行**、终点却是 `no_success`（进过 `auto_warranty`/`auto`）。
    按「几行 ⇒ 卡在最后那个状态」分类的话，这一趟会被读成「卡在 auto」——
    而它其实是**能走的都走完了、成功那一页没出现**（另一种病、另一种修法）。
    ⇒ 分类的判据是**终点是哪一个**，不是行数。
    """
    said = fmr.where_it_stopped(walk("26015658", REAL_WALKS["26015658"]))
    assert "卡住" not in said, "5 行就当成「卡住」了（这一趟其实是走完没成功）：%s" % said
    assert "一个状态都没进去" not in said, "它明明进过状态：%s" % said
    assert "没成功" in said, said
    assert "auto_warranty" in said and "auto" in said, "进过哪几个状态没说出来：%s" % said


def test_the_shape_is_decided_by_the_terminal_marker_not_by_how_many_rows():
    """★ 两条**都是 5 行**的真单，推出来的话必须**不一样** —— 而且差别只能来自分类。

    ⚠️ **两条要用同一个「停下的网址」**：不然它们本来就会因为 URL 不同而不相等，
    这条断言就可以**因为错的原因**通过（实测栽过一次：变异「按行数分类」在这条上
    照样绿，因为两条的 URL 不一样）。所以下面先量一句「两边的 URL 是同一个」当正控。
    """
    stop = LIVE_STUCK_ROWS[-1]["url"]
    finished_rows = walk("26015658", REAL_WALKS["26015658"], stop_url=stop)
    assert len(LIVE_STUCK_ROWS) == len(finished_rows), \
        "这两条行数不一样 —— 那就量不到「按行数分类会错」这件事"
    stuck = fmr.where_it_stopped(LIVE_STUCK_ROWS)
    finished = fmr.where_it_stopped(finished_rows)
    #: 正控：唯一可能造成差别的东西**两边都一样**（URL、体量），所以差别只能来自分类
    assert stop in stuck and stop in finished, (stuck, finished)
    assert stuck != finished, "同样 5 行、两种病，推出来却是同一句：%s" % stuck
    assert "卡住" in stuck and "卡住" not in finished, (stuck, finished)


def test_a_stuck_marker_that_is_not_last_is_not_read_as_the_terminal():
    """⚠️ **不许假设「`stuck` 一定在最后一行」**。

    对 gowizard 那一族它确实总在最后（`_rpt("stuck")` 紧跟 `return`），
    但这一层不许把这个假设写死 —— 别的产物、别的版本、脏数据都可能不是。
    所以：`stuck` 出现在中间时，**最后一行**才算终点；最后一行不认识就**不硬套**。
    """
    said = fmr.where_it_stopped(walk("1", ["start", "auto", "stuck", "auto"]))
    assert "走到了「auto」这一步" not in said, said
    assert "卡住" not in said, said
    assert "不认识这个值" in said, said


def test_a_tail_nobody_recognises_is_never_forced_into_either_sentence():
    """不认识的收尾 ⇒ **照实说「这一步它报的是 X，我不认识这个值」**，不许硬套一句人话。

    为什么这条不是洁癖：这一格的取值是**开放的** —— 生产日志里真报出去过的有 **160 个**
    （`logs/` 那 160 行 `[URL Report] step=`），绝大多数是 AI 那条路自由起的名。
    「我知道的取值」与「实际会出现的取值」是两回事。
    """
    said = fmr.where_it_stopped(walk("1", ["start", "auto", "brand_new_ending"]))
    assert "brand_new_ending" in said, said
    assert "不认识" in said, said
    assert "卡住" not in said and "一个状态都没进去" not in said, said


def test_a_success_last_row_in_a_failed_task_is_said_as_a_contradiction():
    """终点是 `success`、可这一趟在**失败列表**里 ⇒ 两件事对不上，**照实摆着**。

    不许挑一个当结论（说「它成功了」或说「它失败了」都是替数据下判断）。
    """
    said = fmr.where_it_stopped(walk("26005787", REAL_WALKS["26005787"]))
    assert "成功" in said and "失败列表" in said, said
    assert "对不上" in said, said


def test_a_walk_that_is_only_start_says_it_never_reported_an_ending():
    """只报了 `start` 一行 ⇒ **它没报终点** —— 「走到哪」这份账答不了，别替它推。"""
    said = fmr.where_it_stopped(walk("1", ["start"]))
    assert "没有报终点" in said, said
    assert "start" in said, said


def test_the_stop_url_is_the_one_on_the_terminal_row():
    """停下的那一刻页面在哪：取**终点那一行**记的那个（`_rpt` 报的是当下的 URL）。

    ⚠️ 终点那一行没记网址时退到最后一个进过的状态那一行；都没有就**不给**（不编一个）。
    """
    rows = [{"id": 1, "step": "start", "url": "https://a/1"},
            {"id": 2, "step": "auto", "url": "https://a/2"},
            {"id": 3, "step": "stuck", "url": "https://a/3-stopped"}]
    assert "https://a/3-stopped" in fmr.where_it_stopped(rows)
    rows[-1]["url"] = ""
    assert "https://a/2" in fmr.where_it_stopped(rows), "终点没记网址时没退到上一条"
    rows[1]["url"] = ""
    said = fmr.where_it_stopped(rows)
    assert "没记网址" in said, "一条网址都没有却不说一声：%s" % said


def test_a_step_row_without_a_url_still_counts_as_a_step():
    """`url` 可能是 NULL（契约里明说「容忍 NULL url」）—— 那一步**不许**因此消失。

    形状：`start` 没有网址、`auto` 也没有 —— 但只要终点在，「走到了哪个状态」就说得出来。
    """
    rows = [{"id": 1, "task_id": 9, "step": "start", "url": None},
            {"id": 2, "task_id": 9, "step": "auto", "url": ""},
            {"id": 3, "task_id": 9, "step": "stuck", "url": None}]
    said = fmr.where_it_stopped(rows)
    assert "auto" in said and "卡住" in said, said
    assert "没记网址" in said, said


def test_a_task_with_no_step_rows_says_so_and_invents_nothing():
    """③ 回来是空的 → **明说**「这一趟没有逐步记录」，**不许**编一步出来。"""
    said = fmr.where_it_stopped([])
    assert "没有" in said and "逐步记录" in said, said
    assert "走到了" not in said, "账是空的却编出了「走到了哪」：%s" % said


def test_the_evidence_is_the_header_plus_that_one_sentence():
    """`evidence` 的形状：**抬头（这一趟是谁）+ 一句「走到哪、然后怎么样」**。

    ⚠️ 抬头那半截也要在：没有它，修站那一趟拿到一段不知道**是谁**的证据。
    """
    text = fmr.evidence_text(MEASURED_ROWS[1], MEASURED_STEPS)
    assert "26033398" in text, text
    assert "www.gowizard.com/auto-warranty" in text, text
    assert "9-19 09:58" in text, text
    assert "一个状态都没进去" in text, text
    assert text.count("\n") == 1, "抬头与那一句之间正好一个换行：%r" % text


def test_the_evidence_does_not_paste_the_rows_in():
    """★ **不摆那几行原始账**（追加实测点名的那件事）。

    `[{id,task_id,step,url},…]` 不是人话；而 `step` 那一列也不是流水账 ——
    把它当「第 1 步 / 第 2 步」摆出来，读的人会以为那是一份**走路的清单**。
    """
    text = fmr.evidence_text(MEASURED_ROWS[1], MEASURED_STEPS)
    for junk in ('"id"', "'id'", "task_id\":", "\n1. ", "\n2. ", "tracking", "{", "}"):
        assert junk not in text, "原始账漏进 evidence 了（%s）：\n%s" % (junk, text)


def test_the_evidence_no_longer_hands_the_plan_parser_a_walk():
    """★ 这一改**推翻了设计注 §2.3.2 的一个前提**，并且要**钉住**这件事。

    设计注原话：「formStep 那类证据**本来就是带步骤序号的**，`plan.parse()` 收它，
    同一套机制，不另开一条路」—— **那个前提被实测否掉了**：那几行里没有「步骤」这回事。

    所以：`plan.parse(evidence)` **必须解析不出步骤**（`source == ""`）。
    为什么这也算一条判据：留着编号行的话，修站那条路会拿到一份**编出来的计划**
    （「第 1 步：进站 / 第 2 步：走完了但没出现成功那一页」）—— 那比空着手更坏。
    """
    from agent import plan as plan_mod
    text = fmr.evidence_text(MEASURED_ROWS[1], MEASURED_STEPS)
    got = plan_mod.parse(text, source="evidence")
    assert got.steps == [], "evidence 又被拼成「计划」了：%r" % [(s.n, s.text) for s in got.steps]
    assert got.source == "", got.source
    #: 正控：**这条尺子量的不是「plan.parse 坏了」** —— 真的编号行它照样解析得出来
    assert len(plan_mod.parse("1. 点开始\n2. 填表", source="evidence").steps) == 2


def test_evidence_for_reads_the_row_from_the_backend_and_the_steps_from_the_backend():
    """`evidence_for(task_id)`：**两样都从后端读**（不是一个从调用方手上拿）。

    ⚠️ 顺序也要钉：先 formLog（拿这一趟的站/时刻），再 formStep。
    """
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    got = client(rec).evidence_for("26033398", site="www.gowizard.com/auto-warranty/",
                                   since="2026-09-19 00:00:00")
    assert [query_of(u)[0] for u in rec.urls] == ["/api/quest/formLog", "/api/quest/formStep"]
    assert got["task_id"] == "26033398"
    assert got["url"] == MEASURED_STEPS[0]["url"]
    assert "9-19 09:58" in got["row_say"], got["row_say"]
    #: ⚠️ 这一行的**旧版**断言是「`no_success` 不在 evidence 里」—— 那个口径在
    #: 2026-09-20 的追加实测之后**反了**：那一段证据现在**故意点名**收尾那个值
    #:（「只报了「start」就直接「no_success」」是协调者给的句子形状）。
    #: 换成的这一条**更强**：只有 `step` 那一列的值许出现，**分类码**（`site_specific`）
    #: 与原始 JSON 一个都不许。
    assert "no_success" in got["evidence"], got["evidence"]
    for junk in ("site_specific", '"task_id"', '{"', "US"):
        assert junk not in got["evidence"], "码漏进 evidence 了（%s）：%s" % (junk, got["evidence"])


def test_evidence_for_a_task_that_is_not_in_the_window_says_so():
    """给了一个**不在这个时间窗里**的 task_id → **明说**（不拿第一条顶替）。

    顶替是最坏的一种：那会把**另一趟**的逐步记录拼成这一趟的证据。
    """
    rec = Recorder(envelope(MEASURED_ROWS), envelope(MEASURED_STEPS))
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec).evidence_for("99999999", site="www.gowizard.com/auto-warranty/")
    said = str(e.value)
    assert "99999999" in said, said
    assert len(rec.urls) == 1, "没找着那一条还是去拉了逐步记录：%r" % rec.urls


# ─────────────────── 服务那一层：token 什么时候读、报什么 ───────────────────


def test_the_token_is_read_from_the_environment_and_never_from_the_code():
    """★ token **从环境变量读**（`FMR_AGENT_TOKEN`），不写死在代码里、不进 git。

    量两下：① 环境里有 → 请求头里就是它；② 环境里没有 → 客户端就是「没配」。
    """
    monkey = pytest.MonkeyPatch()
    monkey.setenv(fmr.TOKEN_ENV, "from-the-env")
    try:
        rec = Recorder(envelope([]))
        fmr.FmrClient(opener=rec).fetch_failures("s/")
        assert rec.headers[0]["X-Api-Token"] == "from-the-env", rec.headers[0]
        monkey.delenv(fmr.TOKEN_ENV)
        assert fmr.FmrClient(opener=rec).configured is False
    finally:
        monkey.undo()


def test_the_default_base_is_the_one_that_was_measured():
    """默认那个域名是**线上实测过的那一个**（`FMR_BASE_URL` 只是给测试/灰度留的口子）。"""
    assert fmr.DEFAULT_BASE == "https://fmr.3tkj.cn"
    monkey = pytest.MonkeyPatch()
    try:
        monkey.delenv(fmr.BASE_ENV, raising=False)
        assert fmr.FmrClient(token=FAKE_TOKEN).base == "https://fmr.3tkj.cn"
    finally:
        monkey.undo()


def test_the_client_says_which_of_the_two_it_is_configured_or_not():
    """`/health` 那句人话：**配了** / **没配**（各自一句人话）。

    为什么要有：没配 token 的部署上，「查失败」按下去只会红一次；
    运维在那儿能先看到「这台机器上没有那个 token」—— 而不是去查网络。
    ⚠️ 那句话里**不许有 token 本身**。
    """
    assert "FMR_AGENT_TOKEN" in fmr.FmrClient(token="").say()
    said = fmr.FmrClient(token=FAKE_TOKEN).say()
    assert "配了" in said or "有了" in said, said
    assert FAKE_TOKEN not in said, "token 本身进了人话：%r" % said
