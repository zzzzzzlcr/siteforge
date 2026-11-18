#!/usr/bin/env python3
"""Site script for japansdates.com — same wizard platform as goldenagesouls,
different option mechanics.

Why a dedicated script (2026-09-20)
-----------------------------------
The platform is the one goldenagesouls.py already drives: the DOM holds
`step-1 … step-21` (+ `step-content-item-<Type>`) and one `continue-button`
per step. 43 of its 90 `data-testid` values match goldenagesouls' vocabulary.

Two things differ, and they are why this is not a route-table one-liner:

1. Options are `label` (+ `input[type=checkbox]`), not `button`. Thisromance.py
   — the obvious candidate — clicks the age step with `_click_btn(age)`, which
   only queries `document.querySelectorAll('button')`; here that finds nothing
   and the run spins until its 80-step budget dies. Its button texts are also
   matched case-sensitively ("Next"/"Continue") while this site renders
   'continue' / 'ok, next' / 'got it' / 'go on'.
2. A second option family exists for the checkbox questions:
   `option-checkbox__item_*` (item_retired / item_healthcare / item_fun-yes …).

Measured on the live site (headless, 2026-09-20)
------------------------------------------------
Funnel: age → who to meet → mood/misc Preferences questions → gender →
more Preferences → … → 名字/邮箱/密码 → 条款 → 提交. Step *types* seen live are
coarser than the static DOM suggested (`Basic` / `Preferences` / `ChooseGender`),
so the loop never branches on the type: it reads what is visible and acts.

Three live traps, each cost a run
---------------------------------
1. **Every step has its own `continue-button` / `legal-checkbox`.** A CSS
   selector hit the first (hidden) one → `nextVisible` was always false.
2. **The same `data-testid` repeats across steps.** `cdp click '[data-testid=…]'`
   lands on the first match, usually the one in a hidden step → the click did
   nothing and the run stalled on "Do you have children?". Every click/fill now
   goes through the *visible* match (`click_visible` / `fill_visible`).
3. **Non-current steps stay in `body.innerText`.** innerText only excludes
   `display:none`, so a step mid-transition still "shows" its old question while
   nothing is clickable — that is a transition, not a dead end. Hence a time
   budget (`IDLE_TIMEOUT`) instead of an iteration count.

✅ 成功落点**已实测**（2026-09-20，headless 真站）：本站是 SPA —— **路径永远是
`/land/sp/<id>/`**，状态写在 **hash 里（base64）**。实测序列：
`c3RlcDE=`(step1) … `c3RlcDIx`(step21) → **`d2l6YXJkMg==`(wizard2)**。
走到 wizard2 的那一轮，姓名/邮箱/密码都填了、条款勾了、提交也过了 ——
所以 **wizard2 就是注册完成后的引导区 = 成功态**（用户 2026-09-20 的判断是对的；
我先前只看路径，把它误报成了 max_steps，是**假阴性**）。

`is_success` 因此同时看两条：路径标识（同族兼容，见 `SUCCESS_URL_MARKERS`）
**和** hash 状态名（`hash_state()` 解 base64，见 `HASH_SUCCESS_STATES`）。
判据以外一律 `max_steps` —— **读不出来就如实说读不出来**，绝不报 `submitted`。

Honesty rules (borrowed from goldenagesouls.py:52-56)
-----------------------------------------------------
Never report success for work not done. Success is a URL-level signal only —
page text was how the old scripts fooled themselves.
"""

import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

NAMES = ["James", "John", "Robert", "Michael", "David", "William",
         "Richard", "Joseph", "Thomas", "Daniel", "Paul", "Mark"]
EMAIL_DOMAINS = ["outlook.com", "gmail.com", "yahoo.com", "hotmail.com"]

#: 可点的选项 testid 前缀（本站 + goldenagesouls 同族词汇）
OPTION_TESTID_RE = (r"^(age-|gender-|pref-gender-|option-|interest-|goal-)")
#: 点了这单就废 —— "Under 18" 与它的 checkbox 版本
DISQUALIFYING = {
    "age-under-18",
    "option-checkbox__item_age-no",
    "option-under-18",
    "option-not-eligible",
}
#: 选项标签里绝不许点的文本（导航 / 法律 / 退出 / 按钮类）—— sparkydates 的
#: 「坐标点击漂移落进页脚法律链接，整条漏斗跳出」就是这类东西。
#: 按钮文案也算进来：`continue` / `Sign up` 该走 continue-button 那条路，
#: 被当成"选项"点掉会让"答了题"的计数骗人。
DENY_TEXT_RE = re.compile(
    r"under\s*18|not\s*eligib|no\s*thanks|skip|decline|sign\s*in|sign\s*up|"
    r"log\s*in|register|terms|privacy|arbitration|about\s*us|policy|cookie|"
    r"community|rules|continue|next|got\s*it|go\s*on|submit|agree",
    re.I)
#: 标题/问句不是选项（"How's your mood today?" / "I am a"）
HEADING_TEXT_RE = re.compile(
    r"[?？]|^(i\s*am|i'?m|select|choose|what|who|how|do\s*you|are\s*you|is\s*there|"
    r"which|find\s+your)\b", re.I)
#: 长度超过这个的 label 当标题，不当选项
MAX_OPTION_TEXT_LEN = 40
#: 年龄档白名单 —— 文本兜底时优先点这些
AGE_BUCKETS = ("18-24", "25-34", "35-44", "45-54", "55-64", "65+")

#: 成功落点。同族（goldenagesouls / thisromance）是这几个；**本站待真站确认** ——
#: 确认前只认这些标识，其余一律如实报 max_steps。
SUCCESS_URL_MARKERS = ("/wizard", "/main-page", "/auth/login", "action=reg")

