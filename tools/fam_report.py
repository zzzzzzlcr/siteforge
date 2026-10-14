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
**不另立一把尺子**。钉子：`tests/test_fam_report.py`。

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

#: 抱怨里**点名了哪个东西**（「…」里那几个）。判据只用它 —— 步号**一个字都不看**：
#: 修完会重写整份 py、步号跟着重编号（真事：`job-cedaca6eaa52` 第 4 步 → 第 3 步，
#: `job-c053e0b049f3` 第 9 步 → 第 60 步，抱怨一字未变），而 `第 0 步` 满场都是 ——
#: 那是**没记到**，不是「第 0 步」。
_QUOTED = re.compile(r"[「『“\"]([^」』”\"]+)[」』”\"]")

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
def _named(note) -> frozenset:
    """这一步的抱怨**点名了哪个东西**（`「…」`里那几个）。空集 = 没点名。"""
    return frozenset(_QUOTED.findall(str(note or "")))


def _shape(note) -> str:
    """抱怨正文，**步号抹平**（跨轮不可比，见 `_QUOTED` 上面那段）。"""
    return " ".join(re.sub(r"第\s*\d+\s*步", "第 N 步", str(note or "")).split())


def classify_rounds(rounds) -> str:
    """几轮诊断 → `STUCK` / `ADVANCING` / `UNNAMED` / `UNCLEAR`。

    每一轮是 `{"failed_step": int|None, "note": str}`（后端自己记的那两格）。

    判据（与 `END_REPAIR_STUCK` 同一句话：「同一处、同一句话连着两次」）：
      · 两轮都点名、且点的是**同一个东西** ⇒ A（那一处修不动）；
      · 两轮都点名、但点的是**不同的东西** ⇒ B（修好一处又冒一处）；
      · 两轮都没点名、但**抱怨正文逐字相同** ⇒ 记账类（**我们自己没记下来**，另一回事）；
      · 其余（轮次不够 / 一轮点名一轮没有 / 换了说法认不出）⇒ **分不出**。

    ⚠️ **步号一个字都不参与**：修完会重写整份 py，步号跟着重编号 —— 真事
       `job-cedaca6eaa52` 第 4 步 → 第 3 步、`job-d40bb4d2d2be` 第 19 步 → 第 16 步，
       而点名的都是同一个元素。第一版拿步号当判据，被这两条打回来了。
    ⚠️ 「分不出」不许默认成任何一类：判据里只要有一样没量到，
       这句话就该说「没量到」，不该说「没推进」。
    """
    rs = [r for r in (rounds or []) if isinstance(r, dict)]
    if len(rs) < 2:
        return ROUNDS_UNCLEAR
    names = [_named(r.get("note")) for r in rs]
    if all(names):                                   # 每一轮都点了名，才谈得上 A / B
        if len(set().union(*names)) == 1:
            return ROUNDS_STUCK
        return ROUNDS_ADVANCING
    shapes = [_shape(r.get("note")) for r in rs]
    if all(shapes) and len(set(shapes)) == 1:
        return ROUNDS_UNNAMED
    return ROUNDS_UNCLEAR


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
                "'lint_bounces','py_path')"):
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
    for root in roots:
        db = pathlib.Path(root) / "checkpoints.sqlite"
        if not db.exists():
            missing.append(str(db))
            continue
        ledger = read_ledger(db)
        for tid, j in ledger.items():
            reason = str((j.get("end_reason") or (0, ""))[1] or "")
            if not reason:
                continue                             # 还没走到终局的趟（在跑的）
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
                **st,
            })
    if not rows and missing:
        return {"empty": True, "missing": missing}
    return {"empty": False, "rows": rows, "no_journal": no_journal, "missing": missing}


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
                #: 摆**抱怨点名了哪个东西**，不摆步号 —— 步号跨轮不可比（见 `_QUOTED` 上面那段）。
                names = ["+".join(sorted(_named(x.get("note")))) or "（没点名）"
                         for x in r["rounds"][:4]]
                w("        %s  %-34s %s" % (r["job"], site_host(r["site"]),
                                            " → ".join(names)))
                if k == ROUNDS_UNNAMED:
                    #: 这一档里混着**好几种**病（记账 / 超时 / 没到成功页）—— 原文摆出来，
                    #: 由人分；合成一句「记账类」就是把三件事读成一件。
                    w("              { %s" % _shape(r["rounds"][0].get("note"))[:96])
    w()

    w("─── ⑥ 量不到的（不许并进 0）" + "─" * 44)
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
