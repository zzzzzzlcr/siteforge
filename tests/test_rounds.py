"""Task 6：轮次投影（`agent/rounds.py`）—— checkpoint → **运营看得懂的轮次卡片**。

「一轮」= 图**到过几次闸口**（设计注 §六：轮是「回答」的单位）。这张卡片的每一格
都只从它该来的地方来（§8.3 第 3 条）：闸口的原话、`journey` 的步子、`report.summary()`、
`values["visits"]` —— **读不出来就写「这一步没说它做了什么」，不编**。

## 这一片钉的四件事

1. **配对规则**（§六，写死在 `rounds.project` 的 docstring 里）：第 n 道闸上拍的那张叫
   `pause-<n>`；`rounds[n-1].step = gate.step`、`done.step = values["visits"][-1]`、
   `done.shots = (pause-<n-1>, pause-<n>)`、`rounds[0].done = None`。
2. **闸只在 `waiting` 时露头** —— 跑着/排队时是 `null`，否则页面上会出现一个「还能按」的按钮。
3. **D16**：这一屏里没有选择器、没有 `target`、没有原始 `result` —— 只有人话与文件名
   （字节走 `/job/{id}/shot/{name}`，一个字节都不进 JSON）。
4. **同一个名字、两个事实**：**闸拍轮次**（本模块）不是**探路的模型轮数**
   （`journey.rounds` —— 它记 7，也不代表这里有 7 轮）。有一条用例专门钉它。

⚠️ 这个模块是**纯函数**：不许读盘、不许读时间、不许碰全局 —— 有一条 AST 用例钉着
（加一句 `open(...)` / `time.time()` 就红）。

TODO(Task 7)：卡片的消费方是 `agent/console.html`（页面只渲染、不推断）。
"""

from __future__ import annotations

import ast
import copy
import json
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, rounds, selftest, service, state  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"

#: 一道闸的原话（`_Gate` 交上来的那个 `say`）
GATE_ASK = "接下来要打开真浏览器，把「走到 Thank you」走一遍：「每一步都点主按钮」"


# ═══════════════════════ 桩：真 `Journey` / 真 `Report` ═══════════════════════


def _step(n, note, *, before=None, after=None, deferred=False):
    """一条**真形状**的探路步（键集合与 `Journey.steps` 逐字相同）。

    ⚠️ `target` 里**故意**带着选择器（`#get-started`）、`result` 里带着原始回执 ——
    它们正是**不许**进这一屏的东西（D16）。这一条是「投影有没有搬它们」的量具。
    """
    return {
        "state": "funnel", "action": "click", "origin": "model", "step_no": n,
        "target": {"text": "Get Started", "role": "button", "near": "hero",
                   "selectors": ["#get-started", "a.btn-primary"]},
        "result": {"ok": True, "raw": {"evaluated": "document.querySelector('#get-started')"}},
        "note": note, "receipt": {"ok": True},
        "sig_before": "sig-1", "sig_after": "sig-2", "why": {},
        "shot_before": before, "shot_after": after, "shot_after_deferred": deferred,
    }


def _journey(steps=(), *, model_rounds=0):
    """真 `Journey`（Task 2/5 那个形状）。

    `model_rounds` 是**探路的模型轮数**（`journey.rounds`）—— 与「闸拍轮次」**不是**
    一个东西，所以这里默认 0、用例里显式给一个大数来证「两个数不互相冒充」。
    """
    book = browser_agent.Journey()
    book.steps = [dict(s) for s in steps]
    book.rounds = model_rounds
    book.rounds_measured = True
    return book


def _run(name="baseline", status="failed", note="卡在第 2 步：没找到「Go」"):
    return selftest.Run(name=name, label=selftest.RUN_LABELS.get(name, name), status=status,
                        ok=(status == "passed"), failed_step=(2 if status == "failed" else None),
                        trace_path="/tmp/trace.jsonl", note=note)


def _report(runs=None, *, passed=False):
    """真 `Report`（`values["report"]` 里躺着的那个）。"""
    rows = tuple(runs if runs is not None else (_run(),))
    return selftest.Report(runs=rows, passed=passed, allowed_skips=("country",),
                           cdp_bin=None, site=SITE, py_path="/tmp/x.py")


def _gate(step, say=GATE_ASK):
    """`Service._project` 算好的那道闸（`/job/{id}` 与 `/live` **同一份**）。"""
    return {"step": step, "step_say": graph.STEP_SAY.get(step, step), "ask": say,
            "facts": {"站点": SITE}, "can": list(graph.HUMAN_CAN)}


#: 「这一轮拍成了」的哨兵（`_shot(name=…)` 的默认值）—— 与 `None`（=没拍成）分开
_AUTO = object()


def _shot(n, *, name=_AUTO, why=""):
    """闸拍清单上的一条（`Job.shot_notes` 的形状：`{"n", "name", "why"}`）。

    `name=_AUTO` = 「这一轮拍成了」（名字就是 `pause-<n>.png`）；
    `name=None` = 「这一轮**没拍成**」（没有图，`why` 里是人话原文）。
    """
    return {"n": n, "name": ("pause-%d.png" % n) if name is _AUTO else name, "why": why}


def _values(*, visits=(), journey=None, report=None, **over):
    values = {"site": SITE, "ws_url": WS_URL, "visits": list(visits)}
    if journey is not None:
        values["journey"] = journey
    if report is not None:
        values["report"] = report
    values.update(over)
    return values


def _project(values, gate=None, *, pauses=(), **over):
    kw = {"job_id": "job-abc", "status": service.WAITING, "say": GATE_ASK, "delivered": False}
    kw.update(over)
    return rounds.project(values, gate, pauses=list(pauses), **kw)


