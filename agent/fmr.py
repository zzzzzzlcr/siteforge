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

## 这一层现在有五个读口 + 一个写口

| 口 | 答的是 |
|---|---|
| `fetch_failures(site)` | **这个站**失败了哪几趟 |
| `fetch_steps(task_id)` | 那一趟报过哪几步 |
| `fetch_rank(date)` | **今天哪些站**在失败（榜单）—— Task 4 加的 |
| `fetch_diag(task_id)` | **这一单为什么**失败（原因行）—— Task 4 加的 |
| `form_config(site)` | **这个站的 JSON 配置**长什么样 —— Task B1 加的 |
| `update_form_config(site, steps)` | **把一份 JSON 配置写回去** —— Task B1 加的（唯一一个写口） |

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
   ⚠️ `rank[].site` 是**展示用的键，不是干净的站点键**：线上实测过它带 query、带尾斜杠、
      甚至粘着一段别人的报错。
      ⚠️ **但「脏」不等于「查不到」** —— 这一格我原先写宽了（Task 4 复审订正）：
      【复审量的·出处 `task-4-review.md` §三②】拿榜单里**每一个**真键去打 `formLog`，
      那条 1430 字符、带 query 又粘着报错的键**查得到**（200 + 6 行，后端把它归到 config 66 上了）；
      真正查不到的是**另一条** —— 榜单上那条**没有配置**的键（后端回业务码 404）。
      ⇒ 所以这一层的判据只能是「**查得到就摆，查不到就如实说查不到**」，
      **不许**替它预设是哪一种；而**要办事一律走 `task_id`**（Task 4 的三条硬要求 R1）。
④ GET {base}/api/quest/failDiag?task_id=<单号>                X-Api-Token: 同上
 → {"status":200,"msg":"success","data":[
      {"id":83,"task_id":99999999,"site":"…","machine":"…","exit":"stuck",
       "lines":"…那几行日志…","at":"…","created_at":"…"}]}
   按 `created_at` **倒序**（第一行 = 最近一次的现场）；`task_id` 回来是**数字**（库里是 int）。
   ⚠️ **空数组 = 「量到了，这单还没有原因行」**（新功能 / 老单 / 那台机器没发上来）——
      它不是「量不到」，但屏幕上**也不许留白**（要明说「还没有原因」，Task 4 R2）。
   ⚠️ `exit` 的取值**有意是开放的**（后端刻意不校验），实测客户端会报 `unknown` ⇒ 见 `EXIT_SAY`。

## ⑤ 一份 JSON 配置：**读**（`form_config`）与**写**（`update_form_config`）—— Task B1

后端有 174 个站在跑 JSON 配置（`plan.md` §1 量的），而这一屏原先只认 py。
这两个口把那条路接上：`form_config` 拿回来一份配置、`update_form_config` 写回去一份。

```
读  GET  {base}/api/quest/formConfig?site=<站点键>          （**公开，无鉴权**）
 → {"status":200,"msg":"success","data":{
      "site":"cvrefresh.com",
      "steps":{"form_type":"magic_link","site":"cvrefresh.com",
               "steps":[{"action":"wait","max":10,"min":5}, …],
               "success":{"any":[{"body_contains":["Check your email"]}]}}}}
   ⚠️ **`data.steps` 是个对象**（**里面那份**才是配置），不是数组 —— 这个形状坑过一次
      （`ad-task.py:2157` 那一段注释记着「读错层，把包装层当配置」）。
   ⚠️ `form_config()` 交出去的是**里面那一份**，与 `update_form_config()` 要的 `steps`
      **是同一个东西** ⇒ 读回来就能原样写回去（`update_form_config(k, form_config(k))`）。
   ⚠️ 查不到时：`{"status":404,"msg":"config not found","data":[]}`（**HTTP 也是 200**）。
写  POST {base}/api/quest/formConfig/update
      Header: X-Api-Token: <token>
      Body:   {"site":"<站点键>","steps":<那份 {form_type,site,steps[],success}>}
 → 缺 site / 缺 steps：{"status":400,"msg":"参数错误: site 与 steps 必填"}
   不带 token：        {"status":401,"msg":"unauthorized"}
```

★★ **这一族一律 HTTP 200 + 业务码写在 body 里。** 【我量的·B1】
`POST {base}/api/quest/formConfig/update` **不带 token** →
**HTTP 200** + `{"status":401,"msg":"unauthorized","data":[]}`；
`GET {base}/api/quest/formConfig?site=definitely-not-a-site-b1probe.example` →
**HTTP 200** + `{"status":404,"msg":"config not found","data":[]}`。
⇒ **按 HTTP 码做验收会全部误判成失败**；判据只能是 body 那一格 `status`。

