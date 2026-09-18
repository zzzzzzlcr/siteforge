"""Task 2：探路时的步拍策略 —— 只留「不对劲」的那些步（设计注 §5.4）。

**判据一句话**：留的集合 = **{没做成}** ∪ **{做成了但页面没变}**，其余情况**一张不留**。

三条形状上的要点（都来自 §5.4）：
1. **点前那条命令是下限、省不掉** —— 动手之前不可能知道这一步会不会出问题。
   所以「只对可疑的步拍」落在**留**上，落不到**拍**上。
2. **「页面签没变」是免费信息** —— `_Pages` 已有的 `(url, title, page_text[:400])`，
   下一张 `observe` 一到就能比，不额外发命令。
3. **判据只在「紧接着」的那次观测上生效** —— `click A → click B → observe` **不认**
   （那时「没变」说不清是谁造成的），**认不出来就按「不留」**。

全部用**桩**：桩 session（`test_browser_agent._run` 那套）+ **桩 shooter**。**不开浏览器。**
"""

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, shots  # noqa: E402
from test_browser_agent import PAGE_LANDING, PAGE_QUIZ, _run  # noqa: E402

#: 一张**真** PNG 的头几个字节。内容不重要 —— 这几条用例断的是**文件在不在盘上**。
PNG_HEAD = b"\x89PNG\r\n\x1a\n"


class _Shooter:
    """桩 shooter：记下每次被叫的落点，按需「拍不成」或「炸掉」。

    ⚠️ 它同时是**命令计数器**：`dests` 的长度 = 「发出去几条截图命令」。
    这个数在几条用例里比「盘上剩几张」更要紧 —— `shots_dir=None` 那条断的就是它。
    """

    def __init__(self, root, *, fail=False, boom=False, mark=b""):
        self.root = pathlib.Path(root)
        self.dests = []
        #: **每次被叫时，盘上还剩什么**（按名字排序）。
        #: 为什么要记这个：`explore` 收尾会 `finish()` 把没结算的删掉 ——
        #: 只看「跑完盘上剩几张」的话，「观测**当场**结算了」与「收尾顺手清了」**分不开**，
        #: 而这两件事是**不同的性质**（前者是策略在跑，后者只是没漏）。
        self.state_at_call = []
        self.fail = fail
        self.boom = boom
        #: 写进字节里的标记（默认空）—— 跨趟那几条用例靠它分辨「这个名字上的**像素**是谁的」：
        #: 只看「文件还在不在」分不出「第 1 趟那张还在」与「第 2 趟写了个同名的、内容已经换主」。
        self.mark = mark

    def __call__(self, session, dest):
        dest = pathlib.Path(dest)
        self.dests.append(dest)
        self.state_at_call.append(sorted(p.name for p in self.root.glob("*.png")))
        if self.boom:
            raise RuntimeError("shooter 炸了（桩）")
        if self.fail:
            return None, "拍不成（桩说的）"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_HEAD + bytes(self.mark))
        return dest.name, ""

    @property
    def left(self):
        """盘上还剩几张（按名字排序）。"""
        return sorted(p.name for p in self.root.glob("*.png"))


def _go(tmp_path, responses, turns, shooter, **kwargs):
    """跑一趟桩探路，shooting 打开。

    `shots_dir` 用**桩自己那个 root** —— 「落点由调用方给的目录说了算」这条要求，
    两边必须是同一个目录才验得出来。
    """
    journey, fake, calls = _run(tmp_path, responses, turns,
                                shots_dir=shooter.root, shooter=shooter, **kwargs)
    return journey, fake, calls


# ────────────────── 不拍的：看两眼不算「动页面」──────────────────