# ═════════════════ 1. 配对规则（§六，brief 逐字照抄的那四条）═════════════════


def test_two_gates_make_two_rounds_and_each_round_is_paired_with_its_shot():
    """两个闸 → 两轮；每一轮的「正要做的」「刚才做完的」「夹住它的那对图」都对得上。

    站在第 2 道闸（`explore`）上：只跑过 `intake`，盘上有 `pause-1.png` / `pause-2.png`。
    """
    values = _values(visits=["intake"], journey=_journey([_step(1, "看了一眼页面")]))
    proj = _project(values, _gate("explore"), pauses=[_shot(1), _shot(2)])

    assert [r["n"] for r in proj["rounds"]] == [1, 2]
    first, second = proj["rounds"]

    # 第 1 轮：**第一道闸之前什么都没跑**
    assert first["done"] is None
    assert first["step"] == "intake"
    assert first["now"] == {"n": 1, "name": "pause-1.png", "why": ""}

    # 第 2 轮（脚下这一道闸）：正要做的 = 闸说的那一步；刚做完的 = `values["visits"][-1]`
    assert second["step"] == "explore"
    assert second["done"]["step"] == "intake"
    assert second["done"]["shots"] == {"before": "pause-1.png", "after": "pause-2.png"}

    # `now` 与 `done.shots.after` 是**同一张文件**（一份字节两处引用；页面别显示两遍）
    assert second["now"]["name"] == second["done"]["shots"]["after"] == "pause-2.png"


def test_the_round_shows_which_node_each_round_was_about():
    """历史轮次按 `visits` 的顺序落位 —— 每张卡说**它自己**那个节点，不是同一句话说十遍。

    站在 `draft` 那道闸上（= 第 3 轮）：第 1 轮正要做的 = `intake`、第 2 轮 = `explore`
    （它那一轮的「刚才那一步」是 `intake`）。
    """
    values = _values(visits=["intake", "explore"])
    proj = _project(values, _gate("draft"), pauses=[_shot(1), _shot(2), _shot(3)])
    cards = proj["rounds"]

    assert [(r["n"], r["step"]) for r in cards] == [(1, "intake"), (2, "explore"), (3, "draft")]
    assert [r["done"] and r["done"]["step"] for r in cards] == [None, "intake", "explore"]
    # 人话（D16）：卡片上是「打开浏览器探路」，不是节点名 `explore`
    assert cards[1]["step_say"] == graph.STEP_SAY["explore"]
    assert cards[2]["done"]["step_say"] == graph.STEP_SAY["explore"]


def test_a_round_that_lost_its_shot_says_why_instead_of_showing_an_empty_frame():
    """这一轮没留下图 → `now.why` 是**那句人话原文**（空图框不许冒充页面，§3.2 第 6 行）。"""
    why = "拍照超过 20 秒还没回来（超时）—— 窗口可能卡住了；这张图没留下，闸照旧在"
    proj = _project(_values(visits=["intake"]), _gate("intake"),
                    pauses=[_shot(1, name=None, why=why)])
    assert proj["rounds"][0]["now"] == {"n": 1, "name": None, "why": why}


def test_a_shot_row_that_says_nothing_at_all_does_not_become_an_unexplained_empty_frame():
    """一条**空记录**（没有图、也没说为什么）也不许静默 —— 那一格自己说清「这条是空的」。"""
    proj = _project(_values(visits=["intake"]), _gate("intake"), pauses=[{}])
    now = proj["rounds"][0]["now"]
    assert now["name"] is None
    assert now["why"].strip(), "没有图、也没有 why —— 必须有一句话说清这条记录是空的"


# ═══════════════════ 2. 闸只在 waiting 时露头（brief 点名的坑）═══════════════════


def test_the_gate_is_null_unless_it_is_waiting_and_no_card_borrows_from_it():
    """跑着/排队/到头了**没有闸** —— 露着头，页面上就是一个「还能按」的按钮。

    ⚠️ **两道判据**（复审 2026-09-18 F2）：`gate` 那一格是 `null` 只是第一半 ——
    **卡片也不许从闸上借东西**：`step`（闸说的那一步）、`say`（闸口的原话）、
    `revisable`（「打回」那个按钮）在**没人等你回话**的时候一个都不许出现。

    ⚠️ 量具用**会打回的那道闸**（`lint`）：拿 `explore` 试的话 `revisable` 恒为 False
    （它本来就不在那三道里），这条断言就永远绿。
    """
    for status in (service.QUEUED, service.RUNNING, service.DONE, service.FAILED):
        proj = _project(_values(visits=["intake", "explore", "draft"]), _gate("lint"),
                        pauses=[_shot(i) for i in range(1, 5)], status=status, stage="explore")
        assert proj["gate"] is None, status
        for card in proj["rounds"]:
            assert card["revisable"] is False, (status, card["n"])
            assert card["say"] == "", (status, card["n"], card["say"])
            assert card["step"] != "lint", (status, card["n"],
                                            "这一轮从闸上借了「正要做的」那一步")

    waiting = _project(_values(visits=["intake", "explore", "draft"]), _gate("lint"),
                       pauses=[_shot(i) for i in range(1, 5)], status=service.WAITING)
    assert waiting["gate"]["step"] == "lint"
    assert waiting["gate"]["ask"] == GATE_ASK, "闸口的**原话**照抄（不重写）"
    assert waiting["gate"]["can"] == list(graph.HUMAN_CAN)
    assert waiting["gate"]["revisable"] is True, "停在 lint 上 = 页面该给「打回」那个按钮"
    assert waiting["rounds"][-1]["revisable"] is True
    assert waiting["rounds"][-1]["say"] == GATE_ASK
    assert waiting["rounds"][-1]["step"] == "lint"


