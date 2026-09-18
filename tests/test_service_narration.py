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


def _journey(*, steps=(), failures=(), shots_why=""):
    """一个桩 journey。`failures` = `journey.shot_failures`（**只增**那本账，见下）。"""
    j = browser_agent.Journey()
    j.steps = list(steps)
    j.shot_failures = list(failures)
    j.shots_why = shots_why
    return j


def _shots(journey, where, shooter):
    """一个真的 `_StepShots`（步拍那条路的正身 —— 这一片要测的正是它记了什么）。"""
    where.mkdir(parents=True, exist_ok=True)
    return browser_agent._StepShots(journey, where, shooter)


def _shooter(*, fails=0, why="桩说的：相机没电"):
    """前 `fails` 次报「拍不成」，之后每次都成。"""
    calls = {"n": 0}

    def shooter(session, dest):
        calls["n"] += 1
        if calls["n"] <= fails:
            return None, why
        pathlib.Path(str(dest)).write_bytes(b"x")
        return pathlib.Path(str(dest)).name, ""

    return shooter


def test_a_shot_that_failed_and_then_worked_is_still_on_the_timeline(tmp_path):
    """**验收 1**：第 1 张没成、第 2 张成 —— 时间线上**有**一条 `shot_missing`。

    ⚠️ 这一条钉的是修复轮 2 的正身（复审判的洞）：`shots_why` 是**一格状态**
    （拍成了就清 —— Minor-5 要的就是那个），于是「失败又恢复」在那上面留不下痕迹。
    出口必须是**只增的账**：`journey.shot_failures`（步拍在**没成的那一刻**记的）。
    这里走的是**真的** `_StepShots`（不是手搭的桩），失败与恢复都是它自己发生的。
    """
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter(fails=1))
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert "相机没电" in journey.shots_why, journey.shots_why
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert journey.shots_why == "", "拍成之后那格状态该清掉（Minor-5）"
    assert len(journey.shot_failures) == 1, journey.shot_failures

    client, job_id = _one_job(tmp_path, _narrating(call="none",
                                                   values={"journey": journey}))
    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e for e in _live(client, job_id)["events"]]
    assert "相机没电" in told[0]["say"], told[0]["say"]


def test_a_step_shot_code_crash_that_recovered_is_still_on_the_timeline(tmp_path):
    """**验收 2**：步拍**自己的代码**抛了、随后一张拍成了 —— 一样有那条事件。

    这是 `_safe` 那个 `except` 的出口（`browser_agent.py` 里那条「旁路，什么都得吞」）：
    它够不着「哪一步」，所以那一句说的是「探路里的步拍图这一次没留下」。
    """
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter())

    def boom():
        raise AttributeError("步拍自己坏了（桩）")

    shots._safe("before_mutation", boom)
    assert "步拍自己坏了" in journey.shots_why, journey.shots_why
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert journey.shots_why == "", "拍成之后那格状态该清掉（Minor-5）"
    assert len(journey.shot_failures) == 1, journey.shot_failures

    client, job_id = _one_job(tmp_path, _narrating(call="none",
                                                   values={"journey": journey}))
    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e for e in _live(client, job_id)["events"]]
    assert "步拍自己坏了" in told[0]["say"], told[0]["say"]
    assert "这一步" not in told[0]["say"], told[0]["say"]


