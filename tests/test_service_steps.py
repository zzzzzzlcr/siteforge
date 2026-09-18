"""契约 §六 那条**验收**：把 `receipt` 改成「元素没找到」，系统看得出与「成了」不一样。

判据的原文（`docs/执行事实契约-2026-09-18.md` §六）：

> **把某个脚本的 `receipt` 改成「元素没找到」，而它今天报的是 `success`** ——
> 系统**必须**看得出这两者不一样。

这一版之前，那个「系统」**只会把 `receipt` 原样回显**（复审 2026-09-18 实测：`/live`
十五个字段没有一个是按 receipt 算出来的）。这份文件钉的是**换裁判之后**的样子：

    同一条脚本、同一个页面、同一个模型剧本 —— 只把 CDP 的回执换掉，
    `verdict` 必须不同，而且那个不同**是服务算出来的**。

## 这条链上每一跳都是真的（除了两个必须的桩）

    真 HTTP（TestClient → 真 ASGI app）
      → 真 `Service.start()` / `_advance` / 工作线程
        → 真 `browser_agent.explore`（`dispatch` / `_describe` / `_summarize` 全是真的）
          → 真 `tools.McpSession` + 真 `Popen`
            → 子进程：wrapper 脚本 → `tests/stub_mcp_server.py`（**真 JSON-RPC**，不开浏览器）
        → 真 `Service._note_step`（脚本那条 + 服务判的那条）
          → 真 `/live`（`GET /job/{id}/live`，量的是**原始响应体**）

两个桩都是**必须**的、而且桩的不是被测物：**图**（`_ExploreGraph` 里 `invoke` 只管调
`deps.explore` —— 服务拼好的那根真线）与**模型**（`FakeLLM`：这一步走哪几下由剧本定，
不然两次运行没法只差一个回执）。

⚠️ 「只差一个回执」是这条判据的**全部要害**：两次运行的页面、脚本、模型剧本
逐字节相同 —— 于是 `/live` 上任何不是 `receipt` 本身的差别，都只可能来自**裁判**。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, events, graph, llm, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example.test/funnel"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"

#: CDP 那边**真**的一句回执（`tools/cdp/internal/mcp/handlers.go` 的 `performed()`：
#: 写类动作（click / form / scroll）成功时回的就是这份，一个字节都不差）。
PERFORMED = {"ok": True,
             "note": "动作已下发（没有报错）。它有没有推进页面，用 diff 比一比才知道"
                     "—— 别把这条读成成功。"}

#: 同一句话的**另一侧**：工具没成时 cdp-mcp 回的那串错（走 MCP 的 `isError`）。
NOT_FOUND = "元素没找到：`#get-started` 在这个页面上解析不出来（ClickElementStrict）"


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


# ─────────────────────────── 桩：图（探路那一跳是真的）───────────────────────────


class _Snap:
    def __init__(self, values, next=(), interrupts=()):
        self.values = values
        self.next = next
        self.interrupts = interrupts


class _ExploreGraph:
    """`invoke` 里**真跑一趟探路**（`deps.explore` 是服务拼好的那根真线）。

    图不是被测对象 —— 被测的是「探路报上来的每一步，服务怎么看」。所以这里只做一件事：
    把 `deps.explore` 调起来，然后交一个终局快照。
    """

    def __init__(self, deps, url=URL, goal=GOAL):
        self.deps = deps
        self.url, self.goal = url, goal
        self.journeys: list = []
        self.payloads: list = []

    def invoke(self, payload, config):
        self.payloads.append(dict(payload))
        self.journeys.append(self.deps.explore(payload.get("url") or self.url,
                                               payload.get("goal") or self.goal))
        return _END

    def get_state(self, config):
        return _Snap(values=dict(_END), next=(), interrupts=())


_END = {"site": SITE, "ws_url": WS_URL, "visits": ["intake"],
        "end_reason": "explore_unfinished", "end_note": "探了一趟就收工。"}


def _stub(tmp_path, responses, name="program"):
    """起一个**桩 MCP 服务**（真子进程、真 JSON-RPC）+ 一个 exec 它的 wrapper。

    走 wrapper 这条路的理由与 `test_service_shots` 那条一样：**服务闭包 → 真 `explore`
    → 真 `McpSession` → 真 `Popen` → 子进程**，这一整条链上没有一个地方被换掉。
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    program = tmp_path / ("%s.json" % name)
    # ⚠️ `ensure_ascii=True`：这份程序文件本身也得是**写得出去**的 —— 「回执是任意字节」
    # 那条判据要往里面放一个孤立代理对，而它写不进 UTF-8 文件（写进去的是**转义序列**
    # `"\ud800"`，纯 ASCII，`json.load` 读回来还是那个代理对）。
    program.write_text(json.dumps({"responses": responses}, ensure_ascii=True),
                       encoding="utf-8")
    wrapper = tmp_path / ("%s-wrapper" % name)
    wrapper.write_text('#!/bin/sh\nexec %s %s "%s"\n'
                       % (sys.executable, ROOT / "tests" / "stub_mcp_server.py", program),
                       encoding="utf-8")
    wrapper.chmod(0o755)
    return str(wrapper)


