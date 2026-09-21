#!/usr/bin/env python3
"""Common utilities for form fill subprocess scripts.

════════ 这份文件的出身（siteforge 的运行时真源，2026-09-16 抄入）════════

  抄自        /opt/skills/auto-farm-skill/forms/common.py
  那个仓库    /opt/skills/auto-farm-skill @ 6c54785e299d486a191bd7d6222555f48f5906cf
              （2026-06-09 16:31:09 +0800，最后一次改到这份文件）
  源文件 md5  2a245ad4960c472b5ff97059e4ded5f8
  抄入日期    2026-09-16
  抄入时改了  三处，**全是加法式** —— 逐条见下面 RUNTIME_PROVENANCE["changes"]

为什么这里才是真源：用户 2026-09-16 定 —— siteforge **不再引用** auto-farm 的那份，
那 57 个生产站点脚本将来共用**这一份**（生产仓库里那份从此是「部署产物」，
不再是需要维护的源）。产物的 `from common import CDPHelper, setup_logger, report_url`
一行没变（Task 3 的测试逐字钉着它），变的只是「从哪解析」：产物在
`<root>/forms/sites/` 时，它那行 `sys.path.insert(0, dirname(dirname(abspath(__file__))))`
解析到的就是这份。

⚠️ 部署（不在本仓库的范围内）：铺产物时**必须把这份同版本带过去**，否则生产跑的
还是老那份 —— 这里改的东西在生产等于没发生。

看分歧（不靠记忆）：`python3 -m agent.runtime diff`
"""

import json
import logging
import os
import subprocess
import base64
import time
from typing import Dict, Optional
import threading

#: 这份运行时从哪来、抄入时改过哪几处 —— **机器可读**（`python3 -m agent.runtime show`）。
#: 为什么要写下来：memory 里的 `two-json-executors` 就是两份真源并存、而「哪份是真的」
#: 只活在人脑子里，改错地方等于白改。这份数据是「两边漂没漂」唯一不靠记忆的判据。
RUNTIME_PROVENANCE = {
    "copied_from": "/opt/skills/auto-farm-skill/forms/common.py",
    "source_commit": "6c54785e299d486a191bd7d6222555f48f5906cf",
    "source_commit_date": "2026-06-09 16:31:09 +0800",
    "source_md5": "2a245ad4960c472b5ff97059e4ded5f8",
    "copied_at": "2026-09-16",
    "owner": "siteforge（真源；生产仓库那份降级为部署产物）",
    "changes": (
        "文件头加了这段出身说明与 RUNTIME_PROVENANCE",
        "CDP_PATH 改环境变量优先（默认值一个字没改：/opt/skills/auto-farm-skill/cdp）",
        "CDPHelper.screenshot() 失败时可归因（新增 last_screenshot_error；成功路径返回值不变）",
        "report_url() 认两个**环境**旋钮（SITEFORGE_TRACE / SITEFORGE_NO_REPORT）——"
        "B 线 ③ 乙：老写法那一族（66 份）的 main() 里没有 --trace/--no-report，"
        "自测那条线只能从运行时这一处进；两个都没设时**行为逐字节不变**",
    ),
}

# CDP binary path
#
# ⚠️ 默认值与生产那份**逐字相同**（/opt/skills/auto-farm-skill/cdp）：部署后 57 个既有
# 站点脚本共用这份，改默认值 = 悄悄改生产行为（R-16）。要指到别处（容器里的新版 cdp、
# siteforge 构建的那个）就设环境变量：
#   SITEFORGE_CDP_BIN  siteforge 这一侧的旋钮 —— 产物自己的回退链（observe/diff/goto）
#                      也认这个名字，一个旋钮指一件事，不会「一半走这个一半走那个」
#   CDP_PATH           容器里既有的那个名字（Dockerfile:85 设 /usr/local/bin/cdp）
# 两个都没设 = 生产默认。两个都设了 = 更专的那个（SITEFORGE_CDP_BIN）优先。
CDP_PATH = (os.environ.get("SITEFORGE_CDP_BIN")
            or os.environ.get("CDP_PATH")
            or "/opt/skills/auto-farm-skill/cdp")

