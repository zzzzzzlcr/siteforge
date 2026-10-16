"""第二种产物形态：**步进循环型** —— 每轮读页面 → 有什么做什么 → 循环。

## 为什么要有它（2026-09-24 用户点破「男/女 ⇒ 第二问会变」+ 仓里的两个活样本）
现在那条模板（`agent/template.py::render`）产的是「**固定步骤表**」：一串状态 + 每状态几步 +
每步一个 `when`。那一族在**单页漏斗 / 问卷**上会栽在同一个形状上 —— 它只记下**走过的那一条路**
✗，而这类站的下一问常常**取决于上一个答案** ⇒ `when` 不成立 ⇒ **整组静默跳过** ✗。
（2026-09-24 实测：`solarincentives` 那一趟从第 6 步起 **19 步全被跳过**，一趟白跑。）

## 活样本（两份都在生产跑着，这份模板逐条照它们的形状）
- `goldenagesouls.py`（14220 字节，2026-09-17 重写上线）：
  `run(max_steps=60)` + `for step in range(1, max_steps + 1)`——每轮 `read()` → 填空输入 →
  选题 → 点 Next → 分清「忙」与「真空转」→ 到顶**如实报失败**；**成功只看 URL**。
- `forms/sites/lastingpowerofattorney.before.py`：同族的稳定性三招 ——
  **按文字定位**（那些按钮没有稳定 CSS ⇒ JS 找到 → 打标记 → 再 `cdp click`）、
  **推进判据 = 等下一屏那句话**、**遮挡反复清**（点完复读确认）。
- 诚实那条来自 `docs/执行事实契约-2026-09-18.md`：`read()` **读不到必须返回 `None`** ✗
  不许当成「做完了」（`datewhirl.py:175` 连瞎三周、日志里成功 0 次，就是这么来的）。

⇒ 生成器要填的只有**四格**：`READ_PAGE_JS` / `value_for`（每格填什么）/ `pick_option`（挑哪个，
**可以随机**）/ 成功判据；骨架一个字不用动。
"""

from __future__ import annotations

import json

from agent.template import _COVER_JS, _RECENTER_JS, _json_lit, _lit, _provenance_literal

__all__ = ["render_loop", "funnel_states", "LOOP_PROVENANCE_KEEP", "DEFAULT_READ_JS",
           "LOOP_MIN_STATES", "LOOP_IDLE_LIMIT", "LOOP_MAX_STEPS", "LOOP_BUSY_TIMEOUT"]

#: 这一族也要交代出处（与 `template.render` 同一套 key —— 一条口径）。
LOOP_PROVENANCE_KEEP = ("site", "entry_url", "explored_at", "from", "note")

#: 读/点那几段 JS 共用的「**穿透 shadow root**」收集器（★ 2026-09-28，`try` 那站的问卷实测）。
#:
#: 为什么需要它：那一屏的题目控件长在 **shadow root** 里 —— `document.querySelectorAll`
#: **看不见**它们（`cdp observe` 自己报了 `shadow_roots: 1`，而读取器那一屏只读到一颗「Back」）
#: ⇒ 每轮「没有可动作的东西」⇒ 空转到收摊、如实报失败 ✗（那一趟 61 步里 60 步是这个）。
#: ⚠️ `cdp` 自己的选择器引擎**本来就穿 shadow**（`observe` 给出的选择器在 shadow 里也点得中）
#: —— 这里补齐的是「我们自己手拼的那几段 JS」这一处短板，从此两边是**同一套可见性**。
#:
#: ⚠️ **这段文本会被拼进 py 里的字符串字面量**（骨架里的 `__DEEP__` 是在 `_json_lit`
#: **之后**替进去的）⇒ 它里面**不许有** `"` 和 `\`（`tests/test_loop_template.py` 钉着这条）。
#: 默认的「读这一屏」JS：**只读，不点**。`goldenagesouls.read()` 那一套的最小版 ——
#: 它读回 `{url, head, progress, inputs:[[testid,value]], options:[…], nextVisible, nextTxt}`。
#: ⚠️ 选项如果没有 `data-testid`，就用 `"\u0001" + 文字` 交回去 —— 调用方看到这个前缀
#: 就走「按文字打标记再点」那条路（那一族按钮**没有稳定的 CSS 选择器**）。
#: ⚠️ 这里面**不许**出现 python 式 `#` 注释（`tests/test_loop_template.py` 拿 `node --check`
#: 真验它的语法 —— 2026-09-24 我自己塞过一行，那会让浏览器 eval 报错、`read()` 恒 None）。
DEEP_JS = ("function deepAll(sel){var out=[];"
           "function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});"
           "var all=root.querySelectorAll('*');"
           "for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}"
           "walk(document);return out;}")