def test_observe_and_diff_never_shoot(tmp_path):
    """`observe` / `diff` **一次都不拍** —— 它们不改页面，拍它们等于给每一步都留一张。"""
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}], "diff": [{"structured": {"actionable": True}}]},
        [{"calls": [("observe", {}), ("diff", {}), ("observe", {})]},
         {"content": "看完了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert [x["action"] for x in journey.steps] == ["observe", "diff", "observe"], journey.steps
    assert s.dests == [], f"观测步不该拍，却拍了 {len(s.dests)} 次"
    assert s.left == []


# ────────────────── 不留的：跑顺的那一步 ──────────────────


def test_a_clean_click_leaves_nothing_on_disk(tmp_path):
    """**跑顺**的一步（紧接着的观测显示页面变了）→ **两张都不留、磁盘上一个文件都没有**。

    这是整个策略的**主要收益**：一次跑顺的探路可以一张不留。

    ⚠️ **光看「跑完盘上剩几张」验不出这条** —— `explore` 收尾会 `finish()` 把没结算的删掉，
    于是「观测**当场**结算了」与「收尾顺手清了」**长得一模一样**。
    所以这里跑**两步**，并断在 `state_at_call` 上：**第二次动手时盘上必须是空的**
    —— 那证明第一张是在**观测那一刻**就没的，不是收尾时才清的。
    """
    second = dict(PAGE_QUIZ, page_text="第二问：你平时读多少本书", title="Example 第二问")
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING},
                     {"structured": PAGE_QUIZ},
                     {"structured": second}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "两步都跑顺了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    clicks = [x for x in journey.steps if x["action"] == "click"]
    assert len(clicks) == 2 and all(c["result"]["ok"] is True for c in clicks), journey.steps

    assert len(s.dests) == 2, f"两步各拍一张（点前是下限），实际 {len(s.dests)} 次"
    assert s.state_at_call[1] == [], \
        f"第二次动手时盘上还有 {s.state_at_call[1]} —— 第一张不是观测结算掉的（是收尾清的）"
    assert s.left == [], f"跑顺的两步都不该留东西，盘上却有 {s.left}"
    for c in clicks:
        assert not c.get("shot_before"), f"跑顺了还挂着点前那张：{c.get('shot_before')!r}"
        assert not c.get("shot_after")


# ────────────────── 留的两种：没做成 / 没变 ──────────────────


def test_a_click_that_changed_nothing_keeps_both_and_marks_deferred(tmp_path):
    """**做成了但页面没变** → 两张都留，而且点后那张标 `shot_after_deferred=True`。

    ⚠️ 为什么必须标：点后那张是**随后补拍**的（在紧接着那次观测那一刻），
    **签没变 ≠ 像素没变**（spinner / 布局抖动）—— 不许把两种来源的图显示成一模一样的东西。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "点了好像没反应"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]

    assert click["result"]["ok"] is True, click["result"]
    assert click["shot_before"], "没变的那一步必须留点前那张"
    assert click["shot_after"], "没变的那一步必须留点后那张"
    assert click["shot_after_deferred"] is True, "随后补拍的那张必须标 deferred"
    assert sorted(s.left) == sorted([click["shot_before"], click["shot_after"]]), s.left


def test_a_failed_click_keeps_both_immediately(tmp_path):
    """**`ok` 为假** → 两张都留，而点后那张是**当场**拍的（`shot_after_deferred is False`）。

    与上一条成对：同样是「两张都留」，但**来源不同**，所以标志位必须不一样 ——
    把两种来源混成一个值，那一列就在说假话。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}],
         "click": [{"error": "没有找到选择器 #ghost（这个页面上没有它）"}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#ghost"})]},
         {"content": "点不着"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]

    assert click["result"]["ok"] is False, click["result"]
    assert click["shot_before"], "没做成的那一步必须留点前那张"
    assert click["shot_after"], "没做成的那一步必须留点后那张"
    assert click["shot_after_deferred"] is False, "当场拍的那张不许标成 deferred"
    assert sorted(s.left) == sorted([click["shot_before"], click["shot_after"]]), s.left


# ────────────────── 认不出来的：一律不留 ──────────────────


def test_two_mutating_actions_in_a_row_keep_nothing(tmp_path):
    """`click A → click B → observe(没变)` → **都不留**（§5.4 判据 2）。

    为什么：那次观测**说不清「没变」是谁造成的** —— A 与 B 之间没有观测，
    A 的效果从没被看见过。**认不出来就按「不留」。**

    ⚠️ 这条是「紧接着」那三个字的**唯一**验法：只看单步的实现会在这里留下 B 的两张。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "连点两下都没反应"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    clicks = [x for x in journey.steps if x["action"] == "click"]
    assert len(clicks) == 2, journey.steps

    assert s.dests, "两个动作都该拍（点前是下限）"
    assert s.left == [], f"认不出来的那一段一张都不该留，盘上却有 {s.left}"
    for c in clicks:
        assert not c.get("shot_before"), c.get("shot_before")
        assert not c.get("shot_after"), c.get("shot_after")


def test_a_screenshot_in_between_does_not_break_the_chain(tmp_path):
    """`click → screenshot → observe(没变)` → **照留**。

    **为什么 `screenshot` 不算断链**：它**不动页面** —— 所以「动手时那一签」与
    「现在这一签」照样可比，判据仍然成立。真正让判据失效的只有**另一个动页面动作**
    （那个由 `tainted` 挡下，见上一条）。

    ⚠️ 我第一版把 `screenshot`/`wait` 也当成断链（保守），复审驳了：
    **同一段代码里 `diff` 是透明的、`wait` 却断链，同性质两个待遇**；
    而且保守的方向在这里是**多截**（该留的不留），不是少留。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}],
         "screenshot": [{"structured": {"png_base64": "AAAA"}}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("screenshot", {})]},
         {"calls": [("observe", {})]},
         {"content": "中间夹了一张截图"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert click["result"]["ok"] is True, click["result"]
    assert click["shot_before"] and click["shot_after"], "夹一张截图不该把该留的截掉"
    assert click["shot_after_deferred"] is True
    assert len(s.left) == 2, s.left


# ────────────────── 「页面没变」只对 click / goto 成立 ──────────────────


@pytest.mark.parametrize("action,args", [
    ("form", {"selector": "#email", "value": "a@b.test"}),
    ("scroll", {"selector": "#get-started"}),
])
def test_a_successful_fill_or_scroll_keeps_nothing(tmp_path, action, args):
    """**做成了的 `form` / `scroll`，即使页面签没变，也一张不留。**

    为什么：**「页面签没变」这条判据对它们根本不成立** —— 签是 `body.innerText`，
    填框不改它、滚动也不改它。拿它判 = **必然假阳性**。
    产物侧那张表（`template.DIFF_JUDGES = ("click", "goto")`）写的就是这个道理，
    这里**复用同一张表**，不抄第二份。

    ⚠️ 复审实测过这条假阳性的代价：一趟**全部成功**的漏斗（3×form + 1×scroll + 1×click）
    在盘上留了 **10 张**，而 40 张的上限会被**健康步**吃掉 —— 真正不对劲的步反而没有图了。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        # 两次观测**同一页**（签没变）—— 换成 click 的话这就该留了
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [(action, args)]},
         {"calls": [("observe", {})]},
         {"content": "填了/滚了，页面没变"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    step = [x for x in journey.steps if x["action"] == action][0]
    assert step["result"]["ok"] is True, step["result"]
    assert s.left == [], f"{action} 做成了却留了 {s.left}（签没变对它不是判据）"
    assert not step.get("shot_before")


def test_the_two_diff_judge_tables_agree():
    """**两张 `DIFF_JUDGES` 必须一致**：agent 侧一份，产物模板字符串里一份。

    为什么**没法合成一份**：生成的 py 是**独立跑的**（生产里它不 import `agent`），
    所以它必须自带那张表。**两份是结构上免不了的**，那就得有东西盯着它们别分家 ——
    分家的后果：产物在 `form`/`scroll` 上按「判不了」办、agent 侧按「判得了」办，
    **同一步两边留法不同**，而这种不一致**不会响**（各自都自洽）。

    这条哨兵**直接从模板字符串里抠**产物那份 —— 不抄、不靠注释。
    """
    import re as _re

    from agent import template as _template

    found = _re.search(r"^DIFF_JUDGES = \((.*?)\)$",
                       _template.SKELETON.template, _re.M)
    assert found, "产物模板串里找不到 DIFF_JUDGES —— 它改名了？那这条哨兵要跟着改"
    artifact = tuple(_re.findall(r'"([^"]+)"', found.group(1)))

    assert artifact == tuple(browser_agent.DIFF_JUDGES), (
        f"两张表分家了：产物 {artifact} vs agent {tuple(browser_agent.DIFF_JUDGES)}")
    assert artifact == ("click", "goto")


# ────────────────── 点前那张**不许漏在盘上** ──────────────────


def test_an_attempt_ending_on_a_click_leaves_nothing(tmp_path):
    """**一趟以动页面动作收尾** → 点前那张必须删掉（收尾结算）。

    为什么这条要紧：模型收工 / 预算到顶 / **人按停** 都会这么收尾，而**「按停」正是这个
    功能的主交互**。不结算的话那张图会留在盘上、没有任何 `shot_before` 指向它、
    **也不计入 `kept`** —— `MAX_KEPT_SHOTS` 于是**管不住盘**。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"content": "就点这一下，收工"}],       # ← 没有后续观测
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert [x["action"] for x in journey.steps] == ["observe", "click"], journey.steps
    assert s.dests, "点前那张该拍"
    assert s.left == [], f"收尾没结算，盘上漏了 {s.left}"
    assert journey.steps[1].get("shot_before") is None


