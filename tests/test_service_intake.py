"""Task 8 补丁 A：**入口那道消毒** —— 「外面来的字」不许把运营那一屏打挂。

## 病（复审 2026-09-19 在 `07fcbd7` 基线上实测，不是猜的）

一个**孤立代理对**（`"\\ud800"`）过得了 `json.dumps`、**过不了 `/live` 最后那次
`.encode("utf-8")`** ⇒ `/live` **500**，而后果不是「那一条坏掉」，是
**整条时间线一条都读不出来**（运营那一屏整个挂掉）。

`/say` 的文本**按定义就是「外面来的字」**（运营在输入框里打的/粘的）—— 而它没走消毒。
`events.safe_value` 的注释里那句原话说的正是这一类：「这些字节**来自外面**」。

## 这一份钉的是**那一类**，不是被点名的那一处（这一片的病史：六轮「修了被点名的那处」）

三件事，一条一条对着拍板的那三条：

| 判据 | 用例 |
|---|---|
| 载荷里的坏字节**换掉**（按 `\\ufffd` 记），**好字节一个不少** | `…replaces_it_and_counts_it…` |
| **换掉要说出来**（没有静默的路径）—— 响应那句人话 + 时间线那条都报个数 | 同上 |
| **这一类**：`/run` 各字段 / `/reply` 的 `note` / `/reopen` 的 `ws_url` / `/say` 的 `text` —— 一个都不许 500，而且**之后 `/live` 读得出来** | `…cannot_take_the_console_down` / `…rejected_field…` |
| **机器守**：`agent/service.py` 里**每一个载荷模型、每一个字段**都消毒（新加一个字段/一个模型，这条自己会红） | `…every_payload_model_and_field…` |

⚠️ 这一份**不开浏览器、不打真模型**（Global Constraints）：`graph_factory` 桩图 + `TestClient`。
⚠️ `agent/console.html` 一个字节都不动；`/job/{id}` 的既有形状不动（新增的字都进人话/
时间线，没有加过任何一格）。

## 量具说明：为什么 JSON 体要**自己拼字节**

`httpx` 的 `json=` 参数在**客户端**就 `UnicodeEncodeError`（`ensure_ascii=False` 编不出
孤立代理对）—— 那量到的是量具自己坏了，不是线上。所以载荷一律走
`json.dumps(obj)`（`ensure_ascii=True`，坏码位以 `\\ud800` 的形状上线，
**这正是浏览器 `JSON.stringify` 会发出来的那串字节**）+ `.encode("ascii")`。
"""

from __future__ import annotations

import json
import pathlib
import sys
import typing

import pytest
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402
from test_service_input import (GOAL, SITE, SUCCESS, URL, WS_URL, FakeGraph,  # noqa: E402
                                StubWindow, _Snap, _brief, _client, _factory, _gate, _live,
                                _wait)

#: 「外面来的字」里那个**线上写不出来的**码位：孤立代理对（半个代理对）。
BAD = "\ud800"
#: 它被换成什么（`events._UNWRITABLE`）。**写死这个码位本身**：换成 `?` 会让人以为
#: 页面上本来就写着一个问号（`events.py` 里那句话）。
SAFE = "�"


def _raw(client, method: str, path: str, obj):
    """把 JSON 体**自己拼**成字节发出去（理由见模块 docstring）。"""
    body = json.dumps(obj).encode("ascii")
    return client.request(method, path, content=body,
                          headers={"content-type": "application/json"})


