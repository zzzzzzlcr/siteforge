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

    # 实测：4 趟被轮预算钉死的真站探路，每一轮**成功推进**了几个动作
    #   job-36ab36b56754 9/20=0.450  job-62b192d2aca4 9/20=0.450
    #   job-82d0709571b6 9/20=0.450  job-dc1bece3b52c 8/20=0.400
    #   ⚠️ 分子**只数 `result.ok == true` 的那些** —— 「点了没做成」不叫推进。
    #      （2026-09-20 复审复算过我第一版：把 `ok == false` 也算进去，
    #       四个数会偏成 0.450 / 0.500 / 0.500 / 0.600。）
    # 最坏 0.400 ⇒ 25 / 0.400 = 62.5 → 63 轮，再加「收尾那一轮」（模型不调工具、只说话）
    #   ⇒ **64 轮**（⚠️ 是**估计**不是下界 —— 见下面那段保留）。

⚠️ **它不是一个「模型自然要用多少轮」的量**（那个数今天仍然量不到：要量它得放开预算
跑一趟真模型，而那一趟这一轮不许开）。它是个**估计**：按手头那几条（**偏的**）样本折出来的
—— 4 条**全是同一个站**（gowizard）、**全是被 20 轮钉死的那一档**（能走完的早就走了，
走完那几趟只用 2–4 轮）⇒ 这个子集天然是「动作密度最低」的那一类，**偏在哪一边没量**。
已知目标 25 步、已知实测推进速率 ⇒ 低于 64 就（按这个估计）走不完。

★ **2026-10-08：那个「什么时候该再校准」的样本来了，而且响在估计的上方。**
`job-42daa38499e9`（hormn.com）烧到 `rounds=86 / steps=95` 还没走完（旧的 80 把它掐了，
`explore_unfinished`、没有 py），而它一路在推进 ⇒ **64 这个估计是偏低的**（与上面那句
「偏在哪一边没量」合得上）。用户当天裁定「放大点」⇒ 默认值放到 **160 / 200**，
**下面那条短漏斗的下界（64）一个字没动** —— 新加了一条**长漏斗**的下界（86 / 95）
在 `test_the_budget_also_covers_a_real_long_funnel_not_just_the_gowizard_one` 里。
再校准的时机改成：**下一趟把 160 也烧光**的真站探路。

⚠️ **这一段的措辞与 `agent/browser_agent.py` 那张预算注释是同一套**（2026-09-20 修复轮 5
对齐：那边写的是「64 是**估计**不是下界」/「就**可能**响」）—— **两个文件不许一个知道、
另一个不说**：这一份原先还写着「结构性下界」「就会响是预期的」，上一轮改了
`browser_agent.py` 而没碰这一份，两边当场打架。

