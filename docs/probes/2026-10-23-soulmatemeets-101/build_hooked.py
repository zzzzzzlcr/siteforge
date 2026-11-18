#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `agent/template.py` 里 `_POPUP_HOOKS` 的**前两行**（同意弹层 / 同意条）
原样搬进 japansdates 平台脚本，生成 `japansdates_HOOKED.py`。

为什么是「原样搬」而不是重写：判据只写一份是这一族的硬规矩（template.py 的注释里
反复写着）。这里从 `_POPUP_MACHINERY_SRC` 的源码里**按 AST 取段**，把
`_CONSENT_ROOTS_JS` / `_VIS_JS` / `_CONSENT_MODAL_JS` / `_CONSENT_HOOK_JS` /
`_MARK_CONSENT_BY_TEXT_JS` 五个赋值的**原文**贴进去，再拼一段只问这两条的探针。

基线：`japansdates_PATCHED.py`（已经含 10-23 的两行白名单，sha 35444610…）。
产物保留 CRLF（基线是 CRLF；搬进来的段落统一转成 CRLF）。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, "/company/siteforge")
import agent.template as template                       # noqa: E402

BASE = HERE / "japansdates_PATCHED.py"
OUT = HERE / "japansdates_HOOKED.py"

CONSTS = ("_CONSENT_ROOTS_JS", "_VIS_JS", "_CONSENT_MODAL_JS",
          "_CONSENT_HOOK_JS", "_MARK_CONSENT_BY_TEXT_JS")


def extract_consts() -> dict:
    src = template._POPUP_MACHINERY_SRC
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id in CONSTS:
                seg = ast.get_source_segment(src, node)
                assert seg, tgt.id
                out[tgt.id] = seg
    missing = [n for n in CONSTS if n not in out]
    assert not missing, missing
    return out


PROBE = '''
#: 一次问完这两条（次序 = 优先级：满视口的最硬，先问；同意条其次）。答复
#: `"<钩子名>|<#id|TEXT>|<按钮文字>"`，空串 = 页面上没有这类东西（正常情况）。
_CONSENT_PROBE_JS = (
    _CONSENT_ROOTS_JS + _VIS_JS
    + "var hit=(function(all,vis,anchor){" + _CONSENT_MODAL_JS + "})(all,vis,'');"
      "if(hit)return '盖满视口的同意弹层|'+hit;"
      "var hit2=(function(all,vis,anchor){" + _CONSENT_HOOK_JS + "})(all,vis,'');"
      "if(hit2)return '同意条|'+hit2;"
      "return '';"
)
'''

HEADER = '''#: ★ 2026-10-23（方案 2）：通用**同意钩子** —— 从 siteforge 产物骨架
#: `agent/template.py` 的 `_POPUP_HOOKS` 里**原样搬来前两行**（满视口同意弹层 / 同意条）。
#: 判据是「同意语境 + 接受/拒绝/关闭按钮」，不是具体选择器 ⇒ 浮层换名字不用再改表。
#: 只搬这两条：通用模态 / 市场抽屉 / 广告 creative 是站点专用判据，不搬。
#: ⚠️ 下面的 JS 是 `_POPUP_MACHINERY_SRC` 里的原文（`build_hooked.py` 按 AST 取段）。
#: **本平台实测后加了两道窄闸**（真页面量到的，不加上去会误点）：
#:   ① 同意条只认 `position: fixed|sticky` 的容器 —— 页脚里带着 "Privacy Policy / Cookie
#:      Policy"，而 "Arbitration Agreement" 命中 btnW 的 `agree` 子串 ⇒ 静态页脚会被
#:      当成同意条点掉（2026-10-23 真站 A/B 量到）。真浮层 CSS 是 `position: fixed`。
#:   ② 按文字打标记只认**可见**的那一颗 —— 页面上有隐藏的同文字按钮时，原来会标记到它，
#:      真按钮反而没点（模板里那句已知 caveat，在本站的 A/B 里真发生了）。
#:   两条都回 `agent/template.py` 该不该一起补，是另一个决定（会动所有产物）。
'''