def _run_job(tmp_path, monkeypatch, *, click, page=None, page_after=None, expects=None,
             name="program", url=URL, ascii_json=False):
    """一趟**真的**走完的 job → `(client, job_id, 时间线上的事件, 图的盒子)`。

    `click` 就是 CDP 那一侧的回执（`{"structured": …}` = 成了 / `{"error": …}` = 没成）；
    其它一切（页面、模型剧本、期望）两次运行里逐字节相同。
    """
    from test_browser_agent import PAGE_LANDING, FakeLLM

    turns = [{"calls": [("observe", {})]},
             {"calls": [("click", {"selector": "#get-started"})]},
             {"calls": [("observe", {})]},
             {"content": "看完了"}]
    first = page or PAGE_LANDING
    responses = {"observe": [{"structured": first},
                             {"structured": page_after or first}],
                 "click": [click]}
    monkeypatch.setattr(llm, "client", lambda: FakeLLM(turns))

    box: dict = {}

    def factory(brief, deps):
        box["graph"] = _ExploreGraph(deps, url=url)
        return box["graph"]

    brief = {"url": url, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    if expects is not None:
        brief["expects"] = expects
    client = TestClient(service.create_app(
        graph_factory=factory, window=None, capture=lambda *a, **k: (None, "桩里不拍"),
        checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST),
        mcp_bin=_stub(tmp_path, responses, name=name)))
    if ascii_json:
        # ⚠️ 载荷里塞孤立代理对时**只能**这么发：UTF-8 字节里装不下它，
        # 而 `"\ud800"` 这个**转义序列**是纯 ASCII —— Python 的 `json.dumps` 默认
        # （`ensure_ascii=True`）就是这么发的，服务端 `json.loads` 解出来就是那个代理对。
        # 这条路上它**到得了**服务（`TestClient.post(json=…)` 那条发不出去：编码当场就炸）。
        r = client.post("/run", content=json.dumps(brief, ensure_ascii=True).encode("ascii"),
                        headers={"content-type": "application/json"})
    else:
        r = client.post("/run", json=brief)
    job_id = r.json()["job_id"]
    client.app.state.service._queue.join()
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, "整条时间线读不出来了：%s" % r.text[:400]
    return client, job_id, r.json()["events"], box


def _of(events_list, who, action=None):
    """时间线上**某一方**说的那些 `step` 事件（脚本那条 / 服务那条）。"""
    out = [e for e in events_list if e["kind"] == "step" and e["who"] == who]
    if action is not None:
        out = [e for e in out if (e["data"].get("action") or {}).get("what") == action]
    return out


def _verdict_of(events_list, step_no):
    hit = [e for e in _of(events_list, "system")
           if e["data"].get("step_no") == step_no and "verdict" in e["data"]]
    assert hit, "第 %s 步没有裁判那条事件：%r" % (step_no, events_list)
    return hit[0]


# ═══════════════════ 一、契约 §六：两条路必须被分开 ═══════════════════


