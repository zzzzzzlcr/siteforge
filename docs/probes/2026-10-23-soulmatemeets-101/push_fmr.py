#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 cookie 变体补丁推到 FMR 的 **config 101**（10 个站点键共用一份脚本）。

用法：
    python3 -I push_fmr.py            # 只读：读后端现值、留底、打印每个键的 sha
    python3 -I push_fmr.py --apply    # 真写：update_form_script(enable=True)，再逐键回读

口径（与 2026-10-22 healthinsurancewatch / 2026-10-20 rebates 那两趟同一条）：
  · 写口要 `FORM_SCRIPT_WRITE_TOKEN`（读口用 `FMR_AGENT_TOKEN`，都在 siteforge.env）；
  · **写的是 config 101 那一行**（`form_configs.script_source`）⇒ 用组里**已有的**键写，
    绝不拿还没挂的 23d247da 写（那会按 createIfMissing 建出**另一份新配置**）；
  · source **逐字节**上传（不做 strip），回读 sha256 必须与本地一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path("/company/siteforge")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

#: config 101 里随便哪一个已存在的键都行 —— 写口按它解析到 config，再改 config 那一行
WRITE_KEY = "soulmatemeets.com/land/sp/976b69dc"
SOURCE = HERE / "japansdates_PATCHED.py"
BEFORE = HERE / "fmr_before_41c74af2.py"

#: 这一组里我们逐个回读的键（23d247da 现在还没挂，不在这里）
GROUP_KEYS = (
    "japansdates.com",
    "soulmatemeets.com/land/sp/976b69dc",
    "soulfuldate.com/land/sp/5a4f738d",
    "charmingtalks.com/land/sp/196c57bd",
    "charmingtalks.com/land/sp/79229f81",
    "cupidfeel.com/land/sp/df41954d",
    "datingarts.com/land/sp/5aa1a92d",
    "goldenromances.com/land/sp/88374ec2",
    "juliettdate.com/land/sp/9426b634",
    "miaromance.com/land/sp/96654b49",
)


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def sha(src: str) -> str:
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--operator",
                    default="claude (用户 2026-10-23 11:28 选方案1：japansdates 补 cookie 变体)")
    args = ap.parse_args()

    src_path = pathlib.Path(args.source)
    raw = src_path.read_bytes()
    source = raw.decode("utf-8")
    local_sha = hashlib.sha256(raw).hexdigest()
    print("LOCAL", src_path.name, "bytes", len(raw), "sha256", local_sha)
    print("SENTINEL accept-all:", source.count("cookie-popup-consent-accept-all"),
          "| variation overlay:", source.count(".cookie-popup-variation-consent"),
          "| dismiss:", source.count("_dismiss_consent"), "| probe:",
          source.count("_CONSENT_PROBE_JS"))

    env = read_env()
    client = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                           write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))

    # ── 写之前：逐键读，确认整组是同一份（不是同一份就停手） ─────────
    before = {}
    shas = {}
    for key in GROUP_KEYS:
        try:
            d = client.form_script(key)
            src = d.get("source") or ""
            before[key] = src
            shas[key] = sha(src) if src else ""
        except Exception as exc:                       # noqa: BLE001
            print("READ_BEFORE_ERR", key, type(exc).__name__, str(exc)[:160])
            return 1
    uniq = sorted(set(shas.values()))
    print("BEFORE uniq shas:", uniq)
    for key, h in shas.items():
        print("  ", h[:12], len(before[key].encode("utf-8")), key)
    if len(uniq) != 1:
        print("!! 这一组现在就不是同一份 —— 先查清楚再写")
        return 1
    if not BEFORE.exists() and before:
        BEFORE.write_text(next(iter(before.values())), encoding="utf-8")
        print("留底 →", BEFORE.name, "sha", sha(BEFORE.read_text(encoding='utf-8'))[:12])

    if not args.apply:
        print("[只读] 没写。加 --apply 才写。")
        return 0

    # ── 写：用组里已有的键（按它解析到 config 101） ─────────────────
    r = client.update_form_script(WRITE_KEY, source, operator=args.operator, enable=True)
    print("WRITE verdict=%s status=%r" % (getattr(r, "verdict", "?"),
                                          getattr(r, "status", None)))
    print("SAY", getattr(r, "say", "")[:500])
    if getattr(r, "verdict", "") != fmr.WRITE_OK:
        print("!! 写没成功 —— 不做后面的事")
        return 1

    # ── 写完：逐键回读 + 组别没变 ──────────────────────────────────
    bad = []
    for key in GROUP_KEYS:
        d = client.form_script(key)
        src = d.get("source") or ""
        h = sha(src) if src else ""
        print("AFTER", h[:12], len(src.encode("utf-8")), key)
        if h != local_sha:
            bad.append(key)
    sites = client.form_config_sites()
    group = sorted({str(r.get("site")) for r in sites
                    if str(r.get("config_id")) == "101"})
    print("GROUP(101):", json.dumps(group, ensure_ascii=False))
    if bad:
        print("!! 回读不一致的键：", bad)
        return 1
    print("== 读回 10 个键全部 == 本地新 sha", local_sha[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
