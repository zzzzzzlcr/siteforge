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
import datetime
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

from agent import fix, selftest, template, template_loop  # noqa: E402

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


def test_at_least_one_criterion_is_required_and_the_text_one_can_stand_alone():
    """★ 2026-10-14（**用户裁定**「网址或正文至少有一个」）：**两格都空**才拒。

    ⚠️ 逐行交代（这一条原先写的是「这一族**只看网址** ⇒ 没给网址判据就不许产」）：
    那条护栏与**运行期**打架 —— 骨架里 `SUCCESS(url)` 与 `SUCCESS(text)` 本来就并排
    （同一轮里先判网址、再判正文），而 `render_loop` 自己的 docstring 也写着
    「`success_text` 给了就加一条兜底判据（URL 判据不成立、纯 SPA 不改地址时用得上）」。
    撞出这个口子的真事：`foodandwine` —— **交单之后地址不变**、只在原地把
    「Check your email」显示出来 ⇒ 正文判据读得出来、网址判据**没有**
    ⇒ 修站那条路在这一格**必然卡死**（而它连的是「修一份现成的 py」这条主路）。

    老手那句「正文判据是这一族栽跟头的**老地方**」仍然成立 —— 所以它是**兜底**，
    不是替掉网址：两格都给时网址仍是主判据。
    """
    #: ① 只有正文判据 ⇒ **许产**（这一版就是给「地址不变」那种站用的）
    src = _src(success_text="Check your email", success_urls="")
    assert "Check your email" in src, "正文判据要逐字写进去"
    assert "SUCCESS_URL_MARKERS = []" in src, \
        "网址那格空着就该是空的 —— 不许拿正文那串**冒充**网址判据（那会让它永远不成立）"
    #: ② 两格都空 ⇒ **照旧拒**：「跑到底再说自己成功」那个入口没被打开
    with pytest.raises(ValueError) as got:
        _src(success_text="", success_urls="")
    assert "成功判据不能空" in str(got.value)
    #: ③ 两格都给 ⇒ 网址仍是主判据（老口径的钉子，一个字没松）
    both = _src(success_text="Thank you", success_urls="thank-you")
    assert 'SUCCESS_URL_MARKERS = ["thank-you"]' in both, both[:400]


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