def test_a_channel_that_stayed_broken_is_told_once(tmp_path):
    """**验收 3**：一直坏 —— 那本账会一直涨，但时间线上**只报一次**（去重不许坏）。"""
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter(fails=99))
    for _ in range(3):
        shots.before_mutation({"action": "click"}, None, ("k",))
    assert len(journey.shot_failures) == 3, journey.shot_failures

    client, job_id = _one_job(tmp_path, _narrating(call="none", values={"journey": journey}))
    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e["say"] for e in told]


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
    """步拍那条路**整条**坏掉（步拍自己的代码抛了那种）也要说。

    ⚠️ 它的形状是「**够不着哪一步**」那种（`_safe` 记的），所以那句话说的是这一路、
    不是「这一步」（Important-2）。带 `when` 的那条（`_take` 记的）走的是另一句 ——
    两句的分别由 `test_the_three_missing_shot_sentences_are_told_apart` 钉。
    """
    values = {"journey": _journey(failures=[{"why": CHANNEL_WHY}])}
    client, job_id = _one_job(tmp_path, _narrating(call="none", values=values))

    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e for e in _live(client, job_id)["events"]]
    assert CHANNEL_WHY in told[0]["say"], told[0]["say"]
    assert "这一步" not in told[0]["say"], told[0]["say"]


def test_a_journey_without_a_missing_shot_says_nothing(tmp_path):
    """**验收 4**（反面）：图都拍成了 → **没有** `shot_missing`。

    不许变成「见到过就永远报」那种怂改法：这本账**只增**，而这里它自始至终是空的
    （走的是真 `_StepShots`，两张都拍成）。
    """
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter())
    for _ in range(2):
        shots.before_mutation({"action": "click"}, None, ("k",))
    assert journey.shot_failures == [] and journey.shots_why == "", journey.shot_failures

    client, job_id = _one_job(tmp_path, _narrating(call="none",
                                                   values={"journey": journey}))
    assert _events(client, job_id, "shot_missing") == []


#: **判据扫哪些文件**（Task 5 修复轮 4 把射程写下来了 —— 复审点名：射程是这条判据最弱的地方）。
#:
#: **扫**：仓库里的**每一个 `.py`**（`ROOT.rglob("*.py")`）—— 不只是 `browser_agent.py`。
#: 为什么：耦合的另一头在**别的模块**里也可能被写（`agent/service.py:1125` 本来就在跨模块写
#: `journey.notes`）。复审实测过：只看一个文件时，往 `agent/shots.py` 写一句
#: `journey.shots_why = why` —— 这条判据**完全瞎**（而它正是唯一挡着那个耦合的东西）。
#:
#: **不扫**这几类，各有各的理由：
#:   - `.venv` / `venv` / `__pycache__` / `.git` / `.pytest_cache` —— 不是源码；
#:   - **`.superpowers/` 与 `runtime/`** —— **harness 目录**：两个都不进 git
#:     （`sdd/.gitignore` 里就是 `*`），产品代码一处都不 import 它们
#:     （`agent` / `tests` / `forms` / `fixtures` / `tools` 里 grep = 0）。
#:     ⚠️ 理由**不是**「扫描器会把数据当代码读」——**字符串字面量不会**被当代码
#:     （`.superpowers/task-2-mutations.py` 里那条 `journey.shots_why = …`
#:     就躺在一个字符串里，**扫它也不会红**）。真正的理由是：
#:     **harness 目录里会有代码形状的复现体 / 变异体**（变异脚本会把改过的源码
#:     **写进真文件本身**，写完还原 —— `TASK-3-mutations.py` 里 `TARGET = ROOT/"agent"/…`）。
#:     一个「最小复现」哪天被写成真代码摆在那儿，排除就是唯一挡住**误杀**的东西 ——
#:     而 `events.py` 那条判断词规矩自己写着「误杀一次，人就再也不信这条规矩了」。
#:     漏的边界很干净：**能上生产的代码，全在被扫的这一侧**。
_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".pytest_cache", "runtime",
              "node_modules", ".superpowers"}


def _repo_py_files() -> list:
    """判据要扫的那一串文件（射程见 `_SKIP_DIRS` 那头）。"""
    return sorted(p for p in ROOT.rglob("*.py")
                  if not _SKIP_DIRS & set(p.relative_to(ROOT).parts))


def _is_journey(node) -> bool:
    """这个节点是不是「那个 journey」（`journey` / `self.journey` / `x.journey`）。"""
    if isinstance(node, ast.Name):
        return node.id == "journey"
    return isinstance(node, ast.Attribute) and node.attr == "journey"