def test_a_receipt_that_says_not_found_is_not_the_same_as_one_that_says_sent(
        tmp_path, monkeypatch):
    """**§六 那条验收**：只换回执，`verdict` 必须不同 —— 而且不同**来自裁判**。

    两次运行逐字节相同（同一个页面、同一条脚本、同一份模型剧本、同一个期望），
    **唯一的差别是 CDP 那一句回执**：

    | | 路径 A | 路径 B |
    |---|---|---|
    | 回执 | `performed()`（动作下发了） | `isError` + 「元素没找到」 |
    | 脚本自己那句人话 | 「点了「Get Started」」 | 「页面上没找到…没做成」 |
    | **服务判的 `verdict.sent`** | **True** | **False** |
    | **服务判的那句话** | 「动作**已经下发**…」 | 「**这一步没有发生**…别当成功」 |

    要害在最后两行：**同一句「页面没变」，两处的意思完全相反** ——
    路径 A 是「动作真送出去了但没生效」，路径 B 是「这一步压根没发生」。
    这个区别**不在回执里**（回执只说「没找到」），它是裁判拿 Action Truth 去读 State Truth
    读出来的。
    """
    _, _, good, _ = _run_job(tmp_path / "a", monkeypatch, click={"structured": PERFORMED},
                             name="a")
    _, _, bad, _ = _run_job(tmp_path / "b", monkeypatch, click={"error": NOT_FOUND}, name="b")

    # ① 两条路都走到了同一个地方（第 2 步就是那一下点击）
    click_good = _of(good, "agent", action="click")
    click_bad = _of(bad, "agent", action="click")
    assert len(click_good) == 1 and len(click_bad) == 1
    assert click_good[0]["data"]["step_no"] == click_bad[0]["data"]["step_no"] == 2

    # ② 回执**逐字**到了线上，而且两条路的回执确实不一样（这是「回显」那一半，必要不充分）
    assert click_good[0]["data"]["receipt"] == PERFORMED, click_good[0]["data"]["receipt"]
    assert click_bad[0]["data"]["receipt"]["isError"] is True
    assert NOT_FOUND in click_bad[0]["data"]["receipt"]["text"]
    assert click_good[0]["data"]["receipt"] != click_bad[0]["data"]["receipt"]

    # ③ **判**：两条路的 verdict 必须不同，而且都带着三层各自的答案（不是回执的副本）
    va = _verdict_of(good, 2)
    vb = _verdict_of(bad, 2)
    assert set(va["data"]["verdict"]) == {"sent", "changed", "met"}, va["data"]["verdict"]
    assert va["data"]["verdict"]["sent"] is True, va["data"]["verdict"]
    assert vb["data"]["verdict"]["sent"] is False, vb["data"]["verdict"]
    assert va["data"]["verdict"] != vb["data"]["verdict"], "两条路判成了一样 —— 没换裁判"

    # ④ 判的那句话是**结论**，不是回执的复读：路径 B 必须说「这一步没有发生」
    assert "没有发生" in vb["say"], vb["say"]
    assert "没有发生" not in va["say"], va["say"]
    assert "已经下发" in va["say"], va["say"]
    # 而且回执的原文**没有**混进裁判那句话里（裁判说的是自己的判断，不是转抄）
    assert NOT_FOUND not in vb["say"], vb["say"]
    assert PERFORMED["note"] not in va["say"], va["say"]

    # ⑤ 逐条钉住「这是算的」：回执那一格与判那一格**是两个不同的人写的两句话**
    assert click_bad[0]["who"] == "agent" and vb["who"] == "system"