METHOD = '''    def _ev_text(self, js):
        """`_ev` 的回话可能是**一层 JSON 字符串**（`cdp eval` 会包）—— 解到裸串再给判据用。

        与 `read()` 里那段两种客户端的处理同源：只认字符串，解不开就原样返回。
        """
        raw = str(self._ev(js) or "").strip()
        for _ in range(2):
            if raw.startswith('"') and raw.endswith('"'):
                try:
                    raw = str(json.loads(raw))
                except ValueError:
                    break
            else:
                break
        return raw

    def _dismiss_consent(self):
        """问一遍**通用同意钩子**（满视口同意弹层 → 同意条），点掉并**复核**。永不抛。

        2026-10-23（方案 2）：判据与 siteforge 产物骨架 `_POPUP_HOOKS` 前两行**同一份**
        （原文见上面 `_CONSENT_*_JS`）。与下面 `_close_cookie()` 的分工：
          · 这里按「同意语境 + 决定按钮」**认**，换一个浮层名字也认得出；
          · `_close_cookie()` 的老 selector 表留作兜底（调用处：`_dismiss_consent() or
            _close_cookie()`）；
          · 点完**再问一次**才算成 —— `cdp click` 回 ok 只说明「选择器命中 1 个」。
        读炸 / 点不动都只记一行 INFO，**绝不往上抛**：弹层是环境噪声，不该把这一趟判死。
        """
        try:
            raw = self._ev_text(_CONSENT_PROBE_JS)
            if not raw:
                return False                      # 页面上没有这类东西 —— 正常
            parts = raw.split("|", 2)
            if len(parts) != 3:
                self.log.info("[%s] 同意弹层探针的答复形状不认识（%r）—— 当它没答",
                              self.cid, raw[:60])
                return False
            who, how, text = parts
            if how == "TEXT" and not text.strip():
                self.log.info("[%s] 「%s」答的是 TEXT 而文字是空的 —— 不猜、不点",
                              self.cid, who)
                return False
            if how == "TEXT":
                marked = self._ev_text(_MARK_CONSENT_BY_TEXT_JS % json.dumps(text))
                if "marked" not in marked:
                    self.log.info("[%s] 「%s」那颗按钮（%r）按文字没标记上 —— 不猜",
                                  self.cid, who, text[:30])
                    return False
                sel = '[data-sf-consent="1"]'
            else:
                sel = how
            try:
                self.cdp.click(sel)
            finally:
                if how == "TEXT":
                    self._ev("var a=document.getElementsByTagName('*');"
                             "for(var i=0;i<a.length;i++){if(a[i].removeAttribute)"
                             "a[i].removeAttribute('data-sf-consent');}return 'clean';")
            still = self._ev_text(_CONSENT_PROBE_JS)
            if still:
                self.log.info("[%s] 点了「%s」（%s）之后它**还在**（复核答复 %r）—— 这一下没关掉",
                              self.cid, who, sel, still[:80])
                return False
            self.log.info("[%s] 点掉了「%s」（%s，%r）", self.cid, who, sel, text[:30])
            return True
        except Exception as exc:                   # noqa: BLE001
            self.log.info("[%s] 看/点同意弹层这一下没成（%s: %s）—— 当它没有，照旧往下走",
                          self.cid, type(exc).__name__, exc)
            return False
'''


def crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def tune_read_js(src: str) -> str:
    """`read()` 多带回一格 `checkedOptions`（visible 选项里 input 已选中的 testid）。

    为什么：多选屏（checkbox + "Next"）选完一题后会冒出 continue；`pick_option` 是随机挑的、
    不排除已选中的 —— 再挑到那一颗就把勾去掉（2026-10-23 真站 step 14→15 的现场）。
    """
    old = "  var opt=[],lbl=[],ins=[];"
    new = "  var opt=[],chk=[],lbl=[],ins=[];"
    assert src.count(old) == 1, src.count(old)
    src = src.replace(old, new)
    old2 = ("      if(/%s/.test(t)&&t.indexOf('-text')<0&&opt.indexOf(t)<0)\n"
            "        opt.push(t);}")
    new2 = ("      if(/%s/.test(t)&&t.indexOf('-text')<0&&opt.indexOf(t)<0){\n"
            "        opt.push(t);\n"
            "        var ci=e.querySelector('input');if(ci&&ci.checked)chk.push(t);}}")
    assert src.count(old2) == 1, src.count(old2)
    src = src.replace(old2, new2)
    old3 = "    head:bt.substring(0,120),options:opt,labels:lbl,inputs:ins,"
    new3 = "    head:bt.substring(0,120),options:opt,checkedOptions:chk,labels:lbl,inputs:ins,"
    assert src.count(old3) == 1, src.count(old3)
    return src.replace(old3, new3)