@pytest.mark.parametrize("step", ["intake", "explore", "draft", "lint", "selftest", "deliver"])
def test_revisable_is_true_only_on_the_three_gates_that_can_send_it_back(step):
    """「打回」= 这一版不要了、回 `draft` 重写 —— 只有 `lint`/`selftest`/`deliver` 三道闸是这个意思。

    名字直接从 `graph.REVISABLE` 来（不在这儿另抄一张名单 —— 两张名单早晚会漂）。
    """
    values = _values(visits=["intake", "explore", "draft"])
    proj = _project(values, _gate(step), pauses=[_shot(i) for i in range(1, 5)])
    want = step in graph.REVISABLE

    assert proj["gate"]["revisable"] is want
    assert proj["rounds"][-1]["revisable"] is want
    assert [r["revisable"] for r in proj["rounds"][:-1]] == [False, False, False], \
        "历史轮次上没有闸 —— 打回那两个字对它们没有意义"


# ═════════════ 2.5 到头了那一档：没有幽灵卡 / 清单最后那张不是一轮 ═════════════


def test_a_run_that_reached_the_end_has_no_ghost_round():
    """⚠️ **幽灵卡**（复审 2026-09-18 F1，Critical）：`_capture_pause` 在**每一次** advance
    之后都拍 —— **跑完那一次也拍** ⇒ 闸拍清单比闸数多一条。照单全收的话，最后会多出一张
    `step == done.step` 的卡（它的 `step` 取不到闸、落到 `stage` 兜底；`done.step` 落到
    `visits[-1]`；两个兜底指向**同一个节点**）：运营读到的是「正要开始做一件已经做完的事」。

    判据两条：**卡片张数 = 闸数**、**一张卡都没有那种自指**；最后那张图**没丢**。
    """
    visits = ["intake", "explore", "draft", "lint", "selftest", "deliver"]
    pauses = [_shot(i) for i in range(1, 8)]                 # 6 道闸 + 跑到头那一次
    proj = _project(_values(visits=visits), None, pauses=pauses, status=service.DONE,
                    say="写进站点目录了。", delivered=True, stage="deliver")

    assert [c["n"] for c in proj["rounds"]] == [1, 2, 3, 4, 5, 6], "6 道闸 = 6 张卡"
    assert rounds.count(pauses, service.DONE) == 6, "轮数只有一个算法（`count`）"
    for card in proj["rounds"]:
        assert not (card["done"] and card["done"]["step"] == card["step"]), card
    # 最后那张图不是一轮，但**也不许没人提**：它挂在最后一张卡上
    assert proj["rounds"][-1]["last_shot"]["name"] == "pause-7.png"
    assert [c["last_shot"] for c in proj["rounds"][:-1]] == [None] * 5
    assert proj["rounds"][-1]["step"] == "deliver", "最后那一道闸正要做的还是 deliver（它真做了）"
    assert proj["rounds"][-1]["done"]["step"] == "selftest"


def test_a_run_that_was_stopped_at_the_gate_says_that_step_did_not_happen():
    """**喊停**那一支（复审 F1 的另一半）：人在 lint 门口喊停 —— lint **一步没做、也不会做**。

    ⇒ 「正要做的」那一格**当场说清它没做**；「刚做完的」是**上一个真做了**的节点（`draft`），
    而**不许**把「没做」那句安到它头上（那会是新的一句假话）。
    """
    proj = _project(_values(visits=["intake", "explore", "draft", "lint"],
                            end_reason="human_stop", end_note="人喊停：…"),
                    None, pauses=[_shot(i) for i in range(1, 6)], status=service.DONE,
                    say="人喊停：…", stage="lint")
    last = proj["rounds"][-1]
    assert [c["n"] for c in proj["rounds"]] == [1, 2, 3, 4]
    assert last["step"] == "lint" and "没做" in last["step_say"], last
    assert last["done"]["step"] == "draft", "刚做完的是**真做了**的那一个"
    assert last["done"]["say"] == "", "draft 真做了 —— 别把「没做」那句安在它头上"
    assert last["last_shot"]["name"] == "pause-5.png", "喊停那一刻的图也不许丢"


def test_a_step_that_was_sent_back_is_not_claimed_as_something_that_just_finished():
    """**打回**那一支（复审 2026-09-18 F7）：人在 selftest 门口说「这版不行」⇒
    selftest **进过、一步都没做**，图回 `draft`。

    下一张卡（`draft` 那道闸）的「刚才那一步」就是 selftest —— 它必须**当场说清没做**，
    而且**不许**把 state 里那本探路的账挂到它头上（那会读成「它做了 30 步」）。
    """
    journey = _journey([_step(1, "看了一眼页面")])
    proj = _project(_values(visits=["intake", "explore", "draft", "lint", "selftest"],
                            journey=journey, revised_at="selftest"),
                    _gate("draft"), pauses=[_shot(i) for i in range(1, 7)],
                    status=service.WAITING)
    last = proj["rounds"][-1]
    assert last["step"] == "draft"
    assert last["done"]["step"] == "selftest"
    assert "没做" in last["done"]["say"], last["done"]
    assert last["done"]["steps"] == [], "那一步没做 —— 没有步子清单"
    assert "没做" in last["done"]["steps_note"]


