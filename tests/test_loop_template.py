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
import importlib.util
import json
import logging
import pathlib
import re
import shutil
import subprocess
import sys
import types

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

    ⚠️ 按 **Python** 的语义读它（`ast.literal_eval`）—— 产物拿到的就是 Python 解出来的那串。
    用 `json.loads` 会误报：`_json_lit` 把 4 字节字符写成 `\\UXXXXXXXX`（JSON 里没这个转义，
    但 Python 认），而运行期那串字符是对的。
    """
    src = _src()
    body = src.split("READ_PAGE_JS = ", 1)[1].split("\n", 1)[0]
    js = ast.literal_eval(body)
    assert "#" not in js, "JS 里出现 python 式注释 —— 浏览器 eval 会报错：%r" % js[:80]
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node —— 这条只验了「没有 #」，没验语法")
    path = ROOT / "runtime" / "loop-template-read-page.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(js, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]


def test_the_reader_and_the_click_scan_pierce_shadow_roots():
    """★ 2026-09-28（`try` 那站的问卷真跑逼出来的）：控件长在 **shadow root** 里 ⇒
    `document.querySelectorAll` 看不见它们 ⇒ 读取器那一屏只读到一颗「Back」、
    连着 60 步「没有可动作的东西」到收摊 ✗（真 trace）。

    所以三处（读这一屏 / 按文字点 / 按占位符填）都走**同一个** `deepAll` ——
    ⚠️ 判据只有一处，读得到却点不到就是「每轮空转」那个老病（2026-09-24 撞过）。
    """
    src = _src()
    assert "function deepAll(" in template_loop.DEFAULT_READ_JS
    assert "deepAll('input,select,textarea')" in template_loop.DEFAULT_READ_JS
    assert "deepAll('button,label,[role=button]')" in template_loop.DEFAULT_READ_JS
    #: 点/填那两处（骨架里拼的 JS）也要穿 —— 它们各自在字符串里带一次 `deepAll(`
    assert src.count("deepAll(") >= 5, "点/填那几处没穿 shadow：%d 处" % src.count("deepAll(")
    assert "__DEEP__" not in src, "占位符没被替掉（产物里会留一串 __DEEP__）"


def test_the_shadow_helper_can_be_dropped_into_a_string_literal():
    """`DEEP_JS` 是**拼进 py 字符串字面量**的（`__DEEP__` 在 `_json_lit` **之后**替）
    ⇒ 它里面**不许有** `"` 和 `\\`，否则产物那份 py 会当场语法错。

    ⚠️ 这条是**我自己写错过一次**才补的：第一版把 `+ DEEP_JS +` 直接写进骨架，
    产物一上来就 `NameError` / `unmatched ')'`。
    """
    assert '"' not in template_loop.DEEP_JS
    assert "\\" not in template_loop.DEEP_JS
    ast.parse(_src())          # 产物本身要是合法 py


def test_both_js_literals_of_the_product_really_parse():
    """产物里那两段模块级 JS（读这一屏、同意条在不在）都要过 `node --check`。

    与 `test_the_reader_js_really_parses` 同一个理由，但验的是**渲染之后**的那一份 ——
    替占位符那一下（`__DEEP__`）就是在这之后才发生的，光验原文验不到它。
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node —— 没验语法，别当验过")
    tree = ast.parse(_src())
    seen = set()
    for stmt in tree.body:
        if not (isinstance(stmt, ast.Assign)
                and getattr(stmt.targets[0], "id", "") in ("READ_PAGE_JS", "COOKIE_GONE_JS")):
            continue
        name = stmt.targets[0].id
        seen.add(name)
        js = ast.literal_eval(stmt.value).replace("__WORDS__", "[]")
        path = ROOT / "runtime" / ("loop-template-%s.js" % name.lower())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(js, encoding="utf-8")
        done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, "%s: %s" % (name, done.stderr[:300])
    assert seen == {"READ_PAGE_JS", "COOKIE_GONE_JS"}, "产物里少了那两段 JS 里的一段：%s" % seen


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


def _fake_common(monkeypatch, page, calls, snap=None):
    """一个够用的假 `common`：产物 import 的就是它（只记调用，不碰真浏览器）。"""
    mod = types.ModuleType("common")

    class CDPHelper:
        def __init__(self, ws_url, *a, **kw):
            calls["ws"] = ws_url

        def eval(self, js, *a, **kw):
            if "data-sf-pick" in js or "data-sf-fill" in js:
                #: 「按文字找」那一招是 JS 找 → 打标记 → 再点 ⇒ 把那段 JS 记下来，
                #: 测试才分得清这一下找的是**哪个**字（选项 vs Continue）。
                calls.setdefault("marks", []).append(js)
                return "marked"
            return json.dumps(page)

        def snapshot(self):
            """结构化那一路（兜底）读的就是它 —— 不给就抛（产物的兜底会「读不回来」）。"""
            calls["snapshot"] = calls.get("snapshot", 0) + 1
            if snap is None:
                raise RuntimeError("这个桩没给快照")
            return snap

        def click(self, sel, *a, **kw):
            calls["click"].append(sel)

        def form(self, sel, **kw):
            calls["form"].append((sel, kw))

        def navigate(self, url, *a, **kw):
            calls["nav"].append(url)

        def scroll(self, sel, *a, **kw):
            calls["scroll"].append(sel)

    mod.CDPHelper = CDPHelper
    mod.setup_logger = lambda name: logging.getLogger(name)
    mod.report_url = lambda cdp, tid, step, log, reason="": calls["report"].append((step, reason))
    monkeypatch.setitem(sys.modules, "common", mod)
    return mod