def test_a_failed_observation_after_a_click_leaves_nothing(tmp_path):
    """`click`（成功）→ **`observe` 报错** → **再跟一次成功的 `observe`** → 点前那张必须删掉。

    报错的观测**结算不了**手上那一步，而「紧接着」这个条件**再也回不来了** ——
    所以按「不留」办。

    ⚠️ **为什么要跟那第三次**：不跟的话，`explore` 收尾的 `finish()` 也会把那张删掉，
    **两种原因长得一样**（实测：去掉这处作废，只跑两步的版本照样绿）。
    跟上之后区别就出来了 —— 那次**成功的**观测会拿**过期的签**去比，页面上什么都没变，
    于是它会**错误地留下**两张。**这条用例断的就是那个「错误地留下」。**
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING},
                     {"error": "这一眼没看成"},
                     {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("observe", {})]},
         {"content": "第一眼没看成，第二眼看到了同一页"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    looks = [x for x in journey.steps if x["action"] == "observe"]
    assert looks[1]["result"]["ok"] is False, looks[1]["result"]
    assert looks[2]["result"]["ok"] is True, looks[2]["result"]

    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert s.left == [], f"链在报错那一眼就断了，点前那张该删，盘上却剩 {s.left}"
    assert not click.get("shot_before")


# ────────────────── 四个动页面动作都要拍 ──────────────────


@pytest.mark.parametrize("action,args", [
    ("click", {"selector": "#get-started"}),
    ("form", {"selector": "#email", "value": "a@b.test"}),
    ("scroll", {"selector": "#get-started"}),
    ("goto", {"url": "https://example.test/funnel"}),
])
def test_every_mutating_action_shoots_before(tmp_path, action, args):
    """`click` / `form` / `scroll` / `goto` **四个都要**拍 —— 一个都不能漏。

    `MUTATING` 是**从 `REPLAY_ACTIONS` 推出来的**（不是手抄的第二张表）：
    哪天 `REPLAY_ACTIONS` 加了一个动作，这里会跟着变；手抄的表不会，那正是这个仓库
    栽过的形状（「第二张手写名单」）。
    """
    assert set(browser_agent.MUTATING) <= set(browser_agent.REPLAY_ACTIONS)
    assert set(browser_agent.MUTATING) == {"click", "form", "scroll", "goto"}, browser_agent.MUTATING

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ}]},
        [{"calls": [("observe", {})]},
         {"calls": [(action, args)]},
         {"calls": [("observe", {})]},
         {"content": "走了一步"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert len(s.dests) == 1, f"{action} 这一步没拍（拍了 {len(s.dests)} 次）"


# ────────────────── 上限 ──────────────────


def test_the_cap_stops_shooting_and_says_so(tmp_path, monkeypatch):
    """到上限之后**不再拍**，并往 `journey.notes` 写一句人话。

    判据落在两处：`shooter.dests` 不再增长（命令真的没发），
    以及人话在账上（人看得出「后面那些为什么没有图」）。
    """
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 2, raising=True)

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},                     # 没变 → 留 2 张，到顶
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},                     # 这一步起就不该再拍
         {"content": "到头了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert len(s.left) == 2, f"上限 2，盘上却是 {s.left}"
    assert len(s.dests) == 2, f"到顶之后还在拍：一共发了 {len(s.dests)} 条截图命令"
    assert any("上限" in n or "到顶" in n for n in journey.notes), \
        f"到顶没说一句人话：{journey.notes}"


def test_the_cap_is_hard_at_the_moment_of_the_write_not_only_at_finish(tmp_path, monkeypatch):
    """**上限在要落盘的那一刻就硬** —— 不是收尾重算一遍。

    复审实测的那个形状：结算钩子（`_keep`）自己抛异常 ⇒ `kept` **永远不涨** ⇒
    上限 2 的那道闸**一次都没 engage**：**10 条命令、运行期盘上最多 9 张**，
    收工后才由收口抹平到 5。**「收口把账做平」与「闸是硬的」是两件事。**

    判据 = **运行期任一刻**的盘上张数（桩 shooter 每次被叫时记的 `state_at_call`），
    **不是**收工后那个数。`+1` 是因为它记的是**落盘之前**那一刻 —— 那一张马上要写下去。
    """
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 2, raising=True)

    def boom(*_a, **_kw):
        raise RuntimeError("_keep 自己炸了（桩）")

    monkeypatch.setattr(browser_agent._StepShots, "_keep", boom)

    s = _Shooter(tmp_path / "shots")
    turns = [{"calls": [("observe", {})]}]
    for _ in range(5):
        turns.append({"calls": [("click", {"selector": "#get-started"})]})
        turns.append({"calls": [("observe", {})]})          # 每一下都没变 ⇒ 每次都该留两张
    turns.append({"content": "连点五下"})
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}] * 10},
        turns,
        s,
        budget=browser_agent.Budget(max_steps=20, max_rounds=20),
    )
    assert len(journey.steps) >= 6, [x["action"] for x in journey.steps]
    peak = max((len(seen) + 1 for seen in s.state_at_call), default=0)
    assert peak <= 2, (
        f"上限 2，运行期盘上却到过 {peak} 张 —— 闸是收尾才算的，不是落盘那一刻："
        f"{[len(x) for x in s.state_at_call]}")
    assert len(s.left) <= 2, f"收工后也超了：{s.left}"
    assert any("上限" in n for n in journey.notes), f"撞上限没说一句人话：{journey.notes}"


class _Notes(list):
    """人话账本 —— **每句话写下去的那一刻，`where` 底下真有几张图**。

    `journey.notes` 只是个 list，所以换成本类就能在**说话的那一刻**把盘拍下来。
    为什么要它：上限那句人话报了一个数（「已经有 N 张了」），而它报得对不对看的是
    **说那句话的当时**盘上有几张 —— 跑完之后再量是另一回事（后面还会落盘、还会删）。
    """

    def __init__(self, where):
        super().__init__()
        self.where = pathlib.Path(where)
        #: 与 `self` **一一对应**：第 i 句话写下去那一刻，`where` 里的 png 名字（排序）。
        self.state_at_say: list = []

    def append(self, text) -> None:
        super().append(text)
        self.state_at_say.append(sorted(p.name for p in self.where.glob("*.png")))

    def extend(self, items) -> None:
        for item in items:                 # 逐条走 `append` —— 搬家也一样记盘（口径只有一份）
            self.append(item)


def _notes_spy(monkeypatch, where):
    """把 `journey.notes` 换成会记账的那本（见 `_Notes`），返回它。

    换在 `_StepShots` **出生那一刻**：`explore()` 里它是跟着 `journey` 一起建的，
    所以从它一存在起，后面每一句话都在账上。之前已经写下的话一并搬过去（逐条 `append`）。
    """
    real_init = browser_agent._StepShots.__init__
    notes = _Notes(where)

    def spy_init(self, journey, shots_where, shooter):
        real_init(self, journey, shots_where, shooter)
        notes.extend(journey.notes)
        journey.notes = notes

    monkeypatch.setattr(browser_agent._StepShots, "__init__", spy_init)
    return notes


def test_the_gate_follows_the_disk_not_how_many_were_written(tmp_path, monkeypatch):
    """**闸必须跟着「盘上此刻几张」走** —— 一张被丢掉之后它当场放松，不许留幽灵计数。

    上限这件事有**两半**，而 `_take` 只钉住了其中一半（顶不许破）。这一条钉的是另一半：

    形状：上限 2 + **四个跑顺的 click**（每个都拍一张点前图，紧接着那次观测显示页面变了
    ⇒ 当场把它删掉 ⇒ 盘上任何一刻都不超过 1 张）。正确行为下闸**一次都不该 engage**
    ⇒ 四个 click **一个都不许被挡** —— 点前那张是**下限**（动手之前不可能知道这一步会不会
    出问题，所以它省不掉）。

    ⚠️ 拿「写过多少张」（`_written`）或者「一个只增不减的集合」当「盘上有多少张」时，
    闸会在第 3 步起**关死**：后面每一步的**点前图全没了**（正是这个功能存在的理由的反面），
    而且它还会写下一句**假话** ——「盘上已经有 2 张了」，而此刻目录**是空的**。
    两条变异（`_drop` 不减 `_on_disk` / 闸改数 `_written`）在这条之前**各自 31 全绿**。
    """
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 2, raising=True)

    pages = [dict(PAGE_LANDING, title="Example 第 %d 屏" % i, page_text="第 %d 屏" % i)
             for i in range(5)]
    turns = [{"calls": [("observe", {})]}]
    for _ in range(4):
        turns.append({"calls": [("click", {"selector": "#get-started"})]})
        turns.append({"calls": [("observe", {})]})        # 页面变了 ⇒ 这一步跑顺 ⇒ 点前那张丢掉
    turns.append({"content": "四步都跑顺了"})

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": p} for p in pages]},
        turns, s, budget=browser_agent.Budget(max_steps=20, max_rounds=20),
    )
    clicks = [x for x in journey.steps if x["action"] == "click"]
    assert len(clicks) == 4 and all(c["result"]["ok"] is True for c in clicks), \
        f"这条形状该是四个跑顺的 click：{[x['action'] for x in journey.steps]}"
    assert len(s.dests) == 4, (
        f"四个跑顺的 click 各该拍一张点前图（那是下限），实际只发了 {len(s.dests)} 条命令 —— "
        f"闸被前面那些**已经删掉**的图关死了。每次动手前盘上：{s.state_at_call}")
    assert s.left == [], f"跑顺的四步一张都不该留，盘上却有 {s.left}"
    lied = [n for n in journey.notes if "已经有" in n]
    assert not lied, f"盘上任何一刻都不超过 1 张，却报了「撞上限」：{lied}"


def test_the_cap_note_reports_a_number_that_is_really_on_disk(tmp_path, monkeypatch):
    """**那句人话报的数必须是真的** —— 说「已经有 N 张」时，`where` 底下真要有 N 张。

    形状（上限 2）：第 1 步**跑顺**（点前那张丢掉 ⇒ 盘上回到空），第 2 步**点了没变**
    （两张都留 ⇒ 盘上 2 张），第 3 步动手前撞上限。

    判据落在**说那句话的那一刻**（`_Notes` 把盘拍在 append 里），不是收工之后 ——
    收工那个数由收口收拾过，与「当时句话说得对不对」是两件事。
    「已经有 N 张」里的 N 是**盘上此刻的实况**：报大了会让人以为图还在（幽灵计数），
    报小了会让运维按错的数去翻目录。
    """
    monkeypatch.setattr(browser_agent, "MAX_KEPT_SHOTS", 2, raising=True)

    where = tmp_path / "shots"
    notes = _notes_spy(monkeypatch, where)

    turns = [{"calls": [("observe", {})]},                            # 第 1 屏
             {"calls": [("click", {"selector": "#get-started"})]},    # 跑顺：点前那张会丢
             {"calls": [("observe", {})]},                            # 第 2 屏 ≠ 第 1 屏
             {"calls": [("click", {"selector": "#get-started"})]},    # 点了没变：两张都留
             {"calls": [("observe", {})]},                            # 还是第 2 屏
             {"calls": [("click", {"selector": "#get-started"})]},    # 动手前：撞上限
             {"calls": [("observe", {})]},
             {"content": "看看它报几张"}]
    s = _Shooter(where)
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING},
                     {"structured": PAGE_QUIZ},
                     {"structured": PAGE_QUIZ},
                     {"structured": PAGE_QUIZ}]},
        turns, s, budget=browser_agent.Budget(max_steps=20, max_rounds=20),
    )
    cap = [(text, state) for text, state in zip(notes, notes.state_at_say)
           if "已经有" in text]
    assert cap, f"这条形状该撞上限（不撞就是在量空气）：{list(notes)}"
    for text, state in cap:
        m = re.search(r"已经有 (\d+) 张", text)
        assert m, f"这句人话里没有那个数，判不出来就别放过：{text!r}"
        assert int(m.group(1)) == len(state), (
            "这句人话报的数与它说话那一刻盘上的实况对不上：说「已经有 %d 张」，"
            "而那一刻 %s 底下是 %d 张（%s）—— 报大 = 让人以为图还在，报小 = 按错的数去翻目录"
            % (int(m.group(1)), where, len(state), state))


# ────────────────── 拍照永远不许把探路搞挂 ──────────────────


def test_a_broken_shooter_does_not_break_the_run(tmp_path):
    """**shooter 抛异常 → 探路照常跑完。**（简报点名「最关键的一条」）

    拍照是**旁路**：它坏了不该把一趟真探路拖垮 —— 与 `emit`（旁路坏掉不许带塌主路）
    同一条规矩。失败要说出来（`journey.shots_why` / `notes`），**但不许抛上去**。
    """
    s = _Shooter(tmp_path / "shots", boom=True)
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "拍不成也走完了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert [x["action"] for x in journey.steps] == ["observe", "click", "observe"], journey.steps
    assert journey.steps[1]["result"]["ok"] is True, journey.steps[1]["result"]
    # 断在两处「说法分得开」上 —— 与上一条（shooter **回一句**拍不成）**必须不一样**：
    # 「截图工具坏了」和「步拍自己的代码坏了」是**两个诊断**，混成一个值就分不出该修哪儿。
    assert "拍照时它抛了" in journey.shots_why, journey.shots_why
    assert "步拍自己坏了" not in journey.shots_why, journey.shots_why
    assert s.left == []


def test_the_step_shot_code_itself_blowing_up_does_not_break_the_run(tmp_path, monkeypatch):
    """**步拍自己的代码抛异常 → 探路照常跑完。**（旁路纪律的**另一半**）

    ⚠️ 与「shooter 抛异常」那条**不是**同一件事：那条挡的是**外部世界**（截图工具），
    这条挡的是**步拍自己的代码**（写错了、外部模块改名了……）。
    **实测栽过一次**：`on_observation` 里一个 `AttributeError` 被 `run_tool_loop`
    记成了「**一次工具失败**」—— 于是**模型照着那条假错换路走**，
    而用例报的是 `KeyError: 'shot_before'`（看起来像「没留图」，其实是**拍照把探路搞挂了**）。
    """
    def boom(*_a, **_kw):
        raise AttributeError("步拍自己坏了（桩）")

    monkeypatch.setattr(browser_agent._StepShots, "on_observation", boom)

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "步拍坏了也走完了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    # 三步都真的发生了，而且**那次观测没被记成「工具失败」**
    assert [x["action"] for x in journey.steps] == ["observe", "click", "observe"], journey.steps
    assert all(x["result"]["ok"] is True for x in journey.steps), \
        [x["result"] for x in journey.steps]
    assert "AttributeError" in journey.shots_why, journey.shots_why


def test_a_shooter_that_returns_a_reason_is_recorded(tmp_path):
    """shooter **不抛、但报「拍不成」** → 那句话要进 `journey.shots_why`。

    与上一条成对：**抛异常**和**回一句「拍不成」**是两条不同的路（`shots.capture_via_session`
    两条都不抛，它把失败说在返回值里）—— 只堵其中一条的实现会在另一条上静默。
    """
    s = _Shooter(tmp_path / "shots", fail=True)
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"content": "拍不成"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert s.dests, "该试还是要试"
    assert "桩说的" in journey.shots_why, f"shooter 报的原因没进账：{journey.shots_why!r}"


# ────────────────── 不给就不拍（回归的底线）──────────────────


def test_no_shots_dir_means_the_shooter_is_never_called(tmp_path):
    """**`shots_dir=None` → shooter 一次都不调。**

    这是回归的底线：不给这个参数时，行为要与今天**一字不差**。
    断在 `dests` 上（命令计数）而不是盘上 —— 「拍了又删」也会让盘是空的，
    但那已经**发过命令**了，不是「一个字节不变」。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "没给目录"}],
        shooter=s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert s.dests == [], f"没给 shots_dir 却拍了 {len(s.dests)} 次"
    assert [c["name"] for c in calls] == ["observe", "click", "observe"], calls