def test_the_page_not_changing_says_two_different_things_on_the_two_paths(tmp_path, monkeypatch):
    """**同一句「页面没变」，两处意思相反** —— 这是 §六 那条判据的内核。

    路径 A：动作发出去了、页面**没变**（两次观测的原始签一模一样）。
    路径 B：动作根本没发出去，页面当然也没变。

    两条路上「页面没变」这个**观测**是同一个，而**结论**必须是两个：
    前者 = 「这一步没生效」，后者 = 「这一步没发生」。把两句合成一句，
    读的人就永远分不出来 —— 而 `datewhirl` 的病正是从这里长出来的。
    """
    _, _, good, _ = _run_job(tmp_path / "a", monkeypatch, click={"structured": PERFORMED},
                             name="a")
    _, _, bad, _ = _run_job(tmp_path / "b", monkeypatch, click={"error": NOT_FOUND}, name="b")

    for name, seen in (("A", good), ("B", bad)):
        # 页面确实**没变**：第 3 步那一眼里，`sig_before` 与 `sig_after` 一模一样
        look = _of(seen, "agent", action="observe")[-1]
        assert look["data"]["sig_before"] == look["data"]["sig_after"], (name, look["data"])
        assert _verdict_of(seen, 3)["data"]["verdict"]["changed"] is False, name
        # 「页面没变」这一层两条路**一样**（State Truth 分不出它们）
        assert _verdict_of(seen, 3)["data"]["verdict"] == {"sent": True, "changed": False,
                                                           "met": None}, name

    # 而「这一步发出去没有」这一层把它们分开了 —— 差异**只**来自 Action Truth
    assert _verdict_of(good, 2)["data"]["verdict"]["sent"] is True
    assert _verdict_of(bad, 2)["data"]["verdict"]["sent"] is False


# ═══════════════ 二、两份原始签：是**算出来**的，不是摘要冒充的 ═══════════════


def test_the_signature_is_raw_observation_not_our_own_summary(tmp_path, monkeypatch):
    """`sig_*` 必须是「那一页的原始签」：url + 正文指纹 + 可见元素计数。

    ⚠️ 复审点名：**不许拿 `Journey.steps[].state` 或 `pages[].url/title` 冒充** ——
    那两样是**我们自己写的摘要**（状态名是我们起的 slug），拿它当签等于让裁判去读
    被测量者自己写的报告。所以这条判据逐条对着三样：指纹是**十六进制**（不是状态名）、
    计数是**页面上那几条元素**、url 是页面报的那个。
    """
    _, _, seen, box = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED})
    look = _of(seen, "agent", action="observe")[0]
    sig = look["data"]["sig_after"]
    assert set(sig) == {"url", "text", "visible"}, sig
    assert sig["url"] == "https://example.test/funnel"
    assert len(sig["text"]) == 12 and all(c in "0123456789abcdef" for c in sig["text"]), sig
    # 那一页上 1 个 action + 0 个 field + 0 个 option_group（`PAGE_LANDING` 是这么写的）
    assert sig["visible"] == 1, sig
    # 与「我们自己写的摘要」**同在**、但**不是它**：状态名是我们起的 slug（`funnel`）、
    # 标题是页面自报的一句话 —— 三样都在，而签谁也不借（复审点名的那两样正是前两样）。
    journey = box["graph"].journeys[0]
    assert journey.pages[0]["name"] == "funnel"
    assert journey.pages[0]["url"] == sig["url"]
    assert sig["text"] != journey.pages[0]["name"]
    assert sig["text"] != journey.pages[0]["title"]


def test_a_page_that_really_changed_is_reported_as_changed(tmp_path, monkeypatch):
    """页面**真变了**的时候，裁判必须说「变了」—— 与上一条同一把尺子（正文指纹）。

    这一条同时是「`changed` 是算出来的」那一半的正对照：上一条的 False 与这一条的 True
    出自**同一个比较**，而两次运行的差别在**页面**上（回执一模一样）。
    """
    from test_browser_agent import PAGE_LANDING

    other = dict(PAGE_LANDING)
    other["url"] = "https://example.test/funnel?step=2"
    other["page_text"] = "你多久用一次？每天 每周 很少"
    _, _, seen, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED},
                             page_after=other)
    look = _of(seen, "agent", action="observe")[-1]
    assert look["data"]["sig_before"] != look["data"]["sig_after"]
    assert _verdict_of(seen, 3)["data"]["verdict"]["changed"] is True
    assert "变了" in _verdict_of(seen, 3)["say"]


# ═══════════════ 三、`expect`：运营写、从载荷进来、菜单是封闭的 ═══════════════