def test_the_phone_number_never_comes_from_the_profile_and_a_site_can_pin_its_area_code(
        tmp_path, monkeypatch):
    """★ 2026-10-13（用户点名，compareinsulation）：「手机号**不要用资料给的**。就用
    `0207xxxxxxx` 格式，后几位随机就行。」

    两件事，都钉在这条里：
    ① **不再取资料**：账本里那个号（`EXAMPLES`）是探路时从**资料**里拿的真号 —— 它就在产物的
       `PROVENANCE["examples"]` 里（本例 `07880539011`）。原来那一支**先查它**，与「那一格
       永远是现编的号码」这句注释打架 ⇒ 现在一律现编。
    ② **这一站要的号段**由 `FILLS[name]['prefix']` 给（`'0207'`）—— 尾数照旧随机，
       而**没给前缀的站**照旧从那一串号段里挑（2026-10-08 那个口径不能丢）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "", "inputs": [],
            "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    prov = dict(PROV, examples={"phone": "07880539011"})

    def _load(tag, fills):
        src = template_loop.render_loop("phonesite", "Thank you", "thank-you", fills, prov)
        path = tmp_path / ("%s.py" % tag)
        path.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("%s_product" % tag, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        obj = module.Filler.__new__(module.Filler)
        obj.form_data = {}
        return module, obj

    # ① 资料里那个号一个都别用上（拿它当答案就是被认出来的那种）
    module, obj = _load("pinned", {"phone": {"kind": "value", "label": "07xxx xxx xxx",
                                            "prefix": "0207"}})
    got = [obj.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(20)]
    assert "07880539011" not in got, "手机号用了资料里那个：%r" % got
    assert all(g.startswith("0207") and len(g) == 11 for g in got), \
        "这一站的号段不是 `0207`（或位数不对）：%r" % got
    assert len(set(got)) > 1, "尾数没随机（20 次全一样）：%r" % got
    #: 直接问池子也一样（同一条口径，两处别分家）
    assert all(module.Filler._pool(None, "phone", "0207").startswith("0207") for _ in range(20))

    # ② 没给前缀的站照旧从那一串号段里挑（2026-10-08 的口径没被这条弄丢）
    _mod2, obj2 = _load("free", {"phone": {"kind": "value", "label": "07xxx xxx xxx"}})
    free = [obj2.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(50)]
    assert "07880539011" not in free, free
    assert all(len(g) == 11 and g.isdigit() for g in free), free


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


def test_a_continue_that_does_not_take_is_retried_after_scrolling_it_into_view(
        tmp_path, monkeypatch):
    """★ 2026-10-13 真事（`job-e8bbc9b2612a`，compareinsulation 的问卷）。

    现场：那一屏是「What's your email?」（进度 83%），产物连点 3 次 Continue 页面一动不动 ⇒
    收摊。手量那一屏：`cdp click` 自己的回话里写着 `landing_blind: true`（「那个坐标上取不到
    节点 —— 点可能在**视口外**」）⇒ 抬手发出去了、没落在按钮上；把那一颗挪进视口再点就过。
    读取器那一层只判 `offsetWidth/Height > 0` —— **在视口外它也判「可见」**，空点的根就在这儿。

    又：原来那条路**写死 `ok=True`** ⇒ 账上每一次 Next 都是「成功」，失败那几次一点痕迹都没有，
    诊断只能判「不知道」（那一趟的诊断就是 `unknown`）。

    这条钉三件：① 第一下就是**手势**；② 同一屏再来一次时**先落定 + 先挪进视口**
    （`calls["scroll"]`）；③ 点没点出去**照实记**（标记打不上时那一行必须 `ok=False`）。
    ⚠️ 点法**始终是手势** —— `lint` 禁手拼 JS 点击（契约 §5.2），所以这条还兼着一条负控：
    产物里不许出现自己 `.click()` 的 JS。
    """
    page = {"url": "https://funnel.test/quiz", "head": "What's your email?", "progress": "83%",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    _fake_common(monkeypatch, page, calls)

    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "next_retry.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("next_retry_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = tmp_path / "next_retry.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=5)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    nexts = [r for r in rows if r.get("note") == "Next"]
    assert nexts, "这一屏一次 Next 都没点：这一条测不到东西"
    assert not nexts[0].get("target", {}).get("retry"), \
        "第一下就该是**普通手势**（重试那一下不许抢在前面）：%r" % nexts[0]
    assert nexts[0].get("ok") is True, nexts[0]
    assert any(n.get("target", {}).get("retry") for n in nexts), \
        "同一屏没改判据就反复点（该改成「先落定 + 挪进视口」）：%r" % nexts
    assert calls["scroll"], "重试那一下没有先把它挪进视口：%r" % calls
    assert all(c == '[data-sf-pick="1"]' for c in calls["click"]), \
        "点法不是「打标记 + cdp click」了：%r" % calls["click"]
    assert "e.click()" not in src, "产物里出现手拼 JS 点击 —— 契约 §5.2 禁它（lint 会打回）"

    # 负控：标记打不上（页面上没那段文字）⇒ 这一行必须照实写 ok=False，不许伪装成成功
    calls2 = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod2 = _fake_common(monkeypatch, page, calls2)
    #: 只让**打标记那一段**回 `none`（读取那一层照旧回页面）—— 全回 `none` 的话产物连页面都读不到，
    #: 走的是「页面读不到」那条路，这一条就测不到「点了没点上」。
    _orig2 = mod2.CDPHelper.eval
    mod2.CDPHelper.eval = lambda self, js, *a, **kw: (
        "none" if "setAttribute('data-sf-pick'" in js else _orig2(self, js, *a, **kw))
    src2 = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path2 = tmp_path / "next_none.py"
    path2.write_text(src2, encoding="utf-8")
    spec2 = importlib.util.spec_from_file_location("next_none_product", path2)
    module2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module2)
    trace2 = tmp_path / "next_none.jsonl"
    module2.Filler("ws://x/1", str(form), "cid",
                   trace_path=str(trace2), no_report=True).run(max_steps=5)
    rows2 = [json.loads(ln) for ln in trace2.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("note") == "Next" and r.get("ok") is False for r in rows2), \
        "没点上，账上却没有一条 ok=False：%r" % [r for r in rows2 if r.get("note") == "Next"]


def test_the_same_next_counter_resets_when_the_screen_changes(tmp_path, monkeypatch):
    """★ 2026-10-13 真事（`job-e8bbc9b2612a`）：一道「连着点 N 次没变就收摊」的闸，把**换屏**
    读成了「同一屏」。

    现场：那一族的 `head` 是**页面标题**（每屏都长一样：「Free Eligibility Checker」）、选项也都
    是空的 ⇒ 旧判据（题面 + 选项）在 postcode / name / email 三屏上是**同一个字符串** ⇒ 计数器
    **跨屏累加**：postcode、name 两屏各点过一次之后，**email 那一屏第一次点之前**就数到 3 ⇒ 收摊
    一次都没试（trace 里 step 11/14 都写着 `retry: True` —— 它们根本不在同一屏上）。

    ⇒ 判「同一屏」得看**看得见的形状**：题面 + 选项 + **进度** + **这一屏有哪几个格**。
    这条钉住它：三屏各自换过之后，产物**每一屏都还点得动**（不是数到 3 就收摊）。
    """
    screens = [
        {"url": "https://funnel.test/quiz", "head": "Free Eligibility Checker", "progress": "50%",
         "inputs": [["\u0002e.g. NN3 3AQ", ""]], "options": [], "nextVisible": True,
         "nextTxt": "Continue"},
        {"url": "https://funnel.test/quiz", "head": "Free Eligibility Checker", "progress": "72%",
         "inputs": [["\u0002e.g. Margaret Johnson", ""]], "options": [], "nextVisible": True,
         "nextTxt": "Continue"},
        {"url": "https://funnel.test/quiz", "head": "Free Eligibility Checker", "progress": "83%",
         "inputs": [["\u0002e.g. margaret@example.co.uk", ""]], "options": [], "nextVisible": True,
         "nextTxt": "Continue"},
    ]
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod = _fake_common(monkeypatch, screens[0], calls)
    seen = {"n": 0}

    def eval_(self, js, *a, **kw):
        if "setAttribute" in js:                    # 打标记那一段
            return "marked"
        #: ★ 2026-10-16（Task 4）：**只有「读这一屏」那一段推进场景** —— 桩原来把每一次
        #: `eval` 都当成一次读，而产物动手之前还会探好几次（落点 / 弹层）⇒ 场景被那些探针
        #: 推着走，三屏提前走完 ⇒ 第 6 步撞上「连着 3 次点 Next、页面还是那一屏」那道闸
        #: （`_stop` 那一行也是 `action="click"`、`ok=False`）⇒ 这条用例的判据被**桩**带红。
        #: 「按读取器那段 JS 认读」是这个文件里本来就有的认法（同 `"nextVisible" in js`）。
        if "nextVisible" not in js:                 # 别的探针（落点 / 弹层）：这一屏没有那些东西
            return json.dumps("")
        seen["n"] += 1
        return json.dumps(screens[min((seen["n"] - 1) // 2, len(screens) - 1)])

    mod.CDPHelper.eval = eval_
    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "screens.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("screens_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = tmp_path / "screens.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=6)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    nexts = [r for r in rows if r.get("note") == "Next"]
    assert len(nexts) >= 4, \
        "换了两屏之后就被那道闸收摊了（计数器跨屏累加）：%r" % [(r.get("step"), r.get("target")) for r in nexts]
    assert not any(r.get("action") == "click" and r.get("ok") is False for r in rows), rows


def test_a_consent_layer_that_shows_up_mid_run_is_cleared_before_acting(tmp_path, monkeypatch):
    """★ 2026-10-13（用户口径：**修系统**，不是修某一份）：「同意条这类**啥时候冒出来都不确定**」。

    循环这一族是「读到什么做什么」—— 中途才冒出来的同意条，原来会被读取器当成**一个可点的
    选项**（`pick_option` 随机挑：挑中「Accept All」算运气，挑中「管理偏好」就点进一个弹窗里
    空转 ✗）。现在两处都收住：
      ① 读取器**不把同意那几颗当选项**（读回来那一格 `consent` 只是「这一屏有同意层」）；
      ② 每一轮**动手之前**先清掉它，清不掉**不算失败**（同意层是环境噪声）。

    现场：第 1 读说有同意层、清掉之后就没有了 —— 钉的是「清完了还接着把题答下去」。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    seen = {"reads": 0, "cleared": False}
    page = {"url": "https://funnel.test/quiz", "head": "Question 1", "progress": "20%",
            "inputs": [], "options": ["\u0001No", "\u0001Yes"], "nextVisible": False, "nextTxt": ""}
    mod = _fake_common(monkeypatch, page, calls)

    def eval_(self, js, *a, **kw):
        if "setAttribute('data-sf-pick'" in js:          # 打标记再点（同意那颗也走这条）
            calls.setdefault("marks", []).append(js)
            return "marked"
        if "still-here" in js:                           # 「同意条还在吗」那一读
            return "gone" if seen["cleared"] else "still-here"
        if "nextVisible" in js:                          # 读这一屏
            seen["reads"] += 1
            got = dict(page, consent=(seen["reads"] == 1))
            return json.dumps(got)
        return json.dumps(page)

    mod.CDPHelper.eval = eval_
    #: 点一下就算清了（真实的 `clear_cookies` 会复读确认，这里让复读说「gone」）
    orig_click = mod.CDPHelper.click

    def click_(self, sel, *a, **kw):
        seen["cleared"] = True
        return orig_click(self, sel, *a, **kw)

    mod.CDPHelper.click = click_
    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "consent.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("consent_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = tmp_path / "consent.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=4)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "popup" for r in rows), \
        "这一屏有同意层，账上却没有那一步：%r" % rows[:4]
    marks = " ".join(calls.get("marks") or [])
    assert "Accept All Cookies" in marks, \
        "清同意层那一下点的不是同意那几颗之一：%r" % (calls.get("marks"),)[:2]
    assert any(str(r.get("note") or "").startswith("答了第") for r in rows), \
        "清完同意层之后没有接着答题（被软跳过的那一步不该结束这一趟）：%r" % rows


