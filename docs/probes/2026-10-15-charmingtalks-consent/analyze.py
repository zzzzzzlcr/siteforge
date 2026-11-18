#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 chatty.json 的 testid 分族，并与 10-10 soulfuldate 实测族 + goldenagesouls 标记对照。"""
import json
import re
import sys

d = json.load(open(sys.argv[1]))
t = d.get("testids") or []
u = sorted(set(t))
print("testids total=%d unique=%d" % (len(t), len(u)))

fam = {}
for x in u:
    if x.startswith("step-content-item-"):
        k = "step-content-item-*"
    elif re.match(r"^step-\d+$", x):
        k = "step-N"
    elif x.startswith("option-checkbox__item"):
        k = "option-checkbox__item_*"
    elif x.startswith("option-"):
        k = "option-*"
    elif x.startswith("age"):
        k = "age-*"
    elif x.startswith("image-button"):
        k = "image-button-*"
    else:
        k = "other"
    fam.setdefault(k, []).append(x)

for k in sorted(fam):
    v = fam[k]
    shown = v if len(v) <= 45 else v[:45] + ["..."]
    print("%3d  %-24s %s" % (len(v), k, json.dumps(shown, ensure_ascii=False)))

print("\n-- 关键标记 --")
for m in ["age-", "image-button-", "gender-", "continue-button", "legal-checkbox",
          "option-checkbox__item", "btn_skip", "ChooseAge", "ChoosePrefGender", "ProgressPanel"]:
    hit = [x for x in u if m in x]
    print("  %-26s %2d  %s" % (m, len(hit), json.dumps(hit[:14], ensure_ascii=False)))

soul = ["step-content-item-Basic", "step-content-item-ChooseGender", "step-content-item-ChoosePrefGender",
        "step-content-item-Preferences", "step-content-item-DynamicComponent", "step-content-item-ChooseAge",
        "step-content-item-AgeLegalText", "step-content-item-CheckboxTerms", "step-content-item-InputNameAutofill",
        "step-content-item-InputEmail", "step-content-item-InputPassword", "step-content-item-LoadingBarComponent",
        "gender-male", "gender-female", "pref-gender-male", "pref-gender-female", "legal-checkbox",
        "continue-button", "firstname", "email", "password", "signup-form", "toggle-login", "about-us-link"]
print("\n-- soulfuldate(10-10) 有、这里缺的 --")
print("  ", json.dumps([x for x in soul if x not in u], ensure_ascii=False))

print("\n-- inputs --")
print("  " + ", ".join("%s/%s=%dx%d" % (i["type"], i["name"], i["w"], i["h"]) for i in d.get("inputs", [])))
print("\n-- 遮挡层 --")
print("  " + json.dumps(d.get("cookieish"), ensure_ascii=False)[:400])
print("\n-- 可见按钮 --")
for b in d.get("buttons", []):
    print("   %-6s %-22s tid=%-24s %dx%d @%d,%d" % (b["tag"], b["t"][:22], b["tid"], b["w"], b["h"], b["x"], b["y"]))