def test_a_product_without_an_entry_url_runs_on_the_current_page(tmp_path, monkeypatch):
    """★ 2026-09-24 真站上**当场**逼出来的：入口网址空**不是错**，产物照样该开干。

    现场：生成器产出的第一份一上来就退（`EXIT=1` + 日志「这一版**没有入口网址** ——
    不知道从哪儿开始，如实报失败」），而它明明能干活。生产那份样本（`goldenagesouls.py`
    的 `run()`）里**一句导航都没有** ⇒ 这一族本来就在「平台开好的那一页」上开干
    （2026-09-23 用户定的规矩：产物不自己导航一遍）。

    这条钉两件：① **不导航**；② 照样往下做（不是一上来就退）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Question 1", "progress": "",
            "inputs": [], "options": ["\u0001No"], "nextVisible": False, "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)

    src = template_loop.render_loop("noentry", "Thank you", "thank-you",
                                    {"email": {"kind": "email"}},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "product.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("noentry_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    trace = tmp_path / "t.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid",
                           trace_path=str(trace), no_report=True)
    filler.run(max_steps=2)

    assert calls["nav"] == [], "产物自己导航了 —— 那是 2026-09-23 点名不许的那一下"
    assert calls["click"], "一上来就退场，一步都没做"
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "goto" and r.get("ok") is None for r in rows), rows[:3]
    assert any(r.get("action") == "click" for r in rows), rows
    assert all("没有入口网址" not in str(r) for r in rows), "退场那条还在：%r" % (rows[:3],)


def test_the_phone_pool_gives_a_number_the_site_accepts(tmp_path, monkeypatch):
    """★ 2026-09-25（用户点名）：号码要**座机**形状，不是 `07…` 手机号；
    ★ 2026-10-08（用户点名「**不是**都是 `0207` 啊」）：号段**别锁死一个**。

    现场：真站那一格的校验收座机；产物原来给 `07…`（位数一样合法）⇒ 填了过不去，
    停在那一屏。顺带钉住 Ofcom 影视保留段**不许**出现（格式合法、一眼是假的）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "", "inputs": [],
            "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("phonesite", "Thank you", "thank-you",
                                    {"phone": {"kind": "value"}}, dict(PROV))
    path = tmp_path / "phone.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("phone_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for _ in range(50):
        got = module.Filler._pool(None, "phone")
        assert (got.startswith(("0203", "0207", "0208", "0113", "0114", "0115", "0116",
                                "0117", "0118", "0121", "0131", "0141", "0151", "0161",
                                "0191")) and len(got) == 11), got
        assert not got.startswith(("02079460", "0113496", "0114496", "0115496", "0116496",
                                   "0117496", "0118496", "0121496", "0131496", "0141496",
                                   "0151496", "0161496", "0191496", "0289018", "0292018")), \
            "给了 Ofcom 影视保留段：%s" % got


def test_four_byte_characters_are_written_as_escapes(tmp_path, monkeypatch):
    """★ 2026-09-25 真上传撞出来的：后台的库是 `utf8`（不是 `utf8mb4`）⇒ **4 字节字符存不进去**
    （那面 `🇬🇧` 是探路从电话格旁边的页面上抄回来的 ✓）⇒ 后端报 1366 然后 500。

    产物里把这类字符写成 `\\UXXXXXXXX` 转义：文件落回 3 字节以内，**运行期那串字符一个不变**。
    钉两件：① 产出的源码里**没有** 4 字节字符；② 转义之后字面量解出来还是那面旗（没丢信息）。
    """
    flag = "\U0001F1EC\U0001F1E7"
    src = template_loop.render_loop(
        "flagsite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "target": {"nearby_text": [flag]}}}, dict(PROV))
    assert not re.search("[\U00010000-\U0010FFFF]", src), "源码里还有 4 字节字符"
    assert "\\U0001F1EC" in src and "\\U0001F1E7" in src, "没写成转义"

    #: 这份产物 import 的是 `common`（脚本靠 `dirname(dirname(__file__))` 找 forms/）——
    #: 这一条只验「字面量转义」，给个假替身就够（别去碰真浏览器那套）。
    _fake_common(monkeypatch, {"url": "https://x/", "head": "", "progress": "", "inputs": [],
                               "options": [], "nextVisible": False, "nextTxt": ""},
                 {"click": [], "form": [], "nav": [], "scroll": [], "report": []})
    path = tmp_path / "flag.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("flag_product", path)
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)
    got = module.FILLS["phone"]["target"]["nearby_text"]
    assert got == [flag], "转义把字丢了：%r" % (got,)