def test_the_degrade_switch_turns_every_step_shot_off(tmp_path, monkeypatch):
    """**降级 B**：`SITEFORGE_STEP_SHOTS=0` ⇒ 每步抓拍**一次都不拍**（只留闸拍）。

    为什么要有这条：设计注 §5.5 拿「一张图多少钱」当**降级判据**（> 1.5s 就关掉）。
    实测是 193ms ⇒ **默认走主路**；而这条开关是**退路** —— 退路没验过就等于没有。
    """
    monkeypatch.setenv("SITEFORGE_STEP_SHOTS", "0")
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "关掉了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert s.dests == [], f"降级开关开了却还拍了 {len(s.dests)} 次"
    assert s.left == []
    # 探路本身**照常**：降级关的是抓拍，不是探路。
    assert [x["action"] for x in journey.steps] == ["observe", "click", "observe"], journey.steps


def test_the_degrade_switch_is_exactly_zero(tmp_path, monkeypatch):
    """只有**恰好** `"0"` 才关 —— 别的值（空串 / `"1"` / 没设）一律**照拍**。

    判据写宽了（比如「非空就关」）会让 `SITEFORGE_STEP_SHOTS=1` 这种**明确要求开着**的
    部署反而被关掉 —— 那是**反着来**，比没有这个开关更坏。
    """
    for value in ("1", "", "yes"):
        monkeypatch.setenv("SITEFORGE_STEP_SHOTS", value)
        s = _Shooter(tmp_path / f"shots-{value or 'empty'}")
        _go(
            tmp_path,
            {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
            [{"calls": [("observe", {})]},
             {"calls": [("click", {"selector": "#get-started"})]},
             {"calls": [("observe", {})]},
             {"content": "开着"}],
            s,
            budget=browser_agent.Budget(max_steps=10, max_rounds=10),
        )
        assert s.dests, f"SITEFORGE_STEP_SHOTS={value!r} 不该关掉抓拍"


def test_the_shot_files_land_where_the_caller_said(tmp_path):
    """落点由**调用方给的那个目录**说了算（`explore` 自己不去猜 job 目录）。"""
    where = tmp_path / "somewhere-else"
    s = _Shooter(where)
    _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "看看落在哪"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert s.dests, "该拍"
    for d in s.dests:
        assert where in d.parents, f"落点跑出调用方给的目录了：{d}"


# ────────── 一个名字 = 一次落盘（跨趟 / 同趟都不许撞）──────────


def _one_attempt(tmp_path, where, shooter, responses, turns):
    """跑一趟桩探路（`shots_dir` = `where`）—— 跨趟那两条用例要跑**两趟**，共用一个目录。"""
    return _go(tmp_path, responses, turns, shooter,
               budget=browser_agent.Budget(max_steps=20, max_rounds=20))[0]


def test_a_second_attempt_on_the_same_job_dir_cannot_eat_the_first_attempts_evidence(tmp_path):
    """**同一个 job 目录里的两趟探路：第 2 趟的收口不许碰第 1 趟留下的图。**

    生产形状（`agent/service.py` 的 `shots.dir_for(job_id)`）：一次节点**最多 3 趟**重探
    共用一个 `<root>/<job_id>/`，而收口是**按名字**删的。复审实测（名字不带趟号那版）：
    第 2 趟写了自己的 `step-1-before.png`（它这一步是跑顺的，紧接着的观测就把那张删了），
    而**第 1 趟特意留下的那张证据正压在这个名字上** ⇒ **2 张丢 1 张**。

    判据 = **第 1 趟引用到的名字，一个都不许没**（外加：第 2 趟自己那几张要么在、要么不留）。

    ⚠️ 「**字节还得是第 1 趟的**」那句断言原来长在这条用例里，复审把它**打不红**
    （`M-D-tag-constant`：强制同标记，这条形状里断1 先响，断2 **永远够不着**）——
    因为这条形状的第 2 趟是**跑顺**的一趟：它一写就删，名字先没了，字节换主这件事
    根本量不到。**一条永远不会红的钉子 = 没钉**，所以那半根轴搬去了它自己的形状：
    `test_two_attempts_with_the_same_tag_still_never_overwrite`（第 2 趟也**留**两张）。
    这条只管名字那根轴。
    """
    where = tmp_path / "shots"
    first = _Shooter(where, mark=b"one")
    j1 = _one_attempt(
        tmp_path, where, first,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "点了没变，两张都留"}])
    kept_by_first = {st.get("shot_before") for st in j1.steps} | \
                    {st.get("shot_after") for st in j1.steps}
    kept_by_first.discard(None)
    assert len(kept_by_first) == 2, f"第 1 趟该留两张：{kept_by_first}"

    second = dict(PAGE_QUIZ, page_text="第二问：你平时读多少本书", title="Example 第二问")
    second_shooter = _Shooter(where, mark=b"two")
    j2 = _one_attempt(
        tmp_path, where, second_shooter,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": second}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "这一步跑顺了（一张不留）"}])
    # ⚠️ 别用 `second_shooter.left`：那个属性 glob 的是**整个共用目录**（含第 1 趟留的）。
    wrote_by_second = [d.name for d in second_shooter.dests]
    assert wrote_by_second, "第 2 趟该拍过（点前那张是下限）"
    assert not [n for n in wrote_by_second if (where / n).is_file()], \
        f"第 2 趟是跑顺的一步，它自己写的那张不该留：{wrote_by_second}"
    assert not ({st.get("shot_before") for st in j2.steps} - {None}), \
        [st.get("shot_before") for st in j2.steps]

    gone = sorted(n for n in kept_by_first if not (where / n).is_file())
    assert gone == [], f"第 2 趟的收口把第 1 趟的证据删了：{gone}（盘上现在 {second_shooter.left}）"


def test_two_attempts_with_the_same_tag_still_never_overwrite(tmp_path, monkeypatch):
    """**两趟拿到同一个标记时，第 1 趟那张还是一个字节都不许换主。**

    上一版挡住跨趟撞车的**只有** `uuid4().hex[:6]`（24 位）⇒ 那是**概率**（3 趟 ≈ 1.8e-7），
    而类注释把它写成了「一个名字 = 一次落盘」这条不变量。复审逐字复现过这个形状
    （`M-D-tag-constant`）：强制两趟同标记 ⇒ 第 2 趟把第 1 趟那张写成了自己的像素。

    所以这条用例**故意让标记撞上**（桩把 `self.tag` 写死成 `aaaaaa`）—— 随机标记下这个形状
    一辈子也复现不出来，而它是**唯一**能分辨「名字的唯靠 uuid」与「名字的唯靠盘上有没有人占」
    的形状。撞车时**正确行为是让名字岔开**（第 2 趟数到下一个没人占的名字），
    不是「反正 uuid 撞不上、照写」：后者一写就把别人的证据换主，而**证据被换主比没有证据更坏**
    （页面上看着像第 1 趟那一步的图，其实是第 2 趟的）。

    ⚠️ 第 2 趟**留两张**（点了没变）是这条形状的要害：跑顺那趟一写就删，名字先没了，
    「字节换主」这件事就**量不到**（那是 `test_a_second_attempt_…` 里那句断言的形状，
    复审判它够不着）。`_name_for` 里那道 `exists()` 去掉，红的就是这条。
    """
    where = tmp_path / "shots"
    real_init = browser_agent._StepShots.__init__

    def same_tag(self, journey, shots_where, shooter):
        real_init(self, journey, shots_where, shooter)
        self.tag = "aaaaaa"                  # 模拟 `uuid4().hex[:6]` 撞车

    monkeypatch.setattr(browser_agent._StepShots, "__init__", same_tag)

    first = _Shooter(where, mark=b"one")
    j1 = _one_attempt(
        tmp_path, where, first,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "第 1 趟：点了没变，两张都留"}])
    kept_by_first = {st.get("shot_before") for st in j1.steps} | \
                    {st.get("shot_after") for st in j1.steps}
    kept_by_first.discard(None)
    assert len(kept_by_first) == 2, f"第 1 趟该留两张：{kept_by_first}"
    assert sorted(first.left) == sorted(kept_by_first), first.left

    second = _Shooter(where, mark=b"two")
    j2 = _one_attempt(
        tmp_path, where, second,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "第 2 趟：同一个标记，也点了没变"}])
    wrote_by_second = [d.name for d in second.dests]
    assert len(wrote_by_second) == 2, f"第 2 趟该拍两张（点前是下限、点后是补拍）：{wrote_by_second}"
    kept_by_second = {st.get("shot_before") for st in j2.steps} | \
                     {st.get("shot_after") for st in j2.steps}
    kept_by_second.discard(None)
    assert len(kept_by_second) == 2, f"第 2 趟也该留两张（不然下面量的是空气）：{kept_by_second}"

    # ① 名字一个都不许没（第 1 趟的、第 2 趟的都不许）
    gone = sorted(n for n in (kept_by_first | kept_by_second) if not (where / n).is_file())
    assert gone == [], f"盘上该有 4 张（两趟各 2 张），这些没了：{gone}（盘上现在 {second.left}）"
    # ② 而且第 1 趟那两张**字节还是第 1 趟的** —— 同标记下最坏的那条路是「照写、把别人的换主」，
    #    这时 ① 照样绿（名字都在），只有这一句能分辨。
    swapped = sorted(n for n in kept_by_first
                     if (where / n).read_bytes() != PNG_HEAD + b"one")
    assert swapped == [], (
        f"这两张上的像素已经不是第 1 趟的了（两趟撞上同一个标记时，第 2 趟把它们顶了）：{swapped}"
        f"（第 2 趟写的是 {wrote_by_second}）")
    assert sorted(second.left) == sorted(kept_by_first | kept_by_second), second.left