def _writes_to_journey_shots_why(src: str) -> list:
    """扫一份源码：**哪些地方在写 `journey.shots_why`**（Task 5 修复轮 3/4 的量具）。

    返回 `[(函数链, 值节点, 行号)]`（函数链带类名，于是能认出 `("_StepShots", "_fail")`）。
    认这些写法：

      - `journey.shots_why = …` / `x.journey.shots_why = …`
      - `journey.shots_why += …`（`AugAssign`）
      - `journey.shots_why: str = …`（`AnnAssign`；**只有注解没有赋值不算写**）
      - `journey.shots_why, n = '窗口没了', n`（**元组解包** —— 值按位置对上那一格）
      - `setattr(journey, "shots_why", …)`（值**没法静态判断**，所以它永远算「非空」）

    ⚠️ **看接收者，不是看属性名**（修复轮 4 的 N3）：`self.shots_why`（**产物自己**那一份，
    `template.py` / `fixtures/` / `forms/sites/*.py` 里那些）与 `journey.shots_why` 是
    **同名不同物** —— 只有接收者是 `journey`（`journey` / `something.journey`）的才算。
    全仓换这条规则后，命中仍然只有 `browser_agent.py` 里那两处（零误伤）。

    ⚠️ **已知的盲点**（静态分析的固有边界 —— **这是给下一位读者的地图，不是返工清单**；
    别为这些把判据写复杂，成本比收益高）：

      1. **别名**：`self._journey.shots_why = …` 判据**看不见**。⚠️ 而本仓**已经有**这个命名 ——
         `_Gate.__init__` 里就写着 `self._journey = journey`，`_Gate` 里已经在用
         `self._journey.notes.append(...)` 那样写。**这是四个盲点里最现实的一个。**
      2. **下标写**：`journey.__dict__["shots_why"] = …` / `vars(journey)["shots_why"] = …`。
      3. **`for` / `with` 的目标**：判据只认 `Assign` / `AnnAssign` / `AugAssign` / `setattr`
         四种语句形状；`for x.shots_why in …` 这类编译期写入点不在里面。
      4. **动态拼属性名**：`setattr(o, "shots" + "_why", …)`（值没法静态判）。

    这四条今天**都不是活的**（判据 + 全量套件在本轮之前一直绿，且跨模块那句是被逮住的），
    写下来是因为**射程**正是这类判据最弱的地方 —— 复审原话：「这是地图」。
    """
    tree = ast.parse(src)
    out: list = []

    def record(targets, value, lineno, where):
        flat: list = []
        for target in targets:
            flat.extend(_flatten(target))
        hits = [i for i, target in enumerate(flat) if _is_shots_why_of_journey(target)]
        if not hits:
            return
        # 元组解包：值也摊平，**按位置**对上那一格（`a, b = x, y` ⇒ a 拿 x）
        elts = list(value.elts) if isinstance(value, (ast.Tuple, ast.List)) else None
        for i in hits:
            out.append((where, elts[i] if (elts is not None and i < len(elts)) else value,
                        lineno))

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, where + (child.name,))     # 类名也要进函数链
                continue
            if isinstance(child, ast.Assign):
                record(child.targets, child.value, child.lineno, where)
            elif isinstance(child, ast.AnnAssign):
                if child.value is not None:            # 只有注解 = 没写东西
                    record([child.target], child.value, child.lineno, where)
            elif isinstance(child, ast.AugAssign):
                record([child.target], child.value, child.lineno, where)
            elif isinstance(child, ast.Call) and getattr(child.func, "id", "") == "setattr":
                args = child.args
                if (len(args) >= 3 and isinstance(args[1], ast.Constant)
                        and args[1].value == "shots_why" and _is_journey(args[0])):
                    out.append((where, args[2], child.lineno))
            walk(child, where)

    walk(tree, ())
    return out