def test_a_screen_with_options_is_answered_once_then_continue_is_clicked(tmp_path, monkeypatch):
    """★ 2026-09-26 用户原话：「场上有选项但是选完还得点按钮那种场景他就过不去」。

    真 trace（`job-c053e0b049f3`）：那一趟 **58 次点击全是「答了第 N 题」** —— ② 每轮都重新挑一个
    选项，永远轮不到 ③ 去点 Continue ⇒ 在「先选一个、再点下一步」的屏上原地打转直到收摊。

    这条钉：「同一屏只答一次」⇒ 下一轮（再下一轮，等到空转闸放行）点的是 **Continue**，
    而且**不再**挑第二个选项；**换屏之后照旧挑**（自动前进那种漏斗不受影响）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Question 1", "progress": "10%",
            "inputs": [], "options": ["\u0001No", "\u0001Yes"], "nextVisible": True,
            "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "sel.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("sel_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    trace = tmp_path / "t.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace), no_report=True).run(max_steps=4)

    marks = calls.get("marks", [])
    picks = [m for m in marks if '"No"' in m or '"Yes"' in m]
    assert len(picks) == 1, "同一屏挑了不止一次选项（%d 次）：%r" % (len(picks), marks)
    assert any('"Continue"' in m for m in marks), "答完之后没去点「下一步」：%r" % (marks,)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    answered = [r for r in rows if str(r.get("note") or "").startswith("答了第")]
    assert len(answered) == 1, "同一屏答了不止一次：%r" % [(r.get("step"), r.get("note")) for r in rows]
    assert any(r.get("action") == "click" and r.get("note") == "Next" for r in rows), rows[-4:]


def test_an_input_that_only_has_an_id_is_filled_by_id(tmp_path, monkeypatch):
    """★ 2026-09-26 第五次真站试跑（`easystorebuilderpro` 那三个格）逼出来的：
    输入框**只有 `id`、没有 `data-testid`** ⇒ 原来一律按 `[data-testid=…]` 找 ⇒
    `cdp form` 报 `element not found`，而 `fill()` **照样 return True** ⇒
    trace 上写着「填了 qv3-fn」、表单里一个字都没有、提交一直被拒（点三次 Next 页面一动不动 ✗）。

    这条钉三件：① 读取器给 id 来源打 `\\x04` 标记；② 填的时候按 `[id=…]`；
    ③ **cdp 说没填上就不许报成功**（`_filled_ok`）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/lead", "head": "Activation", "progress": "92%",
            "inputs": [["\u0004qv3-fn", ""]], "options": [], "nextVisible": False, "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    #: 那一站的账本里有 `fill_order`（探路时真填过的顺序）—— 这一格按它兜底填（见 `value_for` 第 ③ 档）
    src = template_loop.render_loop(
        "idsite", "Thank you", "thank-you", {"full_name": {"kind": "full_name"}},
        dict(PROV, fill_order=["full_name"]))
    path = tmp_path / "idsite.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("id_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "t.jsonl"
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace), no_report=True).run(max_steps=1)

    sels = [sel for sel, _kw in calls["form"]]
    assert any(sel == '[id="qv3-fn"]' for sel in sels), "没有按 id 去找这一格：%r" % (sels,)
    assert not any("data-testid" in sel for sel in sels), sels
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows

    #: ③ cdp 说没填上 ⇒ 不许报成功（`_filled_ok`）
    assert module.Filler._filled_ok("Error: scroll mouse wheel failed: element not found") is False
    assert module.Filler._filled_ok("ok") is True
    assert module.Filler._filled_ok("") is True


