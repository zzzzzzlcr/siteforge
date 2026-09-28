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


def _fake_common(monkeypatch, page, calls):
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
    """★ 2026-09-25（用户点名）：号码要 **0207 开头**（伦敦座机形状），不是 `07…` 手机号。

    现场：真站那一格的校验收 0207；产物原来给 `07…`（位数一样合法）⇒ 填了过不去，
    停在那一屏。顺带钉住 Ofcom 影视保留段 `020 7946 0xxx` **不许**出现（格式合法、一眼是假的）。
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
        assert got.startswith("0207") and len(got) == 11, got
        assert not got.startswith("02079460"), "给了 Ofcom 影视保留段：%s" % got


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
    assert got.startswith("0207") and len(got) == 11, "没按 0207+7 位现编：%r" % (got,)
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
