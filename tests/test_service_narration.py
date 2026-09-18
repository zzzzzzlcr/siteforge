"""Task 5：长节点会说话 —— `on_note` / `on_run` / 步拍那条 `shot_missing`。

`on_step` 那条线 Task 4 就通了（`Service._step_teller`）。这一片补的是**另外两条**，
外加设计注 §3.2 目录表第 6 行少的那一半：

| # | 事实 | 谁在说 | 时间线上的 `kind` | 这里的用例 |
|---|---|---|---|---|
| 10 | 模型每一轮的推理（`_Gate._note` 的 `AI 说：…`） | agent | `agent_said` | `…what_the_model_says…` |
| 10 | 脚本每一步的人话（`on_step`，Task 4 已通） | agent | `step` | `…every_step_the_script_reports…` |
| 3 | 自测**每一遍**的结果（含没跑的） | system | `selftest_run` | `…a_round_that_did_not_run…` / `…every_run…` |
| 6 | **步拍**没成（这一半原先没有） | system | `shot_missing` | `…a_step_whose_shot_did_not_land…` |

## 两条铁律（这一片最容易写歪的两处，所以各有一条用例钉着）

1. **回调抛异常 → 探路 / 自测照常跑完**（旁路坏掉不许带塌主路，同 Task 2 那条）。
   但**不许静默**：坏掉这件事自己也要留下来（探路落 `journey.notes`、自测落报告）。
2. **`on_*` 默认 `None` → 今天的行为一个字节不变**（`_two_readers` 全是 `None` 就返回
   `None`，也就是**这条线压根不接**）。

## 桩的形状（为什么这么桩）

服务要的 `Deps.explore` / `Deps.selftest` 是**服务自己拼好的那根线**
（窗口、账本、时间线、二进制、`run_dir` 全在里面）。所以被测的「接线」只有从
`Deps` 那一头叫进来才验得到 —— 探路桩换的是**库函数** `browser_agent.explore`、
自测桩换的是 `selftest.run`，**不是** `Deps` 上那两个字段。
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sys
import time
import types
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, events, graph, selftest, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"

#: 模型这一轮说的话（探路的 `_Gate._note` 记的就是这句，带前缀）
MODEL_SAID = "AI 说：先看看这一页"
#: 脚本报的那一步（`on_step`）
STEP_NOTE = "点了「Get Started」"
#: 桩 journey 里那一步的步拍失败原因（人话）
SHOT_WHY = "截图没成：连不上"
#: 步拍那条路整条坏掉时的原因（`journey.shots_why` 的形状）
CHANNEL_WHY = "拍照时它抛了 RuntimeError：相机没电"


# ─────────────────────────────── 桩：假图 ───────────────────────────────


@dataclass
class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段。"""

    values: dict
    next: tuple = ()
    interrupts: tuple = ()


