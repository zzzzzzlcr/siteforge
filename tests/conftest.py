"""全仓测试的兜底：**不变量 —— 测试永远不碰真实世界**（真浏览器 / 仓库里的目录）。

**不变量怎么实现的（这才是它今天成立的原因，不是这个文件）**：服务把「图落哪、账落哪、
用哪个 cdp」在**构造那一刻**定死（`Service.__init__`），而构造发生在**用例里面**
（兜底生效时）。于是「抓拍/探针/自测比用例活得久」这件事**没有任何影响** ——
它不会再回头看一眼环境。

**这个文件只负责把构造期那几条输入指到安全的地方**：

- `SITEFORGE_SHOTS_DIR` → `tmp_path`（图：闸拍 + 步拍）
- `SITEFORGE_EXPLORE_DIR` → `tmp_path`（账：`baseline.json` / `attempt-*.jsonl`）
- `SITEFORGE_CDP_BIN` → 一条**不存在**的路径（于是谁要用 cdp 都在**进程内**失败，
  一个进程都不起）

⚠️ **会起子进程的三条通道封住了，靠的是「它们都走 `self._cdp_bin`」这一条**（修复轮 2 才封齐）：

| 通道 | 走哪儿 |
|---|---|
| 闸拍 / 步拍（`shots.capture_via_cli`） | `Service._shoot` 把它 `functools.partial` 绑死 |
| 窗口探针（`live_viewport`） | `Service.__init__` 起它时就绑死（它以前**自己读环境**） |
| 自测（`selftest.run`） | `Service._selftest_cb` 绑死（它以前**又读一次环境**，回退链第二候选就是仓库里那个二进制） |

还有**第四条**，性质不同 —— 它**不起子进程**，封的是「**报的 = 用的**」：

| 通道 | 走哪儿 |
|---|---|
| `/health` 报的那个 `cdp` | 报 `svc._cdp_bin`（构造时定死的那个）。以前读活环境：显式给了 `capture_bin` 却报环境里那个；两个变量都没设时报 `null` —— 而服务实际用的是仓库里那个 |

⚠️ **这张表不是自动成立的**：以后再出现一条「自己读环境、自己起子进程」的通道，
它**不在这条不变量里** —— 加它的时候要一起把它绑到 `self._cdp_bin` 上，
并照着 `tests/test_service_shots.py` 里那几条「**删干净环境之后再调用 / 或者显式给一个与环境不同的值**」
的判据写一条。那几条判据就是这条不变量的射程：
`test_a_shot_that_runs_after_…`、`test_a_viewport_probe_that_runs_after_…`、
`test_a_selftest_that_runs_after_…`、`test_health_reports_the_cdp_…`、
`test_health_never_reports_null_…`。

⚠️ 为什么是「不存在」而不是 `/bin/false` 那种**存在但没用**的东西：
`selftest._cdp_binary()` 的兜底链是「环境变量 → 本仓库的 `tools/cdp/cdp` → 现构建」，
它先做 `os.access(given, os.X_OK)`；指到一个**不可执行**的名字，那条链**照旧**能落到
仓库里那个真二进制上（`test_selftest.py` 的 e2e 全靠它）；指到 `/bin/false` 会让它们红。

⚠️ 它**只是兜底**，不替谁做决定：显式给了根/二进制的（`Service(shots_dir=...)`、
`explore(shots_dir=...)`、`capture_via_cli(cdp_bin=...)`、测试自己 `setenv`）一律赢 ——
所以要验真 `capture_via_cli` 的用例（`tests/test_shots.py`、`tests/test_service_shots.py`）
照旧走真代码。
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
    if pathlib.Path(request.path).name in SANDBOX_FILES:
        return
    monkeypatch.setenv("SITEFORGE_CDP_BIN", NO_SUCH_CDP)
