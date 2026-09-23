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
import logging
import pathlib
import re
import subprocess
import sys
import time

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


def test_delay_knob_is_registered_and_documented(rendered):
    """R-4：扰动自测的第 3 遍（注入延迟）只有一个诚实的旋钮 —— 产物自带 `--delay`。

    `Filler.__init__` 早就收 `delay`，但 `main()` 从没把它接出来。接出来的理由是
    **扰动自测不许改写产物源码**（改写过的源码测的是另一个产物）；顺带它也让
    §5.1c 那段「调试契约」名副其实：产物自己说得出怎么让它慢下来。
    """
    assert '"--delay"' in rendered
    doc = ast.get_docstring(ast.parse(rendered))
    for flag in ("--trace", "--stop-at", "--shots", "--delay"):
        assert flag in doc, "§5.1c 是读产物的人最先看的地方，%s 要写在里面" % flag


def test_no_report_knob_is_registered_and_documented(rendered):
    """`--no-report`：这一次跑**不往生产上报**。

    为什么产物自己要有这个开关（而不是让调用方去改环境变量或改代码）：产物成功时会调
    `report_url`，把 URL 记录写进生产的接口 —— 那是生产任务要的（运维靠它看进度），
    但**自测/调试不是生产任务**，不该在生产那边留下记录。关掉它的唯一诚实做法是产物
    自己有开关，而不是让外面去猜怎么让它闭嘴。
    """
    assert '"--no-report"' in rendered
    doc = ast.get_docstring(ast.parse(rendered))
    assert "--no-report" in doc, "§5.1c 里要有它，跟 --delay 挨着"


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
    for arg in PROD_ARGS + ("--trace", "--stop-at", "--shots", "--delay"):
        assert arg in out.stdout


def test_delay_flag_reaches_the_filler(rendered, sandbox, monkeypatch):
    """`--delay` 从命令行一路接到 `Filler` 上：不给 = 拟人的随机停顿；给了 = 每步固定这么久。

    ⚠️ 这条测的是**接线**，不是「有没有那个字符串」：`main()` 里少接一根线
    （参数解析出来了但没传给 Filler），第 3 遍扰动就会变成「什么都没扰」，
    而那种空转看着跟「跑过了」一模一样。
    """
    module, path = _load("rendered_delay", rendered, sandbox)
    seen = {}

    class _Filler:
        def __init__(self, ws_url, form_file, correlation_id, task_id="",
                     trace=None, stop_at=None, shots="failed", delay=None,
                     no_report=None):
            seen.update(delay=delay, shots=shots, trace=trace, stop_at=stop_at,
                        ws_url=ws_url, no_report=no_report)

        def run(self):
            return True

    monkeypatch.setattr(module, "Filler", _Filler)
    base = [str(path), "--ws-url", WS, "--form-file", "form.json", "--correlation-id", "cid_1"]

    monkeypatch.setattr(sys, "argv", list(base))
    with pytest.raises(SystemExit) as done:
        module.main()
    assert done.value.code == 0
    assert seen["delay"] == module.DELAY_RANGE, "不给 --delay 就该是基线那套随机停顿"

    monkeypatch.setattr(sys, "argv", list(base) + ["--delay", "2"])
    with pytest.raises(SystemExit) as done:
        module.main()
    assert done.value.code == 0
    assert seen["delay"] == (2.0, 2.0), seen


