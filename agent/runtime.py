"""运行时的门房：siteforge 那份 py 运行时（`forms/common.py`）从哪来、漂流了没有。

## 为什么需要它（R-15/R-16）

用户 2026-09-16 定：siteforge **不再引用** `/opt/skills/auto-farm-skill/forms/common.py`，
改为自己拥有一份 `forms/common.py`（真源；生产那份从此是**部署产物**）。
可「自己有一份」如果既没出身记录、又不能按需比对，就会重演 memory 里的
`two-json-executors`：生产 `form_executor/` 与实验 `src/lanuage/` 两份执行器并存，
**改错地方等于白改** —— 而「哪份是真的」只活在人脑子里。

所以这里只做两件事：

    python3 -m agent.runtime show             这份运行时的出身（抄自哪份、哪天、md5、改过哪几处）
    python3 -m agent.runtime diff [--other <路径>]
                                              跟另一份逐行比，**并列出已知的加法式改动** ——
                                              好让人分得清「我们改的」与「它漂的」

退出码（好进脚本/CI）：0 没有分歧 · 1 有分歧 · 2 比不了（对方不在）或用法不对。
**「比不了」永远不算「没有分歧」** —— 那是本项目最忌讳的那种话。
"""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import pathlib
import sys

__all__ = ["RUNTIME_PATH", "PRODUCTION_PATH", "load", "provenance", "known_changes",
           "compare", "show", "main", "USAGE"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: siteforge 自己那份运行时（真源）。路径**必须**是 `forms/common.py`：
#: 产物落在 `<root>/forms/sites/<site>.py`，它那行路径算术
#: （`sys.path.insert(0, dirname(dirname(abspath(__file__))))`）解析到的就是这一层。
RUNTIME_PATH = _REPO / "forms" / "common.py"

#: 比对时默认的另一边：生产仓库那份。与 `common.py` 头里的
#: `RUNTIME_PROVENANCE["copied_from"]` 是同一个值（`tests/test_runtime.py` 钉着两者相等）。
PRODUCTION_PATH = "/opt/skills/auto-farm-skill/forms/common.py"

#: 加载运行时那份时用的模块名 —— **不是** `common`：那个名字属于真实产品的解析结果，
#: 谁先占了 `sys.modules["common"]`，谁就会把别人的 import 接到自己身上。
_LOAD_AS = "siteforge_forms_common"

USAGE = """siteforge 的 py 运行时（forms/common.py）的门房：

  python3 -m agent.runtime show                 它从哪来：抄自哪份、哪天、md5、改过哪几处
  python3 -m agent.runtime diff [--other <路径>]
                                                跟另一份逐行比（默认跟生产那份比）
                                                退出码：0 没分歧 / 1 有分歧 / 2 比不了

为什么要有它：这份运行时是**真源**，生产仓库那份降级成了部署产物。两边一旦并存，
「哪份是真的」就不许靠记性（memory: two-json-executors —— 改错地方等于白改）。"""


def load(path=None):
    """把运行时那份当模块加载回来（**不注册进 `sys.modules`**，不占 `common` 这个名字）。

    注意：加载会执行它模块级的代码（含 `logging.basicConfig`）—— 与产品里的
    `from common import ...` 是同一件事，所以这不是副作用，是它本来的行为。
    """
    path = pathlib.Path(path or RUNTIME_PATH)
    spec = importlib.util.spec_from_file_location(_LOAD_AS, path)
    if spec is None or spec.loader is None:
        raise ValueError("这不是一个能加载的 py 文件：%s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def provenance(path=None) -> dict:
    """出身：抄自哪份、哪个 commit、哪天、源文件 md5、抄入时改了哪几处。"""
    return dict(load(path).RUNTIME_PROVENANCE)


def known_changes(path=None) -> tuple:
    """抄入时**故意**做的那几处加法式改动 —— 「改的」与「漂的」靠它分开。"""
    return tuple(provenance(path)["changes"])


def _md5(path) -> str:
    return hashlib.md5(pathlib.Path(path).read_bytes()).hexdigest()


def compare(other=None, path=None) -> tuple:
    """跟另一份逐行比。返回 `(两边是否**逐行逐字相同**, 给人看的报告)`。

    ⚠️ `[0]` 读作**「一样吗」**（`True` = 没有分歧），不是「有分歧吗」——
    在「漂了没有」这根轴上读反，比没有这个函数更坏。
    ⚠️ 对方不在 = **比不了**，返回 `(False, …)` 且话里不许出现「没有分歧」那句结论。
    """
    mine = pathlib.Path(path or RUNTIME_PATH)
    theirs = pathlib.Path(other) if other else pathlib.Path(PRODUCTION_PATH)
    if not mine.is_file():
        return False, "比不了：这份运行时不在 %s。" % mine
    if not theirs.is_file():
        return False, ("比不了：要跟它比的那一份不在 %s —— 这**不是**「没有分歧」，"
                       "是没法比（部署过去了？还是路径变了？）。" % theirs)
    mine_text = mine.read_text(encoding="utf-8")
    theirs_text = theirs.read_text(encoding="utf-8")
    head = [
        "这份（siteforge 真源）：%s" % mine,
        "    行数 %d · md5 %s" % (len(mine_text.splitlines()), _md5(mine)),
        "那一份：%s" % theirs,
        "    行数 %d · md5 %s" % (len(theirs_text.splitlines()), _md5(theirs)),
    ]
    if mine_text == theirs_text:
        return True, "\n".join(head + ["", "没有分歧：两边逐行逐字相同。"])

    rows = list(difflib.unified_diff(
        theirs_text.splitlines(), mine_text.splitlines(),
        fromfile=str(theirs), tofile=str(mine), lineterm="", n=1))
    shown, more = rows[:200], max(0, len(rows) - 200)
    body = ["", "有分歧 —— 下面 `-` 是那一份、`+` 是这份："] + shown
    if more:
        body.append("（还有 %d 行没显示）" % more)
    body += ["", "上面这些差异里，**已知的加法式改动**是："]
    body += ["  · %s" % change for change in known_changes(path)]
    body.append("除此之外的差异 = 漂移（谁把哪边改了），看一眼再说，别顺手覆盖。")
    return False, "\n".join(head + body)


def show(path=None) -> str:
    """人话版的出身（`diff` 只能告诉你「不一样」，这条告诉你「这一份是什么」）。"""
    path = pathlib.Path(path or RUNTIME_PATH)
    prov = provenance(path)
    rows = [
        "siteforge 的 py 运行时（真源）：%s" % path,
        "  抄自        %s" % prov["copied_from"],
        "  那个 commit %s（%s）" % (prov["source_commit"], prov["source_commit_date"]),
        "  源文件 md5  %s" % prov["source_md5"],
        "  抄入日期    %s" % prov["copied_at"],
        "  归属        %s" % prov["owner"],
        "  抄入时的改动（三处，全是加法式）：",
    ]
    rows += ["    · %s" % change for change in prov["changes"]]
    rows += [
        "",
        "默认值仍与生产等价（CDP_PATH=/opt/skills/auto-farm-skill/cdp、两个 API URL 指 fmr.3tkj.cn）——",
        "部署后既有站点脚本共用这一份，改默认值就是悄悄改生产行为。",
        "看分歧：python3 -m agent.runtime diff",
    ]
    return "\n".join(rows)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(USAGE)
        return 2
    if args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]

    if command == "show":
        if rest:
            print(USAGE)
            return 2
        print(show())
        return 0

    if command == "diff":
        other = None
        if rest:
            if rest[0] != "--other" or len(rest) != 2:
                print(USAGE)
                return 2
            other = rest[1]
        target = pathlib.Path(other) if other else pathlib.Path(PRODUCTION_PATH)
        identical, text = compare(other)
        print(text)
        if not target.is_file() or not pathlib.Path(RUNTIME_PATH).is_file():
            return 2
        return 0 if identical else 1

    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
