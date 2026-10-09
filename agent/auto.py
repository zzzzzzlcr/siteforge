"""自动那条路（检测到就自己开修）—— 与「人按着走」分开的那一半。

**为什么单独一个文件**（用户 2026-09-24 点名：「别把代码写个好几千行可读性很差」）：
`service.py` 已经 6500+ 行，这套东西（到闸替人按 / 两遍为限 / 刹车）再堆进去没人读得动。
所以：**话与判据在这儿**（纯函数、可单测、一眼看完），`service.py` 只留两个碰 I/O 的口
（`_auto_reply`：读快照 + 交 resume；`_auto_give_up`：记进 job + narrate）和一行调用。

这一版（Task 1/2）只有「按几下、什么时候停」；值班脚本那半（看榜 / 对账 / 记账 / 心跳）
在 Task 5 加进这个文件。
"""
from __future__ import annotations

#: ★ 2026-10-08：停点从**图的上限**推出来（见下面 `AUTO_MAX_ATTEMPTS`）——
#: 两处不许各写一份。`state` 不 import 本模块，所以这里没有环。
from agent.state import Caps  # noqa: E402

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
#:
#: ★ 2026-10-08（用户：自测没过那三趟 —— 「拿这份记录去定位，定位完回 draft 改一版」里
#: 「定位」和「改一版」都是人干的；要它自己好）：这个数**不再单独拍** —— 它必须等于
#: **图自己触顶的那一步**（`state.Caps.max_diagnoses + 1`）。
#: 为什么（真事，`job-b9f0c6a74cbd` / `job-9c5b17be2629` / `job-cfefcd7a3aef`）：三趟都停在
#: `diagnose` 闸上 —— `src` 里有稿、`unverified` 是空的，运营看到的是「什么都没有」。
#: 而「留料」（这一版落进交付路径 + `unverified` 写清「自测没过」）**只发生在图触顶那一步**
#: （`graph._selftest` → `_keep_unverified`，`tests/test_graph.py` 钉着它）。
#: auto 要是**早一步**抛人，那一步永远走不到。⇒ 按到那儿为止，两件事同一步发生：
#: 「出了、标清了没验过」+「需要人」。
#: ⚠️ 所以这一格是**推出来的**，与图的上限一处口径（`tests/test_service_auto.py::
#: test_the_auto_stop_point_is_pinned_to_the_graphs_own_cap` 拿等式钉着）。
AUTO_MAX_ATTEMPTS = Caps().max_diagnoses + 1

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
#: ⚠️ 路径**照设计文档**（`docs/superpowers/specs/2026-09-24-auto-repair-design.md` §7：
#: 「每轮往 `runtime/auto/heartbeat` 写一行」）—— 一处口径，别另起一个目录名。
STATE_DIR = pathlib.Path("runtime") / "auto"

#: 面板读「值班那几格」那个口（服务那侧只用它拼路由 —— 一处口径）。
AUTO_PATH = "/auto"

#: 值班脚本回调服务用的地址（**默认值**）—— 必须与 `tools/relaunch-svc.py` 启动时监听的端口一致。
#: ★ 2026-09-24 验收第一跑逮出来的：这里原本是 `8080`（**没人听**），而服务在 `8099` ⇒
#: 那一圈「挑中站 ✓ 过了对账 ✓ 记了账 ✓」然后**死在 callback 上** ✗（`Connection refused`）——
#: 而「先记再开」意味着**账本里白白留下一笔**，那个站当天再也开不了 ✓（代价是真的 ✓）。
#: `tests/test_auto_run_once.py` 有一条**跨文件**的钉子钉着这一格（改启动脚本的口，这条就红）。
SERVICE_URL = "http://127.0.0.1:8099"


