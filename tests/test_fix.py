"""修站那条路（`agent/fix.py` + `graph` 的 `MODE_FIX`）—— 拿现成的 py 改，**不重新探索**。

## 为什么单独一份（2026-09-17）

生产里 agent 最常见的用法不是「从零产出」，而是**「这一单跑挂了 → 叫 agent 去修那一份」**。
而在这条路之前，`plan.from_states()`（规格 §2.3 那个「可选口子」）写好了却**从来没被接进图里**。
这一份钉住三件：

1. 现成 py 里**判错的字段身份**会被重判（真站那个案例：`textfield_173862` 兜底填人名）；
2. 认不出的格子**原样不动**，并把「没动它」说出来（认不出就不填，绝不猜）；
3. 图这条路**真的不探索** —— 一个探索桩都不该被调用。
"""
from __future__ import annotations

import json
import pathlib
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fix, graph  # noqa: E402

#: 一份「像真站上那份」的坏产物：州那一格的名字是个不透明 id，兜底填人名。
CANDIDATE = '''
STATES = [
    {"name": "start", "when": None, "steps": [
        {"action": "form", "fill": "textfield_173862", "note": "填好了「textField-173862」",
         "target": {"text": None, "label": "textField-173862", "role": None, "near": None,
                    "selectors": ["input.MuiInputBase-input.css-mnn31"],
                    "above_fold_only": False, "frame_id": "FRAME1"}},
        {"action": "form", "fill": "mystery", "note": "填好了「textField-000000」",
         "target": {"text": None, "label": "textField-000000", "role": None, "near": None,
                    "selectors": ["input.unknown"], "above_fold_only": False, "frame_id": "FRAME1"}},
    ]},
]

FILLS = {
    'textfield_173862': {'name': 'textfield_173862', 'source': 'textfield_173862',
                         'kind': 'value', 'label': 'textField-173862',
                         'target': {"text": None, "label": "textField-173862", "role": None,
                                    "near": None, "selectors": ["input.MuiInputBase-input.css-mnn31"],
                                    "above_fold_only": False, "frame_id": "FRAME1"},
                         'fallback': [{'random': 'full_name'}]},
    'mystery': {'name': 'mystery', 'source': 'mystery', 'kind': 'value', 'label': 'textField-000000',
                'target': {"text": None, "label": "textField-000000"},
                'fallback': [{'random': 'full_name'}]},
}
'''


def _fake_config(url):
    """站方配置的替身（**不联网**）：照真站那份的形状，只留要被问到的那一块。"""
    body = {
        "props": {"pageProps": {"formSettings": {"forms": {"page_components": {
            "component_173862": {
                "component": {"id": 173862, "type": "textfield", "subtype": "",
                              "mappedQuestionId": "", "text": "What state do you live in?"},
                "input": {"placeholder": "e.g. California or Texas"},
            },
        }}}}},
    }
    return ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(body) + '</script>')


def test_the_fix_pass_repairs_the_field_that_was_filled_with_a_person_name():
    """真站那个案例：州那一格（不透明 id + 兜底 full_name）被重判成 state。

    凭什么改得动：产物里的 `label` 就是**站方的整数组件号**（实测 `textField-173862`
    ↔ 配置里的 `component_173862`，号直接对得上），而配置是公开可取的。
    """
    plan, states, fills, notes = fix.from_py(CANDIDATE, url_get=_fake_config)
    assert plan.steps, "plan.from_states 要能读出一份步骤清单（那条口子一直没跑过）"
    assert plan.source == "evidence", plan.source
    assert fills["textfield_173862"]["source"] == "state", fills["textfield_173862"]
    assert fills["textfield_173862"]["fallback"] == [{"random": "state"}]
    assert any("state" in n for n in notes), notes


