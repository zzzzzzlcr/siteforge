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

import copy
import json
import pathlib
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fix, graph, lint  # noqa: E402

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


# ── ★ 2026-09-24：从底稿里读**它自己声明的成功文案**（用户点名「没说就按之前那个脚本来」）
#
# 这一层是**纯函数**（不碰网、不碰盘），所以它自己单钉：读得到的**逐字**读出来、
# 读不到的**不编**（回空 + 一句说清它靠什么判）。搬进产物之后是**永远认不出成功**那
# 几种形状（判网址 / 判据写在函数里），靠的是这两条分得开 —— 合起来就会把
# 「搬不过去」读成「搬好了」。

def test_the_draft_own_criteria_are_read_verbatim():
    """底稿自己声明的那几串字**逐字**读出来（列表 / 单数 / 下划线前缀那三种都认）。"""
    src = ('SUCCESS_TEXTS = ["Thank you for subscribing!", "Check your email"]\n'
           'OTHER = ["别动我"]\n')
    assert fix.success_texts_from_source(src) == \
        ["Thank you for subscribing!", "Check your email"]
    #: 单数那一种（`warthunder` 的真形状：`_SUCCESS_TEXT = "…"`）
    assert fix.success_texts_from_source('_SUCCESS_TEXT = "You can start playing"\n') == \
        ["You can start playing"]
    #: 正控：别的常量**不许**被读进来（读进来就是猜）
    assert fix.success_texts_from_source('OTHER = ["别动我"]\n') == []


def test_a_draft_with_no_readable_criterion_is_not_guessed():
    """① 函数体里那串字**不读**（捞它就是猜）；② **网址那一格**现在搬得动了。

    ⚠️ 逐行交代（2026-09-24 改的，加 `success_urls` 那一格时）：这一条原先量的是
    「判网址那种 ⇒ 回空 + 说清它看的是网址」—— 那是**当时**的事实（网址搬不过去）。
    现在有了第二格 ⇒ 那几行**照搬**（`success_urls_from_source`），
    而这句话只剩**真搬不了**的两种（判据写在函数里 / 判的是解出来的 hash 状态）。
    """
    #: ⚠️ 写在函数里 —— `entyrecare` 的真形状（`".../auth/verify"` 那种）
    inside = ('def run(self):\n'
              '    if "thank-you" in page:\n'
              '        self._rpt("success")\n')
    assert fix.success_texts_from_source(inside) == [], "从函数体里捞字符串了（那是猜）"
    assert fix.success_urls_from_source(inside) == [], "同上：捞字符串就是猜"
    #: 判网址那一种（`japansdates` 的真形状）⇒ **照搬它那几行**（不是挑一个）
    url_only = ('SUCCESS_URL_MARKERS = ("/wizard", "/main-page")\n'
                'def is_success(url):\n    return any(m in url for m in SUCCESS_URL_MARKERS)\n')
    assert fix.success_texts_from_source(url_only) == []
    assert fix.success_urls_from_source(url_only) == ["/wizard", "/main-page"]
    #: 判的是**解出来的 hash 状态**（`japansdates` 的 `wizard2` 那种）⇒ 搬不了，**说清为什么**
    hashed = ('HASH_SUCCESS_STATES = ("wizard", "wizard2")\n'
              'def is_success(url):\n    return True\n')
    assert fix.success_urls_from_source(hashed) == []
    clue = fix.success_clue_from_source(hashed)
    assert "wizard2" in clue and "base64" in clue, clue
    #: 判据写在函数里那一支（有 `is_success`，但没有可搬的字面量）也要说得出 ——
    #: ⚠️ 不许写成「或 True」那种白送的断言：那看起来像有守，实际什么都没量。
    func_only = 'def is_success(url):\n    return "/done" in url\n'
    assert "is_success" in fix.success_clue_from_source(func_only)
    #: 说不出靠什么判时**回空串**（调用方那边有兜底那句话）—— 不编一句。
    assert fix.success_clue_from_source("x = 1\n") == ""


# ── 诊断进提示词（★ 2026-10-10）────────────────────────────────────────


