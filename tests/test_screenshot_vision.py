"""截图必须**到得了模型的眼睛里** —— 而不是变成一条几万字符的文本。

**缺陷是读代码定位的，不是猜的**：MCP 门上的 `screenshot` 回的是内核的 `*Shot`
（`tools/cdp/internal/screenshot.go:61` —— PNG 的 base64 在 `png_base64` 字段，
外加坐标证据 `image_px` / `viewport_css_px` / `dpr`），而 `llm.run_tool_loop`
把它 `json.dumps` 成一条 **纯文本** 的 tool message。
→ 模型**看不见图**（能力是有的：spike 实测 `deepseek-v4-flash` 数矩形 → `2`、认颜色 → `红色`），
却为这串 base64 付出一整条巨长上下文的代价。真跑那次日志：20 步里调了 5 次截图，5 次全白调。

这个文件钉四件事：

1. 到模型手里的 tool message 里**真的有图**（`image_url` + data URL），
   而且坐标证据在**同一份消息**的文本 part 里（`image_px = viewport_css_px × dpr` 那条契约
   是「点得准」的前提）；
2. 超过上限时**说出来**（不许静默丢）—— 说清「图没进来」以及为什么；
3. 别的工具**逐字节不变**（这一次改动不许碰到那条路）；
4. 账本（Journey）**只留 bytes**，base64 不落盘。

⚠️ 判据只能落在「模型**实际收到**的那条消息」上：只断言「追加了一条消息」什么都证明不了
（缺陷版本也追加消息）。所以下面那个假模型把每次 `create()` 收到的 `messages` 原样记下来。
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, llm, tools  # noqa: E402
from test_browser_agent import PAGE_LANDING, FakeLLM, _run  # noqa: E402

#: 一张**真**的 PNG（1×1 透明），不是随口编的字符串 ——
#: 「data URL 里装的是不是一张图」这件事，用假串验不出来。
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

#: 内核对 `screenshot` 的返回（`Shot` 的 JSON 形状，字段名逐个对过 `screenshot.go`）。
SHOT = {
    "png_base64": TINY_PNG_B64,
    "image_px": {"width": 2560, "height": 1600},
    "viewport_css_px": {"width": 1280, "height": 800},
    "dpr": 2.0,
}


# ─────────────────────────── 假模型（判据的落点）───────────────────────────

class SpyLLM:
    """按剧本回话的假模型，**把每次收到的东西原样留住**。

    与 `test_browser_agent.FakeLLM` 同一片 OpenAI 形状，只是这里不需要桩 MCP：
    要验的是「工具结果 → tool message」这一段，所以 dispatch 直接回那份 `Shot`。
    """

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.calls: list[dict] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        calls = [
            types.SimpleNamespace(
                id=f"call_{i}",
                function=types.SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for i, (name, args) in enumerate(turn.get("calls") or [])
        ]
        msg = types.SimpleNamespace(content=turn.get("content", ""), tool_calls=calls or None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg,
                                           finish_reason="tool_calls" if calls else "stop")],
            usage=None,
        )


def _loop(result, *, tool="screenshot", turns=None):
    """跑一轮工具循环（`result` 就是那次工具调用的原始返回），返回假模型收到的东西。"""
    fake = SpyLLM(turns or [{"calls": [(tool, {})]}, {"content": "看完了"}])
    rounds = llm.run_tool_loop("sys", "usr", [], lambda name, args: result,
                               max_rounds=4, _client=fake)
    return fake, rounds


def _tool_message(fake: SpyLLM) -> dict:
    """模型**第二次**被问时看到的那条 tool 消息（第一次它只看到 user）。"""
    assert len(fake.calls) >= 2, f"循环没走到第二轮，只问了 {len(fake.calls)} 次"
    tools_msgs = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"]
    assert tools_msgs, f"第二轮里一条 tool 消息都没有：{[m.get('role') for m in fake.calls[1]['messages']]}"
    return tools_msgs[0]


def _parts_of(msg: dict) -> list:
    content = msg.get("content")
    assert isinstance(content, list), (
        "tool 消息的 content 是一串**文本** —— 这正是缺陷本身（模型看不到图）："
        f"{str(content)[:160]}"
    )
    return content


def _image_part(parts: list) -> dict:
    images = [p for p in parts if isinstance(p, dict) and p.get("type") == "image_url"]
    assert images, f"parts 里没有 image_url（模型看到的是）：{[p.get('type') for p in parts]}"
    return images[0]


def _text_part(parts: list) -> dict:
    texts = [p for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    assert texts, f"parts 里没有 text —— 坐标证据丢了：{parts}"
    return texts[0]


# ─────────────────── 1. 图真的到模型手里（而且证据没丢）───────────────────


def test_the_model_gets_the_png_not_a_base64_wall():
    """判据：tool 消息里有一张**图**，且那串 base64 就在 data URL 里（不是当文本灌进去）。"""
    fake, _ = _loop(SHOT)
    part = _image_part(_parts_of(_tool_message(fake)))
    url = (part.get("image_url") or {}).get("url") or ""
    assert url.startswith("data:image/png;base64,"), f"不是 PNG 的 data URL：{url[:64]}"
    assert url.split(",", 1)[1] == TINY_PNG_B64, "图被改过了（或压根不是这一张）"


def test_the_coordinate_evidence_travels_in_the_same_message():
    """坐标证据（`image_px` / `viewport_css_px` / `dpr`）必须**同一条消息**里给到 ——
    模型要拿它把 observe 的 bbox 叠到图上（`image_px = viewport_css_px × dpr`）。

    另外：证据里**不许**再夹一份 base64 —— 同一份东西灌两遍是纯浪费。
    """
    fake, _ = _loop(SHOT)
    parts = _parts_of(_tool_message(fake))
    evidence = json.loads(_text_part(parts)["text"])
    assert evidence["image_px"] == SHOT["image_px"]
    assert evidence["viewport_css_px"] == SHOT["viewport_css_px"]
    assert evidence["dpr"] == SHOT["dpr"]
    assert TINY_PNG_B64 not in json.dumps(evidence), "文本 part 里又抄了一份 base64"


# ─────────────────────── 2. 太大就说出来（不许静默）───────────────────────


def test_an_oversized_screenshot_says_so_instead_of_disappearing():
    """超上限时：**没有**图，但同一条消息里**明说**没进来、以及为什么。

    静默丢是最坏的一种 —— 模型会以为自己看见了，然后照着「想象」点。
    """
    huge = {**SHOT, "png_base64": "A" * (tools.SHOT_B64_MAX + 1)}
    fake, _ = _loop(huge)
    parts = _parts_of(_tool_message(fake))
    assert not [p for p in parts if p.get("type") == "image_url"], "超上限的图还是进了上下文"
    text = _text_part(parts)["text"]
    assert "没" in text and str(tools.SHOT_B64_MAX) in text, (
        f"没说清「图没进来、上限是多少」：{text}"
    )
    evidence = json.loads(text)
    assert evidence["dpr"] == SHOT["dpr"], "证据不该跟着图一起丢"
    assert evidence["image_px"] == SHOT["image_px"]


def test_a_screenshot_that_fits_is_not_dropped():
    """反向钉子：**刚好到上限**的那张要进得去（上限是天花板，不是筛子）。"""
    just_fits = {**SHOT, "png_base64": "A" * tools.SHOT_B64_MAX}
    fake, _ = _loop(just_fits)
    _image_part(_parts_of(_tool_message(fake)))


def test_the_cap_cannot_be_quietly_widened_to_nothing(monkeypatch):
    """上限可由 env 覆盖（运维在 4K 机器上不用改代码），但**写错就往安全方向退** ——
    与 `llm._budget_from_env` 同一条规矩：坏值不许把它变成 0 / 负数。"""
    assert tools._shot_b64_max() == tools.SHOT_B64_MAX
    for bad in ("八万", "0", "-1", " "):
        monkeypatch.setenv("SITEFORGE_SHOT_B64_MAX", bad)
        assert tools._shot_b64_max() == tools.SHOT_B64_MAX, bad
    monkeypatch.setenv("SITEFORGE_SHOT_B64_MAX", "123456")
    assert tools._shot_b64_max() == 123456


# ─────────────────── 3. 别的工具逐字节不变（那条路不许被碰）───────────────────


@pytest.mark.parametrize("tool,result", [
    ("observe", PAGE_LANDING),
    ("click", {"x": 57.69, "y": 73.67, "match_count": 1}),
    ("diff", {"actionable": True}),
    ("goto", {"url": "https://example.test/funnel"}),
])
def test_every_other_tool_arrives_exactly_as_before(tool, result):
    """别的工具仍是**一条 JSON 文本**，且与改动前**逐字节相同**。"""
    fake, _ = _loop(result, tool=tool)
    msg = _tool_message(fake)
    assert msg["content"] == json.dumps(result, ensure_ascii=False), (
        f"{tool} 的结果被改了形状：{str(msg['content'])[:160]}"
    )


def test_a_screenshot_result_without_a_png_stays_text():
    """门那边没给图时（老内核 / 只回了证据）不许编一张 —— 照旧走文本那条路。"""
    fake, _ = _loop({"image_px": {"width": 1, "height": 1}, "dpr": 1})
    assert _tool_message(fake)["content"] == json.dumps(
        {"image_px": {"width": 1, "height": 1}, "dpr": 1}, ensure_ascii=False)


# ─────────────────────── 4. 账本只留 bytes（不落 base64）───────────────────────


def test_the_journal_keeps_only_the_size():
    """`Journey` 那一步的结果仍是 `{ok, elapsed_ms, bytes}` —— 几百 KB 的 base64 不落盘。"""
    step_result = browser_agent._summarize("screenshot", {}, SHOT, 42, None)
    assert step_result == {"ok": True, "elapsed_ms": 42, "bytes": len(str(SHOT))}
    assert TINY_PNG_B64 not in json.dumps(step_result)


def test_explore_hands_the_screenshot_to_the_model_and_keeps_the_journal_small(tmp_path):
    """**整条路**跑一遍（真 dispatch + 真 MCP 协议 + 假模型）：图到模型，账本里没有 base64。

    这条是给「缝」钉的：修在 `llm.py` 一处，别的层（browser_agent 的 dispatch / `_summarize`）
    都不该把结果重写一遍 —— 重写了这条就会红。
    """
    journey, fake, _ = _run(
        tmp_path,
        {"screenshot": [{"structured": SHOT}]},
        [{"calls": [("screenshot", {})]}, {"content": "页面我看见了"}],
    )
    part = _image_part(_parts_of(_tool_message(fake)))
    url = (part.get("image_url") or {}).get("url") or ""
    assert url.endswith(TINY_PNG_B64), "图上路时被改过（dispatch 那一层不许重写结果）"

    step = journey.steps[0]
    assert step["action"] == "screenshot"
    assert step["result"]["bytes"] == len(str(SHOT)), f"账本那份变了：{step['result']}"
    dumped = json.dumps({"steps": journey.steps, "notes": journey.notes}, ensure_ascii=False)
    assert TINY_PNG_B64 not in dumped, "base64 进了账本"