def test_the_phone_field_never_takes_the_profile_value(tmp_path, monkeypatch):
    """★ 2026-09-26 用户点名（solarincentives）：「手机号别取资料」。

    那一格**永远现编**（`0207` + 7 位）—— 资料里那份是别人填过的真实号，同一串在站上反复
    出现迟早被认出来。这条把「资料里给了 phone 也不许用」钉住（正控：别的格照旧用资料里的值）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://uk.test/lead", "head": "Phone", "progress": "",
            "inputs": [["\u0002Enter your phone", ""]], "options": [], "nextVisible": False,
            "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("ukphone", "Thank you", "thank-you",
                                    {"phone": {"kind": "phone", "label": "Enter your phone"}},
                                    dict(PROV, fill_order=["phone"]))
    path = tmp_path / "ukphone.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("uk_phone_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text(json.dumps({"phone": "9998887777"}), encoding="utf-8")   # ← 资料里给了号
    module.Filler("ws://x/1", str(form), "cid", no_report=True).run(max_steps=1)

    assert calls["form"], "一格都没填"
    _sel, kw = calls["form"][0]
    got = str(kw.get("value") or "")
    assert (got.startswith(("0203", "0207", "0208", "0113", "0114", "0115", "0116", "0117",
                            "0118", "0121", "0131", "0141", "0151", "0161", "0191"))
            and len(got) == 11), "没按英国座机形状现编（多个号段，用户点名不许只有 0207）：%r" % (got,)
    assert got != "9998887777", "取了资料里那个号"




def test_street_and_city_fields_get_a_value(tmp_path, monkeypatch):
    """★ 2026-09-28 用户报的那一趟（`georgiapower`）：「我跟他说 street 没填…他还是没改」。

    查出来的第一层：产物里 `FILLS` **有** `city` / `country` / `search_for_address`，
    可 `_pool` **一个分支都没有** ⇒ 一律空串 ⇒ **那几格永远填不上** ✗。
    这条钉：street / city 现在拿得到值；而且**账本里真用过的值仍然优先**。
    """
    def _fill(src_prov, page_input):
        calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
        page = {"url": "https://us.test/lead", "head": "Address", "progress": "",
                "inputs": [[page_input, ""]], "options": [], "nextVisible": False, "nextTxt": ""}
        _fake_common(monkeypatch, page, calls)
        src = template_loop.render_loop(
            "addrsite", "Thank you", "thank-you",
            {"street": {"kind": "street", "label": "Street"},
             "city": {"kind": "city", "label": "City"}},
            src_prov)
        path = tmp_path / "addr.py"
        path.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("addr_product", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        form = tmp_path / "form.json"
        form.write_text("{}", encoding="utf-8")
        module.Filler("ws://x/1", str(form), "cid", no_report=True).run(max_steps=1)
        return str(calls["form"][0][1].get("value") or "") if calls["form"] else ""

    street = _fill(dict(PROV, fill_order=["street"]), "\u0002Street")
    assert street, "street 那一格还是空的"
    assert any(w in street for w in ("St", "Ave", "Rd", "Ln", "Dr")), street

    city = _fill(dict(PROV, fill_order=["city"]), "\u0002City")
    assert city, "city 那一格还是空的"

    #: 账本里真用过的值（`examples`）**优先**——那是那一站收过的地址形状
    from_ledger = _fill(dict(PROV, fill_order=["street"], examples={"street": "1234 Ledger Ave"}),
                        "\u0002Street")
    assert from_ledger == "1234 Ledger Ave", from_ledger


def test_a_household_count_field_gets_a_number_not_a_name(tmp_path, monkeypatch):
    """★ 2026-09-28 用户报的那一趟（`georgiapower`）：「How many people live in your household?
    他自己第一遍用的 mcp-cdp 明显知道填**数字**，产出的 py 就是填的**名字**」。

    根在两处，这条一起钉：
      ① `_RANDOM_HINTS` 里**没有**「人数」那一族 ⇒ 那一格认不出种类；
      ② `_fallback` 认不出时**一律给 `full_name`** ⇒ 往「家里几口人」里填人名 ✗✗。
    """
    from agent import browser_agent as ba
    assert ba._field_kind("How many people live in your household?", {}) == "number"
    #: ⚠️ 顺序是判据的一部分：别把「number」那一族挪到 phone / postcode 前面
    assert ba._field_kind("Mobile Phone Number", {}) == "phone"
    assert ba._field_kind("*Zip/Postal Code", {}) == "postcode"
    assert ba._fallback("value", "3", "How many people live in your household?", []) \
        == [{"random": "number"}]

    #: ② 认不出种类时：用**账本里那趟真填进去的值**；连值都没有 ⇒ **不给兜底**（不再给姓名）
    assert ba._fallback("value", "abc", "???", []) == ["abc"]
    assert ba._fallback("value", "", "???", []) == []

    #: ③ 产物那一侧要真给得出数字（骨架的池子）
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://us.test/q", "head": "Household", "progress": "",
            "inputs": [["\u0002How many people live in your household?", ""]],
            "options": [], "nextVisible": False, "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "countsite", "Thank you", "thank-you",
        {"how_many_people_live_in_your_home": {"kind": "number",
                                               "label": "How many people live in your household?"}},
        dict(PROV, fill_order=["how_many_people_live_in_your_home"]))
    path = tmp_path / "count.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("count_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid", no_report=True).run(max_steps=1)
    got = str(calls["form"][0][1].get("value") or "")
    assert got.isdigit(), "那一格拿到的不是数字：%r" % (got,)


def test_a_screen_the_light_reader_cannot_see_is_read_by_the_snapshot_fallback(tmp_path, monkeypatch):
    """★ 2026-09-28（`try` 那站真跑逼出来的）：轻量读取器读不到的那一屏，**换结构快照也要能动手**。

    现场：那一站的问卷第一屏，产物连着 60 步「没有可动作的东西」、答过 0 题 ⇒ 收摊；
    而同一屏探路那层（observe/screenshot）看得见 ⇒ 差的是**读取层**。
    这一条钉住兜底那一路：轻量 JS 空 ⇒ `snapshot()` 里挑出**能点的**与**能填的**，
    用**结构选择器**（`body > … > tag:nth-of-type(n)`）动手，并且**记进 trace**。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    #: 轻量读取器：这一屏**什么都没有**（就是那一站的真实形状）
    page = {"url": "https://funnel.test/survey", "head": "Hi there! I'm Jackie.",
            "progress": "", "inputs": [], "options": [], "nextVisible": False, "nextTxt": ""}
    #: 结构快照：选项是一个 `<label>`（**没有 testid、class 还是随机名**）+ 一个按 placeholder
    #: 认得出的输入框 ⇒ 选择器只能靠**结构**拼出来
    snap = {"frame": {"frameId": "F1", "body": {"tag": "BODY", "children": [
        {"tag": "DIV", "children": [
            {"tag": "DIV", "children": [{"tag": "LABEL", "text": "Myself"}]},
            {"tag": "INPUT", "attr": {"placeholder": "postcode"}}]}]}}}
    _fake_common(monkeypatch, page, calls, snap=snap)

    src = template_loop.render_loop("try", "Thank you", "thank-you",
                                    {"postcode": {"kind": "postcode"}}, dict(PROV))
    path = tmp_path / "fallback.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fallback_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    trace = tmp_path / "t.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid",
                           trace_path=str(trace), no_report=True)
    filler.run(max_steps=6)

    assert calls.get("snapshot", 0) >= 1, "轻量读取器读不到，兜底那一路没去读快照"
    assert calls["form"], "结构快照里那个输入框没填"
    sel = calls["form"][0][0]
    assert sel.startswith("body > div:nth-of-type(1)"), \
        "填的不是结构选择器（那站的 class 是随机名，靠不住）：%r" % sel
    assert calls["click"], "结构快照里那个选项没点"
    assert calls["click"][0].startswith("body > div:nth-of-type(1)"), calls["click"][0]
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any("结构化快照" in str(r.get("note") or "") for r in rows), \
        "兜底动手了却没记 trace：%r" % (rows[-3:],)

# ── 落点判据（★ 2026-10-16：循环型第一次有它 —— 与固定步骤表**同一把尺子**）──────────