def test_the_whole_diagnosis_goes_into_the_prompt_not_just_one_sentence():
    """★ 2026-10-10（`job-d037348fba4d` / `job-bbc57727025c`）：递**整套**诊断，不是那一句 `say`。

    在这之前 `service.write` 只传 `diag["say"]` —— `_diagnosis` 算出来的
    `failed_step` / `note` / `per_run` / `现场` **一格都没到模型眼前**。
    而 `states_user` 那一侧本来就写着「诊断**逐字**贴进去」：只递一句话，
    那条规矩等于空转，改稿那双手看得见「卡在第 6 步」、看不见「这一步真用的是哪条选择器」。
    """
    diag = {"run": "baseline", "failed_step": 6,
            "say": "第 1 遍挂了：卡在第 6 步 —— 页面没动",
            "note": "填好了「E-mail」；页面没有变化",
            "submissions": 2,
            "per_run": [{"name": "baseline", "status": "failed", "ok": False,
                         "failed_step": 6, "note": "…"}],
            "现场": {"哪一步": 6, "动作": "form", "这一步的目标": "E-mail",
                    "实际用的选择器": "#input-sections--1__footer--contactemail",
                    "当时页面上这段字（末尾 240 字）": "…Please try again. ⚠️ Verify EN",
                    "为什么是这一步": "这一遍卡在第 6 步（trace 里第一条**承重步**没做成的那行）"}}
    block = fix.diagnosis_for_prompt(diag)
    # 那三格以前一格都传不过去 —— 这一条钉的就是它们现在**到了**。
    assert "#input-sections--1__footer--contactemail" in block, block
    assert "Please try again" in block, block
    assert "2" in block and "次" in block, block          # submissions（R-84 那个数）
    assert "页面没动" in block, block                      # 原来那一句 `say`

    #: 提示词那一侧**逐字**贴（不许在这儿概括）—— 摆进去的就是上面那一段原文。
    text = fix.states_user({"site": "s", "states": [], "fills": {}}, diagnosis=block)
    assert block in text, text
    assert "## 上一版自测没过：诊断（逐字）" in text, text


def test_an_empty_diagnosis_contributes_nothing_at_all():
    """**负控**：空诊断 ⇒ 空串 —— `states_user` 见到空串**就不摆那一段**。

    摆一个空标题比不摆更坏：读的人会以为「诊断是空的」是这一趟的结论，
    而真相是「这一轮根本没有诊断」（第一次出稿）。
    """
    assert fix.diagnosis_for_prompt(None) == ""
    assert fix.diagnosis_for_prompt({}) == ""
    text = fix.states_user({"site": "s", "states": [], "fills": {}},
                           diagnosis=fix.diagnosis_for_prompt({}))
    assert "上一版自测没过" not in text, text


# ── ★ 2026-10-14：「读到了却丢掉」那一族 ────────────────────────────────
#
# 起因（一串真事）：`foodandwine` / `goldenromances` 那几份**步进循环型**产物
# （1069 / 1151 行）修不了 —— 报的是 `504 Gateway Time-out`，看着像网关的事。
# 实际是这里丢的：`_literals` 明明抠出了 `FILLS`，可 `from_py` 因为 `states is None`
# **两个都返回 `None`** ⇒ 判成 `legacy` ⇒ 修站把 **57 KB 的整份源码**丢给模型重写
# ⇒ 超网关 180 秒。改成走数据表之后那一跳是 **1653 字**（差 35 倍）。


def test_a_py_with_only_fills_is_not_called_unreadable():
    """**只有 `FILLS`、没有 `STATES`** 的 py（步进循环型）—— `fills` 读出来了就不许丢。

    ⚠️ 那一族**本来就没有 `STATES`**（它是每轮现读页面的），所以「没有 STATES」
    不是「读不出来」，是**这一种形状的正常样子**。
    """
    src = "FILLS = {'email': {'source': 'email'}}\n"
    plan, states, fills, notes = fix.from_py(src)

    #: ⚠️ `states` 回来是 **`[]`**（不是 `None`）—— `repair_states(None, …)` 把「没有」
    #: 记成空表。**所以 `graph.py` 那句 `if fix_states:` 对它是 falsy** ——
    #: 上游那一句也得跟着改，不然这里读出来了，那边照样走 `legacy`。
    assert states == [], "它确实没有 STATES（回来是空表，不是 None）"
    assert fills == {"email": {"source": "email"}}, ("读到了就不许丢", fills)
    assert not notes, ("不许报「读不出来」—— 那不是这一档", notes)


