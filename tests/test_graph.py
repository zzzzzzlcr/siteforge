"""Task 7：LangGraph 图（`agent/graph.py`）—— intake → explore → draft → lint → selftest → deliver。

## 这份测试要钉住的三件事（brief 说的「三处必须写对」）

1. **人不是最后一道关**（§6.2）：**每一个节点之前**都停一次等人 —— 包括第一个节点之前、
   也包括 `deliver` 之前。中断之后**真能恢复**（R-19：saver 是必需的参数，不是可选项）。
2. **两条回灌都有硬上限**：`lint` 挂了回 `draft` **带违规行**；`selftest` 挂了走 `diagnose`
   回 `draft` **带证据（failed_step）**。两条都不许转不完（测试的 `_drive` 自带急停：
   图要是停不下来，它会**红**，而不是把 pytest 挂死）。
3. **`deliver` 写出的 py 带 `PROVENANCE`**（§5.3），而且**交付的字节与自测的字节只差那一块**
   （拿 `ast` 比出来，不靠眼看）。

## 这里全是桩

没有浏览器、没有模型、没有 cdp 二进制、没有网。唯一的「真」是：真 `Journey` → 真
`states()` / `fills()` → 真 `template.render()` → 真 `lint.check()`。**产物那一段必须真跑**，
否则「图会把脏产物交出去」这种事测试自己也看不见。

（Task 6 的 `agent/selftest.py` 在并发改动中 —— 这里只用它的公开形状 `Report` / `Run` /
`run`，不碰它的内部实现。）
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import pathlib
import sys
import time

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, lint, selftest, template  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"

#: 产物 import 的那一行要的东西（只在本文件内用；用完进出一趟都摘干净，
#: 免得把别的测试文件的同名替身留在 `sys.modules` 里 —— test_browser_agent 同款处理）
STUB_COMMON = '''
class CDPHelper:
    def __init__(self, *a, **kw): pass
def setup_logger(*a, **kw): return None
def report_url(*a, **kw): return True
'''


# ─────────────────────────── 桩与夹具 ───────────────────────────


class Rec:
    """桩把「谁被调了、拿到了什么」记在这里 —— 断言看它，不看实现。"""

    def __init__(self):
        self.explore: list = []
        self.write: list = []
        self.lint: list = []
        self.selftest: list = []
        self.tested_src = None      # 自测那一遍，产物文件里是什么（真的是什么）


def _journey(*, stop_reason="model_done"):
    """一份**真** Journey（手写的账本，不是跑模型跑出来的）。

    `states()` / `fills()` 走真代码 —— 图里 draft 那一步渲染出来的才是真产物。
    """
    steps = [
        {"state": "landing", "action": "click", "note": "点了「Get Started」",
         "target": {"text": "Get Started", "role": "button", "near": "hero",
                    "selectors": ["#get-started"], "above_fold_only": False},
         "result": {"ok": True, "selector": "#get-started"}},
        {"state": "landing", "action": "form", "note": "填好了「Postcode」",
         "target": {"text": None, "label": "Postcode", "role": None, "near": None,
                    "selectors": ["input#postcode"], "above_fold_only": False},
         "result": {"ok": True, "selector": "input#postcode",
                    "fill": {"name": "postcode", "source": "postcode", "kind": "value",
                             "label": "Postcode", "value": "SW1A 1AA",
                             "fallback": [{"random": "postcode"}]}}},
    ]
    return browser_agent.Journey(
        steps=steps,
        notes=["页面变了：现在是「Get Started」那一页", "走完了：点一次、填一个邮编"],
        stop_reason=stop_reason,
        final_answer="成功时页面上会出现「Thank you」",
        pages=[{"name": "landing",
                "when": {"url_contains": "example.test", "text_contains": ["Get Started"]},
                "url": URL, "title": "Get Started"}],
    )


def _run(name, status, *, ok, failed_step=None, note="跑通了"):
    return selftest.Run(name=name, label="第 %d 遍" % (("baseline", "rerun", "delay",
                                                        "viewport", "country").index(name) + 1),
                        status=status, ok=ok, failed_step=failed_step,
                        trace_path="/tmp/%s.trace.jsonl" % name, note=note)


def _pass_report():
    return selftest.Report(
        runs=tuple(_run(n, "passed", ok=True) for n in ("baseline", "rerun", "delay", "viewport")),
        passed=True, allowed_skips=("country",), cdp_bin="/usr/local/bin/cdp",
        site=SITE, py_path="/tmp/%s.py" % SITE)


def _fail_report(*, run_name="delay", failed_step=6, note=None):
    """一遍挂掉的报告：第 3 遍（延迟扰动）卡在第 6 步。"""
    note = note or "卡在第 %d 步：点了「Get My Quote」之后页面没动" % failed_step
    runs = [_run("baseline", "passed", ok=True), _run("rerun", "passed", ok=True),
            _run(run_name, "failed", ok=False, failed_step=failed_step, note=note)]
    return selftest.Report(runs=tuple(runs), passed=False, allowed_skips=("country",),
                           cdp_bin="/usr/local/bin/cdp", site=SITE,
                           py_path="/tmp/%s.py" % SITE)


def _dirty(spec):
    """把一份 spec 弄脏：states 里某一步的 note 里塞一句手拼 JS。

    产物是 python，note 会**逐字**进文件（pprint 的字面量）—— 所以 `lint.check`
    能在那一行上抓到 `js-click`。这是「违规行」最真实的来源：不是编出来的字符串。
    """
    dirty = copy.deepcopy(spec)
    dirty["states"][0]["steps"][0]["note"] = "顺手用 el.click() 补了一下"
    return dirty


def _viewport_cb(width, height):
    """窗口层那根线（`POST /browser/update` 的替身）—— Task 8 的服务该给的就是它。"""
    return None


def _faithful_selftest(rec):
    """一个**照着自测的判据**做的自测桩（不是「一律返回 passed」）。

    ⚠️ 这个桩的形状是有来历的（评审 Important 1）：图原先一个窗口旋钮都不往
    `selftest.run` 传 → 真的 `run()` 走 `set_viewport=None` 那一支 → 第 4 遍记成
    `skipped` → `_judge` 不认「跳过」为「过了」（默认只允许跳 country）→
    `Report.passed` **恒为 False** → 图在 selftest↔diagnose 之间转到上限，
    **看上去像产物不行**。桩要是「一律 passed」，这个接线 bug 在测试里**永远不会现形**。
    所以这里用**真的** `Run` / `Report` / `_judge`。

    ⚠️ R-84 起形状跟着变：真的 `run()` 现在是**按需跑**（过了就不往下跑），
    所以桩也照那样返回 —— 基线过了 + 后面几遍 `not_needed`。桩要是还停在
    「五遍都跑」，那它描述的是一份**真跑产不出来**的报告（桩与现实漂了，
    后面接消费者的人会被它带偏）。要「挂掉的报告」的用例**显式**传 `reports=[…]`。
    """
    def run(py_path, ws_url, form_file, site, **kw):
        rec.tested_src = pathlib.Path(py_path).read_text(encoding="utf-8")
        rec.selftest.append({"py_path": str(py_path), "ws_url": ws_url,
                             "form_file": form_file, "site": site, **kw})
        allowed = tuple(kw.get("allow_skips") or selftest.DEFAULT_ALLOWED_SKIPS)
        runs = tuple([_run("baseline", "passed", ok=True, note="跑通了")]
                     + [_run(name, selftest.STATUS_NOT_NEEDED, ok=None,
                             note="这一遍没跑：前一遍就过了（R-84：过了就算过）")
                        for name in selftest.RUN_NAMES[1:]])
        return selftest.Report(runs=runs, passed=selftest._judge(runs, allowed),
                               allowed_skips=allowed, cdp_bin=None, site=site,
                               py_path=str(py_path))
    return run


def _deps(*, journey=None, explores=None, reports=None, write=None, rec=None, **over):
    """**全部**外部依赖的桩。返回 `(Deps, Rec)`。

    `over` 直接盖到 `Deps` 上（例如 `set_viewport=None` = 「这根线没人接」）。
    默认接线给的是**该给的都给上**的样子（窗口层那根线接好 + 照着 Task 6 判据的自测桩）——
    这样「图少传一个旋钮」才会在测试里现形，而不是被一个过于宽容的桩盖住。
    """
    rec = rec if rec is not None else Rec()
    book = journey if journey is not None else _journey()
    queue = list(reports) if reports else None

    # `explores` 给的是**一串** journey（重探那几趟各一份）；不给就每次都返回 `journey`
    queue_j = list(explores) if explores else None

    def explore(url, goal, budget=None, **kw):
        rec.explore.append({"url": url, "goal": goal, "budget": budget, "kw": kw})
        if queue_j:
            return copy.deepcopy(queue_j[min(len(rec.explore) - 1, len(queue_j) - 1)])
        return copy.deepcopy(book)

    def write_stub(spec, feedback):
        rec.write.append({"spec": copy.deepcopy(spec), "feedback": copy.deepcopy(feedback)})
        if write is not None:
            return write(copy.deepcopy(spec), copy.deepcopy(feedback), len(rec.write))
        return spec

    def lint_stub(src):
        rec.lint.append(src)
        return lint.check(src)

    def selftest_stub(py_path, ws_url, form_file, site, **kw):
        rec.tested_src = pathlib.Path(py_path).read_text(encoding="utf-8")
        rec.selftest.append({"py_path": str(py_path), "ws_url": ws_url,
                             "form_file": form_file, "site": site, **kw})
        return queue[min(len(rec.selftest) - 1, len(queue) - 1)]

    chosen = selftest_stub if queue is not None else _faithful_selftest(rec)
    knobs = {"set_viewport": _viewport_cb}
    knobs.update(over)
    return graph.Deps(explore=explore, write=write_stub, lint=lint_stub,
                      selftest=chosen, **knobs), rec


def _brief(tmp_path, **over):
    """一次运行的开场白（等于 CLI/HTTP 那边收上来的东西）。"""
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


def _build(*, deps, caps=None, thread="t1"):
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    app = graph.build(checkpointer=saver, deps=deps, caps=caps)
    return app, {"configurable": {"thread_id": thread}}, saver


def _drive(app, cfg, initial, reply=None, limit=40):
    """一路把人该说的都说了，直到图**自己**停下。

    `limit` 是急停：图要是停不下来（比如上限被摘掉了），它在这里**红** ——
    而不是把整个 pytest 挂死（挂在 40 次之后才叫「没人看得出发生了什么」）。
    """
    payloads, out = [], app.invoke(initial, cfg)
    while out.get("__interrupt__"):
        payloads.append(out["__interrupt__"][0].value)
        if len(payloads) > limit:
            raise AssertionError(
                "图没有自己停下来：连着 %d 次都停在「等人」这儿（上限没起作用？）" % limit)
        value = reply(payloads[-1]) if reply else "continue"
        out = app.invoke(Command(resume=value), cfg)
    return payloads, out


# ───────────────────────── happy path ─────────────────────────


def test_happy_path_walks_every_step_in_order_and_writes_a_py(tmp_path):
    """六个节点按序走完，最后**真**落一条 py 下来。

    ⚠️ 这条用的是**照着 Task 6 判据**的自测桩（`_faithful_selftest`）+ 接好的窗口旋钮 ——
    它就是评审 Important 1 的回归钉子：图若少传 `set_viewport`，桩会把第 4 遍记成 `skipped`、
    真 `_judge` 判 `passed=False`，这条路就红（而不是像原先那样被一个「一律 passed」的桩
    盖过去，等到 Task 8 真跑才发现自测**永远**过不了）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))
    assert out["report"].passed is True, out.get("end_note")
    assert rec.selftest[0]["set_viewport"] is _viewport_cb

    # ⚠️ `intake` **不在停顿序列里**（2026-09-20：运营点「开一趟」就是确认，不再问第二遍）——
    # 但它**在 `visits` 里**（下一行），因为「走过的节点」它是真的走过。
    assert [p["step"] for p in payloads] == ["explore", "draft", "lint",
                                             "selftest", "deliver"], payloads
    assert out["end_reason"] == "delivered", out.get("end_note")
    assert out["visits"] == ["intake", "explore", "draft", "lint", "selftest", "deliver"]

    py = tmp_path / "forms" / "sites" / ("%s.py" % SITE)
    assert py.is_file(), out.get("end_note")
    assert out["py_path"] == str(py)
    # 自测跑过的那份候选不留（交付点旁边只该有那一份产物）
    assert not (tmp_path / "forms" / "sites" / ("%s.candidate.py" % SITE)).exists()

    # 产物里那几样必须是**从账本里真翻译过来**的（不是占位符）
    src = py.read_text(encoding="utf-8")
    assert 'SITE = "example-funnel"' in src
    assert "Thank you" in src
    assert "input#postcode" in src          # 账本里填成功的那个字段
    assert "#get-started" in src            # 账本里点成功的那一步
    assert lint.check(src) == []

    # explore 只跑了一次，draft 也只写了一次（没人打回）
    assert len(rec.explore) == 1
    assert len(rec.write) == 1
    assert len(rec.selftest) == 1
    # 自测跑的就是**摆出来准备交付的那份源码**（文件在，且内容一致）
    assert rec.tested_src and "SITE = \"example-funnel\"" in rec.tested_src