def test_two_steps_never_share_one_name(tmp_path):
    """**同一个名字只许压一次落盘** —— 两条步引用同一张图时，前一条的证据是假的。

    复审记过这个形状（`click(没变) → observe(补拍) → click(失败)`）：两条步算出来的落点是
    **同一个** `step-3-after.png`（名字里的 `%d` 是**此刻**的 `len(journey.steps)`，
    **不是**步的唯一号），而盘上那串字节是后一条写的 ⇒ 前一条「点后」那张
    **看着像证据，其实是别人的像素**（比没有图更坏）。
    """
    s = _Shooter(tmp_path / "shots")
    j = _one_attempt(
        tmp_path, tmp_path / "shots", s,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}],
         "click": [{}, {"error": "没有找到选择器 #ghost"}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#ghost"})]},
         {"content": "第二下点不着"}])

    names = [d.name for d in s.dests]
    assert len(names) == 4, f"这条形状该拍 4 张：{names}"
    assert len(set(names)) == len(names), f"同一次落盘被写了两次（后一张会顶掉前一张）：{names}"
    for st in j.steps:
        for key in ("shot_before", "shot_after"):
            if st.get(key):
                assert (s.root / st[key]).is_file(), f"{st['action']} 引用的图不在盘上：{st[key]}"