def test_the_rounds_are_anchored_to_the_end_of_the_visits_ledger():
    """**重启之后接着跑**（复审 2026-09-18 F5）：`visits` 里还带着重启之前那些节点，
    而轮次账（闸拍清单）是从这次启动**重新数**的 ⇒ 第 k 轮对应 `visits` **末尾**那几个。

    起点对齐会把第 1 张卡说成 `intake`（它说的其实是 `lint` 那一轮）。
    """
    proj = _project(_values(visits=["intake", "explore", "draft", "lint"]), _gate("selftest"),
                    pauses=[_shot(1), _shot(2)], status=service.WAITING)
    assert [c["step"] for c in proj["rounds"]] == ["lint", "selftest"]
    assert proj["rounds"][-1]["done"]["step"] == "lint", \
        "刚做完的是**最近**那个节点，不是重启之前最老的那个"


def test_the_round_count_comes_from_the_one_algorithm():
    """`count()` 是轮数的唯一算法（`/live` 的卡片张数、`/runs` 上那个数字）。

    ⚠️ 到头了那两档要把「最后那张图」去掉 —— 它是 `_capture_pause` 在跑完/跑挂那一次拍的，
    **不是某一轮的闸拍**（复审 F1 的复现里正是这里报成 7，而实际只过了 6 道闸）。
    """
    five = [_shot(i) for i in range(1, 6)]
    assert rounds.count(five, service.WAITING) == 5
    assert rounds.count(five, service.RUNNING) == 5
    assert rounds.count(five, service.QUEUED) == 5
    assert rounds.count(five, service.DONE) == 4
    assert rounds.count(five, service.FAILED) == 4
    assert rounds.count([], service.DONE) == 0

    done = _project(_values(visits=["intake"]), None, pauses=five, status=service.DONE)
    assert len(done["rounds"]) == rounds.count(five, service.DONE)
    failed = _project(_values(visits=["intake"]), None, pauses=five, status=service.FAILED)
    assert len(failed["rounds"]) == rounds.count(five, service.FAILED)


def test_a_final_image_with_no_card_at_all_is_still_mentioned():
    """一张卡都没有（还没走到过闸口）时，「这一趟最后一张图」得在 `rounds_note` 里点名 ——
    **不许有一张图没人提**（那张常常正是跑挂那一刻的窗口）。"""
    proj = _project(_values(visits=[]), None, pauses=[_shot(1)], status=service.FAILED,
                    say="这一步没跑成，停下了。")
    assert proj["rounds"] == []
    assert "pause-1.png" in proj["rounds_note"], proj["rounds_note"]


def test_the_bail_markers_come_from_the_gates_own_code():
    """「闸把节点拦下来」那两条停因**不是手抄的名单** —— 从 `graph._enter` 的赋值点长出来。

    闸上将来多一条拦人的路（又写一个 `end_reason`）而这里不知道 ⇒ 那条路上的卡片会说
    「这一步做了」（复审 F7 的根：名单得跟着调用点走，别跟着记忆走）。
    """
    tree = ast.parse(pathlib.Path(graph.__file__).read_text(encoding="utf-8"))
    enter = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_enter")
    written = set()
    for node in ast.walk(enter):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "end_reason"):
                assert isinstance(node.value, ast.Name), ast.dump(node.value)
                written.add(getattr(state, node.value.id))
    assert written, "一条都没扫到 = 量具坏了"
    assert written == set(rounds.BAILED_END_REASONS), written


def test_the_three_names_that_can_be_sent_back_are_the_graphs_own():
    """那三道闸的名字**只有一处**（`graph.REVISABLE`）—— 这里再抄一份就等着漂。"""
    assert rounds.REVISABLE is graph.REVISABLE
    assert rounds.WAITING == service.WAITING, "状态词表只有一套（rounds 不 import service 是防环）"


# ═══════════════════ 3. 步子清单：人话、没有选择器、不许编 ═══════════════════


def test_the_steps_are_human_words_and_carry_no_selector_and_no_raw_result():
    """`done.steps` 带 `note` + `shot_after_deferred`；**不许**出现 `target` / 选择器 / 原始 `result`。"""
    journey = _journey([
        _step(1, "看了一眼页面", before="runA-step-1-before.png", after="runA-step-1-after.png"),
        _step(2, "点了「Get Started」", before="runA-step-2-before.png",
              after="runA-step-2-after.png", deferred=True),
    ])
    proj = _project(_values(visits=["intake", "explore"], journey=journey),
                    _gate("draft"), pauses=[_shot(1), _shot(2), _shot(3)])
    done = proj["rounds"][-1]["done"]

    assert done["step"] == "explore", "探路的账长在**探路刚做完**的那一轮上"
    assert [s["n"] for s in done["steps"]] == [1, 2]
    assert done["steps"][1]["note"] == "点了「Get Started」"
    assert done["steps"][1]["shots"] == {"before": "runA-step-2-before.png",
                                         "after": "runA-step-2-after.png"}
    assert done["steps"][1]["shot_after_deferred"] is True
    assert done["steps"][0]["shot_after_deferred"] is False

    # 每一格就这四样（键集合钉住 = 「不许搬 target / result」的机械判据）
    for row in done["steps"]:
        assert set(row) == {"n", "note", "shots", "shot_after_deferred"}, row

    # ⚠️ 下面这几条字符串断言的**射程**（复审 2026-09-18 F3 点破的）：fixture 里那句
    # `note` 是**手写的人话**，所以它们**只能**证明「没有整段搬 `target` / `result`」——
    # 证明不了「脚本自己那句话里没有选择器」（那条路由
    # `test_a_target_without_a_text_name_does_not_put_a_selector_into_the_words` 钉，
    # 用的是**生产函数**造出来的那句话）。别把这一条读成 D16 的全部。
    blob = json.dumps(proj, ensure_ascii=False)
    assert "#get-started" not in blob, "`target.selectors` 一个字都不许搬进这一屏"
    assert "querySelector" not in blob, "原始回执一个都不许搬进这一屏"
    assert "a.btn-primary" not in blob and "sig-1" not in blob