def _gate(step="intake", say="准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]}, id="i-1")


class _NarratingGraph:
    """假图：`invoke` 唯一干的事就是**把服务拼好的那根线叫一遍**。

    ⚠️ 为什么不自己造一个回调喂给服务：被测的是**接线** —— 「服务有没有把 `on_note` /
    `on_run` 传下去、传下去的那根线会不会落到时间线上」只有从 `Deps` 那一头叫进来才验得到。

    `waits` = 头几次 `invoke` 之后**停在闸上**（于是还能有第二次 `_advance`：
    `POST /job/{id}/reply` 那条路）。用于「同一句 `why` 不许报第二遍」。
    """

    def __init__(self, deps, *, call="explore", values=None, waits=0):
        self.deps = deps
        self.call = call
        self.values = dict(values or {})
        self.waits = waits
        self.invokes = 0

    def invoke(self, payload, config):
        self.invokes += 1
        if self.call == "explore":
            self.deps.explore(URL, GOAL)
        elif self.call == "selftest":
            self.deps.selftest("py", WS_URL, "/tmp/form.json", SITE)
        #: `call="none"` = 这一步不叫任何东西（测的是 `_advance` **返回之后**那一扫，
        #: 与探路/自测那两根线无关）
        return {}

    def get_state(self, config):
        gates = (_gate(),) if self.invokes <= self.waits else ()
        return _Snap(values=dict(self.values), next=(), interrupts=gates)

    def update_state(self, config, values, as_node=None):
        return {"configurable": dict(config["configurable"])}


class StubWindow:
    """窗口层的替身（这一片只用到「它还活着吗」）。"""

    def __init__(self, *, alive=True):
        self.calls: list = []
        self.alive_ = alive

    def set_viewport(self, width, height):
        self.calls.append((width, height))

    def alive(self):
        return self.alive_


def _shot_ok(ws_url, dest, *, timeout=None):
    return pathlib.Path(str(dest)).name, ""


def _shot_boom(ws_url, dest, *, timeout=None):
    """闸拍那条路**拍不成**（要点出「这一轮没留下图」那一条）。"""
    raise RuntimeError("cdp 子进程没了")


def _brief(tmp_path, **over):
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    """运行产物一律落 `tmp_path`（计划 Global Constraints：测试不写 `runtime/`）。"""
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


def _client(build, **kw):
    """`build(deps) -> 图`：假图要拿着**服务拼好的 `Deps`** 才造得出来（那正是被测的接线）。"""
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    kw.setdefault("capture", _shot_ok)
    return TestClient(service.create_app(graph_factory=lambda brief, deps: build(deps), **kw))


def _narrating(*, call="explore", values=None, waits=0):
    """一个假图的**造法**（拿到 `deps` 才造）：`invoke` 只会叫那根线。"""
    return lambda deps: _NarratingGraph(deps, call=call, values=values, waits=waits)


def _wait(client, job_id, *, until=("waiting", "done", "failed"), timeout=20.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get("/job/%s" % job_id).json()
        if last["status"] in until:
            return last
        time.sleep(0.02)
    raise AssertionError("job 没有在 %.0fs 内走到 %s：%r" % (timeout, until, last))


def _live(client, job_id):
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


def _events(client, job_id, kind):
    return [e for e in _live(client, job_id)["events"] if e["kind"] == kind]


def _says(client, job_id, kind):
    return [e["say"] for e in _events(client, job_id, kind)]


def _one_job(tmp_path, build, **kw):
    """跑一个 job 到它停下来，返回 `(client, job_id)`。"""
    client = _client(build, **kw)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    return client, job_id


# ────────────────── 桩：服务拼好的那两根线（探路 / 自测）──────────────────


class _ExploreStub:
    """`browser_agent.explore` 的替身：**只做一件真事 —— 叫回调**。

    叫法照真 `explore` 的接口（关键字参数），于是「服务有没有把它们传下来」当场现形。

    `note_only=True`：**只叫 `on_note`**（不叫 `on_step`）。用在「note 那根线自己
    有没有说」这类用例上 —— 两根线共用 `timeline_broken` 那一本，两根都叫的话
    先叫的那根会把消息占住，后叫的那根就看不见了（修复轮 1 的 Important-3
    正的正是这个：那样写出来的断言**两根线随便哪根活下来都绿**）。
    """

    def __init__(self, *, note=MODEL_SAID, step_note=STEP_NOTE, journey=None, note_only=False):
        self.note = note
        self.step_note = step_note
        self.journey = journey
        self.note_only = note_only
        self.kw: dict = {}

    def __call__(self, url, goal, **kw):
        self.kw = dict(kw)
        if kw.get("on_note") is not None:
            kw["on_note"](self.note)
        if not self.note_only and kw.get("on_step") is not None:
            kw["on_step"]({"note": self.step_note, "action": "click", "step_no": 2})
        return self.journey if self.journey is not None else browser_agent.Journey()


@pytest.fixture
def explore_stub(monkeypatch):
    """换掉**库函数** `browser_agent.explore`（服务里那根线照旧走一遍）。"""
    def install(stub):
        monkeypatch.setattr(service.browser_agent, "explore", stub)
        return stub
    return install


def _skipped_viewport() -> selftest.Run:
    """自测第 4 遍**没跑**（真形状：没给 `set_viewport` 回调时的那个 Run）。"""
    return selftest.Run(
        name="viewport", label=selftest.RUN_LABELS["viewport"], status="skipped", ok=None,
        failed_step=None, trace_path=None,
        note=("这一遍没跑：换窗口大小要调用方在窗口层动手（POST /browser/update），"
              "产物和 cdp 内核都够不着。所以「折叠 / 遮挡 / 坐标假设」这一类失败这次"
              "**没验到**；要跑就传 set_viewport=回调，要放弃就把它写进 allow_skips"
              "（默认不算过）。"))


@pytest.fixture
def selftest_stub(monkeypatch):
    """换掉**库函数** `selftest.run`：按规定回调 `on_run`，然后回一份报告。

    ⚠️ 真跑五遍要起真产物（那是 `tests/test_selftest.py` 的事）。这一片测的是
    「服务有没有把每一遍当场播出去」—— 桩替掉的正是那五遍。
    """
    def install(runs=None):
        seen: dict = {}
        rows = tuple(runs or (_skipped_viewport(),))

        def fake_run(py_path, ws_url, form_file, site, **kw):
            seen.update(kw)
            for run in rows:
                if kw.get("on_run") is not None:
                    kw["on_run"](run)
            return selftest.Report(runs=rows, passed=False, allowed_skips=("country",),
                                   cdp_bin=None, site=site, py_path=str(py_path))

        monkeypatch.setattr(service.selftest, "run", fake_run)
        return seen
    return install


# ══════════════════ 1. 模型每一轮的话 → 时间线（`agent_said`）══════════════════


def test_what_the_model_says_lands_on_the_timeline(tmp_path, explore_stub):
    """`on_note` 那条线接上了：**它自己的推理当场看得见**（设计注 §3.3 / B3）。

    这是「我能充当他的眼睛」那句话里最需要的那一半 —— 今天它只在跑完之后才看得到。
    """
    stub = explore_stub(_ExploreStub())
    client, job_id = _one_job(tmp_path, _narrating())

    said = _events(client, job_id, "agent_said")
    assert len(said) == 1, [e["kind"] for e in _live(client, job_id)["events"]]
    assert said[0]["who"] == "agent", said[0]
    assert said[0]["say"] == MODEL_SAID, said[0]
    # 服务传下去的那根线**真的接到了库函数上**（不是接了个空钩子）
    assert stub.kw.get("on_note") is not None, "服务没把 on_note 传下去"
    assert stub.kw.get("on_step") is not None, "Task 4 那条线不许在这一片被碰掉"


def test_the_note_is_transcribed_word_for_word(tmp_path, explore_stub):
    """服务那一层**只转抄**（`say` 与交上来的那句逐字相同）—— 不改写、不加前缀。

    「一份真相」的另一半（时间线那句 == `journey.notes` 那句）在**库那一层**钉着：
    `test_the_note_callback_hears_everything_the_gate_records`（同一个字符串对象，
    不是两处各拼一遍）。两半合起来才成立：`_Gate._note` 里那一处触发 + 这里不改写。
    """
    said = "AI 说：先看看这一页（别动那个按钮）"
    explore_stub(_ExploreStub(note=said))
    client, job_id = _one_job(tmp_path, _narrating())

    assert _says(client, job_id, "agent_said") == [said]


# ═══════════ 2. 脚本每一步的人话 → 时间线（Task 4 那条线，别被碰掉）═══════════


def test_every_step_the_script_reports_lands_on_the_timeline(tmp_path, explore_stub):
    """`on_step` 那条线在这一片**仍然通着**（`say` = `step["note"]` 原话）。

    ⚠️ 词的读法：计划/设计注里那条事件叫 `agent_step`，代码里落地的词是 `step`
    （`events.KINDS` 里那一行，Task 4 的 `_step_teller` 就在用）。这一片**不改那个词** ——
    「探路的每一步在时间线上有一条 `who="agent"` 的事件」这件事实才是判据。
    """
    explore_stub(_ExploreStub())
    client, job_id = _one_job(tmp_path, _narrating())

    steps = [e for e in _events(client, job_id, "step") if e["who"] == "agent"]
    assert steps, [e["kind"] for e in _live(client, job_id)["events"]]
    assert steps[0]["say"] == STEP_NOTE, steps[0]


# ═══════════════ 3. 自测每一遍 → 时间线（`selftest_run`）═══════════════


def test_a_round_that_did_not_run_is_broadcast_with_its_own_words(tmp_path, selftest_stub):
    """**跳过的那一遍也要播**（设计注 §3.2 第 3 行）—— 「没跑」不许长得像「过了」。

    `say` = `Run.label` + `Run.note` **原文**（note 本来就是人话，照搬，不重写）。
    """
    run = _skipped_viewport()
    seen = selftest_stub((run,))
    client, job_id = _one_job(tmp_path, _narrating(call="selftest"))

    told = _events(client, job_id, "selftest_run")
    assert len(told) == 1, [e["kind"] for e in _live(client, job_id)["events"]]
    assert told[0]["who"] == "system", told[0]
    assert "第 4 遍" in told[0]["say"], told[0]["say"]
    assert run.label in told[0]["say"] and run.note in told[0]["say"], told[0]["say"]
    assert "没跑" in told[0]["say"], told[0]["say"]
    # 服务把 on_run 传下去了（不是接了个空钩子）
    assert seen.get("on_run") is not None, seen
    # 那份「拿它当证据」的东西也照旧绑着（构造时定死的二进制与 trace 目录）
    assert seen.get("cdp_bin") == client.app.state.service._cdp_bin
    assert seen.get("run_dir"), "trace 落哪仍然由服务说了算"


def test_the_service_narration_kinds_stay_inside_the_closed_vocabulary():
    """新加的 kind 必须**在词表里**（`KINDS` 是封闭的，不在表里的当场抛）。

    ⚠️ 这条钉的是 Task 4 复审点名的那件事：回调里撞词表会**静默少掉整条播报**
    （异常被旁路的护栏吞掉），而它看起来像「这一趟没有这一条」。
    """
    tree = ast.parse(pathlib.Path(service.__file__).read_text(encoding="utf-8"))
    kinds = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and len(node.args) >= 2:
            name = getattr(node.func, "attr", "")
            if name == "narrate" and isinstance(node.args[1], ast.Constant):
                kinds.add(node.args[1].value)
    assert kinds, "一个调用点都没扫到 = 量具坏了"
    missing = sorted(k for k in kinds if k not in events.KINDS)
    assert not missing, "这些 kind 不在词表里，写下去会抛（回调里就是静默少一条）：%s" % missing


# ═══════════════ 4. 步拍没成 → 时间线（`shot_missing`）═══════════════


def _journey(*, steps=(), shots_why=""):
    j = browser_agent.Journey()
    j.steps = list(steps)
    j.shots_why = shots_why
    return j


def test_a_step_whose_shot_did_not_land_is_told_once(tmp_path):
    """目录表第 6 行的**步拍**那一半：某一步的图没拍成 → 时间线上一条。

    只说一次：同一句 `why` 在一趟探路里会在每一步上重复（拍照坏了通常是**每步都坏**），
    每一步报一条会把时间线灌满 —— 而那正是「多条不是信息」。
    第二次 `_advance`（人回话之后接着走）也不许再报一遍。
    """
    why = SHOT_WHY
    values = {"journey": _journey(steps=[{"action": "click", "shots_why": why}])}
    client, job_id = _one_job(tmp_path, _narrating(call="none", values=values, waits=1))
    assert client.get("/job/%s" % job_id).json()["status"] == "waiting"

    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e for e in _live(client, job_id)["events"]]
    assert told[0]["who"] == "system", told[0]
    assert why in told[0]["say"], told[0]["say"]

    # 人回一句话 → 再推一步（同一个 `why` 还在账上）→ **不许**再报一条
    assert client.post("/job/%s/reply" % job_id, json={"action": "continue"}).status_code == 200
    _wait(client, job_id)
    assert len(_events(client, job_id, "shot_missing")) == 1, "同一句 why 报了第二遍"


def test_the_step_shot_channel_breaking_is_told_too(tmp_path):
    """步拍那条路**整条**坏掉（`journey.shots_why`）也要说 —— 它是另一个 `why` 字段。

    ⚠️ 两种形状都认：`step["shots_why"]`（这一步的图没了）与 `journey.shots_why`
    （这条**路**最近一次的坏法 —— 步拍自己的代码抛了就是这一种，它没有「哪一步」）。
    「没有静默的路径」管的是**任何一个 `why` 字段**，不是只管路一个形状。
    """
    values = {"journey": _journey(steps=[{"action": "click"}], shots_why=CHANNEL_WHY)}
    client, job_id = _one_job(tmp_path, _narrating(call="none", values=values))

    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e for e in _live(client, job_id)["events"]]
    assert CHANNEL_WHY in told[0]["say"], told[0]["say"]