def test_a_py_with_neither_is_still_called_unreadable():
    """**两个都没有**才算读不出来 —— 这一档照旧，别把「真读不出」也放过去。"""
    plan, states, fills, notes = fix.from_py("x = 1\n")

    assert states is None and fills is None
    assert notes and "读不出来" in notes[0], notes


def test_one_literal_that_cannot_be_read_does_not_throw_the_other_away():
    """`_literals`：一个抠不出来，**另外那个已经读到的照旧留着**。

    原来那句是 `except Exception: return None, None` —— 把**已经读到的**一起丢了。
    「量到了不许当成量不到」这条纪律的**反面**。
    """
    src = "FILLS = {'a': 1}\nSTATES = build()\n"     # STATES 是动态构造，抠不出来
    st, fl = fix._literals(src)

    assert st is None, "STATES 确实抠不出来"
    assert fl == {"a": 1}, ("FILLS 读到了，不该被 STATES 那一下连累丢掉", fl)


# ── ★ 2026-10-14：修站**保不住形状**那一族 ──────────────────────────────
#
# 起因（真事 `job-d29e4c847c1c` foodandwine）：拿一条**步进循环型**的底稿去修，
# `_draft` 里修站支把 `journey` 置 `None` ⇒ `funnel` 也是 `None` ⇒ `shape` 恒为
# `"table"` —— 而循环型的 `STATES` **本来就是空的**（每轮现读页面）⇒ 产出一份
# **走不动**的固定步骤表。而「修站**不切形态**：那一版的形状是人交进来的」是这条链上
# 的老规矩。要保住它，就得把循环型那几格从**底稿自己身上**读回来。

#: 一条像真的循环型底稿：元数据在 `PROVENANCE`、旋钮在模块常量、`STATES` 压根没有。
LOOP_SRC = '''
PROVENANCE = {"shape": "loop",
              "arrive_url": "https://x.test/reg",
              "pre_steps": [{"action": "click", "target": {"text": "Join now"}}],
              "fill_order": ["email"],
              "examples": {"email": "a@b.c"},
              "why": "有 3 个状态共用同一个地址"}
PICK = "random"
MAX_STEPS = 60
IDLE_LIMIT = 60
BUSY_TIMEOUT = 90
FILLS = {"email": {"name": "email", "source": "email", "kind": "value"}}
'''


def test_a_loop_draft_keeps_its_shape_when_read_back():
    """循环型的底稿：那几格**从它自己身上**读回来（元数据在 PROVENANCE、旋钮在模块常量）。"""
    spec = fix.loop_spec_from_source(LOOP_SRC)
    assert spec is not None, "这一份**是**循环型（PROVENANCE 里那格写着）"
    assert spec["arrive_url"] == "https://x.test/reg"
    assert spec["pre_steps"] == [{"action": "click", "target": {"text": "Join now"}}]
    assert spec["fill_order"] == ["email"]
    assert spec["examples"] == {"email": "a@b.c"}
    #: 旋钮那四格 —— 它们是**这一份现在的行为**（手调过 max_steps 就该照新的，不是照默认）
    assert spec["pick"] == "random" and spec["max_steps"] == 60
    assert spec["idle_limit"] == 60 and spec["busy_timeout"] == 90


def test_a_draft_that_is_not_a_loop_is_not_called_one():
    """**负控**：固定步骤表的底稿、以及不带那几格的老产物，都不许被判成循环型。

    形态切错的代价在下游**看不出来**：产物照样跑，只是走的是另一条路。
    """
    #: ① 固定步骤表那一族（有 `STATES`、没有 `PROVENANCE`）
    assert fix.loop_spec_from_source('STATES = []\nFILLS = {"a": 1}\n') is None
    #: ② `PROVENANCE` 在，但那一格不是 `loop`（老产物 / 固定步骤表那份）
    assert fix.loop_spec_from_source('PROVENANCE = {"shape": "table"}\n') is None
    #: ③ 那片字段根本不是字面量（动态构造）⇒ 读不出来
    assert fix.loop_spec_from_source("PROVENANCE = build()\n") is None
    assert fix.loop_spec_from_source("") is None