DEFAULT_READ_JS = r"""(function(){""" + DEEP_JS + r"""
  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}
  function txt(e){return ((e.textContent||'').replace(/\s+/g,' ').trim());}
  // ★ 2026-09-26（第五次真站试跑，`easystorebuilderpro` 那三个格）：**`id` 与 `data-testid` 不是一回事** ——
  //   那一站三个格只有 `id`（`qv3-fn` / `qv3-em` / `qv3-ph`），而填的那一侧一律按
  //   `[data-testid="…"]` 找 ⇒ **一个都找不到**、`cdp form` 报「element not found」，
  //   而调用方把这一下当成功了 ⇒ 表单永远是空的、提交永远被拒（真事：点三次 Next 页面一动不动）。
  //   ⇒ 来源**打上标记**（`\u0004` = 按 id/name 找），填/点那一侧照着用对选择器。
  function tid(e){var t=e.getAttribute('data-testid');
    if(t)return t;
    var i=e.id||e.getAttribute('name');
    if(i)return "\u0004"+i;
    var p=(e.getAttribute('placeholder')||e.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
    return p?("\u0002"+p):'';}
  var inputs=[];
  deepAll('input,select,textarea').forEach(function(e){
    if(!vis(e))return; var t=tid(e); if(!t)return;
    inputs.push([t, (e.value||'').trim()]);});
  var options=[], nextVisible=false, nextTxt='';
  // ★ 2026-09-24 第九趟真站：`a[href]` 一律不收 —— 页脚那几个「Privacy Policy」会被当成
  //   选项，每轮挑中一个、又点不到（它们不是 button/label）⇒ 「再看一眼」空转到收摊 ✗。
  // ★ 2026-09-26（`job-c053e0b049f3` 的真 trace）：**导航/编辑类控件也不算选项** ——
  //   那一趟 58 次点击里就有两次点在「‹ edit previous piece」上，**倒回上一屏** ⇒
  //   来回震荡、永远走不到提交那一步。这一类不是答案，是往回走。
  var NAVISH=/(previous|\bback\b|go back|\bedit\b|\u2039|\u00ab|\u2190)/i;
  deepAll('button,label,[role=button]').forEach(function(e){
    if(!vis(e))return; var s=txt(e); if(!s)return;
    // ★ 2026-09-26（`job-c053e0b049f3` 那一站的真屏幕）：「下一步」那一颗**不叫 Next/Continue** ——
    //   它是「INSTALL 2 MODULES →」⇒ 只认那几个英文词就永远点不到它，于是答完一题就干等 ✗。
    //   收宽两条：① 多认几个「往前走」的词；② **以箭头收尾的按钮**（`→` / `»` / `›`）也算下一步。
    // ★ 2026-09-26（第四次真站试跑）：**输入框自己的 `<label>` 不是选项** ——
    //   那一趟把「📬 Email — where your blueprint is sent」当成一题点了（trace 里那行
    //   「答了第 14 题」就是它）✗。带 `for=` 或肚子里有输入框的 label 一律不算选项。
    if(e.tagName==='LABEL'&&(e.htmlFor||e.querySelector('input,select,textarea')))return;
    if(/^\s*(next|continue|get results|submit|install|finish|done|complete)\b/i.test(s)
       || /[\u2192\u00bb\u203a]\s*$/.test(s)){nextVisible=true;nextTxt=s;return;}
    if(NAVISH.test(s))return;
    var t=tid(e);
    if(t) options.push(t); else options.push("\u0001"+s);});
  var pm=(document.body.innerText.match(/\b\d{1,3}\s*%/)||[])[0]; var prog=pm?pm:'';
  var head=(document.querySelector('h1,h2,h3')||document.body).innerText||'';
  return JSON.stringify({url:location.href, head:head.replace(/\s+/g,' ').trim().slice(0,300),
                         progress:prog, inputs:inputs, options:options, nextVisible:nextVisible,
                         nextTxt:nextTxt});})()"""