⚠️ **还有一条比「下界是多少」更要紧的**（同一轮复审量的）：**生产路径上 `stall_limit`
一次都不参与**，所以 `max_rounds` 是**唯一**在起作用的上限 —— 松开 20 就是松开那唯一
一根（不是「两根里的一根」）。逐条与用例见
`test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake`。
"""

from __future__ import annotations

import ast
import inspect
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, service, state  # noqa: E402

#: 同一个站的确定性重放脚本里**一共几个 step**（`forms/_remote/www.gowizard.com_auto-warranty_.py`：
#: 19 个 state 组、各自的 `steps` 数相加 = 25；动作分解 `goto 1 / click 19 / form 5`）。
#: 这是「一趟探路至少要推进几步」的硬目标。
TARGET_MUTATING_STEPS = 25

#: 实测：4 趟被轮预算钉死的真站探路，**每一轮成功推进了几个动作**（`job-*.jsonl` 复算）。
#: 取最坏的那个当折算率 —— 预算要按最坏情况给，不然「够不够」就成了撞运气。
#: ⚠️ **分子只数 `result.ok == true` 的动作**：`job-dc1bece3b52c` 那一趟 20 轮里有 12 轮
#: 调了会改页面的工具，其中 **4 轮 `ok == false`**（页面上没找到那个元素，那一步没做成）
#: —— 「点了没做成」**不叫推进**。2026-09-20 复审复算过：把 `ok == false` 也算进去，
#: 这四个数会偏成 0.450 / 0.500 / 0.500 / 0.600，下界跟着偏成 57。
MEASURED_ACTIONS_PER_ROUND = (0.450, 0.450, 0.450, 0.400)

#: 结构上走得完的下界（轮）：动作数 ÷ 最坏推进率，**再加收尾那一轮**
#: （模型不调工具、只把结论说出来 —— 那一轮不推进任何动作，但没它就没有结论）。
IMPLIED_ROUNDS = (math.ceil(TARGET_MUTATING_STEPS / min(MEASURED_ACTIONS_PER_ROUND)) + 1)


def test_the_round_budget_covers_a_25_step_replay_at_the_measured_rate():
    """**轮预算不许低于「25 步按实测速率折算」的下界** —— 低于它就是结构上走不完。

    这一条就是 `job-62b192d2aca4` 那个停因的回归钉子：那一趟拿了 20 轮、
    推进了 10 个动作、然后被钉死；而要走完需要 **64 轮**（按真·最坏 0.400 折算）。

    ⚠️ 那个 64 是**修正过**的：原先是按 0.450 折出 57，而 0.450 那个分子里混进了
    「点了没做成」的动作。分子换成只数成功的之后，最坏是 `job-dc1bece3b52c` 的
    `8/20 = 0.400` ⇒ `ceil(25/0.400) + 1 = 64`。
    """
    assert IMPLIED_ROUNDS == 64, (
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


#: ★ 2026-10-08 的**真长漏斗**样本（`job-42daa38499e9`，hormn.com，build）：
#: `attempts.jsonl` 记 `rounds=86 / steps=95`、`stop_reason=budget_rounds`、
#: `end_reason=explore_unfinished`、`delivered=false` —— **一个 py 都没有**。
#: ⚠️ 它不是「卡住」：95 步里**只有 1 步**真的没做成（一个选择器没找到），
#: 一路在推进（13 题的问卷 + 注册漏斗，最后已经走到 app 页）⇒ 是**路比预算长**。
MEASURED_LONG_FUNNEL_ROUNDS = 86
MEASURED_LONG_FUNNEL_STEPS = 95


def test_the_budget_also_covers_a_real_long_funnel_not_just_the_gowizard_one():
    """**上面那个 64 只管得住「25 步的短漏斗」** —— 真长漏斗今天量到了一个，比它大。

    `IMPLIED_ROUNDS` 那个 64 是从**一个**已知重放（gowizard 的 25 步）折出来的，
    而它的样本子集**自己承认是偏的**（「4 条全是同一个站、全是被 20 轮钉死的那一档」）。
    2026-10-08 拿到了那条注释一直在等的**第一个真样本**：hormn 那趟按 13 题问卷 +
    注册的漏斗，烧到 **86 轮 / 95 步还没走完**就被旧的 80 掐断。

    ⇒ 两条下界**同时**要成立：短漏斗那条（64）管「别退回去」，这条（86/95）管
    「今天这种长度的漏斗也走得完」。**旧的断言一条都没动**（`IMPLIED_ROUNDS == 64`
    照旧钉着），这是**另加**的一条 —— 加而不是改，因为那两个数说的是两件事。
    """
    assert browser_agent.DEFAULT_MAX_ROUNDS > MEASURED_LONG_FUNNEL_ROUNDS, (
        "轮预算 %d 不大于实测的长漏斗需求 %d —— 那种 13 题问卷 + 注册的站**结构上又走不完**"
        "（2026-10-08 `job-42daa38499e9` 就是这么停在 80 的：86 轮还在推进，被掐断，"
        "`explore_unfinished`，没有 py）"
        % (browser_agent.DEFAULT_MAX_ROUNDS, MEASURED_LONG_FUNNEL_ROUNDS))
    #: ⚠️ 步预算这半条**不许**写成 `DEFAULT_MAX_STEPS > MEASURED_LONG_FUNNEL_STEPS`（95）——
    #: 变异测试当场抓到了：**旧的 100 本来就 > 95**，那条断言改成什么样都是绿的，是个**假钉子**
    #: （它看着像在钉「这次放大了」，其实一个字节都没钉住）。
    #: 步预算真正的规矩是「**不许先于轮数卡死**」—— 实测每轮正好一次工具调用，
    #: 所以步数卡住时模型连收尾那一轮都拿不到；那条规矩由下面
    #: `test_the_step_budget_does_not_bind_before_the_round_budget` 钉着。
    #: 这里钉的是**这一放之后它仍然成立**：`steps > rounds = 160`，顺带就覆盖了 95。
    assert browser_agent.DEFAULT_MAX_STEPS > browser_agent.DEFAULT_MAX_ROUNDS, (
        "步预算 %d 不大于轮预算 %d —— 实测每轮正好一次工具调用 ⇒ 步会先卡死，"
        "而那个停因（`budget_steps`）连收尾那一轮都不给。长漏斗那 95 步也就一起被盖住了"
        % (browser_agent.DEFAULT_MAX_STEPS, browser_agent.DEFAULT_MAX_ROUNDS))


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


# ───────────────── 「唯一刹车」那条（2026-09-20 复审量的）─────────────────
#
# ⚠️ 这一组钉的不是「注释怎么写」，是**注释里那句话是不是真的**：
#    「生产路径上 `stall_limit` 一次都不参与 ⇒ `max_rounds` 是唯一的刹车」。
#    不钉的话，下一个人调 `max_rounds` 时会以为「还有停滞判据兜着」——
#    而那条线是**断的**。


def _explore_calls(path: pathlib.Path) -> list:
    """这个文件里所有 `.explore(...)` 的调用节点（AST）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "explore"]