COOKIE_SELECTORS = (
    #: ★ 2026-10-23（soulmatemeets.com/land/sp/23d247da）：新变体
    #: `cookie-popup-variation-consent` 的接受键带 testid；容器 class 也不在下面那张表里
    #: （`.cookie-popup` 是整词匹配，匹配不到 `cookie-popup-variation-consent`）。
    '[data-testid="cookie-popup-consent-accept-all"]',
    '[data-testid="cookie-popup-common-agree-and-close"]',
    #: 变体：容器 `cookie-popup-consent` + 按钮 `cookie-main__btn`（无 testid）。
    #: 2026-10-15 charmingtalks / 2026-10-20 soulfuldate 真站实测到的都是这一套。
    ".cookie-popup-consent button",
    "button[class*=cookie]",
)
COOKIE_TEXT_RE = re.compile(r"agree\s*&?\s*close|got\s*it|accept|allow", re.I)

#: 「同意弹层还在不在」的判据容器 —— 关完**必须复核**。
#: `CDPHelper.click()` 对「元素不存在」**不抛异常**，所以「点了」与「点到了」是两件事。
COOKIE_OVERLAYS = (".cookie-popup-consent", ".cookie-container", ".cookie-main",
                   ".cookie-popup", ".cookie-popup-variation-consent",
                   "#onetrust-consent-sdk", ".ot-sdk-container")

#: ★ 2026-10-23（方案 2）：通用**同意钩子** —— 从 siteforge 产物骨架
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
_CONSENT_ROOTS_JS = (
    "var roots=[document],ri=0,all=[];"
    "while(ri<roots.length){var r=roots[ri++];"
    "var els=(r.getElementsByTagName?r.getElementsByTagName('*'):r.querySelectorAll('*'));"
    "for(var j=0;j<els.length;j++){all.push(els[j]);"
    "if(els[j].shadowRoot){roots.push(els[j].shadowRoot);}}}"
    #: 这条判据是**两段 JS 共用**的（找按钮那段、判「还在不在」那段）—— 一处一张嘴。
    "var boxLabel=function(e){return String(e.id||'')+' '+String(e.className||'')"
    "+' '+String((e.getAttribute&&e.getAttribute('aria-label'))||'')"
    "+' '+String(e.innerText||'').replace(/\\s+/g,' ').slice(0,200);};"
)

_VIS_JS = (
    "var vis=function(e){var r=e.getBoundingClientRect();"
    "if(!(r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight))return false;"
    #: ★ 2026-10-17（终审 Ruling 28-①）：**矩形过了才问 display / visibility**（顺序有意：
    #: 只在「矩形这一关已经过了」的那些元素上取 computedStyle —— 几千个元素逐个取太贵，
    #: 而这一格问的正是「这一颗看得见吗」）。
    #: 为什么补这一段：`visibility:hidden` 的关闭键**矩形照样有面积** ⇒ 原来被当成可见
    #: （真站实测 `docs/superpowers/plans/2026-10-16-弹窗与遮挡-能力进骨架-实测.md` §1.3：
    #: wildnutrition 那一屏**两颗 `visibility:hidden` 的关闭键**都进了候选、差点被点）。
    #: ⚠️ `getComputedStyle` 可能**根本不存在**（`tests/popup_probe_driver.js` 的假 window 就没有）
    #: ⇒ 必须 try/catch 住：抛出去 = 整段探针炸 = 十条钩子全哑（`_COVER_JS` 那处同一个包法）。
    "var cs=null;try{cs=getComputedStyle(e);}catch(err){}"
    "if(cs){var d=String(cs.display||''),v=String(cs.visibility||'');"
    "if(d==='none'||v==='hidden'||v==='collapse')return false;}"
    "return true;};"
)

_CONSENT_MODAL_JS = (
    "var decW=/(reject|decline|deny|accept|agree|allow|同意|接受|拒绝|不同意)/i;"
    "var badW=/(reject|decline|deny|拒绝|不同意)/i;"
    "var ctxW=/(cookie|consent|gdpr|privacy|tracking|同意|隐私|跟踪)/i;"
    "var strongW=/(reject|accept|allow|deny|decline)\\s+(all|cookies?)|全部接受|全部拒绝/i;"
    "var full=function(e){var r=e.getBoundingClientRect();"
    "return r.width>=window.innerWidth*0.8&&r.height>=window.innerHeight*0.8;};"
    #: ★ 2026-10-23（本平台实测）：静态整页/页脚、条款屏都会被 80% 视口判据命中 ——
    #: 真弹层是浮层（fixed）；ctx 只认容器自己的 id/class/aria-label，不看 innerText。
    "var boxIdLabel=function(e){return String(e.id||'')+' '+String(e.className||'')"
    "+' '+String((e.getAttribute&&e.getAttribute('aria-label'))||'');};"
    "var overlay=function(e){var cs=null;try{cs=getComputedStyle(e);}catch(err){}"
    "return !!cs&&(cs.position==='fixed'||cs.position==='sticky');};"
    "var out='',outT='';"
    "for(var i=0;i<all.length;i++){var box=all[i];"
    #: ★ 2026-10-19（真站 compareinsulation）：`full()` 会命中 `body`/`html` —— 它们是
    #: 满视口的**根节点**，不是「盖满视口的弹层」。排掉它们，这一行才会按设计卡在
    #: 真正容器的尺寸闸（那站 `#cookiescript_injected` 只占视口高 28%），落到第 2 行。
    "  var bt=String(box.tagName||'').toLowerCase();"
    "  if(bt==='body'||bt==='html')continue;"
    "  if(!vis(box)||!full(box)||!overlay(box))continue;"
    "  var ctx=ctxW.test(boxIdLabel(box));"
    "  var inner=(box.getElementsByTagName?box.getElementsByTagName('*'):box.querySelectorAll('*'));"
    "  for(var j=0;j<inner.length;j++){var b=inner[j],t=String(b.tagName||'').toLowerCase();"
    "    var ctl=(t==='button'||t==='a'||t==='input');"
    "    if(!ctl&&t!=='div'&&t!=='span')continue;"
    #: ★ 2026-10-19（真站 compareinsulation）：wrapper 的合并文字也可能很短
    #: （`#cookiescript_buttons` = "ACCEPT ALL\nDECLINE ALL"）—— 新加的 div/span 只认叶子，
    #: 子元素里带文字的是容器，跳过（与「同意条」那条同一道闸）。
    "    if(!ctl&&b.children&&b.children.length){var tc=false;"
    "      for(var q=0;q<b.children.length;q++){var ch=b.children[q];"
    "        if(String(ch.innerText||ch.value||'').trim()){tc=true;break;}}"
    "      if(tc)continue;}"
    "    if(!vis(b))continue;"
    "    var x=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "    if(!ctl&&x.length>40)continue;"
    "    if(!decW.test(x))continue;"
    "    if(!ctx&&!strongW.test(x))continue;"
    "    var sel=(b.id?('#'+b.id):'TEXT');"
    "    if(badW.test(x))return sel+'|'+x.slice(0,40);"
    "    if(!out){out=sel;outT=x;}}"
    "}"
    "return out?(out+'|'+outT.slice(0,40)):'';"
)