def tune_run_multiselect(src: str) -> str:
    """多选屏：已选中 ≥1 且 continue 可见 ⇒ **先按 Next**；挑选项时排除已选中的。"""
    anchor = (
        '                if not page.get("legalChecked") and self._check_terms():\n'
        '                    idle = 0\n'
        '                    idle_since = None\n'
        '                    busy_since = None\n'
        '                    self.log.info("[%s] step %d: 勾选条款", self.cid, step)\n'
        '                    continue\n')
    assert src.count(anchor) == 1, src.count(anchor)
    insert = (
        '\n'
        '                # ★ 2026-10-23（真站 23d247da）：多选屏（checkbox "(up to 3 options)"）\n'
        '                # 选完会冒出 continue（文字 "Next"）。这时**先按 Next**，不要再随机点一个：\n'
        '                # `pick_option` 不排除已选中的，随机点到那一颗会把勾去掉，页面随即收回\n'
        '                # Next ⇒ 脚本在「同题重复 → 等 continue」里耗到 45 秒收摊\n'
        '                # （生产单 26800520 / 26800522 / 26800681 的现场）。\n'
        '                if page.get("nextVisible") and page.get("checkedOptions"):\n'
        '                    self.click_visible(CONTINUE_BTN)\n'
        '                    idle = 0\n'
        '                    idle_since = None\n'
        '                    busy_since = None\n'
        '                    self.log.info("[%s] step %d [%s]: 已选中 %d 个，按 continue",\n'
        '                                  self.cid, step, page.get("step", "?"),\n'
        '                                  len(page.get("checkedOptions") or []))\n'
        '                    continue\n')
    src = src.replace(anchor, anchor + insert)

    old = '                pick = None if stuck else pick_option(page.get("options", []))'
    new = ('                checked = page.get("checkedOptions") or []\n'
           '                pick = None if stuck else pick_option(\n'
           '                    [o for o in page.get("options", []) if o not in checked])')
    assert src.count(old) == 1, src.count(old)
    src = src.replace(old, new)

    #: ★「同一题」的键：原来用 `head[:40]`，而 `head` 是 **body.innerText 的前 120 字** ——
    #: 这平台每一屏都从同一行 chrome 开头（"1 2 3 4 Your profile Preferences Finish up …"），
    #: 40 字全落在这行里 ⇒ **不同的问题被当成同一题**。连着两屏 Preferences 就 repeat=2、
    #: 判成「多选题」→ 从此不再点选项 → 45 秒收摊（生产 26800520/522/681 的真因）。
    #: 改成按**选项集合**认题（没有选项才退回 head）。
    #: ★ 2026-10-23（真站量到）：这平台**点击会漏**——第一下点完 input 已 checked，页面却不推进；
    #: 再点一下（有时换一个选项）就过了。原来的闸是 `repeat >= 2`，两下没成就不再点
    #: （短路径 15:33 那趟：yes 一下、no 一下都不动 → 直接收摊）。放宽到 6：漏点还能再试。
    old_rep = '                stuck = self._repeat >= 2'
    new_rep = ('                #: 2026-10-23：两点就收摊太急 —— 真站上「点一下没推进」很常见\n'
               '                #: （input 已 checked、页面不动；再点一下就过）。给到 6 次再判死。\n'
               '                stuck = self._repeat >= 6')
    assert src.count(old_rep) == 1, src.count(old_rep)
    src = src.replace(old_rep, new_rep)

    old2 = ('                key = "%s|%s" % (page.get("step", ""), (page.get("head", "") or "")[:40])')
    new2 = ('                sig = page.get("options") or [\n'
            '                    (x[0] if isinstance(x, (list, tuple)) else x)\n'
            '                    for x in (page.get("labels") or [])]\n'
            '                #: ★ 2026-10-23（真站 23d247da）：原来用 `head[:40]` 认「同一题」，\n'
            '                #: 而 head 是 body.innerText 的前 120 字 —— 每一屏都从同一行 chrome\n'
            '                #: （"1 2 3 4 Your profile Preferences Finish up …"）开头，40 字全落在\n'
            '                #: 那行里 ⇒ **不同的问题被当成同一题**：连着两屏 Preferences 就 repeat=2、\n'
            '                #: 判成多选、从此不再点选项（生产单 26800520/522/681 的真因）。\n'
            '                #: 按**选项集合**认题；没有选项（纯文字/输入屏）才退回 head。\n'
            '                key = "%s|%s" % (page.get("step", ""), "|".join(str(x) for x in sig)\n'
            '                                 or (page.get("head", "") or "")[:40])')
    assert src.count(old2) == 1, src.count(old2)
    return src.replace(old2, new2)


