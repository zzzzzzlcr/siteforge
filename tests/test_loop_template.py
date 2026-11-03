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


def test_two_success_urls_can_travel_in_one_field():
    """★ 2026-10-22（用户裁定「不合资格也算成功」）：Atticus 那种问卷**两种结局都算成功**
    （合格屏 `.../qual/post-conversion-ah` + 拒绝屏 `.../survey/rejection/`）。

    面板只有一格 ⇒ **一行一条**；产物里两条都要进 `SUCCESS_URL_MARKERS`（任一命中即成功）。
    """
    src = _src(success_urls="https://x.test/qual\nhttps://x.test/rejection/")
    assert ('SUCCESS_URL_MARKERS = ["https://x.test/qual", "https://x.test/rejection/"]'
            in src), src[:500]


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


def test_react_auto_ids_do_not_win_over_stable_placeholders(tmp_path):
    """★ 2026-10-28（Atticus `How can we reach you?` 的真事）：React 的自动 id `:r1:`…

    **会整排漂移**（账本 `:r1:`=First … `:r4:`=Phone，线上那一屏变成 `:r2:`…`:r5:`）。
    读取器按 id 认格 ⇒ 每格拿到**前一格**的值（邮箱进姓氏、电话进邮箱、年龄进电话）
    ⇒ 邮箱/电话校验红字、Submit 永远灰着（task 26829734 的 fail_img 就是这个现场）。

    判据：**自动 id 不算身份** —— 有 placeholder / aria-label 就按那个认；
    稳定 id（`zipcode` / `qv3-fn`）照旧优先。这条**真跑 JS**（node + 假 DOM），
    不是断言字符串：字符串断言活不过一个写错的实现。
    """
    node = shutil.which("node")
    assert node, "这条必须真跑 JS（没有 node 就是红，不是跳过）"
    js_path = tmp_path / "read.js"
    js_path.write_text(template_loop.DEFAULT_READ_JS, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(r"""
const fs = require('fs');
const READ = fs.readFileSync(process.argv[2], 'utf8');
function el(id, ph, val) {
  return {tagName:'INPUT', id:id||'', value:val||'', offsetWidth:10, offsetHeight:10,
          getAttribute:function(n){
            if(n==='placeholder')return ph||null;
            if(n==='aria-label')return null;
            if(n==='name')return null;
            if(n==='data-testid')return null;
            if(n==='type')return 'text';
            return null; }};
}
const inputs=[el(':r2:','First name'),el(':r3:','Last name'),el(':r4:','Email address'),
              el(':r5:','Phone number'),el('zipcode','ZIP code'),el('qv3-fn',null)];
const document={querySelectorAll:function(s){return s==='input,select,textarea'?inputs:[];},
                querySelector:function(){return null;}, body:{innerText:''},
                getElementById:function(){return null;}};
const location={href:'https://example.test/'};
console.log(eval(READ));
""", encoding="utf-8")
    done = subprocess.run([node, str(harness), str(js_path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]
    tokens = [pair[0] for pair in json.loads(done.stdout)["inputs"]]
    assert tokens == ["\x02First name", "\x02Last name", "\x02Email address",
                      "\x02Phone number", "\x04zipcode", "\x04qv3-fn"], tokens


def test_the_consent_hint_reads_identity_not_footer_text(tmp_path):
    """★ 2026-11-02（educationdirectory 真站，8100 那趟）：读取器原来把容器 `innerText`
    也算进「同意层」的凭据 —— 页脚那行「Privacy Policy」把整页判成有同意层 ⇒ 每一轮都去
    清、清不掉、`idle` 清零 ⇒ 第 10–126 步全是同一句，走满上限。

    判据收窄成**只看 id / class / aria-label**：页脚（身份里没有那组词）不算；真同意条
    （`cookie-banner`）照旧算。这条**真跑 JS**（node + 假 DOM）。
    """
    node = shutil.which("node")
    assert node, "这条必须真跑 JS（没有 node 就是红，不是跳过）"
    js_path = tmp_path / "read.js"
    js_path.write_text(template_loop.DEFAULT_READ_JS, encoding="utf-8")
    harness = tmp_path / "consent.js"
    harness.write_text(r"""
const fs = require('fs');
const READ = fs.readFileSync(process.argv[2], 'utf8');
function el(a) {
  a = a || {};
  return {offsetWidth:10, offsetHeight:10, id:a.id||'', className:a.className||'',
          position:a.position||'static',
          innerText:a.innerText||'', textContent:a.innerText||'',
          getAttribute:function(n){return (a.attrs||{})[n]||null;},
          querySelectorAll:function(){return [];}, querySelector:function(){return null;}};
}
function readWith(target) {
  global.document = {
    querySelectorAll:function(s){ return /footer/.test(s) ? [target] : []; },
    querySelector:function(){ return {innerText:''}; },
    body:{innerText:''},
    getElementById:function(){return null;}};
  global.getComputedStyle = function(){ return {position: target.position || 'static'}; };
  global.location = {href:'https://example.test/'};
  return JSON.parse(eval(READ));
}
const footer = readWith(el({className:'lp-legal', innerText:'Privacy Policy'}));
const inline = readWith(el({className:'lp-consent',
                            innerText:'By clicking "Agree" I consent to be contacted'}));
const banner = readWith(el({className:'cookie-banner', innerText:'We use cookies',
                            position:'fixed'}));
console.log(JSON.stringify([footer.consent, inline.consent, banner.consent]));
""", encoding="utf-8")
    done = subprocess.run([node, str(harness), str(js_path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]
    assert json.loads(done.stdout) == [False, False, True], done.stdout



def test_an_explicit_submit_button_is_the_next_action(tmp_path):
    """★ 2026-11-02（educationdirectory 真站，8100 那趟）：最后一颗写的是
    「Agree & See My Schools」—— 不匹配 next 那几个词，又命中「同意条」排除规则
    （`agree` 开头）⇒ 读成「没有可动作的东西」，六题答完、格子填完之后空转 60 步。

    判据加结构那一条：`<button type="submit">` 就是「下一步」。这条真跑 JS。
    """
    node = shutil.which("node")
    assert node, "这条必须真跑 JS（没有 node 就是红，不是跳过）"
    js_path = tmp_path / "read2.js"
    js_path.write_text(template_loop.DEFAULT_READ_JS, encoding="utf-8")
    harness = tmp_path / "submit.js"
    harness.write_text(r"""
const fs = require('fs');
const READ = fs.readFileSync(process.argv[2], 'utf8');
const btn = {tagName:'BUTTON', offsetWidth:100, offsetHeight:30, disabled:false,
  id:'submit-button', className:'lp-action', textContent:'Agree & See My Schools',
  getAttribute:function(n){ if(n==='type')return 'submit'; return null; },
  querySelectorAll:function(){return [];}, querySelector:function(){return null;}};
global.document = {
  querySelectorAll:function(s){ return s==='button,label,[role=button]' ? [btn] : []; },
  querySelector:function(){ return {innerText:''}; },
  body:{innerText:''}, getElementById:function(){return null;}};
global.location = {href:'https://example.test/contact'};
const r = JSON.parse(eval(READ));
console.log(JSON.stringify([r.nextVisible, r.nextTxt, r.options]));
""", encoding="utf-8")
    done = subprocess.run([node, str(harness), str(js_path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]
    assert json.loads(done.stdout) == [True, "Agree & See My Schools", []], done.stdout


def test_a_select_with_no_value_picks_a_real_option_from_the_page(tmp_path, monkeypatch):
    """★ 2026-11-02（educationdirectory 真站，8100 那趟）：探路把这一格的名字退成
    `field-5`（没有账本示例、池子也不认识 `education_level`）⇒ 值算不出来 ⇒ 这一格空着
    ⇒ Next 被表单校验拦住。下拉的值**本来就在页面上**：挑第一个可选项。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/quiz", "head": "School Search", "progress": "80%",
            "inputs": [["\u0004education_level", ""]], "options": [], "nextVisible": False,
            "nextTxt": ""}

    def form_out(sel, kw):
        if sel == '[id="education_level"]':
            return "Error: scroll mouse wheel failed: element not found"
        page["inputs"] = [["\u0004education_level", "bachelors"]]
        return '{"success":true,"value":"bachelors"}'

    mod = _fake_common(monkeypatch, page, calls, form_out=form_out)
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if "!!(es.length" in js:                 # `_is_select`：只有 name 那根地址存在
            return json.dumps({"s": "name=" in js})
        if "e.options.length" in js:             # `_any_select_option`
            return json.dumps({"v": "bachelors", "t": "Bachelor's degree"}
                              if "name=" in js else {})
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    src = template_loop.render_loop(
        "selsite", "Thank you", "thank-you", {"education_level": {"kind": "select"}},
        dict(PROV))
    path = tmp_path / "selsite2.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("select_product2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "selsite2.jsonl"
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace), no_report=True).run(max_steps=1)

    kinds = [(sel, kw.get("select")) for sel, kw in calls["form"]]
    assert any(sel == '[name="education_level"]' and select == "bachelors"
               for sel, select in kinds), "没有从页面上挑一个真实选项：%r" % (kinds,)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows


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
    """★ 判「单页漏斗」的那把尺子是**量到的**：多少个状态停在**同一个网址**上。

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


def test_pages_that_only_share_a_url_prefix_are_not_a_single_page_funnel():
    """★ 2026-10-21（真事 `job-6203bc32c1c4` · directmeds）：**同一目录下的多页问卷不是单页漏斗**。

    现场：`_when_for` 的稳定前缀把 `article-3in1-tab-ed.php` / `index-stc-2.php` /
    `questionnaire-1..18.php` 全截成了 `https://directmeds.com/dm-directmax/` ⇒
    按 `url_contains` 分组时它们成了「18 个状态共用同一个地址」⇒ 判成步进循环型。
    而循环型的 `pre_steps` 只收「漏斗之前」那些状态 ⇒ 落地页那两下**点进问卷的 CTA**
    （`See If You Qualify` / `Get Started`）一个都没进去 ⇒ 产物在落地页上空转 60 步。

    判据要的是**真的同一页**（同一屏靠正文换题）：账本里逐页的 `url`（query/fragment 不算）
    才是它。地址只共享目录前缀 ⇒ 多页导航型，照旧固定步骤表。
    """
    base = "https://directmeds.com/dm-directmax/"
    pages = [{"name": "article_3in1_tab_ed", "url": base + "article-3in1-tab-ed.php",
              "when": {"url_contains": base, "text_contains": ["After 15 Years"]}},
             {"name": "index_stc_2", "url": base + "index-stc-2.php",
              "when": {"url_contains": base, "text_contains": ["Get 33% off"]}}]
    pages += [{"name": "questionnaire_%d" % i, "url": base + "questionnaire-%d.php" % i,
               "when": {"url_contains": base, "text_contains": ["Question %d" % i]}}
              for i in range(1, template_loop.LOOP_MIN_STATES + 1)]
    got = template_loop.funnel_states(_book(pages))
    assert not got["states"], got
    assert "只有 1 个状态" in got["why"], got["why"]


def test_a_single_page_funnel_still_counts_when_only_the_query_changes():
    """★ 反向钉子：**真的单页漏斗**每一屏只有 query 在变（`?step=`）—— 那还是同一页，别判丢。

    这条与上一条是同一条判据的两侧：**path 相同 ⇒ 同一页**（query / fragment 不算），
    **path 不同 ⇒ 不同的页**。少了这一条，把 key 换成 path 之后会把真漏斗判成多页。
    """
    pages = []
    for i in range(template_loop.LOOP_MIN_STATES):
        pages.append({"name": "quiz-%d" % i,
                      "url": "https://quiz.example.test/start?step=%d" % (i + 1),
                      "when": {"url_contains": "https://quiz.example.test/start",
                               "text_contains": ["Question %d" % i]}})
    got = template_loop.funnel_states(_book(pages))
    assert got["states"] == ["quiz-0", "quiz-1", "quiz-2"], got
    assert got["url"] == "https://quiz.example.test/start", got


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


def _fake_common(monkeypatch, page, calls, snap=None, on_form=None, click_out=None,
                 on_click=None, form_out=None):
    """一个够用的假 `common`：产物 import 的就是它（只记调用，不碰真浏览器）。

    `on_form(sel, kw)`：填完那一下的副作用（测试拿它模拟「值被页面弄丢 / 留住」）。
    `click_out`：`cdp click` 的回话（默认成功那段 JSON；给 `"Error: …"` 演没点着）。
    `on_click(sel)`：`cdp click` 的副作用（模拟点了之后页面变了、或没变）。
    """
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
            if "getOwnPropertyDescriptor" in js and "keypress" in js:
                #: ★ 2026-10-29：`_set_value_events` 那一招（原生 setter + 事件）。
                #: 记下 JS 本身 —— 断言「打的是同一个值」要看里面那个 JSON 字面量。
                calls.setdefault("events", []).append(js)
                return json.dumps({"ok": True, "value": "retyped"})
            return json.dumps(page)

        def snapshot(self):
            """结构化那一路（兜底）读的就是它 —— 不给就抛（产物的兜底会「读不回来」）。"""
            calls["snapshot"] = calls.get("snapshot", 0) + 1
            if snap is None:
                raise RuntimeError("这个桩没给快照")
            return snap

        def click(self, sel, *a, **kw):
            calls["click"].append(sel)
            if on_click is not None:
                on_click(sel)
            return ('{"match_count":1,"target":"stub"}' if click_out is None
                    else click_out)

        def form(self, sel, **kw):
            calls["form"].append((sel, kw))
            if on_form is not None:
                on_form(sel, kw)
            return "ok" if form_out is None else form_out(sel, kw)

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



def test_a_phone_fill_takes_the_profile_unless_the_script_pins_an_area_code(
        tmp_path, monkeypatch):
    """★ 2026-10-22（用户：「手机号肯定要取资料」；「别取资料」只是**个别特殊脚本**的处理）。

    ① 默认：资料给了电话就用**资料那一串**（推翻 2026-09-26/10-13 的「一律现编」）；
    ② 特殊脚本用 `prefix`（例 `0207`）或 `from_profile: false` 点名「这一站要现编」
       ⇒ 不取资料、尾数随机；
    ③ 没资料、没前缀 ⇒ 照旧从池子的那串号段里挑（2026-10-08 的口径没丢）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "", "inputs": [],
            "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    prov = dict(PROV, examples={"phone": "07880539011"})

    def _load(tag, fills, form=None):
        src = template_loop.render_loop("phonesite", "Thank you", "thank-you", fills, prov)
        path = tmp_path / ("%s.py" % tag)
        path.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("%s_product" % tag, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        obj = module.Filler.__new__(module.Filler)
        obj.form_data = form if form is not None else {}
        return module, obj

    # ① 默认：资料优先（账本里那个真号不再压过资料）
    _m, obj = _load("profile", {"phone": {"kind": "value", "label": "07xxx xxx xxx"}},
                    {"phone": "2125550147"})
    got = [obj.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(5)]
    assert got == ["2125550147"] * 5, "没取资料里的电话：%r" % got

    # ② prefix 是「特殊脚本」的信号：现编、不用资料
    module, pinned = _load("pinned", {"phone": {"kind": "value", "label": "07xxx xxx xxx",
                                               "prefix": "0207"}},
                           {"phone": "2125550147"})
    got = [pinned.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(20)]
    assert "2125550147" not in got and "07880539011" not in got, \
        "点名现编的站还是取了资料：%r" % got
    assert all(g.startswith("0207") and len(g) == 11 for g in got), \
        "这一站的号段不是 `0207`（或位数不对）：%r" % got
    assert len(set(got)) > 1, "尾数没随机（20 次全一样）：%r" % got
    #: 直接问池子也一样（同一条口径，两处别分家）
    assert all(module.Filler._pool(None, "phone", "0207").startswith("0207") for _ in range(20))

    # ②b 显式 `from_profile: false` 也一样（没有 prefix 的特殊脚本走这一格）
    _m3, optout = _load("optout", {"phone": {"kind": "value", "label": "07xxx xxx xxx",
                                             "from_profile": False}},
                        {"phone": "2125550147"})
    assert "2125550147" not in [optout.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0)
                                for _ in range(5)]

    # ③ 没资料、没前缀 ⇒ 池子（2026-10-08 的口径没被这条弄丢）
    _mod2, obj2 = _load("free", {"phone": {"kind": "value", "label": "07xxx xxx xxx"}})
    free = [obj2.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(50)]
    assert "07880539011" not in free, free
    assert all(len(g) == 11 and g.isdigit() for g in free), free

    # ④ 站点国家 US、资料没电话 ⇒ 10 位美国形状（不是英国 11 位）；ZIP 那格也能给同一信号
    us_src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "07xxx xxx xxx"}},
        dict(prov, env={"proxy_country": "US"}))
    us_path = tmp_path / "us_phone.py"
    us_path.write_text(us_src, encoding="utf-8")
    us_spec = importlib.util.spec_from_file_location("us_phone_product", us_path)
    us_module = importlib.util.module_from_spec(us_spec)
    us_spec.loader.exec_module(us_module)
    us_obj = us_module.Filler.__new__(us_module.Filler)
    us_obj.form_data = {}
    us = [us_obj.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(50)]
    assert all(len(g) == 10 and g.isdigit() for g in us), us
    assert all(g[3:8] == "55501" for g in us), "不是 NANP 保留段形状：%r" % (us,)

    #: 没有代理国家时，资料里那个五位 ZIP 也能说明是 US 站
    us_obj.form_data = {"zip": "60601"}
    zipped = [us_obj.value_for("\u0002e.g. 07xxx xxx xxx", {}, 0) for _ in range(10)]
    assert all(len(g) == 10 and g.isdigit() for g in zipped), zipped



def test_a_us_phone_placeholder_decides_the_country_without_the_proxy(
        tmp_path, monkeypatch):
    """★ 2026-11-02（8099 `job-1b5767017cc7`）：`PROVENANCE["env"]` 是 `None`，
    资料也没有 ZIP ⇒ 国家只剩**页面自己**这一处信号：phone 那格的
    placeholder `(555) 555-5555` 是 NANP。池子必须给 10 位美国号，
    否则 US 站把 11 位英国号压成 `(019) 164-7517`，回读对不上。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "Phone number",
                   "target": {"placeholder": "(555) 555-5555"}}}, dict(PROV))
    path = tmp_path / "phone_placeholder.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("phone_placeholder_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    got = [module.Filler._pool(None, "phone") for _ in range(50)]
    assert all(len(g) == 10 and g.isdigit() for g in got), got
    assert all(g[3:8] == "55501" for g in got), got


def test_a_zip_fill_takes_the_pools_postal_code_not_the_ledger_example(
        tmp_path, monkeypatch):
    """★ 2026-10-29（try.atticus.com/ssd/benefits，线上 26919522 / 26919524）。

    资料库的列名是 **`postal_code`**（`QuestFormMessageController::getFormMessage`），
    而循环产物是照**页面字段名** `postcode` 去查的 —— 两边没有约定 ⇒ 永远取不到
    ⇒ 静默落到账本示例 `10001`（`EXAMPLES['postcode']`）。用户口径：
    「需要取资料给的邮编」。真事：那两单的截图里邮编永远是 10001，
    资料里给的邮编一次都没上过页面。

    这一条钉住：资料给 `postal_code`，那一格就得填它，不是账本示例。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    prov = dict(PROV, examples={"postcode": "10001"})
    src = template_loop.render_loop(
        "zipsite", "Thank you", "thank-you",
        {"postcode": {"kind": "value", "label": "ZIP code",
                      "target": {"selectors": ["#zipcode"]}}}, prov)
    path = tmp_path / "zip_alias.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("zip_alias_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"postal_code": "90210"}
    got = obj.value_for("\u0004zipcode", {}, 0)
    assert got == "90210", "资料里的 postal_code 没被用上（又填了账本示例）：%r" % got


def test_a_phone_fill_takes_the_pools_phone_number(tmp_path, monkeypatch):
    """★ 2026-10-29（同一类）：资料库的键是 `phone_number`，产物查的是 `phone`
    ⇒ 电话也一次没取过资料。与固定步骤表 `agent/template.py::_ALIASES` 同一条规则。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/reach", "head": "How can we reach you?",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    prov = dict(PROV, examples={"phone": "07880539011"})
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "Phone number",
                   "target": {"selectors": ['[id=":r4:"]']}}}, prov)
    path = tmp_path / "phone_alias.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("phone_alias_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"phone_number": "2125550147"}
    got = obj.value_for("\u0004:r4:", {}, 0)
    assert got == "2125550147", "资料里的 phone_number 没被用上：%r" % got


