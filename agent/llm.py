"""LLM 客户端 + 工具循环。

⚠️ **spike 脚手架**（Task 1, 2026-09-17）：这个文件存在的目的是**量一条能力** ——
LLM 能不能稳定地「调 `observe` → 读结果 → 决定下一步 → 再调」，而不是凭常识编一个答案。
规格 §1.1 记着上一条路（规则折叠）正是死在盲猜页面上，所以这条能力是本设计的生死判据。

**它刻意做得很薄**：没有兜底、没有「模型忘了调工具就提醒它」的补救 ——
那会把要量的东西量没。默认状态下模型什么样，这里就如实记什么样。
（★ 2026-10-15 加的**长退避重试**是唯一的例外：它只治「上游那一刻没空」，
一个字都不碰「模型答什么」—— 见 `_worth_waiting` 那一节。）

对外只有一个函数，见 `run_tool_loop`。
"""

from __future__ import annotations

import contextvars
import json
import os
import time
from typing import Any, Callable, Protocol

from . import tools

DEFAULT_MODEL = os.environ.get("SPIKE_MODEL", "deepseek-v4-flash")
#: ⚠️ **这一格故意没有默认值**（2026-09-23，用户点名：「想法是都要走我 test.sh 那个配置的」）。
#: 原来这里兜底 `https://api.deepseek.com` —— 那是一条**静默**的路：env 一旦没带上
#: （换机器 / `docker restart` 没重建 / 谁手滑删了一行），请求就打到**别处**去了，
#: 而屏幕上与「模型答得不好」长得一模一样。兜底一个地址 = 把「配错了」变成「看不出来」，
#: 正是这个项目从头到尾在治的那类失败。现在：没给就**当场报错并点名是哪个变量**
#:（见 `client()`），**一个请求都不发出去**。
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()

# reasoning 模型（deepseek-v4-*）的**思考 token 也算在 max_tokens 里**。
# 实测：max_tokens=20 问一句 "say OK" → content 为空、20 token 全被 reasoning 吃掉
# （`finish_reason: "stop"`、`completion_tokens_details.reasoning_tokens: 20`）。
# 2026-09-16 那次「deepseek-v4-pro 在 800 预算下看图返回空」是同一个坑。
# 所以这里的默认预算必须**远大于**答案本身需要 —— 它是「思考 + 答案」的总预算。
#
# **C1（计划二约束）：必须 ≥ 12000。** 计划一 spike 的预算矩阵实测：
#   · 4000 那一档 1/8 次最终答案是**空**的（`q2-rep1`：`reasoning_tokens = 4000 =
#     completion_tokens = max_tokens`，`content` 长度 0，而 `finish_reason` 是**正常**
#     的 `length`，不是异常）—— 空答案看起来跟「模型说没有」一模一样；
#   · 同一问句提到 12000 复跑 5/5 正常，`reasoning_tokens` 峰值 7164。
# 所以别往下调：那不是省钱，是把已经验出来的能力又削掉。
# （spike 报告 §5 第 5 条原写「默认 ≥8k」，低于这条约束 —— 以 C1 的 12000 为准。）
DEFAULT_MAX_TOKENS = 12000


