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
| 1 | 出错的**那一刻**服务那句话原样上屏，且**活过之后 18 次重画** | 「写进去」与「还在不在」是两件事（`setErr` 写对了，下一次 `fetchLive` 成功就擦掉 —— 修复轮 1 的坑） |
| 2 | 左边那一栏读不到时，那句真话**不被过期列表盖掉** | 修复轮 3 的坑：`fetchRuns` 失败只是**写了字**，而 `paint()` 每 3 秒把 `state.runs`（上一次那份**好**列表）照画一遍 |
| 3 | 服务那两格**对不上**时，页面说出来（而不是自己挑一句） | 两句话是同一段 `innerHTML` 的**两个分支** —— 源码文本断言看得见「分支在」，看不见**这一刻走了哪一支** |

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


def _live(status: str, mode: str, *, n: int, stop_requested: bool = False) -> dict:
    """一份 `/live` 正文（**只填这一份夹具要读的那几格**，其余按页面「可能不在」的读法留空）。"""
    return {
        "job_id": "job-1", "status": status, "delivered": False,
        "say": "在跑。" if status == "running" else "停下来了，在等你一句话。",
        "stage": "explore", "stage_say": "探路",
        "note": "", "shots_note": "",
        "events": [{"n": i + 1, "at": "2026-09-19T21:0%d:00+08:00" % i, "kind": "running",
                    "who": "system", "say": "第 %d 条（夹具）" % (i + 1)} for i in range(n)],
        "gate": None if status != "waiting" else
                {"step": "deliver", "step_say": "交付", "ask": "要交付吗？", "facts": {},
                 "can": ["让它继续"], "revisable": True},
        "input": {"mode": mode, "draft_note": "", "queued": []},
        "stop": {"requested": stop_requested, "where": "", "will_stop_at": ""},
        "window": None, "rounds": [], "truncated": False,
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


def _drive(tmp_path, *, final_mode: str, page: pathlib.Path = None) -> dict:
    """跑一次夹具，把驱动脚本打回来的那份观测解析出来。"""
    data = tmp_path / ("payloads-%s.json" % final_mode)
    data.write_text(json.dumps(_payloads(final_mode=final_mode)), encoding="utf-8")
    r = subprocess.run([NODE, str(DRIVER), str(page or service.CONSOLE_PATH), str(data)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "夹具自己挂了（rc=%s）：\n%s\n%s" % (r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="这台机器上没有 `node`（`command -v node` 没找到）—— 这套执行夹具跑不起来。"
           "**替代方案**：在有 node 的机器上跑这一份；或者在本地 headless Chrome 里开 "
           "`GET /console` 手工走一遍（那是 Task 10 Step 3 那些手工验收条目里的事）。")


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

    ⚠️ 「重画真的发生了」是**量出来的**（`urls` 那一行）：`/live` 每次都喂一份**不一样的**
    正文，而页面只在正文变了才重画 —— 次数对不上就说明这条用例量的是别的什么东西。
    """
    out = _drive(tmp_path, final_mode="gate")

    # 量具**不是瞎的**：这几跳真的被走过了（不然下面几条是空过）
    urls = out["urls"]
    assert urls.count("/runs") >= 2, urls
    assert urls.count("/job/job-1/live") >= 8, (
        "重画次数不够（每一份 `/live` 都不一样才画一次）：%r" % urls)
    assert urls.count("/job/job-1/stop") == 1, urls

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

    # ③ ★ 十八次重画之后：三句话**一句都没被擦掉**，而过期列表**没上屏**
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
