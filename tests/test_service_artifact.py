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

import hashlib
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
    # ⚠️ **服务只说它知道的那件事**（复审 R1）：「跑挂」是它知道的；
    #    「**没跑到**写下那个文件那一步」**不是** —— 交付之后服务自己那一步也会炸
    #    （`_capture_pause` 在 `job.status = DONE` 之前），那种一趟**交付过**。
    #    ⇒ 话说的是「**记录里**没有它写下 py 的证据」（可查的），不是「它没走到那一步」（猜的）。
    assert "记录里" in detail, "那句话没说是**记录里**有什么（说的是服务不知道的事）：%r" % detail
    assert "没跑到" not in detail, "又断言了服务不知道的因果（「没跑到那一步」）：%r" % detail


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


def test_a_delivered_run_whose_file_vanished_still_hands_over_its_own_bytes(tmp_path):
    """**结局说交付了、可那个文件现在不在盘上** —— 这一趟那一份**照样拿得到**，而且说清盘上没有。

    交付的那串字节在**记录**里（`state["src"]`），不在盘上那个文件里 ⇒
    文件被挪走/删掉**不影响**这一趟的产物能不能拿到；要说的只是「盘上那个路径现在没有文件了」。
    """
    client = _client(tmp_path, _factory())
    job_id, view = _run_to_the_end(client, tmp_path)
    assert view["delivered"] is True, view
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    on_disk.unlink()                                   # 有人把它挪走了

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == _py_bytes(SITE), "这一趟写下的那串字节拿不到了"
    cell = _live(client, job_id)["artifact"]
    assert cell["url"] and cell["path"] == str(on_disk), cell
    assert "没有这个文件" in cell["say"], "盘上那一份没了没说：%r" % cell["say"]
    assert "没有产出" not in cell["say"], "「产出过、后来没了」被说成了「没有产出」：%r" % cell["say"]


# ══════════════ 3. ★ 安全边界：路径只来自这一趟的记录 ══════════════


def test_a_recorded_path_outside_the_products_dir_is_never_looked_at(tmp_path):
    """记录里的路径**跑到产物目录外面**了 ⇒ 那个路径服务**不去看**（§15.3）。

    ⚠️ 这条**不是**「反正路径是服务自己记的所以不用查」：记录也会错
    （图改过、checkpoint 被污染、将来的某一步把外面来的东西写进 `py_path`）。
    核对一次是**唯一**拦住「读走产物目录外任何一个文件」的地方 —— 而端出去的字节
    来自**这一趟的记录**（`state["src"]`），与那个文件**没有关系**。

    钉住的是三件事：① **不读**那个文件（下面拿它的哈希当量具）；
    ② 端出去的永远是**记录里那一串**（TOPSECRET 一个字节都不出现）；
    ③ 话说清「那个路径没去看」（不是「没有产物」）。
    """
    outside = tmp_path / "outside" / "secret.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("TOPSECRET = 1\n", encoding="utf-8")
    secret_hash = hashlib.sha256(outside.read_bytes()).hexdigest()[:12]
    client = _client(tmp_path, _factory(path=outside))
    job_id, _ = _run_to_the_end(client, tmp_path)

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == _py_bytes(SITE), "端出去的不是这一趟写下的那串字节"
    assert "TOPSECRET" not in r.text, "把产物目录外面的文件内容漏出来了"
    cell = _live(client, job_id)["artifact"]
    assert str(outside) in cell["say"], "要人拿路径去对账，路径得在话里：%r" % cell["say"]
    assert "不去看" in cell["say"], cell["say"]
    assert secret_hash not in cell["say"], \
        "话里带上了那个文件的哈希 —— 说明**读了**它（§15.3 说好不去看的）：%r" % cell["say"]


