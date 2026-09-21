"""Task B3（**不写回的那一半**）：面板上的「看这份 JSON 配置差在哪一格」。

## 这一份钉的是什么

`plan.md` §4 Task B3 的落点是「面板加输入（站点键），闸上摆 diff + 证据」。
控制者 2026-09-21 把**写回**那一半按下了（写接口的语义没验：是「按 site 更新」
还是「每次插一条新的」—— 猜错会在生产库里堆重复配置），所以这里只有前半截：

    读配置（B1）→ 复跑（B2）→ diff（B2）→ 端给页面

## ★ 这一份的重心：**四种「空」必须分得开**

本仓最贵的一次实测就是这个形状（无效 key 回 404，诊断页显示成「近 2 天没有失败 ✓」）。
这一屏上有四个都能被读成「没问题」的状态，**每一个都得自己那句话**：

| 情形 | 给什么 | 不许被读成 |
|---|---|---|
| 配置读回来了、**还没复跑** | `200` + `diff: null` + 一句说清「还没复跑」 | ❌「复跑了，没有差异」 |
| 复跑了、**哪一格都对得上** | `200` + `changes: []` + `unresolved: []` | ❌「还没复跑」 |
| 复跑了、有对不上的 | `200` + 两条清单 | —— |
| **配置读不回来**（后端说不认识这个站） | **非 2xx** | ❌「这个站的配置没问题」 |

⚠️ 第一行与第二行**在数据上都是「没有 changes」** —— 分开它们的**只有那句 `say`**，
所以这个文件里最要紧的两条用例就是钉这两句**不许互相顶替**。

⚠️ 这一份**不打真网络、不开浏览器、不打真模型**（`failures_reader` 与
`json_rerun` 都是注入的口子）。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import FAKE_TOKEN, Recorder, envelope  # noqa: E402
from test_service_input import StubWindow  # noqa: E402

#: 一个真的站点键（真形状：带斜杠带点 —— 所以路由**只能**走查询参数，
#: 走路径参数的话 `/jsondiff/www.gowizard.com/auto-warranty/` 会被切成好几段）。
SITE_KEY = "cvrefresh.com"

#: 一份真配置的形状（2026-09-21 经 B1 的读口取回，实测）。
CONFIG = {
    "form_type": "magic_link",
    "site": SITE_KEY,
    "steps": [
        {"action": "click", "find": {"text": "Refresh my resume"}},
        {"action": "form",
         "field": {"label": "Email", "placeholder": "your@email.com", "type": "email"},
         "value": "{{random.email}}"},
    ],
    "success": {"any": [{"body_contains": ["Check your email"]}]},
}

FACTS_OK = {
    "url": "https://cvrefresh.com/",
    "body_text": "Refresh my resume Check your email",
    "actions": [{"text": "Refresh my resume"}],
    "fields": [{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
}
FACTS_DRIFT = {
    "url": "https://cvrefresh.com/",
    "body_text": "Resume refresh Check your email",
    "actions": [{"text": "Resume refresh"}],
    "fields": [{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
}


def _fmr_stub(*bodies):
    """一个**真的** `FmrClient`，只把传输那一格换成桩（判据代码真跑）。"""
    return fmr.FmrClient(token=FAKE_TOKEN, opener=Recorder(*bodies))


def _client(*, fmr_client, json_ws_url="", json_rerun=None):
    return TestClient(
        service.create_app(failures_reader=fmr_client, window=StubWindow(),
                           json_ws_url=json_ws_url, json_rerun=json_rerun),
        raise_server_exceptions=False)


def _stub_rerun(facts, summary=None, seen=None):
    def run(config, **kw):
        if seen is not None:
            seen["config"] = config
            seen.update(kw)
        return {"summary": summary or {"status": "failed", "failed_step": 1},
                "facts": facts}
    return run


# ── 「还没复跑」与「没有差异」必须分得开 ────────────────────────

def test_还没复跑时_说清是还没复跑而不是没有差异():
    """★ 这一屏最容易骗人的一格。

    `diff: null` 与 `changes: []` 在页面上**都是「没东西可看」** ——
    分开它们的只有那句 `say`。说成「没有差异」就是在告诉运营
    「这份配置是好的」，而**它只是还没被跑过**。
    """
    app = _client(fmr_client=_fmr_stub(envelope({"site": SITE_KEY, "steps": CONFIG})))

    r = app.get("/jsondiff", params={"site": SITE_KEY})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["diff"] is None, "还没复跑，却给了一份 diff：%s" % body["diff"]
    assert "复跑" in body["say"], body["say"]
    assert "没有差异" not in body["say"], "把「还没跑」说成了「没有差异」：%s" % body["say"]
    # 配置本身要端出去（面板要摆「现在是什么样」）
    assert body["config"]["form_type"] == "magic_link"
    assert len(body["config"]["steps"]) == 2
    # 页面上「复跑」那个按钮该不该可按，由这一格说了算
    assert body["can_rerun"] is False


def test_复跑了且哪一格都对得上_是另一句话():
    """与上一条**成对**：这里 `changes` 也是空的，但 `say` 不许是「还没复跑」。"""
    app = _client(
        fmr_client=_fmr_stub(envelope({"site": SITE_KEY, "steps": CONFIG})),
        json_ws_url="ws://127.0.0.1:9222/devtools/page/X",
        json_rerun=_stub_rerun(FACTS_OK, summary={"status": "success"}))

    body = app.get("/jsondiff", params={"site": SITE_KEY}).json()

    assert body["diff"] is not None
    assert body["diff"]["changes"] == []
    assert body["diff"]["unresolved"] == []
    assert "复跑" in body["say"] and "没有差异" not in body["say"], body["say"]
    assert body["can_rerun"] is True


def test_复跑了且对不上_摆出那两条清单():
    app = _client(
        fmr_client=_fmr_stub(envelope({"site": SITE_KEY, "steps": CONFIG})),
        json_ws_url="ws://127.0.0.1:9222/x",
        json_rerun=_stub_rerun(FACTS_DRIFT, summary={"status": "failed", "failed_step": 1}))

    body = app.get("/jsondiff", params={"site": SITE_KEY}).json()

    assert body["diff"]["changes"] == []
    assert len(body["diff"]["unresolved"]) == 1, body["diff"]["unresolved"]
    assert body["diff"]["unresolved"][0]["path"] == "steps[0].find.text"
    # ★ 复跑自己的结论也要端出去（「失败在第几步」是执行器量的，不是这一层猜的）
    assert body["summary"]["failed_step"] == 1


# ── 配置读不回来：非 2xx ────────────────────────────────────────

def test_这个站没有配置_非2xx而不是一份空diff():
    """后端原话是 `status:404 / config not found`（HTTP 仍是 200）。

    ⚠️ 这一格**绝不能**变成「没东西可看」：那会被读成「这个站的配置没问题」，
    而事实是**这个站它不认识 / 没有配置**。与 `/rank` / `/diag` 同一个形状。
    """
    app = _client(fmr_client=_fmr_stub(
        envelope([], status=404, msg="config not found")))

    r = app.get("/jsondiff", params={"site": SITE_KEY})

    assert not (200 <= r.status_code < 300), r.text
    assert r.status_code in (404, 502, 503), r.status_code


def test_没说是哪个站_是400而不是502():
    """★ **400 与 502 不是一回事**，而这一条**差点写成 502**。

    · 没给站点键 = **调用方自己的毛病**（一个请求都没发出去）⇒ `400`；
    · 量不到（连不上 / 后端回码 / 没配 token）⇒ `502` / `503`。

    把前者写成后者，运维会去查后端与网络 —— 而他真正该做的是**把那一格填上**。
    这与 `failures()` 门口那道闸同一个码（`service.py` 那个 400）。
    """
    app = _client(fmr_client=_fmr_stub(envelope({"site": SITE_KEY, "steps": CONFIG})))

    r = app.get("/jsondiff", params={"site": "   "})

    assert r.status_code == 400, r.text
    assert "免费" in r.text or "哪个站" in r.text, r.text


def test_复跑没量着_非2xx而不是一份空diff():
    """复跑挂了（窗口没了 / 执行器 import 失败）—— 这是「**没量着**」。

    绝不能回 `diff: null` + 一句「还没复跑」：那与**真的还没跑**长得一样，
    而两件事的处置完全不同（一个去开窗口，一个去点按钮）。
    """
    from agent import jsondiag

    def boom(config, **kw):
        raise jsondiag.RerunUnmeasured("复跑超时（300 秒）—— 页面卡住了")

    app = _client(
        fmr_client=_fmr_stub(envelope({"site": SITE_KEY, "steps": CONFIG})),
        json_ws_url="ws://127.0.0.1:9222/x", json_rerun=boom)

    r = app.get("/jsondiff", params={"site": SITE_KEY})

    assert not (200 <= r.status_code < 300), r.text
    assert "300" in r.text or "卡住" in r.text, r.text
