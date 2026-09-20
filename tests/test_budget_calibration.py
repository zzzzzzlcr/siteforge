"""探路预算的**标定**（2026-09-20，真站实测逼出来的）。

## 为什么有这份文件

`job-62b192d2aca4`（fix 模式，真站）那一趟**是正常的**：它一路在点问卷的
`Get Free Quote` / `Continue`，一直在往前走，最后一句是
「Now clicking Continue to advance the quiz.」—— 然后停在了 `budget_rounds`。
Console 的原话：「这次探路没走完（预算到顶了。走了 20 步。预算到顶：**问满 20 轮就停下**）」。

而**同一个站的确定性重放脚本要走 25 步**。20 < 25 ⇒ 那一趟**结构上走不完** ——
不是模型不行，是预算不够。

## 这份文件钉的是三件事

1. **轮预算够走完一个已知的 25 步重放**（按实测的「每轮能推进几个动作」折算）；
2. **步预算不许先于轮预算卡死**（实测 1 次工具调用/轮 ⇒ 步数卡住 = 同一件事，
   而它卡住时模型连「把话说完」的那一轮都拿不到）；
3. **改的确实是那个挡路的数** —— `Caps.explore_rounds` 是**从**这个默认值推出来的
   （`state.py` 那一行），所以改这个常量**不是白改**；这一条是拿测量钉住的，
   不是拿回忆。

## 这些数是**怎么推出来的**（可复算）

    # 目标：同一个站的确定性重放要走几步
    ./.venv/bin/python -c "…STATES…"   # → 25（19 个 state 组、steps 数相加）

    # 实测：4 趟被轮预算钉死的真站探路，每一轮推进了几个动作
    #   job-36ab36b56754 9/20=0.450  job-62b192d2aca4 10/20=0.500
    #   job-82d0709571b6 10/20=0.500  job-dc1bece3b52c 12/20=0.600
    # 最坏 0.450 ⇒ 25 / 0.450 ≈ 56 轮，再加「收尾那一轮」（模型不调工具、只说话）
    #   ⇒ **57 轮**是结构上走得完的下界。

⚠️ **它不是一个「模型自然要用多少轮」的量**（那个数今天仍然量不到：要量它得放开预算
跑一趟真模型，而那一趟这一轮不许开）。它是个**结构性下界**：已知目标 25 步、已知
实测推进速率 ⇒ 低于 57 就必然走不完。什么时候该再校准：**放开预算之后的第一趟真站
探路**（`attempts.jsonl` 的 `rounds` 那一格）—— 若它落在新预算的 80% 以上，说明还是紧的。
"""

from __future__ import annotations

import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, state  # noqa: E402

#: 同一个站的确定性重放脚本里**一共几个 step**（`forms/_remote/www.gowizard.com_auto-warranty_.py`：
#: 19 个 state 组、各自的 `steps` 数相加 = 25）。这是「一趟探路至少要推进几步」的硬目标。
TARGET_MUTATING_STEPS = 25

#: 实测：4 趟被轮预算钉死的真站探路，**每一轮推进了几个动作**（`job-*.jsonl` 复算）。
#: 取最坏的那个当折算率 —— 预算要按最坏情况给，不然「够不够」就成了撞运气。
MEASURED_ACTIONS_PER_ROUND = (0.450, 0.500, 0.500, 0.600)

#: 结构上走得完的下界（轮）：动作数 ÷ 最坏推进率，**再加收尾那一轮**
#: （模型不调工具、只把结论说出来 —— 那一轮不推进任何动作，但没它就没有结论）。
IMPLIED_ROUNDS = (math.ceil(TARGET_MUTATING_STEPS / min(MEASURED_ACTIONS_PER_ROUND)) + 1)


def test_the_round_budget_covers_a_25_step_replay_at_the_measured_rate():
    """**轮预算不许低于「25 步按实测速率折算」的下界** —— 低于它就是结构上走不完。

    这一条就是 `job-62b192d2aca4` 那个停因的回归钉子：那一趟拿了 20 轮、
    推进了 10 个动作、然后被钉死；而要走完需要 ~57 轮。
    """
    assert IMPLIED_ROUNDS == 57, (
        "这条下界是从实测常量算出来的，改了常量就该在这里看见它动了：%d" % IMPLIED_ROUNDS)
    assert browser_agent.DEFAULT_MAX_ROUNDS >= IMPLIED_ROUNDS, (
        "轮预算 %d 低于「25 步按实测速率折算」的下界 %d —— 这一趟**结构上走不完**"
        "（`job-62b192d2aca4` 就是这么停在 20/20 的）"
        % (browser_agent.DEFAULT_MAX_ROUNDS, IMPLIED_ROUNDS))


