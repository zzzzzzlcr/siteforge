"""Task 6：扰动自测 —— 决定一条产出的 py 值不值得交出去（规格 §10）。

## 它为什么存在

同一环境连跑三遍容易**假通过**：它证明的只是「同一个条件下能重复」，
而生产失败几乎都来自条件变化。所以这里是**扰动序列**，每遍打的是不同类的失败：

| 遍 | 扰动 | 打的是什么 | 怎么落地 |
|---|---|---|---|
| 1 | 正常 | 基线 | 直接跑 |
| 2 | 页面上**接着再跑一遍** | 状态残留 / 首次加载假设 | 同一个 `ws_url`、**不重置页面**（R-6）；给了 `entry_url` 就先 `cdp navi` 过去，覆盖「刷新后重跑」的字面读法 |
| 3 | 注入延迟 | 时序竞争 / 没等就点 | 走产物自带的 `--delay`（R-4），**不改写产物源码** —— 改写过的源码测的是另一个产物 |
| 4 | 换 viewport | 折叠 / 遮挡 / 坐标假设 | 注入式回调 `set_viewport`（R-5）：viewport 是**窗口层**的事（`POST /browser/update`），产物和 cdp 内核都够不着 |
| 5 | 换代理国家 | 地区内容差异（规格 §10 说这是 R1 的主要来源） | 注入式回调 `set_country`；不给就跳过（这条要重拉 gost 链，单遍成本高，计划里就标了**可选**）|

## R-84（2026-09-17，**用户裁定**）：不再固定跑三遍 —— **每一遍都是一次真实提交**

**用户原话：「不需要 3 遍。刷太多不太好」**（现场佐证：他为此**亲手关掉了 Bit 窗口**才止住它）。
每跑一遍 = 产物把整个漏斗从头走一遍 = **一次真实提交到站方**；而流水线还会循环
（定稿→自检→自测→诊断→再定稿），**每循环又是一遍三趟** —— 73 趟自测 ≈ **上百次提交**
到同一个 lead 表单。**对 lead-gen 类站点，这不是效率问题，是会伤到站方的。**

新判据（**这一节是承重的，别把它做软**）：

| | |
|---|---|
| **默认** | 只跑 **1 遍**（`baseline`）。**过了就算过。** |
| **补跑** | **只在没过的时**才往下跑（`rerun` → `delay`）—— 用来分辨「产物不行」还是「这一趟环境抖了」 |
| **上界** | 仍然按 `RUN_NAMES` 那一串往下走，但**只在需要时才用到** |
| **硬顶** | **每轮验收最多 `MAX_SUBMISSIONS`（3）次提交**，到顶就停、**如实报**，不许悄悄超 |

⚠️ **「没过必须补跑」是承重的一半**：少跑的那几遍本来是打「状态残留 / 时序竞争 /
折叠遮挡」这三类的 —— 默认只跑一遍会把它们**全漏掉**。所以新判据的核心不是「少跑」，
是「**成功才少跑，失败必须补**」。把这条做丢，验收就变成走过场。

两条随之而来的性质（都落在报告里，别让读者自己猜）：

- **没跑的那几遍照样列出来**（状态 `not_needed`，`ok=None`），并写清它打的那一类
  **这一次没验到** —— 「少跑」不许读成「验过了」；
- **提交次数**（`Report.submissions`）跟着报告走，闸口的 facts 里也报 ——
  **这次出事就是因为看不见**；看不见的话，下次还会有人悄悄刷起来。

## 一条不能破的性质：诚实

**任一遍挂 → `passed=False`，并且说得清是哪一遍、卡在第几步**（计划 Task 6 的 ⚠️：
不许把「某遍挂」吞成「部分通过」）。落地成两条：

1. **判据是证据，不是愿望**：一遍算过，必须是「退出码 0」**且**「trace 里没有
   `ok=false` 的行」。产物自己谎报成功（退出码 0 但那步没做成）也**按没做成算**。
   「卡在第几步」= trace 里第一条 `ok == false` 的行的 `step` ——
   **不读 `progress`**（老 cdp 上它恒 `null`，旁边有 `progress_why`）。
2. **「跳过」不许长得像「过了」**：跳过的那遍 `status="skipped"`、`ok=None`
   （**不是 True**），而且默认**不算通过** —— 只有调用方**点名**允许的那几遍
   （`allow_skips`，默认只有第 5 遍）才不拦 `passed`。第 4 遍没给回调时
   跳过 + **吵**：报告里明说「这一类失败这次没验到」。

## 边上两件必须说清的事

- **工具侧自测通过 ≠ 生产一定过**（规格 §10 原话）：自测跑的浏览器与生产 worker 的
  代理出口、指纹、时序都不是一套。扰动测试缩小这个差，但不消除它 —— 所以
  `CAVEAT` 跟着每一份报告走（`Report.summary()` 里永远有它）。
- **自测不往生产写**：自测是拿真浏览器跑真站，但它不是生产任务。所以每一遍都带
  产物的 `--no-report`（`NO_REPORT_FLAG`，永远给），一个字节都不发去生产的 URL
  记录接口。这条不许退化成「argv 里有个字符串」——`tests/test_selftest.py` 是在
  子进程里装网络守卫、按**有没有出网动作**断言的（并且带正控：不给这个参数时必须
  留下记录，否则「没有记录」可能只是网线本来就没通）。

## 谁消费它

- Task 7 的图：`selftest` 节点挂 → 走 `diagnose`，**带上 `failed_step`**
- Task 8 的真站端到端：交付前跑一遍，报告进 PROVENANCE 的 `selftest` 键
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

__all__ = ["Run", "Report", "run", "RUN_NAMES", "DEFAULT_ALLOWED_SKIPS", "CAVEAT",
           "DEFAULT_ROOT", "default_run_dir"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 五遍的名字（稳定键：报告、图、PROVENANCE 都认这几个，改它们等于改契约）
RUN_NAMES = ("baseline", "rerun", "delay", "viewport", "country")

#: 每遍**人话**说它打什么（D16：给非技术人员看的不是错误码）
RUN_LABELS = {
    "baseline": "第 1 遍：正常跑一遍（基线）",
    "rerun": "第 2 遍：在同一个页面上接着再跑一遍（打状态残留 / 首次加载假设）",
    "delay": "第 3 遍：每一步多停一会儿再跑（打时序竞争 / 没等就点）",
    "viewport": "第 4 遍：换个窗口大小再跑（打折叠 / 遮挡 / 坐标假设）",
    "country": "第 5 遍：换个代理国家再跑（打地区内容差异）",
}

#: 这一遍**打的是哪一类失败**（R-84：没跑的那几遍要能一句说清「哪一类这次没验到」）。
RUN_BLASTS = {
    "baseline": "基线（正常一遍走不走得通）",
    "rerun": "状态残留 / 首次加载假设",
    "delay": "时序竞争 / 没等就点",
    "viewport": "折叠 / 遮挡 / 坐标假设",
    "country": "地区内容差异",
}

#: **一次提交** = 产物把整个漏斗走一遍 = 一次真实的 lead 提交（R-84）。
#: `MAX_SUBMISSIONS` 是**硬顶**：一轮验收最多提交几次，到顶就停、如实报，不许悄悄超。
#: 那个「3」是用户裁定的上界（原来固定跑三遍 = 每轮 3 次；现在只在需要时才用到）。
MAX_SUBMISSIONS = 3

#: R-84 加的那一种状态：**这一轮用不着跑它**（前一遍就过了 / 已经用满提交次数）。
#: ⚠️ 与 `skipped` 是**两件事**，别混：
#:   - `skipped`      = 该跑而没跑成（没旋钮 / 回调炸了）→ **默认拦** `passed`（R-5）；
#:   - `not_needed`   = 按 R-84 的判据**本来就不该跑** → 不拦，但**报告里照样列出来**，
#:                      并写清它打的那一类这次没验到。
STATUS_NOT_NEEDED = "not_needed"

#: 默认**允许**跳过的那几遍。只有第 5 遍：规格 §10 与计划都把「换代理国家」标成可选
#: （要重拉 gost 链，单遍成本高）。第 4 遍**不在**里面 —— 跳了就得让报告说没过，
#: 除非调用方**明确**把它加进来（R-5）。
DEFAULT_ALLOWED_SKIPS = ("country",)

#: 自测拼给产物的参数 —— 每一个都必须是产物 CLI 上真有的（`agent/template.py` §5.1c）。
#: `test_selftest_and_the_template_agree_on_the_artifact_cli` 拿它当闸门。
ARTIFACT_FLAGS = ("--ws-url", "--form-file", "--correlation-id", "--task-id",
                  "--log-level", "--trace", "--delay", "--no-report")

#: 自测**永远**给产物带上这个 —— 自测不是生产任务，不该在生产那边留下记录。
#: 断言这件事的测试走的是**网线**（子进程里装网络守卫，任何出网动作都记一笔），
#: 不是「argv 里有没有那个字符串」：手段会变，网线不会。
NO_REPORT_FLAG = "--no-report"

#: 跑一遍产物的上限（秒）。与 `scripts/ad-task.py:1532` 的 `communicate(timeout=600)`
#: 同一个数：自测不该比生产更宽容，也不该更苛刻。
DEFAULT_TIMEOUT = 600

#: 第 3 遍的延迟（秒/步）。基线的拟人停顿是 0.4–1.6s 随机，这里固定 2s ——
#: 规格 §10 的「每步 +2s」在这个旋钮上就是这个意思（R-4：定值覆盖 DELAY_RANGE）。
DEFAULT_DELAY = 2.0

#: 第 4 遍换到的窗口大小（规格 §10 的例子：1280×800 → 1024×768）
DEFAULT_VIEWPORT = (1024, 768)

CAVEAT = ("提醒：工具侧自测通过 ≠ 生产一定过 —— 自测跑的浏览器与生产 worker 的"
          "代理出口、指纹、时序都不是一套（规格 §10）；扰动测试缩小这个差，但不消除它。")


@dataclass(frozen=True)
class Run:
    """一遍扰动跑完之后的样子。

    `ok` 是三态：`True` 过了 / `False` 挂了 / `None` **没跑**。
    为什么不用两态：`ok=True` 加一句「其实没跑」的备注，正是「跳过」冒充「过了」
    的写法 —— 读报告的人（和 Task 7 的图）看的是 `ok`。
    """

    name: str                    # 稳定键：baseline / rerun / delay / viewport / country
    label: str                   # 人话：这一遍在打什么
    status: str                  # "passed" | "failed" | "skipped" | "not_needed"（R-84）
    ok: Optional[bool]
    failed_step: Optional[int]   # 卡在第几步（trace 里第一条 ok=false 的 step）
    trace_path: Optional[str]    # 这一遍自己的 trace（没跑就没有）
    note: str                    # 人话：为什么是这个结果

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "ok": self.ok,
            "failed_step": self.failed_step,
            "trace_path": self.trace_path,
            "note": self.note,
        }


@dataclass(frozen=True)
class Report:
    """自测的结论。`passed` 是**判据**，不是「跑了几遍」。"""

    runs: tuple
    passed: bool
    allowed_skips: tuple
    cdp_bin: Optional[str]
    site: str
    py_path: str
    #: **播报那条旁路**没送成的原因（第一条，人话）。空 = 每一遍都送到了。
    #: Task 5：`run(on_run=…)` 是 Console 的实时视图 —— 旁路坏掉**不许带塌自测**
    #: （同 Task 2 那条铁律），但也**不许静默**：坏掉这件事得有个落点，
    #: 而自测这一层**够不着时间线**（那是服务的事），所以落在这里。
    #:
    #: ⚠️ **落点是这两个**（修复轮 1 的 Minor-2 改准了）：
    #:   ① `summary()` —— 那段人话（服务把它放进 `result.selftest.say`，
    #:      所以读 `/job/{id}` 的人看得见）；
    #:   ② `as_dict()` —— 谁调它谁拿得到（今天主要是测试）。
    #: **不是 PROVENANCE**：`PROVENANCE["selftest"]` 是 `graph._selftest_block`
    #: **手工挑键**拼的（runs / passed / submissions / at / verdict），它不调 `as_dict()`。
    #: 真正让时间线上也能看见的那一步在服务侧：`Service._note_narration_broken`
    #: （读 state 里的 `report`，记一条 `narration_broken`）。
    narrate_broken: tuple = ()

    # ── 谁拦住了 `passed` ────────────────────────────────────────────
    @property
    def failed_runs(self) -> tuple:
        return tuple(r for r in self.runs if r.status == "failed")

    @property
    def skipped_runs(self) -> tuple:
        return tuple(r for r in self.runs if r.status == "skipped")

    @property
    def not_needed_runs(self) -> tuple:
        """R-84：**这一轮用不着跑**的那几遍（过了就不跑 / 到顶了）。"""
        return tuple(r for r in self.runs if r.status == STATUS_NOT_NEEDED)

    @property
    def submissions(self) -> int:
        """这一轮**真提交了几次**（R-84）—— 一次提交 = 产物把整个漏斗走一遍。

        ⚠️ 判据是「**产物真起来了没有**」，不是「这一遍的结论是什么」：
        `trace_path` 是 `_execute` 记的，非空 ⟺ 这一遍真的起过产物。
        别只看 `status` —— `rerun` 那一步可能**记挂但没跑**（`cdp navi` 没成，
        产物一次都没起来），那**不算一次提交**：算进去这个数就虚高，
        而虚高正是这次要治的病（「上百次提交」就是没人看得见才发生的）。
        """
        return sum(1 for r in self.runs
                   if r.status in ("passed", "failed") and r.trace_path)

    @property
    def blocking(self) -> tuple:
        """让 `passed` 变成 False 的那几遍：挂掉的 + 没跑又没人允许不跑的。

        ⚠️ `not_needed` **不在里面**（R-84：那是「这一轮用不着跑」，不是「少给了一个输入」）——
        它们照旧出现在 `summary()` 里，只是不拦判据。
        """
        return tuple(r for r in self.runs
                     if r.status == "failed"
                     or (r.status == "skipped" and r.name not in self.allowed_skips))

    def as_dict(self) -> dict:
        """进 PROVENANCE / 落盘用（§5.3 的 `selftest` 键就是它的形状）。"""
        return {
            "site": self.site,
            "py_path": self.py_path,
            "passed": self.passed,
            # R-84：**这一轮提交了几次** —— 产物里也留着，光看报告的人也能看见
            "submissions": self.submissions,
            "allowed_skips": list(self.allowed_skips),
            "cdp_bin": self.cdp_bin,
            "runs": [r.as_dict() for r in self.runs],
            # Task 5：播报那条旁路坏掉时**不是空话**（见字段那头）。给出去的是 list
            # （`as_dict` 要能进 JSON —— 与 `allowed_skips` 同一个写法）。
            "narrate_broken": list(self.narrate_broken),
        }

    def summary(self) -> str:
        """给非技术人员看的一段话（D16）：过没过、**提交了几次**、哪遍挂、卡在第几步、哪遍没验到。"""
        lines = []
        if self.passed:
            lines.append("扰动自测过了：这一轮往站方提交了 %d 次。" % self.submissions)
            # R-84：判过也可能是**挂了一遍、补跑过的** —— 那件事必须说出来
            # （不然「过了」这两个字会把那一次失败吞掉）。
            for run in self.failed_runs:
                lines.append("· %s —— 挂过一遍，补跑过了：按判据读作**这一趟环境抖了**，"
                             "不是产物的问题（%s）" % (run.label, run.note))
        else:
            lines.append("扰动自测没过，先别交付。这一轮提交了 %d 次。" % self.submissions)
        for run in self.blocking:
            if run.status == "failed":
                lines.append("· %s —— %s" % (run.label, run.note))
            else:
                lines.append("· %s —— 这一遍没跑，而没人明确允许不跑：%s"
                             % (run.label, run.note))
        for run in self.skipped_runs:
            if run not in self.blocking:
                lines.append("· %s —— %s" % (run.label, run.note))
        for run in self.not_needed_runs:
            lines.append("· %s —— %s" % (run.label, run.note))
        if self.narrate_broken:
            # Task 5：**旁路坏掉要说**（不许静默）—— 上面那几行讲的是每一遍的结果，
            # 这一行讲的是「那些结果有没有送到正在看的人手里」。
            lines.append("⚠️ 有几遍的播报没送到时间线上（%s）—— 自测**照常跑完了**"
                         "（旁路坏掉不许带塌自测），但看的人那边会少掉这几遍。"
                         % self.narrate_broken[0])
        lines.append(CAVEAT)
        return "\n".join(lines)


# ── 细节工具 ────────────────────────────────────────────────────────

def _as_text(chunk) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", "replace")
    return chunk


def _tail(text: str, limit: int = 200) -> str:
    rows = [ln.strip() for ln in _as_text(text).splitlines() if ln.strip()]
    if not rows:
        return ""
    last = rows[-1]
    return last if len(last) <= limit else last[:limit] + "…"


def _host_port(ws_url: str) -> tuple:
    """从 ws_url 拆出 host/port。

    规则**照抄** `forms/common.py` 的 `CDPHelper._parse_ws_url`：cdp CLI 收到的那一对
    必须与产物自己发出去的一致，否则「自测点的那个窗口」与「产物点的那个窗口」可以是两个。
    """
    if not ws_url:
        return "127.0.0.1", "9222"
    url = ws_url.replace("ws://", "").replace("wss://", "")
    host_port = url.split("/")[0]
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        return host, port
    return host_port, "9222"


def _read_trace(path) -> tuple:
    """trace 是 JSON Lines（`agent/template.py` 的 `_trace` 一行一步）。

    返回 (行们, 读不动的行数)。读不动的行**不猜也不吞**：数出来，让它出现在结论里。
    """
    lines, bad = [], 0
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return lines, bad
    for row in text.splitlines():
        if not row.strip():
            continue
        try:
            loaded = json.loads(row)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(loaded, dict):
            lines.append(loaded)
        else:
            bad += 1
    return lines, bad


def _artifact_cmd(py, ws_url, form_file, correlation_id, log_level, trace_path,
                  task_id=None, delay=None) -> list:
    """起产物的命令行。

    形态**照抄** `scripts/ad-task.py:1525`（生产就这么调的）：
    `python3 <script> --ws-url … --form-file … --correlation-id … --log-level INFO`，
    成功判据是退出码 0。`--trace` / `--delay` / `--task-id` 是加法式的。

    `--task-id` 要显式给：产物成功时会往上报告接口写一条 URL 记录，那是它自带的行为
    （关掉就等于改产物），所以**报的是哪个 id**必须由自测说了算 —— 默认 `selftest-<site>…`，
    免得自测在生产那边留下一个看着像真任务的 id。
    """
    cmd = ["python3", str(py),
           "--ws-url", ws_url,
           "--form-file", str(form_file),
           "--correlation-id", correlation_id,
           "--log-level", log_level,
           "--trace", str(trace_path)]
    if task_id:
        cmd += ["--task-id", task_id]
    cmd.append(NO_REPORT_FLAG)      # 自测**永远**不上报（见那个常量上面的注释）
    if delay is not None:
        cmd += ["--delay", ("%g" % float(delay))]
    return cmd


def _verdict(rc, timed_out: bool, lines: list, bad_lines: int,
             err: str, out: str, timeout: float) -> tuple:
    """一遍的证据 → (ok, failed_step, note)。**这一处就是「诚实」本身**，别把它做软。"""
    first_bad = next((ln for ln in lines if ln.get("ok") is False), None)
    failed_step = first_bad.get("step") if first_bad else None
    step_said = (first_bad or {}).get("note") or "trace 里那一步没写为什么"
    # `lines` 非空是承重的一环（R-27）：一行 trace 都没有时，「trace 里没有没做成的步」
    # 这句话是**空口白话** —— 没有证据的东西不许被读成「过了」。
    ok = (rc == 0) and bool(lines) and first_bad is None and not timed_out

    if timed_out:
        note = "这一遍没跑完就超时了（>%s 秒）—— 卡在第 %s 步" % (
            ("%g" % timeout), failed_step) if failed_step else \
            "这一遍没跑完就超时了（>%s 秒）" % ("%g" % timeout)
    elif rc == 0 and first_bad is not None:
        note = ("退出码说成功，但 trace 里第 %s 步没做成（%s）—— 按没做成算"
                % (failed_step, step_said))
    elif rc == 0 and not lines:
        note = ("退出码说成功，但这一遍**一行 trace 都没有** —— 没有证据就不算过。"
                "两种可能：trace 没写进去（产物会警告一句再接着跑），或者一步都没走到"
                "（states 的 when 一个都没匹配上）。两种都不能当「验过了」。")
    elif rc == 0:
        note = "跑通了（退出码 0，trace 里没有没做成的步）"
    elif first_bad is not None:
        note = "卡在第 %s 步：%s" % (failed_step, step_said)
    elif lines:
        note = "每一步都做成了，但页面上一直没出现成功文案（退出码 %s）—— 不是卡在哪一步，是没走到成功" % rc
    else:
        why = _tail(err) or _tail(out)
        note = "它一步都没走成：进程退了（退出码 %s）%s" % (
            rc if rc is not None else "起不来", "，它最后说：%s" % why if why else "")
    if bad_lines:
        note += "（trace 里还有 %d 行读不动）" % bad_lines
    return ok, failed_step, note


def _skipped(name: str, note: str) -> Run:
    """没跑的那一遍：`ok=None`、没有 trace、没有步号 —— 处处都不许长得像「过了」。"""
    return Run(name=name, label=RUN_LABELS[name], status="skipped", ok=None,
               failed_step=None, trace_path=None, note=note)


def _not_needed(name: str, why: str) -> Run:
    """R-84：**这一轮用不着跑**的那一遍（前一遍就过了 / 到顶了）。

    与 `_skipped` 一样 `ok=None`、没有 trace、没有步号；**不一样的是判据**：
    跳过要拦 `passed`（R-5），这个不拦。⚠️ 但**不许因此静悄悄**：note 里要写清
    它打的那一类失败这次**没验到**，`summary()` 里也照样列出来。
    """
    return Run(name=name, label=RUN_LABELS[name], status=STATUS_NOT_NEEDED, ok=None,
               failed_step=None, trace_path=None,
               note="这一遍没跑：%s。它打的那一类失败（%s）这一次**没验到**。"
                    % (why, RUN_BLASTS[name]))


#: 默认根：`runtime/selftest/`（`runtime/` 不进 git）。服务侧可以**构造时**换掉它
#: （`SITEFORGE_SELFTEST_DIR`，与 `SITEFORGE_SHOTS_DIR` / `SITEFORGE_EXPLORE_DIR` 同一套）。
DEFAULT_ROOT = _REPO / "runtime" / "selftest"


def default_run_dir(site: str, *, root=None) -> pathlib.Path:
    """这一趟的 trace 放哪：`<root>/<site>-<时刻>/`（不给 root 就是仓库里那个）。

    ⚠️ **服务那条路要在构造时把 `root` 定死**（修复轮 3）：这个名字里带**时刻**，
    只能在调用的那一刻算 —— 于是「调用时再解析」这件事一旦留在服务路径上，
    测试/teardown 就管不住它（实测：拿服务拼好的 `Deps.selftest` 不给 `run_dir` 调一次，
    仓库里立刻多一个 `runtime/selftest/<site>-<时刻>/`）。
    库的默认值**留着是对的**（直接调用方就想要这个），但服务不许走到它。
    """
    return pathlib.Path(root if root is not None else DEFAULT_ROOT) / (
        "%s-%s" % (site, time.strftime("%Y%m%d-%H%M%S")))


def _default_run_dir(site: str) -> pathlib.Path:
    """默认把每一遍的 trace 放在 `runtime/selftest/<site>-<时刻>/`（`runtime/` 不进 git）。"""
    return default_run_dir(site)


def _default_cdp_bin() -> Optional[str]:
    """没点名时用哪个 cdp：环境变量 → 本仓库构建的那个 → 容器里装的那个。

    ⚠️ 找不到就返回 None（不动子进程环境，谁配的算谁的）。**不保证**挑中的那个带
    observe / diff / screenshot —— 老 cdp 缺这三条时产物会自己说（trace 里
    `progress: null` 带 `progress_why`、`shots_why`），运维设 `SITEFORGE_CDP_BIN`
    指到新版即可（R-15 第 5 条）。
    """
    for cand in (os.environ.get("SITEFORGE_CDP_BIN"),
                 str(_REPO / "tools" / "cdp" / "cdp"),
                 "/usr/local/bin/cdp"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _execute(name: str, py, ws_url, form_file, correlation_id, log_level, env,
             run_dir: pathlib.Path, site: str, timeout: float, task_id=None,
             delay=None) -> Run:
    """跑一遍产物，按 trace + 退出码下结论。"""
    # ⚠️ **每一遍一个子目录**：三遍共用一个目录时截图会撞名（`11-before.png` 只有一份，
    # 分不清是哪一遍的 —— 2026-09-17 真站排查时正是被这个绊了一下）。
    # trace 落进子目录，截图跟着它走（`Filler._shot` 用的是 trace 那一层）。
    run_dir = pathlib.Path(run_dir) / name
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = run_dir / ("%s.%s.trace.jsonl" % (site, name))
    cmd = _artifact_cmd(py, ws_url, form_file, correlation_id, log_level, trace,
                        task_id, delay)
    rc, timed_out, out, err = None, False, "", ""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        rc, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out, err = _as_text(exc.stdout), _as_text(exc.stderr)
    except OSError as exc:
        err = "起不来：%s" % exc

    lines, bad_lines = _read_trace(trace)
    ok, failed_step, note = _verdict(rc, timed_out, lines, bad_lines, err, out, timeout)
    return Run(
        name=name,
        label=RUN_LABELS[name],
        status="passed" if ok else "failed",
        ok=ok,
        failed_step=failed_step,
        trace_path=str(trace),
        note=note,
    )


def _judge(runs: Sequence[Run], allowed_skips: Sequence[str]) -> bool:
    """`passed` 的判据。这是本模块最承重的一个函数 —— 改动前先看 `tests/test_selftest.py`。

    三条（R-84 起）：

    1. **至少一遍真的过了**。以前「任一遍挂就是没过」，现在补跑过了可以判过 ——
       但**一遍都没过就是没过**：那一次失败没有反面证据，不许被读成「差不多」。
    2. **最多挂一遍**。一遍挂、补跑过 = 「这一趟环境抖了」（这正是 `rerun` 存在的理由，
       brief 明写的行为）；**连着两遍挂 = 产物不行**，不是环境抖。
       ⚠️ 这一条是 R-84 之后**判据的边界**：它决定了「挂几次算不行」。
    3. **跳过那几条照旧**（R-5）：没被点名允许的跳过仍然拦。

    `not_needed`（R-84：这一轮用不着跑）**不参与判据** —— 它既不是挂，也不是「没验到的输入」。
    「不许把『某遍挂』吞成『部分通过』」这条老规矩仍在：吞它的路只有一条，
    就是第 1、2 条合起来说的「**挂了一遍、但补跑真过了**」。
    """
    failed = [r for r in runs if r.status == "failed"]
    if not any(r.status == "passed" for r in runs):
        return False
    if len(failed) > 1:
        return False
    for run in runs:
        if run.status == "skipped" and run.name not in allowed_skips:
            return False
    return True


def run(py_path, ws_url, form_file, site, *,
        entry_url: Optional[str] = None,
        set_viewport: Optional[Callable] = None,
        viewport: Sequence[int] = DEFAULT_VIEWPORT,
        set_country: Optional[Callable] = None,
        country: Optional[str] = None,
        delay: float = DEFAULT_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
        run_dir=None,
        cdp_bin: Optional[str] = None,
        correlation_id: Optional[str] = None,
        task_id: Optional[str] = None,
        log_level: str = "INFO",
        allow_skips: Sequence[str] = DEFAULT_ALLOWED_SKIPS,
        max_submissions: int = MAX_SUBMISSIONS,
        on_run: Optional[Callable] = None) -> Report:
    """在真浏览器上按 `RUN_NAMES` 的序列跑，返回一份**说得清**的结论。

    ## 跑几遍（R-84，**用户裁定**）：按需，不再固定

    一遍**过了就停**（默认只跑 `baseline`）；没过才往下补跑（`rerun` → `delay` → …），
    用来分辨「产物不行」还是「这一趟环境抖了」。**硬顶 `max_submissions` 次提交**，
    到顶就停、如实报（没跑到的那几遍记 `not_needed`，note 里写清哪一类没验到）。
    ⚠️ 「不过必须补跑」是承重的一半 —— 少了它，验收就变成走过场（见模块 docstring）。

    参数（四个位置参数是 brief 的接口，其余全是可选）：
        py_path / ws_url / form_file / site   产物、窗口、表单数据、站点短名
        entry_url      给了就在第 2 遍前先 `cdp navi` 过去（R-6：字面意义的「刷新后重跑」）
        set_viewport   窗口层回调：`set_viewport(width, height)`（R-5，第 4 遍用）
        set_country    代理层回调：`set_country(country)`（第 5 遍用，不给就跳过）
        run_dir        每一遍的 trace 放哪（默认 `runtime/selftest/<site>-<时刻>/`）
        cdp_bin        产物与 cdp 命令都用哪一个 cdp（默认见 `_default_cdp_bin`）
        delay          第 3 遍的固定每步延迟（秒）
        allow_skips    明确允许不跑的那几遍（默认只有 `country`）
        max_submissions **硬顶**（默认 3，R-84 的裁定）：一轮最多提交几次。
            ⚠️ 调大它 = 把「刷太多」那条裁定改掉 —— 要有人裁，不许顺手调。
            （它是参数、不是写死的常量：那套「没旋钮就跳过」的机制要靠它才验得到 ——
            默认 3 次之下，第 4/5 遍**到不了**。）
        on_run         **每一遍跑完当场**回调一次那个 `Run`（Task 5，Console 的实时视图）——
            **没跑的那几遍也要回调**：`skipped` / `not_needed` 正是「没验到」被吞掉的那条
            路（设计注 §3.2 第 3 行），只在报告里看得见就等于没人看见。
            不给 = 今天那条路，一个字节不变。
            ⚠️ 它是**旁路**：回调抛异常**不许带塌自测**（剩余几遍照跑、报告照出），
            但也不许静默 —— 原因落在 `Report.narrate_broken`（自测这一层够不着时间线）。
    """
    py = pathlib.Path(py_path)
    if not py.is_file():
        raise ValueError("产物不在：%s（扰动自测要跑的就是这一份，先把它落盘再来自测）" % py_path)

    allowed = tuple(allow_skips)
    unknown = [name for name in allowed if name not in RUN_NAMES]
    if unknown:
        raise ValueError("allow_skips 里有不认识的遍：%s（认的是这五个：%s）"
                         % ("、".join(unknown), "、".join(RUN_NAMES)))
    if set_country is not None and not country:
        raise ValueError("给了 set_country 就要说清换到哪个国家（country=...）—— 不然第 5 遍等于没扰")

    run_dir = pathlib.Path(run_dir) if run_dir else _default_run_dir(site)
    run_dir.mkdir(parents=True, exist_ok=True)
    cdp_bin = cdp_bin or os.environ.get("SITEFORGE_CDP_BIN") or _default_cdp_bin()
    correlation_id = correlation_id or "selftest-%s_%s" % (site, time.strftime("%Y%m%d-%H%M%S"))
    if task_id is None:
        task_id = correlation_id.split("_")[0]

    env = os.environ.copy()
    if cdp_bin:
        env["SITEFORGE_CDP_BIN"] = str(cdp_bin)

    def _once(name, **kw):
        return _execute(name, py, ws_url, form_file, correlation_id, log_level, env,
                        run_dir, site, timeout, task_id=task_id, **kw)

    runs: list = []
    submissions = 0
    #: 播报那条旁路**第一条**没送成的原因（人话）。见 `Report.narrate_broken`。
    narrate_broken: list = []

    def _tell(run_obj: Run) -> None:
        """一遍**跑完的当场**：入账 + 播报（Task 5）。**所有出口都必须走它。**

        ⚠️ 为什么不是各处 `runs.append` 之后顺手补一句回调：这一支有**九个**出口
        （硬顶 / 前一遍过了 / 五遍各自的分支），漏掉哪个出口，那一遍在时间线上就凭空
        消失 —— 而「没跑的那几遍」正是最容易被漏的那些（它们看起来不像结果）。
        与 `explore` 的 `emit` 同一条规矩：**旁路坏掉不许带塌主路**（回调抛了，
        后面几遍照跑、报告照出），但**不许静默**（第一条原因记进 `narrate_broken`）。
        """
        runs.append(run_obj)
        if on_run is None:
            return
        try:
            on_run(run_obj)
        except Exception as exc:                   # noqa: BLE001 —— 外部世界，什么都可能抛
            if not narrate_broken:
                narrate_broken.append("%s: %s" % (type(exc).__name__, exc))

    def _spend(name, **kw) -> Run:
        """跑一遍 = 一次**提交**（产物会把整个漏斗走一遍）。**计数只在这里加**。

        ⚠️ 计数加在**真起产物**这一处，不是加在「轮到这个名字」那一处：
        `rerun` 那一步的 `cdp navi` 失败时，产物一次都没起来 —— 那不算一次提交。
        """
        nonlocal submissions
        submissions += 1
        return _once(name, **kw)

    for name in RUN_NAMES:
        # R-84 的两条闸：**到顶就停**、**过了就不再跑**。两条都要如实说为什么。
        if submissions >= max_submissions:
            _tell(_not_needed(name, "这一轮已经用满 %d 次提交（硬顶）" % max_submissions))
            continue
        if any(r.status == "passed" for r in runs):
            _tell(_not_needed(name, "前一遍就过了（R-84：**过了就算过**，不再往下跑）"))
            continue

        if name == "baseline":
            _tell(_spend("baseline"))
            continue

        if name == "rerun":
            # 第 2 遍（R-6）：同一个 ws_url、同一个已经走到的页面，**接着**再跑一遍 ——
            # 这才是「状态残留 / 首次加载假设」真正要打的东西。给了 entry_url 就先导航过去，
            # 覆盖「刷新后重跑」的字面读法；导航本身也是一次动作，所以走 cdp 命令（不手拼 JS）。
            navi_failed = None
            if entry_url:
                if not cdp_bin:
                    navi_failed = "没有可用的 cdp 二进制，刷新这一步做不了"
                else:
                    host, port = _host_port(ws_url)
                    try:
                        done = subprocess.run([str(cdp_bin), "navi", entry_url,
                                               "--host", host, "--port", port],
                                              capture_output=True, text=True, timeout=60, env=env)
                        if done.returncode != 0:
                            navi_failed = "cdp navi 没成（退出码 %d，它说：%s）" % (
                                done.returncode,
                                _tail(done.stderr or done.stdout) or "什么都没说")
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        navi_failed = "cdp navi 没成（%s）" % exc
            if navi_failed:
                _tell(Run(name="rerun", label=RUN_LABELS["rerun"], status="failed",
                          ok=False, failed_step=None, trace_path=None,
                          note="这一遍的刷新没做成，所以它没验到状态残留：%s" % navi_failed))
            else:
                _tell(_spend("rerun"))
            continue

        if name == "delay":
            _tell(_spend("delay", delay=delay))
            continue

        if name == "viewport":
            # 第 4 遍（R-5）：viewport 是窗口层的事，只有调用方能动。没给回调 = 跳过 + 吵。
            if set_viewport is None:
                _tell(_skipped("viewport", (
                    "这一遍没跑：换窗口大小要调用方在窗口层动手（POST /browser/update），"
                    "产物和 cdp 内核都够不着。所以「折叠 / 遮挡 / 坐标假设」这一类失败这次"
                    "**没验到**；要跑就传 set_viewport=回调，要放弃就把它写进 allow_skips"
                    "（默认不算过）。")))
            else:
                try:
                    set_viewport(*viewport)
                except Exception as exc:                       # 回调是外部世界，什么都可能抛
                    _tell(_skipped("viewport", (
                        "这一遍没跑成：换窗口大小的时候出错了（%s）。这一类失败这次**没验到** —— "
                        "不算过。" % exc)))
                else:
                    _tell(_spend("viewport"))
            continue

        # country（第 5 遍）：换代理国家（重拉 gost 链，成本高）。不给回调就跳过 —— 默认允许。
        if set_country is None:
            _tell(_skipped("country", (
                "这一遍没跑：换代理国家要重拉 gost 链（单遍成本高，规格 §10 就把它标成可选）。"
                "所以「地区内容差异」这次**没验到**；要跑就传 set_country=回调 + country=…。")))
        else:
            try:
                set_country(country)
            except Exception as exc:
                _tell(_skipped("country", (
                    "这一遍没跑成：换代理国家的时候出错了（%s）。地区内容差异这次**没验到**。"
                    % exc)))
            else:
                _tell(_spend("country"))

    runs = tuple(runs)
    return Report(runs=runs, passed=_judge(runs, allowed), allowed_skips=allowed,
                  cdp_bin=str(cdp_bin) if cdp_bin else None, site=site, py_path=str(py),
                  narrate_broken=tuple(narrate_broken))
