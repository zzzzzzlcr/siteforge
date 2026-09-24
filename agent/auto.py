"""自动那条路（检测到就自己开修）—— 与「人按着走」分开的那一半。

**为什么单独一个文件**（用户 2026-09-24 点名：「别把代码写个好几千行可读性很差」）：
`service.py` 已经 6500+ 行，这套东西（到闸替人按 / 两遍为限 / 刹车）再堆进去没人读得动。
所以：**话与判据在这儿**（纯函数、可单测、一眼看完），`service.py` 只留两个碰 I/O 的口
（`_auto_reply`：读快照 + 交 resume；`_auto_give_up`：记进 job + narrate）和一行调用。

这一版（Task 1/2）只有「按几下、什么时候停」；值班脚本那半（看榜 / 对账 / 记账 / 心跳）
在 Task 5 加进这个文件。
"""
from __future__ import annotations

# ── 到闸替人按（Task 1）──────────────────────────────────────────────
#: `auto` 那一趟到闸时，服务替人按「继续」留下的那句。
#: ⚠️ 它**不是**「人说的」—— 时间线上两张嘴不许混成一张（所以单有一个 kind）。
AUTO_REPLIED_SAY = "自动模式：这一步自己走了（没人按）。"

#: 一次最多替人按几下（**防死循环**的兜底，不是「修几遍」那个计数）。
AUTO_MAX_PRESSES = 30

#: 刹车踩下去时那句人话（`%s` = 哪一道闸，`%d` = 已经按过几下）。
AUTO_STUCK_SAY = ("自动模式：**停在这儿了** —— 第 %s 这道闸按过之后没往前走"
                  "（已经替人按过 %d 下）。再按下去就是在原地空转（真站上=一直烧钱），"
                  "所以停住等人。")

# ── 两遍为限（Task 2，spec §5）───────────────────────────────────────
#: 最多修两遍 —— 一遍 = 出一版稿 + 自测一次。两遍都不过 ⇒ 停、报人。
AUTO_MAX_ATTEMPTS = 2

#: 两遍都没过那句人话（`%s` = 为什么需要人）。
AUTO_GIVE_UP_SAY = ("自动模式：**两遍都没过**（一遍 = 出一版稿 + 自测一次），再试下去只是在猜 ⇒ "
                    "停下、**需要人接手**。为什么：%s")

#: 「**做不成**」的停因：这些**不等两遍**、立刻报人（再试也不会变）。
#: ⚠️ 名字来自 `state.END_*`（那几个常量没全在服务里 import，所以按它们的**字面值**列；
#: `tests/test_service_auto.py` 钉着这张表与 `state` 里那几个名字逐字对得上）。
AUTO_CANNOT_DO = ("no_window", "missing_knob", "no_success_text", "window_gone")

#: 做不成那句人话的头（后面接那一趟自己留下的 `end_note`）。
AUTO_CANNOT_DO_SAY = "自动模式：这一趟**做不成**（不是没修好）—— 不等第二遍，直接报人。为什么：%s"


def attempts(values: dict) -> int:
    """这一趟**已经到第几遍** —— 只认账上有的东西：`visits` 里 `draft` / `selftest` 的次数。

    ⚠️ 为什么数 `visits`：图的每一步都会往 `visits` 里落一个节点名（`graph._visited`），
    所以「写过几版稿」这件事**账上本来就有** —— 不另开一格状态（那会是第二个真相源）。
    """
    visits = [str(v) for v in (values or {}).get("visits") or []]
    return max(visits.count("draft"), visits.count("selftest"))


def give_up_reason(values: dict) -> str:
    """该**不再替人按**了吗 ⇒ 为什么（空串 = 继续按）。两道判都在这一处（一处口径）。

    ① `AUTO_CANNOT_DO`（窗口没了 / 缺旋钮 / 底稿读不到 / 判据搬不出来）—— **不等第二遍**；
    ② 已经到 `AUTO_MAX_ATTEMPTS` 遍 —— 这就是领导拍的那个停点（「修两遍不通才抛给人」）。
    """
    values = values or {}
    reason = str(values.get("end_reason") or "")
    if reason in AUTO_CANNOT_DO:
        return AUTO_CANNOT_DO_SAY % (str(values.get("end_note") or reason)[:200])
    reached = attempts(values)
    if reached >= AUTO_MAX_ATTEMPTS:
        return "已经到第 %d 遍（上限 %d）都没过" % (reached, AUTO_MAX_ATTEMPTS)
    return ""