def test_a_swallowed_step_shot_error_leaves_no_orphan(tmp_path, monkeypatch):
    """**吞掉异常之后，盘上仍然不许留无主的图 —— 而且收的是 `finish()`，不是那句回滚。**

    这个形状就是「本该被回滚挡住」的那个：结算钩子在**清 `_pending` 之前**抛
    （`_safe` 的 `except` 那一刻 `_pending` 还在手上），于是那张点前图没人引用。
    复审实测：以前挡住它的是 `_safe` 里那句 `self._discard_pending()`；**那句已经删掉**
    （它对「盘上不留孤儿」一次都没起作用 —— 结算钩子抛异常时 `_pending` 早就被清了，
    删掉它这一片**全绿**），现在挡住它的是 `finish()` 的两道收口。

    ⚠️ 所以这条用例**不能**只断「收工时盘上是空的」—— 那对「谁清的」一无所知。
    它在 `finish()` **跑之前**量一次：那张孤儿**真的在盘上**（不然这条用例是空转），
    跑完之后它没了 ⇒ 「收口是那道闸」这句话是**量出来的**，不是推断的。
    （`finish()` 里两道收口**都**删掉，红的就是这条；只删一道不会红 —— 两道都挡得住。）

    ⚠️ 反过来说：**把那句回滚加回去，这条也会红** —— 红的不是性质（盘上照样不留孤儿），
    是**前提**（那条形状下孤儿在收口之前就被清了，这条用例量不到东西）。加回回滚的人
    会看到这句人话，然后要来这里说清楚「为什么宁可丢那一步的证据也要把那链掐死」。
    """
    def boom(*_a, **_kw):
        raise RuntimeError("步拍自己坏了（桩）")

    monkeypatch.setattr(browser_agent._StepShots, "on_observation", boom)

    seen: dict = {}
    real_finish = browser_agent._StepShots.finish

    def spy(self):
        seen["before"] = sorted(p.name for p in self.where.glob("*.png"))
        real_finish(self)
        seen["after"] = sorted(p.name for p in self.where.glob("*.png"))

    monkeypatch.setattr(browser_agent._StepShots, "finish", spy)

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "步拍坏了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert [x["action"] for x in journey.steps] == ["observe", "click", "observe"], journey.steps
    assert "RuntimeError" in journey.shots_why, journey.shots_why
    assert len(s.dests) == 1, f"那张点前图该拍下去、然后没人引用它：{[d.name for d in s.dests]}"
    assert seen["before"] == [s.dests[0].name], (
        "收口跑之前盘上该正好压着那张没主的点前图（不然这条用例量的是空气）：%r"
        % (seen["before"],))
    assert seen["after"] == [], f"收口没把那张没主的图收掉：{seen['after']}"
    assert s.left == [], f"收工时盘上还有无主的图：{s.left}"


