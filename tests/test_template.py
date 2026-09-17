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
    """缺命令 → **人话说一次**（每条一次）+ trace 里的 null 带上为什么，而不是静默。"""
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
        assert len(hits) == 1, (command, said)        # 两条步、每条要 observe 两次 —— 只喊一次
    assert any("SITEFORGE_CDP_BIN" in m for m in said), ("要告诉人怎么换", said)
    assert not [m for m in said if "#" in m], ("给人看的话里不许出现选择器", said)

    lines = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2, lines
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
        #: 这些**帧号**上的命令一律报错 —— 真 cdp 对一个不存在的 frameID 就是这个行为
        #: （`OOPIF eval: attach failed: No target with given id found`）。
        #: 「帧号漂了」这条路上的钉子靠它（同一个选择器在死帧里挂、在活帧里成）。
        self.fail_frames = ()
        # 生产那个 cdp 没有 screenshot 命令，而真 CDPHelper.screenshot() 只交 stdout、
        # 把 stderr 丢了（common.py:244 `return result.stdout.strip()`）—— 于是它返回空串。
        self.fail_screenshot = False
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
        if "elementFromPoint" in script:     # 遮挡判据那个探针
            return json.dumps(STATE.cover)
        if "document.readyState" in script:              # `goto` 之后等这一页加载
            return json.dumps("complete")
        if "innerText" in script:
            return json.dumps(STATE.current_text())
        if "location.href" in script:
            # 帧里那一份（`frame_url`）与主帧那一份（`url`）可以不一样 ——
            # 真站上它们本来就不一样（子帧是部件、主帧是壳）
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
    它们活着 → `_live_frames_ok()` 永远为真 → **再也不会去找真正的问卷帧** →
    读页面只剩主帧 + 广告帧，问卷正文（与成功文案）**永远读不到**。
    外部对照实验（同一窗口同一时刻读那个问卷帧）证明那一刻那段文本**读得到**。
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
