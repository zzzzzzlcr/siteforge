"""全仓测试的兜底：**不碰「共享的」真实世界**（不去连载荷里那些真地址、不往仓库里写运行产物）。

⚠️ **这句话的射程**（修复轮 3 逐条量过）—— **别读成「测试的世界是干净的」**：
「起真浏览器」这件事套件里**本来就有**，而且是**有意的**（私有端口 + 私有 profile）。
本轮**不动它**（控制器裁定：那是 `test_browser_agent.py` 有意走生产形状的结果）。

## 兜底管的是什么

**不变量**：服务要用的每一样外部东西，都在 `Service` **构造那一刻**定死 ——
而构造发生在**用例里面**（兜底生效时）⇒「抓拍/探针/自测/探路比用例活得久」**没有影响**。
兜底只负责把构造期那几条输入指到安全的地方：

- `SITEFORGE_SHOTS_DIR` → `tmp_path`（图：闸拍 + 步拍）
- `SITEFORGE_EXPLORE_DIR` → `tmp_path`（账：`baseline.json` / `attempt-*.jsonl`）
- `SITEFORGE_SELFTEST_DIR` → `tmp_path`（自测的 trace：`<root>/<site>-<时刻>/`）
- `SITEFORGE_CDP_BIN` → 一条**不存在**的路径（谁要用 cdp 都在**进程内**失败，一个进程都不起）

**会起子进程 / 会连浏览器的通道，都绑到「构造时定死的那个值」上**：

| 通道 | 走哪儿 |
|---|---|
| 闸拍（`_capture_pause` → `shots.capture_via_cli`） | `Service._shoot` 把它 `functools.partial` 绑死 |
| 窗口探针（`live_viewport`） | `Service.__init__` 起它时就绑死（它以前**自己读环境**） |
| 自测（`selftest.run`） | `Service._selftest_cb` 绑死**两样**：二进制 + `run_dir`（后者以前**调用时**解析进仓库） |
| **探路会话**（`McpSession.open` → `cdp-mcp`，唯一真连浏览器的那条） | `Service._explore_for` 传 `binary=self._mcp_bin`（构造时定死）；**去向**由载荷的 `ws_url` 定 —— 修复轮 3 把 `tools` 里的优先级改对了：**显式给的赢过 `CDP_WS_URL`** |

还有一条性质不同（它不起子进程，封的是「**报的 = 用的**」）：

| 通道 | 走哪儿 |
|---|---|
| `/health` 报的 `cdp` / `cdp_source` | 报 `svc._cdp_bin` / `svc._cdp_bin_source`（构造时那一次解析的产物）。以前读活环境：显式给了 `capture_bin` 却报环境里那个；两个变量都没设时报 `null` —— 而服务实际用的是仓库里那个 |

## 兜底**管不到**的（今天就有，且是有意的）

| 不在兜底里 | 它是什么 | 一趟全量套件的数（`sys.addaudithook` 数 `subprocess.Popen`） |
|---|---|---|
| `tests/test_browser_agent.py::live_browser` | module 级 fixture，**按设计起真 Chrome**（私有端口 + 私有 profile） | **2 台** `google-chrome --headless=new` |
| 探路那条 MCP 会话（`McpSession.open` → `cdp-mcp`） | 唯一**真连浏览器**的通道（步拍也走它）。服务那条路已绑死二进制，但**本兜底不钉 `CDP_MCP_BIN`** —— 钉了 `live_browser` 就会红（那是有意的） | **1 个** `cdp-mcp` + **1 次** `go build`（现构建） |
| `CDP_WS_URL` | 那条会话**连谁**。生产不设它；修复轮 3 把优先级改对了（显式参数赢），但它是「读活环境」这条病在真连浏览器那条通道上的最后一个旋钮 | —— |
| `tests/test_tool_loop.py` | 打真 LLM、打真站（`RUN_LLM=1` 才跑，默认不跑） | **0** |
| 产物自己的回退链（`agent/template.py`） | `SANDBOX_FILES` 已点名 | —— |
| 其余 457 条/趟的 `Popen` | 全是桩：`python` 起的桩 MCP 服务 127 条、tmp 里的沙箱假 cdp 134 条、兜底那条「**试图**起」180 条（全在进程内失败） | —— |

⇒ 真话只有一句：**不碰「共享的」真实世界** —— 不去连载荷里那些真地址、不往仓库里写运行产物。
**私有实例的真浏览器，套件本来就会起。**

⚠️ **这张表不是自动成立的**：以后再出现一条「自己读环境、自己起子进程」的通道，
它**不在这条不变量里** —— 要么一起绑到构造时定死的那个值上，要么照下面这些判据补一条。
判据的射程（**每一条都对着上面某一行**）：

- `test_a_shot_that_runs_after_the_fixtures_are_gone_cannot_touch_the_real_world`（闸拍）
- `test_a_viewport_probe_that_runs_after_…`（窗口探针）
- `test_a_selftest_that_runs_after_…` + `test_a_selftest_that_runs_after_…_writes_its_traces_in_tmp`（自测的两样）
- `test_the_explore_session_binary_is_frozen_at_construction_too`（探路会话的二进制）
- `test_an_explicit_host_and_port_win_over_the_environment`（探路会话的去向）
- `test_a_job_driven_through_the_service_leaves_its_books_in_tmp_not_in_the_repo`（**往仓库里写**那半 —— M26 钉的就是它）
- `test_health_reports_the_cdp_…` / `test_health_never_reports_null_…` / `test_health_says_which_hop_won`（报的=用的）
- `test_the_services_explore_wiring_hands_over_the_job_shots_dir` / `test_an_explore_with_a_job_id_…`（接线本身）

⚠️ 为什么是「不存在」而不是 `/bin/false` 那种**存在但没用**的东西：
`selftest._cdp_binary()` 的兜底链是「环境变量 → 本仓库的 `tools/cdp/cdp` → 现构建」，
它先做 `os.access(given, os.X_OK)`；指到一个**不可执行**的名字，那条链**照旧**能落到
仓库里那个真二进制上（`test_selftest.py` 的 e2e 全靠它）；指到 `/bin/false` 会让它们红。

⚠️ 它**只是兜底**，不替谁做决定：显式给了根/二进制的（`Service(shots_dir=…)`、
`explore(shots_dir=…)`、`capture_via_cli(cdp_bin=…)`、测试自己 `setenv`）一律赢 ——
所以要验真 `capture_via_cli` 的用例（`tests/test_shots.py`、`tests/test_service_shots.py`）
照旧走真代码。

⚠️ **例外表**（`SANDBOX_FILES`）：产物沙箱（`test_template.py`）把假 cdp 摆在自己目录里、
靠「环境干净」让产物的回退链落到它上面 —— 兜底**不碰它**。豁免表是**减法**，漏了会响。
"""