def _gateful(tmp_path, **over):
    """一个停在闸上的 job（`/live` `/say` `/reply` `/run` 那一档都读得回来）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True),
                     raise_server_exceptions=False)
    r = _raw(client, "POST", "/run", _brief(tmp_path, **over))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    _wait(client, job_id)
    return client, job_id


def _has_lone_surrogate(value) -> bool:
    """这个值里还有没有**线上写不出来**的码位（哪一层都算）。"""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(_has_lone_surrogate(k) or _has_lone_surrogate(v)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_lone_surrogate(v) for v in value)
    return False


# ═══════════════════ ① 被点名的那一处：`/say` 的文本 ═══════════════════


def test_a_lone_surrogate_in_say_no_longer_takes_the_console_down(tmp_path):
    """`/say` 收下一个带孤立代理对的句子：**收下**（202）、那一屏**照旧读得出来**。

    基线实测（复审 2026-09-19）：同一报文 ⇒ `/say` **500**，**并且 `/live` 一起挂**
    —— 运营那一屏整条时间线一条都读不出来。这一条钉的就是那两个 500 都不许再有。
    """
    client, job_id = _gateful(tmp_path)
    r = _raw(client, "POST", "/job/%s/say" % job_id,
             {"text": "先点同意" + BAD + "那个按钮"})
    assert r.status_code == 202, (
        "带坏字节的报文把 `/say` 打挂了（基线就是这个 500）：%s" % r.text[:300])

    live = client.get("/job/%s/live" % job_id)
    assert live.status_code == 200, (
        "**运营那一屏读不出来** —— 这是这条线真正的代价（不是那一条事件坏掉）：%s"
        % live.text[:300])


def test_a_lone_surrogate_is_replaced_and_the_count_is_said_out(tmp_path):
    """换掉是有损的 ⇒ **要说出来**（Global Constraints：没有静默的路径）。

    三样一起量：① 好字节一个不少、坏字节变成 `�`；② 响应那句人话报个数；
    ③ 时间线上那条（`human_said`）报同一个数。
    """
    client, job_id = _gateful(tmp_path)
    mixed = "先点同意" + BAD + "那个按钮"
    sentence = service.UNWRITABLE_BYTES_SAY % 1
    r = _raw(client, "POST", "/job/%s/say" % job_id, {"text": mixed})
    assert r.status_code == 202, r.text[:300]
    assert sentence in r.json()["say"], (
        "换掉了字节却不说 —— 那就是静默：%r" % r.json()["say"])

    live = _live(client, job_id)
    assert [x["text"] for x in live["input"]["queued"]] == ["先点同意" + SAFE + "那个按钮"], (
        "队里那一条应当是换过的那句（好字节一个不少、坏字节成了 `�`）：%r"
        % live["input"]["queued"])
    said = [e for e in live["events"] if e["kind"] == "human_said"][-1]
    assert said["data"]["text"] == "先点同意" + SAFE + "那个按钮", said
    assert sentence in said["say"], (
        "时间线上那条也得说（那一屏才是运营看的东西）：%r" % said["say"])


# ═══════════════════ ② 这一类：所有载荷入口 ═══════════════════


def test_no_payload_entry_can_take_the_console_down(tmp_path):
    """**这一类**：载荷里进来的字，一个都不许让服务自己接不住。

    每一行量三件事：① 那个请求**不许 500**；② 之后 `/live` 与 `/job/{id}` **读得出来**；
    ③ 返回的字节里**不许**再留着那个孤立代理对（换了却说没换 = 假话）。
    """
    cases = [
        ("/run url", "url", "https://example-funnel.test/" + BAD),
        ("/run goal", "goal", GOAL + BAD),
        ("/run success_text", "success_text", SUCCESS + BAD),
        ("/run evidence", "evidence", "formLog #12" + BAD),
        ("/run site", "site", SITE + BAD),
        ("/run ws_url", "ws_url", WS_URL + BAD),
        ("/run form_file", "form_file", "/tmp/form" + BAD + ".json"),
        ("/run out_dir", "out_dir", "/tmp/out" + BAD),
        ("/run entry_url", "entry_url", URL + BAD),
        ("/run hints", "hints", ["人说过的话" + BAD]),
        ("/run expects", "expects", [{"text_appears": SUCCESS + BAD}]),
        ("/run env", "env", {"ua": "Mozilla" + BAD}),
        ("/run platform", "platform", {"guess": "datewhirl" + BAD}),
    ]
    for label, field, value in cases:
        tmp_path.mkdir(exist_ok=True)
        client, job_id = _gateful(tmp_path, **{field: value})
        live = client.get("/job/%s/live" % job_id)
        assert live.status_code == 200, (
            "%s：`/live` 读不出来（载荷里那个坏字节漏到线上去了）" % label)
        one = client.get("/job/%s" % job_id)
        assert one.status_code == 200, "%s：`/job/{id}` 也读不出来" % label
        for path in ("/job/%s" % job_id, "/job/%s/live" % job_id, "/runs"):
            body = client.get(path).json()
            assert not _has_lone_surrogate(body), (
                "%s：`%s` 的正文里还留着线上的坏字节（换了却没说 / 根本没换）" % (label, path))
        if field in service.RunRequest.SANITISED_AT_ITS_OWN_SEAM:
            continue        # `expects` 那一路**故意不在入口换**（它自己那两个出口各自报数）
        submitted = [e for e in live.json()["events"] if e["kind"] == "submitted"]
        assert submitted and (service.UNWRITABLE_BYTES_SAY % 1) in submitted[-1]["say"], (
            "%s：换了字节却不说个数（有损必须说）—— 运营读的那一句在 `submitted` 上：%r"
            % (label, submitted[-1]["say"] if submitted else None))


def test_a_bad_byte_in_a_rejected_field_still_gives_a_readable_400(tmp_path):
    """**400 的正文自己也要能上线**（复审在基线上量到的另一格：这一条是 **500**）。

    `allow_skips` 里那个不认识的遍会被**原样引用**进报错句
    （「`allow_skips` 里有不认识的遍：%s」）—— 坏字节因此从**报错正文**里漏出去，
    于是「本该是一次读得懂的 400」变成一次 500。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g), raise_server_exceptions=False)
    r = _raw(client, "POST", "/run", _brief(tmp_path, allow_skips=[BAD]))
    assert r.status_code == 400, (
        "本该是「那几遍我不认识」这条 400（基线是 500 —— 报错正文自己编码不了）：%s"
        % r.text[:300])
    detail = r.json()["detail"]
    assert "不认识的遍" in detail and SAFE in detail, (
        "拒了它，也得**说得出是哪一个**（换过的那份）：%r" % detail)