# ── 开修之前：那一单的国家要核成代理链上的码（Task 3）──────────────────────
#: 「那一屏上写的国家名」→ 代理链上用的码。**只放有出处的**（后端那张 `fmr.COUNTRY_SAY`
#: 里查得到的），表外一律 `None` ⇒ 调用方按「报人、不开」处理（不猜那条：不认识/没记/核不到）。
#: ⚠️ 「英国」在后端表里挂**两个**码（`GB` + 别名 `UK`）—— 所以这儿**明确写 GB**，
#: 不靠反查：反查出来有歧义，挑错就是把这一单导到**错的代理**上（真站上=拿错国家的号去做单）。
#: ⚠️ 这张表不许自己长：加一格之前先去 `fmr.COUNTRY_SAY` 里核到出处
#: （`tests/test_auto_country.py` 钉着这条 —— 编出来的格子会让那条用例红）。
SAY_CODE = {"美国": "US", "英国": "GB"}


def code_for(say) -> str | None:
    """那一屏上写的国家名 ⇒ 码。**表外一律 `None`**（不猜）。

    ⚠️ 只认表里那个写法（前后空格可以）：直接塞码（`"US"`）、大小写不对（`"us"`）、
    没记过的国家 —— 全是 `None`。调用方拿到 `None` 的规矩是**报人、不开 job**，
    不是「试试看」—— 真站上那一下是拿钱和号去跑的。
    """
    return SAY_CODE.get(str(say or "").strip())


import json
import pathlib
import time

# ── 值班那半：记账 / 开关 / 心跳（Task 5）───────────────────────────────
#: 每天最多自动开修几个站（用户拍的无人值守护栏）。
DAY_CAP = 10
#: 心跳多久算「值班脚本不在了」（面板那条报警用这一格 —— 一处口径）。
HEARTBEAT_MINUTES = 15
#: 值班的东西都落在这个目录下（开关、账本、心跳）。
STATE_DIR = pathlib.Path("runtime") / "auto-repair"


def read_flags(root=".") -> dict:
    """值班的**总开关**（`runtime/auto-repair/flags.json`）—— **默认全关**。

    返回 `{"on": bool, "note": str}`。⚠️ **读不到 / 不是 JSON / 不是对象 ⇒ 一律「关」**：
    值班脚本是无人看着跑的，「读不懂就当作开着」是这一仓最贵的那类形状的反面
    （宁可今天不干活，也不要没人看着干活）。
    """
    path = pathlib.Path(root) / STATE_DIR / "flags.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"on": False, "note": ""}
    if not isinstance(raw, dict):
        return {"on": False, "note": ""}
    return {"on": bool(raw.get("on")), "note": str(raw.get("note") or "")}


class Ledger:
    """**今天已经开过哪几个站** —— 一个串一天只开一次 + 一天最多 `DAY_CAP` 个。

    账本是一份 JSON：`{"<日期>": {"<站点键>": <时刻>}}`。只留今天（写的时候顺手把旧的丢掉）。

    ⚠️ 读不回来时**抛**（不是当作空）：当成空 = 同一个站今天会被再开一遍
    （真站上就是重复烧钱、重复提交）—— 这种时候该停下来让人看，不是自己接着跑。
    """

    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise RuntimeError("账本读不回来（%s）：%s —— 不敢当作空的接着跑" % (self.path, exc))
        if not isinstance(raw, dict):
            raise RuntimeError("账本不是一份对象（%s）—— 不敢当作空的接着跑" % self.path)
        return raw

    def may_open(self, site: str, *, today: str) -> tuple:
        """今天还能不能开这个站 ⇒ `(能不能, 为什么不能)`。**同一处口径**（`run_once` 只问它）。"""
        site = str(site or "").strip()
        if not site:
            return False, "这个站没有键（榜单那一格是空的）—— 不知道拿谁去记账，**不猜**"
        done = self._load().get(today) or {}
        if site in done:
            return False, "今天已经开过它了（%s）—— 同一个站一天只开一次" % done[site]
        if len(done) >= DAY_CAP:
            return False, "今天自动开修已经到顶（%d 个）—— 剩下的等人" % DAY_CAP
        return True, ""

    def note(self, site: str, *, today: str, at: str) -> None:
        """记下「今天开过它」（**先记再开** —— 开完再记，中途挂了就会重复开）。"""
        book = self._load()
        day = dict(book.get(today) or {})
        day[str(site).strip()] = str(at)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({today: day}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)               # 换文件是原子的：读到半截账本那种事不会发生

    def count(self, *, today: str) -> int:
        return len(self._load().get(today) or {})


def heartbeat(root=".") -> None:
    """值班脚本每次转一圈就写一下「我还在」（时刻用数字，免得时区绕进去）。"""
    path = pathlib.Path(root) / STATE_DIR / "heartbeat.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("%.0f" % time.time(), encoding="utf-8")


def heartbeat_age(root=".", *, now: float = None) -> float | None:
    """心跳过去多久了（秒）；**从来没跳过** ⇒ `None`（「没跳过」与「刚跳过」不是一回事）。"""
    path = pathlib.Path(root) / STATE_DIR / "heartbeat.txt"
    try:
        stamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, float(now if now is not None else time.time()) - stamp)