def test_every_expensive_node_is_preceded_by_a_pause_that_speaks_human(tmp_path):
    """**人不是最后一道关**：每个**花钱 / 动真页面**的节点之前都停一次，问的话是人话（D16）。

    这是 §6.2 的机器化：`deliver` 之前那次停顿与 `explore` 之前那次**同等重要** ——
    人可以在第 2 步就拦住它，而不是等它带着错走完。

    ⚠️ **名字改过**（2026-09-20，复审 §2 残留那条）：原名叫
    `test_every_node_is_preceded_by_a_pause_that_speaks_human` —— 它量的是**5 个
    「花钱 / 动真页面」的节点**（`intake` 不设闸），名字里的「every node（每个节点）」
    比它量的东西**宽**：下一个人按名字找覆盖会以为 6 个都在里面。改成
    `every_expensive_node`，与 docstring 与断言里的「5 个会花钱的节点」**同一个口径**。
    （复审判它只是**命名**问题、不是覆盖缺失 —— 下面那三条断言一条没少。）

    ⚠️ **计数从 6 改成 5**（2026-09-20）：`intake` 不再设闸（运营点「开一趟」就是确认）。
    这是**有意改的事实**，不是把断言放松了 —— 这条用例要钉的两件事一件没少，
    而且第一件钉得**更细**了：
      ① **一步贵的都不许在没确认之前跑** —— 现在是拿「第一道闸**就是** `explore`、
         而 `explore` 的闸排在开真窗口之前」钉的（`test_no_browser_and_no_model_before_a_human_confirms`
         直接量了那件事：确认之前 `rec.explore` 是空的）；
      ② 每一道闸问的话都是人话（下面那个循环，一道没少）。
    """
    deps, _ = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _ = _drive(app, cfg, _brief(tmp_path))

    assert [p["step"] for p in payloads] == ["explore", "draft", "lint",
                                             "selftest", "deliver"], payloads
    assert len(payloads) == 5, "五个会花钱的节点 = 五次停顿（少一次就是「跑完才汇报」）"
    assert payloads[0]["step"] == "explore", \
        "第一道闸必须是 explore —— 它前面只剩下不花钱的 intake：%r" % (payloads[0]["step"],)
    for payload in payloads:
        assert payload["say"].strip(), payload
        assert payload["can"], payload                       # 人能做什么，得写出来
        assert isinstance(payload["facts"], dict), payload
        # 不是错误码、不是选择器：说人话那一段里不许出现 traceback / 异常类名
        for junk in ("Traceback", "Exception", "<class", "selector:"):
            assert junk not in payload["say"], payload["say"]


def test_the_operator_is_not_asked_to_confirm_the_start_a_second_time(tmp_path):
    """**运营点了「开一趟」之后，不该再被问一次「开工前的确认」**（2026-09-20）。

    验收口径（一句话）：第一次 `invoke` 之后停下来的那道闸**不是 `intake`**。
    `intake`（「开工前的确认」）与运营点「开一趟」那一下是**重复的两次确认** ——
    而第二次等不到人时**不出声**（2026-09-20 真面板上连撞三次：①停在 intake 没人按 ⇒
    运营以为「失败了」把页面关了；②同一趟再派时按成了「停」；③原话
    「我作为一个使用者我现在就是看着他页面卡住啥也操作不了也不知道啥情况啊」）。

    留着的那部分（`visits` 记账）在同一趟上钉着，见
    `test_intake_still_shows_up_in_the_ledger_even_though_it_does_not_ask`。
    """
    deps, _rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _out = _drive(app, cfg, _brief(tmp_path))

    assert payloads, "一道闸都没有 —— 那等于「跑完才汇报」"
    assert payloads[0]["step"] == "explore", (
        "第一道闸还是 %r —— 运营点完「开一趟」又被问了一次「开工前的确认」" % payloads[0]["step"])
    assert "intake" not in [p["step"] for p in payloads], payloads


def test_intake_still_shows_up_in_the_ledger_even_though_it_does_not_ask(tmp_path):
    """`intake` **不再设闸，但它依然是个走过的节点** —— 账上要看得见它。

    为什么非记不可：`visits` 是「走过的节点」（§6.4 人看路线用），而 `rounds.py`
    给每一轮卡片配「刚做完的是哪一步」时读的就是它。少记一格，卡片就会把
    「刚做完的」说成**下一个**节点的名字 —— 那是编话。
    """
    deps, _rec = _deps()
    app, cfg, _ = _build(deps=deps)
    out = app.invoke(_brief(tmp_path), cfg)              # 停在第一道闸上
    assert out["__interrupt__"], "第一道闸不见了"
    assert out["__interrupt__"][0].value["step"] == "explore", out["__interrupt__"]
    assert app.get_state(cfg).values["visits"] == ["intake"], \
        app.get_state(cfg).values["visits"]


def test_the_first_gate_carries_the_brief_the_operator_is_confirming(tmp_path):
    """第一道闸上要看得到**人在确认的那份开场白**。

    ⚠️ 为什么这条跟着上面那条一起加：第一道闸从 `intake` 换成 `explore` 之后，
    `intake` 那道闸原本摊开的几项（**成功判据** / 模式 / 要用的窗口 / 还缺的窗口旋钮）
    要是没人接手，运营就是在**少看了一半信息**的情况下点「继续」——
    那不叫「少问一次」，那叫「把确认变成走过场」。
    （url / goal / 模式那几项 `explore` 自己的人话里本来就有，所以这里钉的是**新补上来的**那几项。）
    """
    deps, _rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _out = _drive(app, cfg, _brief(tmp_path))

    first = payloads[0]
    assert first["step"] == "explore", first
    assert first["facts"]["成功判据"] == SUCCESS, first["facts"]
    assert first["facts"]["url"] == URL, first["facts"]
    assert first["facts"]["要用的窗口"] == WS_URL, first["facts"]
    assert first["facts"]["还缺的窗口旋钮"] == [], first["facts"]


def test_no_browser_and_no_model_before_a_human_confirms(tmp_path):
    """**「人不是最后一道关」那条不变量的承重部分**：确认之前一步**贵**的都不许跑。

    这条之所以要单独立着：把第一道闸从 `intake` 挪到 `explore` 之后，
    「六个节点六次停顿」那个**字面**说法不再成立（少一次）—— 但那句话的**要点**
    （任何一步都不许设计成「不可打断、跑完才汇报」）**必须原样成立**。
    要点落在哪儿：`_explore` 的 `_enter` **排在** `deps.explore`（开真窗口 + 跑模型）**之前**。

    判据是**测量出来的两半**：第一道闸之前 `rec.explore` 是空的（一步没跑），
    回了「继续」之后它才有东西（那一步真的做了）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)

    out = app.invoke(_brief(tmp_path), cfg)              # 停在第一道闸上
    assert out["__interrupt__"], "第一道闸不见了"
    assert rec.explore == [], \
        "还没人按「继续」，真窗口/模型那一步就已经跑了：%r" % (rec.explore,)

    app.invoke(Command(resume="continue"), cfg)          # 人说了「继续」
    assert len(rec.explore) == 1, rec.explore            # 现在它才真的做了


def test_the_delivered_py_carries_provenance(tmp_path):
    """§5.3：环境指纹随产物落盘 —— 同一 URL 在不同代理国家是**不同的页面**。"""
    deps, _ = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, env={"proxy_country": "US", "dpr": 1,
                                                     "ua": "Mozilla/5.0 (stub)",
                                                     "viewport": [1280, 800]}))

    src = pathlib.Path(out["py_path"]).read_text(encoding="utf-8")
    prov = _constant(src, "PROVENANCE")
    assert set(template.PROVENANCE_KEYS) <= set(prov), prov
    assert prov["generator"] == "siteforge/v0.1"
    assert prov["env"]["proxy_country"] == "US"              # 前提层给的，原样带上
    assert prov["source"]["kind"] == "build"
    assert prov["source"]["evidence"] == GOAL                # 人给的意图，原样带上
    # 自测那块：按 R-84 的形状（默认只跑 1 遍、过了就算过），而且**判据**（allow_skips 那套）跟着走
    # ⚠️ 期望值从 4/4 改成 1/1：口径变了（`selftest.run` 不再固定跑几遍），不是为了让测试过
    assert prov["selftest"]["runs"] == 1
    assert prov["selftest"]["passed"] == 1
    assert prov["selftest"]["verdict"] is True
    # ★ R-84：**这一轮提交了几次**必须跟着产物走（看不见的东西会再犯一次）
    assert prov["selftest"]["submissions"] == 1, prov["selftest"]
    assert prov["selftest"]["at"]
    # 运行时出身（R-15：这份 py 跑起来用的是哪一份 common.py）也在里面
    assert prov["source"]["runtime"]["source_md5"]
    assert prov["source"]["runtime"]["copied_from"]


def test_what_nobody_told_the_graph_stays_none_instead_of_being_invented(tmp_path):
    """没人告诉图的事（代理指纹 / 平台猜测）**留 None** —— 缺的键补 None，不编内容。"""
    deps, _ = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))               # 没给 env

    prov = _constant(pathlib.Path(out["py_path"]).read_text(encoding="utf-8"), "PROVENANCE")
    assert prov["env"] is None
    assert prov["platform"] is None


def test_the_delivered_bytes_differ_from_the_tested_bytes_only_in_provenance(tmp_path):
    """自测过的字节 vs 交付的字节：**只差 PROVENANCE 那一块**（拿 ast 比，不靠眼看）。

    为什么天生会差：产物要把**自己的自测结果**写进 `PROVENANCE`（§5.3），
    而自测结果只有跑完才知道 —— 所以「同一份源码自测完再落盘」在物理上不可能，
    只能「同 spec 再渲一次」。这条测试保证第二次渲染没有顺手改别的东西。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    delivered = pathlib.Path(out["py_path"]).read_text(encoding="utf-8")
    tested = rec.tested_src
    assert tested != delivered, "自测那份没带自测结果，两份不可能一字节不差"
    assert _without_provenance(tested) == _without_provenance(delivered)
    # 差的那一块**正是**自测结果：自测时还是 None，交付时填上了
    assert _constant(tested, "PROVENANCE")["selftest"] is None
    assert _constant(delivered, "PROVENANCE")["selftest"]["runs"] == 1   # R-84：默认只跑 1 遍


