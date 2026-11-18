#!/usr/bin/env python3
"""Site script for 'try' — 步进循环型：每轮读页面 → 有什么做什么 → 循环。

（由 siteforge 生成：骨架固定，只填了「怎么读 / 填什么 / 挑哪个 / 判成功」四格。）
形状照 `goldenagesouls.py`（生产在跑的那份）；诚实那条照 `docs/执行事实契约-2026-09-18.md`：
**读不到就是读不到**，绝不折算成「做完了」。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

SITE = 'try'
#: ★ 2026-09-24 第三次真站试跑逼出来的：**新窗口开出来停在 BitBrowser 控制台那一页** ✗
#: ⇒ 不导航过去，`read()` 只会一路返回 None（trace 里连着 6 行「页面读不到」✓ 它就是这么哑的）。
#: 入口网址从「这一版是从哪儿探出来的」那一格拿（同一个 key 与固定步骤表那条同源）。
#: ★ 2026-10-22（用户裁定）：**这条漏斗有两种成功** ——
#: ① 随机答到了合格分支 ⇒ `qual/post-conversion*`；
#: ② 答到不合资格 ⇒ 站方给 `survey/rejection/<原因>`（例如 `.../rejection/legal-representation`，
#:    正文「We're sorry. We can only help people who haven't had a representative…」）——
#:    这也是**一趟跑完了**（拿到了明确结论），不是卡住/空转。两种都算 exit 0。
#:
#: ★ 2026-10-23（真单 `26786002`，北京 07:58 那趟**假失败**）：合格屏有两个网址，
#:   取决于**当时客服在不在上班** —— 站方 survey layout 里 contact/info 那一屏的出口是
#:   `[to("qual/post-conversion-ah", 仅当 !isBusinessOpen), catchall("qual/post-conversion")]`；
#:   同一份 postConversion 组件按 `isBusinessOpen` 写标题：
#:   上班时「We’ll reach out **in a few minutes** about your case.」、否则「**tomorrow**」（明天也不开 ⇒「Monday」）。
#:   那趟 08:00 CST（美东 20:00，上班）落在 `post-conversion`（**没有 -ah**）⇒ 原来只写死 `-ah`
#:   与「tomorrow」两处判据全落空 ⇒ 真提交成功后空转到 IDLE_LIMIT，报成失败（截图就是成功页）。
#:   ⇒ 网址取**共同前缀**（-ah 与不带 -ah 都收），正文收三种已知变体。
SUCCESS_URL_MARKERS = [
    "https://www.atticus.com/disability-benefits/n/get-help/survey/qual/post-conversion",
    "https://www.atticus.com/disability-benefits/n/get-help/survey/rejection/",
]
SUCCESS_TEXTS = [
    "We’ll reach out in a few minutes about your case.",   # 上班时间（isBusinessOpen）
    "We’ll reach out tomorrow about your case.",           # 下班/明天开
    "We’ll reach out Monday about your case.",             # 下班且明天不开
]
FILLS = { 'age': { 'name': 'age',
           'source': 'age',
           'kind': 'value',
           'label': 'Age',
           'target': { 'text': None,
                       'label': 'Age',
                       'role': None,
                       'near': None,
                       'hint': ':Rcrlbd7rufkq:',
                       'placeholder': 'Age',
                       'nearby_text': [ '.css-1x5jdmq{font:inherit;letter-spacing',
                                        '.css-19w1uun{border-color:rgba(0, 0, 0, ',
                                        '.css-1bp1ao6{font-family:"Roboto","Helvetica","Arial",sans-serif;font-weight:400'],
                       'selectors': [ '[id=":Rcrlbd7rufkq:"]',
                                      'input[placeholder="Age"]',
                                      'input.MuiInputBase-input.MuiOutlinedInput-input'],
                       'above_fold_only': False,
                       'frame_id': ''},
           'fallback': ['58']},
  'age-2': { 'name': 'age-2',
             'source': 'age-2',
             'kind': 'value',
             'label': 'Age',
             'target': { 'text': None,
                         'label': 'Age',
                         'role': None,
                         'near': None,
                         'hint': ':Rcrlbd7rufkq:',
                         'placeholder': 'Age',
                         'nearby_text': [ '.css-1x5jdmq{font:inherit;letter-spacing',
                                          '.css-19w1uun{border-color:rgba(0, 0, 0, ',
                                          '.css-1bp1ao6{font-family:"Roboto","Helvetica","Arial",sans-serif;font-weight:400'],
                         'selectors': [ '[id=":Rcrlbd7rufkq:"]',
                                        'input[placeholder="Age"]',
                                        'input.MuiInputBase-input.MuiOutlinedInput-input'],
                         'above_fold_only': False,
                         'frame_id': ''},
             'fallback': ['58']},
  'postcode': { 'name': 'postcode',
                'source': 'postcode',
                'kind': 'value',
                'label': 'ZIP code',
                'target': { 'text': None,
                            'label': 'ZIP code',
                            'role': None,
                            'near': None,
                            'hint': 'zipcode',
                            'placeholder': 'ZIP code',
                            'nearby_text': ['ZIP code', 'ZIP code'],
                            'selectors': [ '#zipcode',
                                           'input[placeholder="ZIP code"]',
                                           'input.MuiInputBase-input.MuiOutlinedInput-input'],
                            'above_fold_only': False,
                            'frame_id': ''},
                'fallback': [{'random': 'postcode'}]},
  'first_name': { 'name': 'first_name',
                  'source': 'first_name',
                  'kind': 'value',
                  'label': 'First name',
                  'target': { 'text': None,
                              'label': 'First name',
                              'role': None,
                              'near': None,
                              'hint': ':r1:',
                              'placeholder': 'First name',
                              'nearby_text': ['First name', 'First name'],
                              'selectors': ['[id=":r1:"]', 'input[placeholder="First name"]'],
                              'above_fold_only': False,
                              'frame_id': ''},
                  'fallback': [{'random': 'first_name'}]},
  'last_name': { 'name': 'last_name',
                 'source': 'last_name',
                 'kind': 'value',
                 'label': 'Last name',
                 'target': { 'text': None,
                             'label': 'Last name',
                             'role': None,
                             'near': None,
                             'hint': ':r2:',
                             'placeholder': 'Last name',
                             'nearby_text': ['Last name', 'Last name'],
                             'selectors': ['[id=":r2:"]', 'input[placeholder="Last name"]'],
                             'above_fold_only': False,
                             'frame_id': ''},
                 'fallback': [{'random': 'last_name'}]},
  'email': { 'name': 'email',
             'source': 'email',
             'kind': 'value',
             'label': 'Email address',
             'target': { 'text': None,
                         'label': 'Email address',
                         'role': None,
                         'near': None,
                         'hint': ':r3:',
                         'placeholder': 'Email address',
                         'nearby_text': ['Email address', 'Email address'],
                         'selectors': ['[id=":r3:"]', 'input[placeholder="Email address"]'],
                         'above_fold_only': False,
                         'frame_id': ''},
             'fallback': [{'random': 'email'}]},
  'phone': { 'name': 'phone',
             'source': 'phone',
             'kind': 'value',
             'label': 'Phone number',
             'target': { 'text': None,
                         'label': 'Phone number',
                         'role': None,
                         'near': None,
                         'hint': ':r4:',
                         'placeholder': 'Phone number',
                         'nearby_text': ['Phone number', 'Phone number'],
                         'selectors': ['[id=":r4:"]', 'input[placeholder="Phone number"]'],
                         'above_fold_only': False,
                         'frame_id': ''},
             'fallback': [{'random': 'phone'}]}}
PICK = 'random'
#: ★ 2026-10-24（线上 26807011 / 26807496）：随机答到「Family member or friend」/
#: 「Patient or client」之后，联系信息屏会多出一个必填的「Relationship to applicant」下拉
#: （`relationship` / `patient-client-professional-role`）—— 我们不会填它，Next 一直是禁用态。
#: `Myself` / `I'm the applicant` 那一条只有 `:r1:`–`:r4:` 四格，是验过的路。
#: 提示是**优先**不是**只认**：别的屏上这些字不出现，照旧掷骰子。
PICK_HINTS = ['Myself', "I'm the applicant"]
MAX_STEPS = 148
IDLE_LIMIT = 60
BUSY_TIMEOUT = 90
READ_PAGE_JS = "(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}\n  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}\n  function txt(e){return ((e.textContent||'').replace(/\\s+/g,' ').trim());}\n  // ★ 2026-09-26（第五次真站试跑，`easystorebuilderpro` 那三个格）：**`id` 与 `data-testid` 不是一回事** ——\n  //   那一站三个格只有 `id`（`qv3-fn` / `qv3-em` / `qv3-ph`），而填的那一侧一律按\n  //   `[data-testid=\"…\"]` 找 ⇒ **一个都找不到**、`cdp form` 报「element not found」，\n  //   而调用方把这一下当成功了 ⇒ 表单永远是空的、提交永远被拒（真事：点三次 Next 页面一动不动）。\n  //   ⇒ 来源**打上标记**（`\\u0004` = 按 id/name 找），填/点那一侧照着用对选择器。\n  function tid(e){var t=e.getAttribute('data-testid');\n    if(t)return t;\n    var i=e.id||e.getAttribute('name');\n    var p=(e.getAttribute('placeholder')||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();\n    if(i)return (p&&/^:r[0-9a-z]+:$/i.test(i))?(\"\\u0002\"+p):(\"\\u0004\"+i);\n    return p?(\"\\u0002\"+p):'';}\n  var inputs=[];\n  deepAll('input,select,textarea').forEach(function(e){\n    if(!vis(e))return; var t=tid(e); if(!t)return;\n    inputs.push([t, (e.value||'').trim()]);});\n  var options=[], nextVisible=false, nextTxt='', nextEnabled=false;\n  // ★ 2026-09-24 第九趟真站：`a[href]` 一律不收 —— 页脚那几个「Privacy Policy」会被当成\n  //   选项，每轮挑中一个、又点不到（它们不是 button/label）⇒ 「再看一眼」空转到收摊 ✗。\n  // ★ 2026-09-26（`job-c053e0b049f3` 的真 trace）：**导航/编辑类控件也不算选项** ——\n  //   那一趟 58 次点击里就有两次点在「‹ edit previous piece」上，**倒回上一屏** ⇒\n  //   来回震荡、永远走不到提交那一步。这一类不是答案，是往回走。\n  var NAVISH=/(previous|\\bback\\b|go back|\\bedit\\b|\\u2039|\\u00ab|\\u2190)/i;\n  deepAll('button,label,[role=button]').forEach(function(e){\n    if(!vis(e))return; var s=txt(e); if(!s)return;\n    // ★ 2026-09-26（`job-c053e0b049f3` 那一站的真屏幕）：「下一步」那一颗**不叫 Next/Continue** ——\n    //   它是「INSTALL 2 MODULES →」⇒ 只认那几个英文词就永远点不到它，于是答完一题就干等 ✗。\n    //   收宽两条：① 多认几个「往前走」的词；② **以箭头收尾的按钮**（`→` / `»` / `›`）也算下一步。\n    // ★ 2026-09-26（第四次真站试跑）：**输入框自己的 `<label>` 不是选项** ——\n    //   那一趟把「\U0001F4EC Email — where your blueprint is sent」当成一题点了（trace 里那行\n    //   「答了第 14 题」就是它）✗。带 `for=` 或肚子里有输入框的 label 一律不算选项。\n    if(e.tagName==='LABEL'){var ref=e.htmlFor?document.getElementById(e.htmlFor):null;var refTxt=!!(ref&&(ref.tagName==='TEXTAREA'||ref.tagName==='SELECT'||(ref.tagName==='INPUT'&&!/^(checkbox|radio|hidden)$/i.test(ref.getAttribute('type')||''))));if(e.querySelector('input:not([type=checkbox]):not([type=radio]):not([type=hidden]),select,textarea')||refTxt)return;}\n    if(/^\\s*(next|continue|get results|submit|install|finish|done|complete)\\b/i.test(s)\n       || /[\\u2192\\u00bb\\u203a]\\s*$/.test(s)){nextVisible=true;nextTxt=s;\n      // ★ 2026-10-24（Atticus survey/claim/zip）：Next 是不是禁用也得交回去 —— 那一屏的 value 明明在（回读说填好了），可 Next 一直 disabled（站点那次 benefitsByZip 被中止），循环看不见这个事实，只会干点 8 次、如实报失败。\n      if(!(e.disabled||e.getAttribute('aria-disabled')==='true'))nextEnabled=true;\n      return;}\n    if(NAVISH.test(s))return;\n    var t=tid(e);\n    if(t) options.push(t); else options.push(\"\\u0001\"+s);});\n  var pm=(document.body.innerText.match(/\\b\\d{1,3}\\s*%/)||[])[0]; var prog=pm?pm:'';\n  var head=(document.querySelector('h1,h2,h3')||document.body).innerText||'';\n  return JSON.stringify({url:location.href, head:head.replace(/\\s+/g,' ').trim().slice(0,300),\n                         progress:prog, inputs:inputs, options:options, nextVisible:nextVisible,\n                         nextTxt:nextTxt, nextDisabled:(nextVisible&&!nextEnabled)});})()"
#: 蜜罐：这些字样命中的输入框**永远不填**（真人看不见它、机器人会填 ⇒ 整单判垃圾）。
SKIP_FIELDS = ("company", "company_url", "website", "url", "fax")
#: ★ 2026-10-29（线上 26919522 / 26919524）：`FILLS` 的键是照**页面字段名**起的
#: （`postcode` / `phone`），而任务资料（FMR `formMessage`）的键是**资料库的列名**
#: （`postal_code` / `phone_number` / `username`）。两边没有约定 ⇒ 只有恰好同名的
#: `email` 取得到，其余**静默**掉进账本示例/随机池 —— 截图里邮编永远是示例 `10001`，
#: 资料给的邮编一次都没上过页面（用户 2026-10-29：「需要取资料给的邮编」）。
#: 只加回退，不动原来的键（与固定步骤表 `agent/template.py::_ALIASES` 同源）。
PROFILE_ALIASES = {
    "phone": ("phone_number", "tel", "mobile", "phone1", "telephone"),
    "first_name": ("firstname", "first", "given_name"),
    "last_name": ("lastname", "last", "surname", "family_name"),
    "email": ("mail", "email_address"),
    "postcode": ("postal_code", "zip", "zipcode", "zip_code"),
    "city": ("town",),
    "street": ("address", "address1", "street_address"),
    "country": ("country_code",),
    "dob": ("birthday", "date_of_birth", "birthdate"),
}
#: 资料只给**一个全名**（`username: "Lin Cameron"`）时，First / Last 各取一段。
PROFILE_SPLIT_FROM = {"first_name": "username", "last_name": "username"}
#: Next 是禁用态 + 格子有值 ⇒ **同一屏最多重打几次**（站点那次查询是偶发被中止的）。
NUDGE_TRIES = 3
#: 每次重打后最多等多久看 Next 开没开（真站 `benefitsByZip` 约 1.7s 返回）。
NUDGE_WAIT_SECONDS = 5.0
#: 页面「在处理」的文案（忙 ≠ 空转；与 goldenagesouls 同一套思路）。
BUSY_MARKERS = ("almost done", "processing your", "setting up", "just a moment",
                "getting things ready", "hold on", "calculating", "please wait")
PROVENANCE = { 'generated_at': '2026-09-28T06:38:53+00:00',
  'generator': 'siteforge/v0.1',
  'env': None,
  'platform': None,
  'selftest': None,
  'source': { 'kind': 'build',
              'evidence': '填表单问卷',
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
  'arrive_url': 'https://www.atticus.com/disability-benefits/n/get-help/survey/landing/default',
  'pre_steps': [ { 'action': 'click',
                   'target': { 'text': 'See if you qualify for benefits',
                               'role': 'a',
                               'near': 'body',
                               'selectors': [ '#main > div:nth-of-type(1) > div:nth-of-type(3) '
                                              '> a:nth-of-type(1)'],
                               'above_fold_only': False,
                               'frame_id': ''}}],
  'fill_order': ['age', 'age-2', 'postcode', 'first_name', 'last_name', 'email', 'phone'],
  'examples': { 'age': '58',
                'postcode': '10001',
                'first_name': 'James',
                'last_name': 'Carter',
                'email': 'james.carter.58@example.com',
                'phone': '2125550147'},
  'shape': 'loop',
  'why': '有 3 个状态共用同一个地址「https://www.atticus.com/disability-benefits/n/get-」—— 单页漏斗/问卷'}
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
#: ★ 2026-10-22（用户：资料里没有年龄，这一格要**随机**）—— 每趟开跑时定一个值，
#: 同一次里的 Age / age-2 两格共用（别一个 58 一个 61）。资料真给了 `age` 时仍以资料为准。
EXAMPLES["age"] = EXAMPLES["age-2"] = str(random.randint(50, 64))
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

#: 连着点几次「Next」页面还是那一屏 ⇒ 当**空转**，如实收摊（第一版没有这道闸，
#: 真站上才会空点 55 次 Continue 把步数耗光 ✗）。
#: ★ 2026-10-22：**3 → 8**（Atticus 单页漏斗实测）。点完 Next 换屏要 2–3 秒，
#: 而 3 次同屏就收摊（≈4–6 秒）⇒ 页面刚推进、护栏先收（20:14 那趟：收摊后再看，
#: 已经到下一屏了）。8 次 ≈ 12–15 秒。证据：docs/probes/2026-10-21-atticus-stop/README.md。
SAME_NEXT_LIMIT = 8

#: ★ 2026-10-22（Atticus 真站）：`cdp form` 回 `ok=true` 之后**回读 value**，没留住就重填。
#: 现场：`#zipcode.value` 一直是 `""`、Next 一直禁用，而 trace 上写着「填了 zipcode」——
#: React 在 hydration / 重渲染时把早期写进去的值覆盖掉了。`form` 的回话只说明「发出去」。
#: 回读**读不到**（`None`）不算「没填上」：那是看不见，不是事实。
FILL_READBACK_TRIES = 3
FILL_READBACK_GAP = 0.4

#: 「这一屏答过了、但页面一动不动」最多再放行几次（见 ⑤ 里那段注释）。
#: 2 次够用：一次留给站点偶发忽略点击，一次留给随机换一个选项试试。
STUCK_RETRIES = 2

#: 用户 2026-09-24 拍的规则：「**根据是否可见来判断**」—— 读取器本来就只收可见的
#: (`offsetWidth>0 && offsetHeight>0` ✓) ⇒ 所以「没有可动作的东西」=「**可见的题/按钮为零**」✓。
#: 于是 Continue 降为**兜底**：连着这么多轮确实一件可见的都没有，才轮到点它 ✗
#: （不这么办 ⇒ 页面正把题画出来的那两轮里它抢先点 Continue ⇒ 22 秒就被自己的闸误杀 ✓ 真发生过）。
EMPTY_ROUNDS_BEFORE_NEXT = 2

#: 同意条上那几个「接受」长什么样（两份样本都先清它：`goldenagesouls` 的第一句就是
#: `self.cdp.click(COOKIE_BTN)` ✓ `lastingpowerofattorney` 的 `_clear_cookies` 同一条路 ✓）。
#: ⚠️ 它**异步注入** ⇒ 得上限内等它出现 ✓。
COOKIE_TEXTS = ("Accept All Cookies", "Accept All", "Accept Cookies", "I Accept", "Got it")
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

class Filler:
    def __init__(self, ws_url, form_file, correlation_id, task_id="",
                 trace_path="", no_report=False):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id
        self.log = setup_logger("loop")
        self._filled = set()
        #: 每一格**试过几次**、以及**最后给它的那个值**（见 ① 里那段：重试要重试同一个值，
        #: 不能每次换一个新的随机值 —— 换来换去站点永远收不到一份完整的表单）。
        self._field_tries = {}
        self._field_value = {}
        #: ★ 自测那两格（`selftest.ARTIFACT_FLAGS`）：少了它们，产物在自测里**一上来就 argparse
        #: 退出**（2026-09-24 实测：`unrecognized arguments: --trace … --no-report` ⇒ 报告只剩
        #: 「进程退了（退出码 2）」）。`--no-report` 是硬规矩（自测不是生产任务，不该在生产那边
        #: 留记录）；`--trace` 是自测判「每一步成没成」的读数（没有它只剩退出码）。
        self.trace_path = trace_path
        self.no_report = bool(no_report)

    def _dly(self, a=0.6, b=1.8):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step, reason=""):
        if self.no_report:
            return None                     # 自测那一遍：**永不上报**
        return report_url(self.cdp, self.tid, step, self.log, reason)

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
        """
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
        """
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
            return self._fill_and_check(token, '[data-sf-fill="1"]', value)
        if token.startswith(BY_ID):
            return self._fill_and_check(token, '[id="%s"]' % token[len(BY_ID):], value)
        return self._fill_and_check(token, '[data-testid="%s"]' % token, value)

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

    def _fill_and_check(self, token, sel, value) -> bool:
        """填 → **回读** → 没留住再填（见 `FILL_READBACK_TRIES` 那段）。

        `cdp form` 的 `ok` 只说明「发出去了」；值有没有留在页面上，只有回读说得清。
        先等 `FILL_READBACK_GAP` 再读：hydration / 重渲染把值盖掉通常就在填完那一下。
        """
        if not self._filled_ok(self.cdp.form(sel, value=value)):
            return False
        for _ in range(FILL_READBACK_TRIES):
            time.sleep(FILL_READBACK_GAP)
            got = self._read_back(token)
            if got is None or self._same_value(got, value):
                return True
            if not self._filled_ok(self.cdp.form(sel, value=value)):
                return False
        time.sleep(FILL_READBACK_GAP)
        got = self._read_back(token)
        return got is None or self._same_value(got, value)

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
              "if(token.charCodeAt(0)===4){var es=deepAll('[id=\"'+token.slice(1)+'\"]');e=es[0];}"
              "else if(token.charCodeAt(0)===2){var want=token.slice(1);"
              "var all=deepAll('input,textarea,select');"
              "for(var i=0;i<all.length;i++){"
              "var p=(all[i].getAttribute('placeholder')||'').replace(/\\s+/g,' ').trim();"
              "var al=(all[i].getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim();"
              "if(p===want||al===want){e=all[i];break;}}}"
              "else {var es2=deepAll('[data-testid=\"'+token+'\"]');e=es2[0];}"
              "if(!e)return JSON.stringify({ok:false,why:'not-found'});"
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
        """Next 是禁用态、格子又都有值 ⇒ 用**原生 setter + 全套事件**重写一遍。

        ★ 2026-10-24（Atticus `survey/claim/zip` 真站，线上 `26789962` / `26826628`）：
        填进去之后站点发了 `benefitsByZip`，可那一次**被中止**
        （performance 里 `responseStatus: 0`）—— app 没拿到回话就一直把 Next 禁着；
        而 DOM 的 value 是**留住的** ⇒ 回读说「填好了」⇒ 循环再也不碰这一格
        ⇒ 干点 8 次 Next、如实报失败。
        ★ 同日真站量证：`cdp form` 重打**不解决问题**（45 秒 Next 仍禁用）；
        原生 setter + 全套事件 1–2 秒就能把 Next 打开。
        ★ 2026-10-29（线上 26919524）：那一下是**偶发**被中止的，而旧实现
        「一屏只打一次」（`_nudged_sig`）⇒ 第一次撞上就再也没机会，只能干点 8 次 Next。
        改成**同一屏最多 `NUDGE_TRIES` 次**，每次打完最多等 5 秒看 Next 开没开。
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
            if self._set_value_events(token, value):
                did = True
                self.log.info("[%s] step: Next 是禁用态、格子有值 ⇒ 原生 setter+事件重写 %s",
                              self.cid, token)
                self._trace(step=0, action="form", ok=True, progress=None,
                            target={"testid": token},
                            note="原生 setter+全套事件（cdp form 只改 DOM，不进 React state）")
        if did:
            #: 给站点发 `benefitsByZip` 的时间（真站量到 ~1.7s；最多等 `NUDGE_WAIT_SECONDS`）。
            deadline = time.monotonic() + NUDGE_WAIT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.5)
                page2 = self.read()
                if page2 and not page2.get("nextDisabled"):
                    break
        return did


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
        hay = str(token or "").lower()
        if hay.startswith(BY_PLACEHOLDER):
            hay = hay[len(BY_PLACEHOLDER):]
        if hay.startswith(BY_ID):
            hay = hay[len(BY_ID):]
        if any(s in hay for s in SKIP_FIELDS):
            return ""                       # 蜜罐：绝不填
        p = self.form_data if isinstance(self.form_data, dict) else {}
        for name, spec in (FILLS or {}).items():
            spec = spec or {}
            kind = str(spec.get("kind") or "")
            label = str(spec.get("label") or name).lower()
            #: ★ 规则（用户 2026-09-24 拍的「做成规则」）：**字样认不出来**时，拿**账本里那一格
            #: 自己记的地址**去对（`FILLS[name].target.selectors` = 探路时真用过的那份 ✓）——
            #: 例：`e.g. RG24 8PE` 对 `postcode` 那格的 `input[placeholder="e.g. RG24 8PE"]` ✓。
            #: ⚠️ 这一条**不写任何站特有的字**（样本里那个 `"rg24" in hay` 是它那个站的补丁 ✗）
            #: ⇒ 换站照样成立 ✓ —— 这就是「做成规则」与「照抄那一家」的分界 ✓。
            blob = " ".join([label, kind] + [str(x) for x in
                                             ((spec.get("target") or {}).get("selectors") or [])]).lower()
            if ((label and label in hay) or (kind and kind in hay)
                    or (hay and hay in blob)):
                if self._is_phone(name, kind):
                    #: ★ 2026-10-22（用户：「手机号肯定要取资料」；「别取资料」只是**个别特殊
                    #: 脚本**的处理）—— 资料给了电话就用**资料那一串**；账本/池子只当兜底。
                    got = self._profile_value(p, name, kind)
                    if got:
                        return str(got)
                    return (EXAMPLES.get(name) or EXAMPLES.get(kind)
                            or self._pool(name) or self._pool(kind))
                #: ★ 2026-10-29：资料先按**原名 + 别名 + 全名拆分**找（`PROFILE_ALIASES`）。
                got = self._profile_value(p, name, kind)
                if got:
                    return str(got)
                for key in (name, kind):
                    got = EXAMPLES.get(key)
                    if got:
                        return str(got)
                #: ★ 名**先于** kind：`kind` 是「这一格填什么」那一档（这一站给的是笼统的
                #: `value`），拿它当池子的名 ⇒ `_pool('value')` 什么都没有 ⇒ **邮编永远填不上** ✗
                #: ⇒ 掉到「点页脚噪声」空转到收摊（第九趟真站的原形）。
                return self._pool(name) or self._pool(kind)
        #: ③ 按**账本顺序**兜底（见 docstring 第 ③ 档）
        if 0 <= int(index) < len(FILL_ORDER):
            name = FILL_ORDER[int(index)]
            spec = (FILLS or {}).get(name) or {}
            kind = str(spec.get("kind") or name)
            if self._is_phone(name, kind):        # 同上：资料优先；账本/池子兜底
                got = self._profile_value(p, name, kind)
                if got:
                    return str(got)
                return (EXAMPLES.get(name) or EXAMPLES.get(kind)
                        or self._pool(name) or self._pool(kind))
            got = (self._profile_value(p, name, kind)
                   or EXAMPLES.get(name) or EXAMPLES.get(kind))
            return str(got) if got else (self._pool(name) or self._pool(kind))
        return ""

    def _profile_value(self, p, *names) -> str:
        """资料里这一格给的值 —— 原名 → 别名 → 全名拆分，都没有就是空串。

        ★ 2026-10-29（线上 26919522 / 26919524）：`FILLS` 的键是照**页面**起的，
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
                return got
            for alt in PROFILE_ALIASES.get(key, ()):
                got = str(p.get(alt) or "").strip()
                if got:
                    return got
            whole = str(p.get(PROFILE_SPLIT_FROM.get(key, "")) or "").strip()
            if whole and " " in whole:
                parts = whole.split()
                return parts[0] if key == "first_name" else " ".join(parts[1:])
        return ""

    @staticmethod
    def _is_phone(name, kind) -> bool:
        """这一格是不是**电话号**（`phone` / `tel` / `mobile` 任何一种写法都算）。"""
        return any(s in ("%s %s" % (name, kind)).lower() for s in ("phone", "tel", "mobile"))

    def _pool(self, kind):
        kind = (kind or "").lower()
        if "first" in kind or "last" in kind or "name" in kind:
            return random.choice(("James", "Sarah", "David", "Emma", "Michael", "Laura"))
        if "email" in kind:
            return "user%d@%s" % (random.randint(100, 9999),
                                  random.choice(("gmail.com", "outlook.com", "yahoo.com")))
        if "phone" in kind or "mobile" in kind or "tel" in kind:
            #: ★ 2026-09-25 **用户点名**：给 **0207 开头**（伦敦座机的形状）——
            #: 真站那一格的校验收它；原来给的是 `07…`（英国手机号形状，位数一样合法），
            #: 在那一站上**过不去** ✗（填了但校验不过 ⇒ 停在那一屏）。
            #: 顺带避开 Ofcom 的影视保留段 `020 7946 0xxx`：格式合法、一眼是假的。
            while True:
                got = "0207" + "".join(str(random.randint(0, 9)) for _ in range(7))
                if not got.startswith("02079460"):
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

    def pick_option(self, options):
        """选项里挑一个：**先只认带 `data-testid` 的那些**（那是「真选项」）✓，
        一个都没有才退到「按文字」那一档。`PICK` 决定随机还是取第一个。没有 ⇒ None。

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
        #: ★ 2026-10-24（线上 26807011 / 26807496）：**先看提示**（见 PICK_HINTS 那段）。
        for want in PICK_HINTS:
            w = str(want or "").strip().lower()
            if not w:
                continue
            for o in pool:
                t = str(o)
                if t.startswith(BY_TEXT):
                    t = t[len(BY_TEXT):]
                if t.strip().lower() == w:
                    return o
        return random.choice(pool) if PICK == "random" else pool[0]

    def click(self, token):
        """点一个「选项/按钮」：有 `data-testid` 就用它；没有就**按文字打标记再点**。

        ★ 稳定性那一招（`lastingpowerofattorney.before.py` 的原话：「这些按钮**没有稳定的
        CSS 选择器**」—— 选项 id 是 `option-<epoch毫秒>`、类名一长串）⇒ 找用 JS、点还是
        `cdp click`（真人手势那一层不能省）。
        """
        token = str(token or "")
        if token.startswith(BY_TEXT):
            text = token[len(BY_TEXT):]
            payload = json.dumps(text)
            #: 扫描集**与读取器一致**（2026-09-24 真站逼出来的）：读取器把 `[role=button]` 也当
            #: 选项，这里少认一种 ⇒ 「读到了却点不到」⇒ 每轮落回「再看一眼」空转到收摊 ✗。
            js = ("(function(){function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}walk(document);return out;}var t=" + payload + ";var bs=deepAll('button,label,[role=button]');"
                  "for(var i=0;i<bs.length;i++){var e=bs[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var s=(e.textContent||'').replace(/\\s+/g,' ').trim();"
                  "if(s===t){var old=deepAll('[data-sf-pick]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-pick');}"
                  "e.setAttribute('data-sf-pick','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            self.cdp.click('[data-sf-pick="1"]')
            return True
        if token.startswith(BY_ID):
            #: 只有 `id` 的元素（见 `BY_ID` 那段）：按 `[id=…]` 点。
            self.cdp.click('[id="%s"]' % token[len(BY_ID):])
            return True
        self.cdp.click('[data-testid="%s"]' % token)
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

            answered = 0
            idle = 0
            busy_since = None
            for step in range(1, max_steps + 1):
                self._dly(0.6, 1.8)
                page = self.read()
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

                # ① 有空的可填 ⇒ 填第一个（「空了才填」⇒ 一格/两格的形态切换天然不怕）
                todo = [t for t, v in (page.get("inputs") or []) if t and not v
                        and not any(s in str(t).lower() for s in SKIP_FIELDS)]
                if todo:
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
                        if not self.fill(tid, val):
                            self.log.info("[%s] step %d: %s 没填上 ⇒ 下一轮再看", self.cid,
                                          step, tid)
                            self._trace(step=step, action="form", ok=False, progress=None,
                                        target={"testid": tid},
                                        progress_why="这一格没填上（不是「填过了」）")
                            continue             # 填不上就下一轮再看（绝不谎报填了）
                        self._field_value[tid] = val
                        self._field_tries[tid] = tries + 1
                        self._filled.add((tid, val))
                        filled_any += 1
                        self._empty = 0  # ★ 只有**真填成功**才算是「这一屏有可动的东西」
                        self.log.info("[%s] step %d: 填了 %s", self.cid, step, tid)
                        self._trace(step=step, action="form", ok=True, progress=False,
                                    target={"testid": tid}, note="填了 %s" % tid)
                    if filled_any:
                        idle = 0
                        busy_since = None
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
                answered_sig = "%s|%s" % ((page.get("head") or "")[:60],
                                          "|".join(str(o) for o in (page.get("options") or []))[:160])
                pick = self.pick_option(page.get("options"))
                if pick and answered_sig != getattr(self, "_answered_sig", ""):
                    self._empty = 0
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
                        continue
                    self.log.info("[%s] step %d: 「%s」这一下没点到（页面上没找到那段文字）⇒ 下一轮再看",
                                  self.cid, step, str(pick)[:32])
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
                    sig = answered_sig          # ← 与 ② 那把尺子同一处（别各拼一份）
                    if sig == getattr(self, "_last_next_sig", ""):
                        self._same_next = getattr(self, "_same_next", 0) + 1
                    else:
                        self._same_next = 0
                    self._last_next_sig = sig
                    if self._same_next >= SAME_NEXT_LIMIT:
                        self.log.error("[%s] 连着 %d 次点 Next、页面还是那一屏 —— 如实报失败",
                                       self.cid, self._same_next)
                        self._rpt("max_steps", "next without change")
                        self._stop(step, "click", "连着 %d 次点 Next、页面还是那一屏"
                                   % self._same_next)
                        return False
                    self.click(BY_TEXT + str(page.get("nextTxt") or "Next"))
                    idle = 0
                    busy_since = None
                    self.log.info("[%s] step %d: Next（%s）", self.cid, step,
                                  (page.get("head") or "")[:40])
                    self._trace(step=step, action="click", ok=True, progress=None,
                                progress_why="点了 Next，页面变没变由下一轮读出来",
                                target={"text": str(page.get("nextTxt") or "Next")}, note="Next")
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
                    continue
                busy_since = None

                idle += 1
                #: ★ 2026-09-26：**已经答过、但没有「下一步」可点** —— 这是最容易被误读的一种空转
                #: （屏幕上明明有选项，却说「没有可动作的东西」）。把真正的原因说出来。
                why = "没有可动作的东西（%d）" % idle
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
                self._trace(step=step, action="none", ok=None, progress=False,
                            progress_why=why)
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
    a = ap.parse_args()
    log = setup_logger("loop")
    log.setLevel(a.log_level)
    f = Filler(a.ws_url, a.form_file, a.correlation_id, a.task_id,
               trace_path=a.trace, no_report=a.no_report)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