def test_a_failed_screenshot_does_not_void_the_chain(tmp_path):
    """**报错的 `screenshot` 不作废那条链** —— 它既不动页面、也不负责结算。

    复审点名：原来那条闸只按 `ok` 判，于是报错的 `screenshot` / `diff` 也会把链作废，
    而成功的不会 —— **同一个动作因为成功与否得到两种待遇**，
    而「凭什么」说不出来（它们既不改页面，也不结算）。

    只有**报错的 `observe`** 才该作废：它是唯一「本该结算而没结算成」的那个。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}],
         "screenshot": [{"error": "截图工具报错（桩）"}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("screenshot", {})]},
         {"calls": [("observe", {})]},
         {"content": "截图坏了，可页面没变"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    shot = [x for x in journey.steps if x["action"] == "screenshot"][0]
    assert shot["result"]["ok"] is False, shot["result"]
    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert click["shot_before"] and click["shot_after"], \
        "报错的截图不动页面也不结算，不该把 click 那条链作废"
    assert click["shot_after_deferred"] is True
    assert len(s.left) == 2, s.left


def test_the_accounting_reconciles_against_the_disk_not_the_pending(tmp_path, monkeypatch):
    """**收口按盘上的实数，不靠回滚** —— `_keep` 自己抛异常时也不留孤儿。

    复审当场证过：上一版加的那句「吞掉后回滚」**只在 `_pending` 还在手上时有用**，
    而 `_keep` 抛异常那一刻它**早就被清了** —— 把回滚删掉，25 条用例 **0 红**。
    根因一句话：**回滚只看得到 `_pending`，而账的真相在盘上。**

    所以 `finish()` 改成「**拍过的所有名字** 减 **留住的步引用到的**」。
    这条用例断的就是那个够不着的窗口：`_keep` 抛了，盘上**只剩 1 张** ——
    点后那张（`shot_after` 在 `_keep` 抛**之前**就已经设到步上了）⇒ **它有主、该留**；
    点前那张没人引用 ⇒ 孤儿，**收口必须把它删掉**。

    ⚠️ 「1 张」是**钉死**的数（不是 0 张、也不是 2 张）：这条 docstring 原来写的是
    「盘上**仍然 0 张**」，而实测是 1 —— **名字说 A、量的是 B**（修复轮 4 改的 A 条）。
    现在下面那条 `assert s.left == [...]` 量的就是它：说错一个数就红。
    """
    def boom(*_a, **_kw):
        raise RuntimeError("_keep 自己炸了（桩）")

    monkeypatch.setattr(browser_agent._StepShots, "_keep", boom)

    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "记账那一步炸了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    assert [x["action"] for x in journey.steps] == ["observe", "click", "observe"], journey.steps
    assert s.dests, "该拍"
    click = [x for x in journey.steps if x["action"] == "click"][0]
    # 断的是**不变量**（盘上每一个文件都有主），不是「盘上必须为空」——
    # `shot_after` 在 `_keep` 抛之前就已经设到步上了，所以它是**有主的**，留着是对的。
    # 上一版会红在这里：那张**点前**图没人引用，却是它写的 ⇒ 孤儿。
    assert not click.get("shot_before"), \
        f"`_keep` 抛了，点前那张不该留在步上：{click.get('shot_before')!r}"
    assert click.get("shot_after"), click
    referenced = set()
    for st in journey.steps:
        for key in ("shot_before", "shot_after"):
            if st.get(key):
                referenced.add(st[key])
    orphans = sorted(set(s.left) - referenced)
    assert orphans == [], f"盘上留下无主的图：{orphans}"
    assert s.left == [click["shot_after"]], (
        f"盘上该正好剩那张**有主的**点后图（1 张），实际 {s.left}")


def test_the_reconcile_does_not_delete_what_was_kept(tmp_path):
    """**收口不许删过头** —— 已经留好的那两张，一个都不许动。

    与上一条成对：**收口要「清掉孤儿」而不是「清空目录」**。
    复审实测：上一版让回滚清空整个目录也 **0 红** —— 说明「删过头」根本没人钉。
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "点了没变，该留两张"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert click["shot_before"] and click["shot_after"], click
    assert sorted(s.left) == sorted([click["shot_before"], click["shot_after"]]), \
        f"收口删过头了：{s.left}"


