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


def _frame_of_element(element, fallback=""):
    """重新 observe 找到的这个候选**在哪一帧**（读它自己的 `frame_path`）。

    与 `agent/browser_agent.py:_frame_id_of_path` 同一套判据（那边写账本，这边读回来）：

    - `["main"]` / 空 → `""`（主帧）
    - `["main", "<frameID>"]` → 那个 frameID
    - 读不出来（没有这个键 / 嵌了两层以上）→ `fallback`（`target` 里那一帧）

    第三种为什么退回 `fallback` 而不是取最深那一段：`cdp` 换点击坐标只补**目标帧的
    owner `<iframe>` 在主帧里**那一个原点，中间几层没人补 —— 猜一个更深的帧号不是
    「够不着」，是**按错的坐标点了一下**。
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
        line = "往下滚了一屏" if ok else "滚不动"
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
        self.stalled = 0       # 连续「点了但页面没动」的步数（另一种原地打转）
        self._reported_url = ""
        self.missing = set()   # 这个 cdp 没有的命令（认出来一次就够：之后不再白跑、不再喊）
        self.commands = None   # 它的命令表（`--help` 数的）；None = 还没探过/探不出来
        self.probed = False
        self.observe_why = None    # 最近一次「重新看页面」没成的原因（人话）
        self.progress_why = None   # 最近一步「为什么 progress 是 null」（人话）
        self.shots_why = None      # 最近一步截图没落成的原因（人话）
        #: 这条流程**动过手的帧**（STATES 里出现过的 frame_id）。产物读页面时也读它们 ——
        #: 见 page_signature / _urls 的 docstring：动手在子帧、读页面却只看主帧，
        #: 那两件事量的根本不是同一页。
        self.frames = _frames_in_states(STATES)

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
        return self._ev("return window.location.href;").strip().strip('"').strip("'")

    def _urls(self):
        """这一页的地址：**主帧的 + 这条流程动过手的每一帧的**（去重、保序）。

        为什么不是一个：流程活在跨源 iframe 里时，账本里那套判据的 URL **就是子帧的**
        （`cdp observe` 报的 `url` 在这种页面上是子帧的 —— 实测 gowizard：19 个状态里
        16 个的 `url_contains` 是 `chameleon-…`，而主帧的地址从头到尾是
        `www.gowizard.com/auto/…`）。只拿主帧比 → 那 16 组步骤**静默跳过**
        （`_applies` 返回 False 是不出声的）：产物看着跑完了，其实一步没走。
        """
        out = []
        for url in [self._url()] + [self._frame_url(fid) for fid in self.frames]:
            if url and url not in out:
                out.append(url)
        return out

    def _frame_url(self, frame_id):
        """某一帧现在的地址（读不到就空串 —— 读不到不是「它是空的」，是没法判）。"""
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
        parts += [_norm(self._ev(_PAGE_TEXT_JS, fid)) for fid in self.frames]
        return " ".join(p for p in parts if p)

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
            return json.loads(done.stdout)
        except ValueError:
            self.observe_why = "cdp 重新看这一页给的答复看不懂（不是 JSON）"
            return None

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
        """
        model = self._observe()
        if not model:
            return []
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
                    key = _norm(element.get("label") or element.get("hint") or "").lower()
                    if not want_label or want_label not in key:
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
        for element in hits:
            frame = _frame_of_element(element, fallback)
            for selector in [element.get("selector")] + list(element.get("alternates") or []):
                if selector and (selector, frame) not in out:
                    out.append((selector, frame))
        return out

    # ── 动作 ────────────────────────────────────────────────

    def _do(self, action, selector, value=None, kind="value", frame_id=""):
        """一个动作只走这一条路：cdp 命令（规格 §5.2：动作一律走 cdp，不手拼 JS）。

        `frame_id` 交给 `CDPHelper.click / form` 的**同名参数**（`forms/common.py:146` /
        `:202` 本来就收它，命令行上是 `--frame-id`）。元素在跨源 iframe 里时，**同一个
        选择器在主帧里命中 0** —— 不带这一帧，重放每跑一遍都会「页面上没找到」，
        而这件事在账本里原本看不出来（那份产物没有「帧」这个概念）。
        """
        if action == "click":
            return self.cdp.click(selector, frame_id=frame_id or "")
        if action == "form":
            if kind == "check":
                return self.cdp.form(selector, check=str(value).lower(),
                                     frame_id=frame_id or "")
            if kind == "select":
                return self.cdp.form(selector, select=str(value), frame_id=frame_id or "")
            return self.cdp.form(selector, value=str(value), frame_id=frame_id or "")
        if action == "scroll":
            return self.cdp.scroll(str(value if value is not None else "400"))
        if action == "goto":
            done = self._cdp("navi", value)
            ok = done is not None and done.returncode == 0
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
        if kind == "dob":
            return "%02d/%02d/%d" % (random.randint(1, 12), random.randint(1, 28),
                                     random.randint(1970, 1995))
        if kind == "password":
            return "Test%d!" % random.randint(1000, 9999)
        raise ValueError("产物写错了：不认识这个随机值类型「%s」" % kind)

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
            out = self._do("scroll", "", step.get("pixels", "400"))
            return _ok(out), "", None, _say("scroll", label, _ok(out)), ""
        if action == "goto":
            url = step.get("url") or ""
            if not url:
                return False, "", None, "产物写错了：goto 这一步没写 url", ""
            out = self._do("goto", "", url)
            return _ok(out), "", None, _say("goto", url, _ok(out)), ""
        if action not in ("click", "form"):
            return False, "", None, "产物写错了：不认识「%s」这个动作" % (action or "(空)"), ""

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
        for level, selector in enumerate(selectors):
            out = self._do(action, selector, value, kind, frame)
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
            out = self._do(action, selector, value, kind, cand_frame)
            if _ok(out):
                return (True, selector, level,
                        _say(action, label, True, level, landing=_landing_say(out)),
                        cand_frame)
        return False, "", None, _say(action, label, False), frame

    # ── 一步的执行 ──────────────────────────────────────────

    def _applies(self, when):
        """这一页看着像不像这个状态（防 A/B 变体、防步骤增减）。

        URL 与正文都按**这一页的每一帧**判（`_urls` / `page_signature`）：判据是从
        `observe` 那份**跨帧合并**的模型里来的，只在主帧里比 = 拿两把不同的尺子量同一
        件事 —— 而它失配的方向是**整组步骤被静默跳过**，本项目最贵的那类失败。
        """
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
                        self.log.info("[%s] 第 %d 步跳过：这一页不像「%s」那个状态",
                                      self.cid, index, name)
                        continue
                    self._rpt_if_moved(name)
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
            self.log.error("[%s] 走完了 %d 步也没见到成功文案", self.cid, total)
            self._rpt("no_success")
            return False
        except Exception as exc:
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
