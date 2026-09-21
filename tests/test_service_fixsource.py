"""B 线 py 支 ②：**修站那条路的底稿从后端来** —— 服务侧。

## 这一份钉的是什么

图里「修站」那条分支（`graph.py:349`）早就写好了：给它一个 `fix_py`（一份旧 py 的**路径**），
它就**不探索**、拿 `fix.py` 把每一格的身份重判一遍，然后照旧走 draft / lint / 自测 / 交付。
`tests/test_fix.py` 那条「不探索」已经钉住了图那一侧。

**缺的是没人给它喂值**：`fix_py` 在 `service.py` / `console.html` 里一次都没出现过，
`_payload` 的 keep 清单里也没有它 —— 于是 `graph.py:346` 那个 `if fix_py:` 恒为假，
那条路「接上了但不响」（`state.py:176` 记的正是这个坑）。

## ★ 底稿从**后端**来，不从本地目录猜

用户 2026-09-21 点破的：后端的 `GET /api/quest/formScript` 就能拿到那个站的脚本源码，
而**后端那份才是生产在跑的那一份**。所以这一层不读 `forms/sites/<短名>.py`
—— 那是本地的另一份，可能早就不是线上那份了。

## ★★ 三种「修不了」都必须**在门口说**，不许静默走全量探索

`mode=fix` 而底稿拿不到时，**最贵的那个后果**是悄悄退化成「从头重新探索那个站」：
开真窗口、跑模型、把已有的证据丢掉重买一次 —— 而它在日志上与修站**长得一样**。
所以三种都在门口挡掉（非 2xx + 人话）：

| 情形 | 为什么不能放它过去 |
|---|---|
| 后端说这是 **JSON 站**（`type: json`） | 拿一份空脚本去修一个好好的 json 站 —— 看起来与「该补脚本」一模一样 |
| 后端**没有这个站**（404） | 同上：那是「查不到」，不是「没有可修的」 |
| 后端读不到（连不上/超时/非 JSON） | 「没量着」—— 去查后端与网络，不是去重新探索 |
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import graph  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import FAKE_TOKEN, Recorder, query_of  # noqa: E402
from test_service_input import StubWindow  # noqa: E402

#: 这个站（后端那个匹配器认的是 **URL 或域名**，不是产物短名 —— 【我量的·2026-09-21】：
#: `callyourdate` 它不认、`callyourdate.com` 与整条 URL 都认，且**大小写敏感**）。
URL = "https://callyourdate.com/land/sp/519015a5"
#: 产物的短名（`graph.site_name(URL)` 推出来的那个）—— 与上面的键**不是一回事**。
SITE = "callyourdate"

#: 【我量的·2026-09-21】后端给的源码。⚠️ **结尾那个换行是承重的**：后端原话
#: 「首尾换行是源码的一部分」，而且写口拿 sha256 当指纹 —— 底稿被 strip 过就等于改坏了源码。
SCRIPT_SRC = "#!/usr/bin/env python3\nSTATES = []\nFILLS = {}\n"


def _script_body(src=SCRIPT_SRC):
    return {"status": 200, "msg": "ok",
            "data": {"site": "callyourdate.com", "requested_site": URL,
                     "type": "py", "version": "20260918",
                     "sha256": "0" * 64, "source": src}}


def _json_body():
    return {"status": 200, "msg": "ok",
            "data": {"site": "callyourdate.com", "requested_site": URL,
                     "type": "json", "version": None, "sha256": None, "source": None}}


def _not_found_body():
    return {"status": 404, "msg": "script not found", "data": []}


def _client(tmp_path, *, fmr_client, **kw):
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(
        service.create_app(graph_factory=lambda brief, deps: None,
                           failures_reader=fmr_client, window=StubWindow(), **kw),
        raise_server_exceptions=False)


def _brief(tmp_path, **over):
    b = {"url": URL, "goal": "", "mode": "fix",
         "evidence": "9-21 10:21 失败 · 美国 · 站点专属",
         "success_text": "Check your email", "site": SITE,
         "out_dir": str(tmp_path / "sites")}
    b.update(over)
    return b


# ── 修站模式：底稿从后端来 ──────────────────────────────────────

def test_fix_mode_stages_the_backend_script_as_the_draft(tmp_path):
    """修站模式 ⇒ 后端那份脚本落成一份底稿（`<out_dir>/<短名>.before.py`）。

    ★ **逐字节**：`read_text()` 拿回来的必须与后端给的**一字不差** ——
    结尾那个换行也算。这一条是下面「写回」那一步的前提（写口拿 sha256 当指纹）。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path))

    assert r.status_code == 202, r.text
    staged = tmp_path / "sites" / ("%s.before.py" % SITE)
    assert staged.exists(), "没落底稿：%s" % sorted(
        p.name for p in (tmp_path / "sites").glob("*")) if (tmp_path / "sites").exists() else []
    assert staged.read_text(encoding="utf-8") == SCRIPT_SRC, "底稿被改过了（逐字节！）"
    #: 走的是那个读口，且带的是 **URL**（后端自己解析成它认的键）。
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formScript", path
    assert params["site"] == URL, params