def test_the_operator_can_say_i_cannot_tell_and_that_is_a_first_class_value(tmp_path, monkeypatch):
    """契约 §四：**「未声明」是一等值，而且必须能选** —— 不许逼运营编一个。

    这一条钉三样：① 载荷里能写 `未声明`；② 它一路到 `/live` 原样；③ 裁判在那一格
    明说「Business Truth 就是不知道」，**不拿动作的回执冒充**。
    """
    _, _, seen, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED},
                             expects=[events.UNDECLARED, events.UNDECLARED,
                                      events.UNDECLARED])
    judged = _verdict_of(seen, 2)
    assert judged["data"]["expect"] == events.UNDECLARED
    assert judged["data"]["verdict"]["met"] is None
    assert "不知道" in judged["say"], judged["say"]


def test_an_operator_who_wrote_nothing_is_not_the_same_as_one_who_said_i_cannot_tell(
        tmp_path, monkeypatch):
    """「**没写**」与「说了**说不好**」在纸上必须分得开（这一版把它们分成两格）。

    载荷里只写了第 1 步的期望 ⇒ 第 2 步那一格**键不在**（「这一格没人碰过」），
    而第 1 步是 `未声明`（「他说不好」）。合成一个就是在替运营说一句他没说过的话。
    """
    _, _, seen, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED},
                             expects=[events.UNDECLARED])
    first, second = _verdict_of(seen, 1), _verdict_of(seen, 2)
    assert first["data"]["expect"] == events.UNDECLARED
    assert "expect" not in second["data"], second["data"]
    assert "没写" in second["say"], second["say"]
    assert "没写" not in first["say"], first["say"]


def test_a_declared_expectation_is_judged_against_the_signature(tmp_path, monkeypatch):
    """运营声明的那种（菜单里的 `url_contains`）：裁判拿**动作之后那一页的地址**去比。"""
    _, _, seen, _ = _run_job(tmp_path, monkeypatch, click={"structured": PERFORMED},
                             expects=[{"url_contains": "/funnel"}, {"url_contains": "/funnel"},
                                      {"url_contains": "/funnel"}])
    assert _verdict_of(seen, 1)["data"]["verdict"]["met"] is True
    assert "对上了" in _verdict_of(seen, 1)["say"]

    _, _, miss, _ = _run_job(tmp_path / "miss", monkeypatch, click={"structured": PERFORMED},
                             expects=[{"url_contains": "/wizard"}], name="miss")
    assert _verdict_of(miss, 1)["data"]["verdict"]["met"] is False
    assert "没对上" in _verdict_of(miss, 1)["say"]


def test_the_menu_is_closed_and_a_bad_shape_is_refused_at_submission(tmp_path, monkeypatch):
    """菜单是**封闭**的：不认识的形状在**提交那一刻**就被拒（免费的那道闸）。

    为什么非要拒（复审 2026-09-18 实测）：`{"随便什么": 1}` / `"Thank you"` / `["/wizard"]`
    与四种对的值**一模一样地收下** ⇒ 「机器怎么判」那一列在代码里一行都没有。
    """
    from test_browser_agent import FakeLLM

    monkeypatch.setattr(llm, "client", lambda: FakeLLM([{"content": "算了"}]))
    client = TestClient(service.create_app(
        graph_factory=lambda brief, deps: _ExploreGraph(deps), window=None,
        capture=lambda *a, **k: (None, "桩里不拍"),
        checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)))
    base = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE, "ws_url": WS_URL}
    for bad in ({"随便什么": 1}, "Thank you", ["/wizard"], {"url_contains": ""},
                {"url_contains": "/a", "text_appears": "b"}):
        r = client.post("/run", json={**base, "expects": [bad]})
        assert r.status_code == 400, "这个形状被收下了：%r" % (bad,)
        assert "expects" in r.json()["detail"], r.text


def test_the_script_never_sees_the_expectation(tmp_path, monkeypatch):
    """契约 §四：期望**来自业务，不来自执行者** —— 所以它一个字节都不往下发。

    判据是**真的那一份载荷**：图收到的那份里不许有 `expects`（那是服务判它用的），
    而服务自己手里那份要有（不然裁判没得判）。两件事一起看才作数 —— 只看一头的话，
    「谁都没拿到」也能让这条绿。
    """
    _, _, seen, box = _run_job(
        tmp_path, monkeypatch, click={"structured": PERFORMED},
        expects=[{"url_contains": "/funnel"}, events.UNDECLARED, events.UNDECLARED])
    graph_obj = box["graph"]
    # ① 图（= 探路那一侧）收到的那份载荷里**不许有** `expects`
    assert graph_obj.journeys, "探路压根没跑"
    assert graph_obj.payloads, "图没收到过载荷"
    assert "expects" not in graph_obj.payloads[0], sorted(graph_obj.payloads[0])
    # ② 服务自己手里那份要有（不然裁判没得判）—— 两件事一起看才作数
    assert _verdict_of(seen, 1)["data"]["verdict"]["met"] is True


