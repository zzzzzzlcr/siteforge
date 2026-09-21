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


# ══════════════ Task 4 ③：榜单（今天哪些站在失败）══════════════
#
# 病（用户 2026-09-20 的原话那条链）：FMR 上**失败看得见、原因看不见**；
# 而 ①（`formLog`）**必须带 `site`** —— 它问的是「**这个站**今天怎么了」。
# 要答「今天**哪些站**在失败」，原来只能去翻后台页 / 控制台。
#
# `formLogRank` 补的就是这一问。这一份钉的是它**读回来之后**那几件事：
#
# | # | 性质 | 为什么值得单钉一条 |
# |---|---|---|
# | 1 | 那一天**没有失败**时回来的是**一个对象**（不是空数组） | 形状事故与「今天没有站在失败」在屏幕上会一模一样 |
# | 2 | 三个数（`failed_total` / `unattributed` / 摆出来的）**对不上是正常的**，`say` 要把差额说出来 | 只说「摆了 3 个站」，读的人会以为今天就坏这几个 |
# | 3 | `has_script` 是**三态** | `False`（有配置没脚本）与 `null`（没有配置）处置相反 |
# | 4 | 没有配置时**照抄后端的 `note`** | 后端那两种 note 的处置也相反（建配置 vs 修映射） |

# ═══════════════════ 这份夹具的**来历**（★ 逐格标清楚）═══════════════════
#
# ⚠️ **Task 4 修复轮 1 把这一块整个重标了一遍**（复审 F3）。上一版这里写的是
# 「**线上实测的那一行**（brief §1.1 里那一条，**逐字**）」「脏键那一条**不是编的**」——
# 那两句里**有真有假**，而假的正好在**本任务头号判据的靶子**上。复审量的真值见
# `task-4-review.md` §三②。下面**每一格都注明它从哪来**：
#
#   · 【转述的·出处 brief §1.1】—— 那份 JSON 样例（**转述**：我手里没有线上 200 正文，
#     线上要 token，我没有；见 `task-4-report.md` 的顾虑）。
#   · 【转述的·出处 brief §2 R1】—— **截断引用**（brief 原文自己就带 `…`）。
#   · 【复审量的·出处 `task-4-review.md` §三②】—— 复审拿真 token 打线上量的。
#   · 【构造】—— 我为**驱动某条分支**摆的值，契约里合法、但**没有人量过它**。
#
# ⚠️ **「脏」不等于「查不到」**——这是上一版最大的错（复审 §三②）：
# 那条 1430 字符、带 query 又粘着报错的键，后端**查得到**（200 + 6 行，归到 config 66）。
# 今天真查不到的是**另一条**：榜单上那条**没有配置**的键（后端回业务码 404）。

#: 【转述的·出处 brief §1.1】那一行（干净键）—— 逐字。它是这一族里唯一一条
#: 【转述的】来源，所以下面凡是拿它当输入的人都得知道：**它不是我量的**。
RANK_KEY_CLEAN = "callyourdate.com/land/sp/519015a5"
#: 【转述的·出处 brief §2 R1】—— ★ **截断引用，不是完整键**。
#: brief 原文那一行自己就带着 `…`（`…/?utm_source=taboola&…`），我照抄了它给的那一段。
#: 【复审量的·出处 同上】完整那条长 **1430 字符**，结尾是
#: `…unknown IPAddressSpace value: Private`（这一段我**没有**，也不替它编）。
#: 它在这一份夹具里的用处只有一个：**证明一个又长又脏、粘着别人报错的键能被正确转义与摆出来**。
RANK_KEY_LONG = ('callyourdate.com/land/sp/519015a5/?utm_source=taboola&id_visit_prev=#'
                 'c3RlcDM=" 2026/09/20 03:45:22 ERROR: could not unmarshal event')
#: ★【复审量的·出处 `task-4-review.md` §三②】今天**真的**「按这个键查不到」的那一条：
#: 榜单上那个**没有配置**的键，后端对它回**业务码 404**（`config not found`）。
#: ⚠️ 复审量的是「拿这个键去打 `formLog` 会得到什么」；「它在榜单里那一行长什么样」
#: 是按后端的 `formLogRank` 契约摆的（没有配置 ⇒ `config_id`/`config_status`/`has_script`
#: 三格 `null` + 一句 `note`）—— 那一部分是【构造】，有 `FormLogRankTest.php:392` 的
#: `assertSame('没有配置', $row['note'])` 当形状依据。
RANK_KEY_NO_CONFIG = "secure.comparethemarket.com.au/ctm/health_quote_v4.jsp"
#: 【构造】只为驱动「失败列表比榜单**少**」那一支的一条键（复审那一天 8 个真键里
#: **没有**这个方向 —— 全是 `n > want` 或相等）。**没有人量过它**，它是合成的。
RANK_KEY_FEWER = "cvrefresh.com/land/sp/constructed-sample"

#: 榜单那一天的正文。**每一行都标了来历**（见上）；`failed_total` / `unattributed`
#: 是【构造】配平出来的（3+1+1+5 = 10，+8 = 18）—— 为的是让抬头那句落在
#: 「**与上面那个总数对得上**」那一支上，于是别的用例量差额时不会被这一句干扰。
RANK_FIXTURE = {
    "date": "2026-09-20", "failed_total": 18, "unattributed": 8,
    "rank": [
        #: 【转述·brief §1.1】干净那一行，逐字。
        {"site": RANK_KEY_CLEAN, "fail": 3,
         "config_id": 66, "config_status": "启用", "has_script": True},
        #: ⚠️ 上一版这几格编成 `None/None/None/"没有配置"`（复审 F3：**归属整个写反了**）。
        #: 这一版按【复审量的·出处 §三②】填回去：真那条是 `66 / "启用" / true`。
        {"site": RANK_KEY_LONG, "fail": 1,
         "config_id": 66, "config_status": "启用", "has_script": True},
        #: 【复审量的】键 + 【构造】那几格（见 `RANK_KEY_NO_CONFIG`）。
        {"site": RANK_KEY_NO_CONFIG, "fail": 1,
         "config_id": None, "config_status": None, "has_script": None, "note": "没有配置"},
        #: 【构造】驱动「少」那一支。
        {"site": RANK_KEY_FEWER, "fail": 5,
         "config_id": 7, "config_status": "停用", "has_script": False},
    ],
}


def _rank_row(site: str) -> dict:
    """夹具里那一行（按**键**取，不按位置取 —— 位置会随夹具长一截而漂）。"""
    for row in RANK_FIXTURE["rank"]:
        if row["site"] == site:
            return row
    raise AssertionError("夹具里没有这个键：%r" % site)


