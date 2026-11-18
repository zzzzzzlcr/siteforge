#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 japansdates 的 `_close_cookie` 修成「可见件 + 复核」，并在每一步前也清一次。

背景（2026-10-20 soulfuldate 5a4f738d）：页面在姓名屏才挂出 `.cookie-main`
（Accept all / Reject all），盖住 NEXT。旧 `_close_cookie` 的
`self.cdp.click(sel)` 对「选择器不存在」**不抛异常** ⇒ 第一个选择器就把函数
return 掉了，`button[class*=cookie]` 与文案兜底根本没走到；而且它只在开跑前
调一次，后来才挂出来的弹层也轮不到。

用法: python3 patch_japansdates_cookie.py <src.py> <dst.py>
"""
import sys

SRC, DST = sys.argv[1], sys.argv[2]
text = open(SRC, encoding="utf-8", newline="").read()
eol = "\r\n" if "\r\n" in text else "\n"


def L(*lines):
    return eol.join(lines) + eol


OLD_SELECTORS = L(
    "COOKIE_SELECTORS = (",
    '    \'[data-testid="cookie-popup-common-agree-and-close"]\',',
    '    "button[class*=cookie]",',
    ")",
    'COOKIE_TEXT_RE = re.compile(r"agree\\s*&?\\s*close|got\\s*it|accept|allow", re.I)',
)
NEW_SELECTORS = L(
    "COOKIE_SELECTORS = (",
    '    \'[data-testid="cookie-popup-common-agree-and-close"]\',',
    "    #: 变体：容器 `cookie-popup-consent` + 按钮 `cookie-main__btn`（无 testid）。",
    "    #: 2026-10-15 charmingtalks / 2026-10-20 soulfuldate 真站实测到的都是这一套。",
    '    ".cookie-popup-consent button",',
    '    "button[class*=cookie]",',
    ")",
    'COOKIE_TEXT_RE = re.compile(r"agree\\s*&?\\s*close|got\\s*it|accept|allow", re.I)',
    "",
    "#: 「同意弹层还在不在」的判据容器 —— 关完**必须复核**。",
    "#: `CDPHelper.click()` 对「元素不存在」**不抛异常**，所以「点了」与「点到了」是两件事。",
    'COOKIE_OVERLAYS = (".cookie-popup-consent", ".cookie-container", ".cookie-main",',
    '                   ".cookie-popup", "#onetrust-consent-sdk", ".ot-sdk-container")',
)

OLD_METHOD = L(
    "    def _close_cookie(self):",
    "        for sel in COOKIE_SELECTORS:",
    "            try:",
    "                self.cdp.click(sel)",
    "                self._dly(0.4, 1.0)",
    "                return True",
    "            except Exception:",
    "                continue",
    "        # 文案兜底：'Agree & close' / 'got it' / 'accept'",
    '        mk = "mk%d" % random.randint(1000, 9999)',
    "        js = (\"var bs=document.querySelectorAll('button,a,[role=button]');\"",
    "              \"for(var i=0;i<bs.length;i++){var e=bs[i];var t=(e.innerText||'').trim();\"",
    "              \"if(/%s/i.test(t)){e.setAttribute('data-%s','1');return 'found';}}\"",
    "              \"return 'none';\" % (COOKIE_TEXT_RE.pattern, mk))",
    '        if "found" not in (self._ev(js) or ""):',
    "            return False",
    "        sel = '[data-%s=\"1\"]' % mk",
    "        try:",
    "            self.cdp.click(sel)",
    "        except Exception:",
    "            pass",
    "        self._clear(sel, mk)",
    "        return True",
    "",
)
NEW_METHOD = L(
    "    _MARK_VISIBLE_CSS_JS = (",
    "        \"function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);\"",
    "        \"if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}\"",
    "        \"return true;}\"",
    "        \"var es=document.querySelectorAll('%s');\"",
    "        \"for(var i=0;i<es.length;i++){var r=es[i].getBoundingClientRect();\"",
    "        \"if(r.width>0&&r.height>0&&v(es[i])){es[i].setAttribute('data-%s','1');\"",
    "        \"return 'found';}}return 'none';\")",
    "",
    "    def _cookie_gone(self):",
    '        """同意弹层是不是**真的**不在了（可见的大块容器）。"""',
    "        js = (\"function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);\"",
    "              \"if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}\"",
    "              \"return true;}\"",
    "              \"var ss=%s;\"",
    "              \"for(var i=0;i<ss.length;i++){var es=document.querySelectorAll(ss[i]);\"",
    "              \"for(var j=0;j<es.length;j++){var r=es[j].getBoundingClientRect();\"",
    "              \"if(r.width>60&&r.height>40&&v(es[j]))return 'here';}}\"",
    "              \"return 'gone';\") % json.dumps(list(COOKIE_OVERLAYS))",
    '        return "here" not in (self._ev(js) or "")',
    "",
    "    def _click_visible_css(self, sel):",
    '        """按 CSS 选择器点**可见的那一颗**。返回是否找到了可见件（点没点成由调用方复核）。"""',
    '        mk = "mk%d" % random.randint(1000, 9999)',
    '        if "found" not in (self._ev(self._MARK_VISIBLE_CSS_JS % (sel, mk)) or ""):',
    "            return False",
    "        msel = '[data-%s=\"1\"]' % mk",
    "        try:",
    "            self.cdp.click(msel)",
    "        except Exception:",
    "            pass",
    "        self._clear(msel, mk)",
    "        return True",
    "",
    "    def _close_cookie(self):",
    '        """关同意弹层：挑**可见的那一颗**点，关完**复核弹层真的没了**才算关掉。',
    "",
    "        2026-10-15 charmingtalks 真站定位（同一天合成页测试也红在这条上）：",
    "        旧写法是 `self.cdp.click(sel)` 不抛异常就 `return True` —— 可",
    "        `CDPHelper.click()` 对「元素不存在」**不抛异常**，于是「选择器没命中」",
    "        被当成「点成功了」，文案兜底永远轮不到。弹层留着（`position:fixed`），",
    "        坐标点击全被它吃掉：选项能被点成 checked，页面却纹丝不动。",
    "",
    "        2026-10-20 soulfuldate 5a4f738d：弹层是**姓名屏才挂出来**的（`.cookie-main`），",
    "        所以除了开跑前，`run()` 每一步之前也调一次；`_cookie_gone()` 先做廉价检查，",
    "        没有弹层时只多一次 eval。",
    '        """',
    "        if self._cookie_gone():",
    "            return False",
    "        for sel in COOKIE_SELECTORS:",
    "            if not self._click_visible_css(sel):",
    "                continue",
    "            self._dly(0.5, 1.2)",
    "            if self._cookie_gone():",
    "                return True",
    "        # 文案兜底：'Agree & close' / 'Accept all' / 'got it'",
    '        mk = "mk%d" % random.randint(1000, 9999)',
    "        js = (\"var bs=document.querySelectorAll('button,a,[role=button]');\"",
    "              \"for(var i=0;i<bs.length;i++){var e=bs[i],r=e.getBoundingClientRect();\"",
    "              \"var t=(e.innerText||'').trim();\"",
    "              \"if(r.width>0&&r.height>0&&/%s/i.test(t)){e.setAttribute('data-%s','1');\"",
    "              \"return 'found';}}return 'none';\" % (COOKIE_TEXT_RE.pattern, mk))",
    '        if "found" not in (self._ev(js) or ""):',
    "            return self._cookie_gone()",
    "        sel = '[data-%s=\"1\"]' % mk",
    "        try:",
    "            self.cdp.click(sel)",
    "        except Exception:",
    "            pass",
    "        self._clear(sel, mk)",
    "        self._dly(0.5, 1.2)",
    "        return self._cookie_gone()",
    "",
)

OLD_LOOP = L(
    "                self._legal_guard()",
    "                page = self.read()",
)
NEW_LOOP = L(
    "                self._legal_guard()",
    "                self._close_cookie()",
    "                page = self.read()",
)

for name, old in (("selectors", OLD_SELECTORS), ("method", OLD_METHOD), ("loop", OLD_LOOP)):
    n = text.count(old)
    assert n == 1, "%s anchor count %d" % (name, n)

text = text.replace(OLD_SELECTORS, NEW_SELECTORS, 1)
text = text.replace(OLD_METHOD, NEW_METHOD, 1)
text = text.replace(OLD_LOOP, NEW_LOOP, 1)

open(DST, "w", encoding="utf-8", newline="").write(text)
print("wrote", DST, "bytes", len(text.encode()), "CRLF", text.count("\r\n"))