def test_a_symlink_out_of_the_products_dir_is_not_read_either(tmp_path):
    """一个**指向别处**的 `example-funnel.py`：那条路服务也不去读它。

    名字、后缀、目录全对 —— 挡住它的只能是「解析**符号链接**之后还在不在里面」。
    少了这一步，`out_dir/` 里放一个软链就是一个任意文件读取（哪怕端出去的字节来自记录，
    **读**本身就是越界）。
    """
    outside = tmp_path / "outside" / "secret.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("TOPSECRET = 1\n", encoding="utf-8")
    target_hash = hashlib.sha256(outside.read_bytes()).hexdigest()[:12]
    link = _out_dir(tmp_path) / ("%s.py" % SITE)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    #: 手写这份快照（不走 `_factory`）：这一趟**写下过**那串字节（记录里有），
    #: 而记录里那个路径是个**软链** —— 正是不该去读它的那一格。
    body = _py_bytes(SITE)
    g = FakeGraph(steps=[_Snap(values={
        "site": SITE, "visits": ["intake", "deliver"], "out_dir": str(_out_dir(tmp_path)),
        "end_reason": END_DELIVERED, "end_note": "写好了：这一版跑通了。",
        "py_path": str(link), "src": body.decode("utf-8")})])
    client = _client(tmp_path, lambda brief, deps: g)
    job_id, _ = _run_to_the_end(client, tmp_path)
    assert link.is_file(), "桩没造对：这个软链应该指向一个真文件"
    assert outside.read_text(encoding="utf-8") == "TOPSECRET = 1\n", "桩没造对：外面那份被改写了"

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == _py_bytes(SITE), "端出去的不是这一趟写下的那串字节"
    assert "TOPSECRET" not in r.text, "顺着软链把外面的文件读出来了"
    cell = _live(client, job_id)["artifact"]
    assert "不去看" in cell["say"], cell["say"]
    assert target_hash not in cell["say"], \
        "话里带上了软链目标的哈希 —— 说明**读了**它：%r" % cell["say"]


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


def test_two_runs_of_the_same_site_each_get_their_own_bytes(tmp_path):
    """★ **同一个站点跑两趟：各自拿到自己写下的那串字节**（§15.5 / A15 —— **无条件**成立）。

    交付点是 `<out_dir>/<site>.py`（**一个站点一个文件**，生产就是 `forms/sites/<site>.py`）——
    所以第 2 趟**必然覆盖**第 1 趟那个文件。规格 §15.5 那个括号点名的正是这个形状：
    「这条最容易在『**按站点名存一个文件**』的实现里悄悄错掉」。

    ⇒ 服务端的是**这一趟记录里的那串字节**（`state["src"]`：`deliver` 写盘用的就是它，
    与 `py_path` 同一次 update 记着），**不是**去开盘上那个文件 —— 于是两趟各有各的。
    盘上那一份**不一样了要说出来**（它现在不是第 1 趟写的那一份了），
    但那是**一条信息**，不是拒绝的理由（§15.2：「交付的是 `deliver` 那一步写下的那个文件」——
    那串字节就在记录里）。

    两趟的字节**一样长、内容不一样**（`# AAAAA` / `# BBBBB`）：只比长度、或者去盘上取最新那份，
    这一条都过不去。
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

    # ① ★ 第 1 趟：拿到的是**它自己**那串字节（不是第 2 趟的，也不是盘上那份）
    r1 = client.get("/job/%s/artifact" % first)
    assert r1.status_code == 200, (r1.status_code, r1.text)
    assert r1.content == same_len_a, r1.content
    assert "BBBBB" not in r1.text, "把**覆盖它的那一趟**的字节交出去了（正是 A15 要防的）"

    # ② 第 2 趟自己：当然是它自己那份
    r2 = client2.get("/job/%s/artifact" % second)
    assert r2.status_code == 200, (r2.status_code, r2.text)
    assert r2.content == same_len_b, r2.content

    # ③ 页面那一格与端点**同一处判据**：两趟都能下；第 1 趟那句要说清盘上那份**不是它的**了；
    #    路径两趟都在（人要对账）
    cell1 = _live(client, first)["artifact"]
    assert cell1["url"] and cell1["path"] == str(on_disk), cell1
    #: 路径显示在 `path` 那一格上（页面照抄它，§15.2 要的「它写到哪了」）——
    #: 那句话里说的是「盘上那份不是它的了」，不必再复述一遍路径。
    assert "不是这一趟写下的那串字节" in cell1["say"], cell1["say"]
    cell2 = _live(client2, second)["artifact"]
    assert cell2["url"] and cell2["path"] == str(on_disk), cell2
    assert "不是这一趟写下的那串字节" not in cell2["say"], \
        "第 2 趟那一份就是盘上那一份（它就是最后写的那个人），不该说它不一样：%r" % cell2["say"]


def test_an_overwrite_that_happened_while_the_service_was_down_is_still_reported(tmp_path):
    """**服务不在的那段时间被盖掉的**：照样把**这一趟自己那份**交出去，并且说清盘上不是它了。

    这一条是上面那条的加强版，也是「服务第一次看见时才现记一份指纹」**做不到**的那一格：
    重启之后的服务从没见过第 1 趟交付那一翻 —— 拿「此刻盘上是什么」当基准的话，
    它要么把**别人的文件**当成第 1 趟的交出去（旧形状），要么把这一趟拒掉。
    判据来自**这一趟自己的记录**，所以两条都不发生。
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
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == written, "重启之后交出去的不是这一趟写下的那串字节"
    assert "SOMEBODY ELSE" not in r.text, "把别人的字节当成它的交出去了"
    assert "不是这一趟写下的那串字节" in _live(second, job_id)["artifact"]["say"], \
        _live(second, job_id)["artifact"]["say"]

    # ★ 把这一趟那一份**放回去** ⇒ 那句话也回来说「就是它」—— 判据跟着**盘上的字节**走，
    #    不跟着进程里的记忆走（进程换过一次了，记忆里什么都没有）
    on_disk.write_bytes(written)
    cell = _live(second, job_id)["artifact"]
    assert "不是这一趟写下的那串字节" not in cell["say"], cell["say"]
    again = second.get("/job/%s/artifact" % job_id)
    assert again.status_code == 200 and again.content == written


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
    assert "没有留下它写下的那串字节" in detail, detail
    assert "没有产出" not in detail, "盘上有文件，说成「没有产出」是假话：%r" % detail
    # R3：那一屏上还会显示「交付了」（`/job/{id}` 的 `delivered`）—— 这句话得解释一下，
    # 不然运营看到的是「交付了」+「拿不到」两句话并排
    assert "那一步做成了" in detail, "没解释「交付了」与「拿不到」为什么能同时成立：%r" % detail
    cell = _live(client, job_id)["artifact"]
    assert cell is not None and not cell["url"], cell
    assert cell["say"] == detail, cell