def test_a_lone_surrogate_in_reply_still_goes_through(tmp_path):
    """`/reply` 的 `note` 是**人打的字**（同样是外面来的）—— 换掉、报数、照常送下去。"""
    client, job_id = _gateful(tmp_path)
    g = client.app.state.service._jobs[job_id].graph
    r = _raw(client, "POST", "/job/%s/reply" % job_id,
             {"action": "revise", "note": "不是那个按钮，是下面" + BAD + "那个"})
    assert r.status_code == 200, r.text[:300]
    assert client.get("/job/%s/live" % job_id).status_code == 200, "那一屏读不出来"
    said = [e for e in _live(client, job_id)["events"] if e["kind"] == "human_said"][-1]
    assert said["data"]["note"] == "不是那个按钮，是下面" + SAFE + "那个", said
    assert (service.UNWRITABLE_BYTES_SAY % 1) in said["say"], (
        "换掉了就要说：%r" % said["say"])
    # 送下去的那一份也必须是换过的（不然它就漏进图的状态、再漏回 `/live` 的闸口那一格）
    payload = [p for p in g.invokes if isinstance(p, dict)]
    assert payload, "那一句没送下去（`/reply` 的正事没做）"
    assert not _has_lone_surrogate(payload[-1]), payload[-1]


def test_a_lone_surrogate_in_reopen_still_goes_through(tmp_path):
    """`/reopen` 的 `ws_url`（bit.sh 吐的那串，同样是外面来的）—— 换掉、报数、照常接着跑。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g), raise_server_exceptions=False)
    svc = client.app.state.service
    job = service.Job(job_id="job-reopen", brief=dict(_brief(tmp_path)),
                      status=service.FAILED, created_at="2026-09-19T00:00:00+08:00")
    # 「炸在 `explore` 那一跳」= 最后落下的那一步是 `intake`（`_resume_point` 的判据）——
    # 桩图的那一格得**先摆成**这样（它的 `steps` 要 `invoke` 过才生效，而这里不推图）
    snap = _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]})
    job.graph = FakeGraph(steps=[snap])
    job.graph.state = snap
    svc._jobs["job-reopen"] = job

    r = _raw(client, "POST", "/job/job-reopen/reopen", {"ws_url": WS_URL + BAD})
    assert r.status_code == 200, r.text[:300]
    assert client.get("/job/job-reopen/live").status_code == 200, "那一屏读不出来"
    events = [e for e in _live(client, "job-reopen")["events"] if e["kind"] == "window_reopened"]
    assert events, "「窗口重开了」这一条没记上"
    assert events[-1]["data"]["ws_url"] == WS_URL + SAFE, events[-1]
    assert (service.UNWRITABLE_BYTES_SAY % 1) in events[-1]["say"], (
        "换掉了就要说：%r" % events[-1]["say"])


# ═══════════════════ ③ 机器守：每一个载荷模型、每一个字段 ═══════════════════


def _payload_models() -> list:
    """`agent/service.py` 里定义的**每一个**载荷模型（新加一个，这里自己会多一个）。"""
    out = []
    for name, obj in vars(service).items():
        if not isinstance(obj, type) or not issubclass(obj, BaseModel):
            continue
        if obj is BaseModel or getattr(obj, "__module__", "") != service.__name__:
            continue
        out.append(obj)
    return sorted(out, key=lambda m: m.__name__)


def _bad_value(annotation):
    """给一个标注造一个**带孤立代理对**的值；不是文本形状的返回 `None`（跳过）。"""
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    inner = args[0] if args else annotation
    origin = typing.get_origin(inner) or inner
    if origin is str:
        return BAD
    if origin is list:
        return [BAD]
    if origin is dict:
        return {"k": BAD}
    return None                      # bool / int / 别的：这里不量（它们本来也带不了字节）


def test_every_payload_model_and_every_field_is_sanitised():
    """**这一类**的机器守：模型里**每一个文本字段**都消毒，新加一个也自动在内。

    为什么要有它（这一片的病史：六轮「修了被点名的那一处，没修那一类」）：
    消毒要是挂在**端点里逐个字段手写**，下一个人往 `RunRequest` 加一格就漏一格。
    这条守自己把**每个模型的每个文本字段**都塞一个坏字节进去，量出来的值还写不写得出去
    —— 所以「加了新字段忘了消毒」这件事**当场红**，不靠谁记得。

    ⚠️ **射程照实写**：它量的是**模型那一步**（构造出来的值干不干净），
    不量「端点有没有把换掉的个数说出来」—— 后者每一处的人话都不一样，
    由上面那几条端到端的用例钉（每一处一条）。
    """
    # 底座自己不算「一个载荷模型」（它没有字段 —— 它**就是**那条消毒）
    models = [m for m in _payload_models() if m is not service._Intake]
    assert {m.__name__ for m in models} >= {"RunRequest", "ReplyRequest", "SayRequest",
                                            "ReopenRequest"}, (
        "载荷模型没找全（这条守会变成量空气）：%r" % [m.__name__ for m in models])
    measured, skipped = 0, set()
    for model in models:
        values = {}
        for name, field in model.model_fields.items():
            bad = _bad_value(field.annotation)
            if bad is not None:
                values[name] = bad
        assert values, "%s 一个文本字段都没有？这条守量不到它" % model.__name__
        got = model(**values).model_dump(exclude_none=True)
        for name in values:
            if name in model.SANITISED_AT_ITS_OWN_SEAM:
                skipped.add("%s.%s" % (model.__name__, name))
                continue
            assert not _has_lone_surrogate(got[name]), (
                "%s.%s 没过消毒（把线上的坏字节原样收下了）" % (model.__name__, name))
            measured += 1
    assert measured >= 15, "量到的字段数不对（%d）—— 这条守多半在量空气" % measured
    # ⚠️ **例外只许有一个**（多一个都要在这儿过一遍，见 `_Intake.SANITISED_AT_ITS_OWN_SEAM`
    # 那段：能进那格的唯一条件是「这个字段没有任何直通响应的出口」）——
    # `expects` 那条路自己的两个出口各自消毒+报数，由既有那条端到端用例钉着
    # （`test_service_steps.py::test_a_receipt_with_unwritable_bytes_…`：本条守改动前后都绿）。
    assert skipped == {"RunRequest.expects"}, (
        "入口消毒的例外集变了（%r）—— 加一个例外之前先说清它在**哪个出口**换、在哪报数"
        % (sorted(skipped),))


def test_the_surrogate_is_really_unwritable_so_the_probe_is_not_vacuous():
    """量具自检：`BAD` 确实**过得了 `json.dumps`、过不了 `.encode("utf-8")`**。

    没有这一条，上面那些用例可能只是「拿一个普通字符试了试」——
    而这条线要治的正是**这两个判据分岔**的那一格（`events._facts` 的注释原话）。
    ⚠️ `ensure_ascii=False` 那一格不能省：默认的 `ensure_ascii=True` 会把它转义成
    六个 ASCII 字符（`\\ud800`），于是 `.encode("utf-8")` **过得去** ——
    量到的就不是线上那一层了（`_facts` 与 `/live` 走的都是**不转义**的那一条）。
    """
    assert json.dumps({"t": BAD}, ensure_ascii=False)                 # 过得了
    with pytest.raises(UnicodeEncodeError):
        json.dumps({"t": BAD}, ensure_ascii=False).encode("utf-8")    # 过不了（`/live` 那一步）
