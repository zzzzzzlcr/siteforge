"""① 「读」：**从 FMR 取失败记录**（Task 13）。

## 它解决的是哪件事

运营那一屏（`agent/console.html`）到 Task 12 为止能「看一趟 / 回话 / 停 / 开一趟」，
但它**不知道哪一趟失败了** —— 要发起修复，得有人从别处把失败**手抄**进来。
这一层把那条路接上：**失败列表 → 那一趟的逐步记录 → 一段人话（`evidence`）**。

## 契约（**线上实测过的，原样用；不许猜、不许改**）

```
① GET {base}/api/quest/formLog   X-Api-Token: <token>
     ?site=<config 的 site>&status=failed&since=<ISO8601>&limit=<n>
     ⚠️ since 默认「今天 00:00」—— 要查昨天必须显式给
  → {"status":200,"msg":"success","data":[
       {"task_id":"26034602","site":"www.gowizard.com/auto-warranty","status":"failed",
        "type":"site_specific","country":"US","created_at":"2026-09-19T10:21:38+08:00"}, …]}
    按时间**倒序**（**不重排** —— 重排就是这一层自己编了一个顺序）
② GET {base}/api/quest/formStep?task_id=<id>   X-Api-Token: 同上
  → {"status":200,"msg":"success","data":[
       {"id":79794,"task_id":26033398,"step":"start","url":"https://…"},
       {"id":79795,"task_id":26033398,"step":"no_success","url":"…"}]}
    按 id **升序**；**不含 base64、不含时间**
信封 {"status","msg","data"}；**HTTP 恒 200** —— 错码只在 body 的 `status` 里：
400（缺 site / 缺 task_id）、401（鉴权）、404（site 反查不到 / 没有这个 config）。
```

## 这一层的重心：**「量不到」与「没有失败」是两件事**

这是本仓反复栽过的形状（`fail-script` 实测过一次最贵的：无效 key 回 404，
诊断页把它显示成「近 2 天没有失败 ✓」—— **一个查不到的站被标成健康的**）。

所以在这一层里它是一条**类型**上的区分，不是一句注释：

| 情形 | 这一层给的 | 到了屏幕上 |
|---|---|---|
| 真量了、真没有（`status:200` + `data:[]`） | **空 list** | 「这段时间里没有失败记录」 |
| 没配 token / 连不上 / 超时 / 正文不是 JSON | `FmrUnreachable` | 「**量不到**」（并明说不是「没有失败」） |
| 后端回了 400/401/404 | `FmrRefused` | 同上（带上后端那句 `msg`；404 那句要点到 key 上） |

**唯一能变成空列表的，只有第一行。** 其余**一律抛**。

## 两条小纪律

- **token 走请求头，绝不进 URL**（URL 会进日志、进 `ps`）—— 与
  `fail-script/src/diagnosis/datasource.py` 那句注释同源。
- **token 在构造那一刻读一次环境**（`FMR_AGENT_TOKEN`）：与 `Service.__init__` 里
  `_cdp_bin` / `_shots_dir` / `_selftest_root` 同一条不变量 —— 服务跑起来之后
  环境再变，不该悄悄换一个身份（那是「名字说 A、量的是 B」）。

## 这一层现在有四个读口（Task 4 起）

| 口 | 答的是 |
|---|---|
| `fetch_failures(site)` | **这个站**失败了哪几趟 |
| `fetch_steps(task_id)` | 那一趟报过哪几步 |
| `fetch_rank(date)` | **今天哪些站**在失败（榜单）—— Task 4 加的 |
| `fetch_diag(task_id)` | **这一单为什么**失败（原因行）—— Task 4 加的 |

```
③ GET {base}/api/quest/formLogRank?date=<YYYY-MM-DD>&limit=<n>   X-Api-Token: <token>
 → {"status":200,"msg":"success","data":{
      "date":"2026-09-20","failed_total":18,"unattributed":2,
      "rank":[{"site":"callyourdate.com/land/sp/519015a5","fail":3,
               "config_id":66,"config_status":"启用","has_script":true}, …]}}
   ⚠️ `data` 是**对象，不是数组**；而**没有失败的那一天回的也是这个对象**
      （`failed_total:0` / `rank:[]`）⇒ 所以「`data` 不是对象」= 这一次**没量着**，
      **不是**「今天没有失败」。（这就是下面 `_call(shape=…)` 存在的理由。）
   ⚠️ `limit`（默认 50、上限 200）**只截断 `rank`**：`failed_total` / `unattributed` 是当日全量
      ⇒ `sum(rank[].fail) + unattributed` 可以**小于** `failed_total`，差额就是没摆出来的部分
      （不是接口漏了数据，但**屏幕上要说出来**）。
   ⚠️ `rank[].site` 是**展示用的键，不是 join 键**：线上实测过它带 query、带尾斜杠、
      甚至粘着一段别人的报错 ⇒ 拿它去 `formLog` 查**可能查不到**，而那一次是「量不到」。
      **要办事一律走 `task_id`**（Task 4 的三条硬要求 R1）。
④ GET {base}/api/quest/failDiag?task_id=<单号>                X-Api-Token: 同上
 → {"status":200,"msg":"success","data":[
      {"id":83,"task_id":99999999,"site":"…","machine":"…","exit":"stuck",
       "lines":"…那几行日志…","at":"…","created_at":"…"}]}
   按 `created_at` **倒序**（第一行 = 最近一次的现场）；`task_id` 回来是**数字**（库里是 int）。
   ⚠️ **空数组 = 「量到了，这单还没有原因行」**（新功能 / 老单 / 那台机器没发上来）——
      它不是「量不到」，但屏幕上**也不许留白**（要明说「还没有原因」，Task 4 R2）。
   ⚠️ `exit` 的取值**有意是开放的**（后端刻意不校验），实测客户端会报 `unknown` ⇒ 见 `EXIT_SAY`。

## ★ 后端地址：**一处旋钮**（Task 4 §4）

`DEFAULT_BASE` ／ 环境变量 `FMR_BASE_URL`（常量名 `BASE_ENV`）—— **四个读口全走它这一个**，
在 `FmrClient.__init__` 里读**一次**（构造之后不再看环境）。

⚠️ 今天两边的部署**不同步**（这是 §4 存在的原因）：线上 `fmr.3tkj.cn` **还没有** `failDiag`
（brief 实测 404），本地 `192.168.1.51:6060` **有**。所以要对着本地实例跑就把这一处换掉：
`FMR_BASE_URL=http://192.168.1.51:6060`。
**没有「按接口各配一个基址」这回事** —— 真长出来就是「名字说 A、量的是 B」那个老病的新变种。

⚠️ 这一层**不打真模型、不开浏览器**；`opener` 是注入的口子（测试给它桩）。
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, List, Optional

__all__ = [
    "FmrClient", "FmrUnmeasured", "FmrUnreachable", "FmrRefused",
    "DEFAULT_BASE", "TOKEN_ENV", "BASE_ENV", "DEFAULT_LIMIT", "DEFAULT_TIMEOUT",
    "UNMEASURED_SAY", "failure_say", "step_say", "evidence_text", "entry_url",
    "today_midnight", "since_text",
    # Task 4：榜单（③）与原因（④）
    "DEFAULT_RANK_LIMIT", "RANK_MAX_LIMIT", "DIAG_PAGE_SIZE",
    "EXIT_SAY", "CONFIG_STATUS_SAY", "NO_DIAG_SAY",
    "exit_say", "has_script_say", "rank_row_say", "rank_say", "diag_head_say",
]

#: 线上实测过的那一个。`FMR_BASE_URL` 只是给测试/灰度留的口子 —— **默认值就是它**。
DEFAULT_BASE = "https://fmr.3tkj.cn"
#: token 从哪儿来。**绝不写死在代码里、绝不进 git**（没配就明说，见 `FmrClient`）。
TOKEN_ENV = "FMR_AGENT_TOKEN"
#: 改后端地址的口子（默认值不变）。
BASE_ENV = "FMR_BASE_URL"

#: 一次最多摆几条。**摆满了要说**（「可能还有更早的」）—— 悄悄截断就是「没有静默的路径」的反面。
DEFAULT_LIMIT = 20
#: ③ 榜单一次最多摆几个站。与 ① 那个 `DEFAULT_LIMIT` **不是一回事**（两个接口的默认值/上限
#: 各归各的：后端 `RANK_DEFAULT_LIMIT = 50` / `RANK_MAX_LIMIT = 200`）—— 所以单开一格，
#: 不拿 ① 那个数去顶（顶了就是「名字说 A、量的是 B」）。
#: 出处：`farmer` 仓 `app/Http/Controllers/api/QuestDiagnosisController.php` 的
#: `const RANK_DEFAULT_LIMIT = 50;`（本文件只读它，不改它）。
DEFAULT_RANK_LIMIT = 50
#: ③ 榜单一次最多能要几个站（后端 `RANK_MAX_LIMIT = 200` 的镜像 —— 超了后端回 400，
#: 而 400 到屏幕上只剩「量不到」，读的人不知道是自己那一格要多了）。
#: ⚠️ 它**只是**用来把那句话说出来（「后端上限 200」），本层**不替调用方截断**
#: —— 悄悄截到 200 就是把一次 400 换成一句看不出来的少算。
RANK_MAX_LIMIT = 200
#: ④ 一单**最多回几条**原因行 —— 后端那份的 `DEFAULT_LIMIT = 100`（我们不发 `per_page`，
#: 所以就是它）。出处：`farmer` 仓 `QuestDiagnosisController.php` 的
#: `const DEFAULT_LIMIT = 100;` 与 `$perPageRaw = $request->input('per_page', self::DEFAULT_LIMIT);`。
#: ⚠️ 这个数**只用来把「摆满了」说出来**：那接口回的是一根**裸数组**（**不带总数**），
#: 所以「够 100 条」时我们**分不出**是不是到底了 —— 那就照实说分不出，别装作到底了。
DIAG_PAGE_SIZE = 100
#: 读这两个接口的超时（秒）。它们是只读的旁路，慢一点可以等，但**不许无限等**。
DEFAULT_TIMEOUT = 20.0

#: 每一句「量不到」里都带这一句 —— 它是**给读的人**的：这一条不是「没有失败」。
#: ⚠️ 用同一个常量，别在四处各写一句（那样「说了没有」就成了四件事）。
UNMEASURED_SAY = "这一条**不是**「那个站没有失败」—— 是「这一次没量着」"

# ── 代号 → 人话 ──────────────────────────────────────────────────────────
#: `status` 那一格。实测见过的是 `failed`（我们查的就是它）与 `success`。
STATUS_SAY = {
    "failed": "失败",
    "success": "成功",
}
#: `type` 那一格。取值清单的出处：生产仓 `docs/SITE-SCRIPT-GUIDE.md:323`
#: （`form_type: car_insurance, health_insurance, life_insurance, home_improvement,
#: hearing_aid, dating, newsletter, timeshare_cancellation, senior_survey, casino,
#: education, mortgage, site_specific`）+ `docs/ai-form-step-reporting.md`
#: 里的 `json_exec`。**表里没有的一律如实说「这一屏不认识」**（不猜、也不吞）。
TYPE_SAY = {
    "site_specific": "站点专属",
    "car_insurance": "车险",
    "health_insurance": "健康险",
    "life_insurance": "寿险",
    "home_improvement": "房屋改造",
    "hearing_aid": "助听器",
    "dating": "交友",
    "newsletter": "订阅邮件",
    "timeshare_cancellation": "分时度假解约",
    "senior_survey": "老年问卷",
    "casino": "博彩",
    "education": "教育",
    "mortgage": "房贷",
    "json_exec": "配置跑的（JSON）",
}
#: `country` 那一格。**只放有出处的那些**（`US` 在实测样例里；`GB`/`CA` 在
#: 生产仓的代理链与文档里）。表外的如实说「这一屏不认识」——**不猜**。
COUNTRY_SAY = {
    "US": "美国",
    "GB": "英国",
    "UK": "英国",
    "CA": "加拿大",
}
#: `step` 那一格。**它不是流水账，是产物里那个状态组的名字**（见 `TERMINAL_SAY` 那段）。
#: 认不出就如实说（这一格的取值是**开放的**，下面 `TERMINAL_SAY` 的注释里有枚举）。
STEP_SAY = {
    "start": "进站（这一趟第一次打开的那一页）",
}

#: ★ **终点**：`formStep` 那几行里，**最后一行**是它们之一 ⟹ 这一趟走到头了。
#:
#: `(人话短词, 一句解释)`。
#:
#: **出处（可复算，不是猜的）**：生产那份产物
#: `/opt/skills/auto-farm-skill/forms/_remote/www.gowizard.com_auto-warranty_.py`
#: （与仓里 `forms/sites/gowizard_fixed.py` 逐行同源）里，`_rpt(...)` 只有这四种终局，
#: 而且**每一处都紧跟一个 `return`**（`_rpt("stuck")` / `_rpt("stalled")` → `return False`，
#: `_rpt("success")` → `return True`，`_rpt("no_success")` → `return False`）⇒
#: **对这一族来说，终点一定是最后一行。**
#: ⚠️ 但这一格是**开放的**：生产日志里真报出去过的取值有 **160 个**
#: （`/opt/skills/auto-farm-skill/logs/` 那 169 个 log 里 `[URL Report] step=` 那一列，
#: 其中 **111 个只出现过一次**），绝大多数是 AI 那条路自由起的名
#: （「After clicking Compare Now」这种）。⇒ 「最后一行不是认得的终点」这条路
#: **必须走得通**（`where_it_stopped` 里那一支），**不许假设**它一定是终点。
TERMINAL_SAY = {
    "success": ("成功", "走完了，页面上出现了成功文案"),
    "stuck": ("卡住", "连着几步没做成，早停收摊"),
    "stalled": ("原地打转", "连着几步点了页面都没动，早停收摊"),
    "no_success": ("没成功", "能走的都走完了，成功那一页从头到尾没出现"),
    "ai_completed_no_success": ("没成功", "AI 那一路跑完了，没见到成功那一页"),
}

# ── ④ 原因那一屏的两格（Task 4）──────────────────────────────────────────
#: `failDiag` 的 `exit` 那一格 —— 跑单那边报的「这趟是怎么结束的」。
#:
#: ★★ **`unknown` 在这里是「没报上来」，不是那三种之一。** 出处（客户端自己的原话）：
#: `/opt/skills/auto-farm-skill/scripts/ad-task.py:1265` `_fail_diag_exit()` ——
#: 「导航态 / 认不出来 → `unknown` —— 别硬塞成三个已知值之一：那会让查的人**看错原因**，
#: 比空着更坏」（同文件 `:1047` 是它认得的那三个 `FAIL_DIAG_EXIT_LABELS`）。
#: ⇒ 这一屏**照它的意思办**：把 `unknown` 编成三种之一，就是把客户端刻意不说的那句话说回去。
#:
#: ⚠️ **表是从 `TERMINAL_SAY` 派生的，不是抄的**：这三格在 `formStep` 那边是**终点**、
#: 在 `failDiag` 这边是 `exit` —— **同一个词两个口子报**，两处各写一份迟早漂。
#: （`success` / `ai_completed_no_success` 也一起带过来了：后端**有意**不校验取值集合，
#: 万一哪台机器真报了，照实显示比装作不认识有用 —— 而且那是个该被人看见的矛盾。）
EXIT_SAY = {code: word for code, (word, _why) in TERMINAL_SAY.items()}
EXIT_SAY["unknown"] = "没报上来"

#: ③ 榜单里 `config_status` 那一格。后端给的就是中文（`启用` / `停用`，没有配置时是 `null`）。
#: ⚠️ 既然它是**给它自己人看的一格**，为什么还要过表：**取值是开放的**（后端在别处还会
#: 长出别的状态）。过一遍表，认不出的那种才会**带着原样的字**冒出来，而不是悄悄放行
#: （「一个我们不认识的词被原样当人话显示」正是这一屏要防的那类静默）。
CONFIG_STATUS_SAY = {
    "启用": "启用",
    "停用": "停用",
}

#: ★ ④ 里**最常见的那一种**：单号查得到、可是**还没有原因行**。
#: （新功能刚上 / 老单 / 跑那趟的机器没把它发上来 —— 三种都长这样，**分不出是哪一种**，
#: 所以这一句**只说不许猜的那部分**：这一屏现在没有这一单的原因。）
#: ⚠️ 它**不是**错误：`fetch_diag` 回空数组是「量到了、没有」，与「量不到」（抛）是两件事。
NO_DIAG_SAY = ("这一单**还没有原因行** —— 这是**量到的**结果（不是「没查到」）。"
               "FMR 上「原因」这张表是刚接上的：老单不会有，"
               "跑那一趟的机器也可能没把它发上来（这三种在这一屏上长得一样，**分不出**）。")

#: 那三种输入都认：`datetime` / `2026-09-19` / 量过的那一串 `2026-09-19 00:00:00`。
_ACCEPTED_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
#: 出去的那一种形状 —— **量过的那个**（空格分隔）。名字里带 `T` 的那个没量过。
_OUT_FORMAT = "%Y-%m-%d %H:%M:%S"


# ─────────────────────────── 「量不到」那三种 ───────────────────────────


class FmrUnmeasured(RuntimeError):
    """**没量着** —— 与「没有失败」是两件事（这一层所有异常的根）。

    ⚠️ 它的类型本身就是判据：`except FmrUnmeasured` 抓住的是「这一次读不成」，
    而一个空 list 才是「读了、没有」。调用方**不许**把前者写成后者。
    """

    def __init__(self, say: str, *, kind: str = ""):
        super().__init__(say)
        self.say = say
        #: 给调用方分流用的机器标签（`no-token` / `unreachable` / `refused`）。
        self.kind = kind


class FmrUnreachable(FmrUnmeasured):
    """连不上 / 超时 / 正文不是 JSON / HTTP 不是 200 —— **一个字节都没读成**。"""

    def __init__(self, say: str):
        super().__init__(say, kind="unreachable")


class FmrRefused(FmrUnmeasured):
    """**后端说的**：信封里那个 `status` 不是 200（400/401/404…）。

    ⚠️ 它照样是「量不到」，**不是**「没有失败」—— 404 的语义是「这个站它不认识」。
    """

    def __init__(self, say: str, *, status: int):
        super().__init__(say, kind="refused")
        self.status = status


class FmrNoToken(FmrUnmeasured):
    """这个部署**没配 token** —— 一个请求都不该发出去。"""

    def __init__(self, say: str):
        super().__init__(say, kind="no-token")


# ─────────────────────────── 时间（那几个默认值）───────────────────────────


def today_midnight(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    """今天 00:00（**本机时区**）—— 契约里 `since` 的默认值就是它。

    为什么要有这个函数：那个「默认是今天 00:00」是个**坑**（要查昨天必须显式给），
    所以它得是**一处**可被量的东西，而不是散在调用点上的 `datetime.now()`。
    """
    now = now or datetime.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def since_text(since: Any, *, now: Optional[datetime.datetime] = None) -> str:
    """把「从什么时候起」写成 FMR 认的那串字。**读不出来就抛**（不悄悄改用默认值）。

    ⚠️ 悄悄改用默认值的那种坏：调用方以为查的是昨天，看到的是**今天那份**，
    而屏幕上什么都不说 —— 它就是「量错了对象」。
    """
    if since is None:
        return today_midnight(now).strftime(_OUT_FORMAT)
    if isinstance(since, datetime.datetime):
        return since.strftime(_OUT_FORMAT)
    raw = str(since).strip()
    if not raw:
        return today_midnight(now).strftime(_OUT_FORMAT)
    for fmt in _ACCEPTED_FORMATS:
        try:
            return datetime.datetime.strptime(raw, fmt).strftime(_OUT_FORMAT)
        except ValueError:
            continue
    raise FmrUnmeasured(
        "「从什么时候起」这串字读不出来：%r。它要么是 `2026-09-19 00:00:00`，"
        "要么是 `2026-09-19` —— 服务**不会**替你改用默认值（那样你会以为查的是昨天，"
        "看到的却是今天那份）。" % (since,), kind="bad-since")


# ─────────────────────────────── 人话 ───────────────────────────────


def _word(value: Any, table: dict, what: str) -> str:
    """一个代号 → 人话。表里没有就**如实说没有**（不猜、也不悄悄吞掉）。

    三种情形分得开：
      · 有说法 → 那个说法；
      · 它没给（空 / 缺那一格）→ 「它没给」；
      · 它给了但这一屏不认识 → 「它写的是「××」（这一屏不认识这个代号）」。
    ⚠️ 第三种**必须把那串原样的字带上**：不带的话，读的人既不知道是什么，
       也没法拿去问 —— 那就成了「静默」。
    """
    code = "" if value is None else str(value).strip()
    if not code:
        return "%s它没给" % what
    hit = table.get(code.upper()) or table.get(code)
    if hit:
        return str(hit)
    return "%s它写的是「%s」（这一屏不认识这个代号）" % (what, code)


def _when_say(created_at: Any) -> str:
    """`2026-09-19T10:21:38+08:00` → `9-19 10:21`（**照它自己那个时区**，不换算）。

    为什么不换算成别的时区：那一串是**这一趟发生的那一刻**，运营脑子里的也是那一刻。
    换算会把「9-19 10:21」变成另一个数，而屏幕上没有任何东西提示被换过。
    读不出来 → **明说**，并把原样那串字带上（编一个时刻是最坏的：它像证据）。
    """
    raw = "" if created_at is None else str(created_at).strip()
    if not raw:
        return "时刻它没给"
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        at = datetime.datetime.fromisoformat(text)
    except ValueError:
        return "时刻读不出来（它写的是「%s」）" % raw
    return "%d-%d %02d:%02d" % (at.month, at.day, at.hour, at.minute)


def failure_say(row: dict) -> str:
    """一行的**人话**：`9-19 10:21 失败 · 美国 · 站点专属`。

    ⚠️ 这一段是**给运营读的**（Console 上那一栏）—— 里面不许出现
    `type: site_specific` 这种码。表里没有的代号走 `_word` 那条路（如实说不认识）。
    """
    row = row if isinstance(row, dict) else {}
    return "%s %s · %s · %s" % (
        _when_say(row.get("created_at")),
        _word(row.get("status"), STATUS_SAY, "结果"),
        _word(row.get("country"), COUNTRY_SAY, "国家"),
        _word(row.get("type"), TYPE_SAY, "类型"),
    )


def step_say(step: Any) -> str:
    """一行 `step` 的**人话**（那一格是产物自己起的名，认不出就如实说）。"""
    code = "" if step is None else str(step).strip()
    if not code:
        return "这一步它没记是什么"
    if code.startswith("entry:"):
        # 生产里真有过这个形状（`report_config_site`：`step=f"entry:{config_site}"`）——
        # 它不是「一步」，是「这一趟跑的是哪份配置」，所以单独说。
        return "这一趟跑的是「%s」那份配置" % code[len("entry:"):]
    hit = STEP_SAY.get(code) or TERMINAL_SAY.get(code, (None, None))[0]
    if hit:
        return str(hit)
    return "它记的是「%s」（这一屏不认识这个词）" % code


def _is_start(row: dict) -> bool:
    """这一行是不是那一步「进站」（`start` 是 `STATES` 里第一组的名字）。

    ⚠️ 只有字面的 `start` 算 —— **不认「以 start 开头」**（那会把 `start_over`
    这种没见过的名字吞进「没进去过」那一档）。
    """
    return str((row or {}).get("step") or "").strip() == "start"


def _row_url(row: dict) -> str:
    return str((row or {}).get("url") or "").strip()


def _names_say(names: List[str]) -> str:
    """几个状态名 → 人话里那一串（`「a」「b」×2`）。

    两条规矩，各有各的理由：
      · **连着重复的**折成 `×2` —— `_rpt_if_moved` 是按**步**报的，同一个状态组里
        两步都挪了页就会报两次（实测 `26015658` 就是 `auto` `auto`）。
        原样念两遍像打错字；折起来既不丢「它在这一组做两次」这件事，也读得下去。
      · 太长的**说清还有几个**（不悄悄截断 —— 这是这一片的老规矩）。
    """
    runs: List[str] = []
    for n in names:
        if runs and runs[-1][0] == n:
            runs[-1][1] += 1
        else:
            runs.append([n, 1])
    show = runs[:8]
    said = "".join("「%s」%s" % (n, ("×%d" % c) if c > 1 else "") for n, c in show)
    if len(runs) > len(show):
        said += "……（一共 %d 个状态）" % len(runs)
    return said


def where_it_stopped(steps: List[dict]) -> str:
    """★★ 这一趟**走到哪、然后怎么样** —— `formStep` 那几行能推出的**第一手事实**。

    这一句是诊断真正要的东西。为什么不是「把那几行摆出来」：那些行是
    `[{id,task_id,step,url},…]`，`step` 那一列也不是流水账 —— 它是**产物里状态组的名字**
    （`start` / `auto_warranty` / `auto` / `auto-2` / …），最后一行才是「然后怎么样」。

    **只推得出的事实**（每一条都有实测样例钉着，见 `tests/test_fmr.py`）：

    | 形状 | 推出来的话 |
    |---|---|
    | 终点是 `stuck`/`stalled`，且进过状态 | 「走到了「X」这一步，然后卡住/原地打转了」+ 那时页面在哪 |
    | 终点是 `no_success` 一类，且进过状态 | 「进过「a」「b」，能走的都走完了，成功那一页从头到尾没出现」 |
    | **终点之前只有 `start`** | 「**一个状态都没进去** —— 每一组步骤的判据都被静默跳过了」 |
    | 终点是 `success`（却在失败列表里） | 「这两件事对不上，照实摆着」—— 不挑一个当结论 |
    | 最后一行**不是**认得的终点 | **不硬套**：照实说它报的是什么 |
    | 一行都没有 | 「没有留下逐步记录」，**不编** |

    ⚠️ **判据是「终点是哪一个」，不是「一共几行」** —— 这两件事会分岔，实测过：
    生产日志 `/opt/skills/auto-farm-skill/logs/gowizard.log` 里 `26015658`
    是**5 行**却以 `no_success` 收尾
    （进过 `auto_warranty`/`auto`），而 `26034602` 也是 5 行但以 `stuck` 收尾。
    **同一个行数、两种完全不同的病** ⇒ 按行数分类会读错一半。
    """
    rows = [r for r in (steps or []) if isinstance(r, dict)]
    if not rows:
        return ("这一趟**没有留下逐步记录**（它的逐步账是空的）—— 卡在哪一步，"
                "这份账上没写，别猜。")

    last = rows[-1]
    code = str(last.get("step") or "").strip()
    terminal = TERMINAL_SAY.get(code)
    walked = rows[:-1] if terminal else rows
    entered = [str(r.get("step") or "").strip() for r in walked if not _is_start(r)]
    #: 停下的那一刻页面在哪：**终点那一行**记的那一个（`_rpt` 报的是当下的 URL）；
    #: 它没记就退到最后一个进过的状态那一行，再没有就**不给**（不编一个）。
    stop_url = _row_url(last) or (_row_url(walked[-1]) if walked else "")

    if terminal is None:
        if code in ("", "start"):
            return ("这一趟只报了「%s」就没了 —— **它没有报终点**（最后一行的值是「%s」，"
                    "不是任何一个认得的收尾）。所以「它走到哪」这件事**这份账答不了**，"
                    "别替它推一个。" % (code or "空", code or "空"))
        return ("这一趟账上最后一行报的是「%s」—— **这一屏不认识这个值**（它可能是一种新的"
                "收尾方式），所以**不敢说**它走到哪了；最后那一页在 %s"
                % (code, stop_url or "（这一行也没记网址）"))

    word, why = terminal
    if code == "success":
        return ("⚠️ 这一趟账上最后一行写的是**成功** —— 可它出现在**失败列表**里。"
                "这两件事对不上，照实摆着，别按任何一种解释往下走。")
    if not entered:
        return ("这一趟**一个状态都没进去** —— 只报了「start」就直接「%s」"
                "（每一组步骤的判据都被静默跳过了：页面对不上任何一组 `when`）。" % code)
    if code in ("stuck", "stalled"):
        return ("这一趟走到了「%s」这一步，然后**%s**了（%s）；那时页面在 %s。"
                % (entered[-1], word, why, stop_url or "（它没记网址）"))
    return ("这一趟进过 %s，然后**%s**（%s）；最后那一页在 %s。"
            % (_names_say(entered), word, why, stop_url or "（它没记网址）"))


def entry_url(steps: List[dict]) -> str:
    """这一趟**真从哪儿进去的**：第一个带网址的那一步。

    一步都没带网址 → `""`（调用方据此**不去动**那一格，也不编一个）。
    """
    for row in steps or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            return url
    return ""


def evidence_text(row: dict, steps: List[dict]) -> str:
    """★ **`POST /run` 那个 `evidence` 字段要的东西** —— 两截，都是人话。

    ① **抬头**：哪个单 / 哪个站 / 什么时候失败的（这一趟是谁）；
    ② **`where_it_stopped` 那一句**：它**走到哪、然后怎么样** —— 诊断真正要的第一手事实。

    ⚠️ **不摆那几行原始账**（任务书 2026-09-20 的追加实测点名这件事）：
    `[{id,task_id,step,url},…]` 不是人话，而 `step` 那一列也不是流水账 ——
    它是**产物里状态组的名字**。把它当成「第 1 步 / 第 2 步」摆出来，
    读的人会以为那是一份**走路的清单**。

    ⚠️ 这一改**同时推翻了设计注 §2.3.2 的一个前提**：那里说
    「formStep 那类证据**本来就是带步骤序号的**，`plan.parse()` 收它」——
    **那个前提被实测否掉了**（那几行里没有「步骤」这回事）。
    所以这一版**不再**把它拼成编号行：`plan.parse(evidence)` 现在**解析不出步骤**
    （`tests/test_fmr.py::test_the_evidence_no_longer_hands_the_plan_parser_a_walk` 钉着）——
    让修站那条路拿到一份**编出来的计划**，比让它空着手更坏。
    """
    row = row if isinstance(row, dict) else {}
    task_id = str(row.get("task_id") or "").strip() or "（它没给单号）"
    site = str(row.get("site") or "").strip()
    head = "FMR 单 %s" % task_id
    if site:
        head += "：%s" % site
    head += " 这一趟 %s（%s）。" % (
        _word(row.get("status"), STATUS_SAY, "结果"), failure_say_tail(row))
    return head + "\n" + where_it_stopped(steps)


def failure_say_tail(row: dict) -> str:
    """抬头后面那个括号里的那几样（`9-19 10:21 · 美国 · 站点专属`）。"""
    return "%s · %s · %s" % (_when_say(row.get("created_at")),
                             _word(row.get("country"), COUNTRY_SAY, "国家"),
                             _word(row.get("type"), TYPE_SAY, "类型"))


# ──────────────── ③ 榜单 + ④ 原因：人话（Task 4）────────────────


def _int_or_none(value: Any) -> Optional[int]:
    """一个数 → `int`；读不出来给 `None`（**不给 0** —— 0 是一个合法的读数，混起来就是编话）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def exit_say(value: Any) -> str:
    """④ `exit` 那一格 → 人话。★ **`unknown` → 「没报上来」**，不许编成那三种之一。

    ⚠️ 这句话的分量在**客户端那一侧的注释**上（`ad-task.py:1265`）：
    它**有意**在认不出收尾时报 `unknown`，理由是「硬塞成三个已知值之一会让查的人**看错原因**」。
    这一屏要是在这儿把它折算成 `stuck`，就等于把客户端**刻意留下**的那个空白又填死了 ——
    而填进去的那个字是**这一屏编的**，不是任何一台机器说的。
    """
    return _word(value, EXIT_SAY, "结束方式")