def test_a_covered_pick_is_not_clicked_and_the_step_is_not_reported_ok(
        tmp_path, monkeypatch):
    """★ 本刀：循环型第一次有落点判据 —— 被盖着就**不点**，而且这一行照实写 `ok=False`。

    由头是 2026-09-18 gowizard 那条：点到盖着它的东西上，cdp 照样回 ok ⇒ 被记成做成了。
    """
    page = {"url": "https://funnel.test/quiz", "head": "Pick one", "progress": "50%",
            "inputs": [], "options": ["\u0001Yes"], "nextVisible": False, "nextTxt": ""}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod = _fake_common(monkeypatch, page, calls)
    _orig = mod.CDPHelper.eval
    #: 打标记那一段照旧回 `marked`；**量落点那一段**回「被盖着」。
    #: ⚠️ 回的是 `json.dumps(...)`（**带引号**）—— 真站 `cdp eval` 的 stdout 是 JSON，
    #: 字符串结果就是带引号的（见另一条「答复带引号 ⇒ 闸照样开」的用例）。
    mod.CDPHelper.eval = lambda self, js, *a, **kw: (
        json.dumps("COVER|div#onetrust-banner") if "elementsFromPoint" in js
        else _orig(self, js, *a, **kw))

    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "covered_loop.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("covered_loop_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COVER_WAIT_SECONDS = 0.3          # 别让用例真等
    module.COVER_POLL_SECONDS = 0.02
    trace = tmp_path / "covered_loop.jsonl"
    form = tmp_path / "form.json"; form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=4)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert not [c for c in calls["click"]], "被盖着还点下去了：%r" % calls["click"]
    picks = [r for r in rows if r.get("action") == "click"]
    assert picks and all(r.get("ok") is False for r in picks), picks
    assert any("盖着" in str(r.get("note") or "") for r in picks), picks


def test_a_covered_field_is_not_filled_and_the_row_names_the_cover(
        tmp_path, monkeypatch):
    """★ 本刀的另一半：`fill` 也是「动手」—— 被盖着就**不填**，这一格照实写 `ok=False`+点名。

    与上面那条同一个由头：**「被盖着」不是「没找到」** —— 一格填不上时，读日志的人得看得出
    是**谁**挡着（真事 `job-a6954761e657` 那三轮诊错全在改选择器，而那一刻产物连一次
    `cdp form` 都没发出去、框一直好好地在那儿）。
    """
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "10%",
            "inputs": [["email", ""]], "options": [], "nextVisible": False, "nextTxt": ""}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod = _fake_common(monkeypatch, page, calls)
    _orig = mod.CDPHelper.eval
    #: ⚠️ 同上面那条：回**带引号**的 JSON 字符串（真站 `cdp eval` 就是这么吐的）
    mod.CDPHelper.eval = lambda self, js, *a, **kw: (
        json.dumps("COVER|div#cookiescript_injected") if "elementsFromPoint" in js
        else _orig(self, js, *a, **kw))

    src = template_loop.render_loop("selsite", "Thank you", "thank-you",
                                    {"email": {"kind": "email"}}, dict(PROV))
    path = tmp_path / "covered_fill.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("covered_fill_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COVER_WAIT_SECONDS = 0.3          # 别让用例真等
    module.COVER_POLL_SECONDS = 0.02
    trace = tmp_path / "covered_fill.jsonl"
    form = tmp_path / "form.json"; form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=3)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert not [c for c in calls["form"]], "被盖着还填下去了：%r" % calls["form"]
    fills = [r for r in rows if r.get("action") == "form"]
    assert fills and all(r.get("ok") is False for r in fills), fills
    assert any("盖着" in str(r.get("note") or "") for r in fills), fills


def test_a_quoted_eval_answer_still_opens_the_gate(tmp_path, monkeypatch):
    """★ 2026-10-16 **复核 Critical**：`cdp eval` 的 stdout 是 **JSON**，字符串结果**带引号**
    ⇒ 表那套的 `_ev` 干**两件**事：包 `(function(){…})()` **和** `_clean_eval`（脱引号）。

    循环型这边 `cdp.eval` 两件都不做 ⇒ 两件都得在调用处补。只补了包壳那件的话，真站上
    `got == '"COVER|div#onetrust-banner"'` ⇒ `startswith("COVER|")` 恒假 ⇒ **闸门一次都不会开**，
    而账本上跟「没被盖着」一模一样（正是 blavity 那次假通过的形状）。

    这一条把**真站的答复形状**（`json.dumps(...)`，带引号）喂进去钉死它 ——
    用裸字符串当桩的话，这条闸永远测不出来（那正是第一版三条用例全绿的假象）。
    """
    page = {"url": "https://funnel.test/quiz", "head": "Pick one", "progress": "50%",
            "inputs": [], "options": ["\u0001Yes"], "nextVisible": False, "nextTxt": ""}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod = _fake_common(monkeypatch, page, calls)
    _orig = mod.CDPHelper.eval
    mod.CDPHelper.eval = lambda self, js, *a, **kw: (
        json.dumps("COVER|div#onetrust-banner") if "elementsFromPoint" in js
        else _orig(self, js, *a, **kw))

    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "quoted_loop.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("quoted_loop_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COVER_WAIT_SECONDS = 0.3          # 别让用例真等
    module.COVER_POLL_SECONDS = 0.02
    trace = tmp_path / "quoted_loop.jsonl"
    form = tmp_path / "form.json"; form.write_text("{}", encoding="utf-8")
    f = module.Filler("ws://x/1", str(form), "cid",
                      trace_path=str(trace), no_report=True)

    #: ① 读回来的**形状**：带引号的答复要脱引号（返回的是遮盖物本身，不是 `'"COVER|…"'`）
    got = f._cover_once('[data-sf-pick="1"]')
    assert got == "div#onetrust-banner", \
        "带引号的答复没被脱引号 ⇒ 真站上这条闸恒假：%r" % (got,)
    #: ② 闸**照样开**：被盖着就不动手（这一趟一步都不许点/填出去）
    f.run(max_steps=4)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert not [c for c in calls["click"]], "被盖着还点下去了：%r" % calls["click"]
    picks = [r for r in rows if r.get("action") == "click"]
    assert picks and all(r.get("ok") is False for r in picks), picks