_CONSENT_HOOK_JS = (
    # ⚠️ 用 `getElementsByTagName('*')` + 属性自检，**不走**「按选择器一步查」的那种写法 ——
    # 产品里凡是要按选择器找元素的地方一律避开它（`tests/test_template.py` 的
    # 「动作全走 cdp」那条把它钉死了；而这里本来就只是**读**，`matches()` 一样能读）。
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    "var btnW=/(accept|agree|allow|got it|ok with|close|reject|decline|deny|同意|接受|关闭)/i;"
    #: ★ 2026-10-23（本平台实测）：静态页脚的 innerText 里有 "Privacy Policy / Cookie
    #: Policy"，而 "Arbitration Agreement" 命中 btnW 的 `agree` 子串 ⇒ 会被当成同意条。
    #: 真浮层（`cookie-popup-variation-consent`）的 CSS 是 `position: fixed` ⇒ 加这道闸。
    "var overlay=function(e){var cs=null;try{cs=getComputedStyle(e);}catch(err){}"
    "return !!cs&&(cs.position==='fixed'||cs.position==='sticky');};"
    "for(var i=0;i<all.length;i++){"
    "  var box=all[i];"
    "  if(!boxW.test(boxLabel(box))||!vis(box)||!overlay(box)) continue;"
    "  var inner=(box.getElementsByTagName?box.getElementsByTagName('*'):box.querySelectorAll('*'));"
    "  for(var j=0;j<inner.length;j++){"
    "    var b=inner[j], tag=String(b.tagName||'').toLowerCase();"
    "    var ctl=(tag==='button'||tag==='a'||tag==='input');"
    "    if(!ctl&&tag!=='div'&&tag!=='span') continue;"
    #: ★ 2026-10-19（真站 compareinsulation）：`#cookiescript_buttons` 这个 **wrapper** 的
    #: 合并文字是 `"ACCEPT ALL\nDECLINE ALL"`（22 字）—— ≤40 的短文本闸拦不住它，钩子会
    #: 先挑中 wrapper、点下去是点空气。新加的 div/span **只认叶子**：子元素里带文字的
    #: 是容器，跳过；真正的 accept/reject（或它们里面那颗 span）才留下。
    "    if(!ctl&&b.children&&b.children.length){var tc=false;"
    "      for(var q=0;q<b.children.length;q++){var ch=b.children[q];"
    "        if(String(ch.innerText||ch.value||'').trim()){tc=true;break;}}"
    "      if(tc) continue;}"
    #: ★ 2026-09-28：**按钮也得是可见的** —— 藏起来的那颗（有的是给读屏用的）点不到，
    #: 点了也是白点（`cdp click` 会回错误 ⇒ 白搭一步）。
    "    if(!vis(b)) continue;"
    "    var t=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "    if(!ctl&&t.length>40) continue;"
    "    if(!btnW.test(t)) continue;"
    #: 有稳定地址 ⇒ 直接用；只有文字 ⇒ 交回一句「按文字点」（调用方打标记再点）
    "    return (b.id?('#'+b.id):'TEXT')+'|'+t.slice(0,40);"
    "  }"
    "}"
)

_MARK_CONSENT_BY_TEXT_JS = (
    "var want=%s;"
    + _CONSENT_ROOTS_JS + _VIS_JS +
    "var hit=null;"
    "for(var i=0;i<all.length;i++){var b=all[i],tag=String(b.tagName||'').toLowerCase();"
    "if(tag!=='button'&&tag!=='a'&&tag!=='input'&&tag!=='div'&&tag!=='span')continue;"
    "if((tag==='div'||tag==='span')&&b.children&&b.children.length){var tc=false;"
    "for(var q=0;q<b.children.length;q++){var ch=b.children[q];"
    "if(String(ch.innerText||ch.value||'').trim()){tc=true;break;}}"
    "if(tc)continue;}"
    "var t=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "if(t===want&&vis(b)){hit=b;break;}}"
    "if(!hit)return 'none';"
    "for(var k=0;k<all.length;k++){if(all[k].removeAttribute)all[k].removeAttribute('data-sf-consent');}"
    "hit.setAttribute('data-sf-consent','1');return 'marked';"
)


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


CONTINUE_BTN = "continue-button"
LEGAL_CHECKBOX = "legal-checkbox"

#: 页脚法律链接拦截（从 thisromance.py 借：cdp 坐标点击会漂到页脚，
#: 命中法律链接直接跳出注册漏斗）。幂等，每轮重确认。
LEGAL_GUARD_JS = """
(function(){
  if(window.__jdGuard)return 'ok';window.__jdGuard=1;
  document.addEventListener('click',function(e){
    var a=e.target&&e.target.closest?e.target.closest('a'):null;
    if(a&&/community-pages|\\/rules|\\/privacy|\\/terms|\\/legal|arbitration/i.test(a.href||'')){
      e.preventDefault();e.stopPropagation();window.__jdBlocked=(window.__jdBlocked||0)+1;}
  },true);
  return 'injected';
})()
"""