def test_no_report_flag_reaches_the_filler(rendered, sandbox, monkeypatch):
    """`--no-report` 从命令行一路接到 `Filler` 上。

    默认必须是 **False**（生产重跑照旧上报：运维靠那些 URL 记录看进度）；
    给 `--no-report` 才闭嘴 —— 反向接错（默认闭嘴）就是悄悄改生产行为。
    """
    module, path = _load("rendered_noreport", rendered, sandbox)
    seen = {}

    class _Filler:
        def __init__(self, ws_url, form_file, correlation_id, task_id="",
                     trace=None, stop_at=None, shots="failed", delay=None,
                     no_report=None):
            seen["no_report"] = no_report

        def run(self):
            return True

    monkeypatch.setattr(module, "Filler", _Filler)
    base = [str(path), "--ws-url", WS, "--form-file", "form.json", "--correlation-id", "cid_1"]

    monkeypatch.setattr(sys, "argv", list(base))
    with pytest.raises(SystemExit) as done:
        module.main()
    assert done.value.code == 0
    assert seen["no_report"] is False, "不给 --no-report 就得照旧上报（生产要那些记录）"

    monkeypatch.setattr(sys, "argv", list(base) + ["--no-report"])
    with pytest.raises(SystemExit) as done:
        module.main()
    assert done.value.code == 0
    assert seen["no_report"] is True, seen


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
                {  # 另一个陷阱：屏幕外 —— **能用**，但排在首屏可见的后面（见下面的
                   # test_below_the_fold_candidate_is_usable：off-the-fold 不是不能用）
                    "selector": "#offscreen-cta",
                    "text": "Get Started",
                    "role": "button",
                    "region": "hero",
                    "visible": True,
                    "occluded_by": "offscreen",
                    "above_fold": False,
                    "alternates": [],
                },
                {  # 真的那个
                    "selector": "#hero-cta",
                    "text": "Get Started",
                    "role": "button",
                    "region": "hero",
                    "visible": True,
                    "occluded_by": None,
                    "above_fold": True,
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


# ── §5.1b 回退链的候选筛选：「折线下」不是「不能用」────────────────────
#
# observe 对**只是不在视口里**的元素也填 `occluded_by: "offscreen"`（参考站实测：
# `Get Started` = `visible: true, above_fold: false, occluded_by: "offscreen"` ——
# 它同一次 observe 里也是模型唯一答对的那个按钮）。把 offscreen 当「被挡住」一律拒掉，
# 回退链就**恰好**拒掉它存在的那类元素：声明里的选择器一失效，它一个候选都找不到，
# 这一步白算失败 —— 而声明那条路对同一个元素是照点不误的。
# 视口这一轴看 `above_fold`（target 侧对应 `above_fold_only`），不看 `occluded_by`。

def _one_click_states(selectors=("#stale-a", "#stale-b"), **target_extra):
    target = {"text": "Get Started", "role": "button", "near": None, "selectors": list(selectors)}
    target.update(target_extra)
    return [{"name": "walk", "when": None, "steps": [
        {"action": "click", "note": "点「Get Started」", "target": target}]}]


def _observe_action(selector, **kw):
    action = {"selector": selector, "text": "Get Started", "role": "button", "region": "hero",
              "visible": True, "occluded_by": None, "above_fold": True, "alternates": []}
    action.update(kw)
    return action


def _relocate_only(sandbox, name, actions, target_extra=None):
    """声明里的选择器全挂、observe 只给出 `actions` 这一个池子时的产物。"""
    src = template.render(
        "example-%s" % name, "Thank you",
        _one_click_states(**target_extra or {}), [], SAMPLE_PROVENANCE,
    )
    module, _ = _load("run_%s" % name.replace("-", "_"), src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/s", "fields": [], "actions": actions},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.fail_selectors = ("#stale-a", "#stale-b")
    return module, common


def test_below_the_fold_candidate_is_usable(sandbox, form_file):
    """折线下 ≠ 不能用：滚动一下就看得见，而回退链存在的意义正是找到它。"""
    module, common = _relocate_only(
        sandbox, "below-fold",
        [_observe_action("#below-cta", occluded_by="offscreen", above_fold=False)],
    )
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    f.run()
    assert ("click", "#below-cta") in common.STATE.actions, common.STATE.actions


def test_covered_and_invisible_candidates_are_rejected(sandbox, form_file):
    """另一半钉子：**真被挡住 / 看不见**的仍然一律不要 —— 对它们动手 = 点到别的东西。"""
    module, common = _relocate_only(
        sandbox, "covered",
        [
            _observe_action("#under-banner", occluded_by="#cookie-banner"),  # 被横幅盖住
            _observe_action("#invisible", visible=False),                    # 看不见
        ],
    )
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is False, "一个候选都不该用，这一步只能算没做成"
    assert common.STATE.actions == [], common.STATE.actions


def test_above_fold_only_target_skips_below_the_fold_candidate(sandbox, form_file):
    """target 自己声明了「必须首屏看得见」（`above_fold_only`）时，折线下的不算数。

    这一条同时钉住「`above_fold` 这个字段有人读」—— 在那之前它写在哪都没人看。
    """
    module, common = _relocate_only(
        sandbox, "above-fold-only",
        [_observe_action("#below-cta", occluded_by="offscreen", above_fold=False)],
        target_extra={"above_fold_only": True},
    )
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is False
    assert common.STATE.actions == [], common.STATE.actions


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


def test_a_goto_that_never_saw_load_still_says_so_in_the_trace(sandbox, form_file):
    """★ 2026-09-18 记的那笔账：`navi` 的「成功」现在有两种 —— 真等到 load 了，
    和「导航发出去了、但没等到 load / 没取到 frame tree」。**后者只有 stderr 说得出来**。

    而它原先被 `_do` 的 `return ""` 吞了 —— 「说了，但没人收得到」。这条用例钉两件事：

      ① 那句话**进得了 trace 的 note**（收得到）；
      ② 它里面**就算带个 `Error:`**，也**不许**把这一步判成失败 ——
         判据走 `_ok`+`ERR_MARKERS`（认词），cdp 的原话走**另一个通道**。
         这正是刚修掉的那个病：靠认词判「打不开」。
    """
    steps = [
        {"action": "goto", "url": "https://example.test/quiz", "note": "直接打开问卷"},
        _failed_click(2),
    ]
    src = template.render(
        "example-goto-echo", "Thank you", _walk_states(2, lambda i: steps[i - 1]), [], SAMPLE_PROVENANCE
    )
    module, _ = _load("run_goto_echo", src, sandbox)
    common = _stub(
        sandbox,
        observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
        diff={"actionable": True},
        # 故意塞一个 `Error:` 进去 —— 它不许把「打开了」读成「打不开」；
        # 再塞两行 chromedp 的噪音 —— 它**不许**整段灌进 note（真站实测一次 15 行）
        navi_stderr=("could not unmarshal event: unknown IPAddressSpace value: Private\n"
                     "cdp navi: 没等到 load 事件 —— Error: 这是 cdp 的原话，不是判据\n"
                     "could not unmarshal event: unknown IPAddressSpace value: Private"),
    )
    common.STATE.texts = ["Walk"]

    trace = sandbox / "goto_echo.jsonl"
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                  trace=str(trace)).run()
    first = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert first["ok"] is True, first
    assert "cdp 回执" in first["note"], first["note"]
    assert "没等到 load" in first["note"], first["note"]
    # 噪音**不许**整段灌进来 —— 但要**数出来**（「还有东西没说」）
    assert "unmarshal" not in first["note"], first["note"]
    assert "另有 2 行" in first["note"], first["note"]


def test_scroll_step_is_shaped_like_the_cli(sandbox, form_file):
    """scroll 步必须发**选择器**（`cdp scroll [selector]`）—— 不许把像素当选择器。

    为什么要有这条钉子（2026-09-17 真窗口实测）：`cdp scroll` 的位置参数**是选择器**，
    而骨架原先发的是 `self.cdp.scroll("400")` → `cdp scroll 400` →
    `Error: scroll mouse wheel failed: element not found`，**每一步都必挂**，而且挂得像
    「这一步没什么可滚的」。它是从一条**错的有损声明**长出来的（「工具是滚到某个元素、
    骨架是滚多少像素」），所以这一格必须有人守：判据是「发出去的是那个元素的选择器」。

    反例（同一格）：`pixels` 那条老路**不许**再被当选择器发出去（老产物只写了像素时，
    产物照发老调用但会在 note 里说清楚 —— 这里钉的是**别把数字当选择器**这件事）。
    """
    steps = [
        {"action": "scroll", "note": "把 Year 那个组合框滚进视口",
         "target": {"text": "Year", "role": "combobox", "near": None,
                    "selectors": ["#q1 div[role=combobox]"], "above_fold_only": False,
                    "frame_id": ""}},
    ]
    src = template.render("example-scroll-shape", "Thank you",
                          [{"name": "w", "when": None, "steps": steps}], [], SAMPLE_PROVENANCE)
    module, _ = _load("run_scroll_shape", src, sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    scrolls = [a for a in common.STATE.actions if a[0] == "scroll"]
    assert scrolls == [("scroll", "#q1 div[role=combobox]")], common.STATE.actions
    assert not any(a[1].isdigit() for a in scrolls), "像素不许被当选择器发出去"


def test_scroll_in_a_frame_goes_through_cdp_with_frame_id(sandbox, form_file):
    """子帧里的 scroll：走 cdp CLI（`scroll <选择器> --frame-id <帧>`），**不是**像素。

    为什么不能走助手：`CDPHelper.scroll` 收不了帧号，而跨源 iframe 里的元素在主帧里
    根本找不到（那一整族 bug 的样子）。判据是**真发出去的那串 argv**。
    """
    steps = [
        {"action": "scroll", "note": "把子帧里的元素滚进视口",
         "target": {"text": "Fusion", "role": "option", "near": None,
                    "selectors": ['li[data-value="Fusion"]'], "above_fold_only": False,
                    "frame_id": "AB12CD34"}},
    ]
    src = template.render("example-scroll-frame", "Thank you",
                          [{"name": "w", "when": None, "steps": steps}], [], SAMPLE_PROVENANCE)
    module, _ = _load("run_scroll_frame", src, sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    calls = _cdp_calls(sandbox)
    scroll_calls = [c for c in calls if c.startswith("scroll ")]
    assert scroll_calls, calls
    assert scroll_calls[0].startswith('scroll li[data-value="Fusion"] --frame-id AB12CD34'), calls
    assert not any(a[0] == "scroll" for a in common.STATE.actions), (
        "带帧的 scroll 该走 CLI，不该再走收不了帧号的助手：%s" % common.STATE.actions)


def test_legacy_pixel_scroll_says_it_is_legacy(sandbox, form_file):
    """只写了 `pixels` 的老形状：照发老调用，但 note 里**说清楚**它多半滚不动。

    为什么不能静默：那是一条**注定跑不通**的命令（像素被当选择器），而它失败时
    听起来像「这一步没什么可滚的」——「判不出」被读成「没问题」是本项目最贵的失败。
    """
    steps = [{"action": "scroll", "pixels": "400", "note": "往下一屏"}]
    src = template.render("example-scroll-legacy", "Thank you",
                          [{"name": "w", "when": None, "steps": steps}], [], SAMPLE_PROVENANCE)
    module, _ = _load("run_scroll_legacy", src, sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]

    trace = sandbox / "legacy.jsonl"
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                  trace=str(trace)).run()
    assert ("scroll", "400") in common.STATE.actions, common.STATE.actions   # 老行为保住
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert "只记了滚多少像素" in line["note"], line["note"]


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

    ⚠️ **2026-09-20 这条的形状变了**（Task 5 线③：一步没成 ⇒ 把它那一组从头再走一遍）：
    第 1 步没做成之后多了**一次重走**（同一个 `target`、同样的失败），
    所以现在是 5 步、失败落在第 1/2/5 步。改的是**步号**，判据一个字没放宽：
    仍然是「失败的留两张图、成功的**一张都不留**」，而且现在多钉了一条
    「重走那一步也是一步，它照样按失败留图」。
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
    assert pngs == ["1-after.png", "1-before.png", "2-after.png", "2-before.png",
                    "5-after.png", "5-before.png"], pngs
    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [ln["ok"] for ln in lines] == [False, False, True, True, False]
    assert [ln["target"] for ln in lines][:3] == ["Go", "Go", "没写名字的元素"], (
        "第 2 步是**重走**那一组的第一个动作（同一个 target「Go」），不是「往下走」: %s"
        % ([ln["target"] for ln in lines],))
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


# ── cdp 的能力缺口：observe / diff / screenshot ─────────────────────
#
# 生产那个 cdp 是**老版本**：`--help` 的命令表是
# active/click/close/completion/eval/form/help/navi/scroll/snapshot/targets ——
# **没有** observe、diff、screenshot（2026-09-17 实测）。产物要用到这三条：
# observe 是回退链的最后一跳（生产路径），diff 与 screenshot 在 trace 里。
# 缺了不会让重跑跑不动，但会**静默**地少三样东西 —— 而静默正是最贵的失败：
# 「没算出有没有推进」会被读成「没推进」，「没截图」会被读成「这步没问题」。
# 所以：缺口要用人话说出来（每条一次），trace 里的 null 要**带上为什么**。

#: 生产那个 cdp 的命令表（2026-09-17 实测 `cdp --help`）
PROD_CDP_COMMANDS = ("active", "click", "close", "completion", "eval", "form", "help",
                     "navi", "scroll", "snapshot", "targets")
#: siteforge 内核那个（两组之差 = diff / observe / screenshot）
FULL_CDP_COMMANDS = PROD_CDP_COMMANDS + ("diff", "observe", "screenshot")


def _ok_click(i):
    return {"action": "click", "note": "第 %d 步：点「Go」" % i,
            "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}


def _gap_warnings(caplog, text="不会"):
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and text in r.getMessage()]


def test_legacy_cdp_gaps_are_said_in_human_words(sandbox, form_file, tmp_path, caplog):
    """缺命令 → **人话说一次**（每条一次）+ trace 里的 null 带上为什么，而不是静默。

    ⚠️ **2026-09-20 这条的行数变了**（2 → 3，Task 5 线③ 的状态组重试）：第 1 步没做成
    之后多了**一次重走**，所以 trace 是 3 行。这正是「重试要花一个真实动作」那笔账 ——
    而这个站的产物里那一组至多 2 步（见模板里 `GROUP_RETRY_LIMIT` 的注释）。
    ⚠️ 缺命令那几句的口径**没动**：仍然每条只喊一次（重走那一步没让它多喊）。
    """
    src = template.render("example-legacy", "Thank you", _walk_states(2, _failed_click),
                          [], SAMPLE_PROVENANCE)
    module, _ = _load("run_legacy", src, sandbox)
    common = _stub(sandbox, commands=list(PROD_CDP_COMMANDS))
    common.STATE.fail_actions = True
    common.STATE.fail_screenshot = True
    trace = tmp_path / "legacy.jsonl"

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
        assert f.run() is False

    said = _gap_warnings(caplog)
    for command in ("observe", "diff", "screenshot"):
        hits = [m for m in said if command in m]
        # 3 步（含一次重走）、每一步都要 observe —— 每一条缺命令**只喊一次**
        assert len(hits) == 1, (command, said)
    assert any("SITEFORGE_CDP_BIN" in m for m in said), ("要告诉人怎么换", said)
    assert not [m for m in said if "#" in m], ("给人看的话里不许出现选择器", said)

    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3, lines
    for line in lines:
        assert line["progress"] is None, "判不出来就说判不出来 —— 不许当成「没推进」"
        assert "cdp" in (line["progress_why"] or ""), line
        assert "没算出页面有没有变化" in line["note"], line["note"]
        assert line["shots_why"], line
    # 认出来之后连试都不试：两个步骤本该各 observe 两次，实际一次都没起进程
    observed = [c for c in _cdp_calls(sandbox) if c.startswith("observe")]
    assert observed == [], ("知道它没有这条命令了就别再白起进程", _cdp_calls(sandbox))


def test_unknown_command_output_is_enough_on_its_own(sandbox, form_file, tmp_path, caplog):
    """另一条证据也够用：**这一次的输出**（cobra 明说 `unknown command`），
    不依赖 `--help` 数得出来（探不出来时不许瞎猜「它什么都没有」）。
    """
    src = template.render("example-unknown-cmd", "Thank you",
                          _walk_states(1, _failed_click), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_unknown_cmd", src, sandbox)
    common = _stub(sandbox, unknown=["observe", "diff"])     # 没有 commands 表可数
    common.STATE.fail_actions = True
    common.STATE.fail_screenshot = True
    trace = tmp_path / "unknown.jsonl"

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
        assert f.run() is False

    said = _gap_warnings(caplog)
    assert len([m for m in said if "observe" in m]) == 1, said
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert line["progress"] is None and "cdp" in (line["progress_why"] or ""), line
    # screenshot 这条命令行（CDPHelper）把 stderr 丢了，认不出「没有这条命令」——
    # 但**不许沉默**：至少要说「截图没成」并给出原因。
    assert line["shots_why"], line


def test_diff_gap_is_said_when_observe_itself_works(sandbox, form_file, tmp_path, caplog):
    """diff 那条也要能被单独认出来（observe 好用、只有 diff 缺时，别把它说成别的）。"""
    src = template.render("example-no-diff", "Thank you",
                          _walk_states(1, _failed_click), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_no_diff", src, sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/s", "actions": [], "fields": []},
                   diff={"actionable": True}, unknown=["diff"])
    common.STATE.fail_actions = True
    trace = tmp_path / "no-diff.jsonl"

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
        assert f.run() is False

    said = _gap_warnings(caplog)
    assert len([m for m in said if "diff" in m]) == 1, said
    assert not [m for m in said if "observe" in m], ("observe 好用，别说它不会", said)
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert line["progress"] is None, line
    assert "diff" in (line["progress_why"] or ""), line


def test_a_cdp_that_has_the_commands_is_not_nagged_about(rendered, sandbox, form_file,
                                                         tmp_path, caplog):
    """反例：命令都在时**一个字都不许说** —— 警告变噪音就没人看了。"""
    module, _ = _load("run_full", rendered, sandbox)
    common = _stub(sandbox, commands=list(FULL_CDP_COMMANDS),
                   observe={"url": "https://example.test/step-1", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = list(PAGE_TEXTS)
    common.STATE.url = "https://example.test/step-1"
    trace = tmp_path / "full.jsonl"

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
        assert f.run() is True

    assert not _gap_warnings(caplog), _gap_warnings(caplog)
    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [ln["progress"] for ln in lines] == [True] * len(lines)
    assert not [ln for ln in lines if ln["shots_why"]], "命令都在，不该说截图做不了"
    assert common.STATE.shots > 0, "trace 模式下本来就该真的去截图"


def test_cdp_binary_can_be_pointed_elsewhere(sandbox, tmp_path, monkeypatch):
    """`SITEFORGE_CDP_BIN`：运维不改产物就能指到带这三条命令的新版 cdp。"""
    src = template.render("example-env-cdp", "Thank you", _one_click_states(), [], SAMPLE_PROVENANCE)
    assert "SITEFORGE_CDP_BIN" in src, "要看得到那个环境变量的名字（不然没人知道能换）"

    module, _ = _load("run_env_default", src, sandbox)
    assert module.CDP_BIN == str(sandbox / "cdp"), "默认路径一个字都不许变"

    newer = tmp_path / "cdp-new"
    newer.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    newer.chmod(0o755)
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(newer))
    module, _ = _load("run_env_override", src, sandbox)
    assert module.CDP_BIN == str(newer)


def test_production_rerun_never_probes_the_binary(sandbox, form_file):
    """§13：重跑那条路**一次都不许探**（声明里的找法都成时，连 `--help` 都不跑）。

    这是「加能力探测」最容易踩坏的地方：探一次是几毫秒 —— 而重跑要跑成千上万次。
    """
    src = template.render("example-no-trace", "Thank you", _walk_states(1, _ok_click),
                          [], SAMPLE_PROVENANCE)
    module, _ = _load("run_no_trace", src, sandbox)
    common = _stub(sandbox, commands=list(PROD_CDP_COMMANDS))   # 哪怕它缺一堆命令
    common.STATE.texts = ["Thank you"]

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.run() is True
    assert _cdp_calls(sandbox) == [], _cdp_calls(sandbox)


def test_the_product_gets_the_criterion_without_the_descriptive_shell():
    """★ 2026-09-22 真事（`lp.candidate.py` 第 36 行）：判据那格写「**出现** Tailor Your Cover」，
    产物里就原样写成了 `SUCCESS_TEXTS = ['出现 Tailor Your Cover']` —— 而产物自己的
    `_succeeded()` 是**字面子串**比 ⇒ **永远匹配不上** ⇒ 真站上明明成了却报「没走到成功」。

    这一格钉「塞进产物的是**剥过壳**的那串」（与判据那一侧**同一个实现**）。
    ⚠️ 剥壳更严不更松：页面上真带那个动词时，剥完的那串照样在它里面。
    """
    assert template._success_texts("出现 Tailor Your Cover") == ["Tailor Your Cover"]
    assert template._success_texts("Tailor Your Cover") == ["Tailor Your Cover"]
    assert template._success_texts(["出现 A", "显示 B"]) == ["A", "B"]
    #: 只写了那个动词 ⇒ 剥完什么都不剩 ⇒ **照旧抛**（那是「没有判据」，不是「什么都算成功」）
    try:
        template._success_texts("出现")
    except ValueError as exc:
        assert "不能空" in str(exc), exc
    else:
        raise AssertionError("判据剥完是空的却没有拦 —— 那样产物会「跑到底再说自己成功」")


def test_the_product_waits_for_the_success_page_before_saying_it_failed():
    """★ 2026-09-22（用户原话：「是不是**等待时间不够**啥的」）：提交之后成功页常常是**异步**
    渲染出来的 —— 脚本立刻判 `_succeeded()` 判不到，然后就报「没走到成功」。

    这一格钉产物里那笔**有界**的等待还在（它是收尾那一次才花的钱；每一步之后仍然是立刻判）。
    """
    src = render_sample()
    assert "SUCCESS_WAIT_SECONDS" in src, "产物里没有那笔等待 ⇒ 异步成功页又会判不到"
    assert "def _wait_success(" in src, src[:200]
    assert "if self._wait_success():" in src, "有那个方法却没人调 —— 等于没等"
    assert "SUCCESS_POLL_SECONDS" in src, src[:200]


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
        #: 帧里那份 `location.href`（判据里「要含子帧地址」那一条靠它认）
        self.frame_url = ""
        #: **逐帧**的 `location.href`（`{帧号: 地址}`）—— 给了它，`eval(..., frame_id=…)`
        #: 就按那个帧号答；不在表里的帧号仍退回上面那个标量 `frame_url`。
        #: 为什么需要它：真站上**广告帧与问卷帧的地址本来就不一样**，而
        #: 「活帧表里有没有我要读的那一个」这条判据（`_frame_table_is_usable`）只有在这种
        #: 页面上才验得出来 —— 一个标量会把每一帧答成同一个地址，
        #: 于是「表里全是广告帧」与「表里有问卷帧」在替身眼里长得一模一样。
        self.frame_urls = {}
        # 假页面：动作做得越多，页面越往后走。
        # 默认给一段文字是给**子进程**那条测试用的 —— 子进程里没机会配 STATE，
        # 而「按 ad-task 的调法跑起来能拿 0」必须真跑一次才算验过。
        self.texts = ["READY"]
        self.text = ""
        self.fail_actions = False
        self.fail_selectors = ()
        #: 同意弹层的答复（`"<选择器>|<按钮文字>"`，空串 = 没有弹层）——
        #: 产物 `_clear_obstructions` 的探针认这个形状。
        self.consent = ""
        #: 遮挡判据的答复（`"COVER|tag#id.class"`，空串 = 没被盖着）——
        #: 产物 `_covered_by` **只认带 `COVER|` 前缀**的答复。
        self.cover = ""
        #: 遮挡判据的**逐次**答复：量一次弹一个，弹空了才退回上面那个固定值。
        #: **加载蒙版**那条判据要靠它 —— 「第一次量还盖着、再量就没了」正是蒙版走掉的样子；
        #: 用固定值只能演「一直盖着」，演不出「等一等就好了」。
        self.cover_seq = []
        #: 「**整页还在加载**」的答复（`"LOADER|<选择器>"`，空串 = 这一页好了）——
        #: 产物动手**之前**问的那一句（`_page_ready`）。与 `cover` 是**两个问题**：
        #: `cover` 问「我要点的那个东西被盖着吗」，它问「**整页**好了吗」。
        #: ⚠️ 产物**只认带 `LOADER|` 前缀**的答复（与 `cover` 的 `COVER|` 同一个道理）。
        self.page_loader = ""
        #: 同 `cover_seq`：逐次答复，用来演「加载蒙版自己走了」。
        self.page_loader_seq = []
        #: 这些**帧号**上的命令一律报错 —— 真 cdp 对一个不存在的 frameID 就是这个行为
        #: （`OOPIF eval: attach failed: No target with given id found`）。
        #: 「帧号漂了」这条路上的钉子靠它（同一个选择器在死帧里挂、在活帧里成）。
        self.fail_frames = ()
        # 生产那个 cdp 没有 screenshot 命令，而真 CDPHelper.screenshot() 只交 stdout、
        # 把 stderr 丢了（common.py:244 `return result.stdout.strip()`）—— 于是它返回空串。
        self.fail_screenshot = False
        #: 逐选择器的失败**次数表**：`{"#go": 1}` = 「#go 前 1 次报错，之后就好」。
        #: 为什么需要它：`fail_actions` 是**一路错到底**，演不出「重试之后好了」——
        #: 而「下拉没选上 ⇒ 重开一次多半能选上」正是状态组重试要治的那个形状。
        self.fail_plan = {}
        self.actions = []        # ("click", sel) / ("form", sel, value) / ("scroll", px) / ("goto", url)
        #: 每次 `form` 的 (选择器, strict, expect_label) —— 严格闸那两条参数的钉子
        self.form_strict = []
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

    def _fail(self, selector, frame_id=""):
        left = STATE.fail_plan.get(selector, 0)
        if left > 0:                       # 按次数失败：演「第一次没成、重试成了」
            STATE.fail_plan[selector] = left - 1
            return True
        return (STATE.fail_actions or selector in STATE.fail_selectors
                or (bool(frame_id) and frame_id in STATE.fail_frames))

    def click(self, selector, frame_id=""):
        if self._fail(selector, frame_id):
            return '{"error": "element not found"}'
        STATE.actions.append(("click", selector))
        return '{"clicked": true}'

    def form(self, selector, value=None, check=None, select=None, frame_id="",
             strict=False, expect_label=""):
        if self._fail(selector, frame_id):
            return '{"error": "element not found"}'
        STATE.actions.append(("form", selector, value if value is not None else (check or select)))
        # 严格闸那两条参数**单独记**（不塞进 actions 的元组，免得动到既有断言的形状）
        STATE.form_strict.append((selector, bool(strict), expect_label))
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
        if "privacy" in script:              # 同意弹层那个探针（它按 cookie|consent|gdpr|privacy 判）
            return json.dumps(STATE.consent)
        # ⚠️ 顺序要紧：`_PAGE_LOADER_JS` 里**也用 `elementFromPoint`**
        # （它要判「真的盖在视口中央」）—— 放在 `elementFromPoint` 那条**后面**
        # 就会被截走，于是「整页还在加载」永远读成「没在加载」，闸等于不存在。
        if "pageLoaderRe" in script:         # 「整页还在加载吗」那个探针
            if STATE.page_loader_seq:
                return json.dumps(STATE.page_loader_seq.pop(0))
            return json.dumps(STATE.page_loader)
        if "elementFromPoint" in script:     # 遮挡判据那个探针
            if STATE.cover_seq:
                return json.dumps(STATE.cover_seq.pop(0))
            return json.dumps(STATE.cover)
        if "document.readyState" in script:              # `goto` 之后等这一页加载
            return json.dumps("complete")
        if "innerText" in script:
            return json.dumps(STATE.current_text())
        if "location.href" in script:
            # 帧里那一份（`frame_url`）与主帧那一份（`url`）可以不一样 ——
            # 真站上它们本来就不一样（子帧是部件、主帧是壳）；
            # 逐帧表 `frame_urls` 给了就先查它（见它的注释：广告帧与问卷帧不同址那一格靠它）
            if frame_id and frame_id in STATE.frame_urls:
                return json.dumps(STATE.frame_urls[frame_id])
            return json.dumps(STATE.frame_url if frame_id else STATE.url)
        return "null"

    def screenshot(self):
        STATE.shots += 1
        if STATE.fail_screenshot:
            return ""
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
"""假 cdp（测试用）：observe / diff 从同目录的 fake_cdp.json 读，navi 一律成，
`--help` 吐命令表（cobra 那个形状）。

`fake_cdp.json` 里给了 `commands` 时，它还照**真 cdp（cobra）**的样子对待命令表以外的
子命令：stderr 上一行 `Error: unknown command "x" for "cdp"`、退出码 1。
生产那个 cdp 就是这么答的 —— 它**没有** observe / diff / screenshot（2026-09-17 实测）。

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
known = CFG.get("commands")
if cmd in ("--help", "-h", "help"):
    if known is None:
        print("stub cdp: 这份配置没给命令表")     # 探不出来（不是「什么都没有」）
        sys.exit(0)
    print("A CLI tool to interact with Chrome DevTools Protocol")
    print("")
    print("Usage:")
    print("  cdp [command]")
    print("")
    print("Available Commands:")
    for name in known:
        print("  %-11s stub" % name)
    print("")
    print("Flags:")
    print("  -h, --help   help for cdp")
    sys.exit(0)
if cmd in (CFG.get("unknown") or []):
    sys.stderr.write('Error: unknown command "%s" for "cdp"\\n' % cmd)
    sys.exit(1)
if known is not None and cmd not in known:
    sys.stderr.write('Error: unknown command "%s" for "cdp"\\n' % cmd)
    sys.exit(1)
if cmd in CFG:
    print(json.dumps(CFG[cmd]))
    sys.exit(0)
if cmd == "navi":
    # `navi_stderr`：真 cdp 在「导航发出去了、但没等到 load / 没取到 frame tree」时
    # 会往 stderr 说一句（退出码仍是 0）—— 产物要把那句收进 trace 的 note。
    if CFG.get("navi_stderr"):
        sys.stderr.write(CFG["navi_stderr"] + "\\n")
    print("{}")
    sys.exit(0)
sys.exit(1)
'''


if __name__ == "__main__":
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.write_text(render_sample(), encoding="utf-8")
    print(f"wrote {REFERENCE}")


# ───────── 帧号漂了：声明里的选择器要**拿到活着的帧里再试一次**（③ 钉死）─────────
#
# 现场（2026-09-17 真站、真窗口）：重放一开始的 `goto` 会把页面重载 → 跨源 iframe 重建 →
# 账本里的 frameID **全成死号**（实测 `OOPIF eval: attach failed: No target with given id
# found`）。于是声明里的选择器一条都用不了，整条路只能靠**语义**回落 ——
# 而那个站上三个 MUI 组合框的 `text` 都是**零宽空格**，语义判据分不开它们：
# 实测点开了**另一个**下拉的菜单，紧接着「点它的选项」那一步当然找不到
# （`2020` 不在品牌菜单里）—— 表现为「页面上没找到「2020」」。
#
# 修法：帧号漂了，**页面结构没变** —— 声明里那条 nth-of-type 路径仍然是这个元素
# 最精确的身份，所以先把它拿到**活着的帧**里试一遍（真站实测：账本那三条路径在活帧里
# 各命中 1 个，指向的正是原来那三个控件），再轮到语义候选。


def _drift_sandbox(sandbox, live_frame="LIVE1234"):
    """声明里的选择器在**死帧**里够不着、在**活帧**里能命中的那种页面模型。"""
    return {
        "observe": {
            "url": "https://example.test/",
            "actions": [
                {"selector": "#year", "text": "\u200b", "role": "combobox", "region": "body",
                 "visible": True, "occluded_by": None, "above_fold": True,
                 "stability": "high", "alternates": [],
                 "frame_path": ["main", live_frame]},
            ],
            "fields": [],
        },
        "diff": {"actionable": True},
    }


def test_declared_selectors_are_retried_in_the_live_frame(sandbox, form_file):
    """帧号漂了 → **先**把声明里的选择器拿到活帧里试，别直接跳语义回落。

    为什么这条重要（真站实测）：那个站上三个 MUI 组合框的 `text` **都是零宽空格**，
    语义判据分不开它们 —— 只按语义回落就点开了**另一个**下拉的菜单，
    紧接着「点它的选项」那一步必然找不到（表现为「页面上没找到「2020」」）。
    声明里那条 nth-of-type 路径才是这个元素的身份，它只是**帧号过期**了。
    """
    module, _ = _load(
        "run_drift",
        template.render("example-drift", "Thank you",
                        _one_click_states(selectors=["#year"], frame_id="DEAD0000"),
                        [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox, **_drift_sandbox(sandbox))
    common.STATE.texts = ["Walk"]
    # 死帧里那条命令会报错（真 cdp 的行为：`No target with given id found`）；
    # 同一个选择器换到活帧上就该成 —— 这正是「帧号漂了」这件事的形状。
    common.STATE.fail_frames = ("DEAD0000",)

    trace = sandbox / "drift.jsonl"
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                  trace=str(trace)).run()

    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert line["selector_used"] == "#year", line
    assert line["frame_id"] == "LIVE1234", line          # ← **活帧**，不是账本那个死号
    assert line["ok"] is True, line


# ─────────── 同意弹层（A）与遮挡判据（C）—— 2026-09-17 真站实测的那条链 ───────────
#
# 链（每一环都有现场证据，见 fix-cookie-overlay-report.md）：
#   ① 生产**每单都是新窗口**（清 cookie）→ 每单都会遇到同意弹层；
#   ② 探索跑在一个「弹层已经点掉」的会话里 → **账本学的是没有弹层的世界**；
#   ③ 换到干净会话自测（R-F1）→ 弹层盖住答题区；
#   ④ 快路点击不看遮挡 → 点到弹层上、cdp 照样回 ok → **点了个寂寞被记成做成了**。


def test_the_product_dismisses_a_consent_overlay_before_it_starts(sandbox, form_file):
    """A：开跑之前**自己**把同意弹层点掉 —— 不许指望账本里恰好有这一步。"""
    module, _ = _load(
        "run_consent",
        template.render("example-consent", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.consent = "#onetrust-accept-btn-handler|Accept Cookies"

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert "#onetrust-accept-btn-handler" in clicks, common.STATE.actions
    assert clicks.index("#onetrust-accept-btn-handler") == 0, (
        "弹层要在**第一步之前**点掉（否则第一步就点到它上面）：%s" % clicks)


def test_without_an_overlay_nothing_is_clicked_for_it(sandbox, form_file):
    """反例（同一格）：没有弹层时**一下都不点** —— 「没有」是正常情况，不是失败。"""
    module, _ = _load(
        "run_noconsent",
        template.render("example-noconsent", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.consent = ""            # 没有弹层

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert clicks == ["#go"], clicks


def test_a_covered_element_is_not_clicked_and_does_not_report_ok(sandbox, form_file):
    """C：目标被别的东西盖着 → **不点**，而且这一步**不算做成**（不许静默假成功）。

    真站实测的形状：干净会话里同意弹层盖住答题区，`Sedan` 那一下点到弹层上、
    cdp 回 `ok`（`match_count: 1` 说的是「选择器命中 1 个」，不是「点到的就是它」）。
    """
    module, _ = _load(
        "run_covered",
        template.render("example-covered", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.cover = "COVER|div#onetrust-banner"      # 盖着它的是同意弹层
    # 生产那个 20 秒预算是给**加载蒙版**的（真站量出来的）。这条用例量的是**另一件事**
    # ——「盖着就不点、不许记成做成」，遮挡是**永久**的那种。别让它真等 20 秒。
    module.COVER_WAIT_SECONDS = 0.3
    module.COVER_POLL_SECONDS = 0.02

    trace = sandbox / "covered.jsonl"
    ok = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                       trace=str(trace)).run()
    assert ok is False, "点到被盖住的元素上不许算做成"
    assert not [a for a in common.STATE.actions if a[1] == "#go"], (
        "被盖着就不该点下去：%s" % common.STATE.actions)
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert line["ok"] is False, line


def test_an_uncovered_element_is_clicked_as_usual(sandbox, form_file):
    """反例（同一格）：没被盖着就照常点（这一改不该让正常的路变慢或变怂）。"""
    module, _ = _load(
        "run_uncovered",
        template.render("example-uncovered", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.cover = ""                               # 没盖着
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ("click", "#go") in common.STATE.actions, common.STATE.actions


def test_a_loading_mask_is_waited_out_and_then_clicked(sandbox, form_file):
    """★ 2026-09-18 真站（gowizard）那条：盖着目标的是**加载蒙版**，页面还没加载完。

    用户原话：「反正在人的视角来看他不就是加载蒙版吗？没加载完而已」。
    所以判据是**先等**（蒙版自己会走），**不是**「一看盖着就判这一步不做」——
    后者把「还没好」当成「不能做」，与 `_applies` 那个病是同一个。

    这条用例是那一改的**正例**：蒙版走了 → 照常点下去。
    """
    module, _ = _load(
        "run_mask_clears",
        template.render("example-mask", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    # 头两次量还盖着（`div.js-chameleon-page-loader` —— 真站上那个选择器），再量就没了
    common.STATE.cover_seq = ["COVER|div.js-chameleon-page-loader"] * 2 + [""] * 20
    module.COVER_WAIT_SECONDS = 5.0      # 够宽，让「等」这件事真的发生
    module.COVER_POLL_SECONDS = 0.02     # 但别让用例真等 5 秒

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ("click", "#go") in common.STATE.actions, (
        "蒙版自己走了就该照常点，而不是判这一步不做：%s" % common.STATE.actions)


def test_a_mask_that_never_clears_is_not_waited_for_twice(sandbox, form_file):
    """同一格的反面：**等过、没等到它走**的东西，后面不再等（它不是加载蒙版，是真挡路）。

    没有这一格，每步都要白赔一笔预算，十来步就是一分多钟的空等。
    判据用**量的次数**：第一次该量不止一次（在等），第二次该只量一次就返回。
    """
    module, _ = _load(
        "run_mask_stuck",
        template.render("example-mask-stuck", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.cover = "COVER|div#onetrust-banner"      # 一直在，不会自己走
    module.COVER_WAIT_SECONDS = 0.3
    module.COVER_POLL_SECONDS = 0.02

    def measured():
        return len([s for s in common.STATE.evals if "elementFromPoint" in s])

    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    f._covered_by("#a", until=time.monotonic() + module.COVER_WAIT_SECONDS)
    n1 = measured()
    f._covered_by("#b", until=time.monotonic() + module.COVER_WAIT_SECONDS)
    n2 = measured()
    assert n1 > 1, "第一次该等它（量了不止一次），实际量了 %d 次" % n1
    assert n2 - n1 == 1, (
        "等过一次没走的东西，第二次该只量一次就返回，实际又量了 %d 次" % (n2 - n1))


def test_the_relocated_candidate_is_checked_for_a_cover_too(sandbox, form_file):
    """★ 2026-09-18 真站逮到的那条缝：**快路有遮挡判据，重找那条路没有**。

    声明里的选择器全被盖住时，恰恰是**最该拦的那一下**从重找那条路上溜过去 ——
    真站日志就是这个形状：`fallback_level: 1` + `ok: true` + `progress: false`。

    这条用例是那个缺口的钉子：重找出来的候选**也要过同一道遮挡判据**。
    """
    module, common = _relocate_only(
        sandbox, "relocate-covered", [_observe_action("#below-cta")],
    )
    common.STATE.cover = "COVER|div.js-chameleon-page-loader"
    module.COVER_WAIT_SECONDS = 0.2      # 它会一直在（固定值），别真等
    module.COVER_POLL_SECONDS = 0.02

    ok = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ok is False, "重找出来的候选被盖着，不许算做成"
    assert ("click", "#below-cta") not in common.STATE.actions, (
        "重找那条路也要过遮挡判据 —— 被盖着就不许点：%s" % common.STATE.actions)


def test_a_step_does_not_act_while_the_whole_page_is_still_loading(sandbox, form_file):
    """★★ 2026-09-18 那处**动手之前**的缺口：先问「这一页就绪了吗」，再动手。

    真站对照（这是这一改的全部理由）：
      · **成功那趟**（生产单 26005787）第 4 步的遮挡物是 `iframe#mvfFormWidget-…`
        —— **表单 iframe 本人**，= 表单已经就位；
      · **今天 5 趟全挂**，第 4 步的遮挡物是 `div.js-chameleon-page-loader`
        —— **加载蒙版**，= 表单还没加载完，**而产物照样往下走**，于是后面全错。

    现在只有「动手**之后**问页面变没变」（`progress` / `_covered_by`），
    缺「动手**之前**问页面好没好」。这条用例钉的就是那一格。
    """
    module, _ = _load(
        "run_page_loading",
        template.render("example-page-loading", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.page_loader = "LOADER|div.js-chameleon-page-loader"   # 一直在加载，走不掉
    module.PAGE_READY_SECONDS = 0.3
    module.PAGE_READY_POLL = 0.02

    ok = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ok is False, "整页还在加载就不许算做成"
    assert not [a for a in common.STATE.actions if a[0] == "click"], (
        "整页还在加载时**不许动手**（点了也是白点，还会把后面的判据全带错）：%s"
        % common.STATE.actions)


def test_the_page_loader_is_waited_out_and_then_the_step_runs(sandbox, form_file):
    """同一格的反面：**整页加载完（蒙版自己走了）→ 照常动手**。

    没有这一格，就会写出「只要见过蒙版就整趟不做事」那种怂改法 ——
    而那正好会把成功那趟也一起毙掉。
    """
    module, _ = _load(
        "run_page_ready",
        template.render("example-page-ready", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    # 头两次量还在加载，再量就好了
    common.STATE.page_loader_seq = ["LOADER|div.js-chameleon-page-loader"] * 2 + [""] * 30
    module.PAGE_READY_SECONDS = 5.0
    module.PAGE_READY_POLL = 0.02

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ("click", "#go") in common.STATE.actions, (
        "整页加载好了就该照常点，而不是整趟不做事：%s" % common.STATE.actions)


def test_a_junk_answer_is_not_read_as_a_loading_page(sandbox, form_file):
    """★ 同一格的**闸**：答复形状不认识时，不许读成「整页在加载」。

    这条是**被真事逼出来的**（2026-09-18）：我第一版探针**没要前缀**，
    于是碰上一个「对任何 eval 都回一句页面文字」的替身 cdp（`test_selftest.py` 那个），
    那句话被读成「盖着整页的加载物」—— **每一步干等 60 秒**，
    全量套件 48 秒 → **293 秒**，一条 selftest 用例当场红。

    与 `_covered_by` 的 `COVER|` 是**同一个病**：「量不出来」被读成「量出来了」。
    ⚠️ 而且这一格的误判**比那边严重** —— 那边只是不点一个候选，
    这边是**整趟什么都不做**。
    """
    module, _ = _load(
        "run_page_junk",
        template.render("example-page-junk", "Thank you",
                        _one_click_states(selectors=["#go"]), [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    # 形状不认识的一句答复（真站上替身 cdp 就是这么回的）—— **不许**当成在加载
    common.STATE.page_loader = "Thank you — https://example.test/done"
    module.PAGE_READY_SECONDS = 0.3
    module.PAGE_READY_POLL = 0.02

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    assert ("click", "#go") in common.STATE.actions, (
        "答复形状不认识 = 量不出来 ≠ 在加载 —— 不许因此不做事：%s" % common.STATE.actions)


def test_form_steps_carry_the_strict_gate_and_the_field_identity(sandbox, form_file):
    """产物填值时要带上严格闸（`--strict`）与**字段自己的身份**。

    为什么：宽松路径遇到「选择器命中多个」时静默取文档序第一个 —— 真站实测
    `input.MuiInputBase-input.MuiInputBase-inputAdornedStart` 被 zip / full_name / email
    三个字段组共用，第一条第选择器一挂，值就进了别的框（ZIP 框里躺着手机号、页面红字拒收）。
    严格闸要**认人**，认人的依据就是这一步的字段名（`FILLS[name].label`）。
    """
    states = [{"name": "w", "when": None, "steps": [
        {"action": "form", "fill": "zip", "note": "填邮编",
         "target": {"text": None, "label": "ZIP code", "role": None, "near": None,
                    "selectors": ["input.mui"], "above_fold_only": False, "frame_id": ""}}]}]
    fills = {"zip": {"name": "zip", "source": "zip", "kind": "value", "label": "ZIP code",
                     "target": states[0]["steps"][0]["target"], "fallback": [{"random": "postcode"}]}}
    src = template.render("example-strict", "Thank you", states, fills, SAMPLE_PROVENANCE)
    module, _ = _load("run_strict", src, sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()

    got = common.STATE.form_strict
    assert got, "填值必须走严格闸那条路"
    assert all(strict for _sel, strict, _lab in got), got
    assert any(lab == "ZIP code" for _sel, _s, lab in got), (
        "字段身份要跟着下去（认人靠它）：%s" % (got,))


# ─────── 跳过必须出声（2026-09-17：与「没做成不许说做成」同一条规矩）───────
#
# 「`when` 不成立就整组静默跳过」是这套产物最贵的一类失败：跑完什么都没做，
# 日志里只有一句「走完了 N 步」，读的人看不出是**判据把整组步骤吞了**。


def test_a_skipped_state_leaves_a_line_in_the_trace_with_the_reason(sandbox, form_file):
    """被跳过的步要**在 trace 里留下一行**（带 `skipped` 与「哪条判据不成立」）。"""
    states = [
        {"name": "never", "when": {"url_contains": "this-never-matches.test"},
         "steps": [{"action": "click", "note": "点「Go」",
                    "target": {"text": "Go", "role": "button", "near": None,
                               "selectors": ["#go"]}}]},
    ]
    module, _ = _load(
        "run_silent_skip",
        template.render("example-skip", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    trace = sandbox / "skip.jsonl"
    ok = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                       trace=str(trace)).run()

    assert ok is False
    lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    skipped = [l for l in lines if l.get("skipped")]
    assert skipped, "跳过必须留下痕迹：%s" % lines
    assert skipped[0]["state"] == "never", skipped[0]
    assert "this-never-matches.test" in skipped[0]["why"], skipped[0]
    assert not [a for a in common.STATE.actions if a[1] == "#go"], "跳过的步不许真做"


def test_the_end_says_out_loud_that_it_did_not_arrive(sandbox, form_file, caplog):
    """跑完没见到成功文案 → **大声说「没到」**，并报出「真做了几步 / 跳过几步」。

    「走完了 N 步」这句话本身不是结论（听着像「跑完了」）。
    """
    import logging as _logging
    states = [
        {"name": "ok", "when": None,
         "steps": [{"action": "click", "note": "点「Go」",
                    "target": {"text": "Go", "role": "button", "near": None,
                               "selectors": ["#go"]}}]},
        {"name": "never", "when": {"text_contains": ["这句话页面上没有"]},
         "steps": [{"action": "click", "note": "点「Go」",
                    "target": {"text": "Go", "role": "button", "near": None,
                               "selectors": ["#go2"]}}]},
    ]
    module, _ = _load(
        "run_loud_noarrive",
        template.render("example-noarrive", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    logger = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).log
    records = []
    handler = _logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())
    logger.addHandler(handler)
    try:
        assert module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run() is False
    finally:
        logger.removeHandler(handler)
    hit = [m for m in records if "没走到成功" in m]
    assert hit, records
    assert "真做了 1 步" in hit[0] and "跳过 1 步" in hit[0], hit[0]
    assert "没有出现过成功文案" in hit[0], hit[0]


def test_the_url_judgement_reads_the_frames_own_location(sandbox, form_file):
    """账本里的 `when.url_contains` 是**子帧自己的地址**时，要从**帧那一侧**读出来。

    2026-09-17 第九轮量出来的：主帧 DOM 里**根本没有那个问卷 `<iframe>`**
    （`getElementsByTagName('iframe')` 只拿到广告帧），而且 `src ≠ 帧自己的 location.href` ——
    所以「主帧里读 iframe 的 src」那条路**从根上够不着**，已经摘掉（`_iframe_srcs` 删了）。
    能用的只有一条：**帧自己的 `location.href`**（`cdp eval --frame-id`）。
    """
    states = [{"name": "quiz",
               "when": {"url_contains": "chameleon.example.test/forms/7878"},
               "steps": [{"action": "click", "note": "点「Continue」",
                          "target": {"text": "Continue", "role": "button", "near": None,
                                     "selectors": ["#continue"], "frame_id": "LEDGERFRAME"}}]}]
    module, _ = _load(
        "run_frame_loc",
        template.render("example-frame-loc", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    # 观测里带着一个**子帧里的元素** —— 帧表就是从这儿来的（不是主帧 DOM、不是账本）
    common = _stub(sandbox,
                   observe={"url": "https://example.test/shell",
                            "actions": [{"selector": "#q", "text": "x", "role": "button",
                                         "region": "body", "visible": True, "occluded_by": None,
                                         "above_fold": True, "stability": "high", "alternates": [],
                                         "frame_path": ["main", "LIVEFRAME1"]}],
                            "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.url = "https://example.test/shell"
    common.STATE.frame_url = "https://chameleon.example.test/forms/7878/default/gowizard"
    trace = sandbox / "frameloc.jsonl"
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                  trace=str(trace)).run()

    lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert not [l for l in lines if l.get("skipped")], (
        "帧自己的地址读得到，就不该判成「这一页不像」：%s" % lines)
    assert ("click", "#continue") in common.STATE.actions, common.STATE.actions


def test_a_crash_inside_a_step_leaves_a_failed_line_in_the_trace(sandbox, form_file):
    """步骤里抛异常 → trace 里**必须**有一行 `ok: false`（自测读的就是它）。"""
    states = [{"name": "boom", "when": None, "steps": [
        {"action": "click", "note": "点「Go」",
         "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}]}]
    module, _ = _load(
        "run_crash",
        template.render("example-crash", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]

    def _boom(*a, **kw):
        raise TypeError("form() got an unexpected keyword argument 'expect_label'")
    # ⚠️ 替身是**模块级**的（`_stub` 取的是 `sys.modules["common"]`）：打上去必须还原，
    # 否则**后面跑的每一条**都拿到这个 `_boom`。2026-09-17 实测：不还原时，
    # 追加在文件末尾的那几条测试单跑绿、进全量套件红（`click` 被换成了抛异常的函数）。
    original_click = common.CDPHelper.click
    common.CDPHelper.click = _boom          # 让这一步抛

    try:
        trace = sandbox / "crash.jsonl"
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace))
        assert f.run() is False
    finally:
        common.CDPHelper.click = original_click
    lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = [l for l in lines if l.get("ok") is False]
    assert bad, "产物死了必须留痕（不然自测会把它读成「走完了」）：%s" % lines
    assert bad[0]["step"] == 1 and "TypeError" in bad[0]["error"], bad[0]
    assert "产物自己" in bad[0]["note"], bad[0]


def test_the_artifact_survives_a_stale_common_py_but_says_so(sandbox, form_file, caplog):
    """部署的 `common.py` 不认识严格闸那两个参数时：**退回老路照填 + 大声说**。

    为什么不能让它抛：那一下会把**整趟**炸掉（自测把它读成「跑完了」）。
    为什么不能静默退回：静默降级 = 值可能落进别的框而没人知道（正是这轮要治的东西）。
    """
    import logging as _logging
    states = [{"name": "w", "when": None, "steps": [
        {"action": "form", "fill": "zip", "note": "填邮编",
         "target": {"text": None, "label": "ZIP code", "role": None, "near": None,
                    "selectors": ["input.mui"], "above_fold_only": False, "frame_id": ""}}]}]
    fills = {"zip": {"name": "zip", "source": "zip", "kind": "value", "label": "ZIP code",
                     "target": states[0]["steps"][0]["target"], "fallback": [{"random": "postcode"}]}}
    module, _ = _load(
        "run_stale_common",
        template.render("example-stale", "Thank you", states, fills, SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    # 老签名：不收 strict / expect_label
    def old_form(self, selector, value=None, check=None, select=None, frame_id=""):
        common.STATE.actions.append(("form", selector, value))
        return '{"filled": true}'
    # 同上：替身是模块级的，打完必须还原（不然后面每一条都拿到老签名）
    original_form = common.CDPHelper.form
    common.CDPHelper.form = old_form

    logger = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).log
    records = []
    handler = _logging.Handler()
    handler.emit = lambda rec: records.append(rec.getMessage())
    logger.addHandler(handler)
    try:
        module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run()
    finally:
        logger.removeHandler(handler)
        common.CDPHelper.form = original_form
    assert ("form", "input.mui", "SW1A 1AA") in common.STATE.actions or \
           [a for a in common.STATE.actions if a[0] == "form"], common.STATE.actions
    assert [m for m in records if "不认识 --strict" in m], records


# ⚠️ 「`goto` 之后等这一页加载完再判 when」这一格**没有钉子**：
# 试过两版（替身的 readyState 与正文耦合起来），都不够干净、会随调用次序翻面，
# 与其留一条会骗人的绿，不如明说没有。改法是 `agent/template.py` 的 `_wait_ready`
# （`goto` 之后、判 `when` 之前调），真站上验过（那一趟 33 步里 32 步被跳过 → 修完不再跳）。


def test_the_skip_reason_carries_what_we_actually_saw(sandbox, form_file):
    """判成「不像」时，`why` 里要**原样摆出实际看到的东西**（地址全部 + 正文一段）。

    2026-09-17 第五轮就是靠 `why` 的原文才定位到「子帧地址读不到」那一族 ——
    下一个同样的病不该再靠人翻 trace 去猜。
    """
    states = [
        {"name": "nope", "when": {"url_contains": "wanted.example.test/quiz",
                                  "text_contains": ["这句话页面上没有"]},
         "steps": [{"action": "click", "note": "点「Go」",
                    "target": {"text": "Go", "role": "button", "near": None,
                               "selectors": ["#go"]}}]},
    ]
    module, _ = _load(
        "run_why_ev",
        template.render("example-why", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    common.STATE.url = "https://now.example.test/entry"
    common.STATE.frame_url = "https://frame.example.test/widget"
    trace = sandbox / "why.jsonl"
    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0),
                  trace=str(trace)).run()

    line = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("skipped")][0]
    why = line["why"]
    assert "wanted.example.test/quiz" in why, why            # 要的是什么
    assert "now.example.test/entry" in why, why              # 手里有的（主帧）
    assert "读页面用的帧" in why, why                          # 帧表也得自证（两支都要有）


# ⚠️ 「`page_signature()` 并上最近一次 observe 的正文」这一格**没有钉子**：
# 它在单跑时绿、进全量套件时红（替身的模块级 STATE 与调用次序耦合），
# 与其留一条会骗人的绿，不如明说没有。改法在 `agent/template.py` 的 `page_signature()`
# （并上 `self._last_model["page_text"]`），真站上验过（那一趟 17/7 → 修后见报告）。


def test_live_frames_are_the_ones_in_the_latest_observation(sandbox, form_file):
    """`live_frames` 是**最近一次观测里出现的帧**（替换，不是累加）。

    2026-09-17 第七轮量出来的：累加版本里页面上的**广告帧**会一直留在表里，
    它们活着、当时那道判据（`_live_frames_ok()`，2026-09-20 改名 `_frame_table_is_usable`
    并换成「地址命中」口径）于是永远为真 → **再也不会去找真正的问卷帧** →
    读页面只剩主帧 + 广告帧，问卷正文（与成功文案）**永远读不到**。
    外部对照实验（同一窗口同一时刻读那个问卷帧）证明那一刻那段文本**读得到**。

    ⚠️ 这里量的是**表本身**（替换 vs 累加）；「凭什么算够用」那条判据在
    `test_the_frame_gate_asks_for_the_urls_the_flow_knows` 与
    `test_the_gate_wants_the_quiz_frame_not_just_any_live_frame` 上。
    """
    fill = module_filler = None
    module, _ = _load(
        "run_live_frames",
        template.render("example-live", "Thank you",
                        [{"name": "w", "when": None, "steps": [
                            {"action": "click", "note": "点「Go」",
                             "target": {"text": "Go", "role": "button", "near": None,
                                        "selectors": ["#go"]}}]}],
                        [], SAMPLE_PROVENANCE),
        sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    # 第一次观测：只有广告帧
    f._note_live_frames({"actions": [{"selector": "#ad", "frame_path": ["main", "ADFRAME1"]}]})
    assert f.live_frames == ["ADFRAME1"], f.live_frames
    # 第二次观测：问卷帧出现了（广告帧这一眼没看见）→ 表里就该只剩问卷帧
    f._note_live_frames({"actions": [{"selector": "#q", "frame_path": ["main", "QUIZFRAME"]}]})
    assert f.live_frames == ["QUIZFRAME"], (
        "活帧表要跟着最近一次观测走（累加会让广告帧把真正的问卷帧挡在外面）：%s" % f.live_frames)


# ── 帧门槛：活帧表里**有没有我要读的那一个**（线②）─────────────────────────
#
# 现场（**转述的 · 出处 `fix-gowizard-three-lines-plan.md` §2**，本文件不重复论证）：
# 门槛当年是「某个活帧还读得到」，而**广告帧永远读得到** ⇒ 「不新鲜 / 没有活帧 /
# 判据说不行」三门全假 ⇒ **永远不去找问卷帧** ⇒ 读页面只剩主帧 + 广告帧
# ⇒ 按正文判据的状态**静默跳过**（不出声，看着像「跑完了、一步没走」）。
#
# 死锁闭环（**转述的 · 同上**）：读不到问卷 → `when` 不匹配 → 整组跳过 → 跳过 = 不去找
# 元素 → 不触发 `_relocate` → 帧表永远不刷新 → 更读不到。
#
# 修法：判据改成**比地址** —— 手上这几个活帧里，有没有一个的 URL 命中「这条流程认得的
# 那几个地址」（各状态 `when.url_contains`；**子串匹配**，与 `_matches` / `_urls`
# 同一把尺子）。

#: 真站那三个帧的地址（**转述的 · 出处 plan §2 的 `:163-165`**）—— 三者**完全分得开**，
#: 而坏形状（主帧 + 广告帧，问卷帧不在里面）在日志里出现过 32 次（**转述的 · plan `:170`**）。
#: ⚠️ **不是「逐字抄的」**：三条里只有广告帧那条逐字一致（plan `:165`）；
#: 另两条把 plan 的省略号补成了具体值 —— 主帧 plan 写 `?text=Sedan&touchpointId=…&instance=1#chameleon`，
#: 这里省掉了 `touchpointId=…`；问卷帧 plan 写 `#iFrameId=mvfFormWidget-…`，这里补成 `…-1`。
#: 补是**必要**的（plan 那个 `…` 本来不是字面量），但说法得是「补全」不是「逐字」。
GW_MAIN_URL = "https://www.gowizard.com/auto/?text=Sedan&instance=1#chameleon"
GW_QUIZ_URL = ("https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard"
               "#iFrameId=mvfFormWidget-1")
GW_AD_URL = "https://id-msp.newsbreak.com/sync-nbu?source=2&host=www.gowizard.com"
#: 问卷帧那条**判据要的串**，取自产物 `STATES`（**我量的**：部署件 / `gowizard.py` /
#: `gowizard_fixed.py` / `candidate` 四份的 `want_urls` 里都有它）。
#:
#: ⚠️ **这个值不是从 `agent/template.py:_urls` 的注释里来的**：那句注释只写到 `chameleon-…`
#: （省略号），没有到这个粒度 —— 早先的版本把它标成那个出处，**指错了**（复审 F2）。
#:
#: ⚠️ 更要紧的一条：那句注释说「19 个状态里 **16 个** 的 `url_contains` 是 `chameleon-…`」，
#: 而**我数到的是 14**（【我量的】`ast.literal_eval` 取 `STATES` 逐状态数：
#: 部署件 / `forms/sites/gowizard.py` / `gowizard_fixed.py` / `candidate` 四份**一致 ——
#: 19 个状态中 1 个没有 `when`、4 个是别的地址、14 个 chameleon**；复审独立复算同值）。
#: 那个「16」**我没能证实也没能推翻**（不知道它量于哪一刻、哪一份产物），所以
#: **不去改那句注释**。复审找到了一个很可能说得通的出处：**19 个状态里恰好 16 个的
#: `steps` 带 `frame_id`**（且 14 个 chameleon 状态**全部**带），
#: ⇒「16」极可能是「**带帧号的状态数**」被写成了「url_contains 是 chameleon 的状态数」。
#: 两个数在两个不同的量上都是真的。**14 与 16 都不影响判据**（都远大于 0）。
#: ⇒ 这里用 **14**（我自己量得到、且四份产物一致的那个），并把这个可能出处写在旁边。
GW_QUIZ_WANT = "chameleon-na.www.gowizard.com/forms/7878"


def _frame_states(when_url=GW_QUIZ_WANT):
    """一条**活在帧里**的流程：账本里带 `frame_id` ⇒ `_read_frames` 那道门槛才会开。

    ⚠️ `when_url=None` 时给的是**纯正文判据** —— 「这条流程一个地址都没报过」那一格
    （`want_urls` 为空 ⇒ 回落旧口径，见 `test_a_flow_that_reports_no_url_keeps_the_old_ruler`）。
    """
    when = {"url_contains": when_url} if when_url else {"text_contains": ["Walk"]}
    return [{"name": "quiz", "when": when,
             "steps": [{"action": "click", "note": "点「Continue」",
                        "target": {"text": "Continue", "role": "button", "near": None,
                                   "selectors": ["#continue"], "frame_id": "LEDGERFRAME"}}]}]


def _frame_filler(sandbox, form_file, name, when_url=GW_QUIZ_WANT, observe=None):
    """渲染一条带帧的流程 → 起一个 Filler（替身已配好，帧表**由调用方自己摆**）。"""
    module, _ = _load(
        name,
        template.render("example-" + name, "Thank you", _frame_states(when_url),
                        [], SAMPLE_PROVENANCE),
        sandbox)
    _stub(sandbox,
          observe=observe or {"url": "https://example.test/", "actions": [], "fields": []},
          diff={"actionable": True})
    return module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))


def test_the_frame_gate_asks_for_the_urls_the_flow_knows(sandbox, form_file):
    """判据要的那串地址来自**各状态的 `when.url_contains`** —— 不是账本里的帧号。

    为什么**必须**是地址（这一格最容易走错，走错了也「看着能跑」）：帧号是探索期录的，
    重放一开始的 `goto` 一重建子帧就全成死号，而 `_frame_url` 对死号返回空串 ⇒
    按帧号收的那个集合**恒为空** ⇒ 判据退化成「每 `LIVE_PROBE_EVERY` 秒刷一次」。
    那条路**碰巧也能把帧刷出来**（刷得够勤总能撞上问卷帧），但它不是判据，
    而且代码做的事与 docstring 里写的不是一件事 —— 正是这一片反复栽的形状。
    """
    step = {"action": "click", "note": "点「Continue」",
            "target": {"text": "Continue", "role": "button", "near": None,
                       "selectors": ["#continue"], "frame_id": "LEDGERFRAME"}}
    states = [
        {"name": "landing", "when": {"url_contains": "www.gowizard.com/auto",
                                     "text_contains": ["Get Started"]}, "steps": [step]},
        {"name": "quiz", "when": {"url_contains": GW_QUIZ_WANT}, "steps": [step]},
        # 同一个地址报两遍：只要一份（去重、保序）
        {"name": "quiz-more", "when": {"url_contains": GW_QUIZ_WANT}, "steps": [step]},
        # 纯正文判据 / 没有 when：一个地址都不贡献
        {"name": "textonly", "when": {"text_contains": ["Walk"]}, "steps": [step]},
        {"name": "nwhen", "when": None, "steps": [step]},
    ]
    module, _ = _load(
        "run_want_urls",
        template.render("example-want", "Thank you", states, [], SAMPLE_PROVENANCE),
        sandbox)
    _stub(sandbox,
          observe={"url": "https://example.test/", "actions": [], "fields": []},
          diff={"actionable": True})
    f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
    assert f.want_urls == ["www.gowizard.com/auto", GW_QUIZ_WANT], f.want_urls
    # 反例（这一条才是「来源不是帧号」的钉子）：账本里那个帧号**不在**这把尺子里
    assert f.frames == ["LEDGERFRAME"], f.frames
    assert "LEDGERFRAME" not in f.want_urls


def test_the_gate_wants_the_quiz_frame_not_just_any_live_frame(sandbox, form_file):
    """实测的两种形状（**转述的 · 出处 plan §2 的真站日志**）：

    | 形状 | `live_frames` | 判据应当 |
    |---|---|---|
    | 好（成功单那一刻） | [问卷帧, 广告帧] | **True**（不刷新） |
    | 坏（卡死那 6/13 步） | [广告帧] | **False ⇒ 触发刷新** ← 修好的就是这一格 |
    """
    f = _frame_filler(sandbox, form_file, "run_gate_shapes")
    common = sys.modules["common"]
    common.STATE.frame_urls = {"QUIZFRAME": GW_QUIZ_URL, "ADFRAME": GW_AD_URL}

    f.live_frames = ["ADFRAME", "QUIZFRAME"]
    assert f._frame_table_is_usable() is True, "问卷帧在表里（广告帧排在前面也算）⇒ 不用再去找"

    f.live_frames = ["QUIZFRAME"]
    assert f._frame_table_is_usable() is True, "只有问卷帧 ⇒ 当然不用再找"

    f.live_frames = ["ADFRAME"]
    assert f._frame_table_is_usable() is False, (
        "表里只有广告帧 ⇒ 判据必须说「不行」（这一格是修好的那一格）：广告帧地址是 %s，"
        "要的是含「%s」的" % (GW_AD_URL, GW_QUIZ_WANT))


def test_a_table_of_ad_frames_really_sends_it_looking(sandbox, form_file):
    """坏形状走**整条门槛**：真的去 `observe` 一次，帧表换成当场那一份。

    上一条量的是判据本身；这一条量的是「判据说不行**之后**」—— 另两门（`_model_is_fresh`
    与「活帧表非空」）都堵死，逼这一格单独承重。
    """
    model = {"url": "https://example.test/",
             "actions": [{"selector": "#q", "text": "Continue", "role": "button",
                          "region": "body", "visible": True, "occluded_by": None,
                          "above_fold": True, "stability": "high", "alternates": [],
                          "frame_path": ["main", "QUIZFRAME"]}],
             "fields": []}
    f = _frame_filler(sandbox, form_file, "run_gate_refresh", observe=model)
    common = sys.modules["common"]
    common.STATE.frame_urls = {"QUIZFRAME": GW_QUIZ_URL, "ADFRAME": GW_AD_URL}
    f.live_frames = ["ADFRAME"]
    f._last_model = {"url": common.STATE.url}      # 手上那份观测**还是这一页的**
    assert f._model_is_fresh() is True, "另两门要先堵死，不然量不出这一格"
    assert f.live_frames, "同上"

    frames = f._read_frames()
    assert f.live_frames == ["QUIZFRAME"], (
        "坏形状必须真的去找一次，把表换成当场那一份：%s" % f.live_frames)
    assert frames == ["LEDGERFRAME", "QUIZFRAME"], frames
    assert _cdp_calls(sandbox), "「去找一次」= 起一次 cdp observe，一次都不起就没找"


def test_a_table_with_the_quiz_frame_does_not_go_looking(sandbox, form_file):
    """好形状：问卷帧已经在手上 ⇒ **一次都不去找**（这一条挡「每 3 秒刷一次」那条退化路）。"""
    f = _frame_filler(sandbox, form_file, "run_gate_quiet")
    common = sys.modules["common"]
    common.STATE.frame_urls = {"QUIZFRAME": GW_QUIZ_URL, "ADFRAME": GW_AD_URL}
    f.live_frames = ["ADFRAME", "QUIZFRAME"]
    f._last_model = {"url": common.STATE.url}
    frames = f._read_frames()
    assert _cdp_calls(sandbox) == [], "问卷帧在手上还去 observe = 白起进程"
    assert f.live_frames == ["ADFRAME", "QUIZFRAME"], "没去找就不该动这张表"
    assert frames == ["LEDGERFRAME", "ADFRAME", "QUIZFRAME"], frames


def test_the_deadlock_is_broken(sandbox, form_file):
    """**端到端的那一格**：表里只有广告帧时，判据不再把整组步骤静默跳过。

    摆法就是真站坏形状 + 手上那份观测还是这一页的（另两门堵死）——
    此时**旧代码**会判「这一页不像 quiz」→ 整组跳过（一步不走，还不出声）；
    改完会先去找一次，把问卷帧拿到手，再判 ⇒ 成立。
    """
    # 这一眼观测里**有**那个问卷帧（`observe` 喂给假 cdp 的那一份 —— 「去找一次」
    # 找的就是它，不是别的什么内存里的表）
    model = {"url": "https://example.test/",
             "actions": [{"selector": "#q", "text": "Continue", "role": "button",
                          "region": "body", "visible": True, "occluded_by": None,
                          "above_fold": True, "stability": "high", "alternates": [],
                          "frame_path": ["main", "QUIZFRAME"]}],
             "fields": []}
    f = _frame_filler(sandbox, form_file, "run_gate_deadlock", observe=model)
    common = sys.modules["common"]
    common.STATE.frame_urls = {"QUIZFRAME": GW_QUIZ_URL, "ADFRAME": GW_AD_URL}
    f.live_frames = ["ADFRAME"]
    f._last_model = {"url": common.STATE.url}      # 手上那份观测**还是这一页的**（另两门堵死）
    assert f._frame_table_is_usable() is False, "摆的正是坏形状：表里只有广告帧"
    when = {"url_contains": GW_QUIZ_WANT}
    assert f._applies(when) is True, (
        "帧表里只有广告帧时，判据要先去把问卷帧找出来再判，而不是判「不像」整组跳过")


def test_the_main_frame_cannot_prop_the_gate_up(sandbox, form_file):
    """主帧**天然不在** `live_frames` 里；而且就算将来有人把它塞进去，判据也不许恒真。

    为什么要钉子：判据比的是「地址命中 `want_urls`」，而**主帧的地址常常就命中它** ——
    这不是假想：【我量的】这条流程的真 `want_urls` 三条里有一条
    `https://www.gowizard.com/auto/` **正是主帧地址的一部分**（`agent/template.py:868-870`
    也把这条量过的话写在判据旁边）。主帧一旦混进这张表，判据会**静默恒真**，
    于是又回到「永远不去找问卷帧」那个起点。

    ⚠️ **两条不同的路会把主帧弄进表里，各钉一半**（第 3 半段是针对第 2 条路的守卫）：
    ① 有人去掉 `_note_live_frames` 的 `if fid` 过滤 ⇒ 表里出现**空帧号** ⇒ 靠
       `_frame_url("")` 那个 `if not frame_id` 直接返回空串挡住；
    ② 再进一步（有人让主帧在表里**带上一个地址** —— 改 `_frame_url` 的兜底、
       或给主帧一个帧号）⇒ 空串那一道就挡不住了 ⇒ 靠判据里 `url != main` 那道闸挡住。
    **那道闸不依赖「主帧永不进表」这个不变量** —— 这正是它的价值所在。
    """
    f = _frame_filler(sandbox, form_file, "run_gate_mainframe", when_url="example.test")
    common = sys.modules["common"]
    MAIN = "https://example.test/auto/"          # 主帧此刻的地址（**命中 want**）
    QUIZ = "https://chameleon.example.test/forms/7878"   # 真帧：跨源地址，**也命中 want**
    common.STATE.url = MAIN
    common.STATE.frame_url = MAIN                # 帧答的也跟主帧一样（最坏情况）

    # ── 第 1 半段：表的**来源**不收主帧 —— `frame_path == ["main"]` ⇒ 空帧号 ⇒ 不进表
    f._note_live_frames({"actions": [{"selector": "#hero", "frame_path": ["main"]}]})
    assert f.live_frames == [], f.live_frames

    # ── 第 2 半段：就算把主帧那个**空帧号**塞进表里，判据也不许恒真（路①）
    f.live_frames = [""]
    assert f._frame_table_is_usable() is False, (
        "主帧不许把判据撑成真（`_frame_url('')` 返回空串 —— 主帧根本不在这张表里）")

    # ── 第 3 半段：**主帧带着地址进了表**（路②，`url != main` 那道闸单独承重）
    common.STATE.frame_urls = {"MAIN": MAIN}
    f.live_frames = ["MAIN"]
    assert f._frame_table_is_usable() is False, (
        "候选帧地址与主帧当前地址**逐字相同** ⇒ 跳过（这就是那道闸；"
        "没有它，这条流程的判据在这个站上恒真）")

    # ── 第 4 半段：同一张表里**有真帧**时，判据还得能撑起来（不许矫枉过正成恒假）
    common.STATE.frame_urls = {"MAIN": MAIN, "QUIZFRAME": QUIZ}
    f.live_frames = ["MAIN", "QUIZFRAME"]
    assert f._frame_table_is_usable() is True, (
        "真帧（地址与主帧不同）在表里 ⇒ 判据必须答「够」；"
        "否则那道闸就从「挡主帧」变成了「永远说不够」= 每步都去 observe")


def test_a_flow_that_reports_no_url_keeps_the_old_ruler(sandbox, form_file):
    """`want_urls` 为空 ⇒ **回落旧口径**（任一帧答得上就放行）—— **不是**「永远刷新」。

    为什么回落：这条流程**没有任何状态报过地址**（判据全是 `text_contains` 那种）⇒
    拿什么去比都不知道。此时判 False = 每 `LIVE_PROBE_EVERY` 秒起一次 `observe()`
    （读页面每步都过这一门）—— 既白花钱，又**改掉了这条流程原来的行为**（它以前从不刷新）。
    ⇒ 判不了就不判：**这条流程的帧判不了，按旧口径放行**。

    ⚠️ 回落**不是恒真**：一个帧都答不上时照样要去找（下半段钉住它）。
    """
    f = _frame_filler(sandbox, form_file, "run_gate_no_want", when_url=None)
    common = sys.modules["common"]
    common.STATE.frame_urls = {"ADFRAME": GW_AD_URL}
    f.live_frames = ["ADFRAME"]
    f._last_model = {"url": common.STATE.url}
    assert f.want_urls == [], f.want_urls

    assert f._frame_table_is_usable() is True, (
        "判不了 ⇒ 按旧口径放行（答得上就算数）；判 False 会退化成「每步都去 observe」")
    f._read_frames()
    assert _cdp_calls(sandbox) == [], "回落之后也不许去 observe（旧行为里它本来就不去）"

    # 回落 ≠ 恒真：帧答不上（死号 —— `_frame_url` 读不到就空串）时照样要去找
    common.STATE.frame_urls = {"ADFRAME": ""}
    assert f._frame_table_is_usable() is False, "旧口径也要求「答得上」"


# ⚠️ 「trace 留尾 + 成功布尔」这一格**没有钉子**：单跑绿、进全量套件红
# （替身是模块级的、`success_in_page` 取的是**这一步之前**的页面文字，调用次序一变就翻）。
# 与其留一条会骗人的绿，不如明说没有。实现是 `agent/template.py` 的 trace 那三行
# （`page_sig_tail` / `success_in_page`，**加法**：老键一个字没动）。


# ── 同意弹层那一步：**弹层已经不在了 = 软跳过**（2026-09-17 真站实测）──────────
#
# 现场：扰动自测第 2 遍（定义就是「同一个会话里接着再跑一遍」）挂在**第 2 步**：
#   `页面上没找到「Reject All」`
# 而第 1 遍是过的。同一条产物、同一个站点，两遍表现相反 —— 差别只在**环境的残留**：
# 账本是在一个「弹层已经点掉过」的会话里录的，所以它记了一条**硬**步骤
# `click「Reject All」`；第 2 遍时弹层根本不在页面上，那一步「找不到」→ 记一次失败
# → `_judge` 要求全过 → 整遍挂、产物交不出去。
#
# 判据（两道都要）：① 这一步的名字属于「对同意做个决定」那一族；
#                   ② 此刻页面上**看不到**同意类容器。
# 弹层要真还在，② 不成立 → 照旧算失败（下面那条反例钉住它）。

def _trace_of(sandbox, form_file, name, states, fail_selectors, consent):
    """跑一遍产物，落一份 trace，返回 (最后一行的 trace 字典按步号索引, STATE)。

    ⚠️ 这里**不看 `run()` 的返回值**：产物的 `run()` 只要见到成功文案就收手，
    而这两条要问的是**那一步本身算不算做成**。trace 每步一行 `ok`，那才是这一格的判据。
    """
    module, _ = _load(name, template.render(name, "Thank you", states, [], SAMPLE_PROVENANCE), sandbox)
    common = _stub(sandbox,
                   observe={"url": "https://example.test/", "actions": [], "fields": []},
                   diff={"actionable": True})
    common.STATE.texts = ["Walk"]                      # 成功文案**不在**场上：让它走完每一步
    common.STATE.consent = consent
    common.STATE.fail_selectors = fail_selectors
    trace = str(sandbox / (name + ".trace.jsonl"))
    module.Filler(WS, form_file, "cid_1", "task_1", trace=trace,
                  delay=(0, 0)).run()
    rows = {}
    for line in open(trace, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            rows[r.get("step")] = r
    return rows, common


def test_a_consent_step_whose_banner_is_gone_is_a_soft_skip(sandbox, form_file):
    """弹层不在页面上 → 「点掉弹层」这一步**算做成了**（软跳过），不是失败。

    现场（2026-09-17 真站实测）：扰动自测第 2 遍（同一个会话里接着再跑一遍）挂在第 2 步
    `页面上没找到「Reject All」`，而第 1 遍是过的 —— 差别只在环境的残留：账本录在一个
    「弹层已经点掉过」的会话里，那一步是**硬**的；第 2 遍弹层根本不在，于是记一次失败，
    `_judge` 要求全过 → 整遍挂、产物交不出去。
    """
    rows, common = _trace_of(
        sandbox, form_file, "example-consent-gone",
        [{"name": "walk", "when": None, "steps": [
            {"action": "click", "note": "点「Reject All」",
             "target": {"text": "Reject All", "role": "button", "near": None,
                        "selectors": ["#onetrust-reject-all-handler"]}},
            {"action": "click", "note": "点「Get Started」",
             "target": {"text": "Get Started", "role": "button", "near": None,
                        "selectors": ["#go"]}}]}],
        ("#onetrust-reject-all-handler",), "")
    assert rows[1]["ok"] is True, (
        "弹层不在页面上时，「点掉弹层」这一步找不到元素**不算失败** —— "
        "它要办的事（弹层没了）已经成立了；产物开跑前本来就会自己清一次。trace: %s" % rows.get(1))
    assert "跳过" in (rows[1].get("note") or ""), (
        "软跳过要**说出来**（人话里带「跳过」）—— 不许静默把一次找不到记成做成了: %s" % rows.get(1))
    # 软跳过只作用于这一步：**后面那一步照做**
    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert "#go" in clicks, clicks


def test_a_consent_step_whose_banner_is_still_there_is_still_a_failure(sandbox, form_file):
    """反例（同一格）：弹层**还在**页面上却找不到那个按钮 → 照旧算失败。

    这是软跳过最危险的翻车方向（把真失败读成跳过）。判据的第二道就是为它设的。
    """
    rows, _ = _trace_of(
        sandbox, form_file, "example-consent-still",
        [{"name": "walk", "when": None, "steps": [
            {"action": "click", "note": "点「Reject All」",
             "target": {"text": "Reject All", "role": "button", "near": None,
                        "selectors": ["#onetrust-reject-all-handler"]}}]}],
        ("#onetrust-reject-all-handler",),
        "#onetrust-accept-btn-handler|Accept Cookies")     # 弹层还在
    assert rows[1]["ok"] is False, (
        "弹层还在页面上时，那一步找不到元素**就是失败**，不许软跳过: %s" % rows.get(1))


# ══════════════════════════════════════════════════════════════════════════════
# Task 5 —— 线①（判据只有 URL 要说出来）+ 线③（一步没成 ⇒ 重走那一组）
# ══════════════════════════════════════════════════════════════════════════════


#: 生成期钉不出正文判据时写在 `when` 上的那句话（形状照抄
#: `agent/browser_agent.py` 的 `WHEN_TEXT_DROPPED`；这里**写死一份字面量**是有意的 ——
#: 产物与生成器是两个文件，它们之间那句契约由这条用例钉着，不由 import 糊过去）。
URL_ONLY_WHY = ("这个状态的判据**只有 URL**：生成期没能钉出一段稳定的正文"
                "（这一次观测没读到页面正文（`page_text` 是空的））—— "
                "站点换一版文案，这一组就会**整组被跳过**。")


def _url_only_states():
    """一个判据**只有地址**的状态（生成期没钉出正文那半条）。"""
    return [{
        "name": "quiz",
        "when": {"url_contains": "https://example.test/quiz", "text_why": URL_ONLY_WHY},
        "steps": [_ok_click(1)],
    }]


def test_a_url_only_when_is_announced_when_the_product_starts(sandbox, form_file, caplog):
    """★ 线①：判据**只有地址**的状态，产物**开跑之前**就要说出来 —— 不许沉默。

    为什么这条是硬要求（2026-09-20 gowizard 线①）：`when.text_contains` 是生成期从
    **一次观测**取的子串。站点对同一页给两种免责声明（A/B，实测 72 趟里 3 趟）时，
    钉住 A 的那条判据在 B 那一趟整组不成立 —— 而 `auto_warranty` 是 `start` 之后的
    **第一个**状态组，它一跳过，「Reject All」「Get Free Quote」两个动作都没做 ⇒
    25 步里 24 步全跳过 ⇒ **0 次真实点击** ⇒ `no_success`。

    产物上「只有 URL 一条」与「判据本来就只有 URL」长得**一模一样**，所以这里主动说 ——
    不等它真的整组跳过了，才让读日志的人从「没走到成功」那句里往回猜。
    """
    src = template.render("example-url-only", "Thank you", _url_only_states(),
                          [], SAMPLE_PROVENANCE)
    module, _ = _load("run_url_only", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/quiz", "actions": [], "fields": []})
    common.STATE.texts = ["Thank you"]

    with caplog.at_level(logging.WARNING):
        assert module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run() is True

    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    hits = [m for m in said if "quiz" in m and "只有" in m]
    assert len(hits) == 1, ("判据只有 URL 的状态要喊一次，实际 %r" % (said,))
    assert "https://example.test/quiz" in hits[0], hits[0]
    # 判据丢掉的理由**原样转出来**（生成期说的那句话，产物不许自己另编一句）
    assert "没读到页面正文" in hits[0], hits[0]

    # 生成期**没写** `text_why` 的（手写的 / 更早生成的产物）也要说 —— 判据弱是**事实**，
    # 与「这句话有没有传下来」无关（少说一句就等于沉默，而沉默正是这一格要治的）
    caplog.clear()
    old_module, _ = _load(
        "run_url_only_old",
        template.render("example-url-only-old", "Thank you", [{
            "name": "quiz", "when": {"url_contains": "https://example.test/quiz"},
            "steps": [_ok_click(1)]}], [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common.STATE.reset()
    common.STATE.texts = ["Thank you"]
    with caplog.at_level(logging.WARNING):
        assert old_module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run() is True
    old_hits = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and "quiz" in r.getMessage()
                and "只有" in r.getMessage()]
    assert len(old_hits) == 1, caplog.text

    # 反例（同一格）：**判据齐的产物一个字都不许喊** —— 不然每个产物都在喊狼来了
    caplog.clear()
    ok_module, _ = _load(
        "run_url_only_ok",
        template.render("example-when-ok", "Thank you",
                        [{"name": "go", "when": {"url_contains": "https://example.test/",
                                                 "text_contains": ["Get Started"]},
                          "steps": [_ok_click(1)]}], [], SAMPLE_PROVENANCE),
        sandbox,
    )
    common.STATE.reset()
    common.STATE.texts = ["Thank you"]
    with caplog.at_level(logging.WARNING):
        assert ok_module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run() is True
    assert not [m for m in (r.getMessage() for r in caplog.records) if "只有" in m], caplog.text


def test_a_when_with_no_criterion_at_all_is_not_called_unstable(sandbox, form_file, caplog):
    """反例（同一格）：`when=None` 是**有意不设判据**（旁枝起点那类），不是「钉不出来」。

    这两件事必须分得开 —— 混在一起的话，「有意」的那些会被读成「生成器没钉出来」，
    而真钉不出来的那些会被这堆噪音淹掉。
    """
    src = template.render("example-no-when", "Thank you",
                          [{"name": "walk", "when": None, "steps": [_ok_click(1)]}],
                          [], SAMPLE_PROVENANCE)
    module, _ = _load("run_no_when", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/", "actions": [], "fields": []})
    common.STATE.texts = ["Thank you"]
    with caplog.at_level(logging.WARNING):
        assert module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0)).run() is True
    assert not [m for m in (r.getMessage() for r in caplog.records) if "只有" in m], caplog.text


# ── 线③：一步没做成 ⇒ **把那一组从头再走一遍**（一次为限）────────────────────

def _group(n_steps, selectors=("#a", "#b")):
    return [{"name": "quiz", "when": None, "steps": [
        {"action": "click", "note": "第 %d 步：点「%s」" % (i, sel),
         "target": {"text": sel, "role": "button", "near": None, "selectors": [sel]}}
        for i, sel in enumerate(selectors[:n_steps], start=1)
    ]}]


def test_a_failed_step_replays_its_whole_state_group(sandbox, form_file, caplog):
    """★ 线③：一步没做成 ⇒ **把它那一组从头再走一遍**（一次为限）。

    真站实测（26015658）：第 11 步「页面上没找到「330i」」⇒ 问卷没前进 ⇒ 第 12~24 步的
    `Progress: N%` 判据全废 ⇒ 最后在**空表**上点「See My Match」、一个字段都没填。
    缺口是「失败之后没有退一步重来」—— 产物是**纯线性**的，一步没成后面全部错位。

    这一条钉住的正是那个「重来」：**整组**（不是那一步）、而且**会说话**。
    """
    src = template.render("example-retry", "Thank you", _group(2), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_retry", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/q", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"]
    common.STATE.fail_plan = {"#a": 1}          # 第一步**头一次**没成，之后就好

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
        f.run()

    # ① 整组重走：`#a` 重试成了之后，**同组的 `#b` 照做**（不是只把失败那一步再来一遍）
    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert clicks == ["#a", "#b"], ("重试之后要把这一组走完：%s" % (clicks,))
    # ② 多做的那一下**就是重试那一下**（#a 一共点了两次，其中一次是重试）
    assert f.step == 3, ("第 1 步没成 → 重试（第 2 步）→ 第 3 步接着走；实际停在 %d" % f.step)
    # ③ **会说人话**：第几次、哪一组、为什么
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    hits = [m for m in said if "重试" in m and "quiz" in m]
    assert len(hits) == 1, ("重试必须进日志、说人话（不许静默重试）：%r" % (said,))
    assert "第 1 次" in hits[0] and "上限 1" in hits[0], hits[0]


def test_the_group_retry_happens_at_most_once(sandbox, form_file, caplog):
    """反例（同一格）：**重试有上限，而且上限是 1**（一趟里算总数）。

    运营那边的口径是「刷太多不太好」「能成功为啥还要继续」—— 重试的代价是**在真页面上
    多做几个真实动作**，所以这个数宁少勿多（`GROUP_RETRY_LIMIT` 的注释里写着它是按什么
    量出来的）。这一条钉死「第二次失败**不再**重试」：`#a` 连错两次，只许重走一遍。
    """
    src = template.render("example-retry-cap", "Thank you", _group(2), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_retry_cap", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/q", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"]
    common.STATE.fail_plan = {"#a": 2}          # 头一次、重试那一次**都没成**

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
        f.run()

    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert clicks == ["#b"], ("#a 两次都没成 ⇒ 不再重试，直接往下磨：%s" % (clicks,))
    assert f.step == 3, ("1 失败 + 1 重试 + 1 继续；实际 %d 步 —— 多了就是在上限之外又重试" % f.step)
    hits = [r.getMessage() for r in caplog.records if "重试" in r.getMessage()]
    assert len(hits) == 1, ("上限 1：整趟只许重试一次，实际 %d 次：%r" % (len(hits), hits))


def test_a_clean_run_never_replays_a_group(sandbox, form_file, caplog):
    """反例（同一格）：**一次都没失败 → 一次都不重试**（重试不是「多走一遍保险起见」）。"""
    src = template.render("example-no-retry", "Thank you", _group(2), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_no_retry", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/q", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"]

    with caplog.at_level(logging.WARNING):
        f = module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0))
        f.run()
    clicks = [a[1] for a in common.STATE.actions if a[0] == "click"]
    assert clicks == ["#a", "#b"], clicks
    assert f.step == 2, f.step
    assert not [r for r in caplog.records if "重试" in r.getMessage()], caplog.text


def test_the_group_retry_is_written_into_the_trace(sandbox, form_file, tmp_path):
    """重试也要**进 trace**（Console 与自测读的是 trace，不是日志）。

    判据落在**重走的那一步**上（不是失败的那一步）：它是**在真页面上多做的那个动作**，
    运营要数的正是它。没有这句话，读 trace 的人只看到「第 1 步 ok=false、第 2 步 ok=true
    （同一个 target #a）」，会把它读成「重试了一下」或者干脆读成「它自己好了」。
    """
    src = template.render("example-retry-trace", "Thank you", _group(2), [], SAMPLE_PROVENANCE)
    module, _ = _load("run_retry_trace", src, sandbox)
    common = _stub(sandbox, observe={"url": "https://example.test/q", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"]
    common.STATE.fail_plan = {"#a": 1}
    trace = tmp_path / "retry.jsonl"

    module.Filler(WS, form_file, "cid_1", "task_1", delay=(0, 0), trace=str(trace)).run()
    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3, lines
    assert lines[0]["ok"] is False and lines[1]["ok"] is True, lines
    assert lines[1]["target"] == "#a", "第 2 行是**重走**的那一下（同一个 target）: %s" % (lines[1],)
    assert "重试" in (lines[1].get("note") or ""), (
        "重走的那一步要说出来，不然 trace 里它长得跟第一步一模一样：%s" % (lines[1],))
    assert "重试" not in (lines[2].get("note") or ""), (
        "那句话只属于**触发它的那一步**，不许粘在下一步上：%s" % (lines[2],))
    # ⚠️ trace 的每一行都是「一步」，`ok` 这个键**不许**因为加了重试就不写了
    for line in lines:
        assert "ok" in line, line


def test_the_product_matches_the_success_words_without_case(rendered):
    """★ 2026-09-22：产物这一侧的「算不算成功」**一直**是不区分大小写的 —— 把它钉住。

    为什么值得单钉：探路那一侧（`graph._explore_reached_success`）原先**不是**，
    于是同一个判据给出两个答案：模型在账本里写「按人给的判据已经走通」，
    而服务判「一次都没见到」⇒ 自动重探 2 趟、每趟真提交一遍（真事：`job-f9adaede5503`）。
    两处口径分家比大小写本身贵得多，所以**两头各钉一条**（那边在 `tests/test_graph.py`）。
    判据取 `_succeeded` 的**源码段**：页那一侧（`page_signature().lower()`）与
    文案那一侧（`_norm(t).lower()`）**两边都要转** —— 少一边就是「一边小写一边原样」。
    """
    tree = ast.parse(rendered)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_succeeded")
    src = ast.get_source_segment(rendered, fn) or ""
    assert src.count(".lower()") >= 2, src


# ── 候选顺序：框架生成的 id 排最后（★ 2026-09-23 用户实测）──────────────


def _literal_of(src, name):
    """从渲染出来的源码里抠一个模块级字面量（`STATES` / `FILLS`）。"""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name
                                                for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("渲染出来的源码里没有 %s" % name)


def _render_with(states, fills):
    return template.render(SAMPLE_SITE, SAMPLE_SUCCESS, states, fills, SAMPLE_PROVENANCE)


def test_a_framework_generated_id_goes_last_in_the_declared_selectors():
    """★ 用户实测指出的那条（原话：「用的这个 id？会有问题吧，感觉这种 ID 随时会变」）。

    `#__BVID__38` 是 Vue 的**渲染序号**：同一个框重渲染就换号（真站实测
    `#__BVID__42` → `#__BVID__429`）。产物按声明顺序一条条试 ⇒ 会**先用**它 ✗
    ⇒ 声明里把它排到最后，人写的地址（`input[name="firstName"]`）先试。
    ⚠️ 它仍然**留着**（实测有一格只有它一个候选），只是不再排第一。
    """
    states = [{"name": "s", "when": None,
               "steps": [{"action": "form",
                          "target": {"selectors": ['#__BVID__38', 'input[name="firstName"]']}}]}]
    fills = [{"name": "first_name",
              "target": {"selectors": ['#__BVID__38', 'input[name="firstName"]']}}]
    src = _render_with(states, fills)
    got = _literal_of(src, "STATES")[0]["steps"][0]["target"]["selectors"]
    assert got == ['input[name="firstName"]', '#__BVID__38'], got
    assert _literal_of(src, "FILLS")["first_name"]["target"]["selectors"] == got


def test_the_order_of_human_written_selectors_is_not_touched():
    """人写的地址**一个都不许重排** —— 那个顺序是探索那一趟量的（`sorted` 是稳定的）。"""
    states = [{"name": "s", "when": None,
               "steps": [{"action": "click", "target": {"selectors": ["#nvmct", "a.nav-cta"]}}]}]
    src = _render_with(states, [])
    got = _literal_of(src, "STATES")[0]["steps"][0]["target"]["selectors"]
    assert got == ["#nvmct", "a.nav-cta"], got


def test_a_page_change_is_rechecked_for_a_bounded_while(rendered):
    """★ 2026-09-23 用户实测（原话「**等待时间短了**，我看他重新填了后成功了」）。

    每步做完只 diff **一次** ⇒ 提交之后那一屏还在**异步**换，当场判成「页面没有变化」
    （trace 第 12 步实测如此，而那一下其实成了）。产物里必须有**有界**重试：
    最多 `DIFF_POLL_SECONDS` 秒、每 `DIFF_POLL_STEP` 秒重算一次，**一变立刻停**
    （只有真没变的那一步才付这几秒 —— 不许在这里无限等：它是产物，跑在生产上）。
    """
    for const in ("DIFF_POLL_SECONDS", "DIFF_POLL_STEP"):
        assert re.search(r"^%s = " % const, rendered, re.M), "%s 不在产物的常量里" % const
    assert "while waited < float(DIFF_POLL_SECONDS)" in rendered, \
        "产物没有「页面变了没有」的有界重试 —— 异步换页会被判成「页面没有变化」"
