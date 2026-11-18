#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证因果：cookie 浮层（cookie-popup-variation-consent）是不是就是卡住的那堵墙。

前一支已经量到：
  · 性别屏 `pref-gender-male` 的 input 能被勾上（checked=True / class 带 _checked），
    但 hash 一直是 #c3RlcDM=（step3），页面不前进；
  · `document.elementFromPoint(选项中心)` 命中的是 **cookie-popup-variation-consent**
    —— 浮层正盖在选项卡片上；
  · 页面上一直摆着 `cookie-popup-consent-accept-all`（Accept all）。

这一支就做三步：走到那一屏 → **按它自己的 testid 点掉浮层** → 再点一次 male。
前进 = 因果成立；还不走 = 浮层不是全部原因（那就继续查 SPA 要不要真点击）。

跑法（宿主）：python3 -I probe_after_consent.py
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                        # noqa: E402
ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_prod.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"


def load_mod():
    spec = importlib.util.spec_from_file_location("jd_prod", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    os.makedirs(os.path.join(HERE, "prefgender"), exist_ok=True)
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    mod = load_mod()
    P.say("探活：%r" % P.alive())
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101consent_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(3)
        if not P.walk_to_gender(f, mod):
            P.say("没走到性别屏，停")
            return 2
        P.dump(f, "c0_before_accept")

        # ── ① 按站点自己的 testid 点掉同意浮层 ────────────────────────
        accepted = False
        for sel in ('[data-testid="cookie-popup-consent-accept-all"]',
                    '[data-testid="cookie-popup-consent-reject-all"]'):
            try:
                out = f.cdp.click(sel)
                P.say("点 %s → %r" % (sel, str(out)[:120]))
                accepted = True
                break
            except Exception as exc:             # noqa: BLE001
                P.say("点 %s 报错：%s" % (sel, exc))
        time.sleep(2.5)
        P.dump(f, "c1_after_accept")
        P.say("浮层还在吗：%s" % str(f.cdp.eval(
            "(function(){var e=document.querySelector('.cookie-popup-variation-consent');"
            "if(!e)return 'no-element';var r=e.getBoundingClientRect();"
            "return JSON.stringify({w:r.width,h:r.height,vis:getComputedStyle(e).display});})()",
            ""))[:200])

        # ── ② 再点一次 male（坐标点击走可见卡片） ─────────────────────
        p = f.read()
        P.say("read: step=%r options=%s next=%r" %
              (p.get("step") if p else None, p.get("options") if p else None,
               p.get("nextVisible") if p else None))
        out = f.click_option("pref-gender-male")
        P.say("click_option(pref-gender-male) → %r" % out)
        time.sleep(3)
        p = f.read()
        P.dump(f, "c2_after_male")
        P.say("== 现在 step=%r url=%s" %
              (p.get("step") if p else None, str(p.get("url"))[-60:] if p else None))
        if p and "ChoosePrefGender" not in str(p.get("step")):
            P.say(">>> 浮层点掉后，点一下 male 就前进了 —— 因果成立")
        else:
            P.say(">>> 还不走：浮层不是全部原因")

        # ── ③ 对照：用真文本点击（label）再试一次 ────────────────────
        try:
            f.click_text("Man")
            time.sleep(3)
            p = f.read()
            P.say("== 文本点击后 step=%r url=%s" %
                  (p.get("step") if p else None, str(p.get("url"))[-60:] if p else None))
        except Exception as exc:                 # noqa: BLE001
            P.say("click_text 报错：%s" % exc)
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                            # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(2)
