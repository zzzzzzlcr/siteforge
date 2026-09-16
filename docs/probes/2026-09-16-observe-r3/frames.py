import json, subprocess, sys
out = subprocess.run(["/opt/skills/auto-farm-skill/cdp","--host","127.0.0.1","--port","9222","snapshot"],
                     capture_output=True, text=True, timeout=60).stdout
i = out.find('{'); j = out.rfind('}')
try:
    d = json.loads(out[i:j+1])
except Exception:
    print(""); raise SystemExit
res = []
def walk(n):
    if isinstance(n, dict):
        fid, url = n.get("frameId"), n.get("url")
        if fid and url: res.append((fid, url))
        for v in n.values(): walk(v)
    elif isinstance(n, list):
        for v in n: walk(v)
walk(d)
for fid, url in res: print(f"{fid}\t{url}")