def has_script_say(value: Any) -> str:
    """③ `has_script` 那一格 → 人话。**它是三态，不是两态**（后端那三条各有各的处置）。

    | 值 | 人话 | 运营该做的事 |
    |---|---|---|
    | `True` | 有 py 脚本 | 不用管 |
    | `False` | 有配置，但**没有脚本** | **去写脚本**（这份配置跑不起来） |
    | `None` | 没有配置，谈不上 | **去建配置**（连配置都没有） |

    ⚠️ 把后两行合成一句「没有脚本」是本接口**专门防**的那个形状：
    「去建配置」与「去写脚本」是两件不同的事，在屏幕上长成一句，运营就会做错那一步。
    """
    if value is None:
        return "有没有脚本：没有配置，谈不上"
    if value is True:
        return "有 py 脚本"
    if value is False:
        return "有配置，可是没有脚本"
    return "有没有脚本：它写的是「%s」（这一屏不认识的写法）" % (value,)


def rank_row_say(row: dict) -> str:
    """③ 榜单里一行的**人话**：`失败 3 次 · 配置 66（启用）· 有 py 脚本`。

    ⚠️ **站点键不在这一句里**：它是这一行的身份，由调用方**原样**摆在最前面
    （运营要拿它去别处问）。这一句只说**这一行该怎么读**。
    ⚠️ 没有配置时**照抄后端那句 `note`**，不在这一层合成「没有配置」——
    后端的两种 `note`（`没有配置` / `映射指向的配置行不存在`）处置**完全相反**
    （一个去建、一个去修映射），合起来就是把两件事变成一件。

    ★★ **这一句里一个 `**` 都不许有。** 这一族里**只有它**要同时进 `<li>` 与
    **`<option>`**（页面上它既是「读的那一行」、也是「挑的那一格」）——
    而 `<option>` 里放不了标记：带 `**` 的句子在那儿的可见结果就是**两个星号**
    （本仓修复轮 1 在截图里真看见过这个形状：「漏一处，运营看到的就是两个星号」）。
    强调靠**措辞**（「可是没有脚本」），不靠标记。
    `tests/test_fmr.py::test_the_rank_row_text_carries_no_emphasis_markers` 钉着这一条。
    """
    row = row if isinstance(row, dict) else {}
    parts = []
    n = _int_or_none(row.get("fail"))
    parts.append("失败 %d 次" % n if n is not None
                 else "失败几次它没给（那一格写的是 %r）" % (row.get("fail"),))
    if row.get("config_id") is None:
        note = str(row.get("note") or "").strip()
        parts.append(note if note else "配置它没给，也没说为什么不给")
    else:
        parts.append("配置 %s（%s）" % (row.get("config_id"),
                                     _word(row.get("config_status"),
                                           CONFIG_STATUS_SAY, "它的状态")))
    parts.append(has_script_say(row.get("has_script")))
    return " · ".join(parts)


