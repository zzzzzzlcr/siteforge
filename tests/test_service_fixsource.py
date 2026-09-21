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


# ── ★ 第 ① 件事：拿**失败记录里那个键**去问，不拿人填的入口网址 ──────────────
#
#: 【我量的·2026-09-21】`formLog` 每一行都带一格 `site`，那是**后端自己存的键**；
#: 而证据里那个 `url` 是「这一趟真从哪儿进去的」（`entry_url`）。**两者不是一回事**：
#: 后端的匹配规则是「存的 site 必须是请求值的 host+path 前缀」——
#: 存的键比入口网址**深**时，拿入口网址去问就是 404（明明有这个站）。
FAIL_KEY = "compareinsulation.io/article-1-c"      # 后端自己存的那个键（深）
ENTRY_URL = "https://compareinsulation.io"         # 入口网址（浅）—— 拿它问必 404


def test_the_backend_read_uses_the_failure_key_not_the_shallow_entry_url(tmp_path):
    """★ 给了失败记录那个键（且网址还是它跟着来的那一串）⇒ `formScript` **拿它**去问。

    这一条钉的是第 ① 件事的正身：**键对了，404 就少一大半**。
    拿入口网址去问的后果不是「报错」，是**静默地少修一个站**
    （`_stage_fix_source` 读不到 ⇒ 门口红掉一整趟本该能修的活）。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path, url=ENTRY_URL, fix_site=FAIL_KEY,
                                     fix_site_url=ENTRY_URL))

    assert r.status_code == 202, r.text
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formScript", path
    assert params["site"] == FAIL_KEY, params


def test_the_failure_key_stops_counting_once_the_url_moved(tmp_path):
    """★ 人把「站点网址」那一格改了 ⇒ 那个键**作废**，照旧拿网址去问。

    为什么这条比「键一直带着」更对：键说的是「**修哪个站**」，网址说的是「**从哪儿进去**」。
    两格对不上时，键继续用就是**静默修错站** —— 面板上开的是另一个站，
    服务拿这条失败记录的键去读配置、去修，中间没有一处会响。
    落地这一格是**服务**判的（页面只是两格原样发上来）——
    所以判据写在这里，不写成「页面自己删了」。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path, url="https://another-funnel.test/quiz",
                                     fix_site=FAIL_KEY, fix_site_url=ENTRY_URL))

    assert r.status_code == 202, r.text
    path, params = query_of(rec.urls[0])
    assert path == "/api/quest/formScript", path
    assert params["site"] == "https://another-funnel.test/quiz", params


def test_a_failure_key_with_no_url_to_ride_on_is_not_used(tmp_path):
    """★ 光有键、没有「它跟着来的那一串网址」⇒ **不算数**（照旧拿 `url` 去问）。

    ⚠️ 少了这一条，「只要给了键就用」这种改法照绿 —— 而那正是上一条要挡的
    「页面把键和网址拆开送」的形状：拆开之后服务手上没有能对账的那一格，
    就只能盲信这个键。所以缺那一格时**宁可不认它**（退回老行为，不猜）。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path, url=ENTRY_URL, fix_site=FAIL_KEY))

    assert r.status_code == 202, r.text
    path, params = query_of(rec.urls[0])
    assert params["site"] == ENTRY_URL, params


def test_the_failure_key_never_becomes_the_output_path(tmp_path):
    """★ 那个键**是带着斜杠的**（`主机/路径`）—— 它**不许**变成落盘的路径。

    拿它拼 `<out_dir>/<键>.before.py` 的后果：产物落到一个**没建过的子目录**里
    （`<out_dir>/compareinsulation.io/article-1-c.before.py`）—— 这一下直接写盘失败。
    就算哪天有人补一个 `mkdir`，产物也就散进子目录里了，而后面几步是按**短名**
    去认那一份底稿的（`graph` 那一侧的 `fix_py` 只认路径，交付那一步认短名）。
    所以：**键归键，路径归路径**（`site` / `url` 推出来的短名照旧，一个字不动）。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path, url=ENTRY_URL, fix_site=FAIL_KEY))

    assert r.status_code == 202, r.text
    staged = tmp_path / "sites" / ("%s.before.py" % SITE)
    assert staged.exists(), "底稿没落在短名那个位置上：%s" % sorted(
        p.name for p in (tmp_path / "sites").glob("*"))
    assert staged.read_text(encoding="utf-8") == SCRIPT_SRC, "底稿被改过了（逐字节！）"
    assert not (tmp_path / "sites" / "compareinsulation.io").exists(), \
        "那个键变成了子目录：%s" % sorted(p.name for p in (tmp_path / "sites").glob("*"))


def test_without_a_failure_key_the_entry_url_is_still_what_we_ask_with(tmp_path):
    """没给那个键 ⇒ **照旧拿 `url` 去问**（老那一趟一个字不变）。

    ⚠️ 这一条与上面那条是**一对**：少了它，「干脆不读 `url` 了、只认新键」
    这种改法照绿 —— 而那会把所有**没走失败列表**的修站活全打回 404。
    """
    rec = Recorder(_script_body())
    app = _client(tmp_path, fmr_client=fmr.FmrClient(token=FAKE_TOKEN, opener=rec))

    r = app.post("/run", json=_brief(tmp_path))

    assert r.status_code == 202, r.text
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