def _budget_from_env(default: int = DEFAULT_MAX_TOKENS) -> int:
    """预算可以由环境变量覆盖（`SPIKE_MAX_TOKENS`）—— spike 的预算矩阵靠它复现，
    与上面 `SPIKE_MODEL` / `SPIKE_CDP_BIN` 是同一套约定。

    ⚠️ 写错了（不是数字、不是正数）就**用默认值**：这个方向是安全的
    （默认 ≥12000；反过来「静默把预算压小」正是 C1 要防的那条空答案的路）。
    """
    raw = os.environ.get("SPIKE_MAX_TOKENS", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


DEFAULT_MAX_TOKENS = _budget_from_env(DEFAULT_MAX_TOKENS)


class Dispatch(Protocol):
    """工具名 → 可调用对象。参数用 kwargs 传，返回值会被 json 序列化后回给模型。"""

    def __call__(self, name: str, arguments: dict) -> Any: ...


#: 插进去的那条消息的抬头：先点名**这是谁说的**（人），再逐字贴他的话。
STEER_HEAD = "人在你探路的时候插了一句话，下面是他的原话（逐字）："
#: 那条消息**必须带**的那句提醒（设计注 §3.4 的「已知风险」，**原话**）。
#:
#: 为什么非有它不可：探路那道门上**没有 `done()`** —— 「不调工具」= 它宣布讲完了
#: ⇒ `stop_reason = model_done` ⇒ 图会认成「**探路走完了**」（R11）。
#: 人的一句话很容易被模型读成「话题结束了」；这句是**当面**把那个误读堵回去
#: （背面那一半在 `browser_agent`：真发生了就往 `journey.notes` 追一句人话）。
STEER_REMINDER = "这是人插的话，接着探，别把它当成收尾"


def steer_message(text: str) -> dict:
    """把人插的那句话包成一条 `user` 消息（循环里插进对话的那条**就是它**）。

    一字不改地贴他的话（`text` 原样在中间那行），前后各一句**我们加的**：
    前面那句说清**谁在说**，后面那句说清**这不是收尾**。
    """
    return {"role": "user", "content": "%s\n%s\n%s" % (STEER_HEAD, text, STEER_REMINDER)}


class SteerLine(Protocol):
    """「人的话」那根线（Task 9）—— `steer=` 收的就是它。

    `__call__` 是**必须**的那一半：循环在**每一次模型调用之前**问一次，回一句非空的话就
    插进这一轮。后两个是**可选**的那一半（服务那一侧实现了它们）：那一次调用**有结果之后**
    回填一次 —— **「已经交给它了」与 `delivered` 都等到那一轮真的发出去了才落**。

    ⚠️ 为什么非要把「放进了消息」与「发出去了」分开（复审 F2）：「放进消息」是**可证的**
    （我们确实 append 了），「它看到了」不可证（R6 的原话）；而 `create` 可能在发出**之前**
    就被拦下（人喊停）、也可能自己炸（网络 / 4xx）—— 那时候模型一个字都没看到，
    再说「已经交给它了」就是一句**假话**，而且那句话会跟着被记成「送到了」
    （`/again` 于是不再带上它 —— 人的话两头都没有了）。
    """

    def __call__(self) -> str | None: ...

    def went_out(self) -> None: ...

    def missed(self) -> None: ...


def _tell_steer(steer, *, went_out: bool) -> None:
    """把「这一轮到底发出去没有」告诉那根线（`SteerLine` 的**可选**那一半）。

    ⚠️ 为什么只有**这里**分得清：`create` 就是这一次调用的全部 —— 它**返回** = 请求发出去了
    （模型看到了）；它**抛** = 没发出去（被那道闸拦下、或者调用自己炸了）。这两件事在这一个
    `try` 里干净利落，别处都分不出（循环的调用方只看见「循环炸了 / 没炸」，
    看不出炸在发出去**之前**还是之后）。
    ⚠️ 那根线没有这两个方法（测试里那些只回一句话的 lambda）⇒ **什么也不做** = 默认那条路。
    """
    hook = getattr(steer, "went_out" if went_out else "missed", None)
    if hook is not None:
        hook()


# ── 模型这一跳挂了的**现场**（★ 2026-10-14）─────────────────────────────────
#
# 现场：8 趟 job 被 `nginx 504 Gateway Time-out` 打死（8099 五趟 / 8100 三趟，
# 10-09 → 10-14），而落进 `__error__` 的**只有一页 nginx HTML** —— 没有耗时、
# 没有第几次尝试、没有请求多大。于是三个假设**一个都证不了、也否不了**：
#
#   · 生成太久？—— 拿真网关实测：4000 tokens 输出只要 **17 秒**。
#   · 输入太大？—— 17.3 万 tokens 输入只要 **2.9 秒**。
#   · SDK 在默默重试？—— `OpenAI(base_url=…, api_key=…)` 是裸着建的 ⇒ `max_retries=2`
#     藏在库里，而「闸按下 → 约 180 秒 → 504」**恰好**是 3 × 60（nginx 的默认读超时）。
#
# 三个都**没量到**，因为这三格一个都没记。所以这一段先补**量具**，不动修法：
# 下一次 504 自己会说是哪一类。**修法（超时值 / 走流式 / 网关那层 nginx）等这批数。**
#
# ⚠️ 这两个默认值**就等于 SDK 的默认值** —— 这一刀**不改变行为**，只是别再让它藏在库里。

#: 一次模型调用的读超时（秒）。= `openai` SDK 的默认。env：`SITEFORGE_LLM_TIMEOUT`。
DEFAULT_TIMEOUT_SECONDS = 600.0
#: SDK 自己重试几次。= `openai` SDK 的默认。env：`SITEFORGE_LLM_RETRIES`。
DEFAULT_MAX_RETRIES = 2


def _positive_env(name: str, default):
    """env 里的一个非负数；读不出来 / 是负数 ⇒ **留默认值**（与 `SPIKE_MAX_TOKENS` 同一条口径）。

    ⚠️ `0` 是**合法值**（`SITEFORGE_LLM_RETRIES=0` = 「别重试」正要用它）——
    所以判的是 `< 0`，不是「非正数」。
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < 0:
        return default
    if isinstance(default, int):
        return int(v) if v == int(v) else default
    return v


def timeout_seconds() -> float:
    return float(_positive_env("SITEFORGE_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))


def max_retries() -> int:
    return int(_positive_env("SITEFORGE_LLM_RETRIES", DEFAULT_MAX_RETRIES))


# ── ★ 2026-10-15：上游抖一下，**等一等再打** ──────────────────────────────
#
# 现场：2026-10-15 09:25–09:38，8099 上四趟 job（charmingtalks ×3、gizmodo ×1）
# **全死在第一个模型调用**上 —— 上游回 503「Service is too busy」，SDK 那两次重试
# 在 **2.2 秒**里打完、全部被拒 ⇒ `ModelCallFailed` ⇒ 整趟 `failed`。
# 而重试**只有那一层**：`graph.py` 里没有任何 `retry_policy`（节点抛异常不重试），
# 服务也不会自动重起（人得手按「重新来一遍」，而那是**新的一趟、从零走**）。
# ⇒ 「等一会儿再试」这件事，**没有任何一层在做**：上游抖两秒，前面十几分钟探好的路一起报废。
#
# 这一层**只治「上游那一刻没空」**，一个字都不碰「模型答什么」——
# 所以它不违反这个文件开头那句「刻意做得很薄」（那说的是不许兜底模型的判断）。

#: 每一次长重试之前**等几秒**。这张表**就是全部的重试预算**：3 次、一共 85 秒。
#: env：`SITEFORGE_LLM_BACKOFF`（逗号分隔的秒数；`off` = 整个关掉）。
DEFAULT_BACKOFF_SECONDS = (5.0, 20.0, 60.0)

#: 值得等一等再打的那几档 HTTP 状态码。
#: - 408/409/425/429：**明确是「现在不行」**（超时 / 冲突 / 太频繁）；
#: - 500/502/503/504：上游或中间那层自己出问题。
#: ⚠️ 其余 4xx **一律不等**：密钥错 / 参数错 / 模型名错，等多久都是同一个结果。
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def backoff_seconds() -> tuple:
    """长重试的退避表（秒）。读不出来 / 有负数 ⇒ **留默认值**（与上面两个旋钮同一条口径）；
    `off`（不分大小写）⇒ **空表 = 整个关掉**（这一层是新加的，要能一刀切回去）。"""
    raw = str(os.environ.get("SITEFORGE_LLM_BACKOFF", "") or "").strip()
    if not raw:
        return DEFAULT_BACKOFF_SECONDS
    if raw.lower() in ("off", "none"):
        return ()
    got = []
    for piece in raw.split(","):
        try:
            secs = float(piece.strip())
        except ValueError:
            return DEFAULT_BACKOFF_SECONDS
        if secs < 0:
            return DEFAULT_BACKOFF_SECONDS
        got.append(secs)
    return tuple(got)


def _worth_waiting(exc) -> bool:
    """这个错**值不值得等一等再打**。

    只认**结构化的证据**（HTTP 状态码 / SDK 那两个连接类异常），**不认字样** ——
    拿消息里有没有「503」去猜的话，我们自己的代码缺陷（消息里恰好带这几个数字）
    也会被当成上游抖动，一路等下去。那正是「把真错拖成慢错」。
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and code in RETRYABLE_STATUS:
        return True
    from openai import APIConnectionError, APITimeoutError   # 与 `client()` 同一条：用时才导
    return isinstance(exc, (APIConnectionError, APITimeoutError))


def _long_retry_note(tries: int, waited: float) -> str:
    """长重试那一段（**没等过就是空串** —— 不留一句「等了 0 秒」的噪声）。

    为什么非要写出来：读报告的人得能分开「上游一直没空」与「我们一次都没等」——
    这两件事在 `__error__` 里长得一模一样，而处置完全不同。
    """
    if not tries:
        return ""
    return ("\n  · 长重试 %d 次、一共等了 %.1f 秒（上游一直没空；退避表见 "
            "`SITEFORGE_LLM_BACKOFF`）" % (tries, waited))


class ModelCallFailed(RuntimeError):
    """模型这一跳挂了 —— 但**带着现场**（耗时 / 发出去几次 / 旋钮 / 原错）。

    ⚠️ 为什么要单独一个类：落进 job 状态的只有 `str(exc)` —— 原样往上抛的话，
    `__error__` 里就只剩一页 nginx HTML，而那三格**正是分辨那三个假设的唯一依据**。
    原错挂在 `__cause__` 上，**一个字节没丢**（另见 `client()` 那段）。
    ⚠️ 人喊停（`_Stop`）**不走这里**：它是 BaseException 而不是 Exception，
    包装它 = 把「人按了停」变成「模型挂了」，屏幕上两件事长得一样而意思相反。
    """


#: 这一次调用发出去的 HTTP 请求 —— 只在**这一次** `create()` 前后有效。
#: 用 `ContextVar` 而不是模块级列表：job 跑在**各自的工作线程**上，两条线不许串账。
_ATTEMPTS: contextvars.ContextVar = contextvars.ContextVar("siteforge_llm_attempts", default=None)
#: 长期复用的 httpx client（**每次调用新建一个 = 漏连接池**；而它的钩子只认当前这一次）。
_HTTP = None


def _note_attempt(request, box: list) -> None:
    """记一次 HTTP 请求。SDK 每次重试 = 一次新请求，而**它自己把第几次打在头上**
    （`x-stainless-retry-count`）—— 读它比自己数更可信：那就是 SDK 认的那一次。
    老 SDK 不打这个头 ⇒ 记 `None`，**照样计数**（「几次」仍然量得到）。
    """
    box.append({"retry_count": request.headers.get("x-stainless-retry-count"),
                "body_chars": len(request.content or b"")})


def _http_client():
    """一个**长期复用**的 httpx client，挂一个把请求记进 `_ATTEMPTS` 的钩子。"""
    global _HTTP
    if _HTTP is None:
        import httpx

        def _hook(request):
            box = _ATTEMPTS.get()
            if box is not None:
                _note_attempt(request, box)

        _HTTP = httpx.Client(event_hooks={"request": [_hook]})
    return _HTTP


def format_call_context(attempts: list, seconds: float, model: str, messages: list) -> str:
    """这一次调用的现场 → 一段人话（**要进 `__error__`**，所以都在消息里）。"""
    n = len(attempts)
    if not n:
        sent = "**一个都没发出去**（连不上 / 被拦下 —— 与「发出去又超时」不是一回事）"
    else:
        counts = [str(a.get("retry_count")) for a in attempts]
        sizes = sorted({a.get("body_chars") or 0 for a in attempts})
        sent = ("%d 个 HTTP 请求（SDK 的重试计数 %s；请求体 %s 字节）"
                % (n, "/".join(counts), "/".join(str(s) for s in sizes)))
    chars = sum(len(str((m or {}).get("content") or "")) for m in messages)
    return ("耗时 %.1f 秒；发出去 %s；%d 条消息（正文约 %d 字符）；模型 %s；"
            "这一次客户端的 timeout=%gs、max_retries=%d"
            % (seconds, sent, len(messages), chars, model,
               timeout_seconds(), max_retries()))


def client():
    """按 env 建 OpenAI 兼容客户端。key 只从 env 读，**不落任何文件**。"""
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 没设 —— **花钱的就是这把 key**，它只在**服务进程的环境**里配：\n"
            "  ① 给启动命令加一句 `OPENAI_API_KEY=<你的 key>`（要换线路再加 `OPENAI_BASE_URL`、"
            "要换模型加 `SPIKE_MODEL`）；\n"
            "  ② **重启服务**（key 每次调用现读，但进程的 env 只有重启才变）；\n"
            "  ③ 核对：`sha256(<key>)` 的前 12 位 —— 与你要用的那把对得上才算换成了。\n"
            "⚠️ 值别打进命令行 / 别贴对话：从环境变量或 `read -rs` 里给它（见运营手册 §8）。"
        )
    base = DEFAULT_BASE_URL
    if not base:
        raise RuntimeError(
            "OPENAI_BASE_URL 没设 —— **不许静默挑一个地址**：这一格决定请求打到哪个网关"
            "（也就是花谁的钱、走哪条线路），猜一个就是你最不希望的那种「看起来在跑」。\n"
            "  ① 给启动命令加 `OPENAI_BASE_URL=<你那个网关，形如 https://…/v1>`（与 `OPENAI_API_KEY`"
            "同一处 —— 别打进命令行、别贴对话）；\n"
            "  ② **重启服务**（地址每次调用现读，但进程的 env 只有重启才变）——⚠️ 容器是把 env 烤进"
            "**创建**那一刻的 ⇒ 改完要**重建**（`docker rm -f <容器名>` 再起），光 `docker restart`"
            "不重读任何配置；\n"
            "  ③ 核对：`docker exec <容器名> printenv OPENAI_BASE_URL` —— 与你要的那条对得上才算换成了。\n"
            "（这一格**故意没有默认值**：兜一个官方地址会让「env 没带上」变成「静默打到别处」，"
            "而那件事在这块屏幕上跟「模型答不好」长得一样，没人看得出来。）")
    #: ★ 2026-10-14：**这两格显式写出来**（原先裸着建 ⇒ 两个值都藏在 SDK 默认里，
    #: 而「查 504 的那三格」里有两格就是它们）。值**与 SDK 默认相同** ⇒ 行为不变；
    #: env 能拧（`SITEFORGE_LLM_TIMEOUT` / `SITEFORGE_LLM_RETRIES`）⇒ 调参不必改代码。
    #: `http_client` 那一个是**量具**：SDK 每次重试都过它 ⇒ 「发出去几次」量得到。
    return OpenAI(base_url=base, api_key=key,
                  timeout=timeout_seconds(), max_retries=max_retries(),
                  http_client=_http_client())