def tune_bar_js(seg: str) -> str:
    """同意条：只认 `position: fixed|sticky` 的容器（真站：静态页脚会被误认）。"""
    old = '    "var btnW=/(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;"'
    new = (old + "\n"
           '    #: ★ 2026-10-23（本平台实测）：静态页脚的 innerText 里有 "Privacy Policy / Cookie\n'
           '    #: Policy"，而 "Arbitration Agreement" 命中 btnW 的 `agree` 子串 ⇒ 会被当成同意条。\n'
           '    #: 真浮层（`cookie-popup-variation-consent`）的 CSS 是 `position: fixed` ⇒ 加这道闸。\n'
           '    "var overlay=function(e){var cs=null;try{cs=getComputedStyle(e);}catch(err){}"\n'
           '    "return !!cs&&(cs.position===\'fixed\'||cs.position===\'sticky\');};"')
    assert seg.count(old) == 1, seg.count(old)
    seg = seg.replace(old, new)
    #: 2026-10-23：本站变体的接受键文字是 **"Ok with all"**（testid 仍叫 accept-all）——
    #: 模板的 btnW 不认识它（只剩 "Decline all" 能匹配）；补上这个短语。
    old_btn = '"var btnW=/(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;"'
    new_btn = ('"var btnW=/(accept|agree|allow|got it|ok with|close|reject|decline|deny|'
               '同意|接受|关闭)/i;"')
    assert seg.count(old_btn) == 1, seg.count(old_btn)
    seg = seg.replace(old_btn, new_btn)
    old2 = '    "  if(!boxW.test(boxLabel(box))||!vis(box)) continue;"'
    new2 = '    "  if(!boxW.test(boxLabel(box))||!vis(box)||!overlay(box)) continue;"'
    assert seg.count(old2) == 1, seg.count(old2)
    return seg.replace(old2, new2)


def tune_modal_js(seg: str) -> str:
    """满视口同意弹层：只认**固定定位**的容器，且「同意语境」只看 id/class/aria-label。

    真站量到的两处误伤（2026-10-23）：
      · 整页/页脚这种静态容器也 ≥80% 视口，innerText 里带着 "Privacy Policy / Cookie Policy"，
        里面那颗 "Arbitration Agreement" 命中 `agree` ⇒ 被当成同意弹层，每轮都去点页脚链接；
      · 条款屏（`step-content-item-CheckboxTerms`）正文含 privacy/terms，被当成同意弹层，
        钩子去点站点的 `#terms-agree`「I Accept」——那是**业务控件**，不该由同意钩子点。
    判据收窄：容器必须 `position: fixed|sticky`；ctx 只看 id/class/aria-label（不看 innerText）；
    另保留「强同意短语」那条（`Allow All` / `Reject All` / `全部接受` 那种，Fides 靠它）。
    """
    old_full = ('    "var full=function(e){var r=e.getBoundingClientRect();"\n'
                '    "return r.width>=window.innerWidth*0.8&&r.height>=window.innerHeight*0.8;};"')
    add = ("\n"
           '    #: ★ 2026-10-23（本平台实测）：静态整页/页脚、条款屏都会被 80% 视口判据命中 ——\n'
           '    #: 真弹层是浮层（fixed）；ctx 只认容器自己的 id/class/aria-label，不看 innerText。\n'
           '    "var boxIdLabel=function(e){return String(e.id||\'\')+\' \'+String(e.className||\'\')"\n'
           '    "+\' \'+String((e.getAttribute&&e.getAttribute(\'aria-label\'))||\'\');};"\n'
           '    "var overlay=function(e){var cs=null;try{cs=getComputedStyle(e);}catch(err){}"\n'
           '    "return !!cs&&(cs.position===\'fixed\'||cs.position===\'sticky\');};"')
    assert seg.count(old_full) == 1, seg.count(old_full)
    seg = seg.replace(old_full, old_full + add)
    old_vis = '    "  if(!vis(box)||!full(box))continue;"'
    new_vis = '    "  if(!vis(box)||!full(box)||!overlay(box))continue;"'
    assert seg.count(old_vis) == 1, seg.count(old_vis)
    seg = seg.replace(old_vis, new_vis)
    old_ctx = '    "  var ctx=ctxW.test(boxLabel(box));"'
    new_ctx = '    "  var ctx=ctxW.test(boxIdLabel(box));"'
    assert seg.count(old_ctx) == 1, seg.count(old_ctx)
    return seg.replace(old_ctx, new_ctx)


