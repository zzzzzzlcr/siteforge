"""Task 1 spike：LLM 能不能撑起工具循环。

⚠️ **默认 skip** —— 它打真 LLM（要 key、要钱、要网络），而且打真站，
不能进常规 CI。跑它：

    export OPENAI_API_KEY=$(docker exec auto-llm-script printenv OPENAI_API_KEY)
    RUN_LLM=1 python3 -m pytest tests/test_tool_loop.py -v -s

前置（本文件**不**替你起浏览器 —— 那是「另一个 agent 正在用 9222」那条约束的下游）：
自起一个 headless Chrome 并把页面推到漏斗页：

    google-chrome --headless=new --remote-debugging-port=9333 \\
        --user-data-dir=/tmp/spike-chrome-profile --no-sandbox --window-size=1280,636 about:blank
    /tmp/cdp-spike --port 9333 navi https://compareinsulation.io/article-1-c
    /tmp/cdp-spike --port 9333 click --selector a.cb

⚠️ 窗口高度是**故意的**：vh≈493，而 `Get Started` 的 top=592 →
**在折线下**（`above_fold: false`、`occluded_by: "offscreen"`）。这正是要量的那件事：
模型是「看了页面说它在下面」，还是「凭常识说 Get Started 当然看得见」。

本文件只**度量并打印原始证据**，不做「答对了就绿」的断言 ——
spike 的产出是报告里的结论，不是 CI 里的绿灯。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent import llm, tools  # noqa: E402

RUN = os.environ.get("RUN_LLM") == "1"
DUMP_DIR = pathlib.Path(os.environ.get("SPIKE_DUMP_DIR", "/tmp/siteforge-spike"))

pytestmark = pytest.mark.skipif(
    not RUN, reason="打真 LLM 的 spike，默认 skip；RUN_LLM=1 才跑"
)

# 三轮问题。**原话**照抄 brief —— 不改写、不加提示、不加 few-shot。
Q1 = "这一页有没有一个叫 `Get Started` 的按钮？用工具确认，不要猜。"

Q2 = (
    "请分三步回答，每一步都要用工具核实："
    "① 这一页上 `Yes` 和 `No` 这两个大按钮分别是什么？"
    "② `Get Started` 按钮在页面上的垂直坐标（top）是多少，视口高度（vh）是多少，哪个大？"
    "③ 因此 `Get Started` 现在算「在屏幕内」还是「在屏幕外」？"
)

Q3 = "这一页有哪些可以填的输入框？你会填哪些？"

# 第 4 问是**唯一能逼出 ≥3 次真实工具调用**的问法：`observe()` 无参数，
# 一次快照就把整页交出去了 —— 只有「要多份快照做对照」这件事非多轮不可。
# 而这不是玩具题：写 py 之前先确认页面模型稳不稳，正是规格 §10 扰动自测的日常动作。
Q4 = (
    "在给这一页写自动化脚本之前，我要先确认这个页面模型是稳定的。"
    "请连续观察三次页面，每次报告 actions 数量和 url；然后告诉我三次结果是否一致。"
)

SYSTEM = "你是一个浏览网页的助手，回答关于当前页面的事实问题。"


def _current_url() -> str:
    p = subprocess.run(
        [tools.DEFAULT_CDP_BIN, "--port", str(tools.DEFAULT_CDP_PORT), "eval",
         "location.href"],
        capture_output=True, text=True, timeout=30,
    )
    return p.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def _on_funnel_page():
    url = _current_url()
    assert tools.FUNNEL_HOST in url, (
        f"浏览器不在漏斗页上（当前 {url!r}）。先跑文件头注释里那三行前置命令。"
    )


def _run(tag: str, user: str, max_rounds: int = 6) -> dict:
    dispatch, page_log = tools.make_dispatch()
    rounds = llm.run_tool_loop(SYSTEM, user, tools.specs(), dispatch, max_rounds=max_rounds)
    result = {
        "tag": tag,
        "question": user,
        "rounds": rounds,
        "summary": llm.summarize(rounds),
        "page_log_len": len(page_log),
    }
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{tag}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _report(tag: str, res: dict) -> None:
    """把原始证据打到 stdout（-s 可见）——报告里的引用就抄这里。"""
    print(f"\n{'=' * 72}\n[{tag}] Q: {res['question']}\n{'=' * 72}")
    print(f"summary: {json.dumps(res['summary'], ensure_ascii=False)}")
    for r in res["rounds"]:
        print(f"\n-- round {r['round']}  finish={r['finish_reason']}  {r['elapsed_ms']}ms")
        if r["content"]:
            print(f"   说的话: {r['content']}")
        if not r["tool_calls"]:
            print("   ⚠️ 这一轮没有 tool_calls —— 模型直接开说了")
        for c in r["tool_calls"]:
            outcome = c["error"] if c["error"] else f"ok({len(json.dumps(c['result'], ensure_ascii=False))}B)"
            print(f"   → {c['name']}({c['args_raw'][:120]}) [{outcome}] {c['elapsed_ms']}ms")


def test_q1_is_there_a_get_started_button():
    res = _run("q1", Q1)
    _report("q1", res)
    assert res["summary"]["tool_calls"] >= 1, "模型一次工具都没调"


def test_q2_multi_round_position():
    res = _run("q2", Q2)
    _report("q2", res)


def test_q3_honeypot_fields():
    res = _run("q3", Q3)
    _report("q3", res)


def test_q4_three_consecutive_rounds():
    """≥3 轮**真实 tool_calls** 的压力测试 —— 判定里那一条的字面要求。"""
    res = _run("q4", Q4, max_rounds=8)
    _report("q4", res)
    real = sum(1 for r in res["rounds"] if r["tool_calls"])
    assert real >= 3, f"只有 {real} 轮带真实 tool_calls，没到 3 轮"


# ── 对照组：修复**没落地**时的形状 ─────────────────────────────────────────
# 另一个 agent 正在修「observe 把蜜罐当正常字段」。上面 test_q3 量的是**修复后**：
# 蜜罐在 `honeypots` 里、不在 `fields` 里，看模型读不读那个字段。
# 这里量**修复前**：蜜罐**同时**躺在 `actions` 与 `fields` 里、`stability: high`、
# 没有任何 honeypots 提示 —— 看模型**会不会自己察觉不对**。
#
# 两种形状都要记（brief 明确要求）。这个对照组不依赖任何人的 WIP：
# 它拿真 observe 的输出，按 observe.go 注释里记的修复前形状**原样搬回去**。

HONEYPOT_SELECTOR = 'input[name="company_url"]'


def _pre_fix_shape(model: dict) -> dict:
    """把真模型掰回修复前的形状（蜜罐回到 actions + fields，honeypots 清空）。"""
    import copy

    m = copy.deepcopy(model)
    hp = m.pop("honeypots", [])
    m["honeypots"] = []
    for h in hp:
        sel = h.get("selector", HONEYPOT_SELECTOR)
        m["actions"].append({
            "selector": sel, "alternates": [], "stability": "high", "text": "",
            "role": "textbox", "tag": "INPUT", "type": "text", "visible": True,
            "occluded_by": "offscreen", "shadow_depth": 0, "frame_path": ["main"],
            "bbox": [-9750, 228, 202, 24], "region": "main", "above_fold": False,
            "relative_size": 0.4, "peer_count": 1, "z_index": "auto", "contrast": "",
            "nearby_text": [],
        })
        m["fields"].append({
            "selector": sel, "alternates": [], "stability": "high",
            "label": "", "hint": "", "placeholder": "", "type": "text",
            "required": False, "shadow_depth": 0, "frame_path": ["main"],
        })
    return m


def test_q3b_control_honeypot_as_normal_field():
    """对照组：工具**没**帮它把陷阱挑出来时，它自己看不看得出来。"""
    real = tools.observe()
    doctored = _pre_fix_shape(real)
    assert doctored["fields"], "对照组构造失败：fields 里应该有那条蜜罐"
    assert not doctored["honeypots"], "对照组构造失败：honeypots 应该被清空"

    def dispatch(name: str, args: dict):
        if name == "observe":
            return doctored
        if name == "done":
            return {"acknowledged": True, "answer": args.get("answer", "")}
        raise KeyError(f"没有这个工具: {name}")

    rounds = llm.run_tool_loop(SYSTEM, Q3, tools.specs(), dispatch, max_rounds=6)
    res = {"tag": "q3b-control", "question": Q3, "rounds": rounds,
           "summary": llm.summarize(rounds), "page_log_len": 0}
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / "q3b-control.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _report("q3b-control", res)