def test_the_delivered_py_is_a_real_importable_production_script(tmp_path):
    """交付物得能**真 import**（不是只过 `ast.parse`）—— 它要扔进 `forms/sites/` 被生产调。"""
    deps, _ = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))
    py = pathlib.Path(out["py_path"])

    (tmp_path / "forms" / "common.py").write_text(STUB_COMMON, encoding="utf-8")
    saved = sys.modules.pop("common", None)
    try:
        spec = importlib.util.spec_from_file_location("graph_delivered_site", py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("common", None)
        if saved is not None:
            sys.modules["common"] = saved
    assert module.SITE == SITE
    assert module.SUCCESS_TEXTS == [SUCCESS]
    assert [s["name"] for s in module.STATES] == ["landing"]
    assert module.FILLS["postcode"]["kind"] == "value"


# ─────────────────── 回灌一：lint 挂了 → draft 带违规行 ───────────────────


def test_a_lint_violation_is_handed_back_to_draft_with_its_lines(tmp_path):
    """lint 打回时，**违规行本身**要跟着回到 draft（只说「你手拼 JS 了」它找不到地方）。"""
    deps, rec = _deps(write=lambda spec, fb, n: _dirty(spec) if n == 1 else spec)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert len(rec.write) == 2, "第一版脏、第二版干净 —— draft 应该被叫了两次"
    assert out["end_reason"] == "delivered"

    fb = rec.write[1]["feedback"]
    assert fb["violations"], fb
    v = fb["violations"][0]
    assert v["code"] == "js-click"
    assert isinstance(v["line"], int) and v["line"] > 0
    assert ".click()" in v["snippet"]
    # 行号**指着那一行**（拿真 lint 跑过的源码核对，不是自说自话）
    row = rec.lint[0].splitlines()[v["line"] - 1]
    assert v["snippet"] in row, row
    assert v["message"].strip() and "cdp" in v["message"]      # 人话，且说了该怎么办

    # 人在闸口也看得到：第 N 行 + 人话（**不是** hunk、不是 code）
    draft_rounds = [p for p in payloads if p["step"] == "draft"]
    assert len(draft_rounds) == 2
    say = draft_rounds[1]["say"]
    assert "第 %d 行" % v["line"] in say
    assert v["message"] in say
    assert ".click()" not in say, "给人看的那段不该塞代码片段"
    assert draft_rounds[1]["facts"]["violations"][0]["code"] == "js-click"


def test_lint_that_never_passes_stops_at_the_cap_instead_of_spinning(tmp_path):
    """一条永远过不了 lint 的产物：走到上限就**停**，不许无限循环（§6.5）。"""
    deps, rec = _deps(write=lambda spec, fb, n: _dirty(spec))
    app, cfg, _ = _build(deps=deps, caps=graph.Caps(max_lint_bounces=2))
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "lint_cap", out.get("end_note")
    assert len(rec.write) == 3, "首版 + 两次打回 = 三次；多一次就是上限失效"
    assert len(rec.selftest) == 0, "连 lint 都没过，不该去跑自测"
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists(), \
        "没通过的东西一个字节都不许落到交付路径上"
    assert "第" in out["end_note"] or "次" in out["end_note"]        # 人话说得清为什么停
    assert [p["step"] for p in payloads].count("draft") == 3


# ──────────────── 回灌二：selftest 挂了 → diagnose → draft 带证据 ────────────────


def test_a_selftest_failure_goes_through_diagnose_and_carries_the_failed_step(tmp_path):
    """自测挂了：先 `diagnose` 定位，再回 `draft` —— 回去的时候**带着 failed_step**。"""
    deps, rec = _deps(reports=[_fail_report(failed_step=6), _pass_report()])
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    steps = [p["step"] for p in payloads]
    # 挂掉的那一遍之后**紧接着**是 diagnose，diagnose 之后**紧接着**回 draft
    i = steps.index("diagnose")
    assert steps[i - 1] == "selftest" and steps[i + 1] == "draft", steps
    assert steps.count("selftest") == 2 and steps.count("draft") == 2, steps
    assert out["end_reason"] == "delivered"
    assert len(rec.write) == 2
    assert len(rec.selftest) == 2

    fb = rec.write[1]["feedback"]
    assert fb["diagnosis"]["failed_step"] == 6
    assert fb["diagnosis"]["run"] == "delay"
    assert "第 6 步" in fb["diagnosis"]["say"]
    assert fb["diagnosis"]["say"].strip()

    # 人在 diagnose 那道闸上看到的是**哪遍挂、挂在哪**（§6.4 清单里的第三样）
    diag = [p for p in payloads if p["step"] == "diagnose"][0]
    assert "第 6 步" in diag["say"]
    assert "delay" not in diag["say"] and "failed_step" not in diag["say"], diag["say"]


def test_the_gate_facts_report_how_many_times_this_round_submitted(tmp_path):
    """**R-84：闸口的 facts 必须报出「这一轮往站方提交了几次」。**

    这次出事就是因为**看不见**：固定三遍 × 73 轮 ≈ 上百次提交到同一个 lead 表单，
    而当时没有任何一处把这个数摆到人眼前（用户是**亲手关掉 Bit 窗口**才止住的）。
    看不见的东西会再犯一次 —— 所以它要出现在两处人看得到的地方：

      - `diagnose` 那道闸（自测没过时人落在那儿）的 `facts["证据"]` **与人话**里；
      - `deliver` 那道闸的 `facts["自测"]` 里（同一份还跟着产物进 PROVENANCE）。
    """
    deps, rec = _deps(reports=[_fail_report(failed_step=6), _pass_report()])
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    diag = [p for p in payloads if p["step"] == "diagnose"][0]
    assert diag["facts"]["证据"]["submissions"] == 3, diag["facts"]["证据"]
    assert "提交了 3 次" in diag["say"], diag["say"]

    deliver = [p for p in payloads if p["step"] == "deliver"][0]
    assert deliver["facts"]["自测"]["submissions"] == 4, deliver["facts"]["自测"]


def test_a_run_nobody_allowed_to_skip_is_handed_over_as_not_run(tmp_path):
    """「跳过」不许冒充「过了」（Task 6 的判据）：图也得按 `report.passed` 走，
    而且回灌的话必须说「这一遍没跑」，**不许**编一个 failed_step 出来。"""
    unresolved = selftest.Report(
        runs=(_run("baseline", "passed", ok=True),
              _run("viewport", "skipped", ok=None, note="没人给换窗口大小的回调，这一遍没验到")),
        passed=False, allowed_skips=("country",), cdp_bin=None, site=SITE, py_path="x.py")
    deps, rec = _deps(reports=[unresolved, _pass_report()])
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    steps = [p["step"] for p in payloads]
    i = steps.index("diagnose")
    assert steps[i - 1] == "selftest" and steps[i + 1] == "draft", steps
    assert out["end_reason"] == "delivered"
    diag = rec.write[1]["feedback"]["diagnosis"]
    assert diag["failed_step"] is None
    assert "没跑" in diag["say"] and "没验到" in diag["say"], diag["say"]


def test_selftest_that_never_passes_stops_at_the_cap_instead_of_spinning(tmp_path):
    """自测永远不过：diagnose 也有上限，到顶就停（不是无限修下去）。"""
    deps, rec = _deps(reports=[_fail_report()])         # 每一遍都挂同一处
    app, cfg, _ = _build(deps=deps, caps=graph.Caps(max_diagnoses=2))
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "selftest_cap", out.get("end_note")
    assert [p["step"] for p in payloads].count("diagnose") == 2
    assert len(rec.selftest) == 3, "首版 + 两次修 = 三次自测；多一次就是上限失效"
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()
    assert "自测" in out["end_note"] or "扰动" in out["end_note"]


# ─────────────────────────── 停：不是无限转下去 ───────────────────────────


def test_the_graph_stops_when_exploration_did_not_finish(tmp_path):
    """预算到顶的探路（`budget_steps`）：**停**，并且说清楚「没走完」——
    拿半份账本去写 py 正是「自信地错」的入口（R0）。"""
    deps, rec = _deps(journey=_journey(stop_reason="budget_steps"))
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "explore_unfinished", out.get("end_note")
    assert out["visits"] == ["intake", "explore"]
    assert rec.explore and len(rec.explore) == 1, "探路不许自动重来（重开窗口是人的事）"
    assert len(rec.write) == 0, "没探完就不许写 py"
    assert "预算" in out["end_note"]
    # 上面那次停顿是 explore 那道闸（`intake` 不设闸）；**没有**第二道闸（它没往下走）
    assert [p["step"] for p in payloads] == ["explore"]


def test_a_human_pause_inside_explore_is_a_pause_not_a_failure(tmp_path):
    """人在浏览器里喊停（§6.2 的 `should_pause`）**不是失败** —— 别把「人喊停」写成红的。"""
    deps, _ = _deps(journey=_journey(stop_reason="paused"))
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "paused", out.get("end_note")
    assert "人" in out["end_note"] and "停" in out["end_note"]
    assert "失败" not in out["end_note"] and "挂" not in out["end_note"]


def test_a_pause_signal_from_the_browser_agent_is_never_swallowed(tmp_path):
    """`_Stop` 继承 `BaseException`（browser_agent:206）就是**为了不被吞成工具失败**——
    图这边不许用 `except Exception` 把它接住再降级成「探路失败」。"""
    def exploded(url, goal, budget=None, **kw):
        raise browser_agent._Stop("paused")

    deps, rec = _deps()
    deps.explore = exploded
    app, cfg, _ = _build(deps=deps)
    app.invoke(_brief(tmp_path), cfg)                    # 停在第一道闸前（= explore 那道）
    with pytest.raises(browser_agent._Stop):
        app.invoke(Command(resume="continue"), cfg)      # ← explore 那一步喊停
    assert rec.write == [] and rec.selftest == [], "喊停之后一步都不许再做"


@pytest.mark.parametrize("gate", ["explore", "draft", "lint", "selftest", "deliver"])
def test_the_human_can_stop_the_run_at_any_gate(tmp_path, gate):
    """人在**任意一道闸**上说「停」：图就停在**那一步之前**，那一步没做。

    五道闸挨个停一遍 —— 「每一步都可以被拦住」不是一句设计口号，
    是每个位置各自都验过。

    ⚠️ **`intake` 从这张名单里去掉了**（2026-09-20）：它不再设闸（运营点「开一趟」就是确认），
    所以「在 intake 上喊停」这个位置**不存在了**。这是**改准了事实**，不是删掉一条覆盖：
    原先它验的是「刚点完「开一趟」那一下也能停」—— 而那个位置恰恰是出事的那个
    （2026-09-20 真面板上，运营在这一道闸上把「继续」按成了「停」）。
    现在最早的停点是 `explore`，而它**照样排在开真窗口之前**（`test_no_browser_and_no_model_before_a_human_confirms`）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path),
                           reply=lambda p: "stop" if p["step"] == gate else "continue")

    assert out["end_reason"] == "human_stop", out.get("end_note")
    assert [p["step"] for p in payloads][-1] == gate, payloads
    assert "人" in out["end_note"]
    # 停的那一步**没做**：deliver 之后没有别的步骤，所以只有它之前那一步（自测）已经发生
    assert len(rec.selftest) == (1 if gate == "deliver" else 0), "喊停之后一步都不许再做"
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()
    assert not (tmp_path / "forms" / "sites" / ("%s.candidate.py" % SITE)).exists()


@pytest.mark.parametrize("gate", ["lint", "selftest", "deliver"])
def test_a_revision_at_a_late_gate_goes_back_to_draft_and_that_step_does_not_run(tmp_path, gate):
    """人在 `lint` / `selftest` / `deliver` 门口说「这版不行，重来」→ **回 draft 带那句话**。

    这是规格 §6 里 `review` 那一行的正身（「人否 → 回 `draft` 带人的纠正」）：
    原先这三道闸上「否」只被记进 `hints`，然后**照写不误** —— 人说了不行，产物还是出去了。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps, caps=graph.Caps(max_revisions=2))
    payloads, out = _drive(app, cfg, _brief(tmp_path),
                           reply=lambda p: ({"action": "revise", "note": "这一步不对，重来"}
                                            if p["step"] == gate else "continue"))

    # 一直打回 → 到上限就停（人驱动的循环也不许没有尽头：§6.5「别写出跑不完也不会停的图」）
    assert out["end_reason"] == "revision_cap", out.get("end_note")
    assert [r["at"] for r in out["revisions"]] == [gate] * 3
    assert all(r["note"] == "这一步不对，重来" for r in out["revisions"])
    assert [p["step"] for p in payloads].count("draft") == 3, payloads
    assert len(rec.write) == 3, "首版 + 三次打回里前两次各重写一版"
    # 每一次都是**在门口**被拦下的：那一步的工作一次都没做
    if gate == "lint":
        assert rec.lint == []
    if gate in ("lint", "selftest"):
        assert rec.selftest == []
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists(), \
        "被人否掉的版本一个字节都不许出去"
    assert not (tmp_path / "forms" / "sites" / ("%s.candidate.py" % SITE)).exists(), \
        "到上限停下来时，自测用的候选产物也不留"