def test_a_journey_without_a_missing_shot_says_nothing(tmp_path):
    """反面：图都拍成了 → **没有** `shot_missing`（不许把「没事」也说成「缺图」）。"""
    values = {"journey": _journey(steps=[{"action": "click", "shot_before": "a.png"}])}
    client, job_id = _one_job(tmp_path, _narrating(call="none", values=values))
    assert _events(client, job_id, "shot_missing") == []


def test_the_three_missing_shot_sentences_are_told_apart(tmp_path):
    """闸拍 / 步拍·某一步 / 步拍·整条路 —— **三句人话必须分得开**（修复轮 1 的 Important-2）。

    为什么（复审原话）：「**一致不该靠抹平两个事实来达成**」。三件事的来源不同、
    下一步也不同（闸上那张是给人看现场的那张；探路里那些是留着当证据的那批），
    三句话一字不差的话，读的人分不出缺的是哪一种。

    ⚠️ 而**整条路**那一支**说得出的是整条路**（`journey.shots_why` 不带「哪一步」）——
    所以它既不许跟着步拍那句说「这一步」，也不许跟着闸拍那句说「这一轮」：
    **说得出多少说多少，别编。**
    """
    # ① 步拍·某一步（说得出是哪一步的那种形状）
    c1, j1 = _one_job(tmp_path, _narrating(
        call="none", values={"journey": _journey(steps=[{"action": "click",
                                                          "shots_why": SHOT_WHY}])}))
    step_say = _says(c1, j1, "shot_missing")[0]
    # ② 步拍·整条路（那条路的 why —— 没有「哪一步」）
    c2, j2 = _one_job(tmp_path, _narrating(
        call="none", values={"journey": _journey(shots_why=CHANNEL_WHY)}))
    channel_say = _says(c2, j2, "shot_missing")[0]
    # ③ 闸拍（这一轮停下来的那张没拍成）
    c3, j3 = _one_job(tmp_path, _narrating(call="none"), capture=_shot_boom)
    pause_say = _says(c3, j3, "shot_missing")[0]

    assert len({step_say, channel_say, pause_say}) == 3, (step_say, channel_say, pause_say)
    assert "这一步" in step_say and SHOT_WHY in step_say, step_say
    # ② 不许说「这一步」（那是编的），也不许说「这一轮」（那是闸拍的事实）
    assert CHANNEL_WHY in channel_say, channel_say
    assert "这一步" not in channel_say and "这一轮" not in channel_say, channel_say
    assert "探路" in channel_say, channel_say
    # ③ 闸拍那句说的是「这一轮」，不是探路里的那批
    assert "这一轮" in pause_say and "探路" not in pause_say, pause_say