def read_flags(root=".") -> dict:
    """值班的**总开关**（`runtime/auto/flags.json`）—— **默认全关**。

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


# ── 看榜：后端那张「连续失败」榜 → 一行一个站（Task 5）────────────────────
def _int_or_none(raw) -> int | None:
    """整数就返回它；**其余一律 `None`**（**绝不拿 0 顶替**，也**不许截断**）。

    只认两种：真`int`，和**干干净净的数字串**（后端偶尔把数当字符串来，像 `task_id` 那样；
    那是明确的，不该因为 JSON 的类型习惯就当「没给」）。
    其余（`None` / `""` / 带小数点 / 布尔 / 散文）⇒ `None` = 「它没给」——
    ⚠️ 带小数点那种**不四舍五入**：截出个 23 就成了「连续 23 次」的证据，而那是我们编的。
    """
    if isinstance(raw, bool):                    # `bool` 是 `int` 的子类 ⇒ 先挡掉（True 不是 1 次失败）
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def parse_list(body) -> list:
    """后端那张「连续失败」榜 → 一行一个站（**信封不认就抛**；v2 的格子缺了**不算不认**）。

    口径照 `/tmp/rank-v2-handoff.html`（后端 CR 37794）+ 2026-09-24 真数据实测：
      ① 信封是对象 `{"status","msg","data":{...,"rank":[…]}}`；`data`（或 `rank`）**不是对象/数组**
         = 这一次**没量着**，**不是**「今天没有失败」⇒ **抛**（`agent/fmr.py` 顶部那条纪律：
         只有「真量了、真没有」才给空榜）。
      ② 一行 = `{site, fail, streak, config_id, config_status, has_script}`（v2 还多 `configs[]`）。
      ③ ★ **缺 `streak` 不算不认**（v1 后端就没有那一格，交接写「缺失时退回旧口径…不编数、不假 0」）
         ⇒ 不抛，而且**绝不拿 0 顶替**：`streak=None` 就是「它没给」。
      ④ ★★ **判「有没有在失败」一律用 `streak`**：实测「失败 **0** 次 · 连续失败 **23** 次」——
         `fail` 是**当日**条数、`streak` 是**跨天连败**（后端已按 `min_streak` 筛过），两者会脱钩。
      ⑤ `site` 空的行**照收**（量到了就摆）—— 拒开是值班那边的判（`Ledger.may_open` 会说「没有键」）。
    """
    if not isinstance(body, dict):
        raise ValueError("榜单不是一份对象（%s）—— 这一次**没量着**，不是「今天没有失败」"
                         % type(body).__name__)
    #: ⚠️ **两种形状都认**（2026-09-24 真数据当场逮住的）：`fmr.FmrClient.fetch_rank()` 交回来的
    #: **就是信封里那一份 `data`**（冒烟脚本里直接 `data.get("rank")`）；而契约文档写的是整个信封
    #: `{"status","msg","data":{…}}`。两种都认 —— 但**认不出 `rank` 就是抛**（不许静默成空榜）。
    inner = body.get("data")
    data = inner if isinstance(inner, dict) else body
    if "rank" not in data:
        raise ValueError("这一份里没有 `rank`（%r）—— 这一次**没量着**，不是「今天没有失败」"
                         % (sorted(data)[:6],))
    rows = data.get("rank")
    if not isinstance(rows, list):
        raise ValueError("`rank` 不是数组（%s）—— 同上：这一次**没量着**" % type(rows).__name__)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("榜单里有一行不是对象（%s）" % type(row).__name__)
        out.append({
            "site": str(row.get("site") or "").strip(),
            "fail": _int_or_none(row.get("fail")),
            "streak": _int_or_none(row.get("streak")),   # ★ 缺 = None（**不是 0**）
            "config_id": row.get("config_id"),
            "config_status": str(row.get("config_status") or ""),
            "has_script": bool(row.get("has_script")),
            "task_id": str(row.get("task_id") or ""),
        })
    return out


#: 看榜时一次问几个（后端默认 50、上限 200）—— 一次只开一个，50 够扫了。
RANK_LIMIT = 50


def _post_json(url: str, payload: dict) -> tuple:
    """真发请求那条路（**标准库**：值班脚本不进容器、也不想多背一个依赖）。返回 `(码, 正文)`。"""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as got:
            return int(getattr(got, "status", 200)), got.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:                       # 4xx/5xx 也是「答复」，不是异常
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _stop(did: str, why: str, site: str = "") -> dict:
    """这一圈**没开修**（不是错误）：说清为什么，CLI 把它打出来。"""
    return {"ok": True, "did": did, "why": why, "site": site}
def run_once(*, fmr: object, post=None, root=".", today=None, now=None,
             service_url: str = SERVICE_URL,
             allow_disabled: bool = True) -> dict:
    """**转一圈**：看榜 → 对账 → 开修（一次只开一个）。返回这一圈干了什么。

    口径照设计文档（`docs/superpowers/specs/2026-09-24-auto-repair-design.md`）：
      · §7 总开关**默认关**：关着就只跳一下心跳、什么都不动；
      · §6 一次只修一个（榜上同时冒 3 个新的 ⇒ **排队**，这一圈只挑第一个够格的）；
      · §6 同一串不开第二趟（`Ledger`）；每天最多 `DAY_CAP` 个（`Ledger` 一处判）；
      · ★ **国家不在榜上**（v2 榜没有那一格）⇒ 「对账」是**必需**的一步：
        按站点键读那一单的失败记录，从那儿拿 `task_id` + `country`（读不到 ⇒ 报人、不开）；
      · 开修**不带 `ws_url`**：窗口是服务那摊（§3 职责表）；无窗口时服务会走到 `no_window`
        ⇒ 按「做不成 ⇒ 立刻报人」收尾（§8 验收 2 那条无窗口桩正是这么验的）。

    ⚠️ 还没做的一件（**说清楚**，不假装有）：§6 那条「榜上同一串**已有未结案的活**就不开第二趟」
    只做了一半 —— 账本管住了「同一个串今天开过一次」，管不住「上一次开的还挂着」。
    要管住它得读服务的在跑清单，而那份清单的**行形状**我还没量过（不许照想象写判据）⇒ 留待量过再补。
    """
    post = post or _post_json
    now = float(now if now is not None else time.time())
    today = str(today or time.strftime("%Y-%m-%d", time.localtime(now)))
    heartbeat(root)                       # 先跳一下：值班「还在」与「开不开」是两件事
    flags = read_flags(root)
    if not flags["on"]:
        return _stop("没动", "总开关是**关**的（%s）" % (flags["note"] or "没说为什么"))

    rows = parse_list(fmr.fetch_rank(date=today, limit=RANK_LIMIT))   # 信封不认 ⇒ 它抛（让上面报）
    book = Ledger(pathlib.Path(root) / STATE_DIR / "ledger.json")
    skipped, picked = [], None
    for row in rows:
        site = str(row.get("site") or "").strip()
        if not site:
            skipped.append("榜上有一行的站点键是空的（读不到它的失败记录，**不猜**）")
            continue
        if row.get("streak") is None:
            #: ★ **不猜**：后端没给「连败几次」（v1 后端就没有那一格）⇒ 这一行不开
            #: （0 是合法读数，拿它顶替「没给」就是把「量不出」写成「它没在连败」）。
            skipped.append("「%s」连败几次后端没说（v1 后端？）—— **不猜**" % site)
            continue
        ok, why = book.may_open(site, today=today)
        if not ok:
            skipped.append(why)
            continue
        #: ⚠️ 读不出来就**跳过这一行、看下一行**：别让一条读不出单的行把整圈卡住
        #: （2026-09-24 实测：榜上真有读不出来的行 —— 缺配置的那一条，带不带 `type=debug` 都 404）。
        #: ★ 窗口要**够宽**（2026-09-24 实测）：这个读口不给 `since` 时默认「**今天 00:00**」，
        #: 而榜单是按**跨天连败**筛的 ⇒ 拿「今天」去对账 ⇒ **一条都读不到**
        #: （实测：三行里两行的最新失败是 9-23 / 9-20，用今天查就是 0 条 —— 那不是「没有」）。
        #: 窗口照**后端自己那道闸**的实测取值（榜单回显的 `days: 7`）。
        since = time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400))
        try:
            fails = list(fmr.fetch_failures(site, since=since) or [])
        except Exception as exc:                      # noqa: BLE001 —— 读不到就说读不到
            #: 第二跳：**带上 `type=debug` 再问一次**（只有 404 才值得 —— 与 `form_script` 同源）。
            #: ⚠️ 实测边界：它救的是「被 `where('status',1)` 滤掉」那一支；**缺配置**那一条
            #: 带不带都是 404 ⇒ 第二跳之后还是读不到，就照实报人（不猜）。
            if int(getattr(exc, "status", 0) or 0) != 404:
                skipped.append("对账读不到「%s」的失败记录：%s" % (site, exc))
                continue
            try:
                fails = list(fmr.fetch_failures(site, since=since, debug=True) or [])
            except Exception as exc2:                 # noqa: BLE001
                skipped.append("对账读不到「%s」的失败记录（带 type=debug 也没读到）：%s"
                               % (site, exc2))
                continue
        if not fails:
            skipped.append("「%s」有连败记录、却读不到失败行（存量条目？）—— 对不上账" % site)
            continue
        first = fails[0]                              # 那个读口按时间**倒序** ⇒ 最新那条
        task_id = str(first.get("task_id") or "").strip()
        country = str(first.get("country") or "").strip()
        if not task_id or not country:
            skipped.append("「%s」那一单缺 task_id / country（%r）—— 不知道切哪条链"
                           % (site, first))
            continue
        picked = row
        break
    if picked is None:
        return _stop("没动", "榜上没有一个够格开修的 —— %s" % ("；".join(skipped) or "榜是空的"))

    #: ★ 验收那一趟跑出来的（2026-09-24）：**fix 模式必须带「这次要做什么」** —— 服务在门口就拦：
    #: 「还没说**这次要做什么**：`goal`（或者 fix 模式下的失败证据 `evidence`）」。所以证据从
    #: 那一单自己身上取（与面板「照这条修」同一个做法：失败行 + 它报过的那几步）。
    try:
        steps = list(fmr.fetch_steps(task_id) or [])
    except Exception as exc:                      # noqa: BLE001 —— 读不到就说读不到（口径一条）
        return _stop("没动", "对账读不到「%s」那一单的逐步记录：%s" % (site, exc), site=site)
    from agent import fmr as fmr_mod      #: `evidence_text` 是**模块**里的函数（不是客户端的方法 —— 验收跑出来的）
    evidence = fmr_mod.evidence_text(first, steps)
    #: **先记再开**：开完再记的话，中途挂了就会重复开（真站上=重复烧钱 + 重复真提交）。
    book.note(site, today=today, at=time.strftime("%H:%M:%S", time.localtime(now)))
    code, said = post(service_url.rstrip("/") + "/run", {
        "mode": "fix", "auto": True,
        "url": site, "fix_site": site, "fix_site_url": site,   # 三格同串 ⇒ 服务认这个键（_backend_key）
        #: ★ 国家那一格（**别丢**：T3 的国家闸就认它 —— 丢了这个，真跑时链切不了）。
        "env": {"proxy_country": country},
        #: ★ 2026-09-24（用户点破）：「停用」**是常态、不是例外** —— 一个站连败十几次，运营当然把
        #: 它停用（那是**对失败的反应**）⇒ 自动那条路要**默认允许**拿停用那份当底稿
        #: （`type=debug` 读得到，2026-09-21 量过），否则它永远修不了最该修的那些站。
        #: ⚠️ 两条不放松：① 服务会把「这一版底稿来自**停用那份**」记进状态 / 摆在闸上；
        #: ② 停用行**绝不自动上传**（T4 那条按 `allow_disabled` 判的）。
        "allow_disabled": bool(allow_disabled),
        "evidence": evidence,          # ★ 上面那一条：不带它，服务在门口就拦（验收跑出来的）
        "note": "自动值班：榜上「%s」连败够久（task %s）" % (site, task_id),
    })
    if code not in (200, 202):
        return {"ok": False, "did": "开了但被拒", "site": site,
                "why": "服务回了 %s：%s" % (code, str(said)[:300])}
    return {"ok": True, "did": "开了", "site": site,
            "why": "task %s · 国家 %s · 服务回了 %s" % (task_id, country, code)}


def ledger_at(root=".") -> "Ledger":
    """账本在哪儿 —— **一处口径**（CLI、`run_once`、服务那几个读口都走这儿，别再各拼一份）。"""
    return Ledger(pathlib.Path(root) / STATE_DIR / "ledger.json")


#: 开关拨了之后那句话（`%s` = 为什么）。⚠️ 关那一句必须点明「**在跑的那一趟不受影响**」——
#: 开关管的是「还开不开新的」，管不到已经开出去的那一趟（那是「停」按钮的事）。
FLAGS_ON_SAY = ("自动值班**开了**（%s）—— 它每轮看一次榜：连败够久的站自己开修，"
                "修两遍不通才找人。停它就再按一次这个开关。")
FLAGS_OFF_SAY = ("自动值班**关了**（%s）。⚠️ **这会儿在跑的那一趟不受影响**（它自己会走完）——"
                 "要它现在就停，去那一趟上按「停」。")


def write_flags(root=".", *, on: bool, note: str = "") -> dict:
    """把总开关写下去（**原子**：先写 `.tmp` 再 `replace`）—— 面板那个按钮只按它一下。

    ⚠️ 这是**唯一**一个写开关的地方（读在 `read_flags`，一处口径）：值班脚本自己**不改**开关
    （它只读）——「谁把它打开的」这件事必须只有人能回答。
    """
    path = pathlib.Path(root) / STATE_DIR / "flags.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"on": bool(on), "note": str(note or "")}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)                    # 换文件是原子的：读到半截开关那种事不会发生
    return body