def test_a_revision_is_recorded_so_a_reader_can_tell_it_from_a_kill(tmp_path):
    """「人打回」与「人喊停」在状态里分得开（评审 Important 2）。

    `revisions`（谁、在哪道闸、说了什么 —— 一路留着）≠ `end_reason == "human_stop"`。
    只留一句 `hints` 是不够的：后面读这份记录的人分不出「他改过，产物按他说的重写了」
    与「他没让这份东西出去」。
    """
    deps, rec = _deps(write=lambda spec, fb, n: ({**spec, "states": [
        {**spec["states"][0], "steps": [{**spec["states"][0]["steps"][0],
                                         "note": "第二版：按人说的改了"}]}]} if n == 2 else spec))
    app, cfg, _ = _build(deps=deps)
    state = {"asked": False}

    def reply(p):
        if p["step"] == "deliver" and not state["asked"]:
            state["asked"] = True
            return {"action": "revise", "note": "提交按钮那一下多余，去掉"}
        return "continue"

    payloads, out = _drive(app, cfg, _brief(tmp_path), reply=reply)

    assert out["end_reason"] == "delivered"
    assert out["revisions"] == [{"at": "deliver", "note": "提交按钮那一下多余，去掉"}]
    steps = [p["step"] for p in payloads]
    at = steps.index("deliver")
    assert steps[at + 1] == "draft", "否掉之后要回 draft，而不是照写"
    assert len(rec.write) == 2
    assert "提交按钮那一下多余，去掉" in rec.write[1]["feedback"]["hints"]
    # 回 draft 的那道闸上，人说的话要**摆出来**（不然这一版看上去像是自己决定重写的）
    assert "提交按钮那一下多余，去掉" in [p for p in payloads if p["step"] == "draft"][1]["say"]
    # 交出去的是**第二版**（人打回的那一版没有出去）
    src = pathlib.Path(out["py_path"]).read_text(encoding="utf-8")
    assert "第二版：按人说的改了" in src


@pytest.mark.parametrize("gate", ["explore", "draft"])
def test_the_human_can_say_something_and_it_reaches_the_draft(tmp_path, gate):
    """§6.2：「直接说该点哪」—— 人在闸口留下的那句话，要能跟着进 **draft**。

    每道能开口的闸都试：人可能在任何一步之前开口，包括**最早那一处**
    （现在是 `explore`）—— 收了却不往下带，等于没听他说话。

    ⚠️ `intake` 从名单里去掉了（2026-09-20：它不再设闸）。**最早那一处**那条覆盖没丢，
    只是那一处从 `intake` 变成了 `explore`。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path),
                    reply=lambda p: ({"action": "revise", "note": "登录弹窗要先点掉"}
                                     if p["step"] == gate else "continue"))

    assert out["end_reason"] == "delivered"
    assert out["hints"] == ["登录弹窗要先点掉"]
    assert "登录弹窗要先点掉" in rec.write[0]["feedback"]["hints"], \
        "人在「%s」那道闸说的话没进 draft —— 收了不带等于没听" % gate


def test_no_success_condition_is_refused_at_intake_before_anything_expensive(tmp_path):
    """没给成功判据 → **在 intake 就停**（评审 Important 1b）。

    为什么必须在 intake：成功判据只有人知道（§6.1），而「缺了它」这件事在开场白里就看得见。
    放到 draft 才发现，意味着**先烧掉一个 Bit 窗口 + 一次模型探路**，然后回报一个
    「写不出来」—— 那个失败看上去像模型的问题，其实是缺一个输入。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    brief = _brief(tmp_path)
    brief.pop("success_text")
    payloads, out = _drive(app, cfg, brief)

    assert out["end_reason"] == "no_success_text", out.get("end_note")
    assert out["visits"] == ["intake"], out["visits"]
    assert rec.explore == [], "没给成功判据就不该去开浏览器"
    assert rec.selftest == []
    assert "成功" in out["end_note"] and "success_text" in out["end_note"]
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()


def test_a_draft_that_loses_the_success_condition_still_stops_loudly(tmp_path):
    """intake 查过之后 draft 那道闸还得留着：判据可能在**写的过程中**丢（回调换掉了 spec）。

    「没有成功判据的产物会跑到底再谎报成功」是本项目最忌讳的那类谎 —— 这一层不许只靠
    intake 一次检查兜着。
    """
    deps, rec = _deps(write=lambda spec, fb, n: {**spec, "success_text": None})
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "draft_failed", out.get("end_note")
    assert "成功" in out["end_note"]
    assert rec.selftest == []
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()


# ───────────── 窗口层那三个旋钮（评审 Important 1 / R-31）─────────────


def test_the_window_knobs_reach_the_selftest(tmp_path):
    """`set_viewport` / `allow_skips` / `entry_url` 必须**真的传进** `selftest.run`。

    原先图一个都没传（`grep` 零命中）—— 真跑一次时第 4 遍必然记成 `skipped`，判据必然
    `passed=False`，而报告里说不出为什么（看上去像产物挂了）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, allow_skips=["country", "viewport"],
                                     entry_url="https://example-funnel.test/quiz?fresh=1"))

    assert out["end_reason"] == "delivered"
    got = rec.selftest[0]
    assert got["set_viewport"] is _viewport_cb, "窗口层那根线没传下去"
    assert tuple(got["allow_skips"]) == ("country", "viewport")
    assert got["entry_url"] == "https://example-funnel.test/quiz?fresh=1"


def test_a_missing_viewport_knob_still_stops_under_the_default_cap(tmp_path):
    """**I-2 的哨兵①（默认硬顶，不抬）**：缺 `set_viewport` 而那一遍**真轮得到** → 必须拦。

    ⚠️ 为什么「真轮得到」这句话要写清楚：撤闸（`b571ee5`）的前提是
    「第 4/5 遍这一轮根本轮不到」，**那句话在可达路径下是假的**（复审实测）。
    阶梯里**有的遍不花提交次数**：`rerun` 的 `cdp navi` 没成时产物一次都没起来 ⇒ 不花，
    于是后面**整体前移一位** —— 默认硬顶 3、`entry_url` 给了时，第 4 遍（viewport）
    到得了（`baseline` + `delay` 花掉 2 次，viewport 花第 3 次）。

    所以这条哨兵**给 `entry_url`**（那是载荷里人给的正常字段，R-6 的「刷新后重跑」）——
    闸对着一个真会响的轮次关掉，后果是：缺线时不点名，一路跑到第 4 遍才记 `skipped`，
    **烧掉真窗口 + 最多 3 次提交之后 `passed=False`**，而真正的原因（缺一根线）埋在报告里。
    """
    deps, rec = _deps(set_viewport=None)            # ← 那根线没接
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path, entry_url="https://example.test/start"))

    assert out["end_reason"] == "missing_knob", out.get("end_note")
    assert rec.selftest == [], "旋钮没接就别去动浏览器（那一遍必然记成没验到）"
    assert rec.explore == [], "这件事在 intake 就该拦下 —— 别先烧一个窗口再回报"
    assert "set_viewport" in out["end_note"]        # 缺的是哪个旋钮，点名
    assert "窗口" in out["end_note"]                # 那一遍在打什么（人话）
    assert "allow_skips" in out["end_note"]         # 另一条路：明确放弃它（§10 的诚实条款）
    # 说的是「没验到」，**不是**「产物挂了」—— 这条停不是产物的问题（那正是要避免的误读）
    for junk in ("挂了", "没通过", "自测没过"):
        assert junk not in out["end_note"], out["end_note"]
    assert "没验到" in out["end_note"], out["end_note"]
    # ⚠️ 从 `["intake"]` 改成 `[]`（2026-09-20）：`intake` 不再设闸，而它**就是**停在这里的
    # 那一步 —— 所以这一趟**一道闸都没停**就结束了。这一条要钉的「别先烧一个窗口再回报」
    # 一个字没少（上面 `rec.explore == []` / `rec.selftest == []` 两行就是它），
    # 而且顺带钉住了新行为：**这种「开场白就缺东西」的停法，运营不会被先拉去点一次「继续」**。
    assert payloads == [], payloads


def test_a_round_that_really_cannot_be_reached_does_not_stop_the_run(tmp_path):
    """**I-2 的哨兵②（默认硬顶，不抬）**：**真轮不到**的那一遍不许拦 —— 照跑、照交付。

    与上一条只差一处：**没给 `entry_url`** ⇒ `rerun` 一定花一次提交 ⇒
    `baseline` + `rerun` + `delay` 正好用满硬顶 3 ⇒ **viewport 这一轮到不了**。
    到不了的扰动配一根闸，就是「接上了但不响」：图会为一根**用不上的线**停下。

    判据落在三处：不停、**自测真跑了**（不拦就得真跑，不是绕过它）、facts 里连提都不提它。
    """
    deps, rec = _deps(set_viewport=None)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))     # ← 没给 entry_url

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert len(rec.selftest) == 1, "自测该照跑"
    # ⚠️ 这一格原先从 `intake` 那道闸上读；`intake` 不设闸之后它由**第一道闸**
    # （`explore`）接手 —— `_brief_facts` 把它摊在同一处（2026-09-20）。
    # 读的仍是**同一个事实**（连提都不提那根用不上的线），只是换了张闸去看它。
    first = payloads[0]
    assert first["step"] == "explore", first
    assert first["facts"]["还缺的窗口旋钮"] == [], first["facts"]


def test_the_reachability_question_reads_the_ladder_not_the_position():
    """`_round_reachable` 问的是**阶梯真的走不走得到**，不是「排第几」。

    这一条把那条判据**直接钉住**（上面两条是它的两个方向）：
    `entry_url` 一给，`rerun` 就可能不花次数 ⇒ 第 4 遍进射程；不给就只有前 3 遍。
    """
    deps, _ = _deps()                                      # 两个回调都不接
    brief = _brief(pathlib.Path("/tmp"))                   # 不带 entry_url
    assert [graph._round_reachable(n, brief, deps)
            for n in ("baseline", "rerun", "delay", "viewport")] == \
        [True, True, True, False], "没 entry_url：第 4 遍到不了"
    assert not graph._round_reachable("country", brief, deps)
    brief["entry_url"] = "https://example.test/start"
    assert [graph._round_reachable(n, brief, deps)
            for n in ("baseline", "rerun", "delay", "viewport", "country")] == \
        [True, True, True, True, False], "给了 entry_url：第 4 遍进射程（第 5 遍仍然到不了）"
    # 反向：默认硬顶（3）下，第 5 遍**永远**到不了（前 4 遍至少花 3 次）——
    # 它是默认允许跳过的那一遍，所以这不构成「接上了但不响」
    assert graph._round_reachable("country", dict(brief, allow_skips=[]), deps) is False


def test_a_knob_that_disappears_before_the_selftest_is_caught_at_the_selftest(tmp_path,
                                                                            monkeypatch):
    """窗口层那根线在**跑到一半**没了（进程重启后没接上）：self-test 那一步也要拦。

    两处检查各有各的场景：intake 那处管「开场白就缺」（早停，不烧窗口）；
    这处管「跑到这儿时手上这根线没了」（Task 8 恢复同一个 run 时换了 Deps）。

    ⚠️ 硬顶抬到 5（R-84：默认 3 次提交时第 4 遍**轮不到**，那根闸按裁定就不该拦）——
    这条测的是**两处检查点**，不是「哪一轮跑得到」。
    """
    monkeypatch.setattr(selftest, "MAX_SUBMISSIONS", 5)
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    out = app.invoke(_brief(tmp_path), cfg)
    # ⚠️ 从 4 次改成 3 次（2026-09-20：`intake` 不设闸，开头少一道）
    for _ in range(3):                      # explore 前 → draft → lint → selftest 前
        out = app.invoke(Command(resume="continue"), cfg)
    assert out["__interrupt__"][0].value["step"] == "selftest", out["__interrupt__"]

    deps.set_viewport = None                # ← 恢复这个 run 的「另一个进程」没接这根线
    out = app.invoke(Command(resume="continue"), cfg)

    assert out["end_reason"] == "missing_knob", out.get("end_note")
    assert "set_viewport" in out["end_note"]
    assert rec.selftest == [], "那根线没了就别去跑（跑了必然记成「这一类没验到」）"


def test_a_brief_with_nothing_in_it_does_not_open_a_browser(tmp_path):
    """开场白里连站点都没有：intake 就停下 —— 不开浏览器（§4.6 的前提层是有成本的）。"""
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, url="", goal=""))

    assert out["end_reason"] == "no_brief", out.get("end_note")
    assert rec.explore == []
    assert out["visits"] == ["intake"]


def test_without_a_window_selftest_stops_instead_of_faking_a_pass(tmp_path):
    """没有可用的窗口（前提层没起来 / 窗口死了 —— P6）：不许**跳过自测**当通过。"""
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, ws_url=None))

    assert out["end_reason"] == "no_window", out.get("end_note")
    assert rec.selftest == []
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()
    assert "窗口" in out["end_note"]


def test_the_final_bytes_are_lint_checked_before_they_are_written(tmp_path):
    """交付前最后一道自检：要落盘的**那串字节**得自己再过一次 lint。

    为什么需要：`PROVENANCE` 里有自由文本（人给的证据/意图），而它是 lint 之后才写进去的
    —— 也就是说「lint 过的那份」与「交出去的那份」不是同一串字节（上面那条测试钉着
    「只差 PROVENANCE 那一块」）。脏了就不许落盘，也不许悄悄改一改糊过去。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)

    def lint_stub(src):
        rec.lint.append(src)
        if len(rec.lint) == 2:                  # 第 1 次 = lint 节点；第 2 次 = 交付前
            return [{"line": 1, "code": "js-click", "message": "这段用 JavaScript 直接点了一下",
                     "snippet": "el.click()"}]
        return lint.check(src)

    deps.lint = lint_stub
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "deliver_lint", out.get("end_note")
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()
    assert "第 1 行" in out["end_note"]