# ═══════════════ 5. 旁路坏掉要**响**（回调和它自己的那本账）═══════════════


def _report(*, broken=(), runs=None):
    return selftest.Report(runs=tuple(runs or (_skipped_viewport(),)), passed=False,
                           allowed_skips=("country",), cdp_bin=None, site=SITE,
                           py_path="candidate.py", narrate_broken=tuple(broken))


def test_a_broken_broadcast_lands_on_the_timeline(tmp_path):
    """**没有静默的路径**：报告里那个 `except` 必须有一条时间线事件（Important-1）。

    ⚠️ 这不是「时间线自己坏了所以记不上」那一类 —— 抛的是回调（job 不在登记表 =
    服务的编程错误），时间线好得很。`selftest.run` 的护栏把原因记进
    `Report.narrate_broken`（那是「旁路坏掉不许带塌自测」那一半），而「**没有静默的
    路径**」那一半要等到报告随 state 回来、服务读得着的时候才算兑现 —— 就是这里。
    """
    why = "RuntimeError: 播报线断了"
    values = {"report": _report(broken=(why,))}
    client, job_id = _one_job(tmp_path, _narrating(call="none", values=values, waits=1))

    told = _events(client, job_id, "narration_broken")
    assert len(told) == 1, [e["kind"] for e in _live(client, job_id)["events"]]
    assert told[0]["who"] == "system", told[0]
    assert why in told[0]["say"], told[0]["say"]
    assert "自测" in told[0]["say"] and "时间线" in told[0]["say"], told[0]["say"]

    # 报告会一直躺在 state 里 —— 再推一步**不许**再报一遍
    assert client.post("/job/%s/reply" % job_id, json={"action": "continue"}).status_code == 200
    _wait(client, job_id)
    assert len(_events(client, job_id, "narration_broken")) == 1, "同一句话报了第二遍"


