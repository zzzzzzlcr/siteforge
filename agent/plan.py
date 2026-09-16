"""Task 2：**描述 → 计划清单**（纯函数，**零模型**）。

## 为什么解析不交给模型（设计注 §2.2）

模型解析 = 又要一轮模型调用 + **每次解析都不一样**（同样的描述两次跑出不同的计划，
那「快了多少」就永远不可比）。**编号行是确定性的** —— 33 步那张描述就是编号的。

## 三条硬规矩

1. **一个字都不改写**：`Step.text` 是运营的**原话**，`Plan.raw` 是**整份原文**。
   不改写、不摘要、不合并、不重排、不补全 —— 改写就是**替人重写意图**。
2. **序号原样保留**（`Step.n`），**不重编**：描述可能从 0 开始、可能跳号，
   重编就与人的话对不上了。
3. **非编号行一个字都不许吞**：真描述的格式是「**键值头 + 编号清单**」，
   头里夹着**约束** —— `禁止点击: Cookie Policy,Privacy Policy,Terms`、`轮次: 30`、
   `成功条件URL: /news-feed,/welcome`。这些东西**是运营写的约束，不是废话**：
   清单里没有它们，模型就会去点运营明写的禁区。
   → `Plan.raw` 就是原文（简报里给模型的是**原文 + 清单**两样）。

## 「没有计划」要判得出来

少于 `MIN_STEPS` 个编号行 → `steps == []` 且 `source == ""`。
它与「有计划、但这一趟一步没走到」**在 Task 3 里行为不同**
（自由模式 vs 计划模式），所以**不许退化成空计划**。
⚠️ `raw` 照样是原文 —— 「没计划」不等于「没原文」。

## 步数**不是**结构（§2.2）

问卷/评测这类漏斗是**分支**的：选了 A 多出三道题，选了 B 直接跳过。
同一条描述，20 / 33 / 34 步**都合法**。所以 `Plan` 里**不许**有「期望步数」这种字段 ——
任何以「期望 N 步」为前提的判据（差值、超时、上限）都是错的
（那是把规则折叠又请回来的一种）。

## 给 Task 3 的账本契约（`ledger()`）

`rounds` 是**模型每一轮的话**读出来的记录，一个 dict 一轮：

    {"mark": int | None,              # 这一轮开头报的【第 k 步】；没说就是 None
     "contradiction": str | None}     # 这一轮报的「描述说…页面上是…」；没报就是 None

`ledger()` 给每一步一个终态（`OUTCOMES` 四选一），**一步一条，顺序 = 计划顺序**：

    [{"n": int, "text": str, "state": str, "why": str}, ...]

- **`jumped_over`（绕开）≠ `contradicted`（矛盾）** —— §2.3.1 的硬规矩。
  分支站上「没走到某个检查点」**几乎每一趟都会发生**，它是站点本来的形状，
  **不进 `deviations`**（Task 3 的 `deviations` **只装 `contradicted`**）。
  两者混在一起，`deviations` 会被日常噪音灌满，「同一处反复偏离」那条信号彻底失效。
- **没被提到过的步骤是 `not_reached`** —— 「没提到」本身**不算**终态。
- **`【第 99 步】` / 没标记 → 位置不动**：号不在计划里就**什么都不动**（**不猜**）。

## 已知边界：编号行必须**占一行**

判据锚在**行首**，所以**挤在一行里的编号**（`引导: 1.滚动到底部 2.点击Featured Titles …`）
**解析不出步骤** —— `fixtures/descriptions/blinkist.txt` 就是长这样的，
它在今天这条判据下**得 0 步**（→ 自由模式）。
⚠️ 这不是实现挑的：行首那个锚正是 `轮次: 30` / `2026 年` 不被误当成步骤的**同一个机制**，
去掉它就会开始误吃散文里的数字。**报告里如实记了这条与 Task 2 简报 Step 5 的分歧**
（简报说该出 4 步，而文件里那 4 条不是编号行）。
`Plan.raw` 照样带着那一行原文 —— 约束一个字都没丢。

## `from_states()`：修站那条路的**可选口子**（§2.3）

规格 §6.1 说修站应当「以旧 py 的行走路线为起点」。今天**没有任何地方读得到旧 py**
（`RunRequest` 里没这个字段）。这个口子是**先把形状留出来**，不强制。
旧 py 里的 `STATES` 是 `[{name, when, steps: [{action, note, ...}]}]`，
清单取每步的 `note`（模板自己写着「note 是写给人看的一句话」）。

⚠️ **修站那条路可能给的是坏 py** —— 解析走 `ast`，**不 exec**，
解析不了一律 `Plan(steps=[], source="")`，**抛异常是不行的**。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional

__all__ = [
    "MIN_STEPS", "OUTCOMES", "Step", "Plan",
    "parse", "mark", "ledger", "from_states",
]

#: 少于这么多编号行就不算计划 —— 一句话目标不该被当成「清单」（§2.3「没有」那一行）。
MIN_STEPS = 2

#: `ledger()` 的终态，**四选一**（没有任何别的取值）。
OUTCOMES = ("done", "jumped_over", "contradicted", "not_reached")

#: **只认编号行**，而且锚在**行首**：`1. …` / `1、…` / `1) …` / `1．…`。
#: 行首那个锚是**要害**：`轮次: 30` 不是第 30 步，`2026 年` 不是第 2026 步。
_STEP_RE = re.compile(r"^\s*(\d+)\s*[.、)．]\s*(\S.*)$")

#: 位置标记：模型每轮开头报的 `【第 k 步】`（约定，不是新工具）。
#: 全角方括号与全角数字都收 —— 模型经常混着打。
_MARK_RE = re.compile(r"[【\[]\s*第\s*([0-9０-９]+)\s*步\s*[】\]]")

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

#: `ledger()` 里「这一趟没报到这一步」的那句话（账本是**给人看的**）。
_NOT_REACHED_WHY = "这一趟没报到这一步"


@dataclass
class Step:
    """计划里的一步。`text` 是**原话**，一个字都没改。"""

    n: int          # 序号，**原样**（可能从 0 开始、可能跳号，**不重编**）
    text: str       # 原行去掉序号后的**原样**


@dataclass
class Plan:
    """一条描述解析出来的**检查点清单** —— 不是脚本（§2.2）。"""

    raw: str = ""                       # 原文，**原样**（非编号行一个字都不许吞）
    source: str = ""                    # "goal" | "evidence" | ""（没解析出步骤）
    steps: List[Step] = field(default_factory=list)

    def actionable(self) -> bool:
        """够不够当计划走（`len(steps) >= MIN_STEPS`）。

        ⚠️ 这个判断**只跟清单长度有关**，与「这个站该走几步」**无关**（§2.2）。
        """
        return len(self.steps) >= MIN_STEPS


def parse(text: str, *, source: str = "goal") -> Plan:
    """把描述解析成计划清单。**纯函数，零模型**。

    `source` 是人给的出处（`"goal"` = 描述，`"evidence"` = 修站的失败证据）；
    **没解析出步骤时它被清成 `""`** —— 「没有计划」因此判得出来。
    """
    if not isinstance(text, str):
        text = ""
    steps: List[Step] = []
    for line in text.splitlines():
        m = _STEP_RE.match(line)
        if m is None:                   # 非编号行（约束/头/散文）→ 不进清单，但留在 raw 里
            continue
        steps.append(Step(n=int(m.group(1)), text=m.group(2)))
    if len(steps) < MIN_STEPS:
        return Plan(raw=text, source="", steps=[])
    return Plan(raw=text, source=source, steps=steps)


def mark(content: str, *, plan: Optional[Plan] = None) -> Optional[int]:
    """从模型那一轮的话里读 `【第 k 步】`；**读不出就给 `None`，不猜**（§2.4）。

    给了 `plan` 就顺带校验这个号**在不在计划里** —— 不存在同样是 `None`
    （Task 3 的「`【第 99 步】` → 位置不动」就落在这一条上）。
    不给 `plan` 时只能如实读号（号在不在要拿清单才判得了）；
    范围那一层由 `ledger()` 兜住 —— 不在计划里的号**什么都不动**。
    """
    if not isinstance(content, str):
        return None
    m = _MARK_RE.search(content)
    if m is None:
        return None
    try:
        k = int(m.group(1).translate(_FULLWIDTH_DIGITS))
    except ValueError:                  # 理论上到不了（正则已经限制了字符集）
        return None
    if k < 1:                           # 「第 0 步」不是一个位置
        return None
    if plan is not None and k not in {s.n for s in plan.steps}:
        return None
    return k


def ledger(plan: Plan, rounds: List[dict]) -> List[dict]:
    """每一步一个终态（Task 3 用）。契约见模块 docstring。

    `len(ledger) == len(plan.steps)` **恒成立** —— 「清单上每一项都有交代」。
    """
    entries = [
        {"n": s.n, "text": s.text, "state": "not_reached", "why": _NOT_REACHED_WHY}
        for s in plan.steps
    ]
    if not entries:
        return entries

    # 号 → 下标。同一个号出现两次时按**第一次**算（清单不重编，描述可能重号）。
    by_n: dict = {}
    for i, s in enumerate(plan.steps):
        by_n.setdefault(s.n, i)

    pos = -1                            # 上一个**确认过的**位置（下标）；-1 = 还没有过
    for round_no, r in enumerate(rounds or [], start=1):
        if not isinstance(r, dict):
            continue
        k = r.get("mark")
        if not isinstance(k, int) or isinstance(k, bool):
            continue                    # 没标记 / 读不出 → 位置**不动**
        idx = by_n.get(k)
        if idx is None:
            continue                    # 【第 99 步】这种 → 位置**不动**（不猜）
        statement = r.get("contradiction")

        if idx > pos:
            # 往前跳：中间那几个是被**绕开**的（分支），不是矛盾 —— 只记账（§2.3.1）。
            # ⚠️ 只有**前面报过位置**时才敢说「跳过去了」；一开始就报第 5 步，
            #    前面那几步是「没报到」，如实记 not_reached（§2.4：不猜）。
            if pos >= 0:
                for i in range(pos + 1, idx):
                    if entries[i]["state"] == "not_reached":
                        entries[i]["state"] = "jumped_over"
                        entries[i]["why"] = (
                            f"从第 {plan.steps[pos].n} 步跳到第 {k} 步，"
                            f"这一步没被报到（这一趟绕开了它）"
                        )
            _settle(entries[idx], round_no, k, statement)
        else:
            # 往回跳也照记（§2.4）：中间那几步是**走到过的**，不许被记成 jumped_over。
            # 已经记成 contradicted 的不许被这一轮降级成 done。
            if statement or entries[idx]["state"] != "contradicted":
                _settle(entries[idx], round_no, k, statement)
        pos = idx

    return entries


def _settle(entry: dict, round_no: int, k: int, statement) -> None:
    """把这一轮报的确切位置落到那一条账上。"""
    if statement:
        entry["state"] = "contradicted"
        entry["why"] = str(statement)   # 模型的话**原样**记着，改写就不是事实了
    else:
        entry["state"] = "done"
        entry["why"] = f"第 {round_no} 轮报到第 {k} 步"


def from_states(src: str) -> Plan:
    """旧 py 的 `STATES` → `Plan`（修站那条路的**可选口子**，§2.3）。

    `source` 是 `"evidence"`（这条路拿的是失败证据/旧路线）。
    解析不了（坏 py、没有 `STATES`、`STATES` 不是字面量）→ `Plan(steps=[], source="")`，
    **绝不抛异常**。
    """
    raw = src if isinstance(src, str) else ""
    try:
        tree = ast.parse(raw)
    except Exception:                   # 坏 py 什么样都可能 —— 一律当「解析不了」
        return Plan(raw=raw, source="", steps=[])

    value = None
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "STATES" for t in targets):
            continue
        try:
            # **不 exec**：`STATES = build() / open(...)` 这类一律算解析不了（修站那条路
            # 可能给的是别人写的、坏的、甚至有害的 py）。
            value = ast.literal_eval(node.value)
        except Exception:
            return Plan(raw=raw, source="", steps=[])
        found = True
        break
    if not found:
        return Plan(raw=raw, source="", steps=[])

    steps: List[Step] = []
    for state in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(state, dict):
            continue
        for step in state.get("steps") or []:
            text = _step_text(step)
            if text is None:
                continue
            # 旧 py 里**没有**步骤序号（它不是人写的清单），所以这里按顺序编号 ——
            # 这是唯一不「编」的编法：清单位置就是走路的位置。
            steps.append(Step(n=len(steps) + 1, text=text))

    if len(steps) < MIN_STEPS:
        return Plan(raw=raw, source="", steps=[])
    return Plan(raw=raw, source="evidence", steps=steps)


def _step_text(step) -> Optional[str]:
    """旧 py 里一步的「人话」：优先 `note`（模板自己写着它写给人看），退到动作名。

    没有 `note` 的步骤**不丢** —— 丢了就少一个检查点，而账本要「每一项都有交代」。
    """
    if isinstance(step, str):
        return step or None
    if not isinstance(step, dict):
        return None
    note = step.get("note")
    if isinstance(note, str) and note.strip():
        return note
    action = step.get("action")
    if isinstance(action, str) and action.strip():
        return action
    return None
