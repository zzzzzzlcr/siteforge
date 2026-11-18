#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 blavity 产物加「Quantcast US Privacy 首访弹层」清障（三处插入，CRLF 原样保留）。

用法: python3 patch_privacy.py <src.py> <dst.py>
"""
import sys

SRC, DST = sys.argv[1], sys.argv[2]
text = open(SRC, encoding="utf-8", newline="").read()
eol = "\r\n" if "\r\n" in text else "\n"


def L(*lines):
    return eol.join(lines) + eol


CONST_BLOCK = L(
    "#: ★ 2026-10-20：Quantcast 的 **US Privacy 首访弹层**（\"Do Not Process My Personal",
    "#: Information\"）—— 它不是 cookie 横幅：`observe` 把它归成 `kind: \"overlay\"`，",
    "#: 门铃的 cookie|consent|gdpr|privacy 词表也认不出（`qc-cmp-cleanslate` 不含这些词，",
    "#: 正文前 200 字里也没有）。它的唯一关闭件是右上角那个 X：",
    "#: `<button class=\"qc-usp-close-icon\" aria-label=\"Close\">`（2026-10-20 真站实测",
    "#: 38×38、`dismiss_selector_unique: true`）。点掉之后还会跟一个",
    "#: 「Your privacy choices have been saved」确认框，唯一按钮是",
    "#: `<button aria-label=\"Close success modal\">OK</button>`（无 id、文字 OK 也不在",
    "#: `_CONSENT_ACCEPT_RE` 里）。两个都得点，少一个就还挡着。",
    "#:",
    "#: 为什么单独一条（不并进通用 consent 路）：通用路的三道门都对不上 ——",
    "#:   ① 门铃 `_DOORBELL_JS` 对第一层**不响**（实测返回空串）；",
    "#:   ② 判官给的是 `kind: \"overlay\"`，而 `_consent_banner_selector` 只认 `cookie-banner`；",
    "#:   ③ `_CONSENT_BUTTON_JS` 只认「接受词 + 稳定 id」，X 与 OK 都没有。",
    "#: 这不是判据错，是这一层本来就**不在** consent 那条语义里 —— 它是一次性隐私选择。",
    "#: 定向到 `.qc-cmp-cleanslate` 这一族，**不**把 `close` 加进通用接受词。",
    "_PRIVACY_CHOICE_JS = (",
    "    \"var root=document.querySelector('div.qc-cmp-cleanslate');\"",
    "    \"if(!root) return '';\"",
    "    \"var vis=function(e){var r=e.getBoundingClientRect();\"",
    "    \"return r.width>=8&&r.height>=8&&r.bottom>0&&r.top<window.innerHeight;};\"",
    "    \"var b=root.querySelector('button.qc-usp-close-icon');\"",
    "    \"if(b&&vis(b)) return 'close';\"",
    "    \"var ok=root.querySelector('button[aria-label=\\\"Close success modal\\\"]');\"",
    "    \"if(ok&&vis(ok)) return 'ok';\"",
    "    \"return '';\"",
    ")",
    "",
)

METHOD_BLOCK = L(
    "",
    "    def _clear_privacy_choice(self, why=\"\"):",
    "        \"\"\"清 **Quantcast US Privacy 首访弹层**（`div.qc-cmp-cleanslate`）。",
    "",
    "        为什么要有它（2026-10-20 真站实测，运营报的「好像有新的弹窗要点」）：这一层是",
    "        「Do Not Process My Personal Information」的一次性隐私选择，不是 cookie 横幅 ——",
    "        通用 consent 路**三道门全对不上**：",
    "          ① 门铃 `_DOORBELL_JS` 不响：`qc-cmp-cleanslate` 不含 cookie|consent|gdpr|privacy，",
    "             正文前 200 字里也没有这些词（真站实测返回空串）；",
    "          ② 判官 `observe` 把它归成 `kind: \"overlay\"`，而 `_consent_banner_selector`",
    "             只认 `cookie-banner`；",
    "          ③ 按钮挑选只认「接受词 + 稳定 id」：X 是 `aria-label=\"Close\"`（无 id），",
    "             点掉后的 OK 是 `aria-label=\"Close success modal\"`（无 id、文字 OK 也不在接受词里）。",
    "        结果就是弹层一直挡着，下一步点在它身上、cdp 照样回 ok。",
    "",
    "        两跳，每跳点完**再探一次复核**（cdp 回 ok 只说明命令发出去了）：",
    "          ① 右上角 X `button.qc-usp-close-icon` —— 关掉选择层（实测点它 = 保存当前选择，",
    "             随后会弹「Your privacy choices have been saved」确认框）；",
    "          ② 确认框的 `button[aria-label=\"Close success modal\"]`（OK）—— 关掉它。",
    "        两跳都只在 `div.qc-cmp-cleanslate` 这个容器里、且**可见**时才点；挑不出 ⇒ 静默返回。",
    "        ⚠️ 定向到这一族，**不**把 `close` 加进通用接受词（普通弹窗的 Close 不能点）。",
    "        \"\"\"",
    "        for _ in range(3):",
    "            raw = str(self._ev(_PRIVACY_CHOICE_JS) or \"\").strip().strip('\"').strip(\"'\").strip()",
    "            if raw == \"close\":",
    "                selector = \"button.qc-usp-close-icon\"",
    "            elif raw == \"ok\":",
    "                selector = 'button[aria-label=\"Close success modal\"]'",
    "            else:",
    "                return",
    "            out = self._action_cdp(\"click\", \"--selector\", selector)",
    "            time.sleep(0.6)",
    "            again = str(self._ev(_PRIVACY_CHOICE_JS) or \"\").strip().strip('\"').strip(\"'\").strip()",
    "            self.log.info(\"[%s] %s把隐私选择弹层点了（%s）：%s；点完再看：%s\", self.cid,",
    "                          (\"%s之后 \" % why) if why else \"开跑前 \", selector,",
    "                          \"看起来成了\" if _ok(out) else \"**没成**\",",
    "                          \"还在（%s）\" % again if again else \"没了\")",
    "",
)

ANCHOR_CONST = "#: 「接受」那一族（用来在弹层里挑该点哪个按钮）。**只认动作词**，不认 `close`："
ANCHOR_METHOD = "    def _clear_obstructions(self, why=\"\"):"
ANCHOR_CALL = "        # ★ 2026-10-10：先清**广告弹窗**（`data-click=\"close\"`）。它不是 consent 类，"

CALL_BLOCK = L(
    "        # ★ 2026-10-20：先清 **Quantcast 的 US Privacy 首访弹层**（见 `_clear_privacy_choice`）。",
    "        # 它是全屏 z-index 2147483647 的一次性隐私选择，通用 consent 路三道门都认不出。",
    "        self._clear_privacy_choice(why=why)",
)

for anchor in (ANCHOR_CONST, ANCHOR_METHOD, ANCHOR_CALL):
    assert text.count(anchor) == 1, "anchor not unique: %r (%d)" % (anchor, text.count(anchor))

text = text.replace(ANCHOR_CONST, CONST_BLOCK + ANCHOR_CONST, 1)
text = text.replace(ANCHOR_METHOD, METHOD_BLOCK + ANCHOR_METHOD, 1)
text = text.replace(ANCHOR_CALL, CALL_BLOCK + ANCHOR_CALL, 1)

open(DST, "w", encoding="utf-8", newline="").write(text)
print("wrote", DST, "bytes", len(text.encode()), "CRLF", text.count("\r\n"))
