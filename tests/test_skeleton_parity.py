"""两套骨架的**互认**：同一件事，**两边都得有**（★ 2026-10-16）。

为什么单独立一份：这一条跨两套骨架、不属于任何一边 —— 它钉的是「**以后每一份产物都带**」，
而表那套的钉子住在 `tests/test_template.py`、循环型的住在 `tests/test_loop_template.py`，
两边各自都看不见对方（「两把尺子不许分家」那条规矩的落点就在这儿）。

⚠️ 两个测试文件**各有一份自己的** `SAMPLE_PROVENANCE` / `_one_click_states` / `PROV`
—— 这一份照抄同样的几行，**不 import** 那两个模块：一条断言不该把两份文件的 fixture 绑在一起。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import template as T  # noqa: E402
from agent import template_loop as L  # noqa: E402

#: 与 `tests/test_template.py` 那份同一个形状（合成 provenance，不是真站）。
SAMPLE_PROVENANCE = {
    "generated_at": "2026-09-17T02:00:00+08:00",
    "generator": "siteforge/v0.1",
    "env": {"proxy_country": "US", "dpr": 1, "ua": "Mozilla/5.0 (stub)", "viewport": [1280, 800]},
    "platform": {"guess": "custom-quiz", "confidence": 0.6},
    "selftest": None,
    "source": {"kind": "build", "evidence": "fixtures/reference_spec（合成站，不是真站）"},
}

#: 与 `tests/test_loop_template.py` 那份同一行。
PROV = {"site": "solarincentives", "entry_url": "https://solarincentives.io/article-1/"}


def _one_click_states(selectors=("#stale-a", "#stale-b"), **target_extra):
    target = {"text": "Get Started", "role": "button", "near": None, "selectors": list(selectors)}
    target.update(target_extra)
    return [{"name": "walk", "when": None, "steps": [
        {"action": "click", "note": "点「Get Started」", "target": target}]}]


def test_both_skeletons_carry_the_obstruction_machinery():
    """一句判据管住「以后每一份产物都有」：**两套骨架渲出来的产物都得带那几样**。

    ⚠️ 2026-10-16（本刀做的账）：**Task 3 之后这条仍有一项是红的** —— 循环型产物里还没有
    `_POPUP_PROBE_JS`（那是 **Task 4**「钩子表 + 每次动作前探一次」的产物）。
    红得只剩那一项：`_covered_by` 与 `elementFromPoint` 是本刀（Task 3）搬进去的。
    """
    table = T.render("parity-site", "Thank you", _one_click_states(selectors=["#go"]), [],
                     SAMPLE_PROVENANCE)
    loop = L.render_loop("parity-site", "Thank you", "thank-you", {}, dict(PROV))
    for name, src in (("固定步骤表", table), ("循环型", loop)):
        for need in ("_covered_by", "_POPUP_PROBE_JS", "elementFromPoint"):
            assert need in src, "%s 产物里没有 %s" % (name, need)


def _top_level_function(src, name):
    """从**骨架文本**里切出一个模块级函数的正文（到下一个顶层定义前 —— 两个空行分隔）。"""
    i = src.index("\ndef %s(" % name) + 1
    j = src.find("\n\n\n", i)
    assert j > i, "没找到 %s 的结尾（骨架里没有两个空行的分隔？）" % name
    return src[i:j + 1]


def test_both_skeletons_clean_an_eval_answer_with_the_same_code():
    """★ 2026-10-16 复核 Critical 的钉子：**脱引号那段代码，两套骨架必须逐字节相同**。

    为什么要紧（真事就在这一刀里）：`cdp eval` 的 stdout 是 **JSON**，字符串结果**带引号**；
    表那套的 `_ev` 干两件事（包 IIFE + `_clean_eval` 脱引号）。循环型移植时只补了包壳那件
    ⇒ 真站上 `got == '"COVER|…"'` ⇒ `startswith("COVER|")` 恒假 ⇒ **闸门一次都不会开**，
    而账本上跟「没被盖着」一模一样（blavity 那次假通过的形状）。

    这一条钉的是「**同一份代码**」：谁改了一边、忘了另一边，它先红。
    （行为那一半住在 `tests/test_loop_template.py::test_a_quoted_eval_answer_still_opens_the_gate`。）
    """
    a = _top_level_function(T.SKELETON.template, "_clean_eval")
    b = _top_level_function(L._LOOP_SKELETON, "_clean_eval")
    assert a.strip() and b.strip(), "有一边没有 `_clean_eval`"
    assert a == b, ("两套骨架的 `_clean_eval` 不是同一份代码：\n--- 固定步骤表 ---\n%s\n"
                    "--- 循环型 ---\n%s" % (a, b))
    assert 'startswith(("Error:", "ERROR:", "Usage:", "Flags:")' in a, \
        "脱引号那一段里**报错行要说成「量不出来」**那句丢了？"
