"""Task 8 补丁 B：`journey.notes` —— 「外面来的字」进系统的**第二个口子**。

## 病（基线 `68ddc4f` 上实测 —— **修复轮 1 改准了病名**）

补丁 A 治的是**载荷进门**那一跳（`/say` 的文本、`/run` 的字段）。这一份治的是另一条：
**页面上的字与模型说的话**走进 `journey.notes`，再由 `graph._unfinished_note` /
`graph._journey_say` 拼成 `end_note` 进 state。

⚠️ **真症状是【静默失真】，不是 500**（原版这条 docstring 与提交信息里写的「双双 500」
**不成立** —— 那句是转述派工单，我没复算；复审独立复现后我自己也复算了一遍）：
走**真栈**（真 `explore` + 真 `graph.build` + 真 allowlisted `InMemorySaver` + 走服务）时
**`/job` 与 `/live` 都是 200**，坏码位在 `serde.dumps_typed` 那一步被**悄悄换成 `?`**
（实测 `b'\xe5\x88\xb0?'`，0x3F）—— 运营那一屏上是「第 2 步?」，**一个没人报告过的问号**。
500 只出现在「拿 `FakeGraph.get_state` 的手工快照绕过 saver」的量具里（复审 §一.2 点名，
补丁 A 复审也点过同一个坑）—— **那个 500 是量具造的**。⇒ 这一份的用例现在**一律走真栈**，
`test_the_operator_sees_the_replacement_mark_not_a_silent_question_mark` 是它的正身。

**它和补丁 A 是同一个主题（外面来的字）、不是同一个症状**：那边是响的 500，
这边是**哑的问号** —— 而「**没有静默的路径**」正是这一片要治的东西。

⚠️ **可达性比补丁 A 那个口子更高**：这里的字来自**真站页面**（页面自报的标题 / 元素文字）
与**模型**（它每一轮的推理），**不需要运营犯任何错**。

## 这一份钉的是**那一类**，不是被点名的那三处

`grep -n "journey.notes.append(" agent/*.py` ⇒ **22 处**（`browser_agent` 17 / `graph` 3 /
`service` 2）。这一版把**写入口收成一个**（`browser_agent.Journey.note`），
所以 22 处**一处不落**都在消毒机器后面。

⚠️ **机器守的射程**（`test_the_notes_list_has_exactly_one_write_entry` 的 docstring 里
有那张逐条表，别把承诺想大）：它挡得住 `append` / `extend` / `insert` / 下标赋值 /
整本换掉 / **`+=`** / `getattr(…, "notes").append(…)` / `list.append(x.notes, …)`；
**挡不住**构造函数注入（`Journey(notes=[…])`）、把 `Journey` 整个换掉、
以及「**先 `n = journey.notes` 再 `n.append(…)`**」这种别名（见那条用例的 §挡不住的）。

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
from test_service_input import SITE, URL, WS_URL, StubWindow, _client  # noqa: E402
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


def _run_dirty_then_stop(tmp_path, **budget_kw):
    """一趟**真**探路，跑满轮数就停（`stop_reason = budget_rounds`）。

    ⚠️ **为什么是「跑满轮数」而不是「模型说完了」**（这条不是风格，是判据的选择）：
    `_unfinished_note` 只在 `stop not in FINISHED_EXPLORATION` 时被调；而 `model_done`
    那一支走的是**常量**那句话 —— 脏的 note 进不了收场白，这条用例就量不到它要量的东西。
    `budget_rounds` 那一支拼的是 `"；".join(notes[-2:])`，**脏 note 正在里面**。
    """
    page_a = _dirty(PAGE_LANDING)
    page_b = _dirty(PAGE_QUIZ, title="Example 漏斗 第 2 步" + BAD)
    budget_kw.setdefault("max_rounds", 3)
    return _run_dirty(
        tmp_path,
        {"observe": [{"structured": page_a}, {"structured": page_b}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=50, **budget_kw),
    )


def _real_stack(tmp_path, calls=None):
    """**真栈**：真 `explore`（脏页面）+ 真 `graph.build` + 真 allowlisted `InMemorySaver`
    + 走服务（`TestClient`）。

    ⚠️ **不许用 `FakeGraph` 的手工快照**（复审 §一.2：那个快照绕开了 saver，量出来的
    500 是**量具造的**）。这一份里**一个图桩都没有** —— 桩的只有两样**本来就必须桩**的东西：
    浏览器（桩 MCP 子进程）与模型（`FakeLLM`）。
    """
    from test_service import _reply_until_done

    calls = calls if calls is not None else {}
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)

    def explore(url, goal, budget=None, should_pause=None, **kw):
        calls["journey"] = _run_dirty_then_stop(tmp_path / "mcp")
        return calls["journey"]

    def selftest_stub(py_path, ws_url, form_file, site, **kw):
        raise AssertionError("这条路不该走到自测（探路没走完就不往下写 py）")

    def factory(brief, deps):
        return graph.build(checkpointer=saver,
                           deps=graph.Deps(explore=explore, selftest=selftest_stub,
                                           set_viewport=None))

    client = _client(graph_factory=factory, window=StubWindow(alive=True),
                     raise_server_exceptions=False)
    r = client.post("/run", json={"url": URL, "goal": "看看", "success_text": "Thank you",
                                  "site": SITE, "ws_url": WS_URL,
                                  "form_file": str(tmp_path / "form.json"),
                                  "out_dir": str(tmp_path / "forms")})
    assert r.status_code == 202, r.text[:300]
    job_id = r.json()["job_id"]
    return client, job_id, _reply_until_done(client, job_id), saver


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


def test_the_operator_sees_the_replacement_mark_not_a_silent_question_mark(tmp_path):
    """**这一条就是那个病**，而且**走真栈**（真图 + 真 allowlisted saver + 走服务）。

    它量的是**运营那一屏上到底是什么字**：页面标题里的孤立代理对，到了 `end_note`
    那一格、又过了 saver 一圈之后 ——

      - **基线 `68ddc4f`（实测）**：两个端点 **200**，屏上是「第 2 步**?**」——
        `serde.dumps_typed` 把它**悄悄**换掉了（`b'…\xe5\x88\xb0?'`），**没人被告知**；
      - **现在**：屏上是「第 2 步**`�`**」**外加一句「这条里有 1 个字节线上写不出来…」**。

    ⚠️ 谁把这个口子拆了，红的是这条 —— 而且红在**「线上出现了 `?`」**上，
    不是红在「账本里有坏字节」上（复审 §4.4.2 点的那个盲区正是这一条）。
    """
    client, job_id, view, saver = _real_stack(tmp_path)
    assert view["status"] == "done", view

    # ① 真栈上两个端点都读得出来（基线也是 200 —— 所以这一条**不是**在钉 500）
    one = client.get("/job/%s" % job_id)
    assert one.status_code == 200, one.text[:300]
    live = client.get("/job/%s/live" % job_id)
    assert live.status_code == 200, live.text[:300]

    say = one.json()["say"]
    # ② 页面上那个字**还在**（好字节一个不少），坏的那个**被换掉了**、而且**报了数**
    assert "第 2 步" in say, say
    assert "第 2 步" + SAFE in say, (
        "运营那一屏上没有替换记号 —— 坏字节多半又变成别的什么了：%r" % say)
    assert "写不出来" in say, "换掉了却一个数都没报（没有静默的路径）：%r" % say
    # ③ **不许**是那个哑的问号（这就是这一条存在的全部理由）
    assert "第 2 步?" not in say, (
        "线上出现了一个**没人报告过**的问号 —— 这正是基线那个病：%r" % say)
    assert "/job.say 与 /live.say 是同一句" and live.json()["say"] == say, (
        "两个出口读的是同一格，说的却是两句不同的话：%r / %r" % (live.json()["say"], say))

    # ④ **在真 saver 上**量那一格本身（不是手工快照）：换过的记号**存进去了**，
    #    而且存的是 `�` 不是 `?` —— 这一条钉的是「(G) 真 saver 上的形状」
    values = _saved_values(saver, job_id)
    note = str(values.get("end_note") or "")
    assert note, "saver 里那一格是空的 —— 这条量的不是它：%r" % (sorted(values),)
    assert not _has_lone_surrogate(note), note
    assert SAFE in note, (
        "saver 读回来那一格里没有替换记号（`serde` 多半又把它悄悄换成了别的）：%r" % note)
    assert "第 2 步?" not in note, "saver 里躺着那个哑的问号：%r" % note


def _saved_values(saver, job_id):
    """**真 saver** 里这个 job 的那一格（`channel_values`）—— 直接问 saver，不走桩。"""
    snap = saver.get_tuple({"configurable": {"thread_id": job_id}})
    return dict(getattr(getattr(snap, "checkpoint", None), "get", lambda *a: {})(
        "channel_values", {}) or {})


def test_the_pages_own_words_are_replaced_and_the_count_is_said_out(tmp_path):
    """换掉是有损的 ⇒ **要说出来**（Global Constraints：没有静默的路径）。

    三样一起量：① 好字节一个不少、坏字节变成 `�`；② 「换了几个」**说在那条话自己身上**
    （不另起一条 —— 另起一条会顶掉 `notes[-1]`，而 `graph._journey_say` 的尾巴取的正是它，
    见 `browser_agent.py:810` 那段「顺序有讲究」）；③ 收场白里没有坏字节。
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
        "模型那句话把收场白带坏了 —— 它会一路走到 `end_note`：%r" % (end_note,))


