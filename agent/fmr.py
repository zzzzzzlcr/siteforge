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
]

#: 线上实测过的那一个。`FMR_BASE_URL` 只是给测试/灰度留的口子 —— **默认值就是它**。
DEFAULT_BASE = "https://fmr.3tkj.cn"
#: token 从哪儿来。**绝不写死在代码里、绝不进 git**（没配就明说，见 `FmrClient`）。
TOKEN_ENV = "FMR_AGENT_TOKEN"
#: 改后端地址的口子（默认值不变）。
BASE_ENV = "FMR_BASE_URL"

#: 一次最多摆几条。**摆满了要说**（「可能还有更早的」）—— 悄悄截断就是「没有静默的路径」的反面。
DEFAULT_LIMIT = 20
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


# ─────────────────────────── 客户端 ───────────────────────────


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
    def _call(self, path: str, params: dict) -> Any:
        """一次读。**失败一律抛**（`FmrUnmeasured` 的三个子类），**绝不返回空**。"""
        if not self.configured:
            raise FmrNoToken(
                "读不了失败记录：这个部署**没配 %s**（一个请求都没发出去）。"
                "%s。" % (TOKEN_ENV, UNMEASURED_SAY))
        url = "%s%s?%s" % (self.base, path, urllib.parse.urlencode(params))
        # ⚠️ token 走**请求头**，绝不进 URL（URL 会进日志、进 `ps`）。
        headers = {"X-Api-Token": self.token}
        try:
            raw = self._opener(url, headers)
        except Exception as exc:                       # noqa: BLE001 —— 什么都算「量不到」
            raise FmrUnreachable(
                "读不了失败记录：连不上那个后端（%s）。%s。"
                "（真因：%s: %s）" % (self.base, UNMEASURED_SAY, type(exc).__name__, exc)) from exc
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
        return data if isinstance(data, (list, dict)) else []

    @staticmethod
    def _refused_say(path: str, params: dict, body: dict, status: Any) -> str:
        """后端自己回的那个码 → 人话。**404 那一句要点到 key 上**。

        404 的语义是「**这个站它不认识**」（`fail-script` 的注释：真实 key 形如
        `主机+路径`）。那一次它被显示成「近 2 天没有失败 ✓」——
        一个查不到的站被标成健康的。所以这一句必须让人**照着去改那串 key**。
        """
        msg = str(body.get("msg") or "").strip()
        tail = ("后端自己那句是：「%s」。" % msg) if msg else "后端没给它那句说明。"
        if int(status or 0) == 404:
            key = str(params.get("site") or params.get("task_id") or "")
            return ("读不了失败记录：**那个站它不认识**（后端回 404）—— "
                    "多半是这一串名字给错了：%r（真实的名字形如 `主机名/路径`，"
                    "带上路径那一截）。%s %s" % (key, UNMEASURED_SAY, tail))
        if int(status or 0) == 401:
            return ("读不了失败记录：**鉴权没过**（后端回 401）—— 那串 token 不对或过期了。"
                    "%s %s" % (UNMEASURED_SAY, tail))
        return ("读不了失败记录：后端回的是 %s（不是在读失败列表这件事上成功）。%s %s"
                % (status, UNMEASURED_SAY, tail))

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
