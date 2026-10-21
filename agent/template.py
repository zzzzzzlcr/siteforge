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
import ast
import hashlib
import json
import pathlib
import pprint
import re
from string import Template

__all__ = ["render", "PROVENANCE_KEYS"]

#: 规格 §5.3 的元数据键。缺的补 None（「不知道」），**不编内容**。
#: `fingerprint`（2026-09-28 加）：**探路那个窗口量下来的**视口/DPR/UA —— 产物开跑时拿它
#: 跟当下的窗口比。为什么它必须在**这一张表**里：这张表是「产出时照抄哪几格」的**唯一**名单，
#: 不在名单里的格生成时就丢了（产物里那一格恒为空，而那正是这一格要治的病）。
PROVENANCE_KEYS = ("generated_at", "generator", "env", "fingerprint", "platform",
                   "selftest", "source")
#: ★ 2026-09-29（交接《骨架还缺什么》§2）：**这一版骨架是哪一份**。
#:
#: 为什么非有不可：产物里一直只有 `PROVENANCE`（生成时间 / 生成器 / 运行库指纹），
#: **没有「骨架版本」这一格** ⇒「线上这份产物比仓库里的骨架落后多少」没人答得上来。
#: 代价是实测的：重渲一版 travelandleisure，**一口气多带了 8 个方法**
#: （`_follow_new_tab` / `_ALIASES` / `_page_trouble` / `elementsFromPoint` 那一批 09-28 的活儿）
#: —— 也就是说旧产物在线上继续跑、骨架在仓库里往前走，**中间没有对账**。
#: 现在：每个产物里带一格 `SKELETON_SHA`，`skeleton_drift()` 拿它跟当前骨架一比就有答案。
#: ⚠️ 认的是**这一份文件**的字节（`agent/template.py`）—— 骨架的每一处改动都在它里面，
#: 而站点数据（STATES/FILLS/PROVENANCE）**不算骨架**，所以不能拿产物自己的 hash 顶替。
def _skeleton_sha() -> str:
    """当前骨架的指纹（`agent/template.py` 的 sha256 前 16 位；读不出来给空串 —— **不编**）。"""
    try:
        return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:16]
    except OSError:                      # 读不出来 ⇒ 空串：产物那一格会是空，体检会说「答不上来」
        return ""


SKELETON_SHA = _skeleton_sha()

#: 产物里那一格叫什么（`skeleton_drift` 与体检脚本都按这个名字找）。
SKELETON_FIELD = "SKELETON_SHA"


def skeleton_drift(src: str) -> dict:
    """这份产物**落后当前骨架多少** → `{"have","now","stale","say"}`。

    ⚠️ **读不出那一格时 `stale` 是 `None`，不是 `False`** ——「答不上来」与「不落后」是
    两件事，混成一个就会把「老产物」显示成「没问题」（本项目栽过很多次的老病）。
    手写的那族 py（`forms/sites/warthunder.py` 那类）本来就没有这一格，它们走的正是这一支。
    """
    have = ""
    try:
        tree = ast.parse(str(src or ""))
    except (SyntaxError, ValueError):
        tree = None
    for node in (tree.body if tree is not None else []):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id == SKELETON_FIELD for t in targets):
            continue
        try:
            have = str(ast.literal_eval(node.value) or "")
        except Exception:                # noqa: BLE001 —— 动态构造 = 读不出来
            have = ""
        break
    now = SKELETON_SHA
    if not have:
        return {"have": "", "now": now, "stale": None,
                "say": "这份产物**没记骨架指纹**（没有 `%s` 那一格）：它是哪一版骨架渲染出来的"
                       "**答不上来** —— 这不等于「它跟当前骨架一样」。" % SKELETON_FIELD}
    if not now:
        return {"have": have, "now": "", "stale": None,
                "say": "这份产物记的是骨架 `%s`，**当前骨架的指纹读不出来**（模板文件读不了）"
                       "—— 对不上对不上都答不上来。" % have}
    if have == now:
        return {"have": have, "now": now, "stale": False,
                "say": "这份产物就是**当前骨架**渲染的（`%s`）✓。" % have}
    return {"have": have, "now": now, "stale": True,
            "say": "⚠️ 这份产物是**旧骨架**渲染的：它记的是 `%s`，现在是 `%s` —— "
                   "骨架在它之后改过（新产物会带上那些改动，比如每步前兜同意弹层、"
                   "跟新 tab、字段别名表）。要它跟上就得**重渲一版**。" % (have, now)}

#: 产出的 py 一律把这个常量当默认名（诊断/日志里认它）
DEFAULT_STUCK_LIMIT = 3


#: 4 字节以上的码位（emoji / 国旗那一类）—— **后台那个库存不了**（它是 `utf8`，不是
#: `utf8mb4`）。实测 2026-09-25 一次真上传：后端 500，它自己的 SQL 报
#: `General error: 1366 Incorrect string value: '\xF0\x9F\x87\xAC…'`（那是 🇬🇧 的前 4 个字节），
#: 插 `form_configs.script_source` 那一列时炸的 ⇒ 服务这层只能如实说「不知道写没写成」。
_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")


def _no_astral(literal: str) -> str:
    """字面量里的 4 字节字符 → 写成 `\\UXXXXXXXX` **转义**：源码里不再有 4 字节的字节，
    而运行期那串字符**一个都不变**。

    为什么不删掉：那些字符是**页面上真有的字**（这次那面 `🇬🇧` 就是电话格旁边的旗，
    探路从页面上抄回来的 ✓）—— 删了等于把抄回来的东西改了；转义不丢信息，
    而问题只在「文件里有没有 4 字节的字节」，转义正好治这个。
    ⚠️ 只管**字面量**（`_lit` 的产物）：文件别处（注释/骨架）本来就没有这类字符。
    ⚠️ 上传那一侧另有一道闸（`service._what_to_upload`）：源码里真还有 4 字节字符时
    当场说清是哪一行，而不是让后端 500 一轮、再猜「写没写成」。
    """
    return _ASTRAL.sub(lambda m: "\\U%08X" % ord(m.group(0)), literal)


def _lit(obj) -> str:
    """渲染成 **Python 字面量** —— 不是 JSON。

    `pprint.pformat` 出的是 `True` / `False` / `None`，而 `json.dumps` 出的是
    `true` / `false` / `null`。后者在 Python 里语法合法（所以 `ast.parse` 抓不到），
    import 时才 `NameError` —— 计划一 py_emitter 的断口 1 就是这个。
    """
    return _no_astral(pprint.pformat(obj, width=96, indent=2, sort_dicts=False))


def _success_texts(success_text) -> list:
    """成功判据 → 产物里那份 `SUCCESS_TEXTS`（**剥掉「出现 / 显示」这类说明壳**）。

    ★ 2026-09-22 真事（`lp.candidate.py` 第 36 行）：判据那格写的是「**出现** Tailor Your Cover」，
    产物里就**原样**写成了 `SUCCESS_TEXTS = ['出现 Tailor Your Cover']` —— 而产物自己的
    `_succeeded()` 是**字面子串**比 ⇒ **永远匹配不上** ⇒ 真站上明明成了却报「没走到成功」
    （自测 trace 里第 12 步之后就没有成功那行，用户看到的正是这个）。

    ⚠️ **剥壳的那一处实现只有一份**（`browser_agent.strip_criterion_head` / `wanted_texts`，
    判据那一侧用的是同一份）—— 两处各写一遍，就是「判据说见着了、产物说不认识」那对老病。
    为什么在**函数里**导入：这个模块在 import 期要尽量轻（`service` 也只是在函数里导入重家伙），
    而 `browser_agent` 会拉起 llm/tools —— 那是**渲染那一刻**才需要的东西。
    """


    from agent.browser_agent import wanted_texts     # noqa: PLC0415 —— 见 docstring

    texts = wanted_texts(success_text)
    if not texts:
        # 没有成功判据的产物会「跑到底然后说成功」—— 本项目最忌讳的那类谎。
        # ⚠️ 说完就走那条也在这儿：判据只写了「出现」两个字 ⇒ 剥完什么都不剩 ⇒ 一样是「没有判据」。
        raise ValueError("success_text 不能空（也不能只写「出现 / 显示」这种动词）："
                         "没有成功判据的产物会跑到底再说自己成功")
    return texts


def _json_lit(obj) -> str:
    """一段 JSON 文本，当**Python 字面量**用（`_lit` 的 JSON 兄弟）。

    ⚠️ **不许**用 json 的默认 `ensure_ascii=True`：它把 4 字节字符写成**代理对**
    （`"\\ud83e\\udde9"`），而 **Python 读这个字面量时不会把代理对合成一个字** ——
    它拿到的是两个**孤立代理对** ⇒ 运行期只要有一次 `subprocess` 传参或写文件就
    `UnicodeEncodeError: surrogates not allowed`。
    2026-09-26 真事：产物在 `common.CDPHelper.eval`（读页面那一步）直接退，退出码 1，
    而那两个字只是我写在 JS 注释里的一个 emoji ✗。
    ⇒ `ensure_ascii=False` 出原字符，再由 `_no_astral` 把 4 字节字符写成 `\\UXXXXXXXX`
    （Python 认、运行期还是那个字符、文件全 ASCII）。
    """
    return _no_astral(json.dumps(obj, ensure_ascii=False))


def _success_urls(success_urls) -> list:
    """判据的**网址那一格** → 产物里那份 `SUCCESS_URLS`（去空、去重、保序）。

    ★ 2026-09-24（用户点名「那不能一样加个 success_url」）：老脚本本来就常判**网址**
    （`japansdates` 的 `/wizard`、`warthunder` 的 `#/confirm`），而 `SUCCESS_TEXTS` 那一格
    是「页面上出现的**文字**」—— 网址里那一截**不在正文里**（那族站换页时正文可能一个字不变）
    ⇒ 把 `/wizard` 塞进文字那格就是「**永远认不出成功**」。

    ⚠️ **不剥说明壳**（`strip_criterion_head` 那一套是给「页面上会出现哪段文字」那格用的）：
    这里要的就是网址里的一截，`/wizard` 本来就该是它那个样子。
    ⚠️ 剥壳那一处实现仍然只有一份（`browser_agent.wanted_urls`）—— 判据那一侧用的是它，
    产物这一侧也用它：两处各写一遍就会出现「判据说见着了、产物说不认识」那对老病。
    """
    from agent.browser_agent import wanted_urls     # noqa: PLC0415 —— 与上面同一个理由

    return wanted_urls(success_urls)


#: 框架**生成的** id —— 带渲染序号，重渲染就换号（★ 2026-09-23 用户实测指出）。
#: ⚠️ 与 cdp 那边是**同一份判据**（`tools/cdp/internal/observe.go` 的 `GENERATED_ID`，
#: 它管的是稳定性评级；这里管的是产物里候选的先后）。两处对不上就会出现
#: 「评级说它不稳、产物还是先试它」——那种不一致正是要躲的东西。
_GENERATED_ID = re.compile(r"^#(?:__[A-Za-z]+__\d+$|:r[0-9a-z]+:|mui-\d|radix-"
                           r"|headlessui-|downshift-|react-select-)", re.I)

#: ★ 2026-09-28（缺陷单《产物判据跨元素拼接》§四.1，真站实测）：「自造 id 会漂」。
#: georgiapower 同一天量到的对照（探路记的 → 今天实际的）：`#input-167` → `input-163`、
#: `#combobox-input-182` → `combobox-input-168`、`#combobox-input-182-0-182` → `…-168-0-168`；
#: 而 `input[name="city"]` / `input[aria-label="Search for Address"]` **全对上了**。
#: ⇒ 「**以 `-数字` 结尾的 id**」与上面那一族同一条待遇：**只能当兜底**。
#: ⚠️ 代价说清楚：`#step-2`、`#section-3` 这种**人写的**序号 id 也会被一起贬到最后一档 ——
#: 它仍然**留着**（页面真没给别的地址时它是唯一够得着的路），只是不再排第一。
_RENDER_INDEX_ID = re.compile(r"^#[-\w]*-\d+$")

#: ★ 2026-10-13（用户裁定，lovesac `job-0ccb5c33624d` 现场）：**「`xxx+数字` 不是不允许，是最后的优先级」**。
#: 上面这条 `_RENDER_INDEX_ID` 是按 georgiapower 那一族的实测写的（`#input-167` → `#input-163`：
#: 那族的数字是**每次渲染递增的全局计数**）。而服务端渲染的表单（Wufoo 这类）给的是
#: `id="Field10-1"`：`Field10` 是**表单定义里的字段号**、`-1` 是这一格里的第几个输入 ——
#: 【我量的·2026-10-13】三次不同的探路会话（iframe 实例号每次都换：`A3D3…`/`4E01…`/`DFB8…`）
#: 拿到的**是同一串 id**，它不随渲染漂。两者在字符串上长得一模一样。
#: ⇒ **这一族不再当硬闸**（`lint._rule_i` 那份「只有它一条就不许交」里撤了）：
#:   允许交付，**只保留**这里的降档 —— 「先试谁」它排最后（页面真没给别的地址时它是唯一够得着的路）。
#: ⚠️ 代价：真会漂的那种（georgiapower 实测）也不再被打回，会在真站上以「页面上没找到」失败。

#: `:nth-of-type` / `:nth-child` 的**跳数**：跳数越多越像「位置」而不像「身份」。
#: 阈值与 cdp 那边**同一份判据**（`tools/cdp/internal/observe.go` 的 `stability()`：
#: hops 0 且带锚点 = high、≤3 = medium、**>3 = low**）—— 两处对不上就会出现
#: 「评级说它脆、产物还是先试它」。实测那个换分辨率全挂的 9 跳长链就在这一档。
_HOPS_LOW = 3
_HOPS = re.compile(r":nth-of-type|:nth-child")


def _selector_rank(selector) -> int:
    """产物里试这条选择器的**顺序档**：0 稳（先试）／1 一般／2 兜底（最后试）。"""
    text = str(selector or "")
    if _GENERATED_ID.match(text) or _RENDER_INDEX_ID.match(text):
        return 2
    return 2 if len(_HOPS.findall(text)) > _HOPS_LOW else 1