def rank_say(data: dict, limit: Any = None) -> str:
    """③ 榜单顶上那句人话：**量的是哪一天、量到多少、摆出来的占多少**。

    ⚠️ 三个数都得在，因为**它们对不上是正常的**（后端自己的注释点名过）：
      · `failed_total` = 那一天**全部**失败的行数；
      · `unattributed` = 其中**归不到任何一个站**的（没有匹配键）；
      · `rank` = 按站聚合后**摆出来的**那几个 —— 而 `limit` **只截断它**。
    ⇒ 「摆了 3 个站」与「今天失败了 18 次」**可以同时为真**。只说前者，
      读的人会以为今天就这么几个站坏了；**差额必须说出来**（「没有静默的路径」）。
    """
    data = data if isinstance(data, dict) else {}
    day = str(data.get("date") or "").strip()
    rows = [r for r in (data.get("rank") or []) if isinstance(r, dict)]
    total = _int_or_none(data.get("failed_total"))
    unattr = _int_or_none(data.get("unattributed"))
    shown = sum(n for n in (_int_or_none(r.get("fail")) for r in rows) if n is not None)
    when = ("%s 这一天" % day) if day else "这一天（它没说是哪一天）"

    if total is None:
        head = ("%s：**一共失败了多少次它没给**（那一格写的是 %r）—— "
                "下面摆出来的只是它列的那几个站。" % (when, data.get("failed_total")))
    else:
        head = "%s一共失败 **%d** 次" % (when, total)
        if unattr is None:
            head += "（其中归不到站的有几次，它没给）"
        elif unattr:
            head += "（其中 **%d** 次归不到任何一个站 —— 没有匹配键）" % unattr
        head += "。"

    if not rows:
        if total == 0:
            return head + "一个站都没有 —— 这是**量到的**结果（不是「没量着」）。"
        if unattr is not None and unattr == total:
            return head + "**一个站都归不出来** —— 全部归不到任何一个站。"
        return (head + "可它**一个站都没摆出来** —— 上面那个总数与这一栏对不上，"
                "照实摆着，别读成「今天没有站在失败」。")

    n = _int_or_none(limit)
    cap = ("这一屏一次最多摆 %s 个站。" % n) if n else ""
    tail = "下面摆了 %d 个站" % len(rows)
    gap = None if (total is None or unattr is None) else total - shown - unattr
    if gap is None:
        tail += "（这几个站加起来 %d 次失败）—— 与上面那个总数差多少，这一份算不出来。%s" % (shown, cap)
    elif gap > 0:
        tail += ("（这几个站加起来 %d 次失败）—— ⚠️ **没摆全**：还差 **%d** 次属于没摆出来的站，"
                 "把 `limit` 放大再查（后端上限 %d）。%s" % (shown, gap, RANK_MAX_LIMIT, cap))
    elif gap == 0:
        tail += "（这几个站加起来 %d 次失败，**与上面那个总数对得上**）。" % shown
    else:
        tail += ("（这几个站加起来 %d 次失败 —— ⚠️ 比上面那个总数还多 %d，这两格对不上，"
                 "照实摆着）。" % (shown, -gap))
    return head + tail