# ══════════ ③ 类：写入口只有一个（机器守）══════════


#: 「往 `.notes` 里塞」的动作名（第一形的收方法）。
_NOTES_METHODS = ("append", "extend", "insert")


def _is_notes_attr(node) -> bool:
    """这是个「恰好叫 `notes` 的属性」吗（`job.shot_notes` 不算 —— 那是另一本账）。"""
    return isinstance(node, ast.Attribute) and node.attr == "notes"


def _is_notes_subscript(node) -> bool:
    """`x.notes[i]` 这种（下标赋值 = 第四种塞法）。"""
    return isinstance(node, ast.Subscript) and _is_notes_attr(node.value)


def _mentions_notes_call(node) -> bool:
    """`getattr(x, "notes")` —— 结果**当场**被当作写入口用的时候才算写点。"""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == "notes")


def _notes_writes_in_source(source: str, path: str) -> list:
    """这份源码里所有「往某个 `.notes` 里塞东西」的地方（`(文件:行, 在哪个函数里)`）。

    认**六种**塞法（复审 §2.2/2.3 逐条点过；前三种是原来就有的，后三种是修复轮 1 补的）：

      1. `x.notes.append(…)` / `.extend(…)` / `.insert(…)`
      2. `x.notes = […]`（换掉整本）
      3. `x.notes[i] = …`（下标赋值）
      4. **`x.notes += […]`**（`AugAssign` —— 复审实测这一种**原来全绿**）
      5. **`getattr(x, "notes").append(…)`**（间接取属性 —— 原来全绿）
      6. **`list.append(x.notes, …)`**（把 list 方法当函数调 —— 原来全绿）

    ⚠️ 它认的是**属性名恰好是 `notes`**（`job.shot_notes` 不算 —— 那是另一本账）。
    ⚠️ **它管不到的两处**（`test_..._one_door_...` 的 docstring 里也写着）：
    **别名**（`n = journey.notes; n.append(…)`）与**绕开点号**的写法（`vars()/setattr` 那一族）
    —— 那两种要抓得做数据流分析，这一条不做；它们靠 code review，不靠这条守。
    """
    tree = ast.parse(source)
    found = []

    def note_at(node, funcs):
        found.append(("%s:%d" % (path, node.lineno), ".".join(funcs)))

    def walk(node, funcs):
        for child in ast.iter_child_nodes(node):
            name = funcs
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = funcs + [child.name]
            # ── 1 / 5 / 6：`x.notes.append(…)` / `getattr(x,"notes").append(…)` /
            #              `list.append(x.notes, …)` ──
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in _NOTES_METHODS:
                receiver = child.func.value
                if _is_notes_attr(receiver) or _mentions_notes_call(receiver) \
                        or any(_is_notes_attr(a) for a in child.args):
                    note_at(child, name)
            # ── 5b：`setattr(x, "notes", …)`（同族的间接写法，顺手一起认）──
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) \
                    and child.func.id == "setattr" and len(child.args) >= 2 \
                    and isinstance(child.args[1], ast.Constant) \
                    and child.args[1].value == "notes":
                note_at(child, name)
            # ── 2 / 3：`x.notes = […]` 与 `x.notes[i] = …` ──
            if isinstance(child, ast.Assign) and any(
                    _is_notes_attr(t) or _is_notes_subscript(t) for t in child.targets):
                note_at(child, name)
            # ── 4：`x.notes += […]`（AugAssign —— 这一条是修复轮 1 补的洞）──
            if isinstance(child, ast.AugAssign) and (
                    _is_notes_attr(child.target) or _is_notes_subscript(child.target)):
                note_at(child, name)
            walk(child, name)

    walk(tree, [])
    return found


