"""Task 4：**探路的账本**（journal）—— 每一步落盘，落在**发生的那一刻**。

## 为什么必须当场写（而不是跑完再写）

窗口就在这一步到下一步之间死掉（计划四 Task 1 实测过 `operTime` → `closeTime` 那一段）。
**跑完再写的话，死的正是没写下来的那一段** —— 而这一段恰恰是「窗口是怎么死的」唯一的证据。
所以 `on_step`（`browser_agent.explore` 的注入点）每步调一次，这里就**追一行**。

## 落盘的位置

    <root>/<job_id>/attempt-<n>.jsonl        # root 默认 `runtime/explore`（`runtime/` 不进 git）

**一次尝试一个文件**（一趟探路 = 一个 `attempt-<n>`）。`attempts()` 按**编号**排序 ——
字符串序会把 `attempt-10` 排到 `attempt-2` 前面，而读账的人是按顺序读「这一趟怎么走的」。

## 一次一步一行（**原子**）

`append` 用**单次 `write`** 写一个**完整行**：进程被杀最多在文件尾留下**半行**，
绝不会把两行串在一起。于是 `read()` 唯一要处理的坏形状就是**最后那半行** ——
遇到就跳过，并把**行号与原因**一起报出来（`(rows, skipped)`）：**不抛、不静默**。

## 行就是 `Journey.steps` 的那一步

**逐字同一个 dict**，不包一层、不改键名（跨任务接口 §2）。包一层的话，
「账本」与「重放」会各有一套字段名 —— 那正是漂。

⚠️ 落盘的**只能是 step dict**：不许把原始工具返回塞进来（那里面有几百 KB 的 base64 截图，
`browser_agent._summarize` 就是为这件事存在的）。这里不做检查（那是 `_summarize` 的职责，
两处都写就成了两份判据）——写在这里，是为了让下一个人知道**为什么**账本里看不见原始返回。

## journal 写失败不许把探路带塌（**旁路**）

`append` **自己吞异常**是不可能的（盘满了、权限不对，这些事必须有人知道）。
所以规矩落在**调用侧**：`browser_agent.explore` 的 `emit()` 把 `on_step` 的异常吞掉、
并往 `journey.notes` 记一句 —— 与 Console 那片对 `shooter` 的规矩同一条：
**旁路坏掉不能影响主路**，但**坏了要说出来**。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, List, Tuple

__all__ = ["DEFAULT_ROOT", "dir_for", "attempt_path", "append", "read", "attempts"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 运行产物落在哪（`runtime/` 已在 `.gitignore` 里；与 `runtime/selftest/` **同层不同目录**，
#: 互不覆盖 —— 计划里的 P7）。
DEFAULT_ROOT = _REPO / "runtime" / "explore"

#: 账本文件名的前缀与后缀（`attempt-<n>.jsonl`）——`attempts()` 按它认。
_PREFIX = "attempt-"
_SUFFIX = ".jsonl"


def dir_for(root: Any, job_id: Any) -> pathlib.Path:
    """`<root>/<job_id>/`，**顺手建出来**（`parents=True, exist_ok=True`）。

    为什么建在这里：调用方拿到路径就一定是**能写的目录** ——
    「忘了 mkdir」是这条链上最没意思的一种失败，而且它偏偏在最要紧的时刻才现形
    （窗口已经开了、第一步已经走了，才发现写不进去）。

    `job_id` 要拿来拼目录，所以**不许带路径分隔符**（`../` 那种能写到别人家去）；
    不像个 job_id 就**报错**，不猜、不 sanitize（sanitize 会让两个 job 撞进同一个目录）。

    ⚠️ **读的那两个 API 不走这里**（`read` / `attempts` 不建目录）：
    「问一声」不该有副作用 —— 否则「这个 job 从没跑过」与「跑过」在**目录存不存在**上
    就分不开了（M-1）。
    """
    path = _dir_path(root, job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dir_path(root: Any, job_id: Any) -> pathlib.Path:
    """`<root>/<job_id>/` —— **只算路径、不建目录**（读的那一侧用这个）。

    job_id 的守卫只写在这一处（`dir_for` 与 `attempts` 都走它）——
    同一份**安全判据**写两遍，收紧一处、另一处不动就会有洞（M-2）。
    """
    jid = str(job_id or "")
    if (not jid or jid in (".", "..") or "/" in jid or "\\" in jid
            or jid.startswith(".") or pathlib.PurePosixPath(jid).name != jid):
        raise ValueError("不像个 job_id：%r（它要拿来拼目录，不许带路径分隔符）" % jid)
    return pathlib.Path(root) / jid


def attempt_path(root: Any, job_id: Any, n: Any) -> pathlib.Path:
    """`<root>/<job_id>/attempt-<n>.jsonl`。编号从 **1** 起（`service._next_attempt_no` 给的）。"""
    return dir_for(root, job_id) / ("%s%d%s" % (_PREFIX, int(n), _SUFFIX))


def append(path: Any, step: dict) -> None:
    """往账本追一步。**单次 `write` 一整行**（见模块 docstring「一次一步一行」）。

    ⚠️ 异常**原样抛**（盘满 / 权限 / 路径不对）—— 由调用侧的 `emit()` 归一成
    「旁路坏了」+ 一句 note。吞在这里的话，「盘满了」这种事会永远没人知道。
    """
    target = pathlib.Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(step, ensure_ascii=False) + "\n"
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(line)          # ← 一行一次 write：被杀最多留半行，不会串行
        fh.flush()              # ← 推到操作系统那一层（窗口可能在下一步之前就死了）


def read(path: Any) -> Tuple[List[dict], List[dict]]:
    """读回一份账本 → `(rows, skipped)`。**坏行不吞**（但也不抛）。

    - `rows`：读得出来的那几行，**顺序就是落的顺序**；
    - `skipped`：跳过的行的**行号与原因**：`[{"line": 7, "why": "这一行读不出来", "raw": …}]`。
      最常见的形状是**最后半行**（进程被杀留下的）；也收「不是对象」的行。

    ⚠️ **按字节读、逐行严格解**（C-1 + 新-2）。为什么非得是这个形状：

    - `ensure_ascii=False` 让**每一行都含中文**，而写一半被杀 / 盘满短写时，
      切口落在**一个字的中间**是正常形状 —— **整份文件一次严格解码**会因为一行而全盘皆输
      （连前面那些好行一起读不出来），而那正是这个模块存在的理由：
      窗口死在一半时，得知道走到哪儿了；
    - 但**整份文件一次 `errors="replace"` 也不行**：坏字节落在 JSON **字符串内部**时，
      替换字符让整行**仍然是合法 JSON** ⇒ 它会被**当正常一步收下**、`skipped` 空 ——
      那是**静默**（与这个文件自己的原则相反），而账本里因此多一步**没人看得出是坏的**；
    - 所以：**逐行**严格解，坏的那一行落进 `skipped`（带行号、原因、能显示多少显示多少），
      **后面的行照读不误**。

    文件不在 → `([], [])`：**没跑过 = 空账**，不是异常（与 `measure.read_rows` 同一条）。
    ⚠️ 但**指到一个目录**上是会抛 `OSError`（`IsADirectoryError`）的 —— 那是调用方把路径写错了，
    不是「账本坏了」；这一条**故意不兜**（兜了就等于把「你给错路径」说成「没跑过」）。
    """
    target = pathlib.Path(path)
    rows: List[dict] = []
    skipped: List[dict] = []
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return rows, skipped
    for i, chunk in enumerate(raw.splitlines(), 1):
        if not chunk.strip():
            continue
        try:
            line = chunk.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"line": i,
                            "why": "这一行的字节不是完整的 UTF-8（多半是被杀在写一半的地方："
                                   "切口落在了一个字的中间）",
                            "raw": chunk.decode("utf-8", "replace")[:160]})
            continue
        try:
            loaded = json.loads(line)
        except ValueError:
            skipped.append({"line": i, "why": "这一行读不出来（多半是被杀在写一半的地方）",
                            "raw": line[:160]})
            continue
        if not isinstance(loaded, dict):
            skipped.append({"line": i, "why": "这一行不是一步（JSON 不是对象）",
                            "raw": line[:160]})
            continue
        rows.append(loaded)
    return rows, skipped


def attempts(root: Any, job_id: Any) -> List[pathlib.Path]:
    """这个 job 已有的账本，**按编号排序**（没有就是空表 —— 没跑过不是异常）。

    为什么不按字符串排：`attempt-10` 会排到 `attempt-2` 前面 —— 两次尝试的步会被接反，
    而读账的人是**按顺序**读「这一趟怎么走的」。认不出编号的一律排到最后（**不猜**）。

    ⚠️ 它**不建目录**（M-1）：问一声不该有副作用 —— 否则「从没跑过」与「跑过」
    在目录存不存在上就分不开了。也因此 `job_id` 不像话时会抛 `ValueError`（那是调用方写错了）。
    """
    try:
        found = list(_dir_path(root, job_id).glob("%s*%s" % (_PREFIX, _SUFFIX)))
    except OSError:
        return []           # 目录不在 / 读不动 = 没有账本（job_id 不像话那一支**照抛**）
    return sorted(found, key=_attempt_no)


def _attempt_no(path: pathlib.Path) -> int:
    """`attempt-<n>.jsonl` → `n`；认不出给一个**排在最后**的值（不猜它是第几次）。"""
    stem = path.stem
    if not stem.startswith(_PREFIX):
        return 1 << 30
    tail = stem[len(_PREFIX):]
    return int(tail) if tail.isdigit() else (1 << 30)