def test_the_rank_query_is_the_one_that_was_measured():
    """那一跳的**路径与参数**：`/api/quest/formLogRank?date=…&limit=…`，token 走请求头。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    client(rec).fetch_rank(date="2026-09-20", limit=7)
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formLogRank", rec.urls[0]
    assert params == {"date": "2026-09-20", "limit": "7"}, params
    assert rec.headers[0]["X-Api-Token"] == FAKE_TOKEN, rec.headers[0]


def test_the_rank_leaves_both_cells_out_so_the_backend_picks_the_day():
    """★ 不给 `date` 时**整格不发** —— 缺省（= 后端那边的「今天」）不是「发一个空串」。

    发空串不是缺省：后端对 `?limit=` 会回 400（`ConvertEmptyStringsToNull` 那个坑，
    ① 那边的注释里记着同一个形状）。所以这一格**宁可不发**。
    """
    rec = Recorder(envelope(RANK_FIXTURE))
    client(rec).fetch_rank()
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formLogRank", rec.urls[0]
    assert params == {}, "空着居然发了参数：%r" % (rec.urls[0],)


def test_the_rank_answer_is_the_object_the_backend_gave():
    """回给调用方的是**后端那个对象**（四个格子），这一层**不重排、不改写**。"""
    rec = Recorder(envelope(RANK_FIXTURE))
    got = client(rec).fetch_rank()
    assert got == RANK_FIXTURE, got


def test_a_rank_answer_that_is_not_an_object_is_unmeasured_not_an_empty_day():
    """★★ **形状事故不许变成「今天没有站在失败」。**

    判据在**类型**上：那一天真没有失败时，后端回的**照样是这个对象**
    （`failed_total:0` / `rank:[]`）—— 所以「回的不是对象」只可能是**这一次没量着**。

    正控在下面那一条：**同一个对象、`rank` 空**必须**不抛** —— 两条一起看，
    才说得上「这一条量的是形状，不是空不空」。
    """
    rec = Recorder(envelope([]))
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec).fetch_rank()
    said = str(e.value)
    assert fmr.UNMEASURED_SAY in said, said
    assert "2026-09-20" not in said, "这一句不该像一份榜单：%r" % said


def test_an_empty_rank_object_is_exactly_how_no_failures_looks():
    """**真量了、真没有** → 不抛：`failed_total:0` 的日子回来的还是那个对象。"""
    day = {"date": "2026-09-20", "failed_total": 0, "unattributed": 0, "rank": []}
    got = client(Recorder(envelope(day))).fetch_rank()
    assert got["failed_total"] == 0 and got["rank"] == [], got


def test_the_rank_says_the_three_numbers():
    """★ `say` 里**三个数都要在**（当日总数 / 归不到站的 / 摆出来几个站）。

    ⚠️ **判据写成带着重号的那个数**（`**18**`），不是裸的 `"18"`/`"2"` ——
    裸的那种**会被同一句里的日期 `2026-09-20` 保证为真**（复审 F8 实测：
    `"2" in said` 与 `"0" in said` 两条断言因此**空转**，
    改坏了那句话它们照样绿）。`rank_say` 给这三个数都上了 `**`，
    而日期那一段**不带**着重号 —— 于是这样比才是真的在量那几个数。
    差额那一支另有专门的一条（`test_the_rank_says_when_it_did_not_show_everything`）。
    """
    said = fmr.rank_say(RANK_FIXTURE, 50)
    assert "2026-09-20" in said, said
    assert "**18**" in said, "当日总数那个数没上屏（或没上着重号）：%r" % said
    assert "**8**" in said, "归不到站那个数没上屏：%r" % said
    #: 摆出来几个站：这一份是 4 个；加起来那 10 次失败**不带**着重号，所以按字面比。
    assert "下面摆了 4 个站" in said, said
    assert "加起来 10 次失败" in said, said
    #: ⚠️ 那两个键**原样的字**不许出现在这一句里：它们是给机器 join 用的，不是给人读的。
    assert "callyourdate" not in said, said
    assert "secure.comparethemarket" not in said, said

    #: 正控（去掉上面那几个 `**` 判据的空转可能）：**换一个数，那一句就得跟着变** ——
    #: 不然上面那几条只是在量「这句话里出现过那些字」。
    other = fmr.rank_say(dict(RANK_FIXTURE, failed_total=99, unattributed=89), 50)
    assert "**99**" in other, other
    assert "**18**" not in other, "换了数字那一句没变：%r" % other


def test_a_day_with_nothing_failing_is_said_as_measured_not_as_a_blank():
    """「一个站都没有」有两种成因，**必须分得开**：真没有（总数 0）vs 全归不到站。"""
    said = fmr.rank_say({"date": "2026-09-20", "failed_total": 0, "unattributed": 0,
                         "rank": []}, 50)
    #: ⚠️ 比的是**带着重号**的那个 0（`**0**`）—— 裸的 `"0" in said` 会被
    #: 同一句里的日期 `2026-09-20` 保证为真（复审 F8）。
    assert "量到" in said, said
    assert "**0**" in said, said
    other = fmr.rank_say({"date": "2026-09-20", "failed_total": 2, "unattributed": 2,
                          "rank": []}, 50)
    assert "归不出来" in other or "归不到" in other, other
    assert other != said, "两种成因说了同一句话"


def test_the_rank_says_when_it_did_not_show_everything():
    """★ 截断了**要说**（`limit` 只截断 `rank`，`failed_total` 是当日全量）。"""
    data = {"date": "2026-09-20", "failed_total": 18, "unattributed": 2,
            "rank": [{"site": "a.com/x", "fail": 3, "config_id": 1,
                      "config_status": "启用", "has_script": True}]}
    said = fmr.rank_say(data, 1)
    assert "没摆全" in said, said
    assert "13" in said, "差额没算对：%r" % said
    assert str(fmr.RANK_MAX_LIMIT) in said, "没说后端上限：%r" % said
    #: ★ 截断时**不许**说一句「摆全了」——「差额也报出来了」与「还说了一句摆全了」
    #:    只差几个字，而后者是一句**假话**（摆出来 3 次、当日 18 次，摆全了是哪门子摆全）。
    assert "摆全了" not in said, said
    #: 正控（这个判据的另一半）：**对得上**的那一份说的是**相反**的话 ——
    #: 否则上面那条「不许出现」可以靠「这一句里永远不说摆全了」满足，而那等于没量。
    whole = dict(data, failed_total=5)
    assert "对得上" in fmr.rank_say(whole, 50), fmr.rank_say(whole, 50)
    assert "没摆全" not in fmr.rank_say(whole, 50), fmr.rank_say(whole, 50)


def test_a_rank_row_becomes_one_human_sentence():
    """一行的人话：`失败 3 次 · 配置 66（启用） · 有 py 脚本`（码一个都不许在）。"""
    said = fmr.rank_row_say(RANK_FIXTURE["rank"][0])
    assert said == "失败 3 次 · 配置 66（启用） · 有 py 脚本", said


def test_has_script_is_three_states_not_two():
    """★ `True` / `False` / `None` **三态三句话** —— 后两种的处置**正好相反**。

    `False` = 有配置但脚本那一格是空的（**去写脚本**）；
    `None` = 连配置都没有（**去建配置**）。
    合成一句「没有脚本」，运营就会拿着「没配置」的站去改脚本（那一步根本无处可改）。
    """
    yes, no, absent = (fmr.has_script_say(True), fmr.has_script_say(False),
                       fmr.has_script_say(None))
    assert len({yes, no, absent}) == 3, (yes, no, absent)
    assert "配置" in absent, absent
    assert "脚本" in no and "配置" not in no.split("没有脚本")[0][-4:], no


def test_a_rank_row_without_a_config_uses_the_backends_own_note():
    """★ 没有配置时**照抄后端那句 `note`** —— 后端的两种 note 处置相反，合起来就是把两件事变一件。"""
    said = fmr.rank_row_say(_rank_row(RANK_KEY_NO_CONFIG))
    assert "没有配置" in said, said
    orphan = dict(_rank_row(RANK_KEY_NO_CONFIG), note="映射指向的配置行不存在")
    other = fmr.rank_row_say(orphan)
    assert "映射指向的配置行不存在" in other, other
    assert other != said, "两种 note 说了同一句话"


def test_a_config_status_this_page_does_not_know_is_said_out_loud():
    """取值是开放的 ⇒ 认不出的一格要**带着原样的字**冒出来（不猜、也不悄悄放行）。"""
    row = dict(RANK_FIXTURE["rank"][0], config_status="archived")
    said = fmr.rank_row_say(row)
    assert "archived" in said, said
    assert "不认识" in said, said


# ══════════════ Task 4 ④：原因（这一单为什么失败）══════════════
#
# 结构性事实（2026-09-20 量）：`formStep` 只有 4 列（`id/task_id/step/url`）——
# **设计上就没有「原因」这一格**。原因只写在跑单那台机器自己的 `logs/<site>.log` 里，
# 而本机日志覆盖不到别的机器跑的趟 ⇒「原因看不见」是从这儿来的。
# `fail_diag` 那张表是它的新去处，`failDiag` 是读侧。

#: **后端那份 §2.2 的形状**（`GET /api/quest/failDiag?task_id=…` → 行数组，按 `created_at` 倒序）。
#: ⚠️ `task_id` 回来是**数字**（库里是 int，控制器显式 `(int)` 转）——所以下面这条夹具是数字。
MEASURED_DIAG = [
    {"id": 83, "task_id": 26033398,
     "site": "www.gowizard.com/auto-warranty", "machine": "worker-07", "exit": "stuck",
     "lines": "第 18 步跳过：这一页不像「gowizard-13」那个状态（正文里没有「Progress: 60% …」）",
     "at": "2026-09-20T14:32:11+08:00", "created_at": "2026-09-20T14:35:00+08:00"},
]


def test_the_diag_query_is_the_one_that_was_measured():
    """★ 那一跳**只带单号**（`?task_id=`）—— **没有 `site`**。

    这是 brief §2 R1 那条纪律的可判形状：站点键会**静默**查到 0 行，
    而单号是精确的。所以这一条同时钉「发了什么」与「**没发**什么」。
    """
    rec = Recorder(envelope(MEASURED_DIAG))
    client(rec).fetch_diag("26033398")
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/failDiag", rec.urls[0]
    assert params == {"task_id": "26033398"}, params
    assert "site" not in params, "原因那一跳带了站点键：%r" % (params,)
    assert rec.headers[0]["X-Api-Token"] == FAKE_TOKEN, rec.headers[0]


def test_a_diag_query_without_a_task_is_refused_for_free():
    """没说是哪个单 —— **一个请求都不发**（与 ① 缺 site、② 缺 task_id 同一档）。"""
    rec = Recorder(envelope(MEASURED_DIAG))
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec).fetch_diag("")
    assert rec.urls == [], "缺单号居然发了请求：%r" % (rec.urls,)
    #: ⚠️ 这一句里**没有** `UNMEASURED_SAY`，与 ① 缺 site / ② 缺 task_id 那两句**同一体例**：
    #: 「一个请求都没发出去」本来就不可能是「那个站没有失败」那一族的误会，
    #: 那句话是给**量过之后**没量着的那几种用的。
    assert "免费" in str(e.value) and "没说是" in str(e.value), str(e.value)


def test_an_empty_diag_list_is_measured_not_a_failure_to_measure():
    """★ **空数组 = 「量到了，这单还没有原因行」**，**不抛**。

    这是这一层里少数几个「空是合法的」的口子之一 —— 但它**不是**「没有失败」那一族：
    它说的是**这一屏现在没有这一单的原因**（成因三种，数据上分不出）。
    正控：同一个形状把 `status` 改成 401 就必须**抛**。
    """
    assert client(Recorder(envelope([]))).fetch_diag("26033398") == []
    with pytest.raises(fmr.FmrUnmeasured):
        client(Recorder(envelope([], status=401, msg="no"))).fetch_diag("26033398")


def test_a_diag_answer_that_is_not_a_list_is_unmeasured():
    """回的是**一个对象**（不是行数组）—— 形状事故，**抛**，不许读成「一条都没有」。"""
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(Recorder(envelope({"rows": MEASURED_DIAG}))).fetch_diag("26033398")
    assert fmr.UNMEASURED_SAY in str(e.value), str(e.value)


def test_the_diag_rows_come_back_in_the_order_the_backend_gave_them():
    """后端给的是 `created_at` 倒序 —— 这一层**不重排**（重排就是这一层编了一个顺序）。"""
    two = [MEASURED_DIAG[0], dict(MEASURED_DIAG[0], id=71, exit="no_success")]
    got = client(Recorder(envelope(two))).fetch_diag("26033398")
    assert [r["id"] for r in got] == [83, 71], got


def test_exit_unknown_is_said_as_not_reported_and_not_folded_into_the_three():
    """★★ **`unknown` 是「没报上来」** —— 编成那三种之一就是把客户端的话改掉。

    出处（客户端自己的原话，`/opt/skills/auto-farm-skill/scripts/ad-task.py:1265`）：
    「导航态 / 认不出来 → `unknown` —— 别硬塞成三个已知值之一：那会让查的人**看错原因**，
    比空着更坏」。

    正控：三个已知值各有各的说法，**与 `unknown` 那句都不相等** ——
    一条「什么都答没报上来」的实现过不了这一条。
    """
    said = fmr.exit_say("unknown")
    assert "没报上来" in said, said
    three = {code: fmr.exit_say(code) for code in ("stuck", "stalled", "no_success")}
    assert len(set(three.values())) == 3, three
    for code, word in three.items():
        assert word != said, "%s 与 unknown 说了同一句" % code
        assert "没报上来" not in word, (code, word)


def test_the_exit_words_are_the_same_words_the_terminal_say_uses():
    """★ **同一个词两个口子报**（`formStep` 的最后一行 / `failDiag` 的 `exit`）——
    两处各写一份迟早漂。这一条钉的是「没有各写一份」，不是「这两句长得像」。
    """
    for code in ("stuck", "stalled", "no_success"):
        assert fmr.EXIT_SAY[code] == fmr.TERMINAL_SAY[code][0], code


def test_an_exit_code_this_page_does_not_know_is_said_out_loud():
    """取值**有意是开放的**（后端不校验）⇒ 认不出的一格带着原样的字冒出来。"""
    said = fmr.exit_say("timeout")
    assert "timeout" in said and "不认识" in said, said
    assert "结束方式它没给" == fmr.exit_say(""), fmr.exit_say("")


def test_the_diag_head_uses_the_moment_the_failure_happened_not_the_ingest_moment():
    """★ 时刻取 `at`（**失败真正发生的时刻**），不是 `created_at`（入库时刻）。

    后端自己的注释点过：离线机器**晚补发**时两者差很多，而查的人要的是「哪天失败的」。
    这一条拿一条**两者不同**的夹具把这件事量死（同一句里不许出现入库那个时刻）。
    """
    said = fmr.diag_head_say(MEASURED_DIAG[0])
    assert "9-20 14:32" in said, said
    assert "14:35" not in said, "抬头用了入库时刻：%r" % said
    assert "worker-07" in said, said
    assert "卡住" in said, said


def test_a_diag_row_without_at_says_it_fell_back_to_the_ingest_moment():
    """`at` 没给（老客户端 / 手工发的）才退回入库时刻 —— **并且要说出退过**。

    不说的坏处：一个晚补发的班次会让「9-20 **收上来的**」被读成「9-20 **失败的**」。
    """
    said = fmr.diag_head_say(dict(MEASURED_DIAG[0], at=None))
    assert "9-20 14:35" in said, said
    assert "收上来" in said, said


# ══════════════ Task 4 §4：**一处旋钮** + HTTP 码那句话 ══════════════


def test_the_one_knob_moves_all_four_reads():
    """★★ brief §4 的硬要求：四个读口的基址**是同一个旋钮**，没有「按接口各配一个」。

    为什么这条是硬的：线上 `fmr.3tkj.cn` **还没有** `failDiag`（brief 实测 404），
    本地 `192.168.1.51:6060` **有** ⇒ 要对着本地那份跑就得换基址。
    真长出「按接口各配一个」的那天，「名字说 A、量的是 B」就回来了。
    """
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv(fmr.BASE_ENV, "http://192.168.1.51:6060")
        rec = Recorder(envelope([]), envelope([]), envelope({}), envelope([]))
        got = fmr.FmrClient(token=FAKE_TOKEN, opener=rec)
        got.fetch_failures("s/")
        got.fetch_steps("1")
        got.fetch_rank()
        got.fetch_diag("1")
        assert len(rec.urls) == 4, rec.urls
        for url in rec.urls:
            assert url.startswith("http://192.168.1.51:6060/api/quest/"), url
    finally:
        monkey.undo()


def test_the_default_base_is_still_the_one_that_was_measured():
    """★ 加两个新口子**没有动**那个默认值（旋钮只有一处，默认值还是线上那一个）。"""
    assert fmr.DEFAULT_BASE == "https://fmr.3tkj.cn"


def test_a_route_that_is_not_deployed_is_said_as_that_not_as_cannot_connect():
    """★ HTTP 404（**路由不存在**）与「连不上」**不是一件事** —— 人话要说准。

    为什么这条单钉：`failDiag` 今天就是这个形状（线上还没上），
    把它说成「连不上那个后端」，运维会去查网络 / 代理 / 防火墙，而真因是**那个路由没上**。
    这一族的正常形状是「**HTTP 恒 200**、码在 body 里」⇒ 按 HTTP 回码**本身**就说明
    说话的不是这个接口，是它前面的那一层。
    """
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(urllib.request, "urlopen", boom)
    try:
        with pytest.raises(fmr.FmrUnreachable) as e:
            fmr.FmrClient(token=FAKE_TOKEN).fetch_diag("26033398")
    finally:
        monkey.undo()
    said = str(e.value)
    assert "404" in said, said
    assert "没部署" in said, said
    assert "连不上那个后端" not in said, "把「路由没上」说成了「连不上」：%r" % said
    assert fmr.UNMEASURED_SAY in said, said


def test_the_noun_each_reader_prints_is_the_one_for_that_endpoint():
    """四个口各自那句「读不了**什么**」—— 新加的两个不许顶着「失败记录」这四个字。"""
    assert fmr._PATH_SAY["/api/quest/formLogRank"] == "失败榜单"
    assert fmr._PATH_SAY["/api/quest/failDiag"] == "失败原因"
    assert fmr._PATH_SAY["/api/quest/formLog"] == "失败记录"
    assert fmr._PATH_SAY["/api/quest/formStep"] == "逐步记录"


def test_the_rank_row_text_carries_no_emphasis_markers():
    """★★ 那一行**一个 `**` 都不许有** —— 它是这一族里唯一要进 `<option>` 的一句。

    页面上它既是「读的那一行」（走 `rich()`，`**x**` 会变成 `<b>x</b>`）、
    也是「挑的那一格」（`<option>` 里放不了标记 ⇒ 可见结果就是**两个星号**）。
    两句长相不同 = 人读到的和挑到的不一样；而这一屏的规矩是**同一个来源**。

    ⚠️ 这条钉的是**为什么**：不是「星号不好看」，是那一句的**两个去处**里
    有一个放不了标记。所以判据是「这一句的每一个出口都不含 `**`」——
    下面把三种 `has_script` / 两种 `note` 都过一遍，别只测一个样本。
    """
    samples = list(RANK_FIXTURE["rank"])          # 四行，四种配置形态
    samples.append(dict(_rank_row(RANK_KEY_NO_CONFIG), note="映射指向的配置行不存在"))
    for hs in (True, False, None):
        samples.append(dict(_rank_row(RANK_KEY_CLEAN), has_script=hs))
    for row in samples:
        said = fmr.rank_row_say(row)
        assert "**" not in said, "这一句带了着重号（它在 <option> 里会显示成两个星号）：%r" % said
    #: 正控：`has_script` 那三态**确实**各有各的说法（否则上面那一圈是空转的）。
    assert len({fmr.has_script_say(v) for v in (True, False, None)}) == 3


def test_the_noun_each_reader_actually_prints_is_the_one_for_that_endpoint():
    """★★ 复审 F1：**读的是「印出来的那句话」，不是那张词表。**

    上一版这条只量了 `_PATH_SAY` 那四条 —— 而 `_call` 里**两处 `raise` 把「读不了失败记录」
    硬编码**进去了（共享的那两处：正文不是 JSON、正文不是信封）⇒
    ③④ 两个新读口读不成时，运维在榜单那一栏看到的是「读不了**失败记录**」。
    **名字说 A、量的是 B** —— 判据量的是词表，不是输出。这一条量**输出**。

    ⚠️ 四个口 **×** 两条失败路 = 八格，一格都不许漏（漏的那几格就是上一版逃掉的那两处）。
    ⚠️ Task B1：这一圈**遍历 `_PATH_SAY`**，所以新加的那个读口（⑤ 配置）**自动**进来了
    —— 下面那张表也补了它（不补就是 `KeyError`：这张表与那张词表必须一起长）。
    """
    cases = [
        (r"not json at all", "不是 JSON"),
        ("[1, 2, 3]", "不是一个信封"),
    ]
    for body, kind in cases:
        for path, noun in fmr._PATH_SAY.items():
            rec = Recorder(body)
            got = None
            #: 四个口各自怎么调（参数各给一个合法的，别让「免费的那道闸」先把它挡掉）。
            call = {
                "/api/quest/formLog": lambda c: c.fetch_failures("s/"),
                "/api/quest/formStep": lambda c: c.fetch_steps("1"),
                "/api/quest/formLogRank": lambda c: c.fetch_rank(),
                "/api/quest/failDiag": lambda c: c.fetch_diag("1"),
                #: Task B1 ⑤：它**不带 token 也走得通**（公开口），所以这里不必配 token。
                fmr.FORM_CONFIG_PATH: lambda c: c.form_config("cvrefresh.com"),
            }[path]
            with pytest.raises(fmr.FmrUnmeasured) as e:
                call(client(rec))
            said = str(e.value)
            assert kind in said, "%s（%s）：这一支的说明不见了：%r" % (path, kind, said)
            assert "读不了%s" % noun in said, (
                "%s 上这一支顶着别处的名字（应当说「读不了%s」）：%r" % (path, noun, said))
            got = said
            #: 正控：**不许**顶着「失败记录」（那正是逃掉的那两处印出来的字）——
            #: `formLog` 那一格本身就是「失败记录」，所以只在别的三个口上要求。
            if noun != "失败记录":
                assert "读不了失败记录" not in got, (
                    "%s 上印了「失败记录」（共享 `_call` 里那两处硬编码）：%r" % (path, got))


def test_a_body_with_no_data_cell_is_not_read_as_no_failures():
    """★★ 复审 F5：`data: null` / **整个 `data` 那一格不在** —— 都**不是**「没有失败」。

    ⚠️ 这一条钉的是一处**行为变化**（Task 4 加 `shape` 闸时带出来的），
    报告 §4 那张表上一版把它标成「否」，**是错的**：

    | 输入 | BASE `680529e` | 现在 |
    |---|---|---|
    | `{"status":200,"data":[]}` | `[]` | `[]`（**没变**，这才是「真量了、真没有」） |
    | `{"status":200,"data":null}` | **`[]`** | **抛** |
    | `{"status":200}`（没有 `data` 那一格） | **`[]`** | **抛** |

    ⇒ 后两行**变了**，而这是**照模块 docstring 那张表修的**：
    「**唯一能变成空列表的，只有第一行**（`status:200` + `data:[]` 那个**空数组**）——
    其余一律抛」。BASE 自己那条不变量当时就没做到。

    ⚠️ 这一条**两个读口都过**（`fetch_failures` / `fetch_steps`）——
    它们走的都是同一个 `_call`，只量一个等于放另一半跑。
    """
    for bad in (envelope(None), {"status": 200, "msg": "success"}):
        for noun, call in (("失败记录", lambda c: c.fetch_failures("s/")),
                           ("逐步记录", lambda c: c.fetch_steps("1"))):
            with pytest.raises(fmr.FmrUnmeasured) as e:
                call(client(Recorder(bad)))
            said = str(e.value)
            assert fmr.UNMEASURED_SAY in said, (noun, bad, said)
            #: ⚠️ 各报**各的名**（这也是 F1 那处硬编码的反面：两个口不许都顶着同一句）。
            assert "读不了%s" % noun in said, (noun, bad, said)
    #: ★ 正控：**同一批输入里那个「真空数组」必须照旧回空列表**
    #: （否则上面两条可以靠「什么都抛」满足 —— 而那就把「真量了、真没有」也杀了）。
    assert client(Recorder(envelope([]))).fetch_failures("s/") == []
    assert client(Recorder(envelope([]))).fetch_steps("1") == []


def test_a_backend_cell_that_looks_like_markup_comes_through_verbatim():
    """★ 复审 F9：后端的 `note` / 认不出的 `config_status` **原样过来**，这一层不加工。

    那两格是**数据**（后端搬进来的），不是我们的标记 —— 所以：
      · 这一层**不剥星号、不改写**（剥了就是把后端说的话改掉）；
      · 页面那两个去处**都走 `esc()`**（不走 `rich()`）⇒ 「原样」在两个地方是同一个字节
        （页面那侧：`test_console_js.py::test_the_row_you_read_is_the_row_you_pick`）。

    ⚠️ 上一版的钉子只用**我们自己的词**取样（`没有配置` / `映射指向的配置行不存在`），
    那两格**没覆盖** —— 而它们同样要进 `<option>`。
    """
    note = "**没有配置**（后端真发了星号）"
    row = dict(_rank_row(RANK_KEY_NO_CONFIG), note=note)
    said = fmr.rank_row_say(row)
    assert note in said, "后端那一格被改写了（这一层不许动它）：%r" % said
    #: 而**我们自己写的话**里仍然不许有标记 —— 两半规矩互不干扰。
    assert "**" not in said.replace(note, ""), said
    weird = dict(_rank_row(RANK_KEY_CLEAN), config_status="**archived**")
    other = fmr.rank_row_say(weird)
    assert "**archived**" in other, "认不出的那格被吞了/改了：%r" % other


# ═══════ Task B1 ⑤：一份 JSON 配置 —— 读（`formConfig`）与写（`formConfig/update`）═══════
#
# 这一份钉两件事，按重要性排：
#
# 1. ★★ **判据在 body 的 `status` 上，不在 HTTP 码上。** 这一族**一律 HTTP 200 +
#    业务码写在 body 里**。【我量的·B1】不带 token 打
#    `POST https://fmr.3tkj.cn/api/quest/formConfig/update` →
#    **HTTP 200** + `{"status":401,"msg":"unauthorized","data":[]}`。
#    案底（`ad-task.py` 的 `_fail_diag_verdict()` 那段注释）：按 HTTP 码判的那版
#    failDiag 客户端把这条读成了成功、打了一行「已上传」，而日志一条都没进库。
#    ⇒ 下面第 8 / 12 / 13 条钉的就是这个：**HTTP 200 不算成功**、**HTTP 500 不算「没写」**。
#
# 2. ★ **「查不到」「后端挂了」「不知道成没成」是三件事**，不许合成一句：
#    读那一侧前两种分得开（`FmrRefused` vs `FmrUnreachable`）；写那一侧多一种最坏的 ——
#    **超时 / 非 JSON / HTTP≥400 都不许写成「没写进去」**（那份配置可能已经动了）。
#
# ⚠️ 桩打在**传输**那一格（`opener` / `poster`），**不是**打在判据上 ⇒
#    `business_status` / 那几个 `say` / 那两个函数**都是真跑的**。
#    （今天在这个仓库里抓出过「桩把被测代码一起桩掉了 ⇒ 断言什么都没钉」的假钉子。）

#: 【我量的·B1】线上读回来的那一份 —— `GET https://fmr.3tkj.cn/api/quest/formConfig?site=cvrefresh.com`
#: 的 `data.steps`，**逐字**（5 个动作、`success` 那一格都在）。这一份里**只有这一格是真的**。
MEASURED_CONFIG = {
    "form_type": "magic_link",
    "site": "cvrefresh.com",
    "steps": [
        {"action": "wait", "max": 10, "min": 5},
        {"action": "click", "find": {"text": "Refresh my resume"}},
        {"action": "form", "field": {"label": "Email", "placeholder": "your@email.com",
                                     "type": "email"}, "value": "{{random.email}}"},
        {"action": "click", "find": {"text": "Send magic link"}},
        {"action": "wait", "max": 10, "min": 5},
    ],
    "success": {"any": [{"body_contains": ["Check your email"]}]},
}
#: 【我量的·B1】同一个响应里**包着它的那一层**（`data` 本身）—— 读错层就是把它当配置。
MEASURED_WRAPPER = {"site": "cvrefresh.com", "steps": MEASURED_CONFIG}
#: 【我量的·B1】那个响应的整个信封。
MEASURED_CONFIG_BODY = {"status": 200, "msg": "success", "data": MEASURED_WRAPPER}
#: 【我量的·B1】查不到时的那一个（`?...site=definitely-not-a-site-b1probe.example`）——
#: **HTTP 也是 200**。
MEASURED_NOT_FOUND_BODY = {"status": 404, "msg": "config not found", "data": []}
#: 【我量的·B1】不带 token 写那一次的回执 —— **HTTP 200** + 这一份。
MEASURED_WRITE_401_BODY = {"status": 401, "msg": "unauthorized", "data": []}
#: 【转述的·出处 `task-b1-brief.md` §1】—— 这一句**我没量过**（量它要真写生产配置，
#: brief 与 plan §7 都点名不许）。它只在「后端拒了」那一支当输入用。
MEASURED_WRITE_400_BODY = {"status": 400, "msg": "参数错误: site 与 steps 必填"}


class PostRecorder:
    """一个记下「发了什么」的桩 `poster`（协议比 `Recorder` 多一格：正文）。"""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.urls = []
        self.headers = []
        self.sent = []

    def __call__(self, url, headers, body):
        self.urls.append(url)
        self.headers.append(dict(headers or {}))
        self.sent.append(body)
        if not self.bodies:
            raise AssertionError("桩没给这个请求准备响应：%s" % url)
        out = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


def writer(rec, token=FAKE_TOKEN, **kw):
    """一个**只**注入了 `poster` 的客户端（读那一侧的口没接 —— 写回不许碰它）。"""
    return fmr.FmrClient(token=token, poster=rec, **kw)


# ─────────────────────── 读：这个站的 JSON 配置 ───────────────────────


def test_the_config_query_is_the_one_that_was_measured():
    """请求的形状（路径 + 参数 + 头）+ **交回来的是里面那一份**。

    ★ 这一条同时钉「读回来的 = 能写回去的」：交出去的是 `data.steps`**里面**那份配置，
    **不是**包着它的信封 —— 读错层是这一族踩过的坑（`ad-task.py:2157`）。
    """
    rec = Recorder(MEASURED_CONFIG_BODY)
    got = client(rec).form_config("cvrefresh.com")
    assert got == MEASURED_CONFIG, got
    assert got != MEASURED_WRAPPER, "把包装层当成配置交出去了：%r" % got
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formConfig", path
    assert params["site"] == "cvrefresh.com", params
    #: token 走请求头（与四个读口同一条纪律）。
    assert rec.headers[0].get("X-Api-Token") == FAKE_TOKEN, rec.headers[0]
    assert FAKE_TOKEN not in rec.urls[0], "token 进了 URL：%r" % rec.urls[0]


def test_the_config_read_works_without_a_token_because_the_backend_does_not_ask_for_one():
    """★ 这个口**公开、无鉴权**（【我量的·B1】不带任何头也回 200 + 整份配置）。

    为什么单钉：四个老读口**没配 token 就抛**（`FmrNoToken`）—— 那个行为是对的
    （它们要鉴权），但**照抄到这一个口上就是错的**：没配 token 的部署会读不动一份
    本来就公开的配置。（今天这台机器就没配 —— `FMR_AGENT_TOKEN` / `FARMER_API_TOKEN`
    在环境里都是空的。）
    """
    rec = Recorder(MEASURED_CONFIG_BODY)
    got = fmr.FmrClient(token="", opener=rec).form_config("cvrefresh.com")
    assert got == MEASURED_CONFIG, got
    assert "X-Api-Token" not in rec.headers[0], (
        "没配 token 还是发了个头出去：%r" % rec.headers[0])
    #: 正控：配了的时候**照旧发**（上面那条不许靠「永远不发头」满足）。
    rec2 = Recorder(MEASURED_CONFIG_BODY)
    client(rec2).form_config("cvrefresh.com")
    assert rec2.headers[0].get("X-Api-Token") == FAKE_TOKEN, rec2.headers[0]


def test_a_site_without_a_config_is_a_refusal_not_an_unreachable():
    """★★ 「这个站没有 JSON 配置」与「后端挂了」是**两件事**（处置完全不同）。

    ⚠️ 这一条量的是**两个**输入（少一个就成了「只量了一个形状」）：
    · 后端回 404（**HTTP 200**）→ `FmrRefused`、`kind="refused"`、`status=404`；
    · 连不上 / 超时 → `FmrUnreachable`、`kind="unreachable"`。
    ⇒ 断言里必须出现「**两个类型不一样**」+「两句人话不一样」——
    合成一句话的坏处就是本仓那个老病：**一个查不到的站被标成健康的**。
    """
    with pytest.raises(fmr.FmrRefused) as e404:
        client(Recorder(MEASURED_NOT_FOUND_BODY)).form_config("cvrefresh.com")
    refused = e404.value
    assert refused.kind == "refused", refused.kind
    assert refused.status == 404, refused.status
    assert "没有 JSON 配置" in refused.say, refused.say
    assert "config not found" in refused.say, "后端原话没带上：%r" % refused.say
    #: ⚠️ 这一句**不许**说成「那串名字给错了」——那是把两种可能说成了唯一一种。
    assert "分不出" in refused.say, refused.say

    with pytest.raises(fmr.FmrUnmeasured) as e_down:
        client(Recorder(TimeoutError("timed out"))).form_config("cvrefresh.com")
    down = e_down.value
    assert isinstance(down, fmr.FmrUnreachable), type(down).__name__
    assert down.kind == "unreachable", down.kind
    assert type(down) is not type(refused), "两种情形落到了同一个类型上"
    assert "连不上" in down.say, down.say
    assert "没有 JSON 配置" not in down.say, down.say
    assert fmr.UNMEASURED_SAY in refused.say and fmr.UNMEASURED_SAY in down.say


def test_a_config_read_that_came_back_as_something_other_than_json_is_unmeasured():
    """正文不是 JSON → **抛**（不是「这个站没有配置」）。名词要是它自己的那一个。"""
    with pytest.raises(fmr.FmrUnreachable) as e:
        client(Recorder("<html>502 Bad Gateway</html>")).form_config("cvrefresh.com")
    said = str(e.value)
    assert "不是 JSON" in said, said
    assert "读不了这份 JSON 配置" in said, said
    assert "读不了失败记录" not in said, "顶着别处的名字：%r" % said


def test_a_config_answer_that_lost_its_inner_config_is_unmeasured_not_an_empty_config():
    """信封对得上、可 `data.steps` **不是一个对象** → **抛**（不许读成「配置是空的」）。

    两个输入（`steps` 整格不在 / `steps` 是个数组 —— 后者正是「读错层」反着来的那种）。
    ⚠️ 正控在最后：**真那份形状照旧交得出来**（否则「什么都抛」也能满足上面两条）。
    """
    for bad in ({"site": "cvrefresh.com"}, {"site": "cvrefresh.com", "steps": []}):
        with pytest.raises(fmr.FmrUnreachable) as e:
            client(Recorder(envelope(bad))).form_config("cvrefresh.com")
        said = str(e.value)
        assert "不是一个对象" in said, (bad, said)
        assert fmr.UNMEASURED_SAY in said, said
    assert client(Recorder(MEASURED_CONFIG_BODY)).form_config(
        "cvrefresh.com") == MEASURED_CONFIG


def test_a_config_read_without_a_site_is_refused_for_free():
    """没说哪个站 → 门口就响（**一个请求都没发出去**）。"""
    rec = Recorder(MEASURED_CONFIG_BODY)
    with pytest.raises(fmr.FmrUnmeasured) as e:
        client(rec).form_config("   ")
    assert e.value.kind == "no-site", e.value.kind
    assert rec.urls == [], "免费的那道闸没挡住：%r" % rec.urls


# ─────────────────────── 写：把一份配置写回去 ───────────────────────


def test_the_write_goes_to_the_update_route_with_the_token_and_the_whole_config():
    """请求的形状：**路由 + 头里那串 token + 正文里整份配置**（D4：少带一个键就是弄丢）。"""
    rec = PostRecorder({"status": 200, "msg": "success", "data": []})
    got = writer(rec).update_form_config("cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is True, got
    assert rec.urls[0] == "https://fmr.3tkj.cn/api/quest/formConfig/update", rec.urls[0]
    assert rec.headers[0].get("X-Api-Token") == FAKE_TOKEN, rec.headers[0]
    assert rec.headers[0].get("Content-Type") == "application/json", rec.headers[0]
    assert FAKE_TOKEN not in rec.urls[0], "token 进了 URL：%r" % rec.urls[0]
    sent = json.loads(rec.sent[0].decode("utf-8"))
    assert sent["site"] == "cvrefresh.com", sent
    #: ★ **整份**：`form_type` / `site` / `steps[]` / `success` 四个键一个都不能少。
    assert sent["steps"] == MEASURED_CONFIG, sent["steps"]
    assert set(sent["steps"]) == {"form_type", "site", "steps", "success"}, sent["steps"]


def test_a_200_http_answer_with_status_401_is_not_success():
    """★★ **案底那一条**：HTTP 200 + body `status:401` —— 判据在 body 上，不是 HTTP 码。

    【我量的·B1】不带 token 写那一次回来的就是这一份（HTTP 200）。
    按 HTTP 码判的客户端把这一条读成了成功、打了一行「已上传」——
    ⇒ 这里量三下：`ok` 不为真、`verdict` 是 `auth`、人话里**不许**出现成功那半句。
    ⚠️ 正控：body 说 200 时 `ok` 必须为真（否则「永远不 ok」也能满足上面）。
    """
    got = writer(PostRecorder(MEASURED_WRITE_401_BODY)).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_AUTH, got
    assert got.status == 401, got
    assert "鉴权没过" in got.say, got.say
    assert "unauthorized" in got.say, "后端原话没带上：%r" % got.say
    assert "写回去了" not in got.say, "鉴权失败被打成了成功：%r" % got.say
    ok = writer(PostRecorder({"status": 200, "msg": "success", "data": []})).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert ok.ok is True and ok.verdict == fmr.WRITE_OK, ok


def test_a_write_the_backend_refused_is_a_value_not_an_exception():
    """后端说这请求不对（400）→ **回值、不抛**（brief §2 R3），人话带它自己那句。"""
    got = writer(PostRecorder(MEASURED_WRITE_400_BODY)).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_REFUSED, got
    assert got.status == 400, got
    assert got.msg == "参数错误: site 与 steps 必填", got.msg
    assert "参数错误: site 与 steps 必填" in got.say, got.say


def test_a_write_that_timed_out_is_unknown_not_a_failure():
    """★★ **「不知道成没成」不许写成「没写进去」。**

    超时的那一刻，那份配置**可能已经动了** —— 说成「写失败」会让人以为没事、
    或者照着它去重发。这一条量三下：`verdict` 是 `unknown`、人话里有「不知道写没写成」、
    而且**没有**任何一句「配置没被改动」（那是确定没写时才敢说的话）。
    """
    got = writer(PostRecorder(TimeoutError("timed out"))).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_UNKNOWN, got
    assert got.status is None, got
    assert "不知道写没写成" in got.say, got.say
    assert "没有被改动" not in got.say, "把「不知道」说成了「没写」：%r" % got.say
    assert "TimeoutError" in got.say, "真因没带上：%r" % got.say


def test_a_write_whose_body_is_not_json_is_unknown_too():
    """正文不是 JSON → **也是「不知道」**（brief §2 R3 那句「非 JSON body 当失败」：
    它**一定不是成功**；而它也**不是**「没写进去」—— 回执没读成而已）。"""
    got = writer(PostRecorder("<html>502 Bad Gateway</html>")).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_UNKNOWN, got
    assert "不知道写没写成" in got.say, got.say
    assert "不是 JSON" in got.say, got.say
    assert "没有被改动" not in got.say, got.say


def test_an_http_500_on_the_write_is_unknown_and_says_why():
    """HTTP ≥400（网关那一层回的，**不是**这个接口的话）→ 照旧要处理，且**不许当成功**。"""
    import urllib.error

    def boom(url, headers, body):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)

    got = fmr.FmrClient(token=FAKE_TOKEN, poster=boom).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_UNKNOWN, got
    assert "HTTP 500" in got.say, got.say
    assert "不知道写没写成" in got.say, got.say
    assert "没有被改动" not in got.say, got.say


def test_an_http_401_on_the_write_is_auth_and_says_the_config_was_not_touched():
    """网关层按 **HTTP** 回 401/403（它不看 body）→ 同样算鉴权失败，且**确定没写**。

    出处：`ad-task.py` 那两条路「网关 / 代理层仍可能给 4xx5xx（那一层不看 body）——
    两条路都要走」。
    """
    import urllib.error

    def boom(url, headers, body):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    got = fmr.FmrClient(token=FAKE_TOKEN, poster=boom).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.verdict == fmr.WRITE_AUTH, got
    assert got.ok is False, got
    assert "鉴权没过" in got.say and "401" in got.say, got.say
    assert "没有被改动" in got.say, got.say


def test_a_write_refuses_a_wrapper_for_free():
    """★ 传了**包装层**（`data` 那一层）→ 门口就抛，**一个请求都不发**。

    为什么这条值得单钉：原样写回去会把后端那份配置的 `steps` 变成一个对象
    —— 这就是「读错层」那个坑的写回版本。它是**免费的**检查：不花一次真写。
    """
    rec = PostRecorder({"status": 200, "msg": "success", "data": []})
    with pytest.raises(fmr.FmrUnmeasured) as e:
        writer(rec).update_form_config("cvrefresh.com", MEASURED_WRAPPER)
    assert e.value.kind == "wrong-layer", e.value.kind
    assert "包装层" in e.value.say, e.value.say
    assert rec.urls == [], "免费的那道闸没挡住，真发出去了：%r" % rec.urls


def test_a_write_refuses_a_missing_or_empty_config_for_free():
    """`steps` 不是一份配置（缺了 / 空的 / 是串文本）→ 门口就响，一个请求都不发。"""
    for bad in (None, {}, "", "{\"form_type\": \"x\"}", []):
        rec = PostRecorder({"status": 200, "msg": "success", "data": []})
        with pytest.raises(fmr.FmrUnmeasured) as e:
            writer(rec).update_form_config("cvrefresh.com", bad)
        assert e.value.kind == "no-steps", (bad, e.value.kind)
        assert rec.urls == [], (bad, rec.urls)
    rec = PostRecorder({"status": 200, "msg": "success", "data": []})
    with pytest.raises(fmr.FmrUnmeasured) as e:
        writer(rec).update_form_config("  ", MEASURED_CONFIG)
    assert e.value.kind == "no-site", e.value.kind
    assert rec.urls == [], rec.urls


def test_a_deployment_without_a_token_does_not_write_and_says_so():
    """没配 token → **一个字都没写**（一个请求都没发出去），而且这是**回值**不是抛。

    ⚠️ 与读那个口（`fetch_*` 没配就抛）不同是有意的：读是「量不到」，
    写是「确定没写」—— 前者要人去查后端，后者只要人去配那个环境变量。
    """
    rec = PostRecorder({"status": 200, "msg": "success", "data": []})
    got = fmr.FmrClient(token="", poster=rec).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert got.ok is False, got
    assert got.verdict == fmr.WRITE_NO_TOKEN, got
    assert fmr.TOKEN_ENV in got.say, got.say
    assert "一个字都没写" in got.say and "没有被改动" in got.say, got.say
    assert rec.urls == [], "没配 token 还发出去了：%r" % rec.urls


def test_the_read_result_round_trips_straight_into_the_write():
    """★★ 读回来的那一份**原样喂给写**，出去的就是后端原来那份（没有改动）。

    这条把「读错层」按死在一个性质上：**两个口收发的必须是同一个东西**。
    真拿它跑一趟往返（读一次、写一次），断言写出去的正文里那份配置**逐字等于**
    读回来的那一份 —— 中间没有被拆开重装过。
    """
    read_rec = Recorder(MEASURED_CONFIG_BODY)
    cfg = client(read_rec).form_config("cvrefresh.com")
    write_rec = PostRecorder({"status": 200, "msg": "success", "data": []})
    got = writer(write_rec).update_form_config("cvrefresh.com", cfg)
    assert got.ok is True, got
    assert json.loads(write_rec.sent[0].decode("utf-8"))["steps"] == MEASURED_CONFIG


def test_a_business_status_that_is_not_a_number_is_not_success():
    """`status` 那一格读不出业务码 → **不知道**（不是成功、也不是「没写」）。

    三种（照 `ad-task.py` 的 `_fail_diag_verdict()` 那两条来）：
    · `{"status": true}` —— Python 里 `True == 1`，不挡它就会读成别的什么东西；
    · `{"status": "401"}` —— **字符串数字也算**（原话：「别指望服务端一定发数字」）；
    · `{"msg": "success"}`（整格不在）—— 没有码，就是没读到答复。
    """
    unknown = writer(PostRecorder({"status": True, "msg": "?", "data": []})).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert unknown.ok is False and unknown.verdict == fmr.WRITE_UNKNOWN, unknown
    missing = writer(PostRecorder({"msg": "success", "data": []})).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert missing.ok is False and missing.verdict == fmr.WRITE_UNKNOWN, missing
    assert missing.status is None, missing
    #: 字符串那个码**照旧按它判**（它是 401 ⇒ 鉴权失败，不是 unknown）。
    stringy = writer(PostRecorder({"status": "401", "msg": "unauthorized",
                                   "data": []})).update_form_config(
        "cvrefresh.com", MEASURED_CONFIG)
    assert stringy.verdict == fmr.WRITE_AUTH and stringy.status == 401, stringy


def test_every_write_verdict_is_one_of_the_five_and_ok_is_only_the_first():
    """五态是个**封闭**的集合（写死的表），`ok` **只**在 `ok` 那一态为真。

    为什么单钉：面板会拿 `verdict` 分流（哪一条要人配 token、哪一条要人去核对后端），
    多出一个没人认得的态就会掉进某个 `else` —— 而那种分支正是「静默路径」的入口。
    """
    assert len(set(fmr.WRITE_VERDICTS)) == 5, fmr.WRITE_VERDICTS
    for verdict in fmr.WRITE_VERDICTS:
        got = fmr.FormWriteResult(verdict=verdict, status=None, msg="", say="x")
        assert got.ok is (verdict == fmr.WRITE_OK), verdict
    assert fmr.FormWriteResult(verdict=fmr.WRITE_OK, status=200, msg="success",
                               say="x").as_dict()["ok"] is True


def test_the_two_config_ports_walk_the_one_base_knob():
    """★ 那两个口**也**走 `FMR_BASE_URL` 那一个旋钮（读与写各量一次）。"""
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv(fmr.BASE_ENV, "http://192.168.1.51:6060")
        read_rec = Recorder(MEASURED_CONFIG_BODY)
        write_rec = PostRecorder({"status": 200, "msg": "success", "data": []})
        got = fmr.FmrClient(token=FAKE_TOKEN, opener=read_rec, poster=write_rec)
        got.form_config("cvrefresh.com")
        got.update_form_config("cvrefresh.com", MEASURED_CONFIG)
        assert read_rec.urls[0].startswith("http://192.168.1.51:6060/api/quest/"), read_rec.urls
        assert write_rec.urls[0].startswith("http://192.168.1.51:6060/api/quest/"), write_rec.urls
    finally:
        monkey.undo()


def test_the_config_ports_time_out_sooner_than_the_other_reads():
    """★ brief §2 R4「超时要短」：这两个口比别的读口短，且**绑在真的传输上**。

    量两下：常量本身的关系；以及那两个默认传输**确实**带着这个值
    （光有个常量、传输没绑上就是「名字说 A、量的是 B」）。
    """
    assert fmr.CONFIG_TIMEOUT < fmr.DEFAULT_TIMEOUT, (
        fmr.CONFIG_TIMEOUT, fmr.DEFAULT_TIMEOUT)
    plain = fmr.FmrClient(token=FAKE_TOKEN)
    assert plain._config_opener.keywords["timeout"] == fmr.CONFIG_TIMEOUT, plain._config_opener
    assert plain._poster.keywords["timeout"] == fmr.CONFIG_TIMEOUT, plain._poster
    assert plain._opener is fmr._default_get, "老四个读口那个传输被动了"
