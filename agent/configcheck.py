"""一份 JSON 配置**合不合规** —— 判据从执行器**现量**，不硬编码。

## 为什么要有这一层

用户 2026-09-21 定：「修完的配置要遵守规则」。而**今天没有任何东西管这件事**：
修复那一步还没写，而且**跑得起来 ≠ 合规**。

执行器碰到**不认的动作**时是这么办的（`json_executor.py:3041`）：

    else:
        self.log.warning(f"[JSON] Unknown action: {action}")
        return False

**不报错、不崩**，只在日志里小声警告一句就当这一步失败了，然后接着往下跑。
⇒ 配置里打错一个动作名、或者模型编一个新动作出来，那份配置**照样跑得完** ——
而它在屏幕上的样子与「这一步没效果」**一模一样**。这正是这个仓最贵的那类形状，
所以这一层要把「跑完了」与「跑对了」分开：**不合规就别写回**。

## ⚠️ 判据必须**现量**，不许抄成常量

那个执行器在 `/opt/skills/auto-farm-skill/` —— **别人在改的**
（2026-09-21 一天里改过好几轮）。把动作表抄进这个文件，过几天就成了
「拿旧规矩卡新执行器」。所以：

- 判据一律**从源码文本量**（`measure_rules`，纯函数、只吃文本）；
- 量出来的那份**带着指纹**（路径 + `sha256` + 文件时间）一起交出去 ——
  调用方据此知道「这一次量的是哪一版」，执行器漂了也看得见。

人写的那份规则说明在 `docs/JSON配置-规则-2026-09-21.md`，与这一层同源。

## 两级判据（**故意的**）

| 级别 | 什么情况 | 阻不阻断 |
|---|---|---|
| `error` | 动作不在白名单 / 压根没有 `action` 那一格 / 这个动作要读的键**一个都没给** | **阻断**（别写回） |
| `note` | 这一步带的键，执行器那一支里**我没量到它读** | **不阻断** |

⚠️ `note` 那一级**不许**变成 error：动作分支里读的键是**粗量**的
（`if` / `quiz_loop` 那些子分支里的读法没进去量），而且配置里本来就可能带给别处
看的元数据。把「我没量到」判成「它就是错的」，这道闸就会把好好的配置拦下来 ——
那比不设闸更坏。
"""
from __future__ import annotations

import datetime
import hashlib
import pathlib
import re
from typing import Any, Optional

#: 执行器那一份（11822 行那一套里的主文件）。
#: ⚠️ `/opt/skills/auto-farm-skill` 是**别人在改的** —— 见模块 docstring。
EXECUTOR_PATH = "/opt/skills/auto-farm-skill/form_executor/json_executor.py"

#: 每一步都可以带的**元键**：它们不归哪个动作读，是给执行框架/别的层看的。
#: ⚠️ 只写**量到过**的：`optional`（`:374/:377/:392/:715`）与 `steps`（子步）。
STEP_META = ("action", "optional", "steps")

#: 分派那一行：`if action in ("wait", "delay"):` / `elif action == "click":`
_BRANCH = re.compile(r'^\s*(?:if|elif)\s+action\s*(?:==|in)\s*(.+?):\s*$')
#: 这一支里读的键。⚠️ `\b` 故意挡住 `sub_step.get(...)`：把子步读的键算到父动作头上，
#: 会让这张表**宽松**（少报），而宽松的代价是拦不住 —— 宁可窄。
_KEY_GET = re.compile(r'\bstep\.get\(\s*["\']([A-Za-z_]+)["\']')
_KEY_IDX = re.compile(r'\bstep\[\s*["\']([A-Za-z_]+)["\']\s*\]')


def measure_rules(src: str) -> dict:
    """从执行器**源码文本**量出动作表 → `{"actions": {动作: [它读的键]}}`。

    **纯函数**（只吃文本）—— 于是可以拿一份**改过的**源码去量，
    钉住「这张表是量的、不是抄的」（`test_the_action_table_is_measured_...`）。

    量法：主分派里每一条 `if/elif action ...:` 起一支，到**下一个不比它深的非空行**
    为止都算这一支的正文；正文里 `step.get("k")` / `step["k"]` 读到的键就是这一支认的键。

    ⚠️ **粗量**，如实说：子分支（比如 `if` 里再判一次）里的读法不一定进来；
    同一个动作在多处出现时取并集。这一层量出来的表**只用来拦「一定不认」**，
    不用来判「一定认」—— 见模块 docstring 那两级。
    """
    lines = str(src or "").split("\n")
    found: dict = {}
    i = 0
    while i < len(lines):
        m = _BRANCH.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        names = re.findall(r'["\']([a-z_]+)["\']', m.group(1))
        keys: set = set()
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip():
                if len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                keys.update(_KEY_GET.findall(nxt))
                keys.update(_KEY_IDX.findall(nxt))
            j += 1
        for name in names:
            found.setdefault(name, set()).update(keys)
        i = j
    return {"actions": {k: sorted(v) for k, v in sorted(found.items())}}


