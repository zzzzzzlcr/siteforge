"""Task 3：py 产物骨架（`agent/template.py`）的契约测试。

**为什么要「真 import」这一条**（计划 Task 3 Step 1 的 ⚠️）：
计划一的 `py_emitter` 有三个断口，第一个就是「JSON 字面量发到了 Python 的位置」——
渲染出来的源码里出现 `false` / `null`，`ast.parse` **照样通过**（那是合法语法），
要到 `import` 才炸 `NameError: name 'false' is not defined`。
所以本文件的判据不止「能 parse」，是「**能 import**」（见 test_rendered_source_imports）。

**为什么早停要有反例**（计划 Task 3 + 规格 §13）：产物的重跑必须便宜，而生产 JSON 执行器
的早停有已知未修的 bug（`form_executor/json_executor.py:373` 的 `bool(_cur_tab)` 恒真 →
`_consec_fail` 每步清零 → 失败任务必磨完全程）。**所以早停得产物自带**，
而「自带」这件事只有跑一遍失败路径才验得出来 —— 见 test_early_stop_on_repeated_failure。

参考产物 `fixtures/reference_site.py` 由本文件的 SAMPLE_* 渲染而来（Task 4 拿它跑 lint），
两者必须逐字节一致：**改模板就要重渲染**，否则那条钉子会红：

    python3 tests/test_template.py        # 重新生成 fixtures/reference_site.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import template  # noqa: E402

REFERENCE = ROOT / "fixtures" / "reference_site.py"

PROD_ARGS = ("--ws-url", "--form-file", "--correlation-id", "--log-level", "--task-id")
COMMON_IMPORT = "from common import CDPHelper, setup_logger, report_url"
EXIT_LINE = "sys.exit(0 if f.run() else 1)"


# ── 参考产物的输入（唯一一份，测试与 fixtures/ 都用它）────────────────

SAMPLE_SITE = "example-funnel"
SAMPLE_SUCCESS = ["Thank you", "Your quote is ready"]
SAMPLE_STATES = [
    {
        "name": "landing",
        "when": {"url_contains": "example.test", "text_contains": ["Get Started"]},
        "steps": [
            {
                "action": "click",
                "note": "点「Get Started」进漏斗",
                "target": {
                    "text": "Get Started",
                    "role": "button",
                    "near": "hero",
                    "selectors": ["#get-started", "a.btn-primary"],
                },
            },
            {"action": "scroll", "pixels": "400", "note": "往下滚，露出问卷"},
        ],
    },
    {
        "name": "quiz",
        "when": {"text_contains": ["How often"]},
        "steps": [
            {
                "action": "click",
                "note": "选「Tub to walk-in shower」",
                # False / None 是故意的：它们就是「JSON 字面量」那类断口的钉子。
                "target": {
                    "text": "Tub to walk-in shower",
                    "role": "option",
                    "near": None,
                    "selectors": ["[data-value=tub]"],
                    "above_fold_only": False,
                },
            },
            {"action": "form", "fill": "postcode", "note": "填邮编"},
        ],
    },
    {
        "name": "details",
        "when": None,
        "steps": [
            {"action": "form", "fill": "full_name", "note": "填姓名"},
            {"action": "form", "fill": "email", "note": "填邮箱"},
            {"action": "form", "fill": "phone", "note": "填电话"},
            {
                "action": "click",
                "note": "提交，等报价页",
                "target": {
                    "text": "Get My Quote",
                    "role": "button",
                    "near": "main",
                    "selectors": ["button[type=submit]"],
                },
            },
        ],
    },
]
SAMPLE_FILLS = [
    {
        "name": "postcode",
        "source": "zip",
        "kind": "value",
        "label": "Postcode",
        "target": {
            "text": None,
            "label": "Postcode",
            "role": None,
            "near": None,
            "selectors": ["input#postcode", "input[name=zip]"],
        },
        "fallback": [{"random": "postcode"}],
    },
    {
        "name": "full_name",
        "source": "username",
        "kind": "value",
        "label": "Full name",
        "target": {
            "text": None,
            "label": "Full name",
            "role": None,
            "near": None,
            "selectors": ["input#name"],
        },
        "fallback": [{"random": "full_name"}],
    },
    {
        "name": "email",
        "source": "email",
        "kind": "value",
        "label": "Email",
        "target": {
            "text": None,
            "label": "Email",
            "role": None,
            "near": None,
            "selectors": ["input[type=email]"],
        },
        "fallback": [{"random": "email"}],
    },
    {
        "name": "phone",
        "source": "phone",
        "kind": "value",
        "label": "Phone",
        "target": {
            "text": None,
            "label": "Phone",
            "role": None,
            "near": None,
            "selectors": ["input#phone"],
        },
        "fallback": [{"random": "phone"}],
    },
]
SAMPLE_PROVENANCE = {
    "generated_at": "2026-09-17T02:00:00+08:00",
    "generator": "siteforge/v0.1",
    "env": {"proxy_country": "US", "dpr": 1, "ua": "Mozilla/5.0 (stub)", "viewport": [1280, 800]},
    "platform": {"guess": "custom-quiz", "confidence": 0.6},
    "selftest": None,
    "source": {"kind": "build", "evidence": "fixtures/reference_spec（合成站，不是真站）"},
}


def render_sample() -> str:
    return template.render(
        SAMPLE_SITE, SAMPLE_SUCCESS, SAMPLE_STATES, SAMPLE_FILLS, SAMPLE_PROVENANCE
    )


@pytest.fixture(scope="module")
def rendered() -> str:
    return render_sample()


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> pathlib.Path:
    """产物的落盘环境：`<root>/forms/sites/<site>.py` + `<root>/forms/common.py`（替身）。

    形态照抄生产（`/opt/skills/auto-farm-skill/forms/{common.py,sites/*.py}`）——
    产物靠 `sys.path.insert(0, dirname(dirname(abspath(__file__))))` 找 `common`，
    所以替身必须放在那一层，**不在模板里塞任何测试专用的钩子**。

    同层还摆一个假的 `cdp` 二进制：产物的**回退链最后一跳**（§5.1b：重新 observe）
    要调它，而这一跳在**生产路径**上也会发生，不能只在 trace 时才走。
    """
    root = tmp_path_factory.mktemp("siteforge-sandbox")
    (root / "forms").mkdir()
    (root / "forms" / "common.py").write_text(STUB_COMMON, encoding="utf-8")
    (root / "forms" / "sites").mkdir()
    fake = root / "cdp"
    fake.write_text(STUB_CDP, encoding="utf-8")
    fake.chmod(0o755)
    return root


# ── 静态契约（规格 §5.1 / §5.3 / §5.1c）────────────────────────────

def test_ast_parses(rendered):
    ast.parse(rendered)


def test_production_cli_block_is_verbatim(rendered):
    """生产契约逐字对齐 `forms/sites/{ace,blinkist,compareinsulation}.py`。

    尤其 `a = p.parse_args()` 与 `--task-id` 挤在同一行这种写法 ——
    它跟那三个文件一字不差，改它就是改契约。
    """
    for line in (
        '    p.add_argument("--ws-url", required=True); p.add_argument("--form-file", required=True)',
        '    p.add_argument("--correlation-id", required=True); p.add_argument("--log-level", default="INFO")',
        '    p.add_argument("--task-id", default=""); a = p.parse_args()',
    ):
        assert line in rendered, line
    for arg in PROD_ARGS:
        assert f'"{arg}"' in rendered
    assert EXIT_LINE in rendered


def test_common_import_line_is_verbatim(rendered):
    assert COMMON_IMPORT in rendered
    assert (
        "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))"
        in rendered
    )


def test_provenance_block_present(rendered):
    tree = ast.parse(rendered)
    assign = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "PROVENANCE" for t in node.targets
        ):
            assign = node
    assert assign is not None, "产物里必须有 PROVENANCE（§5.3）"
    prov = ast.literal_eval(assign.value)
    assert prov["generator"] == "siteforge/v0.1"
    assert prov["env"]["proxy_country"] == "US"

    # §5.3 的几个键一个都不能少（缺了就用 None 明说「不知道」，不许编）
    for key in ("generated_at", "generator", "env", "platform", "selftest", "source"):
        assert key in prov, key


def test_debug_contract_args_registered(rendered):
    assert '"--trace"' in rendered
    assert '"--stop-at"' in rendered
    # §5.1c：截图默认只在 progress=false 的步骤落，--shots all 才每步都落
    assert '"--shots"' in rendered and '"all"' in rendered


def test_actions_go_through_cdp(rendered):
    """§5.2：动作走 `self.cdp.*`；`eval` 只准出现在读路径。"""
    assert "self.cdp.click(" in rendered
    assert "self.cdp.form(" in rendered
    assert "self.cdp.scroll(" in rendered
    # 手拼写入的三件套一个都不许有（Task 4 的 lint 会打回）
    for banned in (
        "dispatchEvent",
        "getOwnPropertyDescriptor",
        "document.querySelector",
        ".click()",
    ):
        assert banned not in rendered, banned


def test_early_stop_constant_present(rendered):
    assert "STUCK_LIMIT" in rendered


# ── 真 import（ast.parse 抓不到 JSON 字面量那类断口）──────────────────

def _load(name: str, src: str, sandbox: pathlib.Path):
    path = sandbox / "forms" / "sites" / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # 这里会 NameError 就说明发的是 JSON 字面量
    finally:
        sys.path[:] = before  # 收掉产物自己 insert 的那条
    return module, path


def test_rendered_source_imports(rendered, sandbox):
    module, path = _load("rendered_imports", rendered, sandbox)
    # 顺带确认替身真的被它用上了（不然这条测试是在测空气）
    assert module.CDPHelper.__module__ == "common"
    assert path.exists()


def test_python_literals_round_trip(rendered, sandbox):
    """JSON 字面量的**反向钉子**：发出去是 Python 字面量，import 回来的值要一模一样。

    `false` / `null` 那种断口在这一条上会以 `NameError` 或者值不等的形式现形。
    """
    module, _ = _load("rendered_literals", rendered, sandbox)
    target = module.STATES[1]["steps"][0]["target"]
    assert target["near"] is None
    assert target["above_fold_only"] is False
    assert module.STATES[2]["when"] is None
    assert list(module.SUCCESS_TEXTS) == SAMPLE_SUCCESS
    assert module.FILLS["postcode"]["fallback"] == [{"random": "postcode"}]


def test_cli_help_runs(rendered, sandbox):
    """CLI 真跑一次：argparse 装错了（重名、choices 写错）这里才会响。"""
    _, path = _load("rendered_cli", rendered, sandbox)
    out = subprocess.run(
        [sys.executable, str(path), "--help"], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    for arg in PROD_ARGS + ("--trace", "--stop-at", "--shots"):
        assert arg in out.stdout


#: ad-task.py 就是这么调产物的：**只有那 5 个参数**，成功判据是 returncode == 0。
AD_TASK_ARGS = ("--ws-url", "--form-file", "--correlation-id", "--log-level")


def _run_like_ad_task(path, form_file):
    return subprocess.run(
        [
            sys.executable, str(path),
            "--ws-url", WS,
            "--form-file", str(form_file),
            "--correlation-id", "cid_1",
            "--log-level", "INFO",
        ],
        capture_output=True, text=True, timeout=120,
    )


def test_runs_under_the_production_invocation(sandbox, tmp_path):
    """**能被 ad-task.py 直接跑起来**：只给那 5 个参数 → 成功 0 / 没成功 1。

    这是产物「droppable」的判据（规格 §5.1：CLI 契约不变）。调试参数一个都不能是必填的。
    """
    click = {"action": "click", "note": "点一下",
             "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}
    states = [{"name": "go", "when": None, "steps": [click]}]
    form_file = tmp_path / "form.json"
    form_file.write_text("{}", encoding="utf-8")

    ok_module, ok_path = _load(
        "run_adtask_ok", template.render("example-dropin", "READY", states, [], SAMPLE_PROVENANCE),
        sandbox,
    )
    done = _run_like_ad_task(ok_path, form_file)
    assert done.returncode == 0, done.stdout + done.stderr
    assert ok_module.SUCCESS_TEXTS == ["READY"]

    # 见不到成功文案时必须非 0 —— 不许「跑完了就算成功」
    _, bad_path = _load(
        "run_adtask_bad",
        template.render("example-dropin2", "NEVER-APPEARS", states, [], SAMPLE_PROVENANCE),
        sandbox,
    )
    done = _run_like_ad_task(bad_path, form_file)
    assert done.returncode == 1, done.stdout + done.stderr


# ── 跑起来：早停 / 回退链 / trace / stop-at ────────────────────────

def _stub(sandbox: pathlib.Path, **fake_cdp):
    """把替身 common 配好，并把假 `cdp` 二进制摆到位（回退链那一跳要调它）。"""
    common = sys.modules["common"]
    common.STATE.reset()
    (sandbox / "fake_cdp.json").write_text(json.dumps(fake_cdp), encoding="utf-8")
    log = sandbox / "fake_cdp_calls.log"
    if log.exists():
        log.unlink()          # 每次测试从零数：产物到底调了几次 cdp CLI
    return common


def _cdp_calls(sandbox: pathlib.Path) -> list:
    """产物调 cdp CLI 的每一次（整行 argv），用来数「重跑路径上到底调了几次工具」。"""
    log = sandbox / "fake_cdp_calls.log"
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def form_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("formdata") / "form.json"
    path.write_text(json.dumps({"email": "sam@example.test", "zip": "SW1A 1AA"}), encoding="utf-8")
    return str(path)


# 假页面：动作做得越多越往后走。只有**最后一步做完**才出现成功文案 ——
# 这样「跑到底」与「中途乱报成功」能分开。
PAGE_TEXTS = ["Get Started"] + ["How often"] * 7 + ["Thank you"]
WS = "ws://127.0.0.1:9222/devtools/browser/abc"


def test_successful_run_returns_true(rendered, sandbox, form_file):
    """正控：页面按预期推进时，产物跑到底、报 URL、返回 True。"""
    module, _ = _load("run_ok", rendered, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
    )
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"

    total_steps = sum(len(s["steps"]) for s in module.STATES)
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is True
    assert len(common.STATE.actions) == total_steps, "没走完就报成功 = 假成功"
    assert "success" in common.STATE.reports, common.STATE.reports
    assert common.STATE.actions[-1][1] == "button[type=submit]"  # 最后一步是提交

    # §13：「重跑时不做调试动作」—— 不给 --trace 就不截图、不快照、不 observe。
    # （回退链那一跳的 observe 只在**选择器真挂了**的时候才会发生，正常路径一次都不该有。）
    assert common.STATE.shots == 0, "生产重跑路径不该截图"
    assert _cdp_calls(sandbox) == [], _cdp_calls(sandbox)


def _walk_states(n_steps, spec):
    """一条没有 `when` 门的状态：n 步，按 spec 造。早停/截图那两条用它，
    免得被 SAMPLE 的状态跳转搅乱步号。"""
    return [{"name": "walk", "when": None, "steps": [spec(i) for i in range(1, n_steps + 1)]}]


def _failed_click(i):
    return {
        "action": "click",
        "note": "第 %d 步：点「Go」" % i,
        "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go-a", "#go-b"]},
    }


def test_early_stop_on_repeated_failure(sandbox, form_file):
    """反例：一直失败必须**停在上限**，不许磨完全程（§13 的成本模型）。

    钉子的另一半是「不许无脑早停」：正控那条要求产物把 8 步都走完。
    """
    src = template.render(
        "example-stuck", "Thank you", _walk_states(5, _failed_click), [], SAMPLE_PROVENANCE
    )
    module, _ = _load("run_stuck", src, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": False},
    )
    common.STATE.fail_actions = True

    total_steps = sum(len(s["steps"]) for s in module.STATES)
    assert total_steps > module.STUCK_LIMIT, "前提：本来有的是步可磨"

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is False
    assert f.step == module.STUCK_LIMIT, f"停在第 {f.step} 步，应该停在第 {module.STUCK_LIMIT} 步"
    assert f.step < total_steps, "这不算早停 —— 它磨到了全程"


def test_ladder_falls_back_to_observe(rendered, sandbox, form_file):
    """§5.1b：声明式 target 的最后一跳 —— 选择器全挂 → 重新 observe → 按 text+role+near 找到。

    这是产物稳定性的**承重结构**（规格 §13 前提①），不是可选项。
    """
    module, _ = _load("run_ladder", rendered, sandbox)
    common = _stub(
        sandbox,
        observe={
            "url": "https://example.test/step-1",
            "actions": [
                {  # 陷阱：文字对、但不 visible —— 不该被选中
                    "selector": "#hidden-cta",
                    "text": "Get Started",
                    "role": "button",
                    "region": "hero",
                    "visible": False,
                    "occluded_by": None,
                    "alternates": [],
                },
                {  # 另一个陷阱：屏幕外/被遮挡
                    "selector": "#offscreen-cta",
                    "text": "Get Started",
                    "role": "button",
                    "region": "hero",
                    "visible": True,
                    "occluded_by": "offscreen",
                    "alternates": [],
                },
                {  # 真的那个
                    "selector": "#hero-cta",
                    "text": "Get Started",
                    "role": "button",
                    "region": "hero",
                    "visible": True,
                    "occluded_by": None,
                    "alternates": ["button.cta"],
                },
            ],
            "fields": [],
        },
        diff={"actionable": True},
    )
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"
    common.STATE.fail_selectors = ("#get-started", "a.btn-primary")

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is True
    used = [(a[0], a[1]) for a in common.STATE.actions]
    assert ("click", "#hero-cta") in used, common.STATE.actions
    assert "#hidden-cta" not in [a[1] for a in common.STATE.actions], "看不见的元素不该点"
    assert "#offscreen-cta" not in [a[1] for a in common.STATE.actions], "屏幕外的元素不该点"
    assert any(line.startswith("observe") for line in _cdp_calls(sandbox)), _cdp_calls(sandbox)


def test_goto_and_wait_are_supported(sandbox, form_file):
    """`goto` 走 cdp navi（**不经 eval** —— 写路径不许用 eval，§5.2）。"""
    steps = [
        {"action": "goto", "url": "https://example.test/quiz", "note": "直接打开问卷"},
        _failed_click(2),
    ]
    src = template.render(
        "example-goto", "Thank you", _walk_states(2, lambda i: steps[i - 1]), [], SAMPLE_PROVENANCE
    )
    module, _ = _load("run_goto", src, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
    )
    common.STATE.texts = ["Walk"]

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    f.run()
    calls = _cdp_calls(sandbox)
    assert any(c.startswith("navi") and "example.test/quiz" in c for c in calls), calls
    # 之后那一步照样走下去了（goto 没把它带崩）
    assert ("click", "#go-a") in [(a[0], a[1]) for a in common.STATE.actions], common.STATE.actions


def test_trace_is_jsonl_with_human_notes(rendered, sandbox, form_file, tmp_path):
    """§5.1c：trace 是 JSON Lines，每步一行，`note` 是人话（不是选择器）。"""
    module, _ = _load("run_trace", rendered, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
    )
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"
    trace = tmp_path / "trace.jsonl"

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
    assert f.run() is True

    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == len(common.STATE.actions) >= 3
    for line in lines:
        for key in ("step", "action", "target", "ok", "note", "progress", "page_sig"):
            assert key in line, line
        assert line["note"] and "#" not in line["note"], line["note"]  # 人话，不是选择器
        assert line["progress"] is True
        assert line["page_sig"], "page_sig 是「这一页长什么样」的签名，不能空"
    assert [ln["step"] for ln in lines] == sorted(ln["step"] for ln in lines)


def test_stop_at_stops_and_says_so(rendered, sandbox, form_file, tmp_path):
    """§5.1c：`--stop-at N` 跑完第 N 步就停，trace 里留一行 `{"stopped_at": N}`。"""
    module, _ = _load("run_stop", rendered, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
    )
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"
    trace = tmp_path / "stop.jsonl"

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace), stop_at=2)
    assert f.run() is False, "停在第 2 步不是「任务完成」，不许谎报成功"
    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines[-1] == {"stopped_at": 2}
    assert max(ln.get("step", 0) for ln in lines) == 2
    assert len(common.STATE.actions) == 2, "说是停在第 2 步，就该只做 2 步"
    # 浏览器不关：谁开的谁关（§5.1c），产物里根本不该有关窗口这类动作
    assert "close" not in [a[0] for a in common.STATE.actions]


def test_shots_only_for_failed_steps(sandbox, form_file, tmp_path):
    """截图默认只在没做成的那几步落；成功那几步不留图（§5.1c：省地方）。

    ⚠️ 中间的填表步是**故意**的：diff 判不了填/选（§4.6 的能力边界），
    所以一个填表步就算 progress=false 也不该被当成「失败」而留图。
    """
    steps = [
        _failed_click(1),
        {"action": "scroll", "pixels": "400", "note": "第 2 步：往下滚"},
        {"action": "form", "fill": "email", "note": "第 3 步：填邮箱"},
        _failed_click(4),
    ]
    src = template.render(
        "example-shots", "Thank you", [{"name": "walk", "when": None, "steps": steps}],
        SAMPLE_FILLS, SAMPLE_PROVENANCE,
    )
    module, _ = _load("run_shots", src, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": False},
    )
    common.STATE.fail_selectors = ("#go-a", "#go-b")
    common.STATE.texts = ["Walk"]

    trace = tmp_path / "shots.jsonl"
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
    f.run()
    pngs = sorted(p.name for p in tmp_path.glob("*.png"))
    assert pngs == ["1-after.png", "1-before.png", "4-after.png", "4-before.png"], pngs
    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [ln["ok"] for ln in lines] == [False, True, True, False]
    assert all(ln["shot_before"] and ln["shot_after"] for ln in lines if ln["ok"] is False)
    assert all(ln["shot_before"] is None and ln["shot_after"] is None for ln in lines if ln["ok"])


def test_shots_all_keeps_every_step(rendered, sandbox, form_file, tmp_path):
    """`--shots all`：每步都落（调试时人要逐步看）。"""
    module, _ = _load("run_shots_all", rendered, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
    )
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"

    trace = tmp_path / "all.jsonl"
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace), shots="all")
    assert f.run() is True
    total_steps = sum(len(s["steps"]) for s in module.STATES)
    pngs = sorted(p.name for p in tmp_path.glob("*.png"))
    assert len(pngs) == 2 * total_steps, pngs


# ── 参考产物（Task 4 拿它跑 lint）─────────────────────────────────

def test_reference_fixture_is_current():
    assert REFERENCE.exists(), f"{REFERENCE} 不存在；跑 `python3 tests/test_template.py` 生成"
    on_disk = REFERENCE.read_text(encoding="utf-8")
    fresh = render_sample()
    assert on_disk == fresh, (
        "fixtures/reference_site.py 与模板渲染结果不一致 —— 改过模板就要重渲染：\n"
        "    python3 tests/test_template.py"
    )


# ── 测试替身：common（**只在测试里**，产物里没有它）──────────────────

STUB_COMMON = '''
"""测试用 common 替身。真 common 在 /opt/skills/auto-farm-skill/forms/common.py。