def test_a_dead_run_does_not_hand_over_the_py_of_an_earlier_one(tmp_path):
    """跑挂的那一趟**不许**把上一次真跑留在 state 里的 `py_path` / `src` 端出来。

    与 `test_a_job_that_died_mid_flight_is_not_readable_as_succeeded` 同一个形状
    （那条钉的是 `/job/{id}`，这条钉的是产物端点）：state 里残留的 `py_path` **与 `src`**
    只在 `end_reason == delivered` 时才算「这一趟的产物」。

    ⚠️ **这一条在新形状下更要紧**：端出去的字节现在就是从 `state["src"]` 取的 ——
    要是那道「交付过没有」的闸漏了，残留的 `src` 会被**原样端出去**（而它是上一趟的）。
    """
    stale = _out_dir(tmp_path) / "stale.py"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"# LAST TIME, NOT THIS TIME\n")
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]},
                               next=("intake",), interrupts=(_gate(),)),
                         _Snap(values={"site": SITE, "visits": ["intake", "explore"],
                                       "py_path": str(stale),
                                       "src": "# LAST TIME, NOT THIS TIME\n"},
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


# ══════════════ 4b. 复核点名的三条：跑挂 ≠ 没交付 / 核对过的就是端出去的 / 读不出来 ══════════════


def test_a_run_that_delivered_and_then_blew_up_still_gets_its_bytes_out(tmp_path):
    """**跑挂 ≠ 没交付**（复核 R1）：图**已经交付**之后服务自己那一步抛了 ⇒ 登记表说 failed，
    而产物**在记录里、也在盘上** —— 这一趟的那串字节**照样交得出去**，而且那句话不许说
    「没有产出 py」「没跑到写下那个文件那一步」「产物目录里不会有它写的 py」。

    可达性（复核实测）：`_advance` 在 `invoke` **成功返回之后**先 `_capture_pause(job)`
    再写 `job.status = DONE` —— 中间那一抛逃到 `_work` 的最后一层网（`_note_escaped`）。
    这里照 `tests/test_service_events.py` 那条「最后一层网」的构造做（让 `narrate` 抛）。
    """
    written = "# 交付之后那一步才炸的\nBODY = 1\n"
    py = _out_dir(tmp_path) / ("%s.py" % SITE)
    g = FakeGraph(steps=[_Snap(values={
        "site": SITE, "visits": ["intake", "deliver"], "out_dir": str(_out_dir(tmp_path)),
        "end_reason": END_DELIVERED, "end_note": "写好了：这一版跑通了。",
        "py_path": str(py), "src": written})])
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(written, encoding="utf-8")
    client = _client(tmp_path, lambda brief, deps: g)
    svc = client.app.state.service
    real_narrate = svc.narrate
    boom = {"n": 0}

    def narating(job, kind, say, **kw):
        """第 3 次播报开始抛（照 `_advance` 那三步的次序：running → 抓拍 → done）——
        于是逃到最后一层网，而**图已经交付了**。"""
        boom["n"] += 1
        if boom["n"] >= 3:
            raise RuntimeError("这一条的叙述炸了")
        return real_narrate(job, kind, say, **kw)

    svc.narrate = narating
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id, until=("failed", "done", "waiting"))
    assert view["status"] == "failed", view
    assert py.is_file(), "桩没造对：交付的 py 应该在盘上"

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == written.encode("utf-8"), "交付过的那一趟被当成了「没有产物」"

    # 跑挂那句话里**不许**再断言「没有交付任何东西」（它不知道）
    said = str(view.get("say") or "")
    assert "不会有它写的 py" not in said, "跑挂那句话断言了一件它不知道的事：%r" % said
    assert "没有交付任何东西" not in said, said
    cell = _live(client, job_id)["artifact"]
    assert cell["url"], "页面那一格也该能下：%r" % cell


