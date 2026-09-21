"""修站：拿一份**现成的（坏掉的）py** 改，不重新探索。

## 为什么要有这一条（2026-09-17）

生产里 agent 最常见的用法**不是**「从零产出一份新脚本」，而是
**「这一单跑挂了 → 叫 agent 去修那一份」**。而在这条路之前，siteforge 只会
「探索 → 定稿」那一套：`plan.from_states()` 早就写好了（规格 §2.3 的「可选口子」），
**从来没有被接进图里跑过一次**。

这一条把那个口子接上，判据只有一条：**不重新探索**。
账本/产物已经有了，重跑一趟探索等于把已有的证据丢掉重买一次。

## 修什么（与「字段身份放开」同一批证据）

现成的产物里，每一格的身份是**探索那一刻**判的。它可能判错（实测：那一格判不出种类
→ 兜底填人名）。修的时候按**证据的可靠性**从高到低问，认不出就**留空并说出来**：

  ① 产物自己录下来的身份（`FILLS[k].label` / `target.hint` / `target.placeholder`）
     —— 用**当前**的识别代码重判一遍（那一套已经修过了，见 `browser_agent._field_kind`）
  ② 站方自己的 schema（**可选**）：产物里的 `label` 常常就是站方的整数组件号
     （实测：`textField-173862` ↔ 配置里的 `component_173862`），
     而配置是**公开可取**的（一个 GET，不需要窗口、不需要 CDP）。
     拿到就多一条证据；**拿不到不算失败** —— 第 ① 层够用就够用。
  ③ 都没有 → **不改**，并记一条 note 说「这一格认不出，没动它」。
     绝不猜：往「州」里填一个人名比留空更坏（留空会被校验拦住、能被看见）。

## 边界（这一版**不做**的）

- **不重新探索**、不调模型（`deps.write` 那条路照旧可以给它反馈，但这一版是确定性的）
- **不改地址**（选择器）。地址是那一趟观测的产物，修它要重新观测；
  这一版只修「这一格是什么」，地址原样带过去。
"""
from __future__ import annotations

import ast
import json
import re
import urllib.request
from typing import Any, Callable, Optional

from agent import browser_agent
from agent import plan as plan_mod

__all__ = ["from_py", "repair_states", "REQUIRED_CLI_FLAGS",
           "PATCH_SYSTEM", "shape_of", "check_patch", "patch_user", "extract_source",
           "patch_from_rounds", "apply_diff", "source_from_reply"]

#: 取站方配置的超时（秒）。**短** —— 它是可选层，拿不到就往下走，不能把整趟拖住。
CONFIG_TIMEOUT = 8.0

#: 站方配置里 `subtype` / `mappedQuestionId` 的词 → 我们这边的 `kind`。
#:
#: ⚠️ 这不是「把某个站的 schema 写死进通用模板」—— 那张表是**通用词汇**到**通用词汇**的
#: 对齐（`postcode` → `postcode`、`email1` → `email`），随配置**运行时**读进来，
#: 不随站点变。第 ② 层整体是**可选**的：拿不到配置就永远走不到这里。
_SCHEMA_WORDS = (
    (("postcode", "postal", "zip"), "postcode"),
    (("email",), "email"),
    (("telephone", "phone", "mobile"), "phone"),
    (("telephone",), "phone"),
    (("fullname", "full_name", "first_name", "last_name", "name"), "full_name"),
    (("state", "province", "region"), "state"),
    (("city", "town"), "city"),
    (("address", "street"), "address"),
)


def _literals(src: str):
    """把现成 py 里的 `STATES` / `FILLS` 两个字面量取出来（**不 exec**）。

    取不到返回 `(None, None)` —— 与 `plan.from_states` 同一个规矩：
    坏 py / 动态构造的值一律当「读不出来」，不抛、不猜。
    """
    try:
        tree = ast.parse(src or "")
    except Exception:
        return None, None
    found: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if name in ("STATES", "FILLS") and name not in found:
                try:
                    found[name] = ast.literal_eval(node.value)
                except Exception:
                    return None, None
    return found.get("STATES"), found.get("FILLS")