# ───────────────────── 中断之后**真能恢复**（R-19）─────────────────────


def test_the_run_resumes_from_the_checkpointer_instead_of_starting_over(tmp_path):
    """停在 draft 之前 → 恢复 → **explore 不许再跑一遍**。

    这才是「中断后能恢复」的实证：状态是从 checkpoint 里回来的，
    而不是「从头再跑一遍、跑到同一个地方又停下」（后者在带副作用的世界里是灾难：
    再开一个 Bit 窗口、再跑一遍真站）。
    """
    deps, rec = _deps()
    app, cfg, saver = _build(deps=deps)
    out = app.invoke(_brief(tmp_path), cfg)

    for _ in range(1):                     # 开局停在 explore 前 → explore → draft 前
        out = app.invoke(Command(resume="continue"), cfg)
    assert out["__interrupt__"][0].value["step"] == "draft", out["__interrupt__"]
    assert len(rec.explore) == 1

    out = app.invoke(Command(resume="continue"), cfg)
    assert out["__interrupt__"][0].value["step"] == "lint"
    assert len(rec.explore) == 1, "恢复之后 explore 又跑了一遍 —— 那不是恢复，是重来"
    assert out["journey"].states()[0]["name"] == "landing", "账本是**从 checkpoint 回来**的"


def test_another_graph_object_can_pick_up_the_same_run(tmp_path):
    """同一个 saver、**另一个**编译出来的图也能接着跑 —— 状态在 saver 里，不在图对象里。

    这条直接对应 R-19 的裁定：checkpointer 是**接线**（Task 8 换成 Postgres 就是这里换），
    所以「谁拿着状态」必须是 saver，不能是某个进程里的对象。
    """
    deps, rec = _deps()
    app, cfg, saver = _build(deps=deps)
    app.invoke(_brief(tmp_path), cfg)                        # 开局就停在 explore 前
                                                             # （`intake` 不设闸，2026-09-20）

    other = graph.build(checkpointer=saver, deps=deps)
    out = other.invoke(Command(resume="continue"), cfg)      # 换一个图对象接着跑
    assert out["__interrupt__"][0].value["step"] == "draft", out["__interrupt__"]
    assert out["visits"] == ["intake", "explore"]
    assert len(rec.explore) == 1


def test_the_graph_refuses_to_run_without_a_checkpointer(tmp_path):
    """R-19：saver 是**必需**参数。没有它，中断之后恢复不了 —— 那正是这套图最坏的形状。"""
    deps, _ = _deps()
    with pytest.raises(ValueError) as exc:
        graph.build(checkpointer=None, deps=deps)
    assert "恢复" in str(exc.value)
    with pytest.raises(TypeError):
        graph.build(deps=deps)                                # 不许有默认值