def test_first_and_last_name_split_the_pools_username(tmp_path, monkeypatch):
    """★ 2026-10-29（同一类）：资料只给一个全名 `username: "Lin Cameron"`，
    而页面是 First / Last 两格 —— 光靠别名对不上，得按空格拆。
    与固定步骤表 `agent/template.py::_SPLIT_FROM` 同一条规则。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/reach", "head": "How can we reach you?",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "namesite", "Thank you", "thank-you",
        {"first_name": {"kind": "value", "label": "First name",
                        "target": {"selectors": ['[id=":r1:"]']}},
         "last_name": {"kind": "value", "label": "Last name",
                       "target": {"selectors": ['[id=":r2:"]']}}}, dict(PROV))
    path = tmp_path / "name_split.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("name_split_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"username": "Lin Cameron"}
    assert obj.value_for("\u0004:r1:", {}, 0) == "Lin"
    assert obj.value_for("\u0004:r2:", {}, 0) == "Cameron"


def test_a_fill_takes_the_source_key_first(tmp_path, monkeypatch):
    """★ 2026-10-29：探路给的 `source`（2026-09-30 起就是**资料库列名**）排第一。

    现场形状：页面字段叫 `zip_field`（账本按页面起的名），资料里叫 `postal_code`。
    光靠别名表接不住（`zip_field` 不是任何别名），但 `source` 里已经写着真列名了 ——
    取值的第 0 优先必须看它，而不是拿 `FILLS` 的键去猜。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "zipsite", "Thank you", "thank-you",
        {"zip_field": {"source": "postal_code", "kind": "value", "label": "ZIP code",
                       "target": {"selectors": ["#zipcode"]}}}, dict(PROV))
    path = tmp_path / "source_first.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("source_first_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"postal_code": "90210"}
    got = obj.value_for("\u0004zipcode", {}, 0)
    assert got == "90210", "source 里写着 postal_code，却没用上：%r" % got