def load_rules(path: str = EXECUTOR_PATH) -> dict:
    """读真执行器 + 量 + **带上指纹**（`path` / `sha256` / `mtime`）。

    指纹是给调用方看的：拿这份规则判一份配置之前，先知道**量的是哪一版**。
    执行器漂了（那个目录别人在改）而指纹没变过 —— 那是**不可能的**，
    所以对不上就说明「该重新量了」。
    """
    p = pathlib.Path(path)
    raw = p.read_bytes()
    rules = measure_rules(raw.decode("utf-8", errors="replace"))
    rules["path"] = str(p)
    rules["sha256"] = hashlib.sha256(raw).hexdigest()
    rules["mtime"] = datetime.datetime.fromtimestamp(
        p.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return rules


def _inner(config: Any) -> Any:
    """把**信封**那一层剥掉，拿到 `{form_type, site, steps[], success}`。

    两种都收（写口的 body 是 `{"site": …, "steps": <整份>}` —— 见 `plan.md` D4），
    所以 `steps` 那一格是 `list` 就直接用、是 `dict` 就再往里看一眼。
    """
    if not isinstance(config, dict):
        return None
    steps = config.get("steps")
    if isinstance(steps, dict) and isinstance(steps.get("steps"), list):
        return steps
    return config


def check_config(config: Any, rules: dict) -> list:
    """逐格对照 ⇒ 问题清单（**人话**，每条带 `level` / `where` / `say`）。**纯函数**。

    走进去的不只是最外层：`if` / `quiz_loop` 那些 `<步>["steps"]` 里的子步**也逐格看**
    —— 只扫最外层的话，最贵的那个坏法正好漏掉（修好的主流程 + 子步里一个编出来的动作）。
    """
    inner = _inner(config)
    steps = inner.get("steps") if isinstance(inner, dict) else None
    if not isinstance(steps, list):
        return [{"level": "error", "where": "整份配置",
                 "say": "这一份里没有 `steps` 那一格（或者它不是一列）—— "
                        "执行器读的就是它（`config.get(\"steps\", [])`），读不到就等于空跑。"}]
    problems: list = []
    _walk(steps, rules, problems, prefix="", nested=False)
    return problems


def _walk(steps: list, rules: dict, problems: list, *, prefix: str, nested: bool) -> None:
    known_actions = rules.get("actions") or {}
    for i, step in enumerate(steps):
        where = prefix + (("子步 %d" if nested else "第 %d 步") % (i + 1))
        if not isinstance(step, dict):
            problems.append({"level": "error", "where": where,
                             "say": "这一步不是一个对象（读回来的是 %s）—— "
                                    "执行器那一支是拿它当字典读的。" % str(step)[:40]})
            continue
        action = step.get("action")
        if not action:
            problems.append({"level": "error", "where": where,
                             "say": "这一步**没有 `action` 那一格** —— 执行器读的就是它，"
                                    "读不到这一步就不知道该干什么。"})
        elif action not in known_actions:
            problems.append({"level": "error", "where": where,
                             "say": "执行器**不认**这个动作：%r。⚠️ 它**不报错** —— "
                                    "它只在日志里 warning 一句、就当这一步失败了，"
                                    "然后接着往下跑：屏幕上与「这一步没效果」**一模一样**。"
                                    "白名单在这里：%s。" % (str(action)[:40],
                                                          "、".join(sorted(known_actions)))})
        else:
            known = set(known_actions[action])
            given = {k for k in step if k not in STEP_META}
            if known and not (given & known):
                problems.append({"level": "error", "where": where,
                                 "say": "`%s` 这一步要读的键**一个都没给**（它读的是 %s）—— "
                                        "执行器读不到东西，这一步只会空转。"
                                        % (action, "、".join("`%s`" % k for k in sorted(known)))})
            for extra in sorted(given - known):
                problems.append({"level": "note", "where": where,
                                 "say": "这一步带着 `%s`，可 `%s` 那一支里**我没量到它读这一格** —— "
                                        "可能是别处要看的元数据，也可能是打错了字。"
                                        "（**不阻断**：没量到不等于就是错的。）"
                                        % (extra, action)})
        sub = step.get("steps")
        if isinstance(sub, list):
            _walk(sub, rules, problems, prefix=where + " › ", nested=True)
