#!/usr/bin/env python3
"""Site script for 'homejolly' — 步进循环型：每轮读页面 → 有什么做什么 → 循环。

（由 siteforge 生成：骨架固定，只填了「怎么读 / 填什么 / 挑哪个 / 判成功」四格。）
形状照 `goldenagesouls.py`（生产在跑的那份）；诚实那条照 `docs/执行事实契约-2026-09-18.md`：
**读不到就是读不到**，绝不折算成「做完了」。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

SITE = 'homejolly'
#: ★ 2026-09-24 第三次真站试跑逼出来的：**新窗口开出来停在 BitBrowser 控制台那一页** ✗
#: ⇒ 不导航过去，`read()` 只会一路返回 None（trace 里连着 6 行「页面读不到」✓ 它就是这么哑的）。
#: 入口网址从「这一版是从哪儿探出来的」那一格拿（同一个 key 与固定步骤表那条同源）。
SUCCESS_URL_MARKERS = []
SUCCESS_TEXTS = ["Thank you"]
FILLS = { 'postcode': { 'fallback': ['19099'],
                'kind': 'value',
                'label': 'zipInput',
                'name': 'postcode',
                'sample': '19099',
                'source': 'postcode',
                'target': { 'above_fold_only': False,
                            'frame_id': '',
                            'hint': 'zipInput',
                            'label': 'zipInput',
                            'near': None,
                            'nearby_text': None,
                            'placeholder': 'Enter ZIP Code',
                            'role': None,
                            'selectors': [ '#zipInput',
                                           'input[placeholder="Enter ZIP Code"]',
                                           'form:has(input[placeholder="Enter ZIP Code"]) '
                                           'input[type=text]'],
                            'text': None}}}
PICK = 'random'
PICK_HINTS = []
MAX_STEPS = 68
IDLE_LIMIT = 60
BUSY_TIMEOUT = 90
READ_PAGE_JS = "(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}\n  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}\n  function txt(e){return ((e.textContent||'').replace(/\\s+/g,' ').trim());}\n  // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：**弹层开着时只读弹层** ——\n  //   原来整页一起读：弹层里的题与弹层后面的落地页 CTA 混在一个池子里 ⇒ 循环把\n  //   「Check Prices Now」（被弹层盖着）当 Next 连点三次、页面纹丝不动就收摊。\n  //   判据：可见 + 身份里有 dialog/modal 或 role=dialog + 盖住 ≥80% 视口；取面积最大那个。\n  function deepIn(root,sel){var out=[];function walk(r){r.querySelectorAll(sel).forEach(function(e){out.push(e);});\n    var all=r.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}\n    walk(root);return out;}\n  function modalRoot(){var best=null,ba=0;\n    deepAll('div,section,aside,[role=dialog]').forEach(function(e){\n      if(!vis(e)||!e.getBoundingClientRect)return;\n      var lab=String(e.id||'')+' '+String(e.className||'')+' '+String((e.getAttribute&&e.getAttribute('aria-label'))||'');\n      var sem=/(dialog|modal)/i.test(lab)||!!(e.getAttribute&&(e.getAttribute('role')==='dialog'||String(e.getAttribute('aria-modal'))==='true'));\n      if(!sem)return;\n      var r=e.getBoundingClientRect();\n      if(r.width<window.innerWidth*0.8||r.height<window.innerHeight*0.8)return;\n      var ar=r.width*r.height; if(ar>ba){best=e;ba=ar;}});\n    return best;}\n  var ROOT=modalRoot()||document;\n  //: ★ 2026-11-06：这个模态**就是这一屏**（读取器在它里面读题/读格）⇒ 打上「流程本身」的\n  //: 标记 —— `clear_popups` 那一侧靠它认出「这颗 × 不是遮挡」。跟 DOM 走，关掉即消失。\n  if(ROOT!==document&&ROOT.setAttribute)ROOT.setAttribute('data-sf-flow','1');\n  // ★ 2026-09-26（第五次真站试跑，`easystorebuilderpro` 那三个格）：**`id` 与 `data-testid` 不是一回事** ——\n  //   那一站三个格只有 `id`（`qv3-fn` / `qv3-em` / `qv3-ph`），而填的那一侧一律按\n  //   `[data-testid=\"…\"]` 找 ⇒ **一个都找不到**、`cdp form` 报「element not found」，\n  //   而调用方把这一下当成功了 ⇒ 表单永远是空的、提交永远被拒（真事：点三次 Next 页面一动不动）。\n  //   ⇒ 来源**打上标记**（`\\u0004` = 按 id/name 找），填/点那一侧照着用对选择器。\n  // ★ 2026-10-28（Atticus `How can we reach you?` 的真事）：React 的自动 id（`:r1:`…）**会整排漂移**\n  //   （账本 `:r1:`=First … `:r4:`=Phone，线上那一屏变成 `:r2:`…`:r5:`）⇒ 按 id 认格 = 每格拿到\n  //   **前一格**的值（邮箱进姓氏、电话进邮箱、年龄进电话）⇒ 校验红字、Submit 永远灰着。\n  //   ⇒ 自动 id **不算身份**：有 placeholder / aria-label 就按那个认；稳定 id 照旧优先。\n  function tid(e){var t=e.getAttribute('data-testid');\n    if(t)return t;\n    var i=e.id||e.getAttribute('name');\n    var p=(e.getAttribute('placeholder')||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();\n    if(i)return (p&&/^:r[0-9a-z]+:$/i.test(i))?(\"\\u0002\"+p):(\"\\u0004\"+i);\n    if(p)return \"\\u0002\"+p;\n    // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly 的街道那格）：别的身份一个都没有，\n    //   只有 class（`leadforms-full-street-address-input`）。取**最后一个**够长的 class\n    //   （站点习惯把语义类放最后；`mb-4` 这类版式类太短，不当身份）。\n    //   ⚠️ 只给**表单控件**用 —— 选项（button/label）的身份是它那段文字，按 class 认会\n    //   把「按文字打标记再点」那条路整条换掉。\n    var tg=String(e.tagName||'').toUpperCase();\n    if(tg==='INPUT'||tg==='SELECT'||tg==='TEXTAREA'){\n      var cs=String(e.className||'').replace(/\\s+/g,' ').trim().split(' ');\n      for(var ci=cs.length-1;ci>=0;ci--){var cc=cs[ci];\n        if(cc.length>=8&&/^[A-Za-z][A-Za-z0-9_-]*$/.test(cc))return \"\\u0008\"+cc;}}\n    return '';}\n  var inputs=[];\n  deepIn(ROOT,'input,select,textarea').forEach(function(e){\n    if(!vis(e))return; var t=tid(e); if(!t)return;\n    inputs.push([t, (e.value||'').trim()]);});\n  var options=[], unanswered=[], items=[], nextVisible=false, nextTxt='', nextEnabled=false;\n  // ★ 2026-09-24 第九趟真站：`a[href]` 一律不收 —— 页脚那几个「Privacy Policy」会被当成\n  //   选项，每轮挑中一个、又点不到（它们不是 button/label）⇒ 「再看一眼」空转到收摊 ✗。\n  // ★ 2026-09-26（`job-c053e0b049f3` 的真 trace）：**导航/编辑类控件也不算选项** ——\n  //   那一趟 58 次点击里就有两次点在「‹ edit previous piece」上，**倒回上一屏** ⇒\n  //   来回震荡、永远走不到提交那一步。这一类不是答案，是往回走。\n  var NAVISH=/(previous|\\bback\\b|go back|\\bedit\\b|\\u2039|\\u00ab|\\u2190)/i;\n  deepIn(ROOT,'button,label,[role=button]').forEach(function(e){\n    if(!vis(e))return; var s=txt(e); if(!s)return;\n    // ★ 2026-09-26（`job-c053e0b049f3` 那一站的真屏幕）：「下一步」那一颗**不叫 Next/Continue** ——\n    //   它是「INSTALL 2 MODULES →」⇒ 只认那几个英文词就永远点不到它，于是答完一题就干等 ✗。\n    //   收宽两条：① 多认几个「往前走」的词；② **以箭头收尾的按钮**（`→` / `»` / `›`）也算下一步。\n    // ★ 2026-09-26（第四次真站试跑）：**输入框自己的 `<label>` 不是选项** ——\n    //   那一趟把「\U0001F4EC Email — where your blueprint is sent」当成一题点了（trace 里那行\n    //   「答了第 14 题」就是它）✗。带 `for=` 或肚子里有输入框的 label 一律不算选项。\n    // ★ 2026-10-22（Atticus 真站）：**收窄到「文本类输入」** —— 上面那条写宽了：\n    //   那一屏的选项是 `<label>Myself<input type=\"checkbox\"></label>`，checkbox 就是选项本身，\n    //   却被「包着 input 就跳过」整组丢掉 ⇒ 连 60 步「没有可动作的东西」。\n    //   现在只有 label 指向/包着**文本类**输入（input 非 checkbox/radio/hidden、select、\n    //   textarea）才跳过；包着 checkbox/radio 的 label 是选项，要收。\n    if(e.tagName==='LABEL'){var ref=e.htmlFor?document.getElementById(e.htmlFor):null;var refTxt=!!(ref&&(ref.tagName==='TEXTAREA'||ref.tagName==='SELECT'||(ref.tagName==='INPUT'&&!/^(checkbox|radio|hidden)$/i.test(ref.getAttribute('type')||''))));if(e.querySelector('input:not([type=checkbox]):not([type=radio]):not([type=hidden]),select,textarea')||refTxt)return;}\n    // \\u2605 2026-10-24\\uff08Atticus `survey/claim/zip`\\uff09\\uff1a\\u300c\\u4e0b\\u4e00\\u6b65\\u300d**\\u662f\\u4e0d\\u662f\\u7981\\u7528**\\u4e5f\\u5f97\\u4ea4\\u56de\\u53bb \\u2014\\u2014\n    //   \\u90a3\\u4e00\\u5c4f\\u7684 value \\u660e\\u660e\\u5728\\uff08\\u56de\\u8bfb\\u8bf4\\u586b\\u597d\\u4e86\\uff09\\uff0c\\u53ef Next \\u4e00\\u76f4\\u662f `disabled`\\uff08\\u7ad9\\u70b9\\u90a3\\u6b21\n    //   `benefitsByZip` \\u88ab\\u4e2d\\u6b62\\uff09\\uff0c\\u5faa\\u73af\\u770b\\u4e0d\\u89c1\\u8fd9\\u4e2a\\u4e8b\\u5b9e\\uff0c\\u53ea\\u4f1a\\u5e72\\u70b9 8 \\u6b21\\u3001\\u5982\\u5b9e\\u62a5\\u5931\\u8d25\\u3002\n    //: ★ 2026-11-02（educationdirectory 真站，8100 那趟）：最后那颗提交按钮写的是\n    //: 「Agree & See My Schools」—— 既不匹配上面那几个词，又被下面那条同意层排除规则\n    //: 当成「同意条按钮」扔掉 ⇒ 六题答完、格子填完，之后 60 步全「没有可动作的东西」。\n    //: 判据加一条**结构**的：`<button type=\"submit\">` 就是「下一步」（submit 是它的本义）。\n    if(String(e.getAttribute('type')||'').toLowerCase()==='submit'\n       || /^\\s*(next|continue|get results|submit|install|finish|done|complete)\\b/i.test(s)\n       || /[\\u2192\\u00bb\\u203a]\\s*$/.test(s)){nextVisible=true;nextTxt=s;\n      if(!(e.disabled||e.getAttribute('aria-disabled')==='true'))nextEnabled=true;\n      return;}\n    // ★ 2026-11-06（真事 `job-2aaa04c550b6` · homejolly）：**输入框自己的浮动标签**\n    //   （`<label class=\"… form-control-placeholder\">Street Address*</label>`）不是一道题 ——\n    //   它压在自己那格输入框上，挑中它只会得到一句「被 input…盖着」。\n    if(e.tagName==='LABEL'&&/placeholder/i.test(String(e.className||'')))return;\n    // ★ 2026-11-06（同上）：电话那格的**国家选择器**（`\U0001F1FA\U0001F1F8+1`）也不是一道题 —— 挑中它\n    //   会打开一张国家列表浮层，然后整趟都在里面挑国家（后面 40 步全在那一屏上打转）。\n    if(/^[^A-Za-z0-9_]*\\+[0-9]{1,4}$/.test(s))return;\n    if(NAVISH.test(s))return;\n    // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：弹层那颗关闭键写的就是「×」——\n    //   它不是一道题；随机挑中它等于把整张表单关掉（后面全空转）。\n    if(/^\\s*[×✕✖✗]\\s*$/.test(s))return;\n    // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：站外的**评价徽章**\n    //   （页面上 6 颗 <button class=\"reviewBtnN\">Read Review on Google</button>）也是\n    //   普通 button ⇒ 被当成「这一屏的一道题」随机挑中，点完直接导航到 Google Maps\n    //   ⇒ 整趟再也回不到漏斗（后面 60 步全在 Map details 上打转）。\n    //   判据只认「评价 + 站外平台」那几句短语（不按站点类名）；别的屏上这些字不出现。\n    if(/^(read|view|see|write|leave)\\s+(a\\s+|your\\s+)?review\\b/i.test(s)\n       || /\\breviews?\\s+on\\s+(google|facebook|yelp|trustpilot|bbb)\\b/i.test(s))return;\n    // ★ 2026-10-13（用户口径：**修系统**）：「同意条**啥时候冒出来都不确定**」⇒ 同意那几颗\n    //   **不许被当成「这一屏的一道题」**：它出来得晚（异步注入），混进选项里就会被随机挑 ——\n    //   挑中「Accept All」算运气，挑中「管理偏好 / 设置」就点进一个弹窗里空转 ✗。\n    //   这一类交给**动手之前那一段**（`run()` 里读 `consent` 那一格 ⇒ `clear_cookies()`）。\n    if(/^\\s*(accept|agree|allow|got it|close|reject|decline|deny|manage|settings|preferences|more options|customi[sz]e)\\b/i.test(s)\n       || /(cookie|consent|gdpr|privacy|同意|接受|拒绝|隐私|设置|偏好)/i.test(s))return;\n    // ★ 2026-11-02（educationdirectory 真站）：选项包在 label 里的 radio/checkbox 用\n    //   `\\u0005文字\\u0006name\\u0007value` 交回去 —— 同一屏的两组 Yes/No 文字一模一样，\n    //   按文字点永远点到第一组（第二组一直空着，Next 被表单校验拦住）。\n    //   顺便把「这一组选过没有」算出来：`unanswered` 只放还没选过的那几组（+ 没有 input 的\n    //   普通按钮），调用方按它挑 —— 老读取器没有这一格，调用方照旧按 options 挑。\n    var t=tid(e), ctl=null, g=\"\", chk=false;\n    if(e.tagName==='LABEL'){\n      ctl=e.control||(e.htmlFor?document.getElementById(e.htmlFor):null)||e.querySelector('input');\n      if(ctl&&(ctl.type==='checkbox'||ctl.type==='radio')){\n        g=ctl.name?(\"n:\"+ctl.name):(\"u:\"+items.length);\n        chk=!!ctl.checked;\n        if(ctl.name&&ctl.value)t=\"\\u0005\"+s+\"\\u0006\"+ctl.name+\"\\u0007\"+ctl.value;\n      }\n    }\n    var tok=t?t:(\"\\u0001\"+s);\n    options.push(tok); items.push({t:tok,g:g,c:chk});});\n  // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：**地址联想**（Google Places）\n  //   不是 button/label/role=button ⇒ 老读取器**根本读不到** ⇒ 街道那格填完也选不上，\n  //   而提交要的是「从联想里选一条」（站点的 name=street/city/state/zip 是选中后才填的）。\n  //   `[role=option]` 是各家自定义下拉的通用写法（原生 <option> 不可见，`vis` 会挡掉）。\n  //   交给调用方时按 `\\u0001`+文字 —— 与别的「按文字点」选项同一条路。\n  deepAll('.pac-container .pac-item,[role=option]').forEach(function(e){\n    if(!vis(e))return; if(String(e.tagName||'').toUpperCase()==='OPTION')return;\n    var s=txt(e); if(!s)return;\n    var tk=\"\\u0001\"+s;\n    if(options.indexOf(tk)<0){options.push(tk); items.push({t:tk,g:\"\",c:false});}});\n  // ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：题目的答案是**普通 div**\n  //   （`div.leadforms-answer`，没有 button/label/role）⇒ 老读取器一个选项都读不到、\n  //   只剩弹层那颗 ×。按 class 语义（answer/option/choice）收**短文字**的可见块；\n  //   包着文本输入的不算（那是格子，不是选项）；同一段文字只进一次。\n  deepIn(ROOT,'div,span,li').forEach(function(e){\n    if(!vis(e))return;\n    var cl=String(e.className||'').toLowerCase();\n    if(!/(answer|option|choice|select-item)/.test(cl))return;\n    var s=txt(e); if(!s||s.length>60)return;\n    if(e.querySelector&&e.querySelector('input:not([type=checkbox]):not([type=radio]):not([type=hidden]),textarea,select'))return;\n    if(NAVISH.test(s)||/^\\s*[×✕✖✗]\\s*$/.test(s))return;\n    if(/^\\s*(accept|agree|allow|got it|close|reject|decline|deny|manage|settings|preferences|more options|customi[sz]e)\\b/i.test(s)\n       || /(cookie|consent|gdpr|privacy|同意|接受|拒绝|隐私|设置|偏好)/i.test(s))return;\n    if(/^(read|view|see|write|leave)\\s+(a\\s+|your\\s+)?review\\b/i.test(s)\n       || /\\breviews?\\s+on\\s+(google|facebook|yelp|trustpilot|bbb)\\b/i.test(s))return;\n    var tk=\"\\u0001\"+s;\n    if(options.indexOf(tk)<0){options.push(tk); items.push({t:tk,g:\"\",c:false});}});\n  var gdone={}; items.forEach(function(it){if(it.g&&it.c)gdone[it.g]=1;});\n  items.forEach(function(it){if(!it.g||!gdone[it.g])unanswered.push(it.t);});\n  //: 「这一屏有没有同意层」—— 同意层这类**看得见的、命中那几组字样**的元素算（与上面那条\n  //: 排除规则同一套词）：有 ⇒ 调用方动手之前先清掉它。**只读，不动手**。\n  var consent=false;\n  deepAll('div,section,aside,footer,[role=dialog]').forEach(function(e){\n    //: ★ 2026-11-02（educationdirectory 真站）：**只看身份**（id / class / aria-label），\n    //: 不看 `innerText` —— 页脚那行「Privacy Policy」把整页判成「有同意层」⇒ 每一轮都\n    //: 去清、清不掉、`idle` 被清零 ⇒ 空转到步数上限（8100 那趟第 10–126 步全是这一句）。\n    //: ★ 2026-11-02 第二刀（8099 那趟第 9–158 步）：`<div class=\"lp-consent\">` 是页面里\n    //: **内联的 TCPA 声明**（`position:static`），不是浮层 —— 身份里有 consent 也**不算**。\n    //: 判据加一条形状：**浮层**（computed position 是 fixed/sticky）或 `role=dialog`。\n    //: 认不出来的真弹层由 `clear_popups` 的形状判据兜住（每次动手之前都会跑）；这里只是\n    //: 「要不要提前清一下」的提示，**宁漏不误标**（误标的代价是整趟空转）。\n    if(!vis(e))return;\n    var st=\"\"; try{st=getComputedStyle(e).position;}catch(err){}\n    if(st!==\"fixed\"&&st!==\"sticky\"&&e.getAttribute(\"role\")!==\"dialog\")return;\n    var lab=String(e.id||\"\")+\" \"+String(e.className||\"\")+\" \"\n      +String((e.getAttribute&&e.getAttribute(\"aria-label\"))||\"\");\n    if(/(cookie|consent|gdpr)/i.test(lab))consent=true;});\n  var pm=(document.body.innerText.match(/\\b\\d{1,3}\\s*%/)||[])[0]; var prog=pm?pm:'';\n  var _hd=(ROOT.querySelector?ROOT.querySelector('h1,h2,h3'):null)||document.body;\n  var head=_hd.innerText||'';\n  return JSON.stringify({url:location.href, head:head.replace(/\\s+/g,' ').trim().slice(0,300),\n                         progress:prog, inputs:inputs, options:options, unanswered:unanswered,\n                         nextVisible:nextVisible,\n                         nextTxt:nextTxt, nextDisabled:(nextVisible&&!nextEnabled),\n                         consent:consent});})()"
#: 每轮先清一遍 —— 见 `run()` 里「动手之前」那一段（`clear_cookies`）；判据是读取器读回来的
#: `consent` 那一格，**不额外往返**（那一读本来就有）。
#: 蜜罐：这些字样命中的输入框**永远不填**（真人看不见它、机器人会填 ⇒ 整单判垃圾）。
SKIP_FIELDS = ("company", "company_url", "website", "url", "fax")
#: 资料列名 ↔ 页面字段名（单一事实源在 `agent/template_loop.py` —— 与固定步骤表同源）。
#: `FILLS` 的键是页面名、资料的键是库列名（`postal_code` / `phone_number` / `username`）；
#: 两边没有约定 ⇒ 只加一层回退。⚠️ 不写任何站特有的字。
PROFILE_ALIASES = { 'phone': ('phone_number', 'tel', 'mobile', 'phone1', 'telephone'),
  'first_name': ('firstname', 'first', 'given_name'),
  'last_name': ('lastname', 'last', 'surname', 'family_name'),
  'email': ('mail', 'email_address'),
  'postcode': ('postal_code', 'zip', 'zipcode', 'zip_code'),
  'city': ('town',),
  'street': ('address', 'address1', 'street_address'),
  'country': ('country_code',),
  'dob': ('birthday', 'date_of_birth', 'birthdate')}
#: 资料只给一个全名（`username`）时，First / Last 各取一段。
PROFILE_SPLIT_FROM = {'first_name': 'username', 'last_name': 'username'}
#: Next 是禁用态 + 格子有值 ⇒ **同一屏最多重打几次**。
#: ★ 2026-10-29（线上 26919524）：站点那次查询是**偶发**被中止的（再打一次就 200），
#: 而旧实现「一屏只打一次」⇒ 第一次撞上中止就再也不碰这格，只能干点 Next 到收摊。
NUDGE_TRIES = 3
#: 每次重打后最多等多久看 Next 开没开（真站 `benefitsByZip` 约 1.7s 返回）。
NUDGE_WAIT_SECONDS = 5.0
#: 重打前先清空、隔这么久再写回。★ 2026-11-06（线上 23 单全卡 ZIP）：
#: 站点对 `benefitsByZip` 只给 2.5 秒（客户端硬编码），美国代理冷启动常 2.5 秒以上
#: ⇒ 第一下被 abort，`Next` 一直禁用；而同一个值再写一遍时 React 的 `onChange`
#: 认为「没变」⇒ **请求根本不重发**。清空再写回才逼它重新查一次。
NUDGE_CLEAR_GAP = 0.3
#: 页面「在处理」的文案（忙 ≠ 空转；与 goldenagesouls 同一套思路）。
BUSY_MARKERS = ("almost done", "processing your", "setting up", "just a moment",
                "getting things ready", "hold on", "calculating", "please wait")
PROVENANCE = { 'generated_at': '2026-11-06T08:29:22+00:00',
  'generator': 'siteforge/v0.1',
  'env': {'proxy_country': 'US'},
  'fingerprint': None,
  'platform': None,
  'selftest': None,
  'source': { 'kind': 'fix',
              'evidence': '1.浏览到Check Prices In My Area并点击  2.回答问题及填表单',
              'runtime': { 'copied_from': '/opt/skills/auto-farm-skill/forms/common.py',
                           'source_commit': '6c54785e299d486a191bd7d6222555f48f5906cf',
                           'source_commit_date': '2026-06-09 16:31:09 +0800',
                           'source_md5': '2a245ad4960c472b5ff97059e4ded5f8',
                           'copied_at': '2026-09-16',
                           'owner': 'siteforge（真源；生产仓库那份降级为部署产物）',
                           'changes': ( '文件头加了这段出身说明与 RUNTIME_PROVENANCE',
                                        'CDP_PATH '
                                        '改环境变量优先（默认值一个字没改：/opt/skills/auto-farm-skill/cdp）',
                                        'CDPHelper.screenshot() 失败时可归因（新增 '
                                        'last_screenshot_error；成功路径返回值不变）',
                                        'report_url() 认两个**环境**旋钮（SITEFORGE_TRACE / '
                                        'SITEFORGE_NO_REPORT）——B 线 ③ 乙：老写法那一族（66 份）的 main() '
                                        '里没有 '
                                        '--trace/--no-report，自测那条线只能从运行时这一处进；两个都没设时**行为逐字节不变**')}},
  'entry_url': '',
  'arrive_url': 'https://home.helloprojectusa.com/walk-in-showers-v3-7899',
  'pre_steps': [ { 'action': 'scroll',
                   'target': { 'text': 'Check Prices In My Area →',
                               'role': 'a',
                               'near': 'main',
                               'label': None,
                               'hint': None,
                               'placeholder': None,
                               'nearby_text': [ '1Enter your ZIP code and answer a few qu',
                                                'How do I get started?',
                                                'What would you like to update in your '
                                                'bathroom? Walk-In Shower Tub-to-Shower Som'],
                               'selectors': [ 'a.adv-big-cta',
                                              'body:nth-of-type(1) > main:nth-of-type(1) > '
                                              'article:nth-of-type(1) > a:nth-of-type(3)'],
                               'above_fold_only': False,
                               'frame_id': ''}},
                 { 'action': 'click',
                   'target': { 'text': 'Check Prices In My Area →',
                               'role': 'a',
                               'near': 'main',
                               'label': None,
                               'hint': None,
                               'placeholder': None,
                               'nearby_text': [ '1Enter your ZIP code and answer a few qu',
                                                'How do I get started?',
                                                'What would you like to update in your '
                                                'bathroom? Walk-In Shower Tub-to-Shower Som'],
                               'selectors': [ 'a.adv-big-cta',
                                              'body:nth-of-type(1) > main:nth-of-type(1) > '
                                              'article:nth-of-type(1) > a:nth-of-type(3)'],
                               'above_fold_only': False,
                               'frame_id': ''}}],
  'fill_order': ['postcode', 'full_name', 'full_name-2', 'email', 'phone'],
  'examples': { 'email': 'michael.johnson1985@gmail.com',
                'full_name': '1450 Sunset Blvd',
                'phone': '2138675309',
                'postcode': '19099'},
  'shape': 'loop',
  'why': '有 3 个状态停在同一个网址「https://home.helloprojectusa.com/walk-in-showers-v」上（query 不算）—— '
         '单页漏斗/问卷',
  'profile_unmapped': []}
ENTRY_URL = str((PROVENANCE or {}).get("entry_url") or "")
#: ★ 前置（用户 2026-09-24 点破）：进问卷之前那几步 —— **探路时真走过的**（从账本搬进来 ✓
#: 不编 ✗ 也不抄别的站的表 ✗）。每步是账本里那一步的原样：`{action, target{text,role,selectors}}`。
PRE_STEPS = list((PROVENANCE or {}).get("pre_steps") or [])
#: 到没到问卷的判据（账本里**下一门**的 `when.url_contains` ✓）：地址里出现这一截才算到 ✓。
ARRIVE_URL = str((PROVENANCE or {}).get("arrive_url") or "")
#: ★ 账本里那几格**填过的顺序**（用户 2026-09-24 的原话：「**一般输入框顺序也不会变**」✓）。
#: 哪一格都认不出来时用它兜底 —— 本屏第 n 个空格 ⇒ 账本第 n 格 ✓（我们自己的事实 ✓ 不猜 ✗）。
FILL_ORDER = list((PROVENANCE or {}).get("fill_order") or [])
#: ★ 2026-09-26：**探路那一趟每一格真用过的那个值**（生成器从账本搬进来）——
#: 优先于我们自己编的池子。实测：那站国家选择是 US，而我们的手机池给英国座机号 ⇒
#: 客户端校验直接拒（页面写着「valid phone」）⇒ 提交永远过不去；用账本里那个
#: `5551234567` 就过 ✓。任务给的 `form_data` 仍然最优先（那是运营填的）。
EXAMPLES = dict((PROVENANCE or {}).get("examples") or {})
#: 选项没给 `data-testid` 时的前缀：`\x01文字` = 「按这段文字去页面上找」。
BY_TEXT = "\x01"
#: 输入框没给 `testid`/`id`/`name` 时的前缀：`\x02placeholder` = 「按占位符去页面上找」。
#: ★ 2026-09-24 第一次真站试跑逼出来的：那一屏的邮编格**只有 placeholder**（形状同 LPA 的
#: `input[placeholder="e.g. RG24 8PE"]`）⇒ 读取器原来直接丢掉它 ⇒ 只剩 Continue ⇒ 空点 55 次。
BY_PLACEHOLDER = "\x02"
#: 元素**只有 `id`（或 `name`）、没有 `data-testid`** 时的前缀：`\x04id` = 「按 `[id=…]` 找」。
#: ★ 2026-09-26 第五次真站试跑逼出来的（`easystorebuilderpro` 那三个格只有 id）：
#: 不分来源就一律按 `[data-testid=…]` 找 ⇒ **一个都找不到**，而填的那一步照样报成功 ✗✗。
BY_ID = "\x04"
#: 选项是**包在 label 里的 radio/checkbox** 时的前缀：`\x05文字\x06name\x07value`
#: = 「按 name/value 找到那个 input，再点它的 label」。
#: ★ 2026-11-02（educationdirectory 真站）：同一屏会追加题（选了「Still deciding」才冒出
#: 两颗必答的 Yes/No），而两组的文字一模一样（两个「Yes」两个「No」）—— 按文字点永远只
#: 点到第一组 ⇒ 第二组一直空着、Next 被表单校验拦住。name/value 是页面上**唯一**的地址。
BY_FIELD = "\x05"
#: 输入框**连 id/name/placeholder/aria-label 都没有**、只有 class 时的前缀：`\x08class`
#: = 「按 `[class~=…]` 找」。★ 2026-11-06（真事 `job-e1af552518de` · homejolly 的街道那格：
#: `<input class="… leadforms-full-street-address-input">` 别的身份一个都没有）——
#: 读取器原来直接丢掉它 ⇒ 循环**永远填不到街道**，而提交要的是联想里那条**被选中**的地址。
#: 取**最后一个** class（站点习惯把语义类放最后），且要够长（≥8）：`mb-4` 这类版式类不当身份。
BY_CLASS = "\x08"
#: 这一格是原生 `<select>`、可我们**没有**任何值可填（账本没记、池子也不认识这一格）时的
#: 返回值：由执行那一侧**从页面上挑第一个可选项**（见 `_any_select_option`）。
#: ★ 2026-11-02（educationdirectory 真站）：探路把名字退成 `field-5`（取值名那条老缺陷），
#: `education_level` 这一格既没有账本值、池子也认不出 ⇒ 空着 ⇒ Next 被表单校验拦住。
#: 下拉的值**本来就在页面上**，挑一个真实存在于页面上的选项，比编一个必然不存在的值好。
SELECT_ANY = "\x00select-any"

#: 连着点几次「Next」页面还是那一屏 ⇒ 当**空转**，如实收摊（第一版没有这道闸，
#: 真站上才会空点 55 次 Continue 把步数耗光 ✗）。
SAME_NEXT_LIMIT = 3

#: ★ 2026-10-22（Atticus 真站）：`cdp form` 回 `ok=true` 之后**回读 value**，没留住就重填。
#: 现场：`#zipcode.value` 一直是 `""`、Next 一直禁用，而 trace 上写着「填了 zipcode」——
#: React 在 hydration / 重渲染时把早期写进去的值覆盖掉了。`form` 的回话只说明「发出去」。
#: 回读**读不到**（`None`）不算「没填上」：那是看不见，不是事实。
FILL_READBACK_TRIES = 3
FILL_READBACK_GAP = 0.4
#: ★ 2026-10-29（Atticus 第一趟 step 1）：`cdp form` 在页面还没 hydrate 完的那一下回
#: `Error: … element not found` —— 旧实现**立刻判失败**。这一格下一轮会不会被再填一次
#: 全看运气。短促重试几次，仍不行才如实失败（并把原因写进 `_fill_why`）。
FILL_READY_TRIES = 3
FILL_READY_GAP = 0.6
#: ★ 2026-10-29（Atticus 插页屏）：Next 是禁用态、又没有可重打的格子 ⇒ **等**，别去点。
#: 点禁用按钮 = 点空气，还会把「同一屏最多几次」的额度烧光（旧流程就这么收摊的）。
DISABLED_NEXT_WAIT = 1.0
#: ★ 2026-10-29（Atticus `age-interstitial-*` 旁路轮询现场）：点完 Next **先等页面变**
#: （URL 或题面），别 1.5 秒一次地连点 —— 那一趟 8 次点击全被吞，而页面在停止点击
#: 之后才走（同一时刻有 15–18 秒才失败的第三方请求）。变了就进下一轮。
#: ★ 2026-10-29 真站复跑：ZIP 屏从点 Next 到下一屏 **13 秒**（本地 headless，第三方请求
#: 15–18 秒才失败），12 秒差一点不够 ⇒ 20 秒留余量。正常的屏 1–2 秒就变了，不受影响。
NEXT_SETTLE_SECONDS = 20.0
NEXT_SETTLE_POLL = 0.4

#: 「这一屏答过了、但页面一动不动」最多再放行几次（见 ⑤ 里那段注释）。
#: 2 次够用：一次留给站点偶发忽略点击，一次留给随机换一个选项试试。
STUCK_RETRIES = 2

#: 用户 2026-09-24 拍的规则：「**根据是否可见来判断**」—— 读取器本来就只收可见的
#: (`offsetWidth>0 && offsetHeight>0` ✓) ⇒ 所以「没有可动作的东西」=「**可见的题/按钮为零**」✓。
#: 于是 Continue 降为**兜底**：连着这么多轮确实一件可见的都没有，才轮到点它 ✗
#: （不这么办 ⇒ 页面正把题画出来的那两轮里它抢先点 Continue ⇒ 22 秒就被自己的闸误杀 ✓ 真发生过）。
EMPTY_ROUNDS_BEFORE_NEXT = 2
#: ── 随机池按**站点国家**分流（★ 2026-11-02，educationdirectory 真站）──────────────
#:
#: 现场：US 站、资料没电话 ⇒ 池子给了英国 11 位 `01148106686`，US 站压成
#: `(011) 481-0668` ⇒ 回读少一位 ⇒ 重填两遍判「没填上」。
#: 信号只有一处现成的：`PROVENANCE["env"]["proxy_country"]`（与固定步骤表
#: `agent/template.py::_site_country()` 同一处，两字母码）；没有它时看资料里的
#: 五位 ZIP（英国/加拿大/澳洲邮编都带字母或不是五位）。**都不知道 = 英国那一档**
#: —— 与既有口径逐字相同（不静默改成美国）。
_PHONE_US_AREAS = ("202", "212", "305", "312", "404", "415", "480", "512", "602", "617",
                   "702", "703", "713", "801", "813", "847", "901", "919", "214", "267",
                   "336", "407", "469", "614", "646", "678", "786", "818", "858", "916")
_PHONE_CA_AREAS = ("416", "604", "514", "403", "613", "902", "780", "905", "204", "306",
                   "709", "867")
_PHONE_COUNTRIES = ("US", "CA", "GB", "AU")


def _site_phone_country(filler=None) -> str:
    """这个站的电话该按哪一国形状（`US` / `CA` / `GB` / `AU`；认不出给空串）。"""
    env = (PROVENANCE or {}).get("env") if isinstance(PROVENANCE, dict) else None
    code = str((env or {}).get("proxy_country") or "").strip().upper()
    if code in _PHONE_COUNTRIES:
        return code
    data = getattr(filler, "form_data", None)
    zip_code = str((data or {}).get("zip") or (data or {}).get("zip_code")
                   or (data or {}).get("postal_code") or "").strip()
    if re.fullmatch(r"\d{5}", zip_code):
        return "US"
    #: 站点国家也可以从**页面/账本**读出来（不靠代理出口）：`phone` 那格的
    #: placeholder `(555) 555-5555` 是 NANP；账本里那个真填过的号形状也能说明问题。
    for _name, spec in (FILLS or {}).items():
        spec = spec or {}
        words = ("%s %s" % (_name, spec.get("kind") or "")).lower()
        if not ("phone" in words or "mobile" in words or "tel" in words):
            continue
        placeholder = str((spec.get("target") or {}).get("placeholder") or "")
        if re.search(r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}", placeholder):
            return "US"
    ledger = str(EXAMPLES.get("phone") or "").strip()
    if re.fullmatch(r"\d{10}", ledger):
        if ledger[:3] in _PHONE_CA_AREAS:
            return "CA"
        if ledger[:3] in _PHONE_US_AREAS:
            return "US"
    if re.fullmatch(r"04\d{8}", ledger):
        return "AU"
    if re.fullmatch(r"0\d{10}", ledger):
        return "GB"
    return ""


def _nanp_phone(areas) -> str:
    """NANP 的**保留段** `NPA-555-01xx`（10 位）—— 号段真、尾号随机。"""
    return "%s55501%02d" % (random.choice(areas), random.randint(0, 99))

#: 同意层上那几个「接受/拒绝」长什么样（两份样本都先清它：`goldenagesouls` 的第一句就是
#: `self.cdp.click(COOKIE_BTN)` ✓ `lastingpowerofattorney` 的 `_clear_cookies` 同一条路 ✓）。
#: ⚠️ 它**异步注入** ⇒ 得上限内等它出现 ✓。
#: ★ 2026-10-13（用户口径：**修系统**）：两头都收宽 —— ① 盖满视口那种同意弹层的按钮
#: （`Allow All` / `Reject All` / `Agree` / 中文那几颗）；② 补几个**只关掉**它的说法
#: （`Close` / `关闭`）—— 清不掉的那一次**不算失败**（`clear_cookies` 只记一行），
#: 但能清就清，弹层留在页面上后面每一步都受影响。
COOKIE_TEXTS = ("Accept All Cookies", "Accept All", "Accept Cookies", "I Accept", "Got it",
                "Allow All", "Allow all", "Accept all", "Agree", "Reject All", "Decline",
                "同意", "接受", "全部接受", "关闭", "Close")
#: 「同意条还在吗」——**按文字**问页面上有没有那几句（复读确认用 ✓ 不猜）。
COOKIE_GONE_JS = r"""(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}
  var words=__WORDS__;
  var bs=deepAll('button,label,a[href],[role=button],div,span');
  for(var i=0;i<bs.length;i++){var e=bs[i];
    if(e.offsetWidth<=0||e.offsetHeight<=0)continue;
    var s=(e.textContent||'').replace(/\s+/g,' ').trim();
    for(var j=0;j<words.length;j++){
      if(s===words[j]&&e.tagName!=='BODY')return 'still-here';}}
  return 'gone';})()"""

#: ★ 2026-10-16：**落点判据**那几笔预算 —— 与固定步骤表**同一套数**（`agent/template.py` 里
#: 同名的几个常量就是它们，那边的量证也是这几个数的来路）。**一个数都不许改**：
#: 两套骨架量的是同一件事，数不一样就又是「两把尺子」。
#: ⚠️ `COVER_WAIT_SECONDS` 是「**目标被盖着时最多等它多久**」（等的是加载蒙版 ——
#: 它自己会走；35.0 是两趟真站量出来的寿命上界，不是拍的）；
#: `COVER_POLL_SECONDS` 是等的时候每隔多久再量一眼；`RECENTER_TRIES` 是等的同时
#: 最多把目标往视口正中挪几次（粘性页眉那类只站错地方，挪一下就躲开了 —— **有界**）。
COVER_WAIT_SECONDS = 35.0
COVER_POLL_SECONDS = 0.4
RECENTER_TRIES = 2

#: ★ 2026-10-16：那两段 JS 是**模块级源字符串**（`agent/template.py` 里同名的那两份 ——
#: 表那套在用，这边**同一把尺子**：判据只写一份，改一次两套都变）。
#: ⚠️ `_COVER_JS` 是**裸语句序列**（**不带** `(function(){…})()` 壳）：表那套的 `_ev` 在
#: 调用时替它包，而这一份的 `cdp.eval` **不替调用方包** ⇒ **调用处自己包**（见 `_cover_once`）。
#: 别在这儿包：包了表那套那边就成了双层包裹。
_COVER_JS = ("var all=document.getElementsByTagName('*'), el=null;for(var "
 'i=0;i<all.length;i++){try{if(all[i].matches(%s)){el=all[i];break;}}catch(e){}}if(!el) return '
 "'';var r=el.getBoundingClientRect();if(!r||r.width<=0||r.height<=0) return '';var "
 'x=r.left+r.width/2, '
 "y=r.top+r.height/2;if(x<0||y<0||x>window.innerWidth||y>window.innerHeight) return '';var "
 'stack=document.elementsFromPoint(x,y)||[];for(var k=0;k<stack.length;k++){var h=stack[k];var '
 "pe='';try{pe=getComputedStyle(h).pointerEvents||'';}catch(e){}if(pe==='none') "
 "continue;if(h===el||el.contains(h)||h.contains(el)) return '';var "
 'labs=(el.labels||[]);for(var j=0;j<labs.length;j++){if(labs[j]===h||labs[j].contains(h)) '
 "return '';}var htag=String(h.tagName||'').toUpperCase(), "
 "etag=String(el.tagName||'').toUpperCase();if(htag==='LABEL'&&etag==='SELECT'){var "
 "hcls=String(h.className||'').toLowerCase();if(hcls.indexOf('select')>=0||hcls.indexOf('dropdown')>=0) "
 "return '';}return "
 "'COVER|'+(h.tagName||'').toLowerCase()+(h.id?('#'+h.id):'')+'.'+String(h.className||'').split(' "
 "')[0];}return '';")
#: ⚠️ `_RECENTER_JS` 相反 —— 它**自带** `(function(){…})()` 壳（本来就那样，一个字符没动）
#: ⇒ 调用处**不要**再包一层（双层包裹当场坏，见 `_recenter`）。
_RECENTER_JS = ("(function(){var all=document.getElementsByTagName('*');for(var "
 "i=0;i<all.length;i++){try{if(all[i].matches(%s)){all[i].scrollIntoView({block:'center'});return "
 "'ok';}}catch(e){}}return '';})()")

#: ★ 2026-10-16（Task 4）：同意层 / 弹层那一整块 —— **与固定步骤表是同一份**（贴的是**原文**，
#: 不是另抄一份）：钩子表 `_POPUP_HOOKS`、拼好的探针 `_POPUP_PROBE_JS`、按文字打标记的
#: `_MARK_CONSENT_BY_TEXT_JS`。发源地在 `agent/template.py` 的 `_POPUP_MACHINERY_SRC`
#: （那一段是从它那边的骨架里原样搬出来的，两套骨架渲的时候取同一份 —— spec §3.1
#: 「判据只写一份」）。
#: ⚠️ `_POPUP_PROBE_JS` 与 `_MARK_CONSENT_BY_TEXT_JS` **都是裸语句序列**（里面有裸 `return`）：
#: 表那套的 `_ev` 替它们包 `(function(){…})()`，而这一份的 `cdp.eval` 不替调用方包
#: ⇒ **调用处自己包**（见 `clear_popups`）—— 与 `_COVER_JS` 同一条规矩，**别在这儿包**。
#: 找同意弹层那段 JS（**读**，不动页面；§5.2 允许 eval 读）。返回 `"<选择器>|<按钮文字>"`，
#: 找不到返回空串。判据与 `cdp observe` 的 obstructions 是**同一套词汇**
#: （文字 / id / class 里有没有 cookie|consent|gdpr|privacy），只是这里自己做一遍 ——
#: 原因见 `_clear_obstructions` 的 docstring（生产那个 cdp 没有 observe）。
#:
#: 只认**有把握**的那一下：可见的同意类容器 + 按钮文字属于「接受/同意/关闭/拒绝」那一族，
#: 按钮**带 id 就走 `#id`、只有文字就交回一句「按文字点」**（调用方打标记再点，见
#: `_MARK_CONSENT_BY_TEXT_JS`）。**两者都找不到就什么都不做**（宁可不做，也不猜一条会点错的
#: 选择器）；「点到弹层上」由 `_covered_by` 兜住，不会被记成做成了。
#:
#: ★ 2026-09-28（用户看到的现场：**「有个弹窗挡住了」**；那一趟第 11 步勾同意框没成）：
#: 原来这里写着 `if(!b.id) continue;` —— 按钮**必须带 id**。而现在常见的同意条
#: （自绘的那种）按钮只有文字、没有 id ⇒ **一个都点不到** ⇒ 「什么都不做」⇒ 弹层留着挡住 ✗。
#: 两条一起改：① 没 id 的走「按文字打标记再点」；② 按 `_CONSENT_ROOTS_JS` **穿 shadow root**。
#:
#: ── 「页面上现在还有没有同意弹层」────────────────────────────
#:
#: 与上面那段是**两个问题**，所以是两段 JS（不是把上面那段改一改）：
#:   上面那段问「我该点哪个按钮」—— 要点得着才算数；
#:   这一段问「弹层还在不在」—— 只是**判据**，所以它连按钮都不用找。
#: 用上面那段来回答下面这个问题是**错的**：弹层在、而按钮没 id 时它会退到按文字那条路，
#: 于是「弹层还在」被读成「弹层不在」。
#:
#: ★ 2026-09-28：**穿 shadow root**（web component 里的同意条原来直接看不见 ✗）——
#: 与 `_PAGE_TEXT_JS` 同一套 roots 走法（`ShadowRoot` 没有 `getElementsByTagName`）。
#:
#: ★★ 同一天、更关键的一条（用户看到现场后骂醒的那句「**cookie 没点啊**」）：
#: 认「这是不是同意条」**只看了 id/class/aria-label** ✗ —— 而 OneTrust 那个容器的 id 是
#: `#onetrust-banner-sdk`，**这三个词一个都没有** ⇒ 整条弹层被跳过 ⇒ 一下都不点 ✗✗
#: （`cdp observe` 之所以标得出来，是因为它看的是**页面文字**「This website uses
#: third-party cookies…」✓ 那边含 `cookies` ✓ —— 两边的判据不一致，从此对齐）。
#: ⇒ `label` 里加上**它自己那截文字**（前 200 字，够装下一整条横幅的正文）。
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
#: 「这个东西**看得见**吗」—— 弹层那几段 JS 共用的一处（原来同意条那段里一份、
#: 「还在不在」那段里又一份，逐字一样：同一件事写了两遍）。
#: 判据：有面积、而且顶边还没划出视口 —— 与 `cdp observe` 看 obstructions 一个口径。
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
    "if(d==='none'||v==='hidden'||v==='collapse')return false;"
    #: ★ 2026-11-06（真事 `job-669c375b51f9` · antigravity）：**点不到的东西不算看得见**。
    #: 现场：页面上叠着**三份** `div.Modal_modal__Abm7m`（377×757）：两份 `opacity:0` +
    #: `pointer-events:none`（元素在里面时计算值继承成 none），真正开着的那份才是 auto。
    #: 旧判据只问 display/visibility ⇒ 隐藏那两份的关闭键也算「可见」⇒ 钩子按 DOM 顺序
    #: 先挑中**隐藏**那份的关闭键 ⇒ `cdp click` 在落点上取不到节点（拒绝点击）⇒
    #: 复核「还有盖住整页的模态」⇒ 真弹窗一直关不掉，脚本被它挡住。
    #: `pointer-events` 是**继承属性**（容器 none ⇒ 子元素计算值也是 none），所以按子元素问
    #: 就够；`opacity` 不继承，另按元素自己的值判（≤0.05 当看不见）。
    "var pe=String(cs.pointerEvents||'');if(pe==='none')return false;"
    "var op=parseFloat(cs.opacity);if(!isNaN(op)&&op<0.05)return false;}"
    "return true;};"
)
_CONSENT_BOX_JS = (
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    + _VIS_JS +
    _CONSENT_ROOTS_JS +
    "for(var i=0;i<all.length;i++){"
    "  var b=all[i];"
    "  if(boxW.test(boxLabel(b))&&vis(b)) return '1';"
    "}"
    "return '';"
)


#: ★ 2026-09-23（用户点的那件事：「最后画面的截图不好分析，运营懵、我们 AI 也懵」）——
#: **失败那一步的现场**，变成机器读得懂的一段字。为什么是字而不是图：视觉模型今天**没配**
#: （`modelRoles.vision` 空 ⇒ `inspect_image` 直接报「不支持图像输入」），而图本身也只是
#: 「给人看」；真正缺的是**没人把现场写成字** ⇒ 运营在图前发呆、AI（探路 / 以后的 Repair）
#: 也只能猜。这一格把现场摆出来：
#:   `ready`    这一页加载完了吗（`loading` ≠ 「页面不对」）
#:   `overlay`  页面上现在还有没有同意类容器（与 `_CONSENT_BOX_JS` 同一条判据）
#:   `at_point` 动作那个坐标上**站着的是谁** —— 「点到的其实是弹层」这句话的全部证据
#:   `texts`    视口里最显眼的三段短文字（一眼认出「这是哪一页」）
#:   `top_url`  活跃页地址；`url` 这一帧自己的地址（子帧里动手时两个不是一回事）
#:   `text`     整页正文前 N 字；`trouble` 这一页是不是风控/空白页（`_page_trouble`，**复用**）
#:   `tabs`     当时所有页/tab；`shot` 失败现场那张图（`common.report_screenshot` 那条路）
#: 判据与 `_cover_once` 同源（`elementFromPoint` + 自己/后代/祖先都算同一个东西）。
#: ⚠️ 只在**没做成**那一步探一次（§13：重跑必须便宜），而且**一次 eval 全给**（不是四次）。
#: ⚠️ ★ 2026-09-29（§4）：这一格**不再只在 --trace 时才有用** —— 生产重跑不落 trace，
#: 现场随日志/上报一起走（见 `Filler._scene` 与 `_run_step` 里那句日志）。
_SCENE_JS = (
    #: ★ 2026-09-28（用户点的那件事：**「这类问题下次不用开窗就能判」**）：再加三样 ——
    #: `dpr` / `vw,vh` / `ua`。为什么：那一趟 `cdp screenshot` 一直报「图 984 设备像素 vs
    #: 视口×dpr = 2952」⇒ 没有这三个数，只能靠开窗去猜「窗口到底多大、报的什么 dpr」；
    #: 有了它们，日志里一眼就能看出「窗口与指纹不一致」。
    "var out={ready:String(document.readyState||''),overlay:'',at_point:'',texts:[],"
    #: ★ 2026-09-29（交接《骨架还缺什么》§4：funchatt 那天 7 条失败**一条现场都拿不到**）：
    #: 再加三样当时定案最值的 —— `top_url` = **活跃页地址**（跨源读不到就是空串，**不编**）、
    #: `url` = 这一帧自己的地址（动作在子帧里时它跟活跃页**不是同一个**，两个都要）、
    #: `text` = **这一页整页正文前 N 字**（N 与 `page_sig` 同口径 = `PAGE_TEXT_CHARS`）。
    "url:'',top_url:'',text:'',"
    "dpr:(window.devicePixelRatio||1),vw:window.innerWidth,vh:window.innerHeight,"
    "ua:String(navigator.userAgent||'').slice(0,160)};"
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    "var vis=function(e){var r=e.getBoundingClientRect();"
    "return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;};"
    "var all=document.getElementsByTagName('*');"
    "for(var i=0;i<all.length;i++){var b=all[i];"
    "var lab=String(b.id||'')+' '+String(b.className||'')+' '"
    "+String(b.getAttribute&&b.getAttribute('aria-label')||'');"
    "if(boxW.test(lab)&&vis(b)){out.overlay=String(b.tagName||'').toLowerCase()"
    "+(b.id?('#'+b.id):'');break;}}"
    "var want=%s;"
    "if(want){var el=null;"
    "for(var k=0;k<all.length;k++){try{if(all[k].matches(want)){el=all[k];break;}}catch(e){}}"
    "if(el){var r=el.getBoundingClientRect();var x=r.left+r.width/2,y=r.top+r.height/2;"
    "var hit=(x>=0&&y>=0&&x<=window.innerWidth&&y<=window.innerHeight)"
    "?document.elementFromPoint(x,y):null;"
    "if(hit&&hit!==el&&!el.contains(hit)&&!hit.contains(el)){"
    "out.at_point=String(hit.tagName||'').toLowerCase()+(hit.id?('#'+hit.id):'')"
    "+'.'+String(hit.className||'').split(' ')[0];}}}"
    "for(var m=0;m<all.length&&out.texts.length<3;m++){var t=all[m];"
    "if(!vis(t))continue;var s=String(t.innerText||'').trim();"
    "if(s&&s.length>=2&&s.length<=40&&t.children.length===0)out.texts.push(s);}"
    "try{out.url=String(window.location.href||'');}catch(e){}"
    "try{out.top_url=String((window.top&&window.top.location"
    "&&window.top.location.href)||'');}catch(e){}"
    "out.text=String((document.body&&document.body.innerText)||'')"
    ".replace(/\\s+/g,' ').slice(0,%d);"
    "return JSON.stringify(out);"
)

#: 「这一段是同意弹层那一步」认哪几个词（**只认动作词**，不认 `close`）。
#:
#: 为什么另有一套词、而不复用上面那两段 JS 的 `btnW`：这里的用途完全不同 ——
#: 上面是**去找一个按钮**（宁可漏，不可错点），这里是**给一个已经找不到的元素定性**
#: （宁可不算，不可把真失败读成跳过）。`close` 在上面那套里是对的（弹层里的关闭按钮
#: 常常就叫 Close），在这里是**最危险的一个**：普通弹窗、抽屉、提示条上的关闭按钮
#: 也叫 Close —— 把「没找到 Close」读成「跳过不算失败」会把真失败抹掉。
#: 所以这里只留**语义上就是「对同意做个决定」**的那几个词。
_CONSENT_STEP_WORDS = ("reject", "accept", "agree", "allow", "deny", "decline",
                       "got it", "cookie", "consent", "gdpr", "privacy")


def _is_consent_step(label: str) -> bool:
    """这一步是不是「点掉同意弹层」（只按**步骤自己的名字**判，不猜）。"""
    text = str(label or "").lower()
    return bool(text) and any(w in text for w in _CONSENT_STEP_WORDS)

#: ★ 2026-11-06（真事 `job-6119866e26d9` · antigravity）：**弹层/同意层那类辅助步** ——
#: 名字或地址里**同时**写着「这是个弹层」（modal/dialog/popup/overlay/consent/cookie/…）
#: 与「这是个关闭/同意动作」（close/dismiss/accept/agree/…）。
#: 这类步骤**不做意图重定位**：声明的地址失效 = 这一屏根本没有那个弹层了 ⇒ 软跳过。
#: 现场：账本那步 `close modal button` 的 `.SubscribeModal_closeIcon__S50rw` 在
#: user-agreement 那一屏不存在 ⇒ 重定位按 aria-label 找到**页面导航里另一颗**同名按钮
#: ⇒ 点开移动端菜单 ⇒ 底下的 footer 链接被 `ul.index_mobile_list` 盖住 ⇒ 下一步
#: 「不许点盖着的」⇒ 整组重试 ⇒ 收摊。**宁可不做**：弹层真还在，下一步会被 `_covered_by`
#: 挡住并如实失败 —— 比点错一颗按钮好。
#: ⚠️ 两个词族**都要**命中才算（`close` 单出现在业务按钮上不触发；`modal` 单出现在地址里
#: 也不触发）—— 只认这一种形状，别把它扩成「带 close 的按钮都软跳过」。
_OVERLAY_WORDS = ("modal", "dialog", "popup", "overlay", "consent", "cookie",
                  "gdpr", "privacy")
_OVERLAY_CLOSE_WORDS = ("close", "dismiss", "accept", "agree", "allow",
                        "reject", "deny", "decline", "got it")


def _is_overlay_step(step, label="") -> bool:
    """这一步是不是「点掉弹层/同意层」（只按**它自己写着的字**判，不猜）。见上。"""
    target = (step or {}).get("target") or {}
    hay = " ".join([
        str((step or {}).get("note") or ""), str(label or ""),
        str(target.get("label") or ""), str(target.get("text") or ""),
        " ".join(str(s) for s in (target.get("selectors") or [])),
    ]).lower()
    return (any(w in hay for w in _OVERLAY_WORDS)
            and any(w in hay for w in _OVERLAY_CLOSE_WORDS))

#: ★ 2026-10-10（用户当天那句：「**执行的时候都检测是否有弹窗**」）：
#: 再看一眼**盖住整页的模态对话框** —— 它不是同意条，但比同意条更硬：
#: 同意条最多盖住一条边，模态底下**什么都点不到**。
#:
#: 现场（topcopdtrends / cardahealth 问卷的生日那一屏，2026-10-10 真站实测）：
#: `div.MuiDialog-root.MuiModal-root` —— position:fixed、z-index 1300、盖满 390x844，
#: **底下还压着 `MuiBackdrop-root`**；backdrop 把点击全吃掉 ⇒ 那一步就成了
#: 「做了、页面没动」（日志里那句 `第 2 步没做成`）。内容是一句挽留话 +
#: `1-800-592-2165` + 「Finish My Assessment」；**唯一该点的是那颗 `<button aria-label="close">`**
#: （实测：点它弹窗就没了、底下的生日页立刻能操作）。
#:
#: 为什么**不能**并进上面那条 `boxW`：那条认的是「**对同意做个决定**」这件事
#: （cookie|consent|gdpr|privacy），而模态里那颗按钮**不一定是**那种 ——
#: 可能是「Finish My Assessment」这种**会真做事**的按钮。所以这个分支**只认明确的关闭控件**，
#: 一颗都找不到就**什么都不点**（宁可不做，也不替人做决定）。
#:
#: 三条窄口径，缺一不可：
#:   ① 容器自己得是**模态语义**（id/class 里有 `dialog|modal`，或 `role=dialog` / `aria-modal=true`）；
#:   ② 它得**盖住大半个视口**（宽高都 ≥80%）—— 小浮标、侧栏抽屉不算
#:      （那些可能真是页面要人看的东西，点掉它同样是「点到别的东西」）；
#:   ③ 里面得有一颗**可见的、明确叫「关闭/拒绝」**的控件（close / dismiss / 关闭 / ×，
#:      或退订弹窗那族 `no thanks` / `not now` / `maybe later` / `skip`，且很短）。
#: ⚠️ 剩下的那点风险与「同意条只有文字」那条**同一个量级**（调用方按文字打标记再点）：
#: 页面上若另有一颗文字恰好也是 `close` 的按钮，标记会命中靠前的那个。
#: 由 `_covered_by` 兜住 —— 点歪了那一步照样会被判成没做成，不会被记成做成了。
#:
#: ★ 2026-10-10 起它是**钩子表里的一条**（`_POPUP_HOOKS` 第二行）：签名 `(all, vis)` ——
#: 用调用方穿好 shadow root 的那份元素表，**不再**吃上一条钩子的局部变量（原来那是个暗扣）。
#:
#: ★ 2026-10-19：关闭键也认 `div`/`span` —— 同意控件不一定是 `button/a/input`（真事
#: compareinsulation 的 CookieScript 把 accept/reject 做成 `<div>`）。`closeW` 的 ≤24 字闸
#: 已经把散文挡在外面，这里不再加第二条长度闸。
#: ★ 2026-11-03（真事 `job-1a42633f4d4d` · smartwool）：订阅弹窗
#: `div.modal.micromodal-slide.signup-modal` 的遮罩 `div.modal__overlay` 是
#: `position:fixed`、盖满视口（765×437）⇒ ② 早就成立；可它**唯一**那颗关闭键是
#: `<button aria-label="No Thanks">`（32×32，X 图标）—— `closeW` 不认「拒绝」这族
#: ⇒ 钩子从不答应，弹窗一路盖住尺寸/加购/结算三步。原话见
#: `runtime/selftest/smartwool-20261103-073854/baseline/*.log`：
#: `第 1 个选择器指的元素被 h2#signup-modal-title.signup-modal__title 盖着`。
#: 窄口径：只在**盖住整页的模态**这一条里加、且要**整句**是拒绝词（`^…$`）——
#: 广告 creative 那条自己那份 `closeW` 不动（它没有「盖满视口」这道前置闸）。
#:
#: ★ 2026-11-03（同一条真站现场）：这一页上**同时**有 17 个「语义是模态 + 盖满视口」
#: 的容器（商品图模态 / 横幅 / 尺码表 / 抽屉…），要点的那个 `div.modal__overlay`
#: 排在很后面。原来那句 `return ''` 写在外层 `for` **里面** ⇒ 扫完**第一个**容器
#: 没找到关闭键就整段收摊，后面的容器一个都不看（这就是「判据全都成立、却从不答应」
#: 的真因）。`return ''` 收到外层循环**之后**。
_MODAL_BOX_JS = (
    "var isModal=function(e){"
    "var lab=String(e.id||'')+' '+String(e.className||'')+' '"
    "+String((e.getAttribute&&e.getAttribute('aria-label'))||'');"
    "var sem=/(dialog|modal)/i.test(lab)"
    "||!!(e.getAttribute&&(e.getAttribute('role')==='dialog'"
    "||String(e.getAttribute('aria-modal'))==='true'));"
    "if(!sem)return false;"
    "var r=e.getBoundingClientRect();"
    "return r.width>=window.innerWidth*0.8&&r.height>=window.innerHeight*0.8;};"
    "var closeW=function(s){s=String(s||'').trim().toLowerCase();"
    "if(!s)return false;"
    "if(s==='×'||s==='✕'||s==='✖')return true;"
    "if(/^(close|dismiss|关闭)/.test(s)&&s.length<=24)return true;"
    "return /^(no,? thanks|no thank you|not now|maybe later|skip)$/.test(s)"
    "&&s.length<=24;};"
    #: ★ 2026-11-06（真事 `job-0ecaf3b90a6a` · antigravity）：**点名的那个模态要有身份** ——
    #: `_modal_holds_target` 要问「就是**这一个**模态吗」。答出去之前先清掉上一轮的标记，
    #: 给这一颗打上 `data-sf-hit=1`；答复形状不动（三格是两套消费端与假 DOM 夹具钉着的
    #: 契约 —— 往里塞第四格 = 全线改判据）。
    "for(var z=0;z<all.length;z++){if(all[z].removeAttribute)all[z].removeAttribute('data-sf-hit');}"
    "for(var m=0;m<all.length;m++){var mb=all[m];"
    "if(!isModal(mb))continue;"
    #: ★ 2026-11-06（真事 `job-669c375b51f9` · antigravity）：**隐藏的那份模态不算遮挡**。
    #: 现场三份同名满屏容器，两份 `opacity:0` + `pointer-events:none`，它们的关闭键
    #: `aria-label` 与真要关的那颗一字不差 ⇒ 先答出去的是隐藏那颗（点不到）⇒ 真弹窗
    #: 一直关不掉。容器自己就藏着 ⇒ 整份跳过。
    "if(!vis(mb))continue;"
    "var mi=(mb.getElementsByTagName?mb.getElementsByTagName('*'):mb.querySelectorAll('*'));"
    "for(var n=0;n<mi.length;n++){var cb=mi[n],ct=String(cb.tagName||'').toLowerCase();"
    "var ctl=(ct==='button'||ct==='a'||ct==='input');"
    "if(!ctl&&ct!=='div'&&ct!=='span')continue;"
    #: ★ 2026-10-19：新加的 div/span **只认叶子** —— 子元素里带文字的是容器，
    #: 点它是点空气（真站 `#cookiescript_buttons` 就是这个形状）。
    "if(!ctl&&cb.children&&cb.children.length){var tc=false;"
    "  for(var q=0;q<cb.children.length;q++){var ch=cb.children[q];"
    "    if(String(ch.innerText||ch.value||'').trim()){tc=true;break;}}"
    "  if(tc)continue;}"
    "if(!vis(cb))continue;"
    #: ★ 2026-11-03（同一条真站现场）：`_VIS_JS` 只看**纵向**有没有划出视口 ——
    #: 这一页上两只 `.drawer` 的关闭键在 `left:1194`（视口只有 780 宽），
    #: 却照样算「可见」⇒ 钩子先把抽屉那颗答出去，订阅弹窗排在后面永远轮不到。
    #: 这里补横向：整颗在视口左边/右边之外的不算（真 DOM 的 rect 一定有 left/right）。
    "var cr=cb.getBoundingClientRect();"
    "if(cr.right<=0||cr.left>=window.innerWidth)continue;"
    "var ca=String((cb.getAttribute&&cb.getAttribute('aria-label'))||'').trim();"
    "var cx=String(cb.innerText||cb.value||'').trim();"
    "if(!(closeW(ca)||closeW(cx)))continue;"
    "if(mb.setAttribute)mb.setAttribute('data-sf-hit','1');"
    "return (cb.id?('#'+cb.id):'TEXT')+'|'+(ca||cx).slice(0,40);}}"
    "return '';"
)

#: ★ 2026-10-23（真事 firstday.com · `job-863ba9a731d7`）：**站点自己的购物车抽屉**
#: 也是「role=dialog + aria-modal=true + 盖满视口 + 一颗 Close shopping cart」——
#: 上面那条通用模态钩子把它当遮挡点掉了，而下一步要点的「CHECKOUT+」**就在抽屉里**
#: ⇒ 自测三版稿都卡在「页面上没找到 CHECKOUT+」。抽屉是流程的一部分，不是遮挡。
#:
#: 判据（三种，任一成立都不点关）：
#:   · **这一步要点的东西就在这个模态里**（`text` / `selector`）—— 把这一步的 target
#:     （text + selectors）交给页面，按「可见元素」找一遍；
#:   · **这个模态刚才就在用**（`flow`）—— 见下面 `data-sf-flow` 那一段：下一屏要隔几秒
#:     才渲出来，按「这一眼」找必然落空，所以「握着过一步」这件事得**记在容器上**；
#:   · **这一屏有一个已经打开的下拉**（`open-dropdown`）。
#: 为什么不能只按「容器叫 cart」：站点给抽屉起什么名字追不完；「里面有没有下一步的目标」
#: 是形状，而且对「任何模态里装的下一步」都成立。
#: ⚠️ 只对「盖住整页的模态」这一行生效（见 `_MODAL_HOOK_NAME`）：同意层照旧先做决定 ——
#: 同意层里不会有「下一步要点的东西」。
#: ⚠️ 目标两样都没有（没写 text、也没 selectors）⇒ 返空 = **照旧清弹层**（不猜）；但
#: `flow` 与 `open-dropdown` 两格**不看目标**——它们说的是「这一下清扫会毁掉流程」。
_MODAL_HOLDS_TARGET_BODY = (
    #: 测试替身靠这个标记认这一段（`tests/test_template.py` 的 STUB_COMMON）——
    #: 它不执行 JavaScript，只能按标记回一句预设答复。
    "var sfModalHolds=1;"
    "var want=__WANT__,sels=__SELS__;"
    "var norm=function(s){return String(s||'').replace(/\\s+/g,' ').trim().toLowerCase();};"
    "var isModal=function(e){var lab=String(e.id||'')+' '+String(e.className||'')+' '"
    "+String((e.getAttribute&&e.getAttribute('aria-label'))||'');"
    "var sem=/(dialog|modal)/i.test(lab)"
    "||!!(e.getAttribute&&(e.getAttribute('role')==='dialog'"
    "||String(e.getAttribute('aria-modal'))==='true'));"
    "if(!sem)return false;var r=e.getBoundingClientRect();"
    "return r.width>=window.innerWidth*0.8&&r.height>=window.innerHeight*0.8;};"
    #: ★ 2026-11-06（真事 `job-6119866e26d9` · antigravity）：**这一屏有一个已经打开的下拉**
    #: ⇒ 也当「这个模态握着这一步」。下拉的选项渲染在 body 的 portal 里、**不在模态 DOM 里**
    #: ⇒ 上面那两格按「在不在里面」判是 false ⇒ 清弹层去点模态的关闭键 ⇒ 那一下等于点
    #: 页面别处，下拉当场关掉（`.rc-select-item-option[title="10K–100K"]` 等 45 秒等不到）。
    #: 判据只认下拉自己声明的开合（`role=combobox` + `aria-expanded=true`，rc-select/antd
    #: 那一族都这么写）—— 不按站点类名认，也不拿「页面上有个 listbox」当开
    #: （常驻列表会把同意弹层永远清不掉）。放在 `!want&&!sels.length` 那道闸**之前**：
    #: 这一步有没有目标都一样 —— 下拉开着时，任何清扫点击都只会把它点没。
    "for(var q=0;q<all.length;q++){var cb=all[q];"
    "if(!vis(cb)||!cb.getAttribute)continue;"
    "if(cb.getAttribute('role')!=='combobox'"
    "||String(cb.getAttribute('aria-expanded'))!=='true')continue;"
    "return '1|open-dropdown';}"
    #: ★ 2026-11-06（真事 `job-0ecaf3b90a6a` · antigravity）：**先认「是哪一个模态」**。
    #: `_MODAL_BOX_JS` 答出去之前把那一颗打了 `data-sf-hit=1`；这里只认它。
    #: 原先这几格扫的是**页面上所有模态** —— 促销弹层被点名要清，可「这一步的目标」在
    #: **底下那个表单模态**里 ⇒ 判成「目标在里面」⇒ 不清促销层 ⇒ 它一路盖着下一步
    #: （trace 原话：`div.Content_promo__hcTsr 一直盖着「没写名字的元素」`）。
    #: 清的是哪一颗，判据就只能看哪一颗。
    "var hit=null;"
    "for(var h=0;h<all.length;h++){var hb=all[h];"
    "if(!vis(hb)||!hb.getAttribute)continue;"
    "if(String(hb.getAttribute('data-sf-hit'))==='1'){hit=hb;break;}}"
    "if(!hit)return '';"
    "var rel=function(x){try{return x===hit||(hit.contains&&hit.contains(x))"
    "||(x.contains&&x.contains(hit));}catch(err){return false;}};"
    #: ★ 2026-11-06（真事 `job-54fbc668f03d` · homejolly）：**这个模态刚才就在用** —— 上一屏
    #: 的目标算进了「在它里面」，容器上留了 `data-sf-flow=1`；下一屏要隔几秒才渲出来，
    #: 按「这一眼」找必然落空。标记只对**被点名那一颗**（或与它同一条祖先链）有效 ——
    #: 别的模态上挂着它，不代表这一颗是流程本身。
    "for(var f=0;f<all.length;f++){var fb=all[f];"
    "if(!vis(fb)||!fb.getAttribute)continue;"
    "if(String(fb.getAttribute('data-sf-flow'))==='1'&&rel(fb))return '1|flow';}"
    "if(!want&&!sels.length)return '';"
    "var inner=(hit.getElementsByTagName?hit.getElementsByTagName('*'):hit.querySelectorAll('*'));"
    "for(var j=0;j<inner.length;j++){var e=inner[j];if(!vis(e))continue;"
    "var t=norm(e.innerText||e.value||(e.getAttribute&&e.getAttribute('aria-label'))||'');"
    #: ★ 2026-11-06：`t` 已经过 `norm`（压空白 + casefold），`want` 没有 ⇒ 原先
    #: `t===want` 拿 "checkout+" 比 "CHECKOUT+"，**永不相等**（"1|text" 那一支从来没响过 ——
    #: firstday 那条只靠 selector 那半撑着，替身测试不跑 JS 所以一直没露）。两边同一把尺子。
    "if(want&&t===norm(want)){if(hit.setAttribute)hit.setAttribute('data-sf-flow','1');"
    "return '1|text';}"
    "for(var k=0;k<sels.length;k++){try{if(e.matches(sels[k])){"
    "if(hit.setAttribute)hit.setAttribute('data-sf-flow','1');return '1|selector';}}catch(err){}}"
    "}"
    "return '';"
)


def _modal_holds_target_js(target):
    """把这一步的目标（text + selectors）填进 `_MODAL_HOLDS_TARGET_BODY`。

    ⚠️ 用 `.replace` 而**不用 `%` 格式化**：这段 JS 里有 `80%` 这种百分号
    （`_popup_probe_js` 那段注释记着同一个坑）。
    """
    target = target or {}
    want = str(target.get("text") or "").strip()
    sels = [str(s) for s in (target.get("selectors") or []) if s]
    return (_VIS_JS + _CONSENT_ROOTS_JS + _MODAL_HOLDS_TARGET_BODY
            .replace("__WANT__", json.dumps(want, ensure_ascii=False))
            .replace("__SELS__", json.dumps(sels, ensure_ascii=False)))


#: 「盖住整页的模态」那一行在表里叫什么 —— `_clear_obstructions` 要靠它认出
#: 「这一步的目标就在它里面，别点关」只对**这一行**生效（见 `_MODAL_HOLDS_TARGET_BODY`）。
#: ⚠️ 必须与 `_POPUP_HOOKS` 里那一行的名字**一字不差**：表里仍写字面量（测试的
#: `_popup_hook_rows` 是按字面量解析表序的）；新用例
#: `test_a_modal_that_holds_the_step_target_is_not_dismissed` 钉着两边对得上。
_MODAL_HOOK_NAME = "盖住整页的模态"


#: ★ 2026-10-13（用户裁定「都收吧」；真事 `job-708dfae74b73` · lovesac 的 Fides 弹层）：
#: **盖满视口的同意弹层** —— 上面那条「同意条」是**按容器身份**认的（`cookie|consent|gdpr|privacy`），
#: 这条改按**形状**认，因为供应商的名字追不完：Fides 那个容器叫 `div.fides-modal-overlay`
#: / `div#fides-banner`，名字里一个同意词都没有 ⇒ 两条钩子都从它身上滑过去（模态那条又要求
#: 有一颗写着 `close/×` 的按钮，而它有 `Allow All`/`Reject All`）。
#: 现场代价：产物每一步都在填 Wufoo 的那 12 格，而重找出来的 **16 个候选全部
#: 「被 div.fides-modal-overlay 盖着 —— 这一下不点」** ⇒ 撞 600 秒上限，整趟白跑。
#:
#: 三条窄口径（缺一不可，与模态那条同一套「宁可不做，不可错点」）：
#:   ① **盖住大半个视口**（宽高都 ≥80%）—— 形状就是判据：小浮标、抽屉不算；
#:   ② 容器自己**带同意语境**（`boxLabel` 里的前 200 字命中 cookie/consent/privacy/隐私/跟踪…），
#:      **或**里面那颗按钮是**强同意短语**（`allow all` / `reject all` / `accept cookies`
#:      / 全部接受 / 全部拒绝）—— `Accept` 一个字不算：那是「Accept offer」那种业务按钮；
#:   ③ 按钮文字得是**对同意做个决定**（优先点「拒绝 / 不同意」那一颗：它副作用最小，
#:      与账本里 `#fides-reject-all` 那一版选择同一条路）。
#:
#: ★ 2026-10-19：与「同意条」同一道 tag 白名单（`div`/`span`）+ 同一条 ≤40 字闸（只查新 tag）。
#: 这一条扫的是**满视口容器的全部后代**，散文更像按钮，所以那道长度闸在这里更要紧。
_CONSENT_MODAL_JS = (
    #: ★ 2026-11-06（真事 `job-82c14d34db71` · antigravity）：**同意词要锚在开头**。
    #: 原先 `agree` 是子串匹配 ⇒ 页脚那颗 `USER AGREEMENT` 链接（还有同意条里的
    #: `PRIVACY POLICY` 一类）被当成「同意控件」点了，页面当场跳到用户协议页，
    #: 后面「Creator Recruitment」当然找不到。判据：控件文字**以决定动词开头**
    #: （可选 "yes," / "I" 前缀）才算 —— "Accept all" / "I agree" / "同意" 照过，
    #: "USER AGREEMENT" 不过。
    "var decW=/^\\s*(?:yes,?\\s+)?(?:i\\s+)?(reject|decline|deny|accept|agree|allow|同意|接受|拒绝|不同意)/i;"
    "var badW=/(reject|decline|deny|拒绝|不同意)/i;"
    "var ctxW=/(cookie|consent|gdpr|privacy|tracking|同意|隐私|跟踪)/i;"
    "var strongW=/(reject|accept|allow|deny|decline)\\s+(all|cookies?)|全部接受|全部拒绝/i;"
    "var full=function(e){var r=e.getBoundingClientRect();"
    "return r.width>=window.innerWidth*0.8&&r.height>=window.innerHeight*0.8;};"
    "var out='',outT='';"
    "for(var i=0;i<all.length;i++){var box=all[i];"
    #: ★ 2026-10-19（真站 compareinsulation）：`full()` 会命中 `body`/`html` —— 它们是
    #: 满视口的**根节点**，不是「盖满视口的弹层」。排掉它们，这一行才会按设计卡在
    #: 真正容器的尺寸闸（那站 `#cookiescript_injected` 只占视口高 28%），落到第 2 行。
    "  var bt=String(box.tagName||'').toLowerCase();"
    "  if(bt==='body'||bt==='html')continue;"
    "  if(!vis(box)||!full(box))continue;"
    "  var ctx=ctxW.test(boxLabel(box));"
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


#: ★ 2026-10-19（真事 wildnutrition）：站点自己的 **markets offcanvas 抽屉** 在新会话里
#: 每单都会打开，而它的关闭键被 sticky header / hero 盖住 —— `cdp click` 的落点判据
#: 会拒绝（那是对的：不点被盖住的东西），于是脚本的每一步都被 `div.OffcanvasDimmer`
#: 挡在外面。这一条**只认 `data-drawer-names="markets"` 的 offcanvas**，由钩子
#: **自己用 JS 的 `.click` 关掉**（绕开 hit-testing），答复 `JS|done`；消费端看到 `JS`
#: 就不再走 cdp click，但**照样复核**（还在 ⇒ 记没关掉）。
#: ⚠️ 只认 markets：站点别的抽屉（promo / filters 之类）不归这一条管 ——「宁可不做」。
_MARKETS_OFFCANVAS_JS = (
    "var cls=function(e){return String((e&&(e.className||(e.getAttribute&&e.getAttribute('class'))))||'');};"
    "var isM=function(e){return String((e.getAttribute&&e.getAttribute('data-drawer-names'))||'').toLowerCase()==='markets';};"
    "var hasM=false;"
    "for(var i=0;i<all.length;i++){var box=all[i];"
    "if(!isM(box)||!vis(box))continue;hasM=true;"
    "var ins=(box.getElementsByTagName?box.getElementsByTagName('*'):[]);"
    "for(var j=0;j<ins.length;j++){var b=ins[j];"
    "if(!/(^|\\s)js-offcanvas-close(\\s|$)/.test(cls(b))||!vis(b)||!b.click)continue;"
    "try{b.click();}catch(e){continue;}"
    "return 'JS|done';}}"
    "if(!hasM)return '';"
    "for(var k=0;k<all.length;k++){var d=all[k];"
    "if(!/OffcanvasDimmer/.test(cls(d))||!vis(d)||!d.click)continue;"
    "try{d.click();}catch(e){continue;}"
    "return 'JS|done';}"
    "return '';"
)

#: ★ 2026-10-19（真事 compareinsulation）：同意控件不一定是 `button/a/input` —— CookieScript
#: 的 accept/reject 是 `<div>`。tag 白名单加 `div`/`span`；**新加的两种 tag 还要过一道 ≤40 字**：
#: 同意容器里那段 "By clicking ACCEPT ALL…" 的散文也命中 `btnW`，长度闸把它挡在动作之外
#: （`button/a/input` 的老判据不动 —— 它们本来就是控件）。
_CONSENT_HOOK_JS = (
    # ⚠️ 用 `getElementsByTagName('*')` + 属性自检，**不走**「按选择器一步查」的那种写法 ——
    # 产品里凡是要按选择器找元素的地方一律避开它（`tests/test_template.py` 的
    # 「动作全走 cdp」那条把它钉死了；而这里本来就只是**读**，`matches()` 一样能读）。
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    #: ★ 2026-11-06：同 `decW` 那条 —— 锚在开头，别让 `USER AGREEMENT` 这种链接混进来。
    "var btnW=/^\\s*(?:yes,?\\s+)?(?:i\\s+)?(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;"
    "for(var i=0;i<all.length;i++){"
    "  var box=all[i];"
    "  if(!boxW.test(boxLabel(box))||!vis(box)) continue;"
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


#: ★ 2026-10-16：**广告 creative** 那一型（blavity 2026-10-10 / kotzheating 2026-10-09 两份产物
#: 的判据交集）。它与上面三条的差别：**没有**模态语义、也**不一定是**同意那件事 ——
#: 认它靠「**有一颗明确的关闭键**」这件事本身。
#: ⚠️ 三条窄口径（缺一不可，与前三条同一套「宁可不做，不可错点」）：
#:   ① 关闭语义 —— **三条路都收窄**（Ruling 22；不许变成「凡带 close 字样就点」）：
#:      · 属性**名**带 close（`data-close="1"` 那种）且值 ∈ {"",true,1,close}；
#:      · 属性**值****精确等于** close/dismiss/关闭 —— blavity 现场 `data-click="close"`
#:        就是这一条（`close` 是它的**值**、名字叫 `data-click`）；
#:      · `id` / `class` 命中关闭词 —— **子串**（`#cwCloseButton` 那种驼峰靠词边界正则会漏；
#:        `.op2-close` 两边都中）；
#:      · `aria-label` / 文字 —— **锚定** `^(close|dismiss|关闭)`（散文里出现 close 不算：
#:        「Do not close this window」是**别关**的意思 —— Ruling 23 抓的就是这一条），
#:        或 `×/✕/✖`，且要**短**；
#:   ② 它自己**可见**、尺寸 ≥8×8、**中心点的落点是它自己/祖先/后代**（blavity 那把尺子：
#:      同页 6 个候选里只有 1 个可见，直接点会点空气）；
#:   ③ 找不到 ⇒ 空串（**正常情况**）。
#: ★ Ruling 22（2026-10-16）：①的后两条路（值那条 / id·class·aria·文字那条）**必须**再
#: 过一道 **≤120×120** 的尺寸闸（照 2026-10-09 那一刀的界）—— 否则 `close-banner-wrapper`
#: 那种**容器**会被当成关闭键点掉（假 DOM 里实测：没闸的时候它真答 `#close-banner-wrapper|close`）。
#: ①的第一条（属性名那条）**不要**闸：它本来就够 specific（名字里带 close 的东西不会是容器）。
#: ## 未验（真弹窗上没再次观察到）
#: 2026-10-16（Task 6）：照 `docs/probes/2026-10-10-blavity-ad-popup/README.md`「复现第 7 条」
#: 在 Bit `192.168.1.222` 上开窗重试了一次**只读**观察（21:29:51 开窗 → 21:39:27 关窗，
#: 20 眼、每眼约 27 秒，第 3 眼起每 3 眼滑动诱发一次）：那段窗口里**一次都没再弹** ——
#: `[data-click="close"]` 候选 20/20 眼恒为 0（`{"n":0,"vis":[]}`），钩子 19/20 眼恒返空串
#: （剩 1 眼是一次 `DOM.documentUpdated` 传输错），与 10-10 那次「点掉之后 28 分钟没再弹
#: （疑频次抑制）」同形。⇒ 这一行是**照两份产物的判据交集收窄出来的，真弹窗上没再次观察到**
#: （**不许**当成验过了）。原始读数见报告的「前置验证」那一节。
#: ⚠️ 另按真站那份现场（README 表格：`<button id="bx-close-inside-…" data-click="close">`）
#: 拿 node + 假 DOM 核过语义：那颗按钮**不带 aria-label、也没有文字**时，光靠「属性名带
#: close」**命不中**（名字是 `data-click`，`close` 是它的**值**）—— 要靠 aria-label / 文字
#: 那两条才认得出。如实记在这儿，**不**在这条注释里偷偷放宽判据（等裁决）。
#: ⚠️ Ruling 22 之后那条「属性值精确等于 close」就是为它加的；Ruling 23 又把 aria-label 那支
#: 从子串**收回锚定**（散文里出现 close 不算）。
#: ⚠️ 尺寸闸 **≤120×120 是一个「接受的赌」**（Ruling 24）：那个界是照 2026-10-09 产物那一刀取的，
#: **真控件 rect 从来没量到过**（10-10 只量到候选计数），而真弹窗这一刀又观察不到
#: ⇒ **不编数**、也不假装它量过：这条界就是这么来的，真站上它是**假设**。
#: ⚠️ **已量到误答**（2026-10-17 订正，读这段的人别再以为这一行的风险只是「没验过」）：
#: 本刀真站那一趟（实测文档 §1.3）它**照着判据答出来并点了** —— wildnutrition 那一屏上
#: 它答 `广告 creative|TEXT|Close filters drawer`，把站点自己的**抽屉控件**当成了关闭键。
#: ⇒ 终审 **Ruling 30**：这一行**只报不点**（表里那一格带着 `_REPORT_ONLY` 标志，
#: 认得出、账上记一行，**不动手**），直到真 creative 上验过「可见 + 有落点」。
_AD_CREATIVE_JS = (
    # ①-a 属性**名**带 close（值可信）—— 本来就够 specific，**不加**尺寸上限
    "var closeAttr=function(e){var as=e.attributes||[];"
    "for(var i=0;i<as.length;i++){var n=String(as[i].name||'').toLowerCase();"
    "var v=String(as[i].value||'').trim().toLowerCase();"
    "if(/(^|[-_])close([-_]|$)/.test(n)&&(v===''||v==='true'||v==='1'||v==='close'))return true;}"
    "return false;};"
    # ①-b 属性**值精确等于**关闭词 —— blavity 的 `data-click="close"` 走这一条
    "var closeVal=function(e){var as=e.attributes||[];"
    "for(var i=0;i<as.length;i++){var v=String(as[i].value||'').trim().toLowerCase();"
    "if(v==='close'||v==='dismiss'||v==='关闭')return true;}return false;};"
    # ①-c `id` / `class` 命中关闭词 —— 子串（它们是**标识符**：`#cwCloseButton` 这种驼峰，
    #      词边界正则会漏；`.op2-close` 两边都能中）
    "var closeWord=function(s){s=String(s||'').toLowerCase();"
    "return s.indexOf('close')>=0||s.indexOf('dismiss')>=0||s.indexOf('关闭')>=0;};"
    # ①-c `aria-label` / 文字：**锚定**（散文里出现 close 不算 —— 「Do not close this window」
    #      是**别关**的意思；Ruling 23 抓的就是这一条），× 那三个单字符也算，且要短。
    "var closeT=function(s){s=String(s||'').trim().toLowerCase();if(!s)return false;"
    "if(s==='×'||s==='✕'||s==='✖')return true;"
    "return /^(close|dismiss|关闭)/.test(s)&&s.length<=24;};"
    #: ★ 2026-10-19：**站点锚**（表里第 4 格）。空串 = 不设锚（broad 判据，仍只报不点）；
    #: 站点补丁填了 `[data-click="close"]` / `.op2-close` 后，只有匹配它的候选才算 ——
    #: 这是把「站点自己的 Close filters drawer」挡在外面的那道闸。`matches` 只读；
    #: 选择器写坏了就 catch 成 false（宁可不做）。
    "var anchored=function(e){if(!anchor)return true;"
    "try{return !!(e.matches&&e.matches(anchor));}catch(err){return false;}};"
    # ② 逐元素：可见 + ≥8×8 + **落点那一摞里没有别的元素排在它前面**（＝它没被盖着）
    #    —— 只在**有落点**的那一个上动手（blavity 那把尺子：同页 6 个候选里只有 1 个可见，
    #    直接点会点空气）。
    # ★ Ruling 27（2026-10-17 终审）：这一格改用**与 `_COVER_JS` 同一把尺子**（整摞、
    #   跳过 `pointer-events:none`、看第一个**别的**元素）。原来那版是「复数
    #   `elementsFromPoint` + `any(自己/祖先/后代)`」—— 被盖着的候选**仍然在那一摞里**
    #   ⇒ 这条判据**挡不住它**（Task 7 实施者量到的口径差：blavity 那刀用的是单数
    #   `elementFromPoint`）。⚠️ 两把尺子不许分家：改这里就要照 `_COVER_JS` 的写法改。
    "for(var i=0;i<all.length;i++){var e=all[i];"
    "var tg=String(e.tagName||'').toLowerCase();"
    "if(tg!=='button'&&tg!=='a'&&tg!=='div'&&tg!=='span'&&tg!=='input')continue;"
    "if(!vis(e))continue;"
    "var r=e.getBoundingClientRect();"
    "if(r.width<8||r.height<8)continue;"
    "var x=r.left+r.width/2,y=r.top+r.height/2;"
    "if(x<0||y<0||x>window.innerWidth||y>window.innerHeight)continue;"
    "var mark=(e.getAttribute&&e.getAttribute('aria-label'))||'';"
    "var txt=String(e.innerText||e.value||'').trim();"
    "var cls=String((e.getAttribute&&e.getAttribute('class'))||e.className||'');"
    # ★ Ruling 22：①-b / ①-c 那两条路**必须**过 ≤120×120 这道闸（不然容器会被点掉）；
    #   `small` 只挡后两条，①-a 不挡。
    "var small=(r.width<=120&&r.height<=120);"
    "if(!(closeAttr(e)||(small&&(closeVal(e)||closeWord(e.id)||closeWord(cls)"
    "||closeT(mark)||closeT(txt)))))continue;"
    "if(!anchored(e))continue;"                  # ← 站点锚（空串 = 不过滤）
    "var stack=document.elementsFromPoint(x,y)||[],covered=false;"
    "for(var k=0;k<stack.length;k++){var h=stack[k];var pe='';"
    "try{pe=getComputedStyle(h).pointerEvents||'';}catch(err){}"
    "if(pe==='none')continue;"
    #: ★ 2026-10-19：影子宿主在 `elementsFromPoint` 里只以**宿主**出现，而
    #: `host.contains(影子里的 e)` 是 **false** —— 必须看 `host.shadowRoot.contains(e)`；
    #: 否则影子里的 ad creative 会被误判成「被盖着」（合成页 `data:` + open shadow 复现）。
    #: `_COVER_JS` 不遍历 shadow（找不到目标就返回空 = 静默放行），这里只补 ad 的落点尺子。
    "if(h===e||e.contains(h)||h.contains(e)"
    "||(h.shadowRoot&&h.shadowRoot.contains(e)))break;"
    "covered=true;break;}"
    "if(covered)continue;"                   # ← 被盖着 ⇒ 不认（宁可不做）
    "return (e.id?('#'+e.id):'TEXT')+'|'+(mark||txt).slice(0,40);}"
    "return '';"
)


#: ★ 2026-10-17（终审 C2 · Ruling 30）：表里那一行的**第三格 = 这个标志** ⇒ 这一行
#: 「认得出，但**不点**」。真站上已经量到误点（实测 §1.3），而「加容器锚」那把尺子对
#: 这一型**还没有正向证据** ⇒ **不点**是唯一「不需要再验就不会做错事」的那条路。
_REPORT_ONLY = "report_only"

#: ★ 2026-10-19：点完复核的**短轮询** —— 同意层的关闭常常是异步的（真站 compareinsulation：
#: 点 accept 之后紧接的那一次再探还会看到它，再晚一点点才没了）。只在「复核还答**可点**行」
#: 时等；返空 / 只答 `_REPORT_ONLY` 都立即收（那两件事都已经有结论了）。
POPUP_RECHECK_WAIT_SECONDS = 1.0
POPUP_RECHECK_POLL_SECONDS = 0.2


#: ★ 2026-10-10（用户问的那句：「假如会出现某些弹窗的钩子函数」）：**弹窗钩子表**。
#:
#: 这一段原来是一根用 `+` 拼起来的字符串（同意条那段 + 模态那段 + 兜底 `return ''`）：
#: 加一类弹窗得动那段拼接，还得当心别踩到上一条的局部变量 —— 模态那段就**偷偷用着**
#: 同意条那段里定义的 `all` / `vis`（那段哪天被删掉或挪走，模态会**静默**失灵）。
#: 现在**加一类弹窗 = 表里加一行**。
#:
#: 一条钩子 = 一个**函数体**，签名 `(all, vis)`：
#:   `all`  **穿好 shadow root 的全部元素**（`_CONSENT_ROOTS_JS` 走一遍 DOM，所有钩子
#:          共用这一份 —— 不各自再走一遍；这条探针挂在**每一步动手之前**，贵不起）
#:   `vis`  「看得见吗」（`_VIS_JS`）
#: 返回 `"<选择器|TEXT>|<按钮文字>"`：选择器 = 直接点它；`TEXT` = 那颗按钮只有文字、
#: 没有 id ⇒ 调用方按文字打标记再点（`_MARK_CONSENT_BY_TEXT_JS`）。
#: **认不出就返回空串** —— 空串是正常情况（页面上没有这类东西），不是失败。
#:
#: 写新钩子时照抄这三条纪律：
#:   ① **表里的次序就是优先级**：第一个答应的那个被点掉，后面的不再问；
#:   ② **宁可不做，不可错点**：找不到就返回空串，点歪了由 `_covered_by` 兜
#:      （它保证「点了个寂寞」不会被记成做成了）；
#:   ③ 钩子只许**读**页面：动手那一下一律交回选择器，由调用方走 `cdp click`（§5.2）。
#: 每一条的「为什么这么窄」写在它自己的注释里（下面三条就是）。
#: ★ 2026-10-13：**次序 = 优先级** —— 「盖满视口的同意弹层」放最前（它最硬：底下什么都点不到），
#: 然后是窄的同意条，最后是只认「关闭」控件的通用模态。
#:
#: ★ 2026-10-17（终审 C2 · Ruling 30）：**行可以带第三格 = 「报但不点」的标志**
#: （`(名字, 钩子 JS)` 是普通行，`(名字, 钩子 JS, _REPORT_ONLY)` 是「认得出、但不动手」）。
#: 为什么要它：真站上已经**量到误点**（实测 §1.3：第 4 行把站点自己的抽屉控件
#: `Close filters drawer` 当成了关闭键）⇒ 那一型在拿到真站正向证据之前，**只报不点**。
#: ⚠️ 标志**按名字回来**（`_popup_report_only(who)`），**答复的形状一个字符都不动** ——
#: 那三格被两套消费端的 `split("|", 2)` 与十条假 DOM 夹具钉着，动它 = 全线改判据。
_POPUP_HOOKS = (
    ("盖满视口的同意弹层", _CONSENT_MODAL_JS),
    ("同意条", _CONSENT_HOOK_JS),
    ("盖住整页的模态", _MODAL_BOX_JS),
    #: ★ 2026-10-19：**markets offcanvas**（wildnutrition 那型）—— 关闭键被 sticky header
    #: 盖住，`cdp click` 点不到；这一条自己用 JS 点掉，答复 `JS|done`。排在模态之后、
    #: 广告 creative 之前：它比模态窄（只认 `data-drawer-names="markets"`），但比 creative
    #: 硬（它盖住的是整页，而且站点自己就有一颗关闭键）。
    ("市场/地区抽屉", _MARKETS_OFFCANVAS_JS),
    #: ★ 2026-10-16：广告 creative（blavity 那型）—— 排在**最后**：它比同意条软、也比模态软
    #: （它的容器**没有**语义，只靠「有一颗明确的关闭键」）。判据见 `_AD_CREATIVE_JS`。
    #: ★ 2026-10-17（Ruling 30）：**降级为「只报不点」**，直到真 creative 上验过
    #: 「可见 + 有落点」为止（消费端照 `_REPORT_ONLY` 只记一行 INFO，见 `_clear_obstructions`）。
    #: ★ 2026-10-19（**毕业**）：row 不再只报不点 —— **第三格 = 站点锚**（CSS 选择器）。
    #: 已知的两个锚取并集：blavity `[data-click="close"]` / kotzheating `.op2-close`。
    #: 真站证据：blavity 10-10 真弹窗那颗 close 点掉、kotzheating 10-09 真 `.op2-close`
    #: 浮层点掉（都用的是这两个精确控件）；合成锚测试证明站点自己的 `Close filters drawer`
    #: / 没有锚的 `TEXT|×` 会被挡在外面。第四格仍保留「只报不点 + 锚」的写法，留给以后
    #: 还想先观察的站点。
    ("广告 creative", _AD_CREATIVE_JS, '[data-click="close"], .op2-close'),
)


def _popup_report_only(name):
    """这一行是不是「**报但不点**」（表的第三格，Ruling 30）。认不出来 = 不是（不猜）。

    ⚠️ 标志**按名字查回**，答复的**形状不动**：`_POPUP_PROBE_JS` 照旧答
    `"<钩子名>|<选择器|TEXT>|<按钮文字>"` 三格（那三格是两套消费端与十条假 DOM 夹具
    钉着的契约；往答复里塞第四格 = 全线改判据，且**探针/夹具那一层钉的是「认得出哪一颗」，
    不是「点不点」**）。表里那一行同时带着名字与标志 ⇒ 名字回来 = 标志也就回来了。
    """
    for row in _POPUP_HOOKS:
        if row[0] == name:
            return len(row) > 2 and row[2] == _REPORT_ONLY
    return False


def _popup_probe_js(hooks=None):
    """把表拼成**一段**探针 JS（`_POPUP_PROBE_JS` 就是它拼出来的）。

    为什么拼成一段、而不是一条钩子一次 eval：它挂在**每一步动手之前**（`_perform` 里那处），
    往返次数不许随钩子条数涨 —— 一次问完，第一个答应的就 `return`，后面的不再问。

    名字（表里的第一格）**跟着答复一起回来**：日志要照它说「把『盖住整页的模态』点掉了」——
    原来那句话一律说「同意弹层」，模态被点掉时也是（读日志的人会去找错的东西）。
    ⚠️ 行是**两格或三格**（第三格 = `_REPORT_ONLY`，见 `_POPUP_HOOKS`）⇒ 这里只取前两格；
    带标志的那一行照旧会被问、照旧会答（「报」这一半就是靠它）。
    """
    out = [_CONSENT_ROOTS_JS, _VIS_JS]      # `all` / `vis`：只算一遍，所有钩子共用
    for row in (_POPUP_HOOKS if hooks is None else hooks):
        name, js = row[0], row[1]
        #: ★ 2026-10-19：**站点锚**。行形有三种：
        #:   `(name, js)` ⇒ 可点、不设锚；`(name, js, _REPORT_ONLY)` ⇒ 只报不点、不设锚；
        #:   `(name, js, _REPORT_ONLY, anchor)` ⇒ 只报不点 + 锚；
        #:   `(name, js, anchor)` ⇒ **可点 + 锚**（第三格不是标志就是锚）。
        #: 所有钩子都多收一个 `anchor` 参数；不用它的钩子直接忽略（JS 允许多传实参）。
        anchor = ""
        if len(row) > 3:
            anchor = row[3]
        elif len(row) > 2 and row[2] != _REPORT_ONLY:
            anchor = row[2]
        #: ⚠️ 钩子的 `return` 只在**它自己那个函数**里有效，而「第一个答应的就此打住」
        #: 要的是**探针这一层**的 return ⇒ `if(hit)` 那半句必须留在外面这一层。
        #: （写成 `(function(){…if(hit)return…})()` 的话，那一下只返回给那个空壳、
        #: 探针照样一路走到兜底的 `return ''` —— 2026-10-10 拿假 DOM 真跑一遍才逮到，
        #: 替身测试**测不出来**：它根本不执行这段 JS。）
        #: ⚠️ 拼接而**不用 `%` 格式化**：钩子是别人写的 JS，里面完全可能有 `width:100%`
        #: 这种百分号 —— 那会在 import 期炸出一个跟钩子八竿子打不着的格式错误。
        out.append("var hit=(function(all,vis,anchor){" + js + "})(all,vis,"
                   + json.dumps(anchor, ensure_ascii=False) + ");"
                   "if(hit)return " + json.dumps(name, ensure_ascii=False) + "+'|'+hit;")
    out.append("return '';")
    return "".join(out)


_POPUP_PROBE_JS = _popup_probe_js()

#: 按文字给同意按钮**打标记**（只有文字的那种）—— 与产物里「按占位符填」同一招：
#: 找用 JS、点还是走 `cdp click`（§5.2），标记属性用完就清。
#:
#: ★ 2026-10-19：tag 白名单与三条钩子同源 —— 钩子答 `TEXT` 的 div/span 要靠这里落点；
#: 不放它，探针答得出来、动作永远落不下去（真站上就是「没敢点」）。
#: 这里是**精确文字匹配**（`t===want`），不需要长度闸。
_MARK_CONSENT_BY_TEXT_JS = (
    "var want=%s;"
    #: ★ 2026-11-06（真事 `job-669c375b51f9` · antigravity）：**只标记看得见、点得到的那一颗**。
    #: 现场：三份同名模态里两份是隐藏的（`pointer-events:none`），它们的关闭键 `aria-label`
    #: 与真弹窗那颗**一字不差** ⇒ 按 DOM 顺序先标记到隐藏那颗 ⇒ `[data-sf-consent=1]`
    #: 点下去落到空气上、真弹窗照旧盖着。判据与探针同一把尺子（`_VIS_JS`）。
    + _VIS_JS + _CONSENT_ROOTS_JS +
    "var hit=null;"
    "for(var i=0;i<all.length;i++){var b=all[i],tag=String(b.tagName||'').toLowerCase();"
    "if(tag!=='button'&&tag!=='a'&&tag!=='input'&&tag!=='div'&&tag!=='span')continue;"
    "if(!vis(b))continue;"
    "var t=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "if(t===want){hit=b;break;}}"
    "if(!hit)return 'none';"
    "for(var k=0;k<all.length;k++){if(all[k].removeAttribute)all[k].removeAttribute('data-sf-consent');}"
    "hit.setAttribute('data-sf-consent','1');return 'marked';"
)



def _clean_eval(raw):
    """cdp eval 的 stdout 是 JSON（字符串会带引号）；cobra 的报错行会混在 stderr 里。"""
    text = (raw or "").strip()
    if not text or text.startswith(("Error:", "ERROR:", "Usage:", "Flags:")):
        return ""
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text.strip('"')
    return text


def _format_date(day, month, year, fmt):
    out = []
    for part in re.split(r"([/\-\.])", str(fmt or "")):
        if not part:
            continue
        if part in ("/", "-", "."):
            out.append(part)
            continue
        token = part.upper()
        if token == "D":
            out.append(str(day))
        elif token == "DD":
            out.append("%02d" % day)
        elif token == "M":
            out.append(str(month))
        elif token == "MM":
            out.append("%02d" % month)
        elif token == "YYYY":
            out.append(str(year))
        elif token == "YY":
            out.append(str(year)[-2:])
        else:
            raise ValueError("产物写错了：生日格式里有不认识的 token「%s」" % part)
    return "".join(out)


def _strong_password():
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    digits = "0123456789"
    specials = "!@#$%^&*"
    chars = [random.choice(upper), random.choice(lower),
             random.choice(digits), random.choice(specials)]
    alphabet = upper + lower + digits + specials
    chars += [random.choice(alphabet) for _ in range(8)]
    random.shuffle(chars)
    return "".join(chars)


def _looks_street_address(value):
    """这个值像不像一条**街道地址**（`1450 Sunset BlvdLos Angeles, CA,`）。

    ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：探路把地址填进了街道那格，可
    账本把**那一格记成了别的名字**（`full_name-2`，label 是模型复用的「name」）⇒ 循环
    按字样认不出街道格；而这一格的值（`sample`）本身带着门牌号 + 街道后缀 —— 拿它认。
    """
    t = str(value or "")
    return bool(re.match(r"\s*\d{2,6}\b", t)
                and re.search(r"[A-Za-z]", t)
                and re.search(r",|\b(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard"
                              r"|ln|lane|way|ct|court|pl|place|ter|terrace|pkwy|parkway"
                              r"|hwy|highway)\b", t, re.I))


class Filler:
    def __init__(self, ws_url, form_file, correlation_id, task_id="",
                 trace_path="", no_report=False, stop_at=0):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self._rule_values = {}
        self.cid = correlation_id
        #: ★ 2026-10-30：与固定步骤表那族同一条（`template.py` 的
        #: `self.tid = task_id or correlation_id.split('_')[0]`）—— 生产**不传**
        #: `--task-id`，只传 `--correlation-id <task>_<ts>`；只认 `task_id` 的话
        #: `tid` 恒为空 ⇒ `report_url` 带着 `task_id: ""` 被后端 400 拒掉
        #: ⇒ formStep 一条 step/url 都没有（用户报「只看到 success」）。
        self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger("loop")
        self._filled = set()
        #: 每一格**试过几次**、以及**最后给它的那个值**（见 ① 里那段：重试要重试同一个值，
        #: 不能每次换一个新的随机值 —— 换来换去站点永远收不到一份完整的表单）。
        self._field_tries = {}
        self._field_value = {}
        #: 每一格那个值是**从哪儿来的**（资料 / 账本示例 / 池子）—— 跟着填的那一行日志走。
        self._field_from = {}
        #: ★ 自测那两格（`selftest.ARTIFACT_FLAGS`）：少了它们，产物在自测里**一上来就 argparse
        #: 退出**（2026-09-24 实测：`unrecognized arguments: --trace … --no-report` ⇒ 报告只剩
        #: 「进程退了（退出码 2）」）。`--no-report` 是硬规矩（自测不是生产任务，不该在生产那边
        #: 留记录）；`--trace` 是自测判「每一步成没成」的读数（没有它只剩退出码）。
        self.trace_path = trace_path
        self.no_report = bool(no_report)
        #: ★ 2026-09-30：`--stop-at`（见 `run()` 里那一格 —— 这一族是「跑到第 N 步就停」）。
        self.stop_at = int(stop_at or 0)
    def _dly(self, a=0.6, b=1.8):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step, reason=""):
        if self.no_report:
            return None                     # 自测那一遍：**永不上报**
        return report_url(self.cdp, self.tid, step, self.log, reason)

    def _rpt_if_moved(self, step, url, label):
        """这一屏**第一次动手**时上报一行（与固定步骤表那族的 `_rpt_if_moved` 同一口径）。

        生产靠这些记录看「任务走到哪了」；同一屏上连点/重填不再刷（已经报过）。
        只报「第几步、在干什么」，**不报值**（姓名/电话不该进那条记录）。
        """
        if not url or url == getattr(self, "_reported_url", ""):
            return None
        label = "".join(ch for ch in str(label) if ch >= " ")
        self._reported_url = url
        return self._rpt("step", "第 %d 步 %s" % (step, label))

    def _trace(self, **row):
        """落一行 trace（JSON Lines）。自测/面板按它读「这一步成没成」——没有它只剩退出码。"""
        if not self.trace_path:
            return
        try:
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass                            # 旁路，坏了不带塌这一趟

    def _stop(self, step, action, why, **kw):
        """收摊：**先落一行 trace 再走**（哑着退场 ⇒ 下一个查的人只看得见退出码 ✗）。

        ★ 2026-09-24 立的规矩：任何 `continue` / 收摊路径都先落一行 —— 那一天「看不见
        却不说」出现了三次，每次都让诊断慢一大截（trace 里全是「再看一眼」，看不出
        那一屏到底有什么）。

        ★ 2026-10-14：那句话**两格都写**。原来只写 `progress_why`（「有没有推进」那一路
        读的），而「为什么没做成」那一路（`selftest._verdict` 的 `step_said`）读的是
        `note` ⇒ 收摊那一步的诊断落成兜底话「trace 里那一步没写为什么」⇒ 模型拿着那句话
        去改稿、**改不出任何东西**。实测 4 趟就是这么丢的。调用方若自己点名给了 `note`
        （今天没有这样的调用点），以调用方为准。
        """
        kw.setdefault("note", why)
        self._trace(step=step, action=action, ok=False, progress=None,
                    progress_why=why, **kw)

    def _cookie_gone(self) -> bool:
        """同意条还在不在（**复读确认** ✓ 点完不等于真没了 —— 样本的原话就是这个）。"""
        js = COOKIE_GONE_JS.replace("__WORDS__", json.dumps(list(COOKIE_TEXTS)))
        try:
            return "still-here" not in str(self.cdp.eval(js, "") or "")
        except Exception:                          # noqa: BLE001 —— 外面世界
            return False                           # 问不出来 ⇒ 当它还在（不静默放行）

    def clear_cookies(self, tries=3) -> bool:
        """点掉同意条，**并复读确认**（点完还在就再来）。清不掉 ⇒ False（调用方如实报失败）。

        ★ 2026-09-24 第一次公平试跑逼出来的：不清它 ⇒ 横幅盖在页面上 ✗ ⇒ 它上面那个
        `si-settings`（设置按钮）会被当成「可点的选项」⇒ `random` 一点就歪 ✗✗。
        """
        for _ in range(max(1, tries)):
            if self._cookie_gone():
                return True
            for text in COOKIE_TEXTS:
                if self.click(BY_TEXT + text):
                    break                          # 点到一个就够了（横幅是一整块）
            self._dly(1.0, 2.0)
        ok = self._cookie_gone()
        if not ok:
            self.log.info("[%s] 同意条清不掉（复读说它还在）—— 交回去如实说", self.cid)
        return ok

    def _popup_trace(self, ok, why):
        """弹层这一下**如实**落一行（`ok`：True = 关掉了 / False = 它还在 / None = 这一次没量到）。

        ★ 2026-10-16（Task 5）三条纪律钉在这一处：
          ① **不许**拿 `cdp click` 的回话当做成 —— `ok` 只由**复核**给（再问一遍同一张表）。
             那句回话说的是「**选择器命中 1 个**」，**不是**「点到的就是它」（blavity 那条口径）；
          ② `soft=True`：弹层是**环境噪声**（新窗口有、上一趟点掉过就没有、还可能是后冒出来的）
             ⇒ 与 `browser_agent` 给「点掉同意条」那类步标的记号同一档：自测**不拿它否决**那一遍
             （`selftest._verdict` 的 `hard_rows` 排除 `soft`），但照旧点名 —— spec §3.5：
             探针自己**永远不许变成新的失败点**；
          ③ **两格都写**（`note` + `progress_why`）：同 `_stop` 那条 2026-10-14 的规矩 ——
             「为什么没做成」那一路读 `note`、「有没有推进」那一路读 `progress_why`。
        """
        self._trace(step=self._step, action="popup", ok=ok, progress=None, soft=True,
                    note=why, progress_why=why)

    def _popup_answer(self) -> str:
        """问一遍钩子表（探针只读，不动页面）：**脱引号后**的原文 —— 空串 = 表里一条都没答应。

        ⚠️ 包壳与脱引号**都在这一处**（`cdp eval` 两件都不做，见表那套的 `_ev`）：
        「探之前」与「探之后」（复核）走的是**同一个函数** —— 复核必须是同一把尺子，
        不然 `ok` 那一格量的是另一件事，写它就没有意义了。
        """
        return _clean_eval(self.cdp.eval("(function(){%s})()" % _POPUP_PROBE_JS, "")).strip()

    def _popup_still_there(self):
        """复核：**再问一遍同一张表**，返回还在的那一个（空串 = 这一屏没有它了）。

        三种结果，**不许并成两种**：
          · 空串 = 没有它了；
          · 非空 = 它还在（返回钩子表点名的那个）；
          · `None` = **这一次没量到**（读炸了 / 答复形状不认识）—— 量不到就如实说量不到，
            把它读成「关掉了」正是这一刀要治的病。

        ★ 2026-10-19：**短轮询**（有界）—— 同意层的关闭是异步的（真站 compareinsulation：
        点 accept 之后紧接的那一次再探还会看到它，再晚一点点才没了）。只在「复核还答**可点**
        行」时等；返空 / 只答 `_REPORT_ONLY` 都立即收。带 `_REPORT_ONLY` 的行（表尾的
        「广告 creative」）本来就不挡路 —— 不排掉它，一次真的关掉了会被记成 `ok:false`
        （真站 wildnutrition 量到：`Close filters drawer`）。
        """
        try:
            deadline = time.time() + POPUP_RECHECK_WAIT_SECONDS
            while True:
                raw = self._popup_answer()
                if not raw:
                    return ""
                parts = raw.split("|", 2)
                if len(parts) != 3:
                    return None
                if _popup_report_only(parts[0]):
                    return ""
                if time.time() >= deadline:
                    return parts[0]
                time.sleep(POPUP_RECHECK_POLL_SECONDS)
        except Exception:                                   # noqa: BLE001 —— 外面世界
            return None

    def clear_popups(self, why="") -> bool:
        """把**挡路的弹层**点掉：问一遍钩子表（`_POPUP_HOOKS`），谁先答应就点谁。**永不抛**。

        ★ 2026-10-16（Task 4）：**逐字搬固定步骤表的 `_clear_obstructions`**（同一个由头、
        同一张表、同一套纪律），只换「怎么读」：那边走 `self._ev(...)`（它替调用方包
        `(function(){…})()` **并** `_clean_eval` 脱引号），这边的 `cdp.eval` **两件都不做**
        ⇒ 两件都在**调用处**补上。**要加一类弹窗，改表**（`_POPUP_HOOKS`），不改这里。

        为什么要产物自带（2026-09-17 真站实测，链条闭合）：生产**每一单都是新窗口**、
        每次都清 cookie —— 所以**每单都会遇到同意弹层**；而探索那一趟跑在一个「弹层已经
        被自己点掉」的会话里，**账本学的是「没有弹层的世界」**，产物里也就没有那一步。
        自测换到干净会话（R-F1）之后弹层盖住答题区，下一步的点击**点到弹层上**、
        cdp 照样回 ok（`match_count: 1` 说的是「选择器命中 1 个」，不是「点到的就是它」），
        于是「点了个寂寞」被记成做成了、后面全塌。
        ⚠️ **不能指望账本里恰好有这一步** —— 弹层是环境带来的，不是站点流程的一部分。

        判据（与 `cdp observe` 的 obstructions **同一套词汇**：文字 / id / class 里有没有
        `cookie|consent|gdpr|privacy`）—— 为什么不用 observe：**生产那个 cdp 没有 observe**，
        而弹层恰恰在生产每单都出现。所以这里走 `eval`（**读**，§5.2 允许），点还是走 cdp 的 click。

        只做**有把握**的那一下：得在一个可见的同意类容器里找到一个按钮，它的文字属于
        「接受/同意/关闭/拒绝」那一族 —— **带 id 就点 `#id`**，**只有文字就按文字打标记再点**
        （★ 2026-09-28，用户看到的现场「有个弹窗挡住了」；原来那种「没 id 就什么都不做」
        等于把弹层留在页面上挡住后面每一步 ✗）。两条都走不通就**什么都不做**（宁可不做），
        由 `_covered_by` 保证「点到弹层上」不会被记成做成了。

        ★ 2026-09-29（travelandleisure 生产：25 条里 18 条一个形状）：弹层**是页面加载完之后
        才异步冒出来**的 ⇒ **每一次动手之前**（`fill` / `click` 开头那一处）都要再问一遍，
        只在「开跑前 / 前置之后」看一眼是不够的。既然它现在挨着**每一步**，它就**不许自己
        变成新的失败点**：探不动 / 点不动一律只记一行 INFO 走人（与表那套的
        `_dismiss_consent` 同一条口径：它是为了修失败才加的，自己再变成失败点就本末倒置）。

        ★ 2026-10-16（Task 5）：**关掉之后要复核**（spec §3.4）—— `cdp click` 的回话**不算做成**
        （它说的是「选择器命中 1 个」），点完**再问一遍同一张表**，把「还在不在」如实写进账本
        （`_popup_trace` 那三分）。⚠️ **「探针返空」分不清两件事**（Task 1 真站量到：同一个站
        首载有同意层、来回趟一趟之后**同一条探针返空**）⇒ 只见「没有它」时**只写 `ok=None`**
        并说明「它本来就不在」—— **不许**写成「关掉了」：这一刀治的就是「把量不到当成做成了」，
        在这一格上自己犯同一条病最讽刺。

        ★ 2026-10-17（终审那一波，两条与表那套逐字同义 —— 两套骨架一个口径）：
          · **C2 · Ruling 30**：表里带 `_REPORT_ONLY` 的那一行**只报不点**（认得出、记一行
            INFO + 一行账，**不动手**）：真站上已经量到它误点（实测 §1.3）；
          · **Ruling 28-②**：答复是 `TEXT|` 而**文字是空的** ⇒ **不点**（那是「指向不确定
            元素的地址」，与「形状不认识」同一处置）。
        """
        try:
            #: ⚠️ `_POPUP_PROBE_JS` 是**裸语句序列** ⇒ 调用处自己包 `(function(){…})()`；
            #: ⚠️ 而 `cdp eval` 的 stdout 是 **JSON**（字符串带引号）⇒ 必须 `_clean_eval` 脱引号
            #: （2026-10-16 复核逮到的那条：不脱引号 ⇒ 三格恒拆不出来 ⇒ 真站上一次都不点）。
            #: 这两件都在 `_popup_answer` 里 —— 复核那一次也走它（**同一把尺子**）。
            raw = self._popup_answer()
            #: 答复是 `"<钩子名>|<选择器|TEXT>|<按钮文字>"`（三格，表见 `_POPUP_HOOKS`）。
            parts = raw.split("|", 2)
            if len(parts) != 3:
                #: 空串 = 表里一条都没答应（**正常情况**，页面上就是没有这类东西）—— 静默是对的。
                #: 但**形状不认识 ≠ 没有弹层**：那是「量不出来」，说出来（与本文件里
                #: `COOKIE_GONE_JS` 那类前缀同一个道理），只是不当成「有弹层」去点。
                if raw:
                    self.log.info("[%s] 弹窗探针的答复形状不认识（%r）—— 当它没答，"
                                  "照旧走这一步自己的判据", self.cid, raw[:60])
                    self._popup_trace(None, "弹窗探针答复形状不认识（%r）—— **量不出来**"
                                            "（这一次没量到覆盖现场），不当成做成" % raw[:40])
                else:
                    #: ★ 2026-10-16（Task 5）：**返空分不清两件事** —— 只许如实写 `ok=None`。
                    self._popup_trace(None, "这一屏没探到它 —— **它本来就不在**（这一次没量到"
                                            "覆盖现场），只如实记「没量到」，不当成做成")
                return False
            who, how, text = parts
            if who == "盖住整页的模态":
                #: ★ 2026-11-06（真事 `job-2aaa04c550b6` · homejolly）：**这一屏就在这个模态里**
                #: （读取器把 ROOT 定在它身上、在它里面读题/读格）⇒ 它不是遮挡，是流程本身 ——
                #: 点掉那颗 × 等于把整张表单删掉（题一个都点不到、Next 永远空点）。
                #: 判据与固定步骤表同一份 JS：容器上的 `data-sf-flow=1` / 已经打开的下拉。
                _hjs = (_VIS_JS + _CONSENT_ROOTS_JS + _MODAL_HOLDS_TARGET_BODY
                        .replace("__WANT__", '""').replace("__SELS__", "[]"))
                try:
                    _h = _clean_eval(self.cdp.eval("(function(){%s})()" % _hjs, "") or "")
                except Exception:                      # noqa: BLE001 —— 外面世界
                    _h = ""
                if str(_h or "").startswith("1|"):
                    self.log.info("[%s] 认出了「%s」（%s）—— 但**这一屏就在它里面**："
                                  "不当遮挡点掉，不点", self.cid, who, how)
                    self._popup_trace(None, "「%s」**就是这一屏**（读取器在它里面读题/读格）"
                                            "—— 不当遮挡点掉" % who)
                    return False
            if _popup_report_only(who):
                #: ★ 2026-10-17（终审 C2 · Ruling 30）：这一行**只报不点**（表里那格带
                #: `_REPORT_ONLY`）—— 真站上已经量到它误点（实测 §1.3：把站点自己的抽屉控件
                #: `Close filters drawer` 当成关闭键点了），而「加容器锚」那把尺子对**这一型**
                #: 还没有正向证据 ⇒ 认得出、说出来、**不动手**。与「形状不认识」同一处置。
                self.log.info("[%s] 认出了「%s」（%s，%r）—— 但**没有动手**：这一型还没有"
                              "真站正向证据，按 Ruling 30 **只报不点**（照 `_REPORT_ONLY`）",
                              self.cid, who, how, text[:30])
                self._popup_trace(None, "认出了「%s」（%s，%r）但**没有动手**：这一型还没有"
                                        "真站正向证据，按 Ruling 30 只报不点"
                                        % (who, how, text[:30]))
                return False
            if how == "TEXT" and not text.strip():
                #: ★ 2026-10-17（终审 Ruling 28-②）：`TEXT|` 而**文字是空的** ——
                #: 那是「**指向不确定元素的地址**」（按空串找一颗空文字的按钮，页面上这种
                #: 按钮可能有好几颗）⇒ 与「形状不认识」同处置：说出来、**不点**。
                #: 真站实测（实测文档 §1.3）：`广告 creative|TEXT|` 就是这么答的。
                self.log.info("[%s] 「%s」答的是 `TEXT|` 而**文字是空的** —— 那是一个"
                              "**指向不确定元素的地址**（按空文字打标记可能点到别的东西）："
                              "不猜、不点", self.cid, who)
                self._popup_trace(None, "「%s」答的是 `TEXT|` 而文字是空的 —— **指向不确定"
                                        "元素的地址**：没敢点" % who)
                return False
            if how == "TEXT":           # 只有文字：打标记再点（点还是走 cdp）
                marked = _clean_eval(self.cdp.eval(
                    "(function(){%s})()" % (_MARK_CONSENT_BY_TEXT_JS % json.dumps(text)), ""))
                if "marked" not in str(marked or ""):
                    self.log.info("[%s] 「%s」那颗按钮（%r）没有 id、按文字也没标记上 —— 不猜，"
                                  "留给 `_covered_by` 兜（下一步会被挡住并如实失败）",
                                  self.cid, who, text[:30])
                    #: 探之前**有**、而我们**没动手** ⇒ 它当然还在（不用再探一次也知道）。
                    self._popup_trace(False, "「%s」还在（认不出该点哪一颗 —— 没敢点）" % who)
                    return False
                selector = '[data-sf-consent="1"]'
            elif how == "JS":
                #: 钩子自己已经用 JS 的 `.click` 关掉了（绕开被盖住的落点判据）；
                #: 不再走 cdp click —— 复核照旧。
                selector = "JS"
                out = None
            else:
                selector = how
            if how != "JS":
                out = self.cdp.click(selector)   # 动作一律走 cdp（§5.2）
            if how == "TEXT":
                #: 标记用完就清（不清的话下一屏上会留一个 `[data-sf-consent="1"]`，那是脏状态）
                self.cdp.eval("(function(){%s})()"
                              % ("var a=document.getElementsByTagName('*');"
                                 "for(var i=0;i<a.length;i++){if(a[i].removeAttribute)"
                                 "a[i].removeAttribute('data-sf-consent');}return 'clean';"), "")
            self.log.info("[%s] %s把「%s」%s（%s，%r）—— %s", self.cid,
                          ("%s之后 " % why) if why else "开跑前 ", who,
                          ("用 JS 关掉" if how == "JS" else "点掉了"),
                          selector, text[:30],
                          "JS 直接点（不等 cdp 回话）" if how == "JS"
                          else ("看起来成了" if self._filled_ok(out) else "**没成**"))
            #: ★ 2026-10-16（Task 5）：**复核** —— 上面那句只是 cdp 的**回话**，不是做成。
            #: 再问一遍**同一张表**：探之前有、探之后没有 ⇒ True；探之后**还有** ⇒ False；
            #: 复核探不动（读炸了 / 答复形状不认识）⇒ None（量不到就如实说量不到）。
            still = self._popup_still_there()
            if still is None:
                self._popup_trace(None, "点完复核不了：探不动（这一次没量到覆盖现场）—— "
                                        "不当成做成")
            elif still:
                self._popup_trace(False, "点完复核：这一屏**还有**「%s」（探之前有、探之后还有）"
                                         "—— 这一下没能关掉" % still)
            else:
                self._popup_trace(True, "点完复核：这一屏没有它了（探之前有、探之后没有）")
            return True
        except Exception as exc:                           # noqa: BLE001
            #: **永不失败**：读不到 / 点不动都只记一行，绝不往上抛 ——
            #: 往上抛会把这**一步**判成「产物自己出错了」，而弹层只是环境带来的噪声，
            #: 不该有这种权力。
            self.log.info("[%s] 看/点弹层这一下没成（%s: %s）—— 当它没有，照旧往下走",
                          self.cid, type(exc).__name__, exc)
            #: ⚠️ 这一格 `ok=None`（**没量到**）：探针自己炸了、或点那一下炸了 ⇒ 既不能说
            #: 「关掉了」（没量过）也不能说「还在」（没看见）—— 三等分里就剩这一格。
            self._popup_trace(None, "看/点弹层这一下没成（%s: %s）—— 没量到，不当成做成"
                                    % (type(exc).__name__, str(exc)[:40]))
            return False

    def read(self):
        """读这一屏。**读不到返回 None** —— 那是「看不见」，不是「做完了」。

        ⚠️ `None` 是一等值（`docs/执行事实契约-2026-09-18.md` 第 ② 条）：`datewhirl.py:175`
        就是把「什么都没看见」判成「做完了」，连瞎三周、日志里成功 0 次。
        """
        raw = (self.cdp.eval(READ_PAGE_JS, "") or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    def is_success(self, url):
        low = (url or "").lower()
        return any(str(m).lower() in low for m in SUCCESS_URL_MARKERS)

    # ---------- 填什么（生成器填的那一格）----------

    def fill(self, token, value):
        """填一格：按 token 的**来源**用对选择器 —— `data-testid` / ``=id/name /
        ``=placeholder（打标记再填）。**填没填上要看回话**，不是「发出去就算填了」。

        ★ 2026-09-24 第一次真站试跑逼出来的：那一屏的邮编格**只有 placeholder**（形状同 LPA 的
        `input[placeholder="e.g. RG24 8PE"]`）⇒ 读取器原来直接丢掉它 ✗ ⇒ 「没东西可填」⇒
        只剩 Continue ⇒ **空点 55 次** ✗。做法与「按文字点」同一条：找用 JS、填还是走 `cdp.form`。
        ★ 2026-09-26 第五次真站试跑：那一站的三个格**只有 `id`、没有 `data-testid`** ⇒ 一律按
        `[data-testid=…]` 找 ⇒ `cdp form` 报 `element not found`，而这里**照样 return True** ⇒
        表单永远是空的、提交被拒、页面一动不动（而 trace 上写着「填了」✗✗）。
        所以这一版：① id 来源用 `[id=…]`；② **回话里有 Error 就算没填上**（不许谎报）。

        ★ 2026-10-16（Task 4）：**动手之前先清一次弹层** —— 与下面那条落点判据**同一处**，
        次序是「先清弹层、再量落点」（表那套 `_perform` 里也是这两句、这个次序）：
        先把弹层点掉，`_covered_by` 才不必为它白等一轮（等的是加载蒙版，弹层等不走）。
        """
        #: ⚠️ 它**永不算失败**（探不动 / 点不动只记一行），见 `clear_popups`。
        self.clear_popups(why="这一步动手之前")
        token = str(token or "")
        if token.startswith(BY_PLACEHOLDER):
            want = token[len(BY_PLACEHOLDER):]
            js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var t=" + json.dumps(want) + ";"
                  "var es=deepAll('input,textarea,select');"
                  "for(var i=0;i<es.length;i++){var e=es[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var p=(e.getAttribute('placeholder')||'').replace(/\\s+/g,' ').trim();"
                  "var al=(e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();"
                  "if(p===t||al===t){var old=deepAll('[data-sf-fill]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-fill');}"
                  "e.setAttribute('data-sf-fill','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            #: ★ 本刀：动手之前先量落点 —— 被盖着就不动手，并**如实报**（不是「没找到」）
            if self._covered_before_acting('[data-sf-fill="1"]', "填"):
                return False
            return self._fill_and_check(token, '[data-sf-fill="1"]', value)
        if token.startswith(BY_ID):
            #: ★ 2026-11-02（educationdirectory 真站）：读取器把 `id` 与 `name` 都打成
            #: ``（`tid(e)` 的原话「只有 `id`（或 `name`）」），可这一支原来只拼
            #: `[id=…]` —— 那三个格只有 `name`（`zip_code` / `education_level` /
            #: `high_school_graduation_year`）⇒ `cdp form` 报 `element not found`，
            #: 三次 Next 全空点。先按 id 找，找不到再按 name 找（同一个 token 的两种地址）。
            key = token[len(BY_ID):]
            for sel in ('[id="%s"]' % key, '[name="%s"]' % key):
                if self._covered_before_acting(sel, "填"):
                    return False
                if self._fill_and_check(token, sel, value):
                    return True
                if "element not found" not in str(getattr(self, "_fill_why", "") or ""):
                    return False
            return False
        if token.startswith(BY_CLASS):
            #: ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：这一格别的身份都没有，
            #: 只有 class（读取器末尾那个够长的类名）—— 按 `[class~=…]` 找它。
            sel = '[class~="%s"]' % token[len(BY_CLASS):]
            if self._covered_before_acting(sel, "填"):
                return False
            return self._fill_and_check(token, sel, value)
        sel = '[data-testid="%s"]' % token
        if self._covered_before_acting(sel, "填"):
            return False
        return self._fill_and_check(token, sel, value)

    @staticmethod
    def _clicked_ok(out) -> bool:
        """`cdp click` 说点着了没有 —— 成功是 `{"match_count":…}`，失败是 `Error: …`。

        ★ 2026-10-29（Atticus 第二趟）：`click()` 原来不看这句回话，没点着也 `return True`
        ⇒ trace 里每一次 Next 都是「成功」，失败在账上一点痕迹都没有。
        """
        said = str(out or "").strip()
        return bool(said) and "Error" not in said

    @staticmethod
    def _filled_ok(out) -> bool:
        """`cdp form` 说填上了没有 —— **只看它自己那句话**（报错就不算填上）。

        ⚠️ 为什么必须有这一道（2026-09-26）：`common.CDPHelper.form` **不抛异常**，
        它把 cdp 的 stderr 一起交回来；调用方不看就变成「接上了但不响」——
        今天那一趟就是照这样在 trace 里写了三次「填了 qv3-fn」，而格子里一个字都没有 ✗。
        """
        said = str(out or "")
        return "Error" not in said and "element not found" not in said

    @staticmethod
    def _same_value(a, b) -> bool:
        """两个值是不是「同一个」（站点会格式化：`2125550147` ↔ `(212) 555-0147`）。"""
        def norm(x):
            return "".join(ch for ch in str(x if x is not None else "").lower()
                           if ch.isalnum())
        return norm(a) == norm(b)

    def _read_back(self, token):
        """回读这一格现在的值：读到了是字符串，**读不到是 `None`**（看不见 ≠ 没填上）。"""
        page = self.read()
        if not isinstance(page, dict):
            return None
        for pair in (page.get("inputs") or []):
            if (isinstance(pair, (list, tuple)) and len(pair) >= 2
                    and str(pair[0]) == str(token)):
                return str(pair[1] if pair[1] is not None else "")
        return None

    def _is_select(self, sel) -> bool:
        """这一格是不是原生 `<select>` —— `cdp form --value` 打不进它（那一路是
        insertText），得走 `--select`（按 option 的 value / 文字挑一项）。

        ★ 2026-11-02（educationdirectory 真站，8100 那趟）：`education_level` /
        `high_school_graduation_year` 都是原生 select，打不进 ⇒ 回读永远对不上。
        """
        js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var es=deepAll(%s);"
              "return JSON.stringify({s:!!(es.length&&es[0].tagName==='SELECT')});})()"
              % json.dumps(sel))
        got = self._eval_json(js)
        return bool(isinstance(got, dict) and got.get("s"))

    def _any_select_option(self, sel) -> str:
        """这一格是下拉、又没有值可填 ⇒ 挑**页面上第一个可选项**（value 优先，其次文字）。

        ★ 2026-11-02（educationdirectory 真站）：`education_level` 退成 `field-5`、账本没留值
        ⇒ 空着 ⇒ Next 被表单校验拦住。编一个页面里不存在的值只会让 `--select` 报
        option not found，所以从页面自己身上取。
        """
        js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var es=deepAll(%s);var e=es[0];"
              "if(!e||e.tagName!=='SELECT')return JSON.stringify({});"
              "for(var i=0;i<e.options.length;i++){var o=e.options[i];"
              "var t=(o.textContent||'').replace(/\\s+/g,' ').trim();"
              "if(o.value||t)return JSON.stringify({v:o.value,t:t});}"
              "return JSON.stringify({});})()" % json.dumps(sel))
        got = self._eval_json(js) or {}
        return str(got.get("v") or got.get("t") or "")

    def _fill_and_check(self, token, sel, value) -> bool:
        """填 → **回读** → 没留住再填（见 `FILL_READBACK_TRIES` 那段）。

        `cdp form` 的 `ok` 只说明「发出去了」；值有没有留在页面上，只有回读说得清。
        先等 `FILL_READBACK_GAP` 再读：hydration / 重渲染把值盖掉通常就在填完那一下。

        ★ 2026-10-29：`element not found` **短促重试**（`FILL_READY_TRIES`）——
        页面还没 hydrate 完的那一下就会回这句，旧实现立刻判失败（真站第一趟 step 1）。
        仍然不行 ⇒ 如实失败，并把 `_fill_why` 留给日志说清楚为什么。
        """
        self._fill_why = ""
        #: ★ 2026-11-02：原生 `<select>` 不吃 `--value`（那一路是 insertText，打不进去）⇒
        #: 走 `--select`（按 option 的 value / 文字挑）。回读也不比：select 的 `value` 是
        #: 选项的 value（`bachelors`），不是我们给的那段文字（`Bachelor's degree`）。
        is_select = self._is_select(sel)
        if value == SELECT_ANY:
            if not is_select:
                #: 让 `fill()` 的 id→name 回退接着走（它靠这句里的 `element not found` 判
                #: 「这根地址上不是那个东西」）。控制字符**绝不能**当参数发给子进程。
                self._fill_why = "element not found（这一根地址不是下拉）"
                return False
            value = self._any_select_option(sel)
            if not value:
                self._fill_why = "这一格是下拉，但页面上一个可选项都没有"
                return False

        def _send():
            return (self.cdp.form(sel, select=value) if is_select
                    else self.cdp.form(sel, value=value))

        out = _send()
        for _ in range(max(0, FILL_READY_TRIES - 1)):
            if self._filled_ok(out) or "element not found" not in str(out or ""):
                break
            time.sleep(FILL_READY_GAP)
            out = _send()
        if not self._filled_ok(out):
            said = str(out or "").strip()
            self._fill_why = ("cdp form 报错：%s" % said[:160]) if said else "cdp form 没回话"
            return False
        if is_select:
            said = str(out or "")
            if '"error"' in said or "option not found" in said:
                self._fill_why = "cdp form --select 报错：%s" % said[:160]
                return False
            return True
        for _ in range(FILL_READBACK_TRIES):
            time.sleep(FILL_READBACK_GAP)
            got = self._read_back(token)
            if got is None or self._same_value(got, value):
                return True
            if not self._filled_ok(_send()):
                self._fill_why = "回读是 %r（不是 %r），重填也被拒" % (got, value)
                return False
        time.sleep(FILL_READBACK_GAP)
        got = self._read_back(token)
        ok = got is None or self._same_value(got, value)
        if not ok:
            self._fill_why = "回读是 %r，不是填进去的 %r" % (got, value)
        return ok

    def _eval_json(self, js):
        """跑一段 JS，按 `read()` 同一套拆两层 JSON；读不到回 `None`。"""
        raw = (self.cdp.eval(js, "") or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    def _set_value_events(self, token, value) -> bool:
        """用**原生 setter + 全套事件**写一格 —— React 受控输入只认这条。

        ★ 2026-10-24 真站量证（Atticus `survey/claim/zip`，`cdp form` vs 原生事件）：
        `cdp form '#zipcode' --value 10001` 之后 **45 秒** Next 一直是禁用态；
        同一屏改用原生 setter + `keydown/keypress/input/keyup/change/blur` 写 `90210`，
        **约 1–2 秒** Next 就由禁用变可用（`benefitsByZip` 请求约 1.7s 返回）。
        ⇒ `cdp form` 只改了 DOM 的 value，没进 React 的 state；`_nudge` 原来用它重打，
        等于没打。这条也穿透 shadow root（`deepAll`），与 `read()` 同一套找法。
        """
        token = str(token or "")
        js = ("(function(){function deepAll(sel){var out=[];function walk(root){"
              "root.querySelectorAll(sel).forEach(function(e){out.push(e);});"
              "var all=root.querySelectorAll('*');"
              "for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}"
              "walk(document);return out;}"
              "var token=%s,value=%s;var e=null;"
              "if(!token)return JSON.stringify({ok:false,why:'no-token'});"
              "if(token.charCodeAt(0)===4){var es=deepAll('[id="'+token.slice(1)+'"]');e=es[0];"
              "if(!e){es=deepAll('[name="'+token.slice(1)+'"]');e=es[0];}}"
              "else if(token.charCodeAt(0)===2){var want=token.slice(1);"
              "var all=deepAll('input,textarea,select');"
              "for(var i=0;i<all.length;i++){"
              "var p=(all[i].getAttribute('placeholder')||'').replace(/\\s+/g,' ').trim();"
              "var al=(all[i].getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();"
              "if(p===want||al===want){e=all[i];break;}}}"
              "else {var es2=deepAll('[data-testid="'+token+'"]');e=es2[0];}"
              "if(!e)return JSON.stringify({ok:false,why:'not-found'});"
              "if(value===''&&e.tagName==='SELECT')return JSON.stringify({ok:false,why:'select'});"
              "e.focus();"
              "if(e.tagName==='SELECT'){e.value=value;}"
              "else{var proto=e.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:"
              "window.HTMLInputElement.prototype;"
              "var d=Object.getOwnPropertyDescriptor(proto,'value');"
              "if(d&&d.set)d.set.call(e,value);else e.value=value;}"
              "['keydown','keypress','input','keyup','change','blur'].forEach(function(t){"
              "e.dispatchEvent(t==='input'||t==='change'?new Event(t,{bubbles:true}):"
              "new KeyboardEvent(t,{bubbles:true,key:'1',keyCode:49}));});"
              "return JSON.stringify({ok:true,value:e.value});})()"
              % (json.dumps(token), json.dumps(str(value))))
        got = self._eval_json(js)
        return bool(isinstance(got, dict) and got.get("ok"))

    def _nudge(self, page) -> bool:
        """Next 是禁用态、格子又都有值 ⇒ 把值**用同一个重打一遍**（同值重填）。

        ★ 2026-10-24（Atticus `survey/claim/zip` 真站，线上 `26789962`）：
        那一格填进去之后，站点发了 `benefitsByZip`，可那一次**被中止**
        （performance 里 `responseStatus: 0`）—— app 没拿到回话就一直把 Next 禁着。
        而 DOM 的 value 是**留住的** ⇒ 回读说「填好了」⇒ 循环再也不会碰这一格
        ⇒ 干点 8 次 Next、如实报失败。
        手工量证（同一个窗口）：用**原生 setter + 事件**把这格写一遍 ⇒ 站点重发请求（200）
        ⇒ Next 由禁用变可用（`cdp form` 只改 DOM，进不了 React state）。

        ★ 2026-11-06（线上 23 单卡在 ZIP）：**同值重写不算变化** —— 站点 `onChange`
        里是 `n !== t` 才发查询，value 没变就什么都不发。所以现在**先清空、隔一小拍
        再写回**（`NUDGE_CLEAR_GAP`）：清空让 state 变空，写回才成为一次新输入。

        ★ 2026-10-29（线上 26919524）：那一下是**偶发**被中止的，而旧实现
        「一屏只打一次」（`_nudged_sig`）⇒ 第一次撞上就再也没机会。改成
        **同一屏最多 `NUDGE_TRIES` 次**，每次打完最多等 5 秒看 Next 开没开。
        重打用 `_set_value_events`（原生 setter + 事件）—— `cdp form` 进不了 React state。
        """
        sig = "%s|%s" % ((page.get("head") or "")[:60], str(page.get("url") or ""))
        tries = getattr(self, "_nudge_tries", None)
        if tries is None:
            tries = self._nudge_tries = {}
        n = int(tries.get(sig, 0))
        if n >= NUDGE_TRIES:
            return False
        tries[sig] = n + 1
        did = False
        for pair in (page.get("inputs") or []):
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            token, value = str(pair[0] or ""), str(pair[1] if pair[1] is not None else "")
            if not token or not value:
                continue
            #: ★ 2026-11-06：先清空再写回。同值重写时站点的 `onChange` 判「没变」⇒
            #: `benefitsByZip` 不会重发（复现：模拟首查超时后，同值 nudge 仍然禁用、
            #: performance 里 0 条请求）。清空让 state 先变成空，写回才算一次**新**输入。
            if self._set_value_events(token, ""):
                time.sleep(NUDGE_CLEAR_GAP)
            if self._set_value_events(token, value):
                did = True
                self.log.info("[%s] step: Next 是禁用态、格子有值 ⇒ 先清空再原生 setter+事件重写 %s",
                              self.cid, token)
                self._trace(step=0, action="form", ok=True, progress=None,
                            target={"testid": token},
                            note="原生 setter+全套事件（先清空再写回；同值重写不会重发站点查询）")
        if did:
            #: 给站点发 `benefitsByZip` 的时间（真站量到 ~1.7s；最多等 `NUDGE_WAIT_SECONDS`）。
            deadline = time.monotonic() + NUDGE_WAIT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.5)
                page2 = self.read()
                if page2 and not page2.get("nextDisabled"):
                    break
        return did


    def _wait_for_change(self, before) -> bool:
        """点完「下一步」先等页面**真的变了**（URL 或题面）—— 变了返回 True。

        ★ 2026-10-29（Atticus `age-interstitial-over-50-early-btr`，旁路轮询 0.6s/次）：
        点完立刻重读、看到同一屏就再点 ⇒ 8 次点击全被吞，而页面在**停止点击之后**自己
        走到了下一屏；同一时刻页面上有一批 15–18 秒才失败（`responseStatus: 0`）的第三方
        请求。连点不会加速过渡，只会把「同一屏最多几次」的额度烧光。
        读不到（`None`）不算变 —— 看不见不是事实。
        """
        before_sig = self._page_sig(before)
        #: 测试/自测可以把等待压到 0（`SITEFORGE_NEXT_SETTLE`）；生产不设 ⇒ 用默认预算。
        budget = NEXT_SETTLE_SECONDS
        raw = os.environ.get("SITEFORGE_NEXT_SETTLE")
        if raw not in (None, ""):
            try:
                budget = float(raw)
            except ValueError:
                budget = NEXT_SETTLE_SECONDS
        if budget <= 0:
            return False
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            time.sleep(NEXT_SETTLE_POLL)
            now = self.read()
            if not isinstance(now, dict):
                continue
            if self._page_sig(now) != before_sig:
                return True
        return False

    @staticmethod
    def _page_sig(page) -> str:
        """这一屏的「看得见的形状」—— 与 `_same_next` 同一把尺子（题面可能每屏都一样）。

        只比 URL + 题面会漏掉「标题不变、进度/格子变了」的那一族（Free Eligibility Checker 型），
        那会白白等满 12 秒。
        """
        page = page if isinstance(page, dict) else {}
        return "|".join([
            str(page.get("url") or ""),
            str(page.get("head") or ""),
            str(page.get("progress") or ""),
            "|".join(str(t) for t, _v in (page.get("inputs") or [])),
            "|".join(str(o) for o in (page.get("options") or []))[:160],
        ])

    def _rule_value(self, fill):
        rule = str(fill.get("rule") or "")
        key = (rule, tuple(fill.get("prefixes") or ()),
               str(fill.get("date_format") or ""))
        if key in self._rule_values:
            return self._rule_values[key]
        if rule == "phone_exact":
            value = str(fill.get("value") or "").strip()
        elif rule == "phone_prefix":
            prefixes = [str(x).strip() for x in (fill.get("prefixes") or [])
                        if str(x).strip()]
            prefix = random.choice(prefixes) if prefixes else "04"
            value = prefix + "".join(str(random.randint(0, 9))
                                     for _ in range(max(0, 10 - len(prefix))))
            if (len(prefix) <= 3 and not prefix.startswith("0")
                    and len(value) == 10 and value[3] in "01"):
                value = value[:3] + str(random.randint(2, 9)) + value[4:]
        elif rule == "dob_random":
            fmt = str(fill.get("date_format") or "DD/MM/YYYY")
            year = time.localtime().tm_year - random.randint(19, 68)
            value = _format_date(random.randint(1, 28), random.randint(1, 12), year, fmt)
        elif rule == "password_strong":
            value = _strong_password()
        else:
            raise ValueError("产物写错了：不认识的规则「%s」" % rule)
        self._rule_values[key] = value
        return value

    def value_for(self, token, page, index=0):
        """这一格填什么 —— 三档，从**有据可依**往**兜底**走：
        ① 字样（`kind`/`label` ✓）对得上 ⇒ 用它 ✓
        ② 对不上 ⇒ 拿**账本里那格自己记的地址**去对 ✓（探路时真用过那份 ✓）
        ③ 还认不出 ⇒ ★ **按账本里那几格的顺序**填（本屏第 `index` 个空格 ⇒ 第 `index` 格 ✓）
        ⚠️ 第 ③ 档为什么非有不可（2026-09-24 实测）：那一屏的占位符是 `e.g. HD6 4AH` ✗ 而账本里
        记的是 `e.g. RG24 8PE` ✗ —— 同一格、**站点每次给的例子不一样** ⇒ ①② 全落空 ⇒ 不填 ✗ ⇒
        掉到「点页脚噪声」⇒ 空转到 60 ✗。（样本那份靠写死 `"rg24" in hay` 绕过 ✗ 那是它那个
        站的补丁，不是规则 ✓ 我们不这么干 ✓。）
        """
        #: ★ 2026-10-29：填上了 ≠ 填对了 —— 这个值的**来源**（资料 / 账本示例 / 池子）
        #: 跟着返回值一起说出去，由调用方拼进日志与 trace（固定步骤表 2026-09-28 起就这么做）。
        self._value_from = ""
        hay = str(token or "").lower()
        if hay.startswith(BY_PLACEHOLDER):
            hay = hay[len(BY_PLACEHOLDER):]
        if hay.startswith(BY_ID):
            hay = hay[len(BY_ID):]
        if hay.startswith(BY_CLASS):
            hay = hay[len(BY_CLASS):]
        if any(s in hay for s in SKIP_FIELDS):
            return ""                       # 蜜罐：绝不填
        p = self.form_data if isinstance(self.form_data, dict) else {}
        for name, spec in (FILLS or {}).items():
            spec = spec or {}
            kind = str(spec.get("kind") or "")
            label = str(spec.get("label") or name).lower()
            #: ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：`hay in blob` 是**子串**
            #: 匹配 —— token「name」会命中 `input[name="email"]` 这种地址（`name` 出现在
            #: 属性名里）⇒ 名字格拿到的是邮箱值。名字那类 token 只认**身份里也写着 name**
            #: 的那几格（`username`/`filename` 自己的身份里也有 name，不受影响）。
            if ("name" in hay and "email" not in hay and "user" not in hay
                    and "file" not in hay and "name" not in (label + " " + name.lower())):
                continue
            #: ★ 规则（用户 2026-09-24 拍的「做成规则」）：**字样认不出来**时，拿**账本里那一格
            #: 自己记的地址**去对（`FILLS[name].target.selectors` = 探路时真用过的那份 ✓）——
            #: 例：`e.g. RG24 8PE` 对 `postcode` 那格的 `input[placeholder="e.g. RG24 8PE"]` ✓。
            #: ⚠️ 这一条**不写任何站特有的字**（样本里那个 `"rg24" in hay` 是它那个站的补丁 ✗）
            #: ⇒ 换站照样成立 ✓ —— 这就是「做成规则」与「照抄那一家」的分界 ✓。
            blob = " ".join([label, kind] + [str(x) for x in
                                             ((spec.get("target") or {}).get("selectors") or [])]).lower()
            if ((label and label in hay) or (kind and kind in hay)
                    or (hay and hay in blob)):
                if spec.get("rule"):
                    self._value_from = "规则「%s」现编" % str(spec.get("rule") or "")
                    return self._rule_value(spec)
                #: ★ 2026-10-29：探路给的 `source` **排第一** —— 2026-09-30 起它就是
                #: **资料库的列名**（`postal_code` / `phone_number` …），比拿 `FILLS`
                #: 的页面名去猜更准；源里没有这个名字时才回退到原名/别名。
                source = str(spec.get("source") or "")
                if source:
                    got = self._profile_value(p, source)
                    if got:
                        return str(got)
                if self._is_phone(name, kind):
                    #: ★ 2026-10-22（用户：「手机号肯定要取资料」；「别取资料」只是**个别特殊
                    #: 脚本**的处理，不是全局口径）—— 推翻 2026-09-26/10-13 那两刀：
                    #: ① 默认：资料给了电话就用 **资料那一串**；
                    #: ② 特殊脚本用 `prefix`（例 `'0207'`）或 `from_profile: false` 点名
                    #:    「这一站要现编」⇒ 不取资料，按那个号段/池子现编。
                    #: 账本里的 `EXAMPLES`（探路时从资料里拿的真号）仍然只当**兜底**，
                    #: 不再压过资料。
                    _p = str(spec.get("prefix") or "")
                    if not _p and spec.get("from_profile", True) is not False:
                        got = self._profile_value(p, name, kind)
                        if got:
                            return str(got)
                    self._value_from = "随机池「%s」" % name
                    return self._pool(name, _p) or self._pool(kind, _p)
                if self._is_age(name, kind):
                    #: ★ 2026-10-22（用户：资料里没有年龄，这一格要**随机**）——
                    #: 资料给了就听资料的；没给就每趟现取一个成年年龄（50–64，
                    #: 与 Atticus 探路那趟 58 同一档，不跨到未成年人那一支）。
                    got = self._profile_value(p, name, kind)
                    if got:
                        return str(got)
                    self._value_from = "现取年龄（50–64）"
                    return str(random.randint(50, 64))
                #: ★ 2026-10-29：资料先按**原名 + 别名 + 全名拆分**找（`PROFILE_ALIASES`），
                #: 找不到才轮到账本示例 —— 原来只按原名查，`postal_code`/`phone_number`
                #: 永远对不上，静默落进示例（Atticus 每单都是 10001）。
                got = self._profile_value(p, name, kind)
                if got:
                    return str(got)
                #: ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：**先看这一格自己的
                #: sample**（探路真填进去的值），再看 `EXAMPLES`。`EXAMPLES` 是按 `source`
                #: 归并的（`phone-2` → `phone`），而探路可能把**另一格**记成了同一个
                #: source（那一趟把街道地址记成了 `full_name-2`）⇒ 按 source 取会取到
                #: **别的格子**的值；sample 是这一格自己的，不会被别的格子串味。
                sample = str(spec.get("sample") or "").strip()
                if sample:
                    #: ★ 2026-11-06（同一条真事）：这一格是**名字那类**，而 sample 是一串
                    #: 街道地址（探路把地址填进了名字格/记错了格）⇒ 这一笔不是它的，换下一格。
                    if "name" in hay and _looks_street_address(sample):
                        continue
                    self._value_from = "账本 sample"
                    return sample
                for key in (name, kind):
                    got = EXAMPLES.get(key)
                    if got:
                        self._value_from = "账本示例「%s」" % key
                        return str(got)
                #: 下拉的值**本来就在页面上**：认不出这一格、账本也没留值时，挑第一个
                #: 可选项 —— 编一个页面里不存在的值只会让 `--select` 报 option not found。
                if kind == "select":
                    self._value_from = "页面上的第一个可选项"
                    return SELECT_ANY
                #: ★ 名**先于** kind：`kind` 是「这一格填什么」那一档（这一站给的是笼统的
                #: `value`），拿它当池子的名 ⇒ `_pool('value')` 什么都没有 ⇒ **邮编永远填不上** ✗
                #: ⇒ 掉到「点页脚噪声」空转到收摊（第九趟真站的原形）。
                self._value_from = "随机池「%s」" % name
                return self._pool(name) or self._pool(kind)
        #: ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：这一格的 class 自己说是
        #: **街道地址**（`leadforms-full-street-address-input`）—— 而账本把那笔值记到了
        #: 别的名字下（`full_name-2`，label 是模型复用的「name」）⇒ ① 认不出。
        #: 值本身带着门牌号 + 街道后缀 ⇒ 按形状把它认出来，别让街道格落到「按顺序兜底」
        #: （那会往街道格里填邮编）。
        if "address" in hay or "street" in hay:
            for _name, _spec in (FILLS or {}).items():
                cand = str((_spec or {}).get("sample") or "").strip()
                if _looks_street_address(cand):
                    self._value_from = "账本 sample（地址那笔，记在「%s」下）" % _name
                    return cand
        #: ③ 按**账本顺序**兜底（见 docstring 第 ③ 档）
        if 0 <= int(index) < len(FILL_ORDER):
            name = FILL_ORDER[int(index)]
            spec = (FILLS or {}).get(name) or {}
            kind = str(spec.get("kind") or name)
            #: ★ 2026-10-20（Review finding 3）：兜底那一支也要认规则 —— 否则
            #: 页面 placeholder 一变、① 对不上时，这里会把资料值/旧 prefix 又填回去。
            if spec.get("rule"):
                self._value_from = "规则「%s」现编" % str(spec.get("rule") or "")
                return self._rule_value(spec)
            source = str(spec.get("source") or "")
            if source:
                got = self._profile_value(p, source)
                if got:
                    return str(got)
            if self._is_phone(name, kind):        # 同上：资料优先；prefix / from_profile=False 才现编
                _p = str(spec.get("prefix") or "")
                if not _p and spec.get("from_profile", True) is not False:
                    got = self._profile_value(p, name, kind)
                    if got:
                        return str(got)
                self._value_from = "随机池「%s」" % name
                return self._pool(name, _p) or self._pool(kind, _p)
            if self._is_age(name, kind):          # 同上：资料优先，没给就现取
                got = self._profile_value(p, name, kind)
                if got:
                    return str(got)
                self._value_from = "现取年龄（50–64）"
                return str(random.randint(50, 64))
            got = (self._profile_value(p, name, kind)
                   or EXAMPLES.get(name) or EXAMPLES.get(kind))
            if got:
                if not self._value_from:
                    self._value_from = "账本示例"
                return str(got)
            sample = str(spec.get("sample") or "").strip()
            if sample:
                self._value_from = "账本 sample"
                return sample
            if kind == "select":
                self._value_from = "页面上的第一个可选项"
                return SELECT_ANY
            self._value_from = "随机池「%s」" % name
            return self._pool(name) or self._pool(kind)
        return ""

    def _profile_value(self, p, *names) -> str:
        """资料里这一格给的值 —— 原名 → 别名 → 全名拆分，都没有就是空串。

        ★ 2026-10-29（Atticus `survey/claim/zip`）：`FILLS` 的键是照**页面**起的，
        资料库的键是**列名**，两边没有约定 ⇒ `postal_code` / `phone_number` /
        `username` 这三样原来一次都没取到，静默落进账本示例/随机池。
        这里**只加回退**：原名优先，一个字都不改老行为。
        """
        if not isinstance(p, dict):
            return ""
        for name in names:
            key = str(name or "")
            if not key:
                continue
            got = str(p.get(key) or "").strip()
            if got:
                self._value_from = "任务资料「%s」" % key
                return got
            for alt in PROFILE_ALIASES.get(key, ()):
                got = str(p.get(alt) or "").strip()
                if got:
                    self._value_from = "任务资料「%s」（别名 → %s）" % (alt, key)
                    return got
            whole = str(p.get(PROFILE_SPLIT_FROM.get(key, "")) or "").strip()
            if whole and " " in whole:
                parts = whole.split()
                self._value_from = "任务资料「%s」拆出（→ %s）" % (
                    PROFILE_SPLIT_FROM[key], key)
                return parts[0] if key == "first_name" else " ".join(parts[1:])
        return ""

    @staticmethod
    def _is_phone(name, kind) -> bool:
        """这一格是不是**电话号**（`phone` / `tel` / `mobile` 任何一种写法都算）。"""
        return any(s in ("%s %s" % (name, kind)).lower() for s in ("phone", "tel", "mobile"))

    @staticmethod
    def _is_age(name, kind) -> bool:
        """这一格是不是**年龄**（按词认：`age` / `age-2` 算，`message` / `page` 不算）。"""
        words = ("%s %s" % (name, kind)).lower().replace("_", " ").replace("-", " ").split()
        return "age" in words

    def _pool(self, kind, prefix=""):
        kind = (kind or "").lower()
        if "first" in kind or "last" in kind or "name" in kind:
            return random.choice(("James", "Sarah", "David", "Emma", "Michael", "Laura"))
        if "email" in kind:
            return "user%d@%s" % (random.randint(100, 9999),
                                  random.choice(("gmail.com", "outlook.com", "yahoo.com")))
        if "phone" in kind or "mobile" in kind or "tel" in kind:
            #: ★ 2026-09-25 **用户点名**：给**座机**形状（原来给 `07…` 手机 —— 位数一样合法，
            #: 在那一站上**过不去** ✗：填了但校验不过 ⇒ 停在那一屏）。
            #: ★ 2026-10-08（用户点名「**不是**都是 `0207` 啊」）：号段**别锁死一个** ——
            #: 每站每趟都是伦敦 `0207`，那本身就是个可疑的常量。
            #: ★ 2026-11-02：站点国家先决定形状（US/CA = 10 位 NANP、AU = `04…` 10 位），
            #: 认不出才退回英国 11 位座机；`prefix` 仍然点名压过一切。
            area = str(prefix or "")
            if not area:
                country = _site_phone_country(self)
                if country == "US":
                    return _nanp_phone(_PHONE_US_AREAS)
                if country == "CA":
                    return _nanp_phone(_PHONE_CA_AREAS)
                if country == "AU":
                    return "04" + "".join(str(random.randint(0, 9)) for _ in range(8))
                area = random.choice(("0203", "0207", "0208", "0113", "0114",
                                      "0115", "0116", "0117", "0118", "0121",
                                      "0131", "0141", "0151", "0161", "0191"))
            while True:
                got = area + "".join(str(random.randint(0, 9)) for _ in range(7))
                if not got.startswith(("02079460", "0113496", "0114496", "0115496",
                                       "0116496", "0117496", "0118496", "0121496",
                                       "0131496", "0141496", "0151496", "0161496",
                                       "0191496", "0289018", "0292018")):
                    return got
        if "post" in kind or "zip" in kind:
            return random.choice(("SW1A 1AA", "M1 1AA", "B1 1AA", "LS1 1AA"))
        #: ★ 2026-09-28（用户报的那一趟 `georgiapower`：「我跟他说 street 没填…他还是没改」）：
        #: 产物里那一格的 `FILLS` **有** `city`、`country`、`search_for_address`，可 `_pool`
        #: **一个分支都没有** ⇒ 一律返回空串 ⇒ `value_for` 给不出值 ⇒ **那一格永远填不上** ✗✗
        #: （街/城市/州/国家这几个在真站上都是必填）。补上这几档：
        #: ⚠️ 优先用账本里那趟**真用过的值**（`EXAMPLES`，`value_for` 里先查它）——
        #: 这里是**没有账本值时**的兜底，形状按最常见的美国地址写（邮编那档仍是英国形状，
        #: 谁与谁配套由站点的国家选择决定；填错了站上会拒，不会静默成"成功"）。
        if "street" in kind or "address" in kind or "line1" in kind or "addr" in kind:
            return "%d %s %s" % (random.randint(100, 9899),
                                 random.choice(("Maple", "Oak", "Cedar", "Pine", "Elm", "Birch")),
                                 random.choice(("St", "Ave", "Rd", "Ln", "Dr")))
        if "city" in kind or "town" in kind:
            return random.choice(("Springfield", "Riverton", "Fairview", "Greenville", "Madison",
                                  "Clinton", "Georgetown", "Salem"))
        if "state" in kind or "province" in kind or "county" in kind:
            return random.choice(("CA", "TX", "FL", "NY", "GA", "OH", "IL", "NC"))
        if "country" in kind:
            return random.choice(("United States", "United Kingdom"))
        if "number" in kind or "household" in kind or "occupants" in kind or "count" in kind:
            #: ★ 2026-09-28 同上（那一趟「How many people live in your household?」被填了姓名）：
            #: 人数/件数那一类给个小正整数。
            return str(random.randint(1, 5))
        return ""

    # ---------- 挑哪个（生成器填的那一格）----------

    def _click_spec(self, spec) -> bool:
        """照账本记的那一步点一下：有可用选择器就用第一条，否则**按文字定位再点** ✓。"""
        tgt = (spec or {}).get("target") or {}
        sels = [s for s in (tgt.get("selectors") or []) if str(s or "").strip()]
        if sels:
            try:
                self.cdp.click(sels[0])
                return True
            except Exception:                      # noqa: BLE001 —— 选择器失效就退文字
                pass
        text = str(tgt.get("text") or "").strip()
        return self.click(BY_TEXT + text) if text else False

    def _wait_arrive(self, timeout=45.0) -> bool:
        """等**到达判据**出现（有界 ✓ 每秒看一眼）。空串 ⇒ 这一版没有到达判据，不等。"""
        if not ARRIVE_URL:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            page = self.read()
            if page and str(ARRIVE_URL).lower() in str(page.get("url") or "").lower():
                return True
            self._dly(0.8, 1.6)
        return False

    def run_pre(self, tries=2) -> bool:
        """开跑前照**账本那串前置**走一遍（清横幅 → 滚 → 点进问卷的 CTA ✓），再等到达判据。

        ★ 为什么必须有它（用户 2026-09-24 点破）：没有前置，脚本**压根没到问卷** ✗ ——
        它会在落地页上把同意条那两个按钮当成「题」一直点 ✗✗（那一趟的 trace 就是 30 连击）。
        """
        for _ in range(max(1, tries)):
            for spec in PRE_STEPS:
                act = str((spec or {}).get("action") or "")
                tgt = (spec or {}).get("target") or {}
                sels = [s for s in (tgt.get("selectors") or []) if str(s or "").strip()]
                if act == "scroll":
                    try:
                        self.cdp.scroll(sels[0] if sels else "")
                    except Exception:              # noqa: BLE001 —— 滚不动不是致命
                        pass
                    self._dly(0.6, 1.4)
                    continue
                if act == "click":
                    got = self._click_spec(spec)
                    self.log.info("[%s] 前置：点「%s」-> %s", self.cid,
                                  str(tgt.get("text") or (sels[0] if sels else ""))[:30],
                                  "点了" if got else "没点到")
                    self._trace(step=0, action="pre", ok=bool(got), progress=None,
                                target={"text": tgt.get("text"), "selector": (sels or [""])[0][:40]},
                                progress_why="前置：进问卷之前那几步")
                    self._dly(1.0, 2.0)
            if self._wait_arrive():
                self.log.info("[%s] 前置：到了（地址里有 %s）", self.cid, ARRIVE_URL[:60])
                return True
        return False


    @staticmethod
    def _option_text(o) -> str:
        """选项 token → 它那段文字（三种前缀各切一刀）。"""
        t = str(o)
        if t.startswith(BY_TEXT):
            return t[len(BY_TEXT):]
        if t.startswith(BY_FIELD):
            return t[len(BY_FIELD):].split("\x06", 1)[0]
        return t

    def address_suggestion(self, page):
        """这一屏有没有**与已填地址对得上的联想条目**（`1450 Sunset Blvd…` 这种）。

        ★ 2026-11-06（真事 `job-2aaa04c550b6` · homejolly）：填完街道那格之后**必须马上**
        选一条 —— 再去填下一格（邮箱/电话）会让街道那格失焦，联想列表当场关掉，
        之后就再也选不上了（表单的 name=street/city/state/zip 是选中之后才写的）。
        """
        values = [str(v).strip() for _t, v in (page.get("inputs") or [])
                  if str(v).strip()]
        numbers = []
        for v in values:
            m = re.match(r"\s*(\d{2,6})\b", v)
            if m and re.search(r"[A-Za-z]", v):
                numbers.append(m.group(1))
        if not numbers:
            return None
        for o in (page.get("options") or []):
            t = self._option_text(o)
            if not _looks_street_address(t):
                continue
            if any(re.search(r"(^|\D)%s(\D|$)" % re.escape(n), t) for n in numbers):
                return o
        return None
    def pick_option(self, options, inputs=None):
        """选项里挑一个：**先只认带 `data-testid` 的那些**（那是「真选项」）✓，
        一个都没有才退到「按文字」那一档。`PICK` 决定随机还是取第一个。没有 ⇒ None。

        ★ 2026-11-02（educationdirectory 真站）：提交前站点会弹地址联想
        （`Use ZIP 10118`）—— 原来靠随机挑选项「碰巧」点中。现在先把**已经填进页面的
        五位 ZIP** 与建议文字对一遍，对得上就选那一条；对不上照旧掷骰子。

        ★ 2026-09-24 第一次公平试跑逼出来的：`random` 原来在**全部** token 里挑 ✗ ⇒ 它挑中了
        Cookie 条上的 `si-settings`（设置按钮 ✓ 没有 testid ✓）⇒ 点完什么都没发生 ⇒ 空转到收摊 ✗。
        `goldenagesouls._pick_option` 那边认的正是 `data-testid` ✓ —— 「按文字」是**兜底**，
        不是同一个池子（那里面混着页脚链接、州郡下拉、免责声明 ✗）。
        """
        cand = [o for o in (options or []) if str(o or "").strip()]
        real = [o for o in cand if not str(o).startswith(BY_TEXT)]
        pool = real or [o for o in cand if str(o).startswith(BY_TEXT)]
        if not pool:
            return None
        #: 先对**已填的 ZIP**：联想建议里带着同一串数字才是这一单的那一条。
        values = [str(v).strip() for _t, v in (inputs or []) if str(v).strip()]
        zips = [v for v in values if re.fullmatch(r"\d{5}", v)]
        if zips:
            for o in pool:
                t = str(o)
                if t.startswith(BY_TEXT):
                    t = t[len(BY_TEXT):]
                elif t.startswith(BY_FIELD):
                    t = t[len(BY_FIELD):].split("\x06", 1)[0]
                if any(z in t for z in zips):
                    return o
        #: ★ 2026-11-06（真事 `job-e1af552518de` · homejolly）：**街道地址联想** ——
        #: 街道那格填完之后 Google Places 会弹 `.pac-container .pac-item`（读取器现在交回来）。
        #: 挑哪一条：与已填值**开头那个门牌号**相同、而且长得像地址（带逗号或街道后缀）的
        #: 第一条 —— 与 ZIP 那条同一条道理：带同一串数字的才是这一单的那一条。
        _addr = self.address_suggestion({"options": pool, "inputs": inputs})
        if _addr:
            return _addr
        #: ★ 2026-10-24（Atticus 真站）：**先看提示**。
        #: 现场：随机答成「Family member or friend」/「Patient or client」之后，联系信息屏
        #: 多出一个必填的「Relationship to applicant」下拉 —— 那是我们不会填的格子；
        #: 本人申请那一条根本没有它。提示是**优先**不是**只认**：别的屏上这些字不出现，
        #: 照旧往下掷骰子。
        for want in PICK_HINTS:
            w = str(want or "").strip().lower()
            if not w:
                continue
            for o in pool:
                t = str(o)
                if t.startswith(BY_TEXT):
                    t = t[len(BY_TEXT):]
                elif t.startswith(BY_FIELD):
                    t = t[len(BY_FIELD):].split("\x06", 1)[0]
                if t.strip().lower() == w:
                    return o
        return random.choice(pool) if PICK == "random" else pool[0]

    def click(self, token, scroll_first=False):
        """点一个「选项/按钮」：有 `data-testid` 就用它；没有就**按文字打标记再点**。

        ★ 稳定性那一招（`lastingpowerofattorney.before.py` 的原话：「这些按钮**没有稳定的
        CSS 选择器**」—— 选项 id 是 `option-<epoch毫秒>`、类名一长串）⇒ 找用 JS、点还是
        `cdp click`（真人手势那一层不能省）。

        `scroll_first=True` = 点之前**先把这一颗挪进视口**（`cdp scroll`）。
        ★ 2026-10-13（`job-e8bbc9b2612a`，compareinsulation 的问卷）真事：那一屏手势连点 3 次
        没进，而 `cdp click` 自己的回话里写着 `landing_blind: true`（「那个坐标上取不到节点
        —— 点可能在**视口外**」）⇒ 抬手发出去了，却不一定落在按钮上。读取器那一层只判
        `offsetWidth/Height > 0`，**在视口外它也判「可见」** ⇒ 空点的根就在这儿。
        ⚠️ 手势照旧是**唯一**的点法（契约 §5.2：动页面一律走 cdp，手拼 JS 点击会被 lint 打回）
        —— 这一条只是把它挪到看得见的地方再点。

        ★ 2026-10-16（Task 4）：**动手之前先清一次弹层** —— 与下面那条落点判据**同一处**，
        次序是「先清弹层、再量落点」（表那套 `_perform` 里也是这两句、这个次序）：
        弹层**是页面加载完之后才异步冒出来**的（travelandleisure 那 18 条生产失败），
        只在「开跑前 / 前置之后」看一眼不够 ⇒ 每一次动手之前都再看一眼。
        ⚠️ 它**永不算失败**（探不动 / 点不动只记一行），见 `clear_popups`。
        """
        #: ⚠️ 次序要紧：先清弹层、再量落点（见上面那段）。
        self.clear_popups(why="这一步动手之前")
        token = str(token or "")
        if token.startswith(BY_TEXT):
            text = token[len(BY_TEXT):]
            payload = json.dumps(text)
            #: 扫描集**与读取器一致**（2026-09-24 真站逼出来的）：读取器把 `[role=button]` 也当
            #: 选项，这里少认一种 ⇒ 「读到了却点不到」⇒ 每轮落回「再看一眼」空转到收摊 ✗。
            #: ★ 2026-11-06（`job-e1af552518de` · homejolly）：读取器现在还会把
            #: `[class*=answer|option|choice]` 的普通 div 当选项（`div.leadforms-answer`）——
            #: 这里同一套扫描集也得认，否则「读到了、点不到」。
            js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var t=" + payload + ";var bs=deepAll("
                  "'button,label,[role=button],[class*=answer],[class*=option],[class*=choice]');"
                  "for(var i=0;i<bs.length;i++){var e=bs[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var s=(e.textContent||'').replace(/\\s+/g,' ').trim();"
                  "if(s===t){var old=deepAll('[data-sf-pick]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-pick');}"
                  "e.setAttribute('data-sf-pick','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            #: ★ 本刀：动手之前先量落点 —— 被盖着就不动手，并**如实报**（不是「没找到」）
            if self._covered_before_acting('[data-sf-pick="1"]', "点"):
                return False
            if scroll_first:
                self.cdp.scroll('[data-sf-pick="1"]')
            return self._clicked_ok(self.cdp.click('[data-sf-pick="1"]'))
        if token.startswith(BY_FIELD):
            #: `文字namevalue`：按 **name/value** 找到那个 input，点包着它的
            #: label。为什么不能按文字：同一屏两组 Yes/No 文字一模一样，按文字永远点到
            #: 第一组（2026-11-02 educationdirectory：第二组一直空着、Next 一动不动）。
            rest = token[len(BY_FIELD):]
            _text, _sep, rest = rest.partition("\x06")
            name, _sep, value = rest.partition("\x07")
            js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var n=" + json.dumps(name) + ",v=" + json.dumps(value) + ";"
                  "var es=deepAll('input');var hit=null;"
                  "for(var i=0;i<es.length;i++){var e=es[i];"
                  "if(String(e.name||'')!==n||String(e.value||'')!==v)continue;"
                  "var lb=e.closest?e.closest('label'):null;var t=lb||e;"
                  "if(t.offsetWidth<=0||t.offsetHeight<=0)continue;"
                  "hit=t;break;}"
                  "if(!hit)return 'none';"
                  "var old=deepAll('[data-sf-pick]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-pick');}"
                  "hit.setAttribute('data-sf-pick','1');return 'marked';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            if self._covered_before_acting('[data-sf-pick="1"]', "点"):
                return False
            if scroll_first:
                self.cdp.scroll('[data-sf-pick="1"]')
            return self._clicked_ok(self.cdp.click('[data-sf-pick="1"]'))
        if token.startswith(BY_ID):
            #: 只有 `id` 的元素（见 `BY_ID` 那段）：按 `[id=…]` 点。
            sel = '[id="%s"]' % token[len(BY_ID):]
            if self._covered_before_acting(sel, "点"):
                return False
            return self._clicked_ok(self.cdp.click(sel))
        sel = '[data-testid="%s"]' % token
        if self._covered_before_acting(sel, "点"):
            return False
        return self._clicked_ok(self.cdp.click(sel))

    # ── 落点判据（★ 2026-10-16：循环型第一次有它 —— 与固定步骤表**同一把尺子**）──────
    #
    # 判据（那段 JS）**只写一份**：它住在 `agent/template.py` 里，两套骨架渲的时候取同一份；
    # 这边只有**挂点**与**报文**。为什么循环型也要它（与表那套同一条真站证据）：
    # 拿 token 直接动手、不看目标上有没有盖着东西 —— 点到 consent 弹层上，`cdp` 照样回 ok
    # （那句回话说的是「选择器命中 1 个」，**不是**「点到的就是它」）⇒ 这一步被记成做成了、
    # 页面纹丝不动，后面全塌（2026-09-18 gowizard）。
    #
    #: 这一趟里等过、但没等到它走的遮挡物（`"tag#id.class"`）—— 与表那套同一个字段名。
    #: ⚠️ 类默认值用**不可变**的 `frozenset()`、赋值时**重新绑一个集合**（见 `_covered_by`）：
    #: `object.__new__(Filler)` 那种实例也要够得着；可变对象当类默认值会让所有实例共享一份
    #: （老坑）。
    _stuck_covers = frozenset()
    #: 这一步**因为被盖着而没能动手**时，盖着它的那个东西（空串 = 不是这个原因）——
    #: 每一轮开头清零（见 `run()`），报文那边照它说「被挡着」而不是「没找到」。
    cover_why = ""
    #: ★ 2026-10-16（Task 5）：**这一趟走到第几步** —— 弹层那一行（`_popup_trace`）要带上它。
    #: 为什么它得住在类上：`clear_popups` 被 `run()` 从几处叫（开跑前 / 前置之后 / 每步动手
    #: 之前），它自己不知道步号，而账本里**每一行都带步号**（不带的话自测那句点名会写成
    #: 「第 None 步」）。默认 0 = 「开跑前 / 前置那几步」（与 `run_pre` 里那些行同一个步号），
    #: `object.__new__(Filler)` 那种实例也够得着。
    _step = 0

    def _cover_once(self, selector, frame_id=""):
        """**量一次**：这个选择器指的元素是不是被别的东西盖着。盖着就返回盖它的东西，否则空串。"""
        js = _COVER_JS
        #: ⚠️ 表那套的 `_ev` 干**两件**事：包 `(function(){…})()` **和** `_clean_eval`（脱引号）。
        #: 这一份的 `cdp.eval` 是 `subprocess.run([CDP_PATH,"eval",script])`，**两件都不做**
        #: ⇒ 两件都得在调用处补上（2026-10-16 复核点出来的第二条）：
        #:   ① 包壳：`_COVER_JS` 是**裸语句序列**（里面一句裸 `return '';`）—— 不包 = 浏览器
        #:      那边的 SyntaxError ⇒ 恒返空（2026-10-10 blavity 那次的假通过就是这个形状）；
        #:   ② 脱引号：`cdp eval` 的 stdout 是 **JSON**，字符串结果**带引号**（`"COVER|…"`）——
        #:      不脱的话 `startswith("COVER|")` 恒假 ⇒ **闸门在真站一次都不会开**，而账本上
        #:      跟「没被盖着」一模一样（同一种假通过；桩回裸字符串的用例永远测不出来）。
        got = _clean_eval(self.cdp.eval("(function(){%s})()" % (js % json.dumps(selector)),
                                        frame_id)).strip()
        # ⚠️ **只认带 `COVER|` 前缀的答复**。空串 / `null` / 任何别的形状都是「量不出来」——
        # 一律**不当成盖着**（当成盖着会让每一步都无缘无故不点；不认前缀则会在
        # 「这条 cdp 的 eval 根本不会跑这段」时把它的乱答复读成遮挡）。
        if not got.startswith("COVER|"):
            return ""
        return got[len("COVER|"):]

    def _covered_by(self, selector, frame_id="", until=None):
        """这个选择器指的元素**是不是被别的东西盖着**。盖着就返回盖它的东西（读，不动页面）。

        **盖着不立刻下结论**：先等一等（`until`，单调时钟的截止点；`None` = 不等）。
        等的是**加载蒙版** —— 页面还没加载完，它自己会走（用户 2026-09-18：
        「他不就是加载蒙版吗？没加载完而已」）。**等到头了还不走，才真的当它盖着。**

        `until` 由调用方按**步**给（同一歩所有候选共享一笔预算，见 `COVER_WAIT_SECONDS`）：
        不共享的话，一个被盖住的候选就赔一份，十来步会慢到没法用。

        为什么要它（同一条真站证据）：快路点击拿声明选择器**直接点**，不看目标上有没有盖着
        东西 —— 点到 consent 弹层上，cdp 照样回 ok（`match_count: 1` 说的是「选择器命中 1 个」，
        **不是**「点到的就是它」）。于是这一步被记成做成了，页面纹丝不动，后面全塌。
        表那套的 `_usable` 那条遮挡闸只在回退链上（要 observe 模型），快路没有；
        循环型更干脆：**一支落点闸都没有**（拿到 token 就直接发 cdp）—— 这个函数就是补那一格。

        判据与表那套的 `_usable` 同源（**盖着 = 不能动手**），只是换成在页面上直接量：
        取元素中心点，问「那个点上站着的是谁」，接受自己 / 自己的后代 / 自己的祖先
        （文字节点、label 包 input 这类都是「同一个东西」），别的一律算盖着。
        量不出来（元素不在、零尺寸、点在视口外）**返回空串**（= 不加判断）——
        那种情况由 cdp 自己的滚动与报错去说，不在这里替它下结论。
        """
        waited = False
        recentered = 0
        while True:
            cover = self._cover_once(selector, frame_id)
            # 「没盖着」与「量不出来」都是空串 —— 两种都**不拦**，直接放行。
            if not cover:
                return ""
            # 这个东西**这一趟里已经赖着不走过了** —— 它不是加载蒙版，是真挡路。
            # 不再等（加载蒙版会走，赖过一次的不会）：不然每步白赔一笔，
            # 十来步就是一分多钟的空等。
            if cover in self._stuck_covers:
                return cover
            if until is None or time.monotonic() >= until:
                if waited:
                    # 等过还没走 —— 它已经不是「还没好」，是「挡住了」。记下来（上面那条），
                    # 并把这件事说出来：别让读日志的人以为我们压根没等。
                    # **重新绑一个新集合**，不去改手上那个 —— 类默认值是 `frozenset`
                    # （见类属性那里），`object.__new__(Filler)` 的实例拿到的就是它；
                    # 而可变对象当类默认值会让所有实例共享一份（老坑）。
                    self._stuck_covers = self._stuck_covers | {cover}
                    self.log.info("[%s] 等了 %.1f 秒，%s 还盖着这个元素 —— "
                                  "不等了（它这一趟里已经不是加载蒙版了）",
                                  self.cid, COVER_WAIT_SECONDS, cover)
                return cover
            if not waited:
                waited = True
                self.log.info("[%s] 这个元素现在被 %s 盖着（多半是加载蒙版）—— "
                              "先等它自己走，最多 %.0f 秒", self.cid, cover, COVER_WAIT_SECONDS)
            # ★ 2026-10-13：**等的同时把它挪出遮挡物底下**（见 `_recenter`）——
            # 粘性页眉那类遮挡不是「还没好」，等一辈子也不会走，可它只压住视口顶上那一条：
            # 挪到正中就躲开了（人在那儿就是这么做的）。有界（`RECENTER_TRIES`）：
            # 真弹层挪几次也没用，别把预算花在空滚上 —— 预算到点照旧不动手、照旧失败。
            if recentered < RECENTER_TRIES:
                recentered += 1
                self._recenter(selector, frame_id)
            time.sleep(COVER_POLL_SECONDS)

    def _recenter(self, selector, frame_id=""):
        """把目标**挪到视口正中**（滚一下）。挪不动 / 找不着都**不报错**：它只是「多试一下」。

        ★ 2026-10-13（真事 `job-a6954761e657` · healthinsurancewatch.com.au）：**遮挡物不都是
        「不能碰」—— 有的只是「站错地方了」**。真站实测（读数 `_probe_hiw_cover.py`）：
        填完上一格之后站点自己滚动，`#postcode` 的**中心点正好落在粘性页眉**底下
        （`div.h-full`，[0,0,360,98] —— 视口顶上那一条），于是产物等满 35 秒、**一个候选都没试**，
        报文却只有一句「没找到…没填成」；自测三轮都死在这一步。

        **为什么是「正中」**：粘性页眉只压住顶上那一条、固定页脚只压住底下那一条，正中一定躲得开。
        **为什么不动用 cdp**：这一下只是「把页面挪一挪」，JS 的 `scrollIntoView` 就够，
        不必为它多起一个进程（生产重跑那条路越便宜越好）。
        **量不出来就当没做**（返回空串）：判据仍然是 `_cover_once` 那一把尺子 ——
        这一格改的只是「有没有资格再看一眼」，不是「算不算盖着」。
        """
        js = _RECENTER_JS
        #: ⚠️ `_RECENTER_JS` **自带** `(function(){…})()` 壳 ⇒ 这一处**不要再包一层**
        #: （双层包裹当场坏：外层的返回值被丢掉，判据拿不到 `ok`）。
        #: ⚠️ 但**脱引号这一件照旧要做**（同 `_cover_once` ②）：真站回的是 `"ok"`（带引号）；
        #: 不脱的话「没挪动」（回 `""`）也会被读成挪动了 —— 表那套的 `_ev` 两件都做。
        try:
            got = _clean_eval(self.cdp.eval(js % json.dumps(selector), frame_id)).strip()
        except Exception:                                   # noqa: BLE001
            return ""
        if got:
            self.log.info("[%s] 把它挪到视口正中再看一眼（遮挡物是页眉/页脚那类就躲开了）",
                          self.cid)
        return got

    def _covered_before_acting(self, selector, doing):
        """动手**之前**量一次落点：被盖着就**不动手**，并把「谁盖着」记在这一步上（如实报）。

        ★ 2026-10-16 本刀：循环型第一次有落点判据（由头同 `_covered_by` 里那条 ——
        2026-09-18 gowizard：点到盖着它的东西上，`cdp` 照样回 ok ⇒ 被记成做成了）。
        返回 `True` = **这一下别动手**：调用方照实把这一步记成没做成，报文里点名
        `self.cover_why`（**不许**写成「没找到」—— 两件事说成一句话，读的人只会去查选择器）。
        """
        cover = self._covered_by(selector, "", until=time.monotonic() + COVER_WAIT_SECONDS)
        if not cover:
            return False
        self.cover_why = cover
        self.log.info("[%s] 挑中的那一颗被 %s 盖着 —— 这一下不%s", self.cid, cover, doing)
        return True

    # ---------- 主循环 ----------

    # ── 结构化快照兜底（★ 2026-09-28，`try` 那站的问卷逼出来的）────────────────
    #
    # 为什么要有它：那一站的问卷第一屏，产物里那几行轻量 JS **读不到控件**（真 trace：连 60 步
    # 「没有可动作的东西」、答过 0 题），而**探路那一层**（`observe` / `screenshot`，同一台机器）
    # 在同一屏上是做得动的 ⇒ 差的是**读取层**：
    #   - 轻量 JS 只认那几种标签，也不往外走（闭 shadow / 跨帧看不见）；
    #   - `cdp snapshot` 是**跨帧的结构树**（生产那份 `CDPHelper` 本来就有它）。
    # 所以：**快路先用轻量 JS；连着几轮读不到东西时，换一次结构化快照**，从树里挑
    # 「能填的」与「能点的」，用**结构选择器**动手（`body > div:nth-of-type(1) > …` 链 ——
    # 探路那份产物用的就是这个形状；那站的 class 是 `css-1nb0d45` 这种每次都不一样的随机名，
    # 靠不住）。
    # ⚠️ 三条纪律：① 一轮只试一次、**同一屏只试一次**（签字 = 读取器给的头/地址）；
    # ② 全程 `STRUCTURED_LIMIT` 次上限；③ **真填/真点成功**才 `idle = 0`
    # （「换了读法」本身不算「这一屏有可动的东西」）。
    #: 结构化这一路全程最多动手几次（它是**兜底**，不是主路）
    STRUCTURED_LIMIT = 12
    #: 结构化树里「像下一步」的词（与读取器那段 JS 同一套 —— 一处一张嘴）
    NEXT_WORDS = ("next", "continue", "submit", "finish", "done", "complete",
                  "install", "get results", "see results", "start")
    #: 结构化树里**不点**的（往回走 / 页脚 / 站标那一族 —— 与 JS 里那份 NAVISH 同一套；
    #: 「logo」是补的：真跑里那颗站标也读成了可点的东西，点它等于**导航走** ✗）
    NAVISH = ("previous", "back", "edit", "home page", "terms", "privacy", "policy",
              "disclaimer", "cookie", "sign in", "log in", "logo")

    def _tree_text(self, node, cap=60):
        """结构树里那一棵的文字（递归拼、压空白、截断 —— 选项都很短）。"""
        got: list = []

        def walk(n, depth=0):
            if depth > 6 or not isinstance(n, dict) or len(" ".join(got)) > cap:
                return
            if str(n.get("text") or "").strip():
                got.append(str(n["text"]).strip())
            for kid in (n.get("children") or []):
                walk(kid, depth + 1)

        walk(node)
        return " ".join(" ".join(got).split())[:cap]

    def _tree_rows(self, snap):
        """`snapshot()` → `[(frame_id, 结构选择器, tag, attr, 文字)]`（跨帧、深度有界）。

        ⚠️ `nth-of-type` 是**按同标签**数的（`div` 与 `label` 各数各的）—— 数错一位，
        选择器就指向另一个元素（探路那份产物里就是这个形状 ✓）。
        """
        frames = []
        if isinstance(snap, dict):
            if isinstance(snap.get("frames"), list):
                frames = list(snap["frames"])
            elif snap.get("frame"):
                frames = [snap["frame"]]
        rows: list = []
        for fr in frames:
            if not isinstance(fr, dict):
                continue
            fid = str(fr.get("frameId") or "")

            def walk(node, path, depth=0):
                if depth > 12 or not isinstance(node, dict):
                    return
                seen: dict = {}
                for kid in (node.get("children") or []):
                    if not isinstance(kid, dict):
                        continue
                    tag = str(kid.get("tag") or "").lower()
                    seen[tag] = seen.get(tag, 0) + 1
                    seg = "%s:nth-of-type(%d)" % (tag, seen[tag]) if tag else ""
                    here = ("%s > %s" % (path, seg)) if (path and seg) else (seg or path)
                    rows.append((fid, here, tag, kid.get("attr") or {}, self._tree_text(kid)))
                    walk(kid, here, depth + 1)

            walk(fr.get("body"), "body", 0)
        return rows

    def structured(self):
        """`snapshot()` → `{"fields": [...], "actions": [...]}`；**读不回来给 `None`**（不编）。"""
        try:
            snap = self.cdp.snapshot()
        except Exception as exc:                    # noqa: BLE001 —— 外面世界
            self.log.warning("[%s] 结构化快照读不回来：%s", self.cid, exc)
            return None
        fields: list = []
        by_text: dict = {}
        for fid, sel, tag, attr, text in self._tree_rows(snap):
            if tag in ("input", "textarea", "select"):
                if str(attr.get("type") or "").lower() in ("hidden", "submit",
                                                           "button", "image"):
                    continue
                hint = " ".join(str(attr.get(k) or "") for k in
                                ("placeholder", "aria-label", "name", "id", "data-testid"))
                fields.append({"frame": fid, "selector": sel, "attr": attr, "hint": hint})
                continue
            if not text or len(str(text)) > 44:
                continue
            low = str(text).lower()
            if any(w in low for w in self.NAVISH):
                continue
            by_text[str(text)] = {"frame": fid, "selector": sel, "text": str(text),
                                  "next": any(w in low for w in self.NEXT_WORDS)}
        return {"fields": fields, "actions": list(by_text.values())}

    @staticmethod
    def _token_of(field) -> str:
        """结构化那一格 → 产物**自己那套 token**（值池与蜜罐都按 token 判）。认不出给空串。"""
        attr = field.get("attr") or {}
        for key, pre in (("data-testid", ""), ("placeholder", BY_PLACEHOLDER),
                         ("id", BY_ID), ("name", BY_ID)):
            val = str(attr.get(key) or "").strip()
            if val:
                return pre + val
        return ""

    def _structured_try(self, step, page) -> bool:
        """兜底那一路：读 → 填 → 点。**真动手了**才返回 `True`（并把它记进 trace）。"""
        left = getattr(self, "_structured_left", self.STRUCTURED_LIMIT)
        if left <= 0:
            return False
        sig = "%s|%s" % (str(page.get("url") or ""), str(page.get("head") or "")[:60])
        if sig == getattr(self, "_structured_sig", ""):
            return False                      # 同一屏只试一次
        got = self.structured()
        if got is None:
            return False                      # 读不回来：不算试过（下一轮还能再试）
        self._structured_sig = sig
        self._structured_left = left - 1
        did = False
        for f in got["fields"][:3]:
            token = self._token_of(f)
            if not token or token in self._filled:
                continue
            value = self.value_for(token, {"inputs": [[token, ""]]}, 0)
            if not value:
                continue
            if self._filled_ok(self.cdp.form(f["selector"], value=value,
                                             frame_id=f["frame"])):
                self._filled.add(token)
                did = True
                self.log.info("[%s] step %d: 结构化快照填了 %s（%s）",
                              self.cid, step, token, f["selector"][:60])
                self._trace(step=step, action="form", ok=True, progress=False,
                            target={"selector": f["selector"]},
                            note="结构化快照填了 %s" % token)
        #: ★ 2026-09-28：**挑哪一处点**有讲究 —— 真跑撞过一次：那一屏的 FAQ 标题（「What is
        #: Atticus?」这种）也被读成可点的东西，于是循环一直在点它、页面当然不动 ✗。
        #: 判据：先「像下一步」的词 ✓，再把**像问句的标题**（`?` 结尾，或以 what/why/how/is… 开头）
        #: 排到后面 ✓，其余（选项那种短名词/短语）优先 ✓。**只点一处**（不连环点）。
        def rank(a):
            low = str(a.get("text") or "").lower()
            question = low.rstrip().endswith("?") or low.startswith(
                ("what ", "why ", "how ", "is ", "do ", "does ", "can ", "when ", "where "))
            return (0 if a.get("next") else 1, 1 if question else 0)

        acts = sorted(got["actions"], key=rank)
        if acts and self._filled_ok(self.cdp.click(acts[0]["selector"], acts[0]["frame"])):
            did = True
            act = acts[0]
            self.log.info("[%s] step %d: 结构化快照点了「%s」（%s）",
                          self.cid, step, act["text"], act["selector"][:60])
            self._trace(step=step, action="click", ok=True, progress=False,
                        target={"text": act["text"], "selector": act["selector"],
                                "frame_id": act["frame"]},
                        note="结构化快照点了「%s」" % act["text"])
        if not did:
            self.log.info("[%s] step %d: 结构化快照也没读出能动的东西（fields=%d actions=%d）",
                          self.cid, step, len(got["fields"]), len(got["actions"]))
        return did

    def run(self, max_steps=MAX_STEPS):
        try:
            self.log.info("[%s] loop: site=%s provenance=%s", self.cid, SITE,
                          PROVENANCE.get("site"))
            #: ★ 2026-10-16（Task 4）：**开跑之前先清一次弹层**（与固定步骤表 `run()` 里
            #: 那一处同源）—— 生产每单都是新窗口、每单都会遇到同意弹层，而账本学的是
            #: 「没有弹层的世界」（见 `clear_popups` 的 docstring）。
            #: ⚠️ 位置在导航**之前**：平台开好的那一页上（不导航那一支）弹层可能已经在——
            #:    导航之后还有 `run_pre()` 之后那一次，三步互补。
            #: ⚠️ 它**永不算失败**（清不掉也照旧往下走）。
            self.clear_popups()
            if ENTRY_URL:
                self.log.info("[%s] navi -> %s", self.cid, ENTRY_URL)
                try:
                    self.cdp.navigate(ENTRY_URL)
                    self._dly(1.5, 3.0)
                except Exception as exc:            # noqa: BLE001 —— 外面世界
                    self.log.error("[%s] 导航到 %s 没成：%s —— 如实报失败",
                                   self.cid, ENTRY_URL, exc)
                    self._rpt("step", "navi failed")
                    self._stop(0, "give_up", "导航到入口网址没成：%s" % str(exc)[:60])
                    return False
            else:
                #: ★ 2026-09-24（生成器产出来的第一份在真站上**当场**逼出来的）：入口网址**空**
                #: 不是错 —— 那是「**谁开页面谁负责**」（2026-09-23 用户定的规矩：产物不自己导航一遍）。
                #: 生产那份样本（`goldenagesouls.py` 的 `run()`）也是**一句导航都没有**，直接从
                #: 当前这一页开干 ✓。原来这里写成「没有入口网址 ⇒ 如实报失败」，于是生成器产的
                #: 那一份一上来就退（`EXIT=1`），而它明明能干活。
                #: ⚠️ 不导航时**前置那几步照走**（点同意条 / 点进问卷）：它们本来就只在
                #: 「页面停在落地页」时成立；平台要是已经停在问卷页，那几步点不到是**正常的**，
                #: 后面 `_wait_arrive()`（到达判据）会兜住。
                self.log.info("[%s] 这一版**不自己导航**（谁开页面谁负责）——就在当前这一页上开干",
                              self.cid)
                self._trace(step=0, action="goto", ok=None, progress=None,
                            progress_why="不导航：页面由平台按任务开好（产物不自己导航一遍）")

            #: ★ 前置：**先到问卷，再谈答题**（没到就别往下走 —— 那只是在落地页上瞎点 ✗）。
            if not self.run_pre():
                self.log.error("[%s] 前置没走成（没等到 %s）—— 如实报失败",
                               self.cid, ARRIVE_URL[:60])
                self._rpt("step", "pre-steps failed")
                self._stop(0, "give_up", "前置没走成（没等到 %s）" % ARRIVE_URL[:40])
                return False

            #: ★ 2026-10-16（Task 4）：**前置那几步点完之后再看一眼** —— 前置本身就在点
            #: （清横幅 / 点进问卷的 CTA），同意层常常正是被那几下**唤出来**的；而下一处探针
            #: 要等到第一次动手（`fill` / `click`）才跑，中间隔着「读这一屏」。
            #: ⚠️ 同一条纪律：它**永不算失败**（清不掉照旧往下走）。
            self.clear_popups(why="前置之后")

            answered = 0
            idle = 0
            busy_since = None
            for step in range(1, max_steps + 1):
                #: ★ 2026-10-16（Task 5）：账本里每一行都带步号 —— `clear_popups` 那几行
                #: 由它读（它自己没有步号，见类属性那一处）。
                self._step = step
                #: ★ 2026-09-30（用户裁定「Py 必须通过稳定性测试才能交付」）：`--stop-at` 在这个族里
                #: 就是「**跑到第 N 步就停**」（循环稿没有固定步表，一步 = 看过一屏）。
                #: 形状与平铺那一族**逐字对齐**：先落一行 `{"stopped_at": N}`、再走，退出码非 0
                #: （「不谎报成功」）。自测判那一遍看的就是这一行（`selftest._verdict`）。
                #: ⚠️ 这一族**不用它做「停在提交前一步」**：提交发生在漏斗走到头的那一刻，
                #: 静态说不清是第几步 ⇒ 自测对循环稿**不做同环境复跑**（见 `selftest.run` 的 `loop`）。
                if self.stop_at and step >= int(self.stop_at):
                    self._trace(step=step, action="none", ok=None, progress=None,
                                progress_why="按 --stop-at 停在第 %d 步" % step,
                                stopped_at=int(self.stop_at))
                    self.log.info("[%s] 按 --stop-at 停在第 %d 步", self.cid, step)
                    return False
                self._dly(0.6, 1.8)
                page = self.read()
                #: ★ 2026-10-16 本刀：**每一轮开头清零** —— 「这一步被谁盖着」是**这一轮的读数**，
                #: 不外借：上一轮那一颗遮挡物这一轮还在不在，由这一轮自己量了再说
                #: （报文那边靠它把「被挡着」和「没找到」分开说）。
                self.cover_why = ""
                if page is None:
                    idle += 1
                    self.log.info("[%s] step %d: 页面读不到（%d）", self.cid, step, idle)
                    self._trace(step=step, action="read", ok=None, progress=None,
                                progress_why="页面读不到")
                    if idle >= IDLE_LIMIT:
                        self.log.error("[%s] 连续 %d 步读不到页面 —— 如实报失败", self.cid, idle)
                        self._rpt("max_steps", "page unreadable")
                        self._stop(step, "read", "连续 %d 步读不到页面（IDLE_LIMIT=%d）"
                                   % (idle, IDLE_LIMIT))
                        return False
                    continue

                url = page.get("url", "")
                if self.is_success(url):
                    self.log.info("[%s] SUCCESS(url): %s", self.cid, url[:90])
                    self._trace(step=step, action="success", ok=True, progress=True,
                                url=url[:120])
                    self._rpt("success", url[:60])
                    return True
                text = (page.get("head") or "")
                if SUCCESS_TEXTS and any(str(t).lower() in text.lower() for t in SUCCESS_TEXTS):
                    self.log.info("[%s] SUCCESS(text): %s", self.cid, text[:60])
                    self._trace(step=step, action="success", ok=True, progress=True,
                                url=url[:120], progress_why=text[:60])
                    self._rpt("success", text[:60])
                    return True

                #: ★ 2026-10-13（用户口径：**修系统**，不是修某一份）：「同意条这类
                #: **啥时候冒出来都不确定**」⇒ 每一轮**动手之前**先看这一屏有没有同意层，
                #: 有就清掉、这一轮到此为止（下一轮重读，免得刚点掉的东西还留在读数里）。
                #: ⚠️ 判据是读取器**这一读**带回来的 `consent` 那一格 —— **不额外往返**。
                #: ⚠️ 位置在**成功判据之后**：已经到成功页了就别再动页面。
                #: ⚠️ `clear_cookies()` 清不掉**不算失败**（它自己只记一行）—— 与「那一步
                #: 是软步骤」同一条口径：同意层是环境噪声，不该有结束整趟的权力。
                if page.get("consent"):
                    self.log.info("[%s] step %d: 这一屏有同意层 —— 先清掉再动手", self.cid, step)
                    self._trace(step=step, action="popup", ok=None, progress=None,
                                progress_why="这一屏有同意层（cookies/consent/privacy），先清掉",
                                target={"consent": True})
                    self.clear_cookies()
                    idle = 0
                    busy_since = None
                    continue


                # ① 有空的可填 ⇒ 填第一个（「空了才填」⇒ 一格/两格的形态切换天然不怕）
                todo = [t for t, v in (page.get("inputs") or []) if t and not v
                        and not any(s in str(t).lower() for s in SKIP_FIELDS)]
                #: ★ 2026-11-06（真事 `job-2aaa04c550b6` · homejolly）：这一屏有**与已填地址
                #: 对得上的联想条目** ⇒ 先去选它（见 `address_suggestion`）—— 再填下一格会让
                #: 街道那格失焦、联想列表当场关掉，之后就再也选不上了。
                if todo and not self.address_suggestion(page):
                    #: ★ 2026-09-26（第四次真站试跑）：那一屏有 **三个** 格（full name / email /
                    #: phone），原来只填 `todo[0]` 就 `continue` ⇒ 那个格被站点回读成空就又填它
                    #: （每次还塞一个**新的随机值**）⇒ 后面两格**永远轮不到** ⇒ 提交一直被拒 ✗。
                    #: ⇒ 一轮把**空着的格都填掉**（上限 3 格），而且**每一格记住第一次给的值**
                    #: （重试就重试那一个值，不再换新的 —— 换来换去只会让站点永远收不到一个完整的表单）。
                    filled_any = 0
                    for tid in todo:
                        if filled_any >= 3:
                            break
                        tries = self._field_tries.get(tid, 0)
                        if tries >= 2:
                            continue            # 这一格试过两次了：别再往里塞东西
                        val = self._field_value.get(tid) or self.value_for(tid, page,
                                                                           todo.index(tid))
                        if not val:
                            continue
                        if (tid, val) in self._filled:
                            #: ★ 2026-10-22（Atticus 真站）：`todo` 只收**页面上空着**的格 ——
                            #: 这一格空了，说明上一次填的值被页面弄丢了（hydration/重渲染）。
                            #: 「填过」的记号对「值已经不在」不成立 ⇒ 抹掉，用**同一个值**再填一次。
                            self._filled.discard((tid, val))
                        #: ★ 2026-10-16 复核修：**动手之前清零** —— `cover_why` 是**这一次动手**
                        #: 的读数，不许外借给同一轮里别的失败（一轮里最多 3 填 + 1 点；
                        #: 表那套的守卫是 `if self.cover_why and not tried_any:`，这边靠这一行
                        #: 拿到同一个语义：没量过落点的失败，不许拿遮挡物背锅）。
                        self.cover_why = ""
                        if not self.fill(tid, val):
                            self.log.info("[%s] step %d: %s 没填上（%s）⇒ 下一轮再看", self.cid,
                                          step, tid, getattr(self, "_fill_why", "") or "原因未记")
                            if self.cover_why:
                                #: ★ 2026-10-16 本刀：**「被盖着」不是「没找到」**（同 ② 那段）
                                self._trace(step=step, action="form", ok=False, progress=None,
                                            target={"testid": tid},
                                            note="这一格被 %s 盖着 —— 这一下不填"
                                                 % self.cover_why,
                                            progress_why="这一格被 %s 盖着（等过、也把它往"
                                                         "视口正中挪过，它没走）—— 没敢填"
                                                         % self.cover_why)
                            else:
                                self._trace(step=step, action="form", ok=False, progress=None,
                                            target={"testid": tid},
                                            progress_why="这一格没填上（不是「填过了」）：%s"
                                                         % (getattr(self, "_fill_why", "")
                                                            or "原因未记"))
                            continue             # 填不上就下一轮再看（绝不谎报填了）
                        self._field_value[tid] = val
                        #: ★ 2026-10-29：值是**资料**的还是**账本示例/池子**的，写进日志 ——
                        #: 原来只写「填了 zipcode」，两个月没人发现填的一直是示例 10001。
                        self._field_from[tid] = getattr(self, "_value_from", "") or "来源未记"
                        self._field_tries[tid] = tries + 1
                        self._filled.add((tid, val))
                        filled_any += 1
                        self._empty = 0  # ★ 只有**真填成功**才算是「这一屏有可动的东西」
                        self.log.info("[%s] step %d: 填了 %s（%s）", self.cid, step, tid,
                                      self._field_from[tid])
                        self._trace(step=step, action="form", ok=True, progress=False,
                                    target={"testid": tid},
                                    note="填了 %s（%s）" % (tid, self._field_from[tid]))
                        #: ★ 2026-11-06（真事 `job-2aaa04c550b6` · homejolly）：填完**地址那格**
                        #: 先停手 —— 接着填下一格会让它失焦、联想列表当场关掉；下一轮先去选联想。
                        if ("address" in str(tid).lower() or "street" in str(tid).lower()):
                            break
                    if filled_any:
                        idle = 0
                        busy_since = None
                        self._rpt_if_moved(step, url, "填了 %d 格" % filled_any)
                        continue

                # ② 有题可选 ⇒ 挑一个（随机或第一个）—— ★ **同一屏只答一次**。
                #: ★ 2026-09-26 真 trace（`job-c053e0b049f3`，用户原话「场上有选项但是选完还得点
                #: 按钮那种场景他就过不去」）：那一趟 **58 次点击全是「答了第 N 题」** ——
                #: ② 每轮都重新挑一个选项，于是**永远轮不到 ③ 去点 Continue**，
                #: 在「先选一个、再点下一步」的那种屏上原地打转直到收摊。
                #: ⇒ 记住「这一屏已经答过了」，下一轮直接去点 Next/Continue。
                #: ⚠️ 只有**点成功**才记（点空了不记 ⇒ 下一轮还挑，保留重试）。
                #: ⚠️ 点完**换屏**了 ⇒ 签名变 ⇒ 新屏照常挑 ✓（自动前进的那种漏斗不受影响）。
                #: ⚠️ 签名**不含 `progress`**：答完一题站点常常把百分比往上推（实测 69% ⇒ 77%），
                #: 带上它的话「同一屏」会被读成新屏 ⇒ 又答一次 ⇒ 在单页漏斗上反复点 ✗。
                #: 题面（head）+ **选项那一串文字**才是「这是不是同一道题」的判据。
                #: ★ 2026-11-02（educationdirectory 真站）：**同一屏会追加题** ——
                #: 选了「Still deciding」才冒出两颗必答的 Yes/No（rn_license /
                #: teaching_certificate），而上面那条签名截在 160 字、追加的选项排在末尾
                #: ⇒ 签名一点都不变 ⇒ 下一轮读成「这一屏答过了」⇒ 直接点 Next ⇒ 表单校验
                #: 不过、页面一动不动（自测第 8 步那三次空点）。现在读取器多交一格
                #: `unanswered`（还没选过的那几组）：签名带上它，每答完一组就变一次；
                #: 挑也只从那一格里挑 —— 老读取器没有这一格 ⇒ 照旧按 options 挑。
                unanswered = page.get("unanswered")
                answered_sig = "%s|%s|%s" % ((page.get("head") or "")[:60],
                                             "|".join(str(o) for o in (page.get("options") or []))[:160],
                                             "|".join(str(o) for o in (unanswered or [])))
                pick = self.pick_option(unanswered if isinstance(unanswered, list)
                                        else page.get("options"), page.get("inputs"))
                if pick and answered_sig != getattr(self, "_answered_sig", ""):
                    self._empty = 0
                    #: ★ 2026-10-16 复核修：同上 —— 这一下动手之前清零（`click()` 打标记返
                    #: `none` / 别的早退路径压根不量落点 ⇒ 上一步的遮挡物会串味到这里）。
                    self.cover_why = ""
                    if self.click(pick):
                        answered += 1
                        idle = 0
                        busy_since = None
                        self._answered_sig = answered_sig
                        self._stuck_retries = 0      # 这一屏答成了 ⇒ 次数归零（见 ⑤）
                        self.log.info("[%s] step %d [%s] %s -> %s", self.cid, step,
                                      page.get("progress", "?"), (page.get("head") or "")[:48],
                                      str(pick)[:32])
                        self._trace(step=step, action="click", ok=True, progress=True,
                                    target={"token": str(pick)[:48]},
                                    note="答了第 %d 题" % answered)
                        self._rpt_if_moved(step, url, "答了第 %d 题：%s"
                                                    % (answered, str(pick)[:32]))
                        continue
                    if self.cover_why:
                        #: ★ 2026-10-16 本刀：**「被盖着」不是「没找到」** —— 两件事说成
                        #: 同一句话，读的人只会去查选择器（而这里连一次 cdp 都没发出去）。
                        self.log.info("[%s] step %d: 「%s」被 %s 盖着 —— 这一下不点",
                                      self.cid, step, str(pick)[:32], self.cover_why)
                        self._trace(step=step, action="click", ok=False, progress=None,
                                    target={"token": str(pick)[:48]},
                                    note="挑中的那一颗被 %s 盖着 —— 这一下不点" % self.cover_why,
                                    progress_why="挑中的选项被 %s 盖着（等过、也把它往视口"
                                                 "正中挪过，它没走）—— 没敢点" % self.cover_why)
                    else:
                        self.log.info("[%s] step %d: 「%s」这一下没点到（页面上没找到那段文字）"
                                      "⇒ 下一轮再看", self.cid, step, str(pick)[:32])
                        self._trace(step=step, action="click", ok=False, progress=None,
                                    target={"token": str(pick)[:48]},
                                    progress_why="挑中的选项在页面上没找到（点了没点到）")
                elif pick:
                    #: ★ 2026-09-26：**这一屏已经答过了**（② 故意不再挑）⇒ 下一轮该去点下一步；
                    #: 要是它就是没有「下一步」可点，⑤ 那句「没有可动作的东西」会误导人
                    #: （屏幕上明明有选项）—— 所以把这件事**记下来**，⑤ 照着说。
                    self._answered_but_stuck = True

                # ③ 只剩 Next ⇒ 点它
                if page.get("nextVisible"):
                    self._empty = getattr(self, "_empty", 0) + 1
                    if self._empty < EMPTY_ROUNDS_BEFORE_NEXT:
                        self.log.info("[%s] step %d: 这一屏可见的东西还没有 ⇒ 再看一眼再决定%s",
                                      self.cid, step, "" if self._empty < 2 else "（下一轮就轮到 Continue）")
                        self._trace(step=step, action="none", ok=None, progress=None,
                                    progress_why="Next 已可见、这一屏还没成形（第 %d 轮）"
                                                 % self._empty,
                                    head=(page.get("head") or "")[:80],
                                    target={"options": len(page.get("options") or []),
                                            "inputs": len(page.get("inputs") or []),
                                            "nextTxt": str(page.get("nextTxt") or "")[:24]})
                        continue
                    #: ★ 2026-10-24（Atticus `survey/claim/zip`）：**Next 是禁用态、格子又有值**
                    #: ⇒ 先把值重打一遍（见 `_nudge`）—— 干点 Next 只会空转到收摊。
                    if page.get("nextDisabled") and self._nudge(page):
                        idle = 0
                        continue
                    #: ★ 2026-10-29（Atticus `age-interstitial-*` / `personalization-question`）：
                    #: Next 是禁用态、又没有可重打的格子 ⇒ 站点在发请求 / 还没轮到这一步。
                    #: 这时候点它 = **点空气**，还会把「同一屏最多几次」的额度烧光（真站就这么
                    #: 连点 8 次收摊，而那一屏再等几秒自己就好了）⇒ **等**，最多等 `BUSY_TIMEOUT`。
                    if page.get("nextDisabled"):
                        if busy_since is None:
                            busy_since = time.time()
                        waited = time.time() - busy_since
                        if waited >= BUSY_TIMEOUT:
                            self.log.error("[%s] Next 一直是禁用态、等了 %.0f 秒 —— 如实报失败",
                                           self.cid, waited)
                            self._rpt("max_steps", "next disabled %.0fs" % waited)
                            self._stop(step, "wait",
                                       "Next 一直是禁用态 %.0fs（BUSY_TIMEOUT=%ds）"
                                       % (waited, BUSY_TIMEOUT))
                            return False
                        idle = 0                # 等 ≠ 空转
                        self.log.info("[%s] step %d: Next 是禁用态、没有可重打的格子 ⇒ 等它（%.0fs/%ds）",
                                      self.cid, step, waited, BUSY_TIMEOUT)
                        self._trace(step=step, action="wait", ok=None, progress=None,
                                    progress_why="Next 是禁用态、没有可重打的格子 —— 等它 %.0fs"
                                                 % waited)
                        time.sleep(DISABLED_NEXT_WAIT)
                        continue
                    #: ★ 2026-10-13（真事 `job-e8bbc9b2612a`）：这一道闸原来拿 `answered_sig`
                    #: （题面 + 选项）当「同一屏」的判据，而那一族的 `head` 是**页面标题** ——
                    #: 每一屏都长一样（「Free Eligibility Checker」）⇒ 计数器**跨屏累加**：
                    #: 那一趟在 postcode/name 两屏上点过之后，**第一次**点 email 那一屏的 Continue
                    #: 就直接收摊 ✗（trace 里 step 11/14 都写着 `retry: True` —— 它们根本不在同一屏上）。
                    #: ⇒ 换成**看得见的形状**：题面 + 选项 + **进度** + **这一屏有哪几个格**。
                    #: ⚠️ ② 挑选项那边**照旧不含 progress/inputs**（答完一题站点常把百分比往上推，
                    #: 带上它「同一屏」会被读成新屏 ⇒ 又答一次 ✗）—— 这把尺子只给 ③ 用。
                    next_sig = "%s|%s|%s" % (answered_sig, page.get("progress") or "",
                                             "|".join(str(t) for t, _v in (page.get("inputs") or []))[:120])
                    if next_sig == getattr(self, "_last_next_sig", ""):
                        self._same_next = getattr(self, "_same_next", 0) + 1
                    else:
                        self._same_next = 0
                    self._last_next_sig = next_sig
                    if self._same_next >= SAME_NEXT_LIMIT:
                        self.log.error("[%s] 连着 %d 次点 Next、页面还是那一屏 —— 如实报失败",
                                       self.cid, self._same_next)
                        self._rpt("max_steps", "next without change")
                        self._stop(step, "click", "连着 %d 次点 Next、页面还是那一屏"
                                   % self._same_next)
                        return False
                    #: ★ 2026-10-13（真事 `job-e8bbc9b2612a` 的问卷）：这一屏**已经点过一次、
                    #: 页面还是没变** ⇒ 这一下换两样东西再试一次：**先让站点那边落定**（等几秒，
                    #: 它这一屏在做异步校验），**再把按钮挪进视口**（`scroll_first` —— 那一趟
                    #: `cdp click` 自己就报了 `landing_blind`：坐标上取不到节点、抬手空放了）。
                    #: ⚠️ 点法**还是手势**（契约 §5.2）—— 不许改成 JS 点击（lint 会打回）。
                    #: ⚠️ `ok` 报的是**这一下有没有点出去**（原来写死 `True` ⇒ trace 里每一次 Next
                    #: 都是「成功」，失败那几次在账上一点痕迹都没有，诊断只能判「不知道」✗）。
                    retrying = self._same_next >= 1
                    if retrying:
                        self._dly(2.0, 4.0)
                    clicked = self.click(BY_TEXT + str(page.get("nextTxt") or "Next"),
                                         scroll_first=retrying)
                    idle = 0
                    busy_since = None
                    self.log.info("[%s] step %d: Next（%s）%s", self.cid, step,
                                  (page.get("head") or "")[:40], "（重试：先落定、挪进视口）" if retrying else "")
                    self._trace(step=step, action="click", ok=bool(clicked), progress=None,
                                progress_why="点了 Next（%s），页面变没变由下一轮读出来"
                                             % ("重试：先落定、挪进视口" if retrying else "第一次"),
                                target={"text": str(page.get("nextTxt") or "Next"),
                                        "retry": (True if retrying else None)}, note="Next")
                    #: ★ 2026-10-29：点完**先等页面变**，别 1.5 秒一次地连点（见 `_wait_for_change`）。
                    if clicked:
                        self._wait_for_change(page)
                        self._rpt_if_moved(step, url, "点了 Next（%s）"
                                                    % (page.get("head") or "")[:40])
                    continue

                # ④ 没得点：先分清「页面在处理」还是「真的没事干」
                head = page.get("head") or ""
                if any(m in head.lower() for m in BUSY_MARKERS):
                    if busy_since is None:
                        busy_since = time.time()
                    waited = time.time() - busy_since
                    if waited >= BUSY_TIMEOUT:
                        self.log.error("[%s] 页面在「处理」态停了 %.0f 秒 —— 如实报失败",
                                       self.cid, waited)
                        self._rpt("max_steps", "busy %.0fs" % waited)
                        self._stop(step, "wait", "页面在「处理」态停了 %.0fs（BUSY_TIMEOUT=%ds）"
                                   % (waited, BUSY_TIMEOUT))
                        return False
                    idle = 0                    # 忙 ≠ 空转
                    self.log.info("[%s] step %d: 页面在处理中（%.0fs/%ds）", self.cid, step,
                                  waited, BUSY_TIMEOUT)
                    self._trace(step=step, action="wait", ok=None, progress=None,
                                progress_why="页面在处理中 %.0fs" % waited)
                    continue

                # ⑤ 真的没事干（★ 先怀疑同意条：它异步注入、会盖住整页 ✗）
                if not self._cookie_gone():
                    self.clear_cookies()
                #: ★ 2026-09-28：轻量读取器读不到的两种情形都要走兜底 ——
                #:   ① 连着几轮**什么都没有**（`idle` 涨到 `EMPTY_ROUNDS_BEFORE_NEXT`）；
                #:   ② **同一屏打转**（`_answered_but_stuck`：屏幕上那几样读得到、可全都点不动
                #:      页面 —— 真跑撞过：阅读器把 FAQ 那几颗当选项，一直点它、`idle` 永远只有 1）。
                #: ⚠️ 同一屏只试一次（`_structured_try` 里那个签字），真动手才清零 idle。
                if (idle >= EMPTY_ROUNDS_BEFORE_NEXT
                        or getattr(self, "_answered_but_stuck", False))                         and self._structured_try(step, page):
                    self._answered_but_stuck = False
                    idle = 0
                    self._rpt_if_moved(step, url, "结构化兜底点了东西")
                    continue
                busy_since = None

                idle += 1
                #: ★ 2026-09-26：**已经答过、但没有「下一步」可点** —— 这是最容易被误读的一种空转
                #: （屏幕上明明有选项，却说「没有可动作的东西」）。把真正的原因说出来。
                why = "没有可动作的东西（%d）" % idle
                #: ★ 2026-10-16（Task 5）**报文纪律：被挡着 ≠ 没有可动作的东西** ——
                #: 这一轮之所以一件都没做，可能正是因为有东西盖着。`cover_why` 是**这一步
                #: 动手之前**量的读数（每轮开头清零、每次动手前清零 —— Ruling 13）⇒ 走到
                #: 这一句时它还是**这一步**的值，照实点名「谁盖着」，并说清**等过 / 挪过**。
                #: 为什么非说不可（与 2026-09-18 gowizard 同一个病）：账本上写着「没有可动作的
                #: 东西」⇒ 读的人只会去查选择器，而那一刻屏幕上明明有东西、只是被盖着。
                if self.cover_why:
                    why = ("%s 一直盖着这一屏能点的东西（等过、也把它往视口正中挪过，它没走）"
                           "—— 这一下没敢点（%d）" % (self.cover_why, idle))
                if getattr(self, "_answered_but_stuck", False):
                    self._answered_but_stuck = False
                    why = ("这一屏**已经答过了**、但页面上没有认得出的「下一步」可点（%d）"
                           % idle)
                    #: ★ 2026-09-26（第三次真站试跑逼出来的）：**点过了 ≠ 页面动了** ——
                    #: `click()` 只保证「找到了那个元素、点了它」，站点忽略这一下是有可能的
                    #: （实测：同一屏上再点一次，`progress` 一动不动）。原来那一屏就此**永远**
                    #: 不再尝试（签名记着「答过了」）⇒ 空转到收摊 ✗。
                    #: ⇒ 卡住时**放行再答一次**（随机会换个选项），最多 `STUCK_RETRIES` 次；
                    #: 每次真答成功都会把 `idle` 清零，所以「一直答不动」仍会在 IDLE_LIMIT 收摊 ✓。
                    if getattr(self, "_stuck_retries", 0) < STUCK_RETRIES:
                        self._stuck_retries = getattr(self, "_stuck_retries", 0) + 1
                        self._answered_sig = ""
                        why += "｜放行再答一次（第 %d 次）" % self._stuck_retries
                self.log.info("[%s] step %d: %s | %s", self.cid, step, why, head[:50])
                #: ⚠️ 为什么这句话**两格都写**（同 `_stop` 那条 2026-10-14 的规矩）：
                #: `progress_why` 是「有没有推进」那一路读的，`note` 是「为什么没做成」那一路
                #: 读的（`selftest._verdict` 的 `step_said`）—— 只写前者，诊断会落成兜底话
                #: 「trace 里那一步没写为什么」。★ Task 5 起这一句**不再**只可能是「没有可动作
                #: 的东西」：被挡着时它点名遮挡物，读的人一眼看得出该去查谁。
                self._trace(step=step, action="none", ok=None, progress=False,
                            progress_why=why, note=why)
                if idle >= IDLE_LIMIT:
                    self.log.error("[%s] 连 %d 步没有可动作的东西（答过 %d 题）—— 如实报失败",
                                   self.cid, idle, answered)
                    self._rpt("max_steps", "stalled after %d answers" % answered)
                    self._stop(step, "none", "连 %d 步没有可动作的东西（答过 %d 题）"
                               % (idle, answered))
                    return False

            self.log.error("[%s] 走满 %d 步还没到成功 —— 如实报失败", self.cid, max_steps)
            self._rpt("max_steps")
            self._stop(max_steps, "give_up", "走满 %d 步还没到成功" % max_steps)
            return False
        except Exception as exc:                    # noqa: BLE001 —— 外面世界
            self.log.error("[%s] Error: %s", self.cid, exc)
            self._stop(0, "crash", "抛异常：%s" % str(exc)[:60])
            return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-url", required=True)
    ap.add_argument("--form-file", required=True)
    ap.add_argument("--correlation-id", required=True)
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--trace", default="")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--stop-at", type=int, default=0)
    a = ap.parse_args()
    log = setup_logger("loop")
    log.setLevel(a.log_level)
    f = Filler(a.ws_url, a.form_file, a.correlation_id, a.task_id,
               trace_path=a.trace, no_report=a.no_report, stop_at=a.stop_at)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