def test_the_bytes_that_were_checked_are_the_bytes_that_go_out(tmp_path, monkeypatch):
    """**核对过的那串字节 == 端出去的那串**（复核 R2）：核对与发送之间**没有第二次开盘**。

    做法：把 `_disk_fingerprint` 换成「先照真做、**再把盘上那个文件换掉**」——
    于是「核对的那一刻」与「发出去的那一刻」之间那个缝被**人为撑开**。
    旧形状（核对完再让 `FileResponse` 开一次盘）在这条上会把**换上去的那份**端出去。

    ⚠️ 这一条量的是「端出去的正文」，所以换上去的那串字节**不许**出现在正文里。
    """
    written = "# THIS RUN\nBODY = 1\n"
    client = _client(tmp_path, _factory(body=written.encode("utf-8")))
    job_id, _ = _run_to_the_end(client, tmp_path)
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    assert on_disk.read_bytes() == written.encode("utf-8")

    swapped = {"done": False}
    real = service._disk_fingerprint

    def swapping(path):
        got = real(path)
        path.write_bytes(b"# SOMEBODY ELSE (a later run)\n")   # ← 核对之后、发出去之前
        swapped["done"] = True
        return got

    monkeypatch.setattr(service, "_disk_fingerprint", swapping)
    r = client.get("/job/%s/artifact" % job_id)
    assert swapped["done"], "探针没触发（换文件那一步没发生）—— 这条就是空过的"
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == written.encode("utf-8"), \
        "端出去的是**换上去的那份**（核对过的 ≠ 端出去的）：%r" % r.content


def test_a_hand_edited_file_gets_no_cause_the_service_never_saw(tmp_path):
    """盘上那份是**人手改的**（**没有任何第二趟运行**）⇒ 那句话**不许**把因果说成结论。

    复审 E3 实测过：只把文件换掉，旧话照样说「**已经被后来的运行覆盖了**……后来的那一趟
    写的就是同一个路径」—— 两个数字真，**因果是编的**。这一条就是那句「退半步」的守：
    话里说的是「**不是这一趟写下的那串字节**」（观测到的），原因只列**可能**（没观测到的）。
    """
    client = _client(tmp_path, _factory())
    job_id, _ = _run_to_the_end(client, tmp_path)
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    on_disk.write_bytes("# 人手改的，没有任何第二趟\n".encode("utf-8"))   # ← 一趟运行都没再跑过

    r = client.get("/job/%s/artifact" % job_id)
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.content == _py_bytes(SITE), "这一趟写下的那串字节拿不到了"
    say = _live(client, job_id)["artifact"]["say"]
    assert "不是这一趟写下的那串字节" in say, "没说清盘上那份不是它的：%r" % say
    assert "可能" in say, "把一个**没观测到**的因果说成了结论（「被后来的运行覆盖了」）：%r" % say


def test_a_file_the_service_cannot_read_still_hands_over_the_records_bytes(tmp_path):
    """盘上那份**读不出来**（权限）⇒ 照样把**记录里那串**交出去，并**照实说**读不出来的原因。

    复核 R3 点名的两条：①「读得出字节」那一档**零守**（去掉它全量照绿）——
    这一条就是它的守；②那句话的括号里原来是**同一句话的复述**（真因被吞了）——
    这一条要那个真因（`Permission denied`）出现在话里。
    """
    client = _client(tmp_path, _factory())
    job_id, _ = _run_to_the_end(client, tmp_path)
    on_disk = _out_dir(tmp_path) / ("%s.py" % SITE)
    on_disk.chmod(0o000)
    try:
        r = client.get("/job/%s/artifact" % job_id)
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.content == _py_bytes(SITE), "读不出盘上那份，就把这一趟的字节也丢了吗"
        cell = _live(client, job_id)["artifact"]
        assert "读不出来" in cell["say"], cell["say"]
        assert "Permission denied" in cell["say"], \
            "话里没带真因（只带了一句同义复述）：%r" % cell["say"]
    finally:
        on_disk.chmod(0o644)          # 还回去，别让 `tmp_path` 清理时翻车


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
