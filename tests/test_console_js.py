"""Task 10 Step 2.5：`agent/console.html` 的**执行夹具**（`node` + 假 `document` / 假 `fetch`）。

## 为什么要有它

Task 7 那一轮的复审发现：「**只有浏览器看得见**」的缺陷**在同一片里出现了 4 次**
（页面的 JS 把某句话写进某个元素，随后**又被下一次重画擦掉**）。当时的判据是「值得上」，
归属后来裁给了 Task 10。这份就是那套夹具的**轻形态**（复审指定的：`node` + 假 `document` /
假 `fetch`，**不是** headless 浏览器）。

**它钉的是「哪个值、什么顺序、被写进哪个元素」**：把 `console.html` 里那段**原样的**脚本
（`agent/console.html` **一个字节都不改** —— 夹具是外挂的，脚本是从文件里**切出来的**）
放进一个假 DOM 里跑起来，按一个真人会走的顺序驱动它，然后把屏幕上那几个元素**此刻的文本**
读回来。`.js` 那位是 `tests/console_js_driver.js`。

## 射程（**先说不钉什么**）

**钉得住**（今天 HTTP 层与源码文本断言都钉不住的那一类）：

| # | 性质 | 它为什么只有跑起来才看得见 |
|---|---|---|
| 1 | 出错的**那一刻**服务那句话原样上屏，且**活过之后 3 次重画**（`/runs` 坏掉那一段；按「停」之后到结束 **8**；整段 **10**） | 「写进去」与「还在不在」是两件事（`setErr` 写对了，下一次 `fetchLive` 成功就擦掉 —— 修复轮 1 的坑） |

⚠️ 上面那**三个数**不是同一个读数，是驱动脚本 `paintMarks` 那四个**时刻读数**两两相减得来的
（实测 `{afterLoad: 1, afterStop: 2, afterRunsBroken: 7, end: 10}`）：整段 = `end` = **10**；
按「停」之后 = `end - afterStop` = **8**；`/runs` 坏掉之后 = `end - afterRunsBroken` = **3**。
四个读数**都在驱动输出里**（`paintMarks`），用例只用其中两个差。
| 2 | 左边那一栏读不到时，那句真话**不被过期列表盖掉** | 修复轮 3 的坑：`fetchRuns` 失败只是**写了字**，而 `paint()` 每 3 秒把 `state.runs`（上一次那份**好**列表）照画一遍 |
| 3 | 服务那两格**对不上**时，页面说出来（而不是自己挑一句） | 两句话是同一段 `innerHTML` 的**两个分支** —— 源码文本断言看得见「分支在」，看不见**这一刻走了哪一支** |
| 4 | 按「重新来一遍」之后**页面上真的变了**（跟着新那一趟走 + 把服务那句原话摆出来） | 服务侧回的是 `202 {job_id, say}`，**页面接不接它是页面的事** —— 接不接，源码文本断言两边都绿 |

**钉不住**（照实说，别把它读大）：

- **DOM 归属/删除**：假 DOM 里元素**不会**因为某个容器 `innerHTML = ""` 而消失 ——
  所以「错误框被放进会被重画的容器」那一种形状**这一份看不见**（它由
  `test_console_page.py::test_the_error_line_cannot_be_wiped_by_a_repaint` 的静态标记断言管）。
- **像素与布局**：`scrollHeight` / `clientHeight` 一律桩成 `0` ⇒ 「跟不跟滚动」那一支
  （`paintTimeline` 里的 `near`）在这一份里**没有被证明**。
- **真浏览器的时序**：假 `fetch` 是 Promise，跟真网络不是一回事（超时、乱序回来、缓存都没有）。
- **CSS / 无障碍 / 真窗口大小**：一个字都不测。
- 射程的**共同前提**：事件处理是按**真人点按钮**那条路调的（`btnStop.listeners.click()`），
  不是直接调内部函数 —— 但夹具仍然看不到「浏览器真的把点击派发到那个元素上」。
- ⚠️ **夹具的牙挂在一个前提上：每一份 `/live` 正文都必须不一样**（页面只在
  `JSON.stringify(正文) !== seen` 时才重画）。这个前提**今天被量着**（驱动脚本数
  `#timeline` 的写入次数 = 重画次数，用例断言它等于 `/live` 的 fetch 次数）——
  Task 10 修复轮 1 之前它**只被 fetch 数装样子**（三种载荷下 fetch 都是 10、重画是 10/3/2）。

⚠️ **正控在用例里**：`test_the_fixture_can_actually_fire` 拿一个**已知会破坏行为**的改写
（把 `setErr("")` 那道 `errFrom` 判据改成恒真）证明这套夹具**真的会响** ——
一条永远绿的夹具和没有夹具是一回事。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr  # noqa: E402
from agent import graph  # noqa: E402
from agent import service  # noqa: E402
from test_fmr import MEASURED_STEPS  # noqa: E402

DRIVER = pathlib.Path(__file__).with_name("console_js_driver.js")
NODE = shutil.which("node")

#: 服务回的那句 404 人话（`/stop` 走到一个不存在的 job 上）—— 页面必须**原样**摆出来。
STOP_404 = "没这个任务：job-1。"
#: 左边那一栏读不到时服务回的那句人话（500）。
RUNS_500 = "Internal Server Error（桩）"
#: 跑完所有重画之后，屏幕上应该还在的那三句。
ERR_STILL = STOP_404
RUNS_STILL = "左边这一栏现在取不到"
STOP_HINT_STILL = "没请求成"
#: 服务对 `/again` 回的那句人话 —— **从服务自己的常量算出来**（不在这里手抄一遍：
#: 那句话改一个字，这一份就会跟着变，不会两边漂）。
AGAIN_SAY = service.AGAIN_SAY % ("job-2", "job-1", 2)
#: 两趟事件人话里的**记号**（修复轮 2 / D1）：时间线上是哪一趟的事件，靠它分。
OLD_TAG = "旧那一趟"
NEW_TAG = "新那一趟"

# ── Task 11（§十五）：产物那一格 ────────────────────────────────────────
#: 有产物那一格。三格字符串**从服务自己的常量算出来**（不在这里手抄一遍 —— 服务的路由或
#: 那句话改一个字，这一份就跟着变，不会两边漂）。与 `AGAIN_SAY` 同一个做法。
#:
#: ⚠️ 地址尾巴上那个 `?fixture=1` **是故意的**：不加的话，这个地址与
#: 「页面拿 `jobId` 自己拼一个 `/job/<id>/artifact`」**长得一模一样** ——
#: 于是「地址是服务给的、页面不自己拼」这条判据**量不出来**（拼出来的那个也照过）。
#: 加上之后，页面里任何一处自己拼地址的写法都会露出马脚。
ART_URL = service.ARTIFACT_URL % "job-1" + "?fixture=1"
ART_SAY = service.ARTIFACT_READY_SAY
ART = {"url": ART_URL, "filename": "example-funnel.py",
       "path": "/srv/siteforge/forms/sites/example-funnel.py", "say": ART_SAY}
#: 到头了但没产物那一格（A14）：**没有地址**，只有一句人话。
#: 那句话的形状与服务那句**同源**（`_no_artifact_say` 就是「头 + 它自己交代的那句」，
#: 撞上限那三种按时间线同一条规则加前缀）—— 这里拼的是这一份夹具要摆上去的那一句。
NO_ART_SAY = "%s。它自己交代的是：\n%s停：这一版 py 被打回 2 次还是同样的地方不过。" % (
    service.NO_ARTIFACT_HEAD_DONE, service.CAP_SAY_PREFIX)
NO_ART = {"url": None, "filename": None, "path": None, "say": NO_ART_SAY}
#: 夹具认的两个记号（`agent/console.html` 的 `artifactStep` 上各挂一个）：
#: `STEP_MARK` = 那一步本身在不在（§15.4）；`DL_MARK` = 那条下载在不在（A14）。
#: ⚠️ 两个名字**不许写得能互相包含**（`data-artifact` 与 `data-artifact-step` 那种）——
#: 那样「那一步在」会让「下载不在」这条断言**永远假绿**。
STEP_MARK = 'data-artifact-step="1"'
DL_MARK = 'data-download="1"'


def _thin(text: str) -> tuple:
    """把服务那句话拆成**它在屏幕上该长的样子**的两段：`(粗体那一段, 它后面那一段)`。

    为什么不整句比：页面的 `rich()` 会把 `**x**` 渲染成 `<b>x</b>`、把换行渲染成 `<br>` ——
    整句拿去 `in` 是比不中的。按 `**` 拆开的两段各自是**原样**上屏的，所以能直接比
    （`NO_ART_SAY` 的形状就是「头 `**…**` 尾」）—— 这么写断言直接引用**服务那句话**，
    它改一个字这一份跟着变，不会两边漂。
    """
    parts = str(text).split("**")
    assert len(parts) == 3, "这句话的形状变了（不再是「头 **粗体** 尾」）：%r" % text
    return parts[1], parts[2]


def _live(status: str, mode: str, *, n: int, stop_requested: bool = False,
          tag: str = "夹具", artifact: dict = None, delivered: bool = False,
          window: dict = None) -> dict:
    """一份 `/live` 正文（**只填这一份夹具要读的那几格**，其余按页面「可能不在」的读法留空）。

    `tag` 进每一句事件的人话里 —— **两趟用不同的 tag**，好让「时间线里是哪一趟的事件」
    这条判据**分得开新旧**（修复轮 2 / D1：同一条文字两趟都有 ⇒ 那条断言钉不住东西）。
    `artifact`（Task 11 / §十五）：`None` = 还没到「写下了 py」那一步（页面**不摆**那个位置）。
    """
    return {
        "job_id": "job-1", "status": status, "delivered": delivered,
        "say": "在跑。" if status == "running" else "停下来了，在等你一句话。",
        "stage": "explore", "stage_say": "探路",
        "note": "", "shots_note": "",
        "events": [{"n": i + 1, "at": "2026-09-19T21:0%d:00+08:00" % i, "kind": "running",
                    "who": "system", "say": "第 %d 条（%s）" % (i + 1, tag)} for i in range(n)],
        "gate": None if status != "waiting" else
                {"step": "deliver", "step_say": "交付", "ask": "要交付吗？", "facts": {},
                 "can": ["让它继续"], "revisable": True},
        "input": {"mode": mode, "draft_note": "", "queued": []},
        "stop": {"requested": stop_requested, "where": "", "will_stop_at": ""},
        #: 窗口那一格（Task 12）：`None` = 这个部署没接窗口层（页面明说看不到、两个按钮都不摆）；
        #: 给了就是服务那一格原样（`state` / `state_say` / `can_close` / `can_reopen`）。
        "window": window, "rounds": [], "truncated": False,
        "artifact": artifact,
    }


def _payloads(*, final_mode: str) -> dict:
    """这一趟要喂给页面的那些响应（顺序有意义 —— 驱动脚本按顺序取）。

    `/live` 那份：开页时在跑 → 点「停」之后那份（**变了** ⇒ 会重画）→ 之后停在闸上，
    **而且每一份都不一样**（事件数在涨）。`input.mode` 由调用方给 —— 两格一致还是
    **对不上**就看它。

    ⚠️ 「每一份都不一样」是**必须的**，不是讲究（实测栽过一次）：页面里 `paint()` 只在
    `JSON.stringify(正文) !== seen` 时才画，喂同一份 JSON 回去**根本不会重画** ——
    那样这条夹具量到的就是「没有重画」，而不是「重画擦不掉」。
    「在跑的任务每 3 秒就变」正是那个缺陷的现场条件（Task 7 复审的原话）。
    """
    lives = [{"body": _live("running", "queue", n=1)},
             {"body": _live("running", "queue", n=2)}]
    lives += [{"body": _live("waiting", final_mode, n=3 + i)} for i in range(20)]
    #: 那句前提**在这里就量掉**（Task 10 修复轮 1 / C1）：22 份正文两两不同，
    #: 而用例还会用「重画次数 == `/live` 的 fetch 次数」再量一遍**行为**。
    _assert_all_different([x["body"] for x in lives], "`/live` 的正文")
    return {
        "search": "?job=job-1",
        "responses": {
            "/runs": [
                {"body": {"note": "", "runs": [{"job_id": "job-1", "site": "example-funnel",
                                                "status": "running", "say": "在跑。",
                                                "created_at": "2026-09-19T21:00:00+08:00",
                                                "rounds": 0, "delivered": False}]}},
                {"status": 500, "body": {"detail": RUNS_500}},
            ],
            #: ⚠️ 每一条都要**包一层信封**（`{"body": …}`）：驱动脚本从信封上读 HTTP 状态码，
            #: 而 `/live` 的正文自己就有一个 `status` 键（"running"…）—— 不包的话它会
            #: 把正文那一格当状态码读（这一步实测栽过一次，正是「两个东西共用一个名字」）。
            "/job/job-1/live": lives,
            "/job/job-1/stop": [{"status": 404, "body": {"detail": STOP_404}}],
        },
    }


def _again_payloads() -> dict:
    """「重新来一遍」那一趟的响应（Task 10 修复轮 1 / C3 = N-3）。

    这一趟**到头了**（`status="failed"`）—— 那个按钮只在到头的两档才露头
    （`over` 为真），而 `/again` 对着一趟还在跑的 job 会回 409（`service.py` 那条）。
    新那一趟是 **job-2**：服务回的那句 `say` 是它自己算好的原话（`AGAIN_SAY` 那句）。

    ⚠️ 两趟的**事件文字故意不同**（`tag`，修复轮 2 / D1）：文字一样的话，
    「时间线上是新那一趟的事件」这条断言在两趟之间**分不出来**（旧那一趟那条也在）。
    """
    over = _live("failed", "queue", n=2, tag=OLD_TAG)
    over["say"] = "这一步没跑成，停下了。"
    fresh = _live("queued", "queue", n=1, tag=NEW_TAG)
    fresh["job_id"] = "job-2"
    fresh["say"] = "排队等窗口（前面还有别的 run 在用）"
    _assert_all_different([over, fresh], "两份 `/live` 的正文")
    #: 「时间线上是哪一趟的事件」这条判据靠**文字不同**分新旧 —— 那两个记号不许被合成一个，
    #: 也不许哪天忘了写进事件里（那样判据会**静默**变成分不出来：两趟的文字又一样了）。
    assert OLD_TAG != NEW_TAG, "两个记号是同一个 —— 「新那一趟」这条判据就分不出新旧了"
    assert OLD_TAG in over["events"][0]["say"], over["events"][0]
    assert NEW_TAG in fresh["events"][0]["say"], fresh["events"][0]
    return {
        "scenario": "again",
        "search": "?job=job-1",
        "responses": {
            "/runs": [
                {"body": {"note": "", "runs": [{"job_id": "job-1", "site": "example-funnel",
                                                "status": "failed", "say": "这一步没跑成，停下了。",
                                                "created_at": "2026-09-19T21:00:00+08:00",
                                                "rounds": 0, "delivered": False}]}},
            ],
            "/job/job-1/live": [{"body": over}],
            "/job/job-1/again": [{"body": {"job_id": "job-2", "say": AGAIN_SAY}}],
            "/job/job-2/live": [{"body": fresh}],
        },
    }


def _assert_all_different(bodies: list, what: str) -> None:
    """那些正文**两两不同** —— 夹具的牙挂在这个前提上（页面只在正文变了才重画）。"""
    seen = {json.dumps(b, sort_keys=True) for b in bodies}
    assert len(seen) == len(bodies), (
        "%s 有重复（%d 份里只有 %d 份不同）—— 页面不会重画，这套夹具的牙就没了"
        % (what, len(bodies), len(seen)))


def _artifact_payloads(*, with_artifact: bool) -> dict:
    """产物那一趟（Task 11 / §十五）的两份载荷（**同一个驱动**，只有那一格不同）：

    · `with_artifact=True`：开页时在跑、**还没有**产物 → 后面几份里那一格出现了；
    · `with_artifact=False`：开页时在跑 → 后面几份**到头了但没产物**（A14 那一档）。

    ⚠️ 每一份正文都不一样（夹具的牙挂在这个前提上），而且产物出现之后**还要继续变** ——
    「写进去了」与「还在不在」是两件事（Task 7 那一族就是这么漏的）。
    """
    tail = (ART if with_artifact else NO_ART)
    status = "done" if with_artifact else "failed"
    lives = [{"body": _live("running", "queue", n=1)},
             {"body": _live("running", "queue", n=2)}]
    lives += [{"body": _live(status, "queue", n=3 + i, artifact=tail,
                             delivered=with_artifact)}
              for i in range(4)]
    _assert_all_different([x["body"] for x in lives], "`/live` 的正文")
    return {"scenario": "artifact", "search": "?job=job-1",
            "responses": {
                "/runs": [{"body": {"note": "", "runs": [
                    {"job_id": "job-1", "site": "example-funnel", "status": "running",
                     "say": "在跑。", "created_at": "2026-09-19T21:00:00+08:00",
                     "rounds": 0, "delivered": False}]}}],
                "/job/job-1/live": lives,
            }}


#: `_drive` 的 `scenario` 名字 → 驱动器认识的那个名字（**只差这一对**）。
_DRIVER_SCENARIO = {"artifact-missing": "artifact"}


def _drive(tmp_path, *, final_mode: str = None, scenario: str = "repaint",
           page: pathlib.Path = None) -> dict:
    """跑一次夹具，把驱动脚本打回来的那份观测解析出来。"""
    if scenario == "again":
        payload = _again_payloads()
    elif scenario == "artifact":
        payload = _artifact_payloads(with_artifact=True)
    elif scenario == "artifact-missing":
        payload = _artifact_payloads(with_artifact=False)
    #: Task 12 那三个（开一趟 / 被拒 / 窗口那两下）—— ⚠️ 分支名与驱动器里
    #: `payload.scenario` 认的那几个**必须同名**：不同名就会静默落到 `repaint` 上，
    #: 而那一条**照样跑得完**（量出来的是一趟完全不同的驱动）—— 实测栽过一次。
    elif scenario == "run":
        payload = _run_payloads()
    elif scenario == "run-refused":
        payload = _run_refused_payloads()
    elif scenario == "window":
        payload = _window_payloads()
    elif scenario == "gate-facts":
        payload = _gate_facts_payloads()
    elif scenario == "failures":
        payload = _failures_payloads()
    elif scenario == "failures-unmeasured":
        payload = _failures_unmeasured_payloads()
    else:
        payload = _payloads(final_mode=final_mode)
    #: 分支名与载荷自己声明的那一个**同不同名**：不同名 = 上面又漏了一个分支，
    #: 而它不会红 —— 会静默落到 `repaint` 上。所以在这儿当场地量一次。
    #: ⚠️ `artifact-missing` 与 `artifact` 在驱动器那边是**同一段**（只差载荷里那一格），
    #: 所以那一对是同名的 —— 这是**唯一的**例外，别拿它当先例。
    wanted = _DRIVER_SCENARIO.get(scenario, scenario)
    assert payload.get("scenario", "repaint") == wanted, (
        "`_drive(scenario=%r)` 选出来的载荷自称是 %r —— 驱动器会按它自己那一格走，"
        "而这一条量到的就不是你想驱动的那一趟了"
        % (scenario, payload.get("scenario", "repaint")))
    data = tmp_path / ("payloads-%s-%s.json" % (scenario, final_mode))
    data.write_text(json.dumps(payload), encoding="utf-8")
    r = subprocess.run([NODE, str(DRIVER), str(page or service.CONSOLE_PATH), str(data)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "夹具自己挂了（rc=%s）：\n%s\n%s" % (r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(autouse=True)
def _node_is_a_hard_dependency():
    """**没有 `node` 就红，不是 skip**（Task 10 修复轮 1 / C2）。

    为什么不是 `skipif`（改之前的样子，复审实测：5 skipped、`rc=0`）：那三条性质
    在**源码文本断言**那边**本来就是空的**（M8/M9/M10 三个变异下 `test_console_page.py`
    的三条同名断言全绿）⇒ 没有 node 时它们**一道守都没有**，而「全绿」会**静默地**
    退化成「948 + 5 skip」。这一片的主题就是「没有静默的路径」，所以这件事要**响**。

    **代价（说清）**：没有 node 的机器上，这一份会**红 6 条**（不是 5 条 —— 这条自己也算），
    而不是安安静静地跳过。要让它绿，装一个 node（本机实测 v22.22.1）；
    真的不想装，就在 CI 里**显式**把这一份排除并记账 —— **别让它变成 skip**。
    """
    if NODE is None:
        pytest.fail(
            "这台机器上没有 `node`（`command -v node` 没找到）—— 这一份执行夹具"
            "（Task 10 Step 2.5）钉着 4 条**只有浏览器看得见**的性质，而源码文本断言那边"
            "一条都拦不住（复审实测：M8/M9/M10 下那三条同名源码断言全绿）。"
            "⇒ 缺了 node，这几条性质**一道守都没有**（不是「守得弱」）。"
            "装一个 node（本机实测 v22.22.1 够用）；真不想装就在 CI 里**显式**排除这一份"
            "并把它记进账 —— **不要退回 `skipif`**（那会让全量套件在别的机器上静默变绿）。")


def test_the_fixture_reads_the_very_file_the_route_serves():
    """夹具切的是**路由读的那个文件**（`service.CONSOLE_PATH`），不是仓库里另抄的一份。

    设计注 §8.4：页面是**可读的资产**。夹具外挂的纪律也在这里：`agent/console.html`
    **一个字节都不改** —— 脚本是从那个文件里**当场切出来**的（文件名与路由同源，
    没有第二份可以漂）。
    """
    assert NODE is not None, "没有 node"
    source = service.CONSOLE_PATH.read_text(encoding="utf-8")
    assert "<script>" in source, "页面里没有可切的脚本"
    assert hashlib.md5(service.CONSOLE_PATH.read_bytes()).hexdigest(), "文件读得到"
    assert DRIVER.is_file(), DRIVER
    #: 驱动脚本把假 `#errBox` 的起点设成 `hidden = true` —— 那是照**静态标记**抄的。
    #: 标记哪天不带那个属性了，这一条红（否则那句「开页时它是空的」量的是夹具不是页面）。
    assert re.search(r'id="errBox"[^>]*\bhidden\b', source.split("<script>")[0]), \
        "静态标记里 `#errBox` 不再带 `hidden` —— 夹具的起点与页面不一致了"


def test_a_sentence_the_service_said_stays_on_screen_through_the_repaints(tmp_path):
    """**Task 7 复审点名过的那一族**：真话写上去之后，**重画不许把它擦掉**。

    驱动脚本走的是真人那条路：点「停」→ 服务回 404（页面把 `detail` **原样**摆出来）
    → 紧接着**重拉** `/live`（`act` 那条路自己会拉）→ 左边那一栏也坏掉（`/runs` 500）
    → 之后**每 3 秒一次重画**（`/live` 一直在变，这是「在跑的任务」的真实样子）。

    这一条要的是：**三处地方的那句话都还在**（错误条 / 左边那一栏 / 「停」那行小字）。
    全是「写进去之后还在不在」，只有真跑起来才看得见。

    ⚠️ 「重画真的发生了」是**量出来的**，而且量的是**重画**、不是 fetch
    （Task 10 修复轮 1 / C1）：驱动脚本给 `#timeline` 的 `innerHTML` 装了写计数器
    （`paintTimeline` 每画一次就写它一次 ⇒ 这个数 = `paint()` 的次数）。页面每 3 拍
    **无条件** fetch、而重画由 `if (key !== seen)` 单独决定 —— 拿 fetch 数装样子的话，
    载荷一旦不变，这一条照绿而夹具的牙全没（复审实测：三种载荷下 fetch 都是 10、
    重画是 10 / 3 / 2）。
    """
    out = _drive(tmp_path, final_mode="gate")

    # 量具**不是瞎的**：这几跳真的被走过了（不然下面几条是空过）
    urls = out["urls"]
    assert urls.count("/runs") >= 2, urls
    assert urls.count("/job/job-1/stop") == 1, urls
    # ★ 重画次数 == `/live` 的 fetch 次数：每一份正文都不一样 ⇒ 每一次都真的重画了
    live_fetches = urls.count("/job/job-1/live")
    assert live_fetches >= 8, urls
    assert out["paints"] == live_fetches, (
        "重画次数（%d）不等于 `/live` 的 fetch 次数（%d）—— 有几次取回正文**没有重画**"
        "（正文没变？），这一条用例量到的就不是「重画擦不掉」：%r"
        % (out["paints"], live_fetches, urls))
    #: 「左边那一栏坏掉」之后**确实又重画过**（不然第 ③ 步是个空步）
    marks = out["paintMarks"]
    assert marks["end"] - marks["afterRunsBroken"] >= 3, marks

    # ① 按下去那一刻：服务的原话上屏（**原样**，不是页面自己编的一句）
    assert out["afterStop"]["errBox"] == ERR_STILL, out["afterStop"]
    assert out["afterStop"]["errHidden"] is False, out["afterStop"]
    # 「停」没请求成那一行小字也在（`stopIt` 的失败分支）
    assert STOP_HINT_STILL in out["afterStop"]["stopHint"], out["afterStop"]
    # 量具的正控：那一刻它是**新写的**（开页时这个框还是空的）
    assert out["afterLoad"]["errBox"] == "" and out["afterLoad"]["errHidden"] is True, out["afterLoad"]

    # ② 左边那一栏坏了 → 它说真话（不是停在一个空 div 上，也不是那句「还没有任何运行」）
    assert RUNS_STILL in out["afterRunsFailure"]["runs"], out["afterRunsFailure"]["runs"]
    assert RUNS_500 in out["afterRunsFailure"]["runs"], out["afterRunsFailure"]["runs"]

    # ③ ★ 又 3 次重画之后：三句话**一句都没被擦掉**，而过期列表**没上屏**
    after = out["afterRepaint"]
    assert after["errBox"] == ERR_STILL, "错误条被重画擦掉了：%r" % after
    assert after["errHidden"] is False, after
    assert RUNS_STILL in after["runs"], "左边那一栏那句真话被过期列表盖掉了：%r" % after["runs"]
    assert 'class="runrow' not in after["runs"], "旧列表被照画回去了（真话被盖掉）：%r" % after["runs"]
    assert STOP_HINT_STILL in out["afterStop"]["stopHint"], out["afterStop"]


def test_the_page_says_the_two_cells_disagree_instead_of_picking_one(tmp_path):
    """服务那两格（`status` / `input.mode`）对不上时，页面**照抄两格 + 说出来**。

    ⚠️ 这一条**只有跑起来才钉得住**：两句话是同一段 `innerHTML` 的**两个分支**，
    源码里都看得见 —— 看见「分支在」不等于看见「这一刻走了哪一支」。
    两半都测（同一份夹具、只换 `input.mode` 那一格）：对不上 ⇒ 说出来；一致 ⇒ **不许说**。
    """
    mismatch = _drive(tmp_path, final_mode="queue")["afterRepaint"]
    assert "对不上" in mismatch["secondHint"], mismatch["secondHint"]
    assert "排队" in mismatch["secondHint"] and "停在闸上" in mismatch["secondHint"], \
        "说了「对不上」却没说清是**哪两格**对不上：%r" % mismatch["secondHint"]

    consistent = _drive(tmp_path, final_mode="gate")["afterRepaint"]
    assert "对不上" not in consistent["secondHint"], \
        "两格一致的时候也在喊「对不上」—— 那是把判据用反了：%r" % consistent["secondHint"]


def test_the_again_button_really_changes_the_screen(tmp_path):
    """**N-3**（Task 10 修复轮 1 / C3）：按下「重新来一遍」之后，页面上**真的变了**。

    修之前那一屏：`act("again", {})` 发完请求就**没人接那个响应** —— 旧那一趟照旧停在
    「跑挂了」上，通知栏一个字没有，运营只会以为按钮坏了（**运营看得见的一个按钮按下去
    没有反馈**，正是这一片的主题）。修之后两件事一起发生：页面**跟着新那一趟走**
    （`#whoJob` 变成新 id、`/live` 真的改成拉新的那个 job）+ **把服务那句原话摆出来**。

    ⚠️ 这一条**只有跑起来才钉得住**：接不接那个响应，源码文本断言两边都绿
    （Task 7 那 29 条里没有一条**跑**过它）。
    """
    out = _drive(tmp_path, scenario="again")

    # 开页：这一趟到头了 ⇒ 那个按钮**露头**（`over` 那一档），屏幕上还停在 job-1
    assert "job-1" in out["afterLoad"]["who"], out["afterLoad"]
    assert out["afterLoad"]["againHidden"] is False, out["afterLoad"]

    # 按下去：服务回的是**新的一趟**（job-2）
    assert out["urls"].count("/job/job-1/again") == 1, out["urls"]
    assert "/job/job-2/live" in out["urls"], (
        "页面没有跟着新那一趟走（一次都没去拉新 job 的 `/live`）：%r" % out["urls"])
    assert "job-2" in out["afterAgain"]["who"], out["afterAgain"]
    # **服务那句原话**（`AGAIN_SAY`）在屏幕上 —— 一字不差地照抄，页面不自己编一句
    assert AGAIN_SAY in out["afterAgain"]["notices"], out["afterAgain"]["notices"]
    # ★ 画面上那**一趟的内容**真的换了（修复轮 2 / D1 改准）：时间线里现在是**新那一趟**的事件，
    #   而**旧那一趟**那条已经不在屏幕上。⚠️ 两趟的事件文字必须**不同**（`OLD_TAG` / `NEW_TAG`）——
    #   文字一样的话这条断言在两趟之间分不出来（复审实测：旧那一趟的时间线里本来就有那句话）。
    assert NEW_TAG in out["afterAgain"]["timeline"], (
        "时间线上没有新那一趟的事件 —— 屏幕还停在旧那一趟上：%r" % out["afterAgain"]["timeline"])
    assert OLD_TAG not in out["afterAgain"]["timeline"], (
        "时间线上还是**旧那一趟**的事件：%r" % out["afterAgain"]["timeline"])
    # ⚠️ 下面这一条**是防线，不是钉子**（复审实测：M14 下它照过）—— 成功路径上没有任何人写
    #    `#errBox`（`act()` 开头先清一次），所以它只在「页面把一个成功当失败」时才会响。
    #    留着是因为它便宜，**别把它读成「这条钉住了什么」**。
    assert out["afterAgain"]["errBox"] == "" and out["afterAgain"]["errHidden"] is True, out["afterAgain"]


def test_the_download_is_not_there_until_the_artifact_really_is(tmp_path):
    """**产物出现之前那个位置不存在；出现了就是真的能下；而且它活得过重画**（Task 11 / §15.4）。

    三件事一条链（一条链是因为它们说的是**同一个位置**的三个时刻）：

    ① 开页时在跑、还没有产物 ⇒ 屏幕上**没有**那一步（不是灰按钮、也不是一个空链接 ——
       §15.4 的原话是「不是灰着的按钮，是**还没有**」）；
    ② `/live` 里出现了那一格 ⇒ 页面上那一步**在了**，而且照抄的是**服务给的那三格**
       （地址 / 文件名 / 路径 —— 页面不许自己拼）；
    ③ 之后**又三次重画** ⇒ 它**还在**（「写进去」≠「还在」—— Task 7 那一族在这一片
       出现过 4 次，而源码文本断言一条都拦不住）。

    ⚠️ 这一条**只有跑起来才钉得住**：三个时刻的差别全在「这一刻画出来的那一份 HTML 里有什么」，
    而源码里 `artifactStep` 那一段三个分支一直都在（源码文本断言照绿）。
    ⚠️ ① 量的是**那一步在不在**（`STEP_MARK`），不是「有没有按钮」——
       只盯按钮的话，「恒画一个空壳子」那种改法量不出来（壳子里本来就没有按钮，实测过）。
    """
    out = _drive(tmp_path, scenario="artifact")

    # 量具**不是瞎的**：这一趟真的走到了「产物出现之后」那几次重画（不然 ③ 是个空步）
    marks = out["paintMarks"]
    assert marks["afterArtifact"] > marks["afterLoad"], marks
    assert marks["end"] - marks["afterArtifact"] >= 3, marks

    # ① 产物出现之前：那个位置**不存在**
    before = out["afterLoad"]["timeline"]
    assert STEP_MARK not in before, "产物还没出现，页面上就已经有了那一步：%r" % before
    assert DL_MARK not in before, before

    # ② 出现了：地址 / 文件名 / 路径都是**服务给的那三格**（页面不自己拼）
    mid = out["afterArtifact"]["timeline"]
    assert STEP_MARK in mid, "产物那一格出现了，页面上却没有那一步：%r" % mid
    assert DL_MARK in mid, "产物那一格出现了，页面上却没有下载：%r" % mid
    assert 'href="%s"' % ART_URL in mid, mid
    # ⚠️ 文件名要按 `download="…"` 那一格量，**不能只量那串字在不在这份 HTML 里**：
    #    路径那一行里本来就有同一串字（`…/sites/example-funnel.py`）——
    #    那样量的话，把链接上的文件名拿掉照绿（实测过：M7）。
    assert 'download="%s"' % ART["filename"] in mid, \
        "下下来叫什么没摆出来：%r" % mid
    assert ART["path"] in mid, "「它写到哪了」没摆出来（人要拿它去对账）：%r" % mid
    assert ART_SAY[:20] in mid, "服务那句人话没上屏：%r" % mid

    # ③ ★ 又三次重画之后：它**还在**（重画不许把它擦掉）
    end = out["afterRepaint"]["timeline"]
    assert STEP_MARK in end, "产物那一步被下一次重画擦掉了：%r" % end
    assert DL_MARK in end, end
    assert 'href="%s"' % ART_URL in end, end
    assert ART["path"] in end, end


def test_a_run_that_ended_without_an_artifact_says_so_and_offers_no_button(tmp_path):
    """**A14**：一趟**没有产出**的运行，页面**明确说出没有产出以及为什么**。

    **不许**出现一个点了没反应、或者下下来一个空文件的下载（§15.5 的原话）——
    所以这一条同时要：那句人话**在屏上**、而且**没有**任何下载/地址。
    """
    out = _drive(tmp_path, scenario="artifact-missing")

    # 量具**不是瞎的**：这一趟真的重画过（不然下面量的是第一帧）
    assert out["paintMarks"]["end"] - out["paintMarks"]["afterLoad"] >= 3, out["paintMarks"]

    end = out["afterRepaint"]["timeline"]
    assert STEP_MARK in end, "到头了却没产出的那一趟，页面上连那句话都没有：%r" % end
    #: 服务那句「没有可交付的产物」**原样**上屏（两段都不许少 —— 见 `_thin`）
    bold, tail = _thin(NO_ART_SAY)
    assert "<b>%s</b>" % bold in end, \
        "服务那句话的粗体那一段没上屏：%r" % end
    assert tail[:16] in end, "服务那句话的尾巴没上屏：%r" % end
    assert "撞上限" in end, "说了「没有产物」却没说是**为什么**：%r" % end
    assert DL_MARK not in end, "没有产物却摆了一个下载（A14 点名不许的形状）：%r" % end
    # 开页时（还在跑）那个位置也不存在 —— 与上一条同一个判据的另一头
    assert STEP_MARK not in out["afterLoad"]["timeline"], out["afterLoad"]


def test_the_fixture_can_actually_fire(tmp_path):
    """**正控**：改坏页面里一处**已知会破坏行为**的写法 ⇒ 同一个断言必须红。

    改的是 `fetchLive` 里那道「只收**拉数据那一类**的错误」的判据
    （`state.errFrom === "live"` ⇒ 恒真）：源码文本断言全都照旧通过
    （`errFrom` 那几个字还在那一行上），而**屏幕上的行为**是「按下去 404 → 紧接着那次
    重拉成功 → 那句话当场被擦掉」—— 正是修复轮 1 实测踩到的那个坑（按下去好像什么都没发生）。
    ⚠️ 这一份**只改副本**（写在 `tmp_path` 里），主仓的 `console.html` 一个字节不动。
    """
    original = service.CONSOLE_PATH.read_text(encoding="utf-8")
    broken = original.replace('if (state.errFrom === "live") { setErr(""); }',
                              'if (state.errFrom === "live" || true) { setErr(""); }')
    assert broken != original, "正控没改动任何东西（判据那一行没找到？）—— 那这条正控是空的"
    page = tmp_path / "console-broken.html"
    page.write_text(broken, encoding="utf-8")

    good = _drive(tmp_path, final_mode="gate")
    bad = _drive(tmp_path, final_mode="gate", page=page)
    #: 同一个观测、同一句话：好的那份留着，坏的那份**当场被擦掉** ⇒ 夹具对行为有分辨力
    assert good["afterStop"]["errBox"] == ERR_STILL, good["afterStop"]
    assert bad["afterStop"]["errBox"] != ERR_STILL, (
        "把「只收拉数据那一类错误」改成恒真之后，夹具**没有响** —— "
        "那说明这一套夹具是瞎的（观测值照旧是 %r）" % bad["afterStop"]["errBox"])


def test_the_console_file_is_not_a_copy_and_was_not_touched(tmp_path):
    """夹具跑一趟**不许改** `agent/console.html`（外挂的夹具，一个字节都不许改）。"""
    before = hashlib.md5(service.CONSOLE_PATH.read_bytes()).hexdigest()
    _drive(tmp_path, final_mode="gate")
    assert hashlib.md5(service.CONSOLE_PATH.read_bytes()).hexdigest() == before, \
        "跑了一趟夹具之后页面文件的指纹变了"


# ═════════════════ Task 12：开一趟 / 窗口那两下（**运营不碰命令行**）═════════════════
#
# 病（用户 2026-09-20 在真面板上量的）：
#   ① 「我自己不能再面板上重派吗？那很不方便啊」—— 页面能发出去的动作只有四个
#      （`/reply` `/say` `/stop` `/again`），**没有 `/run`**；而「重新来一遍」只在**跑完**
#      之后才出现、拿的还是**旧那一趟**的 brief ⇒ **面板上开不了一趟新活**（只有人敲 curl）。
#   ② 「我点继续没任何反应啊」—— `/reply` 先查窗口：窗口没了就 409，而「怎么办」那一步
#      埋在长文本中间。
#   ③ 窗口那两下（派之前核「已关」/ 跑完关窗）原先也落在命令行上。
#
# 这一节钉的是**页面那一半**（服务那一半在 `test_service_window_hands.py` 里）：
# 表单发出去的是不是 `/run` 要的那几格、服务回了错页面摆的是不是**它自己那句话**、
# 窗口那两个按钮在不在**只看服务给的那两格**、以及那些话**写上去之后还在不在**。

#: 页面自己声明的那两跳 + 它自己写的那句按钮文案（**页面侧的常量，服务里没有**）——
#: 所以这里只能抄一份。⚠️ 抄一份就得有东西把它**钉回页面**：
#: `tests/test_console_page.py::test_the_page_names_every_hop_it_will_call_...` 钉着
#: `"run": "/run"` 那几个字面量、`test_the_stop_button_says_it_cannot_be_undone` 那一族
#: 钉着按钮文案 —— 页面哪天改了名，那几条先红，这一份跟着改。
RUN_HOP = "/run"
STOP_LABEL = "停下（撤不回）"

#: 「开一趟」表单里人打进去的那些字（载荷里那份 `run` 就是它们）。
RUN_FORM = {"url": "https://example-funnel.test/quiz",
            "goal": "走到报价那一页，把价拿到",
            "success_text": "Thank you",
            "mode": "fix",
            "evidence": "FMR 单 20481：第二步点不动「下一步」"}
#: 新开出来那一趟的 job id（页面的 `pickJob` 要跟着它走）。
NEW_JOB = "job-new-one"
#: 服务对 `/run` 回的那句人话（**从服务自己的常量算出来**，不在这儿手抄一遍）。
SUBMITTED_SAY = service.SUBMITTED_SAY
#: `/run` 被拒时那句人话 —— **走服务那条真路算出来**（`_intake_problems` 一张单子 +
#: `_problems_say` 那句头）。⚠️ 不许手抄：这一份要证明的正是「页面摆的是**服务说的**那一句」，
#: 手抄一句就变成「页面摆的是一句和它长得一样的字」。
def _intake_say(**over) -> str:
    svc = service.Service()
    body = service.RunRequest(**over)
    problems = svc._intake_problems(body)
    assert problems, "这份载荷居然没有可挑的毛病 —— 那这条判据就没东西可量了"
    return svc._problems_say(problems)


RUN_400 = _intake_say()
#: 窗口那两下的答复（`/live` 里那一格 + 关掉之后服务说的那句）。
WIN_STATE_SAY = service.WINDOW_STATE_SAY[service.measure.DEAD]
WIN_STATE_LIVE_SAY = WIN_STATE_SAY + service.Service._measured_at_say("2026-09-20T09:31:02+08:00")
CLOSE_SAY = service.CLOSE_WINDOW_DONE_SAY % WIN_STATE_LIVE_SAY
REOPEN_SAY = "窗口重开了，从上次停下的地方接着跑（探路那一段不重来）。"


def _window_cell(*, can_close: bool, can_reopen: bool) -> dict:
    """`/live` 的 `window` 那一格（**服务给的那几格**，页面不许自己推）。"""
    return {"worker": "192.168.1.222", "bit_id": "8f2c1a90", "api_port": 54345,
            "how": "连到**那台机器**的桌面……", "when": "**它正在跑（running）的时候不要动手**……",
            "state": "dead", "state_say": WIN_STATE_LIVE_SAY,
            "can_close": can_close, "can_reopen": can_reopen}


def _run_payloads() -> dict:
    """「开一趟」：**开页时这一屏还没挑运行**（真实的入口）→ 人填表 → 按一下 → 跟着新那趟走。"""
    lives = [{"body": _live("running", "queue", n=1 + i, tag="新开的",
                            window=_window_cell(can_close=False, can_reopen=False))}
             for i in range(4)]
    _assert_all_different([x["body"] for x in lives], "`/live` 的正文")
    return {"scenario": "run", "search": "", "run": RUN_FORM,
            "responses": {
                "/runs": [{"body": {"note": "还没有任何运行。", "runs": []}}],
                "/run": [{"body": {"job_id": NEW_JOB, "say": SUBMITTED_SAY}}],
                "/job/%s/live" % NEW_JOB: lives,
            }}


def _run_refused_payloads() -> dict:
    """「开一趟」**被服务拒了**：400 + 服务那张人话单子 ⇒ 原样上屏，而且**不许**跟着换趟。"""
    lives = [{"body": _live("waiting", "gate", n=1 + i)} for i in range(8)]
    _assert_all_different([x["body"] for x in lives], "`/live` 的正文")
    return {"scenario": "run-refused", "search": "?job=job-1",
            "responses": {
                "/runs": [{"body": {"note": "", "runs": [
                    {"job_id": "job-1", "site": "example-funnel", "status": "waiting",
                     "say": "停下来了，在等你一句话。",
                     "created_at": "2026-09-19T21:00:00+08:00", "rounds": 1,
                     "delivered": False}]}}],
                "/job/job-1/live": lives,
                "/run": [{"status": 400, "body": {"detail": RUN_400}}],
            }}


def _window_payloads() -> dict:
    """窗口那两下：开页时服务说**可以关也可以重开** → 之后它改口说**都不给**
    （量「按钮跟的是服务那两格，不是页面自己算的」）。"""
    yes = {"body": _live("waiting", "gate", n=1,
                         window=_window_cell(can_close=True, can_reopen=True))}
    no = [{"body": _live("waiting", "gate", n=2 + i,
                         window=_window_cell(can_close=False, can_reopen=False))}
          for i in range(6)]
    _assert_all_different([yes] + no, "`/live` 的正文")
    return {"scenario": "window", "search": "?job=job-1",
            "responses": {
                "/runs": [{"body": {"note": "", "runs": [
                    {"job_id": "job-1", "site": "example-funnel", "status": "waiting",
                     "say": "停下来了，在等你一句话。",
                     "created_at": "2026-09-19T21:00:00+08:00", "rounds": 1,
                     "delivered": False}]}}],
                "/job/job-1/live": [yes] + no,
                "/job/job-1/window/close": [{"body": {"state": "dead",
                                                      "state_say": WIN_STATE_LIVE_SAY,
                                                      "say": CLOSE_SAY}}],
                "/job/job-1/reopen": [{"body": {"job_id": "job-1", "say": REOPEN_SAY}}],
            }}


# ── Task 14 修复轮 1：闸口摊开的事实要**上屏** ────────────────────────────
#: 那份开场白里**要害那一格的值** —— 页面上必须看得见它（不是「页面上有『成功判据』
#: 这三个字」就算过：键名与值**两样都要上屏**，值才是运营要认的东西）。
SUCCESS_TEXT = "Thank you"


def _gate_facts() -> dict:
    """那道闸摊开的原始事实 —— **从真生产者算出来**（`graph._brief_facts`，
    与 `_explore` 往 facts 里补「预算」那一步同一个形状）。不手抄的原因与 `RUN_400`
    同一条：手抄一份就变成「页面摆的是一句和它长得一样的字」。

    ⚠️ `成功判据` 这一格**必须在里面** —— 它就是这份开场白的要害（复审 2026-09-20 §4.2）。

    ⚠️ **这个函数只能在用例里调**（`_gate_facts_payloads()` 是这么用的）：在模块顶上
    调它、里面又带 `assert` 的话，生产那边一改（开场白少一格）就不是「用例红」而是
    **整个文件收集失败**（`ERROR collecting tests/test_console_js.py`）—— 实测踩过：
    量具会把那种 ERROR 读成「红」，而它其实**一条用例都没跑**。
    """
    state = {"url": "https://example-funnel.test/quiz",
             "goal": "走到「Thank you」那一页，把报价拿到",
             "success_text": SUCCESS_TEXT,
             "ws_url": "ws://192.168.1.197:55555/devtools/page/ABC",
             "allow_skips": ["换个窗口大小", "换个时区"]}
    facts = graph._brief_facts(state, graph.Deps(), [])
    assert facts["成功判据"] == SUCCESS_TEXT, facts
    facts["预算"] = "这一趟最多 80 轮、100 步（到顶就停，且说清为什么停）。"
    return facts


def _round_card(n: int, *, step: str = "explore", done: dict = None,
                facts: dict = None, say: str = "") -> dict:
    """`/live.rounds[]` 的一张卡（**服务投影出来的那几格**，页面只渲染、不推断）。"""
    return {"n": n, "step": step, "step_say": graph.STEP_SAY[step],
            "say": say, "revisable": False,
            "now": {"n": n, "name": "pause-%d.png" % n, "why": ""},
            "last_shot": None, "done": done, "facts": facts or {}}


def _gate_facts_payloads() -> dict:
    """闸口摊开的事实那一趟（Task 14 修复轮 1）：停在第 2 道闸上、**两张卡都在屏上**。

    两张卡各钉一件事，一次驱动全量掉：
      - **第 1 轮**（`done` 是 `null` = 第一道闸）：它那句提示原先写的是
        「第一道闸之前**什么都没跑过**」—— `intake` 不设闸之后**那句话是假的**
        （复审 2026-09-20 §4.4 的 M4：改掉它，全量 **0 红**）；
      - **第 2 轮**（`here`）：闸口摊开的**原始事实**（`成功判据` 在里面）——
        复审 §4.2：它原先只到得了 API 载荷，这一屏**一格都不读** ⇒ 屏幕上没有它。

    ⚠️ 「每一份正文都不一样」是夹具的牙（页面只在正文变了才重画）—— 事件数在涨。
    """
    facts = _gate_facts()                    # ⚠️ **在这儿**算（不在模块顶上）：见那个函数的说明
    done = {"step": "explore", "step_say": graph.STEP_SAY["explore"],
            "say": "探路走完了，账本上有 12 步。",
            "shots": {"before": "pause-1.png", "after": "pause-2.png"},
            "steps": [], "steps_note": "这一轮没有探路的步子清单：刚才那一步是「开工前的确认」。"}
    cards = [_round_card(1, done=None), _round_card(2, done=done, facts=facts,
                                                    say="接下来要打开真浏览器，把这一页走一遍。")]
    lives = []
    for i in range(10):
        body = _live("waiting", "gate", n=1 + i)
        body["rounds"] = cards
        body["gate"] = {"step": "explore", "step_say": graph.STEP_SAY["explore"],
                        "ask": "接下来要打开真浏览器，把这一页走一遍。",
                        "facts": facts, "can": list(graph.HUMAN_CAN), "revisable": False}
        lives.append({"body": body})
    _assert_all_different([x["body"] for x in lives], "`/live` 的正文")
    return {"scenario": "gate-facts", "search": "?job=job-1",
            "responses": {
                "/runs": [{"body": {"note": "", "runs": [
                    {"job_id": "job-1", "site": "example-funnel", "status": "waiting",
                     "say": "停下来了，在等你一句话。",
                     "created_at": "2026-09-19T21:00:00+08:00", "rounds": 2,
                     "delivered": False}]}}],
                "/job/job-1/live": lives,
            }}


def test_the_brief_the_operator_is_confirming_is_on_the_screen_not_just_on_the_wire(tmp_path):
    """★ Task 14 修复轮 1 的正身：那份开场白（**`成功判据` 在里面**）真的印在运营那一屏上。

    复审 2026-09-20 量出来的那个洞：`facts` 只到得了 `/job/{id}` 与 `/live` 的 `gate`
    那两格**载荷**，而 `agent/console.html` 读的是 `card.*` —— 它**一格都不读 `gate.facts`**。
    ⇒ `intake` 不设闸之后「成功判据」从屏幕上**消失**了（旧的 `intake` 的 `say` 里那句
    「成功判据是「Z」」原先经由 `card.say` **直达屏幕**）；把 `service._project` 的 facts
    清空，**全量 0 红**。这一条钉的就是**运营真看得见的那一层** —— `#rounds`。

    三样一起量（全是「屏幕上此刻是什么」，不是「载荷里有什么」）：
      ① 键名与**值**都上了屏（`成功判据` / `Thank you`），而且**只有脚下那一轮**有
         （`data-gate-facts` 恰好一个 —— 历史轮次不许从闸上借这一格）；
      ② 第一道闸那一轮说的那句**不是**「之前什么都没跑过」（`intake` 跑过，那句话是假的）；
      ③ 两样都**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
    """
    out = _drive(tmp_path, scenario="gate-facts")

    # 量具不是瞎的：真的重画过（不然 ③ 是空过）
    assert out["paintMarks"]["afterRepaint"] - out["paintMarks"]["afterLoad"] >= 3, \
        out["paintMarks"]

    for where in ("afterLoad", "afterRepaint"):
        html = out[where]["rounds"]
        # ① 那份开场白在屏幕上（键名 + 值都在），而且**只有脚下那一轮**有
        assert 'data-gate-facts="1"' in html, (where, html[-600:])
        assert html.count('data-gate-facts="1"') == 1, (
            "%s：闸口事实那一块出现了不止一次（历史轮次也从闸上借了这一格？）：%r"
            % (where, html.count('data-gate-facts="1"')))
        assert "成功判据" in html, (
            "%s：屏幕上没有「成功判据」这一格 —— 页面渲染的是卡片，`facts` 只躺在载荷里"
            "就等于运营看不见它（复审 2026-09-20 §4.2）" % where)
        assert SUCCESS_TEXT in html, (
            "%s：「成功判据」的值没上屏 —— 键名在、值不在，运营认不出要确认什么" % where)
        # ② 第一道闸那一轮那句**不许**是「之前什么都没跑过」
        assert "没有可显示的" in html, (where, "第一道闸那一轮那句提示不见了")
        assert "什么都没跑过" not in html, (
            "%s：屏幕上又出现了「什么都没跑过」—— `intake` 不设闸之后（2026-09-20）"
            "那句话是假的（它跑过：校验开场白、修站那条路还读了底稿）" % where)


def test_the_page_says_it_will_stop_and_ask_before_every_expensive_step(tmp_path):
    """★ F14：那句**承诺**要真的在运营那一屏上（复审 2026-09-20 §4③2，修复轮 2 接的）。

    旧 `intake` 的 `say` 里有一句「**开工之后每一步之前都会再问你一次**，随时可以喊停或
    纠正」—— `intake` 不设闸（2026-09-20）之后**全仓没有一处说它了**：复审量的
    `grep -c "喊停\\|随时可以\\|纠正" agent/console.html` = **0**。
    而它说的是一件**真的**事（`graph`：每个花钱 / 动真页面的节点开工之前都 `interrupt()`
    一次，5 道闸一道没少）⇒ 补的是「**把它说出来**」，不是编一句新的。

    量的是**屏幕上此刻的文本**（`#roundsSub`），而且**活过之后三次重画**。
    ⚠️ 措辞不许写成「每一步」那种**比事实宽**的话：`intake` 不设闸 ⇒ 说「每一步」就是
    一句假话（与被判「名字太宽」的那条用例同一种病）。所以第三条断言钉的是那句限定语。
    """
    out = _drive(tmp_path, scenario="gate-facts")
    assert out["paintMarks"]["afterRepaint"] - out["paintMarks"]["afterLoad"] >= 3, \
        out["paintMarks"]
    for where in ("afterLoad", "afterRepaint"):
        sub = out[where]["roundsSub"]
        assert "每一步都会先停下来问你一次" in sub, (
            "%s：那一句承诺不在屏上 —— `intake` 不设闸之后没人说它了（复审 F14）：%r"
            % (where, sub))
        assert "喊停" in sub and "打回带话" in sub, (
            "%s：承诺里「随时可以喊停 / 打回带话」那半不见了：%r" % (where, sub))
        assert "开真窗口 / 动真页面" in sub, (
            "%s：那句限定语不见了 —— 少了它，「每一步」就是一句比事实宽的话"
            "（`intake` 不设闸）：%r" % (where, sub))


def test_the_operator_can_start_a_new_run_from_the_panel(tmp_path):
    """★ Task 12 的正身：**面板上开得了一趟新活** —— 而且这一屏**跟着新那一趟走**。

    三样一起量（缺一样这条路就没通）：
      ① 那一跳**发到哪** —— 必须正好是 `POST /run`（不是 `/job/<id>/run`，那会儿还没有 id）；
      ② 正文**是 `/run` 要的那几格** —— 从 `sent`（请求正文）读，不是从 URL 猜；
      ③ 拿到 job id 之后 `whoJob` 换成新的那一个、并且真去拉了新那一趟的 `/live`
         （不跟着走 = 屏幕上什么都不变 = 运营以为按钮坏了 —— Task 10 修复轮 1 / N-3 同一个坑）。
    """
    out = _drive(tmp_path, scenario="run")
    runs = [x for x in out["sent"] if x["url"] == RUN_HOP]
    assert len(runs) == 1, "按一下「开一趟」应该正好发一次 `POST /run`：%r" % out["sent"]
    assert json.loads(runs[0]["body"]) == {
        "url": RUN_FORM["url"], "goal": RUN_FORM["goal"],
        "success_text": RUN_FORM["success_text"], "mode": RUN_FORM["mode"],
        "evidence": RUN_FORM["evidence"]}, runs[0]["body"]
    # ② 页面上**没有**预先替服务判哪一格必填：四格都填了，正文里就是四条原样（没加没减）
    assert RUN_HOP not in out["afterLoad"]["who"], out["afterLoad"]
    # ① 开页时没挑运行；开完**跟着新那一趟走**
    assert out["afterLoad"]["who"] == "还没挑运行", out["afterLoad"]
    assert out["afterRun"]["who"] == NEW_JOB, out["afterRun"]
    assert "/job/%s/live" % NEW_JOB in out["urls"], out["urls"]
    assert NEW_JOB in out["afterFollow"]["who"], out["afterFollow"]
    # 服务回的那句话**上了屏**（`pickJob` 的挑法说明；页面不自己编一句）
    assert SUBMITTED_SAY in out["afterRun"]["notices"], out["afterRun"]["notices"]


def test_the_run_form_only_reveals_the_evidence_cell_when_the_mode_is_fix(tmp_path):
    """`mode` 与 `evidence` 的联动：**选「老站」才把那一格露出来**，选回「新站」就收回去。

    ⚠️ 页面**只负责露/收**，「要不要它」由服务判（它会说）——
    所以这一条量的是**那一格的隐显**，不是「页面有没有检查它填了没有」。
    """
    out = _drive(tmp_path, scenario="run")
    assert out["afterLoad"]["evidenceHidden"] is True, "开页（新站）时那一格就露着：%r" % out["afterLoad"]
    assert out["afterPickFix"]["evidenceHidden"] is False, "选了「老站」那一格没露出来"
    assert out["afterPickBuild"]["evidenceHidden"] is True, "选回「新站」那一格没收回去"


def test_a_run_the_service_refused_puts_the_services_own_sentence_on_screen(tmp_path):
    """服务拒了这一趟（400）→ **它那句话原样上屏**，而且**活过之后三次重画**。

    这是这一屏的老规矩（`errBox` 那条路）：**页面不许自己编一句「失败了」**。
    它编的那一句会盖掉服务那句话里的**具体是哪一格不对** —— 而那正是运营唯一能照着改的东西。
    一并量：被拒之后这一屏**不许**跟着换趟（它压根没拿到 job id）。
    """
    out = _drive(tmp_path, scenario="run-refused")
    run_posts = [x for x in out["sent"] if x["url"] == RUN_HOP]
    assert len(run_posts) == 1, out["sent"]
    assert out["afterRun"]["errBox"] == RUN_400, out["afterRun"]
    assert out["afterRun"]["errHidden"] is False, "那句话没上屏"
    assert "/job/%s/live" % NEW_JOB not in out["urls"], \
        "被拒了却去拉了「新那一趟」：%r" % out["urls"]
    assert NEW_JOB not in out["afterRun"]["who"], "被拒了却跟着换趟了：%r" % out["afterRun"]
    #: **活过三次重画**（「写进去」与「还在不在」是两件事 —— 这一片的老病）
    assert out["afterRepaint"]["errBox"] == RUN_400, out["afterRepaint"]
    assert NEW_JOB not in out["afterRepaint"]["who"], out["afterRepaint"]
    assert out["paints"] >= 4, "重画次数太少（这一条量不到「还在不在」）：%r" % out["paints"]


def test_the_window_buttons_are_the_ones_the_service_offered(tmp_path):
    """窗口那两个按钮在不在，**只看服务给的那两格**（`can_close` / `can_reopen`）。

    ⚠️ 页面**不许**拿 `state` / 任务状态自己推一遍 —— 推出来的那一份与服务那份一旦不一致，
    就会出现「按钮在、按下去 409」那种**点了没反应**的形状（这一片要治的正是它）。
    所以这一条把服务那两格**翻一次面**：开页时两个都给，之后它改口说都不给 ⇒
    同一个页面、同一份 `/live` 结构，按钮必须跟着**收回去**。
    """
    out = _drive(tmp_path, scenario="window")
    assert out["afterLoad"]["closeHidden"] is False, "服务说能关，按钮却没给：%r" % out["afterLoad"]
    assert out["afterLoad"]["reopenHidden"] is False, "服务说能重开，按钮却没给：%r" % out["afterLoad"]
    #: 状态那一句**照抄服务**（连「这是什么时候问的」一起）—— 按 `rich()` 会渲染成的样子比：
    #: 服务那句话里带 `**着重**`（页面把它排成粗体），整句拿去 `in` 是比不中的。
    bold, rest = _thin(WIN_STATE_LIVE_SAY)
    assert ("<b>%s</b>" % bold) in out["afterLoad"]["state"], out["afterLoad"]["state"]
    assert rest.strip()[:20] in out["afterLoad"]["state"], out["afterLoad"]["state"]
    assert "09:31:02" in out["afterLoad"]["state"], out["afterLoad"]["state"]
    #: 服务改口（都不给）⇒ 两个按钮都收回去
    assert out["afterRepaint"]["closeHidden"] is True, out["afterRepaint"]
    assert out["afterRepaint"]["reopenHidden"] is True, out["afterRepaint"]


def test_closing_the_window_says_what_the_service_said_and_it_stays(tmp_path):
    """按「关掉这个窗口」→ 走 `/job/<id>/window/close`，服务那句话原样上屏、活过三次重画。

    第二半（重开）一并量：它走的是 `/job/<id>/reopen`，而且**不带 `ws_url`**
    —— 服务自己开窗口（这一下原先要人在宿主上敲命令）。
    """
    out = _drive(tmp_path, scenario="window")
    closed = [x for x in out["sent"] if x["url"].endswith("/window/close")]
    assert closed == [{"url": "/job/job-1/window/close", "body": "{}"}], out["sent"]
    assert out["afterClose"]["errBox"] == CLOSE_SAY, out["afterClose"]
    assert out["afterClose"]["errHidden"] is False, "那句话没上屏"
    assert out["afterClose"]["disabled"] is False, "做完之后按钮还锁着（再按一下就按不动了）"
    assert out["afterRepaint"]["errBox"] == CLOSE_SAY, out["afterRepaint"]
    # 重开：**不带 `ws_url`**（服务自己去开）—— 这一格是「运营不碰命令行」那条路的正身
    reopened = [x for x in out["sent"] if x["url"].endswith("/reopen")]
    assert reopened == [{"url": "/job/job-1/reopen", "body": "{}"}], out["sent"]
    assert out["afterReopen"]["errBox"] == REOPEN_SAY, out["afterReopen"]


def test_without_a_window_layer_the_window_buttons_are_not_offered(tmp_path):
    """没接窗口层（`window` 为 `null`）⇒ 两个按钮**都不摆**，而且那一格**明说看不到**。

    这一条与上面两条是**同一个问题的反面**：`null` 不是「现在不能关」，是「这个部署
    给不出这件事」—— 摆一个灰按钮在那儿，人会一直点它。
    """
    out = _drive(tmp_path, final_mode="gate")
    assert out["afterLoad"]["closeHidden"] is True, out["afterLoad"]
    assert out["afterLoad"]["reopenHidden"] is True, out["afterLoad"]
    assert "看不到" in out["afterLoad"]["winState"], out["afterLoad"]["winState"]


def test_the_stop_button_says_it_cannot_be_undone(tmp_path):
    """★ Task 12 第 3 件（**最轻的那个选项：只改措辞**）：按钮上写着「撤不回」。

    为什么只改措辞：另外两个选项（拉开距离 / 二次确认）都要动布局或加一个阻塞弹窗，
    而布局一动，Task 7 那 29 条页面断言与这一套夹具一起要重画 —— 代价不成比例。
    **这一条的代价说清**：它**不防误点**，它只让人**按之前知道这一下的代价**。
    """
    out = _drive(tmp_path, scenario="run")
    label = out["afterLoad"]["stopLabel"]
    assert label == STOP_LABEL, label
    assert "撤不回" in label, "「停」的代价没写在按钮上：%r" % label


def test_the_run_fixture_can_actually_fire(tmp_path):
    """**正控**（Task 12 那几条自己的牙）：改坏一处已知会破坏行为的写法 ⇒ 同一批断言必须红。

    改的是 `runPayload()` 里那一格 —— 把 `success_text` 那一栏**发成空的**
    （「页面觉得服务不会用这一格，就顺手不发」）。这正是这一片最怕的那种改法：
    源码文本断言照旧全绿（`"success_text"` 那几个字还在那一行上），
    而到了服务那边，这一趟会因为「没说**什么算成功**」被当场拒 ——
    **运营填的那一格白填了，而屏幕上没人告诉他为什么**。
    """
    original = service.CONSOLE_PATH.read_text(encoding="utf-8")
    broken = original.replace('"success_text": document.getElementById("runSuccess").value.trim(),',
                              '"success_text": "",')
    assert broken != original, "正控没改动任何东西（那一行没找到？）—— 那这条正控是空的"
    page = tmp_path / "console-broken-run.html"
    page.write_text(broken, encoding="utf-8")

    good = _drive(tmp_path, scenario="run")
    bad = _drive(tmp_path, scenario="run", page=page)
    wanted = {"url": RUN_FORM["url"], "goal": RUN_FORM["goal"],
              "success_text": RUN_FORM["success_text"], "mode": RUN_FORM["mode"],
              "evidence": RUN_FORM["evidence"]}
    got_good = json.loads([x for x in good["sent"] if x["url"] == RUN_HOP][0]["body"])
    got_bad = json.loads([x for x in bad["sent"] if x["url"] == RUN_HOP][0]["body"])
    assert got_good == wanted, got_good
    assert got_bad != wanted, (
        "把「什么算成功」那一格发成空的之后，这一份夹具**没有响** —— 那说明它量不到正文"
        "（观测值照旧是 %r）" % got_bad)


# ═════════════ Task 13 ③：失败列表 → 一键把证据填进「开一趟」═════════════════
#
# 病（用户 2026-09-20 的原话）：
#   「**面板上要能**：看见某个站的失败列表 → 点一条 → **发起一趟 fix，`evidence` 已经填好**」
#   —— 在这一段之前，运营要发起修复得**从别处把失败手抄进来**（后端的失败列表
#   在另一个系统里，页面上一个字都没有）。
#
# 这一节钉的是**页面那一半**（服务那一半在 `test_service_failures.py` 里）。
# 四条判据，都是「只有跑起来才看得见」的那一类：
#
# | # | 性质 | 源码文本断言为什么拦不住 |
# |---|---|---|
# | 1 | 那一栏长出来的**是人话**（不是 `site_specific` 这种码） | 字面量在不在与**这一刻画上去的是哪一份**无关 |
# | 2 | 「量不到」与「没有失败」**在屏幕上长得不一样** | 两句都在源码里；哪一句上了屏只有跑一遍才知道 |
# | 3 | 点一条**只填表、不开跑**（`POST /run` 一次都不许发） | 「有没有发出去」不在源码文本里 |
# | 4 | 填好的那几格**活过之后三次重画** | 写进去 ≠ 还在（Task 7 那一族，实测栽过） |

#: 那一栏要查的那个站（FMR 那边的名字 —— **与 `POST /run` 的 `site` 不是一回事**）。
FAIL_SITE = "www.gowizard.com/auto-warranty/"
#: 三条失败，**人话现算**（`fmr.failure_say`）—— 不在这儿手抄一遍：
#: 那句话改一个字，这一份跟着变，不会两边漂（与 `AGAIN_SAY` / `ART_URL` 同一个做法）。
FAIL_ROWS = [
    {"task_id": "26034602", "site": "www.gowizard.com/auto-warranty", "status": "failed",
     "type": "site_specific", "country": "US", "created_at": "2026-09-19T10:21:38+08:00"},
    {"task_id": "26033398", "site": "www.gowizard.com/auto-warranty", "status": "failed",
     "type": "site_specific", "country": "US", "created_at": "2026-09-19T09:58:02+08:00"},
]
#: 页面上要挑的那一条（**挑的不是最上面那条** —— 挑最新那条的话，「挑」这个动作量不出来）。
FAIL_PICK = "26033398"
#: 服务那两跳的地址（**从服务自己的常量算出来**，不在这儿手拼一份）。
FAIL_LIST_URL = service.FAILURES_PATH + "?site=" + urllib.parse.quote(FAIL_SITE, safe="")
FAIL_EV_URL = (service.FAILURE_EVIDENCE_PATH % FAIL_PICK
               + "?site=" + urllib.parse.quote(FAIL_SITE, safe=""))
#: 那一段证据（服务给的那三样）。**它自己就是 `fmr.evidence_text` 算的** —— 同源。
FAIL_EV = fmr.evidence_text(FAIL_ROWS[1], MEASURED_STEPS)
FAIL_ENTRY_URL = MEASURED_STEPS[0]["url"]
FAIL_ROW_SAY = fmr.failure_say(FAIL_ROWS[1])
def _unmeasured_502() -> Exception:
    """服务会回的那句「量不到」—— 让 `FmrClient` **真的**在连不上的时候抛一次。

    ⚠️ 不在这儿手抄一句：这一份要证明的正是「页面上那句是**服务说的**那一句」——
    手抄就变成「页面上是一句与它长得一样的字」（与 `RUN_400` 同一个做法）。
    """
    def boom(url, headers):
        raise RuntimeError("connection refused")
    try:
        fmr.FmrClient(token="tok", opener=boom).fetch_failures(FAIL_SITE)
    except fmr.FmrUnmeasured as exc:
        return exc
    raise AssertionError("连不上居然没抛 —— 这一份载荷的判据就不成立了")


#: 量不到时服务那句人话。
FAIL_502 = str(_unmeasured_502())


def _failures_payloads() -> dict:
    """失败列表那一趟：查 → 挑一条 → 填表（**然后人自己按「开一趟」**）。

    ⚠️ **开页是挑着某一趟的**（`?job=job-1`）—— 那不是装饰：这一屏的 `paint()`
    要有 `/live` 才会跑（`if (!live) return`），不挑运行的话**一次重画都不会发生**，
    于是「填好的东西活过三次重画」那一条量的是**没有重画**（实测：这一份原先就是
    `search: ""`，而「重画把那一栏擦掉」那个变异因此**没红**）。
    """
    live_one = [{"body": _live("running", "queue", n=1 + i, tag="这一趟")} for i in range(8)]
    _assert_all_different([x["body"] for x in live_one], "`/live` 的正文")
    fresh = [{"body": _live("running", "queue", n=1 + i, tag="新开的")} for i in range(4)]
    _assert_all_different([x["body"] for x in fresh], "新那一趟 `/live` 的正文")
    return {"scenario": "failures", "search": "?job=job-1",
            "failures": {"site": FAIL_SITE, "pick": FAIL_PICK},
            "responses": {
                "/runs": [{"body": {"note": "", "runs": [
                    {"job_id": "job-1", "site": "example-funnel", "status": "running",
                     "say": "在跑。", "created_at": "2026-09-19T21:00:00+08:00",
                     "rounds": 0, "delivered": False}]}}],
                "/job/job-1/live": live_one,
                FAIL_LIST_URL: [{"body": {"site": FAIL_SITE, "since": "2026-09-19 00:00:00",
                                          "limit": 20,
                                          "say": "这个站从 2026-09-19 00:00 起有 2 条失败的记录"
                                                 "（这一屏最多摆 20 条）。",
                                          "failures": [{"task_id": r["task_id"],
                                                        "say": fmr.failure_say(r)}
                                                       for r in FAIL_ROWS]}}],
                FAIL_EV_URL: [{"body": {"task_id": FAIL_PICK, "row_say": FAIL_ROW_SAY,
                                        "url": FAIL_ENTRY_URL, "evidence": FAIL_EV,
                                        "fill_url": True}}],
                "/run": [{"body": {"job_id": NEW_JOB, "say": SUBMITTED_SAY}}],
                "/job/%s/live" % NEW_JOB: fresh,
            }}


def _failures_unmeasured_payloads() -> dict:
    """**量不到**那一趟：服务回 502 + 一句人话（连不上后端）。"""
    return {"scenario": "failures-unmeasured", "search": "",
            "failures": {"site": FAIL_SITE, "pick": FAIL_PICK},
            "responses": {
                "/runs": [{"body": {"note": "还没有任何运行。", "runs": []}}],
                FAIL_LIST_URL: [{"status": 502, "body": {"detail": FAIL_502}}],
            }}


def test_the_panel_shows_the_failures_as_human_words(tmp_path):
    """① 那一栏长出来的**是人话**：服务给的那两句上屏，而且**逐字就是那两句**。

    ⚠️ 断言写成**整行相等**（不是「那句话在里面」）：只比 `in` 的话，
    「那一行把整条记录 `JSON.stringify` 出来、人话也在里面」这种改法照绿 ——
    而运营看到的就是一屏括号和引号。

    ⚠️ 「码不上屏」这一半钉在**服务那一侧**（`test_service_failures._no_code`）：
    页面画的就是服务给的那两格，服务漏了码页面挡不住 —— 在这儿补一条同义断言是假的
    （这一份的载荷里本来就没有码，量了也是空转）。
    """
    out = _drive(tmp_path, scenario="failures")
    html = out["afterQuery"]["fails"]
    assert FAIL_ROW_SAY in html, "那一条的人话没上屏：%r" % html
    assert "9-19 10:21 失败 · 美国 · 站点专属" in html, html
    assert "有 2 条失败的记录" in html, "服务那句「量到了什么」没上屏：%r" % html
    #: ★ 每一行的形状：**单号 + 一句人话**，没有第三样东西
    assert '<li><span class="mono">26033398</span> %s</li>' % FAIL_ROW_SAY in html, \
        "那一行不是「单号 + 一句人话」（多了或少了东西）：%r" % html
    assert html.count("<li>") == len(FAIL_ROWS), html
    for junk in ("{", "}", '"task_id"', "JSON"):
        assert junk not in html, "那一行把整条记录端上来了（%s）：%r" % (junk, html)
    #: 查到之后**那一块才露出来**（挑哪一条那个下拉框 + 那个按钮）
    assert out["afterLoad"]["actHidden"] is True, "还没查就摆出了「照这条修」：%r" % out["afterLoad"]
    assert out["afterQuery"]["actHidden"] is False, "查到了却没摆出那两格：%r" % out["afterQuery"]


def test_clicking_one_fills_the_form_and_does_not_start_anything(tmp_path):
    """★ ③ 那一条的**正身**：点一条 → **三格填好** + **一趟都没开**。

    为什么「没开」也是判据：`POST /run` 要的是**什么算成功**（`success_text`）——
    那一格**只有人知道**（服务自己也是这么说的：「猜出来的成功判据会让产物跑到底再报成功」）。
    页面替他把那格编一个、或者干脆空着发出去，两种都是**替人做决定**。
    所以：**证据填好、按钮留给人按**。
    """
    out = _drive(tmp_path, scenario="failures")
    assert out["afterFix"]["evidence"] == FAIL_EV, out["afterFix"]
    assert out["afterFix"]["url"] == FAIL_ENTRY_URL, out["afterFix"]
    assert out["afterFix"]["mode"] == "fix", "没把「这是哪种活」拨到「老站」：%r" % out["afterFix"]
    assert out["afterFix"]["evidenceHidden"] is False, \
        "填了证据却把那格收着（人看不见填了什么）：%r" % out["afterFix"]
    # ★ **一次 `/run` 都没发**（人还没按那个按钮）
    assert out["afterFix"]["ranAlready"] == 0, "点一条就把活开了 —— 人还没说「什么算成功」：%r" % out["sent"]
    # 服务那句话**原样**上屏（`setErr` 那条绿框），页面不自己编一句
    assert FAIL_ROW_SAY in out["afterFix"]["errBox"], out["afterFix"]
    assert out["afterFix"]["errHidden"] is False, out["afterFix"]
    assert out["afterFix"]["errClass"] == "notice done", out["afterFix"]


def test_the_human_still_presses_the_button_and_the_evidence_goes_with_it(tmp_path):
    """★ 填好的证据**真的会跟着那一趟走**：人自己按「开一趟」，`POST /run` 的正文里有它。

    这一条把「填好了」与「发出去时还在」接上 —— 少了它，
    「页面把那格填上、发的时候又读另一个地方」这种改法量不出来。
    """
    out = _drive(tmp_path, scenario="failures")
    runs = [x for x in out["sent"] if x["url"] == RUN_HOP]
    assert len(runs) == 1, "按一下「开一趟」应该正好发一次：%r" % out["sent"]
    body = json.loads(runs[0]["body"])
    assert body["evidence"] == FAIL_EV, body
    assert body["url"] == FAIL_ENTRY_URL, body
    assert body["mode"] == "fix", body
    #: 人**没填**的那一格照旧原样发出去（页面不替服务判哪格必填 —— Task 12 那条纪律）
    assert body["success_text"] == "", body


def test_what_was_filled_survives_three_repaints(tmp_path):
    """④ **写进去 ≠ 还在**（Task 7 那一族）：三次重画之后那几格还在。

    为什么这条不是白量的：`#fails` 与那三格都在**会重画的那几个容器之外** ——
    但那是**今天**的布局；哪天有人把它挪进 `#notices` / `#timeline` 里，
    这条会红，而源码文本断言不会（它看见的是「赋值那行还在」）。
    """
    out = _drive(tmp_path, scenario="failures")
    assert out["afterRepaint"]["evidence"] == FAIL_EV, out["afterRepaint"]
    assert out["afterRepaint"]["mode"] == "fix", out["afterRepaint"]
    assert out["afterRepaint"]["url"] == FAIL_ENTRY_URL, out["afterRepaint"]
    assert FAIL_ROW_SAY in out["afterRepaint"]["fails"], out["afterRepaint"]
    assert out["afterRepaint"]["errHidden"] is False, \
        "填完那句话被后继的重画擦了：%r" % out["afterRepaint"]


def test_unmeasured_is_never_drawn_as_no_failures(tmp_path):
    """★ **这一片从头到尾在治的那个形状**：「量不到」不许画成「没有失败」。

    量的是**屏幕上那一刻**是哪一句：
      · 服务那句「量不到」上屏了（原样）；
      · 而**「没有失败的记录」那句没有** —— 那半句只有**真量到、真没有**才许出现。

    为什么两条都要量：只量前一条的话，一个「两句话都画上去」的改法照绿 ——
    而屏幕上同时写着「量不到」和「没有失败」时，运营信的是**后者**（它更像结论）。
    """
    out = _drive(tmp_path, scenario="failures-unmeasured")
    html = out["afterQuery"]["fails"]
    #: 服务那句话的**后半截**（`rich()` 会把 `**…**` 渲染成 `<b>`，所以整句比不中 ——
    #: 与 `_thin()` 同一个理由，这里只比那半截原样上屏的）
    marker = "是「这一次没量着」"
    assert marker in FAIL_502, FAIL_502
    assert marker in html, "服务那句「量不到」没上屏：%r" % html
    assert "这不代表「这个站没有失败」" in html, html
    assert "失败的记录" not in html, \
        "「量不到」被画成了「没有失败」—— 这两件事在屏幕上长得一模一样了：%r" % html
    #: 那一块**不许**露出来（量不到就没有可挑的一条）
    assert out["afterQuery"]["actHidden"] is True, out["afterQuery"]
    assert out["afterQuery"]["disabled"] is False, "红了之后按钮还按不动（人没法重试）：%r" % out["afterQuery"]
    #: 而且它**活过之后三次重画**（这一栏不跟着 `paint()` 重画 —— 量这一条）
    assert marker in out["afterRepaint"]["fails"], out["afterRepaint"]


def test_the_failures_fixture_can_actually_fire(tmp_path):
    """**正控**（这一节自己的牙）：改坏一处已知会破坏行为的写法 ⇒ 同一批断言必须红。

    改的是「量不到」那条路上那一步：把服务回的错误正文**当成一份空列表**画出来
    （「读不到就当没有」—— 这正是这一片要治的那个形状的**代码**）。
    """
    original = service.CONSOLE_PATH.read_text(encoding="utf-8")
    broken = original.replace(
        "          failProblem(sayOf(res));",
        "          paintFailures({ \"say\": \"这个站没有失败的记录。\", \"failures\": [] });")
    assert broken != original, "正控没改动任何东西（那一行没找到？）—— 那这条正控是空的"
    page = tmp_path / "console-broken-fails.html"
    page.write_text(broken, encoding="utf-8")

    good = _drive(tmp_path, scenario="failures-unmeasured")
    bad = _drive(tmp_path, scenario="failures-unmeasured", page=page)
    assert "是「这一次没量着」" in good["afterQuery"]["fails"], good["afterQuery"]
    assert "是「这一次没量着」" not in bad["afterQuery"]["fails"], (
        "把「读不到」写成「没有失败」之后这一份夹具**没有响** —— "
        "那说明它量不到那一栏（观测值照旧是 %r）" % bad["afterQuery"])
    #: 正控还得**落到那一句假话上**：改坏之后屏幕上出现的正是「没有失败」那句
    assert "失败的记录" in bad["afterQuery"]["fails"], bad["afterQuery"]