def diag_head_say(row: dict) -> str:
    """④ 一条原因行的**抬头**：`失败于 9-20 14:32 · 机器 worker-07 · 结束方式：卡住`。

    ⚠️ 时刻取 `at`（**失败真正发生的那一刻**），**不是** `created_at`（入库时刻）——
    后端自己的注释点过这件事：离线机器晚补发时两者差很多，而查的人要的是「哪天失败的」。
    `at` 没给（老客户端 / 手工发的）才退回入库时刻，**并且要说出退过**
    （不说的话，一个晚补发的班次会让「9-20 收上来的」被读成「9-20 失败的」）。
    ⚠️ `lines` **不在这句里**：那是几十行的正文，调用方单独摆（原样、不加工）。
    """
    row = row if isinstance(row, dict) else {}
    if str(row.get("at") or "").strip():
        when = "失败于 %s" % _when_say(row.get("at"))
    elif str(row.get("created_at") or "").strip():
        when = ("失败时刻它没给 —— 这是 %s **收上来**的时刻（不是它失败的时刻）"
                % _when_say(row.get("created_at")))
    else:
        when = "时刻它没给（失败那一刻与入库那一刻都没有）"
    machine = str(row.get("machine") or "").strip()
    return "%s · 机器 %s · 结束方式：%s" % (
        when, machine or "（它没给是哪台机器跑的）", exit_say(row.get("exit")))


