"""R-19 的**盘上那一档**：状态存 sqlite 文件 ⇒ 服务重启之后 job 还接得下去。

## 为什么有它（`docs/交接-2026-09-24-步进循环型.md` 欠账 ③）

线上这个部署**没有 Postgres**（`/health` 一直报 `memory`）⇒ 服务一重启，
`_recover` 读到的 `values` 是空的（`agent/service.py:4311`）⇒ **那次的活就没了**。
今天补上的那一档是 `sqlite:////…`：一条依赖（`langgraph-checkpoint-sqlite`）+
一个文件，不引入任何外部服务 —— 而且**真的**过进程边界：

1. `Checkpointer` 认得这个 URL，`/health` 如实报 `sqlite`（不是「易失」）；
2. 图停在闸口 → **换一条连接**（新 saver 对象）→ 接着跑，且探路不重来；
3. 账本那三个 dataclass（`Journey` / `Report` / `Run`）**真的**从文件里回得来；
4. 服务那一层（`_recover` + HTTP）：**换一个 app**（新登记表、新连接）→ 同一个
   job 还接得下去、还交付 —— 这一条才是欠账 ③ 的正身；
5. URL 认不出来时**大声拒**，不许悄悄退回内存 saver（那会把「重启还在」说成恢复）。

⚠️ 与 `tests/test_pg_checkpointer.py` 的分工：那条要一个真 Postgres（没给就 skip），
这条**默认就跑**（临时目录里一个文件，不依赖任何外部服务）。
"""

from __future__ import annotations

import pathlib
import sys
import uuid

import pytest
from langgraph.types import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, service  # noqa: E402
from test_service import (Rec, _brief, _deps, _real_factory,  # noqa: E402
                          _reply_until_done, _wait, _client)

URL = "https://example-funnel.test/quiz"


def _url(tmp_path) -> str:
    """一句 `sqlite:////…`（**四个斜杠** = 绝对路径，见 `Checkpointer._sqlite_path`）。"""
    return "sqlite:///%s" % (tmp_path / "checkpoints.sqlite")


def _db(tmp_path):
    return service.Checkpointer(url=_url(tmp_path))


# ─────────────────────────── URL → 哪一档 saver ───────────────────────────


def test_the_url_picks_the_kind_and_an_unknown_scheme_is_not_postgres():
    """`checkpoint_kind`：两种开头的 URL 各归各的，**认不出来就空串**（不猜成 Postgres）。"""
    assert service.checkpoint_kind("sqlite:////tmp/x.sqlite") == "sqlite"
    assert service.checkpoint_kind("sqlite:///rel.sqlite") == "sqlite"
    assert service.checkpoint_kind("postgresql://u@h/db") == "postgres"
    assert service.checkpoint_kind("postgres://u@h/db") == "postgres"
    assert service.checkpoint_kind("mysql://u@h/db") == ""
    assert service.checkpoint_kind("") == ""