#: 「页面在忙」—— 不能和「没事干」共用计数（goldenagesouls.py:85-99 的教训）
BUSY_MARKERS = ("Almost done", "Processing your", "Setting up your profile",
                "Just a moment", "getting things ready", "Hold on", "Loading")
BUSY_TIMEOUT = 90          # 已知处理态最多等这么久（秒）
IDLE_LIMIT = 6             # 真的没什么可点，连续这么多次……
#: ……**并且**这么久（秒）才收摊。只看次数会误杀慢过渡：过渡期页面整块不可见，
#: 而 body.innerText 仍带着旧文案（innerText 只排除 display:none），
#: goldenagesouls 2026-09-17 生产单 25997728 就是 9 秒过渡被当成"没事干"。
IDLE_TIMEOUT = 45

#: 一次往返读完整页状态：可见选项（testid + 文本）、待填输入、continue 是否可见、
#: 当前步骤类型。可见性必须**沿祖先链上溯**。
#:
#: 选项前缀**直接插值 `OPTION_TESTID_RE`** —— 别在这里再抄一份，抄了就会漂。
READ_PAGE_JS = """
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var i,e,r,t;
  // ⚠️ 作用域 = **所有可见的 step-content-item 容器**（取并集）。
  // 两个实测教训（2026-09-20 真站）：
  //   · 「CSS 可见」不等于「属于当前这步」—— 别的步的元素也会可见，随机点到
  //     别题的选项 → 点了没反应 → 卡死在 "Do you have children?"
  //   · 但也不能只取「第一个可见的」—— 落地页同时可见 Basic + AgeLegalText +
  //     ChooseAge 三个容器，第一个是介绍块，里面没有选项 → 第 1 步就卡死
  var roots=[],ses=document.querySelectorAll('[data-testid^="step-content-item-"]');
  for(i=0;i<ses.length;i++){if(vis(ses[i]))roots.push(ses[i]);}
  if(!roots.length){var ss=document.querySelectorAll('.step');
    for(i=0;i<ss.length;i++){if(vis(ss[i]))roots.push(ss[i]);}}
  if(!roots.length)roots=[document];
  var step='';
  for(i=0;i<roots.length;i++){if(roots[i].getAttribute){
    var st=roots[i].getAttribute('data-testid');if(st)step+=(step?'+':'')+st;}}
  var opt=[],chk=[],lbl=[],ins=[];
  for(var ri=0;ri<roots.length;ri++){
    var root=roots[ri];
    var es=root.querySelectorAll('[data-testid]');
    for(i=0;i<es.length;i++){e=es[i];t=e.getAttribute('data-testid');r=e.getBoundingClientRect();
      if(r.width<=0||r.height<=0||!vis(e))continue;
      if(/%s/.test(t)&&t.indexOf('-text')<0&&opt.indexOf(t)<0){
        opt.push(t);
        var ci=e.querySelector('input');if(ci&&ci.checked)chk.push(t);}}
    // 只认**真正包着控件**的 label（真站：选项 = label>input[checkbox]）。
    // ⚠️ 不加这条，文本兜底会把提示语当选项 —— 真站实测：
    // "We appreciate your experience!" 被连点 44 次。
    var ls=root.querySelectorAll('label');
    for(i=0;i<ls.length;i++){e=ls[i];r=e.getBoundingClientRect();
      if(r.width<=0||r.height<=0||!vis(e))continue;
      if(!e.querySelector('input'))continue;
      var tx=(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();
      if(tx)lbl.push([tx,e.getAttribute('data-testid')||'']);}
    var is=root.querySelectorAll('input');
    for(i=0;i<is.length;i++){e=is[i];r=e.getBoundingClientRect();
      if(r.width<=0||r.height<=0||!vis(e))continue;
      t=e.getAttribute('data-testid')||e.name||'';
      if(t&&t.indexOf('legal')<0&&t.indexOf('checkbox')<0)ins.push([t,e.value||'']);}
  }
  // ⚠️ 每一步都有自己的 continue-button / legal-checkbox。在可见作用域里找，
  // 找不到再退回全文档（有的步把按钮放在容器外）—— 但**必须**是可见的那个。
  function firstVisible(scope,sel){var ns=scope.querySelectorAll(sel);
    for(var k=0;k<ns.length;k++){if(vis(ns[k]))return ns[k];}return null;}
  function firstInAny(sel){for(var q=0;q<roots.length;q++){
    var f=firstVisible(roots[q],sel);if(f)return f;}return firstVisible(document,sel);}
  var cb=firstInAny('[data-testid="continue-button"]');
  var lcb=firstInAny('[data-testid="legal-checkbox"]');
  var bt=document.body?document.body.innerText.replace(/\\s+/g,' ').trim():'';
  return JSON.stringify({url:location.href,step:step,
    head:bt.substring(0,120),options:opt,checkedOptions:chk,labels:lbl,inputs:ins,
    nextVisible:!!cb,
    legalChecked:!!(lcb&&(lcb.checked||lcb.getAttribute('aria-checked')==='true'))});
})()
""" % OPTION_TESTID_RE


# ---------- 纯逻辑（tests/test_japansdates.py 钉的就是这几个）----------

def pick_option(options):
    """从可见选项 testid 里挑一个；不合格的一个都不给。"""
    usable = [o for o in (options or []) if o and o not in DISQUALIFYING]
    return random.choice(usable) if usable else None