def test_the_budget_is_still_a_brake_not_a_number_with_a_zero_added():
    """**放开 ≠ 取消**：它仍然是「防跑飞」那根线（§6.5 / P5），只是不再卡在路中间。

    为什么钉上界：真正的失败模式不是「给少了」，而是下次又看见一趟被钉死的探路时
    **再添一个零**。四倍于已知需要，足够吸收「一趟里多观察几次、走错几步」，
    而跑飞的模型仍然会被这根线拦住。
    """
    assert browser_agent.DEFAULT_MAX_ROUNDS <= 4 * IMPLIED_ROUNDS, (
        "轮预算 %d 已经四倍于已知需要（%d）—— 那不是「放开」，那是把防跑飞那根线拔了"
        % (browser_agent.DEFAULT_MAX_ROUNDS, IMPLIED_ROUNDS))


def test_the_step_budget_does_not_bind_before_the_round_budget():
    """**步预算不许先卡死**。

    实测口径（4 趟真站探路）：`rounds == steps == 20` —— **每一轮正好一次工具调用**。
    在那个regime 里，步数一旦小于轮数，被卡住的其实是同一件事，差别在于停因会变成
    `budget_steps`，而模型**连「把结论说出来」的那一轮都拿不到**（那一轮不调工具）。
    所以步数要**大于**轮数：轮是那根主动的线，步只在「模型在一轮里塞好几个调用」
    （真跑飞了）时才兜底。
    """
    assert browser_agent.DEFAULT_MAX_STEPS > browser_agent.DEFAULT_MAX_ROUNDS, (
        "步预算 %d 不大于轮预算 %d：实测 1 次工具调用/轮 ⇒ 步会先卡死，"
        "而它卡住时模型连收尾那一轮都没有（停因会变成 budget_steps，结论一个字留不下来）"
        % (browser_agent.DEFAULT_MAX_STEPS, browser_agent.DEFAULT_MAX_ROUNDS))


def test_caps_explore_rounds_follows_the_module_default():
    """**改 `DEFAULT_MAX_ROUNDS` 不是白改** —— 真挡路的那个数是 `Caps.explore_rounds`，
    而它是**从这个常量推出来的**（`state.py`：`explore_rounds: int = DEFAULT_MAX_ROUNDS`）。

    ⚠️ 这条是「先量清楚哪个数在挡」那一步的钉子（不许靠回忆）：量出来它们**同一个数**，
    所以改常量就够；哪天有人把 `state.py` 那一行改成硬编码的字面量，这里会红。
    """
    caps = state.Caps()
    assert caps.explore_rounds == browser_agent.DEFAULT_MAX_ROUNDS, (
        "Caps.explore_rounds=%r 不等于 DEFAULT_MAX_ROUNDS=%r —— 上游那个数已经与默认值脱钩，"
        "只改默认值就是白改" % (caps.explore_rounds, browser_agent.DEFAULT_MAX_ROUNDS))
    assert caps.explore_steps == browser_agent.DEFAULT_MAX_STEPS, (
        "Caps.explore_steps=%r 不等于 DEFAULT_MAX_STEPS=%r"
        % (caps.explore_steps, browser_agent.DEFAULT_MAX_STEPS))


def test_the_graph_hands_explore_the_new_budget_on_the_production_path():
    """生产那条路（`graph.build(...)` 不给 `caps` ⇒ `Caps()`）真的把新数交到了探路手上。

    `Caps` 上那个数正确、而 `_budget_left` 里再砍一刀（或写死）的话，前面几条全是白绿。
    """
    budget = graph._budget_left(state.Caps(), {"steps": 0, "rounds": 0, "attempts": 0})
    assert budget.max_rounds == browser_agent.DEFAULT_MAX_ROUNDS, budget
    assert budget.max_steps == browser_agent.DEFAULT_MAX_STEPS, budget


def test_the_calibration_note_says_it_is_still_unmeasured():
    """**注释改准**（这一条钉的是「话说得对不对」，不是行为）。

    原来那段写的是「要校准得先把预算放开再量，**那是另一件事**」——
    这一轮做的就是那件事的**前半截**（放开预算）。而「模型自然要用多少轮」**仍然没量到**
    （量它要跑一趟真模型，不许）。所以那段注释必须①不再说「那是另一件事」，
    ②说清现在这个数是**按什么推的**、③点名什么时候该再校准。
    """
    src = pathlib.Path(browser_agent.__file__).read_text(encoding="utf-8")
    assert "那是另一件事" not in src, (
        "`browser_agent.py` 里还留着「要校准得先把预算放开再量，那是另一件事」—— "
        "预算**已经放开了**（就是这一轮做的），这句话现在是假的")
    doc = browser_agent.Budget.__doc__ or ""
    assert "25" in doc and "校准" in doc, (
        "`Budget` 的说明要写清这个数是按什么推出来的（同一个站的重放 25 步）、"
        "以及什么时候该再校准：%r" % doc)
