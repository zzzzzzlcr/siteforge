# 约会站自动分类与关联 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给一个约会站 URL，只读普查并判定它属于哪份脚本族（japansdates / goldenagesouls / delachat），解析出应挂的 config，并在 `--apply` 时通过 `POST /api/quest/formConfig/link` 完成关联。

**Architecture:** 新增 `agent/classify.py`：一段 `CENSUS_JS` 收集 DOM 信号 → 纯函数 `classify_census()` 对照 `FINGERPRINTS` 表 → `resolve_config()` 用锚点站反查 `config_id` → 可选 `form_config_link()` 写入并回读。窗口/导航复用 `BitWindow.fresh_open()` 与 `selftest._navigate/_cdp_eval`；分类逻辑不碰网络，可单测。

**Tech Stack:** Python 3.10、现有 `agent.fmr` / `agent.service` / `agent.selftest`、`cdp` 二进制、pytest。

**Spec:** `docs/superpowers/specs/2026-10-22-dating-site-classifier-design.md`

## Global Constraints

- 只覆盖三族：`japansdates` / `goldenagesouls` / `delachat`；其余一律 `unknown`，不猜。
- 默认**只读**：不填表、不提交；`--apply` 才写，且只在「恰好一个平台命中 + config 解析成功」时写。
- 最多**一次** Start 点击；只点可见的 start 类按钮；永不提交。
- `unknown` / `ambiguous` 绝不写；`formConfig/link` 只做站点↔配置关联，不写脚本、不建 config。
- 站点键归一化：去协议 / `?` `#` 及其后 / 末尾 `/`（与后端同一套）。
- 真站测试只在 **8100（Bit 192.168.1.222）**；**不碰 8099**。
- 写 token 只从环境读，绝不打印、绝不进 URL。
- 每个 Task 结束提交，`git add` 只加本 Task 的文件（工作区有其它未提交改动）。

## Review Focus

- **慢加载 / 未 hydration 的页**：census 可能只看到营销页 → 必须报 `unknown` 或点一次 Start 后再判，**不能**因为没看到标记就猜一个平台。
- **多平台标记同时出现**：必须 `ambiguous` 且不写；测试要钉住。
- **URL 带 query / fragment / 尾斜杠**：`site_key` 必须归一化，写后回读用归一化形式比对，不能拿原文比。
- **`/api/quest/*` 的 HTTP 200 + body.status 404/400**：绝不能读成成功；测试要钉住。
- **Start 点击误触提交**：只点 start 类文字、最多一次、不碰 form submit；点了要如实写进输出。

---

### Task 1: FMR `form_config_link` 客户端

**Files:**
- Modify: `agent/fmr.py`（在 `FORM_SCRIPT_UPDATE_PATH` 附近加常量；在 `update_form_script` 之后加方法）
- Test: `tests/test_fmr_link.py`（新文件；`from test_fmr import FAKE_TOKEN, PostRecorder, writer`）

**Interfaces:**
- Consumes: `FmrClient.__init__(token=..., write_token=..., poster=...)`；`PostRecorder` / `writer`（`tests/test_fmr.py`）。
- Produces: `FmrClient.form_config_link(site: str, config_id: int, *, operator: str, reason: str = "") -> dict`，返回 `{"site","config_id","created"}`；非 200 抛 `FmrRefused`；没写 token 抛 `FmrNoToken`；本地参数错抛 `FmrUnmeasured`。

- [ ] **Step 1: Write the failing tests**

创建 `tests/test_fmr_link.py`：

```python
"""POST /api/quest/formConfig/link 的客户端契约（2026-10-22 后端 df4938d）。"""
from __future__ import annotations

import json

import pytest

from agent import fmr
from test_fmr import FAKE_TOKEN, PostRecorder

WRITE = "WRITE-TOKEN-LINK-1"


def linker(rec):
    """写口要的是 write_token —— 与读 token 分开传。"""
    return fmr.FmrClient(token=FAKE_TOKEN, write_token=WRITE, poster=rec)


def test_link_posts_json_to_the_link_route_with_the_write_token():
    rec = PostRecorder({"status": 200, "msg": "success",
                        "data": {"site": "a.test/x", "config_id": 101, "created": True}})
    got = linker(rec).form_config_link(
        "a.test/x", 101, operator="claude (classify)", reason="testid: step-content-item-")
    assert got == {"site": "a.test/x", "config_id": 101, "created": True}
    assert rec.urls[0] == "https://fmr.3tkj.cn/api/quest/formConfig/link", rec.urls[0]
    assert rec.headers[0].get("X-Api-Token") == WRITE, rec.headers[0]
    sent = json.loads(rec.sent[0].decode("utf-8"))
    assert sent == {"site": "a.test/x", "config_id": 101,
                    "operator": "claude (classify)",
                    "reason": "testid: step-content-item-"}, sent


def test_link_reads_body_status_404_instead_of_treating_http_200_as_success():
    rec = PostRecorder({"status": 404, "msg": "config not found: 999"})
    with pytest.raises(fmr.FmrRefused) as got:
        linker(rec).form_config_link("a.test/x", 999, operator="who")
    assert got.value.status == 404


def test_link_refuses_locally_without_operator_or_config_id():
    rec = PostRecorder({"status": 200, "msg": "success", "data": {}})
    with pytest.raises(fmr.FmrUnmeasured):
        linker(rec).form_config_link("a.test/x", 101, operator="")
    with pytest.raises(fmr.FmrUnmeasured):
        linker(rec).form_config_link("a.test/x", 0, operator="who")
    assert rec.sent == [], "本地能查出来的错不该发请求"


def test_link_without_write_token_sends_nothing():
    rec = PostRecorder({"status": 200, "msg": "success", "data": {}})
    with pytest.raises(fmr.FmrNoToken):
        fmr.FmrClient(token=FAKE_TOKEN, write_token="", poster=rec).form_config_link(
            "a.test/x", 101, operator="who")
    assert rec.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fmr_link.py -q`
