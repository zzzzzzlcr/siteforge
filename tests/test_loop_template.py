"""第二种产物形态（**步进循环型**）的闸。

它为什么存在：固定步骤表那一族在**单页漏斗/问卷**上会栽在同一个形状上（2026-09-24 实测
`solarincentives` 从第 6 步起 19 步全被跳过、一趟白跑）—— 而**分支**（男/女 ⇒ 第二问变）
在「每轮现读页面」这个形状下**根本不存在**。形状照生产在跑的 `goldenagesouls.py`。

这一份钉五件：
  ① 产出的 py **是合法的**（`ast.parse`）而且是**生产认的那一族**（`fix.shape_of` 不返 unknown）；
  ② **生产那四个开关 + 自测那三个一个不少**（2026-09-24 实测：少了 `--trace` / `--no-report`，
     产物在自测里一上来就 argparse 退出，报告只剩「进程退了（退出码 2）」—— 我第一版就把它们
     当「可选」跳过了 ✗，这一条改成**一个都不许少**）；
  ③ **网址判据不能空** —— 这一族只看网址（老手原话：「正文判据是这一族栽跟头的老地方」）；
  ④ 挑选项规则、填值 `kind` 这两格给得不认识 ⇒ **当场拒**（不知道填什么就别产）；
  ⑤ ★ 「读这一屏」那段 JS **真能过语法检查**（`node --check`；没有 node 就跳过并说出来，
     **不假装验过**）—— 我自己就往里塞过一行 python 式 `#` 注释，那会让浏览器 eval 报错
     ⇒ `read()` 恒返回 None ⇒ 整个循环空转。
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
    """★ 一个都不许少：少一个，那个站从这一单起**每一单**都一上来就 argparse 报错。

    （自测那三个尤其：`--trace` / `--delay` / `--no-report` —— 2026-09-24 实测过一次，
    报告只剩「进程退了（退出码 2）」。）
    """
    src = _src()
    missing = [f for f in fix.REQUIRED_CLI_FLAGS if f not in src]
    missing += [f for f in selftest.ARTIFACT_FLAGS if f not in src]
    assert not missing, "少了这些开关：%r" % (missing,)
    assert "sys.exit(0 if" in src, "退出码就是生产的成功判据，少了它这一趟没有判据"
    assert "report_url" in src, "进度上报那条线"


def test_the_selftest_never_reports_into_production():
    """`--no-report` 不是摆设：自测那一遍**永不上报**（自测不是生产任务）。"""
    src = _src()
    assert "if self.no_report:" in src, "没有把 --no-report 用在 `_rpt` 上"


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


def _book(pages):
    """一个够用的账本桩（这一条只读 `pages[].when.url_contains` —— 别把整本账搬进来）。"""
    import types
    return types.SimpleNamespace(pages=pages)


def test_the_shape_rule_reads_the_ledger_and_says_why():
    """★ 判「单页漏斗」的那把尺子是**量到的**：多少个状态共用同一个 `url_contains`。

    为什么需要这条规则（交接文档欠账 ④）：固定步骤表在问卷上会静默跳过整组 ——
    它记的只是走过的那一条路，下一问变了 `when` 就不成立。
    """
    pages = [{"name": "landing", "when": {"url_contains": "example.test/article"}}]
    pages += [{"name": "quiz-%d" % i, "when": {"url_contains": "quiz.example.test"}}
              for i in range(template_loop.LOOP_MIN_STATES)]
    got = template_loop.funnel_states(_book(pages))
    assert got["states"] == ["quiz-0", "quiz-1", "quiz-2"], got
    assert got["url"] == "quiz.example.test", got
    assert "单页漏斗" in got["why"], got["why"]


def test_the_shape_rule_refuses_to_guess_without_pages():
    """判不出来就说判不出来（**不猜**）：没有带地址的页 / 最大那组不够大，两种都空手回去。

    空手的代价只是「照旧产固定步骤表」—— 而猜错的代价是**换错形态**（那份产物在真站上
    要么空转到收摊、要么跳过整组），两者不是一个量级。
    """
    none = template_loop.funnel_states(_book([]))
    assert not none["states"] and "判不出" in none["why"], none

    one = template_loop.funnel_states(_book([{"name": "a", "when": {"url_contains": "x.test"}}]))
    assert not one["states"] and "只有 1 个状态" in one["why"], one

    #: 有页但**没有 url_contains**（账本记不下地址那一格）⇒ 一样不猜
    blind = template_loop.funnel_states(_book([{"name": "a", "when": {"text_contains": ["x"]}}]))
    assert not blind["states"] and "判不出" in blind["why"], blind