def test_stop_writes_the_why_into_the_note_too(monkeypatch, tmp_path):
    """★ 2026-10-14：`_stop` 收摊时那句话**两格都写** —— `progress_why` 与 `note`。

    为什么要两格：`progress_why` 是「**有没有推进**」那一路读的（`steps.layer_of`），
    `note` 是「**为什么没做成**」那一路读的（`selftest._verdict` 的 `step_said`）。
    只写前者 ⇒ 诊断落成兜底话「trace 里那一步没写为什么」⇒ **模型拿着那句话去改稿，
    改不出任何东西**（它不知道要改什么）。实测 4 趟就是这么丢的
    （`job-0efb84117920` / `job-c053e0b049f3` / `job-cf1d84521a09` / `job-fc0d7a398eff`）。

    真事的 trace 行逐字（`easystorebuilderpro-20260925-192239`）：
        {"step": 60, "action": "give_up", "ok": false, "progress": null,
         "progress_why": "走满 60 步还没到成功"}

    ⚠️ 这一份骨架（`template_loop.py`）是**一个字符串**：`class Filler` 长在
    `_LOOP_SKELETON` 里面，模块顶层拿不到它（`hasattr(template_loop, "Filler")` 是 False）
    —— 所以只能**渲出来、exec 出来**再叫它（与这一份里其他用例同一个法子）。
    """
    calls: dict = {}
    _fake_common(monkeypatch, {"url": "https://x.test/", "head": "", "progress": "",
                               "inputs": [], "options": [], "nextVisible": False,
                               "nextTxt": ""}, calls)

    src = _src()
    path = tmp_path / "stop_note.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("stop_note_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows: list = []
    obj = module.Filler.__new__(module.Filler)
    obj._trace = lambda **row: rows.append(row)      # 只钉收摊那一行，别的都不碰
    obj._stop(60, "give_up", "走满 60 步还没到成功")

    assert rows[0]["note"] == "走满 60 步还没到成功", rows[0]
    assert rows[0]["progress_why"] == "走满 60 步还没到成功", (
        "原来那一格照旧要写 —— 别为了补 note 把 progress_why 挤掉", rows[0])
    assert rows[0]["ok"] is False and rows[0]["step"] == 60 and rows[0]["action"] == "give_up"


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