def test_a_report_that_says_nothing_broken_is_not_narrated(tmp_path):
    """反面：报告里没有那个原因 → **没有** `narration_broken`（不许把「没事」说成「坏了」）。"""
    client, job_id = _one_job(tmp_path, _narrating(call="none", values={"report": _report()}))
    assert _events(client, job_id, "narration_broken") == []


def test_a_note_with_no_job_to_land_on_is_not_swallowed(tmp_path, explore_stub):
    """时间线没地方记的时候**也要说**（`_step_teller` 那条规矩，新线照做）。

    ⚠️ **桩只叫 `on_note`**（`note_only=True`）—— 这是修复轮 1 的 Important-3 要求的那一修：
    `on_step` 与 `on_note` 共用 `timeline_broken` 那一本，两根都叫的话**先叫的那根**
    占住消息，于是断言里那两个子串**哪根线活着都成立**（复审实测：把 note 那根线
    整个弄成静默，这条用例**照样绿**）。
    所以这里：① 只让 note 那根线说话；② 断言的**是那句点名它的话**
    （「模型这一轮的话没记上」）—— 弄静默它就红。
    """
    explore_stub(_ExploreStub(note_only=True))
    client = _client(_narrating())
    svc = client.app.state.service
    run = svc._explore_for(_brief(tmp_path), "job-nope")
    assert run is not None, "没有 ws_url 那种情况才允许返回 None"
    journey = run(URL, GOAL)

    said = "\n".join(str(n) for n in journey.notes)
    assert "模型这一轮的话没记上" in said, journey.notes
    assert "不在登记表里" in said, journey.notes
    assert "时间线没记全" in said, journey.notes