Expected: FAIL with `AttributeError: 'FmrClient' object has no attribute 'form_config_link'`.

- [ ] **Step 3: Implement minimal code**

在 `agent/fmr.py` 的 `FORM_SCRIPT_UPDATE_PATH` 附近加：

```python
#: ★ 2026-10-22（后端 `df4938d`）：只做站点↔配置关联，不写脚本、不建 config。
#: 文档 `/company/farmer/docs/agent-data-api.md` §18.4。
#: 鉴权同写口：`X-Api-Token: <configs.agentWriteToken>`；body 必须是 application/json。
FORM_CONFIG_LINK_PATH = "/api/quest/formConfig/link"
```

在 `update_form_script` 之后加：

```python
    def form_config_link(self, site: str, config_id: int, *, operator: str,
                         reason: str = "") -> dict:
        """把站点挂到**已有** config → `{"site","config_id","created"}`。

        成功判据 = body 里 `status == 200`（`/api/quest/*` 是旧约定：HTTP 恒 200）。
        400 = 参数/形状错；404 = config 不存在；401 = 写 token 错 —— 都抛 `FmrRefused`。
        """
        key = str(site or "").strip()
        if not key:
            raise FmrUnmeasured("没说是哪个站：一个请求都没发出去。", kind="no-site")
        who = str(operator or "").strip()
        if not who:
            raise FmrUnmeasured("没说 operator（谁改的）：一个请求都没发出去。",
                                kind="no-operator")
        try:
            cid = int(config_id)
        except (TypeError, ValueError):
            raise FmrUnmeasured("config_id 必须是正整数：一个请求都没发出去。",
                                kind="bad-config-id")
        if cid <= 0:
            raise FmrUnmeasured("config_id 必须是正整数：一个请求都没发出去。",
                                kind="bad-config-id")
        if not self.write_token:
            raise FmrNoToken(
                "**一个字都没写**：这个部署没配写用的 token（`%s`）。" % WRITE_TOKEN_ENV)
        url = "%s%s" % (self.base, FORM_CONFIG_LINK_PATH)
        headers = {"Content-Type": "application/json; charset=utf-8",
                   "X-Api-Token": self.write_token}
        body_in = {"site": key, "config_id": cid, "operator": who,
                   "reason": str(reason or "")}
        body = json.dumps(body_in, ensure_ascii=False).encode("utf-8")
        raw = self._poster(url, headers, body)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise FmrUnreachable("关联的回执读不成 JSON：%s" % str(exc)[:120]) from exc
        if not isinstance(payload, dict):
            raise FmrUnreachable("关联的回执不是对象：%r" % (payload,))
        status = int(payload.get("status") or 0)
        if status != 200:
            raise FmrRefused("关联没成（body.status=%s）：%s"
                             % (status, str(payload.get("msg") or "")[:200]),
                             status=status)
        data = payload.get("data") or {}
        return {"site": str(data.get("site") or key),
                "config_id": int(data.get("config_id") or cid),
                "created": bool(data.get("created"))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fmr_link.py tests/test_fmr.py -q`
Expected: 新 4 条 + 原 `test_fmr.py` 全绿。

- [ ] **Step 5: Commit**

```bash
git add agent/fmr.py tests/test_fmr_link.py
git commit -m "feat: FMR formConfig/link client (site↔config association)"
```

---

### Task 2: `site_key` + 指纹表 + 纯分类器

**Files:**
- Create: `agent/classify.py`
- Create/Modify: `tests/test_classify.py`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces:
  - `site_key(url: str) -> str`
  - `FINGERPRINTS: list[dict]`
  - `classify_census(census: dict) -> dict`（`platform` / `confidence` / `matches` / `ambiguous`）
  - `census_signature(census: dict) -> str`

- [ ] **Step 1: Write the failing tests**

创建 `tests/test_classify.py`：

```python
"""约会站分类器的纯逻辑（不碰窗口/网络）。"""
from __future__ import annotations

from agent import classify


def _census(**kw):
    base = {"url": "https://x.test/", "title": "", "hash": "",
            "testids": [], "classes": [], "buttons": [], "links": [],
            "inputs": [], "body_text": "", "start_candidates": []}
    base.update(kw)
    return base


def test_japansdates_is_recognised_by_step_markers_and_base64_hash():
    got = classify.classify_census(_census(
        testids=["step-content-item-Basic", "step-content-item-ChooseAge",
                 "option-have-fun", "legal-checkbox", "continue-button"],
        hash="#c3RlcDE="))
    assert got["platform"] == "japansdates"
    assert got["confidence"] == "high"
    assert got["ambiguous"] is False
    assert any("step-content-item-" in e for e in got["matches"][0]["evidence"])


def test_goldenagesouls_is_recognised_by_age_and_image_buttons():
    got = classify.classify_census(_census(
        testids=["continue-button", "image-button-basic", "age-18-24"]))
    assert got["platform"] == "goldenagesouls"
    assert got["confidence"] == "high"


def test_delachat_is_recognised_by_class_tokens():
    got = classify.classify_census(_census(
        classes=["terms__button", "step_active"],
        testids=["continue-button"]))
    assert got["platform"] == "delachat"
    assert got["confidence"] == "high"


def test_a_plain_continue_button_is_not_enough():
    got = classify.classify_census(_census(testids=["continue-button"]))
    assert got["platform"] is None
    assert got["confidence"] == "unknown"


def test_two_matching_platforms_is_ambiguous():
    #: delachat 与 goldenagesouls 的标记**不互斥**（delachat 只否 step-content-item-）。
    got = classify.classify_census(_census(
        classes=["terms__button", "step_active"],
        testids=["continue-button", "image-button-basic"]))
    assert got["ambiguous"] is True
    assert got["platform"] is None
    assert {m["platform"] for m in got["matches"]} >= {"delachat", "goldenagesouls"}


def test_none_of_marker_vetoes_japansdates():
    got = classify.classify_census(_census(
        testids=["step-content-item-Basic", "option-have-fun", "legal-checkbox",
                 "continue-button", "image-button-basic"],
        hash="#c3RlcDE="))
    assert all(m["platform"] != "japansdates" for m in got["matches"])


def test_site_key_strips_scheme_query_fragment_and_trailing_slash():
    assert classify.site_key(
        "https://soulmatemeets.com/land/sp/23d247da/?id_visit_prev=1#c3RlcDE=") \
        == "soulmatemeets.com/land/sp/23d247da"
    assert classify.site_key("http://a.test/x/") == "a.test/x"
    assert classify.site_key("a.test/x?q=1") == "a.test/x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.classify'`.

- [ ] **Step 3: Implement minimal code**

创建 `agent/classify.py`（本 Task 只放这些；窗口/CLI 在 Task 4/5 加）：

```python
"""约会站分类器：只读 DOM 指纹 → 平台 → 建议 config；--apply 时调 formConfig/link。

设计见 docs/superpowers/specs/2026-10-22-dating-site-classifier-design.md。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys

#: 穿透 shadow root 的收集器（与 agent/template_loop.py 的 DEEP_JS 同款）。
DEEP_JS = ("function deepAll(sel){var out=[];"
           "function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});"
           "var all=root.querySelectorAll('*');"
           "for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}"
           "walk(document);return out;}")

CENSUS_JS = r"""(function(){""" + DEEP_JS + r"""
  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}
  function txt(e){return ((e.textContent||'').replace(/\s+/g,' ').trim());}
  function uniq(a){var o=[],s={};for(var i=0;i<a.length;i++){var k=a[i];if(k&&!s[k]){s[k]=1;o.push(k);}}return o;}
  var testids=[];
  deepAll('[data-testid]').forEach(function(e){
    if(!vis(e))return; var t=e.getAttribute('data-testid'); if(t)testids.push(t);});
  var classes=[];
  deepAll('*').forEach(function(e){
    if(!vis(e))return;
    String(e.className||'').split(/\s+/).forEach(function(c){
      if(c && c.length>=3 && c.indexOf('css-')!==0 && c.indexOf('Mui')!==0)classes.push(c);});});
  var buttons=[], links=[];
  deepAll('button,[role=button]').forEach(function(e){
    if(!vis(e))return; var s=txt(e); if(s)buttons.push(s.slice(0,80));});
  deepAll('a[href]').forEach(function(e){
    if(!vis(e))return; var s=txt(e); if(s)links.push(s.slice(0,80));});
  var inputs=[];
  deepAll('input,select,textarea').forEach(function(e){
    if(!vis(e))return;
    inputs.push({type:String(e.getAttribute('type')||e.tagName||''),
                 name:String(e.getAttribute('name')||''),
                 placeholder:String(e.getAttribute('placeholder')||''),
                 testid:String(e.getAttribute('data-testid')||'')});});
  var START=/^(start|begin|continue|join|see if|qualify|get started|take the quiz|find out|check)\b/i;
  var starts=[];
  deepAll('button,[role=button],a[href]').forEach(function(e){
    if(!vis(e))return; var s=txt(e); if(!s||!START.test(s))return;
    if(e.tagName==='A'&&!e.getAttribute('href'))return;
    starts.push({text:s.slice(0,80), tag:String(e.tagName||'').toLowerCase()});});
  return JSON.stringify({
    url:location.href, title:document.title||'', hash:location.hash||'',
    testids:uniq(testids).slice(0,300), classes:uniq(classes).slice(0,200),
    buttons:uniq(buttons).slice(0,80), links:uniq(links).slice(0,80),
    inputs:inputs.slice(0,80),
    body_text:((document.body||{}).innerText||'').replace(/\s+/g,' ').slice(0,500),
    start_candidates:starts.slice(0,5)});})()"""

#: 每族：all_of 全中 + any_of 至少 any_min + none_of 全不中。
FINGERPRINTS = [
    {"platform": "japansdates", "anchor": "japansdates.com",
     "all_of": [{"kind": "testid_prefix", "value": "step-content-item-"}],
     "any_of": [{"kind": "testid_prefix", "value": "option-"},
                {"kind": "testid_exact", "value": "legal-checkbox"},
                {"kind": "testid_exact", "value": "continue-button"},
                {"kind": "hash_b64_prefix", "value": "step"}],
     "any_min": 2,
     "none_of": [{"kind": "testid_prefix", "value": "image-button-"}]},
    {"platform": "goldenagesouls", "anchor": "goldenagesouls.com",
     "all_of": [{"kind": "testid_exact", "value": "continue-button"}],
     "any_of": [{"kind": "testid_prefix", "value": "image-button-"},
                {"kind": "testid_prefix", "value": "age-"}],
     "any_min": 1,
     "none_of": []},
    {"platform": "delachat", "anchor": "chattyromance.com/land/sp/81d0a481",
     "all_of": [{"kind": "class_token", "value": "terms__button"}],
     "any_of": [{"kind": "class_token", "value": "step_active"},
                {"kind": "class_token", "value": "terms__checkbox"},
                {"kind": "testid_exact", "value": "continue-button"}],
     "any_min": 1,
     "none_of": [{"kind": "testid_prefix", "value": "step-content-item-"}]},
]


def _hash_b64(census: dict) -> str:
    raw = str(census.get("hash") or "").lstrip("#")
    if not raw:
        return ""
    try:
        pad = "=" * (-len(raw) % 4)
        return base64.b64decode(raw + pad).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 —— 解不出就不猜
        return ""


def _marker_hit(marker: dict, census: dict) -> bool:
    kind = str(marker.get("kind") or "")
    value = str(marker.get("value") or "")
    if kind == "testid_prefix":
        return any(str(t).startswith(value) for t in (census.get("testids") or []))
    if kind == "testid_exact":
        return value in (census.get("testids") or [])
    if kind == "class_token":
        return value in (census.get("classes") or [])
    if kind == "hash_b64_prefix":
        return _hash_b64(census).startswith(value)
    if kind == "text_contains":
        return value.lower() in str(census.get("body_text") or "").lower()
    return False


def classify_census(census: dict) -> dict:
    """纯函数：census → {platform, confidence, matches, ambiguous}。"""
    matches = []
    for fp in FINGERPRINTS:
        ev = []
        if not all(_marker_hit(m, census) for m in fp["all_of"]):
            continue
        if any(_marker_hit(m, census) for m in fp["none_of"]):
            continue
        any_hits = [m for m in fp["any_of"] if _marker_hit(m, census)]
        if len(any_hits) < int(fp["any_min"]):
            continue
        ev = ["%s: %s" % (m["kind"], m["value"]) for m in fp["all_of"] + any_hits]
        matches.append({"platform": fp["platform"], "score": len(ev), "evidence": ev})
    matches.sort(key=lambda m: -m["score"])
    if len(matches) > 1:
        return {"platform": None, "confidence": "low", "matches": matches,
                "ambiguous": True}
    if matches:
        return {"platform": matches[0]["platform"], "confidence": "high",
                "matches": matches, "ambiguous": False}
    return {"platform": None, "confidence": "unknown", "matches": [],
            "ambiguous": False}


def site_key(url: str) -> str:
    """URL → FMR 站点键：去协议 / `?` `#` 及其后 / 末尾 `/`。"""
    raw = str(url or "").strip()
    raw = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", raw)
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return raw


def census_signature(census: dict) -> str:
    """「页面变了没有」的便宜签名（Start 点击后用）。"""
    testids = sorted(str(t) for t in (census.get("testids") or []))
    blob = "|".join([str(census.get("url") or ""), str(census.get("title") or ""),
                     hashlib.sha1("\n".join(testids).encode("utf-8")).hexdigest()])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: 7 passed.

- [ ] **Step 5: Verify the census JS parses**

Run:

```bash
.venv/bin/python - <<'PY'
import pathlib, subprocess, tempfile
from agent import classify
f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
f.write(classify.CENSUS_JS); f.close()
r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
print("rc", r.returncode, r.stderr[:300])
raise SystemExit(r.returncode)
PY
```

Expected: `rc 0`.

- [ ] **Step 6: Commit**

```bash
git add agent/classify.py tests/test_classify.py
git commit -m "feat: dating-site census fingerprints and pure classifier"
```

---

### Task 3: 锚点 → config 解析

**Files:**
- Modify: `agent/classify.py`
- Modify: `tests/test_classify.py`

**Interfaces:**
- Consumes: `FINGERPRINTS`（Task 2）、`form_config_sites()` 返回的 `[{"site","config_id","status"}]`。
- Produces: `resolve_config(platform: str, site_rows: list[dict], *, override: int | None = None) -> int`；失败抛 `ClassifyError`（本模块自定义）。

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_classify.py`：

```python
import pytest


def test_resolve_config_uses_the_platform_anchor():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1},
            {"site": "goldenagesouls.com", "config_id": 66, "status": 1}]
    assert classify.resolve_config("japansdates", rows) == 101
    assert classify.resolve_config("goldenagesouls", rows) == 66


def test_resolve_config_override_wins_and_must_be_positive():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1}]
    assert classify.resolve_config("japansdates", rows, override=55) == 55
    with pytest.raises(classify.ClassifyError):
        classify.resolve_config("japansdates", rows, override=0)


def test_resolve_config_refuses_a_missing_anchor():
    with pytest.raises(classify.ClassifyError) as got:
        classify.resolve_config("japansdates", [])
    assert "锚点" in str(got.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_classify.py -k resolve_config -q`
Expected: FAIL with `AttributeError: module 'agent.classify' has no attribute 'resolve_config'`.

- [ ] **Step 3: Implement minimal code**

追加到 `agent/classify.py`：

```python
class ClassifyError(RuntimeError):
    """分类/解析失败（CLI 按错误类型给退出码）。"""


def resolve_config(platform: str, site_rows: list[dict], *,
                   override: int | None = None) -> int:
    """平台 → config_id：`--config-id` 优先；否则用锚点站在站点清单里精确匹配。"""
    if override is not None:
        try:
            cid = int(override)
        except (TypeError, ValueError):
            raise ClassifyError("config_id 必须是正整数：%r" % (override,))
        if cid <= 0:
            raise ClassifyError("config_id 必须是正整数：%r" % (override,))
        return cid
    fp = next((f for f in FINGERPRINTS if f["platform"] == platform), None)
    if fp is None:
        raise ClassifyError("不认识的平台：%r" % (platform,))
    anchor = fp["anchor"]
    hits = [r for r in (site_rows or []) if str(r.get("site") or "") == anchor]
    if len(hits) != 1:
        raise ClassifyError(
            "锚点站 %r 在 formConfig/sites 里找了 %d 条（要恰好 1 条）—— 不猜 config。"
            % (anchor, len(hits)))
    try:
        return int(hits[0]["config_id"])
    except (KeyError, TypeError, ValueError):
        raise ClassifyError("锚点站 %r 那一条没有可用的 config_id。" % (anchor,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/classify.py tests/test_classify.py
git commit -m "feat: resolve dating config via platform anchor site"
```

---

### Task 4: Census 运行时 + 一次 Start 点击

**Files:**
- Modify: `agent/classify.py`
- Modify: `tests/test_classify.py`

**Interfaces:**
- Consumes: `CENSUS_JS`（Task 2）、`classify_census`（Task 2）、`census_signature`（Task 2）。
- Produces:
  - `census_with_start(eval_fn, click_fn, *, allow_click=True, wait_seconds=5.0, poll=0.5) -> dict`
    （返回 `{"census": dict, "clicked_start": bool, "clicked_text": str, "note": str}`）
  - 真实窗口/导航/CDP 的接线在 Task 5 的 `main()` 里（本 Task 只做可单测的部分）。

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_classify.py`：

```python
def _eval_from(pages):
    """按顺序返回 census 的假 eval：pages 是 dict 列表。
    `MARK_START_JS` 那一下（带 `data-sf-start`）固定回 "marked"，不占 pages。"""
    seq = list(pages)

    def _call(js):
        if "data-sf-start" in js:
            return "marked"
        if not seq:
            raise AssertionError("eval 调多了")
        return json.dumps(seq.pop(0))
    return _call


def test_census_with_start_does_not_click_when_the_first_screen_matches():
    page = _census(testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                   hash="#c3RlcDE=")
    clicked = []
    got = classify.census_with_start(_eval_from([page]), lambda: clicked.append(1))
    assert got["clicked_start"] is False
    assert clicked == []


def test_census_with_start_clicks_once_when_unknown_and_returns_the_new_census():
    first = _census(buttons=["Start"], start_candidates=[{"text": "Start", "tag": "button"}])
    second = _census(testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                     hash="#c3RlcDE=")
    clicked = []
    got = classify.census_with_start(_eval_from([first, second]),
                                     lambda: clicked.append(1))
    assert got["clicked_start"] is True
    assert got["clicked_text"] == "Start"
    assert clicked == [1]
    assert got["census"] == second


def test_census_with_start_does_not_click_with_no_candidate_or_no_click_flag():
    page = _census(buttons=["Start"], start_candidates=[])
    got = classify.census_with_start(_eval_from([page]), lambda: None)
    assert got["clicked_start"] is False

    page2 = _census(start_candidates=[{"text": "Start", "tag": "button"}])
    clicked = []
    got2 = classify.census_with_start(_eval_from([page2]), lambda: clicked.append(1),
                                      allow_click=False)
    assert got2["clicked_start"] is False and clicked == []


def test_census_with_start_reports_when_the_click_does_not_change_the_page():
    page = _census(start_candidates=[{"text": "Start", "tag": "button"}])
    got = classify.census_with_start(_eval_from([page, page]),
                                     lambda: None, wait_seconds=0.0)
    assert got["clicked_start"] is True
    assert "没变" in got["note"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_classify.py -k census_with_start -q`
Expected: FAIL with `AttributeError: ... census_with_start`.

- [ ] **Step 3: Implement minimal code**

追加到 `agent/classify.py`：

> 在文件顶部 import 区加 `import time`（Task 2 那份还没用到它）。

```python
START_SELECTOR = '[data-sf-start="1"]'

#: 给可见的 start 候选打标记（只打第一个；点还是走 `cdp click` 的真人手势层）。
MARK_START_JS = r"""(function(){""" + DEEP_JS + r"""
  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}
  function txt(e){return ((e.textContent||'').replace(/\s+/g,' ').trim());}
  var START=/^(start|begin|continue|join|see if|qualify|get started|take the quiz|find out|check)\b/i;
  var old=deepAll('[data-sf-start]');for(var j=0;j<old.length;j++)old[j].removeAttribute('data-sf-start');
  var es=deepAll('button,[role=button],a[href]');
  for(var i=0;i<es.length;i++){var e=es[i];
    if(!vis(e))continue; var s=txt(e); if(!s||!START.test(s))continue;
    e.setAttribute('data-sf-start','1'); return 'marked';}
  return 'none';})()"""


def _as_census(raw) -> dict:
    """`selftest._cdp_eval` 可能已经拆成 dict，也可能还是 JSON 文本 —— 两种都收。"""
    return raw if isinstance(raw, dict) else json.loads(raw)


def census_with_start(eval_fn, click_fn, *, allow_click: bool = True,
                      wait_seconds: float = 5.0, poll: float = 0.5) -> dict:
    """第一轮 census；`unknown` 且允许 → 点**一次** Start → 第二轮 census。

    `eval_fn(js) -> dict | JSON 文本`、`click_fn()` 只负责点（点了哪个字从 census 里读）。
    点了但页面没变（签名不变）⇒ `clicked_start=True` 且 note 明说「没变」。
    """
    first = _as_census(eval_fn(CENSUS_JS))
    verdict = classify_census(first)
    if verdict["platform"] or not allow_click:
        return {"census": first, "clicked_start": False, "clicked_text": "", "note": ""}
    if not (first.get("start_candidates") or []):
        return {"census": first, "clicked_start": False, "clicked_text": "",
                "note": "没找到可点的开始"}
    clicked_text = str((first.get("start_candidates") or [{}])[0].get("text") or "")
    marked = str(eval_fn(MARK_START_JS) or "")
    if "marked" not in marked:
        return {"census": first, "clicked_start": False, "clicked_text": "",
                "note": "没找到可点的开始"}
    click_fn()
    before = census_signature(first)
    deadline = time.time() + max(0.0, float(wait_seconds))
    second = first
    while True:
        time.sleep(min(poll, max(0.0, deadline - time.time())))
        second = _as_census(eval_fn(CENSUS_JS))
        if census_signature(second) != before:
            return {"census": second, "clicked_start": True, "clicked_text": clicked_text,
                    "note": ""}
        if time.time() >= deadline:
            return {"census": second, "clicked_start": True, "clicked_text": clicked_text,
                    "note": "点了 %r，页面没变" % (clicked_text or "?")}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/classify.py tests/test_classify.py
git commit -m "feat: census runtime with one optional Start click"
```

---

### Task 5: CLI（只读 + `--apply` + 回读）

**Files:**
- Modify: `agent/classify.py`
- Modify: `tests/test_classify.py`

**Interfaces:**
- Consumes: Tasks 1–4；`FmrClient.form_config_sites()` / `form_config_link()`；`site_key()`。
- Produces:
  - `run_classify(url, *, census_fn, site_rows, client=None, apply=False, config_override=None) -> dict`
    （返回 `{"exit": int, "result": dict}`；不打印）
  - `main(argv=None) -> int`（argparse + 开窗 + 打印）

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_classify.py`：

```python
class _FakeClient:
    def __init__(self, rows, link_result=None, update_rows=True):
        self.rows = rows
        self.link_result = link_result or {"site": "x", "config_id": 101, "created": True}
        self.update_rows = update_rows
        self.linked = []

    def form_config_sites(self):
        return self.rows

    def form_config_link(self, site, config_id, *, operator, reason=""):
        self.linked.append((site, config_id, operator, reason))
        if isinstance(self.link_result, Exception):
            raise self.link_result
        if self.update_rows:
            #: 真后端是加性 upsert —— 回读能看见这一行。
            self.rows = [r for r in self.rows if r.get("site") != site] + [
                {"site": site, "config_id": config_id, "status": 1}]
        return self.link_result


JAPAN = _census(url="https://soulmatemeets.com/land/sp/23d247da/",
                testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                hash="#c3RlcDE=")
#: delachat + goldenagesouls 的标记**不互斥**，用来造 ambiguous。
AMBIGUOUS = _census(classes=["terms__button", "step_active"],
                    testids=["continue-button", "image-button-x"])
ROWS = [{"site": "japansdates.com", "config_id": 101, "status": 1}]


def test_run_classify_read_only_does_not_link():
    client = _FakeClient(ROWS)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=ROWS, client=client)
    assert got["exit"] == 0
    assert got["result"]["platform"] == "japansdates"
    assert got["result"]["config_id"] == 101
    assert got["result"]["site_key"] == "soulmatemeets.com/land/sp/23d247da"
    assert client.linked == []


def test_run_classify_unknown_or_ambiguous_exits_2_and_never_links():
    client = _FakeClient(ROWS)
    for c in (_census(), AMBIGUOUS):
        got = classify.run_classify("https://x.test/", census_fn=lambda c=c: {
            "census": c, "clicked_start": False, "clicked_text": "", "note": ""},
            site_rows=ROWS, client=client, apply=True)
        assert got["exit"] == 2, got
    assert client.linked == []


def test_run_classify_apply_links_then_reads_back():
    client = _FakeClient(ROWS)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=ROWS, client=client, apply=True)
    assert got["exit"] == 0, got
    assert client.linked and client.linked[0][0] == "soulmatemeets.com/land/sp/23d247da"
    assert client.linked[0][1] == 101
    assert got["result"]["linked"] == {"created": True, "config_id": 101}


def test_run_classify_apply_refuses_when_readback_does_not_match():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1},
            {"site": "soulmatemeets.com/land/sp/23d247da", "config_id": 66, "status": 1}]
    #: 写成功但回读仍是 66 —— 不许报成功。
    client = _FakeClient(rows, update_rows=False)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=rows, client=client, apply=True)
    assert got["exit"] == 4, got


def test_run_classify_missing_anchor_exits_5():
    client = _FakeClient([])
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=[], client=client, apply=True)
    assert got["exit"] == 5, got
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_classify.py -k run_classify -q`
Expected: FAIL with `AttributeError: ... run_classify`.

- [ ] **Step 3: Implement minimal code**

追加到 `agent/classify.py`：

```python
EXIT_OK = 0
EXIT_UNKNOWN = 2
EXIT_WINDOW = 3
EXIT_LINK = 4
EXIT_CONFIG = 5


def run_classify(url: str, *, census_fn, site_rows, client=None,
                 apply: bool = False, config_override: int | None = None) -> dict:
    """一次分类（+ 可选写入）。返回 `{"exit": int, "result": dict}`；不打印。"""
    got = census_fn()
    census = got["census"]
    verdict = classify_census(census)
    key = site_key(url)
    result = {
        "url": str(url), "site_key": key,
        "platform": verdict["platform"], "confidence": verdict["confidence"],
        "ambiguous": verdict["ambiguous"], "matches": verdict["matches"],
        "config_id": None, "anchor": None, "evidence": [],
        "clicked_start": bool(got.get("clicked_start")),
        "clicked_text": str(got.get("clicked_text") or ""),
        "click_note": str(got.get("note") or ""),
        "census": {"title": census.get("title"), "hash": census.get("hash"),
                   "testids": (census.get("testids") or [])[:40]},
        "linked": None,
    }
    if verdict["platform"] is None:
        return {"exit": EXIT_UNKNOWN, "result": result}
    result["evidence"] = verdict["matches"][0]["evidence"] if verdict["matches"] else []
    result["anchor"] = next(f["anchor"] for f in FINGERPRINTS
                            if f["platform"] == verdict["platform"])
    try:
        result["config_id"] = resolve_config(verdict["platform"], site_rows,
                                             override=config_override)
    except ClassifyError:
        return {"exit": EXIT_CONFIG, "result": result}
    if not apply:
        return {"exit": EXIT_OK, "result": result}

    if client is None:
        return {"exit": EXIT_LINK, "result": result}
    try:
        linked = client.form_config_link(
            key, result["config_id"],
            operator="claude (classify: %s, %s)" % (verdict["platform"], key),
            reason="census: " + "; ".join(result["evidence"])[:300])
    except Exception:  # noqa: BLE001 —— 写失败/没 token/回执读不成都算没写成
        return {"exit": EXIT_LINK, "result": result}
    result["linked"] = {"created": bool(linked.get("created")),
                        "config_id": int(linked.get("config_id") or result["config_id"])}
    #: 回读复核：这一行必须是目标 config 且 status=1，否则不许报成功。
    try:
        rows = client.form_config_sites()
    except Exception:  # noqa: BLE001
        return {"exit": EXIT_LINK, "result": result}
    row = next((r for r in rows if str(r.get("site") or "") == key), None)
    if row is None or int(row.get("config_id") or 0) != result["config_id"] \
            or int(row.get("status") or 0) != 1:
        return {"exit": EXIT_LINK, "result": result}
    return {"exit": EXIT_OK, "result": result}


def main(argv=None) -> int:
    import argparse
    import os
    import subprocess

    from agent import fmr, selftest, service

    ap = argparse.ArgumentParser(description="约会站分类器（只读；--apply 才关联）")
    ap.add_argument("url")
    ap.add_argument("--ws-url", default="")
    ap.add_argument("--worker", default="")
    ap.add_argument("--bit-id", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--config-id", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-click", action="store_true")
    ap.add_argument("--keep-window", action="store_true")
    a = ap.parse_args(argv)

    window = None
    if a.ws_url:
        ws_url, owns = a.ws_url, False
    else:
        env = dict(os.environ)
        if a.worker:
            env["BIT_WORKER_IP"] = a.worker
        if a.bit_id:
            env["BIT_ID"] = a.bit_id
        window = service.bit_window_from_env(env)
        if window is None:
            sys.stderr.write("没配 BIT_WORKER_IP / BIT_ID，也没给 --ws-url。\n")
            return EXIT_WINDOW
        ws_url, owns = str(window.fresh_open() or ""), True
        if not ws_url:
            sys.stderr.write("开不出窗口。\n")
            return EXIT_WINDOW

    cdp_bin = os.environ.get("SITEFORGE_CDP_BIN") or "cdp"
    cdp_env = os.environ.copy()
    try:
        why = selftest._navigate(cdp_bin, ws_url, a.url, cdp_env)
        if why:
            sys.stderr.write("导航没成：%s\n" % why)
            return EXIT_WINDOW

        def eval_fn(js):
            return selftest._cdp_eval(cdp_bin, ws_url, js, cdp_env)

        host, port = selftest._host_port(ws_url)

        def click_fn():
            #: 打标记是 `census_with_start` 做的；这里只负责真人手势那一下。
            subprocess.run([cdp_bin, "click", START_SELECTOR, "--host", host,
                            "--port", str(port)], capture_output=True, text=True,
                           timeout=60)

        got = census_with_start(eval_fn, click_fn, allow_click=not a.no_click)
        client = fmr.FmrClient(
            token=os.environ.get("FMR_AGENT_TOKEN", ""),
            write_token=os.environ.get("FORM_SCRIPT_WRITE_TOKEN", ""))
        try:
            rows = client.form_config_sites()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("读站点清单失败：%s\n" % str(exc)[:200])
            return EXIT_CONFIG
        out = run_classify(a.url, census_fn=lambda: got, site_rows=rows,
                           client=client, apply=a.apply,
                           config_override=a.config_id)
    finally:
        if owns and not a.keep_window and window is not None:
            try:
                window.close()
            except Exception:  # noqa: BLE001
                pass
    if a.json:
        sys.stdout.write(json.dumps(out["result"], ensure_ascii=False, indent=1) + "\n")
    else:
        r = out["result"]
        sys.stdout.write(
            "URL    %s\n平台   %s（%s）  → config %s（锚点 %s）\n键     %s\n证据   %s\n"
            "动作   %s；只读普查\n写入   %s\n" % (
                r["url"], r["platform"] or "unknown", r["confidence"],
                r["config_id"] if r["config_id"] is not None else "—",
                r["anchor"] or "—", r["site_key"], "; ".join(r["evidence"]) or "—",
                ("点了 Start %r" % r["clicked_text"]) if r["clicked_start"]
                else (r["click_note"] or "未点 Start"),
                ("已关联：%r" % (r["linked"],)) if r["linked"] else "未写（加 --apply 才写）"))
    return int(out["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_classify.py tests/test_fmr_link.py -q`
Expected: 全绿（Task 2–5 的测试）。

- [ ] **Step 5: Commit**

```bash
git add agent/classify.py tests/test_classify.py
git commit -m "feat: classify CLI with optional formConfig/link apply"
```

---

### Task 6: 真站只读抽查 + 指纹校正

**Files:**
- Create: `docs/probes/2026-10-22-dating-classifier/README.md`

**Interfaces:**
- Consumes: `python -m agent.classify <url>`（Task 5）。
- Produces: 一份抽查记录；如指纹与真站不符，回到 Task 2 改表并重跑。

- [ ] **Step 1: 跑四个已知站（只读）**

```bash
set -a; . /company/siteforge/siteforge.env; set +a
B='--worker 192.168.1.222 --bit-id 982d158b41b14fbb87754667859b4719'
.venv/bin/python -m agent.classify 'https://soulmatemeets.com/land/sp/23d247da/' $B --json
.venv/bin/python -m agent.classify 'https://soulfuldate.com/land/sp/a64f40a4/' $B --json
.venv/bin/python -m agent.classify 'https://callyourdate.com/land/sp/5549e30e/' $B --json
.venv/bin/python -m agent.classify 'https://chattyromance.com/land/sp/81d0a481/' $B --json
```

> ⚠️ **必须显式 `--worker 192.168.1.222`**：`siteforge.env` 里那格是 197（8099 那台运营在用的），
> 不覆盖就会去开运营的窗口。

Expected（**按 DOM 家族**，不是「后端现在把它挂在哪」——2026-10-22 实测订正）：
- soulmatemeets 23d247da → `japansdates` / config 101；
- soulfuldate a64f40a4 → `japansdates` / 101；
- callyourdate 5549e30e → `japansdates` / **101**（后端现存配置是 66，但那页的 DOM 是
  `step-content-item-*` 那一套 —— 与 soulfuldate 10-10 的错配同类）；
- chattyromance 81d0a481 → `japansdates` / **101**（后端现存 142 的 Delachat；但那页也是
  `step-content-item-*` 变体）。
- **goldenagesouls.com** → `goldenagesouls` / 66（真正的 goldenagesouls 落地屏样本）。

- [ ] **Step 2: 跑一个非约会页**

```bash
set -a; . /company/siteforge/siteforge.env; set +a
.venv/bin/python -m agent.classify 'https://www.wildnutrition.com/' \
  --worker 192.168.1.222 --bit-id 982d158b41b14fbb87754667859b4719 --json
```

Expected: `platform: null`，退出码 2。

- [ ] **Step 3: 记录结果（并校正指纹）**

把每条的平台 / config / 证据 / 是否点了 Start 写进
`docs/probes/2026-10-22-dating-classifier/README.md`。
若有家族没命中，按真站 census 的 `testids`/`classes` 修 `FINGERPRINTS`（Task 2），
重跑本 Task 的 Step 1。

- [ ] **Step 4: Commit**

```bash
git add docs/probes/2026-10-22-dating-classifier/README.md agent/classify.py tests/test_classify.py
git commit -m "docs: dating classifier live read-only checks"
```

---

### Task 7: `--apply` 端到端（等后端部署）

**Files:**
- Modify: `docs/probes/2026-10-22-dating-classifier/README.md`

**Interfaces:**
- Consumes: Task 1 的客户端 + Task 5 的 CLI + 已部署的 `formConfig/link`。
- Produces: 一次真实关联记录（created + 回读行）。

- [ ] **Step 1: 确认后端路由已部署**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS \
  'https://fmr.3tkj.cn/api/quest/formConfig/link'
```

Expected: 不是 404（部署后）。仍是 404 ⇒ **停在这里**，不要跑 `--apply`。

- [ ] **Step 2: 用一个新 landing 跑 `--apply`**

先只读确认它当前**没有**映射行，再 apply：

```bash
set -a; . /company/siteforge/siteforge.env; set +a
B='--worker 192.168.1.222 --bit-id 982d158b41b14fbb87754667859b4719'
.venv/bin/python -m agent.classify '<new-url>' $B --json
.venv/bin/python -m agent.classify '<new-url>' $B --apply --json
```

Expected: 第一条退出 0 且 `linked: null`；第二条退出 0、`linked.created: true`，
回读行 `config_id` 等于建议值、`status: 1`。

- [ ] **Step 3: 记录并提交**

```bash
git add docs/probes/2026-10-22-dating-classifier/README.md
git commit -m "docs: dating classifier apply smoke test"
```

---

## Self-Review

**Spec coverage**
- 只读 census + 一次 Start：Task 2（JS）/ Task 4（运行时）。
- 三族指纹 + unknown/ambiguous：Task 2。
- 锚点 → config：Task 3。
- link 客户端 + body.status + 回读：Task 1 / Task 5。
- CLI / JSON / 退出码：Task 5。
- 真站只读抽查：Task 6；`--apply` 等部署：Task 7。
- 安全约束（不填表/不提交/最多一次点击）：Task 4 测试 + Task 5 apply 门。

**Placeholder scan**
- 已扫过：无 TBD/TODO/「类似 Task N」；每个代码步骤都有可执行的代码。
- 真站命令**必须**带 `--worker 192.168.1.222`（`siteforge.env` 里那格是 197），Task 6/7 已写明。

**Type consistency**
- `classify_census` 返回 `platform/confidence/matches/ambiguous`；`run_classify` 直接用。
- `resolve_config` 返回 `int`；`form_config_link` 返回 `{site, config_id, created}`；回读用
  `form_config_sites` 的 `config_id/status`。

**Review Focus → 测试归属**
- 慢加载/未知：Task 2 `unknown` 用例 + Task 4 不点/点了没变用例。
- 多平台：Task 2 `ambiguous` 用例。
- URL 归一化：Task 2 `site_key` 用例 + Task 5 `site_key` 断言。
- body.status 404：Task 1 用例。
- Start 误触：Task 4 只点一次/无候选不点用例。