def test_the_fill_says_where_the_value_came_from(tmp_path, monkeypatch):
    """★ 2026-10-29：**填上了 ≠ 填对了** —— 这一格的值是「任务资料」还是「账本示例/池子」，
    必须让人在日志里一眼看见（固定步骤表 2026-09-28 就是这么做的，循环这边一直缺）。

    现场代价：两个月里日志一直写「填了 zipcode」，值却永远是账本示例 `10001`。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "zipsite", "Thank you", "thank-you",
        {"postcode": {"kind": "value", "label": "ZIP code",
                      "target": {"selectors": ["#zipcode"]}}},
        dict(PROV, examples={"postcode": "10001"}))
    path = tmp_path / "value_from.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("value_from_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"postal_code": "90210"}
    assert obj.value_for("\u0004zipcode", {}, 0) == "90210"
    assert "postal_code" in obj._value_from and "资料" in obj._value_from, obj._value_from

    #: 资料里没有这一格 ⇒ 说出来「这是账本示例 / 池子」，不许装成资料
    obj2 = module.Filler.__new__(module.Filler)
    obj2.form_data = {}
    assert obj2.value_for("\u0004zipcode", {}, 0) == "10001"
    assert "账本示例" in obj2._value_from, obj2._value_from


def test_render_marks_fills_that_cannot_come_from_the_profile(tmp_path, monkeypatch):
    """★ 2026-10-29：生成期就把「这格取不到资料」标出来。

    `FILLS` 的键/`source`/`kind` 既不是资料列名、也没有别名、又没 `rule` /
    `from_profile: false` ⇒ 写进 `PROVENANCE['profile_unmapped']`，
    别等到截图里发现填的是账本示例。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    _fake_common(monkeypatch, {"url": "", "head": "", "inputs": [], "options": []}, calls)

    def _load(tag, fills):
        src = template_loop.render_loop("probe", "Thank you", "thank-you", fills, dict(PROV))
        path = tmp_path / ("%s.py" % tag)
        path.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("%s_product" % tag, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    bad = _load("unmapped", {"weird_field": {"kind": "value"}})
    assert bad.PROVENANCE.get("profile_unmapped") == ["weird_field"], bad.PROVENANCE

    #: 认得出的（别名键 / source 列名）一个都不许标；rule / from_profile:false 也不算
    good = _load("mapped", {
        "postcode": {"kind": "value"},
        "zip_field": {"source": "postal_code", "kind": "value"},
        "age": {"kind": "value"},
        "special": {"kind": "value", "from_profile": False},
        "pin": {"kind": "value", "rule": "phone_prefix", "prefixes": ["04"]},
    })
    assert good.PROVENANCE.get("profile_unmapped") == [], good.PROVENANCE


def test_an_age_field_is_random_unless_the_profile_gives_one(tmp_path, monkeypatch):
    """★ 2026-10-22（用户：资料里没有年龄，这一格要**随机**）。

    ① 资料没给 `age` ⇒ 每趟现取一个 50–64 的值（账本里那个 `58` 不再写死）；
    ② 资料给了 ⇒ 听资料的。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "What's your age?", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)

    def _load(tag, form):
        src = template_loop.render_loop(
            "agesite", "Thank you", "thank-you",
            {"age": {"kind": "value", "label": "Age"}},
            dict(PROV, examples={"age": "58"}))
        path = tmp_path / ("%s.py" % tag)
        path.write_text(src, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("%s_product" % tag, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        obj = module.Filler.__new__(module.Filler)
        obj.form_data = form
        return module, obj

    _m, obj = _load("agefree", {})
    got = [obj.value_for("\u0002Age", {}, 0) for _ in range(30)]
    assert all(g.isdigit() and 50 <= int(g) <= 64 for g in got), got
    assert len(set(got)) > 1, "年龄还是写死的（30 次全一样）：%r" % got

    _m2, obj2 = _load("ageprofile", {"age": "41"})
    assert obj2.value_for("\u0002Age", {}, 0) == "41"


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


def test_a_screen_that_adds_required_follow_ups_is_answered_group_by_group(
        tmp_path, monkeypatch):
    """★ 2026-11-02（educationdirectory 真站，`job-e004a054663b`）：**同一屏会追加题**。

    那一屏（`data-step-id=area`）有 12 个复选框；选中「Still deciding」之后，站点在**同一屏**
    追加两颗必答的 Yes/No（`rn_license` / `teaching_certificate`）。旧实现两处一起栽：
      · 签名 `head + 选项[:160]` 把追加的选项截掉了 ⇒ 下一轮读成「这一屏答过了」；
      · 就算不截，两组 Yes/No 文字一模一样，按文字点永远点第一组。
    ⇒ Next 点三次、页面一动不动（trace 里 step 5/6/7 那三次）。

    这条钉：读取器交 `unanswered`（还没选过的组）⇒ **每组答一次**，答完才去点 Next；
    而且两组 Yes 各自按 name/value 定位（不是同一颗点三下）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    area = "\u0005Still deciding\u0006area_of_interest\u0007general"
    rn_yes = "\u0005Yes\u0006rn_license\u0007rn"
    rn_no = "\u0005No\u0006rn_license\u0007no"
    tc_yes = "\u0005Yes\u0006teaching_certificate\u0007yes"
    tc_no = "\u0005No\u0006teaching_certificate\u0007no"
    page = {"url": "https://funnel.test/get-started", "head": "It only takes 60 seconds",
            "progress": "60%", "inputs": [], "options": [area, rn_yes, rn_no, tc_yes, tc_no],
            "unanswered": [area, rn_yes, rn_no, tc_yes, tc_no],
            "nextVisible": True, "nextTxt": "Next Step"}

    def on_click(sel):
        if sel != '[data-sf-pick="1"]':
            return
        js = next((m for m in reversed(calls.get("marks") or [])
                   if "deepAll('input')" in m), "")
        if "area_of_interest" in js:
            page["unanswered"] = [rn_yes, rn_no, tc_yes, tc_no]
        elif "rn_license" in js:
            page["unanswered"] = [tc_yes, tc_no]
        elif "teaching_certificate" in js:
            page["unanswered"] = []

    _fake_common(monkeypatch, page, calls, on_click=on_click)
    src = template_loop.render_loop("selsite", "Thank you", "thank-you", {}, dict(PROV),
                                    pick="first")
    path = tmp_path / "followups.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("followups_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    trace = tmp_path / "followups.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid",
                  trace_path=str(trace), no_report=True).run(max_steps=8)

    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    answered = [r for r in rows if str(r.get("note") or "").startswith("答了第")]
    assert len(answered) == 3, ("三组各答一次（area / rn_license / teaching_certificate）：%r"
                                % [(r.get("step"), r.get("note")) for r in rows])
    marks = calls.get("marks") or []
    assert any("rn_license" in m for m in marks), "第一组 Yes/No 没按 name 定位：%r" % (marks,)
    assert any("teaching_certificate" in m for m in marks), (
        "第二组 Yes/No 没被点到（按文字点只会点第一组）：%r" % (marks,))
    assert any('"Next Step"' in m for m in marks), "三组答完之后没去点 Next：%r" % (marks,)


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
    #: 真站填完就留在格子里 —— 桩也得这么演，否则 2026-10-22 那条「填完回读」会如实判它没填上。
    _fake_common(monkeypatch, page, calls,
                 on_form=lambda sel, kw: page.__setitem__(
                     "inputs", [["\u0004qv3-fn", str(kw.get("value") or "")]]))
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