⚠️ **这个坑在本仓有案底**（这条是**出处**，不是我的经历）：
`/opt/skills/auto-farm-skill/scripts/ad-task.py` 的 `_fail_diag_verdict()` 段注释记着
老代码按 HTTP 码判的后果 —— token 不对时服务端回 `HTTP 200 + {"status":401}`，
`urlopen` **不抛**，于是走进成功那一支、打一行「已上传」，而日志一条都没进库；
注释的原话是「**说假话比不打日志更坏**」。
⇒ `update_form_config` 的判据**只有** body 的 `status`，`ok` 只在它等于 200 时为真。

⚠️ **这一对不对称，是照 brief §2 R3 定的**（写「不抛」、读照旧抛）：
- **读**：**抛**（`FmrUnmeasured` 那三个子类 —— 与 ①②③④ 同一个形状）。
  「查不到」= `FmrRefused`（`status` 是 404，`kind="refused"`）；
  「后端挂了」= `FmrUnreachable`（`kind="unreachable"`）—— **两件处置完全不同的事**，
  在这一层是一条**类型**上的区分（与模块开头那张表同源）。
- **写**：**回一个值**（`FormWriteResult`，五态 `verdict`）。
  最要紧的那一态是 **`unknown`（不知道成没成）**：超时 / 正文不是 JSON / HTTP ≥400
  **都不许**写成「没写进去」—— 那份配置**可能已经动了**，这是写与读最不一样的地方。

## ★ 后端地址：**一处旋钮**（Task 4 §4）

`DEFAULT_BASE` ／ 环境变量 `FMR_BASE_URL`（常量名 `BASE_ENV`）—— **四个读口全走它这一个**，
在 `FmrClient.__init__` 里读**一次**（构造之后不再看环境）。
**没有「按接口各配一个基址」这回事** —— 真长出来就是「名字说 A、量的是 B」那个老病的新变种。
（Task B1 的两个口**也走这一个**：读配置是同一个 `self.base`，写回的 URL 是
`self.base + FORM_CONFIG_UPDATE_PATH` —— 没有第二个基址旋钮。）

⚠️ **这条旋钮的来历，以及它今天的状态（Task 4 修复轮 1 订正）**：
它当初存在，是因为两边部署**不同步** —— 线上 `fmr.3tkj.cn` 那时**还没有** `failDiag`（brief 实测 404），
本地 `192.168.1.51:6060` 有。
**那两个事实今天都过期了**：
· 【我量的·修复轮 1】`GET https://fmr.3tkj.cn/api/quest/failDiag` → **HTTP 200**
  （正文是 `{"status":401,"msg":"unauthorized","data":[]}` —— 没带 token；不存在的路由是 **HTTP 404**
  ⇒ 这不是「404 页面」，**路由在**）；
· 【复审量的·出处 `task-4-review.md` §三】本地实例那条测试行（`task_id=99999999` / `id=83`）
  已经**不在了**（五种查法全是 `data: []`）。
⇒ **旋钮留着**（多机部署仍然要它，而且它现在是四个口唯一的那一个），
但**别再拿「线上没有」当它存在的理由** —— 那个理由今天不成立。