def test_the_knobs_fall_back_when_the_draft_does_not_carry_them():
    """`PROVENANCE` 说它是循环型、但那份**没写旋钮** ⇒ 只回元数据，旋钮那几格**不进表**。

    （调用方那边 `_loop_source` 对缺的键有默认值 —— 所以「没读到」与「读到 0」必须分得开。）
    """
    spec = fix.loop_spec_from_source('PROVENANCE = {"shape": "loop", "why": "单页漏斗"}\n')
    assert spec is not None and spec["why"] == "单页漏斗"
    for key in ("pick", "max_steps", "idle_limit", "busy_timeout"):
        assert key not in spec, ("没读到的旋钮不许编一个值顶上", key)


def test_a_loop_shaped_draft_also_skips_exploration(tmp_path):
    """**循环型的底稿也不探索** —— 这是 2026-10-14 补的那一刀（与上面那条同一条判据）。

    ⚠️ 为什么单钉：`fix_states` 读出来是**空表**，而空表是 falsy —— 判据写成
    `if state.get("fix_states")` 时它被读成「这不是修站」⇒ **真开浏览器从零探一路**
    （实测 `job-d29e4c847c1c`：50 秒、11 步、白烧一个窗口 + 一次模型账），
    而探出来的账本回头又让下游认了一条**假的成功网址**。
    """
    draft = tmp_path / "loop.py"
    draft.write_text(LOOP_SRC, encoding="utf-8")
    explored = []

    def spy_explore(*a, **kw):
        explored.append(a)
        raise AssertionError("循环型的底稿也是修站 —— 这条路**不许**探索")

    deps = graph.Deps(explore=spy_explore, write=graph._writer_from_journey)
    app = graph.build(checkpointer=graph.allowlisted(InMemorySaver()), deps=deps)
    cfg = {"configurable": {"thread_id": "fix-loop-1"}}
    #: ⚠️ **网址那格留空、只给正文判据** —— 这就是 `foodandwine` 的真形状
    #: （交单之后地址不变，只在原地显示「Check your email」）。
    #: 原先这一格非给不可（`render_loop` 的护栏写着「这一族只看网址」）⇒ 修站那条路
    #: 在这一格必然卡死；2026-10-14 用户裁定放宽成「网址或正文**至少有一个**」。
    app.invoke({"url": "https://example.test/x", "goal": "把它走通",
                "success_text": "Check your email",
                "site": "example-fixloop",
                "out_dir": str(tmp_path / "sites"), "fix_py": str(draft),
                "allow_skips": ["country", "viewport"]}, cfg)

    state = app.get_state(cfg)
    gate = state.values.get("__interrupt__") or state.tasks[0].interrupts[0].value
    assert gate["step"] == "explore", gate
    assert gate["facts"].get("模式") == "修站（MODE_FIX）", gate["facts"]
    assert "没有探路" in json.dumps(gate["facts"], ensure_ascii=False), gate["facts"]
    assert explored == [], explored

    app.invoke(Command(resume="continue"), cfg)          # 过 explore（**不探索**）
    assert explored == [], "走完 explore 这一步之后仍然一次都不许探索"

    #: ★ 另一半（同一族）：产出的**形状**要照底稿 —— 这一格掉了的话，一份循环型的底稿
    #: 会被渲成 `STATES` 为空的固定步骤表（走不动），而下游**不会响**。
    #: ⚠️ `draft` 自己也有一道闸（`shape` 是在**闸之后**才算的）⇒ 要多过一次。
    app.invoke(Command(resume="continue"), cfg)          # 过 draft
    after = app.get_state(cfg)
    assert after.values.get("end_reason") in (None, ""), \
        (after.values.get("end_reason"), after.values.get("end_note"))
    assert after.values.get("shape") == "loop", \
        ("形状掉了：%s" % after.values.get("shape_why"))
    assert "修站" in str(after.values.get("shape_why") or ""), after.values.get("shape_why")
    #: 底稿那份 `fix_fills` **真的用上了**（不是拿别的账本重算了一份）
    assert (after.values.get("fills") or {}).get("email"), after.values.get("fills")
    #: 判据那一格：正文那句进了产物、网址那格**空着**（不是从哪儿认了一个顶上）
    src = str(after.values.get("src") or "")
    assert "Check your email" in src, "正文判据要在产物里"
    assert "SUCCESS_URL_MARKERS = []" in src, \
        "网址判据空着就该是空的 —— 认一条顶上正是那次假成功的来路"