def test_the_env_prefers_the_siteforge_name_over_database_url(monkeypatch):
    """两个名字都认，但 `SITEFORGE_CHECKPOINT_URL` 更具体 ⇒ 它说了算（一处口径）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://old/db")
    monkeypatch.setenv("SITEFORGE_CHECKPOINT_URL", "sqlite:////tmp/new.sqlite")
    assert service.checkpoint_url() == "sqlite:////tmp/new.sqlite"
    monkeypatch.delenv("SITEFORGE_CHECKPOINT_URL")
    assert service.checkpoint_url() == "postgresql://old/db"      # 老的还在，别把它读没了
    monkeypatch.delenv("DATABASE_URL")
    assert service.checkpoint_url() == ""                         # 没配 = 一等值（空串）


def test_an_unrecognised_url_refuses_instead_of_quietly_going_in_memory(tmp_path):
    """认不出来的 URL ⇒ **当场挑明**，不许退回内存 saver。

    为什么非这样不可：退回内存时 `/health` 会说「重启就没了」（诚实），但**跑起来是好的** ⇒
    人以为「配上了」；直到服务重启那天才发现状态全没了 —— 那时已经不是配置问题了。
    """
    bad = service.Checkpointer(url="mysql://u@h/db")
    assert bad.kind == "unknown", bad.kind
    assert "认不出" in bad.say()
    with pytest.raises(ValueError) as got:
        bad.get()
    assert "认不出来" in str(got.value) and "内存" in str(got.value)


def test_sqlite_path_reads_four_slashes_as_absolute_and_three_as_relative():
    """`sqlite:////abs` = 绝对；`sqlite:///rel` = 相对（照 sqlite 的老约定，别自作主张）。"""
    got = service.Checkpointer._sqlite_path
    assert got("sqlite:////opt/siteforge/runtime/checkpoints.sqlite") \
        == "/opt/siteforge/runtime/checkpoints.sqlite"
    assert got("sqlite:///runtime/checkpoints.sqlite") == "runtime/checkpoints.sqlite"
    with pytest.raises(ValueError):
        got("sqlite:")


def test_the_service_builds_a_sqlite_saver_and_says_so(tmp_path):
    """`/health` 如实报 `sqlite`，并且那句话不再说「重启就没了」。"""
    from fastapi.testclient import TestClient
    app = service.create_app(graph_factory=lambda brief, deps: None, checkpointer_url=_url(tmp_path))
    body = TestClient(app).get("/health").json()
    assert body["checkpointer"] == "sqlite", body
    assert "重启也还在" in body["say"], body["say"]
    assert "重启就没了" not in body["say"], body["say"]
    # saver 是**懒**建的（`import agent.service` 不该去连库）⇒ 文件要 `get()` 那一下才出现
    app.state.service._check.get()
    assert (tmp_path / "checkpoints.sqlite").is_file(), "建 saver 时没把那个文件建出来"
    rec = Rec()
    thread = "sq-%s" % uuid.uuid4().hex[:8]
    cfg = {"configurable": {"thread_id": thread}}
    brief = {"url": URL, "goal": "走通", "success_text": "Thank you",
             "ws_url": "ws://window/1", "form_file": "/tmp/form.json",
             "allow_skips": ["country", "viewport"],
             "out_dir": str(tmp_path / "forms" / "sites")}

    # —— 第一次：接上、中断 ——
    first = graph.build(checkpointer=_db(tmp_path).get(), deps=_deps(rec, tmp_path))
    out = first.invoke(brief, cfg)
    # ⚠️ 第一道闸是 `explore`（`intake` 自 2026-09-20 起不设闸 —— 见 `agent/graph.py:386`）
    assert out["__interrupt__"][0].value["step"] == "explore"
    del first                                     # 图对象没了（相当于进程死了）

    # —— 第二次：新图、**新连接**、同一个 thread ——
    second = graph.build(checkpointer=_db(tmp_path).get(), deps=_deps(rec, tmp_path))
    snap = second.get_state(cfg)
    assert snap.values.get("url") == URL, "状态没从文件里回来"
    assert snap.values.get("success_text") == "Thank you"
    while out.get("__interrupt__"):
        out = second.invoke(Command(resume="continue"), cfg)

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert pathlib.Path(out["py_path"]).is_file()
    assert len(rec.explore) == 1, "恢复之后探路又跑了一遍 —— 那不是恢复"


def test_the_ledger_dataclasses_come_back_from_sqlite(tmp_path):
    """`Journey` / `Report` / `Run` **真的**从文件里回得来（msgpack 那三个点名允许的类）。

    没点名允许时 serde 会警告；真读不回来的话，恢复出来的账本是空的 ——
    而**空账本看起来与「还没探路」一模一样**。
    """
    rec = Rec()
    thread = "sq-%s" % uuid.uuid4().hex[:8]
    cfg = {"configurable": {"thread_id": thread}}
    app = graph.build(checkpointer=_db(tmp_path).get(), deps=_deps(rec, tmp_path))
    app.invoke({"url": URL, "goal": "走通", "success_text": "Thank you",
                "ws_url": "ws://window/1", "form_file": "/tmp/form.json",
                "allow_skips": ["country", "viewport"],
                "out_dir": str(tmp_path / "forms" / "sites")}, cfg)
    app.invoke(Command(resume="continue"), cfg)                 # 跑完 intake
    app.invoke(Command(resume="continue"), cfg)                 # 跑完 explore

    snap = graph.build(checkpointer=_db(tmp_path).get(),
                       deps=_deps(rec, tmp_path)).get_state(cfg)
    journey = snap.values.get("journey")
    assert isinstance(journey, browser_agent.Journey), type(journey)
    assert journey.states() and journey.fills(), "账本从文件里回来是空的"
    assert journey.stop_reason == "model_done"


def test_the_service_survives_a_restart_on_sqlite(tmp_path):
    """★ 欠账 ③ 的正身：**换一个 app**（新登记表、新连接）→ 同一个 job 还接得下去、还交付。

    走 HTTP 那一层（`POST /run` → `GET /job/{id}` → `reply`）—— 也正是
    `_recover`（`agent/service.py:4298`）那条路：登记表里没有这个 job，但**盘上有**。
    """
    rec = Rec()
    first = _client(graph_factory=_real_factory(rec, tmp_path, saver=_db(tmp_path).get()),
                    checkpointer_url=_url(tmp_path), window=None)
    job_id = first.post("/run", json=_brief(tmp_path, allow_skips=["country", "viewport"])
                        ).json()["job_id"]
    _wait(first, job_id)
    assert rec.explore == []                      # 还停在第一道闸上

    # —— 服务「重启」：新 app、新 saver、新连接（登记表是空的）——
    second = _client(graph_factory=_real_factory(rec, tmp_path, saver=_db(tmp_path).get()),
                     checkpointer_url=_url(tmp_path), window=None)
    view = _wait(second, job_id)
    assert view["status"] == "waiting", view
    assert view.get("gate", {}).get("step") == "explore", view

    done = _reply_until_done(second, job_id)
    assert done["status"] == "done", done
    assert done["delivered"] is True, done
    assert len(rec.explore) == 1

    # 第三个 app 只读一眼：状态确实住在文件里（不是上一步那个进程的内存）
    third = _client(graph_factory=_real_factory(rec, tmp_path, saver=_db(tmp_path).get()),
                    checkpointer_url=_url(tmp_path), window=None)
    again = third.get("/job/%s" % job_id).json()
    assert again["delivered"] is True, again
    assert again["result"]["end_reason"] == "delivered"