⚠️ 这一层**不打真模型、不开浏览器**；`opener` 是注入的口子（测试给它桩）。
"""
from __future__ import annotations

import datetime
import functools
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

__all__ = [
    "FmrClient", "FmrUnmeasured", "FmrUnreachable", "FmrRefused",
    "DEFAULT_BASE", "TOKEN_ENV", "BASE_ENV", "DEFAULT_LIMIT", "DEFAULT_TIMEOUT",
    "UNMEASURED_SAY", "failure_say", "step_say", "evidence_text", "entry_url",
    "today_midnight", "since_text",
    # Task 4：榜单（③）与原因（④）
    "DEFAULT_RANK_LIMIT", "RANK_MAX_LIMIT", "DIAG_PAGE_SIZE",
    "EXIT_SAY", "CONFIG_STATUS_SAY", "NO_DIAG_SAY", "int_or_none",
    "exit_say", "has_script_say", "rank_row_say", "rank_say", "diag_head_say",
    # Task B1：一份 JSON 配置的读（⑤）与写
    "FORM_CONFIG_PATH", "FORM_CONFIG_UPDATE_PATH", "CONFIG_TIMEOUT",
    "FormWriteResult", "WRITE_VERDICTS", "business_status", "looks_like_wrapper",
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

# ── ⑤ 一份 JSON 配置：读与写（Task B1）────────────────────────────────────
#: 读一份配置的口（**公开，无鉴权** —— 【我量的·B1】不带任何头去拿它回 200 + 整份配置）。
#: ⚠️ 正因为它是公开的，`form_config` 走 `_call(need_token=False)`：
#: 这个部署**没配 token 也读得动**（写不动 —— 写那个口要鉴权）。
FORM_CONFIG_PATH = "/api/quest/formConfig"
#: 写回一份配置的口（要 `X-Api-Token`）。
FORM_CONFIG_UPDATE_PATH = "/api/quest/formConfig/update"

#: 这两个口的超时（秒）—— **比别的读口短**（`DEFAULT_TIMEOUT` 是 20）。
#: brief §2 R4：这是**给面板用的**，不是批量任务：人按了按钮在等一个答复，
#: 让它挂 20 秒不如早一点说「还没读成」。旋钮在 `FmrClient(config_timeout=…)`。
CONFIG_TIMEOUT = 10.0

#: 写回那一次的**五种结局**（`FormWriteResult.verdict`）。**只有第一种是成功。**
#: ⚠️ 名字与 `ad-task.py` 的 `_fail_diag_verdict()` 那三个（`ok` / `auth` / `fail`）
#: 是一套话：这里把最后那个叫 `refused`（与本模块 `FmrRefused` 同源），
#: 另外多出两态 —— `unknown`（**不知道成没成**）与 `no-token`（**一个请求都没发出去**）。
WRITE_OK = "ok"
WRITE_AUTH = "auth"
WRITE_REFUSED = "refused"
WRITE_UNKNOWN = "unknown"
WRITE_NO_TOKEN = "no-token"
WRITE_VERDICTS = (WRITE_OK, WRITE_AUTH, WRITE_REFUSED, WRITE_UNKNOWN, WRITE_NO_TOKEN)


@dataclass(frozen=True)
class FormWriteResult:
    """写回一份 JSON 配置之后的样子。**`ok` 只在后端说 200 时为真。**

    ⚠️ **为什么这个口回值、而读那个口抛**（brief §2 R3）：写的调用方（面板）要对
    **每一种**结局都有话说，而五态里最要紧的是 `unknown` ——
    「请求发出去了、回执没读成」**既不是成功、也不是「没写进去」**：
    那份配置**可能已经动了**。把它写成「写失败了」就是在编一句没人量过的话，
    而写的人会照着它去重发（或者更坏：以为没事）。

    `say` 是**给面板原样摆出来的那一句**（brief §2 R4）：后端自己那句 `msg`
    一定在里面（有的话），不加工、不改写。

    ⚠️ 它是 `frozen` 的（本仓 `lint.py` / `selftest.py` 那一族同款）：
    调用方**不许**在拿到之后把 `ok` 改掉。
    """

    #: 五态之一（见上面那组常量）。**判成功只认 `WRITE_OK`。**
    verdict: str
    #: 后端信封里那一格 `status`（读不出数字就是 `None`）。
    #: ⚠️ **`None` 不是 200**：它意味着「回的不是这一个接口的信」（见 `say`）。
    status: Optional[int]
    #: 后端**原话**（`msg` 那一格，逐字；它没给就是空串）。
    msg: str
    #: 一句人话（含后端原话），面板原样摆。
    say: str

    @property
    def ok(self) -> bool:
        """**只有后端说 200 才是真。** 其余四态一律 `False`（包括 `unknown`）。"""
        return self.verdict == WRITE_OK

    def as_dict(self) -> dict:
        return {"ok": self.ok, "verdict": self.verdict, "status": self.status,
                "msg": self.msg, "say": self.say}


def business_status(body: Any) -> Optional[int]:
    """信封里那一格 `status` → 数字；**读不出数字就回 `None`（不是 200）**。

    这一段的形状是照 `ad-task.py` 的 `_fail_diag_verdict()` 抄的两条：
    · **`True`/`False` 不是业务码**（Python 里 `True == 1`，不挡它就会把
      `{"status":true}` 读成别的什么东西）；
    · **字符串数字也算**（原话：「别指望服务端一定发数字」）。

    ⚠️ 回 `None` 的那些（没有 `status` 这一格 / 它不是数字 / 正文不是对象）
    **一律按「没读到答复」办** —— 绝不按「成了」办（老代码那个错就是反着来的）。
    """
    if not isinstance(body, dict):
        return None
    biz = body.get("status")
    if isinstance(biz, bool):
        return None
    if isinstance(biz, str) and biz.strip().isdigit():
        return int(biz.strip())
    if isinstance(biz, int):
        return biz
    return None


def looks_like_wrapper(steps: Any) -> bool:
    """这一格是不是**包装层**（`data` 那一层）而不是配置本身。

    实测过的两种形状（【我量的·B1】读回来的正文）：
    · **包装层**：`{"site": "cvrefresh.com", "steps": {"form_type": …}}` —— `steps` 是**对象**
    · **配置**：  `{"form_type": …, "site": …, "steps": [{"action": …}], "success": {…}}`
      —— `steps` 是**数组**

    ⇒ 判据就这一条：**`steps` 那一格存在、而且它是个对象**。
    ⚠️ 故意**只**认这一种：不去猜「配置一定得有 `form_type` / `success`」
    （那是替后端定 schema，这一层没资格），也不去猜别的形状。
    撞上它的后果是把包装层原样写进生产配置（那份配置就废了），
    所以这一处**宁可红一次**（`update_form_config` 直接抛，一个请求都不发）。
    """
    if not isinstance(steps, dict):
        return False
    return isinstance(steps.get("steps"), dict)

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


def int_or_none(value: Any) -> Optional[int]:
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

    ★★ **这一句是「数据行」，两个去处必须逐字节一样。**

    这一族里**只有它**要同时进页面的 `<li>`（读的那一行）与 **`<option>`**（挑的那一格），
    而 `<option>` 里放不了标记 —— 带 `**` 的话，两个去处显示的东西就**不一样**
    （本仓修复轮 1 在截图里真看见过那个形状：「漏一处，运营看到的就是两个星号」）。

    所以规矩分两半（Task 4 修复轮 1 想清楚；复审 F9 指出上一版只钉了前一半）：
      · **我们自己写的话里不出现 `**`** —— 强调靠措辞（「可是没有脚本」），不靠标记。
        钉子：`test_the_rank_row_text_carries_no_emphasis_markers`。
      · **后端原样搬进来的那两格**（`note` / 认不出的 `config_status`）**原样显示** ——
        它们是**数据**，不是我们的标记：这一层**不加工、不转义、不剥星号**
        （剥了就是把后端说的话改掉）。页面那两个去处**都走 `esc()`**（不走 `rich()`），
        于是「原样」在两个地方是同一个字节。钉子：
        `test_a_backend_cell_that_looks_like_markup_comes_through_verbatim` +
        页面那侧的 `test_the_row_you_read_is_the_row_you_pick`。
    """
    row = row if isinstance(row, dict) else {}
    parts = []
    n = int_or_none(row.get("fail"))
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
    total = int_or_none(data.get("failed_total"))
    unattr = int_or_none(data.get("unattributed"))
    shown = sum(n for n in (int_or_none(r.get("fail")) for r in rows) if n is not None)
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

    n = int_or_none(limit)
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
    #: Task B1。⚠️ **写那个口（`FORM_CONFIG_UPDATE_PATH`）有意不在这一张表里**：
    #: 表里每一条都会被读成「**读不了**<名词>」那句话的头，而写那个口说的是
    #: 「**写不回**…」（`update_form_config` 自己起头）—— 塞进来会让它顶着
    #: 一个说不通的动词，也会让 `test_the_noun_each_reader_actually_prints_…`
    #: 那条「每个读口各报各的名」的循环去量一个不是读口的东西。
    FORM_CONFIG_PATH: "这份 JSON 配置",
}


