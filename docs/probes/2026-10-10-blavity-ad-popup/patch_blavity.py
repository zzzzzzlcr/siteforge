#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 blavity 产物打「广告弹窗」补丁（三处插入，CRLF 原样保留）。

用法: python3 -I patch_blavity.py <src.py> <dst.py>

依据（2026-10-10 真站实测，192.168.1.222:61129）：
  · 弹窗 = `bx-creative-<id>`，z-index 2147483645，340x347，自己弹出来
  · 关闭按钮 = `<button id="bx-close-inside-<id>" data-click="close">`，88x54，可见
  · 同页 6 个 `[data-click="close"]`，5 个是 0x0 隐藏件 ⇒ 不能直接点选择器
"""
import hashlib
import sys

JS_CONST = '''\
#: 广告弹窗的关闭按钮。**唯一稳定的标识是 `data-click="close"`**（2026-10-10 真站点出来的）：
#: 同页那几个 campaign id（`bx-close-inside-1967264` 里的数字）每次换，class 也跟着带 id。
#:
#: 为什么先**标记**再点，而不是直接 `cdp click '[data-click="close"]'`：
#: 真站实测同页有 **6 个**候选（3 个 campaign × inside/outside 两个变体），其中
#: **5 个是 0×0 的隐藏件**，而 `cdp click` 命中多个时点的是**文档顺序第一个**
#: （`--strict` 只把「歧义」判成失败，不看可见性）—— 直接点 = 点空气，
#: 与 afrotech 那个「选中隐藏重复件」是同一种病（siteforge 缺陷单 2026-09-29）。
#: 所以这里用 JS 只做一件事：**挑出可见的那一个并打标记**；动作仍然交给 cdp 的拟人手势，
#: 手拼的只有「是哪一个」这个判断（规格 §5.2 那条禁的是手拼动作，不是手拼判断）。
#:
#: 可见性三条（与 `_DOORBELL_JS` 同口径，另加尺寸门槛）：display/visibility、
#: rect ≥ 8×8、中心点 `elementFromPoint` 落在它自己或它的祖先/后代上
#: （被别的东西盖住的**不算** —— 「只做有把握的那一下」）。
_AD_POPUP_JS = (
    "var old=document.querySelectorAll('[data-af-close]');"
    "for(var j=0;j<old.length;j++)old[j].removeAttribute('data-af-close');"
    "var a=document.querySelectorAll('[data-click=\\"close\\"]');"
    "var hit='';"
    "for(var i=0;i<a.length;i++){var e=a[i],cs;"
    "try{cs=getComputedStyle(e);}catch(x){continue;}"
    "if(cs.display==='none'||cs.visibility==='hidden')continue;"
    "var r=e.getBoundingClientRect();"
    "if(r.width<8||r.height<8)continue;"
    "var x=r.left+r.width/2,y=r.top+r.height/2;"
    "if(x<0||y<0||x>window.innerWidth||y>window.innerHeight)continue;"
    "var at=document.elementFromPoint(x,y);"
    "if(!at||!(at===e||e.contains(at)||at.contains(e)))continue;"
    "e.setAttribute('data-af-close','1');"
    "hit=JSON.stringify({id:String(e.id||''),x:Math.round(r.x),y:Math.round(r.y),"
    "w:Math.round(r.width),h:Math.round(r.height)});break;}"
    "return hit;"
)


'''

METHOD = '''\
    def _clear_ad_popup(self, why=""):
        """把**广告弹窗**点掉（`data-click="close"` 那一族）。

        为什么要有它（2026-10-10 真站实测，运营报的「偶尔出现的广告弹窗」）：blavity
        文章页挂着一套 `bx-` 前缀的广告，其中 `bx-creative-1967264` 是**盖住正文的弹窗**
        （z-index 2147483645、340×347、页面开着过几分钟自己弹出来），关掉它的就是
        `<button id="bx-close-inside-1967264" data-click="close">`。

        为什么不能并进 `_clear_obstructions` 那条 consent 路：门铃（`_DOORBELL_JS`）只认
        `cookie|consent|gdpr|privacy` 这几个词，判官（`cdp observe` 的 obstructions）只认
        consent 类容器 —— 这个弹窗**两边都认不出**（实测：弹窗盖着的时候门铃不响）。
        它和 Quantcast 是**同一种病**（挡路的浮层让下一步点到它身上，而 cdp 照样回 ok），
        只是不在那套词汇里。

        做法：`_AD_POPUP_JS` 挑出可见的那个并打标记 → cdp 点标记 → 再探一次看还在不在
        （「没做成不许说做成」：cdp 回 ok 只说明命令发出去了）。挑不出 → **静默返回**，
        这是「页面上没有弹窗」的正常情况，不是失败。

        ⚠️ 只在**主帧**找：真站实测这个弹窗就在主帧。子帧里另有 GPT/Clipcentric 的广告位，
        它们的 `data-click` 值是**点击跳转链**（不是 `close`），别去碰。
        """
        raw = str(self._ev(_AD_POPUP_JS) or "").strip().strip('"').strip("'").strip()
        if not raw:
            return                      # 没有可见的弹窗 —— 静默是对的（不响=白问一句）
        out = self._action_cdp("click", "--selector", '[data-af-close="1"]')
        still = str(self._ev(_AD_POPUP_JS) or "").strip().strip('"').strip("'").strip()
        self.log.info("[%s] %s把广告弹窗点掉了（%s）：%s；点完再看：%s", self.cid,
                      ("%s之后 " % why) if why else "开跑前 ", raw[:80],
                      "看起来成了" if _ok(out) else "**没成**",
                      "还在（%s）—— 下一步动手前会再试一次" % still[:60] if still else "没了")
'''

CALL = '''\
        # ★ 2026-10-10：先清**广告弹窗**（`data-click="close"`）。它不是 consent 类，
        # 门铃和判官都认不出（见 `_clear_ad_popup` 的 docstring）；放在这里是因为
        # 三个调用点（开跑前 / 每次导航后 / 每步动手前）都从这里过。
        self._clear_ad_popup(why=why)
'''


def main(src_path, dst_path):
    raw = open(src_path, "rb").read()
    text = raw.decode("utf-8")
    crlf = "\r\n"
    assert crlf in text, "expected CRLF line endings"
    n_crlf = text.count(crlf)

    def to_crlf(block):
        return block.replace("\n", crlf)

    # ── 锚点 1：模块级常量，插在 _DOORBELL_JS 之后、_CONSENT_ACCEPT_RE 注释之前 ──
    a1 = "#: 「接受」那一族（用来在弹层里挑该点哪个按钮）"
    # ── 锚点 2：方法，插在 _clear_obstructions 定义之前 ──
    a2 = '    def _clear_obstructions(self, why=""):'
    # ── 锚点 3：调用点，插在 _clear_obstructions 里第一句之前 ──
    a3 = "        raw = self._ev(_DOORBELL_JS)"

    for name, anchor in (("const", a1), ("method", a2), ("call", a3)):
        n = text.count(anchor)
        assert n == 1, "anchor %s matched %d times (need exactly 1)" % (name, n)

    text = text.replace(a1, to_crlf(JS_CONST) + a1, 1)
    text = text.replace(a2, to_crlf(METHOD) + a2, 1)
    text = text.replace(a3, to_crlf(CALL) + a3, 1)

    out = text.encode("utf-8")
    open(dst_path, "wb").write(out)

    print("src bytes=%d  dst bytes=%d  (+%d)" % (len(raw), len(out), len(out) - len(raw)))
    print("crlf src=%d dst=%d (%s)" % (n_crlf, text.count(crlf),
                                       "CRLF preserved" if text.count(crlf) == n_crlf + 3 * 0 + (len(to_crlf(JS_CONST + METHOD + CALL)) - len(JS_CONST + METHOD + CALL)) else "CHECK"))
    print("src sha256=%s" % hashlib.sha256(raw).hexdigest())
    print("dst sha256=%s" % hashlib.sha256(out).hexdigest())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