def test_an_input_that_only_has_a_name_is_filled_by_name(tmp_path, monkeypatch):
    """★ 2026-11-02（educationdirectory 真站，8100 那趟）：读取器把 `id` 与 `name` 都写成
    `\\x04…`，而填的那一支只拼 `[id=…]` —— 那三个格只有 `name`（`zip_code` /
    `education_level` / `high_school_graduation_year`）⇒ `cdp form` 报
    `scroll mouse wheel failed: element not found`，Next 点三次全是空点。

    这条钉：id 找不到时必须**再用同一个 token 按 name 找一次**，而且找到就填上。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/quiz", "head": "School Search", "progress": "80%",
            "inputs": [["\u0004zip_code", ""]], "options": [], "nextVisible": False,
            "nextTxt": ""}

    def form_out(sel, kw):
        if sel == '[id="zip_code"]':
            return "Error: scroll mouse wheel failed: element not found"
        page["inputs"] = [["\u0004zip_code", str(kw.get("value") or "")]]
        return "ok"

    _fake_common(monkeypatch, page, calls, form_out=form_out)
    src = template_loop.render_loop(
        "namesite", "Thank you", "thank-you", {"zip_code": {"kind": "postcode"}},
        dict(PROV, fill_order=["zip_code"]))
    path = tmp_path / "namesite.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("name_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "namesite.jsonl"
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace), no_report=True).run(max_steps=1)

    sels = [sel for sel, _kw in calls["form"]]
    assert '[id="zip_code"]' in sels, "没先按 id 找：%r" % (sels,)
    assert '[name="zip_code"]' in sels, "id 找不到之后没按 name 再找一次：%r" % (sels,)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows


def test_a_native_select_is_filled_with_select_not_value(tmp_path, monkeypatch):
    """★ 2026-11-02（educationdirectory 真站，8100 那趟）：`education_level` /
    `high_school_graduation_year` 是原生 `<select>` —— `cdp form --value` 走 insertText，
    打不进 select；回读的 `value` 又是选项的 value（`bachelors`）而不是我们给的文字
    （`Bachelor's degree`）⇒ 永远判「没填上」。这一条钉：select 走 `--select`，且不回读。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/quiz", "head": "School Search", "progress": "80%",
            "inputs": [["\u0004education_level", ""]], "options": [], "nextVisible": False,
            "nextTxt": ""}
    mod = _fake_common(monkeypatch, page, calls,
                       form_out=lambda sel, kw: (
                           page.__setitem__("inputs", [["\u0004education_level", "bachelors"]])
                           or '{"success":true,"value":"bachelors"}'))
    _orig = mod.CDPHelper.eval

    def eval_(self, js, *a, **kw):
        if "!!(es.length" in js:          # `_is_select` 那一段（不是读取器里那个 LABEL 判断）
            return json.dumps({"s": True})
        return _orig(self, js, *a, **kw)

    mod.CDPHelper.eval = eval_
    src = template_loop.render_loop(
        "selsite", "Thank you", "thank-you", {"education_level": {"kind": "education"}},
        dict(PROV, examples={"education_level": "Bachelor's degree"}))
    path = tmp_path / "selsite.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("select_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "selsite.jsonl"
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace), no_report=True).run(max_steps=1)

    kinds = [(sel, kw.get("select"), kw.get("value")) for sel, kw in calls["form"]]
    assert any(select == "Bachelor's degree" and value is None
               for _sel, select, value in kinds), "select 走了 --value：%r" % (kinds,)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows


def test_a_fill_that_does_not_stick_is_filled_again(tmp_path, monkeypatch):
    """★ 2026-10-22（Atticus 真站）：`cdp form` 回 `{"ok":true}`，回读 `#zipcode.value` 却是 `""` ——
    React 在 hydration 时把早期写进去的值覆盖掉，Next 一直禁用，后面连点 8 次全是空点。

    这条钉：填完之后**回读**；值不在（或读出来是空的）就再填一次 ——
    「发出去」不等于「留在页面上」。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/landing", "head": "What's your ZIP code?", "progress": "",
            "inputs": [["\u0004zipcode", ""]], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    state = {"n": 0}

    def on_form(sel, kw):
        state["n"] += 1
        if state["n"] >= 2:            # 第 1 次写进去被页面弄丢；第 2 次留住
            page["inputs"] = [["\u0004zipcode", str(kw.get("value") or "")]]

    _fake_common(monkeypatch, page, calls, on_form=on_form)
    src = template_loop.render_loop(
        "sticksite", "Thank you", "thank-you",
        {"postcode": {"kind": "postcode", "label": "ZIP code"}},
        dict(PROV, fill_order=["postcode"], examples={"postcode": "10001"}))
    path = tmp_path / "sticksite.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("stick_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "t.jsonl"
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace),
                  no_report=True).run(max_steps=1)

    sels = [sel for sel, _kw in calls["form"]]
    assert sels.count('[id="zipcode"]') == 2, \
        "值没留住却只填了一次（%d 次）：%r" % (sels.count('[id="zipcode"]'), sels)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows
    assert page["inputs"][0][1] == "10001", page


def test_a_value_that_disappears_is_filled_again(tmp_path, monkeypatch):
    """★ 2026-10-22（Atticus 真站）：第一趟 `_filled` 记了「这一格填过」——
    可页面在下一轮读出来是**空的**（值被 hydration / 重渲染弄丢）。

    这条钉：页面上空了就是空了，「填过」的记号不成立 ⇒ 抹掉它、用**同一个值**再填一次
    （不是换个新值，也不是永远不再填）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    token, value = "\u0004zipcode", "10001"
    page = {"url": "https://x.test/landing", "head": "What's your ZIP code?", "progress": "",
            "inputs": [[token, ""]], "options": [], "nextVisible": True, "nextTxt": "Next"}

    def on_form(sel, kw):
        page["inputs"] = [[token, str(kw.get("value") or "")]]

    _fake_common(monkeypatch, page, calls, on_form=on_form)
    src = template_loop.render_loop(
        "wipedsite", "Thank you", "thank-you",
        {"postcode": {"kind": "postcode", "label": "ZIP code"}},
        dict(PROV, fill_order=["postcode"], examples={"postcode": value}))
    path = tmp_path / "wipedsite.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("wiped_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "t.jsonl"
    filler = module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace),
                           no_report=True)
    #: 上一轮：填过这一格、值也记住了 —— 但这一轮读出来是空的。
    filler._field_value[token] = value
    filler._field_tries[token] = 1
    filler._filled.add((token, value))
    filler.run(max_steps=1)

    sels = [sel for sel, _kw in calls["form"]]
    assert any(sel == '[id="zipcode"]' for sel in sels), \
        "页面上已经空了，却因为「填过」的记号不再填：%r" % (sels,)
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(r.get("action") == "form" and r.get("ok") is True for r in rows), rows


