"""Task 1（计划四）：一次探路 / 一个 run 的账 —— **先量，再决定**。

## 这一页为什么存在

计划四的其余部分（journal + 重放前缀 + 续跑）全建在一个判断上：
**「一次探路装不进一个窗口」**（设计注 §0.1）。而支撑它的证据只有**一次观察**（7m42s）。
所以第一件事不是实现，是**测量**。

## 判据（设计注 §3.3，**2026-09-17 改过** —— 照新版写）

```
max(M2) > M1              → 照做 A（存在**一条**路径装不进一个窗口，一条就够）
抽 ≥3 次且 max(M2) < 0.5×M1 → A 降级（Task 4/5/6 缩成「窗口死因 + 停因区分」，先做 B）
0.8 ~ 1.5 倍              → **undecided：这个数判不出来**，不许挑方向（处置按保守走：做 A）
```

⚠️ **为什么不是「`M2 ≤ M1` → 降级」**：这个漏斗是**分叉**的（§2.2）—— 选 Yes 多出三道题、
选 No 就跳过，同一条描述 20 / 33 / 34 步都合法。**一次跑只是一次抽样**，
拿它去判这条边界，下一次跑就能推翻它。所以判据看 `max`（最长的那条路），不看中位，
并且**抽样贴边界时必须说「判不出来」**（`verdict_for()` 把这三条写死了）。

## 三条规矩（比数字本身重要）

1. **算不出来就给 `None` 并说清为什么，绝不填 0。** 0 会被读成「量到了，而且它就是零」——
   那是「基线」与「猜」的分界。每一条 M 都带一个 `why`。
2. **`alive()` 问不出来（`None`）记 `unknown`，不许记成死。** 同 `agent/service.py:915`
   那条三态规矩：不知道不等于死。位置（窗口号）也不许动 —— 不知道换没换就是不知道。
3. **一次跑 = 一次抽样**（`single_sample`）。`M9`（这一趟走了哪条路）与
   `M10`（N 次抽样的步数跨度）就是为这条存在的：一个不带路径的墙钟数字**不可复现**，
   也就不是基线。

## 落盘的位置（`runtime/` 不进 git）

```
runtime/explore/<job_id>/
  window.jsonl        窗口时间线：一次探活一行（`window_row`）
  attempts.jsonl      一次探路一行：墙钟 / 轮数 / 步数 / 停因（`record_attempt`）
  attempt-<n>.jsonl   探路途中**每一步**一行（`append_step`，Task 4 会把它长成 journal.py）
  baseline.json       汇总：M1~M8（`baseline`）
```

⚠️ **两处已知的「量得到的下界」**（别把它们当精确值读）：

- `window_lifetimes()` 的寿命是**下界**：探针每 N 秒才看一眼，真实寿命是它加上
  **至多一个探测间隔**。要精确值得读 `/browser/detail` 的 `operTime`/`closeTime`。
- M6（死因：固定租约 vs 空闲回收）**本任务不自动判** —— 它要 `operTime`→`closeTime`
  的差恒不恒定、以及死亡前有没有一段空闲。这两样本任务都没采（只采了活/死与 PID），
  所以给 `None` + 一句说清要人去哪儿看。**不许拿「探测间隔」冒充「寿命差」。**
"""

from __future__ import annotations

import datetime
import json
import pathlib
import statistics
from typing import Any, Optional

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 运行产物落在哪（`runtime/` 已在 `.gitignore` 里；与 `runtime/selftest/` 同层不同目录）。
DEFAULT_ROOT = _REPO / "runtime" / "explore"

#: 窗口状态的三态（**`unknown` 不是 `dead`**）。
ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"

