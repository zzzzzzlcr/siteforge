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

    assert [p["step"] for p in payloads] == ["intake", "explore", "draft", "lint",
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


def test_every_node_is_preceded_by_a_pause_that_speaks_human(tmp_path):
    """**人不是最后一道关**：每个节点之前都停一次，而且问的话是人话（D16）。

    这是 §6.2 的机器化：`deliver` 之前那次停顿与 `explore` 之前那次**同等重要** ——
    人可以在第 2 步就拦住它，而不是等它带着错走完 6 步。
    """
    deps, _ = _deps()
    app, cfg, _ = _build(deps=deps)
    payloads, _ = _drive(app, cfg, _brief(tmp_path))

    assert len(payloads) == 6, "六个节点 = 六次停顿（少一次就是「跑完才汇报」）"
    for payload in payloads:
        assert payload["say"].strip(), payload
        assert payload["can"], payload                       # 人能做什么，得写出来
        assert isinstance(payload["facts"], dict), payload
        # 不是错误码、不是选择器：说人话那一段里不许出现 traceback / 异常类名
        for junk in ("Traceback", "Exception", "<class", "selector:"):
            assert junk not in payload["say"], payload["say"]


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
    # 上面那次停顿是 explore 那道闸；**没有**第二道闸（它没往下走）
    assert [p["step"] for p in payloads] == ["intake", "explore"]


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
    app.invoke(_brief(tmp_path), cfg)
    app.invoke(Command(resume="continue"), cfg)          # intake 做完，停在 explore 前
    with pytest.raises(browser_agent._Stop):
        app.invoke(Command(resume="continue"), cfg)      # ← explore 那一步喊停
    assert rec.write == [] and rec.selftest == [], "喊停之后一步都不许再做"


@pytest.mark.parametrize("gate", ["intake", "explore", "draft", "lint", "selftest", "deliver"])
def test_the_human_can_stop_the_run_at_any_gate(tmp_path, gate):
    """人在**任意一道闸**上说「停」：图就停在**那一步之前**，那一步没做。

    六个闸挨个停一遍 —— 「每一步都可以被拦住」不是一句设计口号，
    是六个位置各自都验过。
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


@pytest.mark.parametrize("gate", ["intake", "explore", "draft"])
def test_the_human_can_say_something_and_it_reaches_the_draft(tmp_path, gate):
    """§6.2：「直接说该点哪」—— 人在闸口留下的那句话，要能跟着进 **draft**。

    三道闸都试：人可能在任何一步之前开口，包括**开工前**那一道 —— 收了却不往下带，
    等于没听他说话。
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


def test_a_knob_for_a_round_this_run_never_reaches_does_not_stop_the_run(tmp_path):
    """**R-84（用户裁定）**：跑不到的那一遍**不配一根闸** —— 缺 `set_viewport` 也不许拦。

    为什么（裁定原话的意思）：阶梯**一过就停、到顶也停**，默认硬顶 3 次提交，
    第 4 遍（viewport）**这一轮根本轮不到**。给一个跑不到的扰动配一根闸，
    就是「**接上了但不响**」（本项目的头号忌讳）：图会为一根**用不上的线**停下，
    人还得去查一个跟这次结论无关的旋钮。

    判据落在三处：不停（照跑）、**自测真跑了**、而且**照常交付**。
    """
    deps, rec = _deps(set_viewport=None)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert len(rec.selftest) == 1, "自测该照跑（不拦就得真跑，不是绕过它）"
    assert [p["step"] for p in payloads][:2] == ["intake", "explore"], payloads
    # 那一遍既然这一轮轮不到，它的旋钮**连提都不提**（不是「列出来但不拦」）——
    # 提它等于让人去查一根跟这次结论无关的线
    intake = [p for p in payloads if p["step"] == "intake"][0]
    assert intake["facts"]["还缺的窗口旋钮"] == [], intake["facts"]


def test_when_the_cap_reaches_viewport_a_missing_knob_still_stops_by_name(tmp_path, monkeypatch):
    """硬顶抬到**轮得到 viewport**时（这里抬到 5）：缺那根线**必须拦**，而且点名（R-31）。

    两条路都摆在明面上：接上线，或者明确写进 `allow_skips` —— 不许默认放过
    （R-5：跳过的遍不算过）。**这条是上面那条的反例**：闸没有整个失效，
    它只是**只在真轮得到的时候**才拦。
    """
    monkeypatch.setattr(selftest, "MAX_SUBMISSIONS", 5)
    deps, rec = _deps(set_viewport=None)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, _brief(tmp_path))

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
    assert [p["step"] for p in payloads] == ["intake"], payloads


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
    for _ in range(4):                      # intake 前 → explore → draft → lint → selftest 前
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

    for _ in range(2):                     # 开局停在 intake 前 → intake → explore → draft 前
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
    app.invoke(_brief(tmp_path), cfg)
    app.invoke(Command(resume="continue"), cfg)              # 跑完 intake，停在 explore 前

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
    assert "重探了 3 趟" in (out.get("end_note") or ""), out.get("end_note")
    assert len(out.get("explore_attempts") or []) == 3, out.get("explore_attempts")
    assert not rec.selftest, "没走到成功文案就不该进入定稿+自测"


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