def site_schema(url_get: Callable[[str], str], label: str) -> Optional[dict]:
    """站方配置里，这个组件号对应的那一块（拿不到给 `None`）。

    `label` 里那个号就是配置里的 `id`（实测：`textField-173862` ↔ `component_173862`）。
    **号对得上，不用猜** —— 这是这一层为什么可靠的全部理由。
    """
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    if not digits:
        return None
    try:
        raw = url_get("https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard")
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(1))
        comps = data["props"]["pageProps"]["formSettings"]["forms"]["page_components"]
    except Exception:
        return None                      # 可选层：任何一种失败都只是「这一条没有」
    for key, block in (comps or {}).items():
        if str((block.get("component") or {}).get("id")) == digits:
            comp = block.get("component") or {}
            inp = block.get("input") or {}
            return {"subtype": comp.get("subtype") or "",
                    "mapped": comp.get("mappedQuestionId") or "",
                    "text": comp.get("text") or "",
                    "placeholder": inp.get("placeholder") or ""}
    return None


def _kind_from_schema(block) -> Optional[str]:
    """配置那一块里的 `subtype` / `mappedQuestionId` / 题目正文 → `kind`。"""
    if not block:
        return None
    for blob in (block.get("subtype"), block.get("mapped"), block.get("text")):
        text = str(blob or "").lower()
        if not text:
            continue
        for words, kind in _SCHEMA_WORDS:
            if any(w in text for w in words):
                return kind
    return None


def repair_states(states, fills, *, url_get: Optional[Callable[[str], str]] = None):
    """把现成产物里那些**判错了的字段身份**重判一遍。

    返回 `(states, fills, notes)`：`notes` 是每一条改动/没改动的人话
    （「哪一格、原来是什么、现在是什么、凭什么」）—— 修站这条路**必须留下这个**，
    不然「它到底改了什么」只能靠读两份源码对 diff。
    """
    states = [dict(s) for s in (states or [])]
    fills = {k: dict(v) for k, v in (fills or {}).items()}
    notes: list = []
    for state in states:
        for step in (state.get("steps") or []):
            if step.get("action") != "form":
                continue
            key = step.get("fill") or ""
            info = fills.get(key)
            if not info:
                continue
            target = info.get("target") or step.get("target") or {}
            label = str(info.get("label") or target.get("label") or "")
            # ① 产物自己录下来的身份 → 用**当前**的识别代码重判
            element = {"label": target.get("label") or "", "hint": target.get("hint") or "",
                       "placeholder": target.get("placeholder") or "",
                       "nearby_text": list(target.get("nearby_text") or []),
                       "type": target.get("type") or ""}
            kind = browser_agent._field_kind(label, element)
            why = "产物自己录的身份"
            # ② 站方 schema（可选）
            if kind is None and url_get is not None:
                block = site_schema(url_get, label)
                kind = _kind_from_schema(block)
                if kind:
                    why = "站方配置里 component_%s 的 subtype/mappedQuestionId/正文" % (
                        "".join(c for c in label if c.isdigit()))
            old = info.get("source")
            if kind is None:
                notes.append("「%s」认不出是什么 —— **没动它**（认不出就不填，绝不猜）" % label)
                continue
            if kind == old:
                continue
            info["source"] = kind
            info["name"] = kind
            info["fallback"] = [{"random": kind}]
            fills[key] = info
            notes.append("「%s」：%s → %s（凭：%s）" % (label, old, kind, why))
    return states, fills, notes


def from_py(src: str, *, url_get: Optional[Callable[[str], str]] = None):
    """拿一份现成的 py → `(plan, states, fills, notes)`。

    `plan` 走的是 `plan.from_states()`（规格 §2.3 那条**一直没跑过**的口子）——
    它是「这份 py 里有哪些步骤」的清单，修站这条路拿它当**底稿的骨架**。
    """
    plan = plan_mod.from_states(src)
    states, fills = _literals(src)
    if states is None or fills is None:
        return plan, None, None, ["这份 py 里的 STATES/FILLS 读不出来（坏 py 或动态构造）—— 修不了"]
    states, fills, notes = repair_states(states, fills, url_get=url_get)
    return plan, states, fills, notes