def _nested_funcs(path: pathlib.Path, outer: str, inner: str) -> list:
    """`outer` 里面定义的、叫 `inner` 的那些函数节点（AST）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == outer:
            found += [n for n in ast.walk(fn)
                      if isinstance(n, ast.FunctionDef) and n.name == inner]
    return found


def test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake():
    """⚠️ **生产路径上 `stall_limit` 一次都不参与** —— 所以 `max_rounds` 是**唯一**的刹车。

    这是「20 → 80 到底松开了什么」那一步的钉子。三截链子逐截量（少一截都不成立）：

      ① `browser_agent.explore` 的 `plan` 默认是 `None`，而那个 `watch`（停滞判据的载体）
         只在 `plan is not None and plan.actionable()` 时才建 ⇒ **不给 `plan` = 那条判据
         一次都不跑**；
      ② 图那一跳（`graph._explore` → `deps.explore(...)`）**不带 `plan=`**；
      ③ 服务那一层（`service._explore_for` 里的 `run(...)`）**连这个参数都没有** ——
         所以就算有人往②上加，生产上也会当场 TypeError（不是悄悄接上）。

    ⇒ 松开 20 就是松开**那唯一一根**上限：一个卡住的模型在一趟里对**真站**能做的
    改页面动作从 20 个变成 **80 个**（2026-10-08 又放到 160，同一个道理 —— 见模块顶上）。
    哪天有人把 `plan` 接进生产路径，这一条会红 ——
    那正是 `DEFAULT_MAX_ROUNDS` 旁边那张注释该被重写的时候。
    """
    # ① `plan` 默认 None（不给 = 没有 watch = 停滞判据不参与）
    sig = inspect.signature(browser_agent.explore)
    assert "plan" in sig.parameters, (
        "`browser_agent.explore` 没有 `plan` 参数了 —— 这条判据的前提变了，重读一遍再改它")
    assert sig.parameters["plan"].default is None, (
        "`explore` 的 `plan` 默认不再是 `None`（%r）—— 「生产路径上没有刹车之外的判据」"
        "这句话就不成立了" % (sig.parameters["plan"].default,))

    # ①b 不给 `plan` 时不建 `watch`（那几行是这条结论的落点，还在不在这儿）
    bsrc = pathlib.Path(browser_agent.__file__).read_text(encoding="utf-8")
    assert "if plan is not None and plan.actionable():" in bsrc, (
        "`explore` 里那句「只有真的有计划才建 watch」不见了 —— 停滞判据参与不参与，"
        "判据就变了")

    # ② 图那一跳不带 `plan=`
    calls = _explore_calls(pathlib.Path(graph.__file__))
    deps_calls = [c for c in calls if isinstance(c.func.value, ast.Name)
                  and c.func.value.id == "deps"]
    assert deps_calls, "`agent/graph.py` 里找不到 `deps.explore(...)` 那一跳 —— 判据的前提没了"
    for c in deps_calls:
        kws = {kw.arg for kw in c.keywords}
        assert "plan" not in kws, (
            "`graph.py:%d` 的 `deps.explore(...)` 开始传 `plan=` 了 ⇒ 生产路径上**多了一根**"
            "刹车，`DEFAULT_MAX_ROUNDS` 那张注释（「唯一那根」）当场作废，重写它"
            % c.lineno)

    # ③ 服务那一层没有这个参数（接不上，不是悄悄接上）
    runs = _nested_funcs(pathlib.Path(service.__file__), "_explore_for", "run")
    assert runs, "`service._explore_for` 里找不到那个 `run(...)` —— 判据的前提没了"
    for r in runs:
        names = {a.arg for a in list(r.args.args) + list(r.args.kwonlyargs)}
        assert "plan" not in names, (
            "`service.py:%d` 的 `run(...)` 长出了 `plan` 参数 ⇒ 服务可能开始交计划进来了，"
            "「生产路径上没有停滞判据」这句话要重新量" % r.lineno)


def test_the_default_explore_callable_is_the_one_that_takes_no_plan():
    """`Deps.explore` 的默认值是 `browser_agent.explore` —— 生产上服务**每次**都换掉它，
    但「不换」那条路也得是同一根口径（否则 `Deps()` 的默认值本身就成了一个没量的洞）。"""
    default = graph.Deps.__dataclass_fields__["explore"].default
    assert default is browser_agent.explore, (
        "`Deps.explore` 的默认值不再是 `browser_agent.explore`（%r）—— "
        "「不给 plan」这条口径要跟着重新量" % (default,))