def tune_mark_js(seg: str) -> str:
    """按文字打标记：只认**可见**的叶子（隐藏件 / 同文字的容器都会抢在真按钮前面）。"""
    old = "    + _CONSENT_ROOTS_JS +"
    new = "    + _CONSENT_ROOTS_JS + _VIS_JS +"
    assert seg.count(old) == 1, seg.count(old)
    seg = seg.replace(old, new)
    old2 = '    "if(t===want){hit=b;break;}}"\n'
    new2 = '    "if(t===want&&vis(b)){hit=b;break;}}"\n'
    assert seg.count(old2) == 1, seg.count(old2)
    seg = seg.replace(old2, new2)
    #: ★ 2026-10-23（本平台实测）：容器 div 的 innerText 与按钮一样（"Accept all"），
    #: 不挡容器就会先标记到容器、点下去落在空白处 —— 与钩子那条同一道「div/span 只认叶子」。
    old3 = ('    "if(tag!==\'button\'&&tag!==\'a\'&&tag!==\'input\'&&tag!==\'div\''
            '&&tag!==\'span\')continue;"')
    new3 = (old3 + "\n"
            '    "if((tag===\'div\'||tag===\'span\')&&b.children&&b.children.length){var tc=false;"\n'
            '    "for(var q=0;q<b.children.length;q++){var ch=b.children[q];"\n'
            '    "if(String(ch.innerText||ch.value||\'\').trim()){tc=true;break;}}"\n'
            '    "if(tc)continue;}"')
    assert seg.count(old3) == 1, seg.count(old3)
    return seg.replace(old3, new3)


def main() -> int:
    base = BASE.read_bytes().decode("utf-8")
    assert "\r\n" in base
    consts = extract_consts()
    consts["_CONSENT_HOOK_JS"] = tune_bar_js(consts["_CONSENT_HOOK_JS"])
    consts["_CONSENT_MODAL_JS"] = tune_modal_js(consts["_CONSENT_MODAL_JS"])
    consts["_MARK_CONSENT_BY_TEXT_JS"] = tune_mark_js(consts["_MARK_CONSENT_BY_TEXT_JS"])

    block = (crlf(HEADER)
             + "".join(crlf(consts[n]) + "\r\n\r\n" for n in CONSTS)
             + crlf(PROBE + "\n"))

    anchor = ('COOKIE_OVERLAYS = (".cookie-popup-consent", ".cookie-container", ".cookie-main",\r\n'
              '                   ".cookie-popup", ".cookie-popup-variation-consent",\r\n'
              '                   "#onetrust-consent-sdk", ".ot-sdk-container")\r\n')
    assert base.count(anchor) == 1, base.count(anchor)
    src = base.replace(anchor, anchor + "\r\n" + block)

    # 方法插在 _close_cookie 之后（_legal_guard 之前）
    anchor2 = "    def _legal_guard(self):"
    assert src.count(anchor2) == 1
    src = src.replace(anchor2, crlf(METHOD + "\n") + "\r\n" + anchor2)

    # run() 的两个调用点：新钩子先问，老的 selector 表兜底
    old1 = "            self._close_cookie()\r\n            self._dly(0.5, 1.2)"
    new1 = ("            self._dismiss_consent() or self._close_cookie()\r\n"
            "            self._dly(0.5, 1.2)")
    assert src.count(old1) == 1
    src = src.replace(old1, new1)
    old2 = "                self._close_cookie()\r\n                page = self.read()"
    new2 = ("                self._dismiss_consent() or self._close_cookie()\r\n"
            "                page = self.read()")
    assert src.count(old2) == 1
    src = src.replace(old2, new2)

    # 多选题（checkboxes + Next）那两处（读回 checkedOptions / 选中就先按 Next）
    src = crlf(tune_read_js(src.replace("\r\n", "\n")))
    src = crlf(tune_run_multiselect(src.replace("\r\n", "\n")))

    b = src.encode("utf-8")
    OUT.write_bytes(b)
    print("OUT", OUT.name, "bytes", len(b), "sha256", hashlib.sha256(b).hexdigest())
    print("CRLF", b.count(b"\r\n"), "LF", b.count(b"\n"),
          "lone-LF", b.count(b"\n") - b.count(b"\r\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