#: 基线表旁边**必须**跟着的那句话（人 2026-09-17 提的：这个漏斗分叉）。
SINGLE_SAMPLE_WHY = (
    "这是一次**抽样**，不是分布：这个漏斗是**分叉**的（选 Yes 会多出三道题、选 No 就跳过），"
    "描述里那 33 步只是**其中一条路**的长度 —— 换个选择就是 20 步或 34 步。"
    "要谈「一次探路多贵」得跑几次，别拿一次当结论。"
)


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _seconds(started_at: Any, ended_at: Any) -> tuple[Optional[float], str]:
    """两个时刻之间差多少秒。读不出来 → `(None, 为什么)` —— **不是 0**。"""
    try:
        a = datetime.datetime.fromisoformat(str(started_at))
        b = datetime.datetime.fromisoformat(str(ended_at))
    except (TypeError, ValueError):
        return None, ("这两个时刻里有一个读不出来（%r → %r），所以这一段墙钟**没量到**"
                      % (str(started_at)[:40], str(ended_at)[:40]))
    secs = (b - a).total_seconds()
    if secs < 0:
        return None, "结束时刻早于开始时刻（%s → %s），这一段墙钟**没量到**" % (started_at, ended_at)
    return secs, ""


# ─────────────────────────── 落盘（一行一次 write）───────────────────────────


def _append_line(path: pathlib.Path, row: dict) -> None:
    """追加一行。**一行一次 `write`** —— 进程被杀最多留下半行，不会串行。"""
    path = pathlib.Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Any) -> list:
    """把一份 jsonl 读回来。**坏行不吞**：读不出来的那行原样留一条 `_corrupt` 记录。

    （「跳过的行数与原因一起返回」那一条是 Task 4 的 `journal.read()`；这里做同一件事的
    最小版本：坏行不许**静默**消失。）
    """
    p = pathlib.Path(path)
    if not p.exists():
        return []                        # 没跑过 = 空账，不是异常
    out: list = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            out.append({"_corrupt": line[:160], "_line": i})
            continue
        if isinstance(row, dict):
            out.append(row)
        else:
            out.append({"_corrupt": line[:160], "_line": i})
    return out


def append_row(path: Any, row: dict) -> None:
    """往一份 jsonl 追加一行（窗口时间线 / journal 都走这儿）。"""
    _append_line(pathlib.Path(path), dict(row))


def append_step(path: Any, step: dict) -> None:
    """探路途中**每一步**一行 —— 落盘的就是 `Journey.steps` 的那一步，**逐字同一个 dict**。

    （跨任务接口 §2：journal 的一行不许再包一层、不许改键名 —— 否则「账本」与「重放」
    会各有一套字段名，那正是漂。Task 4 会把它长成 `agent/journal.py`。）
    """
    append_row(path, step)


# ─────────────────────────── 窗口时间线 ───────────────────────────


def window_row(probe: dict, *, at: str, note: str = "", prev: Optional[dict] = None) -> dict:
    """一次探活 → 时间线的一行。

    `probe` 就是 `agent.service.BitWindow.probe()` 的产物：`{"alive": bool|None, "pid": int|None}`。
    `prev` 是**上一行**（给了才知道「换窗口了没有」——这一步必须有人记得上次是谁）。

    判「换了窗口」的两条判据，都是**事实**不是猜测：

    1. **PID 变了**（两个都问得出来时）—— E2：一次跑里 PID 至少换过 3 次；
    2. **上一行是死的、这一行是活的** —— 死掉的窗口不会自己回来，所以这必然是**新开的**。
       （PID 只在活着的时候问得出来：`data` 是空 dict 时什么都没有。所以这一条不是补充，
       是「死→重开」这一跳**唯一**还看得见的信号。）

    问不出来（`alive is None`）→ 记 `unknown`、窗口号**不动**：不知道换没换，就不许编一个。
    """
    probe = probe or {}
    raw = probe.get("alive")
    state = ALIVE if raw is True else (DEAD if raw is False else UNKNOWN)
    pid = probe.get("pid")
    pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None

    prev = prev or {}
    was = int(prev.get("window") or 0)
    prev_pid, prev_state = prev.get("pid"), prev.get("alive")
    if was <= 0:
        number, fresh = 1, True
    elif pid is not None and isinstance(prev_pid, int) and pid != prev_pid:
        number, fresh = was + 1, True
    elif state == ALIVE and prev_state == DEAD:
        number, fresh = was + 1, True
    else:
        number, fresh = was, False

    return {"at": str(at), "alive": state, "pid": pid, "window": number,
            "new_window": fresh, "note": str(note or "")}