# ── 原始观察：抽取 / 对回原件（2026-10-15 修站那一刀）────────────────────────

def _card_src():
    return {
        "states": [{"name": "auth", "steps": [
            {"action": "scroll", "target": {"role": "a", "near": "header",
                                            "selectors": ["body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"],
                                            "text": None}},
            {"action": "form", "target": {"label": "Postcode", "hint": "postcode",
                                          "selectors": ['input[name="postcode"]'],
                                          "nearby_text": ["Where do you live?"]}},
        ]}],
        "fills": {"postcode": {"source": "postcode",
                               "target": {"label": "Postcode", "hint": "postcode",
                                          "selectors": ['input[name="postcode"]']}}},
    }


def test_card_of_takes_only_card_keys():
    got = fix.card_of({"label": "Postcode", "hint": "postcode", "selectors": ["input#a"],
                       "above_fold_only": False, "frame_id": ""})
    assert got == {"label": "Postcode", "hint": "postcode", "selectors": ["input#a"]}
    assert "above_fold_only" not in got and "frame_id" not in got


def test_card_of_empty_target_is_empty_dict():
    assert fix.card_of(None) == {}
    assert fix.card_of({}) == {}


def test_observed_of_is_a_deep_copy():
    src = _card_src()
    got = fix.observed_of(src["states"], src["fills"])
    # 改原件不许动到冻的那份（反过来也一样）
    src["states"][0]["steps"][0]["target"]["selectors"].append("header a")
    src["fills"]["postcode"]["target"]["label"] = "改花了"
    assert got["states"][0]["steps"][0]["target"]["selectors"] == [
        "body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"]
    assert got["fills"]["postcode"]["target"]["label"] == "Postcode"


def test_find_observed_by_state_and_index():
    obs = fix.observed_of(**_card_src())
    card = fix.card_of(obs["states"][0]["steps"][1]["target"])
    assert fix.find_observed(obs, "auth", 1, card)["hint"] == "postcode"


def test_find_observed_falls_back_to_card_fingerprint_when_reordered():
    """模型把两步调了个个儿 ⇒ 按 (状态名, 步序) 对不上，但卡的指纹能救回来。"""
    obs = fix.observed_of(**_card_src())
    postcode = fix.card_of(obs["states"][0]["steps"][1]["target"])
    # 现在去问「auth 的第 0 步，它的卡是 postcode 那张」——原件第 0 步不是它
    got = fix.find_observed(obs, "auth", 0, postcode)
    assert got is not None and got["hint"] == "postcode"


