"""auto：到闸**自己按**（服务替人按），图那半边一个字不改。

计划：`docs/superpowers/plans/2026-09-24-auto-repair.md` 的 Task 1 / Task 2。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service, state  # noqa: E402
#: ⚠️ 用 `test_service.py` 那套**真会停在闸上**的夹具（`FakeGraph` + `_gate`）——
#: 别用 `test_service_narration` 那套：那一套的桩**一口气跑到底**，从来不停在闸口，
#: 于是「没人按」这件事根本发生不了（Task 1 第一次就是这么红的）。
from test_service import FakeGraph, _Snap, _brief, _client, _gate, _wait  # noqa: E402

GATE_1 = _Snap(values={"visits": ["intake"]}, next=("explore",), interrupts=(_gate("explore"),))
GATE_2 = _Snap(values={"visits": ["intake", "explore"]}, next=("draft",),
               interrupts=(_gate("draft"),))


def _draft_gates():
    """一串「刚写完一版」的闸：`visits` 里 draft 的个数 = 已经到第几遍（0/1/2/3）。"""
    return [_Snap(values={"visits": ["intake"] + ["draft"] * n}, next=("draft",),
                  interrupts=(_gate("draft"),)) for n in range(4)]


def _live_said(client, job_id) -> str:
    return " ".join(e["say"] for e in client.get("/job/%s/live" % job_id).json()["events"])


#: ★ 2026-10-08：**停在同一处的两道人造闸**（`visits` 一模一样）—— `auto_stuck` 的现场。
#: 判据在 `service._auto_press` 里那个 `stamp`（「这一趟走到哪了」的指纹）：同一个 stamp
#: 被按第二遍 ⇒ 不再按，交给人的那句话写明「原地打转」。
SAME_PLACE_TWICE = [_Snap(values={"visits": ["intake"]}, next=("draft",),
                          interrupts=(_gate("draft"),))] * 2


def _auto_over_the_real_chain(tmp_path, steps):
    """带 `auto` 跑一趟真链，返回 `(client, job_id)` —— **借给别处复用**。

    ⚠️ 抽成函数是为了**借**（见下面两个 `*_over_the_real_chain`），不是为了省几行：
    `tests/test_service_events.py::test_no_catalog_row_is_silent` 那条并集用例要拿这两条
    场景证明「这两个词真说得出来」—— 那里已经有这条纪律（借 `test_steer` 那两条时写的）：
    **抄第二份就是两份口径**，哪天通道改了只会有一份跟着动。
    """
    fg = FakeGraph(steps=steps)
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=dict(_brief(tmp_path), auto=True)).json()["job_id"]
    _wait(client, job_id, until=("waiting", "done", "failed"))
    return client, job_id


def auto_replied_over_the_real_chain(tmp_path) -> set:
    """「闸口自己过去了」那一条场景记过的 kind 集合（借给并集用例）。"""
    client, job_id = _auto_over_the_real_chain(tmp_path, [GATE_1, GATE_2])
    return {e["kind"] for e in client.get("/job/%s/live" % job_id).json()["events"]}


def auto_stuck_over_the_real_chain(tmp_path) -> set:
    """「停在同一个地方两遍」那一条场景记过的 kind 集合（借给并集用例）。"""
    client, job_id = _auto_over_the_real_chain(tmp_path, SAME_PLACE_TWICE)
    return {e["kind"] for e in client.get("/job/%s/live" % job_id).json()["events"]}


def test_auto_replies_at_the_gate_without_a_human(tmp_path):
    """带 auto 的那一趟：过闸**不需要人按**，而且时间线上留一条痕（谁按的要说清）。

    量三件：
      ① 闸口**自己过去了**（两次 `auto_replied`，两道人造的闸各一次）；
      ② 那条痕的话**就是服务那句**（`AUTO_REPLIED_SAY`，页面/账本都照它说）；
      ③ 时间线上**不许**冒出「人说的」那一条 —— 这一下不是人按的（两张嘴不许混成一张）。
    """
    client, job_id = _auto_over_the_real_chain(tmp_path, [GATE_1, GATE_2])

    events = client.get("/job/%s/live" % job_id).json()["events"]
    said = [e["say"] for e in events if e["kind"] == "auto_replied"]
    assert len(said) == 2, "两道人造的闸没都被自动按过去：%r" % (
        [(e["kind"], e["say"]) for e in events],)
    assert set(said) == {service.AUTO_REPLIED_SAY}, said
    assert not [e for e in events if e["kind"] == "human_said"], events


def test_auto_stops_only_where_the_graph_itself_caps(tmp_path):
    """★ Task 2 + 2026-10-08（用户：自测没过那三趟 —— 「拿这份记录去定位，定位完回 draft
    改一版」里「定位」和「改一版」都是人干的；要它自己好）。

    为什么这条承重：不加它，自动那条路会一直往下修（每一遍都是钱 + 一次真提交），
    而「修了两遍还不过 ⇒ 这件事 AI 搞不定」是领导拍的那个**停点**（spec §5）。

    ★ 2026-10-08 改的是**停在哪儿**：停点必须**正好**是图自己触顶那一步 ——
    因为「留料」（把这一版写进交付路径、`unverified` 里写清「自测没过」）只发生在触顶
    那一步（`graph._selftest` → `_keep_unverified`，`tests/test_graph.py` 钉着）。
    auto 要是**早一步**抛人，运营那边又是「什么都没有」—— 而这一趟明明已经有一版稿了。
    """
    client, job_id = _auto_over_the_real_chain(tmp_path, _draft_gates())
    view = _wait(client, job_id, until=("waiting", "done", "failed"))

    assert view["status"] == "waiting", view                       # 停在闸上等人（不再自动按）
    said = _live_said(client, job_id)
    assert "两遍" in said, said
    assert "需要人" in said, said
    #: 按了几下 = **首版那一闸 + 每一次重写各一闸**：按到图自己触顶那一步为止。
    events = client.get("/job/%s/live" % job_id).json()["events"]
    presses = [e for e in events if e["kind"] == "auto_replied"]
    assert len(presses) == state.Caps().max_diagnoses + 1, [
        (e["kind"], e["say"]) for e in events]


def test_the_auto_stop_point_is_pinned_to_the_graphs_own_cap(tmp_path):
    """★ 2026-10-08：`AUTO_MAX_ATTEMPTS` **不许**与图的上限各写一份 —— 一处口径。

    判据是一行等式（跨文件钉子，与 `AUTO_CANNOT_DO` 那张表同一条纪律）：
    `AUTO_MAX_ATTEMPTS == Caps().max_diagnoses + 1`。
    错成更小 ⇒ auto 在图触顶**之前**抛人 ⇒ 「留料」那一步永远不走 ⇒ 运营只看到
    「什么都没有」（真实现场：`job-b9f0c6a74cbd` / `job-9c5b17be2629` / `job-cfefcd7a3aef`
    三趟都停在 `diagnose` 闸上，`src` 有值、`unverified` 是空的）。
    """
    caps = state.Caps()
    assert service.AUTO_MAX_ATTEMPTS == caps.max_diagnoses + 1, (
        service.AUTO_MAX_ATTEMPTS, caps.max_diagnoses)


def test_auto_stops_when_the_graph_lands_in_the_same_place_twice(tmp_path):
    """★ 2026-10-08 补：**图原地打转**那一档 —— 同一个「停在哪」按第二遍就不按了。

    与上面那条**不是同一件事**（`service._auto_press` 自己的注释点名了要分开读）：
      · 上面那条是 **`cap_hit`**（「自动那条路到头了」：两遍/做不成）；
      · 这一条是 **`auto_stuck`**（**停在同一个 stamp 上**：按下去它还是停在原地 ⇒ 再按
        只是白花一遍的钱和一次真提交）。
    真实的「原地打转」是：`step` 一样、`visits` 一样、`end_reason` / `report` 的长度也一样
    —— 也就是**这一趟走到哪了**一个字都没变。这里用两道**一模一样**的闸造那个形状。

    ⚠️ **这条原本一个用例都没有**（2026-10-08 清存量时发现的）：`auto_stuck` 在生产代码里
    有调用点，而全仓**没有任何一条用例**把它逼出来过 —— 那正是
    `tests/test_service_events.py::test_no_catalog_row_is_silent` 那条并集判据抓到的东西
    （它按 AST 从调用点长名单，所以「新写一个没人记的 kind」它**会**红；红的时候是代码对、
    是缺口在用例上）。
    """
    client, job_id = _auto_over_the_real_chain(tmp_path, SAME_PLACE_TWICE)
    view = _wait(client, job_id, until=("waiting", "done", "failed"))
    assert view["status"] == "waiting", view

    events = client.get("/job/%s/live" % job_id).json()["events"]
    kinds = [e["kind"] for e in events]
    #: 只按了**一下**：第二下之前就认出来「它没动」
    assert kinds.count("auto_replied") == 1, kinds
    assert kinds.count("auto_stuck") == 1, kinds
    #: 那句痕**就是生产那一句**（`agent/auto.py` 的常量，页面/账本都照它说）——
    #: 不在这儿另抄一份措辞（抄了就有两份口径）。
    stuck = [e["say"] for e in events if e["kind"] == "auto_stuck"][0]
    assert stuck == service.AUTO_STUCK_SAY % ("draft", 1), stuck