def render_loop(site, success_text, success_urls, fills, provenance, *,
                read_js: str = "", pick: str = "random", max_steps: int = 60,
                idle_limit: int = 6, busy_timeout: int = 90) -> str:
    """把「怎么读这一屏、每格填什么、选项挑哪个」渲染成一条**步进循环型**的完整 py。

    `success_urls` 是**主判据**（这一族只看网址 —— 老手的原话：「正文判据是这一族栽跟头的
    老地方」，见 `lastingpowerofattorney.before.py` 的头注释）。`success_text` 给了就**加一条**
    兜底判据（URL 判据不成立、纯 SPA 不改地址时用得上），没给就不写。
    `pick`：`random`（反检测那一族）或 `first`。
    `fills`：`{字段名: {kind, label, fallback}}` —— 形状与 `template.render` **同源**。
    """
    if not str(site or "").strip():
        raise ValueError("site 不能空")
    urls = [u for u in ([success_urls] if isinstance(success_urls, str) else (success_urls or []))
            if str(u or "").strip()]
    if not urls:
        raise ValueError("成功判据不能空：这一族**只看网址** —— 没有网址判据的产物"
                         "会「跑到底再说自己成功」，那是这一套里最忌讳的那类谎")
    if str(pick) not in ("random", "first"):
        raise ValueError("pick 只能是 random 或 first（给的是 %r）" % (pick,))
    for name, spec in (fills or {}).items():
        if not isinstance(spec, dict) or not str(spec.get("kind") or "").strip():
            raise ValueError("FILLS[%r] 没给 kind —— 填什么由它决定，没它这一格填不了" % (name,))
    src = _LOOP_SKELETON
    for token, value in (("__SITE__", _lit(str(site))),
                         ("__URL_MARKERS__", _json_lit(urls)),
                         ("__TEXTS__", _json_lit([success_text]
                                                 if str(success_text or "").strip() else [])),
                         ("__FILLS__", _lit(fills or {})),
                         ("__PICK__", _lit(str(pick))),
                         ("__MAX_STEPS__", str(int(max_steps))),
                         ("__IDLE_LIMIT__", str(int(idle_limit))),
                         ("__BUSY_TIMEOUT__", str(int(busy_timeout))),
                         ("__READ_JS__", _json_lit(read_js or DEFAULT_READ_JS)),
                         ("__PROVENANCE__", _provenance_literal(provenance))):
        src = src.replace(token, value)
    #: ★ 2026-09-28：那几段自己手拼的 JS 里的「穿透 shadow root」收集器 —— 见 `DEEP_JS`。
    #: ⚠️ 这一下放在**最后**：`__READ_JS__` 那一格到这儿已经是一个 **JSON 字符串字面量**了，
    #: 替进去的是**原文**（所以 `DEEP_JS` 里不许有 `"` / `\`）。
    src = src.replace("__DEEP__", DEEP_JS)
    return src