它只做一件事：让产物能在没有浏览器、没有网络的机器上**真跑一遍**，
好把「早停有没有响」「回退链跳到哪一级」「trace 写成什么样」验出来。
接口按 CDPHelper 的真签名（host/port/click/form/scroll/eval/screenshot）。
"""

import base64
import json
import logging

# 1x1 的假 PNG —— 产物只负责 base64 解码后落盘，不解释内容
PNG = base64.b64encode(b"\\x89PNG\\r\\n\\x1a\\nstub").decode()


class _State:
    def __init__(self):
        self.reset()

    def reset(self, **kw):
        self.url = "https://example.test/"
        # 假页面：动作做得越多，页面越往后走。
        # 默认给一段文字是给**子进程**那条测试用的 —— 子进程里没机会配 STATE，
        # 而「按 ad-task 的调法跑起来能拿 0」必须真跑一次才算验过。
        self.texts = ["READY"]
        self.text = ""
        self.fail_actions = False
        self.fail_selectors = ()
        self.actions = []        # ("click", sel) / ("form", sel, value) / ("scroll", px) / ("goto", url)
        self.reports = []        # report_url 的 step 标签
        self.shots = 0
        self.evals = []
        self.__dict__.update(kw)

    def current_text(self):
        if not self.texts:
            return self.text
        return self.texts[min(len(self.actions), len(self.texts) - 1)]


STATE = _State()


class CDPHelper:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        head = ws_url.replace("ws://", "").replace("wss://", "").split("/")[0]
        self.host, self.port = (head.split(":") + ["9222"])[:2]

    def _fail(self, selector):
        return STATE.fail_actions or selector in STATE.fail_selectors

    def click(self, selector, frame_id=""):
        if self._fail(selector):
            return '{"error": "element not found"}'
        STATE.actions.append(("click", selector))
        return '{"clicked": true}'

    def form(self, selector, value=None, check=None, select=None, frame_id=""):
        if self._fail(selector):
            return '{"error": "element not found"}'
        STATE.actions.append(("form", selector, value if value is not None else (check or select)))
        return '{"filled": true}'

    def scroll(self, pixels="300"):
        if STATE.fail_actions:
            return '{"error": "element not found"}'
        STATE.actions.append(("scroll", pixels))
        return '{"scrolled": true}'

    def navigate(self, url):
        STATE.actions.append(("goto", url))
        return '{"ok": true}'

    def eval(self, script, frame_id=""):
        STATE.evals.append(script)
        if "innerText" in script:
            return json.dumps(STATE.current_text())
        if "location.href" in script:
            return json.dumps(STATE.url)
        return "null"

    def screenshot(self):
        STATE.shots += 1
        return PNG

    def get_page_info(self):
        return {"url": STATE.url, "title": "stub"}


def setup_logger(name):
    log = logging.getLogger(name)
    log.addHandler(logging.NullHandler())
    return log


def report_url(cdp_helper, task_id, step, log=None, base64_content=""):
    STATE.reports.append(step)
    return True
'''


# ── 测试替身：假 `cdp` 二进制（**只在测试里**）──────────────────────
#
# 产物回退链的最后一跳是「重新 observe 当前页面」（§5.1b），它只能调 cdp CLI
# （规格 §5.1b 的设计后果：那一跳发生在 worker 容器里的 py 中）。所以这一跳要验，
# 就得有一个**真的能被 subprocess 调起来**的 cdp。真 cdp 在 /company/siteforge/tools/cdp
# （Go，另一条线在改），这里给的是按契约手写的假货：只认 observe / diff。

STUB_CDP = '''#!/usr/bin/env python3
"""假 cdp（测试用）：只回答 observe / diff，内容从同目录的 fake_cdp.json 读。

每次被调都往 fake_cdp_calls.log 记一笔 —— 测试靠它数「产物在**生产重跑路径**上
到底调了几次 cdp CLI」（§13 第 3 条：重跑不许做调试动作）。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(HERE, "fake_cdp.json"), encoding="utf-8") as fp:
        CFG = json.load(fp)
except Exception:
    CFG = {}

with open(os.path.join(HERE, "fake_cdp_calls.log"), "a", encoding="utf-8") as fp:
    fp.write(" ".join(sys.argv[1:]) + "\\n")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd in CFG:
    print(json.dumps(CFG[cmd]))
    sys.exit(0)
if cmd == "navi":
    print("{}")
    sys.exit(0)
sys.exit(1)
'''


if __name__ == "__main__":
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.write_text(render_sample(), encoding="utf-8")
    print(f"wrote {REFERENCE}")