def test_a_broadcast_with_no_job_to_land_on_is_not_dropped_silently(tmp_path):
    """自测那条线同上：没有 job 可记时**当场报错**，让叫它的那一层看得见。

    为什么这一个抛、上面那个只记（两者都对）：探路那一趟的 `run()` 手上**有**一个
    `journey` 可以写（两半合起来才成立）；自测的播报在外面**没有**这样的落点 ——
    能看见它的只有 `selftest.run` 的护栏，而护栏正是照「回调抛了」来处置的
    （见 `test_a_selftest_that_cannot_be_broadcast_is_not_silent`）。
    """
    client = _client(_narrating())
    svc = client.app.state.service
    tell = svc._run_teller("job-nope")
    with pytest.raises(Exception) as err:
        tell(_skipped_viewport())
    assert "job-nope" in str(err.value), str(err.value)


# ═══════════ 6. 铁律一：回调抛异常 → 探路 / 自测照常跑完 ══════════


#: 探路要的那一小片会话（**不打浏览器**）：`list_tools` + `call_tool`。
class _StubSession:
    def list_tools(self):
        return [{"name": "observe", "description": "看一眼这一页",
                 "inputSchema": {"type": "object", "properties": {}}}]

    def call_tool(self, name, args):                     # pragma: no cover - 这一片用不着
        raise AssertionError("这一条用例不该走到工具调用")


def _reply(content="", calls=None):
    tool_calls = [
        types.SimpleNamespace(
            id="call_%d" % i,
            function=types.SimpleNamespace(name=n, arguments=json.dumps(a, ensure_ascii=False)))
        for i, (n, a) in enumerate(calls or [])]
    msg = types.SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg, finish_reason="stop")], usage=None)