# ─────────────────────────── 客户端 ───────────────────────────

#: 每个读口**读的是什么**（人话里的开头）。四个口一张表 —— 加接口时在这儿补一行，
#: 别在四个 `raise` 里各写各的（那样「说了没有」会变成四件事）。
_PATH_SAY = {
    "/api/quest/formLog": "失败记录",
    "/api/quest/formStep": "逐步记录",
    "/api/quest/formLogRank": "失败榜单",
    "/api/quest/failDiag": "失败原因",
}


def _default_get(url: str, headers: dict, timeout: float = DEFAULT_TIMEOUT) -> str:
    """真发那一个请求（返回正文）。**HTTP 码不是 200 也算「量不到」**，并带上那个码。

    为什么不读状态码的坏处（`fail-script` 的注释里记着同一个坑）：429/5xx 的非 JSON
    响应会以 `JSONDecodeError` 的面目冒出来，看不出是「被限速了」。
    """
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 —— 地址是配置里定的
        return resp.read().decode("utf-8", "replace")


class FmrClient:
    """走 FMR 那两个只读接口的那个客户端。

    `token` / `base` 在**构造那一刻**读一次环境（`None` ⇒ 读环境；`""` ⇒ 明确「没有」）——
    与 `Service.__init__` 里 `_cdp_bin` / `_shots_dir` / `_selftest_root` 同一条不变量：
    **构造时定死，之后不再看环境**（不然同一个进程里会出现两个不同的后端/身份）。

    `opener` 是注入点：`(url, headers) -> 正文`。测试给它桩，于是这一层不打真网络。
    """

    def __init__(self, token: Optional[str] = None, *, base: Optional[str] = None,
                 opener: Optional[Callable] = None, timeout: float = DEFAULT_TIMEOUT,
                 now: Optional[Callable] = None):
        self.token = (os.environ.get(TOKEN_ENV) or "") if token is None else str(token or "")
        self.base = str(base if base is not None else
                        (os.environ.get(BASE_ENV) or DEFAULT_BASE)).rstrip("/")
        self.timeout = float(timeout)
        self._opener = opener or _default_get
        #: 「现在」怎么取（测试注入一个固定的时钟 —— 默认那条 `since` 是量得出的）。
        self._now = now or datetime.datetime.now

    # ── 报给 `/health` 的那一句（**不许带 token 本身**）────────────────
    @property
    def configured(self) -> bool:
        return bool(self.token)

    def say(self) -> str:
        if self.configured:
            return ("失败记录从 %s 读（这个部署配了读取用的 token）—— "
                    "面板上「查失败」按下去就能用。" % self.base)
        return ("**这个部署没配 %s**，所以面板上「查失败」按下去只会红一次 —— "
                "那是「量不到」，不是「没有失败」。"
                "配法：给服务进程一个 `%s` 环境变量（与 `DATABASE_URL` 同一层）。"
                % (TOKEN_ENV, TOKEN_ENV))

    # ── HTTP ──────────────────────────────────────────────────────
    def _what(self, path: str) -> str:
        """这个读口**读的是什么**（出错的人话用它开头，而不是把接口路径摆在最前）。"""
        return _PATH_SAY.get(path, path)

    def _unreadable_say(self, path: str, exc: Exception) -> str:
        """★ **发出去之后没读成** → 人话。分两种，因为**下一步不是一件事**：

        · 有 HTTP 码（`HTTPError`）—— 请求**到了后端**，是**它**按 HTTP 回了个码。
          重点：这一族的正常形状是「**HTTP 恒 200**，码放在 body 里」（契约那张表），
          所以按 HTTP 回码说明**不是接口在说话** —— 是它前面的那一层（路由 / 网关）。
        · 没有码（DNS 失败 / 连不上 / 超时 / TLS…）—— 一个字节都没到。

        ⚠️ **404 那一支要点名**「**这个接口在这个后端上不存在**」——
        这不是抠字眼：`GET /api/quest/failDiag` 就是一个真实例子（线上还没部署，
        见模块 docstring 那个旋钮）。把它说成「连不上那个后端」，
        读的人会去查网络 / 代理 / 防火墙，而真因是**那个路由没上**。
        """
        noun = self._what(path)
        status = getattr(exc, "code", None)
        if status is None:
            return ("读不了%s：连不上那个后端（%s）。%s。"
                    "（真因：%s: %s）"
                    % (noun, self.base, UNMEASURED_SAY, type(exc).__name__, exc))
        extra = ""
        if int(status) == 404:
            extra = ("—— ⚠️ 这**不是**「这台机器连不上」：请求到了后端，"
                     "是**那个地址在它那儿没有**。多半是这个接口**还没部署到 %s**"
                     "（这一族接口是分开上的，不是一次上齐的）。" % self.base)
        elif int(status) in (401, 403):
            extra = ("—— 后端的**网关那一层**先拦了（这一族的 401 正常是 body 里那个码，"
                     "不是 HTTP 码）：那串 token 对不上，或者根本没到业务层。")
        return ("读不了%s：**请求到了后端，可它按 HTTP 回了 %s**（不是在读这件事上给了答复）。"
                "%s%s（真因：%s: %s）"
                % (noun, status, extra, UNMEASURED_SAY, type(exc).__name__, exc))

    def _call(self, path: str, params: dict, *, shape=(list, dict)) -> Any:
        """一次读。**失败一律抛**（`FmrUnmeasured` 的三个子类），**绝不返回空**。

        `shape` 是 `data` 那一格**该长成什么样**（Task 4 加的）。默认 `(list, dict)` = 老行为
        （`data: null` / 少一格 → 空数组）。

        ★ 为什么 ③ 榜单要传 `shape=dict`：那个接口**没有失败的那一天回的也是一个对象**
        （`failed_total:0` / `rank:[]`）⇒「回的不是对象」只可能是**这一次没量着**。
        不传的话，一次形状事故会以 `200 {"rank": …}` 的面目变成「今天没有站在失败」——
        而那正是这一层从头到尾在治的那句话。
        """
        if not self.configured:
            raise FmrNoToken(
                "读不了%s：这个部署**没配 %s**（一个请求都没发出去）。"
                "%s。" % (self._what(path), TOKEN_ENV, UNMEASURED_SAY))
        url = "%s%s?%s" % (self.base, path, urllib.parse.urlencode(params))
        # ⚠️ token 走**请求头**，绝不进 URL（URL 会进日志、进 `ps`）。
        headers = {"X-Api-Token": self.token}
        try:
            raw = self._opener(url, headers)
        except Exception as exc:                       # noqa: BLE001 —— 什么都算「量不到」
            raise FmrUnreachable(self._unreadable_say(path, exc)) from exc
        try:
            body = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise FmrUnreachable(
                "读不了失败记录：后端回的正文不是 JSON（读了 %d 个字节）。%s。"
                % (len(raw or ""), UNMEASURED_SAY)) from exc
        if not isinstance(body, dict):
            raise FmrUnreachable(
                "读不了失败记录：后端回的正文不是一个信封（%s…）。%s。"
                % (str(body)[:60], UNMEASURED_SAY))
        status = body.get("status")
        if status != 200:
            raise FmrRefused(self._refused_say(path, params, body, status), status=int(status or 0))
        data = body.get("data")
        if not isinstance(data, shape):
            raise FmrUnreachable(
                "读不了 %s：后端回的信封对得上（`status:200`），可 `data` 那一格**不是要的那种"
                "形状**（读回来的是 %s…）。%s。" % (path, str(data)[:60], UNMEASURED_SAY))
        return data

    @staticmethod
    def _refused_say(path: str, params: dict, body: dict, status: Any) -> str:
        """后端自己回的那个码 → 人话。**404 那一句要点到 key 上**。

        404 的语义是「**这个站它不认识**」（`fail-script` 的注释：真实 key 形如
        `主机+路径`）。那一次它被显示成「近 2 天没有失败 ✓」——
        一个查不到的站被标成健康的。所以这一句必须让人**照着去改那串 key**。
        """
        noun = _PATH_SAY.get(path, path)
        msg = str(body.get("msg") or "").strip()
        tail = ("后端自己那句是：「%s」。" % msg) if msg else "后端没给它那句说明。"
        if int(status or 0) == 404:
            # ⚠️ 404 的语义**按查的是什么分岔**：按站查时它是「这个站它不认识」，
            # 按单号查时它是「这个单它那儿没有」。合成一句就会让按单号那次
            # 给人一句「那串名字（站名）给错了」——而那次根本没有站名这回事。
            if params.get("site"):
                key = str(params.get("site"))
                return ("读不了%s：**那个站它不认识**（后端回 404）—— "
                        "多半是这一串名字给错了：%r（真实的名字形如 `主机名/路径`，"
                        "带上路径那一截）。%s %s" % (noun, key, UNMEASURED_SAY, tail))
            key = str(params.get("task_id") or "")
            return ("读不了%s：**这个单它那儿没有**（后端回 404）—— "
                    "多半是单号给错了：%r。%s %s" % (noun, key, UNMEASURED_SAY, tail))
        if int(status or 0) == 401:
            return ("读不了%s：**鉴权没过**（后端回 401）—— 那串 token 不对或过期了。"
                    "%s %s" % (noun, UNMEASURED_SAY, tail))
        return ("读不了%s：后端回的是 %s（不是在读这件事上成功）。%s %s"
                % (noun, status, UNMEASURED_SAY, tail))

    # ── ① 失败列表 ─────────────────────────────────────────────────
    def fetch_failures(self, site: str, since: Any = None, limit: Any = None) -> List[dict]:
        """这个站在 `since` 之后失败的每一趟。**倒序**（后端给的顺序，不重排）。

        ⚠️ **空 list = 真量了、真没有**。任何一种「没量着」都抛（见模块 docstring 那张表）。
        """
        key = str(site or "").strip()
        if not key:
            raise FmrUnmeasured(
                "读不了失败记录：没说是**哪个站** —— 这是免费的检查（一个请求都没发出去）。",
                kind="no-site")
        n = DEFAULT_LIMIT if limit in (None, "") else int(limit)
        data = self._call("/api/quest/formLog", {
            "site": key,
            "status": "failed",
            "since": since_text(since, now=self._now()),
            "limit": n,
        })
        return [r for r in (data or []) if isinstance(r, dict)]

    # ── ② 逐步记录 ─────────────────────────────────────────────────
    def fetch_steps(self, task_id: Any) -> List[dict]:
        """这一趟报过的每一步。**升序**（后端给的顺序，不重排）。空 list = 真的一步都没有。"""
        key = str(task_id or "").strip()
        if not key:
            raise FmrUnmeasured(
                "读不了逐步记录：没说是**哪个单** —— 这是免费的检查（一个请求都没发出去）。",
                kind="no-task")
        data = self._call("/api/quest/formStep", {"task_id": key})
        return [r for r in (data or []) if isinstance(r, dict)]

    # ── ③ 榜单：这一天哪些站在失败 ───────────────────────────────────
    def fetch_rank(self, date: Any = None, limit: Any = None) -> dict:
        """③ **这一天哪些站在失败**（按失败数倒序，并列按站点名字节序 —— 后端那边定的，不重排）。

        ⚠️ 回的是后端那个**对象**：`{date, failed_total, unattributed, rank:[…]}`。
        `data` 那一格不是一个对象 ⟹ **抛**（见 `_call` 的 `shape` 那段）——
        那一天没有失败时后端回的**照样是这个对象**（`failed_total:0` / `rank:[]`），
        所以「形状不对」只可能是「这一次没量着」，**不许**读成「今天没有站在失败」。
        """
        params: dict = {}
        day = str(date or "").strip()
        if day:
            params["date"] = day
        #: ⚠️ 不给就**整格不发**（缺省 = 后端那边的「今天」）。发一个空串不是缺省：
        #: 后端对它回 400（`?limit=` 经 ConvertEmptyStringsToNull 就是 null，同一个坑）。
        if limit not in (None, ""):
            params["limit"] = int(limit)
        return self._call("/api/quest/formLogRank", params, shape=dict)

    # ── ④ 原因：这一单为什么失败 ─────────────────────────────────────
    def fetch_diag(self, task_id: Any) -> List[dict]:
        """④ 这一单在 FMR 上留着的**原因行**（按入库时刻倒序，第一行 = 最近一次的现场）。

        ⚠️ **空 list = 真量了、这单还没有原因行**（成因见 `NO_DIAG_SAY`：新功能 / 老单 /
        那台机器没发上来）—— 它不是「量不到」，但调用方**也不许**把它画成一片空白。
        """
        key = str(task_id or "").strip()
        if not key:
            raise FmrUnmeasured(
                "读不了失败原因：没说是**哪个单** —— 这是免费的检查（一个请求都没发出去）。",
                kind="no-task")
        data = self._call("/api/quest/failDiag", {"task_id": key}, shape=list)
        return [r for r in (data or []) if isinstance(r, dict)]

    # ── ★ 一段人话（`POST /run` 的 `evidence`）─────────────────────
    def evidence_for(self, task_id: Any, *, site: str, since: Any = None) -> dict:
        """把「这一趟怎么了」拼成一段人话，外加**该填进「站点网址」的那一串**。

        ⚠️ **两样都从后端读**（先 formLog 拿这一趟的站/时刻，再 formStep 拿那几步）——
        调用方（页面）一个字都不许往里塞：证据里的每一个字都得是后端的。

        ⚠️ 这一单**不在那个时间窗里** → **明说**（不拿第一条顶替 —— 顶替会
        把**另一趟**的逐步记录拼成这一趟的证据）。
        """
        key = str(task_id or "").strip()
        rows = self.fetch_failures(site, since=since, limit=DEFAULT_LIMIT)
        row = None
        for r in rows:
            if str(r.get("task_id") or "").strip() == key:
                row = r
                break
        if row is None:
            raise FmrUnmeasured(
                "读不了这一段证据：单号 %s **不在**你量的那个时间窗里的失败列表上"
                "（这个窗口里一共 %d 条）。把时间窗放大一点再试 —— "
                "服务**不会**拿别的单顶替它（那会把另一趟的账拼成这一趟的证据）。"
                % (key or "（空的）", len(rows)), kind="not-in-window")
        steps = self.fetch_steps(key)
        return {
            "task_id": key,
            "row": row,
            "row_say": failure_say(row),
            "steps": steps,
            "url": entry_url(steps),
            "evidence": evidence_text(row, steps),
        }
