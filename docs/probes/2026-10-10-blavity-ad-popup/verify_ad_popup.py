#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑**补丁后产物本体**的 `_clear_ad_popup`（真 CDPHelper / 真 cdp 进程 / 真页面）。

用法: python3 -I verify_ad_popup.py <ws_url> [why]

为什么要这么绕：产物是 `siteforge` 的整脚本，直接 `main()` 会跑整条真站漏斗
（要 --form-file、会填真资料）。这里只把 `Filler` 的**真方法**绑到一个假 self 上，
`self.cdp` 用真的 `CDPHelper` —— 被验的是**产物里那份代码本身**，
不是复刻出来的等价物。
"""
import importlib.util
import logging
import os
import sys

ARTIFACT = "/tmp/bl_probe/blavity_patched.py"
FORMS_DIR = "/opt/skills/auto-farm-skill/forms"

sys.path.insert(0, FORMS_DIR)
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws_url = sys.argv[1]
why = sys.argv[2] if len(sys.argv) > 2 else "验证"

spec = importlib.util.spec_from_file_location("blavity_patched", ARTIFACT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)          # 只定义常量/类；main() 在 __main__ 保护里

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("verify")


class Fake:
    pass


f = Fake()
f.cid = "verify-20261010"
f.log = log
f.missing = set()
f.probed = False
f.commands = None
f.cdp = mod.CDPHelper(ws_url)         # ← 真的那个

BIND = ("_run", "_probe", "_note_missing", "_command_absent", "_check_command",
        "_cdp", "_action_cdp", "_ev")
for name in BIND:
    fn = mod.Filler.__dict__.get(name)
    assert fn is not None, "artifact has no %s" % name
    setattr(f, name, fn.__get__(f, Fake))
clear = mod.Filler.__dict__.get("_clear_ad_popup")
assert clear is not None, "补丁没打上：产物里没有 _clear_ad_popup"
setattr(f, "_clear_ad_popup", clear.__get__(f, Fake))

print("== _AD_POPUP_JS 长度 = %d" % len(mod._AD_POPUP_JS))
print("== 调 _clear_ad_popup(why=%r) ==" % why)
f._clear_ad_popup(why=why)
print("== 返回（上面若只有一条 INFO 就是全过程）==")
