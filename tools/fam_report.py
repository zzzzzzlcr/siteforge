#!/usr/bin/env python3
"""失败族报告（宿主上跑，**只读**）：把 `runtime*/checkpoints.sqlite` + 探路账本读成一张表。

## 为什么有它

2026-10-14 那次量证是一次性脚本，跑完就没了 —— 而「每一刀之后重量同一个数」缺的正是它。
没有这张表，每一刀都是猜「哪个病最大」。（9 月那次量出「26 条 `draft_failed` 里 21 条
同一个病」，靠的也是手工数。）

## 三个口径，缺一个就会读反

1. **人喊停不是失败**（`paused` / `human_stop`）—— 占 8099 那本账的四成；**窗口基建**
   （`no_window` / `window_gone`）也不是我们代码的病。两样都能剔掉再算，才算得清。
2. **重试会灌水**：`getfixapro` 一个站 09-30 一天重试 **9 趟** ⇒ 那一族「17 趟」去重后
   只有 **6 个站**。所以每一族都同时给**趟数**与**去重站数**。
3. **量不到不许当成 0**：没账本的趟、没诊断的趟、判不出 A/B 的趟 —— 各自出声，
   一项都不并进 0（这一族坑在本仓有先例：`formLog` 那格 `fail=0` 不是「没失败」）。

## A 类 / B 类（全表唯一带判断的一格）

修到顶的趟，账本每一轮都留着 `failed_step` + `note`（`diagnosis` 通道）：

| 读成 | 形状 | 处置 |
|---|---|---|
| **A 原地反复** | 连着两轮**同一步**、诊断**逐字相同** | 换证据 / 换人 —— 再改一稿还是撞同一堵墙 |
| **B 往前推进** | 每轮都能修好一处，但**冒出下一处** | 查流程可靠性与状态恢复 —— 单点修复治不了 |
| **分不出** | 只有一轮 / 步号缺失 / 步号往回走 | 出声，**不塞进上面任何一类** |

判据与 `agent/state.py` 的 `END_REPAIR_STUCK` 同一句话（「同一处、同一句话连着两次」），
**不另立一把尺子** —— 尺子就是 `agent.graph.failure_identity`（图判「是不是又撞了同一堵墙」
用的同一个函数）：只认**抱怨点名了哪个元素**，步号一个字都不看。
钉子：`tests/test_fam_report.py`（这一层）+ `tests/test_graph.py`（那一把尺子本身）。

## 用法

    python3 tools/fam_report.py                        # 默认读 runtime/（8099 那本）
    python3 tools/fam_report.py --root runtime-222     # 换一本账
    python3 tools/fam_report.py --root runtime --root runtime-222
    python3 tools/fam_report.py --json                 # 只要机器可读那份

退出码：
    0  读到了、也打完了
    2  **一本账都没读到** —— 这一次没检查（**不是**「都干净」）
    3  读到了，但有洞（有账本没有的趟 / 有判不出的趟）—— 表打出来了，洞在上面标着

⚠️ 只读：两个 sqlite 都以 `mode=ro` 打开，探路账本只读不写。
⚠️ **探路步号 ≠ 产物步号**：⑤ 那一格（失败步是哪种动作）是拿 `failed_step` 回探路账本
   按步号找的 —— 两个步号**未必一一对应**，所以那一格是**近似**，表里照实标着。
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 一族的四种读法（见文件头那张表）。`UNCLEAR` 是**一个结论**，不是「还不知道」——
#: 它说的是「这一趟的账本轮次不够 / 两轮都没点名，**这个问题没量到**」。
ROUNDS_STUCK = "A 原地反复"
ROUNDS_ADVANCING = "B 往前推进"
ROUNDS_UNNAMED = "同一个抱怨、但没点名（不是页面元素）"
ROUNDS_UNCLEAR = "分不出"

#: 尺子只有**一把**：`agent.graph.failure_identity`（图判「是不是又撞了同一堵墙」用的
#: 就是它，`_diagnose` 里那一格）。⚠️ **报告与图必须是同一把** —— 各写一个的话，
#: 报告说「A 类 5 趟」而图照旧回 draft 改稿，量出来的东西治不了。
#: 它只认「抱怨点名了哪个元素」，**步号一个字都不看**：修完会重写整份 py、步号跟着
#: 重编号（真事：`job-cedaca6eaa52` 4→3、`job-d40bb4d2d2be` 19→16、
#: `job-c053e0b049f3` 9→60、`job-cf1d84521a09` 0→100），而 `第 0 步` 满场都是 ——
#: 那是**没记到**，不是「第 0 步」。
_IDENTITY = None


def identity_of(round_row) -> str:
    """那一轮失败的**身份**（`failure_identity`）。装不进来就**出声拒绝**，不退化。"""
    global _IDENTITY
    if _IDENTITY is None:
        sys.path.insert(0, str(ROOT))
        try:
            from agent.graph import failure_identity as rule  # noqa: PLC0415
        except Exception as exc:                     # pragma: no cover —— 环境病
            raise SystemExit(
                "读不了失败身份：装不进 `agent.graph.failure_identity`（图用的那一把）。\n"
                "报告与图必须是同一把尺子 —— 这里**不退化**成另写一个。\n"
                "原话：%s: %s" % (type(exc).__name__, exc))
        _IDENTITY = rule
    return str(_IDENTITY(round_row if isinstance(round_row, dict) else {"note": round_row}) or "")

#: 「不算失败」的两支：人在浏览器里喊的停，和窗口基建。剔掉之后那个数才叫「跑到底」。
NOT_A_FAILURE = ("paused", "human_stop")
INFRA = ("no_window", "window_gone")

#: 人话表（`end_reason` → 这一族叫什么）。**没列到的原样印出来**，不吞。
FAMILY_SAY = {
    "delivered": "交付",
    "draft_failed": "写不出 py",
    "explore_unfinished": "探路没走完",
    "selftest_cap": "自测挂了修到顶",
    "lint_cap": "lint 打回 3 次",
    "deliver_lint": "交付前自检没过",
    "repair_stuck": "同一处修不动（图自己停的）",
    "repair_noop": "改稿那双手一处都没改",
    "missing_knob": "缺一根线",
}

#: `draft_failed` 下面那两副面孔（靠 `end_note` 分）。分不开页那一族**会过期**——
#: 报里按天列出最后一例，别拿一个总数当还在发生。
DRAFT_SPLIT = (("判据分不开页", "分不开页"), ("编译层（goto 后有动作 / 只有地址当门）", None))


def _need_msgpack():
    """`msgpack` 是 langgraph 的传递依赖，不是本仓直接声明的 —— 缺了要**当场说清**。"""
    try:
        import msgpack  # noqa: F401
    except ImportError as exc:                       # pragma: no cover —— 环境病
        raise SystemExit(
            "读不了账本：这一趟缺 `msgpack`（langgraph 带的那个）。\n"
            "它不在 requirements.txt 里（本仓没人直接 import 它），所以净环境可能没有 ——\n"
            "  装：%s -m pip install msgpack\n"
            "原话：%s" % (sys.executable, exc))
    return msgpack


def _unpack(blob):
    """一格写入 → 值。**解不开回 None**（调用方自己决定这算不算「量不到」）。"""
    try:
        return _need_msgpack().unpackb(blob, raw=False)
    except Exception:
        return None


# ── 那一处判断（钉子钉在这儿）───────────────────────────────────────────
def _named_of(ident: str) -> str:
    """身份串 → 它点名的那几个元素（`点名:X、Y` ⇒ `X、Y`）；没点名 ⇒ `""`。"""
    return ident[3:] if ident.startswith("点名:") else ""


def classify_rounds(rounds) -> str:
    """几轮诊断 → `STUCK` / `ADVANCING` / `UNNAMED` / `UNCLEAR`。**尺子是图那一把。**

    每一轮是 `{"failed_step": int|None, "note": str}`（后端自己记的那两格），
    身份由 `agent.graph.failure_identity` 算 —— 与图的 `_diagnose` 同一个函数。

    判据：
      · 两轮身份相同、且是**点名** ⇒ A（那一处修不动）；
      · 两轮身份不同、但**两轮都点了名** ⇒ B（修好一处又冒一处）；
      · 两轮身份相同、但**没点名**（正文一致）⇒ 记账类（我们自己没记下来，另一回事）；
      · 其余（轮次不够 / 有量不到的轮次 / 一轮点名一轮没有）⇒ **分不出**。

    ⚠️ 判据里**没有步号**，也不许加回去 —— 修完会重写整份 py，步号跟着重编号
       （真事 `job-cedaca6eaa52` 4→3、`job-d40bb4d2d2be` 19→16）。
    ⚠️ 「分不出」不许默认成任何一类：只要有一轮的身份量不到（note 是空的），
       这句话就该说「没量到」，不该说「没推进」。
    """
    rs = [r for r in (rounds or []) if isinstance(r, dict)]
    if len(rs) < 2:
        return ROUNDS_UNCLEAR
    ids = [identity_of(r) for r in rs]
    if not all(ids):                                 # 有轮次量不到 ⇒ 这个问题没量到
        return ROUNDS_UNCLEAR
    if len(set(ids)) > 1:
        return ROUNDS_ADVANCING if all(i.startswith("点名:") for i in ids) else ROUNDS_UNCLEAR
    return ROUNDS_STUCK if ids[0].startswith("点名:") else ROUNDS_UNNAMED


# ── 读账 ────────────────────────────────────────────────────────────────
def read_ledger(db: pathlib.Path) -> dict:
    """一本 `checkpoints.sqlite` → `{job_id: {...}}`。**只读**（`mode=ro`）。"""
    uri = db.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        jobs: dict = {}
        for tid, cid, ch, blob in cur.execute(
                "select thread_id, checkpoint_id, channel, value from writes "
                "where channel in ('end_reason','end_note','site','url','diagnoses',"
                "'lint_bounces','py_path','visits','hints')"):
            val = _unpack(blob)
            j = jobs.setdefault(tid, {})
            prev = j.get(ch)
            if prev is None or cid > prev[0]:
                j[ch] = (cid, val)
        #: `diagnosis` 是**每一轮一条**，不是最后一格 —— 单独按 cid 升序收。
        rounds: dict = collections.defaultdict(list)
        for tid, cid, blob in cur.execute(
                "select thread_id, checkpoint_id, value from writes "
                "where channel = 'diagnosis' order by checkpoint_id"):
            val = _unpack(blob)
            if isinstance(val, dict):
                rounds[tid].append((cid, val))
        for tid in jobs:
            jobs[tid]["_rounds"] = [r for _, r in sorted(rounds.get(tid, []))]
        return jobs
    finally:
        con.close()


def read_journal(root: pathlib.Path, job: str) -> list:
    """一趟的探路账本（`attempt-*.jsonl` 全读，按 `step_no` 升序）。没账本回 `[]`。"""
    out = []
    for p in sorted(glob.glob(str(root / "explore" / job / "attempt-*.jsonl"))):
        for line in pathlib.Path(p).read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict):
                out.append(o)
    out.sort(key=lambda o: int(o.get("step_no") or 0))
    return out


def journal_stats(steps: list) -> dict | None:
    """账本 → 这一趟的几个数。**没账本回 `None`**（不是一堆 0）。"""
    if not steps:
        return None
    consent = 0
    frames = set()
    for o in steps:
        t = o.get("target")
        t = t if isinstance(t, dict) else {}
        fid = str(t.get("frame_id") or "").strip()
        if fid:
            frames.add(fid)
        if str(o.get("action") or "") == "click":
            if _consent_click_like(str(o.get("note") or ""), t):
                consent += 1
    return {
        "steps": max((int(o.get("step_no") or 0) for o in steps), default=0),
        "consent": consent,
        "frames": len(frames),
    }


def _consent_click_like(note: str, target: dict) -> bool:
    """这一步是不是「对同意条/弹层做个决定」。

    ⚠️ **用的是系统自己那把尺子**（`agent.browser_agent._consent_click_like`）——
    报告与产物判据必须是同一把，不然量出来的东西治不了。装不进来（比如在容器里跑、
    `browser_agent` 的依赖不全）就**出声拒绝**，不退化成「我另写一个差不多的」。
    """
    global _CONSENT_RULER
    if _CONSENT_RULER is None:
        sys.path.insert(0, str(ROOT))
        try:
            from agent.browser_agent import _consent_click_like as ruler  # noqa: PLC0415
        except Exception as exc:                     # pragma: no cover —— 环境病
            raise SystemExit(
                "读不了「同意条」那一族：装不进 `agent.browser_agent` 里那把尺子。\n"
                "报告与产物判据必须是同一把 —— 这里**不退化**成另写一个。\n"
                "原话：%s: %s" % (type(exc).__name__, exc))
        _CONSENT_RULER = ruler
    return bool(_CONSENT_RULER(note, target))


_CONSENT_RULER = None


def family_of(reason: str, note: str) -> str:
    """`end_reason` + `end_note` → 这一族叫什么（人话，带细分）。"""
    r = str(reason or "")
    if r == "draft_failed":
        for label, marker in DRAFT_SPLIT:
            if marker and marker in note:
                return label
        return DRAFT_SPLIT[1][0]
    if r in NOT_A_FAILURE:
        return "人喊停（不算失败）"
    if r in INFRA:
        return "窗口基建（不算我们代码的病）"
    return FAMILY_SAY.get(r, r or "（没写停因）")


# ── 弹窗族：产物运行期那四格（从自测日志读）────────────────────────────
#
# 口径是「**次自测运行**」不是「趟」：自测目录名只有 `<站>-<时刻>`（`[selftest-<站>_<时刻>]`
# 是日志里的标签）、**不带 job id**，没法跟台账 join。而「那两道保护有没有用」这件事，
# 每次自测就是一次检验 —— 保护活在**产物**里，自测就是跑产物。
#
# ⚠️ 这四格量的是**产物运行期**（`template._POPUP_HOOKS` 的钩子表 + 账本那条步骤的下场），
# 与 ③ 那两格（探路期账本里有没有同意条点击）**不是一回事**，别混着读。

#: ① 钩子表命中：`把「同意条」点掉了（#si-accept，'Accept All Cookies'）：看起来成了`
_POPUP_HIT = re.compile(r"把「([^」]+)」点掉了")
#: ③a 账本里那条同意条步骤被**豁免**（`_consent_click_like` 那道保护生效）——
#: ⚠️ 这句**只会对同意类步骤说**，所以不用再判名字。
_POPUP_EXEMPT = re.compile(r"「([^」]+)」这一步：页面上已经没有同意弹层了")
#: ③b 某一步被判死（要再判一次名字是不是同意条 —— 普通步骤也走这句式）。
_POPUP_STEP_FAILED = re.compile(r"第 \d+ 步：页面上没找到「([^」]+)」，这一步没做成")


def selftest_popup_stats(root: pathlib.Path) -> dict:
    """跑一遍 `<root>/selftest/*/*/*.log`，数产物运行期那四格。

    返回 `{"runs", "hits"(Counter), "dup", "exempt", "died", "died_runs"}`。

    ## 判定规则

    ① `_POPUP_HIT` —— **自动弹窗钩子成功处理**的记录（`把「X」点掉了`）。
       同**一次运行**内同名命中 ≥2 次 ⇒ 计入 `dup`（重复处理）。

    ② `_POPUP_EXEMPT` —— 因「同意弹层已经不在了」而**豁免**的步骤。
       ⚠️ 该日志模板**目前只对同意类步骤说**，所以这里**不再判名字**。
       若将来那句模板扩到普通步骤，这一格会**静默多算** —— 这是已知的一处脆点，
       钉在 `test_the_popup_ledger_separates_exempt_from_died` 与段里那句 ⚠️ 上。

    ③ `_POPUP_STEP_FAILED` —— **未找到**的步骤。普通步骤用**同一句式**，
       所以必须拿 `agent.browser_agent._consent_click_like`（**执行期同一个函数**）
       对名字做**二次过滤**，否则 46 个普通步骤会被算成弹窗（实测：8099 的 `Yes` 16 + 2、
       `See if you qualify for benefits` 16；8100 的 `没写名字的元素` 28 + `Get a quote now` 2）。
       那个过滤走的是**宁可漏报、不误报**——与仓里那条判据同向。

    ⚠️ **`exempt` 与 `died` 必须分开记**：它们是「保护有没有用」这件事的两半。
    真事 `compareinsulation-20261013-075250`：钩子表命中 1 次、账本第 1 条同意条被**豁免**、
    第 2 条被判**死**。合成一个数就永远答不出这个问题。

    ## 统计边界（读这一格之前先读这几条）

    · **只读自测日志** —— 覆盖的是**产物运行期**，不是探路阶段的全部弹窗行为
      （探路期那一格在 ③：账本里有没有同意条点击）。两者**不是一回事**，别混着读。
    · **`runs == 0` = 没观测到**，**不是**「零弹窗故障」。段里照实说「没量到」。
    · **同名重复命中 ≠ 重复操作一定造成故障** —— `dup` 是日志事件，不是后果。
    · 以上全是**日志事件**，**不直接等价于失败根因**（真事：`compareinsulation` 那趟
      弹层被豁免了，真正没交付的原因是判据把 10 步判成不该做）。
    · **这量的是产物里那份代码在跑** —— 老产物带老骨架。读之前先对「这批产物是什么时候
      生成的」（次日就栽过：把历史的数当现状读）。
    """
    hits: collections.Counter = collections.Counter()
    dup = exempt = died = runs = 0
    died_runs = []
    for log in sorted(root.glob("selftest/*/*/*.log")):
        runs += 1
        text = log.read_text(encoding="utf-8", errors="replace")
        seen = collections.Counter(_POPUP_HIT.findall(text))
        hits.update(seen)
        dup += sum(1 for n in seen.values() if n > 1)
        exempt += len(_POPUP_EXEMPT.findall(text))
        for name in _POPUP_STEP_FAILED.findall(text):
            if _consent_click_like("", {"text": name}):
                died += 1
                died_runs.append(str(log))
    return {"runs": runs, "hits": hits, "dup": dup, "exempt": exempt,
            "died": died, "died_runs": died_runs}


def said_times(hints) -> int:
    """人在闸上**说过几次话**（`hints` 里每多一条 = 说了一句）。

    ⚠️ ★ 2026-10-14 **撤回了一版判据**，记在这儿免得再犯：
    第一版想从 `visits` 读「他有没有点修复」（`diagnose` 后面跟没跟 `draft`）——
    **那是错的**：`diagnose → draft` 是图在那道闸上的**默认路径**（`_next("draft")`），
    218 趟里 25/25 全中，零信息量。
    真查过之后：人打回那一档（`revised_at`）**218 趟里一次都没被用过**；人在闸上
    做过的事，账本上有痕迹的**只有这一格**（「说一句纠正」那句会进 `hints`）。
    ⇒ **「点修复」这个动作在账本上没有可靠痕迹** —— 别再从路线去猜它。
      要真记这一格，得让闸那一侧落一个明确的动作名。
    """
    return len([h for h in (hints or []) if str(h or "").strip()])


def site_host(key: str) -> str:
    """站点键 → 主机段（去重用；`getfixapro.com/x/y` 与 `getfixapro.com` 归一家）。"""
    s = str(key or "").strip()
    if not s:
        return "（没有站）"
    s = s.split("://", 1)[-1]
    return s.split("/", 1)[0] or "（没有站）"


# ── 算 ──────────────────────────────────────────────────────────────────
def survey(roots: list) -> dict:
    """把几本账读成一份统计。**读不到任何一本 ⇒ `{"empty": True}`**（不是一份空表）。"""
    rows = []
    no_journal = []
    missing = []
    open_jobs = []
    for root in roots:
        db = pathlib.Path(root) / "checkpoints.sqlite"
        if not db.exists():
            missing.append(str(db))
            continue
        ledger = read_ledger(db)
        for tid, j in ledger.items():
            reason = str((j.get("end_reason") or (0, ""))[1] or "")
            route = list((j.get("visits") or (0, []))[1] or [])
            if not reason:
                #: ★ 2026-10-14：**这一档以前是被 `continue` 掉的** —— 台账里 69 个
                #: （10-14 那天 3 个），而族分布 / A/B / 长度墙**全部跳过了它们**。
                #: 现在单列出来点名：读报表的人得看得见这一块。
                open_jobs.append({"job": tid, "last": str(route[-1]) if route else "",
                                  "said": said_times((j.get("hints") or (0, []))[1])})
                continue
            note = str((j.get("end_note") or (0, ""))[1] or "")
            steps = read_journal(pathlib.Path(root), tid)
            st = journal_stats(steps)
            if st is None:
                no_journal.append(tid)
                st = {"steps": None, "consent": None, "frames": None}
            rows.append({
                "job": tid, "root": str(root), "end": reason, "note": note,
                "site": str((j.get("site") or j.get("url") or (0, ""))[1] or ""),
                "rounds": j.get("_rounds") or [],
                "diag_n": int((j.get("diagnoses") or (0, 0))[1] or 0),
                "family": family_of(reason, note),
                "said": said_times((j.get("hints") or (0, []))[1]),
                **st,
            })
    if not rows and missing:
        return {"empty": True, "missing": missing}
    popup = {str(r): selftest_popup_stats(pathlib.Path(r)) for r in roots
             if (pathlib.Path(r) / "selftest").is_dir()}
    return {"empty": False, "rows": rows, "no_journal": no_journal, "missing": missing,
            "popup": popup, "open_jobs": open_jobs}


def _rate(rs: list) -> dict:
    tot = len(rs)
    dl = sum(1 for x in rs if x["end"] == "delivered")
    real = [x for x in rs if x["end"] not in NOT_A_FAILURE + INFRA]
    rdl = sum(1 for x in real if x["end"] == "delivered")
    return {"n": tot, "delivered": dl, "n_real": len(real), "real_delivered": rdl}


def _pct(a, b):
    return "%4.1f%%" % (100.0 * a / b) if b else "  —  "


def render(data: dict) -> str:
    if data.get("empty"):
        return ("一本账都没读到 —— **这一次没检查**（不是「都干净」）：\n  "
                + "\n  ".join(data.get("missing") or []) + "\n")
    rows = data["rows"]
    out = []

    def w(s=""):
        out.append(s)

    total = _rate(rows)
    w("═" * 78)
    w("失败族报告  终局 %d 趟  ·  交付 %d（%s）  ·  跑到底 %d 趟里交付 %d（%s）"
      % (total["n"], total["delivered"], _pct(total["delivered"], total["n"]),
         total["n_real"], total["real_delivered"],
         _pct(total["real_delivered"], total["n_real"])))
    w("  「跑到底」= 剔掉人喊停 + 窗口基建。两个数都要看：只给前一个，短分母会骗人。")
    w()

    w("─── ① 族分布（趟数 / 去重站数）" + "─" * 40)
    fam = collections.defaultdict(list)
    for r in rows:
        fam[r["family"]].append(r)
    for name, rs in sorted(fam.items(), key=lambda kv: -len(kv[1])):
        hosts = {site_host(r["site"]) for r in rs}
        st = _rate(rs)
        #: 「重试灌水」那句话**只对真失败族说** —— 交付/人喊停那两行的趟数本来就该多。
        drowned = (name not in ("交付", "人喊停（不算失败）")
                   and len(rs) > len(hosts) * 1.5)
        note = "  ← 去重后只有 %d 个站（重试灌水，别拿趟数当族的大小）" % len(hosts) if drowned else ""
        w("  %-30s %4d 趟 / %2d 站   交付 %2d（%s）%s"
          % (name, len(rs), len(hosts), st["delivered"], _pct(st["delivered"], st["n"]), note))
    w()

    w("─── ② 长度墙：探路步数分档 × 交付率" + "─" * 36)
    bins = [(0, 10), (11, 20), (21, 40), (41, 80), (81, 10 ** 9)]
    for lo, hi in bins:
        g = [r for r in rows if r["steps"] is not None and lo <= r["steps"] <= hi]
        st = _rate(g)
        if st["n"]:
            w("  %3d–%-4s 步   %3d 趟   交付 %2d（%s）   跑到底 %2d/%2d（%s）"
              % (lo, "+" if hi > 10 ** 8 else hi, st["n"], st["delivered"],
                 _pct(st["delivered"], st["n"]), st["real_delivered"], st["n_real"],
                 _pct(st["real_delivered"], st["n_real"])))
    w("  ⚠️ 相关不是因果：步数多也可能只是「页面本来就难」的影子。曲线陡不陡够用了，别当结论。")
    w("  ⚠️ 0–10 那一档不是「简单流程也做不好」—— 它是**没跑起来**那批（窗口连不上）。")
    w()

    w("─── ③ 假设对照（同一把尺子量出来的三组）" + "─" * 32)
    for label, key, cond in (("碰过 iframe", "frames", lambda v: v > 0),
                             ("有同意条点击", "consent", lambda v: v > 0)):
        a = [r for r in rows if r[key] is not None and cond(r[key])]
        b = [r for r in rows if r[key] is not None and not cond(r[key])]
        sa, sb = _rate(a), _rate(b)
        w("  %-14s 有 %3d 趟（交付 %s / 跑到底 %s）    无 %3d 趟（交付 %s / 跑到底 %s）"
          % (label, sa["n"], _pct(sa["delivered"], sa["n"]),
             _pct(sa["real_delivered"], sa["n_real"]),
             sb["n"], _pct(sb["delivered"], sb["n"]),
             _pct(sb["real_delivered"], sb["n_real"])))
    w("  ⚠️ 「碰过 iframe」只认元素在帧里（`target.frame_id`）；**整个表单是跨域 iframe**")
    w("     （wufoo 那种，探路直接 goto 进去）这一格量不到 —— 别把它读成「iframe 不要紧」。")
    w()

    w("─── ④ 修了几轮" + "─" * 56)
    rounds_hist = collections.Counter(len(r["rounds"]) for r in rows)
    for k in sorted(rounds_hist):
        w("  修了 %d 轮   %3d 趟" % (k, rounds_hist[k]))
    w()

    w("─── ⑤ A 类 / B 类（全表唯一带判断的一格）" + "─" * 29)
    verdict = collections.defaultdict(list)
    for r in rows:
        if len(r["rounds"]) >= 2:
            verdict[classify_rounds(r["rounds"])].append(r)
    n_multi = sum(len(v) for v in verdict.values())
    if not n_multi:
        w("  **这本账上没有一趟修过两轮以上** —— 这一格**没量到**（不是「没有 A 类」）。")
    else:
        w("  分母 = 修过两轮以上的 %d 趟（其余 %d 趟一轮都没修过，这一格对它们是空的）。"
          % (n_multi, len(rows) - n_multi))
        for k in (ROUNDS_STUCK, ROUNDS_ADVANCING, ROUNDS_UNNAMED, ROUNDS_UNCLEAR):
            rs = verdict.get(k) or []
            if not rs:
                if k in (ROUNDS_STUCK, ROUNDS_ADVANCING):
                    w("  %-30s   0 趟 —— 这是**量到的 0**（分母在上面）。" % k)
                continue
            w("  %-30s %3d 趟 / %2d 站" % (k, len(rs), len({site_host(r["site"]) for r in rs})))
            for r in rs[:6]:
                #: 摆**抱怨点名了哪个东西**，不摆步号 —— 步号跨轮不可比（见 `identity_of` 上面那段）。
                names = [_named_of(identity_of(x)) or "（没点名）" for x in r["rounds"][:4]]
                w("        %s  %-34s %s" % (r["job"], site_host(r["site"]),
                                            " → ".join(names)))
                if k == ROUNDS_UNNAMED:
                    #: 这一档里混着**好几种**病（记账 / 超时 / 没到成功页）—— 原文摆出来，
                    #: 由人分；合成一句「记账类」就是把三件事读成一件。
                    w("              { %s" % identity_of(r["rounds"][0])[3:][:96])
    w()

    w("─── ⑥ 人这边：人在闸上干了什么 · 没终局的趟" + "─" * 34)
    open_jobs = data.get("open_jobs") or []
    spoke = [r for r in rows if r.get("said")]
    w("  人在闸上**说过话**的趟：%d / %d（`hints` 里每多一条 = 他说了一句）。"
      % (len(spoke), len(rows)))
    w("  ⚠️ **「`diagnose` 后面跟了 `draft`」不是「他点了修复」** —— 那是图在那道闸上的")
    w("     **默认路径**（`_next(\"draft\")`）。第一版拿它当判据，218 趟里 25/25 全中、零信息量。")
    w("  ⚠️ 人打回那一档（`revised_at`，就是「**这版不行**」那个按钮）**218 趟里一次都没被用过**")
    w("     ⇒ **「点修复」这个动作在账本上没有可靠痕迹**。现在看得见的只有 `hints`。")
    w("     要真记这一格，得让闸那一侧落一个**明确的动作名**，别从路线去猜。")
    if open_jobs:
        w("  **没走到终局 %d 趟** —— 下面每一格（族分布 / A-B / 长度墙 / iframe / 弹窗）"
          "**都不含它们**：" % len(open_jobs))
        for o in open_jobs[:12]:
            w("      %-22s 停在「%s」   说过话 %d 次"
              % (o["job"], o["last"] or "?", o.get("said") or 0))
        if len(open_jobs) > 12:
            w("      …还有 %d 趟" % (len(open_jobs) - 12))
        w("  ⚠️ 「停在 selftest」**不一定是卡住** —— 也可能是**正在跑**。这本账里没有可靠的")
        w("     时间字段，分不出这两样；所以别把它读成「卡住了」，也别读成「他不管」。")
    else:
        w("  **没有没终局的趟**（这一本账上，每一趟都走到了头）。")
    w()

    w("─── ⑦ 弹窗族（产物**运行期**，从自测日志读）" + "─" * 34)
    popup = data.get("popup") or {}
    if not popup:
        w("  **一份自测日志都没读到** —— 这一格没量到（不是「没有弹窗问题」）。")
    for root, st in popup.items():
        w("  %s：自测运行 %d 次" % (root, st["runs"]))
        if not st["runs"]:
            w("      （这一本下没有 selftest 目录）—— 没量到。")
            continue
        w("      ① 钩子表命中 %d 次：%s"
          % (sum(st["hits"].values()),
             "、".join("%s×%d" % (k, v) for k, v in st["hits"].most_common()) or "一次都没有"))
        w("      ② 同一次里同一条被点 ≥2 次：%d 次运行" % st["dup"])
        w("      ③a 账本那条同意条被**豁免**（保护生效）：%d 次" % st["exempt"])
        w("      ③b 被判**死**（保护漏了）：%d 次" % st["died"])
    w("  ⚠️ 口径是「**次自测运行**」不是「趟」：自测目录名只有 `<站>-<时刻>`、不带 job id，")
    w("     没法跟台账 join。而「保护有没有用」这件事，每次自测就是一次检验。")
    w("  ⚠️ 这量的是**产物里那份代码**在跑 —— 老产物会带着老骨架。**跟 ⑤ 一样，")
    w("     读之前先对「这批产物是什么时候生成的」**（今天栽过：把历史的数当现状读）。")
    w()

    w("─── ⑧ 量不到的（不许并进 0）" + "─" * 44)
    if data["no_journal"]:
        w("  有终局、但**探路账本没留下** %d 趟 —— ②③ 两节把它们排除在外了：" % len(data["no_journal"]))
        w("    " + "、".join(sorted(data["no_journal"])[:10])
          + ("…" if len(data["no_journal"]) > 10 else ""))
    else:
        w("  探路账本：每一趟都有（②③ 两节没有缺口）。")
    n_diag0 = sum(1 for r in rows if r["diag_n"] == 0)
    w("  一轮都没修过的 %d 趟 —— ⑤ 那一格对它们是**空的**，不是「分不出」。" % n_diag0)
    n_noend = sum(1 for r in rows if not r["end"])
    if n_noend:
        w("  有记录但**还没走到终局**的 %d 趟（在跑的）—— 本表没收。" % n_noend)
    for m in data.get("missing") or []:
        w("  点名要读、可它不在盘上：%s" % m)
    w()
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="失败族报告（只读）")
    ap.add_argument("--root", action="append", default=None,
                    help="账所在目录（可给多次）；默认 runtime/")
    ap.add_argument("--json", action="store_true", help="只要机器可读那份")
    args = ap.parse_args(argv)
    roots = args.root or ["runtime"]
    data = survey([pathlib.Path(r) if pathlib.Path(r).is_absolute() else ROOT / r
                   for r in roots])
    if data.get("empty"):
        sys.stderr.write(render(data))
        return 2
    if args.json:
        print(json.dumps({"rows": [{k: v for k, v in r.items() if k != "rounds"}
                                   for r in data["rows"]],
                          "no_journal": data["no_journal"]},
                         ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render(data))
    holes = bool(data["no_journal"]) or any(not r["end"] for r in data["rows"])
    return 3 if holes else 0


if __name__ == "__main__":
    raise SystemExit(main())
