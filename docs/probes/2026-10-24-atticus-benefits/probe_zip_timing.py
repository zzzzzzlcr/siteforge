"""Atticus zip 屏时间线：是「顺序变了认不出」还是「等待太短 / 填值没进状态」？

只读式：开干净窗口 → 点 See if you qualify → 到 zip 屏 →
  1) cdp form 填 10001，按秒轮询 45 秒：zip 值、所有 Next/Continue 按钮的 disabled、
     站点请求（performance resource）什么时候回来；
  2) 原生 setter + 全套事件写 90210，再轮询 30 秒；
  3) 若真有可点的 Next，标记它 → cdp 点一次 → 看 URL/head 有没有前进。
结束关窗。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/probe_zip_timing.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window, _as_census

ENTRY = "https://try.atticus.com/ssd/benefits"
CDP = os.environ.get("SITEFORGE_CDP_BIN") or "/opt/siteforge/tools/cdp/cdp"

STATE_JS = r"""(function(){
  function deepAll(sel){var out=[];function walk(root){
    root.querySelectorAll(sel).forEach(function(e){out.push(e);});
    var all=root.querySelectorAll('*');
    for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}
    walk(document);return out;}
  function vis(e){if(!e)return false;var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0;}
  function txt(e){return ((e.innerText||e.textContent||'').replace(/\s+/g,' ').trim()).slice(0,60);}
  var bs=[];
  deepAll('button,[role=button],a').forEach(function(e){
    if(!vis(e))return; var r=e.getBoundingClientRect();
    bs.push({tag:e.tagName.toLowerCase(),text:txt(e),disabled:!!e.disabled,
      aria:e.getAttribute('aria-disabled')||'',x:Math.round(r.x),y:Math.round(r.y),
      w:Math.round(r.width),h:Math.round(r.height)});});
  var z=null;
  var ins=[];
  deepAll('input,select,textarea').forEach(function(e){
    if(e.id==='zipcode')z=e;
    if(!vis(e))return;
    ins.push({id:e.id||'',name:e.name||'',ph:e.getAttribute('placeholder')||'',
      type:e.type||e.tagName.toLowerCase(),value:e.value||''});});
  var perf=performance.getEntriesByType('resource').filter(function(r){
    return /benefit|zip|survey|api|graphql/i.test(r.name);}).slice(-12).map(function(r){
    return {name:r.name.slice(0,140),dur:Math.round(r.duration),start:Math.round(r.startTime)};});
  var hs=deepAll('h1,h2,h3'); var h=hs.length?hs[0]:null;
  return JSON.stringify({url:location.href,zip:z?z.value:null,
    head:h?h.innerText.replace(/\s+/g,' ').slice(0,180):'',
    body:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,220),
    inputs:ins,buttons:bs,perf:perf});})()"""

MARK_CTA_JS = r"""(function(){
  var bs=[].slice.call(document.querySelectorAll('a,button,[role=button]'));
  for(var i=0;i<bs.length;i++){var t=(bs[i].innerText||'').replace(/\s+/g,' ').trim();
    if(/see if you qualify/i.test(t)){bs[i].setAttribute('data-sf-probe','cta');
      return JSON.stringify({text:t.slice(0,60)});}}
  return JSON.stringify({});})()"""

SET_FULL_JS = r"""(function(v){
  function deepAll(sel){var out=[];function walk(root){
    root.querySelectorAll(sel).forEach(function(e){out.push(e);});
    var all=root.querySelectorAll('*');
    for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}
    walk(document);return out;}
  var e=null; deepAll('input').forEach(function(x){if(x.id==='zipcode')e=x;});
  if(!e)return JSON.stringify({error:'no-zip'});
  e.focus();
  var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
  d.set.call(e,v);
  ['keydown','keypress','input','keyup','change','blur'].forEach(function(t){
    e.dispatchEvent(t==='input'||t==='change'?new Event(t,{bubbles:true})
      :new KeyboardEvent(t,{bubbles:true,key:'1',keyCode:49}));});
  return JSON.stringify({value:e.value});})"""

MARK_NEXT_JS = r"""(function(){
  function deepAll(sel){var out=[];function walk(root){
    root.querySelectorAll(sel).forEach(function(e){out.push(e);});
    var all=root.querySelectorAll('*');
    for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}
    walk(document);return out;}
  function tt(e){return ((e.innerText||e.textContent||'').replace(/\s+/g,' ').trim());}
  var bs=deepAll('button,[role=button],a').filter(function(e){
    var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&!/next\s*step/i.test(tt(e));});
  var hit=[];
  for(var i=0;i<bs.length;i++){var t=tt(bs[i]);
    if(/^(next|continue)\b/i.test(t)&&!bs[i].disabled&&bs[i].getAttribute('aria-disabled')!=='true'){
      hit.push(bs[i]);}}
  if(!hit.length)return JSON.stringify({});
  var e=hit[hit.length-1];
  e.setAttribute('data-sf-probe','next');
  var r=e.getBoundingClientRect();
  return JSON.stringify({text:tt(e).slice(0,40),
    x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});})"""


def cdp(args, timeout=45):
    done = subprocess.run([CDP] + args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout or "").strip()


def eval_json(eval_fn, js):
    """`selftest._cdp_eval` 可能回 dict / 一层 JSON 文本 / 两层 JSON 文本。"""
    raw = eval_fn(js)
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:  # noqa: BLE001
            return d
    return d


def next_buttons(state):
    out = []
    for b in state.get("buttons") or []:
        t = str(b.get("text") or "")
        if any(t.lower().startswith(k) for k in ("next", "continue", "get started",
                                                 "get results", "see if")):
            out.append((t[:28], b.get("disabled"), b.get("aria"), b.get("w"), b.get("h")))
    return out


def perf_names(state):
    return [p.get("name", "")[-60:] + " (%sms)" % p.get("dur") for p in state.get("perf") or []]


def snap(eval_fn, tag, last=None):
    s = eval_json(eval_fn, STATE_JS)
    if not isinstance(s, dict) or not s:
        print("[%s] UNPARSED %s" % (tag, str(s)[:160]), flush=True)
        return last or {}
    ins = [(i.get("id") or i.get("ph") or i.get("name") or "", i.get("value") or "")
           for i in (s.get("inputs") or [])][:6]
    line = {"zip": s.get("zip"), "next": next_buttons(s), "url": str(s.get("url"))[-60:],
            "head": str(s.get("head") or "")[:60], "inputs": ins,
            "perf": len(s.get("perf") or [])}
    print("[%s] %s" % (tag, json.dumps(line, ensure_ascii=False)), flush=True)
    return s


def poll(eval_fn, tag, seconds, last=None):
    end = time.time() + seconds
    prev = None
    s = last or {}
    while time.time() < end:
        time.sleep(1.0)
        s = snap(eval_fn, tag, s)
        key = (s.get("zip"), tuple(next_buttons(s)), str(s.get("url")),
               len(s.get("perf") or []))
        if key != prev:
            prev = key
        elif int(time.time()) % 10 < 1:
            print("   ... still %s" % json.dumps({"zip": s.get("zip"),
                                                   "next": next_buttons(s)},
                                                  ensure_ascii=False), flush=True)
    return s


def main() -> int:
    window = service.bit_window_from_env(os.environ)
    if window is None:
        sys.stderr.write("没配 BIT_WORKER_IP / BIT_ID。\n")
        return 3
    ws_url = str(window.fresh_open() or "")
    if not ws_url:
        sys.stderr.write("开不出窗口。\n")
        return 3
    cdp_bin = os.environ.get("SITEFORGE_CDP_BIN") or CDP
    cdp_env = os.environ.copy()

    def eval_fn(js):
        return selftest._cdp_eval(cdp_bin, ws_url, js, cdp_env)

    try:
        if not wait_for_window(eval_fn):
            sys.stderr.write("窗口没就绪。\n")
            return 3
        target = sys.argv[1] if len(sys.argv) > 1 else ENTRY
        why = selftest._navigate(cdp_bin, ws_url, target, cdp_env)
        if why:
            sys.stderr.write("导航没成：%s\n" % why)
            return 3
        time.sleep(3.0)
        host, port = selftest._host_port(ws_url)

        if target == ENTRY:
            for _ in range(3):
                said = eval_json(eval_fn, MARK_CTA_JS)
                if isinstance(said, dict) and said.get("text"):
                    print("CTA", json.dumps(said, ensure_ascii=False), flush=True)
                    print("CLICK_CTA", cdp(["click", '[data-sf-probe="cta"]',
                                            "--host", host, "--port", str(port)]), flush=True)
                    break
                time.sleep(2)

        deadline = time.time() + 25
        clicked = 0
        while time.time() < deadline:
            s = snap(eval_fn, "wait-zip")
            if s.get("zip") is not None:
                break
            if clicked < 3:
                marked = eval_json(eval_fn, MARK_NEXT_JS)
                if isinstance(marked, dict) and marked.get("text"):
                    print("FIRST_STATE_NEXT", json.dumps(marked, ensure_ascii=False),
                          flush=True)
                    print("CLICK_FIRST_STATE", cdp(["click", '[data-sf-probe="next"]',
                                                    "--host", host, "--port", str(port)]),
                          flush=True)
                    clicked += 1
                    time.sleep(3)
                    continue
            time.sleep(2)
        else:
            print("!! 等不到 zip 格（first-state Next 点了 %d 次）" % clicked, flush=True)
            return 4

        print("== A: cdp form 10001", flush=True)
        print("   ", cdp(["form", "#zipcode", "--value", "10001",
                          "--host", host, "--port", str(port)]), flush=True)
        s = poll(eval_fn, "A-cdp-form", 45)

        print("== B: native setter + full events 90210", flush=True)
        raw = eval_json(eval_fn, SET_FULL_JS + '("90210")')
        print("   ", str(raw)[:120], flush=True)
        s = poll(eval_fn, "B-native", 30)

        print("== perf", json.dumps(perf_names(s), ensure_ascii=False), flush=True)

        marked = eval_json(eval_fn, MARK_NEXT_JS)
        print("== MARK_NEXT", json.dumps(marked, ensure_ascii=False)[:220], flush=True)
        if isinstance(marked, dict) and marked.get("text"):
            print("== CLICK_NEXT", cdp(["click", '[data-sf-probe="next"]',
                                        "--host", host, "--port", str(port)]), flush=True)
            time.sleep(2)
            s = snap(eval_fn, "after-next")
            time.sleep(5)
            s = snap(eval_fn, "after-next+5s")
        else:
            print("== 没有可点的 Next（仍然禁用）", flush=True)
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