def as_dict(chunk) -> dict:
    """SDK 的 chunk 对象 / 朴素 dict → dict。读流的那两处**走同一个口**，不许各写一份。"""
    return chunk if isinstance(chunk, dict) else chunk.model_dump()


def delta_content(chunk) -> str:
    """一片 chunk 里模型**说的话**（拼起来才是这一轮的全部）。

    ⚠️ 只读 `delta.content` —— `reasoning_content` 是**思考**不是答案（与 `read_stream` 同一条口径）。
    给 `browser_agent._RoundGate` 用：那一层**每轮都在场**，模型的叙述归它记
    （人被打断时 `run_tool_loop` 的 records 会随异常丢掉）；而它不该自己再写一份解析
    —— 两份解析迟早会漂。
    """
    out: list = []
    for ch in (as_dict(chunk).get("choices") or []):
        said = ((ch or {}).get("delta") or {}).get("content")
        if said:
            out.append(str(said))
    return "".join(out)


def read_stream(chunks) -> dict:
    """把一股流拼回**一次 reply** 的形状（朴素 dict）。

    ★ 2026-10-14 为什么要走流式：`llm.3tkj.cn` 前面那层 nginx 读超时 **60 秒**
    （实测：60.1 秒 → 504、42.8 秒 → 200），而**非流式在生成完成之前一个字节都不吐**
    ⇒ 只要这一次生成超过 60 秒就被掐断；SDK 再默认重试两次 ⇒ **3 × 60 ≈ 180 秒**
    后整个 job 死（8 趟实测，全死在生成最长的 `draft`）。流式**一秒多就开始吐字节**
    （实测首字节 1.3 秒）⇒ 那条「读」的超时**永远不触发**。

    形状照**网关实测**（不是照文档）：

      · `content` 一片一片来，要拼；
      · `tool_calls` 按 `index` 分股 —— `id` 与 `function.name` **只在第一片**上，
        `function.arguments` 是**碎片**，按股拼（拼错 = 模型调不动工具）；
      · 收尾那一片**只带 `usage`、`choices` 是空数组** —— 按 `choices[0]` 硬取会 IndexError；
      · 要 `usage` 得在请求里点名 `stream_options={"include_usage": True}`（见 `run_tool_loop`）。

    ⚠️ `reasoning_content`（deepseek 那一族）**不进 `content`** —— 与非流式那条路一致：
    它是思考不是答案，它的量在 `usage.completion_tokens_details.reasoning_tokens` 里。
    """
    content: list = []
    calls: dict = {}
    finish = None
    usage = None
    for chunk in chunks:
        d = as_dict(chunk)
        if d.get("usage"):
            usage = d["usage"]
        if said := delta_content(d):
            content.append(said)
        for ch in (d.get("choices") or []):
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            delta = ch.get("delta") or {}
            for piece in (delta.get("tool_calls") or []):
                idx = piece.get("index")
                slot = calls.setdefault(0 if idx is None else int(idx),
                                        {"id": "", "name": "", "arguments": ""})
                if piece.get("id"):
                    slot["id"] = piece["id"]
                fn = piece.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    return {"content": "".join(content),
            "tool_calls": [calls[k] for k in sorted(calls)],
            "finish_reason": finish,
            "usage": usage}


