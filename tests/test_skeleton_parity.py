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