def _default_get(url: str, headers: dict, timeout: float = DEFAULT_TIMEOUT) -> str:
    """真发那一个请求（返回正文）。**HTTP 码不是 200 也算「量不到」**，并带上那个码。

    为什么不读状态码的坏处（`fail-script` 的注释里记着同一个坑）：429/5xx 的非 JSON
    响应会以 `JSONDecodeError` 的面目冒出来，看不出是「被限速了」。
    """
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 —— 地址是配置里定的
        return resp.read().decode("utf-8", "replace")


def _default_post(url: str, headers: dict, body: bytes,
                  timeout: float = CONFIG_TIMEOUT) -> str:
    """写回那一个请求（返回正文）。与 `_default_get` 同一条路，只多了正文与那个动词。

    ⚠️ 正文按 **UTF-8 字节**发、**不转义非 ASCII**（`update_form_config` 里
    `json.dumps(..., ensure_ascii=False)`）：配置里的 `find.text` 有中文
    （生产配置里真有），转义会把体量放大几倍 —— `ad-task.py` 发日志那条也是这么写的。
    """
    req = urllib.request.Request(url, data=body, headers=dict(headers or {}), method="POST")
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
                 now: Optional[Callable] = None,
                 poster: Optional[Callable] = None,
                 config_timeout: float = CONFIG_TIMEOUT):
        self.token = (os.environ.get(TOKEN_ENV) or "") if token is None else str(token or "")
        self.base = str(base if base is not None else
                        (os.environ.get(BASE_ENV) or DEFAULT_BASE)).rstrip("/")
        self.timeout = float(timeout)
        self._opener = opener or _default_get
        #: ★ Task B1：那两个配置口**共用**这个超时（比 `self.timeout` 短，见 `CONFIG_TIMEOUT`）。
        #: 注入的桩照旧只收它自己那几个参数 —— 超时**只绑在真的传输上**，
        #: 所以注入 `opener` / `poster` 时这一格不影响桩（桩不睡觉）。
        self.config_timeout = float(config_timeout)
        self._config_opener = opener or functools.partial(_default_get,
                                                          timeout=self.config_timeout)
        #: 写回的注入点。协议与 `opener` 差一格：`(url, headers, body) -> 正文`。
        #: ⚠️ 单开一个口（不把 `opener` 改成收三个参数）是因为 `opener` 那个协议
        #: 已经被四个读口和一堆测试桩用着 —— 改它就是「牵一发动全身」。
        self._poster = poster or functools.partial(_default_post, timeout=self.config_timeout)
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

    def _call(self, path: str, params: dict, *, shape=(list, dict),
              need_token: bool = True, opener: Optional[Callable] = None) -> Any:
        """一次读。**失败一律抛**（`FmrUnmeasured` 的三个子类），**绝不返回空**。

        `shape` 是 `data` 那一格**该长成什么样**（Task 4 加的）。

        `need_token` / `opener` 是 Task B1 加的两个口子（默认值 = 老四个读口的老行为）：
        · `need_token=False` —— 那个口**公开、无鉴权**（【我量的·B1】不带任何头也回
          200 + 整份配置）⇒ 没配 token 的部署**也读得动**它，不该在门口被挡下；
        · `opener=…` —— 换一个传输（配置口用它绑**更短**的超时，见 `CONFIG_TIMEOUT`）。

        ★ 为什么 ③ 榜单要传 `shape=dict`：那个接口**没有失败的那一天回的也是一个对象**
        （`failed_total:0` / `rank:[]`）⇒「回的不是对象」只可能是**这一次没量着**。
        不传的话，一次形状事故会以 `200 {"rank": …}` 的面目变成「今天没有站在失败」——
        而那正是这一层从头到尾在治的那句话。

        ⚠️ **这一道闸改了老两个读口的行为**（Task 4 修复轮 1 被复审量出来，这里补记）：
        `data: null` 或**整个 `data` 那一格不在**时，
        · BASE（这道闸之前）：`return data if isinstance(data, (list, dict)) else []` ⇒ 回**空列表**；
        · 现在：**抛**（`FmrUnreachable`）。
        ⇒ 老路径上 `fetch_failures` / `fetch_steps` 的这一个输入**行为变了** ——
        而这是**照模块 docstring 那张表修的**（「**唯一能变成空列表的，只有第一行**」：
        真量了、真没有 = `status:200` + `data:[]` 那个**空数组**）。
        `data:null` 与「`data` 那一格都不在」都**不是**那个空数组 ——
        它们读成「没有失败」正是这一层立身要防的那句话（BASE 自己那条不变量当时就没做到）。
        `test_a_body_with_no_data_cell_is_not_read_as_no_failures` 钉着这三态。
        """
        if need_token and not self.configured:
            raise FmrNoToken(
                "读不了%s：这个部署**没配 %s**（一个请求都没发出去）。"
                "%s。" % (self._what(path), TOKEN_ENV, UNMEASURED_SAY))
        url = "%s%s?%s" % (self.base, path, urllib.parse.urlencode(params))
        # ⚠️ token 走**请求头**，绝不进 URL（URL 会进日志、进 `ps`）。
        # ⚠️ 没配 token 时**整个头都不发**（不去发一个空值）：这一条只在
        # `need_token=False` 那条路上够得着（老四个口在上面就抛了）——
        # 那个口是公开的，带一个空头去没有意义。
        headers = {"X-Api-Token": self.token} if self.token else {}
        try:
            raw = (opener or self._opener)(url, headers)
        except Exception as exc:                       # noqa: BLE001 —— 什么都算「量不到」
            raise FmrUnreachable(self._unreadable_say(path, exc)) from exc
        try:
            body = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise FmrUnreachable(
                "读不了%s：后端回的正文不是 JSON（读了 %d 个字节）。%s。"
                % (self._what(path), len(raw or ""), UNMEASURED_SAY)) from exc
        if not isinstance(body, dict):
            raise FmrUnreachable(
                "读不了%s：后端回的正文不是一个信封（%s…）。%s。"
                % (self._what(path), str(body)[:60], UNMEASURED_SAY))
        status = body.get("status")
        if status != 200:
            raise FmrRefused(self._refused_say(path, params, body, status), status=int(status or 0))
        data = body.get("data")
        if not isinstance(data, shape):
            raise FmrUnreachable(
                "读不了%s：后端回的信封对得上（`status:200`），可 `data` 那一格**不是要的那种"
                "形状**（读回来的是 %s…）。%s。"
                % (self._what(path), str(data)[:60], UNMEASURED_SAY))
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
        if int(status or 0) == 404 and path == FORM_CONFIG_PATH:
            # ⚠️ **同一个 404，第三个语义**（Task B1）：这里它不是「这个站它不认识」，
            # 是「**这个站没有 JSON 配置**」（后端原话就是 `config not found`）。
            # 说成「那串名字给错了」是把一种可能说成了唯一一种 —— 而这两种
            # （键给错了 / 它本来就没配置）**这一次分不出**（Task 4 复审那条纪律：
            # 「不许替它预设是哪一种」）。分开写，是因为**下一步不是一件事**。
            return ("读不了%s：**这个站在后端没有 JSON 配置**（后端回 404）。%s %s"
                    "⚠️ 这一次**分不出**是哪一种：那串键给错了，"
                    "还是它本来就没有 JSON 配置（只有 py 脚本）—— "
                    "榜单（③）上那些键是后端自己给的，可以拿去对一眼。"
                    % (noun, UNMEASURED_SAY, tail))
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

    # ── ⑤ 一份 JSON 配置：读（Task B1）──────────────────────────────
    def form_config(self, site: str) -> dict:
        """**这个站的 JSON 配置**（后端那一份，逐字；这一层不加工任何一个格）。

        交出去的是**里面那一份** —— `{form_type, site, steps[], success}`，
        不是包着它的那层信封（`{"site":…,"steps":{…}}`）。
        【我量的·B1】`GET /api/quest/formConfig?site=cvrefresh.com` →
        `{"status":200,"msg":"success","data":{"site":…,"steps":{"form_type":"magic_link",…}}}`。

        ★ **读回来的就是能原样写回去的那一份**：`update_form_config(site, form_config(site))`
        是一趟**没有改动**的往返。这不是顺手定的 —— 「读错层」是这一族踩过的坑
        （`ad-task.py:2157`：把包装层当配置），所以两个口收发的**必须是同一个东西**。

        ⚠️ **这个口公开、不要 token**（【我量的·B1】不带任何头发出去也回 200）——
        所以这里走 `need_token=False`：没配 token 的部署**也读得动**它。
        （写那个口要 token —— 见 `update_form_config`。）

        ⚠️ **「查不到」与「后端挂了」是两件事**（这一层的老纪律，这里也照办）：
        · 这个站**没有 JSON 配置** → `FmrRefused`，`status` 是 404、`kind="refused"`
          （后端原话 `config not found`；正文 `data` 是 `[]`）；
        · **没量着**（连不上 / 超时 / 正文不是 JSON / HTTP 不是 200）→ `FmrUnreachable`。
        两件**处置完全不同**：前者去改那串键（或去建配置），后者去查后端/网络。
        谁把这两条合成一条，就是「一个查不到的站被标成健康的」那个老病的下一站。
        """
        key = str(site or "").strip()
        if not key:
            raise FmrUnmeasured(
                "读不了这份 JSON 配置：没说是**哪个站** —— 这是免费的检查"
                "（一个请求都没发出去）。", kind="no-site")
        data = self._call(FORM_CONFIG_PATH, {"site": key}, shape=dict,
                          need_token=False, opener=self._config_opener)
        inner = data.get("steps")
        if not isinstance(inner, dict):
            # ⚠️ 这一格**要的是里面那份配置**。形状不对**只能抛**（不许猜、不许把它当空配置）：
            # 与 `_call(shape=…)` 那条闸同一个道理，只是这一层在信封里面。
            raise FmrUnreachable(
                "读不了%s：信封对得上（`status:200`），可 `data.steps` 那一格"
                "**不是一个对象**（读回来的是 %s…）—— 这一格要的是**里面那份配置**"
                "（`{form_type, site, steps[], success}`）。%s。"
                % (self._what(FORM_CONFIG_PATH), str(inner)[:60], UNMEASURED_SAY))
        return inner

    # ── ⑤ 一份 JSON 配置：写（Task B1）──────────────────────────────
    def update_form_config(self, site: str, steps: Any) -> FormWriteResult:
        """**把一份 JSON 配置写回后端**（`POST /api/quest/formConfig/update`）。

        契约（**转述的·出处 `task-b1-brief.md` §1**，控制者探测得到的那两行：
        缺 `site`/`steps` → `{"status":400,…}`、不带 token → `{"status":401,…}`）。
        【我量的·B1】不带 token 发出去那一次：**HTTP 200** +
        `{"status":401,"msg":"unauthorized","data":[]}` —— **按 HTTP 码判会读成成功**。

        ★★ **判据只有 body 的 `status` 那一格**（`ok` 只在它等于 200 时为真）。
        这不是风格问题：本仓有案底（`ad-task.py` 的 `_fail_diag_verdict()` 注释）——
        按 HTTP 码判的那版客户端把鉴权失败当成了成功、打了一行「已上传」。

        `steps` 要的是**整份配置**（`{form_type, site, steps[], success}`）——
        与 `form_config()` 交出来的是同一个东西（D4：整条读回、只改该改的、整条写回；
        **少带一个键就是把配置弄丢**）。

        ⚠️ **这个口不拋**（brief §2 R3）：每一种结局都回一个 `FormWriteResult`，
        `verdict` 五态见 `FormWriteResult` 的 docstring。**只有下面两件是例外**，
        它们都是**调用方自己的毛病**、而且**一个请求都不会发出去**（所以它们不影响
        「有没有写进去」这个判断）：
        · 没说是哪个站 → `FmrUnmeasured(kind="no-site")`；
        · `steps` 不是一份配置（空的 / 不是对象 / **是包装层**）→
          `FmrUnmeasured(kind="no-steps"|"wrong-layer")`。
        """
        key = str(site or "").strip()
        if not key:
            raise FmrUnmeasured(
                "写不回这份 JSON 配置：没说是**哪个站** —— 这是免费的检查"
                "（一个请求都没发出去）。", kind="no-site")
        if not isinstance(steps, dict) or not steps:
            raise FmrUnmeasured(
                "写不回这份 JSON 配置：`steps` 那一格得是**整份配置**"
                "（`{form_type, site, steps[], success}`），给的是一个 %s。"
                "⚠️ 这一格**少带一个键就是把那份配置弄丢**，所以宁可在这儿红一次 —— "
                "一个请求都没发出去。（手里若是那串 JSON 文本，先 `json.loads` 成对象。）"
                % ("空对象" if isinstance(steps, dict) else type(steps).__name__),
                kind="no-steps")
        if looks_like_wrapper(steps):
            raise FmrUnmeasured(
                "写不回这份 JSON 配置：这一格是**包装层**（`data` 那一层："
                "`{\"site\":…,\"steps\":{…}}`），不是配置本身。"
                "要传的是**里面那份**（`steps` 那一格是**数组**的那一份）—— "
                "`form_config()` 交出来的就是它。"
                "⚠️ 原样写回去的话，后端那份配置的 `steps` 会变成一个对象"
                "（这一族踩过的「读错层」坑，`ad-task.py:2157`）。一个请求都没发出去。",
                kind="wrong-layer")
        if not self.configured:
            # ⚠️ 这里回**值**、不抛：这是一个**已知**的结局（一个请求都没发出去 ⇒
            # **确定没写**），调用方（面板）对它有话说。与读口那个「没配 token 就抛」
            # 不冲突：读那次是「量不到」，这一次是「没写」——两句话，不是一个形状。
            return FormWriteResult(
                verdict=WRITE_NO_TOKEN, status=None, msg="",
                say=("**一个字都没写**：`%s` 的配置**没有被改动** —— 这个部署"
                     "**没配 %s**，请求一个都没发出去。"
                     "配法：给服务进程一个 `%s` 环境变量（与 `DATABASE_URL` 同一层）。"
                     % (key, TOKEN_ENV, TOKEN_ENV)))
        url = "%s%s" % (self.base, FORM_CONFIG_UPDATE_PATH)
        headers = {"Content-Type": "application/json"}
        # ⚠️ token 走**请求头**，绝不进 URL（与四个读口同一条纪律）。
        if self.token:
            headers["X-Api-Token"] = self.token
        body = json.dumps({"site": key, "steps": steps},
                          ensure_ascii=False).encode("utf-8")
        try:
            raw = self._poster(url, headers, body)
        except Exception as exc:                       # noqa: BLE001 —— 什么都算「不知道成没成」
            return self._write_unread_result(key, exc)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return FormWriteResult(
                verdict=WRITE_UNKNOWN, status=None, msg="",
                say=("**不知道写没写成**：请求发出去了，可后端回的正文不是 JSON"
                     "（读了 %d 个字节）—— 既不能当成功，也**不能当「没写进去」**："
                     "那份配置可能已经动了。去后端核对一眼"
                     "（`%s%s?site=%s`）再决定要不要重发。"
                     % (len(raw or ""), self.base, FORM_CONFIG_PATH,
                        urllib.parse.quote(key, safe=""))))
        status = business_status(payload)
        msg = str(payload.get("msg") or "").strip() if isinstance(payload, dict) else ""
        tail = ("后端自己那句是：「%s」。" % msg) if msg else "后端没给它那句说明。"
        if status == 200:
            return FormWriteResult(
                verdict=WRITE_OK, status=200, msg=msg,
                say=("写回去了：`%s` 的 JSON 配置**已经在后端更新**"
                     "（后端回的 `status` 是 200）。%s" % (key, tail)))
        if status == 401:
            return FormWriteResult(
                verdict=WRITE_AUTH, status=401, msg=msg,
                say=("**没写回去 —— 鉴权没过**（后端回的 `status` 是 401）。%s"
                     "⚠️ 这一族**一律 HTTP 200**，所以这一条**不是**「网络出问题」—— "
                     "是这台机器上那个 `%s` 不对或过期了。"
                     "这一趟卡在鉴权那一层，**没进到改配置的那段代码** ⇒ "
                     "`%s` 的配置**没有被改动**。" % (tail, TOKEN_ENV, key)))
        if status is None:
            return FormWriteResult(
                verdict=WRITE_UNKNOWN, status=None, msg=msg,
                say=("**不知道写没写成**：后端回的正文**不是一个带业务码的信封**"
                     "（`status` 那一格读不出 200/401 这种码，读回来的是 %s…）。%s"
                     "⚠️ 这**不是**「没写进去」—— 那份配置可能已经动了，"
                     "去后端核对一眼再说。"
                     % (str(payload)[:60], tail)))
        return FormWriteResult(
            verdict=WRITE_REFUSED, status=status, msg=msg,
            say=("**没写回去 —— 后端说这请求不对**（后端回的 `status` 是 %s）。%s"
                 "⚠️ 这一条是**它明确拒了**（不是「不知道」）—— `%s` 的配置"
                 "**没有被改动**。"
                 % (status, tail, key)))

    def _write_unread_result(self, site: str, exc: Exception) -> FormWriteResult:
        """**请求发出去了、回执没读成** → `unknown`。★ 这一支**不许**说成「没写进去」。

        三种都进这儿：连不上 / 超时 / **HTTP ≥ 400**（网关那一层先回了码 ——
        这一族的正常形状是 HTTP 恒 200，见模块 docstring）。
        ⚠️ 写与读在这里**正好相反**：读那一次「没读成」= 量不到（无害），
        这一次「没读成」= **那份配置可能已经动了**（有害，且不可猜）。
        """
        code = getattr(exc, "code", None)
        if code is not None and int(code) in (401, 403):
            # 与 `ad-task.py` 那两条路同源：网关/代理层仍可能按 HTTP 回 4xx
            # （那一层不看 body）—— 这一支照样是「没写」：它在业务层之前就拦下了。
            return FormWriteResult(
                verdict=WRITE_AUTH, status=None, msg="",
                say=("**没写回去 —— 鉴权没过**（后端按 **HTTP %s** 回的话，"
                     "不是在写这件事上给了答复）。⚠️ 这一族正常是 HTTP 恒 200，"
                     "按 HTTP 回码说明是它**前面那一层**拦的（网关 / 路由）："
                     "那串 `%s` 对不上，或者根本没到业务层 ⇒ "
                     "`%s` 的配置**没有被改动**。（真因：%s: %s）"
                     % (code, TOKEN_ENV, site, type(exc).__name__, exc)))
        if code is None:
            why = "连不上那个后端（%s）" % self.base
        else:
            why = "后端按 **HTTP %s** 回了话（不是在写这件事上给了答复）" % code
        return FormWriteResult(
            verdict=WRITE_UNKNOWN, status=None, msg="",
            say=("**不知道写没写成**：请求发出去了，可回执没读成 —— %s。"
                 "⚠️ 这**不是**「没写进去」：那份配置**可能已经动了**。"
                 "去后端核对一眼（`%s%s?site=%s`）再决定要不要重发。"
                 "（真因：%s: %s）"
                 % (why, self.base, FORM_CONFIG_PATH,
                    urllib.parse.quote(site, safe=""), type(exc).__name__, exc)))
