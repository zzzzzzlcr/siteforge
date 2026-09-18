"""全仓测试的兜底：**测试既不许往仓库里写，也不许起真的 `cdp`**（Task 3，2026-09-18）。

为什么要这条 autouse 的兜底（而不是各测试文件自己传根）：服务侧的**闸拍**会在每一轮
落一张 `runtime/shots/<job_id>/pause-<n>.png`，而它的默认根**就是仓库里**那个
`runtime/shots`（生产就该是那儿 —— `SITEFORGE_SHOTS_DIR` 只是运维的覆盖口）。
于是任何一条「照生产那样拼一个 app、不传 `shots_dir`」的测试都会：

1. 在**源码树**里建目录 —— 实测：一次 `test_service.py` 在 `runtime/shots/` 建 **46 个**；
2. 更要命的是它**起一个真的 `cdp` 子进程**，去连桩载荷里那个地址 ——
   `test_service.py` 用的正是 `ws://127.0.0.1:9222/...` 与 `ws://192.168.1.197:55555/...`
   （宿主那个调试端口 / 一台 worker）。测试不该去敲任何一个。

所以两样一起按住：

- `SITEFORGE_SHOTS_DIR` → `tmp_path`（图落进测试自己的地盘）；
- `SITEFORGE_CDP_BIN` → 一条**不存在**的路径（于是 `capture_via_cli` 在**进程内**
  就失败并说清「起不来 cdp…设 SITEFORGE_CDP_BIN 指到那个二进制」，一个进程都不起）。

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