def test_the_notes_list_has_exactly_one_write_entry(tmp_path):
    """**这一类**：`journey.notes` 只许有一个写入口（`Journey.note`）。

    为什么要有这一条（这一片的病史：六轮「修了被点名的那处」）：消毒机器接在**每个写点**上，
    就得靠**每个人**每次都记得接 —— 而这正是 `_utf8_safe` 的注释里那句「不能靠写的人小心」。
    收成一个口子之后，新加一个写点会**自动**走消毒；而「绕过口子直接往 list 里塞」
    由这一条当场拦下（红的是一行源码，不是一个字节）。

    ⚠️ **射程表**（复审 §2.2/2.3 逐条量过；**别把这条守的承诺想大**）：

    | 绕过法 | 这条守 | 备注 |
    |---|---|---|
    | `x.notes.append(…)` / `.extend(…)` / `.insert(…)` | **红** | |
    | `x.notes = […]`（换掉整本）/ `x.notes[i] = …` | **红** | |
    | `x.notes += […]` | **红** | 修复轮 1 补的（复审 M8b：**原来全绿**） |
    | `getattr(x, "notes").append(…)` / `setattr(x, "notes", …)` | **红** | 修复轮 1 补的（复审探针） |
    | `list.append(x.notes, …)` | **红** | 修复轮 1 补的（复审探针） |
    | **扫描范围**：`agent/` 下**全部 17 个 `.py`** | —— | 修复轮 1 扩过（复审 M13/M14：原来只扫 7 个，`fix.py`/`events.py` 里写点全绿） |
    | `n = journey.notes; n.append(…)`（**别名**） | **不红** | 要数据流分析；靠 code review |
    | `Journey(notes=[…])`（**构造注入**） | **不红** | 见下面「挡不住的」 |
    | 把 `Journey` 整个换成别的对象 | **不红** | 同上 |

    **一条用例钉不住「没有第四条路」，只钉得住「这六种塞法一条都不许有」。**
    """
    writes = []
    for path in sorted((ROOT / "agent").glob("*.py")):        # ← **全部** agent/*.py
        writes.extend(_notes_writes_in_source(path.read_text(encoding="utf-8"),
                                              "agent/" + path.name))
    assert writes, "一个写点都没扫到 —— 这条守瞎了（`Journey.note` 自己那一行总该在）"
    outside = [w for w in writes if w[1] != "Journey.note"]
    assert outside == [], (
        "`journey.notes` 除了 `Journey.note` 之外还有写点（那里没有消毒机器）：%r" % (outside,))
    assert [w[1] for w in writes] == ["Journey.note"], (
        "`Journey.note` 这个写入口不见了（整台消毒机器就没了）：%r" % (writes,))

    # 正控：这条守**看不见**的两种写法 —— 钉在这里，免得下一个读的人以为「都挡住了」
    for shape, src in {
        "别名：先取出来再 append": "def f(journey):\n    n = journey.notes\n    n.append('x')\n",
        "构造注入": "def f():\n    return Journey(notes=['x'])\n",
    }.items():
        assert _notes_writes_in_source(src, "<探针>") == [], (
            "「%s」居然被这条守抓到了 —— 那射程表该改（这条正控的前提没了）" % shape)