def test_the_real_wiring_is_what_compiles(tmp_path):
    """不注桩的那条路（真 explore / 真自测 / 真 lint / 真 provenance）也得拼得起来。

    上面每条测试都注了桩 —— 桩太多的时候，「默认接线接错了」会被整片绿盖住
    （接错一个名字，谁都看不出来，直到真跑一次）。
    """
    app = graph.build(checkpointer=graph.allowlisted(InMemorySaver()))
    nodes = set(app.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == set(graph.NODES), nodes

    real = graph.Deps()
    assert real.explore is browser_agent.explore
    assert real.selftest is selftest.run
    assert real.lint is lint.check
    assert real.write is not None and real.should_pause is None
    # 默认**没有**窗口层那根线：图不许自己发明一个（发明出来的那个会让第 4 遍扰动静默跳过，
    # 而「跳过」不算过 —— R-5/R-31）。缺了它，图在 intake 停下点名。
    assert real.set_viewport is None
    # 默认那双手**改不了**自己的产物（它没有判断力）—— 这条是**说明**，不是缺陷：
    # 能改产物的角色从 `Deps.write` 注入（模型 / Console 里的人）
    spec = {"site": SITE, "success_text": SUCCESS, "states": [{"name": "s"}], "fills": {}}
    assert real.write(spec, {"violations": [{"line": 1}]}) == spec


# ─────────────────────────── 小工具 ───────────────────────────


def _constant(src: str, name: str):
    """把产物里某个常量**真解出来**（不是 grep）—— 产物是 python，就按 python 读。"""
    module = ast.parse(src)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("产物里没有 %s" % name)


def _without_provenance(src: str) -> str:
    """源码去掉 `PROVENANCE = {...}` 那一条赋值（其余逐字保留，用 dump 比）。"""
    module = ast.parse(src)
    kept = [node for node in module.body
            if not (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PROVENANCE" for t in node.targets))]
    assert len(kept) == len(module.body) - 1, "产物里应该正好有一条 PROVENANCE 赋值"
    return "\n".join(ast.dump(node) for node in kept)


# ─────────────── R-F1：自测之前换干净会话（关旧窗 → 开新窗 → 用新 ws_url）───────────────
#
# 裁定：自测跑在探路**之后**的同一个会话里，而生产每单都是新窗口（清 cookie）。
# 在脏会话里自测＝在测一个生产里不会出现的场景（证据见报告 §7.2：横幅 1 → 0）。
# ⚠️ 换的是**条件**，不是判据 —— `_judge` / `STUCK_LIMIT` / 五遍的判据一个字没动。


def test_fresh_session_replaces_the_ws_url_used_by_the_selftest(tmp_path):
    """接了这根线 → 自测**用的是新窗口**的 ws_url（不是探路那个）。"""
    calls = []

    def fresh():
        calls.append("fresh")
        return "ws://127.0.0.1:61129/devtools/browser/BRAND-NEW"

    deps, rec = _deps(fresh_session=fresh)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert calls == ["fresh"], calls
    assert rec.selftest[0]["ws_url"] == "ws://127.0.0.1:61129/devtools/browser/BRAND-NEW"
    assert out["ws_url"] == "ws://127.0.0.1:61129/devtools/browser/BRAND-NEW"
    assert "干净会话" in out["session"] and "换了" in out["session"]


def test_without_the_fresh_session_knob_it_runs_anyway_and_says_so(tmp_path):
    """没人接这根线 → **照跑**（不是跳过、不是判不过），但把「这一次不是干净会话」说出来。

    与 `set_viewport` 的处置**故意不同**：那根线缺了，第 4 遍扰动根本没做，所以必须停；
    这根线缺了，五遍**照跑**，只是条件比生产差 —— 条件差不是产物不行，
    但它必须写在人能看见的地方（`session` / `diagnose` 的 facts），不许静默。
    """
    deps, rec = _deps(fresh_session=None)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "delivered"          # 照样跑到底
    assert rec.selftest[0]["ws_url"] == WS_URL       # 用的还是原来那个窗口
    assert "不是干净会话" in out["session"]


def test_a_broken_fresh_session_does_not_kill_the_run_but_is_recorded(tmp_path):
    """换干净会话**失败**（窗口服务抖了）→ 照旧跑 + 把原因记下来（不判不过、不静默）。"""
    def boom():
        raise RuntimeError("窗口服务连不上")

    deps, rec = _deps(fresh_session=boom)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert rec.selftest[0]["ws_url"] == WS_URL
    assert "不是干净会话" in out["session"]
    assert "窗口服务连不上" in out["session"], out["session"]


def test_fresh_session_that_returns_nothing_is_not_treated_as_a_new_window(tmp_path):
    """回了空串 → 不许当成「换好了」（空字符串会被当成一个 ws_url 用下去，产物连不上）。"""
    deps, rec = _deps(fresh_session=lambda: "")
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert rec.selftest[0]["ws_url"] == WS_URL, rec.selftest[0]
    assert "不是干净会话" in out["session"]


# ── 「这一趟到底走到成功文案没有」——原先没有任何一处问过（2026-09-17 第十一轮）──
#
# `success_text` 只在三处被用：intake（必须给人）/ draft（进产物）/ selftest（产物自己判）。
# **探索那一趟有没有见到它，没人问过** —— 于是拿一条死胡同的账本去定稿+自测必然白跑
# （真站实测：有一趟就是这么白跑的，探索走到了「Sorry we are unable to match you」）。


def test_the_explore_records_whether_it_ever_saw_the_success_text(tmp_path):
    """探索见过成功文案 → 记 True，且**不加**那句提醒。"""
    book = _journey()
    book.steps.append({"state": "s", "action": "observe", "target": None,
                       "result": {"ok": True, "page_text_head": "… " + SUCCESS + " …"},
                       "note": "看了一眼页面"})
    deps, rec = _deps(journey=book)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))
    assert out.get("explore_reached_success") is True, out.get("explore_reached_success")
    assert not out.get("explore_success_note"), out.get("explore_success_note")


def test_an_explore_that_never_sees_the_success_text_stops_after_the_bounded_retries(tmp_path):
    """看过页面、可一次都没见着 → **有界重探**（最多 2 次）→ 仍不见 → **停**、如实报。

    控制器 2026-09-17 的裁定：为 False → 自动重探最多 2 次；仍 False → 停，
    **不进入定稿+自测**（拿一条走不通的账本去定稿+自测是必然白跑，第九轮实测 30 分钟全废）。
    为什么敢重探：便宜、有界、非破坏；而且**重探本身就是「能不能避开那条死路」的解法**。
    """
    book = _journey()
    book.steps.append({"state": "landing", "action": "observe", "target": None,
                       "result": {"ok": True, "page_text_head": "Get Started … 别的什么也没有"},
                       "note": "看了一眼页面"})
    deps, rec = _deps(journey=book)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert len(rec.explore) == 3, "第一次 + 最多 2 次重探：%d" % len(rec.explore)
    assert out.get("explore_reached_success") is False, out.get("explore_reached_success")
    assert out.get("end_reason") == "explore_unfinished", out.get("end_reason")
    # ⚠️ 这个数在 Task 6 修复轮 1 改了**口径**：原先的 `len(attempts)` 把**第一趟**也算进
    # 「重探」里（3）—— 那是一句人话里的假数（与本片收过的「假 0」同族）。现在写的是
    # **真的重探次数**（3 趟 = 1 + 2）。这条用例钉的仍是同一件事（到上限就停、如实报）。
    assert "重探了 2 趟" in (out.get("end_note") or ""), out.get("end_note")
    assert len(out.get("explore_attempts") or []) == 3, out.get("explore_attempts")
    assert not rec.selftest, "没走到成功文案就不该进入定稿+自测"
    #: ★ 2026-09-22 真事：抬头原先写「（走到成功文案就停）」，下面三行全是「没见到成功文案」
    #: —— 读的人（运营）当场就问「一边成功了为啥不生成脚本」。**规则不许写成结论**。
    note = out.get("explore_attempts_note") or ""
    assert "走到成功文案就停" not in note, (
        "抬头把**规则**说成了**结论**（三趟都没见到，读起来却像「见到所以停了」）：%s" % note)
    assert "判据是" in note and "见到你给的成功文案" in note, note
    #: ★ 2026-09-22：**判据的原话要在这句话里**（屏幕上只说「没见到」而不说比的是哪串字，
    #: 运营只能猜 —— 连着三次「明明到了却不认」都卡在这儿）。这句话落在 `end_note` 里，
    #: 也就是屏幕上、时间线上那一句。
    end_note = out.get("end_note") or ""
    assert "判据（原话）" in end_note, end_note
    assert "『%s』" % SUCCESS in end_note, end_note
    #: 没给判据时也要说得出（那一支是 `None` = 判不了，不是「没见到」）
    assert graph._criterion_say(None) == "（**没给**）", graph._criterion_say(None)
    assert graph._criterion_say(["a", " "]) == "『a』", graph._criterion_say(["a", " "])


def test_a_retry_that_sees_the_success_text_wins(tmp_path):
    """第 1 趟没走到、第 2 趟走到了 → 用**第 2 趟**的账本往下走，并把差异记下来。"""
    bad = _journey()
    bad.steps.append({"state": "landing", "action": "observe", "target": None,
                      "result": {"ok": True, "page_text_head": "Get Started … 没有成功文案"},
                      "note": "看了一眼页面"})
    good = _journey()
    good.steps.append({"state": "landing", "action": "observe", "target": None,
                       "result": {"ok": True, "page_text_head": "… " + SUCCESS + " …"},
                       "note": "看了一眼页面"})
    deps, rec = _deps(explores=[bad, good])
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert len(rec.explore) == 2, "第 2 趟就见到了，不该再探：%d" % len(rec.explore)
    assert out.get("explore_reached_success") is True, out.get("explore_reached_success")
    assert rec.selftest, "走到了成功文案，就该照旧往下走（定稿+自测）"
    note = out.get("explore_attempts_note") or ""
    assert "第 1 趟" in note and "第 2 趟" in note, note
    assert "没见到成功文案" in note and "见到了成功文案" in note, note


def test_the_retry_attempts_never_overlap_in_time(tmp_path):
    """**一个 job 的几趟是顺序跑的** —— 这条前提是 `_StepShots` 那道「名字唯一」的底座。

    `agent/browser_agent.py` 的 `_StepShots._name_for` 把「写之前先看盘上这个名字有没有
    人占」（`(self.where / name).exists()`）当**结构保证**用，而那道 `exists()` 到「写下去」
    之间还剩一个窗口 —— 真·**同时**写同一个目录的两趟可以都查到「没人占」。
    它成立靠的是**这条前提**：一个 job 的几趟在**一次节点执行**里同步跑完
    （`_explore` 的 `for n in range(2, EXPLORE_ATTEMPTS + 1)`，一趟跑完才起下一趟）。

    **判据**：桩 `explore` 记下每一趟的（进, 出）时刻，断言**没有一个区间与另一个相交**。
    `sleep` 是必需的：一趟探路要花真时间（~150 秒），不睡的话「同时跑」也量不出重叠 ——
    那就成了一条永远够不着的钉子。谁哪天把那一圈改成并发（`ThreadPoolExecutor` /
    起线程），这条当场红（副本里实测过：红在下面的断言上）。
    """
    book = _journey()
    book.steps.append({"state": "landing", "action": "observe", "target": None,
                       "result": {"ok": True, "page_text_head": "Get Started … 没有成功文案"},
                       "note": "看了一眼页面"})
    deps, rec = _deps(journey=book)
    inner = deps.explore
    spans: list = []

    def timed(url, goal, **kw):
        start = time.monotonic()
        try:
            time.sleep(0.02)          # 一趟真探路要花时间 —— 见 docstring
            return inner(url, goal, **kw)
        finally:
            spans.append((start, time.monotonic()))

    deps.explore = timed
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert len(spans) == 3, f"这条形状该探三趟（不探三趟就是在量空气）：{len(spans)}"
    assert out.get("explore_reached_success") is False, out.get("explore_reached_success")
    overlaps = [(i, j) for i in range(len(spans)) for j in range(i + 1, len(spans))
                if spans[i][0] < spans[j][1] and spans[j][0] < spans[i][1]]
    assert not overlaps, (
        "一个 job 的几趟**在时间上重叠**了（第 %s 对）—— `_StepShots` 的 `exists()` 那道"
        "「写之前先看盘上有没有人占」当场失去保证：两趟可以都查到「没人占」，"
        "第 1 趟特意留下的证据会被第 2 趟顶掉。区间：%s" % (overlaps, spans))


# ═══════════ Task 6：接续跑接进图里（job 级预算 / 重放前缀 / 窗口死）═══════════
#
# 图这一层管三件（设计注 §1.7 / §1.8）：
#   ① **预算改 job 级累计** —— 窗口反复死时，「防跑飞」那根线不许每次都从零开始；
#   ② **重放前缀**（`reopen` 从账本切出来的那段）要真的交到探路手里，并**先**摆到闸口上；
#   ③ **窗口死是一等停因** —— `window_gone` 与「预算走完」的处置**相反**，不许混成一个。


def _resume_rows():
    """一段**能切出前缀**的账（形状照 `browser_agent.replayable_prefix` 要的那样）。"""
    return [
        {"state": "landing", "action": "goto", "target": {"url": URL},
         "result": {"ok": True, "url": URL}, "note": "打开了 %s" % URL, "origin": "model"},
        {"state": "landing", "action": "observe", "target": {},
         "result": {"ok": True, "url": URL, "page_text_head": "Get Started 先看看"},
         "note": "看了一眼页面", "origin": "model"},
    ]


def _books(*, stop_reason, saw_success=False, extra=()):
    """一本账：停因给了，另外至少有一条 observe（否则「见到成功文案没有」判不出来）。"""
    book = _journey(stop_reason=stop_reason)
    book.steps.append({"state": "landing", "action": "observe", "target": None,
                       "result": {"ok": True,
                                  "page_text_head": ("… " + SUCCESS + " …") if saw_success
                                  else "Get Started … 别的什么也没有"},
                       "note": "看了一眼页面"})
    book.steps.extend(extra)
    return book


def test_a_window_that_died_is_its_own_end_reason_not_a_plain_unfinished_explore(tmp_path):
    """`window_gone` → `END_WINDOW_GONE`（**不是** `explore_unfinished`）。

    这两件事的处置**相反**：窗口死了该续跑（账本还在，重开一个窗口就能接着走）；
    预算走完了续跑只是再烧一次（该人看）。今天两者都落在 `explore_unfinished` 里 ——
    `reopen` 于是分不出「值得接」与「接了也白接」。
    """
    deps, rec = _deps(journey=_books(stop_reason="window_gone"))
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "window_gone", out.get("end_note")
    assert out["end_reason"] != "explore_unfinished"
    assert list(out["visits"])[-1] == "explore", out["visits"]


def test_a_budget_that_ran_out_is_still_a_plain_unfinished_explore(tmp_path):
    """反例（互斥的那一半）：预算走完**照旧**是 `explore_unfinished`，而且**不再重探**。

    预算已经花掉了，重探只会拿一个更小的预算再试一遍（job 级累计之后尤其如此）。
    """
    deps, rec = _deps(journey=_books(stop_reason="budget_steps"))
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "explore_unfinished", out.get("end_note")
    assert len(rec.explore) == 1, "预算走完了还去重探：%d 趟" % len(rec.explore)


def test_a_window_that_died_does_not_launch_more_browser_passes(tmp_path):
    """**裁定**（Task 3 遗留 2）：窗口死了**不再重探**。

    原先的重探只认「这一趟没见到成功文案」，而窗口死掉的那一趟当然也没见到 ——
    于是它会**再开 2 趟**，每趟都要起一个会话，而窗口已经不在了。
    重探对「窗口没了」这件事什么也做不到：那是 `reopen` 的事。
    """
    deps, rec = _deps(journey=_books(stop_reason="window_gone"))
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert len(rec.explore) == 1, "窗口死了还去重探：%d 趟" % len(rec.explore)
    assert out["end_reason"] == "window_gone", out.get("end_note")


def test_a_plan_that_stalled_still_gets_its_bounded_retries(tmp_path):
    """**裁定**（同一件遗留的另一半）：计划停滞**照旧重探**。

    为什么留着：停滞是「这一趟在这条路上没走通」的一种，换个随机答案有可能走通
    （重探当初就是为这个加的）。为什么它不失控：job 级预算（§1.8）封住总消耗，
    `explore_spent.attempts` 把「探了几趟」记在明面上 —— 代价是**记着的**，不是隐形的。
    """
    deps, rec = _deps(journey=_books(stop_reason="plan_stalled"))
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert len(rec.explore) == 3, "第一次 + 最多 2 次重探：%d" % len(rec.explore)
    assert out["end_reason"] == "explore_unfinished", out.get("end_note")
    assert out["explore_spent"]["attempts"] == 3, out["explore_spent"]


def test_a_criterion_written_as_a_sentence_is_flagged_on_the_first_gate(tmp_path):
    """★ 2026-09-22 **真事连着三次**：判据被写成**说明句** ⇒ 子串永远找不到 ⇒
    屏幕上只表现成「没见到成功文案」+ 自动重探 3 趟（`出现文字 check your email`、
    `出现Thank you.` —— 后者页面上就是 `Thank you.`，只差「出现」两个字）。

    判据：第一道闸的事实里**多一格提醒**（原始那一格 `成功判据` **一个字不动** ——
    facts 是给感知的 ✓）；⚠️ 它只是提醒：页面上真可能写着「出现」两个字（那就该这么填）
    ⇒ **不许拦人**（拦了就是把「我知道得比你多」写在门口）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _ = _drive(app, cfg, _brief(tmp_path, success_text="出现Thank you."))
    facts = payloads[0]["facts"]
    assert facts["成功判据"] == "出现Thank you.", facts        # 原值一个字没动
    assert "说明词" in facts["成功判据（提醒）"], facts

    #: 干净的那串 ⇒ 不提醒（别把这条钉子做成恒有）
    deps2, _rec2 = _deps()
    app2, cfg2, _ = _build(deps=deps2)
    payloads2, _ = _drive(app2, cfg2, _brief(tmp_path))
    assert payloads2[0]["facts"]["成功判据（提醒）"] == "", payloads2[0]["facts"]


def test_the_explore_gets_the_prefix_that_the_service_put_in_the_state(tmp_path):
    """`reopen` 之后第二次进 `explore`：那段前缀**真的交到了探路手里**。

    前缀是 `reopen` 从账本切出来打进状态的（`update_state`）—— 这里照同一条路走一遍。
    ⚠️ 没接那根线时 `window_alive` 是 `None`（**不编**一个「窗口活着」的假回调）。
    """
    rows = _resume_rows()

    def alive():
        return True

    deps, rec = _deps(window_alive=alive)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, {**_brief(tmp_path), "resume_from": rows,
                               "resume_note": "账本上这 2 行都满足重放的判据（R1–R4）"})

    assert rec.explore[0]["kw"].get("resume_from") == rows, rec.explore[0]["kw"]
    assert rec.explore[0]["kw"].get("window_alive") is alive, "窗口层那根线没转交给探路"


def test_the_gate_shows_what_is_about_to_be_replayed_before_the_node_starts(tmp_path):
    """重放摘要挂在**闸口**上，而且**节点开工前**就有（A3）。

    为什么必须提前：人是在这一步之前点「继续」的 —— 他得先知道「系统打算照账本
    重走哪几步、走到哪儿停下、为什么停下」，而不是等它走完再听汇报。
    """
    # 那几行**真的**切一遍（边界理由用 `replayable_prefix` 自己产的那句 ——
    # 手写一句「像边界理由的话」测的是我的措辞，不是这条接线）。
    rows = _resume_rows() + [{"state": "landing", "action": "click",
                              "target": {"selectors": ["#submit"], "text": "提交"},
                              "result": {"ok": True}, "note": "点了「提交」", "origin": "model"}]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=URL)
    assert prefix == rows[:2] and "R2" in why, (prefix, why)     # 前提：边界就切在 R2 上

    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _ = _drive(app, cfg, {**_brief(tmp_path), "resume_from": prefix,
                                    "resume_note": why})

    # ⚠️ 从 `payloads[1]` 改成 `payloads[0]`（2026-09-20）：`intake` 不设闸之后，
    # explore 那道**就是第一道**闸 —— 这条要钉的「节点开工前就有重放摘要」一个字没少。
    gate = payloads[0]
    assert gate["step"] == "explore", gate
    assert "重放" in gate["facts"], gate["facts"]
    # 没接那根线时 `window_alive` 是 `None`（**不编**一个「窗口活着」的假回调 —— R-31 同款）
    assert rec.explore[0]["kw"].get("window_alive") is None, rec.explore[0]["kw"]
    said = str(gate["facts"]["重放"])
    # 条数**从切出来的那段现算**（不去手抄一个数）：R4 的修复轮会改 `goto` 的判定，
    # 手抄的条数会在那之后变成一颗**跟这件事无关的**钉子。
    assert "%d 行" % len(prefix) in said, said
    assert "%d 个动作" % len(browser_agent.replay_actions(prefix)) in said, said
    assert "R2" in said and "提交" in said, said          # 边界**及为什么**都在


def test_the_explore_budget_is_the_whole_job_not_each_attempt(tmp_path):
    """预算改 **job 级累计**：这个 job 前几趟花掉的，要**从这一次里减掉**（不许重置）。

    重置的代价：窗口每死一次就给一份新预算 → 「防跑飞」那根线在窗口反复死的时候
    **根本不响**（而窗口反复死正是这条线要管的那种情形）。
    """
    deps, rec = _deps()
    caps = graph.Caps(explore_steps=30, explore_rounds=20)
    app, cfg, _ = _build(deps=deps, caps=caps)
    _drive(app, cfg, {**_brief(tmp_path),
                      "explore_spent": {"steps": 20, "rounds": 3, "attempts": 2}})

    budget = rec.explore[0]["budget"]
    assert budget.max_steps == 10, budget          # 30 − 20
    assert budget.max_rounds == 17, budget         # 20 − 3


def test_the_first_pass_adds_its_spend_to_the_job_total(tmp_path):
    """跑完把**这一趟的消耗加回去**（`explore_spent`），下一趟才有得减。

    ⚠️ **三个键都要断言**（复审 ③.1）：原先只断言了 `steps` 与 `attempts` ——
    于是「`rounds` 那一项写得对不对」**零覆盖**（改坏了没人红）。轮数这一项是
    **轮预算**唯一的输入（`_budget_left` 拿它减），漏了它就等于那根线没人看着。
    """
    book = _journey()
    book.rounds = 7                                # 这一趟真问了 7 轮
    deps, rec = _deps(journey=book)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["explore_spent"]["steps"] == 2, out["explore_spent"]
    assert out["explore_spent"]["rounds"] == 7, out["explore_spent"]
    assert out["explore_spent"]["attempts"] == 1, out["explore_spent"]


def test_replayed_steps_are_not_counted_as_steps_but_do_count_as_attempts(tmp_path):
    """§1.8 的那条口径：重放的步**不计 `steps`**（不花模型的钱），**计 `attempts`**（有真动作）。"""
    book = _journey()
    book.steps.append({"state": "landing", "action": "click", "note": "点了「Get Started」",
                       "target": {"selectors": ["#get-started"]},
                       "result": {"ok": True}, "origin": "replay"})
    deps, rec = _deps(journey=book)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path))

    assert out["explore_spent"]["steps"] == 2, out["explore_spent"]      # 模型走的那两步
    assert out["explore_spent"]["attempts"] == 2, out["explore_spent"]   # 一趟 + 一个重放的步


# ── 人话（Task 3 遗留 1：抬头那半句）────────────────────────────────


def test_a_stalled_plan_is_not_described_as_a_stop_nobody_can_explain():
    """`plan_stalled` 是**说得出理由**的停 —— 抬头不许再写「停得不明不白」。

    人话本来就没丢（在 `notes` 里，带「卡在第几步、卡在哪一句描述上」）；
    错的是抬头那半句：它让读账的人以为系统不知道发生了什么。
    ⚠️ 内部停因的 token（`plan_stalled`）也不许进人话（M-5）。
    """
    book = _journey(stop_reason="plan_stalled")
    book.notes.append("计划停滞：连着 6 轮没有推进，位置停在第 2 步「填邮编」上")
    say = graph._journey_say(book)

    assert "不明不白" not in say, say
    assert "计划停滞" in say, say
    assert "填邮编" in say, say                 # 人话一条都没丢
    assert "plan_stalled" not in say, say       # 内部 token 不进人话


def test_a_window_that_died_says_so_in_the_human_sentence():
    """同一个抬头的另一支：窗口没了也是说得出理由的停。"""
    book = _journey(stop_reason="window_gone")
    book.notes.append("窗口没了：连着 2 次工具调用都没成 —— 账本还留着，重开一个窗口就能接着走")
    say = graph._journey_say(book)

    assert "窗口" in say and "不明不白" not in say, say
    assert "window_gone" not in say, say


def test_the_human_sentence_says_how_much_was_replayed():
    """A3：边界那句**必须出现在 `explore_say` 里**（人读的那句话），不只是 facts 里。"""
    book = _journey()
    book.replay = {"done": 4, "landed": URL,
                   "why": "第 5 步「点『提交』」之后没有任何一次做成的观察（R2）"}
    say = graph._journey_say(book)

    assert "重放" in say, say
    assert "4" in say, say
    assert "R2" in say, say


# ── 防漂：图的形状一个字都没动 ───────────────────────────────────────


def test_the_graph_shape_did_not_move_in_this_round():
    """本片往图里加东西，**形状**一个字节都不许动（Task 7 的预检 P 表点名了这三样）。"""
    assert graph.NODES == ("intake", "explore", "draft", "lint", "selftest", "deliver",
                           "diagnose")
    assert graph.STEP_SAY == {
        "intake": "开工前的确认",
        "explore": "打开浏览器探路",
        "draft": "写这一版 py",
        "lint": "检查这一版有没有手拼 JS",
        "selftest": "在真浏览器上按扰动序列自测",
        "deliver": "把它写进站点目录",
        "diagnose": "从自测记录里定位卡在哪",
    }
    assert graph.REVISABLE == ("lint", "selftest", "deliver")


def test_a_pass_whose_replay_was_cut_short_is_not_retried(tmp_path):
    """**不许内外两层 3 次叠加**（复审 Q3）：重放被打断过的那一趟**不重探**。

    复审点出：旧形状里「不叠加」有一半是靠 Q1 那个缺陷兜住的（换了会话却没人换 `explore`
    手里那个，于是根本走不到重探）—— **那是巧合，不是设计**。现在由这一句保证：
    重放没走完 ⇒ 这一趟不是对这条路的一次完整观察 ⇒ 重探只会把同一段再撞一遍。
    """
    rows = _resume_rows()
    cut = _books(stop_reason="model_done")
    cut.replay = {"done": 0, "landed": "", "why": "窗口又死了：整段重来 3 遍都没走完"}
    deps, rec = _deps(journey=cut)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, {**_brief(tmp_path), "resume_from": rows})

    assert len(rec.explore) == 1, "重放被打断过还去重探：%d 趟" % len(rec.explore)
    note = out.get("end_note") or ""
    assert "没有重探" in note, note
    assert "重探了" not in note, "没重探却写「重探了 N 趟」——人话里的假数：%s" % note

    # 反例（同一颗钉子）：重放**走完了** → 照旧重探（它是这一趟对这条路的完整观察）
    whole = _books(stop_reason="model_done")
    whole.replay = {"done": 1, "landed": URL, "why": "这一段都重放了"}
    deps2, rec2 = _deps(journey=whole)
    app2, cfg2, _ = _build(deps=deps2)
    _drive(app2, cfg2, {**_brief(tmp_path), "resume_from": rows})
    assert len(rec2.explore) == 3, "重放走完了却不重探：%d 趟" % len(rec2.explore)


def test_a_budget_that_is_already_overspent_gives_zero_not_a_negative(tmp_path):
    """已经花超了 → 这一次的预算是 **0**（一步都不许走），**不是负数**。

    负数会让「这一趟到底还能不能动」这件事从一个配置问题变成一句看不懂的报错
    （`taken >= -3` 恒真、可读起来像是别的东西坏了）。0 是**如实**的：花超了就是花超了，
    这一趟会以 `budget_steps` 停住（`END_EXPLORE_UNFINISHED`，该人看）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps, caps=graph.Caps(explore_steps=30, explore_rounds=20))
    _, out = _drive(app, cfg, {**_brief(tmp_path),
                               "explore_spent": {"steps": 99, "rounds": 99, "attempts": 9}})

    budget = rec.explore[0]["budget"]
    assert budget.max_steps == 0 and budget.max_rounds == 0, budget
    # 「0 预算 = 一步都不许走、如实以 `budget_steps` 收场」由**真的探路**钉
    # （`test_a_zero_budget_never_even_asks_the_model`）—— 这里的桩不认预算，
    # 在这儿断那件事只是自证。这一条管的是**图交出去的那个数**：夹到 0，不是负数。
    assert len(rec.explore) == 1, rec.explore