def test_the_gate_shot_in_the_same_dir_is_neither_counted_nor_deleted(tmp_path, monkeypatch):
    """**`kept` 的口径**：它是「**本趟步拍**留住的张数」，**不是**「这个目录里有几张图」。

    服务侧的闸拍（`pause-<n>.png`，`agent/service.py`）落在**同一个 job 目录**里
    （步拍与闸拍共用一个 `<root>/<job_id>/`）。两件事都要成立，而它们**不是**一件事：

    - **不删**：收口删的名单是 `_written`（本趟写过的），**不是扫目录** ⇒ 闸拍不会被误伤；
    - **不计入 `kept`**：`kept` 数的是本趟步拍留在盘上的那些（`finish()` 按 `journey.steps`
      引用重算）—— 数成「目录里的图数」的话，`kept` 就是**另一样东西的数**了。

    复审点名要把这条口径写下来（「让下一个人自己猜」是这个项目常见的死法），
    所以这里不给它留余地：**把闸拍预置在同一个目录里**，两件事各断一次。
    """
    where = tmp_path / "shots"
    where.mkdir(parents=True)
    gate = where / "pause-1.png"
    gate.write_bytes(PNG_HEAD + b"gate")

    seen: dict = {}
    real_finish = browser_agent._StepShots.finish

    def spy(self):
        real_finish(self)
        seen["kept"] = self.kept

    monkeypatch.setattr(browser_agent._StepShots, "finish", spy)

    s = _Shooter(where)
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "点了没变，该留两张"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert seen.get("kept") == 2, (
        "`kept` 该是「本趟步拍留在盘上的张数」= 2，实际 %r —— 目录里那第三张是**闸拍**，"
        "不归它管（口径见 `_StepShots` 的类注释）" % (seen.get("kept"),))
    assert gate.read_bytes() == PNG_HEAD + b"gate", \
        f"收口动了同一个目录里的闸拍（它不是本趟写的，一个字节都不该动）：{s.left}"
    assert sorted(s.left) == sorted([click["shot_before"], click["shot_after"], "pause-1.png"]), \
        s.left