def test_the_loop_never_acts_on_the_report_only_row_or_on_an_empty_text(
        tmp_path, monkeypatch):
    """★ 2026-10-17（**终审 C2 · Ruling 30 + Ruling 28-②**）：两条「**不点**」的口径。

    表那套那半在 `tests/test_template.py`（`test_the_report_only_row_is_reported_but_never_
    clicked` / `test_a_text_answer_with_no_text_is_never_clicked`）—— 这一条钉的是
    **循环型同一把尺子**（两套骨架一个口径，谁也不许只说不做）：
      · 表里带 `_REPORT_ONLY` 的测试行（广告 creative 2026-10-19 已毕业）⇒ 只报不点 ——
        真站上曾经量到它误点（实测 §1.3）；
      · 答复是 `TEXT|` 而**文字是空的** ⇒ 不点（那是「指向不确定元素的地址」）；
      · 负控：**表里没有那个名字**的同形状答复 ⇒ 照旧点 —— 挡住它的必须是
        「名字 + 标志」，不是「答复的形状」。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "popup_report_only",
                                               PICK_PAGE, calls)
    #: ★ 2026-10-19：广告 creative 那一行**已毕业**（可点 + 站点锚）⇒ 用临时测试行钉标志机制。
    hook = "return '#fake-report|Close';"
    module._POPUP_HOOKS = module._POPUP_HOOKS + (("假只报", hook, module._REPORT_ONLY),)
    module._POPUP_PROBE_JS = module._popup_probe_js(module._POPUP_HOOKS)
    answer = {"now": json.dumps("假只报|#fake-report|Close")}
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if PROBE_MARK in js:
            return answer["now"]
        if MARK_MARK in js:                    # 按文字打标记那一段
            calls.setdefault("marks", []).append(js)
            return json.dumps("marked")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    trace = tmp_path / "popup_report_only.jsonl"

    # ① `_REPORT_ONLY` 那一行：认得出、说得清、**不动手**
    assert filler.clear_popups() is False, "只报不点 ⇒ 没有「关掉了」这回事"
    assert calls["click"] == [], (
        "带 `_REPORT_ONLY` 的那一行**不许动手**（真站上量到过误点）：%r" % (calls["click"],))
    assert not calls.get("marks"), "连标记那一半也不该做（不动手就是不动手）"
    row = _last_popup_row(trace)
    assert row.get("ok") is None and "只报不点" in (row.get("note") or ""), (
        "账上要记一行、并说清为什么没点：%r" % (row,))

    # ② `TEXT|` 而文字是空的：指向不确定元素的地址 ⇒ 不点
    #: ⚠️ 挑「同意条」那一行（**没有** `_REPORT_ONLY` 标志）—— 用测试只报行的话先命中
    #: 「只报不点」那一支，两条路都**不点**，但**为什么**不点是两件事。
    answer["now"] = json.dumps("同意条|TEXT|")
    assert filler.clear_popups() is False
    assert calls["click"] == [] and not calls.get("marks"), calls
    row2 = _last_popup_row(trace)
    assert row2.get("ok") is None and "不确定" in (row2.get("note") or ""), (
        "账上照实记：那是个指向不确定元素的地址、没敢点：%r" % (row2,))

    # ③ 负控：同一句话、**表里没有那个名字** ⇒ 照旧点（挡住它的是名字+标志，不是形状）
    answer["now"] = json.dumps(CONSENT_ANSWER)
    assert filler.clear_popups() is True, "表里没有那一行 ⇒ 照旧点（不许被上面两条顺带挡住）"
    assert calls["click"] == [CONSENT_SELECTOR], calls["click"]


# ── 关掉之后**复核** + 空转的报文纪律（★ 2026-10-16：Task 5）──────────────────────────

def _popup_rows(trace):
    """账本里 `action="popup"` 的那几行（`clear_popups` 复核那一行就在里面）。"""
    if not trace.exists():
        return []
    return [r for r in (json.loads(ln) for ln in
                        trace.read_text(encoding="utf-8").splitlines() if ln.strip())
            if r.get("action") == "popup"]


def _last_popup_row(trace):
    """最后一「关掉/复核」那一行 —— 没有就说清「一行都没落」，别让它长成 IndexError。"""
    rows = _popup_rows(trace)
    assert rows, '账本里一行 `action="popup"` 都没有 —— `clear_popups` 没落复核那一行'
    return rows[-1]


def test_the_popup_recheck_says_which_of_the_three_things_is_true(tmp_path, monkeypatch):
    """★ 本刀：**`cdp click` 回话不算做成** —— 点完要**再问一遍同一张表**，把「还在不在」如实写进账本。

    为什么不许拿回话当做成（blavity 那条口径）：`cdp click` 的那句 ok 说的是
    「**选择器命中 1 个**」，**不是**「点到的就是它」—— 弹层盖着的时候点了个寂寞，账上照旧是做成。

    三分（由头是 Task 1 在真站上量到的那条「覆盖是**间歇**的」：同一个站**首载有同意层、
    来回趟一趟之后同一条探针返空**）：
      · 探之前有、探之后没有 ⇒ `ok=True`（真关掉了）；
      · 探针**返空** ⇒ `ok=None` + 说清「**它本来就不在**」—— **不许**写成「关掉了」
        （返空分不清「我关掉了」与「它本来就没弹」）；
      · 探之后**还有** ⇒ `ok=False`（点了个寂寞）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "popup_recheck",
                                               PICK_PAGE, calls)
    #: ★ 2026-10-19：广告 creative 那一行**已毕业**（可点 + 站点锚）⇒ 复核「只报不点」那一格
    #: 改用一条临时测试行；否则 `_popup_report_only` 不再认它，测试的前提就翻了。
    hook = "return '#fake-report|Close';"
    module._POPUP_HOOKS = module._POPUP_HOOKS + (("假只报", hook, module._REPORT_ONLY),)
    module._POPUP_PROBE_JS = module._popup_probe_js(module._POPUP_HOOKS)
    trace = tmp_path / "popup_recheck.jsonl"
    seq: list = []
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if PROBE_MARK in js:            # 弹窗探针：按剧本答（`json.dumps` = 真站的带引号形状）
            return json.dumps(seq.pop(0) if seq else "")
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_

    #: ① 探之前有、探之后没有 ⇒ True
    seq[:] = [CONSENT_ANSWER, ""]
    assert filler.clear_popups() is True, "点掉了要返回 True（返回值只说「点了」，判据在账上）"
    assert calls["click"] == [CONSENT_SELECTOR], calls["click"]
    row = _last_popup_row(trace)
    assert row["ok"] is True, "关掉了却没写 True：%r" % (row,)
    assert row["progress"] is None, row
    assert "没有" in str(row.get("progress_why") or ""), row

    #: ② 探针**返空** ⇒ None + 说清「它本来就不在」（**不许**当成「关掉了」）
    seq[:] = [""]
    assert filler.clear_popups() is False, "空串（表里一条都没答应）不是失败，也不是「点掉了」"
    row = _last_popup_row(trace)
    assert row["ok"] is None, ("「探针返空」被当成了「关掉了」或「还在」——"
                              "Task 1 量到的那条间歇性要的就是这一格：%r" % (row,))
    assert "本来就不在" in str(row.get("progress_why") or ""), row

    #: ③ 点完**还有** ⇒ False —— 就算 cdp 回话是「点上了」，也不许写成做成
    #: （给足应答：复核现在会在短有界时间内多探几次，别让脚本在半路「用完了」）
    seq[:] = [CONSENT_ANSWER] * 10
    assert filler.clear_popups() is True, "这一下**是发出去了**（返回值只说这个）"
    row = _last_popup_row(trace)
    assert row["ok"] is False, ("cdp 回话是「点上了」就写成了做成？—— 点了个寂寞：%r" % (row,))
    assert "还有" in str(row.get("note") or ""), row
    #: ④ 复核只答「只报不点」那一行 ⇒ 不算「还有」：True
    #: ★ 2026-10-19（真站 wildnutrition）：点掉同意条之后，复核的整张表先答的是一个
    #: `_REPORT_ONLY` 行（当时是广告 creative；它 2026-10-19 已毕业，这里用测试行）——
    #: 它把一次**真的关掉了**记成了 `ok:false`。复核只认**可点**的那几行。
    seq[:] = [CONSENT_ANSWER, "假只报|#fake-report|Close"]
    assert filler.clear_popups() is True, "这一下**是发出去了**（返回值只说这个）"
    row = _last_popup_row(trace)
    assert row["ok"] is True, (
        "复核答的是 `_REPORT_ONLY` 行（它本来就不挡路）—— 不许记成「还有」：%r" % (row,))
    assert "没有" in str(row.get("progress_why") or ""), row
    #: ⑤ 复核第一次还答「同意条」、紧接着答空 ⇒ True（同意层的关闭是异步的）
    #: ★ 2026-10-19（真站 compareinsulation）：点 accept 之后，紧接的那一次再探还会
    #: 看到 `#cookiescript_accept`；再晚一点点就没了。立即判 False 会把一次真的关掉了
    #: 记成「这一下没能关掉」—— 复核要等一个**短的**有界时间，不是只探一眼。
    seq[:] = [CONSENT_ANSWER, CONSENT_ANSWER, ""]
    assert filler.clear_popups() is True, "这一下**是发出去了**（返回值只说这个）"
    row = _last_popup_row(trace)
    assert row["ok"] is True, (
        "复核第一次还答、紧接着答空——这是异步关掉，不许记成「还有」：%r" % (row,))
    assert "没有" in str(row.get("progress_why") or ""), row
    #: ⚠️ 这一行是 `soft` 的：弹层是**环境噪声**（新窗口有、上一趟点掉过就没有、还可能后冒出来）
    #: ⇒ 与 `browser_agent` 给「点掉同意条」那类步标的记号同一档 —— 自测不拿它否决那一遍
    #: （`selftest._verdict` 的 `hard_rows` 排除 `soft`），但照旧点名（spec §3.5：探针自己
    #: **永远不许变成新的失败点**）。
    assert row.get("soft") is True, "复核那一行不许否决整趟自测：%r" % (row,)


