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
| `tests/test_browser_agent.py::live_browser` | module 级 fixture，**按设计起真 Chrome**（私有端口 + 私有 profile `siteforge-r5-profile-*`） | **1 台** `google-chrome` |
| **`tests/test_selftest.py::live_site`** | **另一个** module 级 fixture，**同样起真 Chrome**（profile `siteforge-live-profile-*`），跑的是「真 Chrome + 真 cdp + 真产物」的 e2e。<br>⚠️ 「仓库里那个真 cdp」**有前提**：那条链（`test_selftest.py::_cdp_binary()`）是「环境变量（要可执行）→ **`ROOT/tools/cdp/cdp`（要存在）** → **现构建到 /tmp**」—— **净检出没有那个文件**（`.gitignore` 里躺着），那一刻它跑的是**现构建**的那个 | **1 台** `google-chrome`（本表量具）<br>+ 真 cdp 的 `execve`：**58 次**（⚠️ **不在本表量具的射程里** —— 见下） |
| 探路那条 MCP 会话（`McpSession.open` → `cdp-mcp`） | **服务运行期**唯一**真连浏览器**的通道（步拍也走它）——⚠️「唯一」只在「服务运行期」这个限定下成立：同一张表上面那两台 `live_*` 也在真连浏览器，其中 `live_browser` 走的就是同一条 `cdp-mcp`。服务那条路已绑死二进制；兜底**钉的是常量 `tools.MCP_BIN`**（没人点名时不可能 exec PATH 上那个） | **1 个** `cdp-mcp` + **1 次** `go build`（现编译） |
| `CDP_WS_URL` | 那条会话**连谁**。生产不设它；修复轮 3 把优先级改对了（显式参数赢），但它是「读活环境」这条病在真连浏览器那条通道上的最后一个旋钮 | —— |
| `tests/test_tool_loop.py` | 打真 LLM、打真站（`RUN_LLM=1` 才跑，默认不跑） | **0** |
| 产物自己的回退链（`agent/template.py`） | `SANDBOX_FILES` 已点名 | —— |

⚠️ 上面那两格写完**不许**再写成「其余 Popen 全是桩」——**那是假的**：
那 2 台真 Chrome 里有一台（`live_site`）是**真 cdp 驱动真产物**在跑（58 次 exec）。
剩下的确实是桩：桩 MCP 服务 ~136 条、tmp 里的沙箱假 cdp 134 条、
兜底那条「**试图**起」~183 条（全在进程内失败）、
`frozen-mcp-wrapper` 1 条（修复轮 5 路线 (b) 的桩，它自己 `exec` 桩 MCP 服务）——
个位数随用例数变。

⚠️ **这张表的量具也有射程**（它数的是 Python 的 `subprocess.Popen`）：

1. 看不见 `go build` 拉起的工具链（compile/link/asm/cgo/gcc ≈9 个），
   也看不见 Chrome 启动脚本拉起的 `cp/readlink/dirname/mkdir`（≈35 条）；
2. **更看不见「子进程自己再起的进程」** —— 上面 `live_site` 那 58 次 `execve`
   就是这一类：它是**产物自己**（pytest 的**孙进程**）起的，本表的钩子在 pytest 进程里
   **数到 0**；复审换 `strace -f` **单跑 `tests/test_selftest.py`** 才复现出那 58 次
   （该文件总 `execve` 566 次）。⇒ 那一格的量纲是**混的**（前半 `Popen` / 后半 `execve`），
   读的时候别当成同一支量具。

别把「这张表」读成「这套件起了几个进程」；要那个数得换 `strace`（内核层 `execve`）。

⇒ 真话只有一句：**不碰「共享的」真实世界** —— 不去连载荷里那些真地址、不往仓库里写运行产物。
**私有实例的真浏览器，套件本来就会起。**

⚠️ **这张表不是自动成立的**：以后再出现一条「自己读环境、自己起子进程」的通道，
它**不在这条不变量里** —— 要么一起绑到构造时定死的那个值上，要么照下面这些判据补一条。
判据的射程（**每一条都对着上面某一行**）：

- `test_a_shot_that_runs_after_the_fixtures_are_gone_cannot_touch_the_real_world`（闸拍）
- `test_a_viewport_probe_that_runs_after_…`（窗口探针）
- `test_a_selftest_that_runs_after_…` + `test_a_selftest_that_runs_after_…_writes_its_traces_in_tmp`（自测的两样）
- `test_the_explore_session_binary_is_frozen_at_construction_too`（探路会话的二进制 ——
  ⚠️ **它的射程只到「服务那一跳」**：它把 `browser_agent.explore` 整个换成了桩，
  所以它**替不了证**「`explore` 到 `cdp-mcp` 那一段」；那一段由下面两条钉：
  `test_the_real_explore_hop_carries_the_frozen_mcp_binary`（真 `explore`，断 argv[0] **与**去向）
  与 `test_the_frozen_binary_is_really_exec_d_with_the_destination_and_the_frame`
  （越过 **exec 边界**：真 Popen → 子进程 → 图回来））
- `test_the_services_explore_path_really_shoots_step_images`（默认 shooter 是**真的** ——
  ⚠️ 只到「盘上有那张图」；「那张图**是**桩回的那张」由 `…really_exec_d…` 那条钉）
- `test_the_payload_destination_really_reaches_the_real_mcp_binary`（去向被**下游**真用成连接目标）
- `test_an_explicit_host_and_port_win_over_the_environment`（探路会话的去向）
- `test_a_job_driven_through_the_service_leaves_its_books_in_tmp_not_in_the_repo`（**往仓库里写**那半 —— M26 钉的就是它）
- `test_health_reports_the_cdp_…` / `test_health_never_reports_null_…` / `test_health_says_which_hop_won`（报的=用的）
- `test_the_services_explore_wiring_hands_over_the_job_shots_dir` / `test_an_explore_with_a_job_id_…`（接线本身）

⚠️ 为什么是「不存在」而不是 `/bin/false` 那种**存在但没用**的东西：
`tests/test_selftest.py::_cdp_binary()`（**注意：它不在 `agent/selftest.py`** —— 那边那个叫 `_default_cdp_bin`，链是「环境 → 仓库 `tools/cdp/cdp` → `/usr/local/bin/cdp`」，**没有「现构建」那一跳**）的兜底链是「环境变量 → 本仓库的 `tools/cdp/cdp` → 现构建」，
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
    #: **没人点名时**那条 MCP 会话用哪个可执行文件：`tools.MCP_BIN` 是**导入期**读的常量
    #: （`CDP_MCP_BIN`），所以「钉环境变量」对它无效（复审实测：钉常量是**免费**的 ——
    #: 钉上后全量 623 绿 0 红，因为两条 live 线都自己造会话、不看这个常量）。
    #: 于是「测试里会不会 exec PATH 上那个 `cdp-mcp`」从「看 PATH」变成**不可能**。
    from agent import tools as _tools
    monkeypatch.setattr(_tools, "MCP_BIN", NO_SUCH_CDP)
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