import pathlib

import pytest

#: 一条**不可能存在**的路径：既挡住「起进程」，又不挡 `_cdp_binary()` 的兜底链。
NO_SUCH_CDP = "/nonexistent/siteforge-tests-must-not-spawn-cdp"

#: ⚠️ **例外**：「产物沙箱」那一份测试（`test_template.py`）。
#: 它把**假 cdp** 摆在自己的沙箱目录里，然后靠**「环境变量是干净的」**让产物自己的
#: 回退链（`SITEFORGE_CDP_BIN` → `CDP_PATH` → 产物旁边的那个）落到它上面 ——
#: 我一设这个变量就把它按死了（实测 **11 条红**，掉进的正是兜底那一跳）。
#: 它是**唯一**一个「靠环境干净」的文件：`test_shots.py` 一律显式传 `cdp_bin=`、
#: `test_selftest.py` 的 `_cdp_binary()` 先做 `os.access(X_OK)` 再回退、
#: `test_runtime.py` 先 `delenv` 再断言默认值 —— 那三个都不受影响（我逐条核过）。
SANDBOX_FILES = {"test_template.py"}


@pytest.fixture(autouse=True)
def _tests_never_touch_the_repo_or_a_real_cdp(request, tmp_path, monkeypatch):
    monkeypatch.setenv("SITEFORGE_SHOTS_DIR", str(tmp_path / "runtime" / "shots"))
    #: 账（`baseline.json` / `attempt-*.jsonl`）落在哪 —— **第三条**通往仓库的路。
    #: 修复轮 1 实测漏掉的就是它：`runtime/explore/<job_id>/` 一趟全量套件 22 个目录，
    #: 全是我那份测试文件建的（`test_service.py` 有自己的 `_runtime_goes_to_tmp`）。
    monkeypatch.setenv("SITEFORGE_EXPLORE_DIR", str(tmp_path / "runtime" / "explore"))
    #: 自测每一遍的 trace（`<root>/<site>-<时刻>/`）—— **第四个**落点。
    #: 它以前**既没有环境变量、也没被冻结**（`_selftest_kwargs` 不给 `run_dir`），
    #: 于是「调用那一刻」解析进仓库里（修复轮 3 的 F2，复审实测真被建出来）。
    monkeypatch.setenv("SITEFORGE_SELFTEST_DIR", str(tmp_path / "runtime" / "selftest"))
    if pathlib.Path(request.path).name in SANDBOX_FILES:
        return
    monkeypatch.setenv("SITEFORGE_CDP_BIN", NO_SUCH_CDP)