def test_a_round_that_did_nothing_because_of_a_cover_says_so(tmp_path, monkeypatch):
    """★ 本刀：空转那一句**先看 `cover_why`** —— 被挡着就照实说「X 一直盖着…」，
    **不许**落成「没有可动作的东西」。

    为什么（2026-09-18 gowizard，与 Task 3 那条同一个病）：那一趟诊断全花在改选择器上 ——
    账本里写着「没有可动作的东西」，而那一刻屏幕上明明有东西，只是**被盖着**。
    `cover_why` 是**这一步动手之前**量的读数（每轮开头清零、每次动手前清零 —— Ruling 13）
    ⇒ 走到这句话时它还是**这一步**的值。
    """
    page = {"url": "https://funnel.test/quiz", "head": "Pick one", "progress": "50%",
            "inputs": [], "options": ["\u0001Yes"], "nextVisible": False, "nextTxt": ""}
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    mod, module, filler, _src_ = _loop_product(tmp_path, monkeypatch, "covered_note",
                                               page, calls)
    module.COVER_WAIT_SECONDS = 0.3          # 别让用例真等 35 秒
    module.COVER_POLL_SECONDS = 0.02
    _orig = mod.CDPHelper.eval
    mod.CDPHelper.eval = lambda self, js, *a, **kw: (
        json.dumps("COVER|div#onetrust-banner") if "elementsFromPoint" in js
        else _orig(self, js, *a, **kw))

    trace = tmp_path / "covered_note.jsonl"
    filler.run(max_steps=2)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    idle = [r for r in rows if r.get("action") == "none"]
    assert idle, "这一趟一步都没动、账上却连一行空转都没有：%r" % (rows,)
    said = " ".join("%s %s" % (r.get("note") or "", r.get("progress_why") or "") for r in idle)
    assert "盖着" in said, ("被挡着却没说「谁盖着」（读的人只会去查选择器）：%r" % (idle,))
    assert "没有可动作的东西" not in said, (
        "被挡着被说成了「没有可动作的东西」—— 屏幕上明明有东西：%r" % (idle,))
    assert not any("没找到" in "%s %s" % (r.get("note") or "", r.get("progress_why") or "")
                   for r in rows), "把「被挡着」说成了「没找到」：%r" % (rows,)


