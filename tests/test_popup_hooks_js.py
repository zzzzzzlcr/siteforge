"""弹窗钩子表那段**探针 JS 的执行夹具**（`node` + 假 DOM）——
产物里那段 JS 唯一一道自动化的守。

## 为什么另开一份（而不是并进 `test_template.py`）

`test_template.py` 那些替身**根本不执行 JS**：它按脚本里有没有 `privacy` 这个词回一句
**预设答复**。所以「第一个答应的钩子是谁」「名字有没有跟着答复回来」这些
——**只有跑起来才看得见**，而它们恰恰是这一片要钉的东西。

2026-10-10 就是这么逮到一个真 bug 的：钩子外面多套了一层函数 ⇒ 那句 `return`
只返回给了空壳、探针一路走到兜底的 `return ''`（**替身全绿，真站上一个弹层都点不掉**）。
所以这一份不是「多一层保险」，是那一段 JS **唯一**的一道守。

## 射程（**先说不钉什么**）

钉得住：十一格里那段探针**真跑**出来的答复 —— 同意条（带 id / 只有文字）、
盖住整页的模态、干净页面、两者同时在（表序 = 优先级）、
★ 广告 creative（`data-click="close"`：6 个候选里只认可见那一颗 / **可见但被盖着 ⇒ 返空**
（★ 2026-10-17 Ruling 27 换过形状：候选**在**那一摞里、只是盖着它的那层排在前面）/
超界的容器不许点 / aria-label 是散文：「Do not close this window」（24 字）与
「Do not close」（12 字，≤24）都不许认 / **`visibility:hidden` 的关闭键不许认**
（★ Ruling 28-①））。

钉不住（照实说）：
- **真站**：假 DOM 只有探针会碰到的那几样（`getElementsByTagName` / `getBoundingClientRect`
  / `getAttribute` / `innerText`）；真浏览器里的渲染、时机、`position:fixed` 一律不测。
- **点没点掉**：这一份只测**读**那半（探针返回什么）；动手那半在 `cdp click`，由
  `test_template.py` 的替身管（它们钉的是「点的是不是它」）。
- 穿 shadow root 那条路（`_CONSENT_ROOTS_JS`）：假 DOM 里的 `shadowRoot` 一律是 `null`。

⚠️ **正控在用例里**（`test_the_fixture_can_actually_fire`）：拿**今天真犯过的那个错**
（`return` 被关进内层函数）证明这套夹具真的会响 —— 一条永远绿的夹具和没有夹具是一回事。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

#: 产物的落盘环境（替身 `common` + 假的 `cdp` 二进制）与渲染样本**都从 `test_template`
#: 那边来** —— 不在这儿抄第二遍：抄一份就会两边漂（与 `test_console_js` 拿
#: `test_configcheck.CLEAN` 同一条口径）。
from test_template import STUB_CDP, STUB_COMMON, _load, render_sample  # noqa: E402

DRIVER = pathlib.Path(__file__).with_name("popup_probe_driver.js")
NODE = shutil.which("node")


@pytest.fixture(autouse=True)
def _node_is_a_hard_dependency():
    """**没有 `node` 就红，不是 skip** —— 与 `test_console_js.py` 那条同一个理由。

    这一份钉的性质在**别处一道守都没有**（替身不执行 JS）；`skipif` 会让「全量绿」
    静默退化成「少了几条」。装个 node（本机实测 v22.22.1 够用）。
    """
    if NODE is None:
        pytest.fail(
            "这台机器上没有 `node`（`command -v node` 没找到）—— 这一份钉着弹窗探针那段 JS"
            "**只有跑起来才看得见**的性质（替身测试不执行 JS，别处一道守都没有）。"
            "装一个 node（本机实测 v22.22.1 够用）；真不想装就在 CI 里**显式**排除这一份"
            "并记账 —— **不要退回 `skipif`**。")


def _product(tmp_path: pathlib.Path):
    """渲一份产物并 import 回来（探针 / 标记两段 JS 都从它身上切）。"""
    sandbox = tmp_path / "sandbox"
    (sandbox / "forms").mkdir(parents=True)
    (sandbox / "forms" / "common.py").write_text(STUB_COMMON, encoding="utf-8")
    (sandbox / "forms" / "sites").mkdir()
    fake = sandbox / "cdp"
    fake.write_text(STUB_CDP, encoding="utf-8")
    fake.chmod(0o755)
    module, _ = _load("probe_under_test", render_sample(), sandbox)
    return module


def _probe_js(tmp_path: pathlib.Path) -> str:
    """从**真渲染出来的产物**里把那段探针切出来（`_POPUP_PROBE_JS`，产物 import 时才拼）。

    ⚠️ 切的是产物里**当前那一版**：钩子表、包装、兜底只要动一个字，这一份量到的就跟着动。
    """
    return _product(tmp_path)._POPUP_PROBE_JS


def _drive(tmp_path: pathlib.Path, js: str, marker_js: str | None = None) -> dict:
    """把一段探针 JS 丢给假 DOM 跑一遍，读回那几格的结果。

    `marker_js` 给了就再跑一遍「按文字打标记」（`_MARK_CONSENT_BY_TEXT_JS`）——
    读半（探针）与动作半（标记）是同一道 tag 闸，两半都得钉。
    """
    p = tmp_path / "probe.js"
    p.write_text(js, encoding="utf-8")
    cmd = [NODE, str(DRIVER), str(p)]
    if marker_js is not None:
        m = tmp_path / "marker.js"
        m.write_text(marker_js, encoding="utf-8")
        cmd.append(str(m))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "夹具自己挂了（rc=%s）：\n%s\n%s" % (r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_probe_answers_the_three_fields_python_reads(tmp_path):
    """产物那段探针在假 DOM 里**真跑**：十七格，一格不许差。

    十七格 = 同意条（带 id / 只有文字 / **按钮是 div（CookieScript 形状）** /
    **wrapper 合并文字也很短 —— 不许点 wrapper** / **按钮是 span（没有 id）** /
    **容器里的长散文不许认**）、盖住整页的模态
    （按钮 / **关闭键是 span** / **关闭键包在 wrapper 里 —— 不许点 wrapper**）、
    干净页面、两者同时在、
    广告 creative（可见那一颗恒命中 / **可见但被盖着 ⇒ 恒返空**（blavity 矩阵第 4 格，
    ★ Ruling 27 换过形状）/ 超界的容器恒不点 / 散文的 aria-label 恒不认 ——
    含**≤24 字**那格，见 Ruling 26 / **`visibility:hidden` 恒不认**，见 Ruling 28-①）。
    答复的形状就是 `_clear_obstructions` 拆的那三格
    （`"<钩子名>|<选择器|TEXT>|<按钮文字>"`）—— 名字、选择器、按钮文字，
    少一格那边就退化成「量不出来」（什么都不点）。
    """
    out = _drive(tmp_path, _probe_js(tmp_path))
    assert out["total"] == 17, out
    assert out["failed"] == 0, [r for r in out["results"] if not r["ok"]]


def test_the_fixture_can_actually_fire(tmp_path):
    """**正控**：拿今天真犯过的那类错，证明这套夹具真的会响。

    那个错（2026-10-10 实测逮到）：`return` 被关进了内层函数 —— 探针一路走到兜底的
    `return ''`，页面上有弹层也当没有。当时犯在「每一条钩子外面套一层」，这里用
    **同一个病的另一个条数**：把**整段探针**套进一层函数。病一样（`return` 出不来），
    而且是**从真探针变异出来的**，钩子表怎么改它都跟着在。

    ⇒ **该有答复的那几格必须当场空掉**；本来就该是空的那几格**不该跟着响**
    —— 否则这条正控自己就分不清「夹具在响」还是「夹具恒红」。
    """
    broken = "(function(){%s})();return '';" % _probe_js(tmp_path)
    out = _drive(tmp_path, broken)
    assert out["failed"] == 10, out["results"]      # 该有答复的那十格
    passed = [r["name"] for r in out["results"] if r["ok"]]
    assert passed == ["干净页面：一条都不答应",
                      "广告 creative：可见但**被盖着**（最顶上那个不是它）不许认",
                      "广告 creative：超界的容器（300×300，class/aria 带 close）不许点",
                      "广告 creative：aria-label 是散文 —— 「Do not close this window」不许认",
                      "广告 creative：aria-label 是散文（12 字 ≤24）—— 「Do not close」不许认",
                      "广告 creative：`visibility:hidden` 的关闭键不许认",
                      "同意条：容器里的长散文不许认（div/span 新白名单）"], passed


def test_the_text_marker_can_mark_a_div_button(tmp_path):
    """★ 2026-10-19：没有 id 的 div/span 候选要走「按文字打标记」那条路。

    钩子答 `TEXT|<文字>` 之后，真正落点的是 `_MARK_CONSENT_BY_TEXT_JS` —— 它原来也有
    同一道 `button/a/input` 闸。只放钩子不放标记 ⇒ 探针答得出来、动作永远落不下去
    （真站上就是「没敢点」）。这一条让那段 JS 真跑：页面里一颗没有 id 的
    `<div>Accept all</div>`，标记要能打上 `data-sf-consent="1"`。
    """
    module = _product(tmp_path)
    marker = module._MARK_CONSENT_BY_TEXT_JS % json.dumps("Accept all")
    out = _drive(tmp_path, module._POPUP_PROBE_JS, marker_js=marker)
    assert out["marker"]["got"] == "marked", out["marker"]
    assert out["marker"]["attr"] == "1", out["marker"]
