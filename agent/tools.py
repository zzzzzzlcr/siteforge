"""工具声明 + dispatch。

⚠️ **spike 脚手架**（Task 1, 2026-09-17）：**只给两个工具**，够判定就行。

- `observe()` —— 调 `cdp observe --json`，返回真页面的页面模型
- `done(answer)` —— 模型声明「我有答案了」

**为什么只有两个、且观察不带参数**：spike 要量的是「模型肯不肯**先看再答**」。
工具越少、参数越少，「它是看了才说的」这个结论就越干净 ——
一旦给了 `observe(selector)` 这种参数，模型答错时我们就分不清是「没调工具」还是「调了但用错参数」。

生产期的工具集是 MCP 那条路（计划二 Task 2），与这里无关。
"""

from __future__ import annotations

import json
import os
import subprocess

# 真站（2026-09-16 验过国内 IP 打得开）。发现页 → 点 `a.cb` → 漏斗页。
FUNNEL_ENTRY = "https://compareinsulation.io/article-1-c"
FUNNEL_LINK = "a.cb"
FUNNEL_HOST = "check.compareinsulation.io"

DEFAULT_CDP_BIN = os.environ.get("SPIKE_CDP_BIN", "/tmp/cdp-spike")
DEFAULT_CDP_PORT = int(os.environ.get("SPIKE_CDP_PORT", "9333"))
OBSERVE_TIMEOUT_S = 60


def observe(port: int = DEFAULT_CDP_PORT, cdp_bin: str = DEFAULT_CDP_BIN) -> dict:
    """调 `cdp observe --json`，返回解析好的 PageModel。

    **失败不吞**：非 0 退出 / 输出不是 JSON，一律抛异常 —— dispatch 会把它变成
    一条 `{"error": ...}` 的工具结果回给模型。模型看见工具报错之后**怎么办**
    本身就是这一轮要量的东西（是它自己换个办法，还是开始编）。
    """
    p = subprocess.run(
        [cdp_bin, "--port", str(port), "observe", "--json"],
        capture_output=True, text=True, timeout=OBSERVE_TIMEOUT_S,
    )
    if p.returncode != 0:
        raise RuntimeError(f"cdp observe 非 0 退出 ({p.returncode}): {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"cdp observe 的输出不是 JSON: {e}; 开头={p.stdout[:200]!r}") from e


def specs() -> list[dict]:
    """OpenAI function-calling 形态的工具声明。

    ⚠️ 描述文字**刻意只讲工具本身**（怎么调、返回什么），不讲「你该怎么用」——
    一旦写进「请务必先 observe 再回答」，量的就不再是默认状态下的能力，
    而是「我们调教之后的能力」。spike 要的是前者。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "observe",
                "description": (
                    "观察当前浏览器标签页，返回这一页的结构化页面模型（JSON）。"
                    "包含：url / title / page_text / 可动作元素 actions（带文本、选择器、坐标 bbox、"
                    "是否在首屏 above_fold、遮挡情况 occluded_by）/ 表单字段 fields / "
                    "选项组 option_groups / 遮挡物 obstructions / 被排除的陷阱元素 honeypots / "
                    "观测自身的诊断 diagnostics。无参数。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "声明你已经有答案了，结束本次任务。",
                "parameters": {
                    "type": "object",
                    "properties": {"answer": {"type": "string", "description": "你的最终答案"}},
                    "required": ["answer"],
                },
            },
        },
    ]


def make_dispatch(*, port: int = DEFAULT_CDP_PORT, cdp_bin: str = DEFAULT_CDP_BIN):
    """建一个 dispatch。

    返回 `(dispatch, log)`：`log` 是每次 observe 的原始 PageModel，
    供事后核对「模型说的话和页面事实对不对得上」—— 判定不能只信模型的转述。
    """
    log: list[dict] = []

    def dispatch(name: str, args: dict):
        if name == "observe":
            model = observe(port=port, cdp_bin=cdp_bin)
            log.append(model)
            return model
        if name == "done":
            return {"acknowledged": True, "answer": args.get("answer", "")}
        # 模型报了一个不存在的工具名 —— 这本身是要记的证据，不要静默兜底。
        raise KeyError(f"没有这个工具: {name}")

    return dispatch, log