def _imports_report_url(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "common":
            if any(al.name == "report_url" for al in node.names):
                return True
    return False


def _calls_report_url(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "report_url":
                return True
    return False


def _cli_flags(tree) -> set:
    """这份 py 的 `add_argument("--x")` 表 —— **量出来的**（不是拿 "--trace" 去搜文本）。"""
    flags = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value.startswith("--"):
                            flags.add(arg.value)
    return flags


def _exit_criterion(tree) -> bool:
    """`sys.exit(0 if f.run() else 1)` 那一下还在吗 —— 退出码就是生产的成功判据。"""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "exit" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id == "sys" and any(isinstance(a, ast.IfExp) for a in node.args):
            return True
    return False


#: 生产 `ad-task.py` 就这么调产物（与 `selftest.ARTIFACT_FLAGS` 同一个口径）。
#: 补丁**不许**把这四条弄丢 —— 少一条，那个站从这一单起每一单都是一上来 argparse 报错。
REQUIRED_CLI_FLAGS = ("--ws-url", "--form-file", "--correlation-id", "--log-level")


def shape_of(src: str) -> dict:
    """这份 py 是**哪一种写法** → `{"kind": "template"|"legacy"|"unknown", "why": [...]}`。

    **判据全是量出来的记号**（`ast` 走一遍），不看文件名、不看目录、更不看类名叫什么：

      · `template` —— 里面有 `STATES` / `FILLS` 两个字面量（siteforge 自己的产物）；
      · `legacy`   —— 线上那一族：走 `common.report_url` 上报进度、`main()` 里认生产那四个
                       开关、收尾是 `sys.exit(0 if … else 1)`；
      · `unknown`  —— 都不是，`why` 里**逐条**说缺什么（人话，给运营看）。

    ⚠️ 顺序要紧：`STATES/FILLS` **先判** —— 模板形是今天那条路，逐字节不许变。
    ⚠️ 类名（`LpaFiller` 之类）**不是判据**：叫什么名字与能不能修没有关系。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"kind": "unknown",
                "why": ["`ast.parse` 都过不了（第 %s 行：%s）—— 这不是一份能跑的 py"
                        % (exc.lineno, exc.msg)]}
    states, fills = _literals(src)
    if states is not None and fills is not None:
        return {"kind": "template", "why": []}
    why = []
    if not _imports_report_url(tree):
        why.append("没有 `from common import … report_url`（进度上报那条线，自测的证据靠它）")
    elif not _calls_report_url(tree):
        why.append("import 了 `report_url` 却**一处都没调** —— 这样的脚本跑到哪儿，外面看不见")
    missing = [f for f in REQUIRED_CLI_FLAGS if f not in _cli_flags(tree)]
    if missing:
        why.append("`main()` 里缺这些开关：%s（生产就按这四个调它）" % "、".join(missing))
    if not _exit_criterion(tree):
        why.append("没有 `sys.exit(0 if … else 1)` 那个收尾 —— 退出码就是生产的成功判据，"
                   "少了它这一趟就没有判据了")
    return {"kind": "legacy" if not why else "unknown", "why": why}


def check_patch(old: str, new: str) -> list:
    """补丁过闸 → 违规清单（**人话**，每条说清「哪一条、为什么、会怎样」）。**纯函数**。

    五条闸，逐条对应一种**真会出事**的坏法：

    | 闸 | 少它会发生什么 |
    |---|---|
    | `ast.parse` 过 | 坏语法落盘 = 生产里那个站**跑 0 次** |
    | 生产那四个开关一个不少 | `ad-task.py` 一调就 argparse 报错 ⇒ 那个站**从这一单起全废** |
    | `sys.exit(0 if … else 1)` 还在 | 换成无条件 `exit(0)` 就是「跑到底再谎报成功」 |
    | 还 import 且**真调** `report_url` | 没它 ⇒ 自测那一路**没有证据**（判据是「没证据不算过」） |
    | 不是空文件 | 空补丁被当成「修好了」是这套系统里最贵的形状 |

    ⚠️ 这一层**只判结构**，不判「改得对不对」—— 那件事只有自测（真浏览器）与人说了算。
    """
    if not str(new or "").strip():
        return ["补丁是**空的** —— 没给源码。空的东西被当成「修好了」，比没修更坏。"]
    try:
        tree = ast.parse(new)
    except SyntaxError as exc:
        return ["补丁过不了 `ast.parse`（第 %s 行：%s）—— 这一版落盘，生产里那个站就是跑 0 次。"
                % (exc.lineno, exc.msg)]
    bad = []
    missing = [f for f in REQUIRED_CLI_FLAGS if f not in _cli_flags(tree)]
    if missing:
        bad.append("补丁把生产要的开关弄丢了：%s —— `ad-task.py` 就按这四个调它，少一个那一单直接报错。"
                   % "、".join(missing))
    if not _exit_criterion(tree):
        bad.append("补丁把 `sys.exit(0 if … else 1)` 那个收尾改掉了 —— 退出码是生产的成功判据，"
                   "换成无条件 `exit(0)` 就是「跑到底再谎报成功」。")
    if not _imports_report_url(tree):
        bad.append("补丁不再 `from common import … report_url` —— 进度上报那条线断了，"
                   "自测就没有证据可读（「没有证据就不算过」）。")
    elif not _calls_report_url(tree):
        bad.append("补丁 import 了 `report_url` 却一处都没调 —— 外面看不见它跑到哪儿，"
                   "自测同样没有证据。")
    return bad


#: 修站那条路给模型的**系统提示**：这是一件「改一份正在生产里跑的脚本」的活，不是从零写。
PATCH_SYSTEM = """\
你在修一份**正在生产环境里跑**的站点脚本（Python）。它由 ad-task.py 那个执行器调起，
成功与否只看**退出码**（0 = 成功）。你要交回**整份新源码**，不是 diff、不是补丁片段。

硬约束（少一条这份稿就不能用）：
1. `main()` 里这几条开关一个都不许少、一个都不许改名：
   --ws-url / --form-file / --correlation-id / --log-level
2. 收尾必须是 `sys.exit(0 if f.run() else 1)` 这个形状 —— 不许改成无条件 `sys.exit(0)`，
   也不许把成功判据放松（那等于「跑到底再谎报成功」）。
3. 必须继续 `from common import … report_url`，并且在每一步真的调它（那是外面看进度的唯一一条线）。
4. 不许加新的第三方依赖，不许改目录结构，不许把逻辑搬去别的文件。
5. 只改**该改的那一处**：其余的行尽量原样留着（改错别的地方比不修更坏）。

怎么答：交回一段 **unified diff**（`@@ -旧起点,行数 +新起点,行数 @@` 那种）——
只含**你要改的那几处**，放在一个 ```diff 围栏里，围栏外**一个字都不要写**。

⚠️ diff 里的**上下文行与 `-` 行必须与旧源码逐字一致**：这一侧拿它们对位，对不上就整份退回、
**一个字都不改**。所以别凭记忆写原文 —— 照着上面贴给你的那份抄。
⚠️ **别把整份源码交回来**：这份文件很长，整份重写会失败（实测：预算会被思考吃满）。"""


def patch_user(old_src: str, *, evidence: str = "", success_text: str = "",
               diagnosis: str = "",
               violations: Optional[list] = None, hints: Optional[list] = None) -> str:
    """给模型的**这一轮**输入：旧源码全文 + 失败证据 + 人给的成功判据 + 上一轮被打回的原因。

    ⚠️ 失败证据与人说的话**逐字**贴进去（不加工、不概括）—— 那是外部世界给的东西；
    这一层替它总结一次，模型就再也看不到原文里那些细节了。
    """
    parts = []
    if str(evidence or "").strip():
        parts.append("## 这一单为什么失败（证据，逐字）\n%s" % evidence.strip())
    if str(success_text or "").strip():
        parts.append("## 人给的成功判据（走通之后页面上会出现哪段文字）\n%s"
                     % success_text.strip())
    for hint in (hints or []):
        if str(hint or "").strip():
            parts.append("## 人插的话（逐字）\n%s" % str(hint).strip())
    if str(diagnosis or "").strip():
        parts.append("## 上一版自测没过：诊断（逐字）\n%s" % str(diagnosis).strip())
    for bad in (violations or []):
        parts.append("## 上一版被打回的原因（逐条改掉）\n%s" % bad)
    numbered = "\n".join("%5d| %s" % (i, ln) for i, ln in enumerate(old_src.splitlines(), 1))
    parts.append("## 旧源码（生产里正在跑的那一份，逐字；**左边那个数是行号**）\n```\n%s\n```"
                 % numbered)
    parts.append("照上面那些约束交一段 **diff**：`@@ -旧起点,行数 +新起点,行数 @@` 里那两个数"
                 "**就用左边那一列**（别自己数）；上下文行与 `-` 行**照着上面抄**。")
    return "\n\n".join(parts)


_CODE_FENCE = re.compile(r"```(?:python|py|diff)?\s*\n(.*?)```", re.S)

#: unified diff 的段落头：`@@ -旧起点[,行数] +新起点[,行数] @@`
_HUNK = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@", re.M)


def _looks_like_source(text: str) -> bool:
    """这段回话**像一份 py 源码**吗：`ast.parse` 过，且有 `def`/`class`/`import`。

    ⚠️ 这一条**故意比 `shape_of` 松**（2026-09-21 改）：这里的活只有一个 ——
    「这段回话是**源码**还是散文/别的东西」。而「这份源码**合不合契约**」是
    `check_patch` 的活。两层混起来，报出来的原因就不准：一份**丢了 `--log-level`**
    的源码会被说成「这不是一份 py」，读的人会去查模型交了什么东西，
    而不是去看它**到底破坏了哪一条契约**。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                              ast.Import, ast.ImportFrom))
               for n in ast.walk(tree))


def apply_diff(old: str, diff: str) -> str:
    """把一段 **unified diff** 套到旧源码上 → 新源码。**严格**：对不上就抛。

    ⚠️ 为什么要有这一层（2026-09-21 量的）：「整份源码进、整份源码出」对**大文件**不成立 ——
    `qualify.lastingpowerofattorney.io` 那份 21916 字节 / 459 行，模型把 12000 / 32000 /
    64000 三档预算**全花在思考上**（`finish_reason=length`、`content` 空），
    而同一形状对小文件是成的。所以改成「只交改动的那几段」，套用这一侧机械做。

    ⚠️ **不做模糊匹配**（fuzz / 偏移搜索）：那种「差不多就贴上」正是这一族最贵的坏法 ——
    贴错地方**不报错**，产出一份语法合法、行为不对的脚本。所以判据只有一条：
    **diff 里每一行 `-`/上下文，与旧源码那一段逐字一致**；不一致就抛，并把
    那一行两边各是什么摆出来。

    ⚠️ 行数那两格（`@@ -a,b +c,d @@`）**只当路标**（拿旧起点定位），不当判据 ——
    模型数错行数很常见，而数错起点会**当场**撞上逐字校验（比信它更早发现）。
    """
    old_lines = str(old).splitlines(keepends=True)
    out: list = []
    pos = 0                      # 旧源码里**还没搬过去**的第一行
    hunks = 0
    body = [ln for ln in str(diff or "").splitlines()]
    i = 0
    while i < len(body):
        line = body[i]
        m = _HUNK.match(line)
        if not m:
            i += 1
            continue
        start = int(m.group(1)) - 1          # 1-based → 0-based
        if start < pos or start > len(old_lines):
            raise ValueError(
                "这段 diff 的第 %d 行说它从旧源码第 %s 行开始改，可那一行不在还没搬过去的范围里"
                "（这一段之前已经搬到第 %d 行了）。" % (i + 1, m.group(1), pos + 1))
        out.extend(old_lines[pos:start])
        pos = start
        hunks += 1
        i += 1
        while i < len(body) and not _HUNK.match(body[i]):
            cur = body[i]
            if cur.startswith("\\"):         # `\ No newline at end of file` 之类，跳过
                i += 1
                continue
            tag, text = (cur[:1], cur[1:]) if cur[:1] in (" ", "-", "+") else (" ", cur)
            if tag in (" ", "-"):
                if pos >= len(old_lines):
                    raise ValueError("这段 diff 要删/改的第 %d 行，可旧源码已经到底了 —— "
                                     "对不上，一个字都没改。" % (i + 1))
                want = text + "\n"
                got = old_lines[pos]
                if got.rstrip("\n") != text:
                    raise ValueError(
                        "这段 diff 对不上：它说旧源码那一行是「%s」，可实际是「%s」"
                        "（在 diff 的第 %d 行附近）。一个字都没改。"
                        % (text, got.rstrip("\n"), i + 1))
                if tag == " ":
                    out.append(got)
                pos += 1
            else:                            # `+`：新加的行
                out.append(text + "\n")
            i += 1
    if not hunks:
        raise ValueError("这段回话里**一处 diff 都没有**（一个 `@@` 都没有）—— 那就没有改动可套。")
    out.extend(old_lines[pos:])
    return "".join(out)


def source_from_reply(old: str, reply: str) -> str:
    """模型的原话 → **这一版的新源码**：先剥围栏，再看它是 diff 还是整份。

    - 有 `@@` ⇒ 走 `apply_diff`（**首选**，也是提示词要的形状）；
    - 整份 py（`_looks_like_source`：`ast.parse` 过且有 `def`/`class`/`import`）⇒ **照收**（模型偶尔直接交源码）；
    - 都不是 ⇒ 抛，说清读出的是什么（不许把一段散文当补丁）。
      ⚠️ 「合不合契约」不在这儿判（那是 `check_patch`），不然「丢了 `--log-level`」会被
      说成「这不是一份 py」—— 两层混起来，读的人就查错方向了。
    """
    block = extract_source(reply)
    if _HUNK.search(block or ""):
        return apply_diff(old, block)
    if _looks_like_source(block):
        return block
    head = (block or "").strip().splitlines()[:1]
    raise ValueError("这段回话既不是 diff（一个 `@@` 都没有）也不是一份读得通的 py"
                     "（开头是：%s）—— 不猜它想干什么。" % (head[0][:60] if head else "（空的）"))


def violations_for_prompt(violations) -> list:
    """回灌给模型的那几条原因 → 一行一句。

    ⚠️ **两种形状都收**（2026-09-21 真跑撞出来的）：`lint` 那一侧给的是**字典**
    （`{"line": 12, "message": …}`），而**我们自己**在重试时回灌的是**人话字符串**
    （`_patch_draft` 里 `said` 那些）。只按字典读 ⇒ 重试那一次当场
    `AttributeError: 'str' object has no attribute 'get'`（而那是**第二次**才炸的形状，
    桩测里 stub 不看 feedback，一路没现形）。
    """
    out = []
    for v in (violations or []):
        if isinstance(v, dict):
            out.append("第 %s 行 —— %s" % (v.get("line"), v.get("message")))
        elif isinstance(v, str) and v.strip():
            out.append(str(v))
    return out


def patch_from_rounds(rounds, *, max_tokens=None) -> str:
    """一次模型回话 → 原话；**空回话要说清为什么**（不许只说「补丁是空的」）。

    ⚠️ 2026-09-21 **实测**（真跑一趟修站，站 `qualify.lastingpowerofattorney.io`）：
    `finish_reason="length"`、`reasoning_tokens = 12000 = max_tokens`、`content` 是**空串**
    —— 思考把预算吃满了（`agent/llm.py` 头里写着这个坑：「reasoning 模型思考 token
    也算在 max_tokens 里」）。这时说「补丁是空的」是把**我们自己的预算不够**记成
    **模型没给东西** —— 两种处置完全相反（一个加预算，一个换模型/换提示词）。

    所以这里把**那一次调用的账**（`finish_reason` / 思考 token / 完成 token / 预算）
    一起抛出来，让它跟着结论上屏。
    """
    last = list(rounds)[-1] if rounds else {}
    text = str(last.get("content") or "")
    if text.strip():
        return text
    usage = last.get("usage") or {}
    det = usage.get("completion_tokens_details") or {}
    raise ValueError(
        "模型这一轮**一个字都没给**（`finish_reason=%s`）：思考 token %s、完成 token %s%s"
        " —— 多半是预算被思考吃满（文件太大 / `max_tokens` 太小）。"
        % (last.get("finish_reason"), det.get("reasoning_tokens"),
           usage.get("completion_tokens"),
           ("，这一次的预算是 %s" % max_tokens) if max_tokens else ""))


def extract_source(reply: str) -> str:
    """模型的原话 → 源码。**先认围栏**；一处围栏都没有才退回「整段原话」。

    ⚠️ 退回那一条是**刻意的**（模型偶尔不套围栏），但退回之后照样要过 `check_patch` ——
    是源码就留下、不是就红，**不猜**。
    """
    text = str(reply or "")
    blocks = _CODE_FENCE.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return text.strip() + ("\n" if text.strip() else "")


def http_get(url: str, timeout: float = CONFIG_TIMEOUT) -> str:
    """默认的取配置方式（公开 GET，不需要窗口、不需要 CDP）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "siteforge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")