# ═══════════════ 四、回执是**任意字节**：脏字节不许把整条时间线打掉 ═══════════════


def test_a_receipt_with_unwritable_bytes_does_not_take_the_whole_timeline_down(
        tmp_path, monkeypatch):
    """**回执是任意字节**（契约 §二③：那格里是 CDP 的原话，脚本逐字转抄）。

    一个**孤立代理对**过得了 `json.dumps`、过不了最后那次 `.encode("utf-8")` ——
    撞上的后果不是那一条事件坏掉，是 `/live` **500、整条时间线一条都读不出来**
    （Task 4 收口复审实测）。所以转抄那一刻就换掉（`events.safe_value`），
    并**数出来**（有损必须说）。

    这一条走的是**真**那条链（真子进程、真 JSON-RPC、真 `explore`、真 `/live`），
    而且**两个出口都塞了脏字节**：

      - **`data` 那一侧**：回执原文里有代理对 → `_utf8_safe` 换掉 + 记个数；
      - **`say` 那一侧**（事件外壳）：页面地址里有代理对，而裁判那句话会**引它**
        （运营声明的 `url_contains` 要比对地址）→ `Service.narrate` 换掉 + 记个数。

    桩 MCP 服务用 `ensure_ascii=True` 把代理对写成 `\\ud800` 这个**转义序列**
    （纯 ASCII，管道过得去），Python 侧 `json.loads` 解出来就是真的孤立代理对。
    """
    from test_browser_agent import PAGE_LANDING

    dirty = "元素没找到：%s" % "\ud800"          # 一个真的孤立代理对
    page = json.loads(json.dumps(PAGE_LANDING, ensure_ascii=True))
    page["url"] = "https://example.test/funnel?x=\ud800"
    _, _, seen, _ = _run_job(tmp_path, monkeypatch, click={"error": dirty}, page=page,
                             expects=[{"url_contains": "funnel\ud800"}] * 3, name="dirty",
                             ascii_json=True)

    # ① `/live` 没 500（`_run_job` 已经断过 200），而且**每一条**都还读得出来
    assert seen, "时间线是空的"
    assert all(isinstance(e["say"], str) and e["say"] for e in seen), seen
    assert "\ud800" not in json.dumps(seen), "脏字节还是漏到线上了"
    # ② `data` 那一侧（CDP 回执原文）：换成了 `�`，而且**说清了换了几个字节**
    hit = _of(seen, "agent", action="click")[0]
    assert "unwritable_bytes" in hit["data"]["why"], hit["data"]["why"]
    assert "写不出来" in hit["data"]["why"]["unwritable_bytes"]
    # ③ 页面那一侧：地址里的脏字节同样换掉了（裁判那句话把它引出来时已经是 `�`）
    assert "�" in _verdict_of(seen, 1)["say"], _verdict_of(seen, 1)["say"]
    # ④ `say` 那一侧（**事件外壳**）：运营写的期望里也有一个 —— 裁判那句话直接引它，
    #    于是 `Service.narrate` 那道闸当场报数（不报的话 `/live` 会 500，整条都读不出来）
    judged = _verdict_of(seen, 1)
    assert "写不出来" in judged["say"], judged["say"]
    assert judged["data"]["expect"] == {"url_contains": "funnel�"}, judged["data"]["expect"]
    # ⑤ 脏字节不许把「判」也带走 —— 裁判照样判得出来
    assert _verdict_of(seen, 2)["data"]["verdict"]["sent"] is False