def window_lifetimes(rows: list) -> list:
    """每个**死过的**窗口活了多久（秒）—— 从「第一次被看见」到「被发现死了」。

    ⚠️ 精度是**探针间隔**：真实寿命 = 这个数 ± 至多一个探测间隔（窗口死的那一刻没人看见，
    是下一次探活才发现）。**别拿它当精确值读**；要精确值得读 `/browser/detail` 的
    `operTime`/`closeTime`（设计注 §3.1 要的正是那个）。
    还活着的窗口**不算**（它没死，寿命未知 —— 不许拿「到这一刻为止」冒充寿命）。
    """
    out: list = []
    counted: set = set()
    for i, row in enumerate(rows):
        number = row.get("window")
        if not isinstance(number, int):
            continue
        if row.get("alive") == DEAD:
            prev_no = number
        elif i > 0 and row.get("new_window"):
            prev_no = number - 1          # 换了个新窗口 → 上一个到此为止（它死在这一行之前）
        else:
            continue
        if prev_no in counted or prev_no < 1:
            continue
        first = next((r for r in rows[:i] if r.get("window") == prev_no), None)
        if first is None:
            continue
        secs, _why = _seconds(first.get("at"), row.get("at"))
        if secs is None:
            continue
        counted.add(prev_no)
        out.append(secs)
    return out


# ─────────────────────────── 一次探路的账 ───────────────────────────


def record_attempt(path: Any, *, started_at: Any, ended_at: Any, rounds: Any = None,
                   steps: Any = None, stop_reason: str = "", notes: Optional[list] = None,
                   path_shape: Optional[dict] = None) -> dict:
    """一次探路 = 一行（**追加**）。返回的那份**就是**落盘的那份。

    - `rounds` 给 `None` = **没记到**（比如被人打断：`rounds` 是工具循环的局部变量，
      `_Stop` 一穿出去就没了）。**不许拿 0 顶上** —— 0 在这里会被读成「这一趟没花轮数」，
      于是 M3 偏低，而偏低看起来像好消息。
    - `seconds` 由两个时刻算；算不出来 → `None` + `seconds_why`。
    - `path_shape`（M9 的载体，关键字参数带默认值 = **对在飞实现是加法**）：`path_shape(rows)`
      的产物。**一次样本一存**：分支站的两次跑长度不同（§2.2），只有存下每趟的形状，
      M10 的跨度才算得出来。
    """
    secs, why = _seconds(started_at, ended_at)
    row = {
        "started_at": str(started_at or ""),
        "ended_at": str(ended_at or ""),
        "seconds": secs,
        "seconds_why": why,
        "rounds": rounds if isinstance(rounds, int) else None,
        "steps": steps if isinstance(steps, int) else None,
        "stop_reason": str(stop_reason or ""),
        "notes": [str(n) for n in (notes or [])],
        "path_shape": dict(path_shape) if isinstance(path_shape, dict) else None,
    }
    _append_line(pathlib.Path(path), row)
    return row


def read_attempts(path: Any) -> list:
    """读回 `attempts.jsonl`。坏行不吞（原样留一条 `_corrupt`，数字全是 `None`）。"""
    out: list = []
    for row in read_rows(path):
        if "_corrupt" in row:
            out.append({"started_at": "", "ended_at": "", "seconds": None,
                        "seconds_why": "这一行读不出来（%s）：墙钟**没量到**，不许当 0"
                                       % row["_corrupt"][:80],
                        "rounds": None, "steps": None, "stop_reason": "",
                        "notes": [], "_corrupt": row["_corrupt"]})
        else:
            out.append(row)
    return out