def _flatten(target) -> list:
    """`a, (b, c) = …` 这样的目标摊平成一层（元组解包里的那一格也是一次写）。"""
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list = []
        for elt in target.elts:
            out.extend(_flatten(elt))
        return out
    return [target]


def _is_shots_why_of_journey(target) -> bool:
    return (isinstance(target, ast.Attribute) and target.attr == "shots_why"
            and _is_journey(target.value))


def _unaccounted_shots_why_writes(src: str) -> list:
    """`(函数链, 行号)`：**把 `journey.shots_why` 写成非空值、却不在对账的地方**。

    判据（修复轮 3 的 ①）：**非空 = 出过事 ⇒ 必须有对账** —— 那本只增的账
    （`journey.shot_failures`）是这条事实**唯一**的时间线出口，所以非空赋值只许待在
    **同时写账**的那个方法里（`_StepShots._fail`）。**唯一例外是赋成 `""`**
    （「现在不坏了」，不是新事实）。

    ⚠️ **这条规则是「看接收者」的**（修复轮 4 的 N3）：`self.shots_why`（产物自己那份）
    不算 —— 见 `_writes_to_journey_shots_why` 的射程说明。
    """
    return [(where, lineno) for where, value, lineno in _writes_to_journey_shots_why(src)
            if where != ("_StepShots", "_fail")
            and not (isinstance(value, ast.Constant) and value.value == "")]


def test_the_four_missing_shot_sentences_are_told_apart(tmp_path, monkeypatch):
    """闸拍 / 步拍·某一步 / 步拍·整条路 / **到顶不再拍** —— **四句人话必须分得开**。

    为什么（修复轮 1 的 Important-2 原话）：「**一致不该靠抹平两个事实来达成**」。
    四件事的来源不同、下一步也不同（闸上那张是给人看现场的那张；探路里那些是留着当
    证据的那批；到顶那一条是**知道的、不再拍**），句子一字不差的话，读的人分不出是哪一种。

    ⚠️ 而**整条路**那一支**说得出的是整条路**（它不带「哪一步」）—— 所以既不许跟着步拍
    那句说「这一步」，也不许跟着闸拍那句说「这一轮」：**说得出多少说多少，别编**。
    """
    # ① 步拍·某一步（说得出是哪一步的那种形状）
    c1, j1 = _one_job(tmp_path, _narrating(
        call="none", values={"journey": _journey(steps=[{"action": "click",
                                                          "shots_why": SHOT_WHY}])}))
    step_say = _says(c1, j1, "shot_missing")[0]
    # ② 步拍·整条路（够不着「哪一步」那种 —— `_safe` 记的，账上不带 `when`）
    c2, j2 = _one_job(tmp_path, _narrating(
        call="none", values={"journey": _journey(failures=[{"why": CHANNEL_WHY}])}))
    channel_say = _says(c2, j2, "shot_missing")[0]
    # ③ 闸拍（这一轮停下来的那张没拍成）
    c3, j3 = _one_job(tmp_path, _narrating(call="none"), capture=_shot_boom)
    pause_say = _says(c3, j3, "shot_missing")[0]
    # ④ 到顶（真的走到 `MAX_KEPT_SHOTS` 那一步上）
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 1, raising=True)
    capped_journey = browser_agent.Journey()
    capped_shots = _shots(capped_journey, tmp_path / "cap-shots", _shooter())
    for _ in range(2):                       # 第一张拍成 → 到顶；第二张不再拍
        capped_shots._take(None, {"action": "click"}, "before")
    c4, j4 = _one_job(tmp_path, _narrating(call="none",
                                           values={"journey": capped_journey}))
    capped_say = _says(c4, j4, "shot_missing")[0]

    says = [step_say, channel_say, pause_say, capped_say]
    assert len(set(says)) == 4, says
    assert "这一步" in step_say and SHOT_WHY in step_say, step_say
    # ② 不许说「这一步」（那是编的），也不许说「这一轮」（那是闸拍的事实）
    assert CHANNEL_WHY in channel_say, channel_say
    assert "这一步" not in channel_say and "这一轮" not in channel_say, channel_say
    assert "探路" in channel_say, channel_say
    # ③ 闸拍那句说的是「这一轮」，不是探路里的那批
    assert "这一轮" in pause_say and "探路" not in pause_say, pause_say
    # ④ 到顶那句说的是「不再拍」，而且必须点明**这是知道的，不是漏了**
    assert "不再拍" in capped_say and "不是漏了" in capped_say, capped_say