def test_find_observed_returns_the_original_card_not_the_injected_one():
    """★ 注入进来的卡**取不到「这一步是谁」**：返回的永远是**原件那张**。

    ⚠️ **钉法**（Ruling 13 之前这条用例钉错了东西，这里如实改成能钉住同一件事的写法）：
    它原来断言 `find_observed(obs, "auth", 0, invented) is None` —— 那是拿 `(auth, 0)` 这个
    **合法步序**去问，靠「地址不相等」判 `None`，等于把**找步**与**验地址**混成了一件事。
    两件事现在是分开的，也各有各的主：
      · **找步**（本函数）：指纹（只比 `selectors`）优先，(状态名, 步序) 兜底 ——
        地址被改写也照样认得出「这一步是谁」；
      · **验地址**（`lint.address_basis`）：逐条断言比卡 —— 编出来的那根照样 `UNSUPPORTED`。
    ⇒ 正确的钉法是下面两句，合起来才说明「注入不进来」：**返回的是原件那张卡**，
    **而被注入的那根地址仍被判 `UNSUPPORTED`**（不是靠「找不到卡」挡住的 —— 那一条
    顺带把「地址一被改写就判无据可依」这个死循环也带进来，是 Ruling 13 要治的病）。
    """
    obs = fix.observed_of(**_card_src())
    invented = {"role": "a", "near": "header", "selectors": ["header a"]}
    got = fix.find_observed(obs, "auth", 0, invented)
    assert got == fix.card_of(obs["states"][0]["steps"][0]["target"])
    assert got != invented and invented["selectors"][0] not in (got or {}).get("selectors", [])
    #: 后半句：注入的那根地址照样过不了 `address_basis`（**逐条断言**那一关，不是找路那一关）
    level, why = lint.address_basis(invented["selectors"][0], got, (got or {}).get("selectors") or [])
    assert level == lint.UNSUPPORTED, "编的那根地址照样要判无据可依：%s" % why


def test_find_observed_finds_the_step_back_when_the_address_was_rewritten():
    """★ Ruling 13：地址被改写（指纹落空）⇒ 按 (状态名, 步序) 照样认得出这一步，**卡照给**。

    真站那一格（spec §3.3 要求模型「换成一根**沾着卡的**地址」）：模型照做之后地址与观测原文
    不再逐字相同 ⇒ 指纹落空。旧实现把这一格当「步序也对不上」⇒ 卡为空 / `kept` 为空 ⇒
    判 `judge-fragile-no-basis` ⇒ `draft ↔ lint` 两趟就 `lint_cap` ——
    **正是 spec 自己要躲的那个死循环**。
    """
    obs = fix.observed_of(**_card_src())
    want_card = fix.card_of(obs["states"][0]["steps"][0]["target"])
    rewritten = {"role": "a", "near": "header", "selectors": ["a#get-started"]}
    assert fix._selector_key(rewritten) != fix._selector_key(want_card)   # 真的是「指纹落空」那一格
    assert fix.find_observed(obs, "auth", 0, rewritten) == want_card


def test_find_observed_prefers_the_step_at_that_index_when_the_address_alone_ties():
    """★ Ruling 15：同一组里两步的 `selectors` **逐字相同**、锚不同 ⇒ 指纹会**并列**，
    这时取 **(状态名, 步序) 那一张** —— 不是「第一个命中的」。

    复审给的确切复现：原件两步都是 `input.cell`（`First name` / `Last name`），
    问 `(auth, 1)` 带第 2 步那张卡 ⇒ 旧实现返回的是**卡 0**。
    后果不是「找错了步」这么轻：`_rule_i` 拿这张卡判出处、并把它**摆进 message** ——
    摆出来的会是**兄弟步**的锚（「探路那一刻量到的是 `First name`」）。
    ⚠️ 并列**之外**仍是「先命中的先赢」（见本用例最后一句）。
    """
    obs = {"states": [{"name": "auth", "steps": [
        {"action": "form", "target": {"label": "First name", "selectors": ["input.cell"]}},
        {"action": "form", "target": {"label": "Last name", "selectors": ["input.cell"]}}]}],
        "fills": {}}
    first = fix.card_of(obs["states"][0]["steps"][0]["target"])
    last = fix.card_of(obs["states"][0]["steps"][1]["target"])
    assert fix._selector_key(first) == fix._selector_key(last)     # 真的是并列那一格
    got = fix.find_observed(obs, "auth", 1, last)
    assert got == last and got["label"] == "Last name", got
    #: 并列之外：步序对不上（越界）⇒ 第一个命中的就是答案（不因为带了别的卡就乱挑）
    assert fix.find_observed(obs, "auth", 9, last)["label"] == "First name"


