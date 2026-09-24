"""第二种产物形态（**步进循环型**）的闸。

它为什么存在：固定步骤表那一族在**单页漏斗/问卷**上会栽在同一个形状上（2026-09-24 实测
`solarincentives` 从第 6 步起 19 步全被跳过、一趟白跑）—— 而**分支**（男/女 ⇒ 第二问变）
在「每轮现读页面」这个形状下**根本不存在**。形状照生产在跑的 `goldenagesouls.py`。

这一份钉四件：
  ① 产出的 py **是合法的**（`ast.parse`）而且是**生产认的那一族**（`fix.shape_of` 不返 unknown）；
  ② **生产/自测那几个开关一个不少**（少一个，那个站从这一单起每一单都 argparse 报错）；
  ③ **网址判据不能空** —— 这一族只看网址（老手原话：「正文判据是这一族栽跟头的老地方」）；
  ④ ★ 「读这一屏」那段 JS **真能过语法检查**（`node --check`；没有 node 就跳过并说出来，
     **不假装验过**）—— 2026-09-24 我自己就往里塞过一行 python 式 `#` 注释，那会让浏览器
    eval 报错 ⇒ `read()` 恒返回 None ⇒ 整个循环空转。
"""
from __future__ import annotations

import ast
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fix, selftest, template_loop  # noqa: E402

FILLS = {"email": {"kind": "email"}, "postcode": {"kind": "postcode"}}
PROV = {"site": "solarincentives", "entry_url": "https://solarincentives.io/article-1/"}


def _src(**kw):
    return template_loop.render_loop("solarincentives", kw.pop("success_text", "Thank you"),
                                     kw.pop("success_urls", "thank-you"), FILLS, PROV, **kw)


def test_the_product_is_a_real_py_in_the_family_production_accepts():
    src = _src()
    ast.parse(src)                                     # 不合法的 py 根本不该产出来
    assert fix.shape_of(src)["kind"] == "legacy", fix.shape_of(src)


def test_every_switch_production_and_selftest_call_it_with_is_there():
    """少一个开关，那个站从这一单起**每一单**都一上来就 argparse 报错。"""
    src = _src()
    for flag in fix.REQUIRED_CLI_FLAGS:
        assert flag in src, flag
    for flag in selftest.ARTIFACT_FLAGS:
        if flag in ("--delay", "--trace", "--no-report"):
            continue                                   # 可选那几格
        assert flag in src, flag
    assert "sys.exit(0 if" in src, "退出码就是生产的成功判据，少了它这一趟没有判据"
    assert "report_url" in src, "进度上报那条线"


def test_the_url_criterion_is_required_because_this_family_judges_by_url():
    """这一族**只看网址**（正文判据是它栽跟头的老地方）⇒ 没给网址判据就不许产。"""
    with pytest.raises(ValueError) as got:
        _src(success_urls="")
    assert "成功判据不能空" in str(got.value)


def test_a_pick_rule_we_do_not_understand_is_refused():
    with pytest.raises(ValueError) as got:
        _src(pick="随便")
    assert "random" in str(got.value) and "first" in str(got.value)


def test_a_fill_without_a_kind_is_refused():
    """填什么由 `kind` 决定 ⇒ 没给就是没告诉我们往这一格写什么。"""
    with pytest.raises(ValueError) as got:
        template_loop.render_loop("s", "t", "u", {"email": {"label": "Email"}}, PROV)
    assert "kind" in str(got.value)


def test_the_reader_js_really_parses():
    """★ 2026-09-24：我自己就往里塞过一行 python 式 `#` 注释 —— 那会让浏览器 eval 报错 ⇒
    `read()` 恒返回 None ⇒ 整个循环空转。所以这一段**必须**过真语法检查。

    ⚠️ 没有 node ⇒ **跳过并说出来**（不假装验过）。
    """
    src = _src()
    body = src.split("READ_PAGE_JS = ", 1)[1].split("\n", 1)[0]
    js = json.loads(body)
    assert "#" not in js, "JS 里出现 python 式注释 —— 浏览器 eval 会报错：%r" % js[:80]
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node —— 这条只验了「没有 #」，没验语法")
    path = ROOT / "runtime" / "loop-template-read-page.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(js, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]