def test_a_label_that_wraps_a_checkbox_is_still_an_option():
    """★ 2026-10-22（Atticus 真站，`claim/age-zip-first-seeking-benefits-for`）：
    选项是 `<label><strong>Myself</strong><input type="checkbox"></label>` ——
    老规则「label 包着 input 就跳过」把三个选项全丢了 ⇒ 连 60 步「没有可动作的东西」。

    这条钉：只有 label 指向/包着**文本类**输入（输入框自己的 label）才跳过；
    包着 checkbox/radio 的 label 是**选项**，要收。
    """
    js = template_loop.DEFAULT_READ_JS
    old = "e.tagName==='LABEL'&&(e.htmlFor||e.querySelector('input,select,textarea'))"
    assert old not in js, "老规则还在：连包着 checkbox 的选项 label 也一起丢了"
    assert "input:not([type=checkbox]):not([type=radio])" in js
    assert "refTxt" in js
    assert "input:not([type=checkbox]):not([type=radio])" in _src()


def test_a_special_site_can_opt_out_of_the_profile_phone(tmp_path, monkeypatch):
    """★ 2026-09-26（solarincentives）/ 2026-10-13（compareinsulation）：**特殊脚本**不吃资料里的号。

    ★ 2026-10-22（用户澄清）：这是**个别特殊脚本**的处理，不是全局默认 —— 所以现在要显式点名
    （`from_profile: False` 或 `prefix`）；没点名的脚本默认取资料（见
    `test_a_phone_fill_takes_the_profile_unless_the_script_pins_an_area_code`）。
    这条钉「点名之后确实不取」：资料里给了 `9998887777`，产物仍按英国座机形状现编。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://uk.test/lead", "head": "Phone", "progress": "",
            "inputs": [["\u0002Enter your phone", ""]], "options": [], "nextVisible": False,
            "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "ukphone", "Thank you", "thank-you",
        {"phone": {"kind": "phone", "label": "Enter your phone", "from_profile": False}},
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
                   "rule": "phone_prefix", "prefixes": ["04"]}},
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
    assert re.fullmatch(r"04\d{8}", got[0]), got


def test_value_for_uk_phone_rule_drops_the_trunk_zero(tmp_path, monkeypatch):
    """★ 2026-10-23（运营）：「英国电话没有 0 开头的，一般是 `20 7040 XXXX`，
    好像只有后面 8 位可以随机」—— 前缀 `20` + 补到 10 位 = 后 8 位随机。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "PhoneNumber",
                   "rule": "phone_prefix", "prefixes": ["20"]}},
        dict(PROV))
    path = tmp_path / "rule_phone_uk.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_phone_uk_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"phone": "0000000000"}
    obj._rule_values = {}
    got = [obj.value_for("\u0002PhoneNumber", {}, 0) for _ in range(3)]
    assert len(set(got)) == 1, got          # 同一趟同值
    assert re.fullmatch(r"20\d{8}", got[0]), got   # 10 位：20 + 后 8 位随机
    assert got[0][3] in "23456789", got[0]   # 交换局首位不许 0/1


def test_value_for_exact_phone_is_used_verbatim(tmp_path, monkeypatch):
    """★ 2026-10-29：人点名的整号原样用（不再随机、不再补位）。"""
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "PhoneNumber",
                   "rule": "phone_exact", "value": "2014237916"}},
        dict(PROV))
    path = tmp_path / "rule_phone_exact.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_phone_exact_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"phone": "0000000000"}
    obj._rule_values = {}
    assert obj.value_for("\u0002PhoneNumber", {}, 0) == "2014237916"


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


def test_a_pick_hint_wins_over_the_dice(tmp_path, monkeypatch):
    """★ 2026-10-24（Atticus 真站）：有的站**必须优先答某一项**。

    现场：循环把「Who are you seeking disability benefits for?」随机答成
    「Family member or friend」/「Patient or client」，联系信息屏就多出一个**必填的关系下拉**
    （`relationship` / `patient-client-professional-role`）—— 而我们会走的那条路
    （本人申请）根本没有那一格。线上两趟（26807011 / 26807496）全卡在这里。

    ⇒ `pick_hints` 这一格：池子里**有**提示就挑它，没有才照旧掷骰子
    （提示是「优先」，不是「只认」—— 别的屏上那些提示根本不出现）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    _fake_common(monkeypatch, {"url": "", "head": "", "inputs": [], "options": []}, calls)
    src = template_loop.render_loop("hinted", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""),
                                    pick_hints=["Myself", "I'm the applicant"])
    path = tmp_path / "hinted.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("hinted_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PICK_HINTS == ["Myself", "I'm the applicant"], module.PICK_HINTS
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)

    pool = ["\u0001Myself", "\u0001Family member or friend", "\u0001Patient or client"]
    for _ in range(20):
        assert filler.pick_option(list(pool)) == "\u0001Myself", (
            "随机把它带去了别的分支 —— 那一支的必填下拉我们不会填")

    #: 池子里一个提示都没有 ⇒ 照旧掷骰子（不许因为配了提示就一个都不挑）
    others = ["\u0001Yes", "\u0001No"]
    assert filler.pick_option(list(others)) in others


def test_an_autocomplete_suggestion_matching_the_filled_zip_wins(tmp_path, monkeypatch):
    """★ 2026-11-02（educationdirectory 真站）：提交前站点弹 `Use ZIP 10118` 这类地址联想。
    原来靠随机挑选项「碰巧」点中；现在**已经填进页面的五位 ZIP** 与建议文字对得上，
    就先选那一条（对不上照旧掷骰子）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    _fake_common(monkeypatch, {"url": "", "head": "", "inputs": [], "options": []}, calls)
    src = template_loop.render_loop("zipfit", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "zipfit.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("zipfit_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)

    pool = ["\u0001Use ZIP 90210", "\u0001Use ZIP 10118"]
    for _ in range(20):
        assert filler.pick_option(list(pool), [["\u0004zip_code", "10118"]]) \
            == "\u0001Use ZIP 10118", "联想建议没按已填的 ZIP 选"

    #: 页面上没有五位 ZIP ⇒ 照旧在池子里挑（不许因此一条都不点）。
    assert filler.pick_option(list(pool), [["\u0004email", "a@b.test"]]) in pool