def test_the_event_shell_hole_is_still_open_at_the_timeline_level(tmp_path, monkeypatch):
    """**已知缺陷**（Task 4 收口复审判「响」，进停办清单）：闸的刻度只到 `data`。

    `Timeline.add(kind, say)` 的 **`say`（事件外壳）没有过同一把尺子** ——
    一个孤立代理对从正门收下，随后 `/live` **500、整条时间线一条都读不出来**。

    ⚠️ 这条判据**钉的是那个洞还在**（不是钉它该在）。生产唯一的写入口是
    `Service.narrate`，那一层 2026-09-18 起挡了（`events.safe_value` + 一句报数）——
    所以**这条路走不到**生产。`events.py` 那份 schema 刚被独立复审验过，这一轮
    不动它（动它要先说明为什么），于是把洞**钉成一条判据**：
    谁哪天把它修好了，这条会红 —— 那时候连 `narrate` 那道闸一起删，别留两条判据。
    """
    from test_browser_agent import FakeLLM

    monkeypatch.setattr(llm, "client", lambda: FakeLLM([{"content": "算了"}]))
    client = TestClient(service.create_app(
        graph_factory=lambda brief, deps: _ExploreGraph(deps), window=None,
        capture=lambda *a, **k: (None, "桩里不拍"),
        checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)),
        raise_server_exceptions=False)
    job_id = client.post("/run", json={"url": URL, "goal": GOAL, "success_text": SUCCESS,
                                       "site": SITE, "ws_url": WS_URL}).json()["job_id"]
    job = client.app.state.service._jobs[job_id]
    # 绕过唯一写入口，直接照复审那条实验做一遍
    job.timeline.add("step", "看一眼页面\ud800", who="agent")
    assert client.get("/job/%s/live" % job_id).status_code == 500, \
        "洞被修好了？那这条判据与 `Service.narrate` 里那道闸都该删掉。"


# ═══════════════ 五、第四个人：`expect` 是运营的，脚本一个字都插不上 ═══════════════


def test_the_script_cannot_declare_a_verdict_or_an_expectation(tmp_path, monkeypatch):
    """契约 §二①：**脚本只填前五格** —— 第 6 格是运营的、第 7 格永远不是它的。

    判据在词表那一侧（`events.VOICE_KINDS` / `SCRIPT_MAY_NOT_FILL`），这里拿**真**
    服务那条路验一次：脚本的嗓子（`who="agent"`）说 `verdict` / `expect` 会当场抛，
    而这条抛会不会带塌探路（旁路坏掉不许带塌主路）。
    """
    import pytest as _pytest

    with _pytest.raises(ValueError) as err:
        events.Timeline().add("step", "点了「下一步」", who="agent",
                              data={"step_no": 1, "verdict": {"changed": True}})
    assert "verdict" in str(err.value) and "前五格" in str(err.value)
    with _pytest.raises(ValueError):
        events.Timeline().add("step", "点了「下一步」", who="agent",
                              data={"step_no": 1, "expect": events.UNDECLARED})


def test_the_judge_is_a_pure_function_of_what_the_others_wrote():
    """裁判只吃**别人写下来的东西**（回执 / 两份签 / 运营的期望），三层分开报。

    这条是上一组端到端判据的**单元面**：三个值各自独立，谁也不替谁说话 ——
    合成一个 `success` 正是 `datewhirl` 那个病的形状。
    """
    sent = {"ok": True, "note": "动作已下发"}
    sig = {"url": "https://x/1", "text": "aaaaaaaaaaaa", "visible": 1}
    other = {"url": "https://x/2", "text": "bbbbbbbbbbbb", "visible": 3}

    got, say = service.judge_step(step_no=1, receipt=sent, sig_before=sig, sig_after=other,
                                  expect=events.UNDECLARED, expect_present=True)
    assert got == {"sent": True, "changed": True, "met": None}, got

    got, _ = service.judge_step(step_no=1, receipt={"isError": True, "text": "没找到"},
                                sig_before=sig, sig_after=sig, expect=None, expect_present=False)
    assert got == {"sent": False, "changed": False, "met": None}, got

    # 量不到就是量不到：不许折算成「没变化」（契约 §二②）
    got, say = service.judge_step(step_no=1, receipt=sent, sig_before=sig, sig_after=None,
                                  expect=None, expect_present=False)
    assert got["changed"] is None and "量不到" in say, (got, say)
