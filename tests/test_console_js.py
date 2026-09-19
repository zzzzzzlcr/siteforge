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

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402

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


def _live(status: str, mode: str, *, n: int, stop_requested: bool = False,
          tag: str = "夹具", artifact: dict = None, delivered: bool = False) -> dict:
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
        "window": None, "rounds": [], "truncated": False,
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


def _drive(tmp_path, *, final_mode: str = None, scenario: str = "repaint",
           page: pathlib.Path = None) -> dict:
    """跑一次夹具，把驱动脚本打回来的那份观测解析出来。"""
    if scenario == "again":
        payload = _again_payloads()
    elif scenario == "artifact":
        payload = _artifact_payloads(with_artifact=True)
    elif scenario == "artifact-missing":
        payload = _artifact_payloads(with_artifact=False)
    else:
        payload = _payloads(final_mode=final_mode)
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
    assert "没有产出 py" in end, "到头了却没产出的那一趟，页面上没说出这件事：%r" % end
    assert "撞上限" in end, "说了「没有产出」却没说是**为什么**：%r" % end
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
