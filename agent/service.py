"""Task 8：agent 服务 —— 把 Task 7 的图变成一个**能提交、能看进展、能答话**的服务。

```
POST /run                 收开场白 → {job_id}（立刻返回；活在一个工作线程上排队跑）
GET  /job/{id}            走到哪了、在问你什么、结果是什么（**人话**）
POST /job/{id}/reply      回答图停下来的那个问题（继续 / 喊停 / 一句纠正 / 这版不行）
POST /job/{id}/reopen     窗口没了 → 重开一个，**从断点接着跑**（P6）
GET  /health              活着吗、状态存哪儿了、cdp 在哪
```

## 这个服务**不肯**做的四件事（每一件都是被吃过的亏）

1. **不猜成功判据**（§6.1）。载荷里没有 `success_text` → **提交那一刻**就拒（400）。
   图里的 `intake` 闸也能拦（`END_NO_SUCCESS_TEXT`），但那要等一次提交 + 一次调度，
   而这条输入是免费的 —— 「先贵后像别的问题」正是 Task 7 修复轮刚拆掉的形状。
2. **不替图发明默认值**（R-31）。没人给窗口层那根线、也没人点名允许跳过第 4 遍扰动 →
   **照收**，让图在 `intake` 停下并说清「缺 `set_viewport`，谁给得了」。
   服务在这儿塞一个默认，等于替人**预授权跳过**（R-5 明令不许：
   「没验到」不许读成「验过了」）。所以那条 `missing_knob` **不是**要在服务里糊掉的 bug。
3. **不许默默丢掉调用方给的约束**：
   - `allow_skips` 里有不认识的遍（图的边界**不校验**，P14：写错名字会先烧掉一次探路再炸）；
   - 显式的空 `allow_skips=[]`（和「没给」在图的边界**不可区分**，P15：约束会被静默吃掉，
     于是 `country` 回到默认放行）；
   - 载荷要了 `set_viewport`（换窗口大小）而这个部署**给不了那根线**。
   三条都在这儿（免费）拦下，并在人话里说清该怎么改。
4. **不把「跑挂了」写成「跑成了」**。`status` 说的是**job 走到哪了**，`delivered` 说的是
   **产物真的落盘了吗** —— 两件事分开摆，跑挂的 job 的 `result` 一律是 `None`。
   下游接的是「这份产物能不能上生产」这个判断，读错一次就完了。

## 状态住在 saver 里，不在这个进程里（R-19）

`GET /job/{id}` 的正文是从 **checkpoint** 投影出来的，登记表只补两件它答不了的事
（「正在跑」和「跑挂了」）。所以服务重启之后，同一个 job 仍然读得到、接得下去 ——
这正是 R-19 要的东西：状态属于 saver，不属于某个进程里的对象。
`/health` 会**明说**这次用的是哪种 saver（内存的那种，重启就没了）。

## 一次只跑一个（D6）

agent 只有**一个** Bit 窗口（外加一个自己的 gost 端口）。两个 run 并发跑会在同一个窗口上
互相踩 —— 所以这个服务是**单飞**的：一个工作线程按队列跑，后面的 job 排队等
（`status="queued"`）。这不是省事，是照着硬件的形状来的。
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import pathlib
import queue
import threading
import traceback
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import browser_agent, graph, selftest
from agent.graph import NODES, STEP_SAY
from agent.state import END_DELIVERED, END_EXPLORE_UNFINISHED, END_NO_WINDOW

__all__ = ["create_app", "app", "BitWindow", "Service", "Checkpointer",
           "QUEUED", "RUNNING", "WAITING", "DONE", "FAILED"]

# ── job 的状态（**说的都是「走到哪了」，不是「成没成」**）──────────────────────
#: 排队等（单飞：前面还有别的 run 在用那个窗口）
QUEUED = "queued"
#: 图正在跑（这一步做完或需要人时会停下）
RUNNING = "running"
#: 停在闸口，等人回话
WAITING = "waiting"
#: 图走到了 END —— **不代表成功**，成功看 `delivered`
DONE = "done"
#: 这个服务自己挂了（图抛异常 / 队列里的活炸了）—— **绝不许**被读成跑成了
FAILED = "failed"

#: 窗口层那个对象要有哪两个方法（`set_viewport` / `alive`）——
#: 服务只认这两个名字（`BitWindow` 是它的一个实现）。
WINDOW_LAYER = ("set_viewport", "alive")

#: Bit 的窗口服务（§4.6）。bit.sh 里写死的就是这个端口。
BIT_API_PORT = 54345

#: `reopen` 接得住的**两步**，以及各自要接在**哪一步**上（= 重跑那一步）：
#:   探路 ← intake（探路重跑）；自测 ← lint（自测重跑，探路与起草都不重来）
WINDOW_STEPS = {"explore": "intake", "selftest": "lint"}
#: 反查：某个「上一步」下面接着的是哪一步（给 `failed` 那一支用）
_AFTER = {prev: step for step, prev in WINDOW_STEPS.items()}
#: 「停在那一步」的哪几种结局算**窗口**造成的（只有 DONE 的 job 需要看这个）
WINDOW_END_REASONS = {"explore": (END_EXPLORE_UNFINISHED,), "selftest": (END_NO_WINDOW,)}


# ─────────────────────────────── 窗口层（§4.6）───────────────────────────────


class BitWindow:
    """窗口层：跟 Bit 的窗口服务说话的那根线（§4.6）。

    只做两件事，都是 `POST {worker}:54345/browser/...`：
      - `set_viewport(w, h)` —— 换窗口大小（扰动自测的第 4 遍要用；`bit.sh update` 不能用，
        它是残缺包装）。**不走 `bit.sh`**：直接 POST。
      - `alive()` —— 窗口还活着吗（`/browser/pids/alive`）。窗口只活几分钟（§4.6/P6），
        所以在「等人回话」之后、下一步之前值得再问一次。

    字段名与行为都是**量出来的**（2026-09-16 在真 worker 上量的，不是猜的）：
    窗口尺寸在 `browserFingerPrint.openWidth` / `openHeight`（回读 393×852），
    而 `/browser/update` 是**整条记录更新**（只发一小段会被拒：`请选择代理方式`）。
    两个硬事实写在 `set_viewport` 的 docstring 里 —— 其中第二条（**写了配置也不动活窗口**）
    正是「只回读配置就报成功」会变成谎的地方。
    """

    def __init__(self, worker_ip: str, bit_id: str, *, port: int = BIT_API_PORT,
                 timeout: float = 15.0):
        self.worker_ip = str(worker_ip).strip()
        self.bit_id = str(bit_id).strip()
        self.port = int(port)
        self.timeout = float(timeout)
        if not self.worker_ip or not self.bit_id:
            raise ValueError("窗口层要 worker_ip 和 bit_id 两样（D6：agent 用自己的窗口）")

    # ── HTTP ──────────────────────────────────────────────────────
    def _post(self, path: str, body: dict) -> dict:
        url = "http://%s:%d%s" % (self.worker_ip, self.port, path)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError("窗口服务连不上（%s）：%s" % (path, exc)) from exc
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}

    def detail(self) -> dict:
        """读回这个窗口的配置（只读）。找不到/读不了就抛 —— 不静默返回空。"""
        out = self._post("/browser/detail", {"id": self.bit_id})
        data = out.get("data") if isinstance(out, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("窗口服务没给出这个窗口的详情（%s）：%s"
                               % (self.bit_id, str(out)[:200]))
        return data

    # ── 那两根线 ───────────────────────────────────────────────────
    def set_viewport(self, width: int, height: int) -> None:
        """把窗口的尺寸**写进配置**（`width x height`）。

        ⚠️ 两个实测出来的硬事实（2026-09-16，在真 worker 上量的）：

        1. **`/browser/update` 是整条记录更新，不是补丁。** 只发 `{id, browserFingerPrint}`
           会被拒：`{"success":false,"msg":"请选择代理方式"}` —— 也就是说，随手发一小段 JSON
           去改尺寸，**会把代理那几项一起弄丢**（`bit.sh update` 那个残缺包装的同一个坑）。
           所以这里先 `detail()` 读回整条，**只改尺寸、其余一个字节不动地写回去**。
           真应答有 **126 个顶层键 / 112 个指纹键**（`tests/fixtures/bit_window_detail.json`
           就是从真 worker 抓的那份）—— 手工挑几个键回写，挑漏的那个就是被悄悄弄丢的设置
           （比如 `resolution`：它会把重开窗口的宽度卡住）。实测整条回写
           → `success:true`、回读 126/112 个键一个不少。
        2. **写进去的尺寸要等下次启动才生效。** 实测：`success:true`、回读 `openWidth/Height`
           已经是新值，而**活着的那个窗口纹丝不动**（还是 377x757）。所以这个方法**只写配置**，
           「活窗口到底变没变」由调用方去量（见 `Service._viewport_cb`）——
           只回读配置就报「改好了」，等于把一次空转记成跑过了。
        """
        want = (int(width), int(height))
        body = dict(self.detail())                   # **整条原样带回**（见下面第 1 条）
        fp = dict(body.get("browserFingerPrint") or {})
        fp["openWidth"], fp["openHeight"] = want
        body["browserFingerPrint"] = fp
        body["id"] = self.bit_id
        out = self._post("/browser/update", body)
        # 只有**明确说成功**才算成功：实测成功是 `{"success":true,…}`、被拒是
        # `{"success":false,"msg":"请选择代理方式"}` —— 而「没这个键」的应答**什么也没承诺**，
        # 按「不是 false 就是成功」读，等于把没答应当答应。
        if not (isinstance(out, dict) and out.get("success") is True):
            raise RuntimeError("换窗口大小的请求没被确认成功：%s" % str(out)[:200])

    def alive(self) -> Optional[bool]:
        """窗口还活着吗。**三态**：True 活 / False 死 / None 问不出来（别拿它当死）。

        ⚠️ `data` 的真实形状是**测出来的**（2026-09-16 在真 worker 上量的）：
        活着的窗口 → `{"success":true,"data":{"<bit_id>": 4256}}`（一个 **dict**，值是该窗口的 PID）；
        关掉之后 → `{"success":true,"data":{}}`（空 dict）。
        **一开始这里只认 list/bool/str，于是真跑时恒返回 `None`（「不知道」）——
        这根线看起来接好了，其实永远不响。** 这种「接上了但不响」比没接更坏：
        它让 P6 的那道前置看起来存在。测试钉住这两种形状。
        """
        try:
            out = self._post("/browser/pids/alive", {"ids": [self.bit_id]})
        except RuntimeError:
            return None
        if not isinstance(out, dict):
            return None
        data = out.get("data")
        if isinstance(data, dict):
            return self.bit_id in data
        if isinstance(data, list):
            return self.bit_id in [str(x) for x in data]
        if isinstance(data, bool):
            return data
        if isinstance(data, str):
            low = data.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no", ""):
                return False
            return None                  # 比如「操作成功」—— 那只说明这次调用成了，没说窗口活着
        return None


def live_viewport(ws_url: str) -> Optional[tuple]:
    """量**活着的**那个窗口现在多大 —— `cdp eval` 读 `window.innerWidth/innerHeight`（只读）。

    为什么要真去量（而不是回读 Bit 的配置）：**配置改了 ≠ 窗口变了**。实测过：
    `POST /browser/update` 回 `success:true`、`/browser/detail` 回读也是新尺寸，
    而活着的窗口还是 377x757（Bit 的窗口尺寸是**启动时**生效的）。
    只回读配置就报「换好了」，会把一次**空转**的扰动记成跑过了 —— R-5 里最贵的那种谎。

    量不出来（没有 cdp 二进制 / 连不上 / 输出看不懂）→ `None`（**不知道，不许当死**）。
    """
    import re as _re
    import subprocess
    cdp_bin = (os.environ.get("SITEFORGE_CDP_BIN") or os.environ.get("CDP_PATH")
               or str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "cdp" / "cdp"))
    m = _re.match(r"^wss?://(?P<host>[^:/]+):(?P<port>\d+)/", str(ws_url or ""))
    if not m or not os.path.exists(cdp_bin):
        return None
    try:
        done = subprocess.run(
            [cdp_bin, "--host", m.group("host"), "--port", m.group("port"), "eval",
             "window.innerWidth + 'x' + window.innerHeight"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    found = _re.search(r"(\d+)\s*x\s*(\d+)", done.stdout + done.stderr)
    return (int(found.group(1)), int(found.group(2))) if found else None


def bit_window_from_env(env: Optional[dict] = None) -> Optional[BitWindow]:
    """按环境变量建窗口层；没配就返回 `None`（= 「这个部署给不了那根线」）。

    ⚠️ 没配 → `None`，**不是**一个“什么都答应、其实什么也没做”的空壳：R-31 要的是
    「要么真给得了，要么图停下点名」，一个假回调会把第 4 遍扰动变成静默的空转。
    """
    env = os.environ if env is None else env
    worker = (env.get("BIT_WORKER_IP") or "").strip()
    bit_id = (env.get("BIT_ID") or "").strip()
    if not worker or not bit_id:
        return None
    try:
        port = int(env.get("BIT_API_PORT") or BIT_API_PORT)
    except ValueError:
        port = BIT_API_PORT
    return BitWindow(worker, bit_id, port=port)


# ─────────────────────────────── checkpointer（R-19）───────────────────────────────


class Checkpointer:
    """按配置**懒**建 saver：`import agent.service` 不该去连数据库（测试会 import 它）。

    生产的形态是 Postgres（R-19：中断之后要恢复，状态就不能只活在进程里）；
    没配 `DATABASE_URL` 时退回内存 saver —— 但要**说出来**（`/health` 里那句人话），
    不然「能恢复」会被读成「重启也还在」。
    """

    def __init__(self, *, saver=None, url: Optional[str] = None):
        self._saver = saver
        self._url = url or os.environ.get("DATABASE_URL") or None
        self._lock = threading.RLock()
        self._read_lock = threading.RLock()
        self._reader = None
        self._conn = None
        #: 「这是哪种 saver」在**建的那一刻**就定下来，之后不变。
        #: ⚠️ 不能靠「还没建」（`_saver is None`）去判：第一次真要用之后 `_saver` 就有值了，
        #: 那样 `/health` 会在一个**状态明明在 Postgres 里**的部署上说「重启就没了」
        #: —— 而那句话恰好是这套系统最不该说错的一句（R-19）。
        self._kind = "memory" if saver is not None else ("postgres" if self._url else "memory")
        #: 读是不是走**另一条连接**（只有那种情况才该绕开写锁）。
        #: ⚠️ 与 `kind` 同一个坑：必须**在构造时**定下来，不能等 `get()` 建完再判。
        self._separate_reader = saver is None and bool(self._url)

    @property
    def kind(self) -> str:
        return self._kind

    def say(self) -> str:
        if self.kind == "postgres":
            return ("状态存在 Postgres 里：中断之后接着跑、服务重启也还在"
                    "（这就是「人能停下来再回来」靠的东西）。")
        return ("状态只存在这个进程的内存里：**服务一重启就没了**，中断之后也就恢复不了。"
                "要能恢复就给 `DATABASE_URL`，用 Postgres 存（R-19）。")

    @property
    def lock(self) -> threading.RLock:
        """saver 的串行闸：一个 Postgres 连接**不能**被两个线程同时用。"""
        return self._lock

    def get(self):
        """写侧 saver（图跑起来用的那条连接）。"""
        with self._lock:
            if self._saver is not None:
                return self._saver
            if not self._url:
                from langgraph.checkpoint.memory import InMemorySaver
                self._saver = graph.allowlisted(InMemorySaver())
                self._kind = "memory"
                return self._saver
            self._saver = self._connect(self._url)
            self._kind = "postgres"
            return self._saver

    def reader(self):
        """**读**侧 saver：另开一条连接，专给 `GET /job/{id}` 这类读用。

        为什么要有第二条：一次 `invoke` 可能跑几分钟（真浏览器 + 模型），
        而它整段都持着写锁（一个 Postgres 连接不能被两个线程同时用）。
        读要是也走那条连接，Console 的「进展」就会排在一次探路后面 —— 人以为卡死了。
        """
        with self._read_lock:
            if self._reader is not None:
                return self._reader
            if self._saver is not None:
                self._reader = self._saver          # 调用方给的 saver：读也问它
                return self._reader
            if not self._url:
                self._reader = self.get()           # 内存 saver：同一条（没有连接可抢）
                return self._reader
            conn = self._connect_conn(self._url)
            from langgraph.checkpoint.postgres import PostgresSaver
            reader = graph.allowlisted(PostgresSaver(conn))
            # 表没建过就建（幂等）。不建的话，「**新库第一次请求就是读**」会在
            # psycopg 那边报「relation \"checkpoints\" does not exist」→ **500**，
            # 而那时候该说的其实是「没这个任务」（404）—— 把「没有」说成「坏了」。
            reader.setup()
            self._reader = reader
            return self._reader

    @property
    def separate_reader(self) -> bool:
        """读连接**真的是另一条连接**吗。

        只有在这种情况下才该绕开写锁：`InMemorySaver` **没有内部锁**，
        读绕过写锁撞上一次 `invoke` 就可能「dictionary changed size during iteration」→ 500。
        同一条 saver 上，读**该**排队（排队是对的，不是 bug）。
        """
        return self._separate_reader

    @property
    def reader_lock(self) -> threading.RLock:
        """读侧那把锁（与写侧分开 —— 读不该排在一次 invoke 后面）。"""
        return self._read_lock

    def _connect_conn(self, url: str):
        import psycopg
        return psycopg.connect(url, autocommit=True)

    def _connect(self, url: str):
        """建 Postgres saver 并 `setup()`（建表）。

        `allowlisted(...)` 是**必须**的：`Journey` / `Report` / `Run` 是 dataclass，
        msgpack 的 serde 要**点名允许**它们（Task 7 实测过那条警告）。
        ⚠️ **不要**开 `LANGGRAPH_STRICT_MSGPACK`（那会把警告变成硬错误）。
        """
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        self._conn = psycopg.connect(url, autocommit=True)
        saver = PostgresSaver(self._conn)
        saver.setup()
        return graph.allowlisted(saver)


# ─────────────────────────────── 收上来的东西 ───────────────────────────────


class RunRequest(BaseModel):
    """`POST /run` 的载荷 —— 等于图的开场白（`state.SiteState` 的前几项）。

    图 **不自己发明** 任何「没验到也算过」的默认值（§2.5），所以它要的那几样只有调用方给得了：
    `success_text`（什么算成功，只有人知道）、`ws_url` / `form_file`（§4.6 前提层的产物）、
    以及窗口层的旋钮（`set_viewport` / `allow_skips` / `entry_url`，R-31）。
    """

    url: str = Field("", description="站点 URL")
    goal: str = Field("", description="人给的意图：要摸清什么 / 什么算完成")
    mode: str = Field("build", description="build（没跑过的站）或 fix（挂着的老站）")
    success_text: str = Field("", description="成功判据：走通之后页面上会出现哪段文字（**只有人知道**）")
    evidence: str = Field("", description="fix 模式：失败证据的引用（FMR formLog / formStep）")
    site: str = Field("", description="站点短名（不给就从 URL 推）")
    ws_url: Optional[str] = Field(None, description="§4.6 前提层开出来的窗口（bit.sh open 吐的那串）")
    form_file: Optional[str] = Field(None, description="表单数据文件（自测用）")
    env: Optional[dict] = Field(None, description='{"proxy_country","dpr","ua","viewport"}；没人给就不带')
    platform: Optional[dict] = Field(None, description='{"guess","confidence"}；平台分类不在这张图里')
    out_dir: Optional[str] = Field(None, description="产物落在哪个目录（默认 forms/sites/）")
    allow_skips: Optional[list[str]] = Field(
        None, description="**点名**允许不跑的那几遍扰动（认的是那五个名字）。"
                          "不给 = 用默认（只允许跳 country）；给空列表没有意义，会被拒")
    entry_url: Optional[str] = Field(None, description="第 2 遍刷新回哪个 URL（R-6）")
    set_viewport: bool = Field(
        False, description="把**窗口层那根线**接上（第 4 遍扰动要换窗口大小，`POST /browser/update`）。"
                           "尺寸由自测那一步定（`selftest.DEFAULT_VIEWPORT`）；这个部署没接窗口层时"
                           "给 true 会被拒 —— 不替你默认放过那一遍（R-5）")


class ReplyRequest(BaseModel):
    """人在闸口的回话（`state.human_reply` 认的那三种写法 + 这几种同义写法）。"""

    action: str = Field("continue", description="continue / stop / revise（不认识的当纠正处理，不丢）")
    note: str = Field("", description="纠正的话：直接说该点哪（会一路带进 draft）")


class ReopenRequest(BaseModel):
    """窗口死了之后重开一个（P6）：把**新窗口**放回状态，从断点接着跑。"""

    ws_url: str = Field(..., description="新开出来的窗口（bit.sh open 吐的那串）")
    entry_url: Optional[str] = Field(None, description="顺便更新第 2 遍刷新回哪个 URL")
    set_viewport: bool = Field(False, description="顺便把窗口层那根线接上（第 4 遍扰动要用）")


# ─────────────────────────────── job ───────────────────────────────


@dataclasses.dataclass
class Job:
    job_id: str
    brief: dict
    status: str = QUEUED
    say: str = ""
    error: Optional[str] = None
    graph: Any = None
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    created_at: str = ""

    def snapshot_for_view(self) -> tuple:
        with self.lock:
            return self.status, self.say, self.error


# ─────────────────────────────── 服务本体 ───────────────────────────────


class Service:
    """登记表 + 一个工作线程 + 一组投影。

    工作线程是**单飞**的（D6：agent 只有一个窗口，两个 run 并发会在同一个窗口上互相踩）。
    """

    def __init__(self, *, graph_factory: Optional[Callable] = None,
                 window: Any = None, checkpointer=None, checkpointer_url: Optional[str] = None,
                 out_dir: Optional[str] = None, viewport_probe: Optional[Callable] = None):
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._check = (checkpointer if isinstance(checkpointer, Checkpointer)
                       else Checkpointer(saver=checkpointer, url=checkpointer_url))
        self._window = window
        self._graph_factory = graph_factory
        self._out_dir = out_dir or str(graph.DEFAULT_OUT_DIR)
        #: 「活着的窗口现在多大」怎么量（默认走 cdp 读页面；测试注入桩）
        self._viewport_probe = viewport_probe or live_viewport
        self._probe = None
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()

    # ── 外面那三层：图、窗口、检查点 ────────────────────────────────
    def _build_graph(self, brief: dict):
        """给一个 job 拼一张图。**旋钮就在这儿给**（R-31：服务是窗口层的供给方）。

        `graph_factory(brief, deps)` 收到的是**本服务拼好的 `Deps`** —— 于是调用方
        （测试 / 计划三的 Console）可以只换掉贵的那些（探路、自测），而**继承**窗口层那根线。
        生产走 `graph_factory=None` 那条：全是真接线。
        """
        deps = graph.Deps(explore=self._explore_for(brief),
                          set_viewport=(self._viewport_cb(brief.get("ws_url"))
                                        if brief.get("set_viewport") else None))
        if self._graph_factory is not None:
            return self._graph_factory(brief, deps)
        return graph.build(checkpointer=self._check.get(), deps=deps)

    def _explore_for(self, brief: dict) -> Optional[Callable]:
        """探路要朝**载荷里那个窗口**去（服务是知道窗口的那一层）。

        为什么要这一根线：`browser_agent.explore()` 自己不收 `ws_url`（图调它时只给
        url/goal/预算/暂停谓词），窗口是从 `tools.McpSession.open(ws_url=…)` 或环境变量
        `CDP_WS_URL` 进去的。载荷里那个窗口不给它，它就会退回默认的 `127.0.0.1:9222` ——
        也就是**别的**浏览器（本机那个 headless，或者更糟：别的任务的窗口）。
        """
        ws_url = str(brief.get("ws_url") or "").strip()
        if not ws_url:
            return None                       # 没人给窗口 → 用默认（图会在自测那步停下点名）
        return lambda url, goal, budget=None, should_pause=None: browser_agent.explore(
            url, goal, budget=budget, should_pause=should_pause, ws_url=ws_url)

    def _viewport_cb(self, ws_url: Optional[str] = None) -> Optional[Callable]:
        """窗口层那根线（第 4 遍扰动要换窗口大小）。

        - 没人点名要它（载荷里 `set_viewport=false`）→ 返回 `None`：**图会停在 `intake`
          点名**「缺 `set_viewport`，谁给得了」—— 那是 R-31 要的诚实停止，不是缺陷。
        - 要了、但这个部署没接窗口层 → `_intake_problems` 已经在**提交那一刻**拒了。
        - 要了、也接了 → 真回调：**先写配置，再量活窗口**。量出来没变就**抛**
          （`selftest` 会把这一遍记成「没跑」，而「没跑」不算过，R-5）。
          尺寸用自测那一步给的那个（`selftest.run` 按 `DEFAULT_VIEWPORT` 调），服务不替它定。
        """
        if self._window is None or not hasattr(self._window, "set_viewport"):
            return None

        def cb(width, height):
            self._window.set_viewport(width, height)
            got = self._viewport_probe(ws_url) if ws_url else None
            if got is None:
                # **量不出来 ≠ 做成了**：没有 cdp / 连不上 / ws_url 不认识时探针给 None，
                # 而「没量到」被当成「没问题」的话，第 4 遍会被记成**跑过了**，
                # 于是产物带着「折叠/遮挡/坐标假设验过了」出门 —— 而一个数都没量到。
                # 抛出去的后果是**对的**那一头：`selftest.run` 把回调抛错记成
                # 「这一遍没跑」（`selftest.py:475`），不是把整跑杀掉。
                raise RuntimeError(
                    "换窗口大小之后**量不出活窗口现在多大**（没有 cdp 二进制 / 连不上 / "
                    "ws_url 认不出来），所以这一遍扰动没法诚实地说自己做成了。"
                    "把它当成「没验到」：要么修好这根线（`SITEFORGE_CDP_BIN` / 窗口还活着），"
                    "要么明确放弃第 4 遍（把 \"viewport\" 写进 `allow_skips`）。")
            if tuple(got) != (int(width), int(height)):
                raise RuntimeError(
                    "把窗口换成 %sx%s 了，但**活着的那个窗口**量出来还是 %sx%s —— 这一遍扰动"
                    "等于没做。Bit 的窗口尺寸是**启动时**生效的（实测：写配置回 success、"
                    "回读也是新值，活窗口纹丝不动）。所以第 4 遍要真跑，得**关掉重开**："
                    "要么放弃这一遍（写进 allow_skips），要么把「关窗口 → 改尺寸 → 重开 → "
                    "换 ws_url」做成一条流程 —— 那要改自测的接口（现在五遍共用一个窗口）。"
                    % (width, height, got[0], got[1]))
        return cb

    def _probe_graph(self):
        """一个只用来 `get_state()` 的图（不跑节点）—— 服务重启后靠它把 job 读回来。

        ⚠️ 它建在**本服务的** checkpointer 的**读连接**上（不是 `graph_factory` 那个、
        也不是写连接）：一个 job 的图是调用方拼的，而「这个 job 现在什么样」这件事得由一个
        **确定接在同一份状态上**的东西来回答（R-19：状态住在 saver 里 —— 那就得问同一个 saver）；
        而**读**这条线不该排在一次 `invoke` 后面（那个可能跑几分钟）。
        """
        if self._probe is None:
            self._probe = graph.build(checkpointer=self._check.reader(), deps=graph.Deps())
        return self._probe

    def _cfg(self, job_id: str) -> dict:
        return {"configurable": {"thread_id": job_id}}

    def _snapshot(self, job_id: str):
        """读这个 job 的状态。

        - **服务自己拼的图**（生产，`graph_factory is None`）→ 走**读连接**：
          读不跟正在跑的那次 `invoke` 抢写锁（那个可能持几分钟）。
        - **调用方自己拼的图** → 读也问它那张图（它接在哪份 saver 上，它最清楚）。
        """
        job = self._jobs.get(job_id)
        if self._graph_factory is None and self._check.separate_reader:
            with self._check.reader_lock:
                return self._probe_graph().get_state(self._cfg(job_id))
        g = job.graph if (job is not None and job.graph is not None) else self._probe_graph()
        with self._check.lock:
            return g.get_state(self._cfg(job_id))

    # ── 排队与跑 ──────────────────────────────────────────────────
    def _ensure_worker(self):
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="siteforge-worker",
                                                daemon=True)
                self._worker.start()

    def _submit(self, job: Job, payload) -> None:
        self._ensure_worker()
        self._queue.put((job, payload))

    def _work(self):
        while True:
            job, payload = self._queue.get()
            try:
                self._advance(job, payload)
            except BaseException:                    # noqa: BLE001 —— 工作线程**不许**死
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _advance(self, job: Job, payload) -> None:
        """把图往前推一步 —— **这是唯一一个 job 会动的地方**（单飞）。

        抛出来的东西一律变成 `failed`：一个跑挂的 job **绝不许**被读成跑成了
        （它 `result` 为 `None`，状态是 `failed`，人话里留着原始错误）。
        """
        with job.lock:
            job.status = RUNNING
            job.say = "在跑：真浏览器 + 模型，做完一步或需要你时就会停下来。"
        try:
            with self._check.lock:                   # 一个 saver 连接不被两个线程同时用
                out = job.graph.invoke(payload, self._cfg(job.job_id))
        except BaseException as exc:                 # noqa: BLE001 —— 包括 _Stop 之类的 BaseException
            with job.lock:
                job.status = FAILED
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say = ("这一步没跑成，停下了：%s\n"
                           "（任务没有交付任何东西 —— 产物目录里不会有它写的 py。）" % exc)
            return
        with job.lock:
            # 登记表只补「正在跑 / 跑挂了」这两件 checkpoint 答不了的事。
            # 「停在等人」还是「跑到头了」、以及**为什么停**，一律**从 checkpoint 投影**
            # （状态的唯一真源在 saver 里，R-19）—— 所以这里不再存一份 `say`。
            job.status = DONE
            job.say = ""

    # ── 服务重启之后：从 checkpoint 把 job 捡回来（R-19）───────────────
    def _recover(self, job_id: str) -> Optional[Job]:
        """登记表里没有这个 job，但 checkpoint 里有 → 就地从状态里把它捡回来。

        这是 R-19 在服务侧的正身：**状态住在 saver 里，不在进程里的对象里**。
        没有它，「中断之后恢复不了」只是从图那一层挪到了服务这一层 ——
        服务一重启，人就没法让那次运行接着走了。

        ⚠️ 有一件东西 checkpoint 里**没有**：调用方在开场白里说的那个 `set_viewport`
        （那是服务这一层的开关，不是图的状态）。所以捡回来的 job 用的是
        「**这个部署有没有窗口层**」—— 这是安全的：给上那根线只会让第 4 遍扰动**真跑**，
        永远不可能变成「静默跳过」（R-5 禁止的是跳过，不是执行）。
        """
        snap = self._snapshot(job_id)
        values = dict(getattr(snap, "values", None) or {})
        if not values:
            return None
        keep = ("url", "goal", "mode", "success_text", "evidence", "site", "ws_url",
                "form_file", "env", "platform", "out_dir", "allow_skips", "entry_url")
        brief = {k: values[k] for k in keep if values.get(k) is not None}
        brief["set_viewport"] = bool(self._window is not None
                                     and hasattr(self._window, "set_viewport"))
        job = Job(job_id=job_id, brief=brief, status=DONE,
                  say="（服务重启过：这个任务是从 checkpoint 里捡回来的）",
                  created_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        job.graph = self._build_graph(brief)
        with self._jobs_lock:
            self._jobs.setdefault(job_id, job)
            return self._jobs[job_id]

    # ── 投影：把 checkpoint 变成给人看的那个东西 ─────────────────────
    def _view(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is not None:
            status, say, error = job.snapshot_for_view()
            if status == FAILED:
                return {"job_id": job_id, "status": FAILED, "delivered": False,
                        "say": say or "这一步没跑成，停下了。", "error": error,
                        "result": None, "gate": None, "site": job.brief.get("site")}
            if status in (QUEUED, RUNNING):
                return {"job_id": job_id, "status": status, "delivered": False,
                        "say": say or ("排队等窗口（前面还有别的 run 在用）" if status == QUEUED
                                       else "在跑。"),
                        "error": None, "result": None, "gate": None,
                        "site": job.brief.get("site")}
        snap = self._snapshot(job_id)
        values = dict(getattr(snap, "values", None) or {})
        if not values and job is None:
            raise KeyError(job_id)          # 登记表没有、checkpoint 也没有 = 没这个任务
        return self._project(job_id, snap)

    def _project(self, job_id: str, snap) -> dict:
        values = dict(getattr(snap, "values", None) or {})
        gates = [g for g in self._interrupts(snap)]
        base = {"job_id": job_id, "delivered": False, "error": None,
                "site": values.get("site"), "gate": None, "result": None}

        if gates:
            value = gates[0].value if hasattr(gates[0], "value") else gates[0]
            value = dict(value or {})
            step = str(value.get("step") or "")
            return {**base, "status": WAITING,
                    "say": str(value.get("say") or "停下来了，在等你一句话。"),
                    "gate": {"step": step, "step_say": STEP_SAY.get(step, step),
                             "ask": value.get("say"), "facts": value.get("facts") or {},
                             "can": value.get("can") or []}}

        end_reason = str(values.get("end_reason") or "")
        py_path = values.get("py_path") if end_reason == END_DELIVERED else None
        delivered = bool(end_reason == END_DELIVERED and py_path
                         and pathlib.Path(str(py_path)).is_file())
        report = values.get("report")
        note = str(values.get("end_note") or "").strip()
        return {**base, "status": DONE, "delivered": delivered,
                "say": note or ("这一趟跑完了，但没说清是怎么结束的（state 里没有 end_reason）—— "
                                "这本身就不正常，别把它当成功。"),
                "result": {"end_reason": end_reason, "delivered": delivered,
                           "py_path": str(py_path) if py_path else None,
                           "site": values.get("site"),
                           "visits": list(values.get("visits") or []),
                           "say": note,
                           "selftest": self._report_brief(report)}}

    @staticmethod
    def _interrupts(snap) -> list:
        """中断可能挂在 `snapshot.interrupts` 上，也可能在 `tasks[*].interrupts` 里。

        两处都看 —— langgraph 的版本间这块换过位置，只认一处会在升级时**静默**变成
        「没有中断」（那会把「在等人」读成「跑完了」）。
        """
        out = list(getattr(snap, "interrupts", None) or ())
        if out:
            return out
        for task in (getattr(snap, "tasks", None) or ()):
            out.extend(list(getattr(task, "interrupts", None) or ()))
        return out

    @staticmethod
    def _report_brief(report) -> Optional[dict]:
        if report is None:
            return None
        runs = [r.as_dict() if hasattr(r, "as_dict") else dict(r)
                for r in (getattr(report, "runs", None) or ())]
        return {"passed": bool(getattr(report, "passed", False)),
                "say": report.summary() if hasattr(report, "summary") else "",
                "runs": runs}

    # ── 输入检查（**免费的那些**：在烧掉一次探路之前）────────────────────
    def _intake_problems(self, body: RunRequest) -> list:
        problems = []
        if not (body.url or "").strip():
            problems.append("还没说**是哪个站点**：`url`。")
        if not (body.goal or body.evidence or "").strip():
            problems.append("还没说**这次要做什么**：`goal`（或者 fix 模式下的失败证据 `evidence`）。")
        if not (body.success_text or "").strip():
            problems.append("还没说**什么算成功**：`success_text` —— 走通之后页面上会出现哪段文字。"
                            "这一条只有人知道，猜不得（猜出来的成功判据会让产物「跑到底再报成功」）。")
        if body.allow_skips is not None:
            if not body.allow_skips:
                problems.append("`allow_skips` 是空的（`[]`）—— 空列表没有意义：它和「没给」在图的边界"
                                "**分不开**，于是你以为「什么也不许跳」，实际会用默认（只允许跳 country）。"
                                "要么别给这个字段（用默认），要么把要跳的那几遍列出来：%s。"
                                % "、".join(selftest.RUN_NAMES))
            else:
                unknown = [n for n in body.allow_skips if n not in selftest.RUN_NAMES]
                if unknown:
                    problems.append("`allow_skips` 里有不认识的遍：%s。认的是这五个：%s。"
                                    "（写错的名字如果拖到自测那一步才炸，会先白烧掉一次探路 —— "
                                    "真窗口 + 一次模型跑。）"
                                    % ("、".join(unknown), "、".join(selftest.RUN_NAMES)))
        if body.set_viewport:
            can_do = (self._window is not None and hasattr(self._window, "set_viewport"))
            allowed = list(body.allow_skips or ())
            if not can_do and "viewport" not in allowed:
                problems.append("载荷里要了 `set_viewport`（第 4 遍换窗口大小），但这个部署**没有接窗口层** —— "
                                "换不了。两条路，都摆在明面上：① 把窗口层接上（`BIT_WORKER_IP`/`BIT_ID`），"
                                "那这一遍就真跑；② 明确放弃它：把 `\"viewport\"` 写进 `allow_skips` —— "
                                "交出来的产物会带着「这一类失败这次**没验到**」。"
                                "服务不会替你默认放过它（R-5）。")
        return problems

    # ── 三个动作 ──────────────────────────────────────────────────
    def start(self, body: RunRequest) -> dict:
        problems = self._intake_problems(body)
        if problems:
            raise HTTPException(status_code=400, detail=self._problems_say(problems))

        brief = body.model_dump(exclude_none=True)
        brief.setdefault("out_dir", self._out_dir)
        brief["success_text"] = body.success_text
        job_id = "job-%s" % uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, brief=brief, status=QUEUED,
                  say="收到了，排队开跑。",
                  created_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        with self._jobs_lock:
            self._jobs[job_id] = job
        try:
            job.graph = self._build_graph(brief)
        except BaseException as exc:                 # 拼不起来（saver 连不上之类）
            with job.lock:
                job.status = FAILED
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say = ("起不来：%s\n（这一趟**没有**开浏览器、也没有写任何产物。）" % exc)
            return self._view(job_id)
        self._submit(job, self._payload(brief))
        return self._view(job_id)

    @staticmethod
    def _payload(brief: dict) -> dict:
        """开场白 → 图认得的那几个键（服务这一层的开关，比如 `set_viewport`，不往里塞）。"""
        keep = ("url", "goal", "mode", "success_text", "evidence", "site", "ws_url",
                "form_file", "env", "platform", "out_dir", "allow_skips", "entry_url")
        return {k: brief[k] for k in keep if k in brief}

    def reply(self, job_id: str, body: ReplyRequest) -> dict:
        job = self._jobs.get(job_id) or self._recover(job_id)   # 重启过就从 checkpoint 捡回来
        if job is None:
            raise KeyError(job_id)
        view = self._view(job_id)
        if view["status"] != WAITING:
            raise HTTPException(status_code=409, detail=
                                "这个任务没在等人（它现在是「%s」）—— 没有要你回的话。"
                                "跑到头了就另起一个任务；要它接着走，先看 `say` 里说了什么。"
                                % self._status_say(view["status"]))

        # P6：窗口只活几分钟，而人可能过了十分钟才回话。先问一句窗口还在不在 ——
        # 不先问的后果是自测拿着一个死窗口跑，报告把「连不上」记成**产物的问题**。
        values = dict((self._snapshot(job_id).values or {}))
        dead = self._window_is_gone(values.get("ws_url"))
        if dead:
            raise HTTPException(status_code=409, detail=
                                "先别接着走：这个窗口**已经不在了**（§4.6：Bit 窗口只活几分钟，"
                                "而这一步可能跑很久）。现在接着走的话，自测会拿着一个死窗口跑，"
                                "报告会把「连不上」说成产物的问题 —— 那是误导。\n"
                                "重开一个窗口，再从这里接着走（账本和这一步的进展都还在）：\n"
                                "    POST /job/%s/reopen  {\"ws_url\": \"<新窗口>\"}" % job_id)
        # ⚠️ 先把 job 从「停住」挪开，**再**交下去。
        # 不挪的话：`_view` 会从 checkpoint 投影（那个中断还在）→ 响应说「在等人」+
        # 旧那道闸 → 调用方再回一次话 → 那一句 resume 落到**下一道闸**上 →
        # **一步在没人看着的情况下跑掉了**（§6.2/D16 的核心承诺）。
        with job.lock:
            job.status = RUNNING
            job.say = "收到你的话，接着跑（下一个要你拿主意的地方会再停下来）。"
        self._submit(job, Command(resume={"action": body.action, "note": body.note}))
        return self._view(job_id)

    def reopen(self, job_id: str, body: ReopenRequest) -> dict:
        """窗口没了 → 换一个新窗口，**从 checkpoint 接着跑**（P6）。

        接得住的是**窗口那一支**，两步：
        - **自测**（`no_window`）：探路那一段**不重来**（账本在 checkpoint 里）；
        - **探路**（`explore_unfinished`，或者探路里**炸了**的那个 `failed`）：
          探路**要从头再走一遍** —— 页面状态没了，账本必须重新收。这一句必须用**人话**
          说给调用方听（它意味着又一次真窗口 + 模型的钱），不许含糊过去。
        """
        job = self._jobs.get(job_id) or self._recover(job_id)
        if job is None:
            raise KeyError(job_id)
        view = self._view(job_id)
        point = self._resume_point(job_id, view)
        if point is None:
            raise HTTPException(status_code=409, detail=self._cannot_reopen_say(job_id, view))
        if not (body.ws_url or "").strip():
            raise HTTPException(status_code=400, detail="`ws_url` 是空的 —— 重开窗口要给出新窗口那串。")
        if body.set_viewport and not (self._window is not None
                                      and hasattr(self._window, "set_viewport")):
            raise HTTPException(status_code=400, detail=(
                "要接窗口层那根线（`set_viewport`），但这个部署没有窗口层 —— 换不了窗口大小。"
                "要么把窗口层接上（`BIT_WORKER_IP`/`BIT_ID`），要么重开时别要它"
                "（那就还是老样子：没人给这根线，图停在自测那一步点名）。"))

        resume_from, step = point
        # 把新窗口放回状态，并**把上一次那个诚实的停止收掉**（它是上一次的结论，不是这一次的）。
        # `as_node=<上一步>`：图接着跑的正是**停下来的那一步** ——
        # 自测接在 lint 上（探路与起草都不重来）、探路接在 intake 上（探路重跑）。
        patch = {"ws_url": body.ws_url, "end_reason": "", "end_note": ""}
        if body.entry_url:
            patch["entry_url"] = body.entry_url
        if step == "explore":
            patch["explore_say"] = ""          # 上一趟探路的说法收掉（新的探路会写新的）
            patch["end_note"] = ""
        with job.lock:
            job.brief.update({"ws_url": body.ws_url})
            job.brief.pop("_failed_at", None)
            if body.entry_url:
                job.brief["entry_url"] = body.entry_url
            # ⚠️ **窗口一换就必须重拼那张图** —— 不是「只有新打开 set_viewport 时才要」。
            #
            # `Deps.explore`（`_explore_for`）与窗口层那根线（`_viewport_cb`）都是**拼图时**
            # 对 `brief["ws_url"]` 闭包出来的。只把新 url 写进 brief / checkpoint 而不重拼，
            # 得到的是：`reopen` 回 200、状态里写着新窗口，而探路手里那根线**还指着旧窗口** ——
            # 续跑会又一次死在旧窗口上，出路只剩「重新开一个任务」= 整轮重来（P6 明令不许）。
            # 探针那根线同理：不重拼它就一直量**旧**窗口（要么以莫名其妙的理由判没做成，
            # 要么旧窗口刚好是那个尺寸时**判成做成了**）。
            #
            # 换一张图接着跑是安全的：状态在 checkpoint 里（Test Graph 那条
            # 「另一个图对象接着跑」的用例钉的就是这件事）。
            job.brief["set_viewport"] = bool(job.brief.get("set_viewport") or body.set_viewport)
            job.graph = self._build_graph(job.brief)
            job.status = RUNNING
            job.error = None                    # 上一次那个失败不再是这个 job 的现状
            job.say = ("窗口重开了，探路要从头再走一遍（页面状态没了，账本得重新收）——"
                       "你之前说的话和开场白都还在。" if step == "explore" else
                       "窗口重开了，从上次停下的地方接着跑（探路那一段不重来）。")
        with self._check.lock:
            job.graph.update_state(self._cfg(job_id), patch, as_node=resume_from)
        self._submit(job, None)                      # None = 「接着跑」，不是新的输入
        return self._view(job_id)

    def _resume_point(self, job_id: str, view: dict) -> Optional[tuple]:
        """这个 job 能不能用「重开窗口 + 接着跑」接住？能就回 `(接在哪一步, 要重跑哪一步)`。

        两种「停」，判据不同（这是这一条最容易写错的地方）：

        - **DONE**：图是**在那一步里**停下的 —— 那一步的结论已经落进状态了
          （`visits[-1]` 就是它）→ 接在它的**上一步**上，于是接着跑的正是它。
        - **FAILED**：图是**在那一步的下一跳里**炸的（那一步落下了、下一步没有）
          → 接在**最后落下的那一步**上。窗口在探路途中断开就是这个形状：
          `visits[-1] == "intake"`，炸掉的是 `explore`。

        两种情况都只认**窗口那一支**（`WINDOW_STEPS` 里那两步）。
        """
        values = dict(getattr(self._snapshot(job_id), "values", None) or {})
        visits = list(values.get("visits") or [])
        last = str(visits[-1]) if visits else ""
        failed = view["status"] == FAILED
        if failed:
            step = _AFTER.get(last)             # 在「上一步」的下一跳里炸的
            return (last, step) if step else None
        if view["status"] != DONE:
            return None
        reason = str(values.get("end_reason") or "")
        if last in WINDOW_STEPS and reason in WINDOW_END_REASONS.get(last, ()):
            return (WINDOW_STEPS[last], last)
        return None

    # ── 人话 ──────────────────────────────────────────────────────
    def _window_is_gone(self, ws_url) -> bool:
        """窗口死了吗。**只有明确说死了才拦**（问不出来一律当不知道 —— 不许误杀）。"""
        if not ws_url or self._window is None or not hasattr(self._window, "alive"):
            return False
        return self._window.alive() is False

    def _cannot_reopen_say(self, job_id: str, view: dict) -> str:
        """接不住时说的**人话**：这个任务现在什么形状、以及「重开窗口」接得住的是哪两种。"""
        can = ("能接住的是**窗口那一支**两种：①自测那一步发现窗口没了（探路不重来）；"
               "②探路没走完、或者探路里炸了（探路要从头再走一遍）。")
        if view["status"] in (QUEUED, RUNNING):
            return ("重开窗口要等这个任务停下来 —— 它现在是「%s」。%s"
                    % (self._status_say(view["status"]), can))
        result = view.get("result") or {}
        reason = result.get("end_reason")
        if reason == END_DELIVERED:
            return ("不用重开：这一趟**已经交付**了（产物在 %s）。要再走一遍就另起一个任务。%s"
                    % (result.get("py_path"), can))
        # ⚠️ 不能从 `view["result"]` 里读 `visits` —— `FAILED` 的 job 那个字段是 `None`
        # （**那是对的**：跑挂的没有结果），于是这句会永远渲染成「不知道哪一步」。
        # 走到哪儿了这件事只在 checkpoint 里。
        visits = list((self._snapshot(job_id).values or {}).get("visits") or [])
        where = str(visits[-1]) if visits else ""
        if view["status"] == FAILED:
            return ("不用重开：这一趟是**炸**在「%s」那一步的（不是窗口没了）：\n%s\n%s\n"
                    "重开一个窗口解决不了它 —— 按上面那句话说的办。"
                    % (STEP_SAY.get(where, where or "不知道哪一步"),
                       str(view.get("say") or "")[:400], can))
        return ("不用重开：这一趟停下来不是因为窗口没了，而是「%s」：\n%s\n%s\n"
                "重开一个窗口解决不了它 —— 按上面那句话说的办。"
                % (STEP_SAY.get(where, reason or "没说清"),
                   str(view.get("say") or "")[:400], can))

    @staticmethod
    def _problems_say(problems: list) -> str:
        return ("这份开场白还开不了工（**没有**碰浏览器、没有跑模型、没有写任何文件）：\n  · "
                + "\n  · ".join(problems))

    @staticmethod
    def _status_say(status: str) -> str:
        return {QUEUED: "还在排队", RUNNING: "正在跑", WAITING: "在等人回话",
                DONE: "跑完了", FAILED: "跑挂了"}.get(status, status)


# ─────────────────────────────── 拼成 app ───────────────────────────────


def create_app(*, graph_factory: Optional[Callable] = None, window: Any = None,
               checkpointer=None, checkpointer_url: Optional[str] = None,
               out_dir: Optional[str] = None, viewport_probe: Optional[Callable] = None) -> FastAPI:
    """拼一个 app。测试从这里注入桩图 / 桩窗口 / 内存 saver。

    `window=None` 是**默认且合法**的：这个部署没接窗口层 —— 于是 `set_viewport` 那根线
    不存在，图会在 `intake` 停下点名（R-31 要的正是这个，不是要服务糊一个假回调）。
    """
    svc = Service(graph_factory=graph_factory, window=window, checkpointer=checkpointer,
                  checkpointer_url=checkpointer_url, out_dir=out_dir,
                  viewport_probe=viewport_probe)
    api = FastAPI(title="siteforge", version="0.1",
                  description="看着真页面产出 cdp-first py 脚本的 agent 服务（计划二 Task 8）")

    @api.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "service": "siteforge",
            "checkpointer": svc._check.kind,
            "say": svc._check.say(),
            "window_layer": (type(svc._window).__name__ if svc._window is not None
                             else "没接（换窗口大小那根线给不了）"),
            "cdp": os.environ.get("SITEFORGE_CDP_BIN") or os.environ.get("CDP_PATH"),
            "out_dir": svc._out_dir,
            "jobs": len(svc._jobs),
            "steps": {k: STEP_SAY[k] for k in NODES},
        }

    @api.post("/run", status_code=202)
    def run(body: RunRequest) -> dict:
        return svc.start(body)

    @api.get("/job/{job_id}")
    def job(job_id: str) -> dict:
        try:
            return svc._view(job_id)
        except KeyError:
            raise HTTPException(status_code=404,
                                detail="没这个任务：%s（服务里没有它，checkpoint 里也没有）。"
                                       "要么 id 写错了，要么它是在**另一个** saver 上跑的 —— "
                                       "状态住在 saver 里，不在这个进程里（R-19）。" % job_id)

    @api.post("/job/{job_id}/reply")
    def reply(job_id: str, body: ReplyRequest) -> dict:
        try:
            return svc.reply(job_id, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="没这个任务：%s。" % job_id)

    @api.post("/job/{job_id}/reopen")
    def reopen(job_id: str, body: ReopenRequest) -> dict:
        try:
            return svc.reopen(job_id, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="没这个任务：%s。" % job_id)

    api.state.service = svc
    return api


#: uvicorn 的入口（`entrypoint.sh` 里 `agent.service:app`）。
#: ⚠️ 导入这个模块**不碰**数据库：saver 是第一次真要用的时候才建的（`Checkpointer`）。
app = create_app(window=bit_window_from_env())