def test_a_field_that_cannot_be_recognised_is_left_alone_and_said_out_loud():
    """认不出的格子**原样不动**，而且把「没动它」写进 notes。

    这条是这一整族改动的底线（简报与用户都点过名）：**认不出时大声说，绝不许填一个猜的值**。
    往「州」里填一个人名比留空更坏 —— 留空会被校验拦住、能被看见。
    """
    _, _, fills, notes = fix.from_py(CANDIDATE, url_get=_fake_config)
    assert fills["mystery"]["source"] == "mystery", "认不出的格子不许被改"
    assert fills["mystery"]["fallback"] == [{"random": "full_name"}]
    assert any("认不出" in n and "没动它" in n for n in notes), notes


def test_without_the_site_schema_it_still_repairs_what_the_code_can_see():
    """站方 schema 是**可选**的：拿不到时不许因此失败，第 ① 层能认多少认多少。

    这一条是简报里那句「站方 schema 不是必须的」的钉子 —— 给一个永远取不到的
    `url_get`（比如没网），`from_py` 必须照样跑完、照样交回 states/fills。
    """
    def dead(url):
        raise OSError("no network")

    plan, states, fills, notes = fix.from_py(CANDIDATE, url_get=dead)
    assert states and fills, "拿不到配置也要交回一份能用的 states/fills"
    assert fills["textfield_173862"]["source"] == "textfield_173862", \
        "拿不到配置时那一格认不出 → 原样不动（不许猜、也不许整条路崩）"
    assert any("认不出" in n for n in notes), notes


def test_the_graph_fix_path_does_not_explore(tmp_path):
    """图这条路**真的不探索**：给一份 fix_py，探索桩一次都不许被调用。

    判据就是这一条（用户原话：「不重新探索」）。多走一趟探索 = 把已有的证据丢掉重买一次。
    """
    candidates = tmp_path / "broken.py"
    candidates.write_text(CANDIDATE, encoding="utf-8")
    explored = []

    def spy_explore(*a, **kw):
        explored.append(a)
        raise AssertionError("修站这条路**不许**探索")

    deps = graph.Deps(explore=spy_explore, write=graph._writer_from_journey)
    app = graph.build(checkpointer=graph.allowlisted(InMemorySaver()), deps=deps)
    cfg = {"configurable": {"thread_id": "fix-1"}}
    app.invoke({"url": "https://example.test/x", "goal": "把它走通",
                "success_text": "Thank you", "site": "example-fix",
                "out_dir": str(tmp_path / "sites"), "fix_py": str(candidates),
                # 第 4 遍（换窗口大小）这根线在这个桩里没接 —— 按规矩**点名跳过**，
                # 不是让图自己发明一个默认（R-5/R-31）。这一段验的不是自测，是「不探索」。
                "allow_skips": ["country", "viewport"]}, cfg)

    # 走到第一道闸（= explore）：它会说清「读到了什么、改了哪些格」
    # ⚠️ 2026-09-20 起这就是**第一道闸** —— `intake` 不再设闸（运营点「开一趟」就是确认），
    # 于是「读到了什么、改了哪些格」这几项由这一道闸接手（`graph._brief_facts` + 修站那一支）。
    # 「修站这条路不探索」这条判据本身**一个字没变**（下面两处 `explored == []`）。
    state = app.get_state(cfg)
    gate = state.values.get("__interrupt__") or state.tasks[0].interrupts[0].value
    assert gate["step"] == "explore", gate
    assert gate["facts"].get("模式") == "修站（MODE_FIX）", gate["facts"]
    assert "改了哪些格" in gate["facts"], gate["facts"]
    # ⚠️ 这一条原先读的是**第二道闸**（过完 intake 之后那一道，就是现在的第一道）——
    # 换闸不换事实：修站这条路的闸上要说清「这一步**不探路**」。
    assert "没有探路" in json.dumps(gate["facts"], ensure_ascii=False), gate["facts"]
    assert explored == [], explored

    app.invoke(Command(resume="continue"), cfg)          # 过 explore（**不探索**）
    assert explored == [], "走完 explore 这一步之后仍然一次都不许探索"