# ── 钩子表第 4 行「广告 creative」：两套骨架**同一份**（★ 2026-10-16：Task 6）──────────

def _ad_creative_segment(src: str) -> str:
    """从一段**产物文本**里切出 `_AD_CREATIVE_JS` 那颗常量自己的正文（切到表那段之前）。

    ⚠️ 不按整份源码找记号（`elementsFromPoint` 在产物里本来就有好几处）—— 只切这一颗。
    """
    i = src.index("_AD_CREATIVE_JS = (")
    j = src.index("_POPUP_HOOKS = (", i)
    return src[i:j]


def _popup_hook_rows(src: str) -> list:
    """表那一块的**行名**，按序（`_POPUP_HOOKS = (` 到它那个右括号为止）。

    ⚠️ **不写死表里有几条**（工作区比 HEAD 多别人未提交的那条）：只问
    「新那条在不在、是不是**最后**一条」—— 次序 = 优先级，这才是要钉的性质。
    ⚠️ ★ 2026-10-17（终审 C2）：行是**两格或三格** —— 第三格 = `_REPORT_ONLY`
    ⇒ 正则里那第三格**可选**（否则带标志的那一行会从表里「消失」）。
    ⚠️ ★ 2026-10-19：第四格 = **站点锚**（字符串，默认空串）—— 正则里也**可选**。
    """
    block = src[src.index("_POPUP_HOOKS = ("):]
    block = block[:block.index("\n)")]
    pattern = r'''\("([^"]+)", _[A-Z_]+(?:, (?:_[A-Z_]+|'[^']*'|"[^"]*"))?(?:, "[^"]*")?\)'''
    return re.findall(pattern, block)


