"""Task 3：py 产物骨架 —— **模板固定，agent 只填「怎么走」**（规格 §5.1）。

## 这个文件存在的理由

产的 py 必须能**直接扔进** `/opt/skills/auto-farm-skill/forms/sites/` 被 `ad-task.py` 跑。
所以骨架里那些东西**不能由模型即兴发挥**：CLI 契约、`from common import` 那一行、
`sys.exit(0 if f.run() else 1)`、trace 的格式 —— 一处写歪，产出的脚本在生产里就是**跑 0 次**
（或者更坏：exit 0 谎报成功）。这些都在下面这份骨架里钉死，模型只填 STATES / FILLS。

骨架逐字对齐生产已有的产物，比对过的文件：
`forms/sites/ace.py` · `forms/sites/blinkist.py` · `forms/sites/compareinsulation.py`
（2026-09-16 复核：这三个文件的 CLI 段与 `sys.exit` 段**一字不差**）。

## 产出的东西长什么样

    template.render(site, success_text, states, fills, provenance) -> str

- `site`         站点短名（logger 名、docstring）
- `success_text` 成功判据：一段文字或一段文字列表（页面上出现任意一段即成功）
- `states`       「怎么走」：状态列表，每项 `{name, when, steps}`；
                 `when` 是**进这个状态时的页面判据**（`url_contains` / `text_contains`），
                 不匹配就整组跳过（防 A/B 变体、防步骤增减 —— blinkist 那种站每轮都不一样）
- `fills`        字段值从哪来：`{name: {source, kind, label, target, fallback}}`
- `provenance`   环境指纹（规格 §5.3）。缺的键**补 None**，不编内容

产物自带三件生产要的东西（规格 §13）：

1. **动作全走 cdp**（`self.cdp.click/form/scroll`），`eval` 只用来读（§5.2）
2. **早停**（`STUCK_LIMIT`）—— 生产 JSON 执行器的早停有**已知未修**的 bug
   （`form_executor/json_executor.py:373` 的 `bool(_cur_tab)` 恒真 → `_consec_fail` 每步清零
   → 失败任务必磨完全程），所以这条必须产物自带
3. **重跑便宜**：不调 LLM、不截图、不 observe、不落 trace —— 除非显式给 `--trace` / `--stop-at`

外加一条 2026-09-17 补的（评审 Critical 1）：

4. **说得出自己缺什么**。生产那个 cdp 是个**老版本**：`--help` 里没有 `observe`、
   没有 `diff`、没有 `screenshot`。缺了不会让重跑跑不动，但会让回退链的最后一跳
   （重新 observe）与 trace 的「有没有推进」**静默**地做不成 —— 而静默正是本项目
   最贵的失败（「判不出」被读成「没推进」）。所以产物把缺口**说成人话**（每条一次），
   并在 trace 里让 `null` 带上原因；运维设 `SITEFORGE_CDP_BIN` 指到新版即可，
   **产物本身不用改**。

## 谁消费它

- Task 4 的 lint：跑 `fixtures/reference_site.py`（本模板渲染出的参考产物）必须零违规
- Task 6 的扰动自测：把它 `--trace` 出来的 JSON Lines 读成「卡在第几步」
- Task 7/8：图里 `draft` 节点调 `render()`，`deliver` 落盘

⚠️ **测试里有一条「真 import」的钉子**（`tests/test_template.py`）：
计划一的 `py_emitter` 三个断口里，第一个就是「JSON 字面量发到了 Python 的位置」——
渲染出的源码里出现 `false` / `null`，**`ast.parse` 照样通过**（语法合法），
要到 `import` 才炸 `NameError: name 'false' is not defined`。
所以本模板一律用 `repr` 系渲染数据（`_lit`），且测试会把渲染产物写进临时文件**真 import 一次**。
"""

from __future__ import annotations

import pprint
from string import Template

__all__ = ["render", "PROVENANCE_KEYS"]

#: 规格 §5.3 的元数据键。缺的补 None（「不知道」），**不编内容**。
PROVENANCE_KEYS = ("generated_at", "generator", "env", "platform", "selftest", "source")

#: 产出的 py 一律把这个常量当默认名（诊断/日志里认它）
DEFAULT_STUCK_LIMIT = 3


def _lit(obj) -> str:
    """渲染成 **Python 字面量** —— 不是 JSON。

    `pprint.pformat` 出的是 `True` / `False` / `None`，而 `json.dumps` 出的是
    `true` / `false` / `null`。后者在 Python 里语法合法（所以 `ast.parse` 抓不到），
    import 时才 `NameError` —— 计划一 py_emitter 的断口 1 就是这个。
    """
    return pprint.pformat(obj, width=96, indent=2, sort_dicts=False)


def _success_texts(success_text) -> list:
    if isinstance(success_text, str):
        texts = [success_text] if success_text.strip() else []
    else:
        texts = [t for t in (success_text or []) if isinstance(t, str) and t.strip()]
    if not texts:
        # 没有成功判据的产物会「跑到底然后说成功」—— 本项目最忌讳的那类谎
        raise ValueError("success_text 不能空：没有成功判据的产物会跑到底再说自己成功")
    return texts


def _fills_map(fills) -> dict:
    """`fills` 收 list（带 name）或 dict，产物里一律是 dict（按名字查）。"""
    if isinstance(fills, dict):
        out = dict(fills)
    else:
        out = {}
        for fill in fills or []:
            name = (fill or {}).get("name")
            if not name:
                raise ValueError(f"FILLS 里有一项没写 name：{fill!r}")
            out[name] = fill
    for name, fill in out.items():
        if not isinstance(fill, dict) or not (fill.get("target") or {}).get("selectors"):
            raise ValueError(f"FILLS[{name!r}] 没给 target.selectors —— 多元声明至少要有选择器")
    return out


def _provenance_literal(provenance) -> str:
    prov = {key: (provenance or {}).get(key) for key in PROVENANCE_KEYS}
    for key, value in (provenance or {}).items():
        if key not in prov:
            prov[key] = value
    return _lit(prov)


def render(site, success_text, states, fills, provenance) -> str:
    """把「怎么走」渲染成一条完整的 py 源码（文件级，可直接落盘进 `forms/sites/`）。"""
    if not site or not str(site).strip():
        raise ValueError("site 不能空")
    site = str(site).strip()
    if not states:
        raise ValueError("states 不能空：没有「怎么走」的产物跑起来只会站在那儿")
    summary = "siteforge 从真页面探索出来的重放脚本：按 STATES 走一遍，见到成功文案就算成功。"
    return SKELETON.substitute(
        site=site,
        summary=summary,
        success_texts=_lit(_success_texts(success_text)),
        states=_lit(list(states)),
        fills=_lit(_fills_map(fills)),
        provenance=_provenance_literal(provenance),
        stuck_limit=DEFAULT_STUCK_LIMIT,
    )