def test_build_mode_does_not_touch_the_backend_script(tmp_path):
    """**不修站就不去读脚本** —— build 那条路一个字都不许变。"""
    rec = Recorder()
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path, mode="build", goal="把它走通", evidence=""))

    assert r.status_code == 202, r.text
    assert rec.urls == [], "build 模式却去读了后端脚本：%s" % rec.urls
    assert not (tmp_path / "sites" / ("%s.before.py" % SITE)).exists()


# ── 三种「修不了」：门口说，不许静默走全量探索 ──────────────────

def test_a_json_site_is_refused_at_the_door(tmp_path):
    """★ 后端说这是 **JSON 站** ⇒ 门口挡住。

    放它过去的后果：拿一份空脚本去修一个好好的 json 站 —— 而下游看起来
    与「这个站该补一份脚本」**一模一样**。它该走的是配置那条路（`form_config`）。
    """
    app = _client(tmp_path, fmr_client=fmr.FmrClient(
        token=FAKE_TOKEN, opener=Recorder(_json_body())))

    r = app.post("/run", json=_brief(tmp_path))

    assert not (200 <= r.status_code < 300), r.text
    said = r.text
    assert "JSON" in said or "配置" in said, said
    assert not (tmp_path / "sites" / ("%s.before.py" % SITE)).exists(), "还是落了底稿"


def test_a_site_without_a_script_is_refused_not_silently_explored(tmp_path):
    """★ **最贵的那一格**：后端没有这个站 ⇒ 门口挡住。

    放它过去的后果是「悄悄退化成从头重新探索」—— 开真窗口、跑模型、
    把已有的证据丢掉重买一次，而它在日志上与修站**长得一样**。
    所以这一条的重点不是「回了错」，是「**没有变成一个 job**」。
    """
    app = _client(tmp_path, fmr_client=fmr.FmrClient(
        token=FAKE_TOKEN, opener=Recorder(_not_found_body())))

    r = app.post("/run", json=_brief(tmp_path))

    assert not (200 <= r.status_code < 300), r.text
    assert "job_id" not in r.text, "开了 job（那就是放它去探索了）：%s" % r.text


def test_a_backend_that_cannot_be_read_is_refused(tmp_path):
    """后端读不到（超时 / 非 JSON）⇒ 门口挡住 —— 这是「**没量着**」。"""
    app = _client(tmp_path, fmr_client=fmr.FmrClient(
        token=FAKE_TOKEN, opener=Recorder(TimeoutError("timed out"))))

    r = app.post("/run", json=_brief(tmp_path))

    assert not (200 <= r.status_code < 300), r.text


# ── 底稿这条路要**真的接进图**（不是落了个文件就完事）──────────────────

def test_the_payload_carries_fix_py_down_to_the_graph():
    """★ `_payload` 的 keep 清单里必须有 `fix_py`。

    没有它的后果就是本来的病：服务算出来了、也落盘了，可**发不到图里** ——
    `graph.py:346` 那个 `if fix_py:` 恒为假，那条路「接上了但不响」。
    （`state.py:176` 记的正是上一次这么栽的：`invoke({..., "fix_py": …})` 一路无声。）
    """
    got = service.Service._payload({"url": "u", "goal": "g", "fix_py": "/tmp/x.py"})

    assert got["fix_py"] == "/tmp/x.py", got
