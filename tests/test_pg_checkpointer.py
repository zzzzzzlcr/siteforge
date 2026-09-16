"""R-19 的那条生产接线：**中断 → 恢复**走真 Postgres 保存点（不是内存里的那个）。

## 为什么这条平时是 skip

全局约束：**测试不许依赖外部服务**（自带 fixture）。宿主上本来也没有 Postgres，
而「必须有一个真库才能跑」会让整套测试在别的机器上跑不起来。所以：

- 没给 `SITEFORGE_PG_URL` → **skip**（理由写在 skip 那句话里，不是静默跳过）；
- 给了 → 真连、真建表、真中断、真恢复。

跑法（Task 8 的验收命令，一次性起一个**扔掉的**库）：

```bash
docker run -d --name sf-pg-acc -e POSTGRES_PASSWORD=sfacc -e POSTGRES_DB=siteforge \\
    -p 127.0.0.1:55432:5432 postgres:16-alpine
SITEFORGE_PG_URL=postgresql://postgres:sfacc@127.0.0.1:55432/siteforge \\
    .venv/bin/python -m pytest tests/test_pg_checkpointer.py -q
```

## 它钉的是什么

Task 7 用**内存** saver 验过「接 saver 就能恢复」（R-19 明确把这个折价写进了裁定：
「若『必须 Postgres 才算数』，那 Task 7 只证明了『接 saver 就能恢复』」）。
这条补上那半步，而且比「同一个 saver 对象」更严：

1. **换一条连接**接着跑（不是换一个图对象）—— 状态真在库里；
2. `Journey` / `Report` / `Run` 这三个 dataclass **真的**从库里回得来
   （msgpack 的 serde 要 `allowlisted(...)` 点名允许它们，见 `graph.MSGPACK_ALLOWLIST`）；
3. 恢复之后**探路不重来**（`explore` 只跑一次）—— 那是 P6 的判据。
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

import pytest
from langgraph.types import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, selftest, service  # noqa: E402
from test_service import (Rec, _brief, _deps, _journey, _pass_report,  # noqa: E402
                          _real_factory, _reply_until_done, _wait, _client)

URL = "https://example-funnel.test/quiz"
PG_URL = os.environ.get("SITEFORGE_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="没给 SITEFORGE_PG_URL —— 这条要一个真 Postgres（套件默认不依赖外部服务，R-19）")


# ─────────────────────────── 真 Postgres 的 saver ───────────────────────────


def _pg_saver():
    """`Checkpointer` **生产走的那条路**：psycopg 连接 → PostgresSaver → setup() → allowlisted。

    直接调服务里那个类（而不是在这儿另写一遍）—— 要验的正是**接线**那段代码本身。
    """
    from agent.service import Checkpointer
    return Checkpointer(url=PG_URL).get()


def _conn():
    import psycopg
    return psycopg.connect(PG_URL, autocommit=True)


def test_the_service_builds_a_postgres_saver_and_says_so():
    """`Checkpointer` 认得这个 URL，并且 `/health` 如实报「postgres」（不是「易失」）。"""
    from fastapi.testclient import TestClient
    from agent import service
    app = service.create_app(graph_factory=lambda brief, deps: None, checkpointer_url=PG_URL)
    body = TestClient(app).get("/health").json()
    assert body["checkpointer"] == "postgres", body
    assert "易失" not in body["say"], body["say"]


def test_interrupt_then_resume_through_postgres_with_a_fresh_connection():
    """**R-19 的正身**：图停在闸口 → 换一张图 + **换一条连接** → 接着跑，且探路不重来。

    这才是「人能停下来再回来」在生产里的样子：进程可以死，状态不能丢。
    """
    rec = Rec()
    thread = "pg-%s" % uuid.uuid4().hex[:8]
    cfg = {"configurable": {"thread_id": thread}}

    # —— 第一次：接上、中断 ——
    first = graph.build(checkpointer=_pg_saver(), deps=_deps(rec, pathlib.Path("/tmp")))
    out = first.invoke({"url": URL, "goal": "走通", "success_text": "Thank you",
                        "ws_url": "ws://window/1", "form_file": "/tmp/form.json",
                        "allow_skips": ["country", "viewport"],
                        "out_dir": "/tmp/sf-pg-test/forms/sites"}, cfg)
    assert out["__interrupt__"][0].value["step"] == "intake"
    del first                                     # 图对象没了（相当于进程死了）

    # —— 第二次：新图、**新连接**、同一个 thread ——
    second = graph.build(checkpointer=_pg_saver(), deps=_deps(rec, pathlib.Path("/tmp")))
    snap = second.get_state(cfg)
    assert snap.values.get("url") == URL, "状态没从库里回来"
    assert snap.values.get("success_text") == "Thank you"

    while out.get("__interrupt__"):
        out = second.invoke(Command(resume="continue"), cfg)

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert pathlib.Path(out["py_path"]).is_file()
    assert len(rec.explore) == 1, "恢复之后探路又跑了一遍 —— 那不是恢复"


def test_the_ledger_dataclasses_come_back_from_postgres():
    """`Journey` / `Report` / `Run` **真的**从库里回得来（msgpack 那三个点名允许的类）。

    没点名允许时，serde 会警告「will be blocked in a future version」—— 真读不回来的话，
    恢复出来的账本是空的，而**空账本看起来和「还没探路」一模一样**。
    """
    rec = Rec()
    thread = "pg-%s" % uuid.uuid4().hex[:8]
    cfg = {"configurable": {"thread_id": thread}}
    app = graph.build(checkpointer=_pg_saver(), deps=_deps(rec, pathlib.Path("/tmp")))
    app.invoke({"url": URL, "goal": "走通", "success_text": "Thank you",
                "ws_url": "ws://window/1", "form_file": "/tmp/form.json",
                "allow_skips": ["country", "viewport"],
                "out_dir": "/tmp/sf-pg-test/forms/sites"}, cfg)
    app.invoke(Command(resume="continue"), cfg)                 # 跑完 intake
    app.invoke(Command(resume="continue"), cfg)                 # 跑完 explore

    snap = graph.build(checkpointer=_pg_saver(),
                       deps=_deps(rec, pathlib.Path("/tmp"))).get_state(cfg)
    journey = snap.values.get("journey")
    assert isinstance(journey, browser_agent.Journey), type(journey)
    assert journey.states() and journey.fills(), "账本从库里回来是空的"
    assert journey.stop_reason == "model_done"


def test_the_service_survives_a_restart_on_postgres(tmp_path):
    """服务侧那条：**换一个 app（新的登记表、新的连接）**，同一个 job 还接得下去、还交付。

    这条走的是 HTTP 那一层（`POST /run` → `GET /job/{id}` → `reply`），也就是
    计划三的 Console 会走的那条路。
    """
    rec = Rec()
    # 两边接的必须是**同一个库**：job 那张图由调用方拼（`graph_factory`），而服务自己的
    # `Checkpointer` 是它用来读状态的 —— 生产里两者是同一份（`graph_factory=None`）。
    first = _client(graph_factory=_real_factory(rec, tmp_path, saver=_pg_saver()),
                    checkpointer_url=PG_URL, window=None)
    job_id = first.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]
    _wait(first, job_id)
    assert rec.explore == []

    second = _client(graph_factory=_real_factory(rec, tmp_path, saver=_pg_saver()),
                     checkpointer_url=PG_URL, window=None)
    view = _wait(second, job_id)
    assert view["status"] == "waiting", view

    done = _reply_until_done(second, job_id)
    assert done["status"] == "done", done
    assert done["delivered"] is True, done
    assert len(rec.explore) == 1

    # 第三个 app 只读一眼：状态确实住在库里（不是上一步那个进程的内存）
    third = _client(graph_factory=_real_factory(rec, tmp_path, saver=_pg_saver()),
                    checkpointer_url=PG_URL, window=None)
    again = third.get("/job/%s" % job_id).json()
    assert again["delivered"] is True, again
    assert again["result"]["end_reason"] == "delivered"


def test_reads_go_through_a_second_connection_so_progress_does_not_wait_for_a_run():
    """生产形状（`graph_factory=None`）：**读走第二条连接**。

    判据两条：
    ① 写锁被占着（模拟一次 `invoke` 正在跑）时，读**照样回得来** ——
       不排在那次可能跑几分钟的 invoke 后面；
    ② 那条读连接看到的是**同一份状态**（不是另开了一个空库）。

    这是「重要 2」的第二半：光把回话的状态挪开还不够 —— 如果 `GET /job/{id}` 要排在
    一次探路后面几分钟，Console 那边看上去和卡死没区别。
    """
    import threading
    import uuid as _uuid
    from fastapi.testclient import TestClient

    app = service.create_app(checkpointer_url=PG_URL)          # graph_factory=None = 生产形状
    svc = app.state.service
    thread = "pg-read-%s" % _uuid.uuid4().hex[:8]

    g = graph.build(checkpointer=svc._check.get(), deps=_deps(Rec(), pathlib.Path("/tmp")))
    g.invoke({"url": URL, "goal": "走通", "success_text": "Thank you",
              "ws_url": "ws://window/1", "form_file": "/tmp/form.json",
              "allow_skips": ["country", "viewport"],
              "out_dir": "/tmp/sf-pg-test/forms/sites"},
             {"configurable": {"thread_id": thread}})

    got: dict = {}

    def read():
        got["values"] = dict(svc._snapshot(thread).values)

    with svc._check.lock:                       # ← 写连接被占着（一次 invoke 的全过程）
        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout=15)
    assert not t.is_alive(), "读卡在写锁后面了 —— 它会一直排到那次 invoke 跑完"
    assert got["values"].get("url") == URL, "读连接看到的不是同一份状态：%r" % got.get("values")

    # 而 /health 那边也得接着说「postgres」（重要 1：建完之后不许翻脸）
    body = TestClient(app).get("/health").json()
    assert body["checkpointer"] == "postgres", body


def test_a_fresh_database_whose_first_request_is_a_read_answers_404_not_500():
    """Minor 3：读连接原先**不 `setup()`** —— 于是一个刚建好的库，如果第一次请求就是读
    （服务起来之后先被人 `GET` 了一下），表还没建 → psycopg 报「relation does not exist」
    → **500**。「读不到」和「坏了」是两件事，500 会把前一句说成后一句。

    这条用一个**全新的库**验：建库 → 只读一次 → 必须是 404。
    """
    import uuid as _uuid
    import psycopg
    from fastapi.testclient import TestClient
    name = "sf_fresh_%s" % _uuid.uuid4().hex[:8]
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute('create database "%s"' % name)
    url = PG_URL.rsplit("/", 1)[0] + "/" + name

    r = TestClient(service.create_app(checkpointer_url=url)).get("/job/nonexistent")
    assert r.status_code == 404, "新库第一次请求就是读 → %s：%s" % (r.status_code, r.text[:200])