def path_shape(rows: list) -> dict:
    """这一趟**走了哪条路**（M9 的载体）—— 从 journal 的步骤里读，机械可判、不猜语义。

    形状（计划里那份，外加一个 `choices`）：

      - `steps`       实际走了多少步（`len(rows)`）
      - `states`      页面状态**序列**（换页 = 换状态，`_Pages` 给的 —— 感知，不是判断）
      - `marks`       模型自己报的位置标记 `【第 k 步】`序列 —— **Task 3 才有**；
                      今天没有任何一行带标记 → `None` + `marks_why`。**不填 `[]`**：
                      空列表会被读成「量到了，一个标记都没报」，而今天根本没人报过。
      - `jumped_over` 标记跳号时被跳过的号（§2.2 的分支：从 2 跳到 5 → 3、4 被跳过）
      - `choices`     点过哪些东西（原样一句话 + target）—— 人 2026-09-17 要的
                      「这一趟在分叉点上选了哪个」：**只有点**会把漏斗带到别的分支上去
                      （看一眼 / 截个图不会）。这里**不判**「哪个是选项」（那是语义判断，
                      D11 不许），只把「点了什么」原样抄下来。
    """
    steps = list(rows or [])
    states: list = []
    choices: list = []
    marks: list = []
    for i, step in enumerate(steps, 1):
        step = step or {}
        name = step.get("state")
        if name and (not states or states[-1] != name):
            states.append(name)
        if step.get("action") == "click":
            choices.append({"n": i, "note": step.get("note") or "",
                            "target": dict(step.get("target") or {})})
        if isinstance(step.get("mark"), int):
            marks.append(step["mark"])

    out: dict = {"steps": len(steps), "states": states, "choices": choices}
    if marks:
        jumped: list = []
        for a, b in zip(marks, marks[1:]):
            if b > a + 1:
                jumped.extend(range(a + 1, b))
        out["marks"] = marks
        out["jumped_over"] = jumped
    else:
        out["marks"] = None
        out["marks_why"] = ("这一趟账里**没有任何一行带位置标记**（`【第 k 步】`是 Task 3 才有的约定："
                            "描述今天只是散文，模型没有标记可报）。**这不是「报了 0 个」。**")
        out["jumped_over"] = None
        out["jumped_over_why"] = "跳号要看标记序列；标记没记到，跳号也无从谈起"
    return out


# ─────────────────────────── 汇总：M1~M8 ───────────────────────────


def _m(value: Any, why: str = "", **extra: Any) -> dict:
    return {"value": value, "why": why, **extra}


def _spread(values: list, what: str) -> dict:
    """一串样本 → `{min, median, max, n}`（**判 A 做不做看的是 max**，见设计注 §3.3）。"""
    return {"value": statistics.median(values), "min": min(values),
            "median": statistics.median(values), "max": max(values), "n": len(values),
            "samples": list(values),
            "how": "%s 的样本序列（value = 中位；**判 A 做不做看的是 max** —— 分支站一次抽样"
                   "说了不算，§3.3）" % what}