#: 生成器产这一族时用的那几格参数：**量出来的**，不是拍的 ——
#: `idle_limit` 60 是因为真站上那个 JS 应用要 ~40 秒才成形（`IDLE_LIMIT=6` ≈ 7 秒那一版
#: 一上来就空转到收摊 ✗，2026-09-24 九趟真站试跑的账里记着）。
LOOP_IDLE_LIMIT = 60
LOOP_MAX_STEPS = 60
LOOP_BUSY_TIMEOUT = 90

#: 判「这是不是单页漏斗 / 问卷」的那把尺子：**同一个 `url_contains` 底下的状态数**。
#: 为什么是这条：问卷整段在一页上（地址不变、靠正文换题）⇒ 账本里那一串状态共用同一个
#: `url_contains`；而多页导航型每一门各占一条地址 ⇒ 最大那一组只有 1 ✓。
#: ⚠️ 天花板（`ponytail:`）：**每页一屏、地址逐页变**的问卷会被判成固定步骤表（保守那一侧，
#: 顶多多跑几趟才回过味来）；反过来「一页上就三五个状态的落地页」会被判成循环型 ——
#: 那也正好是循环型擅长的形状。要收窄就调这个阈值，别再加第二套判据。
LOOP_MIN_STATES = 3


def funnel_states(journey) -> dict:
    """账本 → 「这一趟是不是单页漏斗」+ 漏斗那一组状态。**判不出来就说判不出来**。

    返回 `{"states": [名字…], "url": <共用的那一截地址>, "why": <人话>}`；
    `states` 空的两种情形（没有带地址的页 / 最大那组不够大）都**不猜** ——
    调用方照旧产固定步骤表（那一条至少不会因为形状切换而变坏）。
    """
    whens: dict = {}
    for page in (getattr(journey, "pages", None) or []):
        name = str((page or {}).get("name") or "")
        url = str(((page or {}).get("when") or {}).get("url_contains") or "")
        if name and url:
            whens[name] = url
    if not whens:
        return {"states": [], "url": "",
                "why": "账本里没有「带地址的页」（判不出单页漏斗）"}
    groups: dict = {}
    for name, url in whens.items():
        groups.setdefault(url, []).append(name)
    url, names = max(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
    if len(names) < LOOP_MIN_STATES:
        return {"states": [], "url": url,
                "why": "最大那一组只有 %d 个状态共用地址「%s」（要 %d 个才算单页漏斗）"
                       % (len(names), url[:50], LOOP_MIN_STATES)}
    return {"states": sorted(names), "url": url,
            "why": "有 %d 个状态共用同一个地址「%s」—— 单页漏斗/问卷"
                   % (len(names), url[:50])}


#: 固定骨架。⚠️ 与 `template.render` **同一个生产契约**（`forms/common.py::CDPHelper` + `report_url`
#: + `fix.REQUIRED_CLI_FLAGS` 那四个）；**自测**那三个（`--trace` / `--delay` / `--no-report`）也必须在 ——
#: 少了它们，产物在自测里**一上来就 argparse 退出**（2026-09-24 实测：`unrecognized arguments:
#: --trace … --no-report`，那一刻报告只剩「进程退了（退出码 2）」）。
_LOOP_SKELETON = '''#!/usr/bin/env python3
"""Site script for __SITE__ — 步进循环型：每轮读页面 → 有什么做什么 → 循环。

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

SITE = __SITE__
#: ★ 2026-09-24 第三次真站试跑逼出来的：**新窗口开出来停在 BitBrowser 控制台那一页** ✗
#: ⇒ 不导航过去，`read()` 只会一路返回 None（trace 里连着 6 行「页面读不到」✓ 它就是这么哑的）。
#: 入口网址从「这一版是从哪儿探出来的」那一格拿（同一个 key 与固定步骤表那条同源）。
SUCCESS_URL_MARKERS = __URL_MARKERS__
SUCCESS_TEXTS = __TEXTS__
FILLS = __FILLS__
PICK = __PICK__
MAX_STEPS = __MAX_STEPS__
IDLE_LIMIT = __IDLE_LIMIT__
BUSY_TIMEOUT = __BUSY_TIMEOUT__
READ_PAGE_JS = __READ_JS__
#: 蜜罐：这些字样命中的输入框**永远不填**（真人看不见它、机器人会填 ⇒ 整单判垃圾）。
SKIP_FIELDS = ("company", "company_url", "website", "url", "fax")
#: 页面「在处理」的文案（忙 ≠ 空转；与 goldenagesouls 同一套思路）。
BUSY_MARKERS = ("almost done", "processing your", "setting up", "just a moment",
                "getting things ready", "hold on", "calculating", "please wait")
PROVENANCE = __PROVENANCE__
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
#: 选项没给 `data-testid` 时的前缀：`\\x01文字` = 「按这段文字去页面上找」。
BY_TEXT = "\\x01"
#: 输入框没给 `testid`/`id`/`name` 时的前缀：`\\x02placeholder` = 「按占位符去页面上找」。
#: ★ 2026-09-24 第一次真站试跑逼出来的：那一屏的邮编格**只有 placeholder**（形状同 LPA 的
#: `input[placeholder="e.g. RG24 8PE"]`）⇒ 读取器原来直接丢掉它 ⇒ 只剩 Continue ⇒ 空点 55 次。
BY_PLACEHOLDER = "\\x02"
#: 元素**只有 `id`（或 `name`）、没有 `data-testid`** 时的前缀：`\\x04id` = 「按 `[id=…]` 找」。
#: ★ 2026-09-26 第五次真站试跑逼出来的（`easystorebuilderpro` 那三个格只有 id）：
#: 不分来源就一律按 `[data-testid=…]` 找 ⇒ **一个都找不到**，而填的那一步照样报成功 ✗✗。
BY_ID = "\\x04"

#: 连着点几次「Next」页面还是那一屏 ⇒ 当**空转**，如实收摊（第一版没有这道闸，
#: 真站上才会空点 55 次 Continue 把步数耗光 ✗）。
SAME_NEXT_LIMIT = 3

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
COOKIE_GONE_JS = r"""(function(){__DEEP__
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
                 trace_path="", no_report=False, stop_at=0):
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
        #: ★ 2026-09-30：`--stop-at`（见 `run()` 里那一格 —— 这一族是「跑到第 N 步就停」）。
        self.stop_at = int(stop_at or 0)
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
                f.write(json.dumps(row, ensure_ascii=False) + "\\n")
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
        """填一格：按 token 的**来源**用对选择器 —— `data-testid` / `\x04`=id/name /
        `\x02`=placeholder（打标记再填）。**填没填上要看回话**，不是「发出去就算填了」。

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
            js = ("(function(){__DEEP__var t=" + json.dumps(want) + ";"
                  "var es=deepAll('input,textarea,select');"
                  "for(var i=0;i<es.length;i++){var e=es[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var p=(e.getAttribute('placeholder')||'').replace(/\\\\s+/g,' ').trim();"
                  "var al=(e.getAttribute('aria-label')||'').replace(/\\\\s+/g,' ').trim();"
                  "if(p===t||al===t){var old=deepAll('[data-sf-fill]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-fill');}"
                  "e.setAttribute('data-sf-fill','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            return self._filled_ok(self.cdp.form('[data-sf-fill="1"]', value=value))
        if token.startswith(BY_ID):
            return self._filled_ok(self.cdp.form('[id="%s"]' % token[len(BY_ID):], value=value))
        return self._filled_ok(self.cdp.form('[data-testid="%s"]' % token, value=value))

    @staticmethod
    def _filled_ok(out) -> bool:
        """`cdp form` 说填上了没有 —— **只看它自己那句话**（报错就不算填上）。

        ⚠️ 为什么必须有这一道（2026-09-26）：`common.CDPHelper.form` **不抛异常**，
        它把 cdp 的 stderr 一起交回来；调用方不看就变成「接上了但不响」——
        今天那一趟就是照这样在 trace 里写了三次「填了 qv3-fn」，而格子里一个字都没有 ✗。
        """
        said = str(out or "")
        return "Error" not in said and "element not found" not in said


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
            #: 例：`\x02e.g. RG24 8PE` 对 `postcode` 那格的 `input[placeholder="e.g. RG24 8PE"]` ✓。
            #: ⚠️ 这一条**不写任何站特有的字**（样本里那个 `"rg24" in hay` 是它那个站的补丁 ✗）
            #: ⇒ 换站照样成立 ✓ —— 这就是「做成规则」与「照抄那一家」的分界 ✓。
            blob = " ".join([label, kind] + [str(x) for x in
                                             ((spec.get("target") or {}).get("selectors") or [])]).lower()
            if ((label and label in hay) or (kind and kind in hay)
                    or (hay and hay in blob)):
                if self._is_phone(name, kind):
                    #: ★ 2026-09-26 用户点名（solarincentives 那一站）：「**手机号别取资料**」——
                    #: 那一格永远是**现编的号码**：资料里那份是别人填过的真实号，同一串在站上
                    #: 反复出现迟早被认出来。账本里那一趟真用过的值还能用（那是**这一站**收过的
                    #: 形状，比如美国号），再不然就按池子现编。
                    return (EXAMPLES.get(name) or EXAMPLES.get(kind)
                            or self._pool(name) or self._pool(kind))
                for key in (name, kind):
                    got = p.get(key) or EXAMPLES.get(key)
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
            if self._is_phone(name, kind):        # 同上：号码那一格**不取资料**
                return (EXAMPLES.get(name) or EXAMPLES.get(kind)
                        or self._pool(name) or self._pool(kind))
            got = p.get(name) or p.get(kind) or EXAMPLES.get(name) or EXAMPLES.get(kind)
            return str(got) if got else (self._pool(name) or self._pool(kind))
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
            #: ★ 2026-09-25 **用户点名**：给**座机**形状（原来给 `07…` 手机 —— 位数一样合法，
            #: 在那一站上**过不去** ✗：填了但校验不过 ⇒ 停在那一屏）。
            #: ★ 2026-10-08（用户点名「**不是**都是 `0207` 啊」）：号段**别锁死一个** ——
            #: 每站每趟都是伦敦 `0207`，那本身就是个可疑的常量。口径与 `template.py` 那边
            #: 同一张表（`_GB_AREA_CODES` / `_GB_DRAMA_PREFIXES`），顺带避开 Ofcom 的
            #: **影视保留段**（格式合法、一眼是假的）。
            while True:
                got = (random.choice(("0203", "0207", "0208", "0113", "0114", "0115",
                                      "0116", "0117", "0118", "0121", "0131", "0141",
                                      "0151", "0161", "0191"))
                       + "".join(str(random.randint(0, 9)) for _ in range(7)))
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
            js = ("(function(){__DEEP__var t=" + payload + ";var bs=deepAll('button,label,[role=button]');"
                  "for(var i=0;i<bs.length;i++){var e=bs[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var s=(e.textContent||'').replace(/\\\\s+/g,' ').trim();"
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
                        if not val or (tid, val) in self._filled:
                            continue
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
                        or getattr(self, "_answered_but_stuck", False)) \
                        and self._structured_try(step, page):
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
    ap.add_argument("--stop-at", type=int, default=0)
    a = ap.parse_args()
    log = setup_logger("loop")
    log.setLevel(a.log_level)
    f = Filler(a.ws_url, a.form_file, a.correlation_id, a.task_id,
               trace_path=a.trace, no_report=a.no_report, stop_at=a.stop_at)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
'''