# API endpoints
SCREENSHOT_API_URL = os.environ.get("SCREENSHOT_API_URL", "https://fmr.3tkj.cn/api/quest/screenshot")
FORM_API_URL = os.environ.get("FORM_API_URL", "https://fmr.3tkj.cn/api/quest/formMessage")

# Timeouts (in seconds)
CDP_TIMEOUT = 30
API_TIMEOUT = 15

# Logger setup lock for thread safety
_logger_setup_lock = threading.Lock()

# Configure logging at module level
logging.basicConfig(level=logging.DEBUG)


class CDPHelper:
    """Helper class for Chrome DevTools Protocol operations."""

    def __init__(self, ws_url: str):
        """
        Initialize CDP helper.

        Args:
            ws_url: WebSocket URL from bit.sh (e.g., "ws://192.168.1.222:9222/...")
        """
        self.host, self.port = self._parse_ws_url(ws_url)
        #: 最近一次 screenshot() 失败的原因（人话）；成功是 None。
        #: 加法式新增：老代码把「命令不存在」与「这一页是白的」压成同一个空字符串（R-16）。
        self.last_screenshot_error = None

    def _parse_ws_url(self, ws_url: str) -> tuple:
        """Parse WebSocket URL to extract host and port."""
        # Extract host and port from ws://host:port/path
        if not ws_url:
            return "127.0.0.1", "9222"

        # Remove ws:// prefix
        url = ws_url.replace("ws://", "").replace("wss://", "")

        # Split by / to get host:port part
        host_port = url.split("/")[0]

        if ":" in host_port:
            host, port = host_port.split(":")
            return host, port
        else:
            return host_port, "9222"

    def snapshot(self) -> Dict:
        """
        Get page accessibility snapshot.

        Returns:
            Dict with snapshot data including frames, trees, etc.
        """
        for attempt in range(3):
            try:
                result = subprocess.run(
                    [CDP_PATH, "snapshot",
                     "--host", self.host,
                     "--port", self.port],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    return json.loads(result.stdout)
                else:
                    error_msg = result.stderr or result.stdout
                    if attempt < 2:
                        time.sleep(2)
                        continue
                    raise Exception(f"CDP snapshot failed: {error_msg}")

            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise Exception(f"CDP snapshot failed after 3 attempts: {e}")

    def click(self, selector: str, frame_id: str = "") -> str:
        """
        Click element on page.

        Args:
            selector: CSS selector for element
            frame_id: Optional frame ID for iframe elements

        Returns:
            Command output
        """
        cmd = [CDP_PATH, "click", "--selector", selector]
        if frame_id:
            cmd.extend(["--frame-id", frame_id])
        cmd.extend(["--host", self.host, "--port", self.port])

        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout + result.stderr

    def eval(self, script: str, frame_id: str = "") -> str:
        """
        Execute JavaScript in page.

        Args:
            script: JavaScript code to execute
            frame_id: Optional frame ID for iframe execution

        Returns:
            Execution result
        """
        cmd = [CDP_PATH, "eval", script]
        if frame_id:
            cmd.extend(["--frame-id", frame_id])
        cmd.extend(["--host", self.host, "--port", self.port])

        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout + result.stderr

        # Check for browser closed error
        if "failed to create client" in output or "no page target" in output or "BugError" in output:
            return "ERROR: Browser or page was closed"

        return output

    def form(self, selector: str, value: str = None, check: str = None,
             select: str = None, frame_id: str = "", strict: bool = False,
             expect_label: str = "") -> str:
        """
        Fill form field with human-like behavior.

        Args:
            selector: CSS selector for the element
            value: Text value to input (for text fields)
            check: Checkbox state "true"/"false"
            select: Dropdown option value
            frame_id: Optional frame ID for iframe elements
            strict: 选择器命中多个元素时**拒绝静默挑第一个**（走 cdp form --strict）。
                    默认 False = 老行为（57 个生产脚本一字不变）
            expect_label: 这个字段自己的身份（页面上写着的那句名字）—— strict 认它来消歧

        Returns:
            Execution result

        ⚠️ 为什么要 strict（2026-09-17 真站实测）：宽松路径遇到「选择器命中多个」时
        **静默取文档序第一个** —— 一个 class 选择器被 zip / full_name / email 三个字段组共用时，
        第一条第选择器一挂，值就进了另一个框（ZIP 框里躺着手机号、页面红字拒收）。
        `click` 那道门早就有严格版了，`form` 一直缺 —— 这个参数就是那个缺口。
        """
        cmd = [CDP_PATH, "form", selector]
        if value is not None:
            cmd.extend(["--value", value])
        if check is not None:
            cmd.extend(["--check", check])
        if select is not None:
            cmd.extend(["--select", select])
        if frame_id:
            cmd.extend(["--frame-id", frame_id])
        if strict:
            cmd.append("--strict")
            if expect_label:
                cmd.extend(["--expect-label", expect_label])
        cmd.extend(["--host", self.host, "--port", self.port])

        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout + result.stderr

        # Check for browser closed error
        if "failed to create client" in output or "no page target" in output or "BugError" in output:
            return "ERROR: Browser or page was closed"

        return output

    def navigate(self, url: str) -> str:
        """
        Navigate to URL.

        Args:
            url: Target URL

        Returns:
            Navigation result
        """
        script = f"window.location.href = '{url}'"
        result = subprocess.run(
            [CDP_PATH, "eval", script,
             "--host", self.host, "--port", self.port],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout + result.stderr

    def scroll(self, pixels: str = "300") -> str:
        """
        Scroll the page.

        Args:
            pixels: Number of pixels to scroll (default 300)

        Returns:
            Scroll result
        """
        result = subprocess.run(
            [CDP_PATH, "scroll", pixels,
             "--host", self.host, "--port", self.port],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.stdout + result.stderr

    def screenshot(self) -> str:
        """
        Capture screenshot from current page.

        Returns:
            Base64-encoded screenshot data
        """
        self.last_screenshot_error = None
        result = subprocess.run(
            [CDP_PATH, "screenshot",
             "--host", self.host, "--port", self.port],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            # 命令没成时**说得出为什么**（R-16）：原先 stderr 与退出码被一起丢掉，
            # 于是「这个 cdp 根本没有 screenshot 这条命令」与「这一页是白的」返回值
            # 一模一样 —— 本项目最贵的失败就是「判不出」被读成「没问题」。
            # ⚠️ 加法式：**返回值仍然是 result.stdout.strip()**（下面那行与生产那份
            # 逐字相同），新增的只有这条属性 + 一条警告日志。
            why = (result.stderr or result.stdout or "").strip().splitlines()
            self.last_screenshot_error = (
                "截图命令没成（退出码 %d）：%s。用的 cdp 是 %s —— 它可能根本没有 "
                "screenshot 这条命令，指到有这条命令的那版（新版 cdp）再看。"
                % (result.returncode, why[-1].strip() if why else "它什么都没说", CDP_PATH))
            logging.getLogger("common").warning(self.last_screenshot_error)
        return result.stdout.strip()

    def get_page_info(self) -> Dict:
        """
        Get current page URL and title.

        Returns:
            Dict with 'url' and 'title' keys
        """
        url_result = self.eval("window.location.href")
        title_result = self.eval("document.title")

        # Clean up quotes from result
        url = url_result.strip().strip('"').strip("'")
        title = title_result.strip().strip('"').strip("'")

        return {"url": url, "title": title}


def report_screenshot(task_id: str, step: str, screenshot_b64: str, url: str = "",
                      api_url: str = "https://fmr.3tkj.cn/api/quest/screenshot") -> bool:
    """
    Report screenshot to API.

    Args:
        task_id: Task ID for tracking
        step: Step identifier (can be empty string)
        screenshot_b64: Base64-encoded screenshot data
        url: Current page URL (for tracking)
        api_url: API endpoint URL

    Returns:
        True if reported successfully, False otherwise
    """
    # Input validation
    if not task_id or not isinstance(task_id, str):
        return False

    if step is None or not isinstance(step, str):
        step = ""

    # Simplified validation - just check screenshot_b64 exists and not too large
    if not screenshot_b64 or not isinstance(screenshot_b64, str):
        return False

    if len(screenshot_b64) > 15 * 1024 * 1024:  # 15MB limit for base64 string
        return False

    # Prepare request
    data = {
        "task_id": task_id,
        "step": step,
        "base64": screenshot_b64,
        "url": url
    }

    for attempt in range(2):
        try:
            import urllib.request

            req = urllib.request.Request(
                api_url,
                data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                result = response.read().decode('utf-8')
                return True

        except Exception as e:
            if attempt < 1:
                time.sleep(1)
                continue
            # Log error but don't fail hard
            return False

    return False


#: 自测那条线的两个**环境**旋钮（哪个产物都认，见 `report_url` 里那段注释）。
TRACE_ENV = "SITEFORGE_TRACE"
NO_REPORT_ENV = "SITEFORGE_NO_REPORT"


def _trace_step(step: str, url: str, *, note: str = "") -> None:
    """把这一步追一行到 `SITEFORGE_TRACE` 指的文件（没设就什么都不做）。

    形状与 siteforge 模板那份 trace **同一套**（`selftest._read_trace` 读的就是它）：
    一行一个 JSON 对象。`ok` 是 **`null`** —— 老写法只在「上报这一步」留痕，
    它没做成也会报一次，所以**这一步成没成这一层判不出来**；判据是退出码。
    把它写死成 `true` 就是替外部世界下结论（本项目最贵的那类谎）。

    ⚠️ 写不进去只当没写成（`OSError` 吞掉）：trace 是自测要的东西，
    不该把正在跑的任务搞挂。
    """
    path = os.environ.get(TRACE_ENV)
    if not path:
        return
    line = {"step": step, "ok": None,
            "ok_why": "老写法的 report_url 只说明「这一步上报过」，成没成要退出码说了算",
            "url": url, "note": note}
    try:
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def report_url(cdp_helper: CDPHelper, task_id: str, step: str,
               log: logging.Logger = None, base64_content: str = "") -> bool:
    """
    Report current page URL to API (with optional base64 content).

    This function reports the URL, step, and optional base64 content.

    Args:
        cdp_helper: CDPHelper instance for browser communication
        task_id: Task ID for tracking
        step: Step identifier (e.g., "form_button_clicked", "form_field_filled")
        log: Optional logger for debug output
        base64_content: Optional base64 content (e.g., success reason)

    Returns:
        True if URL reported successfully, False otherwise
    """
    try:
        # Get current page URL
        page_info = cdp_helper.get_page_info()
        current_url = page_info.get("url", "")

        if log:
            log.info(f"[URL Report] step={step}, URL: {current_url[:100] if current_url else 'NO URL'}")
            if base64_content:
                log.info(f"[URL Report] base64_content: {base64_content[:100]}")
        # ── 自测那条线（B 线 ③ 乙）：两个**环境**旋钮，argv 一个字节都不改 ──────────
        #
        # 老写法那一族（线上 66 份 py）的 `main()` 里**没有** `--trace` / `--no-report`，
        # 硬传就是 argparse 报错（那会把自测变成「每一遍都红」）。而它们的进度**全部**
        # 走这一个函数 —— 所以旋钮放在这儿：自测用环境把这条线打开，**产物一个字不用改**。
        #
        #   SITEFORGE_TRACE=<path>   每调一次追一行 JSON（形状与模板那份 trace 同一套）
        #   SITEFORGE_NO_REPORT=1    这一下**不往生产的记录接口写**（自测不是生产任务）
        #
        # ⚠️ 两个都没设 = 生产那条路，逐字节不变（下面的代码原样执行）。
        _trace_step(step, current_url, note=base64_content)
        if os.environ.get(NO_REPORT_ENV):
            if log:
                log.info("[URL Report] SITEFORGE_NO_REPORT 设了 —— 这一次不往生产发（自测那条路）")
            return True

        # Prepare request
        data = {
            "task_id": task_id,
            "step": step,
            "base64": base64_content,
            "url": current_url
        }

        # 认模块顶上那个 SCREENSHOT_API_URL（默认值仍是生产那个 URL —— 一个字没改）。
        # 这里原先写死了一个字面量：于是那个环境变量**只在模块顶上被读、在真正发请求的
        # 这条路上被忽略** —— 一个假装存在的旋钮。设了它什么也不会变，而「以为什么都变了」
        # 比没有旋钮更坏：自测/实验想把它指到别处都指不动（R-2x）。
        api_url = SCREENSHOT_API_URL

        for attempt in range(2):
            try:
                import urllib.request

                if log:
                    log.info(f"[URL Report] Sending data: {json.dumps(data)}")

                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(data).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )

                with urllib.request.urlopen(req, timeout=15) as response:
                    result = response.read().decode('utf-8')
                    if log:
                        log.info(f"[URL Report] API response: {result[:100]}")
                    return True

            except Exception as e:
                if log:
                    log.warning(f"[URL Report] Attempt {attempt + 1} failed: {e}")
                if attempt < 1:
                    time.sleep(1)
                    continue
                return False

        return False

    except Exception as e:
        if log:
            log.warning(f"[URL Report] Failed: {e}")
        return False


def setup_logger(name: str) -> logging.Logger:
    """
    Set up thread-safe logger for form fill scripts.

    Args:
        name: Logger name (e.g., 'car-insurance', 'senior-survey')

    Returns:
        Configured logger instance
    """
    # Sanitize name to prevent directory traversal
    import re
    sanitized = re.sub(r'[^\w\-.]', '_', name)[:50]

    logger = logging.getLogger(f'form_fill_{sanitized}')

    # Thread-safe logger setup
    with _logger_setup_lock:
        if logger.handlers:
            return logger

        logger.setLevel(logging.INFO)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # File handler (with PID suffix to avoid permission conflicts)
        log_dir = "/opt/skills/auto-farm-skill/logs"
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f'{sanitized}.log')
        try:
            file_handler = logging.FileHandler(log_path)
        except (PermissionError, OSError):
            # File owned by another user - use PID suffix
            log_path = os.path.join(log_dir, f'{sanitized}_{os.getpid()}.log')
            file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.DEBUG)

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger


# CDP tool usage guide - shared across all form fill scripts
CDP_USAGE_GUIDE = """
**首先检查是否有CTA按钮**：很多页面不是直接显示表单，而是先有一个大按钮（如 "Get Quotes"、"Apply Now"、"Request Access"、"Get a Free Quote"、"Compare Now"、"START QUOTES" 等）。如果页面没有表单但有这类按钮，先点击按钮进入表单页。

## CDP工具正确用法：
### cdp_form - 填写表单字段（首选）
  - 文本输入: cdp_form(selector="#firstName", value="John")
  - 下拉选择: cdp_form(selector="select.day", select="15")
    **select参数用于SELECT下拉，value参数用于INPUT文本，别搞混！**
  - 复选框: cdp_form(selector="#agree", check="true")
### cdp_click - 点击按钮/链接
  - 普通: cdp_click(selector="button.submit")
  - iframe内: cdp_click(selector="button", frame_id=frameId)
### cdp_eval - 执行JS（仅用于验证字段值或关闭弹窗）
  - 验证: cdp_eval(script="document.querySelector('#fn').value", frame_id=frameId)
  - 关弹窗: cdp_eval(script="document.querySelector('.close').click()")
### iframe表单操作流程：
  1. cdp_snapshot() 获取页面JSON
  2. 看JSON末尾的 childFrames 数组，找到目标iframe的 frameId 和 name
     例: {"frameId":"6F7A...","name":"swift-registration-...","url":"..."}
  3. 操作iframe内元素时，所有cdp_xxx都要传 frame_id=那个frameId
  4. 先cdp_snapshot()看iframe里的字段ID，再用cdp_form填
### 重要提醒：
  - cdp_form返回 "filled: selector = value" 就是成功，不要重复填同一字段
  - 下拉选择器必须用select参数，不能用value参数
  - iframe内的所有操作都必须传frame_id
"""