def test_the_steps_that_are_not_the_exploration_are_not_pretended_onto_this_round():
    """不是探路那一轮：`steps` 空着，而且**当场说清**为什么（不编）。"""
    proj = _project(_values(visits=["intake", "explore"], journey=_journey([_step(1, "看了一眼页面")])),
                    _gate("draft"), pauses=[_shot(1), _shot(2), _shot(3)])
    note = proj["rounds"][-2]["done"]["steps_note"]
    assert proj["rounds"][-2]["done"]["steps"] == []
    assert note.strip() and "没有" in note


def test_without_a_journey_the_steps_are_empty_and_the_note_says_so():
    """没有 journey → `steps == []` 且 `steps_note` **说清**（brief：不许编）。"""
    proj = _project(_values(visits=["intake", "explore"]), _gate("draft"),
                    pauses=[_shot(1), _shot(2), _shot(3)])
    done = proj["rounds"][-1]["done"]
    assert done["steps"] == []
    assert done["steps_note"].strip(), "没有 journey 时必须说清"
    assert "没有" in done["steps_note"]


def test_a_step_that_says_nothing_about_itself_gets_the_design_notes_own_sentence():
    """脚本那一步没留下人话 → 「这一步没说它做了什么」（设计注 §8.3 第 3 条的原话）。"""
    journey = _journey([{**_step(1, ""), "note": ""}])
    proj = _project(_values(visits=["intake", "explore"], journey=journey), _gate("draft"),
                    pauses=[_shot(1), _shot(2), _shot(3)])
    assert proj["rounds"][-1]["done"]["steps"][0]["note"] == "这一步没说它做了什么"


def test_the_selftest_round_says_what_the_report_said_in_the_reports_own_words():
    """自测那一轮的结论 = `report.summary()` 的**原话**（§8.3 第 3 条列的来源之一）—— 不重写。"""
    report = _report()
    proj = _project(_values(visits=["intake", "explore", "draft", "lint", "selftest"],
                            report=report),
                    _gate("deliver"), pauses=[_shot(i) for i in range(1, 7)])
    done = proj["rounds"][-1]["done"]
    assert done["step"] == "selftest"
    assert done["say"] == report.summary()
    assert "自测没过" in done["say"]


def test_a_round_that_has_no_conclusion_of_its_own_says_nothing_rather_than_guessing():
    """别的步没有自己的结论 → 空着（不编一句像结论的话）。"""
    proj = _project(_values(visits=["intake", "explore"], journey=_journey([_step(1, "看了一眼页面")])),
                    _gate("draft"), pauses=[_shot(1), _shot(2), _shot(3)])
    assert proj["rounds"][-1]["done"]["say"] == ""


# ═══════════════════════ 4. 上限：截断也要说清 ═══════════════════════


def test_too_many_rounds_is_truncated_and_says_what_was_left_out():
    """超上限 → `truncated is True`，留**最近**的那几轮，并说清丢了几轮。"""
    proj = _project(_values(visits=["intake"]), _gate("explore"),
                    pauses=[_shot(i) for i in range(1, 6)], max_rounds=2)

    assert proj["truncated"] is True
    assert [r["n"] for r in proj["rounds"]] == [4, 5], "留最近的；轮号是**真号**（别从 1 重数）"
    assert proj["rounds_note"].strip()
    assert "3" in proj["rounds_note"], "丢了几轮要说得出来"
    # 配对不许因为截断而错位：第 5 轮夹的是 (pause-4, pause-5)
    assert proj["rounds"][-1]["done"]["shots"] == {"before": "pause-4.png", "after": "pause-5.png"}
    assert proj["rounds"][0]["done"]["shots"] == {"before": "pause-3.png", "after": "pause-4.png"}


def test_within_the_limit_nothing_is_truncated_and_nothing_is_said_about_it():
    proj = _project(_values(visits=["intake"]), _gate("explore"),
                    pauses=[_shot(1), _shot(2)], max_rounds=50)
    assert proj["truncated"] is False
    assert proj["rounds_note"] == ""


def test_a_limit_that_would_return_nothing_is_a_programming_error_not_a_silent_blank_screen():
    """`max_rounds=0` 不是「一种上限」—— 真那么干页面上什么都不剩，当场炸（别翻译成人话）。"""
    with pytest.raises(ValueError):
        _project(_values(visits=["intake"]), _gate("intake"), pauses=[_shot(1)], max_rounds=0)


# ═══════════════════ 5. 接线信息：原样带上、一个字不加工 ═══════════════════


def test_the_wiring_information_rides_along_untouched():
    """`window` / `shots_note` / `stage` 是**接线信息** —— 原样带上（不许顺手美化）。"""
    window = {"worker": "192.168.1.222", "bit_id": "8f2c1a…", "api_port": 54345,
              "how": "连到**那台机器**的桌面…", "when": "**它正在跑的时候不要动手**…"}
    shots_note = "这一次没留逐步的图：…（降级 B 那句话）"
    proj = _project(_values(visits=["intake"]), _gate("intake"), pauses=[_shot(1)],
                    window=window, shots_note=shots_note, stage="explore")

    assert proj["window"] == window, "一个字都不许改"
    assert proj["shots_note"] == shots_note
    assert proj["stage"] == "explore"