def test_the_loop_carries_the_same_ad_creative_row_the_table_does():
    """★ 本刀（Task 6）：表里那第 4 行**两套骨架共用同一份** —— 循环型这份也得带它。

    同源怎么证：循环型产物里那一段与**发源地**（`template._POPUP_MACHINERY_SRC`）里那一段
    **逐字节相同** —— `render_loop` 就是把这个常量原样贴进 `__POPUP_MACHINERY__`
    （Task 4 那一刀的账）。表那套渲出来的另一半住在
    `tests/test_template.py::test_the_ad_creative_hook_is_one_row_and_both_skeletons_get_it`。

    ⚠️ 循环型**不另抄一份判据**（spec §3.1「判据只写一份」）：它只从 `agent.template` 取；
    这里比的正是「取的是不是同一份」，不是「长得像不像」。
    """
    src = _src()
    assert "_AD_CREATIVE_JS = (" in src, "循环型产物里没有那颗常量（同源破了）"
    #: ★ 2026-10-17（终审 C2 · Ruling 30）：这一行曾经带第三格标志（只报不点）。
    #: ★ 2026-10-19（毕业）：**第三格 = 站点锚**（可点，两个已知锚并集）——
    #: 两套骨架贴的是同一份机器码 ⇒ 这一格也得一模一样。
    assert ("(\"广告 creative\", _AD_CREATIVE_JS, '[data-click=\"close\"], .op2-close'),"
            in src), "循环型产物的表里没有第 4 行（或丢了站点锚）"
    assert "def _popup_report_only(" in src, "按名字查标志的那个函数得在（同源破了）"
    rows = _popup_hook_rows(src)
    assert rows and rows[-1] == "广告 creative", (
        "表序 = 优先级：它要排在**最后**（与表那套同一份表）：%r" % (rows,))
    assert _ad_creative_segment(src) == _ad_creative_segment(template._POPUP_MACHINERY_SRC), (
        "循环型那份与发源地不是同一段（两把尺子分家了）：各自从产物里切出来比一比")


