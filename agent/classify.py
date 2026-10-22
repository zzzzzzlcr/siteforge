"""约会站分类器：只读 DOM 指纹 → 平台 → 建议 config；--apply 时调 formConfig/link。

设计见 docs/superpowers/specs/2026-10-22-dating-site-classifier-design.md。
纯函数部分（census → verdict、锚点 → config）不碰网络/窗口，可直接单测。
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