def test_a_product_without_hints_says_so_and_picks_as_before(tmp_path, monkeypatch):
    """**负控**：不配 `pick_hints` 的产物，那一格是**空表**，挑法一个字不变。"""
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    _fake_common(monkeypatch, {"url": "", "head": "", "inputs": [], "options": []}, calls)
    src = template_loop.render_loop("plain", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "plain.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("plain_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PICK_HINTS == [], module.PICK_HINTS
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)
    pool = ["\u0001Myself", "\u0001No"]
    assert filler.pick_option(list(pool)) in pool


def test_a_disabled_next_with_filled_fields_gets_the_value_retyped(tmp_path, monkeypatch):
    """★ 2026-10-24（Atticus `survey/claim/zip` 真站）：**值在、页面没收下**。

    现场：`#zipcode` 的 value 是 `10001`（DOM 与回读都说填好了），可 `Next` 一直是
    `disabled` —— 站点在填值那下发的 `benefitsByZip` 被**中止**了（resource 里
    `responseStatus: 0`），app 拿不到回话就永远禁用 Next；而循环只认 DOM 的值，
    再也不会碰这一格 ⇒ 连点 8 次、如实报失败（线上 `26789962`）。

    手工量证：同一个窗口里把这格**用同一个值重打一遍** ⇒ 站点重发请求（200）⇒ Next 变可用。
    所以：`Next` 是禁用态 + 格子都有值 ⇒ **重打一遍**，不是再填一个新值。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/survey/claim/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [["\u0004zipcode", "10001"]], "options": [],
            "nextVisible": True, "nextTxt": "Next", "nextDisabled": True}
    _fake_common(monkeypatch, page, calls)

    src = template_loop.render_loop(
        "zipsite", "Thank you", "thank-you",
        {"zipcode": {"kind": "postcode"}},
        dict(PROV, entry_url=""), )
    path = tmp_path / "zip_product.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("zip_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.NUDGE_WAIT_SECONDS = 0          # 不真等（这条钉的是手法，不是时长）
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)
    filler.run(max_steps=5)

    assert calls.get("events"), ("Next 是禁用态、格子又有值 ⇒ 要用原生 setter + 事件"
                                 "把值重打一遍，干点 Next 只会空转到收摊：%r"
                                 % (calls["click"],))
    js = calls["events"][0]
    assert "zipcode" in js, js[:400]          # 找的是那一格（`\x04zipcode`）
    assert '"10001"' in js, "重打得是**同一个值**，不是现编一个新值：%s" % js[:400]
    assert "nextDisabled" in template_loop.DEFAULT_READ_JS, (
        "读那一页的 JS 得把「Next 是不是禁用」交回来 —— 不然循环分不出「点了会动」还是白点")


def test_a_disabled_next_is_retyped_again_on_later_rounds(tmp_path, monkeypatch):
    """★ 2026-10-29（线上 26919524 的真形状）：**同一屏只重打一次不够**。

    真站那次 `benefitsByZip` 被中止（`responseStatus: 0`）之后 `Next` 一直禁用。
    旧实现把「这一屏已经打过一次」记在 `_nudged_sig` 里 ⇒ 之后每一轮都不再碰这格
    ⇒ 只会干点 8 次 Next（日志原样：「step 10..17: Next（What's your ZIP code?）」）。
    站点那一下是**偶发**的（同一个窗口里再打一次就 200）⇒ 得允许再试，直到上限。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/survey/claim/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [["\u0004zipcode", "10001"]], "options": [],
            "nextVisible": True, "nextTxt": "Next", "nextDisabled": True}
    _fake_common(monkeypatch, page, calls)

    src = template_loop.render_loop(
        "zipsite", "Thank you", "thank-you",
        {"zipcode": {"kind": "postcode"}},
        dict(PROV, entry_url=""))
    path = tmp_path / "zip_retry.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("zip_retry_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    filler = module.Filler.__new__(module.Filler)
    filler.cid = "cid"
    filler.log = logging.getLogger("zip_retry")
    filler.form_data = {}
    filler._trace = lambda **kw: None
    retypes = []

    def _retype(token, value):
        retypes.append((token, value))
        return True

    #: 两种重打手法都记成「打了一次」—— 这条钉的是**重试次数**，不是用哪个 API。
    filler.fill = _retype
    filler._set_value_events = _retype
    filler.read = lambda: dict(page)          # 每次读回来 Next 还是禁用
    module.NUDGE_WAIT_SECONDS = 0             # 不真等 —— 这条钉的是次数

    assert filler._nudge(page) is True
    assert filler._nudge(page) is True, (
        "同一屏第二次就没再打 —— 真站那一下是偶发的，只给一次机会等于不给")
    assert filler._nudge(page) is True
    assert filler._nudge(page) is False, "超过上限还在打 —— 会一直占着这一屏"
    assert retypes == [("\u0004zipcode", "10001")] * 3, retypes


def test_after_clicking_next_it_waits_for_the_page_to_change(tmp_path, monkeypatch):
    """★ 2026-10-29（Atticus `age-interstitial-over-50-early-btr` 旁路轮询量到的现场）：

    点完 Next 立刻重读、看到同一屏就再点 —— 8 次点击全被吞，而页面在**停止点击之后**
    自己走到了下一屏；同一时刻页面上有一批 15–18 秒才失败的第三方请求。
    ⇒ 点完先**等页面真的变了**（URL 或题面），变了就进下一轮；别 1.5 秒一次地连点。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/interstitial", "head": "Disability benefits…",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("settlesite", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "settle.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("settle_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)

    module.NEXT_SETTLE_POLL = 0
    monkeypatch.setenv("SITEFORGE_NEXT_SETTLE", "0.5")   # conftest 默认把它压成 0（测试不等）
    reads = {"n": 0}

    def _read():
        reads["n"] += 1
        if reads["n"] >= 3:
            return {"url": "https://x.test/next", "head": "Who are you seeking…"}
        return dict(page)

    filler.read = _read
    assert filler._wait_for_change(page) is True
    assert reads["n"] >= 3, reads

    #: 题面**每屏都一样**的那一族（整站标题相同）：只看 head/url 会白等满预算 ——
    #: 形状（进度/格子）变了也算「变了」。
    page_same_head = dict(page, url="https://x.test/quiz", head="Free Eligibility Checker",
                          progress="50%", inputs=[["\u0002e.g. NN3 3AQ", ""]])
    monkeypatch.setenv("SITEFORGE_NEXT_SETTLE", "0.5")
    filler.read = lambda: dict(page_same_head, progress="72%",
                               inputs=[["\u0002e.g. Margaret Johnson", ""]])
    assert filler._wait_for_change(page_same_head) is True

    #: 负控：一直不变 ⇒ 到点返回 False（这里把预算设成 0）
    monkeypatch.setenv("SITEFORGE_NEXT_SETTLE", "0")
    assert filler._wait_for_change(page) is False


def test_a_click_that_cdp_could_not_perform_is_not_success(tmp_path, monkeypatch):
    """★ 2026-10-29（Atticus 第二趟卡在 `contact/personalization-question`）：

    `click()` 原来不看 `cdp click` 的回话 —— 没点着（`Error: … element not found`）
    也照样 `return True` ⇒ trace 里每一次 Next 都写着「成功」，失败在账上一点痕迹都没有
    （2026-10-13 那条注释已经点名过「原来写死 True」，但 BY_TEXT 这一支仍然没看回话）。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/q", "head": "Q", "progress": "", "inputs": [],
            "options": [], "nextVisible": True, "nextTxt": "Next"}
    _fake_common(monkeypatch, page, calls,
                 click_out="Error: scroll mouse wheel failed: element not found")
    src = template_loop.render_loop("clicksite", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "click_fail.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("click_fail_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)

    assert filler.click("\u0001Next") is False, (
        "cdp 明说没点着，`click()` 却报成功 —— trace 会把点空气记成做成了")