def _stable_first(obj):
    """每处 `selectors` 重排一遍：**稳的排前面、脆的排最后**（见 `_selector_rank`）。

    ★ 2026-09-23，用户实测指出的第一条（原话：「用的这个 id？会有问题吧，感觉这种 ID
    随时会变」）：`#__BVID__408` 是 Vue 的**渲染序号** —— 同一个框重渲染就换号
    （真站实测：这一趟 `#__BVID__42`、下一趟 `#__BVID__429`）。产物按声明顺序一条条试，
    于是**先用**它；而「第 408 个渲染出来的控件」不是这个元素的身份，它是**位置**。

    ★ 2026-09-28（缺陷单《产物判据跨元素拼接》§四.1）补上同族的另外两族：
    以 `-数字` 结尾的 id（`#input-167` 那种框架自造号）与**长 `nth-of-type` 链**
    （实测 9 跳那条：**换个分辨率全挂**）—— 一条判据管三族，都在 `_selector_rank` 里。

    ⚠️ `sorted` 是**稳定**的：不是那三族的，相对顺序一个都不动 ——
    那个顺序是探索那一趟量的，别顺手重排它。
    ⚠️ 脆的仍然**留着**（排在最后）：页面真没给别的地址时，它是唯一够得着的路
    —— 实测有一格（`telephoneNumber`）就只有它一个候选。
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key == "selectors" and isinstance(value, list):
                out[key] = sorted(value, key=_selector_rank)
            else:
                out[key] = _stable_first(value)
        return out
    if isinstance(obj, list):
        return [_stable_first(x) for x in obj]
    return obj


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


def _steps_must_be_findable(states) -> list:
    """**认不出来的那一步**不许进产物 —— 否则**当场拒**。

    ★ 2026-09-24（真站实测逼出来的）：`solarincentives` 那趟探路**误点了一个没名字的元素**
    （真站 trace 里那一步写着 `selector_used: ""` ✓）⇒ 账本把它记成一步 ⇒ 产物**忠实翻译**它 ⇒
    自测卡在第 8 步「页面上没找到」⇒ **三版稿撞同一堵墙**（那一步从根上执行不了，改写法救不了）。
    ⇒ 与门口那条「没人说成功判据就拒」同一条规矩：**这种步骤不该到得了产物** ✓ 而且要说**第几步** ✓。

    ★★ 判据是**「既没有选择器、也没有意图」**（用户 2026-09-24 拦下来的那一下）：
    仓里有一整族产物是「声明里**一条选择器都不给**、全靠运行期 `observe` 给的池子认」
    （`_one_click_states(**target_extra)` 那条用例的原话：「声明里的选择器全挂、observe 只
    给出 `actions` 这一个池子」✓），随机选答案那族也走这条路 ⇒ **有意图的一步一律放行** ✗。
    只砍「四样全空」——今天那个「没写名字的元素」就是四样全空 ✓。

    ⚠️ 只判**点下去的那一类**（`click`）：`goto` 按 url 走 ✓、`observe` 不看元素 ✓、
    **`scroll` 按像素走** ✓、**`form` 的落点在 `FILLS` 里**（那一步自己不带 target ✓）✓ ——
    实测里 22 条用例就是被「把这四类也算上」砍红的 ✗。
    """
    for index, state in enumerate(states or []):
        for step in (state or {}).get("steps") or []:
            act = str((step or {}).get("action") or "")
            if act != "click":
                continue
            target = (step or {}).get("target") or {}
            if [s for s in (target.get("selectors") or []) if str(s or "").strip()]:
                continue
            if any(str(target.get(k) or "").strip() for k in ("text", "role", "near")):
                continue                       # ★ 有意图 ⇒ 交给运行期认（那一族老写法）
            raise ValueError(
                "第 %d 步（%s）**认不出来**：`target.selectors` 是空的、`text`/`role`/`near`"
                "也全空 —— 到了真站上只会「找不到」，而后面几版稿都会撞同一堵墙。这一步要回到"
                "探路那侧弄明白（它到底是哪个元素），别把认不出来的一步写进产物。" % (index + 1, act))
    return states


def render(site, success_text, states, fills, provenance, success_urls=None, *,
           observed=None) -> str:
    """把「怎么走」渲染成一条完整的 py 源码（文件级，可直接落盘进 `forms/sites/`）。

    ★ 2026-09-24：`success_urls`（判据的**第二格** —— 网址里出现哪一截）可有可无；
    **两格都空**时照旧抛（没有判据的产物会「跑到底再说自己成功」，本项目最忌讳的那类谎）。
    ⚠️ 它**不是**把文字那格放宽成「正文+网址」—— 是**另外一格**
    （「判据一个字没放宽，要不要改得人点头」是这条链上的老口径）。

    ★ 2026-10-16（第二刀 spec §3.1）：`observed` = **探路那一刻冻的那份**
    （`state["observed"]`，只读）。**产物里那几步的锚只认它** —— 卡找得到就照卡整份覆写
    （卡上没有的键删掉），卡找不到那一步一个字不动。`selectors` 一个字不动
    （它是候选，是改稿那双手要换的东西）。
    ⚠️ 合并排在 `_steps_must_be_findable` **之前**：判就该判**要写出去的那一份**
    —— 合完认不出来的步当场拒稿、点名第几步（spec §3.2）。
    ⚠️ **不传 = 与从前逐字节相同**（老 job / `tools/rerender_site.py` 那条重渲的路；
    **同一份 `agent/template.py` 版本内** —— 跨版本只差 `SKELETON_SHA` 那一行，
    而它就是**本文件的 sha256 前 16 位**（`_skeleton_sha()`）⇒ 改本文件它必变）。
    """
    if not site or not str(site).strip():
        raise ValueError("site 不能空")
    site = str(site).strip()
    if not states:
        raise ValueError("states 不能空：没有「怎么走」的产物跑起来只会站在那儿")
    if observed is not None:
        from agent import fix              # 局部 import：与 `lint` 那边同一条理由（避免环）
        #: ⚠️ `render` 的 `fills` 收 **list（带 name）也收 dict**（见 `_fills_map`），而
        #: `anchors_from_observed` 只认 dict（它**不猜形状**：不是 dict 就原样返回）⇒
        #: 「表是 list」这一支会在**静默**里跳过合并 —— 编的锚照样进产物（本刀要治的病）。
        #: 归一化因此是 `render` 的责任（它本来就是那个懂两种形状的地方）：只在这一支里做，
        #: **不传 `observed` 的路一个字节都不碰**。
        if not isinstance(fills, dict):
            fills = _fills_map(fills)
        states, fills, _ = fix.anchors_from_observed(states, fills, observed)
    states = _steps_must_be_findable(states)   # ★ 见上面那条：没有落点的一步不许进产物
    #: ⚠️ 文字那格**只在人/底稿真给了字**时才走 `_success_texts`：它自己会在「剥完什么都不剩」
    #: 时抛（只写「出现」那种）—— 那一抛是对的，**别为了网址那格把它吞掉**。
    urls = _success_urls(success_urls)
    texts = _success_texts(success_text) if str(success_text or "").strip() else []
    if not texts and not urls:
        raise ValueError("成功判据不能空：文字那一格剥完什么都不剩（只写了「出现」这种说明词）、"
                         "网址那一格也没给 —— 没有判据的产物会跑到底再说自己成功")
    summary = ("siteforge 从真页面探索出来的重放脚本：按 STATES 走一遍，"
               "见到成功判据（页面上的那串字，或网址里的那一截）就算成功。")
    src = SKELETON.substitute(
        site=site,
        summary=summary,
        success_texts=_lit(texts),
        success_urls=_lit(urls),
        states=_lit(_stable_first(list(states))),
        fills=_lit(_stable_first(_fills_map(fills))),
        provenance=_provenance_literal(provenance),
        skeleton_sha=SKELETON_SHA,
        stuck_limit=DEFAULT_STUCK_LIMIT,
    )
    #: ★ 2026-10-16：两段 JS（`_COVER_JS` / `_RECENTER_JS`）是**本模块的常量**，在这儿替进产物。
    #: 为什么不进 `substitute` 的参数表：那九个是「每趟都不同」那一族，而这两份是骨架自己的常量 ——
    #: `__TOKEN__` 这一招与 `_LOOP_SKELETON`（`__DEEP__`）同一条路子。
    #: ⚠️ 替的是 `_lit(...)` 出来的**字面量**：产物那边要有 `_COVER_JS` 这个**名字**（方法体引用它）。
    #: ★ 2026-10-16（Task 4）：`__POPUP_MACHINERY__` 那一段**贴的是原文、不是字面量** ——
    #: 它就是从这个位置搬走的那些行（`_POPUP_MACHINERY_SRC`），贴回去 = 产物字节一个都没变。
    return (src.replace("__COVER_JS__", _lit(_COVER_JS))
               .replace("__RECENTER_JS__", _lit(_RECENTER_JS))
               .replace("__POPUP_MACHINERY__", _POPUP_MACHINERY_SRC))


# ⚠️ 骨架里的 $ 只有 `render()` 填的那九个（site / summary / success_texts / **success_urls** /
#    states / fills / provenance / **skeleton_sha** / stuck_limit）—— `string.Template` 会把任何别的 $ 也当占位符。
#    其余部分请当作「生成出来的源码」读：注释是给将来读产物的人看的（D16：人话）。
#: ★ 2026-10-16：那段「谁盖着这一点」的 JS 从 `_cover_once` 里提出来 ——
#: **循环型骨架（`agent/template_loop.py`）也要用同一把尺子**。
#: 判据一个字没改：`elementsFromPoint` 整摞、跳 `pointer-events:none`、自己/祖先/后代都算同一个东西。
#:
#: ★ 2026-09-28（ukg 真站实测）：用整摞 `elementsFromPoint`，跳过点得穿的。
#: 单问 `elementFromPoint` 会把 `pointer-events:none` 的透明空遮罩也报成遮挡 ——
#: 真事：`#qualified-multimodal-host`（967x905、全透明、z-index 拉满、
#: **pointer-events:none**）盖在整页上，产物于是「礼貌地」拒绝点击，
#: 后面几个字段全空 ⇒ 提交被站点打回 `The Title field is required`。
#: 而同一页 `cdp observe` 的 occluded_by 是 None（它判对了）——
#: 两把尺子对不上就说明这把量错了：**点得穿的，不算盖着**。
#:
#: ⚠️ **今天不穿 shadow，量到要用再说**（2026-10-16 定的）：这一段走的是
#: `document.getElementsByTagName('*')`，而循环型自己那套 JS 是 `deepAll`（**穿**）。
#: 真站上量过（读数在 `docs/probes/2026-10-16-loop-cover/`）：**影子里一个可点都没有**
#: （闭 shadow 一次没见过）⇒ 穿与不穿**量不出差别**，今天照不穿这版走；
#: 将来真量到「落点长在 shadow 里」，这里要跟着改（改的是**判据**，两套骨架一起变）。
#:
#: ⚠️ `%s` 吃一个**已 json.dumps 过**的选择器（调用方给）。**只此一份**，不许再拷一份。
#: ⚠️ 它是**裸语句序列**，**不带** `(function(){…})()` 壳 —— 表那套的 `_ev` 在调用时替它包
#: （循环型那侧 `cdp.eval` 不包，由调用处自己包；别在这儿包，包了表那套就成了双层包裹）。
_COVER_JS = (
    "var all=document.getElementsByTagName('*'), el=null;"
    "for(var i=0;i<all.length;i++){try{if(all[i].matches(%s)){el=all[i];break;}}catch(e){}}"
    "if(!el) return '';"
    "var r=el.getBoundingClientRect();"
    "if(!r||r.width<=0||r.height<=0) return '';"
    "var x=r.left+r.width/2, y=r.top+r.height/2;"
    "if(x<0||y<0||x>window.innerWidth||y>window.innerHeight) return '';"
    "var stack=document.elementsFromPoint(x,y)||[];"
    "for(var k=0;k<stack.length;k++){var h=stack[k];var pe='';"
    "try{pe=getComputedStyle(h).pointerEvents||'';}catch(e){}"
    "if(pe==='none') continue;"
    "if(h===el||el.contains(h)||h.contains(el)) return '';"
    "return 'COVER|'+(h.tagName||'').toLowerCase()+(h.id?('#'+h.id):'')"
    "+'.'+String(h.className||'').split(' ')[0];}"
    "return '';"
)

#: ★ 2026-10-16：`_recenter` 那段（「把目标挪到视口正中」）同理提到模块级 —— 循环型共用。
#: ⚠️ 它**自带** `(function(){…})()` 壳（本来就那样，一个字符都没动）——
#: 别顺手「统一」成裸语句：统一就是改判据。
_RECENTER_JS = (
    "(function(){var all=document.getElementsByTagName('*');"
    "for(var i=0;i<all.length;i++){try{if(all[i].matches(%s)){"
    "all[i].scrollIntoView({block:'center'});return 'ok';}}catch(e){}}"
    "return '';})()"
)


#: ★ 2026-10-16（Task 4）：同意层 / 弹层那一整块（钩子表 `_POPUP_HOOKS` + 探针 +「按文字打标记」
#: `_MARK_CONSENT_BY_TEXT_JS`）从骨架里**搬出来** —— 循环型骨架（`agent/template_loop.py`）
#: 要取**同一份**（spec §3.1「判据只写一份」：两套骨架在渲的时候取同一份，不各写一套）。
#: ⚠️ 它是**产物源码的原文**，不是值：`render()` 与 `render_loop()` 各自把它原样贴回产物里
#: （贴回去的位置就是骨架里那个 `__POPUP_MACHINERY__`）—— 贴的就是搬走的这一段，
#: 所以表那套渲染出来的字节**一个都没变**（2026-10-16 搬的时候按 hash 核过）。
#: ⚠️ 块里**一个 `$` 都没有**（`string.Template` 那九个占位符一个都不在里面）⇒ 这一步与
#: `__COVER_JS__` / `__RECENTER_JS__` 那两处同一个路子（替在 `substitute()` **之后**）。
#: ⚠️ 里面那句「按文字打标记」的 JS 用 `%s` 吃一个 `json.dumps` 过的按钮文字（调用方给）。
_POPUP_MACHINERY_SRC = r'''#: 找同意弹层那段 JS（**读**，不动页面；§5.2 允许 eval 读）。返回 `"<选择器>|<按钮文字>"`，
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
    "if(d==='none'||v==='hidden'||v==='collapse')return false;}"
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
#:   ③ 里面得有一颗**可见的、明确叫「关闭」**的控件（close / dismiss / 关闭 / ×，且很短）。
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
    "return /^(close|dismiss|关闭)/.test(s)&&s.length<=24;};"
    "for(var m=0;m<all.length;m++){var mb=all[m];"
    "if(!isModal(mb))continue;"
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
    "var ca=String((cb.getAttribute&&cb.getAttribute('aria-label'))||'').trim();"
    "var cx=String(cb.innerText||cb.value||'').trim();"
    "if(!(closeW(ca)||closeW(cx)))continue;"
    "return (cb.id?('#'+cb.id):'TEXT')+'|'+(ca||cx).slice(0,40);}"
    "return '';}"
)

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
    "var decW=/(reject|decline|deny|accept|agree|allow|同意|接受|拒绝|不同意)/i;"
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
    "var btnW=/(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;"
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
    + _CONSENT_ROOTS_JS +
    "var hit=null;"
    "for(var i=0;i<all.length;i++){var b=all[i],tag=String(b.tagName||'').toLowerCase();"
    "if(tag!=='button'&&tag!=='a'&&tag!=='input'&&tag!=='div'&&tag!=='span')continue;"
    "var t=String(b.innerText||b.value||(b.getAttribute&&b.getAttribute('aria-label'))||'').trim();"
    "if(t===want){hit=b;break;}}"
    "if(!hit)return 'none';"
    "for(var k=0;k<all.length;k++){if(all[k].removeAttribute)all[k].removeAttribute('data-sf-consent');}"
    "hit.setAttribute('data-sf-consent','1');return 'marked';"
)
'''

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
# 成功判据之一：页面上出现其中任意一段文字就算走通了（人话，不是选择器）
SUCCESS_TEXTS = $success_texts
# 成功判据之二：**当前网址**里出现其中任意一截也算走通了（★ 2026-09-24 用户点名加的那一格）。
# 为什么要有它：老脚本本来就常判网址（`japansdates` 的 `/wizard`、`warthunder` 的 `#/confirm`），
# 而那种站换页时**正文可能一个字都不变** —— 只认文字就等于永远认不出成功。
# ⚠️ 两格是**或**的关系，都在收尾时判；**空的那格不参与**（没放宽任何东西）。
SUCCESS_URLS = $success_urls
# 早停：连续这么多步没做成，收摊（规格 §13 —— 重跑不许磨完全程）。
# 生产 JSON 执行器的早停有**已知未修**的 bug（form_executor/json_executor.py:373 的
# bool(_cur_tab) 恒真 → 连续失败计数每步被清零 → 失败任务必磨完全程），
# 所以这条必须产物**自带**，不指望外面兜。
STUCK_LIMIT = $stuck_limit
# 连着这么多**组**的判据都不成立 ⇒ 当场喊一句（缺陷单《产物判据跨元素拼接》§六 E3）。
#
# 为什么不是「每一跳都喊」：跳过是一条**正常**的路（SPA 每屏只成立一组，其余都跳过），
# 每跳喊一遍等于刷屏。真正的病是**连着**垮 —— georgiapower 那单 25 步里真做 2 步、
# 后面 23 步全跳，而日志长得像正常跑。3 组是这条线：一组不像可能是判据保守，
# 连着三组不像就只可能是「判据不该在这页成立」或「环境变了」。
SKIPPED_GROUPS_ALOUD = 3
# 一步**没做成**之后，把它那一组**从头再走一遍**；这个数是**一趟里**的总次数上限。
#
# 为什么要重试（2026-09-20 真站实测，单号 26015658）：
#   第 11 步「页面上没找到「330i」」 —— 就这一下，问卷没前进 ⇒ 第 12~24 步的
#   `Progress: N%` 判据全废 ⇒ 最后在**空表**上点「See My Match」、一个字段都没填。
#   现有的兜底（`_relocate` 那条「换了第 3 个找法」）**已经跑过**了：找过、没找到。
#   缺的是「失败之后退一步重来」—— 产物是**纯线性**的，一步没成，后面全部错位。
#   而这类站最常见的自愈就是「下拉没选上 ⇒ 重开一次多半能选上」。
#
# 为什么是 **1**（不是 2、不是 3）：重试的代价是**在真页面上多做几个真实动作**，
# 而运营那边的口径是「刷太多不太好」「能成功为啥还要继续」——
#   - 一趟里至多 1 次 ⇒ 整趟**多做的真实动作 = 那一组的动作数**，不随失败次数累积；
#   - 这个数有多大是量过的（本产物 `STATES` 19 组、25 步：13 组 1 步、6 组 2 步）
#     ⇒ **一次重试最多多做 2 个真实动作**；实测卡住的那一组（「点 330i」）是 1 步。
# ⚠️ 这是**报给运营拍板**的数，不是这里能自己放大的 —— **别调大它而不去问**。
GROUP_RETRY_LIMIT = 1
# 判据**只有地址**时，开跑之前要说的那句话（生成期在 `when["text_why"]` 里留了原因时，
# 把那一句接在它后面 —— 生成期说的话**原样转出来**，产物不另编一句）。
URL_ONLY_WHY = "生成期没给它正文判据 —— 这一组只靠地址认页"
# trace 里落的页面签名长度：与 cdp observe 的 page_text 同口径（§4.3「归一化后前 600 字」）。
PAGE_TEXT_CHARS = 600
#: trace 里**另外**留的尾部长度（`page_sig_tail`）—— 子帧的正文常常落在前 600 字之后，
#: 不留尾就「搜不到 ≠ 没读到」分不开（2026-09-17 第十轮补的，**加法**）。
PAGE_TAIL_CHARS = 240

# 动作命令自己报错时的字样：cdp click / form 找不到元素就直接报这些。
# 「这一步成没成」在**填/选**类步骤上只能看它 —— diff 判不了控件状态（§4.6 的能力边界）。
#: ★ 2026-09-28（travelandleisure 生产单 26221672 实测逼出来的）：**页面根本不是内容页**时的特征词。
#: 命中就说出来 —— 因为**风控/挑战页**与「判据不匹配」在日志里**长得一模一样**，
#: 而前者**不是脚本能解决的**（要换出口 IP 或降频），读日志的人不该往判据上找。
#: 真事：那一单从第 3 步起 12 步读到的正文一直是
#: `One moment, we're checking you're not a bot. 20260928T094845Z-…`，
#: 而现场只报「正文里没有「Log In Join Now」」⇒ 看着像脚本的错，其实是 IP 被盯上了。
_TROUBLE_MARKERS = (
    "checking you're not a bot", "just a moment", "verifying you are human",
    "are you a human", "enable javascript and cookies", "attention required",
    "access denied", "ddos protection", "cf-chl", "captcha",
)
#: ★ 2026-09-28（mapleporchdaily 真站排查）：**同一件东西，两边叫法不同**。
#: 产物的 `FILLS[*].source` 是照**页面字段名**起的（`phone` —— 见 `browser_agent`
#: 那句 `{"source": name}`），而任务资料（`--form-file`）的键是**资料库的列名**
#: （`phone_number` / `username` …）。两边**没有任何约定** ⇒ 只有恰好同名的
#: （`email`）才取得到，其余静默掉进随机池，而日志里照样写着「填好了」。
#: 真事：加拿大站被填了骨架池里的**英国**手机号 `07700900123`，站方红字
#: `The phone number is not valid.` ⇒ 整单提交被拒（截图在 failDiag 里）。
#: 这一格就是照名字再找一遍 —— **只加回退，不动原来的键**。
#: ★ 2026-09-28：切到新开的那个 tab 之后，**让页面稳住**再读 —— 切过去的那一瞬
#: 新页可能还在加载/还在重定向（`/json/activate` 只负责把它弄到前台，不等它加载）。
#: 0.6 秒是「够它把 URL 定下来」的量级；真要不要等，`_wait_when` 还会再等一轮。
TAB_SETTLE_SECONDS = 0.6

_ALIASES = {
    "phone": ("phone_number", "tel", "mobile", "phone1", "telephone"),
    "first_name": ("firstname", "first", "given_name"),
    "last_name": ("lastname", "last", "surname", "family_name"),
    "email": ("mail", "email_address"),
    "postcode": ("postal_code", "zip", "zipcode", "zip_code"),
    "city": ("town",),
    "street": ("address", "address1", "street_address"),
    "country": ("country_code",),
    #: 任务资料里出生日期叫 `birthday`（formMessage 的键），产物里常按页面叫 `dob`。
    "dob": ("birthday", "date_of_birth", "birthdate"),
}
#: 资料只给**一个全名**（`username: "Lin Cameron"`）时，这两格从它按空格拆。
#: 页面上是 First / Last 两个框、资料库里是一个字段 —— 这是**结构差**，
#: 光靠别名对不上（真事：那一站 `first_name` 曾经被填进整个 "Lin Cameron"）。
_SPLIT_FROM = {"first_name": "username", "last_name": "username"}

#: 值里**最后那一串**数字（`\d{3,}` 且后面不再有数字）—— `_shaped_like` 拿它当「可变尾段」。
_TAIL_DIGITS = re.compile(r"\d{3,}(?!.*\d)")


def _shaped_like(sample: str) -> str:
    """照 `sample`（**真站收过的那个值**）的形状编一个新的，前面照抄、尾段数字随机。返回空串 = 不编。

    ★ 2026-10-13（真事 `job-a5f74744a7f9` · healthinsurancewatch.com.au）：探路那趟在真站上
    填的是 `0412345678`（澳洲手机 —— 页面认了、提交也过了），而重放那一格走的是随机池，
    池子照 `PROVENANCE['env']['proxy_country']`（**代理出口**在哪）分档，那趟是 US
    ⇒ 一个美国形状的号填进 `04xx xxx xxx` 的框 ⇒ 站方红字 `Please enter a valid mobile
    number`、**提交那一下卡死**，而每一步的日志都写着「填好了」。

    **站点在哪**与**出口在哪**是两件事；而「真站收过的那条值」是**不必猜**的那份证据 ——
    这条兜底只为「没取到任务资料」的那一趟准备（正常那一趟资料里有值，走不到这儿）。

    规则（两个形状，够覆盖邮编/手机/ZIP 这几族）：
      · **通体是数字**（`0412345678` / `2000` / `98101`）⇒ 头 **2 位**照抄、其余随机
        （`04` 是澳洲手机的前缀、`20` 是澳洲邮编的城市段 —— 头两位正是「形状」的所在）；
      · **数字前还有别的东西**（`+61 412 345 678` / `SW1A 1AA`）⇒ 最后那串数字随机、前面照抄。

    ⚠️ **不编**的三种：一个数字都没有（姓名那类，交给池子）；带 `/`（日期那类，`07/23/1981`
    的尾段是**年份**，随机成 `0231` 比池子更坏）；尾段太短（<3 位，随机等于没形状）。
    ⚠️ 尾段**随机**是故意的：每趟都填同一个号，站方的去重/风控看得见（与那两个池子同一条理由）。
    """
    text = str(sample or "").strip()
    if not text or "/" in text:
        return ""
    digits = [c for c in text if c.isdigit()]
    if not digits:
        return ""
    if all(c.isdigit() or c.isspace() for c in text):
        body = text.replace(" ", "")
        if len(body) < 3:
            return ""
        return body[:2] + "".join(random.choice("0123456789") for _ in range(len(body) - 2))
    m = _TAIL_DIGITS.search(text)
    if not m or text[m.end():].strip():
        #: 那串数字**不在末尾**（`sarah.lee1980@gmail.com` —— 后面还跟着域名）⇒ **不编**：
        #: 照「前缀 + 随机数字」编出来会把它**截断**（实测编成 `sarah.lee4150`，邮箱没了）。
        #: 这一族的兜底交给池子（邮箱/姓名本来就有池子）。
        return ""
    run = m.group(0)
    return (text[:m.start()]
            + "".join(random.choice("0123456789") for _ in range(len(run))))
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
    specials = "!@#$$%^&*"
    chars = [random.choice(upper), random.choice(lower),
             random.choice(digits), random.choice(specials)]
    alphabet = upper + lower + digits + specials
    chars += [random.choice(alphabet) for _ in range(8)]
    random.shuffle(chars)
    return "".join(chars)


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

#: ★ 2026-09-22（生成侧建议第 2 条）：**有界等「when 成立」**最多等几秒。
#: 为什么是 5~8：真不成立的状态组多花这几秒，换来「不是没等就判」；而这类失败
#: **跑完全程不报错**（只表现为「没走到成功」），花这几秒比事后排一遍便宜得多。
WAIT_WHEN_SECONDS = 7.0

#: 收尾**等成功文案**最多几秒（2026-09-22 起；2026-09-23 从 12 → 25 → **30**，
#: 用户原话：「他点完提交需要等待时间的」/「**起码也要30秒才稳定吧**」）。
#: 为什么要这一笔：提交之后成功页常常是**异步**渲染的（那一屏要等后端出报价），
#: 脚本立刻判 `_succeeded()` 判不到 ⇒ 真站上明明成了却报「没走到成功」。
#: 30 这个数是**运营给的**（不是推出来的）—— 页面稳不稳只有天天看它的人知道；
#: 机器这边只保证「有界、见到了立刻走」（`SUCCESS_POLL_SECONDS` 每秒看一眼）。
#: ⚠️ **只在收尾那一次**等：过程中每一步仍然是**立刻判**（见到了就停），
#: 所以代价只有「真没成功的那一趟多等 30 秒」，不是每一步都付。
SUCCESS_WAIT_SECONDS = 30.0
#: 上面那一笔的轮询间隔（秒）。**它决定「多久发现成功页」**：越短越灵，代价是每轮一次读页面。
SUCCESS_POLL_SECONDS = 1.0

#: ★ 2026-09-23 用户实测（原话「**等待时间短了**，我看他重新填了后成功了」）：
#: 每步做完只 diff **一次** ⇒ 提交之后那一屏还在**异步**换，当场判成「页面没有变化」
#: （trace 第 12 步就是这样，而那一下其实成了）。改成**有界重试**：最多再等这么多秒，
#: 每 `DIFF_POLL_STEP` 秒重算一次，**一变立刻停**（只有真没变的那一步才付这几秒）。
DIFF_POLL_SECONDS = 5.0
DIFF_POLL_STEP = 0.5

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

#: ★ 2026-10-13：等遮挡物自己走的同时，**最多**把目标挪到视口正中几次（见 `_recenter`）。
#:
#: 为什么要这一格（真事 `job-a6954761e657` · healthinsurancewatch.com.au，读数在
#: `_probe_hiw_cover.py`）：填完上一格之后**站点自己滚动**，`#postcode` 的中心点正好落在
#: **粘性页眉**（`div.h-full`，视口顶上那一条：[0,0,360,98]）底下 ⇒ 产物「礼貌地」不动手：
#: 等满 35 秒、两个声明地址 + 两个重找候选**一个都没试** ⇒ 报文只有一句
#: 「没找到「Your postcode」这个输入框，没填成」。自测三轮都死在这一步，各花 50.1/49.8/50.1 秒
#: （= 35 秒空等 + 开销；第 9 步只花 14.5 秒 —— 遮挡物已经进了「赖着不走」名单）。
#:
#: **粘性页眉不是「不能碰」，是「站错地方了」** —— 人在那儿只会把页面往回滚一点就填了。
#: 但这条口子**有界**：真弹层（consent 那类）挪几次也没用 ⇒ 预算到点照旧不动手、照旧失败；
#: 也就是「多滚两下」，不是「放开遮挡判据」。
RECENTER_TRIES = 2

#: ★ 2026-10-12：声明里那根地址**没找到**时，先等多久（秒）再试**同一根地址**。
#:
#: 为什么要这一格（真事 `job-d57366d0cf35` · ic.com 的选国家抽屉，读数在
#: `docs/缺陷单-2026-10-12-选国家点不动-影子宿主.md` 的姊妹篇里）：点开抽屉之后
#: **2 秒里**那个搜索框还没进 DOM（那一页当时还没 `readyState=complete`）⇒ 产物当场判
#: 「第 1 个选择器没成」⇒ 掉进「重新 observe + 按意图重找」那一支；而重找在
#: 「账本里没有可比的身份信号」时**按意图猜**（把握 0.50）⇒ 把「United Kingdom」
#: 填进了 `#firstname`，后面整组跟着歪（列表没被过滤 ⇒ 那根位置链点到的国家是 Albania）。
#: 而在同一状态下把那条命令再发一次：**rc=0、ok=true**，列表从 175 项过滤成 1 项 ——
#: **地址一直是对的**，差的就是那两秒。
#:
#: 与 `COVER_WAIT_SECONDS` 是**两个问题**、两笔预算，别合并：
#:   那一笔问「**我要点的那个东西**在不在最上面」（被盖着）；
#:   这一笔问「**它进 DOM 了没有**」（还没渲出来）。
#: 预算同样**按步共用**（不按候选数翻倍），而且只重试**同一根地址** ——
#: 等到头还没出来，就把第一次那个失败原样交回去，换候选 / 重找 / 整组重走一个字不变。
NOT_FOUND_WAIT_SECONDS = 6.0

#: ★ 2026-10-13：**账本说这一组的东西是「后出来的」**时，「没找到」等多久（秒）。
#:
#: 为什么要单开一笔（真事 `job-4d12a95edf34` · innovatingcanada 的 Hustle 弹窗）：
#: 那一页的表格是**加载后约 25~31 秒**才弹出来的（触发是一次 25 秒的定时器）——
#: 上面那 6 秒根本不够，第一趟自测第 1 步就挂：
#: `第 1 步：没找到「field」这个输入框，没填成`（日志：`等了 3.6 秒它还是没出来`）。
#:
#: 而「该不该多等」这件事**不用猜**：账本里有证据 —— 落到那一页的**第一眼**里没有这一格、
#: 后面的眼里有了（`browser_agent.Journey.late_states`）⇒ 那一组标上 `late_element`，
#: 预算换成这一笔。⚠️ 反例（哪一眼里都没有 = 选择器压根不对）**不标**、照旧 6 秒：
#: 生产上 249 趟自测日志里「没找到」等到头的是 28 次、等出来的是 2 次 —— 给那 28 次加预算
#: 就是白等，而它们是失败路径上最常见的形状。
#:
#: 45 这个数的来处：本站实测 31.8 秒（25 秒定时器 + 渲染），留一个量级的余量；
#: 它是**只落在标了那一格的组**上的预算，不标的一步都不多花。
LATE_ELEMENT_WAIT_SECONDS = 45.0

#: 等它出现期间每隔多久再试一次（秒）。与 `COVER_POLL_SECONDS` 分开写：那个是「量一眼」，
#: 这个是「**重发一次动作**」—— 代价不同（一次 cdp 调用 vs 一次 eval），调的时候心里有数。
NOT_FOUND_POLL_SECONDS = 0.6

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
    # ★ 2026-09-29（交接《骨架还缺什么》§7，georgiapower 真广告 URL 开窗实测）：
    # **先问 readyState**。同一个 CTA（`<a target="_blank">`）、同一条产物路径：
    # `readyState=loading`（正文 0 字）时点 **2/2 全不成**（60 秒内新 tab 从没出现、
    # 地址一直是落地页），而 **cdp 回执一切正常**（`match_count:1`、坐标正常）；
    # `readyState=interactive`（正文 2619 字）时点 **2/2 全成**（+2.26 秒新 tab 出现）。
    # 而首屏 ~10 秒才 interactive、runner 导航到第一击只有 ~6 秒 ⇒ 正好压在成/不成那条线上
    # —— 这就是那件事「时好时坏」的来源。
    # ⚠️ 与下面蒙版那条**并列**、不是替代：蒙版问「页面上有没有一块东西盖着」，
    # 这条问「文档到 interactive 了没」。两种都不许动手。
    "if(document.readyState==='loading') return 'READY|loading';"
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

#: ★ 2026-09-29：**文档还没到 interactive** 那一种答复的前缀（§7 实测那半，见 `_PAGE_LOADER_JS`）。
#: 与 `LOADER|` **并列**、不合并：一个是「页面上有东西盖着整页」，一个是「文档还在 loading」——
#: 处置相同（都不动手、都有界等），但**说出来的话不一样**（读日志的人要分得清是哪种）。
#: ⚠️ 同样**只认带前缀的答复**：形状不认识 = 量不出来 = 放行（读成「在加载」会让整趟不做事）。
READY_PREFIX = "READY|"

#: `observe` 给 cookie 同意类遮挡物记的 kind（`internal/observe.go`：按
#: 文字/ id / class 里有没有 cookie|consent|gdpr|privacy 判的）。**只点这一类** ——
#: 一般的浮层（`overlay`）不去动它：那可能是页面自己要人看的东西，点掉它同样是「点到别的东西」。
COOKIE_KIND = "cookie-banner"

__POPUP_MACHINERY__
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
#: 英国邮编（都是真的/格式合法的一批，跨城市）；「不知道」那一档也用它。
POSTCODES = ["SW1A 1AA", "SW1A 2AA", "EC1A 1BB", "W1A 0AX", "NN3 3AQ", "M1 1AA",
             "M1 1AE", "B1 1AA", "LS1 1AA", "G1 1AA", "EH1 1YZ", "CF10 1EP",
             "BT1 1AA", "NE1 1EE", "L1 1AA", "S1 1AA", "BS1 1AA", "OX1 1AA",
             "CB1 1AA", "PL1 1AA"]
# 「州」这一类**生产脚本本来就是一个小池子 + 随机选**（不是从 form-file 取）——
# 照抄 `forms/sites/lifynest.py:16` 的 STATES（同一套「资料逻辑」，站点在美国时适用）。
US_STATES = ["California", "Texas", "Arizona", "Florida", "New York", "Illinois", "Ohio",
             "Pennsylvania", "Michigan", "Georgia", "North Carolina", "Washington",
             "Colorado", "Nevada", "Tennessee", "Missouri", "Minnesota", "Massachusetts",
             "Oregon", "Virginia"]
#: ★ 2026-10-08（用户点名「加上0207规则」+「手机号随机要以0207开头」）：英国风格的手机池
#: 改用 **0207 开头的座机形状**，不是 `07` 手机 —— 真站那一格的校验只收座机。
#:
#: 真站代价（laterlifechoices.uk，2026-10-08）：`07…` 位数一样合法，填进去页面顶回
#: `Please enter a valid phone number`；换 `0207…` 一次过（`job-cce9d4b8e5fd` 那份产物）。
#: 同一个形状 `template_loop._pool()` 那边**早就有**（用户 2026-09-25 点名那条）——
#: 所以这一格补的不是「没人知道」，是**两个骨架不一致**：产物走不走循环型，决定了
#: 英国站拿座机还是手机，而「走不走循环型」根本不是站点的属性。
#:
#: ⚠️ **`PHONES` 同时是「英国那一档」与「不知道那一档」的手机池**（`COUNTRY_POOLS["GB"]`
#: 与 `UNKNOWN_POOLS` 都指着它）。这是**故意的**，不是省事：实测那一版产物
#: （laterlifechoices，`job-7f43337b2ba0`）的 `PROVENANCE["env"]` 是 **`None`**
#: ⇒ `_site_country()` 给空串 ⇒ 真跑起来走的**就是「不知道」那一档**。
#: 所以「手机号要**座机形状**」只修英国那一档等于没修 —— 那一档根本轮不到。两档共用，一次改对。
#:
#: ★ 2026-10-08（用户点名「**不是**都是 `0207` 啊」）：英国那一池不能清一色 `0207` ——
#: 那一个号段本身就是个可疑的常量（每个站、每一趟都是伦敦 0207，站方的去重/风控看得见）。
#: ⇒ 改成**英国座机号的多个号段**里随机（`020 3/7/8…` 与 `011x/01x1 …` 各城市），
#: 位数与形状**一个字没放宽**：11 位、座机、**不是 `07` 手机**、且**照旧避开**
#: Ofcom 的影视保留段（`020 7946 0xxx` / `0113 496 0xxx` … —— 那几段是真站一眼假的号）。
#:
#: ★ 2026-10-08（用户点名「手机号应该更随机一点吧，而不是用 12345 这种的」）：
#: **池子改成每次开跑现生成** —— 产物是**一趟一个新进程**，于是每一趟填的手机号都不同。
#: 为什么非改不可：写死的那几个值（`02071234567` / `02078889999`…）码型一眼是编的
#: （`1234567`、`8889999`、`5550123`），而**每一趟、每个站填的都是同一批** ——
#: 站方的去重/风控看的就是这个重复。形状**一个字没放宽**（见下面三个生成器）。
PHONE_POOL_SIZE = 40
#: 美国 / 加拿大的号段（前 3 位）：**号段是真的、尾号随机**，而 `555-01xx` 是 NANP
#: 留给影艺的保留段（打过去到不了真人）—— 随机只发生在**保留段内部**。
_US_AREA_CODES = ("202", "212", "305", "312", "404", "415", "480", "512", "602", "617",
                  "702", "703", "713", "801", "813", "847", "901", "919", "214", "267",
                  "336", "407", "469", "614", "646", "678", "786", "818", "858", "916")
_CA_AREA_CODES = ("416", "604", "514", "403", "613", "902", "780", "905", "204", "306",
                  "709", "867")
#: 英国座机的号段（真段）：伦敦 `020 3/7/8…` + 各城市 `01x1…` —— 不锁死 `0207` 一个段。
_GB_AREA_CODES = ("0203", "0207", "0208", "0113", "0114", "0115", "0116", "0117", "0118",
                  "0121", "0131", "0141", "0151", "0161", "0191")
#: Ofcom 的**影视保留段**（给电视剧用的，真站一眼假）—— 生成时避开，不写进池子。
_GB_DRAMA_PREFIXES = ("02079460", "0113496", "0114496", "0115496", "0116496", "0117496",
                      "0118496", "0121496", "0131496", "0141496", "0151496", "0161496",
                      "0191496", "0289018", "0292018")


def _random_nanp_phone(areas) -> str:
    """保留段 `555-01xx` 里的一个号（`NPA-555-01xx`）—— 号段真、尾号随机。"""
    return "%s55501%02d" % (random.choice(areas), random.randint(0, 99))


def _random_gb_landline() -> str:
    """英国**座机**形状的一个号（11 位，`020 x…` / `01x1 …`）—— 号段随机、不是 `07` 手机。"""
    while True:
        got = (random.choice(_GB_AREA_CODES)
               + "".join(str(random.randint(0, 9)) for _ in range(7)))
        if not got.startswith(_GB_DRAMA_PREFIXES):
            return got


def _random_au_mobile() -> str:
    """澳洲**手机**形状：`04` + 8 位随机数字（共 10 位，站方那格要的就是这个形状）。"""
    return "04" + "".join(str(random.randint(0, 9)) for _ in range(8))


def _phone_pool(make) -> list:
    """现生成 `PHONE_POOL_SIZE` 个**互不相同**的值（重复的丢掉重抽）。"""
    out: list = []
    while len(out) < PHONE_POOL_SIZE:
        got = make()
        if got not in out:
            out.append(got)
    return out


#: 英国那一档（同时是「不知道」那一档，见上面那段）。
PHONES = _phone_pool(_random_gb_landline)
AU_PHONES = _phone_pool(_random_au_mobile)
AU_POSTCODES = ("2000", "3000", "4000", "5000", "6000", "7000", "0800", "0820", "2250", "4217")
AU_STATES = ("New South Wales", "Victoria", "Queensland", "Western Australia", "South Australia", "Tasmania", "Australian Capital Territory", "Northern Territory")
# ── 随机池按**站点国家**分流（★ 2026-09-29，交接《骨架还缺什么》「别处」那条）──────
#
# 为什么非分流不可（真站代价，`forms/common.py` 的 `_ALIASES` 注释里记着那一次）：
# **加拿大站被填了骨架池里的英国手机号 `07700900123`** ⇒ 站方红字
# `The phone number is not valid.` ⇒ 整单提交被拒（截图在 failDiag 里）。
# 而分流的**依据只有一处现成的信号**：`PROVENANCE["env"]["proxy_country"]`
# （后端连续失败榜上那格 `country`，两字母码，`agent/auto.py` 原样写进来 —— 与
# `service.py` 的 `env` 契约是同一格）。**取不到 = 「不知道」** ——
# 那份不知道必须**出声**（`Filler._say_country`），不许静默当美国、也不许静默当英国。
#
#: ★ 2026-10-08（用户点名「US / CA 都要**对应国家**的邮编和手机号」）：这三档**各自成池、
#: 互不借用**（美国站拿美国邮编 + 美国号、加拿大站拿加拿大邮编 + 加拿大号）—— 这条由
#: `COUNTRY_POOLS` 那张表钉着，`tests/test_template.py` 里有四条用例量它（互不重叠 + 各自形状）。
#: ⚠️ 邮编用**真实/格式合法的一批**（不是随机造）：真站常拿邮编查城市 / 路由（homeknight 就是
#: 拿它选地区），造一个不存在的邮编会走到「地址查不出来」那一屏 —— 手机可以随机（保留段里
#: 尾号无人接），邮编不可以。
#
# ⚠️ `US_STATES` 的口径**一个字没改**：仍然是「小池子 + 随机选」，
# 内容照抄 `forms/sites/lifynest.py:16`（那一注说的「站点在美国时适用」在这里就是
# 「美国那一档用它」）。英国那一档补一个同口径的小池子（郡名），不是新造一套逻辑。
#: 美国邮编：**真的**，跨 20 个州（真 ZIP，查得到城市）。
US_ZIPS = ["10001", "10011", "90001", "90012", "60601", "60614", "30301", "30308",
           "02108", "02116", "98101", "98109", "33101", "33139", "75201", "78701",
           "85001", "85016", "19102", "19103", "44101", "43201", "48201", "48226",
           "80202", "80206", "89101", "89109", "37201", "37203", "63101", "63108",
           "55401", "55403", "28202", "28204", "20001", "20009", "97201", "97205"]
US_PHONES = _phone_pool(lambda: _random_nanp_phone(_US_AREA_CODES))
GB_COUNTIES = ["Greater London", "Greater Manchester", "West Midlands", "West Yorkshire",
               "Merseyside", "Kent"]
#: ★ 2026-09-30 补的第四档（2026-09-29 只有 US / GB）。为什么会漏：那一版分流表是照
#: 「已经量到的那两个国家」建的，而**两字母码对得上、池子里却没有那一国的，走的是
#: `_site_country()` 最后那个 `code in COUNTRY_POOLS` ⇒ 落到「不知道」= 英国那一套**
#: —— `_say_country` 的告警文案里拿 `CA` 当例子写的，说的就是这个洞。
#: 真站代价（mapleporchdaily）：加拿大站被判成「不知道」⇒ 填了英国手机号 ⇒ 站方红字
#: `The phone number is not valid.` ⇒ 整单被拒（骨架注释里那条 `07700900123` 就是它）。
#: 口径与上面两档**一模一样**：小池子 + 随机选；手机与邮编都取**格式合法**的样例值
#: （NANP 的 555-01xx 与 `A1A 1A1` 这种形状），不新造逻辑。
CA_PROVINCES = ["Ontario", "Quebec", "British Columbia", "Alberta", "Manitoba",
                "Saskatchewan"]
#: 加拿大邮编：**格式合法**（`A1A 1A1`）的一批，跨 6 省。
CA_POSTCODES = ["M5V 3L9", "M4B 1B3", "V6B 1A1", "V5K 0A1", "V8W 1A1", "H3Z 2Y7",
                "H2Y 1C6", "K1A 0A6", "K1P 1J1", "T2P 2M5", "T2R 0C5", "R3C 3J7",
                "R3B 0T4", "S4P 3Y2", "S7K 1J5", "B3H 1A1", "B3J 3K5", "E1C 1E8"]
CA_PHONES = _phone_pool(lambda: _random_nanp_phone(_CA_AREA_CODES))
#: 国家（两字母码） → 各随机池。`country` 那一档要的是**国家名**（页面上的 Country 框）。
COUNTRY_POOLS = {
    "US": {"country": "United States", "state": US_STATES,
           "postcode": US_ZIPS, "phone": US_PHONES},
    "GB": {"country": "United Kingdom", "state": GB_COUNTIES,
           "postcode": POSTCODES, "phone": PHONES},
    "CA": {"country": "Canada", "state": CA_PROVINCES,
           "postcode": CA_POSTCODES, "phone": CA_PHONES},
    "AU": {"country": "Australia", "state": AU_STATES,
           "postcode": AU_POSTCODES, "phone": AU_PHONES},
}
#: 「不知道这个站在哪一国」时用的兜底 —— 邮编仍是英国那一套、州仍是美国那一套，
#: 手机与英国那一档**共用 `PHONES`**（见 `PHONES` 上面那段：真跑起来常常就是这一档）。
#: 为什么保留这个兜底而不是当场拒：拿不到国家 = 「不知道」，而「不知道」已经有一个
#: **出声的去处**（`Filler._say_country`）。⚠️ 这条的原话是「与分流之前逐字相同」——
#: ★ 2026-10-08 改的是**手机那一格的形状**（`07` 手机 → **座机**、多个号段里随机），
UNKNOWN_POOLS = {"country": None, "state": US_STATES, "postcode": POSTCODES, "phone": PHONES}

# ── 产出元数据（siteforge 自动写入，勿手工编辑）────────────
PROVENANCE = $provenance
#: ★ 2026-09-29：**这份产物是哪一版骨架渲染出来的** —— `agent/template.py` 的 sha256 前 16 位。
#: 为什么要有这一格：骨架在仓库里往前走、线上那份产物还在跑，中间一直**没有对账**
#: （实测代价：重渲一版 travelandleisure 一口气多带了 8 个方法）。
#: 拿它跟当前骨架的 `template.SKELETON_SHA` 一比，`skeleton_drift()` 就答得出「落后多少」。
#: ⚠️ 它是**骨架**的指纹（这份文件），不是产物的 —— 站点数据（STATES/FILLS）不算骨架。
SKELETON_SHA = "$skeleton_sha"


def _site_country():
    """这个站属于哪一档随机池 —— `PROVENANCE["env"]["proxy_country"]` 里那个两字母码。

    只认**已经有**的那一处信号（见 `COUNTRY_POOLS` 上面那段）；拿不到 / 认不出来
    一律给空串 = **「不知道」** —— 由 `Filler._say_country()` 出声，这里不猜。
    """
    env = PROVENANCE.get("env") if isinstance(PROVENANCE, dict) else None
    if not isinstance(env, dict):
        return ""
    code = str(env.get("proxy_country") or "").strip().upper()
    return code if code in COUNTRY_POOLS else ""


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


def _page_trouble(text):
    """这一页**是不是根本不是内容页** —— 是就给一句人话，不是就给空串（★ 2026-09-28）。

    两样，都**不是脚本能解决的**，所以必须与「判据不匹配」分开说：
      · **风控 / 挑战页** —— 正文明含 `_TROUBLE_MARKERS` 里那几句之一；
      · **空白页** —— 正文读出来是空的（还在加载 / 被拦 / 跨域读不到）。
    """
    t = _norm(text or "")
    low = t.lower()
    for m in _TROUBLE_MARKERS:
        if m in low:
            return "页面是**风控/挑战页**（正文里有「%s」）：%s" % (m, t[:80])
    if not low:
        return "读不到页面正文（空串）—— 还在加载？还是被拦了？"
    return ""


# ★ 2026-10-09：**同一个字的两种排版写法算同一个字**（用户裁定原话：「那就大小写吧」
# —— 按 2026-09-22 那条**大小写**先例办：`Check your email` 与 `check your email` 算
# 同一个词，那么 `’` 与 `'` 就是**同一个撇号**）。
#
# 真事（`job-27389cef3110`，enroll 那趟）：人给的判据是
# `Thank You! We've Received Your Information!`（**直角**撇号），站点把那句渲染成
# `We’ve`（**弯**撇号 U+2019）。只压空白 + 小写时子串不成立 ⇒ **一份 py 都不出**，
# 尽管探路走完了、正文里也真有那句。
#
# ⚠️ 折的只是**同一个字的排版变体**（弯引号 / 长短破折 / 省略号），**不是抹标点**：
# `Thank You!` 与 `Thank You` 仍然是**两串不同的字**，不许配上。
# ⚠️ 这张表**必须与探路那份逐字一致**（`agent/browser_agent.py` 里的 `_FOLD`）——
# 这一份是**独立交付出去的一份 py**，import 不到 agent 包，所以只能是两份。
_FOLD = str.maketrans({
    # 弯单引号 → 直角撇号
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    # 角分 / 角秒
    "′": "'", "″": '"',
    # 弯双引号 → 直角双引号
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # 各种横线 → 直角连字符（连字符 / 不换行连字符 / 短破折 / 长破折 / 横线 / 减号）
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    # 省略号 → 三个点
    "…": "...",
    # ── 全角 ASCII（U+FF01–U+FF5E）→ 半角 ──
    # ★ 2026-10-09 用户第二次点头（原话「算吧算同一个」）：宽度也是「同一个字的两种写法」。
    # ⚠️ **整段一起折** —— Unicode 里它们就是「同一个字符的两种宽度」，只折标点、
    # 不折字母数字是凭空划一条线。
    # ⚠️ 到 U+FF5E 为止。再往后的 U+FF61–U+FF9F 是**半角片假名**（ｱｲｳ）—— 那是**另一个
    # 字符**，不是谁的全角形；按「块」写就会把它们一起吃掉（钉在
    # `test_the_two_norms_do_not_fold_what_is_actually_a_different_character`）。
    **{chr(0xFF01 + i): chr(0x21 + i) for i in range(0x5E)},
})


def _norm(text):
    r"""把一段文字归一成「看内容」的形态：空白（含 NBSP）压成单个空格、两端剪掉，
    **同一个字的两种排版写法折成一个**（`_FOLD`）。

    ⚠️ 这条口径**必须**与 `cdp observe` 的 page_text 完全一致（规格 §4.3 明写
    「与 py 里 page_signature() 同口径」）。不一致的后果是：trace 里的 page_sig 与
    agent 当时看到的页面模型对不上，Console 会把**同一个页面**显示成两个页面。
    ⚠️ 折同形字那一步是 ★ 2026-10-09 加的，**两边一起加**；只加一边就是
    「探路认得出、脚本在真站上认不出」。同理，抹标点**不做**（那才是放宽判据）。
    Python 的 \s 与 JS 的 \s 都含 NBSP（Go 的不含 —— diff.go 那个坑就是它）。
    """
    return re.sub(r"\s+", " ", text or "").translate(_FOLD).strip()


def _criterion_in(text, wants):
    """判据在不在正文里 —— **空白可有可无**（与探路那份 `browser_agent.criterion_in`
    **同一个口径**）。

    先逐字比；不中再比「去掉所有空白」的那一份。页面常把一句话拆在两块上
    （`h3`「Congratulations!」+ `h5`「Your business …」）：视觉上是换行，正文拼出来
    中间有一个空白，而人给的那格常常没有那个空格 —— ★ 2026-10-21 真事
    `job-be76b7ecd92c`（lendio）：判据 `Congratulations!Your business …` 逐字比**永远**
    对不上 ⇒ 探路重探 3 趟；产物这一侧同病 ⇒ 真站上会报「没走到成功」。
    **空白不是内容**：同一句话被排版拆开仍然是同一句。

    ⚠️ 去空白**只**用于判据比对：`_norm` / `page_signature()` 是**页面身份**，去掉空白
    会把两页认成一页。⚠️ 抹标点**不做**（那才是放宽判据）。
    """
    low = str(text or "").lower()
    if not low:
        return False
    flat = None
    for word in wants or []:
        want = str(word or "").lower()
        if not want:
            continue
        if want in low:
            return True
        if flat is None:
            flat = re.sub(r"\s+", "", low)
        if re.sub(r"\s+", "", want) in flat:
            return True
    return False


# 读页面正文（**只读**）。shadow DOM 要逐 root 收：body.innerText 不穿 shadow，
# 而生产站点里 shadow 页越来越多（实测遇过整页正文只剩 10 个字符）。
# 遍历用 getElementsByTagName / querySelectorAll —— 动作一律走 cdp，这里只是「看」。
#
# ⚠️ **ShadowRoot 没有 `getElementsByTagName`**（那是 Document / Element 才有的，
# ShadowRoot 只是 DocumentFragment）。一个网站只要用了 web component，老写法就当场
# 抛 `TypeError: r.getElementsByTagName is not a function` ⇒ 正文永远读成空 ⇒
# 每个 `when` 判据都不成立、整份产物**一步不做**。2026-09-23 afrotech 真站复现：
# 20/20 次抛异常、7 步全跳，日志却只说「这页不像」——真正的原因一个字都不提。
# 所以按 root 的类型挑遍历方式（Document / Element 走老路，ShadowRoot 走 querySelectorAll）。
#: ★ 2026-09-28（缺陷单《产物判据跨元素拼接》§六 D）：shadow 那一支原来写的是
#: `c.innerText || c.textContent` —— `<style>` 的 `innerText` 是空的 ⇒ 回退到 `textContent`
#: ⇒ **把 LWC 的整段 CSS 读进「正文」**（实测旁证：trace 里 `page_sig_tail` 是
#: `--mobileLanding-colorTextButtonBrand:rgb(255, 255, 255);…`）。而 `page_text` 是**给判据
#: 与成功文案看的那一页**，600 字的窗口被 CSS 挤爆 = 判据看不见正文 ⇒ 整组静默跳过。
#: ⇒ 只读**会说话的**那几个标签，`style/script/template/link/meta` 一律不进正文。
_PAGE_TEXT_JS = (
    "return (function(){"
    "var SKIP={SCRIPT:1,STYLE:1,TEMPLATE:1,LINK:1,META:1,NOSCRIPT:1,TITLE:1};"
    "var roots=[document],i=0;"
    "while(i<roots.length){var r=roots[i++];"
    "var els=(r.getElementsByTagName?r.getElementsByTagName('*'):r.querySelectorAll('*'));"
    "for(var j=0;j<els.length;j++){if(els[j].shadowRoot){roots.push(els[j].shadowRoot);}}}"
    "var parts=[];"
    "for(var k=0;k<roots.length;k++){var rt=roots[k];"
    "if(rt.body){try{parts.push(rt.body.innerText||'');}catch(e){}}"
    "else{for(var m=0;m<rt.children.length;m++){var c=rt.children[m];"
    "if(SKIP[c.tagName]){continue;}"
    "try{parts.push(c.innerText||'');}catch(e){}}}}"
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

    给 `_frame_table_is_usable` 当尺子用 —— **子串匹配**，与 `_matches` / `_urls` 同一把尺子
    （判据怎么比地址，这里就怎么比）。

    为什么来源**不是** `_frames_in_states` 那几个帧号：帧号是**探索期录的**，重放一开始的
    `goto` 一重建子帧就全成死号，而 `_frame_url` 对死号返回空串 ⇒ 按帧号收的那集合
    **恒为空** ⇒ 判据退化成「每 `LIVE_PROBE_EVERY` 秒刷一次」。那条路**碰巧也能把帧刷出来**
    （刷得够勤总能撞上问卷帧），但它不是判据，而且代码做的事与 docstring 里写的不是一件事。
    ⇒ 按地址收。

    ⚠️ 收成空集是**合法**的（这条流程没有任何状态报过地址，例如判据全是 `text_contains`）——
    此时 `_frame_table_is_usable` 回落到旧口径，理由写在那条 docstring 里。
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
    #: ★ 2026-10-13：**标签也算名字** —— 取写在这颗**旁边**的那一句（`nearby_text` 第一条）。
    #: 真站那一趟的原话：`点了「没写名字的元素」（换了第 3 个找法才找到）【恢复】…` ——
    #: **挑对了，但读日志的人看不出挑的是哪一颗**；名字改成「Electric」就够了。
    #: `fill` 那一档排在它前面（字段名比旁边的字更像它的名字）。
    near = [str(x).strip() for x in ((target or {}).get("nearby_text") or [])
            if str(x).strip()]
    if near:
        return near[0]
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


#: 从**选择器**或**遮挡物描述**里抠出第一个 `#id` —— `#edit-size` 抠出 `edit-size`，
#: `span#select2-edit-size-container.select2-selection__rendered` 抠出
#: `select2-edit-size-container`。抠不出来（没有 `#`）就是空串。
#: ★ 2026-10-08：给「盖着它的是不是**这一格自己的**控件」那条判据用（见 `_cover_is_own_widget`）。
_ID_IN_SEL = re.compile(r"#([A-Za-z_][-\w]*)")


def _id_in(sel) -> str:
    m = _ID_IN_SEL.search(str(sel or ""))
    return m.group(1) if m else ""


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


#: ★ 2026-10-16：两段 JS 提到模块级 —— 「谁盖着这一点」与「把目标挪到视口正中」。
#: 为什么：循环型骨架要用**同一份**（同一把尺子）；它 import 的是 `agent/template.py`
#: 里那两份常量，渲染时替到这里，**不是**另抄一遍。
#:
#: ★ 2026-10-17（终审 M2）**把解释搬回产物里**：提成常量之前，这段 JS 的判据解释**长在
#: 产物这一处**（`_cover_once` 方法体里那七行注释）；提走之后产物里就只剩一行光秃秃的
#: 赋值 —— 下一个读产物的人看不出**为什么**要「整摞」而不是单问一下。
#: 判据本身（两条，一个字都别省）：
#:   · **单问 `elementFromPoint` 会把 `pointer-events:none` 的透明空遮罩也报成遮挡** ——
#:     真事（2026-09-28 ukg 真站）：`#qualified-multimodal-host`（967×905、全透明、
#:     z-index 拉满、**pointer-events:none**）盖在整页上，产物于是「礼貌地」拒绝点击，
#:     后面几个字段全空 ⇒ 提交被站点打回 `The Title field is required`；
#:   · 而同一页 `cdp observe` 的 `occluded_by` 是 None（它判对了）—— **两把尺子对不上
#:     就说明这把量错了：点得穿的，不算盖着。**
_COVER_JS = __COVER_JS__
_RECENTER_JS = __RECENTER_JS__


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
    #: 正是 `_frame_table_is_usable` 里「回落到旧口径」的那一支，所以这里的默认值与原意一致。
    want_urls = ()
    #: ★ 2026-09-29（见 `COUNTRY_POOLS`）：这个站的随机池按哪一国分。
    #: 类默认值 = **「不知道」那一档**（`UNKNOWN_POOLS`，与分流之前逐字相同）——
    #: `object.__new__(Filler)` 那种实例（单测直接问「这一页算不算那个状态」）也要够得着。
    country = ""
    pools = UNKNOWN_POOLS
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

    #: ★ 2026-10-13：这一步**因为被盖着而一个候选都没试**时，盖着它的那个东西
    #: （`"tag#id.class"`，空串 = 不是这个原因）。只属于触发它的那一步（`_perform` 开头清零）。
    #: 为什么要它：报文原来把「没敢动手」与「找不到框」说成同一句
    #: （`job-a6954761e657` 三轮自测都只留下「没找到「Your postcode」这个输入框，没填成」，
    #: 而那一刻产物**连一次 cdp form 都没发出去**）⇒ 三轮诊错全在改选择器。
    #: 类默认值同上：`object.__new__(Filler)` 那种实例也要够得着。
    cover_why = ""

    #: 最近一次 `goto` 时 cdp 在 stderr 上说的话（见 `__init__` 与 `_do` 里 goto 那段）。
    #: 类默认值同上：`object.__new__(Filler)` 的实例够得着这一格。
    goto_echo = ""

    #: ★ 2026-09-23：最近一次「靠意图重找」的结论（命中哪几样身份 / 把握 0~1）；
    #: 没走那条路、或者没救回来 —— 都是 None。
    #: **类默认值**：`object.__new__(Filler)` 那种实例（单测直接拿它问「这一页算不算
    #: 那个状态」）也要够得着 —— 与 `goto_echo` / `_last_model` / `_stuck_covers`
    #: 同一条规矩。这一格漏掉会当场 AttributeError，而它发生在 `run()` 的主循环里 ⇒
    #: **整趟挂**（2026-09-23 实测：模板用例 6 条就是这么红的）。
    resolve_why = None
    resolve_score = None

    #: 「这一组要重来一遍」那句话 / 这一趟重试过几次（见 `__init__`）。
    #: 类默认值同上：`object.__new__(Filler)` 那种实例也要够得着（它是 `_run_step` 读的）。
    retry_why = None
    group_retries = 0

    def __init__(self, ws_url, form_file, correlation_id, task_id="",
                 trace=None, stop_at=None, shots="failed", delay=DELAY_RANGE,
                 no_report=False):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self._rule_values = {}
        self.cid = correlation_id
        self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger(SITE)
        self.trace_path = trace
        self.stop_at = stop_at or 0
        self.shots = shots or "failed"
        self.delay = delay
        self.no_report = no_report    # --no-report：这次不上报（自测/调试用；生产必须上报）
        self.tracing = bool(trace)
        #: ★ 2026-09-29（见 `COUNTRY_POOLS`）：随机池按**站点国家**分流。
        #: 国家从 `PROVENANCE["env"]["proxy_country"]` 来（已有的那一处信号）；
        #: 拿不到就是 **「不知道」** —— 那就**出声**（`_say_country`），
        #: 池子照旧用 `UNKNOWN_POOLS`（与分流之前逐字相同的那一套）。
        self.country = _site_country()
        self.pools = COUNTRY_POOLS.get(self.country) or UNKNOWN_POOLS
        if not self.country:
            self._say_country()
        if self.no_report:
            self.log.info("[%s] 这一次不上报（--no-report）：结果不往生产接口写", self.cid)
        self.step = 0          # 当前第几步（全局编号，与 --stop-at 同一套）
        self.stuck = 0         # 连续没做成的步数（早停看它）
        self.skipped = 0       # 被 `when` 判据整组跳过的步数（最后那句总结要报出来）
        self.skipped_states = {}   # 状态名 → 被跳过的步数（报「跳过最多的是哪个状态」）
        #: ★ 2026-10-09（用户裁的「耐性」那条）：**靠正文（而不是地址）认出来**的组数 ——
        #: 地址漂了（参数 / slug 变了）而正文没变的那些。收尾那句要报出来（不报 = 看不出来
        #: 这一趟是怎么走下来的），`_url_drift_note` 写它自己的那一句。
        self.drifted = 0
        #: ★ 2026-09-28：这一趟读到**风控页 / 空白页**的 `(步号, 一句人话)` —— **纯诊断**
        #: （收尾那句要报出来）。与 `skipped_states` 是两件事：那个说「卡在哪个状态」，
        #: 这个说「页面压根不是内容页」—— 后者不是判据的问题，也不是脚本能解决的。
        self.trouble_steps = []
        #: ★ 2026-09-28：这一下点完是不是**新开了 tab 并切了过去**（`_follow_new_tab` 写、
        #: `_tab_note` 读）—— 每一步开头清空，只属于触发它的那一步。
        self._followed_tab = ""
        #: 连着**几组**判据都不成立（缺陷单 §六 E3 的那句报警看它；一组真做了就清零）。
        self.skipped_in_a_row = 0
        self.stalled = 0       # 连续「点了但页面没动」的步数（另一种原地打转）
        #: 这一趟**已经重试过几次状态组**（上限 `GROUP_RETRY_LIMIT`，一趟里算总数）。
        #: ⚠️ 它与 `stuck` 是**两个**计数器，别合并：`stuck` 数「连着几步没做成」（早停看它），
        #: 它数「退一步重来过几次」（成本闸看它）。重试成功了 `stuck` 会清零，它**不清**——
        #: 否则「重试上限」就变成了「只要这次重试成了，下次还能再重试」。
        self.group_retries = 0
        #: ★ 2026-09-29（交接《骨架还缺什么》§8）：判据不成立时**复核那一击**要用到的三格。
        #: `_last_hit` = 刚动手的那一步**连同它的 `progress`**（`(index, step, progress)`；
        #: `progress is True` = 页面**判得出动过** ⇒ 那一击落地了、**不许重试**，见 2026-10-20
        #: 那条真事）；`_seen_tabs` = 见过的 tab（用来认
        #: 「晚到的新 tab」）；`_retried_groups` = 复核过重试的状态名（**一组只重试一次**）。
        #: ⚠️ 这三个都只在这一条路上用（判据不成立）—— 成功路径一次都不碰，代价不摊到每步。
        self._last_hit = None
        self._seen_tabs = set()
        self._retried_groups = set()
        #: 触发重试的那一步要带的那句话（进它的 trace 行；用完即清，与 `progress_why` 同规矩）。
        self.retry_why = None
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
        #: 「手上这几个活帧里有没有我要读的那一个」这条判据的尺子（`_frame_table_is_usable`）。
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

        现在：**读之前先确认手上那几个活帧里有我要读的那一个** —— 判据见 `_frame_table_is_usable`
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
        # 命中这条流程认得的 URL**（`_frame_table_is_usable` —— 在 2026-09-20 之前，这一格
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
                            or not self._frame_table_is_usable()):
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

    def _frame_table_is_usable(self):
        """手上这张帧表**够不够用来读页面**（够 ⇒ `_read_frames` 就不必再去找一次）。

        够 = **我认得的那一帧在表里**：表里有一个帧的地址命中这条流程认得的 URL
        （`want_urls`）—— 子串匹配，与 `_matches` / `_urls` **同一把尺子**
        （判据怎么比地址，这里就怎么比）。

        为什么不是「某个帧答得上就行」（2026-09-17 第九轮量到、2026-09-20 改过来）：
        **广告帧永远答得上**（R-68 量到的形状：`id-msp.newsbreak.com/sync-nbu…` 那种）。
        于是这一门恒为真 ⇒ 再也不去找那个真正的问卷帧 ⇒ 读页面只剩主帧 + 广告帧
        ⇒ 按正文判据的状态**静默跳过**（不出声，看着像「跑完了，一步没走」）。
        真站实测：卡死那几趟里活帧表**从头到尾一次都没换过**（恒为同一个广告帧）。

        ⚠️ **主帧地址命中了也不作数**（`url != main` 那道闸）：主帧**本来就不该在**
        这张表里（`_note_live_frames` 用 `if fid` 滤掉主帧那个空串），可这条判据在这个站上
        **恰好悬在那个不变量上** —— 真站实测：这条流程的 `want_urls` 三条里有一条
        `https://www.gowizard.com/auto/` **正是主帧地址的一部分**，主帧一旦进表，
        判据会**静默恒真**、直接退回修之前那个死锁。
        所以这里**不依赖**那个不变量：候选帧的地址与**主帧当前地址**逐字相同 ⇒ 跳过。
        代价说清：主帧地址与某个真帧地址**逐字相同**时会误伤（那种帧会被当成主帧跳过）——
        现实中不会：帧是**跨源** iframe，它的 `location.href` 与主帧必然不同源地址。

        ⚠️ **`want_urls` 为空时回落到旧口径**（「任一帧答得上」）：这条流程**没有任何状态
        报过地址**（判据全是 `text_contains` 那种）⇒ 拿什么去比都不知道。此时
        「永远刷新」是**错的**：那是每 `LIVE_PROBE_EVERY` 秒起一次 `observe()`
        （`_read_frames` 每步都要读页面，而读页面就要过这一门）—— 既白花钱，
        又**改掉了这条流程原来的行为**。⇒ 判不了就按旧口径放行：**这条流程的帧判不了，
        不拿一个猜出来的判据去刷它**。
        ⚠️ **回落那一支没有主帧那道闸**（它就是旧口径本身，旧代码里没有那道闸）——
        那一支上「主帧进表」仍然会让它答「够」；但它只在这条流程一个地址都没报过时走到。
        """
        if not self.want_urls:
            # 判不了 → 放行（旧口径）。为什么不是「永远刷新」见上面 ⚠️ 那一段。
            return any(self._frame_url(fid) for fid in self.live_frames)
        main = self._url()      # 主帧此刻的地址：下面那道闸比的就是它
        for fid in self.live_frames:
            url = self._frame_url(fid)
            if url and url != main and any(want in url for want in self.want_urls):
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
        它们活着、当时那道判据（`_live_frames_ok()`，2026-09-20 改名 `_frame_table_is_usable`
        并换成「地址命中」口径）于是永远为真 → **再也不会去找那个真正的问卷帧** →
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
        #: ★ 2026-09-23：这一跳的结论（**只属于触发它的那一步**，与 observe_why 同规矩）。
        #: `resolve_why` = 命中了哪几样身份，`resolve_score` = 把握（0~1）。
        #: 这两个格子进 trace，是 Repair Agent 以后的素材：**哪些脚本已经靠恢复在苟活**。
        self.resolve_why = None
        self.resolve_score = None
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
                score, why = self._intent_score(element, target, kind)
                hits.append((score, why, element))
            if hits:
                break

        # 排序：**先按「像不像」**（命中了几样身份），再按稳定性评级（D3），
        # 最后「首屏看得见的先试」（折线下那个能用，见 _usable，但先试它可能白滚一屏）。
        # ⚠️ 为什么把语义分排在稳定性前面：稳定性说的是**这条地址**抗不抗改名，
        # 而这里是「**这个元素**是不是账本里那个」—— 认错元素的代价（点到别的框/别的按钮）
        # 比地址脆得多。地址脆还有 alternates 与下一次重找兜着。
        rank = {"high": 0, "medium": 1, "low": 2}
        # ★ 2026-10-13：分数并列时，**多对上一条 `nearby_text` 的排前面**（见 `_nearby_hits`）——
        # 一排同 role 的圆点里人人靠「邻居那句」凑上 nearby_text，不数条数就等于没分。
        hits.sort(key=lambda t: (-t[0], -self._nearby_hits(t[2], target),
                                 rank.get((t[2].get("stability") or "").lower(), 3),
                                 _below_fold(t[2])))
        if hits:
            self.resolve_score, self.resolve_why = hits[0][0], hits[0][1]
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
        for _, _, element in hits:
            frame = _frame_of_element(element, fallback)
            for selector in [element.get("selector")] + list(element.get("alternates") or []):
                if selector and (selector, frame) not in out:
                    out.append((selector, frame))
        return out

    def _nearby_hits(self, element, target):
        """账本那一列 `nearby_text` 里**对上了几条**（0 = 一条都没对上）。

        为什么要数条数、而不只是「有没有对上」（★ 2026-10-13，真站 `job-37d8e6bfa080`
        那一族）：一排同 role 的候选（compareinsulation 那六颗圆点）里，每颗的
        `nearby_text` 都带着**邻居那一句**（自己那句在前、邻居那句在后）——
        「有没有对上」人人有份、`_intent_score` 并列，排序就退化回池子顺序 =
        **按页面上谁在前点谁**（先红用例实测：`#option-2` 被点成了上一颗）。
        自己那一句也在里面 ⇒ 对上的**条数**才是分得开的那把尺子。
        """
        want = [_norm(str(x)).lower() for x in (target.get("nearby_text") or [])
                if str(x).strip()]
        if not want:
            return 0
        have = " ".join(_norm(str(x)).lower() for x in (element.get("nearby_text") or []))
        return sum(1 for w in want if w in have)

    def _intent_score(self, element, target, kind):
        """这个候选**像不像**账本里记的那一个：命中的身份信号越多，分越高（0~1）。

        ★ 2026-09-23（用户与同事定的路线）：产物手里那几格（text / label / role /
        placeholder / near / nearby_text）**本来就写着意图** —— 是运行期只按 selectors
        找，地址一失效整步作废。这一格把「当初为什么是它」变成**可评分的证据**，
        让回退链按分数试，而不是按 observe 返回的顺序试（顺序 = 页面上谁在前，
        与「当初点的是哪一个」没关系）。

        返回 `(分数, 命中了哪几样)`。后者进 trace（`resolved_by`），前者的用途**只有排序**：
        这里**不做**「低于阈值就不用」的闸 —— 那种闸会在「几个孪生控件语义分不开」的站上
        直接把候选清空（真站实测：三个组合框的 text 都是零宽空格），而那时
        「按语义都像」本身就说明**不该由分数来否决**。
        """
        want = {
            "text": (_norm(target.get("text") or "").lower(),
                     _norm(element.get("text") or "").lower()),
            "label": (_norm(target.get("label") or "").lower(),
                      _norm(element.get("label") or element.get("aria_label") or "").lower()),
            "placeholder": (_norm(target.get("placeholder") or "").lower(),
                            _norm(element.get("placeholder") or "").lower()),
            "role": ((target.get("role") or "").strip().lower(),
                     (element.get("role") or "").strip().lower()),
            "near": ((target.get("near") or "").strip().lower(),
                     (element.get("region") or "").strip().lower()),
        }
        consider = [k for k, (w, _) in want.items() if w]
        hit = [k for k in consider if want[k][0] in want[k][1]]
        # nearby_text 是**一列**短句（探索时从控件周围收上来的），它算一样。
        near_want = [_norm(str(x)).lower() for x in (target.get("nearby_text") or [])
                     if str(x).strip()]
        if near_want:
            consider.append("nearby_text")
            have = " ".join(_norm(str(x)).lower() for x in (element.get("nearby_text") or []))
            if any(w in have for w in near_want):
                hit.append("nearby_text")
        if not consider:
            # 账本里这一格一条可比的身份都没有（老产物 / 只记了选择器）—— 交 0.5 当
            # 「说不上像也不像」，让它靠稳定性与首屏去排，别凭空判它不像。
            return 0.5, "账本里没有可比的身份信号"
        return len(hit) / float(len(consider)), "+".join(hit) or "一条身份都没对上"


    # ── 动作 ────────────────────────────────────────────────

    def _do(self, action, selector, value=None, kind="value", frame_id="", label=""):
        """一个动作只走这一条路：cdp 命令（规格 §5.2：动作一律走 cdp，不手拼 JS）。

        `frame_id` 交给 `CDPHelper.click / form` 的**同名参数**（`forms/common.py:146` /
        `:202` 本来就收它，命令行上是 `--frame-id`）。元素在跨源 iframe 里时，**同一个
        选择器在主帧里命中 0** —— 不带这一帧，重放每跑一遍都会「页面上没找到」，
        而这件事在账本里原本看不出来（那份产物没有「帧」这个概念）。
        """
        if action == "click":
            self._followed_tab = ""       # 每一步开头清空：这半句只属于触发它的那一步
            # 点**之前**先记下有哪些页：点完冒出来的**新**页，就是这一下开的 tab
            # （`target="_blank"` 那类）—— 见 `_follow_new_tab` 里那段真站实测。
            before_ids = {t.get("id") for t in self._page_targets()}
            out = self.cdp.click(selector, frame_id=frame_id or "")
            self._followed_tab = self._follow_new_tab(before_ids)
            return out
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

    def _rule_value(self, rule, fill):
        key = (rule, tuple(fill.get("prefixes") or ()),
               str(fill.get("date_format") or ""))
        if key in self._rule_values:
            self._value_from = "规则「%s」现编（同一趟同值）" % rule
            return self._rule_values[key]
        if rule == "phone_prefix":
            prefixes = [str(x).strip() for x in (fill.get("prefixes") or [])
                        if str(x).strip()]
            prefix = random.choice(prefixes) if prefixes else "04"
            value = prefix + "".join(str(random.randint(0, 9))
                                     for _ in range(max(0, 10 - len(prefix))))
            self._value_from = "规则「手机号 %s」现编" % ("/".join(prefixes) or prefix)
        elif rule == "dob_random":
            fmt = str(fill.get("date_format") or "DD/MM/YYYY")
            year = time.localtime().tm_year - random.randint(19, 68)
            value = _format_date(random.randint(1, 28), random.randint(1, 12), year, fmt)
            self._value_from = "规则「生日 %s，18-68」现编" % fmt
        elif rule == "password_strong":
            value = _strong_password()
            self._value_from = "规则「强密码」现编"
        else:
            raise ValueError("产物写错了：不认识的规则「%s」" % rule)
        self._rule_values[key] = value
        return value

    def _fill_value(self, fill, step):
        """这一步填什么：先读 --form-file 里的键，没有就用 fallback 里的一个随机值。

        ★ 2026-09-28（mapleporchdaily 真站排查）加了三样，都是那次踩出来的：

        ① **别名回退**（`_ALIASES`）：`source` 是照页面字段名起的、资料的键是资料库列名，
           两边没约定 ⇒ 取不到就**静默**掉进随机池。
        ② **全名拆分**（`_SPLIT_FROM`）：资料只给一个全名时，First / Last 各取一段。
        ③ **说出值是哪来的**（`self._value_from`，由调用方拼进 note）：
           填上了不等于填对了 —— 「这值是**任务资料**的还是**随机池**的」必须让人一眼看见。
           ⚠️ 只报**来源**，**不报值本身**（生产日志会上报到后端，姓名/电话没必要进那儿）。
        """
        self._value_from = ""
        rule = str(fill.get("rule") or "").strip()
        if rule:
            return self._rule_value(rule, fill)
        source = fill.get("source")

        def take(key):
            return str((self.form_data or {}).get(key) or "").strip() if key else ""

        if source:
            given = take(source)
            if given:
                self._value_from = "任务资料「%s」" % source
                return given
            for alt in _ALIASES.get(source, ()):
                given = take(alt)
                if given:
                    self._value_from = "任务资料「%s」（别名 → %s）" % (alt, source)
                    return given
            #: 资料只给全名时按空格拆：`username: "Lin Cameron"` → Lin / Cameron
            whole = take(_SPLIT_FROM.get(source, ""))
            if whole and " " in whole.strip():
                parts = whole.split()
                got = parts[0] if source == "first_name" else " ".join(parts[1:])
                self._value_from = "任务资料「%s」拆出（%r → %s）" % (
                    _SPLIT_FROM[source], whole, source)
                return got
        #: ★ 2026-10-13：**没取到任务资料，就先照「真站收过的那个值」的形状编一个** ——
        #: 排在随机池**前面**。为什么（真事 `job-a5f74744a7f9` · healthinsurancewatch.com.au）：
        #: 探路那趟在真站上填的是 `0412345678`（澳洲手机，页面认了），重放时那一格走了随机池，
        #: 而池子是照 `PROVENANCE['env']['proxy_country']`（**代理出口**在哪）分的 —— 那趟是 US
        #: ⇒ 一个美国形状的号填进 `04xx xxx xxx` 的框 ⇒ 站方红字 `Please enter a valid mobile
        #: number`、**提交那一下卡死**，而每一步的日志都写着「填好了」（最难查的那种）。
        #: **站点在哪**与**出口在哪**是两件事，而「真站收过的那条值」是**不必猜**的那份证据。
        #: ⚠️ 只在自由输入格（`kind == "value"`）上走这一支；下拉/勾选照旧吃账本里真选过的那个值。
        shaped = _shaped_like(str(fill.get("sample") or "")
                              if str(fill.get("kind") or "value") == "value" else "")
        if shaped:
            self._value_from = "**照真站收过的那个值的形状**编的（避开了随机池的国家档）"
            return shaped
        candidates = list(fill.get("fallback") or [])
        if not candidates:
            self.log.warning("[%s] 「%s」既没有 form-file 的值也没有 fallback", self.cid, source or "?")
            self._value_from = "**没有来源**（form-file 与 fallback 都空）"
            return ""
        pick = random.choice(candidates)
        if isinstance(pick, dict) and pick.get("random"):
            self._value_from = "**随机池**「%s」" % pick["random"]
            return self._random_value(str(pick["random"]))
        self._value_from = "**随机池**（写死的候选）"
        return str(pick)

    def _value_note(self):
        """「这个值是哪来的」那半句（★ 2026-09-28）—— 没有就空串（不是填值那步）。"""
        return ("（值来自 %s）" % self._value_from) if getattr(self, "_value_from", "") else ""


    # ── 新 tab（`target="_blank"`）：点完要认得出来、并且**切过去** ──────────
    def _page_targets(self):
        """当前有哪些**页**（tab）。走 `/json/list`；读不到就返回 `[]`（**不抛**）。

        ⚠️ 为什么要有这一格（2026-09-28 georgiapower 真站实测）：那个 CTA 是
        `<a target="_blank" href="…">` —— 点它是**新开一个 tab**，而 cdp 每次调用挑的是
        「可见 / 最近激活的那一页」。新 tab 若在**后台**开着，后面每一步读到的都还是旧页
        ⇒ 判据全不成立、整段步骤被静默跳过 —— 那一趟 25 步里只做成 2 步、23 步被跳过，
        结账是 `no_success`，而日志里每一步都写着「点上了/填好了」。
        自测那次之所以过，是新 tab 恰好抢到了前台：**运气，不是逻辑**。
        """
        try:
            import urllib.request as _u
            raw = _u.urlopen("http://%s:%s/json/list" % (self.cdp.host, self.cdp.port),
                             timeout=3).read()
            return [t for t in json.loads(raw)
                    if t.get("type") == "page" and t.get("id")]
        except Exception:
            return []

    def _activate_tab(self, target_id):
        """把一个 tab 弄到前台（`GET /json/activate/<id>`）。失败就算了 —— **不抛**。"""
        try:
            import urllib.request as _u
            _u.urlopen("http://%s:%s/json/activate/%s"
                       % (self.cdp.host, self.cdp.port, target_id), timeout=3).read()
            return True
        except Exception:
            return False

    def _follow_new_tab(self, before_ids):
        """点完**新开**的 tab 就切过去，返回它的地址（没有新的就是空串）。

        挑哪一个：优先地址命中本流程某个 `when.url_contains` 的那个 —— 广告站一次点击
        开出好几个 tab 是常态，挑错等于白切；都没有可比地址时退回**最新**那个
        （`/json/list` 的顺序近似 MRU）。
        """
        new = [t for t in self._page_targets() if t.get("id") not in before_ids]
        if not new:
            return ""
        pick = None
        for t in new:
            url = str(t.get("url") or "")
            if any(w and w in url for w in self.want_urls):
                pick = t
                break
        pick = pick or new[-1]
        if not self._activate_tab(pick["id"]):
            return ""
        time.sleep(TAB_SETTLE_SECONDS)
        return str(pick.get("url") or "")

    def _follow_tab_matching(self, when):
        """判据成立的那一页**在另一个 tab 上**时，把它切过来（返回切过去的地址）。

        与 `_follow_new_tab` 互补：那条管「刚点的这一下新开了 tab」，这条管
        「tab 早就开着、只是没在前台」（上一步开的、当时没抢到前台那类）。
        """
        want = str((when or {}).get("url_contains") or "")
        if not want:
            return ""
        here = self._url()
        for t in self._page_targets():
            url = str(t.get("url") or "")
            if want in url and url != here and self._activate_tab(t["id"]):
                time.sleep(TAB_SETTLE_SECONDS)
                return url
        return ""

    def _verify_the_hit(self, when, name):
        """判据不成立 → 复核**刚才那一击**到底落地没有；动过了返回 True（调用方据此重判）。

        ★ 2026-09-29（交接《骨架还缺什么》§8，georgiapower 开窗实测）：
        `_follow_new_tab` 在 `cdp click` 返回后**立刻**看一次 tab 列表 —— 实测那时候
        **一定没有新 tab**（弹窗 2.26 秒才出现、而且**先以空地址出现**，4.3 秒才有地址）。
        于是「点了但页面什么都没动」与「点成了」长得一模一样：生产 `26233250` / `26226785`
        就是这么跑完两分钟、20 步全跳过，而每一步日志都写着「点上了」。

        三步，**代价只落在这一条路上**（判据不成立时；成功路径一次都不碰）：
          ① 有没有**晚到的新 tab**（与我们见过的 id 集合比）⇒ 切过去；
          ② 判据要的那一页是不是**早就在、只是没在前台**（`_follow_tab_matching`）⇒ 切过去；
          ③ 两个都不是 ⇒ 先问**刚才那一击到底动没动页面**：`progress is True`（diff 判得出
             页面变了）⇒ **它已经落地了**，不许再点一次；只有判不出/判出没动时才把那一击
             重试一次（进 `_perform`，所以先过 §7 那道闸：文档到 interactive + 元素中心点得到
             的还是它自己）。
             ⚠️ ★ 2026-10-20（真事 `job-4f7f53be42eb` · delamere）：少了前半句，判据不成立
             就会变成「回头重点一个已经不在页面上的元素」—— 真事里第 3 步点「For myself」
             成功、页面已经到下一屏，下一组的门（`Facebook`）不成立 ⇒ 回头重点 ⇒
             「页面上没找到「For myself」」⇒ 自测把它读成**选择器族**、修稿一直改选择器 ⇒
             `repair_stuck`。判据不成立照旧记「跳过」（跳过是没做），但它**不是**「没落地」。
        ⚠️ **一组只重试一次**（`_retried_groups`）：重试的代价是**在真页面上多做几个真实动作**，
        而运营口径是「刷太多不太好」—— 这一格不许随着失败组数累积。
        ⚠️ 它**只进日志、不进 trace**：trace 的每一行都是「一步」（下游按 `line["ok"]` 读它），
        这里加一行没有 `ok` 的会把读的人读崩 —— 与「没走到成功」那句同一条规矩。
        """
        if self._last_hit is None:
            return False                       # 还没动手过：没什么可复核的
        # ① 晚到的新 tab
        tabs = self._page_targets()
        if tabs:
            before = set(self._seen_tabs)
            self._seen_tabs |= {str(t.get("id") or "") for t in tabs}
            fresh = [t for t in tabs if str(t.get("id") or "") not in before]
            if fresh and before:               # 第一次列不算「新到」——没有基线
                pick = None
                for t in fresh:
                    url = str(t.get("url") or "")
                    if any(w and w in url for w in self.want_urls):
                        pick = t
                        break
                pick = pick or fresh[-1]
                if self._activate_tab(pick["id"]):
                    time.sleep(TAB_SETTLE_SECONDS)
                    self.log.info("[%s] ⚠️ 判据不成立 —— 但**晚到的新 tab 出现了**（%s），"
                                  "已经切过去；这一组不跳过", self.cid,
                                  str(pick.get("url") or "")[:80])
                    return True
        # ② 那一页早就在、只是没在前台
        if self._follow_tab_matching(when):
            self.log.info("[%s] ⚠️ 判据不成立 —— 但**那一页在另一个 tab 上**，已经切过去",
                          self.cid)
            return True
        # ③ 重试那一击（一组一次）—— **只在这一击判不出「页面动过」时**
        index, step, progress = self._last_hit
        if progress is True:
            #: ★ 2026-10-20（见 docstring 里那条真事）：这一击**判得出页面动过** ⇒ 它不是
            #: 「没落地」。再点一次只会在**新的一页**上找一个已经不存在的元素，那句
            #: 「页面上没找到 X」还会被自测读成**选择器族**（改稿治得了）⇒ 修稿改错地方。
            self.log.info("[%s] 「%s」的判据不成立 —— 但**上一击判得出页面动过**"
                          "（progress=True），不再回头重点第 %d 步：这一页只是不像那个状态",
                          self.cid, name, index)
            return False
        if name in self._retried_groups:
            return False
        self._retried_groups.add(name)
        self.log.warning("[%s] ⚠️ 「%s」的判据不成立，而%s —— "
                         "把第 %d 步（%s）**重来一次**（这一组只重试这一次）",
                         self.cid, name,
                         "页面**一点没动**" if progress is False
                         else "这一击**判不出**落地没有",
                         index, _label((step or {}).get("target"), step))
        self._run_step(index, step)
        return True

    def _wait_until_present(self, action, selector, value, kind, frame, label, level, until):
        """同一条地址**没找到**时：先等一等，再试它**自己**（等到这笔预算用完为止）。

        成功返回那次的回执；预算用完还没出来返回 `None`（调用方把**第一次那个失败**
        原样留着，后面的路一个字不变）。

        为什么值得等（而不是立刻换候选 / 掉进重找）：那两条路的代价都大得多 ——
        重找可能**猜错**（真事里就猜错了：见 `NOT_FOUND_WAIT_SECONDS` 那段），
        整组重走更要在真页面上多做一串动作；而这儿只是把**同一根地址**再发一次。
        ⚠️ 只重试同一根地址；等到头还没出来就交回去 —— 不猜、不换。
        """
        waited = 0.0
        announced = False
        while True:
            left = until - time.monotonic()
            if left <= 0:
                if announced:
                    self.log.info("[%s] 等了 %.1f 秒它还是没出来 —— 不等了，照旧往下试别的路",
                                  self.cid, waited)
                return None
            if not announced:
                announced = True
                #: ⚠️ 报**这一笔真正剩多少**，不报常数：预算有两档（6 / `LATE_ELEMENT_WAIT_SECONDS`，
                #: 见 `late_element`），读日志的人得看得出这一组用的是哪一档。
                self.log.info("[%s] 第 %d 个选择器**没找到**（不是被盖着）—— 可能是还没渲出来，"
                              "先等一等：最多 %.0f 秒，一直重试**同一根地址**",
                              self.cid, level + 1, max(0.0, until - time.monotonic()))
            nap = min(NOT_FOUND_POLL_SECONDS, left)
            time.sleep(nap)
            waited += nap
            out = self._do(action, selector, value, kind, frame, label)
            if _ok(out):
                self._miss_waited = waited
                self.log.info("[%s] 等了 %.1f 秒，它出来了 —— 还是这一根地址做成的",
                              self.cid, waited)
                return out

    def _miss_note(self):
        """「第一次没找到、等了一会儿它才出来」说给人听的那半句（没有就是空串）。"""
        waited = getattr(self, "_miss_waited", 0.0)
        if not waited:
            return ""
        return ("；**第一次没找到它，等了 %.1f 秒它才出来**（元素是异步渲出来的 —— 地址没错）"
                % waited)

    def _tab_note(self):
        """「点完新开了一个 tab 并切了过去」说给人听的那半句（没有就是空串）。"""
        if not getattr(self, "_followed_tab", ""):
            return ""
        return "（点完新开了一个 tab：%s —— 已切过去）" % self._followed_tab[:80]

    def _random_value(self, kind):
        """数据随机化（拟人）：每次重跑都同一份数据是明显的机器味。

        ⚠️ ★ 2026-09-29：邮编 / 手机 / 州 这三档**按站点国家走 `self.pools`**
        （见 `COUNTRY_POOLS`）—— 美国站不许被填英国邮编、英国站不许被填美国州；
        国家不知道时 `self.pools` 是 `UNKNOWN_POOLS`（分流之前逐字相同那一套），
        而「不知道」这件事已经由 `_say_country()` 出声。
        """
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
            return random.choice(self.pools["phone"])
        if kind == "postcode":
            return random.choice(self.pools["postcode"])
        #: ★ 2026-09-28：`number` / 地址那几档是**新认出来的种类**（`browser_agent._RANDOM_HINTS`
        #: 与 `_fallback` 一起加的）—— 这里不接的话，产物拿到 `{"random": "number"}` 会**当场抛**
        #: （`_random_value` 原先只认七个种类）。
        if kind == "number":
            return str(random.randint(1, 5))          # 人数/件数那一类：给个小的正整数
        if kind == "city":
            return random.choice(("Springfield", "Riverton", "Fairview", "Greenville",
                                  "Madison", "Clinton", "Georgetown", "Salem"))
        if kind == "street":
            return "%d %s %s" % (random.randint(100, 9899),
                                 random.choice(("Maple", "Oak", "Cedar", "Pine", "Elm", "Birch")),
                                 random.choice(("St", "Ave", "Rd", "Ln", "Dr")))
        if kind == "country":
            #: 知道国家就给那一国的名字（美国站不该填出「United Kingdom」）；
            #: 不知道就照旧随机 —— 老口径一个字不动。
            return (str(self.pools["country"]) if self.country
                    else random.choice(("United States", "United Kingdom")))
        if kind == "state":
            return random.choice(self.pools["state"])
        if kind == "birth_year":
            #: ★ 2026-10-08（policyquail `job-c8730acb27c5`，8100 上验出来的那一趟）：
            #: 「Year of birth」那种框（`placeholder="YYYY"`）只收**4 位年份**，
            #: 而下面 `dob` 给的是整串日期 ⇒ 往年份框里打了 `07/23/1981`。
            #: 区间与 `dob` 用的那一段**是同一个**（说的本来就是同一个出生年）——
            #: 改一处要想着另一处，别让两边悄悄分叉。
            return str(random.randint(1970, 1995))
        if kind == "dob":
            return "%02d/%02d/%d" % (random.randint(1, 12), random.randint(1, 28),
                                     random.randint(1970, 1995))
        if kind == "dob_dmy":
            #: ★ 2026-10-20（belong）：站点 label 明确要 **DD/MM/YYYY**（日/月/年）。
            #: 年份取 [今年-68, 今年-19] ⇒ 不论生日过没过，年龄都落在 18-68
            #: （今年-19 且生日未到 = 18；今年-68 且生日已过 = 68）。
            year = time.localtime().tm_year - random.randint(19, 68)
            return "%02d/%02d/%d" % (random.randint(1, 28), random.randint(1, 12), year)
        if kind == "password":
            #: 至少 8 位，含大写/小写/数字/特殊字符；`!` 在站点要求的 `!@#$$%^&*` 里。
            return "Test%d!" % random.randint(1000, 9999)
        raise ValueError("产物写错了：不认识这个随机值类型「%s」" % kind)

    def _wait_when(self, when, timeout=None) -> bool:
        """**有界等这条 `when` 成立**：每 0.5 秒重判一次，最多 `timeout` 秒。

        与 `_wait_ready` 的分工：那个等的是**文档加载完**（`readyState=complete`，对外层页
        恒真 ⇒ 等于没等），这个等的是**判据自己成立**（地址/正文对得上）—— 后者才是
        「这一组步骤该不该做」的门。两个都留着：先等判据（能成就立刻往下），
        再等文档（判据成了但正文还在长的时候，让它在稳定下来之后再判一次）。
        ⚠️ 返回 `timeout` 内成没成，**调用方照旧自己再判一次**（不许拿这个返回值当结论 ——
        判的那一下必须和说 why 的那一下是同一刻）。
        """
        limit = float(WAIT_WHEN_SECONDS if timeout is None else timeout)
        step = 0.5
        waited = 0.0
        while True:
            try:
                if self._matches(when):
                    return True
            except Exception:                          # noqa: BLE001 —— 判据读不动就当没成立
                pass
            if waited >= limit:
                return False
            time.sleep(step)
            waited += step

    def _wait_success(self, timeout=None):
        """等**页面上出现成功文案**，最多 `SUCCESS_WAIT_SECONDS` 秒；见到了立刻回 `True`。

        为什么不是「每步之后都等」：那样每一步都要白等（而绝大多数步之后不会有成功页）。
        收尾这一笔只花在**该等的那一次**上 —— 走完了、还没见到 ⇒ 再给它这几秒。
        ⚠️ 每一次轮询都是**真读一次页面**（`page_signature()`），所以间隔写在
        `SUCCESS_POLL_SECONDS` 上、而且**有界**（不许在这里无限等：它是产物，跑在生产上）。
        """
        limit = float(SUCCESS_WAIT_SECONDS if timeout is None else timeout)
        if self._succeeded():
            return True
        if limit <= 0:
            return False
        self.log.info("[%s] 还没见到成功文案 —— 再等最多 %.0f 秒（成功页常常是异步渲染出来的）",
                      self.cid, limit)
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            time.sleep(float(SUCCESS_POLL_SECONDS))
            if self._succeeded():
                self.log.info("[%s] 等到了：成功判据成立了", self.cid)
                return True
        return False

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

    def _popup_row(self, ok, why):
        """弹层这一下**如实**落一行 trace（`ok`：True = 关掉了 / False = 它还在 / None = 这一次没量到）。

        ★ 2026-10-17（终审 C3）：与循环型的 `_popup_trace` **同一套三值** —— spec §3.4
        第一句就是「**cdp 回 ok 不算做成**，要再探一次」，而这边原来只按 cdp 的回话记日志。
        三条纪律与那边逐字同义：
          ① `ok` 只由**复核**给（再问一遍同一张表），**不许**拿 `cdp click` 的回话当做成
             （那句回话说的是「选择器命中 1 个」，不是「点到的就是它」）；
          ② `soft=True`：弹层是**环境噪声** ⇒ 自测不拿它否决那一趟（`selftest._verdict`
             的辅助档），但照旧点名 —— spec §3.5：探针自己永远不许变成新的失败点；
          ③ `note` 与 `progress_why` **两格都写**（同 `_stop` 那条 2026-10-14 的规矩）。
        ⚠️ 只在**探针答过话**（或量不出来）时才落行 —— 页面上压根没有这类东西时照旧**不落**
        （那是这一族的常态，不是一步；表这一族的 trace 契约是「每一行都是一步」）。
        """
        self._trace({"step": self.step, "action": "popup", "ok": ok, "soft": True,
                     "note": why, "progress_why": why})

    def _popup_still_there(self):
        """复核：**再问一遍同一张表**，返回还在的那一个（空串 = 这一屏没有它了）。

        三种结果，**不许并成两种**（与循环型 `clear_popups` 那份逐字同义）：
          · 空串 = 没有它了；
          · 非空 = 它还在（返回钩子表点名的那个）；
          · `None` = **这一次没量到**（读炸了 / 答复形状不认识）—— 量不到就如实说量不到，
            把它读成「关掉了」正是这一刀要治的病（Task 1 真站量到：同一站首载有同意层、
            来回一趟之后同一条探针返空 ⇒ 「返空」分不清「关掉了」与「本来就不在」）。

        ★ 2026-10-19：**短轮询**（有界）—— 同意层的关闭是异步的（真站 compareinsulation：
        点 accept 之后紧接的那一次再探还会看到它，再晚一点点才没了）。只在「复核还答**可点**
        行」时等；返空 / 只答 `_REPORT_ONLY` 都立即收。带 `_REPORT_ONLY` 的行（表尾的
        「广告 creative」）本来就不挡路 —— 不排掉它，一次真的关掉了会被记成 `ok:false`
        （真站 wildnutrition 量到：`Close filters drawer`）。
        """
        try:
            deadline = time.time() + POPUP_RECHECK_WAIT_SECONDS
            while True:
                raw = str(self._ev(_POPUP_PROBE_JS) or "").strip()
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
        except Exception:                                  # noqa: BLE001 —— 外面世界
            return None

    def _clear_obstructions(self, why=""):
        """开跑（以及每次导航之后）把**挡路的弹层**点掉：问一遍钩子表（`_POPUP_HOOKS`），
        谁先答应就点谁（今天：同意条 → 盖住整页的模态）。要加一类弹窗，**改表**，不改这里。

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

        只做**有把握**的那一下：得在一个可见的同意类容器里找到一个按钮，它的文字属于
        「接受/同意/关闭/拒绝」那一族 —— **带 id 就点 `#id`**，**只有文字就按文字打标记再点**
        （★ 2026-09-28，用户看到的现场「有个弹窗挡住了」；原来那种「没 id 就什么都不做」
        等于把弹层留在页面上挡住后面每一步 ✗）。两条都走不通就**什么都不做**（宁可不做），
        由 `_covered_by` 保证「点到弹层上」不会被记成做成了。

        ★ 2026-09-29：**每一步动手之前也会调它**（`_perform` 里那一处）。起因是
        travelandleisure 的生产失败（25 条里 18 条）：弹层**是页面加载完之后才异步冒出来的**，
        只在「开跑前 / 导航后」看一眼是不够的 —— 点「Join Now」时它已经压在按钮上，
        日志原话 `第 2 个选择器指的元素被 div#onetrust-group-container.ot-sdk-eight 盖着`，
        然后 `连着 3 步没做成，收摊`。既然它现在挨着**每一步**，它就**不许自己变成新的
        失败点**：探不动 / 点不动一律只记一行 INFO 走人（与 `warthunder.py` 的
        `_dismiss_consent` 同一条口径：它是为了修失败才加的，自己再变成失败点就本末倒置）。

        ★ 2026-10-17（终审那一波，三条都在这儿）：
          · **C3 · spec §3.4**：点完**再探一次**（`_popup_still_there`）才下结论 ——
            「cdp 回 ok 不算做成」，三值（True/False/None）写进日志**与**账本（`_popup_row`）。
            这一天之前这里只按 cdp 的回话记「看起来成了 / 没成」；
          · **C2 · Ruling 30**：表里带 `_REPORT_ONLY` 的那一行**只报不点**（认得出、记一行 INFO
            与一行账，**不动手**）—— 真站上已经量到它误点（实测 §1.3）；
          · **Ruling 28-②**：答复是 `TEXT|` 而**文字是空的** ⇒ **不点**（那是「指向不确定
            元素的地址」，与「形状不认识」同一处置）。
        """
        try:
            raw = str(self._ev(_POPUP_PROBE_JS) or "")
            #: 答复是 `"<钩子名>|<选择器|TEXT>|<按钮文字>"`（三格，表见 `_POPUP_HOOKS`）。
            parts = raw.split("|", 2)
            if len(parts) != 3:
                #: 空串 = 表里一条都没答应（**正常情况**，页面上就是没有这类东西）—— 静默是对的。
                #: 但**形状不认识 ≠ 没有弹层**：那是「量不出来」，说出来（与本文件里
                #: `LOADER|` / `COVER|` 那两条前缀同一个道理），只是不当成「有弹层」去点。
                if raw.strip():
                    self.log.info("[%s] 弹窗探针的答复形状不认识（%r）—— 当它没答，"
                                  "照旧走这一步自己的判据", self.cid, raw[:60])
                    #: ★ 2026-10-17（终审 C3）：**量不出来 ≠ 没有弹层** —— 账上也说一句
                    #: （与循环型那份同一处置；`ok=None` = 这一次没量到，不当成做成）。
                    self._popup_row(None, "弹窗探针答复形状不认识（%r）—— **量不出来**"
                                          "（这一次没量到覆盖现场），不当成做成" % raw[:40])
                return
            who, how, text = parts
            if _popup_report_only(who):
                #: ★ 2026-10-17（终审 C2 · Ruling 30）：这一行**只报不点** —— 探针照认
                #: （「认得出哪一颗」这一半归探针与合成页矩阵），但**不动手**。
                #: 为什么：真站上已经**量到误点**（实测 §1.3：它把站点自己的抽屉控件
                #: `Close filters drawer` 当成关闭键点了），而「加容器锚」那把尺子对
                #: **这一型**还没有正向证据 ⇒ 不点，直到真 creative 上验过「可见 + 有落点」。
                #: ⚠️ 记账/日志要说清**为什么没点** —— 不然读的人会以为探针没答。
                self.log.info("[%s] 认出了「%s」（%s，%r）—— 但**没有动手**：这一型还没有"
                              "真站正向证据，按 Ruling 30 **只报不点**（照 `_REPORT_ONLY`）",
                              self.cid, who, how, text[:30])
                self._popup_row(None, "认出了「%s」（%s，%r）但**没有动手**：这一型还没有"
                                      "真站正向证据，按 Ruling 30 只报不点"
                                      % (who, how, text[:30]))
                return
            if how == "TEXT" and not text.strip():
                #: ★ 2026-10-17（终审 Ruling 28-②）：`TEXT|` 而**文字是空的** —— 那是
                #: 「**指向不确定元素的地址**」（按文字打标记 = 按空串找一颗空文字的按钮，
                #: 页面上这种按钮可能有好几颗）⇒ 与「形状不认识」同处置：说出来、**不点**。
                #: 真站实测（实测文档 §1.3）：`广告 creative|TEXT|` 就是这么答的。
                self.log.info("[%s] 「%s」答的是 `TEXT|` 而**文字是空的** —— 那是一个"
                              "**指向不确定元素的地址**（按空文字打标记可能点到别的东西）："
                              "不猜、不点", self.cid, who)
                self._popup_row(None, "「%s」答的是 `TEXT|` 而文字是空的 —— **指向不确定"
                                      "元素的地址**：没敢点" % who)
                return
            js_done = (how == "JS")
            if js_done:
                #: 钩子自己已经用 JS 的 `.click` 关掉了（绕开被 sticky header 盖住的落点判据）；
                #: 这一步**不再走 cdp click** —— 但下面的复核照旧（还在 ⇒ 记没关掉）。
                selector = "JS"
                out = None
            else:
                if how == "TEXT":           # 只有文字：打标记再点（点还是走 cdp）
                    marked = self._ev(_MARK_CONSENT_BY_TEXT_JS % json.dumps(text))
                    if "marked" not in str(marked or ""):
                        self.log.info("[%s] 「%s」那颗按钮（%r）没有 id、按文字也没标记上 —— 不猜，"
                                      "留给 `_covered_by` 兜（下一步会被挡住并如实失败）",
                                      self.cid, who, text[:30])
                        #: 探之前**有**、而我们**没动手** ⇒ 它当然还在（不用再探一次也知道）。
                        self._popup_row(False, "「%s」还在（认不出该点哪一颗 —— 没敢点）" % who)
                        return
                    selector = '[data-sf-consent="1"]'
                else:
                    selector = how
                out = self.cdp.click(selector)   # 动作一律走 cdp（§5.2）
            if how == "TEXT":
                #: 标记用完就清（不清的话下一屏上会留一个 `[data-sf-consent="1"]`，那是脏状态）
                self._ev("var a=document.getElementsByTagName('*');"
                         "for(var i=0;i<a.length;i++){if(a[i].removeAttribute)"
                         "a[i].removeAttribute('data-sf-consent');}return 'clean';")
            #: ★ 2026-10-17（终审 C3 · spec §3.4）：**cdp 回 ok 不算做成** —— 点完**再探一次**
            #: 同一张表（`_popup_still_there`，**同一把尺子**：同一个 `_ev(_POPUP_PROBE_JS)`）。
            #: 三值，与循环型那份逐字同义：探之前有 + 探之后没有 ⇒ True；探之后**还有** ⇒ False；
            #: 复核探不动（读炸了 / 答复形状不认识）⇒ None（量不到就如实说量不到）。
            still = self._popup_still_there()
            if still is None:
                verdict, said = None, ("点完复核不了：探不动（这一次没量到覆盖现场）"
                                       "—— 不当成做成")
            elif still:
                verdict, said = False, ("点完复核：这一屏**还有**「%s」（探之前有、探之后还有）"
                                        "—— 这一下没能关掉" % still)
            else:
                verdict, said = True, "点完复核：这一屏没有它了（探之前有、探之后没有）"
            self.log.info("[%s] %s把「%s」%s（%s，%r）—— %s%s", self.cid,
                          ("%s之后 " % why) if why else "开跑前 ", who,
                          ("用 JS 关掉" if js_done else "点掉了"),
                          selector, text[:30], said,
                          #: ⚠️ cdp 的回话**不是判据**（它只说「选择器命中 1 个」）—— 只在它说
                          #: 「没成」时补一句，免得读日志的人以为上面那一句是从回话推出来的。
                          "" if (js_done or _ok(out)) else "（另外 cdp 的回话说这一下**没成**）")
            self._popup_row(verdict, said)
        except Exception as exc:                           # noqa: BLE001
            #: **永不失败**：读不到 / 点不动都只记一行，绝不往上抛 ——
            #: 往上抛会把这**一步**判成「产物自己出错了」（`_run_step` 里那条 except 会
            #: 直接结束整趟），而弹层只是环境带来的噪声，不该有这种权力。
            self.log.info("[%s] 看/点弹层这一下没成（%s: %s）—— 当它没有，照旧往下走",
                          self.cid, type(exc).__name__, exc)
            #: ★ 2026-10-17（终审 C3）：这一格 `ok=None`（**没量到**）—— 探针自己炸了、
            #: 或点那一下炸了 ⇒ 既不能说「关掉了」（没量过）也不能说「还在」（没看见）。
            self._popup_row(None, "看/点弹层这一下没成（%s: %s）—— 没量到，不当成做成"
                                  % (type(exc).__name__, str(exc)[:40]))

    def _consent_step_handled(self, label, frame=""):
        """「点掉同意弹层」这一步**找不到元素**时：它是软跳过，不是失败。

        为什么（2026-09-17 真站实测，自测第 2 遍挂在这上面）：
        账本是在一个「弹层已经点掉过」的会话里录的，所以它记了一条硬步骤
        `click「Reject All」`；而**同一会话**再跑一遍（扰动自测第 2 遍的定义就是
        「接着再跑一遍」）时弹层**根本不在页面上** → 找不到 → 记一次失败 → 整遍挂。
        第 1 遍却过（那一遍弹层在）。同一条产物在两遍里表现相反，差别只在环境的残留。

        判据（★ 2026-10-13 改成一条）：
          这一步的名字属于「对同意做个决定」那一族（`_is_consent_step`）⇒ 找不到就是软跳过。

        ★★ 2026-10-13（用户裁定「都收吧」；真事 `job-c02d5768e535` · compareinsulation）：
        **原来还有第二道否决**（「此刻页面上还看得见同意容器吗」，`_CONSENT_BOX_JS`）——
        它把同一件事读成了两种结果：账本里模型对**同一条同意条**点了两次（`Accept all` /
        `Accept All Cookies`），编成步骤表就是两条步骤；产物开跑前钩子已经把它点掉过一次，
        于是第 1 条豁免、第 2 条**判死**（`没找到 ⇒ 这一步没做成`）⇒ 整组重试 ⇒ 自测挂两遍
        ⇒ 人只能喊停。那道否决量的东西（「容器还在不在」）与「这一步该不该承重」根本不是一件事：
        自绘的同意条常常**留着空壳容器**，而「还在不在」这件事**下一步自己会说**——
        弹层真要还在，下一步会被 `_covered_by` 挡住并失败（这正是下面最后一段写着的兜底）。
        所以：**认出是同意步骤就算软跳过**，把否决权交给真正的观测。

        返回 `(算做成了吗, 一句人话)`；不是同意步骤 → `(False, "")`（交回原判据）。"""
        if not _is_consent_step(label):
            return False, ""
        return True, ("「%s」这一步：页面上已经没有同意弹层了（前面那次会话把它点掉过，"
                      "或者这一单本来就是干净会话没有弹层）—— 跳过，不算没做成。"
                      "产物开跑前本来就会自己清一次弹层（`_clear_obstructions`），"
                      "每一步动手前后也会再问一遍（`_POPUP_HOOKS`）。" % label)

    def _page_ready(self, until=None):
        """**整页**能不能动手。不能 → 返回一句**为什么**（人话）；能 → 空串。

        为什么要它（2026-09-18 真站对照，见 `PAGE_READY_SECONDS` 那段）：
        `_covered_by` 只看得见「**我要点的那个东西**被压住了吗」，
        **看不见「整页还没好」** —— 今天每趟就是「元素恰好没被压住」而「整页还烂着」，
        于是它动手、页面不响应、后面 20 步的判据全错。

        ★ 2026-09-29（交接《骨架还缺什么》§7）补上第二种「没好」：**文档还在 `loading`**。
        实测（georgiapower 真广告 URL）loading 时点 2/2 全不成、而 cdp 回执一切正常；
        interactive 时 2/2 全成。两种「没好」的处置相同（都不动手、都有界等），
        但**说出来的话不一样** —— 读日志的人要分得清「被蒙版盖着」还是「文档没到 interactive」。

        `until` 是单调时钟的截止点（`None` = 只量一次，不等）。
        """
        waited = False
        while True:
            got = str(self._ev(_PAGE_LOADER_JS) or "").strip()
            # ⚠️ **只认带前缀的答复**（`LOADER|` / `READY|`，见那两个常量的注释）：
            # 空串 / `null` / 任何别的形状都是「量不出来」—— 一律**不当成在加载**。
            # 当成在加载的代价是**整趟什么都不做**，比漏判严重得多。
            hit = got[1:-1] if (len(got) >= 2 and got[0] == got[-1] == '"') else got
            hit = hit.strip()
            if hit.startswith(LOADER_PREFIX):
                why = "%s 盖着整页" % hit[len(LOADER_PREFIX):]
            elif hit.startswith(READY_PREFIX):
                why = "文档还是 `%s`（首屏还没到 interactive）" % hit[len(READY_PREFIX):]
            else:
                return ""
            if until is None or time.monotonic() >= until:
                if waited:
                    self.log.info("[%s] 等了 %.0f 秒，%s —— 这一页还不能动手",
                                  self.cid, PAGE_READY_SECONDS, why)
                return why
            if not waited:
                waited = True
                self.log.info("[%s] 这一页还在加载（%s）—— 先等它好，最多 %.0f 秒",
                              self.cid, why, PAGE_READY_SECONDS)
            time.sleep(PAGE_READY_POLL)

    def _cover_once(self, selector, frame_id=""):
        """**量一次**：这个选择器指的元素是不是被别的东西盖着。盖着就返回盖它的东西，否则空串。"""
        js = _COVER_JS
        got = self._ev(js % json.dumps(selector), frame_id).strip()
        # ⚠️ **只认带 `COVER|` 前缀的答复**。空串 / `null` / 任何别的形状都是「量不出来」——
        # 一律**不当成盖着**（当成盖着会让每一步都无缘无故不点；不认前缀则会在
        # 「这条 cdp 的 eval 根本不会跑这段」时把它的乱答复读成遮挡）。
        if not got.startswith("COVER|"):
            return ""
        return got[len("COVER|"):]

    def _tabs_now(self):
        """**当时所有页/tab 的地址**（只读；读不回来就说读不回来）。★ 2026-09-28。

        为什么要有它（用户 2026-09-28 那条分析）：`target=_blank` 那一类点击会**新开一个页**，
        而脚本还在老页上找元素 ⇒ 现场只有一句「没找到元素」，判不出「其实是切页没跟上」✗；
        而开窗复验又贵。这一行把「当时一共几个页、各自什么地址、哪个是 active」直接写进 trace。
        `cdp targets` 一次调用、只读 —— 与「切过去」是两件事：这一格只**记录**，不改行为。
        """
        host = str(getattr(self.cdp, "host", "") or "")
        port = str(getattr(self.cdp, "port", "") or "")
        if not host or not port:
            return {"unreadable": "这个 cdp 帮手没给 host/port"}
        try:
            done = subprocess.run([CDP_BIN, "targets", "--host", host, "--port", port],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"unreadable": "%s: %s" % (type(exc).__name__, str(exc)[:60])}
        try:
            rows = json.loads(done.stdout or "[]")
        except ValueError:
            return {"unreadable": str(done.stdout or done.stderr or "")[:80]}
        got = []
        for r in (rows if isinstance(rows, list) else []):
            if not isinstance(r, dict):
                continue
            got.append({"url": str(r.get("url") or "")[:120],
                        "title": str(r.get("title") or "")[:60],
                        "active": bool(r.get("active"))})
        return got

    def _scene(self, index, selector, frame):
        """失败那一步的现场（见 `_SCENE_JS` 那一段）。**一次 eval 给全** DOM 那几样。

        ★ 2026-09-29（交接《骨架还缺什么》§4，funchatt 那 7 条「一条现场都拿不到」逼出来的）：
        这一格过去只是 trace 里的一栏，而生产重跑**不落 trace** ⇒ 现场采了也等于没采。
        现在它在任何一条路上都会跑（`_run_step` 里那句日志把结果交出去），并且补齐了当时
        定案最值的那几样：整页正文前 N 字、活跃页地址（`top_url`）、`_page_trouble`
        （**复用**那一格，不另造一套「是不是风控页」的判据）、以及失败现场那张图
        （`_scene_shot` → 既有的 `common.report_screenshot` 上报通道）。

        ⚠️ 旁路纪律（照抄 `_shots` / shooter 那条）：**探针不许带塌主路** —— 自己出任何事
        都只落一句 why（`unreadable` 里），照常往下走。代价只在「这一步没做成」这条路上付：
        成功的步一次都不探（§13：重跑必须便宜）。
        """
        try:
            raw = _clean_eval(self._ev(_SCENE_JS % (json.dumps(str(selector or "")),
                                                    PAGE_TEXT_CHARS), frame))
            scene = (json.loads(raw) if raw and raw.strip().startswith("{")
                     else {"unreadable": str(raw or "")[:80]})
            if not isinstance(scene, dict):
                scene = {"unreadable": "现场探针回的不是一个对象：%s" % str(scene)[:60]}
        except Exception as exc:               # noqa: BLE001 —— 探现场不许带塌这一步
            scene = {"unreadable": "%s: %s" % (type(exc).__name__, str(exc)[:60])}
        #: 这一页是不是**风控 / 空白页** —— 判据只有 `_page_trouble` 那一份（骨架已有）。
        #: 空串 = 是正常内容页（「不是」也是一个结论，照实记）。
        scene["trouble"] = _page_trouble(str(scene.get("text") or ""))
        #: ★ 2026-09-28（`target=_blank` 那一族）：「点完之后新开了一个页、而脚本还在老页上」
        #: 这类缺陷，现场只有一句「没找到元素」是判不出来的。`cdp targets` 只读、一次调用。
        scene["tabs"] = self._tabs_now()
        scene["shot"] = self._scene_shot(index, scene)
        return scene

    def _scene_shot(self, index, scene):
        """失败现场那张图 —— **窗口在、能力在时**才拍，拍不成说清是**哪一种**不成。

        ★ 2026-09-29（§4）：图走**既有的那条上报通道**（`common.report_screenshot` →
        `/api/quest/screenshot`；本仓里往那个口送图的**只有它** ⇒ 后端那格 `fail_img`
        **多半**就是它的产物。这一点看不到后端，只能说到这儿），**不落本地文件**：
        生产重跑没有 trace 目录可落，而「落了图没人拿得到」跟没落一样。
        `--no-report` 那一趟**连拍都不拍**（发了也白发，还白赔一次截图）。

        ⚠️ 旁路纪律：拍 / 发炸了只记一句 why（返回串就是那句话），绝不许带塌这一步
        （照抄 `_shot`：为了解释失败才加的，自己再变成失败点就本末倒置）。
        """
        if self.no_report:
            return "没拍也没发：这一趟给了 --no-report（不上报）"
        try:
            data = (self.cdp.screenshot() or "").strip()
        except Exception as exc:               # noqa: BLE001
            return "截图没成：%s: %s" % (type(exc).__name__, str(exc)[:60])
        if not data or data.startswith("{"):
            # 空输出 + 命令表里没有它 = 这个 cdp 根本不会截图（与 `_shot` 同一条判据）
            if not data and self._command_absent("screenshot"):
                self._note_missing("screenshot")
            return (_missing_short("screenshot") if "screenshot" in self.missing
                    else "截图没成（cdp 没给出图）")
        try:
            base64.b64decode(data, validate=True)     # 不是图就别往上报接口发
        except Exception as exc:               # noqa: BLE001
            return "截图没成（cdp 给的答复不是图）：%s: %s" % (type(exc).__name__, str(exc)[:40])
        #: 为什么在函数里 import：这一格只在**失败那一步**才走得着，而 `report_screenshot`
        #: 是 common.py 里较新的一格 —— 万一部署那份没有它，这里必须只丢一张图 + 说清楚，
        #: 而不是在 import 期就把**整份产物**炸掉（与「老 common.py 不认识 --strict」同一口径）。
        try:
            from common import report_screenshot     # noqa: PLC0415
        except ImportError as exc:
            return "截图发不出去：部署的 common.py 里没有 report_screenshot（%s）" % exc
        url = str(scene.get("top_url") or scene.get("url") or "")
        try:
            ok = report_screenshot(self.tid, "fail_scene_%d" % index, data, url)
        except Exception as exc:               # noqa: BLE001
            return "截图没发成：%s: %s" % (type(exc).__name__, str(exc)[:60])
        return ("已随这次失败上报（step=fail_scene_%d）" % index) if ok \
            else "截图没发成：上报接口没收下（它也没说为什么）"

    def _cover_is_own_widget(self, action, kind, selector, cover) -> bool:
        """★ 2026-10-08（真事 `job-62086decf839`，shorecrestwpb.com）：**盖着它的是这一格
        自己的自定义控件 ⇒ 不算遮挡物**。窄口径，**只放 `form` + `kind == "select"`**。

        形状：站方用 **select2** 那类插件把原生 `<select>` 换成自定义控件 —— 原生 `<select>`
        还在 DOM 里，而 `cdp form --select` **自己认得**这种（它的说明里写着：目标不是原生
        `<select>` 时会去找可点的控件打开菜单），**手打那条命令是成的**。
        可产物的遮挡判据**排在 cdp 前面**：它量到 `<select>` 被
        `span#select2-edit-size-container.select2-selection__rendered` 盖着 ⇒ 判成
        「被别的东西挡着」⇒ 四个下拉**一个都不动** ⇒ 第 7 步没做成 ⇒ 后面的勾选与 submit
        全没做（那一趟自测 `passed=False`，运营拿不到能用的稿）。

        **只放这一种**，两条都要：
          ① 动作是 `form` 且 `kind == "select"` —— 别的动作照旧拦（那道防线挡的是
             「点到 consent 弹层上、cdp 还回 ok」，真站栽过，见 `_covered_by` 的说明）；
          ② 盖着的东西的 id 里**带着这一格自己的 id** —— select2 那类壳子的命名法就是把
             原生 id 嵌进去（`select2-<原生 id>-container`）。

        ⚠️ 判不出来（选择器里没有 `#id`、或盖着的东西没有 id）⇒ **当遮挡物**（不放行）：
        这条路的默认必须是「拦」，放宽要有依据。
        """
        if action != "form" or kind != "select":
            return False
        want = _id_in(selector)
        got = _id_in(cover)
        return bool(want and got and want in got)

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
        try:
            got = self._ev(js % json.dumps(selector), frame_id).strip()
        except Exception:                                   # noqa: BLE001
            return ""
        if got:
            self.log.info("[%s] 把它挪到视口正中再看一眼（遮挡物是页眉/页脚那类就躲开了）",
                          self.cid)
        return got

    def _scroll(self, step, target, label):
        """把这一步的元素滚进视口。返回 `(ok, selector_used, level, note, frame_used)`。

        **`cdp scroll` 的位置参数是选择器，不是像素**（`scroll [selector]`）——
        所以这一步重放的是「把**那个元素**滚进视口」，与探索时那次是同一件事。

        四条路各自说清为什么：

        - **主帧的元素** → `self.cdp.scroll(选择器)`：与 click / form 走同一类助手，
          生产重跑那条路「一次 cdp CLI 都不额外起进程」的性质不受影响。
        - **子帧的元素** → 自己起进程带 `--frame-id`。为什么不用助手：`CDPHelper.scroll`
          收不了帧号（它的签名只有 pixels），而**跨源 iframe 里的元素在主帧里根本找不到**
          （那正是这一整族 bug 的样子）。
        - **地址全失效了** → 重新 observe 当前页、**按意图重找**（`_relocate`，
          与 click / form 的最后一跳**是同一跳**）★ 2026-09-23 补。
          为什么必须有（vogue 真站实测）：这一步原先**到这儿就死了** —— 滚动这条路上
          没有重找，于是一份**实际能跑通**的产物白赔一次「滚不动」+ 一次整组重试
          （那趟第 3、4 步就是这么来的）。地址是深结构路径时它一改版就指不到元素。
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

        # ★ 2026-09-23：**最后一跳与 click / form 是同一跳**（vogue 真站案例）——
        # 重新 observe，按意图（text / role / near / label / nearby_text）重找那个元素。
        # 上面那两条路用的都是**账本里的地址**；地址一失效（深结构路径最典型）它们
        # 一个都指不到，而滚动这条路上原先没有这一跳 ⇒ 直接判「滚不动」。
        for extra, (selector, cand_frame) in enumerate(self._relocate(target, "action")):
            out = attempt(selector, cand_frame)
            if _ok(out):
                level = len(selectors) + extra
                note = _say("scroll", label, True, level, landing=_landing_say(out))
                if extra == 0 and self.resolve_why:
                    # 与 click / form 同一条人话规矩：靠恢复救回来的必须说出来
                    # （结构化的那一半在 trace 的 `recovery` 里）。
                    note += ("【恢复】声明里的地址全失效，按意图重找到了它"
                             "（命中：%s，把握 %.2f）" % (self.resolve_why,
                                                         self.resolve_score or 0.0))
                return (True, selector, level, note, cand_frame)

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
        not_ready = self._page_ready(until=time.monotonic() + PAGE_READY_SECONDS)
        if not_ready:
            #: `not_ready` **本来就是一句人话**（`LOADER|` 与 `READY|` 两种各自说清是哪一种）——
            #: 这里别再加「盖着整页」那种字样：文档还没到 interactive 时它不是被盖着。
            return False, "", None, (
                "这一页还不能动手（%s；等了 %.0f 秒还是这样）—— **没动手**："
                "点了也是白点，还会把后面的判据全带错" % (not_ready, PAGE_READY_SECONDS)), ""

        # ★ 2026-09-29（travelandleisure 生产实测：25 条失败里 18 条死在这一格）：
        # **动手之前再兜一次同意弹层**。它不只在「开跑 / 导航之后」出现 —— go-offer 那一趟
        # 是**页面加载完之后才异步冒出来的**，等要点「Join Now」时它已经压在按钮上了
        # （日志原话：`第 2 个选择器指的元素被 div#onetrust-group-container.ot-sdk-eight
        # 盖着` → `第 6 步：页面上没找到「Join Now」` → `连着 3 步没做成，收摊`）。
        # 开跑前那次 `_clear_obstructions` 早在弹层出现**之前**就跑过了，中间没人再看一眼。
        # ⚠️ 位置特意选在**整页就绪之后**：早于它就等于在弹层还没渲染出来时问「有没有弹层」；
        #    也特意选在遮挡预算之前 —— 先把弹层清掉，`_covered_by` 才不必为它白等一轮。
        # ⚠️ 它**永不算失败**（清不掉也照走这一步自己的判据），见 `_clear_obstructions`。
        self._clear_obstructions(why="这一步动手之前")

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
        #: ★ 2026-10-13：「被盖着所以一个候选都没试」这件事**只属于这一步**（与
        #: `resolve_why` / `goto_echo` 同一条规矩）：进来先清零，报文要用它。
        self.cover_why = ""
        #: 这一步**到底有没有真动过手**（发过一次 cdp 动作）。报文只在「一个都没试」时
        #: 才把原因归到遮挡上 —— 试过、并且失败了，那是另一回事（别拿遮挡背锅）。
        tried_any = False
        #: ★ 2026-10-12：「没找到」那一支的预算（与上面那笔分开，见 `NOT_FOUND_WAIT_SECONDS`）。
        #: 同样按步开一次。`_miss_waited` 也在这儿清零 —— `_miss_note` 那半句只属于
        #: **等过的那一步**（与 `_followed_tab` 同一条规矩）。
        #: ★ 2026-10-13：账本标了 `late_element` 的那一组换成**长预算**（见那一格的注释）——
        #: 弹窗那类站等的是定时器，6 秒不够，而「它是不是后出来的」账本里有证据。
        miss_until = time.monotonic() + (LATE_ELEMENT_WAIT_SECONDS if step.get("late_element")
                                         else NOT_FOUND_WAIT_SECONDS)
        self._miss_waited = 0.0
        for level, selector in enumerate(selectors):
            # **快路也要接遮挡判据**（真站实测的那条链）：不看一眼就点，
            # 点到盖着它的东西上（consent 弹层那类）cdp 照样回 ok —— 「点了个寂寞」
            # 被记成做成了。盖着就不点它，换下一个候选；全都被盖着就是这一步没做成。
            cover = self._covered_by(selector, frame, until=cover_until)
            if cover and not self._cover_is_own_widget(action, kind, selector, cover):
                self.cover_why = cover
                self.log.info("[%s] 第 %d 个选择器指的元素被 %s 盖着 —— 这一下不点"
                              "（点了等于点到盖着它的东西上）", self.cid, level + 1, cover)
                continue
            if cover:
                #: ★ 2026-10-08：盖着的是**这一格自己的**自定义控件（select2 那类把原生
                #: `<select>` 包起来的壳子）⇒ 照填，交给底下的 cdp 去认那条自定义下拉。
                #: 见 `_cover_is_own_widget`（只放 `form` + `kind=select` 这一种）。
                self.log.info("[%s] 第 %d 个选择器指的元素被 %s 盖着 —— 但那是**这一格自己的**"
                              "（`select` 的自定义控件），照填（底下的 cdp 认得出这种下拉）",
                              self.cid, level + 1, cover)
            out = self._do(action, selector, value, kind, frame, label)
            tried_any = True
            if not _ok(out) and not cover:
                #: ★ 2026-10-12：**没找到**先等一等再试同一根地址（见 NOT_FOUND_WAIT_SECONDS）。
                #: `cover` 那一支不走这儿 —— 它有自己的预算（`_covered_by` 里已经等过了）。
                again = self._wait_until_present(action, selector, value, kind, frame, label,
                                                 level, miss_until)
                if again is not None:
                    out = again
            if _ok(out):
                return (True, selector, level,
                        _say(action, label, True, level, landing=_landing_say(out))
                        + self._tab_note() + self._miss_note()
                        + (self._value_note() if action == "form" else ""), frame)
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
                self.cover_why = cover
                self.log.info("[%s] 重找出来的第 %d 个候选也被 %s 盖着 —— 这一下不点",
                              self.cid, extra + 1, cover)
                continue
            out = self._do(action, selector, value, kind, cand_frame)
            tried_any = True
            if _ok(out):
                note = (_say(action, label, True, level, landing=_landing_say(out))
                        + self._tab_note()
                        + (self._value_note() if action == "form" else ""))
                # ★ 2026-09-23 Recovery Trace（人话那一半）：这一步**声明里的地址全失效了**，
                # 是靠意图重找救回来的 —— 必须说出来，别让它长得像「本来就成了」。
                # 结构化的那一半（primary_selector / resolved_by / confidence）在 trace 里。
                if extra == 0 and self.resolve_why:
                    note += ("【恢复】声明里的地址全失效，按意图重找到了它"
                             "（命中：%s，把握 %.2f）" % (self.resolve_why,
                                                         self.resolve_score or 0.0))
                return (True, selector, level, note, cand_frame)
        # 都试完了还是没找到 —— 「点掉同意弹层」这一步是**软**的（见那个 docstring）：
        # 弹层已经不在了就等于这件事已经办完了，不该记成没做成。
        if action == "click":
            handled, why = self._consent_step_handled(label, frame)
            if handled:
                self.log.info("[%s] %s", self.cid, why)
                return True, "", None, why, frame
        # ★ 2026-10-13：**「没敢动手」不是「没找到」** —— 两个原因说成同一句话，读的人
        # 只会往选择器上查（真事 `job-a6954761e657`：三轮诊错全在改选择器，而那一刻
        # 产物**连一次 cdp form 都没发出去**，框一直好好地在那儿）。
        if self.cover_why and not tried_any:
            return False, "", None, (
                "%s 一直盖着「%s」（等过、也把它往视口正中挪过，它没走）—— 这一下没敢%s"
                % (self.cover_why, label, "点" if action == "click" else "填")), frame
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

    def _url_drift_ok(self, when) -> bool:
        """地址对不上时：**漂在同一条路上**吗（同一个目录前缀 ⇒ 换了末段 / 加了参数）。

        ★ 2026-10-09：**这一格就是那条宽限的边界** ——
          · 地址漂：判据 `…/intake/sh-7aq5xg/location`，手里是 `…/intake/sh-7aq5xg/physical-assessment?ofr=…`
            ⇒ 同一个目录 ⇒ 算同一段路（照走）；
          · 换到**另一条路径**：判据 `…/walk-in-showers/`，手里是 `…/bath-fitters/zz991`
            ⇒ 不算 ⇒ 照旧跳过。
        为什么非要有这条边界：**两页正文一样时，鉴别力只剩地址一条** ——
        `tests/test_browser_agent.py::test_when_url_still_discriminates_a_different_path_prefix`
        就是拿这个形状钉的（正文**故意一样**，放松过头它当场红）。
        ⚠️ 判据只到主机名（或主机根）时**没有「同一条路」可谈** ⇒ 不放宽。
        """
        want = str((when or {}).get("url_contains") or "")
        if "/" not in want.split("://", 1)[-1]:
            return False                       # 判据只到主机名 ⇒ 没有「同一条路」可谈
        base = want if want.endswith("/") else want.rsplit("/", 1)[0] + "/"
        stem = base.split("://", 1)[-1]
        if "/" not in stem:
            return False                       # 判据就是主机根 ⇒ 同上
        #: ⚠️ 比的是**去掉 scheme 的那一段**：判据里写没写 `https://` 两边都碰得到
        #: （真产出里两种写法都有 —— 判据是生成期从账本抄的，写法不统一）。
        return any(str(u).split("://", 1)[-1].startswith(stem) for u in self._urls())

    def _url_drift_note(self, when) -> str:
        """地址那一格**没命中但漂在同一条路上**吗 → 一句人话（空串 = 命中了 / 没漂 / 没写地址判据）。

        ★ 2026-10-09（用户裁的「耐性」那条）：同一段路的地址会**漂** —— 带 `?ofr=…&lang=en`
        这类参数、或换一个末段 slug，而正文一个字没变。这句就是「我认出了这一屏，但**不是靠地址**」。
        """
        want = str((when or {}).get("url_contains") or "")
        if not want:
            return ""
        urls = self._urls()
        if any(want in url for url in urls):
            return ""
        if not self._url_drift_ok(when):
            return ""                          # 换到另一条路上了 ⇒ 这条不算「漂」，不记
        return ("地址对不上但**同一条路**（要含「%s」，手里这些地址是：%s）"
                % (want, " ｜ ".join(urls) or "（一个都没读到）"))

    def _matches(self, when):
        """这条 `when` 现在成不成立（**只看判据**，不做等待）。

        ★ 2026-10-09（用户裁的「不换形态、给固定步骤表加耐性」那条）：**地址那一格允许漂**
        —— 地址对不上、**正文判据命中**、且地址**漂在同一条路上**时算成立
        （并在日志里说明「靠正文认的」）。为什么（真站现场，`job-f9b0090a9472`
        `go.roviran.com`）：同一屏的地址会带 `?ofr=OFR-FOOT-FORTE-10&lang=en` 这类参数、
        也会换末段 slug，而**正文一个字没变** —— 判据把地址钉死 ⇒ 整组静默跳过
        （那趟 18 步里 **15 步**这么没的，最后「没走到成功」）。

        ⚠️ 三条边界（每一条都有真站代价）：
          · 地址命中、正文没命中 ⇒ 照旧**不成立**（地址一个参数就能改，正文才是这一屏的样子）；
          · 换到**另一条路径**（不是同一个目录前缀）⇒ 照旧**不成立**（见 `_url_drift_ok`）；
          · 判据**只有地址**（没写正文）⇒ 照旧**严格**比地址 —— 那种组本来就靠地址认，
            放宽它等于「哪一页都行」。
        ⚠️ 这条**不会**把「两屏正文一样、地址不同」的两组看成一个 —— 那种形状在**生成期**
        就并成一组了（`browser_agent` 那条并组判据：URL 与正文**两向都分不开** ⇒ 并）。
        """
        if not when:
            return True
        want_url = str(when.get("url_contains") or "")
        url_ok = (not want_url) or any(want_url in url for url in self._urls())
        wants = [_norm(str(w)).lower() for w in (when.get("text_contains") or [])]
        if not wants:
            return url_ok
        signature = self.page_signature().lower()
        if not any(w in signature for w in wants):
            return False
        return url_ok or self._url_drift_ok(when)

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
        #: ★ 2026-09-22（生成侧建议第 2 条）：**有界等「when 成立」** —— 而不是只等 `readyState`。
        #: 为什么：**外层页永远 `complete`** ⇒ 等 `readyState` 等于没等（真产物里那一步的实测：
        #: 第 19 步照样被跳过）。而这类失败**跑完全程不报错**，只表现为「没走到成功」，
        #: 最容易被当成站点问题糊过去 —— 所以窗口给 5~8 秒（`WAIT_WHEN_SECONDS`），
        #: 真不成立的状态组多花那几秒，换来「不是没等就判」。
        self._wait_when(when, timeout=WAIT_WHEN_SECONDS)
        self._wait_ready(timeout=WAIT_READY_SECONDS)
        ok = self._matches(when)
        if not ok:
            # ★ 2026-09-28（georgiapower 真站实测）：判据成立的那一页可能在**另一个 tab** 上
            # —— CTA 是 `target="_blank"`，排程页开在新 tab 里，而这里读的一直是旧页。
            # 不补这一条，25 步里 23 步会被**静默跳过**（每步都写着「点上了/填好了」，
            # 结账却是 no_success）。切过去再判一次，判上就当这一页就是它。
            if self._follow_tab_matching(when):
                self._wait_when(when, timeout=WAIT_WHEN_SECONDS)
                ok = self._matches(when)
        #: ⚠️ why 必须在**判的这一刻**算死，不许事后重算 ——
        #: 事后再看一眼时页面已经变了，说出来的话就跟结论打架。
        self._when_why_cache = "" if ok else self._when_why(when)
        return ok

    def _fingerprint_now(self):
        """这个窗口**当下**的指纹：`{"viewport": "984x905", "dpr": 3, "ua": "…"}`；读不到给 `None`。

        读不到就是**读不到**（不许拿空串顶替 —— 那会把「没量着」写成「量着了、是空的」）。
        """
        raw = self._ev("return JSON.stringify({w:Math.round(window.innerWidth),"
                       "h:Math.round(window.innerHeight),dpr:window.devicePixelRatio||1,"
                       "ua:navigator.userAgent||''});")
        for _ in range(2):                       # cdp eval 的答复被编码两层（交接 §7）
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                break
        if not isinstance(raw, dict):
            return None
        try:
            viewport = "%dx%d" % (int(raw["w"]), int(raw["h"]))
        except (KeyError, TypeError, ValueError):
            return None
        return {"viewport": viewport, "dpr": raw.get("dpr"),
                "ua": str(raw.get("ua") or "")[:200]}

    def _say_fingerprint(self):
        """开跑之前把**指纹**摆出来：录它的时候是什么、现在是什么 —— 不一致就**出声**。

        为什么这一格必须存在（2026-09-28 用户报的那件事）：站点按视口/UA 换版式，
        而账本里的选择器是**在某一个尺寸下**录的。指纹一变，那些选择器就变成
        「点了也不动」—— 现场只有一句「没找到元素」，谁都得开窗复验才判得出。
        这一句把那个判断**提前到开跑那一刻**，并且写进 trace（`fingerprint_mismatch`）。

        ⚠️ **不许**把它做成一条闸（不 return False、不停）：
        它只回答「环境与录制时一不一样」。停不停是人的事 —— 而这一次真跑要不要，
        取决于人手上的信息（有时就是要拿老指纹下的产物去新指纹上试）。
        """
        want = (PROVENANCE or {}).get("fingerprint") if isinstance(PROVENANCE, dict) else None
        if not isinstance(want, dict) or not want.get("viewport"):
            self.log.warning("[%s] 这份产物**没记录入指纹**（`PROVENANCE['fingerprint']` 是空的）"
                             "—— 所以「它是在什么尺寸下录的」这件事，谁都答不上来；"
                             "选择器只在某一个版式下成立时，这里一个字都提示不了。", self.cid)
            return
        now = self._fingerprint_now()
        if now is None:
            self.log.warning("[%s] 读不出这个窗口的指纹 —— 录的时候是 %s（视口）/ DPR %s；"
                             "**这一次量不到**，所以对不上对得上都说不了。",
                             self.cid, want.get("viewport"), want.get("dpr"))
            return
        if now.get("viewport") == want.get("viewport"):
            self.log.info("[%s] 指纹对上了：视口 %s（录它的时候也是这个）",
                          self.cid, now["viewport"])
            return
        self.log.warning("[%s] ⚠️ **指纹不一样**：录它的时候视口是 %s，现在是 %s"
                         "（dpr %s→%s）—— 这一站的页头按视口换版式，"
                         "这一趟里「点了也不动 / 找不到元素」很可能就是**这个差**造成的，"
                         "别当成站点改版。", self.cid, want.get("viewport"),
                         now["viewport"], want.get("dpr"), now.get("dpr"))
        self._trace({"fingerprint_mismatch": {"recorded": want, "now": now}})

    def _say_country(self):
        """**不知道这个站在哪一国** —— 说一次（★ 2026-09-29，见 `COUNTRY_POOLS`）。

        为什么非说不可：随机池是**按国家**分的（美国站填美国邮编、英国站填英国邮编），
        而国家只从 `PROVENANCE["env"]["proxy_country"]` 来。取不到时**照样要填**三个字段
        （邮编/手机/州），池子退回分流之前那一套 —— 这件事**必须出声**：
        「不知道」与「知道，就是美国」在日志里长得一模一样，而代价是真金白银的
        （加拿大站被填英国手机号 ⇒ 站方红字 `The phone number is not valid.` ⇒ 整单被拒）。
        ⚠️ 不静默当美国、也不静默当英国 —— 这里说的就是「猜不了，用的是老那一套」。
        """
        self.log.warning(
            "[%s] 这份产物**不知道这个站在哪个国家**"
            "（`PROVENANCE['env']['proxy_country']` 是空的 / 认不出来 —— 两字母码对得上、"
            "但池子里还没有那一国的（像 `AU`）也算这一档）—— "
            "邮编 / 手机 / 州 照旧用分流之前那一套（英国邮编与手机、美国州）。"
            "要按国家分流，就把两字母码写进生成时的 `PROVENANCE['env']['proxy_country']`"
            "（后端连续失败榜上那格 `country` 原样带下来即可）。", self.cid)

    def _say_url_only_whens(self):
        """哪些状态的判据**只有地址** —— 开跑之前说一次（**不许沉默**）。

        ★ 2026-09-20 gowizard 线①：`when.text_contains` 是生成期**从一次观测**取的一段
        子串。站点对同一页给两种免责声明（A/B，实测 72 趟里 3 趟撞上 B）时，钉住 A 的
        那条判据在 B 那一趟**整组不成立** —— 而 `auto_warranty` 是 `start` 之后的
        **第一个**状态组，它一跳过，「Reject All」「Get Free Quote」两个动作都没做
        ⇒ 25 步里 24 步全跳过 ⇒ **0 次真实点击** ⇒ `no_success`。

        产物上「只有地址一条」与「判据本来就只有地址」长得**一模一样** —— 读日志的人
        分不出「查过了，只有地址稳」和「没钉出来，只好只钉地址」。所以这里主动说，
        不等它真的整组跳过了，再从「没走到成功」那句里往回猜。

        ⚠️ **只报「有地址、没正文」的那种**：`when=None` 是**有意不设**判据（旁枝起点
        那类，见 `_drop_incidental_start_when`），与「钉不出来」是两件事。混在一起，
        真钉不出来的那些会被有意不设的那堆淹掉。
        """
        bare = []
        for state in STATES:
            when = state.get("when")
            if not isinstance(when, dict) or not when.get("url_contains"):
                continue
            if when.get("text_contains"):
                continue
            bare.append((state.get("name") or SITE, when))
        for name, when in bare:
            self.log.warning("[%s] 状态「%s」的判据**只有地址**（%s）：%s",
                             self.cid, name, when["url_contains"],
                             when.get("text_why") or URL_ONLY_WHY)
        return len(bare)

    def _succeeded(self):
        signature = self.page_signature().lower()
        if _criterion_in(signature, [_norm(t).lower() for t in SUCCESS_TEXTS if t]):
            return True
        #: ★ 2026-09-24（用户点名「那不能一样加个 success_url」）：**网址那一格**。
        #: 判「成功」的两条路并列：页面上见到了那串字，**或者**现在这个地址里出现了那一截。
        #: `_url()` 拿的是 `window.location.href` —— **含 fragment**（`#/confirm` 那种
        #: 判据就在 fragment 里，`warthunder` 那份老脚本正是这么判的）。
        url = str(self._url() or "").lower()
        return any(_norm(u).lower() in url for u in SUCCESS_URLS if u)

    def _run_step(self, index, step):
        """走一步，返回 (ok, progress)。trace 的一行也在这一步里落。"""
        action = (step.get("action") or "").strip()
        target = step.get("target") or {}
        label = _label(target, step)

        before_path, shot_before, signature = None, None, ""
        self.progress_why, self.shots_why = None, None
        #: 「这一组要重来一遍」那句（`run()` 在上一步**没做成**之后写下的）—— 它是
        #: **这一步**的一部分（这一步就是那一组的重来），所以在这里取走并清掉，
        #: 与 `progress_why` 同一条规矩：那句话只属于触发它的那一步，不许粘在下一步上。
        retry_why, self.retry_why = self.retry_why, None
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
        # ★ 2026-09-23（用户点的那件事）／★ 2026-09-29（交接《骨架还缺什么》§4）：**没做成
        # 的那一步**把现场录下来（`_scene` 的 docstring 写着为什么是字而不是图、为什么它
        # 现在在生产路径上也要跑）。§13 的老口径一个字没变：**成功的步不探**；
        # 探不动就交一句 why（**不许**编一个现场出来）。
        scene = None
        if not ok and getattr(self, "cdp", None) is not None:
            scene = self._scene(index, selector, frame)
            if not self.tracing:
                #: ★ 2026-09-29（§4）：生产重跑**不落 trace** ⇒ 现场只交给**日志**
                #: （运维把它收进 failDiag 的 `lines`；那张图另有 `_scene_shot` 那条上报）。
                #: 一行 JSON，机器读得懂 —— funchatt 那天 7 条失败一条现场都拿不到，
                #: 就是因为现场过去只写进 trace 这个生产不开的通道。
                self.log.warning("[%s] 第 %d 步没做成 —— 现场：%s",
                                 self.cid, index, json.dumps(scene, ensure_ascii=False))
        try:
            self._dly()
            progress = self._diff(before_path) if before_path else None
            #: ★ 2026-09-23 用户实测（「等待时间短了……重新填了后成功了」）：
            #: 只 diff 一次的话，**异步换页**的那些步会被判成「页面没有变化」
            #: （trace 第 12 步实测如此，而那一下其实成了）。有界重试：一变立刻停。
            if progress is False:
                waited = 0.0
                while waited < float(DIFF_POLL_SECONDS):
                    time.sleep(float(DIFF_POLL_STEP))
                    waited += float(DIFF_POLL_STEP)
                    progress = self._diff(before_path) if before_path else None
                    if progress is not False:
                        break
        finally:
            if before_path:
                try:
                    os.unlink(before_path)          # 快照只是给 diff 用的，不留给谁看
                except OSError:
                    pass

        if retry_why:
            # 重试这件事**必须进 trace**（Console 与自测读的是 trace，不是日志）：
            # 不说的话，读的人只看到「第 1 步 ok=false、第 2 步 ok=true」，会读成
            # 「它自己好了」—— 而第 2 步其实是**在真页面上多做的那个动作**。
            note = "%s；%s" % (retry_why, note)
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
                # ★ Recovery Trace（同事 2026-09-23 定的路线：Resolver 上线后必须记下
                # 「这一步是**靠恢复在苟活**」—— 否则将来不知道哪些脚本已经脆到要修）。
                # primary_selector = 账本里原本要用的第一条地址（它失败了）；
                # resolved_by = 重找时命中了哪几样身份；confidence = 把握（0~1）。
                "recovery": (None if not self.resolve_why else {
                    "primary_selector": ((target.get("selectors") or [""]) or [""])[0],
                    "resolved_by": self.resolve_why,
                    "confidence": self.resolve_score,
                }),
                # 这一步真在哪一帧里做的（主帧 = ""）——「帧内的点击有没有带上帧号」
                # 这件事，读 trace 就能看见，不用去读产物源码。
                "frame_id": frame,
                #: ★ 2026-10-09：这一步**不承重**（探路里同一个**无名**目标作成过也做败过
                #: —— 浮窗那类"有时候有有时候没有"的东西，见
                #: `browser_agent._not_load_bearing_clicks`）。自测拿这一格分档：
                #: 它没做成**不否决**那一遍，但要**点名**说清（与 `scroll` / `wait` 同一档）。
                #: 不写这一格的理由也在这句里：**只有产物知道**哪一步是那种步，自测那边
                #: 光看 trace 认不出来（`action` 是 `click`，跟承重步长得一模一样）。
                "auxiliary": (True if step.get("auxiliary") else None),
                #: ★ 2026-10-13（用户裁定「都收吧」）：**这一步是软的**（`soft`）—— 与 `auxiliary`
                #: 同一档：自测**不拿它否决那一遍**，但那样本行里照样点名。区别只在「凭什么」：
                #: `auxiliary` 认的是**观察**（探路里同一目标作成过也做败过），`soft` 认的是
                #: **这一类东西天生看会话脸色**（同意条/弹层：新窗口有、上一趟点掉过就没有），
                #: 由生成侧标（`browser_agent._replay_step`），这一档只读记号。
                "soft": (True if step.get("soft") else None),
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
                # ★ 现场（只有没做成那一步才有）：点到的其实是谁 / 页面加载完没 / 有没有
                # 同意类容器 / 视口里最显眼的三段字。**机器读得懂**，不依赖视觉模型。
                "scene": scene,
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

        # ★ 2026-09-28：**先核指纹**（视口/DPR/UA）—— 这一格以前一个字都不说。
        # 为什么非说不可：这个站的页头**按视口/UA 换版式**（实测：宽的时候汉堡与 BOOK NOW
        # 是 0×0、窄的时候页头那个 Join Now 又不在了）⇒ 在别的尺寸下录出来的选择器，跑起来
        # 就是「点了也不动」，而现场只有一句「没找到元素」—— 判不出是站变了还是**指纹变了**。
        # 判据取 `PROVENANCE["fingerprint"]`（探路那个窗口**量下来**的；没有这一格就是「不知道」，
        # 那也**要说**：不说的话，「没录指纹」与「指纹对上了」长得一模一样）。
        self._say_fingerprint()

        # ★ 线①：判据**只有地址**的状态，先喊一声（见 `_say_url_only_whens` 的 docstring）——
        # 在**任何一步之前**说，这样即使它整组被跳过了，日志里也早就有那句为什么。
        self._say_url_only_whens()

        index = 0
        try:
            for state in STATES:
                steps = state.get("steps") or []
                if not steps:
                    continue
                name = state.get("name") or SITE
                applies = self._applies(state.get("when"))
                if not applies and self._verify_the_hit(state.get("when"), name):
                    # ★ 2026-09-29（交接 §8）：判据不成立 ≠ 这一页不是那个状态 ——
                    # 也可能是**刚才那一击**没落地（新 tab 2.26 秒才冒出来、先空地址）。
                    # 复核动过之后重判一次；成立就照常走这一组，不跳过。
                    applies = self._applies(state.get("when"))
                    if applies:
                        self.log.info("[%s] 复核之后这一页**就是**「%s」那个状态 —— "
                                      "刚才那一击是落地了的（不复核就会整组跳过）", self.cid, name)
                if applies and self._url_drift_note(state.get("when")):
                    # ★ 2026-10-09（用户裁的「耐性」那条）：**靠正文认出来的**这一组要说一句 ——
                    # 地址漂了（参数 / slug 变了）而正文没变，走的是宽限那条路。
                    # 不说的话，「这一趟为什么还能走」在日志里没有出处：地址判据写着别的网址。
                    self.drifted += 1
                    self.log.info("[%s] 「%s」这一组**不是靠地址认出来的** —— %s；"
                                  "正文判据命中了，照走（地址漂了不算换屏）",
                                  self.cid, name, self._url_drift_note(state.get("when")))
                #: 这一组走到第几个动作了。⚠️ 用**游标**而不是 `for step in steps`：
                #: 一步没做成时要把这一组**从头再走一遍**（线③），游标能回去，for 不能。
                cursor = 0
                while cursor < len(steps):
                    step = steps[cursor]
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
                        #: ★ 2026-09-28（缺陷单《产物判据跨元素拼接》§六 E3）：**连着跳过**要当场喊。
                        #: 原来是每一跳一句 INFO + 最后一句总结 ⇒ georgiapower 那单「真做 2 步、
                        #: 跳过 23 步」在日志里长得像**正常跑**（每一步都写着点上了/填好了），
                        #: 到结账才发现整单没成。`STUCK_LIMIT` 只管**失败**，管不到跳过 ——
                        #: 于是「这一页不像任何状态」这件事一直没人说。
                        #: ⚠️ **只喊一次**：第 N 组那一下喊，之后不再刷屏（后面还有十几组）。
                        self.skipped_in_a_row += 1
                        if self.skipped_in_a_row == SKIPPED_GROUPS_ALOUD:
                            self.log.error(
                                "[%s] ⚠️ **连着 %d 组判据都不成立**（从「%s」起）—— 这一页不像"
                                "任何一个状态。跳过**就是没做**：后面每一组都会跟着垮，"
                                "而日志里每一步照样写着「点上了/填好了」。"
                                "先看这句里的 why（判据要的那段 vs 页面上实际有的那段）——"
                                "多半是**判据本身不对**（跨元素拼接 / 瞬时状态 / 换了环境）。",
                                self.cid, self.skipped_in_a_row, name)
                        #: ★ 2026-09-28（travelandleisure 生产 26221672 实测）：跳过时顺手判一眼
                        #: **这一页到底是不是内容页** —— 风控页 / 空白页 / 普通判据不匹配，
                        #: 原先在日志里**长得一模一样**，读的人会一直往判据上找，而前两样
                        #: **不是脚本能解决的**（要换出口 IP/降频、或查加载链路）。
                        #: 记在这里（不是 `_applies` 里）是因为**只有这里的 `index` 是准的**：
                        #: `applies` 是在 while 之前算好的，那时 `self.step` 还是上一步的值。
                        #: 签名下面那句本来就要算一次 —— 借过来，不额外开一次 eval。
                        _sig = self.page_signature() or ""
                        self.log.info("[%s] 第 %d 步**跳过**：这一页不像「%s」那个状态（%s）"
                                      "；页面上现在写着「%s」", self.cid, index, name, why,
                                      _sig[:80])
                        _trouble = _page_trouble(_sig)
                        if _trouble:
                            self.trouble_steps.append((index, _trouble))
                        self._trace({"step": index, "action": (step.get("action") or ""),
                                     "skipped": True, "state": name, "why": why,
                                     "note": "第 %d 步跳过：这一页不像「%s」那个状态（%s）"
                                             % (index, name, why)})
                        # ⚠️ 游标**必须自己前进**：这里原来是 `for step in steps` 里的
                        # `continue`（循环自己会往下走），换成 `while` 之后不推它就是**死循环**
                        # （跳过的那一组会一直判、一直跳过、跑到天荒地老）。
                        cursor += 1
                        continue
                    self._rpt_if_moved(name)
                    # 这一组真做了 ⇒ 「连着跳过」的计数清零（它不是「跳过总数」）。
                    self.skipped_in_a_row = 0
                    if (step.get("action") or "") == "goto":
                        # ① 先**等这一页加载完**再判 `when` —— 不等的话，紧接着的判据会把
                        #    「还没加载完」读成「这一页不像那个状态」，整组步骤静默跳过
                        #    （真站实测：33 步里 32 步这么没的）。
                        # ② 换页也是**同意弹层会出现**的时刻，再看一眼弹层。
                        self._wait_ready()
                        self._clear_obstructions(why="这一次导航")
                    ok, progress = self._run_step(index, step)
                    #: 记下来：判据一旦不成立，要复核的就是**这一下**（§8）——
                    #: 连同它的 `progress`（True = 页面判得出动过 ⇒ 不许重试，见
                    #: `_verify_the_hit` 的 2026-10-20 那条）。
                    self._last_hit = (index, step, progress)

                    if self._succeeded():
                        self.log.info("[%s] 成功：判据成立了（页面上那串字 / 网址那一截）", self.cid)
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

                    # ── 线③：一步没做成 ⇒ **把这一组从头再走一遍**（一次为限）──────────
                    # 真站实测（26015658）：第 11 步「页面上没找到「330i」」⇒ 问卷没前进 ⇒
                    # 第 12~24 步的 `Progress: N%` 判据全废 ⇒ 最后在**空表**上点提交、
                    # 一个字段都没填。产物是**纯线性**的，一步没成后面全部错位；
                    # 而这类站最常见的自愈就是「下拉没选上 ⇒ 重开一次多半能选上」。
                    #
                    # ⚠️ 三件事都钉在这里，缺一不可：
                    #   ① 上限（`GROUP_RETRY_LIMIT`，且**一趟里算总数**）—— 不许无限重试；
                    #   ② **说人话**（日志一行 + 那一步的 trace 行）—— 不许静默重试；
                    #   ③ 重来的是**整组**（游标回 0），不是「把那一步再点一下」。
                    # ⚠️ 位置在**早停之后**：连着没做成的步数照旧攒，`stuck` 到顶就收摊 ——
                    #    重试不许把早停那条唯一的刹车拆掉（§13 的成本模型）。
                    # ⚠️ 重试**不再判一次 `when`**：这一组的门在进组时已经判过了，而页面上
                    #    什么都没推进（失败的那一步没成）。重判会把「重试」变成「整组跳过」——
                    #    那正是这套产物最贵的那类失败。
                    #: ★ 2026-10-09（真站 `job-d40bb4d2d2be`／`taxlawadvocates` 实测）：
                    #: **「滚不动」不该把整组打回**。现场：第 13 步点了「Next →」（页面当场翻页）
                    #: ⇒ 第 14 步还写着「把 Next → 滚进视口」，而那只按钮**已经不在页面上** ⇒
                    #: 整组 25 步从头再来（上限 1 次）⇒ 重走时第二步的 click 也找不到它
                    #: （trace 原话：`step16 click 页面上没找到「Next →」`）—— **一次滚动失败把
                    #: 整条线性流程搅乱了**，最后报「没走到成功」。
                    #: 判据：**滚动是准备动作**（真正要做的下一步自己会把元素滚进视口）——
                    #: 没滚成时**出声 + trace 里如实记 `ok=False`**，然后**接着走下一步**；
                    #: 不重试整组（重试的前提是「那一步真的要做的事没做成」）。
                    #: ★ 2026-10-13（用户裁定「都收吧」；真事 `job-c02d5768e535` ·
                    #: compareinsulation 与 `job-708dfae74b73` · lovesac）：**`soft` 那一步
                    #: 没做成不算失败，也不打回整组** —— 它与上面的 scroll 同一档。
                    #: 什么步被标 `soft`：**环境噪声**，不是业务动作 —— 「点掉同意条 / 弹层」那一类。
                    #: 它天生**有时有有时没有**（新窗口清 cookie ⇒ 有时有；上一趟已经点掉过 ⇒
                    #: 有时没有；弹层还可能是**后冒出来的**）。账本忠实记下「探路那趟点过」，
                    #: 于是它进了步骤表；可它是一条**条件**，写成无条件步骤就是两堵墙：
                    #:   · compareinsulation 那趟：同一条同意条被编成两条步骤（模型点了两次），
                    #:     第 1 条豁免、第 2 条判死（`_is_consent_step` 认出来了却栽在「此刻
                    #:     页面上还看得见同意容器吗」那道上）⇒ 自测挂、改两版稿还是同一堵墙；
                    #:   · 那条真话是：**弹层本来就归前提层管**（`_clear_obstructions` /
                    #:     `_POPUP_HOOKS`：开跑前 + 每一步动手前后各问一遍）。表里这一步是**冗余**的，
                    #:     冗余就该是软的 —— 页面上真还有弹层时，**下一步**会被 `_covered_by` 挡住
                    #:     而失败，那时候的失败才是承重的。
                    #: ⚠️ scroll 那条的**话**一个字不改（`test_a_scroll_that_cannot_move_does_not_
                    #: walk_the_whole_group_again` 按「没滚成」这三个字钉着它）—— `soft` 另开一条。
                    if not ok and str((step or {}).get("action") or "") == "scroll":
                        self.log.warning("[%s] 第 %d 步是 scroll，没滚成（%s）—— 照旧往下走"
                                         "（滚动是准备动作，不拿它打回整组）",
                                         self.cid, index,
                                         str(self._when_why_cache or self.retry_why or "")[:60])
                        cursor += 1
                        continue
                    if not ok and (step or {}).get("soft"):
                        self.log.warning(
                            "[%s] 第 %d 步是**软步骤**（弹层那类：有就点掉、没有就算了），"
                            "没做成（%s）—— 照旧往下走（它是环境噪声、不是业务动作，"
                            "不拿它打回整组）",
                            self.cid, index,
                            str(self._when_why_cache or self.retry_why or "")[:60])
                        cursor += 1
                        continue
                    if not ok and self.group_retries < GROUP_RETRY_LIMIT:
                        self.group_retries += 1
                        self.retry_why = (
                            "重试「%s」这一组（第 %d 次，上限 %d 次）：刚才那一步没做成，"
                            "把这一组 %d 步从头再走一遍"
                            % (name, self.group_retries, GROUP_RETRY_LIMIT, len(steps)))
                        self.log.warning("[%s] 第 %d 次重试「%s」这一组（上限 %d 次）："
                                         "把它那 %d 步从头再走一遍 —— 刚才第 %d 步没做成",
                                         self.cid, self.group_retries, name, GROUP_RETRY_LIMIT,
                                         len(steps), index)
                        cursor = 0
                        continue
                    cursor += 1

            # ★ 收尾那一下要**等一等**（2026-09-22 真事）：提交之后成功页往往是**异步渲染**出来的
            # （真站实测：提交那一下点完，脚本立刻判 `_succeeded()` 判不到，然后就走完了 ⇒
            # 报「没走到成功」）。用户原话：「是不是**等待时间不够**啥的」—— 这一半他说对了。
            # ⚠️ 有界：最多 `SUCCESS_WAIT_SECONDS` 秒，见到了**立刻**返回（happy path 一秒不等）。
            if self._wait_success():
                self._rpt("success")
                return True
            # **没见到成功文案就必须大声说「我没到」**，并说清停在哪、跳过了多少 ——
            # 「走完了 N 步」这句话本身**不是**一个结论（它听着像「跑完了」）。
            self.log.error(
                "[%s] **没走到成功**：%d 步里真做了 %d 步、被 when 判据跳过 %d 步%s%s；"
                "页面上**从头到尾没有出现过成功判据**（要认的那段：%s）……",
                self.cid, total, total - self.skipped, self.skipped,
                ("（跳过最多的那个状态是「%s」）" % self._skip_peak()) if self.skipped else "",
                ("（另有 %d 组是靠**正文**认出来的：地址漂了、正文没变）" % self.drifted)
                if self.drifted else "",
                " / ".join(SUCCESS_TEXTS[:2]) or ("网址 " + " / ".join(SUCCESS_URLS[:2])))
            # ★ 2026-09-28：**页面压根不是内容页**时把话说透 —— 这类失败最容易被当成
            # 「判据没钉好」，而它其实**不是脚本能解决的**（风控要换出口 IP / 降频，
            # 空白页要查加载链路）。本项目的规矩：异常**必须说出来**，不许让人从
            # 「正文里没有 X」里自己猜。
            if self.trouble_steps:
                kinds = {}
                for _, say in self.trouble_steps:
                    k = say.split("（")[0]
                    kinds[k] = kinds.get(k, 0) + 1
                self.log.error(
                    "[%s] ⚠️ 诊断：这一趟有 **%d 步**读到的**根本不是内容页**"
                    "（第一次在第 %d 步）—— %s；分项：%s。"
                    "**这不是判据的问题，也不是脚本能解决的**："
                    "风控/挑战页要换出口 IP 或降频，空白页要查加载链路。",
                    self.cid, len(self.trouble_steps), self.trouble_steps[0][0],
                    self.trouble_steps[0][1],
                    "、".join("%s×%d" % (k, v) for k, v in kinds.items()))
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
