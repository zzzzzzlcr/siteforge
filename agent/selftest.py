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
import re
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


#: 脚本自己那套读页面的 JS（`READ_PAGE_JS = r"""…"""`）—— 拿来**现读一遍**它看到了什么。
_READER_JS = re.compile(r'READ_PAGE_JS\s*=\s*r?"""(.*?)"""', re.S)

#: 通用的「谁看着像可点的」探针（**与脚本无关**）：短文字的链接/按钮类元素。
#: 为什么要它（2026-09-21 实测，站 `qualify.lastingpowerofattorney.io`）：那一页的
#: **「Get A Free Quote」是个 `<a href="https://qualify.lastingpowerofattorney.io//?…">`**，
#: 而脚本只收 `button, [role=button], label` ⇒ `buttons: 0` ⇒ 它连看都看不见那个按钮。
#: 把「页面上有、它没收到的」摆出来，模型/人才不用猜。
_PROBE_JS = r"""(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none'||cs.opacity==='0')return false;n=n.parentElement;}return true;}
  function onScreen(e){var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&r.top<window.innerHeight&&r.bottom>0;}
  var out={url:location.href,cands:[]};
  var els=document.querySelectorAll('a[href],[onclick],[class*="btn"],[class*="button"],[role="link"]');
  for(var i=0;i<els.length&&out.cands.length<12;i++){var e=els[i];
    if(!vis(e)||!onScreen(e))continue;
    var t=(e.textContent||'').replace(/\s+/g,' ').trim();
    if(!t||t.length>40)continue;
    var r=e.getBoundingClientRect();
    out.cands.push({tag:e.tagName,txt:t,cls:String(e.className||'').slice(0,40),
                    href:(e.getAttribute('href')||'').slice(0,90),
                    box:[Math.round(r.width),Math.round(r.height)]});}
  return JSON.stringify(out);
})()"""