def test_find_observed_is_none_when_the_step_is_gone_and_the_address_is_unknown():
    """③ 步序越界、指纹也落空 ⇒ `None` = **这一步没有可依的原始观察**（fail-closed）。"""
    obs = fix.observed_of(**_card_src())
    assert fix.find_observed(obs, "auth", 9, {"selectors": ["#nothing-like-this"]}) is None
    #: 组名也对不上 ⇒ 同样是 None（不跨组乱认）
    assert fix.find_observed(obs, "elsewhere", 0, {"selectors": ["#nothing-like-this"]}) is None


def test_find_observed_returns_none_when_observed_is_missing():
    assert fix.find_observed(None, "auth", 0, {"selectors": ["#a"]}) is None


# ── 锚只认观测：合并（2026-10-16 第二刀，spec §3.1）──────────────────────────

#: 冻结那份：第 0 步有锚（`Competitions`），第 1 步**什么都没有**（量过、没东西）。
_OBS = {
    "states": [
        {"name": "g", "steps": [
            {"action": "click", "target": {"label": "Competitions", "role": "a",
                                           "selectors": ["#__BVID__408"]}},
            {"action": "click", "target": {"selectors": ["li:nth-child(2n+1)"]}},
            {"action": "click", "target": {}},
        ]},
    ],
    "fills": {"email": {"source": "email", "kind": "value", "label": "Email",
                        "target": {"label": "Email", "selectors": ["#e1"]}}},
}


def _steps(*targets):
    return [{"name": "g", "when": None,
             "steps": [{"action": "click", "target": t} for t in targets]}]


def test_a_made_up_anchor_is_overwritten_by_the_card():
    """★ 这一刀的正身：编的锚进不了产物（spec §1.2 ①）。"""
    out, _, changes = fix.anchors_from_observed(
        _steps({"label": "I made this up", "selectors": ["#__BVID__408"]}), {}, _OBS)
    target = out[0]["steps"][0]["target"]
    assert target["label"] == "Competitions"
    assert target["role"] == "a"
    assert changes == ["g#0"]


def test_an_anchor_key_the_card_lacks_is_deleted():
    """只补不删等于白干：卡上没有 `label` ⇒ 产物里也不许有 `label`。

    （第 1 步的卡是 `{"selectors": ["li:nth-child(2n+1)"]}` —— 一根锚都没有。）
    """
    out, _, _ = fix.anchors_from_observed(
        _steps({"selectors": ["#__BVID__408"]},
               {"label": "也编的", "selectors": ["li:nth-child(2n+1)"]}), {}, _OBS)
    assert out[0]["steps"][1]["target"] == {"selectors": ["li:nth-child(2n+1)"]}


def test_a_rewritten_address_still_finds_its_card():
    """★ Review Focus 4：地址被改写**正是要找的那件事**，不许因此当成「没量过」。

    这一步在冻结那份里是 `#__BVID__408`，模型按 spec §3.3 换成了 `a[href='/comp']`
    ⇒ 指纹对不上，但 (组名, 步序) 那一张卡照旧是它的 ⇒ 锚照旧合进去、地址一个字不动。
    """
    out, _, changes = fix.anchors_from_observed(
        _steps({"label": "编的", "selectors": ["a[href='/comp']"]}), {}, _OBS)
    target = out[0]["steps"][0]["target"]
    assert target["label"] == "Competitions" and target["role"] == "a"
    assert target["selectors"] == ["a[href='/comp']"]
    assert changes == ["g#0"]


def test_the_same_address_in_two_groups_is_merged_per_step():
    """★ Review Focus 5：同一根地址出现在两处 ⇒ 各归各的卡，不许串。"""
    frozen = {"states": [
        {"name": "a", "steps": [{"action": "click",
                                 "target": {"label": "第一处", "selectors": ["#x"]}}]},
        {"name": "b", "steps": [{"action": "click",
                                 "target": {"label": "第二处", "selectors": ["#x"]}}]},
    ], "fills": {}}
    live = [{"name": "a", "when": None,
             "steps": [{"action": "click",
                        "target": {"label": "编的 A", "selectors": ["#x"]}}]},
            {"name": "b", "when": None,
             "steps": [{"action": "click",
                        "target": {"label": "编的 B", "selectors": ["#x"]}}]}]
    out, _, changes = fix.anchors_from_observed(live, {}, frozen)
    assert out[0]["steps"][0]["target"]["label"] == "第一处"
    assert out[1]["steps"][0]["target"]["label"] == "第二处"
    assert changes == ["a#0", "b#0"]