def test_a_failure_that_was_not_the_cover_does_not_blame_the_cover(tmp_path, monkeypatch):
    """★ 2026-10-16 **复核 Important**：`cover_why` 是**这一次动手**的读数，不许外借。

    现场（复核用合成桩复现过）：① 那一格（email）真被 `div#onetrust-banner` 挡下 —— 这一行对 ✓；
    同一轮 ② 挑选项时 `click` **连一次落点都没量**（页面上根本没那段文字，打标记返 `none`
    就早退了）—— trace 却写「挑中的那一颗被 div#onetrust-banner 盖着」，**该说的是「没找到」**。

    表那套的守卫是 `if self.cover_why and not tried_any:`（试过并且失败了，那是另一回事），
    循环型这边靠**每次动手之前清零**拿到同一个语义。
    """
    page = {"url": "https://funnel.test/quiz", "head": "Pick one", "progress": "10%",
            "inputs": [["email", ""]], "options": ["\u0001Yes"], "nextVisible": False, "nextTxt": ""}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod = _fake_common(monkeypatch, page, calls)
    _orig = mod.CDPHelper.eval
    #: 落点那一段：① 的那一格**真的**被挡着；打标记那一段：那段文字页面上**没有**（回 `none`）
    mod.CDPHelper.eval = lambda self, js, *a, **kw: (
        json.dumps("COVER|div#onetrust-banner") if "elementsFromPoint" in js
        else ("none" if "data-sf-pick" in js else _orig(self, js, *a, **kw)))

    src = template_loop.render_loop("selsite", "Thank you", "thank-you",
                                    {"email": {"kind": "email"}}, dict(PROV))
    path = tmp_path / "mixed_loop.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mixed_loop_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COVER_WAIT_SECONDS = 0.3          # 别让用例真等
    module.COVER_POLL_SECONDS = 0.02
    trace = tmp_path / "mixed_loop.jsonl"
    form = tmp_path / "form.json"; form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=3)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fills = [r for r in rows if r.get("action") == "form"]
    assert any("盖着" in str(r.get("note") or "") for r in fills), \
        "① 那一格真被挡着，这一行该点名遮挡物：%r" % (fills,)
    picks = [r for r in rows if r.get("action") == "click"]
    assert picks, rows
    assert all("盖着" not in str(r.get("note") or "") for r in picks), \
        "② 这一下**没量过落点**（文字没找到），却拿①的遮挡物背锅：%r" % (picks,)


def test_the_cover_probe_is_wrapped_at_the_call_site_and_the_recenter_is_not(
        tmp_path, monkeypatch):
    """★ Ruling 6：那两段 JS 搬进循环型之后，**包不包由调用处负责**（两条都要钉住）。

    为什么要紧（这仓栽过）：循环型的 `CDPHelper.eval` 是 `subprocess.run([CDP_PATH,"eval",…])`，
    **不替调用方包** IIFE；而 `_COVER_JS` 是**裸语句序列**（里面一句裸 `return '';`）——
    直喂 `cdp eval` = `SyntaxError` ⇒ **恒返空** ⇒ 落点判据永远「量不到」，而账上看起来一切正常
    （2026-10-10 blavity 那次「负向对照通过」就是这么假通过的）。

    ⇒ · `_cover_once` 发出去的 JS **必须以 `(function(){` 开头**（调用处给它包了一层）；
       · `_recenter` 发出去的 JS **不许**再包一层（`_RECENTER_JS` **自带**壳，双层包裹当场坏）。
    顺带钉「产物里那两个常量有定义」—— 只搬字符串那一刀最容易漏的一格（漏了一跑就是 `NameError`）。
    """
    calls: dict = {}
    mod = _fake_common(monkeypatch, {"url": "https://x.test/", "head": "", "progress": "",
                                     "inputs": [], "options": [], "nextVisible": False,
                                     "nextTxt": ""}, calls)
    sent: list = []
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        sent.append(js)
        #: 量落点那一段回「没被盖着」—— 这一条只钉**发出去的形状**，不钉答话
        return "" if "elementsFromPoint" in js else _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_

    src = template_loop.render_loop("wrap-site", "Thank you", "thank-you", {}, dict(PROV))
    for name in ("_COVER_JS", "_RECENTER_JS"):
        assert "\n%s = " % name in src, "产物里没定义 %s —— 它一跑就是 NameError" % name
        assert "__%s__" % name not in src, "占位符没替掉（产物里留了一串 %s）" % name

    path = tmp_path / "wrap.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("wrap_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"; form.write_text("{}", encoding="utf-8")
    f = module.Filler("ws://x/1", str(form), "cid",
                      trace_path=str(tmp_path / "wrap.jsonl"), no_report=True)

    f._cover_once('[data-sf-pick="1"]')
    cover = [js for js in sent if "elementsFromPoint" in js]
    assert cover, "那一下没量落点：%r" % (sent,)
    assert cover[-1].startswith("(function(){") and cover[-1].endswith("})()"), (
        "裸语句序列直喂 `cdp eval` = SyntaxError ⇒ 恒返空（blavity 那次的假通过）：%r"
        % cover[-1][:120])
    assert cover[-1].count("(function(){") == 1, "包了两层：%r" % cover[-1][:120]

    f._recenter("#go")
    rec = [js for js in sent if "scrollIntoView" in js]
    assert rec, "那一下没挪动：%r" % (sent,)
    assert not rec[-1].startswith("(function(){(function(){"), (
        "`_RECENTER_JS` **自带** IIFE 壳，再包一层就是双层包裹（行为当场坏）：%r" % rec[-1][:120])
    assert rec[-1].count("(function(){") == 1, "双层包裹：%r" % rec[-1][:120]


