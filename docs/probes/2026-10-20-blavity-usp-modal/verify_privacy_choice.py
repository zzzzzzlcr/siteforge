#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑**补丁后产物本体**的 `_clear_privacy_choice`（真 CDPHelper / 真 cdp / 真页面）。

用法: python3 -I verify_privacy_choice.py <ws_url> [why]

与 verify_ad_popup.py 同一条纪律：只把 Filler 的**真方法**绑到一个假 self 上，
`self.cdp` 用真的 CDPHelper —— 被验的是产物里那份代码本身。
"""
import importlib.util
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACT = os.path.join(_HERE, "blavity.com_PATCHED_99b1c423.py")
FORMS_DIR = "/opt/skills/auto-farm-skill/forms"

sys.path.insert(0, FORMS_DIR)
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws_url = sys.argv[1]
why = sys.argv[2] if len(sys.argv) > 2 else "验证"

spec = importlib.util.spec_from_file_location("blavity_patched", ARTIFACT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("verify")


class Fake:
    pass


f = Fake()
f.cid = "verify-20261020"
f.log = log
f.missing = set()
f.probed = False
f.commands = None
f.cdp = mod.CDPHelper(ws_url)

BIND = ("_run", "_probe", "_note_missing", "_command_absent", "_check_command",
        "_cdp", "_action_cdp", "_ev")
for name in BIND:
    fn = mod.Filler.__dict__.get(name)
    assert fn is not None, "artifact has no %s" % name
    setattr(f, name, fn.__get__(f, Fake))

clear = mod.Filler.__dict__.get("_clear_privacy_choice")
assert clear is not None, "补丁没打上：产物里没有 _clear_privacy_choice"
setattr(f, "_clear_privacy_choice", clear.__get__(f, Fake))

print("== _PRIVACY_CHOICE_JS 长度 = %d" % len(mod._PRIVACY_CHOICE_JS))
print("== 点之前的状态: %r" % f._ev(mod._PRIVACY_CHOICE_JS))
print("== 调 _clear_privacy_choice(why=%r) ==" % why)
f._clear_privacy_choice(why=why)
print("== 点之后的状态: %r ==" % f._ev(mod._PRIVACY_CHOICE_JS))
