"""换**代理国家**（面板上按的那一下 + 自测第 5 遍那根线）—— 从 `service.py` 里搬出来的一块。

**为什么单独一个文件**（用户 2026-09-24：「那种几千行的代码」）：`service.py` 6600+ 行，
这块（话 + 形状判据 + 起脚本）埋在中间没人找得到；而且它是**这台机器的出口链**那一摊事，
与「服务怎么编排 job」本来不是一件事。找它只该有一个地方：**这儿**。

⚠️ 两条不许动的规矩（原先写在 `service.py` 那段注释里，一并搬过来）：
  ① 它动的是**这台机器上 agent 那条出口链**（宿主 `:1081`，链文件 `config/gost1081.chain`）
     —— 生产那条 `:1080` **一个字都不碰**（那是**单例热换**的，碰了会把正在跑的生产任务
     一起换掉，2026-09-14 实测过；规矩写在仓里 `skills/bit-window/SKILL.md` 那张表上，
     也写在 `gost-watch.sh` 的开头）。
  ② 干这件事的是仓库里的 `tools/set-country.py`（拉链 → 写链文件 → 等 watcher 重启 →
     **核到出口国家真变了才 exit 0**）—— 所以这儿**没有再算一遍判据**：认它的退出码。

HTTP 那两条路（`GET /country` / `POST /country`）与「记着按过哪个国家」那个状态**留在
`service.py`**（路由与 job 编排是一件事，不该搬）；这里只有：话、码的形状判据、起脚本那一下。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

COUNTRY_PATH = "/country"
#: 那个脚本（本仓 `tools/`）；`SITEFORGE_SET_COUNTRY_TOOL` 可以换掉它（测试/运维）。
SET_COUNTRY_TOOL = str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "set-country.py")
#: 换一次最多等它多久（它自己核到再返回，通常十几秒）。**构造时定死**。
SET_COUNTRY_SECONDS = 180.0

#: 换成功的答复（`%s` = 那个国家）。⚠️ 说清**换的是哪一条链** —— 运营按的是一个按钮，
#: 而这件事影响的是整台机器的 agent 出口（下一趟自测、下一趟探路都走新出口）。
COUNTRY_DONE_SAY = ("出口换成 %s 了 —— **核过**：经 :1081 问到的国家就是它。"
                    "这一下换的是**这台机器上 agent 那条链**（生产那条 :1080 一个字没动）。")
#: 没核到（`%s` = 要的国家，`%s` = 脚本自己的原话）。**不许说「换好了」**。
COUNTRY_NOT_VERIFIED_SAY = ("**没换成**：要的是 %s，链写下去了，可出口**核不出来**是它 —— "
                            "所以不能说换好了。它自己说的话：\n%s")
#: 国家码不成形状
COUNTRY_BAD_CODE_SAY = "国家码写成两个字母（比如 `US` / `CA`）—— 你给的是「%s」。"
#: 有任务在跑时不许换（`%s` = 「job id（它现在在哪一步的人话）」那一整串）
COUNTRY_BUSY_SAY = ("现在**不能换**：任务 %s 还在跑 —— 换链要把 gost 重启，"
                    "它正用着的那些连接会被掐断。等它停下来（停在闸上 / 跑完）再按，"
                    "或者先按「停」。")
#: 问不出来现在的出口是哪国（`%s` = 原话）。⚠️ 「问不出来」≠「没换」。
COUNTRY_UNREADABLE_SAY = ("问不出来这个出口现在是哪国：%s\n"
                          "（⚠️「问不出来」**不是**「没换」—— `:1081` 那个实例没在跑的"
                          "时候就是这样。）")
#: 问到了（`%s` = 国家）
COUNTRY_SHOW_SAY = "现在这个出口问到的国家是 %s（经 :1081 实测）。"


def _country_code(raw: Any) -> str:
    """国家码 → 两字母大写；**不成形状就返回空串**（由调用方去说人话，这里不编）。"""
    got = str(raw or "").strip().upper()
    return got if re.fullmatch(r"[A-Z]{2}", got) else ""


def _run_country_tool(*, tool: str, timeout: float, country: str = "", url: str = "") -> tuple:
    """换国家那条路的**默认实现**：起 `tools/set-country.py`。

    返回 `(成没成, 它自己说的话)`。⚠️ 判据**只有**它那一个：那个脚本只有在「经 :1081 问到的
    国家 == 要的那个」时才回 0 —— 所以这里不重算、不猜、也不拿「命令跑完了」当成功
    （`subprocess` 回 0 与「出口真变了」是两件事）。
    `country` 空 = 只读（问一句现在哪国，**一个字节都不写**）。
    """
    #: 与本文件那一处同一条风格：**子进程那两样在函数内导入**（`service.py` 不为它多背两个
    #: 顶层名字；`subprocess` 在 `_cdp_bin_source` 那条路上也是这么导的）。
    import subprocess
    import sys

    argv = [sys.executable, str(tool)]
    argv += ["--show"] if not country else ["--country", country, "--url", url]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "起不来：%s" % exc
    said = "\n".join(x for x in (done.stdout or "", done.stderr or "") if str(x).strip()).strip()
    return done.returncode == 0, said