class _FakeLLM:
    """按剧本一轮一轮回的假模型（一回合说完就停）。"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls: list = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        return _reply(turn.get("content", ""), turn.get("calls"))


def _explore_with_note(on_note):
    """真 `explore`（桩会话 + 假模型），只把 `on_note` 换掉。"""
    return browser_agent.explore(
        URL, GOAL, session=_StubSession(), client=_FakeLLM([{"content": "先看看这一页"}]),
        budget=browser_agent.Budget(max_steps=4, max_rounds=4), on_note=on_note)


def test_the_note_callback_hears_everything_the_gate_records():
    """正面：`on_note` 拿到的**就是** `journey.notes` 里那一句（逐字）。"""
    heard: list = []
    journey = _explore_with_note(heard.append)
    assert heard == [MODEL_SAID], heard
    assert heard[0] in journey.notes, journey.notes


def test_explore_keeps_going_when_the_note_callback_throws():
    """**铁律一**（探路那一半）：旁路抛异常 → 探路照常跑完，账本照样有那一句。

    不吞会怎样：`_Stop` 与工具异常之外，回调抛出去会被当成本次探路自己的故障 ——
    而时间线坏掉**不该**把一趟探路变成废账（与 `emit` 那条护栏同一条规矩）。
    """
    def boom(text):
        raise RuntimeError("时间线挂了")

    journey = _explore_with_note(boom)
    assert MODEL_SAID in journey.notes, "主路被带塌了：模型那句话没进账本"
    said = "\n".join(str(n) for n in journey.notes)
    assert "RuntimeError" in said and "时间线" in said, journey.notes


def _py(tmp_path) -> pathlib.Path:
    py = tmp_path / "candidate.py"
    py.write_text("# 桩产物（这一片不跑它）\n", encoding="utf-8")
    return py


def _failed_run(name, note="卡在第 2 步：没找到「Go」"):
    return selftest.Run(name=name, label=selftest.RUN_LABELS[name], status="failed", ok=False,
                        failed_step=2, trace_path=None, note=note)


@pytest.fixture
def no_real_runs(monkeypatch):
    """把**起产物**那一步（`_execute`）换成一张剧本。

    这一片测的是「每一遍跑完有没有当场播出去」，不是子进程那套机制
    （那套在 `tests/test_selftest.py` 里，有自己的用例）。
    """
    def install(script):
        def fake_execute(name, *a, **kw):
            return script(name)
        monkeypatch.setattr(selftest, "_execute", fake_execute)
    return install


def _selftest_run(tmp_path, **kw):
    """跑一次自测（桩掉起产物那一步）：三遍都挂 + 硬顶 4 → 第 4/5 遍轮得到（跳过的那两支）。"""
    kw.setdefault("run_dir", str(tmp_path / "traces"))
    kw.setdefault("cdp_bin", str(tmp_path / "cdp"))
    return selftest.run(str(_py(tmp_path)), WS_URL, str(tmp_path / "form.json"), SITE, **kw)


def test_selftest_keeps_going_when_the_run_callback_throws(tmp_path, no_real_runs):
    """**铁律一**（自测那一半）：回调抛异常 → 五遍**照常跑完**，报告照常回来。

    而且**不许静默**：播报坏掉这件事自己要留在报告里（不然服务永远不知道
    「这一趟的时间线上少了每一遍的结果」）。
    """
    no_real_runs(_failed_run)

    def boom(run):
        raise RuntimeError("播报线断了")

    report = _selftest_run(tmp_path, max_submissions=4, on_run=boom)
    assert len(report.runs) == 5, report.runs
    assert [r.status for r in report.runs] == ["failed", "failed", "failed",
                                               "skipped", "skipped"], report.runs
    said = "\n".join(report.summary().splitlines())
    assert "播报" in said or "时间线" in said, report.summary()
    assert "RuntimeError" in said, report.summary()


def test_every_run_is_broadcast_in_order_including_the_ones_that_did_not_run(
        tmp_path, no_real_runs):
    """每一遍**跑完当场**播一次，顺序 = 报告里的顺序，**没跑的也在里面**。

    漏掉 `skipped` / `not_needed` 的那几遍，正是「没验到」被吞掉的那条路
    （设计注 §3.2 第 3 行点名的那件事）。
    """
    no_real_runs(_failed_run)
    seen: list = []
    report = _selftest_run(tmp_path, max_submissions=4, on_run=seen.append)

    assert [(r.name, r.status) for r in seen] == [(r.name, r.status) for r in report.runs]
    assert [r.status for r in seen] == ["failed", "failed", "failed", "skipped", "skipped"]
    assert seen[3].note.startswith("这一遍没跑"), seen[3].note


# ═══════════ 7. 铁律二：`on_*` 不给 → 今天的行为一个字节不变 ══════════


def test_nothing_is_wired_when_no_reader_is_given():
    """**Task 4 那条 `on_step` 线的形状**：没有读者 = 这条线压根不接（不是接个空钩子）。

    ⚠️ **射程**（修复轮 1 的 Minor-1 改准了）：这一条测的是 `_two_readers` ——
    Task 4 的接线工具，**与新加的两根线无关**。新线的「不给 = 今天那条路」由下面
    两条钉（`on_note` 那条钉整个 `journey.notes` 列表、自测那条钉报告的字节）。
    """
    assert service._two_readers(None, None) is None
    assert service._two_readers() is None
    marker = []
    assert service._two_readers(None, marker.append)({"n": 1}) is None
    assert marker == [{"n": 1}], "有读者的时候要照常叫它"


def test_an_explore_without_the_note_hook_behaves_exactly_as_before():
    """`on_note` 不给（默认）→ 账本**一个字节都不多**（今天的行为一个字节不变）。

    ⚠️ 钉的是**整个 `notes` 列表**（修复轮 1 的 Minor-1）：只断言「那一句在」的话，
    把 `_Gate._note` 里的 `if self._on_note is None: return` 早退删掉（于是 `None`
    被当回调调、`TypeError` 被护栏吞成一句「旁路没记成」）它**照样绿** ——
    而那句话说明行为已经变了。列表比句子严。
    """
    journey = _explore_with_note(None)
    assert journey.notes == [MODEL_SAID], journey.notes


def test_a_selftest_without_the_run_hook_reports_exactly_the_same_thing(
        tmp_path, no_real_runs):
    """`on_run` 不给 vs 给一个什么都不做的 → 报告**逐字节相同**（这条线是纯加法）。"""
    no_real_runs(_failed_run)
    quiet = _selftest_run(tmp_path, max_submissions=4)

    no_real_runs(_failed_run)
    noisy = _selftest_run(tmp_path, max_submissions=4, on_run=lambda run: None)

    assert json.dumps(quiet.as_dict(), ensure_ascii=False) == \
        json.dumps(noisy.as_dict(), ensure_ascii=False)
    assert quiet.summary() == noisy.summary()
    assert quiet.narrate_broken == () and noisy.narrate_broken == ()


def test_a_later_shot_that_lands_clears_the_broken_channel_note(tmp_path):
    """拍成之后那句话要清掉（修复轮 1 的 Minor-5）—— **事实不许被拉长**。

    `journey.shots_why` 说的是「这条**路**现在坏着吗」，不是一份历史记录。不清的话：
    这一趟里有一张没拍成、后面每一步都正常，它仍然挂着那句话 —— 时间线上就会说
    「探路里的步拍图这一次没留下」，而后面那些图其实都留下了。去重挡得住刷屏，
    挡不住这件事。
    """
    where = tmp_path / "shots"
    where.mkdir()
    calls = {"n": 0}

    def shooter(session, dest):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "桩说的：相机没电"
        pathlib.Path(str(dest)).write_bytes(b"x")
        return pathlib.Path(str(dest)).name, ""

    journey = browser_agent.Journey()
    shots = browser_agent._StepShots(journey, where, shooter)
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert "相机没电" in journey.shots_why, journey.shots_why
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert journey.shots_why == "", "拍成之后那句话还挂着（事实被拉长了）：%r" % journey.shots_why


def test_the_hooks_are_optional_keyword_arguments_not_a_new_shape():
    """两根新线的**形状**：`on_note` / `on_run` 都是**默认 `None` 的关键字参数**。

    钉的是「不给 = 今天那条路」（不是「不给就报错」、也不是「不给就偷偷换一个默认读者」）。
    """
    note = inspect.signature(browser_agent.explore).parameters["on_note"]
    run = inspect.signature(selftest.run).parameters["on_run"]
    assert note.default is None and note.kind is inspect.Parameter.KEYWORD_ONLY, note
    assert run.default is None, run
