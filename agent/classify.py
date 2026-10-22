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
import time

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