def test_filling_in_a_missing_anchor_is_not_an_overwrite():
    """★「**补**」与「**编**」不是一回事 —— 那一格（spec §3.5）报的是**编**。

    模型什么都没写、卡上补上 ⇒ 产物变好了，但**不算「改写过」**。
    不收窄这一刀，账本里那些 `None` 占位键会让**每一版**都记成「改写过」⇒ 常亮的灯没人看。
    """
    out, _, changes = fix.anchors_from_observed(
        _steps({"label": None, "text": "", "selectors": ["#__BVID__408"]}), {}, _OBS)
    assert out[0]["steps"][0]["target"]["label"] == "Competitions"   # 补上了
    assert changes == []                                             # 但没「改写」


def test_selectors_are_never_touched():
    """候选归改稿那双手（spec §3.7）—— 换一根稳的地址正是这一刀**要**它做的事。"""
    out, _, _ = fix.anchors_from_observed(
        _steps({"selectors": ["a[href='/comp']"]}), {}, _OBS)
    assert out[0]["steps"][0]["target"]["selectors"] == ["a[href='/comp']"]


def test_a_step_that_was_never_observed_is_left_byte_identical():
    """卡找不到（步序越界 / 没这一组）⇒ 那一步一个字不动（spec §3.1 规则 ②）。"""
    live = [{"name": "别的组", "when": None,
             "steps": [{"action": "click", "target": {"label": "新加的步",
                                                      "selectors": ["#x"]}}]}]
    out, _, changes = fix.anchors_from_observed(live, {}, _OBS)
    assert out[0]["steps"][0]["target"] == {"label": "新加的步", "selectors": ["#x"]}
    assert changes == []


def test_an_observed_step_with_an_empty_target_wipes_the_anchors():
    """★ **「量过、什么都没有」与「没量过这一步」不是一件事**（Review Focus 2）。

    这一步在冻结那份里**存在**、`target` 是空的 ⇒ 它是「量过、什么都没有」⇒
    模型给它编的锚要**删掉**（`find_observed` 给 `{}`，不是 `None`）。
    """
    frozen = {"states": [{"name": "g", "steps": [{"action": "click", "target": {}}]}],
              "fills": {}}
    #: 「这一步没有选择器」用**没有这个键**表示，**不写** `selectors: []` —— 空表也是表，
    #: 规则 ③（候选归改稿那双手）要求它一个字不动，`{"selectors": []}` 会原样留在产物里。
    out, _, changes = fix.anchors_from_observed(
        _steps({"label": "编的"}), {}, frozen)
    assert out[0]["steps"][0]["target"] == {}
    assert changes == ["g#0"]


def test_fill_targets_are_merged_too():
    """`FILLS[*].target` 一起合 —— `lint._rule_i` 的 FILLS 那一支读的正是它。"""
    out_states, out_fills, changes = fix.anchors_from_observed(
        [], {"email": {"source": "email", "kind": "value", "label": "Email",
                       "target": {"label": "编的", "selectors": ["#e1"]}}}, _OBS)
    assert out_fills["email"]["target"]["label"] == "Email"
    assert changes == ["FILLS[email]"]


def test_without_observed_nothing_changes():
    """不传 `observed` ⇒ 原样返回、`changes` 空（老 job / 老调用点一个字不变）。"""
    live = _steps({"label": "编的", "selectors": ["#__BVID__408"]})
    for frozen in (None, {}):
        out, _, changes = fix.anchors_from_observed(live, {}, frozen)
        assert out == live and changes == []


def test_the_input_tables_are_not_mutated():
    """纯函数：入参一个字节不动（改稿那双手的那张表还得留着）。"""
    live = _steps({"label": "编的", "selectors": ["#__BVID__408"]})
    before = copy.deepcopy(live)
    fix.anchors_from_observed(live, {}, _OBS)
    assert live == before