# ── 弹窗钩子表（★ 2026-10-16 Task 4：循环型也按**形状**认弹层 —— 与表那套**同一张表**）────
#
# 形状照 `tests/test_template.py` 的 A / B 那两条（同一副骨架、同一批纪律）：
#   · 空串 = 表里一条都没答应（**正常情况**，页面上就是没有这类东西）⇒ 静默；
#   · 非空但拆不出三格 = **形状不认识** ⇒ 说出来（一行 log），但不当成「有弹层」去点；
#   · **永不失败**：探不动 / 点不动只记一行 INFO，它挨着每一步，不许自己变成新的失败点。
# ⚠️ 桩回的是 `json.dumps(...)`（**带引号**）—— 真站 `cdp eval` 的 stdout 是 JSON：
#    桩回裸字符串就是把生产不认的契约钉成绿的（2026-10-16 Task 3 复核逮到的那一条）。

#: 探针的答复：`"<钩子名>|<选择器|TEXT>|<按钮文字>"`（三格，表见 `_POPUP_HOOKS`）
CONSENT_ANSWER = "同意条|#onetrust-accept-btn-handler|Accept Cookies"
CONSENT_SELECTOR = "#onetrust-accept-btn-handler"
#: 挑中的那颗选项（`click()` 是「JS 打标记 → `cdp click` 那个标记」）
PICK_SELECTOR = '[data-sf-pick="1"]'
#: 桩靠它认出**弹窗探针**那一段 JS（`_popup_probe_js` 拼出来的那句「谁答应了」）。
#: ⚠️ 别用 `privacy` / `boxW` 认它：**读取器**那段 JS 里也有 `privacy`（它要读回「这一屏
#: 有没有同意层」）⇒ 拿它分流会把读这一屏也截走 ⇒ `read()` 恒 None ⇒ 用例全假红。
PROBE_MARK = "var hit=(function"
#: 桩靠它认出「按文字打标记」那一段（`_MARK_CONSENT_BY_TEXT_JS`）。
MARK_MARK = "var want="
#: 一个「一屏有选项」的假页面（每一轮都会走到 ② 挑一个并点它）
PICK_PAGE = {"url": "https://funnel.test/quiz", "head": "Pick one", "progress": "50%",
             "inputs": [], "options": ["\u0001Yes"], "nextVisible": False, "nextTxt": ""}


def _loop_product(tmp_path, monkeypatch, tag, page, calls, fills=None):
    """渲一份循环稿、装个假 common、把 `Filler` 造出来（新增那几条弹层用例共用这一小段）。

    ⚠️ 返回的 `mod` 是**假的 common 模块** —— 桩要改就在它上面改
    （`mod.CDPHelper.eval = …`），改完再 `filler.run(…)`。
    """
    mod = _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("selsite", "Thank you", "thank-you",
                                    dict(fills or {}), dict(PROV))
    path = tmp_path / ("%s.py" % tag)
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("%s_product" % tag, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid",
                           trace_path=str(tmp_path / ("%s.jsonl" % tag)), no_report=True)
    return mod, module, filler, src


def test_the_loop_dismisses_a_consent_overlay_before_it_starts(tmp_path, monkeypatch):
    """A（照表那套那条）：开跑之前**自己**把同意弹层点掉 —— 不许指望账本里恰好有这一步。

    为什么循环型也要它：生产**每单都是新窗口**、每次都清 cookie ⇒ 每单都会遇到同意弹层；
    而探索那一趟跑在一个「弹层已经点掉」的会话里 ⇒ 账本学的是**没有弹层的世界**。
    弹层盖住答题区时，`cdp click` 照样回 ok（那句回话说的是「选择器命中 1 个」，**不是**
    「点到的就是它」）⇒ 这一步被记成做成了、后面全塌。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, src = _loop_product(tmp_path, monkeypatch, "popup_start",
                                             PICK_PAGE, calls)
    assert "\n_POPUP_PROBE_JS = " in src, "产物里没那个常量 —— 一跑就是 NameError"
    popup_js: list = []
    seen = {"probes": 0}
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if PROBE_MARK in js:                   # 弹窗探针（钩子表那一段）
            popup_js.append(js)
            seen["probes"] += 1
            #: 第 1 次（开跑前）就答应；点掉之后照旧答「没有」（空串 = 正常情况）
            return json.dumps(CONSENT_ANSWER if seen["probes"] == 1 else "")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    filler.run(max_steps=2)

    clicks = calls["click"]
    assert CONSENT_SELECTOR in clicks, ("开跑前的弹层没点掉：%r" % clicks)
    assert clicks.index(CONSENT_SELECTOR) < clicks.index(PICK_SELECTOR), (
        "弹层要在**第一步动手之前**点掉（否则那一下就是点在它上面）：%s" % clicks)
    #: Ruling 6：`_POPUP_PROBE_JS` 是**裸语句序列**（表那套的 `_ev` 替它包壳）——
    #: 循环型的 `cdp.eval` 不包 ⇒ **调用处自己包**（不包 = 浏览器 SyntaxError ⇒ 恒返空）
    assert popup_js and all(j.startswith("(function(){") for j in popup_js), (
        "弹窗探针没在调用处包 IIFE：%r" % (popup_js[:1],))


def test_a_consent_overlay_that_shows_up_late_is_cleared_before_the_next_pick(
        tmp_path, monkeypatch):
    """B（travelandleisure 生产：25 条里 18 条一个形状）：弹层**是页面加载完之后才异步冒出来**的
    —— 只在「开跑前」看一眼**不够**，**每一次动手之前**都得再看一眼。

    真站日志原话（那 18 条）：
      第 2 个选择器指的元素被 div#onetrust-group-container.ot-sdk-eight 盖着 —— 这一下不点
      第 6 步：页面上没找到「Join Now」，这一步没做成
    ⇒ 这一条让弹层**只在前两次探针之后**才出现：开跑前那次、`run_pre()` 之后那次都答「没有」，
    到「动手之前」那次它才在 ⇒ 那一下必须先清掉它、再点选项。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "popup_late",
                                               PICK_PAGE, calls)
    seq = ["", "", CONSENT_ANSWER]             # 前两次「没有」；动手之前那一次才冒出来
    popup_js: list = []
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if PROBE_MARK in js:
            popup_js.append(js)
            return json.dumps(seq.pop(0) if seq else "")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    filler.run(max_steps=2)

    clicks = calls["click"]
    assert CONSENT_SELECTOR in clicks, ("动手之前那一次没探到弹层：%r" % clicks)
    assert clicks.index(CONSENT_SELECTOR) < clicks.index(PICK_SELECTOR), (
        "弹层要在**这一步动手之前**点掉 —— 否则那一下就是点在它上面：%s" % clicks)
    assert len(popup_js) >= 3, ("探针只问了 %d 次 —— 每一步动手之前那一处没挂上？"
                                % len(popup_js))