def _cdp_eval(cdp_bin, ws_url, js, env, timeout: float = 60):
    """在那一页上跑一段 JS（`cdp eval`）→ 解出来的对象；跑不了给 `None`（**不抛**）。

    ⚠️ `cdp eval` 吐的是**被 JSON 包了一层的字符串**（脚本那头就是 `json.loads` 两次）——
    这里替调用方拆掉那一层。
    """
    if not cdp_bin:
        return None
    host, port = _host_port(ws_url)
    try:
        done = subprocess.run([str(cdp_bin), "eval", js, "--host", host, "--port", port],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        got = json.loads((done.stdout or "").strip())
        return json.loads(got) if isinstance(got, str) else got
    except (json.JSONDecodeError, TypeError):
        return None


def _cdp_observe(cdp_bin, ws_url, env, timeout: float = 90):
    """`cdp observe` 的那一份 **PageModel**（**工具自己的读数**）→ `dict`；读不到给 `None`。

    ★ 为什么有它（2026-09-21，用户指的方向：「为什么不根据 cdp 工具来完善」）：
    「这一页上哪个元素能点、它的选择器是什么、**有没有东西挡着**、挡着的话点哪个地址才能清掉」
    —— `cdp observe` **本来就全算好了**（`actions[].selector/alternates/stability/occluded_by`、
    `obstructions[].dismiss_selector`、`honeypots`、`fields`、`option_groups`）。而这条路上
    原来**自己手搓 JS** 去找「像可点的元素」（`_PROBE_JS`）—— 于是屏幕上只看得见「短文字的
    链接/按钮」，看不见「**挡着的那一层**」：那一站真跑栽的就是这个（cookie 横幅盖着整页，
    脚本读到了它、没点它，后面每一步的点击都被浮层吃掉）。

    ⚠️ 与 `reader` / `clickable` 那两块**不是一件事**，三块都要：
      · `reader`    = **脚本自己**收到的（它自己的读法）
      · `clickable` = 页面上「像可点的」短文字元素（**手搓的**那一套判据）
      · `page`      = **工具**看到的那份模型（选择器候选 / 稳定性 / 遮挡物 / 陷阱元素）
    三块不一致的地方，正是要指给人看的那一条。

    ⚠️ 读不到 ⇒ `None`（**不许**画成「页面上什么都没有」）。⚠️ 生产那个 cdp **没有 observe**
    （能力边界见 `agent/template.py::_clear_obstructions`）⇒ 这一块只在**我们这边**
    （证据 / 闸上）用，**不塞进产物脚本**。
    """
    if not cdp_bin:
        return None
    host, port = _host_port(ws_url)
    try:
        done = subprocess.run([str(cdp_bin), "observe", "--host", host, "--port", port],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        got = json.loads((done.stdout or "").strip())
        got = json.loads(got) if isinstance(got, str) else got
    except (json.JSONDecodeError, TypeError):
        return None
    return got if isinstance(got, dict) else None


def _blank_page(page) -> bool:
    """这份 PageModel 读到的**是不是一张空页**（标题空 + 正文 0 字 + 0 个可动作元素）。

    ⚠️ 为什么要有这一格（2026-09-21 实测）：证据那一趟跑完的**那一刻**去读，页面可能正在
    跳转/白屏 —— 实测读到的是「title 空、`page_text` 0 字、`actions` 0 个」，而**三个读法
    全是空的**（脚本自己的、手搓判据的、工具的）。那不是「页面上干净」，是**这一读没读到
    那一页**；把它画成「页面上没有东西挡着」正好是最贵的那种假话。
    """
    if not isinstance(page, dict):
        return False
    return (not str(page.get("title") or "").strip()
            and not str(page.get("page_text") or "").strip()
            and not (page.get("actions") or [])
            and not (page.get("fields") or []))


def _dom_view(py, cdp_bin, ws_url, env, entry_url: str = "") -> dict:
    """跑完那一遍之后，**现读一遍那一页**：脚本自己看到了什么 + 页面上还有什么可点的。

    `None` = 读不到（不是「没有」）—— 那两个字在这一层不许混。

    ★ 读到**空页**时（见 `_blank_page`）：**先导航回入口网址再读一遍** —— 我们要的那一页
    是**漏斗的第一屏**（脚本卡住的那一屏），不是跑完之后那一刻的空白状态。
    """
    view = _dom_view_raw(py, cdp_bin, ws_url, env)
    if _blank_page(view.get("page")) and entry_url:
        why = _navigate(cdp_bin, ws_url, entry_url, env)
        view["page_retried"] = ("导航没过：%s" % why) if why else "读到了空页，导航回入口再读了一遍"
        again = _cdp_observe(cdp_bin, ws_url, env)
        if isinstance(again, dict):
            view["page"] = again
    return view


def _dom_view_raw(py, cdp_bin, ws_url, env) -> dict:
    """`_dom_view` 的那一趟读（**不做空页重试**，见上面那一层）。
    """
    view: dict = {}
    try:
        src = pathlib.Path(py).read_text(encoding="utf-8", errors="replace")
    except OSError:
        src = ""
    m = _READER_JS.search(src)
    if m:
        view["reader"] = _cdp_eval(cdp_bin, ws_url, m.group(1), env)
    view["clickable"] = _cdp_eval(cdp_bin, ws_url, _PROBE_JS, env)
    #: ★ **工具自己**的那一份读数（选择器候选 / 稳定性 / **遮挡物 + dismiss_selector** /
    #: 陷阱元素 / 字段）。这是 2026-09-21 用户指的方向：别手搓，用工具。
    view["page"] = _cdp_observe(cdp_bin, ws_url, env)
    return view


#: cdp 往 stderr 刷的**已知噪音**：`could not unmarshal event: json: cannot unmarshal …
#: unknown IPAddressSpace value: Private`（Chrome 136 的枚举，二进制里那份 cdproto 不认识）。
#: 【现场·2026-09-22】它**每跑一次 `navi`/`observe` 就刷十几行**；退出码仍是 0、stdout 完整
#: ⇒ 它**不是**失败，但它会把有用的那几行（`cdp click` 的落点取证 `covered_by` / 脚本自己的
#: 报错）淹掉。所以**只给人看的那几处**按前缀丢掉它。
#: ⚠️ 「原样落盘」那一份（`<run>.log`）**一个字都不动** —— 那是这一趟的原始证据。
STDERR_NOISE_PREFIXES = ("could not unmarshal event:",)

#: 开窗之后**调试端口要过几秒才通**（【现场·2026-09-22】另一台机器实测：第一次 TCP 探测
#: 全超时，等一会儿再探就通了 —— 窗口还在启动）。所以第一次接触（`navi`）**重试几次**：
#: 不重试的话，那一趟会以一句「导航没成」结账，而它其实只是**窗口还没起来**（假结论）。
#: ⚠️ `navi` 只是导航、不会提交任何东西 ⇒ 重试是安全的（不是重复一次真流量）。
NAVI_TRIES = 4
NAVI_GAP_SECONDS = 2.0


def _noise_free(text: str) -> str:
    """滤掉已知噪音行（**只按前缀** ✓ —— 别误伤 `click` 的落点取证那种有用的 stderr）。"""
    lines = [ln for ln in str(text or "").splitlines()
             if not ln.lstrip().startswith(STDERR_NOISE_PREFIXES)]
    return "\n".join(lines).strip()


def _navigate(cdp_bin, ws_url, entry_url, env) -> Optional[str]:
    """把窗口**导航到 `entry_url`** → `None` 成了 / 一句人话为什么没成。

    ⚠️ 【我量的·2026-09-21】**不导航就白跑**：证据那一趟跑到最后，脚本报的最后地址是
    `https://console.bitbrowser.net/?id=…`（**Bit 浏览器自己的控制台页**）——
    也就是说脚本是在**控制台页**上找元素的，什么都没找到、当场报 `max_steps`。
    生产那边是 ad-task 先开好页面才调脚本的，所以这一步在自测这条路里必须自己做
    （第二遍 `rerun` 的「刷新后重跑」本来就是这么做的，这里把它抽出来共用）。
    """
    if not cdp_bin or not entry_url:
        return "（没导航：%s）" % ("没有可用的 cdp 二进制" if not cdp_bin else "没给入口网址")
    host, port = _host_port(ws_url)
    said = ""
    for attempt in range(NAVI_TRIES):
        try:
            done = subprocess.run([str(cdp_bin), "navi", entry_url, "--host", host, "--port", port],
                                  capture_output=True, text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            said = "导航失败：%s" % exc
        else:
            if done.returncode == 0:
                return None
            said = "导航没成（退出码 %d，它说：%s）" % (
                done.returncode,
                _tail(_noise_free(done.stderr) or done.stdout, 3) or "什么都没说")
        if attempt < NAVI_TRIES - 1:
            time.sleep(NAVI_GAP_SECONDS)      # 端口还没通 ⇒ 等一下再试（见上面那条实测）
    return said


def run_once(py_path, ws_url, form_file, site, *, legacy: bool = False, run_dir=None,
             cdp_bin: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
             task_id: Optional[str] = None, log_level: str = "INFO",
             entry_url: Optional[str] = None) -> dict:
    """**跑一遍**产物（不是扰动序列）→ 证据：`{"rc", "timed_out", "trace", "tail", …}`。

    修站那条路要的第一样东西**不是**「这一版值不值得交出去」（那是 `run()` 的活，
    五遍扰动 + 判据），而是「**这份旧脚本现在停在哪儿、那一页什么样**」——
    模型手上没有这个，就只能瞎改（【我量的·2026-09-21】真跑两趟：一趟只把一个空格改掉了、
    一趟一处都没改）。

    ⚠️ 这是**真页面 + 一次真提交**（运营口径「刷太多不太好」）—— 所以调用方必须把它排在
    **一道闸之后**（见 `graph._explore` 的老写法那一支），不是顺手就跑。
    ⚠️ `legacy=True` 时走那一套：argv 不给 `--trace`/`--no-report`，证据由运行时写
    （与 `_execute` 同一条规矩，见它那段注释）。
    """
    py = pathlib.Path(py_path)
    run_dir = pathlib.Path(run_dir) if run_dir else _default_run_dir(site)
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = run_dir / ("%s.evidence.trace.jsonl" % site)
    cid = "evidence-%s_%s" % (site, time.strftime("%Y%m%d-%H%M%S"))
    task_id = task_id or ("selftest-%s" % site)

    env = os.environ.copy()
    cdp_bin = cdp_bin or os.environ.get("SITEFORGE_CDP_BIN") or _default_cdp_bin()
    if cdp_bin:
        env["SITEFORGE_CDP_BIN"] = str(cdp_bin)
    cmd = _artifact_cmd(py, ws_url, form_file, cid, log_level,
                        (None if legacy else trace), task_id, None)
    if legacy:
        env = dict(env, SITEFORGE_TRACE=str(trace), SITEFORGE_NO_REPORT="1")
    #: ⚠️ **先导航到入口网址**（见 `_navigate` 那段：不导航就是在浏览器的控制台页上跑）
    navi_why = _navigate(cdp_bin, ws_url, entry_url, env)

    rc, timed_out, out, err = None, False, "", ""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        rc, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out, err = _as_text(exc.stdout), _as_text(exc.stderr)
    except OSError as exc:
        err = "起不来：%s" % exc

    #: ⚠️ **原样落盘**（2026-09-21）：证据这一趟的 stdout/stderr 是**唯一**能看到
    #: 「它在页面上说了什么」的东西（生产日志里那几行 `step N: nothing actionable …`
    #: 就是它）—— 只把尾巴塞进一个 dict，事后**查不动**（想复核「它到底看到了什么」
    #: 就只能再跑一趟，而每一趟都是一次真提交）。
    log_path = run_dir / ("%s.evidence.log" % site)
    try:
        log_path.write_text((out or "") + (err or ""), encoding="utf-8", errors="replace")
    except OSError:
        pass

    lines, bad_lines = _read_trace(trace)
    return {"rc": rc, "timed_out": timed_out, "trace": lines, "bad_lines": bad_lines,
            "tail": _tail((out or "") + (err or ""), 60), "cmd": cmd,
            "trace_path": str(trace), "log_path": str(log_path),
            "legacy": bool(legacy), "timeout": timeout,
            "navi": navi_why, "entry_url": entry_url or "",
            #: ★ 现场读到了什么（跑完**再读一遍那一页**）：模型手上没有这一块时只能猜
            #: 「它看到的和它没收到的」—— 实测：那一页的 CTA 是个 `<a href>`，脚本的
            #: 选择器里根本没有 `a`，于是 `buttons: 0`，一直「nothing actionable」。
            "dom": _dom_view(py, cdp_bin, ws_url, env, entry_url or "")}


def evidence_say(ev: dict) -> str:
    """那次证据跑 → **一段人话**（同时是给模型看的那份证据）。**纯函数**。

    ⚠️ 只摆**量到的东西**：退出码、它自己上报过的那几步、最后停在哪、日志尾巴。
    「为什么没走通」这一层**不许**替它下结论 —— 那是模型/人看着这些原料要说的话
    （D11：给感知不给判断）。
    """
    ev = ev if isinstance(ev, dict) else {}
    rc, trace = ev.get("rc"), list(ev.get("trace") or [])
    if ev.get("timed_out"):
        head = "跑了一遍旧脚本（真页面，一次真实提交）：**没跑完就超时了**（>%s 秒）。" % ev.get("timeout")
    elif rc is None:
        head = "跑了一遍旧脚本（真页面，一次真实提交）：**它没起来**。"
    else:
        head = ("跑了一遍旧脚本（真页面，一次真实提交）：进程退出码 **%s**"
                "（这份脚本的约定是 0 = 它自己说走通了）。" % rc)
    parts = [head]
    if ev.get("navi"):
        parts.append("⚠️ %s —— 也就是说这一趟**可能是在别的页面上找元素**，"
                     "它自己打的那些行要按这个前提读。" % ev["navi"])
    if trace:
        steps = "、".join(str(ln.get("step") or "?") for ln in trace[-12:])
        parts.append("它自己上报过的步（倒着数，共 %d 步）：%s" % (len(trace), steps))
        last_url = next((ln.get("url") for ln in reversed(trace) if ln.get("url")), "")
        if last_url:
            parts.append("最后停在：%s" % last_url)
    else:
        parts.append("它**一步都没上报** —— 连第一个动作都没走成（或这一趟是截图之外的写法）。")
    tail = str(ev.get("tail") or "").strip()
    if tail:
        parts.append("它自己打出来的最后几行：\n%s" % tail[-1200:])
    dom = ev.get("dom") if isinstance(ev.get("dom"), dict) else {}
    reader = dom.get("reader") if isinstance(dom.get("reader"), dict) else None
    if reader:
        bs = reader.get("buttons") or []
        parts.append("跑完之后**现读那一页**：脚本自己收到的是 —— buttons: %d 个%s、fields: %d 个、"
                     "progress: %r。\n它收到的那些可点元素：%s"
                     % (len(bs), "（" + "、".join(str(b.get("txt"))[:24] for b in bs[:6]) + "）" if bs else "",
                        len(reader.get("fields") or []), reader.get("progress") or "",
                        "、".join(str(b.get("txt"))[:24] for b in bs[:6]) or "（一个都没有）"))
    cands = (dom.get("clickable") or {}).get("cands") if isinstance(dom.get("clickable"), dict) else None
    if cands:
        known = {(b.get("txt") or "") for b in (reader.get("buttons") or [])} if reader else set()
        missed = [c for c in cands if (c.get("txt") or "") not in known]
        if missed:
            lines = ["- `<%s class=%r>%s</%s>`%s" % (c.get("tag"), c.get("cls"), c.get("txt"),
                                                     c.get("tag"),
                                                     (" href=%s" % c.get("href")) if c.get("href") else "")
                     for c in missed[:8]]
            parts.append("⚠️ **页面上有、它却没收到**（短文字的链接/按钮类；它点不到的东西多半在这里）：\n"
                         + "\n".join(lines))
    #: ★ **工具自己**看那一页（`cdp observe`）—— 2026-09-21 用户指的方向：别手搓，用工具。
    #: 这一块是「**选择器能直接用**」的那一份，尤其是「**挡着的东西该点哪个地址**」。
    page = dom.get("page") if isinstance(dom.get("page"), dict) else None
    if _blank_page(page):
        #: ⚠️ 读到的是空页 ⇒ **不许**说成「页面上没有东西挡着」（这一条是拿真跑换来的）：
        #: 那是「这一读没读到那一页」，不是「页面上干净」。
        parts.append("工具看那一页（`cdp observe`）：**读到的是一张空页**"
                     "（标题空、正文 0 字、可动作元素 0 个）—— 这**不是**「页面上没有东西挡着」，"
                     "是**这一读没读到那一页**（多半是跑完那一刻页面正在跳转/白屏）"
                     "%s。" % ("；%s" % dom.get("page_retried") if dom.get("page_retried") else ""))
        page = None
    if page:
        obs = [o for o in (page.get("obstructions") or []) if isinstance(o, dict)]
        parts.append("**工具自己看那一页**（`cdp observe`）：可动作元素 %d 个、表单字段 %d 个、"
                     "陷阱元素 %d 个、遮挡物 %d 个、诊断 %d 条。"
                     % (len(page.get("actions") or []), len(page.get("fields") or []),
                        len(page.get("honeypots") or []), len(obs),
                        len(page.get("diagnostics") or [])))
        if obs:
            lines = []
            for o in obs[:4]:
                sel, dis = str(o.get("selector") or ""), str(o.get("dismiss_selector") or "")
                lines.append("- `%s`（%s）：挡着的是 %r%s"
                             % (dis or "（没给点掉它的地址）", o.get("kind") or "?",
                                str(o.get("text") or "")[:50],
                                "" if o.get("dismiss_selector_unique") is not False
                                else " ⚠️ 那条地址**没验证过唯一**"))
            parts.append("⚠️⚠️ **页面上有东西挡着**（`obstructions`）—— 生产**每一单都是新窗口、"
                         "每单都会遇到它**，而脚本开跑前/每次导航之后都得先把它点掉；"
                         "挡着的时候点在别处的点击会被它吃掉（页面上看着「做了、没动」）：\n"
                         + "\n".join(lines))
        acts = [a for a in (page.get("actions") or []) if isinstance(a, dict)]
        if acts:
            said = []
            for a in acts[:8]:
                said.append("- `%s`「%s」%s%s%s"
                            % (str(a.get("selector") or "")[:90],
                               str(a.get("text") or a.get("aria_label") or "")[:28],
                               "（稳定：%s）" % a.get("stability") if a.get("stability") else "",
                               " ⚠️禁用" if a.get("disabled") else "",
                               " ⚠️被 %s 压住" % str(a.get("occluded_by"))[:40]
                               if a.get("occluded_by") else ""))
            parts.append("工具给的可动作元素（**这些选择器是它算出来的、能直接拿去点**）：\n"
                         + "\n".join(said))
    if ev.get("bad_lines"):
        parts.append("（它写的证据里有 %d 行读不动）" % ev["bad_lines"])
    return "\n".join(parts)


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
           "--log-level", log_level]
    #: `--trace` / `--no-report` / `--delay` 是**产物自己要有的**（`ARTIFACT_FLAGS`）。
    #: ⚠️ **老写法那一族**（线上 66 份 py）的 `main()` 里没有它们 —— 硬传就是 argparse
    #: 报错，每一遍都红（那不是「产物不行」，是「自测用错了调法」）。所以
    #: `trace_path is None` 时一个都不传；那一条路的证据与「不上报」由**环境**给，
    #: 见 `_execute` 里 `SITEFORGE_TRACE` / `SITEFORGE_NO_REPORT` 那两个旋钮。
    if trace_path is not None:
        cmd += ["--trace", str(trace_path)]
    if task_id:
        cmd += ["--task-id", task_id]
    if trace_path is not None:
        cmd.append(NO_REPORT_FLAG)  # 自测**永远**不上报（见那个常量上面的注释）
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
        why = _tail(_noise_free(err)) or _tail(out)
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
             delay=None, legacy_why: str = "") -> Run:
    """跑一遍产物，按 trace + 退出码下结论。

    `legacy_why` 非空 = 这一版是**老写法**（不认 `--trace` / `--no-report` / `--delay`，
    见 `_artifact_flags`）：argv 那三个开关**一个都不传**，改由环境把那两条线接上
    （`SITEFORGE_TRACE` / `SITEFORGE_NO_REPORT`，siteforge 那份 `forms/common.py` 认）。
    ⚠️ 这样证据**弱一档**（定位不到卡在第几步）—— 那句话必须跟着结论一起摆出来，
    所以 `legacy_why` 会追加到这一遍的 `note` 上。
    """
    # ⚠️ **每一遍一个子目录**：三遍共用一个目录时截图会撞名（`11-before.png` 只有一份，
    # 分不清是哪一遍的 —— 2026-09-17 真站排查时正是被这个绊了一下）。
    # trace 落进子目录，截图跟着它走（`Filler._shot` 用的是 trace 那一层）。
    run_dir = pathlib.Path(run_dir) / name
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = run_dir / ("%s.%s.trace.jsonl" % (site, name))
    cmd = _artifact_cmd(py, ws_url, form_file, correlation_id, log_level,
                        (None if legacy_why else trace), task_id,
                        (None if legacy_why else delay))
    if legacy_why:
        #: 老写法：证据线与「不上报」都从**运行时**进 —— 产物在 `<root>/forms/sites/` 时，
        #: 它 `from common import …` 解析到的就是 siteforge 那一份 `forms/common.py`。
        env = dict(env, SITEFORGE_TRACE=str(trace), SITEFORGE_NO_REPORT="1")
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
    if legacy_why:
        note = (note + " " + legacy_why) if note else legacy_why
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
        legacy: bool = False,
        start_url: Optional[str] = None,
        allow_navigation_skip: bool = True,
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
        start_url      **先导航到这个地址再跑第一遍**（2026-09-21）。⚠️ 不给的话跑的就是
                       窗口当时停的那个页 —— 而 `fresh_open` 开出来的窗口停在 **Bit 自己的
                       控制台页**上（实测），于是「自测没过」会变成一句**对跑法**的判定，
                       而不是对产物的判定。给了但导航不了 ⇒ **不跑**（在错的页面上跑出来的
                       结论比没有结论更坏：它长得像结论）。
        allow_navigation_skip  没给 `start_url` 时**照跑**（老调用方的行为），但那一遍的结论里
                       会写清「这一趟没导航」。给 `False` = 没导航就不许跑（严一格，供新调用方用）。
        legacy         **这一版是不是老写法**（B 线 ③ 乙，默认 `False`）。`True` ⇒
                       argv **不给** `--trace` / `--no-report` / `--delay`（老写法那一族
                       ——线上 66 份 py 全是——的 `main()` 里没有这三个开关，硬传就是
                       argparse 报错），改由环境把证据线接上（`SITEFORGE_TRACE` /
                       `SITEFORGE_NO_REPORT`，siteforge 那份 `forms/common.py` 认）；
                       放慢那一遍记 `not_needed`（没那个开关）。
                       ⚠️ 判据**一个字不改**（仍然要退出码 0 + 有证据行）—— 弱的只是
                       「定位不到卡在第几步」，那句话跟着结论上屏。
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
    #: ★ 这一版是**老写法**吗（B 线 ③ 乙）：**上面量过**（`fix.shape_of` 走 ast 判的），
    #: 这里只是把它带下来 —— **不再另探一次**。为什么不做 argv 探测（跑 `--help` 数开关）：
    #: ① 形状在 intake 已经量过了，第二次量是重复；② 每次自测多起一个子进程；
    #: ③ 实测：带探测那一版把 19 条既有断言打红了（它们的假产物不认 `--help`）——
    #: 那是拿一个**新**的不确定性去换一个**已经量过**的事实。
    #: ⚠️ 不认 `--trace` 的那一族（线上 66 份 py 全是）：argv 不给那三个开关，证据与
    #: 「不上报」由环境接上；`legacy_why` 那句话会跟着结论上屏 —— **证据弱一档那件事
    #: 必须看得见**（定位不到卡在第几步）。
    legacy_why = ""
    if legacy:
        legacy_why = ("⚠️ 这一版是**老写法**（不认 `--trace` / `--no-report` / `--delay`）—— "
                      "所以按老写法跑：证据是**它自己上报过的那些步**（由运行时写）+ 退出码，"
                      "**定位不到卡在第几步**。")

    #: ★ **跑之前先导航到那个站**（2026-09-21 真跑撞出来的）：`fresh_open` 开出来的窗口停在
    #: **Bit 自己的控制台页**上，而生产那边是 ad-task 先开好页面才调脚本的 —— 我们这条工具路
    #: 没人做这一步，于是脚本是在 `console.bitbrowser.net` 上找元素的：它当然什么都没找到。
    #: 【我量的·2026-09-21】那一趟自测的 trace 两遍都写着
    #: `url=https://console.bitbrowser.net/?id=812b7d60…` —— 也就是说那份「自测没过」
    #: **不是对产物的判定**，是对跑法的判定。
    #: ⚠️ 导航不了就**别跑**：在错的页面上跑出来的结论比没有结论更坏（它长得像结论）。
    navi_why = ""
    navi_block = False
    if start_url:
        navi_why = _navigate(cdp_bin, ws_url, start_url, env) or ""
        navi_block = bool(navi_why)      # 给了地址却去不了 ⇒ **一遍都不许跑**
    elif allow_navigation_skip:                      # 老调用方：不导航，但**要说清**
        navi_why = "没给要导航到哪个页面（`start_url`）"
    if navi_block:
        return Report(runs=(_skipped("baseline", (
            "这一遍没跑：**没能先导航到那个页面**（%s）—— 在错的页面上跑出来的结论"
            "**不是对产物的判定**（实测：窗口开出来停在 Bit 自己的控制台页上，"
            "那种「自测没过」是对跑法说的）。先把窗口/地址弄对再来自测。" % navi_why)),),
            passed=False, allowed_skips=allowed, cdp_bin=cdp_bin, site=site,
            py_path=str(py))

    def _once(name, **kw):
        return _execute(name, py, ws_url, form_file, correlation_id, log_level, env,
                        run_dir, site, timeout, task_id=task_id, legacy_why=legacy_why, **kw)

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
                                _tail(_noise_free(done.stderr) or done.stdout) or "什么都没说")
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
            if legacy_why:
                _tell(_not_needed("delay", (
                    "这一遍没跑：这份产物**不认 `--delay`**（老写法的 main() 里没有那个"
                    "开关），放慢那一遍就做不成 —— 「填完立刻点」这一类时序竞争这次"
                    "**没验到**。")))
            else:
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