def test_a_pass_whose_replay_needed_a_second_try_is_not_retried(tmp_path):
    """**「用满 3 次尝试」≠「被打断」**（复审 ②）：第 3 遍**可以走通** ——
    那种趟 `done` 是齐的（走完了），却抖过两下。

    只判「没走完」的话，这一形整个漏掉：重探仍然能叠成 **3 趟 × 3 遍 = 9**。
    判据看的是 `replay` 自己报出来的 **`attempts`**（试了几遍）。
    """
    rows = _resume_rows()
    shaky = _books(stop_reason="model_done")
    shaky.replay = {"done": 1, "landed": URL, "attempts": 3,
                    "why": "这一段都重放了：账上那 1 个动作照本走成了。"}
    deps, rec = _deps(journey=shaky)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, {**_brief(tmp_path), "resume_from": rows})

    assert len(rec.explore) == 1, "重放抖过两下还去重探：%d 趟" % len(rec.explore)
    note = out.get("end_note") or ""
    assert "没有重探" in note, note
    assert "系统分不出" in note, "那句人话把原因说死了（复审 ②：判据看不出是窗口还是选择器）：%s" % note


# ────────── 见到成功文案而停 = 这一趟成了（Task 15）──────────
#
# 真站那一趟（2026-09-20，`job-a4d100addd25`）：第 66 步的正文里**就是**人给的
# 成功文案，而它没停 —— 一路走到第 71 步，最后是**人按了停**才收的。
# 修好之后这一趟会以 `reached_success` 收尾，而它的语义与 `paused` / `budget_*` 相反：
# **它是一次成功的收尾**（那条通向成功的路就在账本里）⇒ 照常定稿 + 自测 + 交付，
# 不许落进「没走完 ⇒ 半份账本写不出对的 py」（R0）那一支。