def test_the_text_appears_expect_reports_the_bytes_it_lost(tmp_path, monkeypatch):
    """**「报了没报」这一类**（复审 §3.1 点名：补丁 B 一个字没提 `text_appears`）。

    为什么单列它：`expects` 那道消毒缝里，`text_appears` 是**最哑**的一支 ——
    它的裁判话是**固定的一句**（`_meets` 不引期望原文），换掉时**最容易一声不吭**
    （补丁 A 修复轮 1 的 commit 里那句「那道缝在 `text_appears` 那一支**不报数**」说的就是它）。
    ⇒ 这一条把那一类**量一次**：两支都要报数，`text_appears` 那支**尤其**要。

    走**真链**（真子进程 MCP、真 `explore`、真 `/live`），与 `test_service_steps` 同一台量具。
    """
    from test_service_steps import PERFORMED, _run_job, _verdict_of

    for kind, value in (("url_contains", "funnel" + BAD), ("text_appears", "Thank you" + BAD)):
        _, _, seen, _ = _run_job(tmp_path / kind, monkeypatch, click={"structured": PERFORMED},
                                 expects=[{kind: value}] * 3, name="rep" + kind[:4],
                                 ascii_json=True)
        judged = _verdict_of(seen, 1)
        assert judged["data"]["expect"] == {kind: value.replace(BAD, SAFE)}, judged["data"]
        assert "写不出来" in judged["say"], (
            "`%s` 那一支换掉了字节却一声不吭 —— 那正是一条**静默路径**：%r"
            % (kind, judged["say"]))


