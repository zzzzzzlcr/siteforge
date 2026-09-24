"""auto：到闸**自己按**（服务替人按），图那半边一个字不改。

计划：`docs/superpowers/plans/2026-09-24-auto-repair.md` 的 Task 1 / Task 2。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402
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


def test_auto_replies_at_the_gate_without_a_human(tmp_path):
    """带 auto 的那一趟：过闸**不需要人按**，而且时间线上留一条痕（谁按的要说清）。

    量三件：
      ① 闸口**自己过去了**（两次 `auto_replied`，两道人造的闸各一次）；
      ② 那条痕的话**就是服务那句**（`AUTO_REPLIED_SAY`，页面/账本都照它说）；
      ③ 时间线上**不许**冒出「人说的」那一条 —— 这一下不是人按的（两张嘴不许混成一张）。
    """
    fg = FakeGraph(steps=[GATE_1, GATE_2])
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=dict(_brief(tmp_path), auto=True)).json()["job_id"]
    _wait(client, job_id, until=("waiting", "done", "failed"))

    events = client.get("/job/%s/live" % job_id).json()["events"]
    said = [e["say"] for e in events if e["kind"] == "auto_replied"]
    assert len(said) == 2, "两道人造的闸没都被自动按过去：%r" % (
        [(e["kind"], e["say"]) for e in events],)
    assert set(said) == {service.AUTO_REPLIED_SAY}, said
    assert not [e for e in events if e["kind"] == "human_said"], events


def test_auto_stops_after_two_attempts_and_names_why(tmp_path):
    """★ Task 2：写着「已经到第 3 遍」的闸 ⇒ **不再按**，并说清「两遍都没过、需要人」。

    为什么这条承重：不加它，自动那条路会一直往下修（每一遍都是钱 + 一次真提交），
    而「修了两遍还不过 ⇒ 这件事 AI 搞不定」是领导拍的那个**停点**（spec §5）。
    """
    fg = FakeGraph(steps=_draft_gates())
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=dict(_brief(tmp_path), auto=True)).json()["job_id"]
    view = _wait(client, job_id, until=("waiting", "done", "failed"))

    assert view["status"] == "waiting", view                       # 停在闸上等人（不再自动按）
    said = _live_said(client, job_id)
    assert "两遍" in said, said
    assert "需要人" in said, said
    #: 而且**只按了两下**（不是按到刹车、也不是无限按）
    events = client.get("/job/%s/live" % job_id).json()["events"]
    presses = [e for e in events if e["kind"] == "auto_replied"]
    assert len(presses) == 2, [(e["kind"], e["say"]) for e in events]