def _journey_that_saw_the_line(*, stop_reason="reached_success"):
    """一份「在页面上见到了那句成功文案就收摊」的真 Journey。

    `SUCCESS` 就在最后那一眼的正文里 —— 这正是真站第 66 步的样子，
    也是 `_explore_reached_success` 判 True 的原料（两处判的是同一句话）。
    """
    book = _journey(stop_reason=stop_reason)
    book.steps.append({"state": "thanks", "action": "observe", "note": "看了一眼页面",
                       "target": {}, "result": {"ok": True, "url": URL, "title": "Thanks",
                                                "page_text_head": "已经收到你的申请。" + SUCCESS}})
    return book


def test_a_run_that_saw_the_success_line_goes_on_and_writes_the_py(tmp_path):
    """★ 见到了成功文案而停 ⇒ **照常往下写 py**（不是 `explore_unfinished`）。

    判据落在**真发生的事**上：`rec.write` 有没有被调（图有没有走到 draft），
    以及 `explore_say`（人读的那句话）有没有把这一趟说成「停得不明不白」。
    """
    deps, rec = _deps(journey=_journey_that_saw_the_line())
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] != "explore_unfinished", (
        "「成了就停」落进了「没走完」那一支 —— 那会因为成功而拒绝写 py：%r"
        % (out.get("end_note"),))
    assert len(rec.write) == 1, (
        "这一趟已经踩到成功文案了，却没往下写 py：%r" % (out.get("end_note"),))
    assert out["explore_reached_success"] is True, out.get("explore_reached_success")
    assert len(rec.explore) == 1, (
        "成了的那一趟还重探了 %d 趟 —— 重探就是在真页面上把同一段再撞一遍" % len(rec.explore))
    say = out["explore_say"]
    #: ⚠️ **正面钉子**（复审 F6）：只写「不含某些词」是**抓空**的 —— 在这句正确抬头
    #: **前面再拼一句假话**（比如「探路停下了：窗口没了」）照样全绿。
    #: 抬头是人读这句话的第一眼，所以这里钉**它本身就是那句话**。
    assert say.startswith(
        "探路走完了：页面上见到了人给的那句**成功文案**（见到就收摊"
        "—— 过了那条线之后每一次点击都可能是重复的真实请求）。"), say
    assert "不明不白" not in say, say
    assert "reached_success" not in say, "内部停因的 token 进了人话（M-5）：%s" % say


def test_the_graph_hands_explore_the_success_text_the_human_gave(tmp_path):
    """探路要拿到**人给的那句成功判据** —— 那是「见到就停」唯一的输入。

    图上没有别的地方能给得出它：`success_text` 只活在 state 里（载荷 → intake → state），
    而 `explore()` 原先只收 url / goal / 预算 / 暂停谓词那几样。少给这一个输入，
    这一趟就会**照旧**在成功之后继续点下去（2026-09-20 真站那一趟的形状）。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _drive(app, cfg, _brief(tmp_path))

    assert rec.explore, "探路一次都没被调"
    assert rec.explore[0]["kw"].get("success_text") == SUCCESS, (
        "图上没把成功判据交给探路（拿到的关键字：%r）" % sorted(rec.explore[0]["kw"]))


def test_the_success_words_are_matched_without_case():
    """★ 2026-09-22 真事（`job-f9adaede5503`）：运营把「什么算成功」写成 `check your email`，
    页面上是 `Check your email …` ⇒ 这一格判「一次都没见到」⇒ **自动重探 2 趟**，
    而每一趟都**真提交**了一遍表单（`attempts.jsonl` 里三趟各有一次提交）。

    ⚠️ 判据**没有放宽**，是**修一处不一致**：产物那边一直是不区分大小写的
    （`template.py` 的 `_succeeded()`：页那一侧与文案那一侧都 `.lower()`），而这一格原先
    连注释都写着「与产物的判据**同一口径**」—— 同一个判据两个答案，比大小写本身贵得多。
    ⚠️ 负例一起钉：页面上没有那句话 ⇒ 仍然是 `False`（「都转小写」不许被写成「什么都算见到了」）。
    """
    def _book(head):
        return browser_agent.Journey(steps=[
            {"state": "s", "action": "observe", "target": {},
             "result": {"ok": True, "url": "https://x.test/", "page_text_head": head}}])

    head = ("Check your email We've sent a code to sit****@gmail.com. "
            "Enter the code here to continue.")
    assert graph._explore_reached_success(_book(head), "check your email") is True
    assert graph._explore_reached_success(_book(head), "Check your email") is True
    assert graph._explore_reached_success(_book(head), ["谢谢", "CHECK YOUR EMAIL"]) is True
    assert graph._explore_reached_success(_book(head), "thank-you") is False
    #: 没给判据 / 一次 observe 都没有 ⇒ **判不了**（`None`，不是「没走到」）
    assert graph._explore_reached_success(_book(head), "") is None
    assert graph._explore_reached_success(browser_agent.Journey(steps=[]), "check") is None


def test_the_selftest_is_told_which_page_to_start_on(tmp_path):
    """★ 2026-09-22 真事（`job-ffa2f5165669`，**新站**那条路）：自测三遍全挂在第 1 步，
    而 self-test 的 trace 里三遍的 url 都写着 `console.bitbrowser.net`
    —— 也就是说三遍都跑在**浏览器自己的控制台页**上，不是那个站。

    为什么：`selftest.run` 拿 `start_url` 导航（导航不了就**一遍都不跑** ✓ 那条规矩在那边），
    而 `_selftest_kwargs` 原先**只在 `MODE_FIX` 下才给这一格** ⇒ **build 那一路从来没导航过**。
    修站那条路的钉子一直在（`tests/test_py_fix_legacy.py`）—— **新站这条路没人钉**，所以漏了。
    这一条钉 build：`start_url` 必须等于这一趟的入口网址。
    """
    deps, rec = _deps()
    app, cfg, _ = _build(deps=deps)
    _drive(app, cfg, _brief(tmp_path))

    assert rec.selftest, "这一趟没走到自测"
    assert rec.selftest[0].get("start_url") == URL, rec.selftest[0]
    #: `entry_url` 给了就用它（那是运营指的那一页），没给才是 `url`
    deps2, rec2 = _deps()
    app2, cfg2, _ = _build(deps=deps2)
    _drive(app2, cfg2, _brief(tmp_path, entry_url="https://deep.example.test/step-1"))
    assert rec2.selftest[0].get("start_url") == "https://deep.example.test/step-1", rec2.selftest[0]


def test_a_run_without_a_window_ends_honestly_instead_of_crashing(tmp_path):
    """★ 2026-09-22 真事（`job-82547a95ba34`）：载荷里没挑窗口 ⇒ 服务那侧塞进 `Deps` 的
    `explore` 就是 `None`（`service.py:2074`）—— 探路那一格原先**直接调它**，运营在面板上
    看到的是 `'NoneType' object is not callable`，**一点信息都没有**（既没说缺什么、
    也没说下一步点哪儿），而这一趟其实**免费、立刻能重来**：挑一个窗口就行。

    判据五条：停因是 `no_window`、`explore_reached_success` 是 `None`（**量不到**，
    不是「没走到」）、人话里点到「窗口」、**一次探路都没调**（没有手就别伸手）、
    以及**没有拦人点「继续」**（连浏览器都没开，没有任何要人点头的东西 —— 这也是
    「免费重来」那句话的依据）。⚠️ 不许抛异常：那正是这条用例存在的理由。
    """
    deps, rec = _deps()
    deps.explore = None          # ← 服务那侧 `_explore_for` 没窗口时回的就是这个（`Deps` 上那格）
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path, ws_url=""))

    assert out["end_reason"] == "no_window", out
    assert out["explore_reached_success"] is None, out
    assert "窗口" in out["end_note"], out
    assert rec.explore == [], rec.explore
    assert payloads == [], "这一趟连浏览器都没开，不该再拦人点一次「继续」"