def verdict_for(max_m2: Optional[float], m1: Optional[float], samples: int) -> dict:
    """**A 做不做**（设计注 §3.3，2026-09-17 改过的判据）—— 只看 `max(M2)` 与 `M1`。

    改过的地方：判据从「`M2 ≤ M1` → 降级」改成「`max(M2) > M1` → 照做 A」，理由是这个漏斗
    **分叉**（§2.2）：一次跑 20 步、下一次 34 步都正常，单次样本判不了这条。

    - `max(M2) > M1` → `A`（**存在**一条路径装不进一个窗口 —— 一条就够）
    - 抽了 **≥3 次**且 `max(M2) < 0.5 × M1`（远小于）→ `A_downgrade`
    - **0.8~1.5 倍 → `undecided`**：这个数判不出来，**不许挑方向**（处置按保守走：照做 A）
    - 样本不够 / 边界外那两条之间 → 一律 `undecided`，并把「还差什么、再抽一次要花什么」写出来
    """
    if max_m2 is None or m1 is None or not m1:
        return {"branch": "unknown", "ratio": None, "samples": samples,
                "why": ("寿命或探路墙钟有一头没量到 —— 判据是 `max(M2) > M1`，两样都要有数。"
                        "窗口这一次没死（M1 空）的话，这条判据**今天给不出结论**："
                        "它要的是「窗口会死」这件事在**同一批样本里**出现过。")}
    ratio = float(max_m2) / float(m1)
    why_more = ("再抽一次 = 一条真窗口 + 一次完整探路（这台机器上大约十几分钟 + 一次模型账）。"
                "样本越多，`max` 越接近「最长的那条分支」。")
    if ratio > 1.5:
        return {"branch": "A", "ratio": ratio, "samples": samples,
                "why": "max(M2)/M1 = %.2f > 1.5：**存在**一条路径装不进一个窗口 —— 一条就够，"
                       "照做 A。" % ratio}
    if samples >= 3 and ratio < 0.5:
        return {"branch": "A_downgrade", "ratio": ratio, "samples": samples,
                "why": "抽了 %d 次、max(M2)/M1 = %.2f 仍不到一半 → A 的收益小，"
                       "Task 4/5/6 缩成「窗口死因识别 + 停因区分」，先做 B。" % (samples, ratio)}
    if 0.8 <= ratio <= 1.5:
        return {"branch": "undecided", "ratio": ratio, "samples": samples,
                "why": "**这个数判不出来**：max(M2)/M1 = %.2f 落在 0.8~1.5 倍这个带里（§3.3），"
                       "边界上的一次抽样谁都说服不了 —— **不许挑一个方向下结论**。"
                       "处置按保守走（照做 A：它的收益只依赖「窗口会死」这个已确认的事实）。"
                       "%s" % (ratio, why_more)}
    small = ("样本只有 %d 次，而「A 降级」要求 **≥3 次**且 max(M2) 远小于 M1 —— "
             "现在这个数既不在边界带里，样本也不够。" % samples)
    return {"branch": "undecided", "ratio": ratio, "samples": samples,
            "why": "%s max(M2)/M1 = %.2f。%s" % (small, ratio, why_more)}