def pick_option_by_text(labels):
    """文本兜底：testid 缺失时的选项点击。

    只认「短、不是标题/问句、不在黑名单」的标签；年龄档优先。
    黑名单是硬约束 —— 点中 Under 18 这单就废，点中页脚法律链接整条漏斗就跳出去。
    """
    safe = []
    for item in (labels or []):
        text = (item[0] if isinstance(item, (list, tuple)) else item) or ""
        text = text.strip()
        if not text or len(text) > MAX_OPTION_TEXT_LEN:
            continue
        if DENY_TEXT_RE.search(text) or HEADING_TEXT_RE.search(text):
            continue
        safe.append(text)
    if not safe:
        return None
    buckets = [t for t in safe if t.strip() in AGE_BUCKETS]
    return random.choice(buckets or safe)


def value_for(testid, first, email):
    """按 testid 决定填什么。legal-checkbox 不是填的（要点击）。"""
    t = (testid or "").lower()
    if "legal" in t or "checkbox" in t:
        return ""
    if "firstname" in t or "name" in t:
        return first
    if "email" in t:
        return email
    if "password" in t:
        return "Pass{}!".format(random.randint(100, 999))
    if "birth" in t or "date" in t:
        return "05-14-1985"
    if "phone" in t or "tel" in t:
        return "5551234567"
    return ""


#: hash 里出现这些状态名 = 注册完成（本站把状态 base64 写在 hash 里，
#: 路径永远不变）。实测：`d2l6YXJkMg==` = "wizard2"，出现在姓名/邮箱/密码/条款
#: 都过完之后（2026-09-20 m7 那轮）。
HASH_SUCCESS_STATES = ("wizard", "wizard2", "main-page", "feed")


def hash_state(url):
    """URL hash 里解出来的站点状态名（`#d2l6YXJkMg==` → "wizard2"）；没有返回 ""。

    纯诊断用 —— 日志里带上它，排查时一眼能看出走到哪一步了。
    """
    frag = (url or "").split("#", 1)[1] if "#" in (url or "") else ""
    frag = frag.strip()
    if not frag:
        return ""
    try:
        import base64
        pad = frag + "=" * (-len(frag) % 4)
        out = base64.b64decode(pad, validate=True).decode("utf-8", "ignore")
    except Exception:
        return ""
    return out if out and out.isprintable() else ""


def is_success(url):
    """成功**只看 URL**（路径标识 或 hash 里的状态名）。页面文案不算 ——
    旧脚本的假成功就是这么来的。"""
    if any(m in (url or "") for m in SUCCESS_URL_MARKERS):
        return True
    state = hash_state(url).lower()
    return any(state.startswith(s) for s in HASH_SUCCESS_STATES)


