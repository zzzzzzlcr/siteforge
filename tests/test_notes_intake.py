"""Task 8 补丁 B：`journey.notes` —— 「外面来的字」进系统的**第二个口子**。

## 病（基线 `68ddc4f` 上实测，不是转述）

补丁 A 治的是**载荷进门**那一跳（`/say` 的文本、`/run` 的字段）。这一份治的是另一条：
**页面上的字与模型说的话**走进 `journey.notes`，再由 `graph._unfinished_note` /
`graph._journey_say` 拼成 `end_note` 进 state —— 一个**孤立代理对**过得了 `json.dumps`、
过不了最后那次 `.encode("utf-8")` ⇒ `GET /job/{id}` 与 `GET /job/{id}/live`
**双双 500**（运营那一屏整条读不出来）。与补丁 A 是**同一个症状、不同的口子**。

⚠️ **可达性比补丁 A 那个口子更高**：这里的字来自**真站页面**（页面自报的标题 / 元素文字）
与**模型**（它每一轮的推理），**不需要运营犯任何错**。

## 这一份钉的是**那一类**，不是被点名的那三处

`grep -n "journey.notes.append(" agent/*.py` ⇒ **22 处**（`browser_agent` 17 / `graph` 3 /
`service` 2）。这一版把**写入口收成一个**（`browser_agent.Journey.note`），
所以 22 处**一处不落**都在消毒机器后面；而**机器守**（`test_..._one_door_...`）
钉住「以后谁再加一处裸 `notes.append`，这条会红」。

## 量具说明：脏字节怎么进得来

桩 MCP 的程序文件与回执都用 `ensure_ascii=True` 写（`"\\ud800"` 这个**转义序列**是纯 ASCII，
管道过得去）—— Python 侧 `json.loads` 解出来就是**真的**孤立代理对。
和补丁 A 那份测试同一个理由、同一种做法。

⚠️ 这一份**不开浏览器、不打真模型**（Global Constraints）：桩 MCP（真子进程、真 JSON-RPC）
+ `FakeLLM` + `TestClient`。真站那一趟（`test_browser_agent.py` 里那两台真 Chrome）
本次一个字没碰。
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, service, tools  # noqa: E402

from test_browser_agent import PAGE_LANDING, PAGE_QUIZ, FakeLLM  # noqa: E402
from test_service_input import SITE, URL, WS_URL, FakeGraph, StubWindow, _client, _factory  # noqa: E402
from test_service_steps import _stub as _stub_ascii  # noqa: E402

#: 「外面来的字」里那个**线上写不出来的**码位：孤立代理对（半个代理对）。
BAD = "\ud800"
#: 它被换成什么（`events._UNWRITABLE`）。**写死这个码位本身**。
SAFE = "�"


def _dirty(obj, **over):
    """一份页面模型，其中某几格带着**真的**孤立代理对（走 JSON 转义序列过管道）。"""
    page = json.loads(json.dumps(obj, ensure_ascii=True))
    page.update(over)
    return page


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


def _wait_done(client, job_id, timeout=20.0):
    """等这一趟跑完，**并且容忍 500**（基线就是 500 —— 这一条要量的正是那个 500 本身，
    所以它不能像 `test_service_input._wait` 那样直接 `.json()`：那会在基线抛
    `JSONDecodeError`，把「500」这条判据淹掉）。"""
    import time

    end = time.time() + timeout
    last = client.get("/job/%s" % job_id)
    while time.time() < end:
        if last.status_code != 200:
            return last                      # 基线：500（这一条就红在这里）
        if last.json()["status"] in ("done", "failed", "waiting"):
            return last
        time.sleep(0.02)
        last = client.get("/job/%s" % job_id)
    return last


def _run_dirty(tmp_path, responses, turns, name="dirty", **kwargs):
    """一趟**真**探路（真 `explore` / 真桩 MCP / 真 `note_page`）—— 桩的**程序文件**用
    `ensure_ascii=True` 写（`test_service_steps._stub`）：页面模型里那个真的孤立代理对
    只能以 `"\\ud800"` 这个**转义序列**的形态过盘/过管道（`test_browser_agent._stub`
    写的是 `ensure_ascii=False`，脏页面在那儿**写不出去** —— 那是量具先炸，不是线上）。
    """
    session = tools.McpSession([_stub_ascii(tmp_path, responses, name=name)])
    fake = FakeLLM(turns)
    try:
        return browser_agent.explore(
            "https://example.test/funnel", "看看这一页怎么走到报价",
            session=session, client=fake, **kwargs)
    finally:
        session.close()


def _explore_from_a_dirty_page(tmp_path):
    """一趟**真**探路，页面标题里有坏字节。

    剧本：看一眼 → 再看一眼（第二眼是**变了的**那一页）⇒ 走 `browser_agent.py:683`
    那条「页面变了」的 note，而那句话里引的正是**页面自报的标题**。
    """
    page_a = _dirty(PAGE_LANDING)
    page_b = _dirty(PAGE_QUIZ, title="Example 漏斗 第 2 步" + BAD)
    return _run_dirty(
        tmp_path,
        {"observe": [{"structured": page_a}, {"structured": page_b}]},
        [{"calls": [("observe", {})]},
         {"calls": [("observe", {})]},
         {"content": "看完了"}],
    )


# ══════════ ① 被点名的那三处之一：页面标题 + url → journey.notes ══════════


def test_a_page_title_with_unwritable_bytes_no_longer_takes_the_console_down(tmp_path):
    """**这一条就是那个病**：页面上的坏字节 ⇒ `notes` ⇒ `end_note` ⇒ 两个 500。

    基线实测（`68ddc4f`，本次会话量的）：同一份 `end_note` ⇒
    `GET /job/{id}` **500**、`GET /job/{id}/live` **500**。
    """
    journey = _explore_from_a_dirty_page(tmp_path)
    # ① 前置对照：那条「页面变了」的 note 真写进去了（否则下面量的不是这条线）
    assert any("变了" in n for n in journey.notes), (
        "「页面变了」那条 note 压根没写 —— 这条测试量的不是它：%r" % (journey.notes,))

    # ② 它拼出来的收场白 —— 这里用的是**真**那两个函数（`graph.py:497` 那两行）
    end_note = graph._unfinished_note(journey.stop_reason, journey)

    # ③ 端到端：那一屏读得出来（两个 500 都不许再有）
    client = _client(graph_factory=_factory(FakeGraph(steps=[
        {"site": SITE, "ws_url": WS_URL, "end_reason": "explore_unfinished",
         "explore_say": graph._journey_say(journey), "end_note": end_note}])),
        window=StubWindow(alive=True), raise_server_exceptions=False)
    r = client.post("/run", json={"url": URL, "goal": "看看", "success_text": "Thank you",
                                  "site": SITE, "ws_url": WS_URL,
                                  "form_file": str(tmp_path / "form.json"),
                                  "out_dir": str(tmp_path / "forms")})
    assert r.status_code == 202, r.text[:300]
    job_id = r.json()["job_id"]

    one = _wait_done(client, job_id)
    assert one.status_code == 200, (
        "页面标题里的坏字节把 `/job/{id}` 打挂了（基线就是这个 500）：%s" % one.text[:300])
    live = client.get("/job/%s/live" % job_id)
    assert live.status_code == 200, (
        "**运营那一屏读不出来** —— 这是这条线真正的代价：%s" % live.text[:300])
    assert SAFE in one.json()["say"], one.json()["say"]


def test_the_pages_own_words_are_replaced_and_the_count_is_said_out(tmp_path):
    """换掉是有损的 ⇒ **要说出来**（Global Constraints：没有静默的路径）。

    三样一起量：① 好字节一个不少、坏字节变成 `�`；② 「换了几个」**说在那条话自己身上**
    （不另起一条 —— 另起一条会顶掉 `notes[-1]`，而 `graph._journey_say` 的尾巴取的正是它，
    见 `browser_agent.py:762` 那段「顺序有讲究」）；③ 收场白里没有坏字节。
    """
    journey = _explore_from_a_dirty_page(tmp_path)
    dirty = [n for n in journey.notes if "变了" in n]
    assert dirty, journey.notes
    note = dirty[0]
    # ① 好字节一个不少（页面自己那句标题、地址都还在）、坏字节成了 `�`
    assert note.startswith("页面变了：现在是「Example 漏斗 第 2 步" + SAFE + "」"), note
    assert "example.test/funnel?step=2" in note, note
    assert not _has_lone_surrogate(journey.notes), (
        "页面上的坏字节原样进了 `journey.notes`：%r" % (journey.notes,))
    # ② 报数说在**这条话自己**身上，而且**没有另起一条**
    assert "写不出来" in note, (
        "换掉是有损的，可一个数都没报（没有静默的路径）：%r" % (note,))
    assert [n for n in journey.notes if "写不出来" in n] == [note], (
        "报数另起了一条（`notes` 是个**有顺序的**单子，另起一条会顶掉 `notes[-1]` —— "
        "而 `graph._journey_say` 的尾巴取的正是那一句）：%r" % (journey.notes,))
    # 对照：同一个剧本、同一页，只有**标题里那一个字节**不同 ⇒ 条数必须一样
    clean = _run_dirty(
        tmp_path / "clean",
        {"observe": [{"structured": _dirty(PAGE_LANDING)}, {"structured": _dirty(PAGE_QUIZ)}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}, {"content": "看完了"}],
        name="clean")
    assert len(clean.notes) == len(journey.notes), (
        "换了字节的那一趟多出/少了 note —— 报数**另起了一条**：\n干净：%r\n脏的：%r"
        % (clean.notes, journey.notes))
    # ③ 收场白编得出去
    assert not _has_lone_surrogate(graph._unfinished_note(journey.stop_reason, journey))


# ══════════ ② 另一处：模型自己说的话 ══════════


def test_the_models_own_words_are_cleaned_before_they_enter_the_notes(tmp_path):
    """`AI 说：…` 那条（`browser_agent` 的 `_note`）—— 字来自**模型**，同样是外面来的。

    这一处不比页面那条少见：模型每一轮的推理都走它，而它**一个字都不用过运营的手**。
    """
    said = "我先点那个按钮" + BAD + "再看看"
    journey = _run_dirty(
        tmp_path,
        {"observe": [{"structured": _dirty(PAGE_LANDING)}]},
        [{"calls": [("observe", {})]}, {"content": said}],
    )
    mine = [n for n in journey.notes if n.startswith("AI 说：")]
    assert mine, "模型说的话一句都没进 notes：%r" % (journey.notes,)
    assert not _has_lone_surrogate(mine), "模型说的话里的坏字节原样进了账本：%r" % (mine,)

    end_note = graph._unfinished_note(journey.stop_reason, journey)
    assert not _has_lone_surrogate(end_note), (
        "模型那句话把收场白带坏了 —— `/job/{id}` 与 `/live` 会双双 500：%r" % (end_note,))


# ══════════ ③ 类：写入口只有一个（机器守）══════════


def _notes_writes_in_source(source: str, path: str) -> list:
    """这份源码里所有「往某个 `.notes` 里塞东西」的地方（`(文件:行, 在哪个函数里)`）。

    认三种塞法：`.append(` / `.extend(` / `.insert(`；以及 `x.notes = [...]` 这种**换掉整本**。
    ⚠️ 它认的是**属性名恰好是 `notes`**（`job.shot_notes` 不算 —— 那是另一本账）。
    """
    tree = ast.parse(source)
    found = []

    def walk(node, funcs):
        for child in ast.iter_child_nodes(node):
            name = funcs
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = funcs + [child.name]
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in ("append", "extend", "insert") \
                    and isinstance(child.func.value, ast.Attribute) \
                    and child.func.value.attr == "notes":
                found.append(("%s:%d" % (path, child.lineno), ".".join(name)))
            if isinstance(child, ast.Assign) and any(
                    isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute)
                    and t.value.attr == "notes" for t in child.targets):
                found.append(("%s:%d" % (path, child.lineno), ".".join(name)))
            if isinstance(child, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and t.attr == "notes" for t in child.targets):
                found.append(("%s:%d" % (path, child.lineno), ".".join(name)))
            walk(child, name)

    walk(tree, [])
    return found


def test_the_notes_list_has_exactly_one_write_entry(tmp_path):
    """**这一类**：`journey.notes` 只许有一个写入口（`Journey.note`）。

    为什么要有这一条（这一片的病史：六轮「修了被点名的那处」）：消毒机器接在**每个写点**上，
    就得靠**每个人**每次都记得接 —— 而这正是 `_utf8_safe` 的注释里那句「不能靠写的人小心」。
    收成一个口子之后，新加一个写点会**自动**走消毒；而「绕过口子直接往 list 里塞」
    由这一条当场拦下（红的是一行源码，不是一个字节）。
    """
    writes = []
    for name in ("browser_agent.py", "graph.py", "service.py", "journal.py",
                 "measure.py", "rounds.py", "state.py"):
        path = ROOT / "agent" / name
        writes.extend(_notes_writes_in_source(path.read_text(encoding="utf-8"),
                                              "agent/" + name))
    outside = [w for w in writes if w[1] != "Journey.note"]
    assert outside == [], (
        "`journey.notes` 除了 `Journey.note` 之外还有写点（那里没有消毒机器）：%r" % (outside,))
    assert [w[1] for w in writes] == ["Journey.note"], (
        "`Journey.note` 这个写入口不见了（整台消毒机器就没了）：%r" % (writes,))


# ══════════ ④ 那个点名的例外：`POST /run` 的 `expects` ══════════


def test_the_expects_exception_is_still_safe_today(tmp_path):
    """`expects` 是补丁 A 点名**不在门口消毒**的那一格。这一条量的是它的安全性。

    它的理由（`RunRequest.SANITISED_AT_ITS_OWN_SEAM` 那段注释）是一句**判断**：
    「它没有任何直通响应的出口」。这一条把那句判断量出来，而不是接着信它：

      - 图收到的那份载荷里没有它（`_payload` 的 keep 清单）；
      - `agent/graph.py` / `agent/state.py` 里一个字都不提它（它进不了 state）；
      ⇒ 它到不了 `.encode("utf-8")` 那一层，所以门口不消毒**今天**不会 500。
    """
    src = (ROOT / "agent" / "graph.py").read_text(encoding="utf-8")
    assert "expects" not in src, "`expects` 进了图 —— 「它没有直通出口」那句判断作废了"
    state_src = (ROOT / "agent" / "state.py").read_text(encoding="utf-8")
    assert "expects" not in state_src, "`expects` 进了 state —— 那句判断作废了"

    keep = service.Service._payload({"url": URL, "goal": "看看", "expects": [{"url_contains": "/x"}]})
    assert "expects" not in keep, (
        "`_payload` 把 `expects` 往下发了 —— 那句判断作废了：%r" % (sorted(keep),))