def test_an_answer_in_a_shape_we_do_not_know_is_spoken_but_not_clicked(
        tmp_path, monkeypatch, caplog):
    """表那套那条纪律照搬：**形状不认识 ≠ 没有弹层** —— 说出来（一行 log），但**不去点**。

    为什么不能点：拆不出三格就不知道「点谁」；猜一个选择器去点 = 「点到别的东西」，
    而那正是弹层这一整套要治的病。所以当它「没答」，照旧走这一步自己的判据。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "popup_shape",
                                               PICK_PAGE, calls)
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if PROBE_MARK in js:                   # 只有两格（真站上「量不出来」就是这种半截答复）
            return json.dumps("同意条|#onetrust-accept-btn-handler")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    with caplog.at_level(logging.INFO):
        filler.run(max_steps=1)

    assert calls["click"] == [PICK_SELECTOR], (
        "形状不认识的答复被当成「有弹层」去点了：%r" % (calls["click"],))
    assert "形状不认识" in caplog.text, (
        "答复形状不认识却一句话都没说（读日志的人会以为页面上没有这类东西）：\n%s"
        % caplog.text)
    assert "点掉了" not in caplog.text, (
        "形状不认识的答复被记成「点掉了」—— 那句话只有真点下去才许说：\n%s" % caplog.text)


def test_the_loop_wraps_the_popup_probe_and_the_mark_js_and_never_throws(
        tmp_path, monkeypatch):
    """`clear_popups()` 的三条硬规矩（表那套逐字搬过来的）各钉一下：

    ① **包壳在调用处**：`_POPUP_PROBE_JS` 与 `_MARK_CONSENT_BY_TEXT_JS` **都是裸语句序列**
       （里面有裸 `return`）⇒ 都必须包一层 IIFE 才喂 `cdp eval`；
    ② 点了返回 `True`、空串（表里一条都没答应）返回 `False` —— 空串是**正常情况**；
    ③ **永不抛**：探针读炸了也只是一行 INFO，不往上抛（它挨着每一步，往上抛会把这**一步**
       判成「产物自己出错了」—— 而弹层只是环境带来的噪声，不该有这种权力）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "popup_wrap",
                                               PICK_PAGE, calls)
    sent: list = []
    answer = {"now": json.dumps("")}
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        sent.append(js)
        if PROBE_MARK in js:
            return answer["now"]
        if MARK_MARK in js:                    # 按文字打标记那一段（`_MARK_CONSENT_BY_TEXT_JS`）
            calls.setdefault("marks", []).append(js)
            return json.dumps("marked")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_

    assert filler.clear_popups() is False, "空串（表里一条都没答应）不是失败，但也不是「点掉了」"
    answer["now"] = json.dumps(CONSENT_ANSWER)
    assert filler.clear_popups() is True, "点掉了要返回 True"
    assert calls["click"] == [CONSENT_SELECTOR], calls["click"]

    answer["now"] = json.dumps("同意条|TEXT|Accept All Cookies")   # 只有文字、没有 id
    assert filler.clear_popups() is True, "按文字打标记再点 —— 那也算点掉了"
    assert '[data-sf-consent="1"]' in calls["click"], (
        "没 id 的那颗要走「打标记再点」：%r" % calls["click"])
    assert calls.get("marks"), "没按文字打标记就点了？"

    answer["now"] = json.dumps("形状不认识的答复")                  # 一眼不是三格
    assert filler.clear_popups() is False, "形状不认识时不许乱点，也不许抛"

    def boom(self, js, *a, **kw):
        raise RuntimeError("cdp 挂了")         # 探针坏掉时的真站形状

    mod.CDPHelper.eval = boom
    assert filler.clear_popups() is False, "探不动就当成「没有」，**绝不往上抛**"
    assert all(j.startswith("(function(){") and j.endswith("})()")
               for j in sent if PROBE_MARK in j or MARK_MARK in j), (
        "裸语句序列直喂 `cdp eval` = SyntaxError ⇒ 恒返空：%r" % (sent[:2],))