def test_the_shot_cap_says_so_on_the_timeline(tmp_path, monkeypatch):
    """**上限第一次生效的那一刻**，时间线上必须有一条事件（修复轮 3 的 ②）。

    ⚠️ 上限本身是对的（**别抬它、别改数**）：缺的只是「它生效了」这件事没人说 ——
    原先那句解释只进 `journey.notes`，而 `notes` **不上时间线**（服务侧只把它写进
    `attempts.jsonl`）。于是从这一步起**既没有图、也没有账、也没有事件**，页面上就是
    一个没人解释的空图框 —— 那正是设计注 §3.2 第 6 行（本片的行）要治的形状。
    """
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 2, raising=True)
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter())
    for _ in range(4):                       # 前两张拍成 → 到顶；后两次不再拍
        shots._take(None, {"action": "click"}, "before")
    assert any("不再拍" in n for n in journey.notes), journey.notes
    capped = [row for row in journey.shot_failures if row.get("capped")]
    assert len(capped) == 1, journey.shot_failures   # 到顶那一刻记一条（不是每步一条）

    client, job_id = _one_job(tmp_path, _narrating(call="none",
                                                   values={"journey": journey}))
    told = _events(client, job_id, "shot_missing")
    assert len(told) == 1, [e["say"] for e in told]
    assert "不再拍" in told[0]["say"] and "不是漏了" in told[0]["say"], told[0]["say"]