class JapanSDatesFiller:
    #: 同 testid 在多步里**重复出现**（21 步各一份），选择器点击/填写命中的是第一个
    #: （多半在隐藏步里）→ 点了等于没点。真站实测：`option-checkbox__item_lonely`
    #: 点不动、"Do you have children?" 卡死；换成可见的那个立刻前进。
    #: 两个占位：testid、标记名。
    _VISIBLE_JS = (
        "var es=document.querySelectorAll('[data-testid=\"%s\"]');"
        "function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
        "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
        "return true;}"
        "for(var i=0;i<es.length;i++){var r=es[i].getBoundingClientRect();"
        "if(r.width>0&&r.height>0&&v(es[i])){es[i].setAttribute('data-%s','1');"
        "return 'found';}}return 'none';")

    def __init__(self, ws_url, form_file, correlation_id, task_id=""):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id or correlation_id.split("_")[0]
        self.log = setup_logger("japansdates")
        self._last_url = ""
        self._filled = set()
        self._last_q = ""      # 上一轮那题（多选题 / 原地打转靠它识别）
        self._repeat = 0

    # ---------- plumbing ----------

    def _dly(self, a=0.4, b=1.6):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step):
        u = self.cdp.eval("window.location.href", "")
        if isinstance(u, str) and u.strip():
            self._last_url = u.strip().strip('"').strip("'")
        return report_url(self.cdp, self.tid, step, self.log)

    def _ev(self, js):
        """⚠️ 必须包 IIFE：`cdp eval` 把 JS 当**脚本**跑，裸 `return` 直接
        `SyntaxError: Illegal return statement`（2026-09-20 实测踩过）。
        thisromance.py 的 `_ev()` 就是这个作用。"""
        return self.cdp.eval("(function(){%s})()" % js, "")

    def read(self):
        raw = self.cdp.eval(READ_PAGE_JS, "")
        # 仓库里有**两套 CDP 客户端**：`forms/common.CDPHelper` 回原始文本，
        # `form_executor/subprocess_cdp_client` 那类直接回解好码的对象
        # （2026-09-20 全量 pytest 实测被后者顶掉过：eval('1+1') 回 int）。两种都认。
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, str):          # cdp eval 会再包一层 JSON
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    # ---------- 只跟**可见的**那个元素打交道 ----------

    def _mark_visible(self, testid):
        """给可见的那个同 testid 元素打标记；返回 (选择器, 标记名) 或 (None, None)。"""
        mk = "mk%d" % random.randint(1000, 9999)
        if "found" not in (self._ev(self._VISIBLE_JS % (testid, mk)) or ""):
            return None, None
        return '[data-%s="1"]' % mk, mk

    def _clear(self, sel, mk):
        if sel:
            self._ev('var b=document.querySelector(\'%s\');'
                     'if(b)b.removeAttribute("data-%s");' % (sel, mk))

    def click_visible(self, testid):
        sel, mk = self._mark_visible(testid)
        if not sel:
            return False
        ok = True
        try:
            self.cdp.click(sel)
        except Exception:
            ok = False
        self._clear(sel, mk)
        return ok

    def fill_visible(self, testid, value):
        sel, mk = self._mark_visible(testid)
        if not sel:
            return False
        ok = True
        try:
            self.cdp.form(sel, value=value)
        except Exception:
            ok = False
        self._clear(sel, mk)
        return ok

    def click_text(self, text):
        """按可见文本点选项（只走兜底路径，且只认包着控件的 label）。"""
        mk = "mk%d" % random.randint(1000, 9999)
        js = ("var ls=document.querySelectorAll('label');"
              "function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
              "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
              "return true;}"
              "for(var i=0;i<ls.length;i++){var e=ls[i];var r=e.getBoundingClientRect();"
              "var t=(e.innerText||'').replace(/\\s+/g,' ').trim();"
              "if(t==='%s'&&r.width>0&&r.height>0&&v(e)&&e.querySelector('input')){"
              "e.setAttribute('data-%s','1');return 'found';}}return 'none';"
              % (text.replace("'", "\\'"), mk))
        if "found" not in (self._ev(js) or ""):
            return False
        sel = '[data-%s="1"]' % mk
        ok = True
        try:
            self.cdp.click(sel)
        except Exception:
            ok = False
        self._clear(sel, mk)
        return ok

    #: 在可见作用域里找目标，并看它**里面有没有 input**（自定义勾选框）。
    #: 「找得到」用可见性判据；「点得动」要看有没有 input —— 这平台把交互绑在
    #: 零尺寸的 input 上，坐标点 label/样式盒子是点不动的（2026-09-20 真站实测：
    #: legal-checkbox 与 option-checkbox__item_* 都是这个形状）。
    _FIND_JS = (
        "function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
        "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
        "return true;}"
        "var es=document.querySelectorAll('[data-testid=\"%s\"]');"
        "for(var i=0;i<es.length;i++){var e=es[i],r=e.getBoundingClientRect();"
        "if(r.width<=0||r.height<=0||!vis(e))continue;"
        "e.setAttribute('data-%s','1');"
        "return e.querySelector('input')?'ctrl':'plain';}return 'none';")

    def click_option(self, testid):
        """点一个选项。返回是否真的点成了。

        自定义勾选框要点里面的 input；普通按钮走坐标点击。两条都做**读回验证** ——
        这个平台"点了没反应"是常态（真站实测：连点两个选项，页面纹丝不动）。
        """
        mk = "mk%d" % random.randint(1000, 9999)
        kind = self._ev(self._FIND_JS % (testid, mk)) or ""
        if "none" in kind:
            return False
        sel = '[data-%s="1"]' % mk
        try:
            if "ctrl" in kind:
                # ① 正规做法：cdp form --check（作用于选中元素里的控件）
                try:
                    self.cdp.form(sel, check="true")
                except Exception:
                    pass
                if self._inner_checked(sel):
                    return True
                # ② 兜底：JS 点里面的 input（真站实测有效）
                self._ev("var e=document.querySelector('%s');"
                         "var i=e?e.querySelector('input'):null;if(i)i.click();" % sel)
                if self._inner_checked(sel):
                    return True
            # ③ 普通元素：坐标点击
            try:
                self.cdp.click(sel)
                return True
            except Exception:
                return False
        finally:
            self._clear(sel, mk)

    def _inner_checked(self, sel):
        r = self._ev("var e=document.querySelector('%s');if(!e)return 'no';"
                     "var i=e.querySelector('input')||e;"
                     "return (i.checked||e.getAttribute('aria-checked')==='true')?'yes':'no';"
                     % sel)
        return "yes" in (r or "")

    _MARK_VISIBLE_CSS_JS = (
        "function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
        "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
        "return true;}"
        "var es=document.querySelectorAll('%s');"
        "for(var i=0;i<es.length;i++){var r=es[i].getBoundingClientRect();"
        "if(r.width>0&&r.height>0&&v(es[i])){es[i].setAttribute('data-%s','1');"
        "return 'found';}}return 'none';")

    def _cookie_gone(self):
        """同意弹层是不是**真的**不在了（可见的大块容器）。"""
        js = ("function v(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
              "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
              "return true;}"
              "var ss=%s;"
              "for(var i=0;i<ss.length;i++){var es=document.querySelectorAll(ss[i]);"
              "for(var j=0;j<es.length;j++){var r=es[j].getBoundingClientRect();"
              "if(r.width>60&&r.height>40&&v(es[j]))return 'here';}}"
              "return 'gone';") % json.dumps(list(COOKIE_OVERLAYS))
        return "here" not in (self._ev(js) or "")

    def _click_visible_css(self, sel):
        """按 CSS 选择器点**可见的那一颗**。返回是否找到了可见件（点没点成由调用方复核）。"""
        mk = "mk%d" % random.randint(1000, 9999)
        if "found" not in (self._ev(self._MARK_VISIBLE_CSS_JS % (sel, mk)) or ""):
            return False
        msel = '[data-%s="1"]' % mk
        try:
            self.cdp.click(msel)
        except Exception:
            pass
        self._clear(msel, mk)
        return True

    def _close_cookie(self):
        """关同意弹层：挑**可见的那一颗**点，关完**复核弹层真的没了**才算关掉。

        2026-10-15 charmingtalks 真站定位（同一天合成页测试也红在这条上）：
        旧写法是 `self.cdp.click(sel)` 不抛异常就 `return True` —— 可
        `CDPHelper.click()` 对「元素不存在」**不抛异常**，于是「选择器没命中」
        被当成「点成功了」，文案兜底永远轮不到。弹层留着（`position:fixed`），
        坐标点击全被它吃掉：选项能被点成 checked，页面却纹丝不动。

        2026-10-20 soulfuldate 5a4f738d：弹层是**姓名屏才挂出来**的（`.cookie-main`），
        所以除了开跑前，`run()` 每一步之前也调一次；`_cookie_gone()` 先做廉价检查，
        没有弹层时只多一次 eval。
        """
        if self._cookie_gone():
            return False
        for sel in COOKIE_SELECTORS:
            if not self._click_visible_css(sel):
                continue
            self._dly(0.5, 1.2)
            if self._cookie_gone():
                return True
        # 文案兜底：'Agree & close' / 'Accept all' / 'got it'
        mk = "mk%d" % random.randint(1000, 9999)
        js = ("var bs=document.querySelectorAll('button,a,[role=button]');"
              "for(var i=0;i<bs.length;i++){var e=bs[i],r=e.getBoundingClientRect();"
              "var t=(e.innerText||'').trim();"
              "if(r.width>0&&r.height>0&&/%s/i.test(t)){e.setAttribute('data-%s','1');"
              "return 'found';}}return 'none';" % (COOKIE_TEXT_RE.pattern, mk))
        if "found" not in (self._ev(js) or ""):
            return self._cookie_gone()
        sel = '[data-%s="1"]' % mk
        try:
            self.cdp.click(sel)
        except Exception:
            pass
        self._clear(sel, mk)
        self._dly(0.5, 1.2)
        return self._cookie_gone()

    def _ev_text(self, js):
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


    def _legal_guard(self):
        try:
            self._ev(LEGAL_GUARD_JS)
        except Exception:
            pass

    #: 在当前**可见的那一步**里取条款勾选框，打上标记。
    #: 不要求尺寸 —— 真站的 input 是零尺寸（w=0 h=0）的自定义勾选框。
    #: 也不能用全局 querySelector：同 testid 在多步里重复，会拿到隐藏的那个
    #: （2026-09-20 实测：验证语句拿到隐藏的那个，于是"勾上了"永远是假的）。
    _TERMS_MARK_JS = (
        "function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
        "if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}"
        "return true;}"
        # ⚠️ **不许回退到全局查询**：那会点到隐藏步里的勾选框、还自报成功，
        # 于是主循环看到 legalChecked 一直是 false → 死循环到 max_steps
        # （2026-09-20 合成页实测踩过）。可见作用域里没有就如实说没有。
        "var rs=[],ses=document.querySelectorAll('[data-testid^=\"step-content-item-\"]');"
        "for(var i=0;i<ses.length;i++){if(vis(ses[i]))rs.push(ses[i]);}"
        "for(var q=0;q<rs.length;q++){var e=rs[q].querySelector('[data-testid=\"legal-checkbox\"]');"
        "if(e){e.setAttribute('data-%s','1');return 'found';}}"
        "return 'none';")

    def _check_terms(self):
        """把条款勾选框勾上。返回是否**真的**勾上了（读回验证）。

        零尺寸 input 坐标点击无效 → 先 `cdp form --check true`，读回 false 再
        用 JS `.click()`（真站实测有效）。勾不上就如实返回 False，别假装勾了。
        """
        mk = "mk%d" % random.randint(1000, 9999)
        if "found" not in (self._ev(self._TERMS_MARK_JS % mk) or ""):
            return False
        sel = '[data-%s="1"]' % mk
        verify = ("var e=document.querySelector('%s');"
                  "return (e&&(e.checked||e.getAttribute('aria-checked')==='true'))"
                  "?'yes':'no';" % sel)
        try:
            for how in ("form", "js"):
                try:
                    if how == "form":
                        self.cdp.form(sel, check="true")
                    else:
                        self._ev("var e=document.querySelector('%s');"
                                 "if(e)e.click();" % sel)
                except Exception:
                    continue
                if "yes" in (self._ev(verify) or ""):
                    return True
            return False
        finally:
            self._clear(sel, mk)

    # ---------- the loop ----------

    def run(self, max_steps=60):
        try:
            p = self.form_data
            first = (p.get("first_name")
                     or (p.get("username", "").split()[0] if p.get("username") else "")
                     or random.choice(NAMES))
            email = p.get("email") or "{}{}@{}".format(
                first.lower(), random.randint(100, 999), random.choice(EMAIL_DOMAINS))
            self.log.info("[%s] japansdates: %s %s", self.cid, first, email)

            self._dismiss_consent() or self._close_cookie()
            self._dly(0.5, 1.2)

            answered = 0
            idle = 0
            idle_since = None
            busy_since = None
            for step in range(1, max_steps + 1):
                self._dly(0.6, 1.8)
                self._legal_guard()
                self._dismiss_consent() or self._close_cookie()
                page = self.read()
                if page is None:
                    idle += 1
                    self.log.info("[%s] step %d: page unreadable (%d)", self.cid, step, idle)
                    if idle >= IDLE_LIMIT:
                        self.log.error("[%s] 页面连续 %d 次读不出来 —— 如实报失败",
                                       self.cid, idle)
                        self._rpt("max_steps")
                        return False
                    continue

                url = page.get("url", "")
                if is_success(url):
                    self.log.info("[%s] SUCCESS: %s（站点状态 %s）", self.cid, url[:80],
                                  hash_state(url) or "?")
                    self._rpt("success")
                    return True

                # 1) 先填可见的空输入（姓名/邮箱/密码）
                todo = [t for t, v in page.get("inputs", []) if t and not v]
                if todo:
                    tid = todo[0]
                    val = value_for(tid, first, email)
                    if val and (tid, val) not in self._filled:
                        self.fill_visible(tid, val)
                        self._filled.add((tid, val))
                        self.log.info("[%s] step %d [%s]: filled %s", self.cid, step,
                                      page.get("step", "?"), tid)
                        idle = 0
                        idle_since = None
                        busy_since = None
                        continue

                # 2) 条款勾选框（没勾就勾）
                # 真站的 input 是**零尺寸**的自定义勾选框（w=0 h=0，position:absolute），
                # 坐标点击永远点不到；先走 cdp form --check（仓库正规做法），
                # 读回仍是 false 再用 JS `.click()` 兜底（2026-09-20 实测后者有效）。
                if not page.get("legalChecked") and self._check_terms():
                    idle = 0
                    idle_since = None
                    busy_since = None
                    self.log.info("[%s] step %d: 勾选条款", self.cid, step)
                    continue

                # ★ 2026-10-23（真站 23d247da）：多选屏（checkbox "(up to 3 options)"）
                # 选完会冒出 continue（文字 "Next"）。这时**先按 Next**，不要再随机点一个：
                # `pick_option` 不排除已选中的，随机点到那一颗会把勾去掉，页面随即收回
                # Next ⇒ 脚本在「同题重复 → 等 continue」里耗到 45 秒收摊
                # （生产单 26800520 / 26800522 / 26800681 的现场）。
                if page.get("nextVisible") and page.get("checkedOptions"):
                    self.click_visible(CONTINUE_BTN)
                    idle = 0
                    idle_since = None
                    busy_since = None
                    self.log.info("[%s] step %d [%s]: 已选中 %d 个，按 continue",
                                  self.cid, step, page.get("step", "?"),
                                  len(page.get("checkedOptions") or []))
                    continue

                # 同一题连着出现 ≥2 次 = 多选题（选完要按下一步）或选项不推进
                sig = page.get("options") or [
                    (x[0] if isinstance(x, (list, tuple)) else x)
                    for x in (page.get("labels") or [])]
                #: ★ 2026-10-23（真站 23d247da）：原来用 `head[:40]` 认「同一题」，
                #: 而 head 是 body.innerText 的前 120 字 —— 每一屏都从同一行 chrome
                #: （"1 2 3 4 Your profile Preferences Finish up …"）开头，40 字全落在
                #: 那行里 ⇒ **不同的问题被当成同一题**：连着两屏 Preferences 就 repeat=2、
                #: 判成多选、从此不再点选项（生产单 26800520/522/681 的真因）。
                #: 按**选项集合**认题；没有选项（纯文字/输入屏）才退回 head。
                key = "%s|%s" % (page.get("step", ""), "|".join(str(x) for x in sig)
                                 or (page.get("head", "") or "")[:40])
                if key == self._last_q:
                    self._repeat += 1
                else:
                    self._repeat = 0
                    self._last_q = key
                #: 2026-10-23：两点就收摊太急 —— 真站上「点一下没推进」很常见
                #: （input 已 checked、页面不动；再点一下就过）。给到 6 次再判死。
                stuck = self._repeat >= 6

                # 3) 答题：先 testid（主路），再文本兜底
                checked = page.get("checkedOptions") or []
                pick = None if stuck else pick_option(
                    [o for o in page.get("options", []) if o not in checked])
                if pick:
                    self.click_option(pick)
                    answered += 1
                    idle = 0
                    idle_since = None
                    busy_since = None
                    self.log.info("[%s] step %d [%s] %s -> %s", self.cid, step,
                                  page.get("step", "?"), page.get("head", "")[:46], pick)
                    continue
                text = None if stuck else pick_option_by_text(page.get("labels", []))
                if text and self.click_text(text):
                    answered += 1
                    idle = 0
                    idle_since = None
                    busy_since = None
                    self.log.info("[%s] step %d [%s] %s -> (文本) %s", self.cid, step,
                                  page.get("step", "?"), page.get("head", "")[:46], text)
                    continue

                # 4) 过场页 / 表单页 / 多选题选完：按 continue
                if page.get("nextVisible"):
                    self.click_visible(CONTINUE_BTN)
                    idle = 0
                    idle_since = None
                    busy_since = None
                    self.log.info("[%s] step %d [%s] continue (%s)", self.cid, step,
                                  page.get("step", "?"), page.get("head", "")[:46])
                    continue

                # 5) 没得点：先分「页面在忙」还是「真没事干」
                head = page.get("head", "") or ""
                if any(m.lower() in head.lower() for m in BUSY_MARKERS):
                    if busy_since is None:
                        busy_since = time.time()
                    waited = time.time() - busy_since
                    if waited >= BUSY_TIMEOUT:
                        self.log.error("[%s] 页面停在处理态 %.0f 秒没出来（%s）—— 如实报失败",
                                       self.cid, waited, head[:50])
                        self._rpt("max_steps")
                        return False
                    idle = 0
                    idle_since = None
                    self.log.info("[%s] step %d: 页面在处理中，继续等（%.0fs / %ds）%s",
                                  self.cid, step, waited, BUSY_TIMEOUT, head[:40])
                    continue

                busy_since = None
                idle += 1
                idle_since = idle_since or time.time()
                waited = time.time() - idle_since
                if idle == 1:      # 卡住的第一时间就把现场打出来（线上只能靠它定位）
                    self.log.warning("[%s] 卡住诊断 step=%s next=%s legal=%s "
                                     "options=%s labels=%s inputs=%s url=%s",
                                     self.cid, page.get("step"), page.get("nextVisible"),
                                     page.get("legalChecked"), page.get("options"),
                                     [t for t, _ in page.get("labels", [])][:6],
                                     page.get("inputs"),
                                     "%s|%s" % (hash_state(url) or "-", url[:50]))
                self.log.info("[%s] step %d: nothing actionable (%d, %.0fs) %s", self.cid,
                              step, idle, waited, head[:60])
                if idle >= IDLE_LIMIT and waited >= IDLE_TIMEOUT:
                    self.log.error("[%s] 答了 %d 题后卡住 %.0fs @ %s —— 如实报失败",
                                   self.cid, answered, waited, url[:90])
                    self._rpt("max_steps")
                    return False

            self.log.error("[%s] 用满 max_steps（答了 %d 题）—— 如实报失败", self.cid, answered)
            self._rpt("max_steps")
            return False

        except Exception as e:
            # 带上栈：没有栈时这个 except 会把「哪一行炸的」永久吃掉，
            # 线上只能看到一句 Error: xxx（failDiag 那条线就是为这个存在的）。
            import traceback
            self.log.error("[%s] Error: %s\n%s", self.cid, e, traceback.format_exc())
            return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url", required=True)
    p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default="")
    a = p.parse_args()
    log = setup_logger("japansdates")
    log.setLevel(a.log_level)
    f = JapanSDatesFiller(a.ws_url, a.form_file, a.correlation_id, a.task_id)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
