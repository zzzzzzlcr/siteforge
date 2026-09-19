"""LLM 客户端 + 工具循环。

⚠️ **spike 脚手架**（Task 1, 2026-09-17）：这个文件存在的目的是**量一条能力** ——
LLM 能不能稳定地「调 `observe` → 读结果 → 决定下一步 → 再调」，而不是凭常识编一个答案。
规格 §1.1 记着上一条路（规则折叠）正是死在盲猜页面上，所以这条能力是本设计的生死判据。

**它刻意做得很薄**：没有重试、没有兜底、没有「模型忘了调工具就提醒它」的补救 ——
那会把要量的东西量没。默认状态下模型什么样，这里就如实记什么样。

对外只有一个函数，见 `run_tool_loop`。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Protocol

from . import tools

DEFAULT_MODEL = os.environ.get("SPIKE_MODEL", "deepseek-v4-flash")
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")

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
STEER_REMINDER = "这是人插的话，接着探，别把它当成收尾。"


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


def client():
    """按 env 建 OpenAI 兼容客户端。key 只从 env 读，**不落任何文件**。"""
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 没设。spike 里从容器借："
            "export OPENAI_API_KEY=$(docker exec auto-llm-script printenv OPENAI_API_KEY)"
        )
    return OpenAI(base_url=DEFAULT_BASE_URL, api_key=key)


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
        try:
            resp = cli.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_specs or None,
                max_tokens=max_tokens,
            )
        except BaseException:                      # noqa: BLE001 —— `_Stop`（人喊停）也是 BaseException
            # 这一次调用**没发出去** —— 那根线要据此把「已经交给它了」收回去（`missed`）。
            _tell_steer(steer, went_out=False)
            raise
        if steered:
            # 真的发出去了（模型看到这一轮了）—— 这才算「交给它了」（复审 F2③）。
            _tell_steer(steer, went_out=True)
        elapsed_ms = int((time.time() - t0) * 1000)

        choice = resp.choices[0]
        msg = choice.message
        usage = resp.usage.model_dump() if resp.usage else None

        raw_calls = list(msg.tool_calls or [])
        record: dict = {
            "round": i,
            "content": msg.content or "",
            "tool_calls": [],
            "usage": usage,
            "elapsed_ms": elapsed_ms,
            "finish_reason": choice.finish_reason,
            "messages": [],
            "steered": steered,
        }

        # 原样把 assistant message 追加进对话（OpenAI 要求 tool 消息必须紧跟带
        # tool_calls 的 assistant 消息，缺了它下一轮会 400）。
        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if raw_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in raw_calls
            ]
        messages.append(assistant_msg)
        record["messages"].append(assistant_msg)

        for c in raw_calls:
            name = c.function.name
            args_raw = c.function.arguments
            call: dict = {"id": c.id, "name": name, "args_raw": args_raw, "args": None,
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
            tool_msg = {"role": "tool", "tool_call_id": c.id, "content": payload}
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