def baseline(path: Any, *, window_lifetimes: list, attempts: list, end: dict,
             window_rows: Optional[list] = None, path_steps: Optional[list] = None,
             probe_seconds: Optional[float] = None) -> dict:
    """把一次跑压成基线表（设计注 §3.2 的 M1~M10），落 `baseline.json` 并返回同一份。

    五条规矩：

      - **能算的算**：M1 寿命、M2/M3 的**样本序列**、M4 重启次数、M5 死一次多花的轮数、
        M9 路径形状、M10 步数跨度
      - **算不出来的给 `None` + `why`**（M6 死因 / M7 计划标记 / M8 站方反应 —— 本任务的输入里
        根本没有它们）。**绝不填 0**：0 会被读成「量到了，是零」。
      - **一次样本 != 一个数**：`single_sample` 标出来。分支站的一次跑只是一次抽样（§2.2），
        拿它去判 §3.3 那条边界是错的。
      - **有一次没量到 → 合计也 `None`**：M2/M3 有一次算不出来就整条作废，
        因为「一部分 + 剩下的当 0」是个偏低的数，而偏低看起来像好消息。
      - **`verdict` 是判据本身**（`max(M2) > M1`，带 `undecided` 带）—— 见 `verdict_for`。
    """
    lifetimes = [float(x) for x in (window_lifetimes or []) if isinstance(x, (int, float))]
    attempts = list(attempts or [])
    end = dict(end or {})
    rows = list(window_rows or [])
    out: dict = {}

    # ── M1 窗口寿命（中位 / 最短）────────────────────────────────
    if lifetimes:
        out["M1"] = _m(statistics.median(lifetimes), "", min=min(lifetimes), n=len(lifetimes),
                       how="探针看见的「第一次被看见 → 被发现死了」（**精度是探针间隔**，"
                           + ("这一次是每 %g 秒探一次" % probe_seconds if probe_seconds
                              else "这一次没记下探针间隔")
                           + "）：真实寿命与它相差至多一个间隔")
    else:
        out["M1"] = _m(None, "这一次跑的时间线里**一次「死了」都没记到**（窗口没死，"
                             "或者探针没看见）—— 寿命量不到。**这不是「寿命是 0 秒」。**")

    # ── M2 一次探路墙钟（样本序列）──────────────────────────────
    bad_secs = [i for i, a in enumerate(attempts, 1) if not isinstance(a.get("seconds"), (int, float))]
    secs = [a["seconds"] for a in attempts if isinstance(a.get("seconds"), (int, float))]
    if not attempts:
        out["M2"] = _m(None, "这一次跑一条探路记录都没有 —— 没量到，不是 0 秒")
    elif bad_secs:
        out["M2"] = _m(None, "第 %s 次尝试的墙钟没量到 → **样本序列不完整**，"
                             "所以 min/中位/max 一个都不给（不许把没量到的那次当 0 加进去）"
                             % "、".join(str(i) for i in bad_secs))
    else:
        out["M2"] = _spread(secs, "一次探路的墙钟（秒）")

    # ── M3 一次探路模型轮数（样本序列）──────────────────────────
    bad_rounds = [i for i, a in enumerate(attempts, 1) if not isinstance(a.get("rounds"), int)]
    rounds = [a["rounds"] for a in attempts if isinstance(a.get("rounds"), int)]
    if not attempts:
        out["M3"] = _m(None, "这一次跑一条探路记录都没有 —— 没量到，不是 0 轮")
    elif bad_rounds:
        out["M3"] = _m(None, "第 %s 次尝试的轮数没记到（被人打断时 `_Stop` 穿过 "
                             "`run_tool_loop`，那个数拿不到）—— 拿 0 顶上会让 M3 **偏低**，"
                             "而偏低看起来像好消息" % "、".join(str(i) for i in bad_rounds))
    else:
        out["M3"] = _spread(rounds, "一次探路的模型轮数（不是 steps —— steps 数的是工具调用，含 observe）")
        out["M3"]["per_checkpoint"] = _m(
            None, "「每检查点摊多少轮」（§四 B1 的判据）今天量不到：检查点清单是 Task 2/3 才有的，"
                  "所以这一趟没有分母。**不许拿总轮数当它** —— 分支站两次跑的长度不同。")

    # ── M4 一个 run 的窗口重启次数 ──────────────────────────────
    opened = end.get("windows_opened")
    numbers = [r.get("window") for r in rows if isinstance(r.get("window"), int)]
    if isinstance(opened, int) and opened >= 1:
        out["M4"] = _m(int(opened) - 1, "", n=int(opened),
                       how="这一次跑开过几个窗口 − 1（重启次数）")
    elif numbers:
        out["M4"] = _m(max(numbers) - 1, "", n=max(numbers),
                       how="从窗口时间线的窗口号推出来（没给 windows_opened）")
    else:
        out["M4"] = _m(None, "窗口时间线是空的（这一次跑没开窗口层，或探针没跑起来）")

    # ── M5 死一次多花多少模型轮数 ────────────────────────────────
    if len(attempts) < 2:
        out["M5"] = _m(None, "这一次跑只探了一趟（没有第二次尝试）—— 「死一次要多花多少」"
                             "**要死过才量得到**，不许拿 0 当量到了")
    elif bad_rounds:
        out["M5"] = _m(None, "第 %s 次尝试的轮数没记到，所以「多花多少」也算不出来"
                             % "、".join(str(i) for i in bad_rounds))
    else:
        out["M5"] = _m(sum(a["rounds"] for a in attempts[1:]), n=len(attempts) - 1,
                       how="今天每死一次就从零再探一遍，所以第 2 趟起的轮数**全是白花的**"
                           "（本片之后这个数应该是 0：重放不花模型轮数）")

    # ── M6 死因：固定租约 vs 空闲回收 ────────────────────────────
    out["M6"] = _m(None, "本任务只采了「活/死 + PID」，而判死因要两样没采的东西："
                         "每次 open→close 的时长（`/browser/detail` 的 `operTime`/`closeTime`，"
                         "看那个差恒不恒定）与死亡前有没有一段空闲。**只能人看**："
                         "window.jsonl 的原始行 + 事后读一次 `/browser/detail`。"
                         "⚠️ 不许拿「探测间隔」冒充「寿命差」。")

    # ── M7 计划标记合规率（B 侧）────────────────────────────────
    out["M7"] = _m(None, "计划模式（每轮开头标 `【第 k 步】`）是 Task 3 才有的 —— "
                         "这一趟的描述只是散文，模型没有标记可报，合规率无从谈起。")

    # ── M8 站方反应（验证码 / 拦截 / 意外弹窗）──────────────────
    out["M8"] = _m(None, "「这是站方弹窗」没有一条自动判据（要判就得猜语义，D11 不许）—— "
                         "只能人看：window.jsonl 的 `note` 与 journal 里那几步的 `note`。")

    # ── M9 这一趟走了哪条路（路径形状）──────────────────────────
    shapes = [a.get("path_shape") for a in attempts if isinstance(a.get("path_shape"), dict)]
    if shapes:
        out["M9"] = _m(shapes, "", n=len(shapes),
                       how="每趟一个 `path_shape`（步数 / 状态序列 / 标记序列 / 点了哪些东西）。"
                           "**它解释 M2/M3 的分散** —— 分支站的长度本来就会变（§2.2）")
    elif path_steps:
        out["M9"] = _m([path_shape(path_steps)], "", n=1,
                       how="只有 journal 的原始行（`attempts.jsonl` 里没存 path_shape）")
    else:
        out["M9"] = _m(None, "这一趟一条 journal 都没落（探路没跑 / journal 没接上）—— "
                             "走了哪条路**没量到**")

    # ── M10 分支离散度：步数的 min→max 跨度 ───────────────────────
    per_sample = [s["steps"] for s in shapes if isinstance(s.get("steps"), int)]
    if not per_sample:
        per_sample = [a["steps"] for a in attempts if isinstance(a.get("steps"), int)]
    if len(per_sample) < 2:
        out["M10"] = _m(None, "只有 %d 次抽样 —— **一次抽样没有跨度**（§2.2：分支站的步数"
                              "本来就变，20 / 33 / 34 都正常）。这一条要 ≥2 次才算得出来。"
                              % len(per_sample))
    else:
        out["M10"] = _m({"min": min(per_sample), "max": max(per_sample),
                         "span": max(per_sample) - min(per_sample)},
                        "", n=len(per_sample),
                        how="同一条描述 N 次抽样里实际步数的 min→max —— 这就是「33 步不固定」的量")

    # ── 判据（§3.3，2026-09-17 改过）────────────────────────────
    out["single_sample"] = len(attempts) <= 1
    out["sample"] = {"runs": len(attempts), "single_sample": len(attempts) <= 1,
                     "why": SINGLE_SAMPLE_WHY}
    out["verdict"] = verdict_for(
        (out["M2"].get("max") if out["M2"]["value"] is not None else None),
        (out["M1"]["value"]), len(attempts))
    out["attempts"] = [{"n": i, "seconds": a.get("seconds"), "rounds": a.get("rounds"),
                        "steps": a.get("steps"), "stop_reason": a.get("stop_reason"),
                        "path_shape": a.get("path_shape")}
                       for i, a in enumerate(attempts, 1)]
    out["end"] = end
    out["at"] = _now()
    out["counts"] = {"windows": (max(numbers) if numbers else None), "attempts": len(attempts)}
    pathlib.Path(path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