def test_a_disabled_next_with_nothing_to_refill_waits_instead_of_clicking(
        tmp_path, monkeypatch):
    """★ 2026-10-29（Atticus `age-interstitial-*` / `personalization-question`）：

    Next 是禁用态、又没有可重打的格子 ⇒ 站点正在发请求/还没轮到这一步。
    旧流程直接去点它 —— **点不动**，还把「同一屏最多 8 次」的额度烧光 ⇒ 如实报失败，
    而那一屏其实只要再等几秒就会自己好。现在：**等**（不点），最多等 `BUSY_TIMEOUT`。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/interstitial", "head": "Disability benefits…",
            "progress": "", "inputs": [], "options": [], "nextVisible": True,
            "nextTxt": "Next", "nextDisabled": True}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("waitsite", "Thank you", "thank-you", {},
                                    dict(PROV, entry_url=""))
    path = tmp_path / "wait_disabled.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("wait_disabled_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NUDGE_WAIT_SECONDS = 0
    module.DISABLED_NEXT_WAIT = 0          # 不真等（这条钉的是「不去点」）
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    trace = tmp_path / "t.jsonl"
    filler = module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace),
                           no_report=True)
    filler.run(max_steps=3)

    assert not calls["click"], ("Next 是禁用态、又没格子可重打 ⇒ 不该去点它：%r"
                                % (calls["click"],))
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    assert any(r.get("action") == "wait" for r in rows), rows


def test_a_fill_that_hits_element_not_found_is_retried(tmp_path, monkeypatch):
    """★ 2026-10-29（Atticus 第一趟 step 1：`ok=false` 但值其实填上了）：

    `cdp form` 在页面还没 hydrate 完的那一下会报 `element not found` —— 旧实现**立刻
    判失败**（不重试、也不看回读）。这一格下一轮会被再填一次是运气；运气不好就整单空转。
    现在：`element not found` 在 `FILL_READY_TRIES` 内重试，仍不行才如实失败，
    并把**为什么**（`_fill_why`）说出来 —— 「没填上」不再是一句没有下文的话。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://x.test/zip", "head": "What's your ZIP code?",
            "progress": "", "inputs": [["\u0004zipcode", ""]], "options": [],
            "nextVisible": True, "nextTxt": "Next"}
    state = {"n": 0}

    def _form(sel, kw):
        state["n"] += 1
        if state["n"] == 1:
            return "Error: scroll mouse wheel failed: element not found"
        page["inputs"] = [["\u0004zipcode", str(kw.get("value") or "")]]
        return "ok"

    _fake_common(monkeypatch, page, calls, form_out=_form)
    src = template_loop.render_loop(
        "fillsite", "Thank you", "thank-you",
        {"postcode": {"kind": "value", "label": "ZIP code",
                      "target": {"selectors": ["#zipcode"]}}}, dict(PROV))
    path = tmp_path / "fill_retry.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fill_retry_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FILL_READY_GAP = 0
    module.FILL_READBACK_GAP = 0
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    filler = module.Filler("ws://x/1", str(form), "cid", no_report=True)

    assert filler.fill("\u0004zipcode", "60601") is True, (
        "第一次 element not found 就放弃了 —— 页面只是还没 hydrate 完：%r" % (calls["form"],))
    assert len(calls["form"]) >= 2, calls["form"]

    #: 一直报 element not found ⇒ 如实失败，并说出为什么
    #: ⚠️ 得**重新 import**：`from common import CDPHelper` 在 import 那一刻就把类绑死了，
    #: 光换 `sys.modules["common"]` 不会影响已经 import 过的产物。
    calls2 = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page2 = dict(page, inputs=[["\u0004zipcode", ""]])
    _fake_common(monkeypatch, page2, calls2,
                 form_out=lambda sel, kw: "Error: element not found")
    spec2 = importlib.util.spec_from_file_location("fill_retry_product2", path)
    module2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module2)
    module2.FILL_READY_GAP = 0
    module2.FILL_READBACK_GAP = 0
    filler2 = module2.Filler("ws://x/1", str(form), "cid2", no_report=True)
    assert filler2.fill("\u0004zipcode", "60601") is False
    assert "element not found" in (filler2._fill_why or ""), filler2._fill_why


def test_progress_is_reported_once_per_screen(tmp_path, monkeypatch):
    """★ 2026-10-30（用户报「这个脚本好像没上报 step」）：循环型产物要**边做边报**。

    固定步骤表那族在每一组判据成立、动手之前 `_rpt_if_moved(name)` 报一行
    （`agent/template.py`）；循环型原来只在 success / 收摊时报 —— 生产那边一整趟
    看不到「走到哪一屏了」。这条钉：真做成一下 ⇒ 报一行 `step`；同一屏再做**不重复报**。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Question 1", "progress": "10%",
            "inputs": [], "options": ["\u0001No"], "nextVisible": False, "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("reportsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "report.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("report_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    trace = tmp_path / "t.jsonl"
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "cid", trace_path=str(trace),
                  no_report=False).run(max_steps=3)

    steps = [r for r in calls["report"] if r[0] == "step"]
    assert len(steps) == 1, "同一屏报了 %d 次 step：%r" % (len(steps), calls["report"])
    assert "第 1 步 答了第 1 题" in steps[0][1], steps


def test_the_task_id_comes_from_the_correlation_id_when_the_runner_does_not_pass_one(
        tmp_path, monkeypatch):
    """★ 2026-10-30（用户报「这站只上报 success / step、url 都没上报」）：

    生产那条路（`ad-task.py` 起 py 脚本）**只给 `--correlation-id <task>_<ts>`**，
    不给 `--task-id`。固定步骤表那族自己从 correlation_id 拆 task id
    （`agent/template.py`：`self.tid = task_id or correlation_id.split('_')[0]`）；
    循环型原来只认 `task_id` ⇒ `tid` 恒空 ⇒ `report_url` 带 `task_id: ""`
    被后端 400 拒掉（「task_id 必填」）⇒ formStep 一条 step/url 都没有。
    """
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Question 1", "progress": "10%",
            "inputs": [], "options": ["\u0001No"], "nextVisible": False, "nextTxt": ""}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop("tidsite", "Thank you", "thank-you", {}, dict(PROV))
    path = tmp_path / "tid.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tid_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seen = []
    module.report_url = lambda cdp, tid, step, log=None, reason="": seen.append(
        (tid, step, reason))
    form = tmp_path / "form.json"
    form.write_text("{}", encoding="utf-8")
    module.Filler("ws://x/1", str(form), "26956985_1793328349",
                  no_report=False).run(max_steps=2)

    assert seen, "整趟一条都没上报"
    assert all(t == "26956985" for t, _s, _r in seen), seen
    assert any(s == "step" for _t, s, _r in seen), seen