def test_a_deployment_without_a_window_layer_is_null_not_an_empty_table():
    """没接窗口层 → `null`（页面据此**明说**看不到，而不是显示一块空表）。"""
    proj = _project(_values(visits=["intake"]), _gate("intake"), pauses=[_shot(1)],
                    window=None, shots_note="")
    assert proj["window"] is None
    assert proj["shots_note"] == ""


def test_the_round_that_is_running_now_takes_its_node_from_the_wiring_not_from_a_gate():
    """跑着的时候**没有闸** —— 这一轮的节点从 `stage`（接线信息）来，不从 `gate` 编一个。"""
    proj = _project(_values(visits=["intake"]), None, pauses=[_shot(1), _shot(2)],
                    status=service.RUNNING, stage="explore")
    assert proj["rounds"][-1]["step"] == "explore"
    assert proj["rounds"][-1]["say"] == "", "没有闸就没有「它刚要做什么」那句话"


# ═══════════════════ 6. 「跑完了」≠「交付了」 ═══════════════════


def test_finishing_is_not_delivering():
    """`status` 说**走到哪了**，`delivered` 说**产物真的落盘了吗** —— 两件事分开摆。"""
    values = _values(visits=["intake", "explore"], end_reason="explore_unfinished",
                     end_note="探路没走完（预算到顶）。")
    proj = _project(values, None, pauses=[_shot(1)], status=service.DONE,
                    say="探路没走完（预算到顶）。", delivered=False)
    assert proj["status"] == service.DONE
    assert proj["delivered"] is False

    ok = _project(values, None, pauses=[_shot(1)], status=service.DONE,
                  say="写进站点目录了。", delivered=True)
    assert ok["delivered"] is True


# ═══════════════════ 7. 纯函数（brief 铁律 1）与「两个 rounds」 ═══════════════════


def test_it_is_a_pure_function_no_disk_no_clock_no_globals():
    """**纯函数**：不许读盘、不许读时间、不许碰全局。判据是机械的（AST 扫这个模块）。

    加一句 `open(...)` / `time.time()` / `os.environ` 就红 —— 输入全在参数里。
    """
    tree = ast.parse(pathlib.Path(rounds.__file__).read_text(encoding="utf-8"))
    banned = {"open", "time", "datetime", "os", "random", "pathlib", "sys", "socket",
              "subprocess", "urllib", "glob", "shutil", "threading", "tempfile", "uuid"}
    used: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
        elif isinstance(node, ast.Import):
            used.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            used.add(node.module.split(".")[0])
    assert not (used & banned), ("纯函数那一条：`rounds.py` 里不许出现 %s"
                                 % sorted(used & banned))


def test_the_same_input_gives_the_same_answer_and_nothing_of_the_callers_is_moved():
    """两次同样的输入 → 同样的输出；而且**不许改调用方的东西**（连那张闸拍清单都不许动）。"""
    values = _values(visits=["intake", "explore"], journey=_journey([_step(1, "看了一眼页面")]),
                     report=_report())
    pauses = [_shot(1), _shot(2), _shot(3)]
    gate = _gate("draft")
    before = copy.deepcopy((values, pauses, gate))

    def call():
        return rounds.project(values, gate, job_id="job-abc", status=service.WAITING,
                              say=GATE_ASK, delivered=False, pauses=pauses,
                              window={"worker": "w"}, shots_note="n", stage="draft")

    first, second = call(), call()
    assert first == second
    assert (values, pauses, gate) == before, "投影改了调用方的东西 —— 那就不是投影了"


def test_the_two_meanings_of_rounds_never_stand_in_for_each_other():
    """⚠️ **同一个名字、两个事实**：闸拍轮次（这里）≠ 探路的模型轮数（`journey.rounds`）。

    模型轮数记 7、闸拍三张 —— 卡片就是**三张**。拿 `journey.rounds` 当轮数的实现会红；
    反过来，这个数也绝不会被写进卡片（它在这条路上一次都没被读过）。
    """
    journey = _journey([_step(1, "看了一眼页面")], model_rounds=7)
    pauses = [_shot(1), _shot(2), _shot(3)]
    proj = _project(_values(visits=["intake", "explore", "draft"], journey=journey),
                    _gate("lint"), pauses=pauses)

    assert len(proj["rounds"]) == 3
    assert [r["n"] for r in proj["rounds"]] == [1, 2, 3]
    # 轮数只有一个算法（`rounds.count`）—— `/runs` 上那个数字与这里的卡片张数同源
    assert rounds.count(pauses) == len(proj["rounds"])
    assert rounds.count([]) == 0


# ═══════════════════ 8. HTTP：`/live` 的 rounds/gate + `/runs` ═══════════════════