def test_value_for_rule_ignores_form_data_and_is_stable(tmp_path, monkeypatch):
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "PhoneNumber",
                   "rule": "phone_prefix", "prefixes": ["02"]}},
        dict(PROV))
    path = tmp_path / "rule_phone.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_phone_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"phone": "0000000000"}
    obj._rule_values = {}
    got = [obj.value_for("\u0002PhoneNumber", {}, 0) for _ in range(3)]
    assert len(set(got)) == 1, got          # 同一趟同值
    assert re.fullmatch(r"02\d{8}", got[0]), got


def test_value_for_dob_rule_works_in_loop(tmp_path, monkeypatch):
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"dob": {"kind": "value", "label": "Date of birth",
                 "rule": "dob_random", "date_format": "DD/MM/YYYY"}},
        dict(PROV))
    path = tmp_path / "rule_dob.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_dob_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {}
    obj._rule_values = {}
    got = obj.value_for("\u0002Date of birth", {}, 0)
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", got), got
    day, month, year = (int(x) for x in got.split("/"))
    today = datetime.date.today()
    age = today.year - year - ((today.month, today.day) < (month, day))
    assert 18 <= age <= 68, (got, age)


def test_value_for_rule_survives_the_index_fallback(tmp_path, monkeypatch):
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"password": {"kind": "value", "label": "Password",
                      "rule": "password_strong"}},
        dict(PROV, fill_order=["password"]))
    path = tmp_path / "rule_password_fallback.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_password_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"password": "weak"}
    obj._rule_values = {}
    got = obj.value_for("\u0002Unrelated", {}, 0)
    assert got != "weak", got
    assert re.fullmatch(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[!@#$%^&*]).{8,}$",
                        got), got
