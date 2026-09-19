"""Task 11：**产物交付** —— `GET /job/{id}/artifact` + `/live` 的产物那一格（规格 §十五）。

## 这一份钉的是什么

在这之前，运营拿到产物的**唯一**方式是去文件系统里捞（`out_dir` 是服务端的一个路径，
页面上一个字都没提）—— 而「产出」恰恰是这一屏最该交到人手上的那件事。
§十五 把它补上，这一份钉服务那一半：

| # | 性质 | 为什么值得单独一条 |
|---|---|---|
| 1 | **交付的是 `deliver` 那一步真正写下的那个文件** | 字节**逐字节**相等 —— 不是 JSON、不是空文件、不是「应该会写在哪」 |
| 2 | **没产物 ⇒ 409 + 一句人话**（撞上限 / 窗口没了 / 人喊停 / 跑挂 / 还没跑到） | §15.2：不许摆一个空按钮了事。沉默的失败是本仓一整天在治的病 |
| 3 | ★ **路径只来自这一趟自己的记录**（`state["py_path"]`） | §15.3：**不接受任何路径参数** —— 接受一个就是**任意文件读取**（`_fs` 那几条 + 符号链接那条） |
| 4 | **A15：跑两趟，下到的必须是各自那一趟的** | 「按站点名存一个文件」「端出目录里最新那个」这类实现在这儿悄悄错 |
| 5 | `/live` 那一格与端点是**同一句人话、同一个判据** | 两套判据 = 下一个洞（本片病史：**同一个事实两个名字**）—— 表现出来会是页面说「能下」而端点回 409 |

## 桩（**不开浏览器、不打模型、不碰网**）

`FakeGraph` / `_Snap` / `_wait` 与 `tests/test_service.py` 里那套是**同一形状**（借过来用，
不另造一份）。它按你要的 `end_reason` / `py_path` / `end_note` 造一份终局快照 ——
真图跑一趟要开真浏览器、要真模型，而 Global Constraints 明令不许。
写到盘上的那个 py 是**真文件**（本用例自己造的字节），因为「字节逐字节相等」只有在
真文件上才量得出来。
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import graph, service  # noqa: E402
from agent.state import (END_DELIVERED, END_DRAFT_FAILED, END_HUMAN_STOP,  # noqa: E402
                         END_LINT_CAP, END_WINDOW_GONE)
from test_service import (FakeGraph, Rec, StubWindow, _gate,  # noqa: E402
                          _real_factory, _reply_until_done, _Snap, _wait)
#: `tests/test_service.py` 里那个装好桩的 client（比本文件的 `_client` 多一根
#: 「窗口探针」的线）—— **真图**那一趟要它（第 4 遍扰动要换窗口大小，R-31）。
from test_service import _client as _svc_client  # noqa: E402

SITE = "example-funnel"
OTHER = "other-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"

#: 这一趟「写下的」那份 py 的字节。**每一趟不一样**（A15 要的就是这个）——
#: 里面那行 `SITE = ...` 是能被下下来之后一眼认出来的东西。
PY = ("# %s 的产物（本用例造的字节）\n"
      "PROVENANCE = {'site': '%s'}\n"
      "def run(page):\n    return '%s'\n")


def _py_bytes(site: str) -> bytes:
    return (PY % (site, site, site)).encode("utf-8")


def _brief(tmp_path, **over) -> dict:
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(_out_dir(tmp_path))}
    brief.update(over)
    return {k: v for k, v in brief.items() if v is not None}


def _out_dir(tmp_path) -> pathlib.Path:
    """产物目录（`forms/sites` 那个位置）—— 一律落 `tmp_path`，**不写仓库的 `forms/`**。"""
    return pathlib.Path(tmp_path) / "forms" / "sites"


def _client(tmp_path, factory, **kw) -> TestClient:
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(service.create_app(
        graph_factory=factory, out_dir=str(_out_dir(tmp_path)), **kw))


def _wrote(brief, *, path=None, body=None) -> pathlib.Path:
    """把「这一趟写下的那个文件」落到盘上，返回它的路径。

    `path` 不给就落在 `out_dir/<site>.py` —— 那是**生产那条路**
    （`graph._delivery_path`：交付点就是 `<out_dir>/<site>.py`）。
    """
    real = pathlib.Path(path) if path is not None else (
        pathlib.Path(brief["out_dir"]) / ("%s.py" % brief["site"]))
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(body if body is not None else _py_bytes(brief["site"]))
    return real


def _factory(*, reason=END_DELIVERED, note="写好了：这一版跑通了。", write=True,
             path=None, extra=None, body=None, record_src=True):
    """一张**照着交付那一趟的形状**做的假图：跑一次就结束，快照里记着它写到哪了。

    - `reason`：`end_reason`（`delivered` = 真写下了；其余 = 没写下，各带自己那句人话）；
    - `path`：显式指定**记录里**的那个路径（安全那几条要的就是「记录指向别处」）；
    - `write=False`：文件不落盘（「结局说交付了、文件却不在」那一档）；
    - `body`：这一趟**写下的字节**（同站点跑两趟要两份不一样的）；
    - `record_src=False`：盘上写了、`py_path` 也记了，**但 checkpoint 里没留那串字节** ——
      与真图**不一样**（真图 `out.update({"src": …})` 与 `py_path` 一起记），
      用来演「更早版本跑的 / 记录不全」那一档：那一档服务**没法核对**。
    """
    def factory(brief, deps):
        body_ = body if body is not None else _py_bytes(brief["site"])
        real = _wrote(brief, path=path, body=body_) if write else (
            pathlib.Path(path) if path is not None
            else pathlib.Path(brief["out_dir"]) / ("%s.py" % brief["site"]))
        values = {"site": brief["site"], "visits": ["intake", "deliver"],
                  "end_reason": reason, "end_note": note,
                  "out_dir": brief["out_dir"]}
        if reason == END_DELIVERED:
            # ⚠️ `py_path` **只在交付过的那一趟**才有 —— 与真图一样（`graph._deliver`）。
            values["py_path"] = str(real)
            if write and record_src:
                #: 真图写盘那句是 `path.write_text(src, encoding="utf-8")`，而 `src` 也进
                #: state（`out.update({"src": src})`）—— 所以「这一趟写下的那串字节」
                #: 在 checkpoint 里查得到（指纹就是拿它算的，见 `_run_wrote`）。
                values["src"] = body_.decode("utf-8")
        values.update(extra or {})
        return FakeGraph(steps=[_Snap(values=values)])
    return factory


def _run_to_the_end(client, tmp_path, *, site=SITE, **over):
    """跑一趟（假图：一次就到头）→ `(job_id, 那一份 `/job/{id}` 的正文)`。"""
    r = client.post("/run", json=_brief(tmp_path, site=site, **over))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    return job_id, _wait(client, job_id, until=("done", "failed"))


def _live(client, job_id) -> dict:
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


# ══════════════ 1. 有产物：200 + 那个文件的字节 ══════════════


def test_the_endpoint_serves_the_very_bytes_that_run_wrote(tmp_path):
    """`GET /job/{id}/artifact` 交付的就是 `deliver` 写下的那个文件的**字节**。

    逐字节相等是这条的要点（§15.1 的第 1 条验收）：它不是一段 JSON、
    不是空文件、也不是「应该会写在哪」推出来的一个路径 ——
    `Content-Disposition` 里那个文件名与人下下来看到的东西要对得上。
    """
    client = _client(tmp_path, _factory())
    job_id, view = _run_to_the_end(client, tmp_path)
    assert view["status"] == "done" and view["delivered"] is True, view
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, r.text
    assert r.content == on_disk.read_bytes(), "下下来的字节与盘上那个文件不是一个东西"
    assert r.content == _py_bytes(SITE), r.content[:80]
    assert r.content, "空文件（A13 点名不许的那一种）"
    assert not r.headers["content-type"].startswith("application/json"), \
        "产物那一格是个文件，不是一段 JSON：%r" % r.headers["content-type"]
    assert r.headers["content-disposition"] == 'attachment; filename="%s.py"' % SITE, \
        r.headers["content-disposition"]


def test_the_real_graph_writes_the_file_this_endpoint_hands_over(tmp_path):
    """**真图**（桩依赖）跑一趟到交付：端点交付的就是 `deliver` **真写下的**那个文件。

    上面那几条 FakeGraph 证明的是「判据对」；这一条证明的是**真的接上了** ——
    `state["py_path"]` 与 `state["out_dir"]` 就是真图写下的那两个（不是桩编出来的），
    于是「产物只认这一趟自己记下的路径」这句话在**生产那条形状**上成立。
    （真图 + 桩依赖：探路/自测都是桩 —— 不开浏览器、不打模型。）
    """
    rec = Rec()
    client = _svc_client(graph_factory=_real_factory(rec, tmp_path), window=StubWindow())
    job_id = client.post("/run", json=_brief(tmp_path, set_viewport=True)).json()["job_id"]
    view = _reply_until_done(client, job_id)
    assert view["status"] == "done" and view["delivered"] is True, view

    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    assert on_disk.is_file(), "真图那一趟没有把 py 写在交付点上"
    assert view["result"]["py_path"] == str(on_disk), view["result"]["py_path"]

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, r.text
    assert r.content == on_disk.read_bytes(), "下下来的不是 `deliver` 写下的那些字节"
    assert "PROVENANCE" in r.content.decode("utf-8")
    cell = _live(client, job_id)["artifact"]
    assert cell["path"] == str(on_disk) and cell["url"], cell


def test_a_restarted_service_still_hands_the_artifact_over(tmp_path):
    """**服务重启过**（登记表里没有它、时间线也空了）—— 产物照样拿得到。

    为什么这条值得单列：这一屏上有两样东西的**寿命不一样** —— 时间线活在进程里（一重启就没了，
    `tl-note` 已经明说了），而产物那一格是从 **checkpoint** 投影出来的。
    重启之后页面**只能靠这一格**把文件交到人手上（时间线上那条 `done` 已经不在了）——
    所以它不许依赖进程里的任何东西。
    """
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    factory = _real_factory(Rec(), tmp_path, saver=saver)
    first = _svc_client(graph_factory=factory, window=StubWindow(), checkpointer=saver)
    job_id = first.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert _reply_until_done(first, job_id)["delivered"] is True

    second = _svc_client(graph_factory=factory, window=StubWindow(), checkpointer=saver)
    assert second.app.state.service._recover(job_id) is not None, "捡不回来就不是重启那一档了"
    assert second.app.state.service._jobs[job_id].recovered is True

    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    got = second.get("/job/%s/artifact" % job_id)
    assert got.status_code == 200, got.text
    assert got.content == on_disk.read_bytes()
    cell = _live(second, job_id)["artifact"]
    assert cell["url"] and cell["path"] == str(on_disk), cell


def test_a_delivered_run_says_where_it_wrote_so_a_human_can_check(tmp_path):
    """顺带把**它写到哪了**摆出来（§15.2）—— `/live` 那一格里的 `path` 就是盘上那个文件。

    为什么这条要单独钉：路径是**服务记账**的那一格，页面只负责照抄；
    它要是页面自己拼的（`out_dir` + 站点名），这儿就会对不上。
    """
    client = _client(tmp_path, _factory())
    job_id, _ = _run_to_the_end(client, tmp_path)
    art = _live(client, job_id)["artifact"]

    assert art and art["path"] == str(_out_dir(tmp_path) / ("%s.py" % SITE)), art
    assert art["filename"] == "%s.py" % SITE, art
    assert art["say"], "给运营看的那一格必须有人话"


def test_the_url_the_page_is_given_really_downloads_the_file(tmp_path):
    """**接上了就要响**：`/live.artifact.url` 那个地址，去取真的能拿到那些字节。

    这一条把两半钉在一起（页面拿到的地址 + 端点的行为）—— 只钉一半的话，
    「页面摆了一个下载、点下去 404」是照绿不误的。
    """
    client = _client(tmp_path, _factory())
    job_id, _ = _run_to_the_end(client, tmp_path)
    art = _live(client, job_id)["artifact"]

    assert art["url"], art
    r = client.get(art["url"])
    assert r.status_code == 200, r.text
    assert r.content == _py_bytes(SITE), r.content[:80]


# ══════════════ 2. 没产物：409 + 一句人话（每一档一条）══════════════


@pytest.mark.parametrize("reason,note,phrase", [
    (END_LINT_CAP,
     "停：这一版 py 被打回 2 次还是同样的地方不过（第 3 行 —— 选择器太宽）。",
     "撞上限"),
    (END_WINDOW_GONE,
     "这次探路没走完（探路停下了：窗口没了（工具连着失败，窗口服务说它已经不在了）），"
     "所以**没有**往下写 py。",
     "窗口没了"),
    (END_HUMAN_STOP,
     "人喊停：在「交付」这一步**之前**停下来，这一步没有做，页面与文件都保持原样。",
     "人喊停"),
], ids=["cap", "window-gone", "human-stop"])
def test_no_artifact_is_a_409_that_says_why(tmp_path, reason, note, phrase):
    """**没产物 ⇒ 409 + 一句人话**，而且三种停因各说得出是哪一种（§15.2 / A14）。

    三种停因的原话是**图自己写下的**（`end_note`），这里照抄 —— 撞上限那三种按时间线
    上那条事件**同一条规则**加 `CAP_SAY_PREFIX`（`_note_end`）。
    判据是**这趟跑到头了但没有文件**：不是 404（那会读成「没这个端点」），
    也不是 200 + 空 body（那是 A14 点名不许的「下下来一个空文件」）。
    """
    client = _client(tmp_path, _factory(reason=reason, note=note, write=False))
    job_id, view = _run_to_the_end(client, tmp_path)
    assert view["status"] == "done" and view["delivered"] is False, view

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert phrase in detail, "没说清是**哪一种**停因：%r" % detail
    assert note in detail, "没把这一趟自己留的那句话带上：%r" % detail
    assert "\n" in detail, "正文是两三句人话，不是一行错误码：%r" % detail


def test_a_run_that_died_says_it_died_instead_of_pretending_it_never_wrote(tmp_path):
    """跑挂那趟（`failed`）也是 409，而且**说的是跑挂了**（不是「它到头了」）。"""
    client = _client(tmp_path, lambda brief, deps: FakeGraph(steps=[{}], raise_on=[1]))
    r = client.post("/run", json=_brief(tmp_path))
    job_id = r.json()["job_id"]
    view = _wait(client, job_id, until=("failed", "done"))
    assert view["status"] == "failed", view

    got = client.get("/job/%s/artifact" % job_id)
    assert got.status_code == 409, (got.status_code, got.text)
    detail = got.json()["detail"]
    assert "跑挂" in detail, detail
    assert "窗口连不上" in detail, "跑挂那句原话要留着：%r" % detail


def test_a_run_that_has_not_reached_the_writing_step_says_it_is_still_coming(tmp_path):
    """**还没跑到那一步**（在跑 / 停在闸上）→ 也 409，但说的是「还没有」而不是「没有」。

    两件事不一样：一个是「它还没写到那儿」，一个是「它跑完了但没有」。
    把前一种说成后一种就是编话（人会把还在跑的那一趟当成失败）。
    """
    from langgraph.types import Interrupt
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]},
                               interrupts=(Interrupt(value={"step": "deliver", "say": "要交付吗？",
                                                            "facts": {}, "can": ["让它继续"]},
                                                     id="i-1"),))])
    client = _client(tmp_path, lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert "还没有" in detail or "还没" in detail, detail
    assert "没有产出" not in detail, "它还没跑到那儿，说成「没有产出」就是假话：%r" % detail


def test_a_delivered_run_whose_file_vanished_does_not_claim_it_never_wrote_one(tmp_path):
    """**结局说交付了、可那个文件现在不在盘上** —— 这一档不许说成「没有产出 py」。

    文件是**产出过**的（结局与路径都在记录里），它只是后来没了。
    说成「没有产出」就是一句假话，而人正拿着它去对账。
    """
    client = _client(tmp_path, _factory())
    job_id, view = _run_to_the_end(client, tmp_path)
    assert view["delivered"] is True, view
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    on_disk.unlink()                                   # 有人把它挪走了

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert "没有产出" not in detail, "「产出过、后来没了」被说成了「没有产出」：%r" % detail
    assert str(on_disk) in detail, "要人拿路径去对账，路径得在话里：%r" % detail


# ══════════════ 3. ★ 安全边界：路径只来自这一趟的记录 ══════════════


def test_a_recorded_path_outside_the_products_dir_is_refused(tmp_path):
    """记录里的路径**跑到产物目录外面**了 → 拒绝（§15.3：解析符号链接之后必须落在 `out_dir` 之内）。

    ⚠️ 这条**不是**「反正路径是服务自己记的所以不用查」：记录也会错
    （图改过、checkpoint 被污染、将来的某一步把外面来的东西写进 `py_path`）。
    服务前核对一次是**唯一**拦住「读走 /etc 下任何一个文件」的地方。
    """
    outside = tmp_path / "outside" / "secret.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("TOPSECRET = 1\n", encoding="utf-8")
    client = _client(tmp_path, _factory(path=outside))
    job_id, _ = _run_to_the_end(client, tmp_path)

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert str(outside) in detail, detail
    assert "TOPSECRET" not in r.text, "把产物目录外面的文件内容漏出来了"
    assert "不在产物目录" in detail, detail


def test_a_symlink_out_of_the_products_dir_is_not_served(tmp_path):
    """一个**指向别处**的 `example-funnel.py` 也不给读。

    名字、后缀、目录全对 —— 挡住它的只能是「解析**符号链接**之后还在不在里面」。
    少了这一步，`out_dir/` 里放一个软链就是一个任意文件读取。
    """
    outside = tmp_path / "outside" / "secret.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("TOPSECRET = 1\n", encoding="utf-8")
    link = _out_dir(tmp_path) / ("%s.py" % SITE)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    client = _client(tmp_path, _factory(write=False, path=link))
    job_id, _ = _run_to_the_end(client, tmp_path)
    assert link.is_file(), "桩没造对：这个软链应该指向一个真文件"

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    assert "TOPSECRET" not in r.text, "顺着软链把外面的文件读出来了"
    assert "不在产物目录" in r.json()["detail"], r.json()["detail"]


@pytest.mark.parametrize("query", [
    "?path=/etc/passwd",
    "?path=%2Fetc%2Fpasswd&file=secret.py",
    "?py_path=/etc/passwd&name=../../etc/passwd",
    "?out_dir=/&path=/etc/passwd",
])
def test_the_endpoint_takes_no_path_parameter_at_all(tmp_path, query):
    """★ **不接受任何路径参数**（§15.3）—— 带参数来的请求，服务只当没看见。

    这条路的形状是「**这一趟记了什么就服务什么**」：一旦有一个参数能影响取哪个文件，
    它就是一个任意文件读取。所以：参数**不改变**响应的正文一个字节
    （没有产物时也不因为参数而变成有、有产物时也不会换成参数点名的那个）。
    """
    secret = tmp_path / "outside" / "secret.py"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("TOPSECRET = 1\n", encoding="utf-8")

    # ① 有产物：参数不许把正文换成参数点名的那个文件
    client = _client(tmp_path, _factory())
    job_id, _ = _run_to_the_end(client, tmp_path)
    got = client.get("/job/%s/artifact%s" % (job_id, query))
    assert got.status_code == 200, got.text
    assert got.content == _py_bytes(SITE), "参数改变了取哪个文件：%r" % query

    # ② 没产物：参数不许把它变成一个「有」
    client2 = _client(tmp_path / "second", _factory(reason=END_DRAFT_FAILED,
                                                   note="写不出这一版 py。", write=False))
    job2, _ = _run_to_the_end(client2, tmp_path / "second")
    got2 = client2.get("/job/%s/artifact%s" % (job2, query))
    assert got2.status_code == 409, (got2.status_code, got2.text)
    assert "TOPSECRET" not in got2.text, "参数把外面的文件读出来了：%r" % query


def test_an_unknown_job_is_a_404_with_the_same_words_as_the_other_routes(tmp_path):
    """没这个任务 → 404（**与 `/job/{id}` 那句同一句**：一件事一处口径）。"""
    client = _client(tmp_path, _factory())
    r = client.get("/job/job-nope/artifact")
    assert r.status_code == 404, (r.status_code, r.text)
    assert "没这个任务" in r.json()["detail"], r.json()["detail"]


# ══════════════ 4. A15：产物是**这一趟**的 ══════════════


def test_two_runs_do_not_serve_each_others_artifacts(tmp_path):
    """**A15**：跑两趟，各自下到的必须是**自己那一趟**写下的那个文件。

    ⚠️ 「这条最容易在『按站点名存一个文件』的实现里悄悄错掉」（§15.5）——
    所以这里钉三件事：① 两趟的字节各自对得上；② 第二趟跑完之后，
    **第一趟的地址**给出的还是**第一趟**那份字节（不是「目录里最新那个」）；
    ③ 产物目录里**多出来**一个别人写的 py 时，这一趟的地址不受影响。
    """
    # 第一趟（站点 A）—— 另起一个服务，同一个产物目录
    client = _client(tmp_path, _factory())
    first, _ = _run_to_the_end(client, tmp_path, site=SITE)
    # 「别人写的 py」：与这两趟都无关的一个文件，落在**同一个**目录里
    other_file = _out_dir(tmp_path) / "somebody-else.py"
    other_file.write_bytes(b"# NOT ANY OF THESE RUNS\n")

    # 第二趟（站点 B）：另起一个服务（各自一颗假图），同一个产物目录
    c2 = _client(tmp_path, _factory(body=b"# SECOND RUN ONLY\n"))
    second, _ = _run_to_the_end(c2, tmp_path, site=OTHER)

    a = client.get("/job/%s/artifact" % first)
    b = c2.get("/job/%s/artifact" % second)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.content == _py_bytes(SITE), a.content
    assert b.content == b"# SECOND RUN ONLY\n", b.content
    assert a.content != b.content, "两趟下到的是同一个文件 —— 产物没有跟着「这一趟」走"
    assert a.content != other_file.read_bytes(), "端出了目录里别人写的那个 py"


def test_a_second_run_of_the_same_site_does_not_hand_over_its_bytes_to_the_first(tmp_path):
    """★ **同一个站点跑两趟**：第 1 趟的地址**不许**把第 2 趟的字节交出去（§15.5 / A15）。

    交付点是 `<out_dir>/<site>.py`（**一个站点一个文件**，生产就是 `forms/sites/<site>.py`）——
    所以第 2 趟**必然覆盖**第 1 趟。规格 §15.5 那个括号点名的正是这个形状：
    「这条最容易在『**按站点名存一个文件**』的实现里悄悄错掉」。

    ⇒ 这一条要的是：**指纹对不上就明说**（拿不到），不是「照给一份别人的」。
    两趟的字节**一样长、内容不一样**（`# AAAAA` / `# BBBBB`）—— 只比 size 的实现过不了这一条。
    """
    same_len_a = b"# AAAAA\n"          # 8 字节
    same_len_b = b"# BBBBB\n"          # 8 字节（**等长**：只比 size 的实现在这儿露馅）
    assert len(same_len_a) == len(same_len_b) and same_len_a != same_len_b
    client = _client(tmp_path, _factory(body=same_len_a))
    first, view1 = _run_to_the_end(client, tmp_path, site=SITE)
    assert view1["delivered"] is True, view1

    # 第二趟：**同一个站点**（同一个文件），写下的字节不一样
    client2 = _client(tmp_path, _factory(body=same_len_b))
    second, view2 = _run_to_the_end(client2, tmp_path, site=SITE)
    assert view2["delivered"] is True, view2
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    assert on_disk.read_bytes() == same_len_b, "桩没造对：第二趟应该把那个文件盖掉"

    # ① 第 1 趟：**不许**给第 2 趟的字节
    r1 = client.get("/job/%s/artifact" % first)
    assert r1.status_code == 409, (r1.status_code, r1.text)
    detail = r1.json()["detail"]
    assert "覆盖" in detail, "没说清为什么拿不到：%r" % detail
    assert str(on_disk) in detail, "要人拿路径去对账，路径得在话里：%r" % detail
    assert "BBBBB" not in r1.text, "把**覆盖它的那一趟**的字节交出去了（正是 A15 要防的）"

    # ② 第 2 趟自己：照给（同一个文件、就是它写的）
    r2 = client2.get("/job/%s/artifact" % second)
    assert r2.status_code == 200, (r2.status_code, r2.text)
    assert r2.content == same_len_b, r2.content

    # ③ ★ 页面那一格与端点**同一处判据**：拿不到就是**没有按钮**（不是给个别人的）
    cell = _live(client, first)["artifact"]
    assert cell is not None and not cell["url"], cell
    assert cell["path"] is None and cell["filename"] is None, cell
    assert cell["say"] == detail, "页面那句话与端点那句话不是同一句：\n页面：%r\n端点：%r" % (
        cell["say"], detail)
    assert _live(client2, second)["artifact"]["url"], "第 2 趟自己那一格应该能下"


def test_an_overwrite_while_the_service_was_down_is_still_caught(tmp_path):
    """**服务不在的那段时间被盖掉的**，也一样认得出（指纹的来源是 checkpoint，不是进程里的缓存）。

    这一条是上面那条的加强版，也是「服务第一次看见时才现记一份」**做不到**的那一格：
    重启之后的服务从没见过第 1 趟交付那一翻 —— 它要是拿「此刻盘上是什么」当基准，
    就会把**别人的文件**当成第 1 趟的交给运营。判据必须来自**这一趟自己的记录**。
    """
    # ⚠️ 这一条必须用**真图**：登记表里没有的 job，`Service._snapshot` 走的是**这份 saver
    #    的读连接**（`_probe_graph`，R-19）—— 假图的状态活在它自己身上，演不了「记录还在、
    #    进程换了一个」。
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    factory = _real_factory(Rec(), tmp_path, saver=saver)
    first = _svc_client(graph_factory=factory, window=StubWindow(), checkpointer=saver)
    job_id = first.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert _reply_until_done(first, job_id)["delivered"] is True
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    written = on_disk.read_bytes()

    # 「别的运行」（或者谁）把那个文件盖掉了 —— 而服务此刻**不在**（下面换一个全新的实例）
    on_disk.write_bytes(b"# SOMEBODY ELSE\n")

    second = _svc_client(graph_factory=factory, window=StubWindow(), checkpointer=saver)
    assert second.app.state.service._jobs == {}, "新服务的登记表必须是空的（等于重启过）"
    r = second.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    assert "SOMEBODY ELSE" not in r.text, "重启之后就把别人的字节当成它的交出去了"
    assert "覆盖" in r.json()["detail"], r.json()["detail"]

    # ★ 把这一趟那一份**放回去** ⇒ 又能下 —— 判据跟着**盘上的字节**走，不跟着进程里的记忆走
    on_disk.write_bytes(written)
    again = second.get("/job/%s/artifact" % job_id)
    assert again.status_code == 200, (again.status_code, again.text)
    assert again.content == written, "放回去的那一份又下不到了（判据不是内容）"


def test_a_run_whose_record_has_no_bytes_to_compare_says_it_cannot_tell(tmp_path):
    """checkpoint 里**没有留下它写下的那串字节**（更早的版本跑的 / 记录不全）⇒ **没法核对** ⇒ 拿不到。

    ⚠️ 这一档不许「反正文件在，就给你吧」：同一个站点共用一个文件，
    核对不了就等于**可能给的是别人的** —— 那正是 A15 那条静默路径。
    """
    client = _client(tmp_path, _factory(record_src=False))
    job_id, view = _run_to_the_end(client, tmp_path)
    assert view["delivered"] is True, view
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    assert on_disk.is_file(), "桩没造对：文件应该在盘上（只是记录里没有那串字节）"

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert "没法确认" in detail, detail
    assert "没有产出" not in detail, "盘上有文件，说成「没有产出」是假话：%r" % detail
    cell = _live(client, job_id)["artifact"]
    assert cell is not None and not cell["url"], cell
    assert cell["say"] == detail, cell


def test_a_dead_run_does_not_hand_over_the_py_of_an_earlier_one(tmp_path):
    """跑挂的那一趟**不许**把上一次真跑留在 state 里的 `py_path` 端出来。

    与 `test_a_job_that_died_mid_flight_is_not_readable_as_succeeded` 同一个形状
    （那条钉的是 `/job/{id}`，这条钉的是产物端点）：state 里残留的 `py_path`
    只在 `end_reason == delivered` 时才算「这一趟的产物」。
    """
    stale = _out_dir(tmp_path) / "stale.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"# LAST TIME, NOT THIS TIME\n")
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]},
                               next=("intake",), interrupts=(_gate(),)),
                         _Snap(values={"site": SITE, "visits": ["intake", "explore"],
                                       "py_path": str(stale)},
                               next=("draft",), interrupts=(_gate("draft"),))],
                  raise_on=[3])
    client = _client(tmp_path, lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)                              # 停在 intake 那道闸上
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    # 停在 draft 那道闸上 —— 这一刻 state 里躺着**上一次**留下的 `py_path`
    assert _wait(client, job_id)["status"] == "waiting"
    assert _live(client, job_id)["artifact"] is None, "还在闸上，那一格就已经有产物了"
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    view = _wait(client, job_id, until=("failed", "done"))
    assert view["status"] == "failed", view

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 409, (r.status_code, r.text)
    assert "LAST TIME" not in r.text, "端出了上一趟留下的那个 py"

    # ⚠️ **页面那一格也要量**（端点是另一条路：它只在有产物记录时才去读 state，
    #    所以只钉端点的话，「判据看错了一格」在端点那头会被**别的条件挡住**而假绿 ——
    #    实测过：把 `_servable_py` 改成「state 里有 py_path 就算数」时，只钉端点的断言全绿，
    #    而 `/live` 那一格会说「能下」（页面于是摆出一个点下去 409 的下载）。
    cell = _live(client, job_id)["artifact"]
    assert cell is not None and not cell["url"], cell
    assert "跑挂" in cell["say"], cell["say"]


# ══════════════ 5. `/live` 的产物那一格 ══════════════


def test_the_live_cell_is_absent_before_the_run_reaches_the_writing_step(tmp_path):
    """**产物出现之前，那个位置不存在**（§15.4）—— 跑着 / 停在闸上时那一格是 `None`。

    不是「空对象」也不是「灰按钮」：`None` 才是页面能据此**根本不摆那一步**的东西。
    """
    from langgraph.types import Interrupt
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]},
                               interrupts=(Interrupt(value={"step": "deliver", "say": "要交付吗？",
                                                            "facts": {}, "can": ["让它继续"]},
                                                     id="i-1"),))])
    client = _client(tmp_path, lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    assert _live(client, job_id)["artifact"] is None, \
        "还没跑到写 py 那一步，页面上就已经摆了一个位置"


def test_the_live_cell_and_the_endpoint_say_the_very_same_thing(tmp_path):
    """两个地方说「为什么没有」时，说的必须是**同一句**（一处口径）。

    判据与话都从 `Service` 来。两边各写一套的话，运营会看到
    「页面说能下、点下去 409」或者两句不一样的原因 —— 两种都是这一片在治的病。
    """
    note = "人喊停：在「交付」这一步**之前**停下来，这一步没有做，页面与文件都保持原样。"
    client = _client(tmp_path, _factory(reason=END_HUMAN_STOP, note=note, write=False))
    job_id, _ = _run_to_the_end(client, tmp_path)

    cell = _live(client, job_id)["artifact"]
    assert cell is not None, "跑到头了但没产物 —— 这一格要有一句人话（不是 None）"
    assert cell["url"] is None, "没有产物却给了个下载地址（A14 点名不许的那种按钮）"
    assert cell["path"] is None and cell["filename"] is None, cell
    got = client.get("/job/%s/artifact" % job_id)
    assert got.status_code == 409, got.text
    assert got.json()["detail"] == cell["say"], \
        "页面那句话与端点那句话不是同一句：\n页面：%r\n端点：%r" % (cell["say"], got.json()["detail"])


def test_the_live_cell_says_nothing_about_a_file_when_there_is_none(tmp_path):
    """没有产物的那一格**不许**带一个地址 / 文件名（页面会照着它摆一个下载）。"""
    client = _client(tmp_path, _factory(reason=END_LINT_CAP,
                                        note="停：这一版 py 被打回 2 次还是不过。", write=False))
    job_id, _ = _run_to_the_end(client, tmp_path)
    cell = _live(client, job_id)["artifact"]
    assert not cell["url"] and not cell["filename"], cell
    assert "撞上限" in cell["say"], cell["say"]