class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段。"""

    def __init__(self, values=None, next=(), interrupts=()):
        self.values = dict(values or {})
        self.next = tuple(next)
        self.interrupts = tuple(interrupts)


class _FakeGraph:
    """假图：`invoke` 停在第 k 张快照上（第 1 次 `invoke` 用第 1 张）。"""

    def __init__(self, snaps):
        self.snaps = list(snaps)
        self.invokes = 0
        self.state = _Snap()

    def invoke(self, payload, config):
        self.invokes += 1
        self.state = self.snaps[min(self.invokes - 1, len(self.snaps) - 1)]
        return {"__interrupt__": list(self.state.interrupts)} if self.state.interrupts else {}

    def get_state(self, config):
        return self.state

    def update_state(self, config, values, as_node=None):
        return {"configurable": dict(config["configurable"])}


class StubWindow:
    def __init__(self, *, alive=True):
        self.alive_ = alive

    def set_viewport(self, width, height):
        pass

    def alive(self):
        return self.alive_


def _interrupt(step, n=1):
    """真 `Interrupt`（闸口的形状：`graph._enter` 交上去的那个 dict）。"""
    return Interrupt(value={"step": step, "say": GATE_ASK, "facts": {},
                            "can": list(graph.HUMAN_CAN)}, id="i-%d" % n)


def _shot_ok(ws_url, dest, *, timeout=None):
    """闸拍那条路**拍成了**：盘上那张图的名字就是落点的名字（`pause-<n>.png`）。"""
    return pathlib.Path(str(dest)).name, ""


def _brief(tmp_path, **over):
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


def _client(graph_factory, *, window=None, checkpointer=None):
    return TestClient(service.create_app(
        graph_factory=graph_factory, window=window if window is not None else StubWindow(),
        checkpointer=(checkpointer if checkpointer is not None
                      else InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)),
        capture=_shot_ok))


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


def test_live_fills_the_rounds_and_the_gate_the_page_need(tmp_path):
    """`/live` 的 `rounds` / `gate` 填上了（Task 4 的骨架这一版接上）—— 两轮走一遍真接线。"""
    g = _FakeGraph([
        _Snap(values={"site": SITE, "ws_url": WS_URL}, interrupts=(_interrupt("intake"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_interrupt("explore", 2),)),
    ])
    client = _client(lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    live = _live(client, job_id)
    assert live["status"] == "waiting"
    assert live["gate"]["step"] == "intake"
    assert [r["n"] for r in live["rounds"]] == [1], "第 1 道闸 = 第 1 轮"
    assert live["rounds"][0]["done"] is None
    assert live["rounds"][0]["now"]["name"] == "pause-1.png", "闸拍那张图的名字就是配对规则"

    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    _wait(client, job_id)
    live = _live(client, job_id)
    assert [r["step"] for r in live["rounds"]] == ["intake", "explore"]
    assert live["rounds"][1]["done"]["shots"] == {"before": "pause-1.png", "after": "pause-2.png"}
    assert live["gate"]["step"] == "explore"
    assert live["truncated"] is False
    # 字节一个都不进来（图走 /job/{id}/shot/{name}）
    blob = json.dumps(live, ensure_ascii=False)
    assert "data:image" not in blob and "base64" not in blob


class _BoomGraph:
    """状态读不回来的图（saver 抖了）—— 「没有静默的路径」在**读**这一侧的样子。"""

    def get_state(self, config):
        raise RuntimeError("saver 连不上了")

    def invoke(self, payload, config):
        raise AssertionError("这个桩只用来读，不该有人推它")


def test_live_says_so_when_the_state_cannot_be_read_instead_of_quietly_losing_the_rounds(tmp_path):
    """读不回状态（GET 不许写时间线）→ 那句话**随响应回去**，页面上看得见（不许静默）。

    场景：一个**正在跑**的 job，saver 这时候抖了（生产上 saver 是远端的，这条是真的）。
    `/live` 不许 500 —— 时间线与「这个窗口」都还在，缺的那几格当场说清。
    """
    client = _client(lambda brief, deps: _FakeGraph([_Snap()]))
    svc = client.app.state.service
    job = service.Job(job_id="job-dark", brief={"site": SITE}, status=service.RUNNING,
                      created_at="2026-09-18T00:00:00+08:00")
    job.graph = _BoomGraph()
    svc._jobs[job.job_id] = job

    r = client.get("/job/job-dark/live")
    assert r.status_code == 200, r.text
    live = r.json()
    assert "读不回" in live["note"], live["note"]
    assert live["rounds"] == [], "读不回来就别编轮次"
    assert live["gate"] is None
    assert isinstance(live["shots_note"], str), "接线信息不受影响"
    assert live["status"] == service.RUNNING


def test_a_waiting_job_whose_state_cannot_be_read_gets_words_not_a_bare_500(tmp_path):
    """**停在闸上**的 job 读不回状态时也要给一句人话（复审 2026-09-18 F4）。

    那一档 `_view` **自己**就要读 state（在 `_live_facts` 之前）⇒ `_live_facts` 兜不住 ——
    它必须**响**（不能拿一个编出来的状态糊过去），但响的是一句**人话**（503 + detail），
    不是一页 `Internal Server Error`。而「停在闸上」恰恰是最该看得见的那一档。
    """
    client = _client(lambda brief, deps: _FakeGraph([_Snap()]))
    job = service.Job(job_id="job-dark-waiting", brief={"site": SITE}, status=service.DONE,
                      created_at="2026-09-18T00:00:00+08:00")
    job.graph = _BoomGraph()
    client.app.state.service._jobs[job.job_id] = job

    r = client.get("/job/job-dark-waiting/live")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "读不回" in detail and "重拉" in detail, detail


def test_a_recovered_job_says_why_the_earlier_rounds_are_not_on_this_screen(tmp_path):
    """重启过：轮次账（`Job.shot_notes`）**在进程里** ⇒ 之前那几轮的卡片这一屏没有。

    **明说**，并且说清**为什么不从盘上反推**（复审 2026-09-18 F8：那句拒绝的理由也得是
    运营看得见的话 —— 不然「少了几轮」本身就是一条静默的路径）。
    ⚠️ 必须用**真图**：假图的状态活在它自己身上，checkpoint 里什么都没有，
    `_recover` 捡不到东西（捡一个不存在的东西不是这条要测的）。
    """
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    factory = lambda brief, deps: graph.build(checkpointer=saver, deps=_never_used_deps())  # noqa: E731
    first = _client(factory, checkpointer=saver)
    job_id = first.post("/run", json=_brief(tmp_path, ws_url=None,
                                            allow_skips=["country", "viewport"])).json()["job_id"]
    assert _wait(first, job_id)["status"] == "waiting"

    second = _client(factory, checkpointer=saver)          # 换一个服务实例 = 重启过
    assert second.app.state.service._recover(job_id) is not None
    live = _live(second, job_id)
    assert "重新数" in live["note"] and "不显示" in live["note"], live["note"]
    assert "反推" in live["note"], "拒绝反推的理由也要说出来（不然就是一条静默的路径）"
    assert live["rounds"] == [], "重启之前那几轮不冒充卡片（账在进程里）"


def test_a_target_without_a_text_name_does_not_put_a_selector_into_the_words():
    """**D16**：目标没有文字名字时，那句话里**一个选择器都不许有**（复审 2026-09-18 F3）。

    ⚠️ 这一条的射程是**生产函数**（`browser_agent._say` → 脚本写进 `note` 的那句话），
    不是手写的假人话：手写的人话里没有选择器，那条字符串断言**永远不会红**
    （这正是复审点破的那条空断言 —— 它当时唯一真正的防线是键集合那条）。
    这句话会同时进**时间线的 `events[].say`**（同屏）与这里的卡片，所以必须堵在**源头**。
    """
    target = {"selectors": ["#get-started", "a.btn-primary"], "role": "button"}
    said = browser_agent._say("click", target, True)
    assert "#" not in said and "get-started" not in said, said
    assert "没写名字的元素" in said, "兜底那句人话还在（只是不许带选择器）"

    journey = _journey([{**_step(1, said), "note": said, "target": target}])
    proj = _project(_values(visits=["intake", "explore"], journey=journey), _gate("draft"),
                    pauses=[_shot(1), _shot(2), _shot(3)])
    blob = json.dumps(proj, ensure_ascii=False)
    assert "#get-started" not in blob and "a.btn-primary" not in blob, blob
    assert proj["rounds"][-1]["done"]["steps"][0]["note"] == said


def _never_used_deps() -> graph.Deps:
    """真图的桩依赖：这一条**只停在 intake 那一道闸上**，一个都不该被叫到。"""
    def boom(*a, **kw):
        raise AssertionError("这一条只停在 intake —— 不该走到会开浏览器的那一步")
    return graph.Deps(explore=boom, selftest=boom)


def test_runs_is_the_short_list_you_pick_from(tmp_path):
    """`GET /runs`：**能挑运行的最小列表**（§8.1）—— 轮数与 `/live` 的卡片同源。"""
    g = _FakeGraph([_Snap(values={"site": SITE, "ws_url": WS_URL},
                          interrupts=(_interrupt("intake"),))])
    client = _client(lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    body = client.get("/runs").json()
    assert set(body) == {"note", "runs"}
    rows = [r for r in body["runs"] if r["job_id"] == job_id]
    assert len(rows) == 1, body
    row = rows[0]
    assert set(row) == {"job_id", "site", "status", "say", "created_at", "rounds", "delivered"}
    assert row["site"] == SITE
    assert row["status"] == "waiting" and row["delivered"] is False
    assert row["say"], "挑运行的人先看这一句人话"
    assert row["created_at"]
    assert row["rounds"] == len(_live(client, job_id)["rounds"])


def test_runs_counts_the_gates_not_the_last_picture(tmp_path):
    """`/runs` 上那个数字 = **到过几道闸**（复审 F1 的复现：跑到头时它报成了 7，而只过了 6 道闸）。

    这一条走**两道闸**的真接线：第 1 次 advance 停在 intake 那道闸上（一轮），
    `reply` 之后这一趟跑到头（`_capture_pause` 又拍了一张 —— 那**不是**一轮）。
    ⇒ `/runs` 说 **1 轮**，`/live` 也是 1 张卡。拿 `len(shot_notes)` 当轮数的实现会说 2。
    """
    g = _FakeGraph([
        _Snap(values={"site": SITE, "ws_url": WS_URL}, interrupts=(_interrupt("intake"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"],
                      "end_reason": "explore_unfinished", "end_note": "探路没走完。"}),
    ])
    client = _client(lambda brief, deps: g)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert _wait(client, job_id)["status"] == "done"

    row = [r for r in client.get("/runs").json()["runs"] if r["job_id"] == job_id][0]
    assert row["rounds"] == 1, row
    live = _live(client, job_id)
    assert len(live["rounds"]) == 1 and live["rounds"][0]["n"] == 1
    assert live["rounds"][0]["last_shot"]["name"] == "pause-2.png", "最后那张图挂在这一张卡上"


def test_runs_on_a_fresh_service_says_why_it_is_empty(tmp_path):
    """空列表**不是**「什么都没提交过」—— 这个列表是 process-local 的，得说清。"""
    client = _client(lambda brief, deps: _FakeGraph([_Snap()]))
    body = client.get("/runs").json()
    assert body["runs"] == []
    assert body["note"].strip(), "空列表要说清它为什么是空的（登记表不持久）"


# ═══════════════════ 9. 我改的两处既有断言（口径变了，得跟着改）═══════════════════


def test_the_service_status_words_are_the_only_vocabulary():
    """状态词表**只有一套**（`service` 那份）—— 投影里那个 `waiting` 是同一串字节。"""
    assert rounds.WAITING == service.WAITING