# ⚠️ 骨架里的 $ 只有 `render()` 填的那七个（site / summary / success_texts / states /
#    fills / provenance / stuck_limit）—— `string.Template` 会把任何别的 $ 也当占位符。
#    其余部分请当作「生成出来的源码」读：注释是给将来读产物的人看的（D16：人话）。
SKELETON = Template(r'''#!/usr/bin/env python3
"""$site —— siteforge 产出的站点脚本。

$summary

骨架固定（agent 只填 STATES / FILLS），调试契约见规格 §5.1c：

  --trace <file>  每步往 <file> 追一行 JSON：这一页长什么样、点没点到、有没有推进、
                  失败那步的截图。note 是写给人看的一句话（不是错误码、不是选择器）。
                  `progress` 判不出来时是 null，**旁边 `progress_why` 说明为什么**
                  （例如「这个 cdp 不会 diff」）—— 观测故障不许被读成策略结论；
                  截图没落成时 `shots_why` 同样说明原因；`frame_id` 是**这一步真在哪一帧
                  里做的**（空串 = 主帧），元素在跨源 iframe 里时它就是那条 `--frame-id`，
                  「帧内的动作有没有带帧号」读这一行就能看见。
  --stop-at <N>   跑完第 N 步就停，**浏览器保持原状不关**（谁开的谁关）
  --shots all     每步都截图。默认只在**没做成**、以及**判得出没推进**的那几步落
                  （点/导航那类才有推进可判；填/选没有通用判据，不算它「没推进」）
  --delay <秒>    每步之后**固定**停这么久（秒），覆盖默认的拟人随机停顿（0.4–1.6s）。
                  扰动自测的第 3 遍用它放大时序（「填完立刻点」这类竞争，慢下来才看得见）。
                  不给（或给 ≤0）= 基线那套随机停顿；生产重跑别给（白等，不是扰）
  --no-report     这一次跑**不上报**（不往生产的 URL 记录接口写）。给自测/调试用：
                  自测也是拿真浏览器跑真站，但它不是生产任务，不该在生产那边留下记录。
                  生产重跑**不许**给（运维正是靠那些 URL 记录看任务走到哪了）

两个调试参数都不给 = **生产重跑路径**，与 forms/sites/ 下的手写脚本行为一致：
不截图、不 observe、不落 trace、不调任何模型（规格 §13：重跑必须便宜）。
"""

import argparse, base64, json, os, random, re, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url


SITE = "$site"
# 成功判据：页面上出现其中任意一段文字就算走通了（人话，不是选择器）
SUCCESS_TEXTS = $success_texts
# 早停：连续这么多步没做成，收摊（规格 §13 —— 重跑不许磨完全程）。
# 生产 JSON 执行器的早停有**已知未修**的 bug（form_executor/json_executor.py:373 的
# bool(_cur_tab) 恒真 → 连续失败计数每步被清零 → 失败任务必磨完全程），
# 所以这条必须产物**自带**，不指望外面兜。
STUCK_LIMIT = $stuck_limit
# trace 里落的页面签名长度：与 cdp observe 的 page_text 同口径（§4.3「归一化后前 600 字」）。
PAGE_TEXT_CHARS = 600
#: trace 里**另外**留的尾部长度（`page_sig_tail`）—— 子帧的正文常常落在前 600 字之后，
#: 不留尾就「搜不到 ≠ 没读到」分不开（2026-09-17 第十轮补的，**加法**）。
PAGE_TAIL_CHARS = 240

# 动作命令自己报错时的字样：cdp click / form 找不到元素就直接报这些。
# 「这一步成没成」在**填/选**类步骤上只能看它 —— diff 判不了控件状态（§4.6 的能力边界）。
ERR_MARKERS = ("Error:", "error:", "not found", "BugError", "no page target",
               "failed to create client", "Usage:", "Flags:", "panic:")

# 这几类动作**可以**拿 diff 的 actionable 当判据（导航与组成会变）；
# 填/勾/选没有通用判据，别拿 diff 判它们（§4.6）。
DIFF_JUDGES = ("click", "goto")

# observe 用这个值说「元素只是不在当前视口里」—— **不是**被谁挡住（滚动就看得见）。
# 判「能不能对它动手」时要把它与真遮挡（被横幅盖住那类）分开，见 _usable。
OFFSCREEN = "offscreen"

# observe 用 `frame_path` 里这个名字表示**主帧**（规格 §4.3：`""` → `["main"]`）。
# ⚠️ 它是给人看的标记，**不是**能回传给 cdp 的 frameID —— 所以要把它翻成 `""`，
# 见 _frame_of_element。target 里的 `frame_id`：空串 = 主帧，非空 = 那一帧的 CDP frameID。
FRAME_MAIN = "main"

#: 「去找活帧」那次 observe 至少隔这么久才再探一次（秒）—— 它是**读页面**这条路的
#: 前置，而读页面每步都要发生；不节流就会在「这一页没有帧」时反复起进程。
LIVE_PROBE_EVERY = 3.0

#: `goto` 之后最多等多久（秒）让这一页加载到 `readyState=complete`。
#: 等不到也照旧往下走（一句人话说明），**不许**把它当成失败。
WAIT_READY_SECONDS = 10.0

#: 目标**被别的东西盖着**时，最多等它多久（秒）—— 等的是**加载蒙版**。
#:
#: 为什么是「先等」而不是「一看盖着就不点」（2026-09-18 真站实测 + 用户原话
#: 「反正在人的视角来看他不就是加载蒙版吗？没加载完而已」）：
#: 盖着目标的那层，十有八九是**页面还没加载完**，它自己会走。
#: 一看盖着就判「这一步不做」，等于把「还没好」当成「不能做」——
#: 与 `_applies` 那个病**同一个**（等不到 ≠ 判据不成立）。同一个坑今天掉两次。
#:
#: 但**等到头了它还不走，就真的不点**：那时它已经不是「还没好」，是「挡住了」，
#: 点下去就是点到它身上。这一格由 `_covered_by` 兜住，**不许**被记成做成了。
#:
#: ⚠️ 这笔预算**按步共享**（同一步里所有候选选择器 + 重找那条路合起来这么多）——
#: 不然每个被盖住的候选各赔一份，十来步会慢到没法用。
#:
#: ⚠️ **35.0 是两趟真站量出来的，不是拍的**（2026-09-18）：
#: 第 1 趟：8 秒时还在，而 10 秒后那一步点成功时遮挡已经没了（⇒ 至少 >8）；
#: 第 2 趟：**20 秒时还在**（`等了 20.0 秒 … 还盖着`），到 32 秒后那一步才没有遮挡。
#: ⇒ 这层蒙版的寿命约 **25–35 秒**。8.0 / 20.0 都定短了，两趟都白赔一次没等着。
#: 定 35 是**贴着量出来的上界**：再长就是替站点猜了，而这个数每步最多赔一次。
COVER_WAIT_SECONDS = 35.0

#: 等蒙版期间每隔多久再量一次（秒）
COVER_POLL_SECONDS = 0.4

#: **动手之前**最多等多久（秒）等「整页加载完」（`_page_ready`）。
#:
#: 为什么要这一格（2026-09-18 真站对照，这是它的全部理由）：
#: 那次成功的生产单（`26005787`）第 4 步的遮挡物是 `iframe#mvfFormWidget-…`
#: —— **表单 iframe 本人**，= 表单已就位；而今天 5 趟全挂，遮挡物是
#: `div.js-chameleon-page-loader` —— **加载蒙版**，= 表单没加载完，
#: **而产物照样往下走**，后面 20 步全错。
#:
#: 与 `COVER_WAIT_SECONDS` 是**两个问题**，别合并：
#:   `_covered_by` 问「**我要点的那个东西**被盖着吗」（元素级）；
#:   `_page_ready` 问「**整页**好了吗」（页面级）。
#: 今天就是「元素没被压住」而「整页没好」—— 只看元素级就会漏掉。
PAGE_READY_SECONDS = 60.0

#: 等整页就绪期间每隔多久再量一次（秒）
PAGE_READY_POLL = 0.5

#: 「**整页还在加载吗**」的探针（读，不动页面）。返回盖着整页的那个加载物的
#: 选择器，没有则空串。
#:
#: 判据三条**都要**（宁可漏，不可误判 —— 判错的代价是整趟不做事）：
#:   ① 名字里带 loader / loading / spinner / preloader 那一族；
#:   ② **真的盖在视口中央**（`elementFromPoint` 命中的就是它或它的后代）——
#:      一个藏在角落里的 loading 图标不是「整页在加载」；
#:   ③ **盖得够大**（至少视口面积的四分之一）—— 小转圈不算。
#: ⚠️ 名字这一条是启发式。所以配了 ②③ 两道：**光看名字会把页面上任何一个
#: 叫 `*loading*` 的小控件读成「整页没好」，那样产物就什么都不做了。**
_PAGE_LOADER_JS = (
    "var pageLoaderRe=/(page-?loader|preloader|loading|spinner|skeleton)/i;"
    "var all=document.getElementsByTagName('*');"
    "var vw=window.innerWidth||0, vh=window.innerHeight||0;"
    "if(vw<=0||vh<=0) return '';"
    "var cx=vw/2, cy=vh/2;"
    "var top=document.elementFromPoint(cx,cy);"
    "if(!top) return '';"
    "for(var i=0;i<all.length;i++){"
    "  var e=all[i];"
    "  var lab=String(e.id||'')+' '+String(e.className||'');"
    "  if(!pageLoaderRe.test(lab)) continue;"
    "  if(!(e===top||e.contains(top)||top.contains(e))) continue;"
    "  var r=e.getBoundingClientRect();"
    "  if(!(r.width>0&&r.height>0)) continue;"
    "  if(r.width*r.height < (vw*vh)*0.25) continue;"
    "  var st=window.getComputedStyle(e);"
    "  if(!st) continue;"
    "  if(st.visibility==='hidden'||st.display==='none') continue;"
    "  if(parseFloat(st.opacity||'1')===0) continue;"
    "  return 'LOADER|'+e.tagName.toLowerCase()+(e.id?('#'+e.id):'')"
    "+'.'+String(e.className||'').split(' ')[0];"
    "}"
    "return '';"
)

#: 「整页在加载」答复的**前缀**。`_page_ready` **只认带它的答复**。
#:
#: ⚠️ 这条规矩是抄 `_covered_by` 的，而且**是被测试逼出来的**（2026-09-18）：
#: 我第一版探针**没要前缀** —— 于是在一个「对任何 eval 都回一句页面文字」的替身 cdp 下，
#: 那句话被读成「盖着整页的加载物」，**每步干等 60 秒**（全量套件 48 秒 → 293 秒，
#: 一条 selftest 用例当场红）。**这正是本项目那条老病**：
#: 「量不出来」（答复形状不认识）被读成「量出来了，而且是那个坏结果」。
#: 与本文件里 `COVER|` 那条是**同一个病、同一道闸**。
LOADER_PREFIX = "LOADER|"

#: `observe` 给 cookie 同意类遮挡物记的 kind（`internal/observe.go`：按
#: 文字/ id / class 里有没有 cookie|consent|gdpr|privacy 判的）。**只点这一类** ——
#: 一般的浮层（`overlay`）不去动它：那可能是页面自己要人看的东西，点掉它同样是「点到别的东西」。
COOKIE_KIND = "cookie-banner"

#: 找同意弹层那段 JS（**读**，不动页面；§5.2 允许 eval 读）。返回 `"<选择器>|<按钮文字>"`，
#: 找不到返回空串。判据与 `cdp observe` 的 obstructions 是**同一套词汇**
#: （文字 / id / class 里有没有 cookie|consent|gdpr|privacy），只是这里自己做一遍 ——
#: 原因见 `_clear_obstructions` 的 docstring（生产那个 cdp 没有 observe）。
#:
#: 只认**有把握**的那一下：可见的同意类容器 + 带稳定 id 的按钮 + 文字属于
#: 「接受/同意/关闭/拒绝」那一族。**找不到就什么都不做**（宁可不做，也不猜一条会点错的选择器）——
#: 「点到弹层上」由 `_covered_by` 兜住，不会被记成做成了。
#:
#: ── 「页面上现在还有没有同意弹层」────────────────────────────
#:
#: 与上面那段是**两个问题**，所以是两段 JS（不是把上面那段改一改）：
#:   上面那段问「我该点哪个按钮」—— 要点得着才算数，所以它要求**按钮带稳定 id**；
#:   这一段问「弹层还在不在」—— 只是**判据**，所以它连按钮都不用找。
#: 用上面那段来回答下面这个问题是**错的**：弹层在、而按钮没 id 时它会返回空串，
#: 于是「弹层还在」被读成「弹层不在」。
_CONSENT_BOX_JS = (
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    "var vis=function(e){var r=e.getBoundingClientRect();"
    "return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;};"
    "var all=document.getElementsByTagName('*');"
    "for(var i=0;i<all.length;i++){"
    "  var b=all[i];"
    "  var label=String(b.id||'')+' '+String(b.className||'')"
    "+' '+String(b.getAttribute&&b.getAttribute('aria-label')||'');"
    "  if(boxW.test(label)&&vis(b)) return '1';"
    "}"
    "return '';"
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


_CONSENT_PROBE_JS = (
    # ⚠️ 用 `getElementsByTagName('*')` + 属性自检，**不走**「按选择器一步查」的那种写法 ——
    # 产品里凡是要按选择器找元素的地方一律避开它（`tests/test_template.py` 的
    # 「动作全走 cdp」那条把它钉死了；而这里本来就只是**读**，`matches()` 一样能读）。
    "var boxW=/(cookie|consent|gdpr|privacy)/i;"
    "var btnW=/(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;"
    "var vis=function(e){var r=e.getBoundingClientRect();"
    "return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;};"
    "var all=document.getElementsByTagName('*');"
    "for(var i=0;i<all.length;i++){"
    "  var box=all[i];"
    "  var label=String(box.id||'')+' '+String(box.className||'')"
    "+' '+String(box.getAttribute&&box.getAttribute('aria-label')||'');"
    "  if(!boxW.test(label)||!vis(box)) continue;"
    "  var inner=box.getElementsByTagName('*');"
    "  for(var j=0;j<inner.length;j++){"
    "    var b=inner[j], tag=String(b.tagName||'').toLowerCase();"
    "    if(tag!=='button'&&tag!=='a'&&tag!=='input') continue;"
    "    var t=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "    if(!btnW.test(t)) continue;"
    # 没有稳定地址就不点（宁可不做，也不猜一条会点错的选择器）
    "    if(!b.id) continue;"
    "    return '#'+b.id+'|'+t.slice(0,40);"
    "  }"
    "}"
    "return '';"
)

# ── 「这个 cdp 会不会做这件事」────────────────────────────
# 生产那个 cdp 是**老版本**：`--help` 里只有 active/click/close/completion/eval/form/
# help/navi/scroll/snapshot/targets —— **没有** observe / diff / screenshot
# （2026-09-17 实测）。产物要用到这三条：observe 是回退链的最后一跳（生产路径），
# diff 与 screenshot 在 trace 里。缺了不会让重跑跑不动，但会**静默**地少三样东西 ——
# 而静默正是本项目最贵的失败：「没算出有没有推进」被读成「没推进」，
# 「没截图」被读成「这步没问题」。所以缺哪条说哪条（人话 + 怎么办），每条只说一次。
# 运维不用改产物就能指到带它们的新版：设 SITEFORGE_CDP_BIN。

#: 命令 → （人话里它叫什么，缺了会怎样）
MISSING_CDP = {
    "observe": ("重新看一遍页面", "声明里的找法失效时没法换个找法再试，这一步只能算没做成"),
    "diff": ("比一比页面变没变", "这一步有没有推动页面判不出来（如实记 null，不记「没推进」）"),
    "screenshot": ("截图", "这次调试没留下截图"),
}

#: cobra 对不认识的子命令是这么答的（一行，在 **stderr** 上，退出码 1）
COMMAND_UNKNOWN = "unknown command"

# common.py 所在的那一层（生产：/opt/skills/auto-farm-skill/forms）
FORMS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# cdp CLI 在它的**上一层**（生产：/opt/skills/auto-farm-skill/cdp，与 common.py 里
# 写死的 CDP_PATH 同一个位置）。回退链的最后一跳（重新 observe）与 goto 都要用它 ——
# 它们不是调试功能，是生产路径。
#
# 默认路径**一个字都不改**（生产就是这么摆的）。要指到别处的 cdp（比如带 observe /
# diff / screenshot 的新版）就设 SITEFORGE_CDP_BIN —— 运维改环境变量，不用改产物。
CDP_BIN = os.environ.get("SITEFORGE_CDP_BIN") or os.path.join(os.path.dirname(FORMS_DIR), "cdp")

# 拟人：动作之间的随机停顿（秒）
DELAY_RANGE = (0.4, 1.6)
# 数据随机化的池子 —— 按站点所在国家调这一小段（每次重跑同一份数据是明显的机器味）
FIRST_NAMES = ["James", "John", "Robert", "Michael", "David", "Alex", "Chris", "Sam"]
LAST_NAMES = ["Smith", "Jones", "Williams", "Taylor", "Brown", "Johnson", "Davies", "Wilson"]
EMAIL_DOMAINS = ["outlook.com", "gmail.com", "yahoo.com", "hotmail.com"]
POSTCODES = ["SW1A 1AA", "NN3 3AQ", "M1 1AA", "B1 1AA", "LS1 1AA", "G1 1AA"]
# 「州」这一类**生产脚本本来就是一个小池子 + 随机选**（不是从 form-file 取）——
# 照抄 `forms/sites/lifynest.py:16` 的 STATES（同一套「资料逻辑」，站点在美国时适用）。
US_STATES = ["California", "Texas", "Arizona", "Florida", "New York", "Illinois", "Ohio"]
PHONES = ["07936567874", "07700900123", "07400123456", "07911123456"]

# ── 产出元数据（siteforge 自动写入，勿手工编辑）────────────
PROVENANCE = $provenance

# ── 怎么走 ────────────────────────────────────────────────
# 每个 target 都是**声明式多元描述**（§5.1b），不写死单个选择器。运行时的回退链：
#
#   selectors[0] 失败 → selectors[1] 失败 → **重新 observe 当前页面**，
#   按 text + role + near(区域) 重新找一个 → 再失败才算这一步失败
#
# 为什么不能只靠选择器：站上一改版 class 就变，而 text + role + 语境比 CSS 路径稳。
# states[].when 是**进这个状态时**的页面判据，不匹配就整组跳过 —— 有的站每轮步骤都不一样。
#
# target 里的 `above_fold_only`：写了 true 才是「**必须**首屏看得见」；没写 / false
# 表示折线下的候选也能用（重新 observe 找到的候选里，`above_fold: false` 或
# `occluded_by: "offscreen"` 只是「不在视口里」，滚动一下就看得见，不是被挡住）。
#
# target 里的 `frame_id`：这一步的元素**在哪一帧**里（空串 = 主帧，非空 = 那一帧的
# CDP frameID，交给 `cdp --frame-id`）。**它跟 selectors 是一体的两半** —— 同一个
# 选择器在主帧与子帧里可以是两个不同的元素，只搬选择器不搬帧，重放就会去主帧里找一个
# 根本不存在的元素（真站实测：跨源 iframe 里的控件主帧命中 0、帧内命中 1，
# 每一遍都「页面上没找到」，而账本里原先没有这个键，看不出是这个原因）。
# 老产物 / 主帧里的元素没有这个键，按空串处理。
#
# 动作只有这五种（别的会在运行时被当成「产物写错了」）：click / form / scroll /
# goto（直接导航，走 cdp navi —— 写路径不许用 eval）/ wait。
STATES = $states

# 字段值从哪来：先读 --form-file 里的键（source），没有就用 fallback 里的一个随机值。
# kind 决定调 cdp form 的哪个模式：value（打字）/ check（勾选）/ select（下拉）。
FILLS = $fills


def _norm(text):
    r"""把一段文字归一成「看内容」的形态：空白（含 NBSP）压成单个空格、两端剪掉。

    ⚠️ 这条口径**必须**与 `cdp observe` 的 page_text 完全一致（规格 §4.3 明写
    「与 py 里 page_signature() 同口径」）。不一致的后果是：trace 里的 page_sig 与
    agent 当时看到的页面模型对不上，Console 会把**同一个页面**显示成两个页面。
    Python 的 \s 与 JS 的 \s 都含 NBSP（Go 的不含 —— diff.go 那个坑就是它）。
    """
    return re.sub(r"\s+", " ", text or "").strip()


# 读页面正文（**只读**）。shadow DOM 要逐 root 收：body.innerText 不穿 shadow，
# 而生产站点里 shadow 页越来越多（实测遇过整页正文只剩 10 个字符）。
# 遍历用 getElementsByTagName —— 动作一律走 cdp，这里只是「看」。
_PAGE_TEXT_JS = (
    "return (function(){"
    "var roots=[document],i=0;"
    "while(i<roots.length){var r=roots[i++];var els=r.getElementsByTagName('*');"
    "for(var j=0;j<els.length;j++){if(els[j].shadowRoot){roots.push(els[j].shadowRoot);}}}"
    "var parts=[];"
    "for(var k=0;k<roots.length;k++){var rt=roots[k];"
    "if(rt.body){try{parts.push(rt.body.innerText||'');}catch(e){}}"
    "else{for(var m=0;m<rt.children.length;m++){var c=rt.children[m];"
    "try{parts.push(c.innerText||c.textContent||'');}catch(e){}}}}"
    "return parts.join(' ').replace(/\\s+/g,' ').trim();})();"
)


def _ok(output):
    """这条 cdp 命令自己报没报错。空输出不算错（成功时它不一定说话）。"""
    text = output or ""
    if not text.strip():
        return True
    return not any(marker in text for marker in ERR_MARKERS)


def _below_fold(element):
    """这个元素在折线下吗。`above_fold` 是主判据；`occluded_by == "offscreen"` 是同一个
    事实的另一种说法（老 observe 只填后者）—— 两边都认，免得因为少一个字段就把
    屏幕外的元素当成首屏可见的。
    """
    if element.get("above_fold") is False:
        return True
    return str(element.get("occluded_by") or "").strip().lower() == OFFSCREEN


def _frames_in_states(states):
    """这条流程**动过手的帧**（STATES 里出现过的 `frame_id`，去重、按出现顺序）。

    产物没有「枚举页面里有哪些帧」的本事（cdp CLI 那条路上没有这个命令），也不需要：
    **它动手的那几帧就是它要读的那几帧** —— 见 `page_signature` / `_urls`。
    """
    out = []
    for state in states or []:
        for step in (state or {}).get("steps") or []:
            fid = str(((step or {}).get("target") or {}).get("frame_id") or "")
            if fid and fid not in out:
                out.append(fid)
    return out


def _wanted_urls(states):
    """这条流程**认得的那几个地址**：各状态 `when.url_contains` 的值（去重、保序）。

    给 `_live_frames_ok` 当尺子用 —— **子串匹配**，与 `_matches` / `_urls` 同一把尺子
    （判据怎么比地址，这里就怎么比）。

    为什么来源**不是** `_frames_in_states` 那几个帧号：帧号是**探索期录的**，重放一开始的
    `goto` 一重建子帧就全成死号，而 `_frame_url` 对死号返回空串 ⇒ 按帧号收的那集合
    **恒为空** ⇒ 判据退化成「每 `LIVE_PROBE_EVERY` 秒刷一次」。那条路**碰巧也能把帧刷出来**
    （刷得够勤总能撞上问卷帧），但它不是判据，而且代码做的事与 docstring 里写的不是一件事。
    ⇒ 按地址收。

    ⚠️ 收成空集是**合法**的（这条流程没有任何状态报过地址，例如判据全是 `text_contains`）——
    此时 `_live_frames_ok` 回落到旧口径，理由写在那条 docstring 里。
    """
    out = []
    for state in states or []:
        url = ((state or {}).get("when") or {}).get("url_contains")
        if url and url not in out:
            out.append(url)
    return out


def _frame_of_element(element, fallback=""):
    """重新 observe 找到的这个候选**在哪一帧**（读它自己的 `frame_path`）。

    与 `agent/browser_agent.py:_frame_id_of_path` 同一套判据（那边写账本，这边读回来）：

    - `["main"]` / 空 → `""`（主帧）
    - `["main", "<frameID>"]` → 那个 frameID
    - 读不出来（没有这个键 / 嵌了两层以上）→ `fallback`（`target` 里那一帧）

    第三种为什么退回 `fallback` 而不是取最深那一段：`cdp` 换点击坐标只补**目标帧的
    owner `<iframe>` 在主帧里**那一个原点，中间几层没人补 —— 猜一个更深的帧号不是
    「够不着」，是**按错的坐标点了一下**。

    ⚠️ **这一格没有测试钉它**（`tests/**` 不在这一轮能动的范围里）：`None` → 退回主帧这条
    路上只有这份注释与报告在守，别以为它被覆盖了。
    """
    path = (element or {}).get("frame_path")
    if isinstance(path, str) or not isinstance(path, (list, tuple)):
        return fallback
    rest = [str(seg or "").strip() for seg in path]
    rest = [seg for seg in rest if seg and seg.lower() != FRAME_MAIN]
    if not rest:
        return ""
    return rest[0] if len(rest) == 1 else fallback


def _missing_short(command):
    """缺这条命令的**短**说法（trace 的 note 与「为什么是 null」用）。"""
    what = MISSING_CDP.get(command, (command, ""))[0]
    return "这个 cdp 不会「%s」（%s）" % (what, command)


def _missing_say(command):
    """缺这条命令时给人看的**整句话**：缺什么 + 什么后果 + 怎么换（D16：人话）。"""
    what, outcome = MISSING_CDP.get(command, ("「%s」这件事" % command, "这一步做不了"))
    return ("这个 cdp 不会「%s」（%s）：%s。换一个带它的 cdp，"
            "或用环境变量 SITEFORGE_CDP_BIN 指到它。" % (what, command, outcome))


def _unknown_command(out, err, command):
    """这一次失败是「它**没有**这条命令」吗（不是「跑失败了」）。"""
    low = ("%s\n%s" % (out or "", err or "")).lower()
    return COMMAND_UNKNOWN in low and (command or "").lower() in low


def _command_list(text):
    """数 `cdp --help` 的命令表（cobra：`Available Commands:` 下面一行一个，名字在第一列）。

    认不出来返回 None —— 「不知道它有什么」和「它什么都没有」是两件事，
    混成一件事会让产物平白放弃一条本来能用的命令。
    """
    if "Available Commands" not in (text or ""):
        return None
    names, started = set(), False
    for line in (text or "").splitlines():
        if not started:
            started = "Available Commands" in line
            continue
        if not line.strip():
            if names:
                break
            continue
        if not line.startswith(" "):
            break                    # 命令表到头了（下一段是 Flags:）
        names.add(line.split()[0])
    return names or None


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


def _label(target, step=None):
    """这一步在**人话**里叫什么。target 没写文字时给个说人话的兜底，不把选择器端给人看。"""
    for key in ("text", "label", "name"):
        value = (target or {}).get(key)
        if value:
            return str(value)
    if step and step.get("fill"):
        return str(step["fill"])
    return "没写名字的元素"


def _landing_say(output):
    """这条 cdp 回执里关于**落点判据**的那件事（没有就返回空串）。

    ⚠️ 读的是**字段**（`release_withheld` / `landing_withheld` / `landing_blind`），
    不是句子里扫「扣下」这类字 —— 扫字改一个词就失效，而失效的方向是**静默**
    （又变回「填好了」）。字段契约见 `cdp form` / `cdp click` 的成功回执
    （内核那一份 `SummarizeLanding`，CLI 与 MCP 两个门共用）。
    """
    raw = (output or "").strip()
    if not raw.startswith("{"):
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("release_withheld") or data.get("landing_withheld"):
        cover = data.get("covered_by") or "别的东西"
        return "（按下之后 %s 盖了上来，这一次抬手没有交给它）" % cover
    if data.get("landing_blind"):
        return "（这一帧的落点判据用不了：跨站子帧里它不触发）"
    return ""


def _say(action, label, ok, level=None, progress=None, landing=""):
    """给人看的一句话（D16：使用者是非技术人员 —— 不是错误码、不是选择器）。"""
    if action == "click":
        line = "点了「%s」" % label if ok else "页面上没找到「%s」，这一步没做成" % label
    elif action == "form":
        line = "填好了「%s」" % label if ok else "没找到「%s」这个输入框，没填成" % label
    elif action == "scroll":
        # 「滚进视口」而不是「往下滚一屏」：cdp 的 scroll 干的是**前一件事**（它收的是
        # 选择器），产物重放的就是账本里那一下 —— 说成像素滚动是另一件事的旧说法。
        line = "把「%s」滚进了视口" % label if ok else "滚不动「%s」" % label
    elif action == "goto":
        line = "打开了 %s" % label if ok else "打不开 %s" % label
    elif action == "wait":
        line = "等页面加载"
    else:
        line = "不认识的步骤「%s」" % action
    if ok and level:
        line += "（换了第 %d 个找法才找到）" % (level + 1)
    if ok and landing:
        # 落点被扣下 / 判据用不了时，光说「点了」「填好了」是**假话** ——
        # 那一下到底交没交给目标，只有这条回执说得清（T2）。
        line += landing
    if progress is False:
        line += "；页面没有变化"
    return line


class Filler:
    """$site 的重放器：确定性执行 STATES。

    ⚠️ 运行期**绝不**调模型（规格 §13：重跑必须便宜）。出问题就按回退链找、
    找不着就早停，不在跑的时候让谁去「想办法」。
    """

    #: 这条流程**动过手的帧**（`__init__` 里从 STATES 填）。**类属性上留一个空元组**：
    #: 不经过 `__init__` 拿到的实例（单测里用 `object.__new__(Filler)` 直接问
    #: 「这一页算不算这个状态」）也要能用 —— 那种实例没有 CDPHelper，读不了子帧，
    #: 而空元组正好等于「只读主帧」，也就是加帧之前的那个行为。
    frames = ()
    #: 同上（活帧）：不进 `__init__` 的实例上也要有一个空表可读 —— 见 `_read_frames`。
    live_frames = ()
    #: 同上（这条流程认得的地址）：空元组 = **这条流程一个地址都没报过** ——
    #: 正是 `_live_frames_ok` 里「回落到旧口径」的那一支，所以这里的默认值与原意一致。
    want_urls = ()
    #: 同上（替身）：不进 `__init__` 的实例没有 CDPHelper —— 读帧那几条路靠它判「读不了」，
    #: 而不是抛 AttributeError（单测里那些只 stub 了 `_url`/`page_signature` 的实例）。
    cdp = None
    #: 同上（观测）：`_model_is_fresh()` 要问「手上那份观测还是不是这一页的」——
    #: `None` = **没有观测**，正是「说不清」那一支，语义与 `__init__` 里的初值一致。
    #: ⚠️ 2026-09-18 补：`_applies` 改成「等完**无论 readyState 说什么都重判一次」之后，
    #: 这条判断在 `object.__new__(Filler)` 那种实例上**第一次变成可达路径** ——
    #: 在此之前它够不着，于是这一格漏了也没人发现（两条用例当场红）。
    _last_model = None

    #: 这一趟里**等过、但没等到它走**的遮挡物（`"tag#id.class"`）。它已经不是加载蒙版了，
    #: 后面再遇到**不再等**（见 `_covered_by`）—— 不然每步白赔一笔。
    #: 同样给一个类默认值：`object.__new__(Filler)` 那种实例上也够得着这一格。
    _stuck_covers = frozenset()

    #: 最近一次 `goto` 时 cdp 在 stderr 上说的话（见 `__init__` 与 `_do` 里 goto 那段）。
    #: 类默认值同上：`object.__new__(Filler)` 的实例够得着这一格。
    goto_echo = ""

    def __init__(self, ws_url, form_file, correlation_id, task_id="",
                 trace=None, stop_at=None, shots="failed", delay=DELAY_RANGE,
                 no_report=False):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger(SITE)
        self.trace_path = trace
        self.stop_at = stop_at or 0
        self.shots = shots or "failed"
        self.delay = delay
        self.no_report = no_report    # --no-report：这次不上报（自测/调试用；生产必须上报）
        self.tracing = bool(trace)
        if self.no_report:
            self.log.info("[%s] 这一次不上报（--no-report）：结果不往生产接口写", self.cid)
        self.step = 0          # 当前第几步（全局编号，与 --stop-at 同一套）
        self.stuck = 0         # 连续没做成的步数（早停看它）
        self.skipped = 0       # 被 `when` 判据整组跳过的步数（最后那句总结要报出来）
        self.skipped_states = {}   # 状态名 → 被跳过的步数（报「跳过最多的是哪个状态」）
        self.stalled = 0       # 连续「点了但页面没动」的步数（另一种原地打转）
        self._reported_url = ""
        self.missing = set()   # 这个 cdp 没有的命令（认出来一次就够：之后不再白跑、不再喊）
        self.commands = None   # 它的命令表（`--help` 数的）；None = 还没探过/探不出来
        self.probed = False
        self.observe_why = None    # 最近一次「重新看页面」没成的原因（人话）
        self.progress_why = None   # 最近一步「为什么 progress 是 null」（人话）
        self.shots_why = None      # 最近一步截图没落成的原因（人话）
        #: 最近一次 `goto` 时 **cdp 自己在 stderr 上说的话**（成功时才留）。
        #: 为什么单独一个通道：见 `_do` 里 goto 那段的说明（它**不参与判据**）。
        self.goto_echo = ""
        #: 这条流程**动过手的帧**（STATES 里出现过的 frame_id）。产物读页面时也读它们 ——
        #: 见 page_signature / _urls 的 docstring：动手在子帧、读页面却只看主帧，
        #: 那两件事量的根本不是同一页。
        self.frames = _frames_in_states(STATES)
        #: 这条流程**认得的那几个地址**（各状态 `when.url_contains`，见 `_wanted_urls`）——
        #: 「手上这几个活帧里有没有我要读的那一个」这条判据的尺子（`_live_frames_ok`）。
        self.want_urls = _wanted_urls(STATES)
        #: **最近一次重新 observe 看见的活帧**（`_note_live_frames`）。账本里的帧号只活在
        #: 录它的那一次会话里，重放时的 `goto` 一重建子帧它们就全死了 —— 读页面要靠这一串。
        self.live_frames = []
        #: 上一次「去找活帧」的时刻 —— 节流用（`_refresh_live_frames` 里比 `LIVE_PROBE_EVERY`；
        #: 不节流的话，「这一页没有帧」的页面上每读一次页面就起一个 cdp 进程）。
        #: ⚠️ 原先这里写的是「读不出东西的那些帧（多半已经不在了）…全死了才去找活帧」——
        #: 那是 `_frames_all_dead` / `_dead` 时代的话，**那两个名字现在代码里没有了**
        #: （找活帧的判据见 `_read_frames`），别再照那句读。
        self._probe_at = 0.0
        #: 最近一次 observe 的模型（`page_signature` 一路都读不到正文时用它兜底）
        self._last_model = None
        #: 这一趟里等过、但没等到它走的遮挡物（见 `_covered_by`：赖过一次的就不再等）
        self._stuck_covers = frozenset()

    # ── 基础设施 ────────────────────────────────────────────

    def _dly(self, low=None, high=None):
        lo, hi = self.delay if low is None else (low, high)
        if hi and hi > 0:
            time.sleep(random.uniform(lo, hi))

    def _ev(self, js, frame_id=""):
        """读路径的 eval（规格 §5.2：动作一律走 cdp 的 click / form / scroll，不手拼 JS）。

        `frame_id` 给了就在**那一帧**里求值（`CDPHelper.eval` 的同名参数 → `--frame-id`）：
        跨源 iframe 里的正文只有在那帧的上下文里读得到，主帧的 JS 看不见它。
        """
        return _clean_eval(self.cdp.eval("(function(){%s})()" % js, frame_id or ""))

    def _url(self):
        """当前地址 —— **只取第一行**：`CDPHelper.eval` 交出来的是 stdout+stderr 串在一起，
        而 chrome 的日志噪声（`ERROR: could not unmarshal event: … IPAddressSpace`）也在里面，
        不切出来就会灌进 trace 的 `url` 字段（真站实测见过）。地址本来就是一行。
        """
        raw = self._ev("return window.location.href;").strip()
        return raw.splitlines()[0].strip().strip('"').strip("'") if raw else ""

    def _read_frames(self):
        """**读页面**时该读哪几帧：**当场活着的那几个**（账本里的号只是线索）。

        为什么不能只信账本里的号：**frameID 只活在录它的那一次会话里** ——
        重放一开始的 `goto` 把页面重载、子帧重建，账本那一串全成死号
        （真站实测：`OOPIF eval: attach failed: No target with given id found`）。
        只读死号 = 什么都读不到 + **不出声**，于是「成功文案在子帧里」这件事就变回看不见 ——
        而产物会一路说「每一步都做成了」（2026-09-17 第七轮量出来的）。

        ⚠️ **历史（`_frames_all_dead` 那个套）**：更早的写法里，找活帧那一下挂在一个
        **不会被调用**的判据上 —— `_frames_all_dead()` 依赖 `_dead`，而 `_dead` 只有
        `_urls()` 会填；判据（`when`）是**纯文本**的时候根本不走 `_urls()` → 探测从来不触发 →
        `live_frames` 永远是空的 → 读页面只剩主帧 → **子帧正文（含成功文案）永远读不到**
        （旁证：`runtime/selftest/` 下几十趟自测，**没有任何一趟报过成功**）。
        **那两个名字在代码里已经没有对应物了** —— 这一段只当教训留着，不是现在的行为。

        现在：**读之前先确认手上那几个活帧里有我要读的那一个** —— 判据见 `_live_frames_ok`
        （帧自己的地址命中这条流程认得的 URL / `want_urls`；认得的地址一条都没有时回落旧口径）；
        没有就去找一次（`observe`，节流见 `LIVE_PROBE_EVERY`）。
        ⚠️ 与 `_relocate` 那条路的**取向**相同（能用的帧 = 当场活着的帧），但两条路
        **不是同一个判据**：`_relocate` 只是把这一眼看见的帧**记下来**，它不挑「是不是我要的那个」。
        """
        # 门槛（2026-09-17 第九轮）：**「我手上这几个帧里，有没有我真正要读的那个」** ——
        # 老门槛是「某个帧还读得到」，而**广告帧永远满足它**（R-68 量到的形状）：
        # 于是再也不去找，读页面只剩主帧 + 广告帧，问卷正文永远读不到。
        # 现在（2026-09-20 补齐这条判据）：帧表必须是**当场那次观测**的
        # （`_model_is_fresh`：手上那份观测还在这一页上），而且**至少有一个帧的地址
        # 命中这条流程认得的 URL**（`_live_frames_ok` —— 在 2026-09-20 之前，这一格
        # 只是「有一个帧答得上」，所以上面那句「我真正要读的那个」当时是句空话）；
        # 判不了/不新鲜就去找一次（节流）。
        # 「该去找一次吗」= 手上那份观测**说不清这一页现在有哪些帧**：
        #   · 它已经过期（换过页）→ 说不清；
        #   · 声明里有帧、可手上一个活帧都没有 → 说不清；
        #   · 有活帧、可**没有一个是我要读的那个**（死号，或者全是广告帧那种）→ 说不清。
        # ⚠️ 连「一个帧都没有」也算说不清：这一页可能**正要长出一个来**（问卷那类部件
        # 是点了才出现的）—— 只在「手上这份观测还是这一页的」时候才放行（每页至多一次）。
        # ⚠️ **只在「这条流程确实活在帧里」时去找**（`self.frames` 非空 = 账本里有帧号）：
        # 一律去找会把 §13 那条「生产重跑不 observe」破掉（`tests/` 有钉子钉着它）。
        # 主帧里的流程一次都不找；带帧的流程每换一页找一次（节流见 LIVE_PROBE_EVERY）。
        if self.frames and (not self._model_is_fresh()
                            or not self.live_frames
                            or not self._live_frames_ok()):
            self._refresh_live_frames()
        out = []
        for fid in list(self.frames) + list(self.live_frames):
            if fid and fid not in out:
                out.append(fid)
        return out

    def _model_is_fresh(self):
        """手上那份观测，**是不是还在这一页上**看的。

        为什么用它当门槛（而不是「某个帧还能读」）：读页面这件事要的是
        「这一页现在有哪些帧」—— 而这件事**只有当场那次观测说得清**。
        页一换（URL 变了），那份观测就过期了：它记的帧可能已经不在了，
        而新出现的帧（比如问卷那个部件）它根本不知道。
        """
        if not self._last_model:
            return False
        return (self._last_model.get("url") or "") == (self._url() or "")

    def _live_frames_ok(self):
        """手上这几个活帧里，**有没有我要读的那一个**。

        判据 = 「帧自己的地址命中这条流程认得的 URL（`want_urls`）」—— 子串匹配，
        与 `_matches` / `_urls` **同一把尺子**（判据怎么比地址，这里就怎么比）。

        为什么不是「某个帧答得上就行」（2026-09-17 第九轮量到、2026-09-20 改过来）：
        **广告帧永远答得上**（R-68 量到的形状：`id-msp.newsbreak.com/sync-nbu…` 那种）。
        于是这一门恒为真 ⇒ 再也不去找那个真正的问卷帧 ⇒ 读页面只剩主帧 + 广告帧
        ⇒ 按正文判据的状态**静默跳过**（不出声，看着像「跑完了，一步没走」）。
        真站实测：卡死那几趟里活帧表**从头到尾一次都没换过**（恒为同一个广告帧）。

        ⚠️ **主帧不在这张表里**（`_note_live_frames` 用 `if fid` 滤掉了主帧那个空串），
        所以「主帧地址命中了 `want_urls`」**不会**把这条判据撑成真 —— `_frame_url("")`
        也直接返回空串。⚠️ 别把主帧塞进 `live_frames`：那会让判据静默退化成恒真。

        ⚠️ **`want_urls` 为空时回落到旧口径**（「任一帧答得上」）：这条流程**没有任何状态
        报过地址**（判据全是 `text_contains` 那种）⇒ 拿什么去比都不知道。此时
        「永远刷新」是**错的**：那是每 `LIVE_PROBE_EVERY` 秒起一次 `observe()`
        （`_read_frames` 每步都要读页面，而读页面就要过这一门）—— 既白花钱，
        又**改掉了这条流程原来的行为**。⇒ 判不了就按旧口径放行：**这条流程的帧判不了，
        不拿一个猜出来的判据去刷它**。
        """
        if not self.want_urls:
            # 判不了 → 放行（旧口径）。为什么不是「永远刷新」见上面 ⚠️ 那一段。
            return any(self._frame_url(fid) for fid in self.live_frames)
        for fid in self.live_frames:
            url = self._frame_url(fid)
            if url and any(want in url for want in self.want_urls):
                return True
        return False

    def _refresh_live_frames(self):
        """去找一次活帧（`observe` 一次，把模型里出现的帧记下来）。

        **节流**：`page_signature()` 每步都要读页面，不节流会在「这一页没有帧」的页面上
        反复起进程。
        """
        now = time.time()
        if now - self._probe_at < LIVE_PROBE_EVERY:
            return
        self._probe_at = now
        model = self._observe()
        if model:
            self._note_live_frames(model)

    def _note_live_frames(self, model):
        """把这一份观测里出现的（非主帧）帧记下来 —— 它们是**现在活着**的那几个。

        ⚠️ **是替换，不是累加**（2026-09-17 第七轮量出来的）：累加版本里，
        页面上的**广告帧**（`id-msp.newsbreak.com` 那种）会一直留在表里 ——
        它们活着、`_live_frames_ok()` 于是永远为真 → **再也不会去找那个真正的问卷帧** →
        读页面只剩主帧 + 广告帧，而问卷正文（与成功文案）**永远读不到**。
        外部对照实验（同一窗口同一时刻 `cdp eval --frame-id <问卷帧>`）证明那一刻
        「`Progress: 80% … Phone Number: …`」**读得到** —— 读不到是产物自己的事。
        """
        seen = []
        for key in ("actions", "fields"):
            for element in (model or {}).get(key) or []:
                if not isinstance(element, dict):
                    continue
                fid = _frame_of_element(element, None)
                if fid and fid not in seen:
                    seen.append(fid)
        self.live_frames = seen

    def _urls(self):
        """这一页的地址：**主帧的 + 这条流程动过手的每一帧的**（去重、保序）。

        为什么不是一个：流程活在跨源 iframe 里时，账本里那套判据的 URL **就是子帧的**
        （`cdp observe` 报的 `url` 在这种页面上是子帧的 —— 实测 gowizard：19 个状态里
        16 个的 `url_contains` 是 `chameleon-…`，而主帧的地址从头到尾是
        `www.gowizard.com/auto/…`）。只拿主帧比 → 那 16 组步骤**静默跳过**
        （`_applies` 返回 False 是不出声的）：产物看着跑完了，其实一步没走。
        """
        # ⚠️ **只认「帧自己的 location.href」这一路**（2026-09-17 第九轮）：
        # 原先还并了「主帧里那些 `<iframe>` 的 `src`」——实测那条**从根上够不着**：
        # 问卷帧根本不在主帧的 DOM 里（`getElementsByTagName('iframe')` 只拿到广告帧），
        # 而且 `src ≠ 帧自己的 location.href`（部件是用 JS 造帧/换 src 的）。
        # 判据要的那串**就是帧自己的 location.href** —— 它只能从帧那一侧（CDP）读。
        out = []
        for url in ([self._url()] + [self._frame_url(fid) for fid in self._read_frames()]):
            if url and url not in out:
                out.append(url)
        return out

    def _frame_url(self, frame_id):
        """某一帧现在的地址（读不到就空串 —— 读不到不是「它是空的」，是没法判）。

        """
        if not frame_id:
            return ""
        return self._ev("return window.location.href;", frame_id).strip().strip('"').strip("'")

    def page_signature(self):
        """这一页长什么样：可见正文，归一化口径与 cdp observe 的 page_text 一致（§4.3）。

        不裁长度 —— 成功文案可能在第 600 字之后；**trace 里落的那份才裁到 600**，
        于是 trace 的 page_sig 能与当时 observe 的 page_text 直接对上。

        ⚠️ 「与 observe 的 page_text 同口径」这件事意味着它**必须跨帧**：observe 的
        page_text 是「主帧 + 各子帧的正文**拼起来**」（`internal/observe_frames.go` 的
        `mergeFrameModel`），而这里原先只读主文档（+shadow）—— 跨源 iframe 里的正文
        一个字都读不到。真站实测的后果：**成功文案就在那个 iframe 里**，`_succeeded()`
        永远看不见 → 产物**永远不可能报成功**，而它会一路说「每一步都做成了」。
        """
        parts = [_norm(self._ev(_PAGE_TEXT_JS))]
        parts += [_norm(self._ev(_PAGE_TEXT_JS, fid)) for fid in self._read_frames()]
        text = " ".join(p for p in parts if p)
        # **再并上最近一次 observe 的正文**（不是只在读空的时候才用）——
        # observe 的 page_text 是**跨帧拼起来**的，与这里同一口径，而且比「直接去读每一帧」
        # 全：帧号会漂、帧可能读不到，而手上那份观测里**本来就写着**子帧的正文。
        # 真站实测（2026-09-17 第六轮）：某一步 `when.text_contains` 要的是
        # 「Progress: 60% Almost done Fill in your last few…」（子帧里的问卷文案），
        # 而这一路只读得到**主帧那页落地页的推广文案** → 判成「不像」→ 整组静默跳过。
        model_text = _norm((self._last_model or {}).get("page_text") or "")
        if model_text and model_text not in text:
            text = (text + " " + model_text).strip() if text.strip() else model_text
        return text

    def _trace(self, line):
        """往 trace 追一行 JSON（JSON Lines）。不给 --trace 时它什么都不做。"""
        if not self.trace_path:
            return
        try:
            with open(self.trace_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.log.warning("[%s] trace 写不进：%s", self.cid, exc)

    def _rpt(self, label):
        if self.no_report:
            return          # --no-report：一个字节都不往生产发（自测/调试走这一条）
        try:
            report_url(self.cdp, self.tid, label, self.log)
        except Exception as exc:                        # 上报失败不能把任务搞挂
            self.log.warning("[%s] 报 URL 失败：%s", self.cid, exc)

    def _rpt_if_moved(self, label):
        url = self._url()
        if url and url != self._reported_url:
            self._reported_url = url
            self._rpt(label)

    # ── 调试路径（只有 --trace 时才走；重跑路径不碰这里）──────

    def _run(self, cmd, timeout=60):
        """起一个 cdp 进程（调命令与探命令表都走这里）。起不来返回 None。"""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:
            self.log.warning("[%s] 调 cdp 失败（%s）：%s",
                             self.cid, cmd[1] if len(cmd) > 1 else "?", exc)
            return None

    def _probe(self):
        """这个 cdp 到底有哪些命令（跑一次 `cdp --help` 数它的命令表）。**最多探一次**。

        ⚠️ 只在**出了问题**或**--trace 开始**时才探 —— 生产重跑那条路（声明里的找法都成）
        一次都不探，所以不多花进程（§13：重跑要便宜）。
        探不出来返回 None：**「不知道它有什么」不是「它什么都没有」**。
        """
        if not self.probed:
            self.probed = True
            done = self._run([CDP_BIN, "--help"], timeout=30)
            if done is not None:
                self.commands = _command_list((done.stdout or "") + "\n" + (done.stderr or ""))
        return self.commands

    def _note_missing(self, command):
        """它没有这条命令：记下 + 用**人话**说一次（每条只说一次 —— 不刷屏，也不白跑）。"""
        if command not in self.missing:
            self.missing.add(command)
            self.log.warning("[%s] %s", self.cid, _missing_say(command))

    def _command_absent(self, command):
        """光看命令表：这个 cdp 没有这条命令吗。**探不出来就不猜**（返回 False）。"""
        known = self._probe()
        return known is not None and command not in known

    def _check_command(self, command, done):
        """这一次失败是「它根本没有这条命令」吗。是就记下 + 说人话，返回 True。

        两条证据：① 这一次的输出（cobra 会明说 `unknown command`）；② 它的命令表。
        """
        if _unknown_command(done.stdout, done.stderr, command) or self._command_absent(command):
            self._note_missing(command)
            return True
        return False

    def _check_capabilities(self):
        """这次调试要用到的几条 cdp 命令，这个 cdp 到底有没有 —— 缺了就说人话。

        ⚠️ **只在 --trace 时调**。老版本的 cdp 缺 observe / diff / screenshot，
        缺了不会让重跑跑不动，但会让 trace 少三样东西，而那是**静默**的 ——
        在这里一次说清，而不是等 trace 读出来才发现「怎么什么都没有」。
        """
        known = self._probe()
        if known is None:
            return                       # 探不出来就不猜
        for command in ("observe", "diff", "screenshot"):
            if command not in known:
                self._note_missing(command)

    def _cdp(self, *args, timeout=60):
        """调 cdp CLI。参数是 host/port —— 与 CDPHelper._parse_ws_url 拆出来的同一对。"""
        command = str(args[0]) if args else ""
        if command in self.missing:
            return None          # 已经知道它没有这条命令：别再白起一个进程
        cmd = [CDP_BIN] + [str(a) for a in args]
        cmd += ["--host", str(self.cdp.host), "--port", str(self.cdp.port)]
        done = self._run(cmd, timeout=timeout)
        if done is not None and done.returncode != 0 and self._check_command(command, done):
            return None          # 按「它做不了这件事」处理，别当成一次普通失败
        return done

    def _observe(self):
        """重新 observe 当前页面：§5.1b 回退链的最后一跳 + trace 的推进判据都靠它。

        没成时把**为什么**记在 `self.observe_why`（人话）—— 调用方要把它带进 trace，
        免得「没看成就说页面没变化」。
        """
        self.observe_why = None
        done = self._cdp("observe", "--json")
        if done is None:
            self.observe_why = (_missing_short("observe") if "observe" in self.missing
                                else "没能让 cdp 重新看这一页")
            return None
        if done.returncode != 0:
            self.observe_why = "cdp 重新看这一页没成"
            return None
        try:
            model = json.loads(done.stdout)
        except ValueError:
            self.observe_why = "cdp 重新看这一页给的答复看不懂（不是 JSON）"
            return None
        self._last_model = model if isinstance(model, dict) else None
        return model

    def _diff(self, before_path):
        """刚才那一下有没有推进：True / False；**算不出来时 None**（原因写在 self.progress_why）。

        观测故障不能说成「没推进」—— 那是一次观测问题被读成一次策略结论。
        """
        self.progress_why = None
        done = self._cdp("diff", "--before", before_path, "--json")
        if done is None:
            self.progress_why = (_missing_short("diff") if "diff" in self.missing
                                 else "没能让 cdp 比这一步")
            return None
        if done.returncode != 0:
            self.progress_why = "cdp 没能比出这一步有没有推动页面"
            return None
        try:
            return bool(json.loads(done.stdout).get("actionable"))
        except (ValueError, AttributeError):
            self.progress_why = "cdp 给的对比结果看不懂（不是 JSON）"
            return None

    def _snapshot_path(self, model):
        """把动作前的页面模型落到临时文件（cdp diff 要文件，不要内存对象）。"""
        try:
            handle, path = tempfile.mkstemp(prefix="siteforge-before-", suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as fp:
                json.dump(model, fp, ensure_ascii=False)
            return path
        except OSError as exc:
            self.log.warning("[%s] 动作前快照写不进：%s", self.cid, exc)
            return None

    def _shot(self, step, when):
        """落一张截图（只在调试路径调）。返回文件名（相对 trace 那一层），失败给 None。

        没成时把原因记在 `self.shots_why` —— 「没截图」不许跟「这步没问题」长得一样。
        """
        if not self.trace_path:
            return None
        try:
            data = self.cdp.screenshot() or ""
        except Exception as exc:
            self.log.warning("[%s] 截图失败：%s", self.cid, exc)
            self.shots_why = "截图没成：%s" % exc
            return None
        if not data.strip() or data.lstrip().startswith("{"):
            # 空输出 + 命令表里没有它 = 这个 cdp 根本不会截图。注意 CDPHelper.screenshot()
            # 只把 stdout 交出来（common.py:244），命令行那句 unknown command 在 stderr 上、
            # 被它丢了 —— 所以这里只能自己去数一次命令表（探一次，之后不再探）。
            if not data.strip() and self._command_absent("screenshot"):
                self._note_missing("screenshot")
            self.shots_why = (_missing_short("screenshot") if "screenshot" in self.missing
                              else "截图没成（cdp 没给出图）")
            return None
        try:
            raw = base64.b64decode(data.strip(), validate=True)
        except Exception:
            return None
        name = "%d-%s.png" % (step, when)
        path = os.path.join(os.path.dirname(os.path.abspath(self.trace_path)), name)
        try:
            with open(path, "wb") as fp:
                fp.write(raw)
        except OSError as exc:
            self.log.warning("[%s] 截图落盘失败：%s", self.cid, exc)
            return None
        return name

    def _unlink(self, path):
        if not path:
            return
        full = os.path.join(os.path.dirname(os.path.abspath(self.trace_path)), path)
        try:
            os.unlink(full)
        except OSError:
            pass

    # ── §5.1b：多元 target 的运行时逐级回退 ────────────────────

    def _usable(self, element, target=None):
        """能不能对它动手：**看不见的、真被挡住的**一律不要。

        对看不见的元素动手不是「没点到」，是**点到别的东西**。
        但**「在折线下」不算被挡住**：observe 给「只是不在视口里」的元素也填
        `occluded_by: "offscreen"`（参考站实测：`Get Started` 是 `visible: true,
        above_fold: false, occluded_by: "offscreen"` —— 滚动一下就看得见，声明里的
        选择器那条路也照点不误）。拿 offscreen 当「被挡住」拒掉，回退链就**恰好**
        拒掉它存在的那类元素：声明一失效 → 一个候选都找不到 → 这一步白算失败。
        所以视口这一轴看 `above_fold`，不看 `occluded_by`：
        折线下的候选**能用**，只是排在首屏可见的后面（见 `_relocate` 的排序）；
        target 自己写了 `above_fold_only` 时才硬性要求首屏。
        """
        if not element.get("visible", True):
            return False
        occluded = str(element.get("occluded_by") or "").strip().lower()
        if occluded and occluded != OFFSCREEN:
            return False
        if (target or {}).get("above_fold_only") and _below_fold(element):
            return False
        return True

    def _relocate(self, target, kind="action"):
        """最后一跳：重新 observe 当前页面，按 text + role + near 重定位（§5.1b）。

        返回候选 `[(选择器, 帧), …]`（可能为空）—— **帧是每个候选自己的**，从它这次
        观测里的 `frame_path` 读（页面重排后元素可能换了帧，甚至换回主帧；拿 target 里
        那一帧去够新候选，正是这一跳最容易白跑的地方）。读不出帧的候选（嵌套两层以上、
        老 cdp 不带这个键）退回 `target` 里那一帧。

        ⚠️ **这一跳在生产路径上也会发生** —— 它不是调试功能，是产物「不因小改版就断」的
        承重结构（规格 §13 前提①）。

        **帧号漂了的时候这一跳就是兜底**（CDP 的 frameID 只活在录它的那一次会话里；
        重放一开始的 `goto` 一重建子帧，账本那一串全成死号 —— 真站实测
        `OOPIF eval: attach failed: No target with given id found`）。**取舍写在这里**：
        兜底的代价是「多花一次 observe + 声明那一下记**一次失败计数**」（`STUCK_LIMIT` 攒到
        3 步就早停）；换来的是**不静默点错**。若有一天要省这一次 observe，先想清楚
        「帧号漂了谁来救」再动。
        """
        model = self._observe()
        if not model:
            return []
        # 这一眼看下去，**活着的帧**是哪些 —— 记下来给「读页面」用（见 _read_frames）。
        # 账本里的 frameID 只活在录它的那一次会话里：重放一开始的 `goto` 会把页面重载，
        # 子帧重建 → 账本那一串**全成了死号**（真站实测：`No target with given id found`）。
        self._note_live_frames(model)
        fallback = str(target.get("frame_id") or "")
        pool = (model.get("fields") if kind == "field" else model.get("actions")) or []
        want_text = _norm(target.get("text") or "").lower()
        want_label = _norm(target.get("label") or "").lower()
        want_role = (target.get("role") or "").strip().lower()
        want_near = (target.get("near") or "").strip().lower()

        hits = []
        for relax in (False, True):     # 先带语境找；找不到再放宽 —— 语境是「优先」，不是「必须」
            for element in pool:
                if not self._usable(element, target):
                    continue
                if kind == "field":
                    # ⚠️ 这四个来源**必须与生成侧取名字时的来源一致**（`browser_agent._fill_info`
                    # 的 `label or hint or placeholder`、`_fill_name` 还会用 `type`）——
                    # 不然就会出现「账本里的名字是 placeholder 来的、这里只认 label/hint」
                    # 的错位：字段明明在页面上，回退链一个候选都找不到
                    # （真站实测：`Email Address:` 那一步就是这么挂的）。
                    key = _norm(" ".join(str(x or "") for x in (
                        element.get("label"), element.get("hint"),
                        element.get("placeholder"), element.get("type"),
                    ))).lower()
                    # ⚠️ 2026-09-17：账本里那几条身份**任意一条**对得上就算这一格 ——
                    # 原先只认 `label` 一条。为什么要放宽：`label` 是「三选一」的结果，
                    # 站点哪天把 `name` 属性补上，observe 的 `hint` 就从 `textField-173862`
                    # 变成 `form_components[173862]`，而账本里存的还是旧的那个 ——
                    # 于是「字段明明在页面上，回退链一个候选都找不到」（上面那条实测的形状）。
                    # 三条都是**这一格自己的**身份（名字 / 自报的 name 或 id / 例子值），
                    # 任一条命中就足以认出它；认错了还有 `_usable` 与排序挡着。
                    wants = [w for w in (_norm(str(target.get(k) or "")).lower()
                                         for k in ("label", "hint", "placeholder")) if w]
                    if wants and not any(w in key for w in wants):
                        continue
                else:
                    text = _norm(element.get("text") or "").lower()
                    if want_text and want_text not in text:
                        continue
                    if want_role and (element.get("role") or "").strip().lower() != want_role:
                        continue
                if want_near and not relax:
                    if (element.get("region") or "").strip().lower() != want_near:
                        continue
                hits.append(element)
            if hits:
                break

        # 稳定性评级（D3）高的先试；同样稳的里面，**首屏看得见的**先试 ——
        # 折线下那个能用（见 _usable），但先试它可能白滚一屏。
        rank = {"high": 0, "medium": 1, "low": 2}
        hits.sort(key=lambda el: (rank.get((el.get("stability") or "").lower(), 3), _below_fold(el)))
        out = []
        # ① **声明里的选择器 × 活着的帧**（只在这条 target 本来就带帧时）。
        #    为什么排在最前：帧号漂了，**页面结构没变** —— 声明里那条 nth-of-type 路径
        #    仍然是这个元素最精确的身份。丢掉它、只按语义找，会在「几个控件长得一模一样」
        #    的地方走错门（真站实测：三个组合框的 text 都是零宽空格，语义判据分不开它们，
        #    于是点开了**另一个**下拉的菜单，紧接着那一步的选项当然不在里面）。
        #    实测：账本里那三条声明路径拿到活帧里各命中 **1** 个，指向的正是原来那三个控件。
        if fallback:
            for frame in self.live_frames:
                if frame == fallback:
                    continue
                for selector in [s for s in (target.get("selectors") or []) if s]:
                    if (selector, frame) not in out:
                        out.append((selector, frame))
        for element in hits:
            frame = _frame_of_element(element, fallback)
            for selector in [element.get("selector")] + list(element.get("alternates") or []):
                if selector and (selector, frame) not in out:
                    out.append((selector, frame))
        return out

    # ── 动作 ────────────────────────────────────────────────

    def _do(self, action, selector, value=None, kind="value", frame_id="", label=""):
        """一个动作只走这一条路：cdp 命令（规格 §5.2：动作一律走 cdp，不手拼 JS）。

        `frame_id` 交给 `CDPHelper.click / form` 的**同名参数**（`forms/common.py:146` /
        `:202` 本来就收它，命令行上是 `--frame-id`）。元素在跨源 iframe 里时，**同一个
        选择器在主帧里命中 0** —— 不带这一帧，重放每跑一遍都会「页面上没找到」，
        而这件事在账本里原本看不出来（那份产物没有「帧」这个概念）。
        """
        if action == "click":
            return self.cdp.click(selector, frame_id=frame_id or "")
        if action == "form":
            # `strict` + `expect_label`：命中多个元素时**不许静默挑第一个** ——
            # 拿这个字段自己的身份（页面上写着的那句名字）认准它，认不出就大声失败。
            # 真站实测：一个 class 选择器被 zip / full_name / email 三个字段组共用，
            # 第一条第选择器一挂值就进了别的框（ZIP 框里躺着手机号、页面红字拒收）。
            extra = {"frame_id": frame_id or "", "strict": True, "expect_label": str(label or "")}
            base = ({"check": str(value).lower()} if kind == "check" else
                    {"select": str(value)} if kind == "select" else {"value": str(value)})
            try:
                return self.cdp.form(selector, **base, **extra)
            except TypeError as exc:
                # 部署的那份 `common.py` 还不认识严格闸那两个参数（2026-09-17 真站实测：
                # 这一下会把**整个产物**炸掉 —— 而 `run()` 的 except 当时不落 trace，
                # 于是「产物死了」在自测那边被读成「走完了 10 步、0 跳过」）。
                # 处置：**退回老路**（不消歧也照填），并把缺什么**大声**说出来 ——
                # 一次版本不齐不该让整趟跑死，但也不许静默降级。
                self.log.warning(
                    "[%s] 部署的那份 common.py 不认识 --strict / --expect-label（%s）；"
                    "这一步退回**不做消歧**的老路 —— 值可能落进别的框。"
                    "把本仓库的 common.py 同版本铺过去就好了。", self.cid, exc)
                return self.cdp.form(selector, **base, **{"frame_id": frame_id or ""})
        if action == "scroll":
            # ⚠️ 位置参数**是选择器**（CLI：`cdp scroll [selector]`）—— 把像素数字塞进
            # 这里 = 拿一个不存在的选择器去滚，真窗口实测 `cdp scroll 400` →
            # `Error: scroll mouse wheel failed: element not found`，每一步都必挂。
            return self.cdp.scroll(selector)
        if action == "goto":
            done = self._cdp("navi", value)
            ok = done is not None and done.returncode == 0
            # ⚠️ **cdp 在 stderr 上说的那句不能丢**（2026-09-18 记的账）：
            # `navi` 的「成功」现在有两种 —— 真等到 load 了，和「导航发出去了、
            # 但没等到 load / 没取到 frame tree」。**后者只有 stderr 说得出来**，
            # 而它原先被 `return ""` 吞了（「说了，但没人收得到」）。
            #
            # 为什么**不**把它并进返回值：返回的东西要过 `_ok()`，而 `_ok` 是**认词**的
            # （`ERR_MARKERS`）。cdp 的原话里偶然带一个 `error:`，就会把「打开了」
            # 读成「打不开」—— **那正是刚修掉的那个病**。所以走**第二个通道**：
            # 放这儿，由 `_run_step` 拼进 trace 的 note，**不参与判据**。
            self.goto_echo = ""
            if ok and done is not None:
                # ⚠️ **只留 cdp 自己说的话，别把它的告警洪水灌进 trace**
                # （2026-09-18 真站实测：原始 stderr 一次 15 行
                #  `could not unmarshal event: … unknown IPAddressSpace value: Private`
                #  —— 那是 chromedp 跟不上 Chrome 新枚举值的噪音，与我们无关）。
                # 噪音不丢干净：**数出来**，让人知道「还有东西没说」。
                lines = [x.strip() for x in (done.stderr or "").splitlines() if x.strip()]
                mine = [x for x in lines if "cdp navi:" in x]
                noise = len(lines) - len(mine)
                self.goto_echo = "；".join(mine)
                if noise:
                    self.goto_echo += ("；" if self.goto_echo else "") + \
                        "（另有 %d 行 cdp 自己的告警，略）" % noise
            return "" if ok else "Error: 打开 %s 失败" % value
        raise ValueError("产物写错了：不认识这个动作「%s」" % action)

    def _fill_value(self, fill, step):
        """这一步填什么：先读 --form-file 里的键，没有就用 fallback 里的一个随机值。"""
        source = fill.get("source")
        if source:
            given = str(self.form_data.get(source) or "").strip()
            if given:
                return given
        candidates = list(fill.get("fallback") or [])
        if not candidates:
            self.log.warning("[%s] 「%s」既没有 form-file 的值也没有 fallback", self.cid, source or "?")
            return ""
        pick = random.choice(candidates)
        if isinstance(pick, dict) and pick.get("random"):
            return self._random_value(str(pick["random"]))
        return str(pick)

    def _random_value(self, kind):
        """数据随机化（拟人）：每次重跑都同一份数据是明显的机器味。"""
        first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        if kind == "first_name":
            return first
        if kind == "last_name":
            return last
        if kind == "full_name":
            return "%s %s" % (first, last)
        if kind == "email":
            return "%s.%s%d@%s" % (first.lower(), last.lower(), random.randint(100, 999),
                                   random.choice(EMAIL_DOMAINS))
        if kind == "phone":
            return random.choice(PHONES)
        if kind == "postcode":
            return random.choice(POSTCODES)
        if kind == "state":
            return random.choice(US_STATES)
        if kind == "dob":
            return "%02d/%02d/%d" % (random.randint(1, 12), random.randint(1, 28),
                                     random.randint(1970, 1995))
        if kind == "password":
            return "Test%d!" % random.randint(1000, 9999)
        raise ValueError("产物写错了：不认识这个随机值类型「%s」" % kind)

    def _wait_ready(self, timeout=None):
        """等这一页**加载完**（`document.readyState === 'complete'`），最多等 `timeout` 秒。

        为什么要有它（2026-09-17 真站实测，`when` 出声之后一眼看出来的）：
        某趟 baseline 33 步里**只有 goto 那一步真做了、其余 32 步全被跳过**，
        第一条跳过的原话是「正文里没有「___ The listings featured…」」——
        而那一页**马上就要有**那句话：`cdp navi` 只负责**发起**导航，
        页面还在下载/执行，紧接着判 `when` 就把「还没加载完」读成了
        「这一页不像那个状态」，整组步骤静默跳过。
        """
        if getattr(self, "cdp", None) is None:
            return False                   # 没有助手（单测里的替身）→ 等不了，别抛
        limit = WAIT_READY_SECONDS if timeout is None else float(timeout)
        deadline = time.time() + limit
        while time.time() < deadline:
            state = (self._ev("return document.readyState;") or "").strip().strip('"').strip("'")
            if state == "complete":
                return True
            time.sleep(0.3)
        self.log.info("[%s] 等了 %.0f 秒这一页还没到 readyState=complete（照旧往下走）",
                      self.cid, limit)
        return False

    def _clear_obstructions(self, why=""):
        """开跑（以及每次导航之后）把**挡路的 cookie 同意弹层**点掉。

        为什么要产物自带（2026-09-17 真站实测，链条闭合）：生产**每一单都是新窗口**、
        每次都清 cookie —— 所以**每单都会遇到同意弹层**；而探索那一趟跑在一个「弹层已经
        被自己点掉」的会话里，**账本学的是「没有弹层的世界」**，产物里也就没有那一步。
        自测换到干净会话（R-F1）之后弹层盖住答题区，下一步的点击**点到弹层上**、
        cdp 照样回 ok（`match_count: 1` 说的是「选择器命中 1 个」，不是「点到的就是它」），
        于是「点了个寂寞」被记成做成了、后面全塌。
        ⚠️ **不能指望账本里恰好有这一步** —— 弹层是环境带来的，不是站点流程的一部分。

        判据（与 `cdp observe` 的 obstructions **同一套词汇**：文字 / id / class 里有没有
        `cookie|consent|gdpr|privacy`）—— 为什么不用 observe：**生产那个 cdp 没有 observe**
        （§4.6 的能力边界），而弹层恰恰在生产每单都出现。所以这里走 `eval`（**读**，§5.2 允许），
        点还是走 cdp 的 click。

        只做**有把握**的那一下：得在一个可见的同意类容器里找到带稳定 id 的按钮，
        且它的文字是「接受/同意/关闭/拒绝」那一族；找不到就**什么都不做**（宁可不做），
        由 `_covered_by` 保证「点到弹层上」不会被记成做成了。
        """
        raw = self._ev(_CONSENT_PROBE_JS)
        if not raw or "|" not in raw:
            return                      # 没有弹层（正常情况）—— 静默是对的，这是「无事可做」
        selector, text = raw.split("|", 1)
        out = self.cdp.click(selector)   # 动作一律走 cdp（§5.2）
        self.log.info("[%s] %s把同意弹层点掉了（%s，%r）：%s", self.cid,
                      ("%s之后 " % why) if why else "开跑前 ", selector, text[:30],
                      "看起来成了" if _ok(out) else "**没成**")

    def _consent_step_handled(self, label, frame=""):
        """「点掉同意弹层」这一步**找不到元素**时：它是软跳过，不是失败。

        为什么（2026-09-17 真站实测，自测第 2 遍挂在这上面）：
        账本是在一个「弹层已经点掉过」的会话里录的，所以它记了一条硬步骤
        `click「Reject All」`；而**同一会话**再跑一遍（扰动自测第 2 遍的定义就是
        「接着再跑一遍」）时弹层**根本不在页面上** → 找不到 → 记一次失败 → 整遍挂。
        第 1 遍却过（那一遍弹层在）。同一条产物在两遍里表现相反，差别只在环境的残留。

        判据（两道，都要）：
          ① 这一步的名字属于「对同意做个决定」那一族（`_is_consent_step`）；
          ② 此刻页面上**看不到**同意类容器（`_CONSENT_BOX_JS`）。
        ②量不出来（eval 不可用）时**照软跳过**，但把这件事写进 note —— 因为真正的
        兜底不在这一格：弹层要真还在，**下一步**会被 `_covered_by` 挡住并失败，
        整遍照样挂。所以这里放宽读不出「这一次」的风险，换不到「把真失败抹掉」的后果。

        返回 `(算做成了吗, 一句人话)`；不是同意步骤 → `(False, "")`（交回原判据）。
        """
        if not _is_consent_step(label):
            return False, ""
        raw = self._ev(_CONSENT_BOX_JS, frame)
        still = str(raw or "").strip().strip('"').strip("'").strip()
        if still:
            return False, ""            # 弹层还在页面上 —— 那这是**真找不到**，照旧算失败
        return True, ("「%s」这一步：页面上已经没有同意弹层了（前面那次会话把它点掉过，"
                      "或者这一单本来就是干净会话没有弹层）—— 跳过，不算没做成。"
                      "产物开跑前本来就会自己清一次弹层（`_clear_obstructions`）。" % label)
    def _page_ready(self, until=None):
        """**整页**加载完了吗。没好 → 返回盖着整页的那个加载物；好了 → 空串。

        为什么要它（2026-09-18 真站对照，见 `PAGE_READY_SECONDS` 那段）：
        `_covered_by` 只看得见「**我要点的那个东西**被压住了吗」，
        **看不见「整页还没好」** —— 今天每趟就是「元素恰好没被压住」而「整页还烂着」，
        于是它动手、页面不响应、后面 20 步的判据全错。

        `until` 是单调时钟的截止点（`None` = 只量一次，不等）。
        """
        waited = False
        while True:
            got = str(self._ev(_PAGE_LOADER_JS) or "").strip()
            # ⚠️ **只认带 `LOADER|` 前缀的答复**（见 `LOADER_PREFIX` 那段）：
            # 空串 / `null` / 任何别的形状都是「量不出来」—— 一律**不当成在加载**。
            # 当成在加载的代价是**整趟什么都不做**，比漏判严重得多。
            hit = got[1:-1] if (len(got) >= 2 and got[0] == got[-1] == '"') else got
            hit = hit.strip()
            if not hit.startswith(LOADER_PREFIX):
                return ""
            hit = hit[len(LOADER_PREFIX):]
            if until is None or time.monotonic() >= until:
                if waited:
                    self.log.info("[%s] 等了 %.0f 秒，%s 还盖着整页 —— 这一页没加载完",
                                  self.cid, PAGE_READY_SECONDS, hit)
                return hit
            if not waited:
                waited = True
                self.log.info("[%s] 这一页还在加载（%s 盖着整页）—— "
                              "先等它加载完，最多 %.0f 秒", self.cid, hit, PAGE_READY_SECONDS)
            time.sleep(PAGE_READY_POLL)

    def _cover_once(self, selector, frame_id=""):
        """**量一次**：这个选择器指的元素是不是被别的东西盖着。盖着就返回盖它的东西，否则空串。"""
        js = ("var all=document.getElementsByTagName('*'), el=null;"
              "for(var i=0;i<all.length;i++){try{if(all[i].matches(%s)){el=all[i];break;}}catch(e){}}"
              "if(!el) return '';"
              "var r=el.getBoundingClientRect();"
              "if(!r||r.width<=0||r.height<=0) return '';"
              "var x=r.left+r.width/2, y=r.top+r.height/2;"
              "if(x<0||y<0||x>window.innerWidth||y>window.innerHeight) return '';"
              "var hit=document.elementFromPoint(x,y); if(!hit) return '';"
              "if(hit===el||el.contains(hit)||hit.contains(el)) return '';"
              "return 'COVER|'+(hit.tagName||'').toLowerCase()+(hit.id?('#'+hit.id):'')"
              "+'.'+String(hit.className||'').split(' ')[0];")
        got = self._ev(js % json.dumps(selector), frame_id).strip()
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
        `_usable` 那条遮挡闸只在回退链上（要 observe 模型），快路没有 —— 这个函数就是补那一格。

        判据与 `_usable` 同源（**盖着 = 不能动手**），只是换成在页面上直接量：
        取元素中心点，问「那个点上站着的是谁」，接受自己 / 自己的后代 / 自己的祖先
        （文字节点、label 包 input 这类都是「同一个东西」），别的一律算盖着。
        量不出来（元素不在、零尺寸、点在视口外）**返回空串**（= 不加判断）——
        那种情况由 cdp 自己的滚动与报错去说，不在这里替它下结论。
        """
        waited = False
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
            time.sleep(COVER_POLL_SECONDS)

    def _scroll(self, step, target, label):
        """把这一步的元素滚进视口。返回 `(ok, selector_used, level, note, frame_used)`。

        **`cdp scroll` 的位置参数是选择器，不是像素**（`scroll [selector]`）——
        所以这一步重放的是「把**那个元素**滚进视口」，与探索时那次是同一件事。

        三条路各自说清为什么：

        - **主帧的元素** → `self.cdp.scroll(选择器)`：与 click / form 走同一类助手，
          生产重跑那条路「一次 cdp CLI 都不额外起进程」的性质不受影响。
        - **子帧的元素** → 自己起进程带 `--frame-id`。为什么不用助手：`CDPHelper.scroll`
          收不了帧号（它的签名只有 pixels），而**跨源 iframe 里的元素在主帧里根本找不到**
          （那正是这一整族 bug 的样子）。
        - **连元素都没有**（老产物 / 手写产物只写了 `pixels`）→ 退回老行为并在 note 里
          **说出来**：那是一条注定跑不通的命令（像素被当选择器），不许它静默地像「滚过了」。
        """
        selectors = [s for s in (target.get("selectors") or []) if s]
        frame = str(target.get("frame_id") or "")

        def attempt(selector, use_frame):
            """滚一次，返回这条命令的输出（空 = 不判断）。**不许**把空输出当成滚过了。"""
            if not use_frame:
                return self._do("scroll", selector)
            done = self._cdp("scroll", selector, "--frame-id", use_frame)
            if done is None:
                return "Error: 没能让 cdp 滚这个元素（见上面那条日志）"
            out = (done.stdout or "") + (done.stderr or "")
            if done.returncode != 0 and not out.strip():
                return "Error: cdp 滚这个元素没成（退出码 %d，它什么都没说）" % done.returncode
            return out

        for level, selector in enumerate(selectors):
            out = attempt(selector, frame)
            if _ok(out):
                return (True, selector, level,
                        _say("scroll", label, True, level, landing=_landing_say(out)), frame)
            self.log.info("[%s] 第 %d 个选择器滚不动：%s", self.cid, level + 1, selector)

        # 帧号漂了的时候，与 click / form 走**同一条**兜底：把声明里的选择器拿到
        # **活着的帧**里再试（帧号会漂、页面结构不会）。滚动这条原先漏了这一步 ——
        # 真站实测就卡在这儿：`滚不动「Honda」`，而同一个选择器在活帧里是好的。
        if frame:
            for extra, live in enumerate([f for f in self._read_frames() if f != frame]):
                for selector in selectors:
                    out = attempt(selector, live)
                    if _ok(out):
                        return (True, selector, len(selectors) + extra,
                                _say("scroll", label, True, len(selectors) + extra,
                                     landing=_landing_say(out)), live)

        pixels = step.get("pixels")
        if pixels is None:
            return False, "", None, _say("scroll", label, False), frame
        # 老形状：这一步只说了「滚多少像素」，而 cdp 的 scroll 要的是选择器 ——
        # 照发（保住老产物的行为），但 note 里把它是什么说清楚。
        out = self.cdp.scroll(str(pixels))
        return (_ok(out), "", None,
                _say("scroll", label, _ok(out)) + "（这一步只记了滚多少像素，"
                "而 cdp 的 scroll 收的是选择器 —— 这一条老形状的命令多半滚不动）", "")

    def _perform(self, action, step, target, label):
        """做一步。返回 `(ok, selector_used, fallback_level, note, frame_used)`。

        回退链（§5.1b）：声明里的选择器逐个试 → 全挂了就重新 observe 按语义找 → 才算失败。
        最后那一个是**这一步真在哪一帧里做的**（主帧是 `""`）—— trace 里要能看见它，
        不然「这条动作带着帧号跑了吗」只能靠读产物源码去猜。
        """
        if action == "wait":
            self._dly(2, 4)
            return True, "", None, _say("wait", label, True), ""
        if action == "scroll":
            return self._scroll(step, target, label)
        if action == "goto":
            url = step.get("url") or ""
            if not url:
                return False, "", None, "产物写错了：goto 这一步没写 url", ""
            out = self._do("goto", "", url)
            ok = _ok(out)
            note = _say("goto", url, ok)
            # cdp 自己那句话（成功时才留）：**「打开了」这句领先于证据** ——
            # `page_sig` 是 navi 返回那一刻读的，导航可能刚发出（2026-09-18 真站实测：
            # 第 1 步 sig 读到的还是浏览器起始页）。把 cdp 的原话贴在 note 上，
            # 让「它到底等没等到 load」在 trace 里**看得见**。
            if ok and self.goto_echo:
                note += "；cdp 回执：%s" % self.goto_echo
            return ok, "", None, note, ""
        if action not in ("click", "form"):
            return False, "", None, "产物写错了：不认识「%s」这个动作" % (action or "(空)"), ""

        # ★ **动手之前**先问「这一页就绪了吗」（2026-09-18 真站对照：见 `PAGE_READY_SECONDS`）。
        # 整页还在加载就**不许动手** —— 点了也是白点，而且会把后面 20 步的判据全带错。
        # 注意这与下面那个 `_covered_by` 是**两个问题**：那个问元素，这个问整页。
        loading = self._page_ready(until=time.monotonic() + PAGE_READY_SECONDS)
        if loading:
            return False, "", None, (
                "这一页没加载完（%s 盖着整页，等了 %.0f 秒还没走）—— **没动手**："
                "点了也是白点，还会把后面的判据全带错" % (loading, PAGE_READY_SECONDS)), ""

        value, kind = None, "value"
        if action == "form":
            fill = FILLS.get(step.get("fill") or "")
            if not fill:
                raise ValueError("产物写错了：这一步要填「%s」，FILLS 里没有它"
                                 % (step.get("fill") or "(没写 fill)"))
            target = fill.get("target") or target
            label = fill.get("label") or _label(target, step)
            kind = fill.get("kind") or "value"
            value = self._fill_value(fill, step)

        # 这一步在哪一帧里做（主帧 = ""）。账本里没有这个键的老产物照旧跑 ——
        # 空串就是「主帧」，与 CDPHelper.click / form 的默认行为一致。
        frame = str(target.get("frame_id") or "")

        selectors = [s for s in (target.get("selectors") or []) if s]
        # 遮挡预算**按步开一次**（快路 + 重找那条路共用）：被盖住时先等加载蒙版走，
        # 但整步只赔这一笔，不按候选数翻倍（见 COVER_WAIT_SECONDS）。
        cover_until = time.monotonic() + COVER_WAIT_SECONDS
        for level, selector in enumerate(selectors):
            # **快路也要接遮挡判据**（真站实测的那条链）：不看一眼就点，
            # 点到盖着它的东西上（consent 弹层那类）cdp 照样回 ok —— 「点了个寂寞」
            # 被记成做成了。盖着就不点它，换下一个候选；全都被盖着就是这一步没做成。
            cover = self._covered_by(selector, frame, until=cover_until)
            if cover:
                self.log.info("[%s] 第 %d 个选择器指的元素被 %s 盖着 —— 这一下不点"
                              "（点了等于点到盖着它的东西上）", self.cid, level + 1, cover)
                continue
            out = self._do(action, selector, value, kind, frame, label)
            if _ok(out):
                return (True, selector, level,
                        _say(action, label, True, level, landing=_landing_say(out)), frame)
            self.log.info("[%s] 第 %d 个选择器没成：%s", self.cid, level + 1, selector)

        found = self._relocate(target, "field" if action == "form" else "action")
        if found:
            self.log.info("[%s] 声明里的选择器都失效了，重新 observe 找到 %d 个候选",
                          self.cid, len(found))
        for extra, (selector, cand_frame) in enumerate(found):
            level = len(selectors) + extra
            # ⚠️ **这条路原先漏了遮挡判据**（2026-09-18 真站实测逮到）：快路有、它没有，
            # 于是「声明里的选择器全被盖住」时，恰恰是**最该拦的那一下**从这条路上溜过去。
            # 真站日志就是这个形状：`fallback_level: 1` + `ok: true` + `progress: false`
            # —— 点了个寂寞，还报成了做成了。
            # 与快路**同一笔预算**（`cover_until` 在上面开的那一笔），不另开一份。
            cover = self._covered_by(selector, cand_frame, until=cover_until)
            if cover:
                self.log.info("[%s] 重找出来的第 %d 个候选也被 %s 盖着 —— 这一下不点",
                              self.cid, extra + 1, cover)
                continue
            out = self._do(action, selector, value, kind, cand_frame)
            if _ok(out):
                return (True, selector, level,
                        _say(action, label, True, level, landing=_landing_say(out)),
                        cand_frame)
        # 都试完了还是没找到 —— 「点掉同意弹层」这一步是**软**的（见那个 docstring）：
        # 弹层已经不在了就等于这件事已经办完了，不该记成没做成。
        if action == "click":
            handled, why = self._consent_step_handled(label, frame)
            if handled:
                self.log.info("[%s] %s", self.cid, why)
                return True, "", None, why, frame
        return False, "", None, _say(action, label, False), frame

    # ── 一步的执行 ──────────────────────────────────────────

    def _skip_peak(self):
        """被跳过最多的那个状态名（没有就空串）—— 收尾那句话要指出**卡在哪个状态**。"""
        if not self.skipped_states:
            return ""
        return max(self.skipped_states.items(), key=lambda kv: kv[1])[0]

    def _frames_say(self):
        """两个帧表 + 手上观测的新鲜度 —— **两支诊断都要带上它**（地址支与正文支）。

        为什么要它：第八轮那条跳过走的是**地址**那一支，而当时的诊断只把帧表放在正文支，
        于是「手里到底有哪些帧」打不出来，只能靠人从别处推。自证就得两支都自证。
        """
        return ("读页面用的帧：账本 %s ／ 活帧 %s ／ 手上那份观测%s"
                % (list(self.frames) or "（无）", list(self.live_frames) or "（无）",
                   "还是这一页的" if self._model_is_fresh() else "已经过期（换过页）"))

    #: 上一条 `when` **判的那一刻**算出来的解释（空串 = 当时判成立）。见 `_applies`。
    _when_why_cache = ""

    def _when_why(self, when):
        """这条 `when` 为什么不成立 —— **把手上实际有的东西原样摆出来**（给日志与 trace 用）。

        ⚠️ 「这一页不像那个状态」是一句**结论**；读的人要的是**哪条判据不成立**、
        以及**我们手里当时有什么**。
        2026-09-17 第五轮就是靠 `why` 的原文才定位到「子帧地址读不到」那一族 ——
        所以这一轮把**实际看到的地址（全部）**与**实际看到的正文**都摆进来：
        下一个同样的病不该再靠人翻 trace 去猜。
        """
        when = when or {}
        if not when:
            return "这一步没有 when 判据"
        want_url = when.get("url_contains")
        if want_url and not any(want_url in url for url in self._urls()):
            have = " ｜ ".join(self._urls()) or "（一个都没读到）"
            return ("地址对不上：要含「%s」；手里这些地址都不含它：%s；%s"
                    % (want_url, have, self._frames_say()))
        wants = when.get("text_contains") or []
        if wants:
            signature = self.page_signature()
            missing = [w for w in wants if _norm(str(w)).lower() not in signature.lower()]
            if missing:
                # 「实际看到的那段」+ **读页面用的帧** + 手上那份观测的正文 ——
                # 三样一起摆出来：下一步的人不用再猜「是帧没找着，还是页面上真没有」。
                model_text = _norm((self._last_model or {}).get("page_text") or "")
                return ("正文里没有「%s」；页面上**实际看到的**开头那段是：「%s」；%s；"
                        "手上那份观测的正文开头：「%s」"
                        % (str(missing[0])[:60], signature[:200], self._frames_say(),
                           model_text[:120] or "（没有观测）"))
        # ⚠️ 走到这里 = 这两条判据**在【此刻】都成立**（那么 `_matches` 此刻也会判成立）。
        # 能走到这儿只有一种可能：**「判的那一刻」与「说这句话的那一刻」不是同一刻**。
        # 所以 `_applies` 只在与判据同一刻一致时才把这句话记下来（见它的 docstring）。
        return ("判据说成立 —— 但这是**事后**算的：判的那一刻它不成立"
                "（这句话自己就是证据：结论与解释在打架，别把它当成原因读）")

    def _matches(self, when):
        """这条 `when` 现在成不成立（**只看判据**，不做等待）。"""
        if not when:
            return True
        if when.get("url_contains") and not any(when["url_contains"] in url
                                                for url in self._urls()):
            return False
        wants = when.get("text_contains") or []
        if wants:
            signature = self.page_signature().lower()
            if not any(_norm(str(w)).lower() in signature for w in wants):
                return False
        return True

    def _applies(self, when):
        """这一页看着像不像这个状态（防 A/B 变体、防步骤增减）。

        URL 与正文都按**这一页的每一帧**判（`_urls` / `page_signature`）：判据是从
        `observe` 那份**跨帧合并**的模型里来的，只在主帧里比 = 拿两把不同的尺子量同一
        件事 —— 而它失配的方向是**整组步骤被静默跳过**，本项目最贵的那类失败。

        ⚠️ 判成「不像」时**再看一眼**（2026-09-17 真站实测）：上一步是点击/导航时，
        这一页**可能还在加载** —— 不等它，「还没加载完」就被读成「不像」，
        整组步骤静默跳过（某趟 34 步里 31 步这么没的，出声之后一眼看出来：
        「正文里没有「The listings featured…」」而那一页马上就有那句话）。
        """
        if self._matches(when):
            self._when_why_cache = ""
            return True
        # ⚠️ 2026-09-18 真站实测的根因：`readyState != 'complete'` **不等于**「判据不成立」
        # （广告/埋点一直在下载，正文早就好了）。原来写成 `if self._wait_ready(...)`——
        # 等不到就**不再看一眼**直接判「不像」，整组步骤被跳过；而**随后**那句 why
        # 读到的是已经加载完的页面，于是诚实地说「url 与正文都对上了却判成不像」——
        # **结论与解释自相矛盾，而且是它自己指出来的**。
        # 现在：等完**无论 readyState 说什么，都重判一次**。
        self._wait_ready(timeout=WAIT_READY_SECONDS)
        ok = self._matches(when)
        #: ⚠️ why 必须在**判的这一刻**算死，不许事后重算 ——
        #: 事后再看一眼时页面已经变了，说出来的话就跟结论打架。
        self._when_why_cache = "" if ok else self._when_why(when)
        return ok

    def _succeeded(self):
        signature = self.page_signature().lower()
        return any(_norm(t).lower() in signature for t in SUCCESS_TEXTS if t)

    def _run_step(self, index, step):
        """走一步，返回 (ok, progress)。trace 的一行也在这一步里落。"""
        action = (step.get("action") or "").strip()
        target = step.get("target") or {}
        label = _label(target, step)

        before_path, shot_before, signature = None, None, ""
        self.progress_why, self.shots_why = None, None
        if self.tracing:
            signature = self.page_signature()
            model = self._observe()
            before_path = self._snapshot_path(model) if model else None
            shot_before = self._shot(index, "before")
            if not before_path:
                # 动作前的快照没落成 = 这一步的 progress 判不了：把原因写清楚，
                # 别留一个光秃秃的 null（那会被读成「没推进」）。
                self.progress_why = self.observe_why or "动作前没能留下页面快照，没得比"

        try:
            ok, selector, level, note, frame = self._perform(action, step, target, label)
        except Exception as exc:                       # noqa: BLE001
            # **产物自己在这一步炸了** —— 必须落 trace（自测只读 trace：不落它，
            # 「产物死了」会被读成「走完了 N 步、0 跳过」。2026-09-17 真站实测踩到过：
            # 第 11 步抛 TypeError，trace 里连 `"step": 11` 那行都没有，
            # 而 `11-before.png` 明明在）。
            self._trace({
                "step": index, "action": action, "target": label,
                "selector_used": "", "fallback_level": None, "frame_id": "",
                "ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "url": self._url(),
                "page_sig": (signature or "")[:PAGE_TEXT_CHARS],
                "note": "产物自己在第 %d 步出错了（%s: %s）—— 这一步没做成，后面也不做了"
                        % (index, type(exc).__name__, exc),
            })
            self.log.error("[%s] 产物自己在第 %d 步出错了：%s: %s",
                           self.cid, index, type(exc).__name__, exc)
            raise
        try:
            self._dly()
            progress = self._diff(before_path) if before_path else None
        finally:
            if before_path:
                try:
                    os.unlink(before_path)          # 快照只是给 diff 用的，不留给谁看
                except OSError:
                    pass

        if progress is False:
            note += "；页面没有变化"
        elif progress is None and self.tracing:
            note += "；没算出页面有没有变化"
            if self.progress_why:
                note += "（%s）" % self.progress_why

        if self.tracing:
            keep = (self.shots == "all") or (not ok) or (progress is False and action in DIFF_JUDGES)
            shot_after = self._shot(index, "after") if keep else None
            if not keep:
                self._unlink(shot_before)
                shot_before = None
            self._trace({
                "step": index,
                "action": action,
                "target": label,
                "selector_used": selector,
                "fallback_level": level,
                # 这一步真在哪一帧里做的（主帧 = ""）——「帧内的点击有没有带上帧号」
                # 这件事，读 trace 就能看见，不用去读产物源码。
                "frame_id": frame,
                "ok": ok,
                "progress": progress,
                # 判不出来时**带上为什么**（「没算出有没有推进」与「没有推进」是两件事）
                "progress_why": self.progress_why if progress is None else None,
                "url": self._url(),
                "page_sig": (signature or self.page_signature())[:PAGE_TEXT_CHARS],
                # **尾部也留一段**（2026-09-17 第十轮）：`page_sig` 只留前 600 字，
                # 而拼起来的正文里**主帧那段在前** —— 子帧（问卷）那几句常常落在 600 之后，
                # 于是「trace 里搜不到问卷正文」会被误读成「判据没读到它」。
                # ⚠️ **加法**：老键一个字没动，只是多了这两个（读 trace 的下游不受影响）。
                "page_sig_tail": self.page_signature()[-PAGE_TAIL_CHARS:],
                "success_in_page": self._succeeded(),
                "shot_before": shot_before,
                "shot_after": shot_after,
                "shots_why": self.shots_why,     # 没落成图时说明原因
                "note": note,
            })
        self.log.info("[%s] 第 %d 步：%s", self.cid, index, note)
        return ok, progress

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        """走完 STATES。成功 True；失败/早停/按 --stop-at 停下都 False（不谎报成功）。"""
        if not STATES:
            self.log.error("[%s] 产物里一步都没有", self.cid)
            return False
        total = sum(len(state.get("steps") or []) for state in STATES)
        self.log.info("[%s] %s 开始：%d 步；%s", self.cid, SITE, total,
                      "调试模式（trace=%s，stop-at=%s）" % (self.trace_path, self.stop_at or "-")
                      if self.tracing else "生产重跑（不截图、不 observe、不落 trace）")
        if self.tracing:
            try:
                with open(self.trace_path, "w", encoding="utf-8"):
                    pass            # 每跑一次一份新 trace：Console 读的是**这一次**
            except OSError as exc:
                self.log.warning("[%s] trace 打不开：%s", self.cid, exc)
                self.trace_path = None
                self.tracing = False
            else:
                # 这次调试要用到的 cdp 能力，缺了先**说清**（重跑路径上不探：§13）
                self._check_capabilities()

        # 开跑之前先看有没有**挡路的同意弹层**并点掉（见 _clear_obstructions 的 docstring：
        # 生产每单都是新窗口 → 每单都会遇到它，而账本里通常没有这一步）。
        self._clear_obstructions()

        index = 0
        try:
            for state in STATES:
                steps = state.get("steps") or []
                if not steps:
                    continue
                name = state.get("name") or SITE
                applies = self._applies(state.get("when"))
                for step in steps:
                    index += 1
                    self.step = index
                    if not applies:
                        # **跳过必须出声**（2026-09-17 加，与「没做成不许说做成」同一条规矩）：
                        # 「`when` 不成立就整组静默跳过」是这套产物最贵的一类失败 ——
                        # 跑完什么都没做，日志里却只有一句「走完了 N 步也没见到成功文案」，
                        # 读的人根本看不出是**判据把整组步骤吞了**。
                        # 所以：① 日志里说明**为什么**（哪一条判据不成立）；
                        # ② trace 里也落一行（`skipped: true`）—— 自测/Console 读的是 trace；
                        # ③ 计入 `self.skipped`，最后那句总结里报出来。
                        # ⚠️ 用 `_applies` 在**判的那一刻**算死的那句，不在这里重算 ——
                        # 重算会读到另一个页面，说出来的话跟结论打架（2026-09-18 真站实测）。
                        why = self._when_why_cache
                        self.skipped += 1
                        self.skipped_states[name] = self.skipped_states.get(name, 0) + 1
                        self.log.info("[%s] 第 %d 步**跳过**：这一页不像「%s」那个状态（%s）"
                                      "；页面上现在写着「%s」", self.cid, index, name, why,
                                      (self.page_signature() or "")[:80])
                        self._trace({"step": index, "action": (step.get("action") or ""),
                                     "skipped": True, "state": name, "why": why,
                                     "note": "第 %d 步跳过：这一页不像「%s」那个状态（%s）"
                                             % (index, name, why)})
                        continue
                    self._rpt_if_moved(name)
                    if (step.get("action") or "") == "goto":
                        # ① 先**等这一页加载完**再判 `when` —— 不等的话，紧接着的判据会把
                        #    「还没加载完」读成「这一页不像那个状态」，整组步骤静默跳过
                        #    （真站实测：33 步里 32 步这么没的）。
                        # ② 换页也是**同意弹层会出现**的时刻，再看一眼弹层。
                        self._wait_ready()
                        self._clear_obstructions(why="这一次导航")
                    ok, progress = self._run_step(index, step)

                    if self._succeeded():
                        self.log.info("[%s] 成功：页面上见到了成功文案", self.cid)
                        self._rpt("success")
                        return True
                    if ok:
                        self.stuck = 0
                    else:
                        self.stuck += 1
                        if self.stuck >= STUCK_LIMIT:
                            self.log.error("[%s] 连着 %d 步没做成，收摊（早停，不磨完全程）",
                                           self.cid, self.stuck)
                            self._rpt("stuck")
                            return False
                    if progress is False and (step.get("action") or "") in DIFF_JUDGES:
                        self.stalled += 1
                        if self.stalled >= STUCK_LIMIT:
                            self.log.error("[%s] 连着 %d 步点了页面都没动，收摊（原地打转）",
                                           self.cid, self.stalled)
                            self._rpt("stalled")
                            return False
                    elif progress is True:
                        self.stalled = 0

                    if self.stop_at and index >= self.stop_at:
                        self.log.info("[%s] 按 --stop-at 停在第 %d 步；浏览器保持原状，不关",
                                      self.cid, index)
                        self._trace({"stopped_at": index})
                        return False

            if self._succeeded():
                self._rpt("success")
                return True
            # **没见到成功文案就必须大声说「我没到」**，并说清停在哪、跳过了多少 ——
            # 「走完了 N 步」这句话本身**不是**一个结论（它听着像「跑完了」）。
            self.log.error(
                "[%s] **没走到成功**：%d 步里真做了 %d 步、被 when 判据跳过 %d 步%s；"
                "页面上**从头到尾没有出现过成功文案**（要认的那段：%s）……",
                self.cid, total, total - self.skipped, self.skipped,
                ("（跳过最多的那个状态是「%s」）" % self._skip_peak()) if self.skipped else "",
                " / ".join(SUCCESS_TEXTS[:2]))
            # ⚠️ 这一句**只进日志、不进 trace**：trace 的每一行都是「一步」
            # （下游按 `line["ok"]` 读它，加一行没有 ok 的会把它读崩 ——
            # `tests/test_template.py` 的截图那条就是这么读的）。
            # 「跳过」那几行**是**步（带 `skipped: true`、不带 ok），所以它们在。
            self._rpt("no_success")
            return False
        except Exception as exc:
            # 这一路也要留痕：抛在 `_run_step` 之外时（例如准备阶段），
            # 光靠日志同样会被读成「跑完了」。
            self._trace({"step": self.step, "action": "", "ok": False,
                         "error": "%s: %s" % (type(exc).__name__, exc),
                         "note": "产物自己出错了（%s: %s）—— 这一趟**没跑完**"
                                 % (type(exc).__name__, exc)})
            self.log.error("[%s] 出错：%s", self.cid, exc)
            return False


def main():
    p = argparse.ArgumentParser()
    # 调试契约（§5.1c）的参数注册在生产那 5 个**之前** —— 这是为了让下面三行
    # 与 forms/sites/*.py 一字不差（包括 `a = p.parse_args()` 挤在 --task-id 那行）
    p.add_argument("--trace", default=""); p.add_argument("--stop-at", type=int, default=0)
    p.add_argument("--shots", choices=("failed", "all"), default="failed")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--ws-url", required=True); p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True); p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default=""); a = p.parse_args()
    log = setup_logger(SITE); log.setLevel(a.log_level)
    # --delay 给的是**定值**（≤0 = 不给 = 基线那套随机停顿）；Filler 收的是区间，
    # 所以定值要摊成 (x, x)。扰动自测的第 3 遍走这条路 —— **不改写产物源码**。
    f = Filler(a.ws_url, a.form_file, a.correlation_id, a.task_id,
               trace=a.trace or None, stop_at=a.stop_at or None, shots=a.shots,
               delay=(a.delay, a.delay) if a.delay > 0 else DELAY_RANGE,
               no_report=a.no_report)
    sys.exit(0 if f.run() else 1)

if __name__ == "__main__": main()
''')