# ══════════ ④ `POST /run` 的 `expects`：它只归服务（修复轮 1 起也在门口换）══════════


def test_expects_only_ever_reaches_the_service(tmp_path):
    """`expects` **只归服务**（契约 §四：期望来自业务，不能来自执行者）。

    ⚠️ **这一条守的是什么，得说准**（补丁 B 复审 §3.3 点名：我原来给它挂的牌子是
    「那个点名的例外今天还安全吗」，那句**承重的话是错的**——见下）：

      - **不是**「它到不了 `.encode("utf-8")` 那一层」—— **它到得了**：
        它的**值**经 `brief` 走到 `_note_step`，进 `/live` 的 `data.expect`
        （`service.py:2335 → 2352 → 2453`，既有断言一直在量它）；
      - **是**「它不许越到**脚本**那一侧去」。三格量的是这件事的形状：
        图收到的那份载荷里没有它（`_payload` 的 keep 清单）、`graph.py` / `state.py`
        一个字都不提它。

    ⚠️ **它护不住真正的缝**（复审 §3.3 的 M9 实测）：那道缝（`judged["expect"] =
    `events.safe_value(expect)[0]`）被拆掉时，这一条**照绿** —— 抓住它的是**既有**用例
    `test_service_steps.py::test_a_receipt_with_unwritable_bytes_…`。
    ⇒ **别把这一条读成「那道缝有人守着」。** 那一条缝由那条既有用例守；
    修复轮 1 起门口也换了一次（`_Intake`），所以缝被拆也还有门口那一道。
    """
    src = (ROOT / "agent" / "graph.py").read_text(encoding="utf-8")
    assert "expects" not in src, "`expects` 进了图 —— 「期望只归服务」这条边界破了"
    state_src = (ROOT / "agent" / "state.py").read_text(encoding="utf-8")
    assert "expects" not in state_src, "`expects` 进了 state —— 那条边界破了"

    keep = service.Service._payload({"url": URL, "goal": "看看", "expects": [{"url_contains": "/x"}]})
    assert "expects" not in keep, (
        "`_payload` 把 `expects` 往下发了 —— 那条边界破了：%r" % (sorted(keep),))
