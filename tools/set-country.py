#!/usr/bin/env python3
"""换**代理国家**（agent 自测第 5 遍 / 面板上那个按钮）。

走的是你们自己那条干净的路 —— 一句话概括：
  ① 问 `PROXY_API?url=<站点>&country=<XX>` 拿一条链（那套接口的契约见 `newTaskTest/src/api.py`）；
  ② 按 `gost-watch.sh` 的**链文件格式**写 `config/gost1081.chain`（两行：jumper 在前、出口在后）；
  ③ **只**让 :1081 那个实例重启（watcher 盯文件变化）—— ⚠️ **绝不碰 :1080**：
     `gost-watch.sh` 开头写着，生产那条链在 :1080，改它会**把正在跑的生产任务一起换掉**
     （2026-09-14 实测过）。
  ④ **核到出口真变了**才算成：经 `:1081` 打一个问国家的接口，读到那个国家才 exit 0。
     ⚠️ 换不成、或核不到 ⇒ **非 0 退出 + 说清**，绝不回一句「换好了」。

用法：
    python3 tools/set-country.py --country US --url https://example.com
    python3 tools/set-country.py --country CA --url https://example.com --dry-run   # 只拉链不写
    python3 tools/set-country.py --show          # 只读：现在这个出口是哪国（不动任何东西）
退出码：0 = **核到了**那个国家的出口；1 = 没成（原因在 stderr）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PROXY_API = os.environ.get("PROXY_API", "https://tmk.3tkj.cn/api/get_proxies")
#: `gost-watch.sh` 盯的那个文件（它的格式写在那个脚本的开头）
CHAIN_FILE = pathlib.Path(os.environ.get(
    "SITEFORGE_GOST_CHAIN", "/opt/skills/auto-farm-skill/config/gost1081.chain"))
SOCKS = os.environ.get("SITEFORGE_GOST_SOCKS", "127.0.0.1:1081")
ASK_COUNTRY = "https://ipinfo.io/country"      # 纯文本两字母国家
TIMEOUT_S = float(os.environ.get("SITEFORGE_COUNTRY_TIMEOUT", "60"))


def fetch_chain(country: str, url: str) -> dict:
    """问那条接口要一条链（返回 `proxies[0]`）。**拉不到就抛** —— 不编一条假的。"""
    target = urllib.parse.urlparse(url)
    site = "%s://%s%s" % (target.scheme or "https", target.netloc, target.path)
    api = "%s?url=%s" % (PROXY_API, urllib.parse.quote(site, safe=""))
    if country:
        api += "&country=%s" % urllib.parse.quote(country)
    with urllib.request.urlopen(api, timeout=300) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    if body.get("code") != 0 or not body.get("proxies"):
        raise RuntimeError("那条接口没给链：%s" % json.dumps(body, ensure_ascii=False)[:200])
    return dict(body["proxies"][0])


def chain_text(p: dict) -> str:
    """按 `gost-watch.sh` 认的两行格式拼（jumper 在前、出口 http 在后）。

    那个脚本原文：「链文件格式（一行，容器侧的 `_setup_proxy` 写入）：
    `kcp://<jumper>?tcp=true&mode=fast3&key=tmk:tomoko2` / `http://<user>:<pass>@<host>:<port>`
    两行 = 两个 `-F`（jumper 在前，出口 http 在后）。空文件 = 停掉实例。」
    """
    lines = []
    jumper = str(p.get("jumper") or "").strip()
    if jumper:
        #: ⚠️ 拿现网那份链文件对出来的（2026-09-22）：接口给的 `jumper` **本身就是完整的一行**
        #: （`kcp://host:port?tcp=true&mode=fast3&key=tmk:tomoko2`）⇒ **原样写**，
        #: 别再套一层前缀/参数（我第一版套了 ⇒ 拼成 `kcp://kcp://…?…&key=…?…&key=…` ✗）。
        #: 没有 scheme 的那种（裸 `host:port`）才补上前缀。
        lines.append(jumper if "://" in jumper
                     else "kcp://%s?tcp=true&mode=fast3&key=tmk:tomoko2" % jumper)
    server = str(p.get("server") or "").strip()
    user, passwd = str(p.get("user") or ""), str(p.get("passwd") or "")
    auth = ("%s:%s@" % (user, passwd)) if user else ""
    if server:
        lines.append("http://%s%s" % (auth, server))
    if not lines:
        raise RuntimeError("那条接口给的链两个字段都空：%s" % p)
    return "\n".join(lines) + "\n"


def exit_country() -> str:
    """经 :1081 问一句「我这个出口是哪国」—— 空串 = 问不到（**不是**「换了」）。"""
    try:
        done = subprocess.run(["curl", "-s", "--max-time", "20", "--socks5", SOCKS, ASK_COUNTRY],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or "").strip().upper()[:2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", help="两字母国家码，如 US / CA")
    ap.add_argument("--url", help="目标任务站（接口按它挑链）")
    ap.add_argument("--dry-run", action="store_true", help="只拉链、不写文件、不重启")
    ap.add_argument("--show", action="store_true",
                    help="只读：经 :1081 问一句现在这个出口是哪国（不动任何东西）")
    a = ap.parse_args()
    if a.show:
        got = exit_country()
        print(got or "（问不到）", flush=True)
        if not got:
            print("[country] ✗ 问不出来 —— 这不代表它是哪一国（可能 :1081 没在跑）",
                  file=sys.stderr)
            return 1
        return 0
    if not (a.country and a.url):
        print("[country] ✗ 要换国家就得给 --country 和 --url（只想看现状用 --show）",
              file=sys.stderr)
        return 1
    want = a.country.strip().upper()

    before = exit_country()
    print("[country] 换之前：出口国家 = %s" % (before or "（问不到）"), flush=True)
    p = fetch_chain(want, a.url)
    text = chain_text(p)
    print("[country] 拿到链：proxy_id=%s server=%s" % (p.get("proxy_id"), p.get("server")), flush=True)
    if a.dry_run:
        print("[country] --dry-run：不写文件。链内容：\n%s" % text, flush=True)
        return 0

    CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAIN_FILE.write_text(text, encoding="utf-8")
    print("[country] 写了 %s（watcher 会重启 :1081；:1080 一个字没动）" % CHAIN_FILE, flush=True)

    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        time.sleep(3)
        got = exit_country()
        if got == want:
            print("[country] 核到了 ✓ 出口国家 = %s（%s）" % (got, SOCKS), flush=True)
            return 0
        if got:
            print("[country] 还在换… 现在读到 %s" % got, flush=True)
    print("[country] ✗ **没核到**：要的是 %s，%G 秒内读到的是 %s —— 这次换链**没成**"
          % (want, TIMEOUT_S, exit_country() or "（问不到）"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