def run_tool_loop(
    system: str,
    user: str,
    tool_specs: list[dict],
    dispatch: Dispatch,
    max_rounds: int = 8,
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    steer: Callable[[], str | None] | None = None,
    _client=None,
) -> list[dict]:
    """跑一轮工具循环，返回**每一轮**的记录。

    一条「轮」= 模型的一次回复 + 由它触发的所有工具结果。返回的每条 dict：

        {
          "round":      1,                   # 从 1 开始
          "content":    "...",               # 模型的原话（可能为空 —— reasoning 吃满就是空）
          "tool_calls": [                    # 空列表 = 模型**没调工具**直接说话了
             {"id", "name", "args_raw", "args", "result", "error", "elapsed_ms"}
          ],
          "usage":      {...},               # 这一轮的 token 账（含 reasoning_tokens）
          "elapsed_ms": 1234,
          "finish_reason": "tool_calls" | "stop" | ...,
          "messages":   [...],               # 追加进对话的原样 message（便于事后重放）
          "steered":    "…" | None,          # 这一轮**人插的那句话**（没有就是 None）
        }

    **为什么返回每一轮而不是只返回最终答案**：这个 spike 要量的不是「答对没有」，
    而是「它**什么时候开始瞎编**」—— 那只能从逐轮的 tool_calls 序列里读出来。

    ⚠️ 刻意**不**做的事：模型不调工具直接答时，这里**不**回一句「请用工具」把循环续上。
    那正是要量的失败模式，接住了就看不见了。

    `steer`（Task 9）：**每一次模型调用之前**问它一次「人刚说了什么吗」——回了非空的一句，
    就把它包装成一条 `user` 消息**插在这一次调用前面**（`steer_message`），
    于是**这一轮**它就看到了；那一轮的 record 里记 `steered`（**人自己那句话**，
    不带包装 —— `messages` 里那条才是发出去的全文）。
    传 `None`（默认）⇒ 一次都不问、一个消息都不插：**这条路与今天逐字节相同**。
    ⚠️ 插进去的那句话里带着 `STEER_REMINDER`（那道门上没有 `done()`，「不调工具」=它宣布
    讲完了 —— 这一条是**当面**堵那个误读；真发生了由 `browser_agent` 记一句人话，见 R11）。
    ⚠️ 这一次调用**有结果之后**还要回填一次（`_tell_steer`）：返回 ⇒ `went_out()`、
    抛 ⇒ `missed()` —— 那根线靠它才知道「到底发出去了没有」（`SteerLine`，复审 F2）。
    """
    cli = _client or client()
    model = model or DEFAULT_MODEL

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    rounds: list[dict] = []

    for i in range(1, max_rounds + 1):
        # 人的话（Task 9）：**在一次调用之前**插进去 —— 插在之后这一轮就看不到它，
        # 而「下一轮就看到了」正是这条通道对那个人唯一的一句承诺。
        steered = None
        if steer is not None:
            said = str(steer() or "").strip()
            if said:
                messages.append(steer_message(said))
                steered = said
        t0 = time.time()
        #: ★ 2026-10-14：**这一次调用发出去几个请求**记在这儿（见 `format_call_context`）。
        #: ⚠️ 它在**所有**长重试之间共用一份 —— 最后报出来的「几个 HTTP 请求」是**这一次调用
        #: 一共打出去多少**（含每次尝试里 SDK 自己那几下），而不是最后一下的。
        box: list = []
        _tok = _ATTEMPTS.set(box)
        waits = backoff_seconds()
        tries, waited = 0, 0.0
        try:
            while True:
                try:
                    #: ★ 2026-10-14：**流式** —— 治「60 秒 nginx 读超时」（见 `read_stream`）。
                    #: `include_usage` 不点名就要不到 token 账（那是预算那条线 C1 的依据）。
                    #: ⚠️ 流**在这里就消费掉**：中途被掐（nginx 切断）也落在同一个 `try` 里，
                    #: 照样被包成带现场的 `ModelCallFailed`。
                    reply = read_stream(cli.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=tool_specs or None,
                        max_tokens=max_tokens,
                        stream=True,
                        stream_options={"include_usage": True},
                    ))
                    break
                except BaseException as exc:       # noqa: BLE001 —— `_Stop`（人喊停）也是 BaseException
                    #: ⚠️ 人喊停（`_Stop`）是 **BaseException 而不是 Exception** ——
                    #: **原样往上走，连等都不等**（等 85 秒再抛，屏幕上就是
                    #: 「我按了停，它还在那儿」——那条承诺当场变假话）。
                    if not isinstance(exc, Exception):
                        raise
                    #: ★ 2026-10-15：**认得出来的瞬态错误 ⇒ 等一等再打**（`_worth_waiting`）。
                    #: 这一支是那段现场（09:25 四趟全死在 503 上）唯一的治法：
                    #: SDK 那两次重试是**同一瞬间**打完的，对「上游这一刻没空」等于没重试。
                    #: ⚠️ 重试的只是**这一次调用**（messages 一个字没动）——
                    #: 人插进来的那句话、工具结果、轮次账都不受影响。
                    if tries < len(waits) and _worth_waiting(exc):
                        pause = waits[tries]
                        tries += 1
                        waited += pause
                        time.sleep(pause)
                        continue
                    # 这一次调用**没发出去** —— 那根线要据此把「已经交给它了」收回去（`missed`）。
                    _tell_steer(steer, went_out=False)
                    #: ★ 2026-10-14：**带着现场往上抛** —— `__error__` 里只留得住 `str(exc)`，
                    #: 而「耗时 / 发出去几次 / 旋钮」正是分辨那三个假设的唯一依据。
                    #: ★ 2026-10-15 再补一格：**长重试了几次、等了多久**（没等过就是空串）。
                    raise ModelCallFailed(
                        "模型这一跳挂了（%s）：%s\n  · %s%s"
                        % (type(exc).__name__, str(exc)[:400],
                           format_call_context(box, time.time() - t0, model, messages),
                           _long_retry_note(tries, waited))) from exc
        finally:
            _ATTEMPTS.reset(_tok)
        if steered:
            # 真的发出去了（模型看到这一轮了）—— 这才算「交给它了」（复审 F2③）。
            _tell_steer(steer, went_out=True)
        elapsed_ms = int((time.time() - t0) * 1000)

        content = reply["content"]
        usage = reply["usage"]
        raw_calls = reply["tool_calls"]
        record: dict = {
            "round": i,
            "content": content,
            "tool_calls": [],
            "usage": usage,
            "elapsed_ms": elapsed_ms,
            "finish_reason": reply["finish_reason"],
            "messages": [],
            "steered": steered,
        }

        # 原样把 assistant message 追加进对话（OpenAI 要求 tool 消息必须紧跟带
        # tool_calls 的 assistant 消息，缺了它下一轮会 400）。
        assistant_msg: dict = {"role": "assistant", "content": content}
        if raw_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"]},
                }
                for c in raw_calls
            ]
        messages.append(assistant_msg)
        record["messages"].append(assistant_msg)

        for c in raw_calls:
            name = c["name"]
            args_raw = c["arguments"]
            call: dict = {"id": c["id"], "name": name, "args_raw": args_raw, "args": None,
                          "result": None, "error": None, "elapsed_ms": None}

            t1 = time.time()
            try:
                args = json.loads(args_raw) if args_raw and args_raw.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError(f"arguments 不是对象，是 {type(args).__name__}")
                call["args"] = args
            except Exception as e:  # noqa: BLE001 —— 解析失败本身就是一条要记的证据
                call["error"] = f"arguments 解析失败: {e}"
                args = {}
            else:
                try:
                    call["result"] = dispatch(name, args)
                except Exception as e:  # noqa: BLE001
                    call["error"] = f"{type(e).__name__}: {e}"
            call["elapsed_ms"] = int((time.time() - t1) * 1000)

            # 这一步的 content **不一定是字符串**：带图的工具（`screenshot`）回的是
            # parts（证据文本 + `image_url` 的 data URL）—— 以前一律 `json.dumps`，
            # 于是那张图以 base64 **文本**的身份进上下文，模型看不见图却付了全部代价。
            # 形状由 `tools.tool_message_content` 定（认工具名的地方只有它一处）；
            # 别的工具走的还是同一串 JSON 文本，逐字节不变。
            payload = (
                tools.tool_message_content(name, call["result"])
                if call["error"] is None
                else json.dumps({"error": call["error"]}, ensure_ascii=False)
            )
            tool_msg = {"role": "tool", "tool_call_id": c["id"], "content": payload}
            messages.append(tool_msg)
            record["messages"].append(tool_msg)
            record["tool_calls"].append(call)

        rounds.append(record)

        # 模型没调工具 → 它选择了「直接说话」。**这就是要量的那件事**，记下来然后停。
        if not raw_calls:
            break
        # 调了 done() → 它自己宣布结束。
        if any(c["name"] == "done" for c in record["tool_calls"]):
            break

    return rounds


def summarize(rounds: list[dict]) -> dict:
    """把逐轮记录压成一行账：几轮、几次工具调用、花了多少 token / 时间。"""
    calls = [c for r in rounds for c in r["tool_calls"]]
    usage_tot: dict[str, int] = {}
    for r in rounds:
        for k, v in (r["usage"] or {}).items():
            if isinstance(v, int):
                usage_tot[k] = usage_tot.get(k, 0) + v
        det = (r["usage"] or {}).get("completion_tokens_details") or {}
        if isinstance(det, dict) and det.get("reasoning_tokens"):
            usage_tot["reasoning_tokens"] = usage_tot.get("reasoning_tokens", 0) + det["reasoning_tokens"]
    return {
        "rounds": len(rounds),
        "tool_calls": len(calls),
        "calls_by_name": {n: sum(1 for c in calls if c["name"] == n) for n in {c["name"] for c in calls}},
        "errors": [c["error"] for c in calls if c["error"]],
        "model_spoke_without_tools": bool(rounds) and not rounds[-1]["tool_calls"],
        "elapsed_ms": sum(r["elapsed_ms"] for r in rounds) + sum(c["elapsed_ms"] or 0 for c in calls),
        "usage": usage_tot,
    }
