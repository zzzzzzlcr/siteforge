import json, re, subprocess, sys, time, urllib.request

CDP = "/opt/skills/auto-farm-skill/cdp"; D = sys.argv[1]
def sh(*a): return subprocess.run(a, capture_output=True, text=True, timeout=120).stdout
def open_tab(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:9222/json/new?" + url, method="PUT"), timeout=10))["id"]
def close_tab(t): subprocess.run(["curl","-s",f"http://127.0.0.1:9222/json/close/{t}"],capture_output=True)
def ev(frame=None):
    cmd = [CDP,"--host","127.0.0.1","--port","9222","eval","--file",f"{D}/observe.js"]
    if frame: cmd += ["--frame-id", frame]
    out = sh(*cmd)
    for line in reversed(out.strip().split("\n")):
        line = line.strip()
        if line[:1] in ('"','{'):
            try:
                v = json.loads(line); return json.loads(v) if isinstance(v,str) else v
            except Exception: continue
    return {"ok": False, "raw": out[:200]}
def child_frames():
    out = sh(CDP,"--host","127.0.0.1","--port","9222","snapshot")
    ids = re.findall(r'"([0-9A-F]{32})"\s*,\s*"[^"]*"\s*:\s*"[^"]*"\s*,\s*"url"\s*:\s*"([^"]*)"', out)
    if not ids:
        ids = [(m, "") for m in dict.fromkeys(re.findall(r'"([0-9A-F]{32})"', out))]
    return ids

def report(tag, o, extra=""):
    if not o.get("ok"): print(f"  ❌ {tag}: {str(o)[:180]}"); return None
    c = o["counts"]
    print(f"  ✅ {tag}{extra}")
    print(f"     shadow_roots={o['shadow_roots']} text_len={o['page_text_len_raw']} "
          f"actions={c['actions']} fields={c['fields']} groups={c['option_groups']} obs={c['obstructions']}")
    print(f"     text[0:66]={o['page_text'][:66]!r}")
    occ = [a["text"][:20] for a in o["actions"] if a["occluded_by"]]
    print(f"     被遮挡: {occ[:4] if occ else '无 ✓'}")
    print(f"     region: {sorted(set(a['region'] for a in o['actions']))}")
    by={}
    for a in o["actions"]: by.setdefault(a["text"][:18],[]).append((a["region"],a["stability"],a["shadow_depth"]))
    dups={k:v for k,v in by.items() if len(v)>1 and k}
    if dups: print(f"     同名可区分: {list(dups.items())[:3]}")
    return o

for name in ("base","shadow"):
    t=open_tab(f"http://127.0.0.1:8892/{name}.html"); time.sleep(3)
    sh(CDP,"--host","127.0.0.1","--port","9222","active",t)
    report(name, ev()); close_tab(t)

# 跨源 iframe：主帧 + 子帧分别 observe 再合并
t=open_tab("http://127.0.0.1:8892/outer.html"); time.sleep(4)
sh(CDP,"--host","127.0.0.1","--port","9222","active",t)
main = report("outer(主帧)", ev())
frames = child_frames()
child = None
for fid,url in frames:
    if "inner.html" in (url or ""): child = fid
if child is None:
    for fid,url in frames:
        o = ev(fid)
        if o.get("ok"): child = fid; break
print(f"     子帧 id = {child}")
sub = report("inner(跨源子帧)", ev(child), extra="  ← 单独一次 eval")
if main and sub and main.get("ok") and sub.get("ok"):
    merged = {"actions": len(main["actions"])+len(sub["actions"]),
              "fields": len(main["fields"])+len(sub["fields"]),
              "groups": len(main["option_groups"])+len(sub["option_groups"]),
              "shadow_roots": main["shadow_roots"]+sub["shadow_roots"]}
    print(f"  ✅ 合并后: {merged}  ← 跨帧必须两次 eval 再合并（同源策略）")
close_tab(t)