def test_a_broken_channel_is_only_written_where_it_is_accounted_for():
    """**非空 = 出过事 ⇒ 必须有对账**：`journey.shots_why` 的非空赋值只许待在 `_StepShots._fail`。

    为什么要有这条（修复轮 3 的 ①）：那本只增的账是这条事实**唯一**的时间线出口，
    而「出口与写入点在同一个方法里」原来只是一句**注释** —— 今天成立只因为恰好没有
    第三方写。全局约束是「**没有静默的路径**」，不是「**有注释的静默路**」；
    这条判据把「恰好」变成「有东西挡着」（同形状的先例：扫 `service.py` 的 narrate 那条）。

    **射程**（修复轮 4 的 N3 —— 射程是这条判据最弱的地方，所以写在这儿）：
    扫的是**全仓每一个 `.py`**（不是只有一个文件），因为耦合的另一头可能在别处被写
    （`agent/service.py:1125` 本来就在跨模块写 `journey.notes`）；规则是**看接收者**
    （`journey` / `x.journey`），所以产物自己那些 `self.shots_why` 全被挡掉。
    跳过哪些目录、为什么，写在 `_SKIP_DIRS` 那一头。

    ⚠️ **已知的盲点**（静态判不了，**可以接受**，**别为它们把判据写复杂** ——
    四个逐个列在 `_writes_to_journey_shots_why` 的 docstring 里，最现实的那个是
    **别名** `self._journey`：本仓 `_Gate` 已经在那么用了）。
    ⚠️ **不许**用改名 / 删字段 / 改成 property 去堵：msgpack 还原走 `cls(**kwargs)`，
    构造函数一抛就被 ext hook 吞掉、返回 `None` —— 老代码收到不认识的字段，
    那个 job 的 `journey` 会**静默变成 `None`**。只能靠测试钉，不能动字段形状。
    ⚠️ 撞上限那一条**不经 `_fail`**（账里那条是 `capped`）—— 它由
    `test_the_shot_cap_says_so_on_the_timeline` 钉。
    """
    writes, bad = [], []
    for path in _repo_py_files():
        where = str(path.relative_to(ROOT))
        found = _writes_to_journey_shots_why(path.read_text(encoding="utf-8"))
        writes.extend((where, w, line) for w, _v, line in found)
        bad.extend((where, w, line) for w, line in _unaccounted_shots_why_writes(
            path.read_text(encoding="utf-8")))
    # 量具**不是瞎的**：真源码里那两处（`_fail` 里写、`_take` 里清空）必须被扫到 ——
    # 一处都扫不到就说明字段改名了 / 射程断了，而这条判据会**静默地永远绿**。
    assert len(writes) >= 2, "一处都没扫到 = 量具坏了（字段改名了？射程断了？）：%r" % (writes,)
    assert any(w == ("_StepShots", "_fail") for _f, w, _l in writes), writes
    assert not bad, (
        "这些地方把 `journey.shots_why` 写成了非空值，却没同时写那本只增的账 —— "
        "非空 = 出过事 ⇒ 必须有对账（否则时间线上是静默的）：%r" % (bad,))

    # 量具**自己有牙**：喂它一段带违规写法的源码，它得**逐行**点出来
    # （不靠外部变异也能证明这条判据会响）。第 6 行是允许的例外（赋成 `""`），
    # 第 10 行是**别人的** `shots_why`（接收者不是 journey ⇒ 不算）。
    trap = (
        "class _StepShots:\n"                                     # 1
        "    def _fail(self, why):\n"                             # 2
        "        self.journey.shots_why = why\n"                  # 3  允许（对账那一处）
        "    def somewhere_else(self, journey, n, why):\n"        # 4
        "        journey.shots_why = '窗口没了'\n"                 # 5  违规
        "        journey.shots_why = ''\n"                        # 6  允许（清空）
        "        setattr(journey, 'shots_why', why)\n"            # 7  违规
        "        journey.shots_why, n = '窗口没了', n\n"           # 8  违规（元组解包）
        "        journey.shots_why: str = '窗口没了'\n"            # 9  违规（AnnAssign）
        "        n.shots_why = '别人的，不算'\n"                    # 10 不算（接收者不是 journey）
        "        journey.shots_why += ' 又一句'\n")                # 11 违规（AugAssign）
    assert _unaccounted_shots_why_writes(trap) == [
        (("_StepShots", "somewhere_else"), 5),
        (("_StepShots", "somewhere_else"), 7),
        (("_StepShots", "somewhere_else"), 8),
        (("_StepShots", "somewhere_else"), 9),
        (("_StepShots", "somewhere_else"), 11)], _writes_to_journey_shots_why(trap)


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
    journey = browser_agent.Journey()
    shots = _shots(journey, tmp_path / "shots", _shooter(fails=1))
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert "相机没电" in journey.shots_why, journey.shots_why
    shots.before_mutation({"action": "click"}, None, ("k",))
    assert journey.shots_why == "", "拍成之后那句话还挂着（事实被拉长了）：%r" % journey.shots_why
    # ⚠️ 但**清空不等于没发生过**（修复轮 2）：那一刻的事在那本只增的账上留着 ——
    # 清的是「现在坏着吗」，不是历史（历史由 `shot_failures` 负责）。
    assert [row["why"] for row in journey.shot_failures] == ["桩说的：相机没电"], \
        journey.shot_failures


def test_the_hooks_are_optional_keyword_arguments_not_a_new_shape():
    """两根新线的**形状**：`on_note` / `on_run` 都是**默认 `None` 的关键字参数**。

    钉的是「不给 = 今天那条路」（不是「不给就报错」、也不是「不给就偷偷换一个默认读者」）。
    """
    note = inspect.signature(browser_agent.explore).parameters["on_note"]
    run = inspect.signature(selftest.run).parameters["on_run"]
    assert note.default is None and note.kind is inspect.Parameter.KEYWORD_ONLY, note
    assert run.default is None, run
