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

    def __init__(self, root, *, fail=False, boom=False):
        self.root = pathlib.Path(root)
        self.dests = []
        self.fail = fail
        self.boom = boom

    def __call__(self, session, dest):
        dest = pathlib.Path(dest)
        self.dests.append(dest)
        if self.boom:
            raise RuntimeError("shooter 炸了（桩）")
        if self.fail:
            return None, "拍不成（桩说的）"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_HEAD)
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
    """
    s = _Shooter(tmp_path / "shots")
    journey, _, _ = _go(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"content": "点完页面变了"}],
        s,
        budget=browser_agent.Budget(max_steps=10, max_rounds=10),
    )
    click = [x for x in journey.steps if x["action"] == "click"][0]
    assert click["result"]["ok"] is True, click["result"]

    assert len(s.dests) == 1, f"点前该拍一张（下限），实际拍了 {len(s.dests)} 次"
    assert s.left == [], f"跑顺的那一步不该留东西，盘上却有 {s.left}"
    assert not click.get("shot_before"), f"跑顺了还挂着点前那张：{click.get('shot_before')!r}"
    assert not click.get("shot_after")


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


def test_an_observation_that_is_not_the_next_action_breaks_the_chain(tmp_path):
    """`click → screenshot → observe(没变)` → **不留**。

    中间的 `screenshot` 本身不改页面、也不观测页面，但它让那次 `observe` **不再是紧接着的**
    —— 判据只认「紧接着」，所以按「认不出来」办。
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
    assert s.left == [], f"链断了就不该留，盘上却有 {s.left}"
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
    assert journey.shots_why, "拍不成要说得出为什么（人话），不许静默"
    assert s.left == []


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
