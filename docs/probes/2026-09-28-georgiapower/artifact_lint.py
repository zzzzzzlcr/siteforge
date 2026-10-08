#!/usr/bin/env python3
"""产物体检 —— 把 2026-09-28 一整天真站排查踩出来的病，做成可复跑的检查项。

用法：
    python3 artifact_lint.py [目录…]               # 默认扫 forms/sites 与 forms-222/sites
    python3 artifact_lint.py --all                 # 连不是今天生成的也扫
    python3 artifact_lint.py --since 2026-09-28    # 只看这天之后生成的（按 PROVENANCE）

检查项（每条都对应今天的一次真站失败，括号里是证据）：
  A. `strict: True`            —— 某些页面上**每一步 form 都失败**（mapleporchdaily 实测）
  B. 判据跨元素拼接            —— 长串 + 含空格，剪点落在两个元素中间（georgiapower「Main Content …」）
  C. 判据是瞬时文案            —— 校验报错 / loading（georgiapower「Please enter a valid email…」）
  D. 判据含填进去的值          —— 与 FILLS 字面量求交（georgiapower「123 Peachtree St NE … 123」）
  E. `when: None`              —— 无条件执行（广告落到别的页会乱点）
  F. 缺 `_follow_new_tab`      —— 点 CTA 开新 tab 不跟随（那两份缺陷单的正题）
  G. FILLS.source 与资料键不同名 —— `phone` vs `phone_number`（mapleporchdaily 那次被填了英国号）
  H. 判据里没有正文那一条      —— 只靠地址认页（SPA 上等于没判据）
  I. 自造 id / nth-of-type 长链 —— 换次构建就漂（georgiapower：#input-174 → #input-167）
"""
import argparse
import ast
import datetime as dt
import os
import re
import sys

#: 瞬时文案：校验报错 / 加载中 / toast —— 都不该当「认页」判据
TRANSIENT = re.compile(
    r"complete this field|please enter|is not valid|invalid|required|"
    r"error|loading|please wait|try again|verification", re.I)
#: 资料库真给的键（formMessage 实测，2026-09-28）
LIB_KEYS = {"task_id", "email", "card_id", "username", "postal_code", "password",
            "address", "city", "email_type", "country", "state", "phone_number",
            "is_use", "source"}
#: 产物 source 名 → 资料库可能对应的键（今天加的别名表就是这张）
#: 账本给「页面上这个框」起的**占位名**（`field` / `field-2` …）—— 它们不是资料的键，
#: 是探索期的临时名，**本来就不该出现在资料库里** ⇒ 不该当成「键名对不上」报出来。
_PLACEHOLDER = re.compile(r"^field(-\d+)?$")
KNOWN_ALIASES = {
    "phone": "phone_number", "first_name": "username", "last_name": "username",
    "postcode": "postal_code", "street": "address", "email": "email",
}


def load(path):
    src = open(path, encoding="utf-8").read()
    d = {"src": src}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                n = getattr(t, "id", "")
                if n in ("STATES", "FILLS", "SUCCESS_TEXTS", "SUCCESS_URLS", "PROVENANCE", "SITE"):
                    try:
                        d[n] = ast.literal_eval(node.value)
                    except Exception:
                        pass
    return d


def check(path, since=None):
    try:
        d = load(path)
    except Exception as e:
        return [("!!", "读不了：%s" % e)]
    if "STATES" not in d:
        return []
    prov = d.get("PROVENANCE") or {}
    gen = str(prov.get("generated_at") or "")
    if since and gen and gen[:10] < since:
        return []
    if since and not gen:
        mtime = dt.date.fromtimestamp(os.path.getmtime(path)).isoformat()
        if mtime < since:
            return []

    out, states, fills = [], d["STATES"], d.get("FILLS") or {}
    literals = set()
    for f in fills.values():
        for c in (f.get("fallback") or []):
            if isinstance(c, str) and len(c) > 3:
                literals.add(c)
    if "_follow_new_tab" not in d["src"]:
        out.append(("F", "骨架缺「点完跟新 tab」—— CTA 开新 tab 时会整段静默跳过"))
    for st in states:
        nm = st.get("name")
        w = st.get("when")
        if w is None:
            out.append(("E", "组「%s」when=None：无条件执行，落在别的页会乱点" % nm))
            continue
        tc = w.get("text_contains")
        if not tc:
            out.append(("H", "组「%s」只有地址判据（SPA 上地址不变 ⇒ 等于没判据）" % nm))
        for s in (tc or []):
            s = str(s)
            if TRANSIENT.search(s):
                out.append(("C", "组「%s」判据是瞬时文案：%r" % (nm, s[:60])))
            for lit in literals:
                if lit and lit in s:
                    out.append(("D", "组「%s」判据含填进去的值：%r ← %r" % (nm, s[:50], lit[:30])))
                    break
            if len(s.split()) >= 3 and len(s) >= 25:
                out.append(("B", "组「%s」判据像跨元素拼接（%d 字）：%r" % (nm, len(s), s[:60])))
        for step in (st.get("steps") or []):
            tgt = step.get("target") or {}
            for sel in (tgt.get("selectors") or []):
                if "nth-of-type" in str(sel) and str(sel).count(">") >= 4:
                    out.append(("I", "组「%s」有长 nth-of-type 链：%s…" % (nm, str(sel)[:50])))
                    break
    if "strict" in d["src"] and re.search(r'"strict":\s*True', d["src"]):
        out.append(("A", "骨架带 --strict（某些页面上每一步 form 都失败，真站实测）"))
    for k, f in fills.items():
        srcl = str(f.get("source") or "")
        if srcl and not _PLACEHOLDER.match(srcl) and srcl not in LIB_KEYS and srcl not in KNOWN_ALIASES:
            out.append(("G", "FILLS[%r].source=%r 与资料库键名对不上（资料里没有它）" % (k, srcl)))
        elif srcl in KNOWN_ALIASES and srcl != KNOWN_ALIASES[srcl]:
            out.append(("G·提示", "FILLS[%r].source=%r —— 资料库叫 %r（旧骨架会静默掉进池子）"
                        % (k, srcl, KNOWN_ALIASES[srcl])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", default=None)
    ap.add_argument("--since", default=None, help="只扫这天（含）之后生成的，如 2026-09-28")
    ap.add_argument("--all", action="store_true", help="不加时间过滤")
    a = ap.parse_args()
    dirs = a.dirs or ["/company/siteforge/forms/sites", "/company/siteforge/forms-222/sites"]
    since = None if a.all else (a.since or dt.date.today().isoformat())
    total = bad = 0
    for dr in dirs:
        if not os.path.isdir(dr):
            continue
        for fn in sorted(os.listdir(dr)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            p = os.path.join(dr, fn)
            total += 1
            hits = check(p, since)
            if hits:
                bad += 1
                print("\n### %s" % fn)
                for code, msg in hits:
                    print("   [%s] %s" % (code, msg))
    print("\n扫了 %d 份（过滤：%s），有发现的 %d 份"
          % (total, since or "不过滤", bad))


if __name__ == "__main__":
    main()
