"""Task 7：**给运营的那一屏**（`GET /console`）。

这一屏是运营看和说话的地方（他们不开 Claude Code 聊天）。前面六个 Task 都在给它建地基
（截图落盘 / 步拍 / 时间线 / 旁白 / 轮次投影），这一片把那些东西拼成一个页面。

## 这一份测试钉的是什么（**不是**页面的像素）

页面是**一个仓库里的文件**（`agent/console.html`，设计注 §8.4：能 diff、能 review），
它的 JS **没有单测**（设计注明说：对策不是补一套 JS 测试框架，而是**把 JS 做笨**）。
所以「能自动断言的」都留在 HTTP 层 —— 这一份钉的就是那些：

| # | 性质 | 对着谁 |
|---|---|---|
| 1 | `/console` 是一张能开的页（200 + `text/html` + 有 `<title>`） | 设计注 §8.4 |
| 2 | 页面**点名**它要用的每一跳（`/live` `/reply` `/say` `/stop` `/shot/`）+ 三个动作的 id | brief Step 1 |
| 3 | 两种 `revisable` 文案都在（「打回带话」/「说一句，接着走」） | brief 页面规则 |
| 4 | 「这个窗口」「worker」「不要动手」三处字样在页面上 | **A10 的自动化那一半** |
| 5 | **零外部资源**（`src="http` / `href="http` / `@import` / `<script src`）—— **A12** | 设计注 §八：内网，出网要过代理 |
| 6 | `GET /` → 302 → `/console` | brief Step 1「可选，做了就测」 |
| 7 | 页面文件读不出来时**响**（500 + 人话），不给一页空白 | brief Step 3 |
| 8 | 渲染 `last_shot`（这一趟最后那张图） | 复审留下的第 1 条 |
| 9 | 轮次措辞把「哪一轮」说清（**闸拍**）—— 不把已知歧义原样印给运营 | 复审留下的第 3 条 |
| 10 | 时间线**不搬**事件里那七格（`events[].data` 一个字节都不进主视图） | D16 |
| 11 | 「跟不跟滚动」按**人在不在底部**判（不是无条件抢） | brief 页面规则 |
| 12 | 三种输入语义（`gate`/`steer`/`queue`）的文案都在，且是**按服务器给的 `mode` 取**的 | brief 页面规则 |
| 13 | `409` 那条路：原样显示 `detail` + **重拉 `/live`**（不许静默什么都不发生） | brief 页面规则 |
| 14 | 页面上那些**只在 `/live` 里才有**的事实（`note` / `shots_note` / `will_stop_at`）都有人渲染 | §3.5 / §5.5 / brief |

⚠️ 第 2 行里 `/say` `/stop` `/again` 这三个端点**现在还不存在**（Task 8 的）——
页面写上它们是**合同的声明**，不是在假装它们已经能用。三个动作真按下去会 **404**，
而 404 的 `detail` 必须**原样显示**（第 13 行那条纪律的另一半）。
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import graph, service  # noqa: E402


def _client(**kw) -> TestClient:
    """一个装好桩的 TestClient —— **不碰**进程级默认（那会去连 Postgres / 真 Bit 窗口）。"""
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(service.create_app(**kw))


def _page(**kw) -> str:
    r = _client(**kw).get("/console")
    assert r.status_code == 200, r.text
    return r.text


# ═══════════════════ 1. 它是一张能开的页 ═══════════════════


def test_the_console_is_a_page_the_operator_can_open():
    """`GET /console` → 200 + `text/html` + 一个非空的 `<title>`。

    ⚠️ `<title>` 不是装饰：运营开着一排标签页时，那个字是他们认这一屏的唯一线索。
    """
    r = _client().get("/console")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers["content-type"]
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title and title.group(1).strip(), "页面得有个名字（`<title>`）"
    assert "charset" in r.text[:400].lower(), "中文页面的编码得在开头就说清（不然一片乱码）"


def test_the_console_file_is_the_one_the_route_reads():
    """页面是**仓库里那个文件**（不是塞在 py 里的字符串）—— 两边指的是同一个东西。

    设计注 §8.4：页面是**可读的资产**（能 diff、能 review）。这一条钉的就是这件事：
    路由读的那个路径 = 仓库里真实存在的那个文件。
    """
    assert service.CONSOLE_PATH.is_file(), service.CONSOLE_PATH
    assert service.CONSOLE_PATH.parent == pathlib.Path(service.__file__).parent
    assert "<title>" in service.CONSOLE_PATH.read_text(encoding="utf-8")


# ═══════════════════ 2. 页面点名它要用的每一跳 ═══════════════════


def test_the_page_names_every_hop_it_will_call_and_the_three_actions():
    """页面**自己**写着它要用的每一跳 + 三个动作各自的 id（brief Step 1）。

    `/say` / `/stop` / `/again` 现在**还不存在**（Task 8 的）—— 页面写上它们是
    **合同的声明**：这一屏承诺的交互就是这三个，端点落地时不用再改页面。
    """
    page = _page()
    for hop in ("/live", "/reply", "/say", "/stop", "/shot/"):
        assert hop in page, "页面没点名这一跳：%s" % hop
    for btn in ("btnContinue", "btnSay", "btnStop"):
        assert 'id="%s"' % btn in page, "页面少了这个动作的 id：%s" % btn
    #: 每一跳的**落点**：逐条钉真字面量（⚠️ 修复轮 1：原来钉的是 `'"say"' in page`，
    #: 而那个子串是被 `ACT` 映射表**自己**满足的 —— 把发出去的载荷改名它**照样通过**，是空钉子）
    #: ★ Task 12 加的三跳：`/run`（开一趟新的 —— 它**不在某个 job 下面**）、
    #: `/window/close`（人按的收摊）、`/reopen`（换窗口接着走）。
    #: ⚠️ 后两个是**相对某一趟**的（`act`/`windowAct` 会拼上 `/job/<id>`），
    #: 而 `run` 那一跳**不拼** —— 页面自己那一段注释写着这件事（别拿 `act()` 发它）。
    for pair in ('"continue": "/reply"', '"say": "/say"', '"stop": "/stop"',
                 '"again": "/again"', '"run": "/run"',
                 '"windowClose": "/window/close"', '"reopen": "/reopen"'):
        assert pair in page, "动作到端点的对应不在页面上：%s" % pair


def test_the_payloads_are_the_ones_the_plan_promised():
    """三个动作**发出去的载荷**（不是它们的名字）—— 逐条钉真字面量。

    ⚠️ 这一条是修复轮 1 补的：计划（`plans/2026-09-17-console-minimal.md`）把请求体写死了
    两遍 —— `POST /job/{id}/say {"text": "…"}`、`POST /job/{id}/reply {"action": …}`。
    我原先发的是 `{"say": …}`：Task 8 照计划实现 ⇒ **页面每一个「说一句」当场 422**。
    钉法用真字面量（`'"text":'` 这种子串会被别处的键名满足 —— 那是空钉子）。
    """
    page = _page()
    assert 'act("say", { "text":' in page, "`/say` 的载荷字段不是计划里的 `text`"
    assert 'act("stop", {})' in page, "「停」这个动作的载荷不在页面上"
    assert '"action": "continue"' in page, "闸上「继续」的载荷不在页面上"
    assert '"action": "say", "note"' in page, "闸上「带话」的载荷不在页面上"


def test_the_page_carries_both_copies_of_the_second_button():
    """`revisable` 两档的文案都在页面上：`true` → 「打回带话」，`false` → 「说一句，接着走」。

    两句话是**两个意思**（打回 = 这一版不要了、回 draft；说一句 = 接着走）——
    页面按闸给的那一格改名，不自己猜（§九）。
    """
    page = _page()
    assert "打回带话" in page, "`revisable` 为真时的那句话不在页面上"
    assert "说一句，接着走" in page, "`revisable` 为假时的那句话不在页面上"
    assert "revisable" in page, "页面得**读**那一格（不然改名就是写死的）"


# ═══════════════════ 3. 「这个窗口」那一块（A10 的自动化那一半）═══════════════════


def test_the_window_block_keeps_the_facts_and_the_hard_rule():
    """「这个窗口」「worker」「不要动手」三处字样都在（**A10 的自动化那一半**）。

    ⚠️ 「跑的时候不要动手」是**写死的方法**，不是动态内容：`window` 为 `null` 时
    它**照样要在页面上**（那一块没有事实可摆，规矩仍然成立）。
    """
    page = _page()
    assert "这个窗口" in page
    assert "不要动手" in page, "「跑的时候不要动手」不许在改版里丢掉（写死的方法）"
    facts = re.search(r'id="winFacts"[^>]*>(.*?)</div>', page, re.S)
    assert facts, "「这个窗口」那一块没有三样事实的位置（worker / 窗口 id / 控制口）"
    for label in ("worker", "窗口 id", "控制口"):
        assert label in facts.group(1), "事实少了这一样：%s" % label


def test_the_page_says_out_loud_when_there_is_no_window_to_look_at():
    """没接窗口层（`window` 为 `null`）时**明说看不到** —— 不许摆一块空表。

    一块空的白底（worker / id / 端口三格全空）读起来像「后台在加载」，人会一直等。
    """
    page = _page()
    assert "看不到" in page, "没窗口时得明说「看不到活窗口」"


# ═══════════════════ 4. 零外部资源（A12）═══════════════════

#: 出网的四条路（brief Step 1 的正控点名的那四条）。⚠️ 内网里每一条都是
#: 「一张永远转圈的空白页」—— 所以它们一条都不许出现在这一页上。
OUTSIDE = (
    r"""src\s*=\s*["']https?://""",      # `<img src="http…">` / `<script src="http…">`
    r"""href\s*=\s*["']https?://""",     # `<link href="http…">`
    r"@import",                          # CSS 里那一句
    r"<script[^>]*\bsrc\s*=",            # 任何带 `src` 的 `<script>`
)


def _outside_hits(text: str) -> list:
    return [p for p in OUTSIDE if re.search(p, text, re.I)]


def test_the_page_asks_for_nothing_from_the_outside():
    """**A12**：这一页不许有一个字节来自外面（CDN / 字体 / 图标 / 打包器都不行）。

    运营的浏览器在**内网**里，出网要过代理 —— 一个 `src="https://…"` 就是一张
    永远转圈的空白页，而那时候没人能告诉你为什么空。
    """
    page = _page()
    hits = _outside_hits(page)
    assert not hits, "页面里有出网的东西：%s" % hits


def test_the_outside_scanner_can_actually_fire():
    """**正控**：上面那把尺子**真的会响**（不然第 5 条是一句空话）。

    一条永远返回「干净」的扫描器，和没有扫描器是一回事 —— 它只让人放心，不让人知道。
    四条判据各喂一个样本，全都要被抓到。
    """
    for sample in ('<img src="https://cdn.example.com/a.png">',
                   "<link href='http://fonts.example.com/x.css' rel=stylesheet>",
                   "@import url(//x.example.com/a.css);",
                   '<script src="./vendor/x.js"></script>'):
        assert _outside_hits(sample), "这把尺子漏了：%r" % sample
    #: 反向：正常的页面元素**不许**被误杀（误杀一次，人就再也不信这条规矩了）
    assert not _outside_hits('<img src="/job/job-1/shot/pause-1.png">')
    assert not _outside_hits("<style>body{background:#fff}</style>")


# ═══════════════════ 5. `/` → `/console` ═══════════════════


def test_the_root_sends_you_to_the_console():
    """`GET /` → 302 → `/console`（brief Step 1：做了就测）。

    为什么值得有：运营拿到的是 `http://<那台机器>:8080`，不是一个带路径的地址。
    """
    r = _client().get("/", follow_redirects=False)
    assert r.status_code == 302, r.status_code
    assert r.headers["location"] == "/console", r.headers["location"]


# ═══════════════════ 6. 读不到那个文件时**响** ═══════════════════


def test_a_console_file_that_cannot_be_read_rings(monkeypatch, tmp_path):
    """页面文件读不出来 → **500 + 一句人话**（brief Step 3：别静默给空白页）。

    空白页是这一屏最坏的失败形状：它看起来像「还没有任何运行」，而真相是
    「服务找不到它自己的页面文件」—— 两件事的处置完全不一样。
    """
    monkeypatch.setattr(service, "CONSOLE_PATH", tmp_path / "没有这个文件.html")
    r = _client().get("/console")
    assert r.status_code == 500, r.status_code
    detail = r.json()["detail"]
    assert "控制台" in detail or "页面" in detail, detail
    assert "没有这个文件.html" in detail, "人话里得说清它在找哪个文件：%r" % detail


# ═══════════════════ 7. 复审留下的三条，这一屏都得照顾到 ═══════════════════


def test_the_page_renders_the_last_shot():
    """★ 复审第 1 条：**必须渲染 `last_shot`**（这一趟最后那张图）。

    跑到头 / 跑挂那一刻拍的那张**不是某一轮的闸拍**（那会儿没有闸），所以它不挂在
    任何一张卡上，只活在最后一张卡的 `last_shot` 里。**有卡的时候，这个事实只在那儿** ——
    页面不渲染它，就没人提：那一趟「挂掉那一刻窗口里是什么样」当场蒸发。
    """
    page = _page()
    assert "last_shot" in page, "页面没读这一格：最后那张图就没人提了"
    assert "最后" in page and "这一趟" in page, "页面得说清那一张是**哪一张**"


def test_the_page_says_which_kind_of_round_it_means():
    """★ 复审第 3 条：卡片上那个「第 N 轮」是**闸拍轮次**，与 `report.summary()` 原话里
    的「这一轮」（一遍扰动）**不是一回事** —— 页面不能把那个歧义原样印在运营眼前。

    做法：卡片抬头带限定词「闸拍」，面板里再写一句这两者各指什么。
    """
    page = _page()
    assert "闸拍" in page, "轮次要带限定（「第 N 轮（闸拍）」），不然和自测的「这一轮」撞车"
    assert "扰动" in page, "面板里得说清另一个「这一轮」指的是一遍扰动"


def test_the_timeline_never_shows_the_raw_cells():
    """D16：`events[].data` 那七格（选择器 / 原始回执 / 判断词）**一个字节都不进主视图**。

    ⚠️ 钉法（修复轮 1 改准了）：**`data` 这个键名在页面上一次都不许出现** ——
    两种读法都挡：`at(ev, "data")` 与 `ev.data`。**旧那条钉的是 `.data` 这一个子串**，
    复审实测：把时间线改成 `at(ev, "data")`（**真的把那七格搬上主视图**）它**照样通过** ——
    空钉子。`data-shot` / `data-job` 这两个**属性名**不受影响（它们是 `data-`，不是 `data`）。
    """
    page = _page()
    hit = re.search(r"""\.data\b|["']data["']""", page)
    assert not hit, "页面上出现了 `data` 这个键名（%s）—— D16：那七格不进主视图" % hit.group(0)


def test_the_timeline_follows_the_tail_unless_the_human_scrolled_up():
    """「新的在下面、自动滚到底」—— **除非人往上翻了**（brief 页面规则）。

    钉法：页面得**量**一次「人在不在底下」（`scrollTop` / `scrollHeight` / `clientHeight`），
    按量出来的结果决定跟不跟 —— 无条件 `scrollTop = scrollHeight` 就是每次轮询都抢人的滚动。
    """
    page = _page()
    for word in ("scrollTop", "scrollHeight", "clientHeight"):
        assert word in page, "页面没量这一格：%s（那它只能无条件抢滚动）" % word


def test_the_three_input_semantics_are_on_the_page_and_keyed_by_the_mode():
    """常开输入的三种语义（`gate` / `steer` / `queue`）各有一句 placeholder ——
    **页面不许自己猜**它在哪种模式（brief 页面规则）。

    钉法：三个键都在文案表里；三句 placeholder 互不相同；取哪一句由服务器给的
    `input.mode` 决定（页面里出现 `input` 与 `mode` 两个词，且没有拿 `status` 去推模式）。
    """
    page = _page()
    for mode in ("gate", "steer", "queue"):
        assert '"%s"' % mode in page, "三种语义里少了哪一个：%s" % mode
    assert "input" in page and "mode" in page, "输入的语义得从 `/live` 的 input.mode 取"
    for sentence in ("或者按「继续」让它往下走", "会直达它的下一轮", "它停下来时会进输入框"):
        assert sentence in page, "这一句 placeholder 不在页面上：%s" % sentence


def test_a_409_is_shown_verbatim_and_the_page_reloads_live():
    """`409` → **原样显示 detail** + 重拉 `/live`（brief 页面规则）。

    ⚠️ 这一条的另一半是**这一版就要成立**的：`/say` `/stop` `/again` 三个端点 Task 8
    才落地，在那之前按下去会 **404** —— 页面对**任何**非 2xx 都必须把 `detail` 原样摆出来。
    **不许静默什么都不发生**（那正是这个项目最老的那条病：假成功）。
    """
    page = _page()
    assert "409" in page, "页面得对 409 单独有一条处置"
    assert "detail" in page, "错误得**原样**显示服务给的那句话（不是页面自己编一句）"
    act = re.search(r"function act\(.*?\n  \}", page, re.S)
    assert act, "找不到那个发动作的函数（三个动作该走同一处）"
    assert "sayOf(" in act.group(0), "发动作那条路上没把服务那句话拿出来 —— 按下去会静默"
    assert "fetchLive" in act.group(0), "出错之后没重拉 /live（画面会停在错的那一刻）"
    reader = re.search(r"function sayOf\(.*?\n  \}", page, re.S)
    assert reader, "找不到那个「照抄服务那句话」的函数"
    assert "detail" in reader.group(0), "它没读服务给的 detail —— 那显示出来的就不是原话"


def test_the_error_line_cannot_be_wiped_by_a_repaint():
    """错误那一条**不许**挂在会被重画掉的容器里 —— 我在 Step 4 里**真踩到过这个坑**。

    实测的坏形状：错误框当初是 JS 建在「告示」容器里的，而那个容器每画一次都
    `innerHTML = ""`，于是下一次轮询就把它删了 —— 之后 `setErr` 找不到那个元素，
    **当场变成静默**：按下去 404，页面上一个字都没有。这正是 brief 点名不许的那件事
    （「不许静默什么都不发生」），而且它**只在浏览器里看得见**（HTTP 层一条断言都拦不住）。
    """
    page = _page()
    markup = page.split("<script>")[0]
    assert 'id="errBox"' in markup, "错误那一条要在**静态标记**里（JS 建的会被下一次重画删掉）"
    painter = re.search(r"function paintNotices\(.*?\n  \}", page, re.S)
    assert painter, "找不到那个重画告示的函数"
    assert 'innerHTML = ""' in painter.group(0), "那个函数就是靠清空来重画的（所以它清谁很关键）"
    assert "errBox" not in painter.group(0), "重画告示的时候会把错误那一条一起删掉 —— 那就静默了"


def test_a_refetch_does_not_erase_the_error_the_human_just_caused():
    """「出错 → 重拉 `/live`」之后，那句 `detail` **还得在**（Step 4 里实测踩到的第二个坑）。

    坏形状：`act` 把 404 的 detail 摆上去、紧接着调 `fetchLive()` 重拉，而重拉成功那一支
    一句 `setErr("")` 把它擦了 —— 页面上于是又变回「按下去什么都没发生」。
    这正是「没有静默的路径」要治的形状，而它**只在浏览器里看得见**。
    修法：错误那一条记得自己**是哪一类**（`live` / `act`），只有同类好了才收。
    """
    page = _page()
    live = re.search(r"function fetchLive\(.*?\n  \}", page, re.S)
    assert live, "找不到那个拉 /live 的函数"
    body = live.group(0)
    assert "errFrom" in body, "重拉要按「这条错误是从哪来的」决定收不收"
    clears = [line.strip() for line in body.splitlines() if 'setErr("")' in line]
    assert clears, "这一屏上一次错误之后总得有一条收回去的路"
    for line in clears:
        assert "errFrom" in line, "这一处清空没看「错误是哪来的」—— 会擦掉动作刚回的那句话：%s" % line


def test_the_facts_that_only_exist_in_live_are_all_rendered():
    """页面上那几件事**只**活在 `/live` 里 —— 页面不渲染，它们就等于不存在：

    - `note`：时间线**不持久**（服务重启就没了）—— 设计注 §3.5 要「明说」；
    - `shots_note`：降级 B 那句话，非空时**一直在顶部**（静默降级是明令禁止的）；
    - `will_stop_at`：「停」按下之后落在哪儿 —— brief 页面规则点名要它；
    - `stop.requested`：已经请求过停这件事，页面得知道（不然按钮会一直亮着）。

    ⚠️ 钉法（修复轮 1 改准了）：钉**取那一格的那次调用**（`at(live, "note"` 这种真字面量），
    不钉光秃秃的键名 —— 旧那条钉的 `"note"` 是 `"shots_note"` 的**子串**，
    复审实测：**删掉渲染 `note` 的那一行**，它照样通过（空钉子）。
    """
    page = _page()
    for call in ('at(live, "note"', 'at(live, "shots_note"', 'at(live, "stop.will_stop_at"',
                 'at(live, "stop.requested"'):
        assert call in page, "页面没渲染这一格：%s" % call


def test_the_page_tolerates_cards_that_are_missing_fields():
    """★ 复审第 2 条：卡片**没有键集合断言** —— 加字段没人拦、丢字段也没人拦。

    钉法（HTTP 层能钉的那一半）：页面读卡片上的东西**一律走一个「可能不在」的取值器**
    （`at()`），而不是一串点到底的属性链 —— 中间那一格不在的时候，那种写法会当场把整页 JS
    打断（页面上就什么都不动了，也没人说是为什么）。

    ⚠️ 修复轮 1 改准了：**旧那条是 `page.count("at(") >= 10`** —— 复审实测页面上有 **83** 个
    `at(`，门槛等于没有；把 `at(card, "done", null)` 换成 `card.done` 它**照样通过**（空钉子）。
    现在钉的是**真要读的那几格各自走取值器**（真字面量），另加「不许出现深链」那三条。
    """
    page = _page()
    assert re.search(r"function at\(", page), "没有那个「可能不在」的取值器"
    for call in ('at(card, "done"', 'at(card, "now"', 'at(card, "last_shot"',
                 'at(card, "step_say"', 'at(done, "shots.before"', 'at(done, "steps"',
                 'at(step, "shot_after_deferred"', 'at(ev, "say"'):
        assert call in page, "这一格没有走「可能不在」的取值器：%s" % call
    for deep in (".done.shots.", ".now.name", ".steps_note"):
        assert deep not in page, "页面里有深链 %s —— 中间那格不在就整页断" % deep


# ═══════════════ 8. 修复轮 1（复审抓到的四条 + 两条改话）═══════════════


def test_a_detail_that_is_not_a_sentence_is_turned_into_human_words():
    """★ I1：`detail` **不一定是字符串** —— FastAPI 的字段校验错（422）里它是个**数组**。

    复审实测的形状：桩台按 Task 8 的签名收 `{"text": …}`、而页面（那时）发 `{"say": …}`
    ⇒ 真 422 ⇒ 浏览器里 `errBox.textContent` = **`[object Object]`**。
    运营看到那个等于没看到，而这一屏的纪律是「没有静默的路径」——
    **字在、话不在，就是一种静默**。
    """
    page = _page()
    reader = re.search(r"function errorText\(.*?\n  \}", page, re.S)
    assert reader, "没有把 `detail` 变成人话的那一处（`errorText`）"
    body = reader.group(0)
    assert "Array.isArray(detail)" in body, "数组那一支不在 —— 422 会退成 `[object Object]`"
    assert 'at(res.obj, "detail"' in page, "`sayOf` 得走 `errorText` 这条路"
    setter = re.search(r"function setErr\(.*?\n  \}", page, re.S)
    assert setter and "textContent" in setter.group(0), \
        "错误是 `textContent` 写进去的（不是 innerHTML —— 服务那句话不许当 HTML 解释）"

    #: ★ 修复轮 2：钉**组装**，不钉「函数体里有 `loc` / `msg` 这两个词」。
    #:    复审的两个变异正是从这儿溜过去的（都绿）：
    #:      ① 分支留着、只把拼装换成 `out.push(item)` ⇒ `[object Object]` 原样回来；
    #:      ② `loc` / `msg` 只用来拼一句**固定话**（不看真错）。
    #: ⚠️ 修复轮 3 改了两处（复审实测的**误报**）：**在整页里找**、而且**不钉它是不是 `out.push(` 的形状**。
    #:    把这段拼装**原地搬进一个助手**（语义逐字节不变、只是改成 `return …`）时，
    #:    「只在 `errorText` 体内找 `out.push(`」那版会**判红** —— 那是误报，不是发现。
    #:    钉的仍是那件事（**那句话由 loc（path）与 msg 拼出来**），只是不钉它住在哪个函数里、怎么被收走的。
    assembled = re.search(r"path\.length \? path\.join\([^;]*?msg", page, re.S)
    assert assembled, "没有找到「由 loc（path）与 msg 拼出来的那句话」（拼一句固定话不算）"


def test_a_stop_that_did_not_land_does_not_claim_it_did():
    """★ I3：`/stop` 没成的时候页面**不许**说「已经请求停了」—— 那是编话。

    404 意味着请求**根本没落地**：红条说「没这个端点」、下一行说「已经请求停了」，
    同一屏两句话互相打架，而且它断言的是一件它不知道的事。
    失败要说「没请求成」，而且**按钮得能再按**（它按下就禁用了，而 `paint()` 只在
    `/live` 的 JSON 变了才重画 ⇒ 载荷不变时那个按钮会一直按不动）。
    """
    page = _page()
    assert "已经请求停了" not in page, "失败那条路上还在断言一件它不知道的事"
    stop = re.search(r"function stopIt\(.*?\n  \}", page, re.S)
    assert stop, "找不到那个「停」的处理"
    body = stop.group(0)
    assert "没请求成" in body, "失败时没说「没请求成」"
    assert "disabled = false" in body, "失败之后按钮一直按不动（载荷不变就不会重画）"

    #: ★ 修复轮 2：钉**两支互斥**。复审的变异：条件反过来写 `if (out)` ——
    #:    于是 **404 时页面说「停的请求送到了」**，而修复轮 1 的钉子照样绿。
    then = body.split(".then(function (out) {", 1)[1]
    assert re.search(r"if \(!out\) \{", then), "失败那一支没判在 `!out` 上（反过来就成了：404 时说送到了）"
    fail_branch, _, ok_branch = then.split("if (!out) {", 1)[1].partition("return;")
    assert fail_branch.strip(), "失败那一支是空的"
    assert "没请求成" in fail_branch, "失败那一支没有说清「没请求成」"
    assert "送到了" not in fail_branch, "失败那一支里出现了「送到了」—— 两句都能成立就等于都没说"
    assert ok_branch.strip(), "失败那一支没有 `return;` —— 它会接着往下走到成功那句话"
    assert "送到了" in ok_branch, "成功那一支没有说「送到了」"
    assert "没请求成" not in ok_branch, "成功那一支里出现了「没请求成」—— 两句都能成立就等于都没说"


def test_the_two_cells_that_disagree_are_named_out_loud():
    """★ I4：输入区**内部**那处矛盾 —— `modeHint`（抄服务）与 `secondHint`（页面自己写的）
    打架时，不许把两句**并排当都成立**印出去。

    复审裁定的规矩：「页面不许自己猜」= 不许**认定**服务没说过的事实；
    「不能自相矛盾」= 不许把两句打架的话**并排当都成立**。两格对不上时唯一合规的形状是
    **照抄服务那两格 + 说出它们对不上** —— 这不需要知道谁对，所以它**不是猜**。
    """
    page = _page()
    # ⚠️ 钉**判据本身**（两格真被拿来比了），不钉 `"mismatch" in page` 那种名字出现没出现 ——
    #    我第一版就是那么钉的，变异实测：把它改成 `var mismatch = false;`（**关掉这个判据**）
    #    照样通过（空钉子）。名字在、判据不在，正是「看着像钉子」的形状。
    assert "waiting !== gated" in page, "两格（状态 / 输入语义）没有被拿来比 —— 那两句就被并排印出去了"

    #: ★ 修复轮 2：再钉两样（复审的变异从这儿溜过去，两条都绿）：
    #:   ① **方向**：判据用反（`!mismatch`）⇒ 两格**一致**时才说「对不上」；
    #:   ② **照抄的那两格**：只说「对不上」而不把两格印出来 ⇒ 运营看不出是哪两格对不上。
    after = page.split('"secondHint").innerHTML = rich(', 1)[1]
    assert re.match(r"\s*mismatch\s*\?", after), \
        "判据被用反了/被绕开了（`!mismatch` 就成了：一致时才说「对不上」）"
    branch = after.split("?", 1)[1].split(": (revisable", 1)[0]
    assert "STATUS_WORD[status]" in branch, "没把「状态那格」照抄出来（只说对不上，人看不出是哪两格）"
    assert "modeShort" in branch, "没把「输入那格」照抄出来（只说对不上，人看不出是哪两格）"
    assert "对不上" in branch, "对不上这件事没有被说出来"


def test_the_left_column_says_so_when_it_cannot_be_read():
    """★ M4：`/runs` 取不到**也要说** —— 这是全页最后一条会静默的路。

    旧形状：`!res.ok` 直接 `return`、`catch` 是空的 ⇒ 左边那一栏停在一个空 div 上，
    而**空 div 读起来就是「还没有任何运行」**（两件事完全不一样）。
    """
    page = _page()
    problem = re.search(r"function runsProblem\(.*?\n  \}", page, re.S)
    assert problem, "没有那句「这一栏读不到」的话"
    assert "box.innerHTML" in problem.group(0), "`runsProblem` 没把话写进 DOM（空转的函数不算）"
    assert "没有运行" in problem.group(0), "得说清「这不代表没有运行」"
    runs = re.search(r"function fetchRuns\(.*?\n  \}", page, re.S)
    assert runs, "找不到 `fetchRuns`"
    body = runs.group(0)
    assert "runsProblem(sayOf(res))" in body, "非 2xx 那条路没说话（空转 / 只喊个名字都不算）"
    assert "runsProblem(" in body.split(".catch(")[-1], "`catch` 那一支还是空的（异常那条路依旧静默）"

    #: ★ 修复轮 3：**200 也要看清楚正文**（复审点名的两个角）——
    #: `/runs` 回 `{}`（没有 `runs` 那一格）或 `[]`（`typeof [] === "object"` 从「是不是对象」
    #: 那道闸下过）时，原先照画「还没有任何运行。」——而那正是这一条要治的那句假话。
    guard = re.search(r"function runsRows\(.*?\n  \}", page, re.S)
    assert guard, "没有那个「这份正文能不能当列表画」的判据"
    gbody = guard.group(0)
    assert "Array.isArray(rows)" in gbody, "守卫没要求「`runs` 那一格是个数组」—— `{}` / `[]` 会漏过去"
    assert "runsRows(res.obj)" in body, "`fetchRuns` 没用那个判据（200 那条路照样会画假话）"

    #: ★★ 修复轮 2（复审最重要的一条）：**那句话得留在屏幕上**。
    #: 复审拿真页面 + 桩台（`/runs` 返 500）装了 `MutationObserver`，看到的是：
    #:   `[24ms 那句话] → [25ms「还没有任何运行。」] → [1s/4s 都还是那句假话]` —— **只活了 1 毫秒**。
    #: 机制就是这一格：`paint()` 里 `paintRuns(state.runs || {})` 的那个 `{}` **绕过**了
    #: `paintRuns` 开头那道 `if (!data …) return`，把「读不到」画成**空列表的默认话**。
    #: ⚠️ 这和我 Step 4 抓到的两个坑是**同一个形状**（重画把话擦掉），HTTP 层一条断言都看不见 ——
    #: 所以这里钉的是**传下去的那一份是不是原样的**（屏幕上的证据在报告 §修复轮 2）。
    assert "paintRuns(state.runs)" in page, "`paint()` 没把「拿到的那一份」原样传下去"
    assert "paintRuns(state.runs || {})" not in page, \
        "那个 `|| {}` 绕过了 `paintRuns` 开头的闸 —— 「读不到」会被画成「还没有任何运行」"


def test_the_elapsed_seconds_say_what_they_actually_measured():
    """★ M5：「已跑 X 分 Y 秒」是从 **`created_at`**（提交时刻）算的 ——
    里面含着排队、也含着停在闸上等你的时间，所以它**不是**「跑了多久」。

    数据里没有「真正开跑的时刻」那一格 ⇒ **改话，不改数**
    （说「这一趟开了多久」是量得住的那句）。
    """
    page = _page()
    assert "这一趟开了" in page, "措辞要跟得上它量到的东西"
    assert "已跑 " not in page, "「已跑」断言了一格数据里没有的东西"


def _strip_js_comments(text: str) -> str:
    """把 JS 注释抠掉 —— 判据只看**真写进 DOM 的表达式**。**只抠字符串外面的**。

    ⚠️ **这是一个已知有洞的尺子，不是一个证明**（修复轮 4 如实记账）。两版各有洞，
    **而且现在这版比上一版更糟** —— 别再把它记成「已修」：

    - **第一版（修复轮 2：`(?<!:)//`）**：把**字符串里**的 `//` 也当注释抠掉 ⇒
      `el.innerHTML = "服务说 // 这一句 **对不上**";` **漏报**（该红不红）。
      但它在「赋值表达式里那句带 `**` 的**合法注释**」这一格上是**对的**（它不认引号，处处都抠）。
    - **第二版（修复轮 3，就是现在这版）：按引号走一遍。** 它修掉了上面那个漏报，
      却换来一个**更大的洞**：**正则字面量里的引号会让它失步** —— 锚是 `esc()` 里
      `.replace(/"/g, …)` 与 `.replace(/'/g, …)` 这两处（行号会变，锚不会）。
      失步之后**它以为自己在字符串里**，于是在那一整段里：
        · 字符串里的裸 `**` ⇒ **漏报**；· 注释里的 `**` ⇒ **误杀**。
      ⚠️ **别照行号找那一段 —— 行号会漂、锚不会**（2026-09-20 修复轮 5 改成代码锚）：
      **锚是 `esc()` 里那两个正则字面量** —— `.replace(/"/g, …)` / `.replace(/'/g, …)`
      所在的那一行（今天在 `console.html:548`，就是 `.replace(/&/g, "&amp;")` 那一行）。
      【我量的·2026-09-20】失步**从那一行开始、到脚本末尾都没恢复**：剥完之后那一段里的
      `//` 注释**仍然留在文本里**（抽查 560 / 650 / 717 / 800 行都是），而整份文件剥完之后的
      `innerHTML … ;` 捕获共 **20 个 —— 全部起点都 ≥548** ⇒ **每一处渲染都落在失步范围里**。
      **那一段正是卡片与时间线区**（以后最可能再动的地方）。**今天没红只是运气。**
      ⚠️ **旧账（对着 `1a2a611` 那一笔）**：那时量的是「第 363–510 行 + 小岛
      656–657 / 687–694 / 1020」—— **那些行号早漂了**（本轮复量落在 548 起，
      一条连续段），留着只为对得上那一笔的账，**不要拿它去找位置**。

    ⇒ 老实用法：**它是一条回归绊线，不是「性质已被钉住」的证明**；射程是**形状**，不是执行。
    更硬的修法（报告里记着，本轮不做）：给这条判据补**页面级正控**，
    或者给扫描器补**正则字面量识别**。
    """
    out, i, n, quote = [], 0, len(text), ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _raw_markdown_hits(html_text: str) -> list:
    """写进 DOM 的表达式里，**没过 `rich()` 却带着 `**`** 的那几个（正控/被测共用同一把尺子）。

    两种写法都在射程里（复审点名的两条路）：`innerHTML = …` 与 `innerHTML += …`。
    ⚠️ **助手内部**（`setStopHint` / `noticeEl`）不在这把尺子里 —— 那是另一个函数体，
    由 `test_the_html_helpers_go_through_rich` 单独钉。
    """
    text = _strip_js_comments(html_text)
    return [m.group(2).strip()[:70]
            for m in re.finditer(r"innerHTML\s*(\+?=)\s*([^;]+);", text, re.S)
            if "**" in m.group(2) and "rich(" not in m.group(2)]


def test_no_raw_markdown_reaches_the_html():
    """`**着重**` 是服务那些话的写法（页面自己写的文案里也用）—— 走 `innerHTML` 的必须过 `rich()`。

    ⚠️ 这一条是修复轮 1 在**截图**里真看见的：新加的那句「服务这两格**对不上**」当时直接
    喂给了 `innerHTML`，运营看到的就是两个星号。HTTP 层别的手段一条都拦不住（`**` 是合法文本），
    所以钉法是把「写进 DOM 的表达式」扫一遍。
    """
    page = _page()
    hits = _raw_markdown_hits(page)
    assert not hits, "这些 innerHTML 没过 `rich()`（运营会看到 `**`）：%r" % hits
    #: 正控：这把尺子**真的会响** —— 两种写法（赋值 / `+=` 追加）都要响
    assert _raw_markdown_hits('el.innerHTML = "服务这两格**对不上**";'), "尺子漏了赋值那一路"
    assert _raw_markdown_hits('el.innerHTML += "服务这两格**对不上**";'), "尺子漏了 `+=` 那一路"
    #: 反向：过了 `rich()` 的不许被误杀；**注释里的 `**` 也不许被误杀**（复审点名的误杀）
    assert not _raw_markdown_hits('el.innerHTML = rich("服务这两格**对不上**");'), "误杀"
    assert not _raw_markdown_hits('el.innerHTML = (\n  // 这里说的是**着重**，不是要写进 DOM 的字\n  "文案");'), \
        "注释里的 `**` 被当成违规了（这个文件到处是这种注释写法）"
    #: ★ 修复轮 3：**字符串里的 `//` 不是注释** —— 抠掉它就成了**漏报**（复审点名的第二个洞，
    #: 我上一轮用 `(?<!:)//` 换来的正是这个）。这一条必须**响**。
    assert _raw_markdown_hits('el.innerHTML = "服务说 // 这一句 **对不上**";'), \
        "字符串里的 `//` 被当成注释抠掉了 —— 那一刻它看不见裸 `**`（漏报）"
    assert _raw_markdown_hits('el.innerHTML = "http://x/**着重**";'), "`://` 那一路也不许被抠掉"


def test_the_rounds_sub_block_is_not_in_the_scanners_blind_spot():
    """★ 修复轮 4：`#roundsSub` 那一块**必须留在扫描器看得见的地方**。

    `_strip_js_comments` 有一条**已知的失步区**（它自己的 docstring 里记着）：落在里面时
    字符串里的裸 `**` **漏报**、注释里的 `**` **误杀**。修复轮 3 复审实测：`#roundsSub`
    那一块**当时正落在里面**，而且它上面那段注释**被圈进了
    `innerHTML\\s*=\\s*([^;]+);` 的捕获串** ⇒ 注释自己那堆 `**` 躺在串里，
    **恰好**被注释正文里的一个 `` `rich(` `` 字样挡住了误杀 —— **两个方向都瞎，今天绿是运气**。
    ⇒ 修复轮 4 把那段注释**挪到 `innerHTML = …;` 语句外面**，这一块就恢复正常了。

    这条是**回归绊线**：谁把注释挪回语句里（或在这一块里再塞一段注释），它**会红** ——
    而不是让这一块安安静静地再瞎一次。⚠️ 射程：它只钉**这一块**，
    不替 `_strip_js_comments` 那段 docstring 里记着的**整片失步区**作证。
    """
    page = _page()
    assert not _raw_markdown_hits(page), _raw_markdown_hits(page)
    #: ★ 正控：把裸 `**` 塞进**这一块**的字符串里，尺子**必须响**
    marker = '      " 它收下你那份开场白之后，'
    assert page.count(marker) == 1, (
        "`#roundsSub` 那句承诺的起头找不到了（文案改了？）—— 这条绊线的锚要跟着改：%d 次"
        % page.count(marker))
    probed = page.replace(marker, '      " **探针** 它收下你那份开场白之后，')
    assert _raw_markdown_hits(probed), (
        "把裸 `**` 塞进 `#roundsSub` 那一块的字符串里，这把尺子**没响** —— 这一块又落回"
        "它的失步区了（多半是那段注释被挪回 `innerHTML = …;` 里面、把捕获串喂饱了）。"
        "`_strip_js_comments` 的 docstring 记着这个洞。")


def test_the_html_helpers_go_through_rich():
    """写进 DOM 的**助手内部**也要过 `rich()`。

    复审的变异：把 `setStopHint` 内部的 `rich(text)` 改回裸串 —— 上面那把尺子**看不见**
    （它只扫 `innerHTML = …` 那一句），于是**绿**，而运营在屏幕上看到 `**没请求成**`。
    """
    page = _page()
    for helper in ("function setStopHint(", "function noticeEl("):
        body = re.search(re.escape(helper) + r"[^{]*\{([^}]*)\}", page, re.S)
        assert body, "找不到这个助手：%s" % helper
        assert "rich(" in body.group(1), "%s 内部没过 `rich()`（运营会看到 `**`）" % helper


def test_the_left_column_warning_cannot_be_wiped_by_a_repaint():
    """★ 修复轮 3：左边那一栏上的警告**不许被重画擦掉** —— 与 `errBox` 那条是**同一条规矩**。

    ⚠️ 措辞（修复轮 4 改准）：**规矩相同，机制不同**（复审判得对，我原先写「同一个形状」不准）——
    · `errBox` 靠**归属分离**：钉的是静态标记里有 `id="errBox"`、且**重画告示的函数里不许出现这个名字**
      —— 这一条**对任何内容都成立**；
    · `#runs` 靠**一个标记位**：`paintRuns` 自己就是重画它的函数，它没法「不碰」，于是改成
      `runsBroken` 时**让位** —— 这一条**只在 `runsProblem` 说过话之后成立**。

    复审实测的场景（`/runs` 第一次好、之后 500、而 `/live` 每次都在变 —— **在跑的任务就是这样**）：

       172ms   「job-… 停在闸上，等你回话 · 3 轮（闸拍）」      ← 旧列表
       15031ms 「左边这一栏现在取不到：Internal Server Error…」  ← 这句真话
       16032ms 「job-… 停在闸上，等你回话 · 3 轮（闸拍）」      ← ★ 被**过期列表**盖掉
       30030ms 「左边这一栏现在取不到：…」                       ← 15 秒后又响一次
       31033ms 「job-… 停在闸上，等你回话 · 3 轮（闸拍）」      ← 又盖掉

    ⇒ 运营每 15 秒只有约 1 秒看得到真话，其余 14 秒看到的是一份**没有任何过期标记的旧列表**。
    机制：`fetchRuns` 失败时只是**写了字**，`state.runs` 里还是上一次成功那份；
    下一次 `/live` 一变 → `paint()` → `paintRuns(state.runs)` 非 null ⇒ 照画 ⇒ 把话擦掉。
    ⚠️ 这条规矩我在修复轮 1 就给 `errBox` 写下了（`test_the_error_line_cannot_be_wiped_by_a_repaint`
    「重画告示的函数不许碰它」）—— **`#runs` 漏了**。这里按**同一条规矩**补上（机制不同，见上）：坏着就别画。
    """
    page = _page()
    problem = re.search(r"function runsProblem\(.*?\n  \}", page, re.S).group(0)
    assert "state.runsBroken = true" in problem, "说话的时候没记下「这一栏现在是坏的」"
    painter = re.search(r"function paintRuns\(.*?\n  \}", page, re.S).group(0)
    assert re.search(r"if \(state\.runsBroken\) \{ return; \}", painter), \
        "`paintRuns` 不知道这一栏坏着 —— 旧列表会照画，把那句话盖掉"
    runs = re.search(r"function fetchRuns\(.*?\n  \}", page, re.S).group(0)
    assert "state.runsBroken = false" in runs, "拿到新列表时没把标记清掉（好了也一直不上屏）"


# ═══════════════════ Task 12：开一趟 / 窗口那两下（**标记这一半**）═══════════════════
#
# ⚠️ 这一节钉的是**标记**（哪一格在不在、旁边有没有那句人话）。**行为**那一半在
# `tests/test_console_js.py`（执行夹具）里 —— 按这一片的纪律，新行为不许只靠
# 源码字符串断言（Task 7 那条「page 只认 mode」的断言正文只有两个 `in` 就是个教训）。


def test_the_new_run_form_has_every_cell_and_a_human_sentence_beside_it():
    """「开一趟」那张表单：**每一格都在**，而且每一格旁边写着**它是干什么的**。

    为什么「旁边那句」也是判据：这一格存在的理由是「运营得知道该往里填什么」——
    一个只有 placeholder 的输入框，人只能靠猜（placeholder 会被输进去的字盖掉）。
    """
    page = _page()
    markup = page.split("<script>")[0]
    for fid in ("runUrl", "runGoal", "runSuccess", "runMode", "runEvidence", "btnRun"):
        assert 'id="%s"' % fid in markup, "表单少了这一格：%s" % fid
    #: 失败证据那一格**开页是收着的**（新站用不上它）—— 露不露由脚本按选的那一档摆
    ev = re.search(r'<div class="field" id="runEvidenceField"[^>]*>', markup)
    assert ev and "hidden" in ev.group(0), "失败证据那一格开页就该是收着的：%r" % (ev and ev.group(0))
    whys = [re.sub(r"<[^>]+>", "", w).strip()
            for w in re.findall(r'<p class="why">(.*?)</p>', markup, re.S)]
    assert len(whys) >= 5, "「每一格旁边那句人话」不够（只有 %d 句）：%r" % (len(whys), whys)
    for w in whys:
        assert len(w) >= 24, "这一句太短，说不出「它是干什么的、不填会怎样」：%r" % w
    #: **这一块里不出现线上那几格的名字**（人话纪律：字段名是给机器的）。
    #: ⚠️ 射程是**这一块**（`#newRunPanel` 那一段），不是整页 —— 页面上别处本来就有
    #: 带 `mode` 的 id（`#modeHint`，Task 7 的），拿整页去扫会误杀。
    block = markup.split('id="newRunPanel"', 1)[1].split("</section>", 1)[0]
    for wire in ("success_text", "ws_url", "job_id", "evidence", "mode"):
        assert wire not in block, "给运营看的这一块里出现了线上那一格的名字：%s" % wire


def test_the_new_run_form_never_checks_the_required_cells_by_itself():
    """★ **不许在页面上预先判断哪一格必填** —— 让服务说（它有那张人话单子）。

    这一条只钉得住**标记与声明**（`runPayload()` 里那几行）；真正「空着也照发」的行为
    由夹具那条量（它读的是**发出去的正文**）。这里钉的是那份**声明**别哪天被删掉。
    """
    page = _page()
    assert "function runPayload()" in page, "找不到「开一趟」那份正文的组装函数"
    body = re.search(r"function runPayload\(\).*?\n  \}", page, re.S).group(0)
    for key in ("url", "goal", "success_text", "mode", "evidence", "form_data"):
        assert '"%s":' % key in body, "那一格没进正文：%s" % key
    #: 它**只是取值**：一个 `if` 都不许有（有 `if` 就有「页面先判了一道」）
    assert "if " not in body, "这一份组装里出现了判断（页面在替服务做决定）：\n%s" % body
    assert "trim()" in body, "取值时该去掉首尾空白（不然「全是空格」会被当成填了）"


def test_the_stop_button_says_what_it_costs():
    """★ Task 12 第 3 件（**只改措辞**那个最轻的选项）：按钮上写着「撤不回」。

    ⚠️ 两处都钉：**静态标记**里那一句（开页时人先看到的）与**脚本里那一格**
    （接线时写进按钮的）—— 两处不一样，人看到的就是闪一下换个说法。
    """
    page = _page()
    markup = page.split("<script>")[0]
    tag = re.search(r'<button[^>]*id="btnStop"[^>]*>(.*?)</button>', markup, re.S)
    assert tag, "找不到「停」那个按钮"
    assert tag.group(1).strip() == "停下（撤不回）", tag.group(1)
    assert 'var STOP_LABEL = "停下（撤不回）";' in page, "脚本里那一格与标记对不上"


def test_the_window_block_carries_the_state_cell_and_the_two_buttons():
    """「这个窗口」那一块：**状态那一格** + **关/重开两个按钮**（默认都不摆）。

    为什么默认不摆：这两下的判据在服务给的那两格里（`can_close` / `can_reopen`）——
    开页就先摆出来，人在第一次 `/live` 回来之前会看到一个**服务还没说能给**的按钮。
    """
    page = _page()
    markup = page.split("<script>")[0]
    assert 'id="winState"' in markup, "「这个窗口」那一块没有状态那一格"
    for bid in ("btnCloseWindow", "btnReopen"):
        tag = re.search(r'<button[^>]*id="%s"[^>]*>' % bid, markup)
        assert tag, "少了这个按钮：%s" % bid
        assert "hidden" in tag.group(0), "这个按钮开页就该是收着的：%r" % tag.group(0)
    #: 它们在不在**只看服务那两格** —— 页面上不许出现「拿状态自己推一遍」的写法
    painter = re.search(r"function setWinActions\(win\).*?\n  \}", page, re.S)
    assert painter, "找不到摆这两个按钮的那一段"
    body = painter.group(0)
    assert 'at(win, "can_close"' in body and 'at(win, "can_reopen"' in body, body
    for wrong in ('at(win, "state"', 'at(live, "status"'):
        assert wrong not in body, "这两下在拿别的东西推（服务给的那两格才是判据）：%s" % wrong


# ═════════════ Task 13 ③：失败列表那一块（**标记层**）═════════════
#
# 行为那一半在 `tests/test_console_js.py`（跑起来才看得见）；
# 这一节钉的是**标记**：那几格在不在、每一格旁边有没有一句人话、
# 以及**那一块里不出现线上那几格的名字**（人话纪律）。


def test_the_failures_block_has_every_cell_and_a_human_sentence_beside_it():
    """失败列表那一块：**四格都在**（站名 / 查哪一段 / 查 / 照这条修），每一格旁边一句人话。

    为什么「旁边那句」也是判据：这一格存在的理由是「运营得知道该往里填什么」——
    一个只有 placeholder 的输入框，人只能靠猜（placeholder 会被输进去的字盖掉）。
    """
    page = _page()
    markup = page.split("<script>")[0]
    for fid in ("failSite", "failSince", "btnFail", "failPick", "btnFixFrom", "fails"):
        assert 'id="%s"' % fid in markup, "少了这一格：%s" % fid
    #: 「要修哪一条」那一块**开页是收着的**（还没有任何一条可挑）
    act = re.search(r'<div class="nrun" id="failAct"[^>]*>', markup)
    assert act and "hidden" in act.group(0), "那一块开页就该是收着的：%r" % (act and act.group(0))
    #: 三句「它是干什么的」（与「开一趟」那张表同一条纪律）
    block = markup.split('id="failsPanel"', 1)[1].split("</section>", 1)[0]
    whys = [re.sub(r"<[^>]+>", "", w).strip()
            for w in re.findall(r'<p class="why">(.*?)</p>', block, re.S)]
    assert len(whys) >= 3, "「每一格旁边那句人话」不够（只有 %d 句）：%r" % (len(whys), whys)
    for w in whys:
        assert len(w) >= 24, "这一句太短，说不出「它是干什么的」：%r" % w
    #: ★ 「只填表、不开跑」这件事**写在按钮旁边**（不然人以为按下去就跑了）
    assert "不会替你开跑" in block or "不会替你开跑" in page, block


def test_the_failures_block_never_shows_a_wire_name_or_a_backend_code():
    """**人话纪律**：给运营看的这一块里不出现线上那几格的名字，也不出现后端那些代号。

    ⚠️ 射程是**这一块**（`#failsPanel` 那一段标记），不是整页 —— 路由与参数名在
    `<script>` 那一段里（那是给机器看的那一半），拿整页去扫会误杀。
    """
    page = _page()
    markup = page.split("<script>")[0]
    block = markup.split('id="failsPanel"', 1)[1].split("</section>", 1)[0]
    for wire in ("task_id", "created_at", "country", "site_specific", "formLog", "formStep",
                 "no_success", "failures", "evidence", "since", "limit"):
        assert wire not in block, "给运营看的这一块里出现了线上那一格的名字：%s" % wire


def test_the_page_names_the_two_new_hops():
    """页面**自己**写着它要用的那两跳（`/failures` 与它下面那条）。

    ⚠️ 与 `test_the_page_names_every_hop_it_will_call_and_the_three_actions` 同一条规矩：
    地址写在这两个常量里（`FAILS` / `FAIL_EV`），而 `tests/test_console_js.py` 拿
    **服务那两个常量**算一遍地址去喂假 `fetch` —— 两边不一致时那几条会红。
    这里钉的是「页面确实点名了这两跳」。
    """
    page = _page()
    assert 'var FAILS = "/failures";' in page, "页面没点名 `/failures` 这一跳"
    assert 'var FAIL_EV = "/evidence";' in page, "页面没点名那一条证据的尾巴"
    #: 服务那边的两个常量也钉一下（页面那两个字符串就是照它们写的）
    assert service.FAILURES_PATH == "/failures", service.FAILURES_PATH
    assert service.FAILURE_EVIDENCE_PATH == "/failures/%s/evidence", service.FAILURE_EVIDENCE_PATH


# ═════════════ Task 4：榜单 → 失败单 → 原因（那三栏在页面上）═════════════════
#
# 这一节钉两件事，都是**静态的**（跑起来那半在 `test_console_js.py` 的 `rank-diag` 里）：
#   ① 那两跳的地址**是服务给的那两个**（不是页面手拼的一份 —— 两份手拼的迟早漂，
#      而漂了的那一份在页面上看着像「后端没数据」）；
#   ② ★ **既有的那一份契约一个字节没动**（brief §3 那四条 + §6.5）。


def test_the_new_hops_are_the_ones_the_service_declares():
    """★ `RANK` / `DIAG` 两个字面量**逐字**等于服务那两个常量（同源，不是「长得像」）。"""
    page = _page()
    assert 'var RANK = "%s";' % service.RANK_PATH in page, \
        "页面那一跳的地址与服务给的 `RANK_PATH` 不一致"
    #: 原因那一跳在页面上是**前缀**（后面拼单号），所以比的是它去掉 `%s` 之后那一段。
    assert 'var DIAG = "%s";' % service.DIAG_PATH.split("%s")[0] in page, \
        "页面那一跳的地址与服务给的 `DIAG_PATH` 不一致"


def test_the_three_panels_are_in_the_order_the_chain_is_read():
    """三栏的**先后**就是那条链的顺序（榜单 → 失败单 → 原因）——
    摆反了人就得从下往上读，而这一屏是给「一屏看完」用的。

    ⚠️ 这一条在 Task 4 修复轮 1 里**被我误删过一次**（重写 F6/F7 那两条时把这一段一起切掉了）——
    是**尺子①（逐行交代删掉的 assert）**把它抓回来的。所以它现在在这儿，
    而且下面每一条都带一句「它为什么值一条」。
    """
    page = _page()
    rank_at = page.index('id="rankPanel"')
    fails_at = page.index('id="failsPanel"')
    diag_at = page.index('id="diagPanel"')
    assert rank_at < fails_at < diag_at, (rank_at, fails_at, diag_at)
    #: 正控：三个地标**真的不同**（否则上面那条比的是同一个下标，恒真）。
    assert len({rank_at, fails_at, diag_at}) == 3


def _js_code(page: str) -> str:
    """页面里那段脚本 **去掉注释** 之后的正文（字符串字面量里的不算注释）。

    为什么需要它：判据要数的是「**代码里**有几处用到原因那一跳」——
    注释里提到它（`//: 原因那一跳拼的是 …DIAG + encodeURIComponent(单号)…`）**不算**。
    不去注释的话，数出来的数会被注释里的提到次数搅乱，「只有一处」就定不死。
    """
    out, i, quote = [], 0, ""
    while i < len(page):
        ch = page[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(page):
                out.append(page[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if page.startswith("//", i):
            j = page.find("\n", i)
            i = len(page) if j < 0 else j
            continue
        if page.startswith("/*", i):
            j = page.find("*/", i)
            i = len(page) if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _js_fetch_args(page: str) -> list:
    """页面上**每一处 `fetch(...)` 的第一个实参**（按括号配平切出来，去掉首尾空白）。

    ⚠️ 为什么要这么切，而不是比字面量：**逐字面量的负控换个写法就绕过去了** ——
    复审 F7 点名的正是这个：`var u = DIAG + key; fetch(u)` 能让
    「`fetch(DIAG + encodeURIComponent(site))` 不在页面上」那条断言**照样绿**。
    切出来之后，判据变成「**这一页发出去的每一个地址是怎么拼的**」——
    那才是「有没有拿站点键去查原因」这件事的正身。

    切法：找 `fetch(`，往后走到配平的 `)`（跳过字符串里的括号），遇到**顶层逗号**就停
    （`fetch(url, {opts})` 的第二个实参不是地址）。
    """
    args, i = [], 0
    while True:
        i = page.find("fetch(", i)
        if i < 0:
            return args
        j, depth, quote = i + len("fetch("), 1, ""
        end = j
        while j < len(page):
            ch = page[j]
            if quote:
                if ch == quote and page[j - 1] != "\\":
                    quote = ""
            elif ch in "\"'`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:            #: 这个 `fetch(` 收口了 —— **不含**这个右括号
                    end = j
                    break
            elif ch == "," and depth == 1:
                end = j                   #: 顶层逗号 = 第一个实参到此为止
                break
            j += 1
        args.append(" ".join(page[i + len("fetch("):end].split()))
        i = j + 1


def test_the_reason_hop_is_built_from_the_task_id_and_never_from_the_site_key():
    """★★ brief §2 R1：原因那一跳**只拼单号** —— 判据是**页面上发出去的地址**。

    榜单那个站点键**不保证干净**（线上实测过：整条 URL 带 query、尾巴上还粘着一段报错）——
    拿它当 join 键会**查到 0 行而且不报错**。所以这一页里**任何一处**拿站点键去查原因的写法
    都必须是错的。

    ⚠️ **复审 F7 之后改的写法**：上一版三条负控是**逐字面量**（`fetch(DIAG + encodeURIComponent(site))`
    之类）—— 换个写法（`var u = DIAG + key; fetch(u)`）就绕过去了，它证明不了「全页没有」。
    现在量的是**每一处 `fetch()` 的实参**：那才是这一页真的会发出去的东西。
    """
    page = _page()
    args = _js_fetch_args(page)
    #: 正控：这一页上确实有一把 `fetch(` 被切出来了（否则下面全是在量一个空列表）。
    assert len(args) >= 8, "只切出 %d 处 fetch —— 切法或页面形状变了：%r" % (len(args), args)
    diag = [a for a in args if "DIAG" in a]
    assert diag == ["DIAG + encodeURIComponent(id)"], \
        "发原因的那一处不是「拿单号拼」（实际：%r）" % (diag,)
    #: ★ 正身：**没有任何一处地址同时出现 `DIAG` 与一个站点键变量**。
    #: （`FAILS` 那一跳**本来就该**带站点键 —— 那是 `formLog` 唯一认的键，所以只查 `DIAG`。）
    for a in args:
        if "DIAG" in a:
            for key_var in ("site", "failSite", "rankPick", "key", "want"):
                assert key_var not in a, "拿站点键拼了原因那一跳：%r" % a
    #: ★★ 上面那两条还不够：**它们只看 `fetch(...)` 的实参文本**，
    #: 于是「先拼好再发」（`var u = DIAG + key; fetch(u)`）**整条滑过去**
    #: —— 复审 F7 点的正是这个形状。这一条量的是**标识符本身**：
    #: 去掉注释之后，`DIAG` 在整段脚本里**只许出现两次** ——
    #: 声明那一处，加上那个**唯一**被许可的用法。
    code = _js_code(page)
    decl = 'var DIAG = "%s";' % service.DIAG_PATH.split("%s")[0]
    assert decl in code, "那一跳的声明不见了：%r" % decl
    rest = code.replace(decl, "", 1)
    hits = re.findall(r"\bDIAG\b", rest)
    assert len(hits) == 1, (
        "去掉注释之后，`DIAG` 还出现在 %d 处（只许那一处「拿单号拼」）——"
        "多出来的那些很可能就是「先拼好再发」那种绕过写法" % len(hits))
    #: 正控：这一条**真的能响** —— 往页面里塞一处「先拼好再发」，它必须数出两处。
    evil = code.replace(decl, decl + "\n  function evilHop(k) { var u = DIAG + k; return fetch(u); }", 1)
    assert len(re.findall(r"\bDIAG\b", evil.replace(decl, "", 1))) == 2, \
        "这条正控自己写错了：塞进去的那一处没被数出来"


def _region(page: str, start: str, end: str) -> str:
    """页面里从 `start` 到 `end`（含）的那一段**原样**文本 —— 用来钉「一个字节没变」。"""
    i = page.index(start)
    j = page.index(end, i) + len(end)
    return page[i:j]


def test_the_task_4_panels_are_added_and_the_existing_contract_is_untouched():
    """★★ brief §3 / §6.5：**既有那一屏一个字节没变** —— 拿**原文**逐字比。

    ⚠️ **复审 F6 之后改的写法**：上一版这条的名字说的是「一个字节没变」，
    而它实际只量「地标还在」—— 改 `MODES` 里任何一句 `hint`、或者把三档换个顺序，
    它**照样绿**。契约**确实**没变（复审用 `cmp` 量过），短的是**这条判据**。

    现在四条契约各自按**边界**切出来，与一份**写死的原文**逐字比：
    改一个字节就红。代价说清：以后**故意**改这几处的文案也要改这一份 ——
    那正是「冻结」的意思（brief §3 说这几条一个字节都不许动）。
    """
    page = _page()
    #: ① `MODES` **三档**，整块逐字（改一句 hint、换一次顺序都会红）。
    assert _region(page, "  var MODES = {", "  };") == (
        '  var MODES = {\n'
        '    "gate": {\n'
        '      short: "闸上的回话",\n'
        '      placeholder: "写一句它该怎么做，或者按「继续」让它往下走",\n'
        '      hint: "这一行现在是**这道闸的回话**：按「说一句，接着走」才会送出去。"\n'
        '    },\n'
        '    "steer": {\n'
        '      short: "直达下一轮",\n'
        '      placeholder: "写一句它该怎么做 —— 会直达它的下一轮",\n'
        '      hint: "这一行现在是**直达**：你打的字不排队，会进它的下一轮（送到之后时间线上会出现一条「交给它了」）。"\n'
        '    },\n'
        '    "queue": {\n'
        '      short: "排队",\n'
        '      placeholder: "写一句它该怎么做 —— 它停下来时会进输入框",\n'
        '      hint: "这一行现在是**排队**：这句话先记下，等它到下一道闸时进到输入框 —— 你按一下才送过去，不会自动发。"\n'
        '    }\n'
        '  };'), "`MODES` 那一整块动了（brief §3 点名的契约）"
    #: ② `ACT`：四条老动作的映射，逐字。
    assert _region(page, "var ACT = {", "};") == (
        'var ACT = { "continue": "/reply", "say": "/say", "stop": "/stop", "again": "/again",\n'
        '              "run": "/run",\n'
        '              "windowClose": "/window/close", "reopen": "/reopen" };'), \
        "`ACT` 那一行动了"
    #: ③ `/say` 的**载荷字段名**（`text` —— 改成别的，Task 8 那条路当场 422），逐字。
    assert _region(page, 'act("say", {', "});") == 'act("say", { "text": text });', \
        "`/say` 的载荷字段名被改了"
    #: ④ `btnAgain` 那个**守卫**（没有 job 就不许发），逐字。
    assert _region(page, 'document.getElementById("btnAgain").addEventListener("click",',
                   "return; }") == (
        'document.getElementById("btnAgain").addEventListener("click", function () {\n'
        '    if (!jobId) { setErr("先挑一趟运行。"); return; }'), \
        "`btnAgain` 那个守卫动了"
    #: 正控：这一条**真的**在比原文（不是「切出来的两段都是空的」那种恒真）——
    #: 把那一段少切一个字，比对必须不等。
    assert _region(page, "  var MODES = {", "  };")[:-1] != _region(page, "  var MODES = {", "  };"), \
        "比对的两边是同一个表达式 —— 这条正控写错了"
    #: ⑤ ★ `STEER_WIRED` **仍是 `False`**（插话通道未上线，等真站演练）。
    assert service.STEER_WIRED is False, "插话通道的开关被翻开了"


def test_the_rank_panel_does_not_offer_anything_that_changes_anything():
    """★ 这一任务**只加「读」**（brief §4）：新那两栏里**没有一个** `POST`。

    判据取的是**真实的写法**（`method: "POST"`）而不是按钮的措辞 ——
    措辞会变，方法不会（而「只读」这句话的全部内容就是「没有写请求」）。
    """
    page = _page()
    for panel in ("rankPanel", "diagPanel"):
        start = page.index('id="%s"' % panel)
        #: 到下一个 `<section` 为止（那一栏自己的那一块）。
        block = page[start:page.index("<section", start + 10)] \
            if "<section" in page[start + 10:] else page[start:]
        assert "POST" not in block, "%s 里有写请求 —— 这一任务只加「读」" % panel
    #: 正控：这一条得**真的能响** —— 页面别处**有** `POST`（证明上面那个判据不是恒真）。
    assert 'method: "POST"' in page, "这一页上根本没有 POST —— 那上面那条判据是空转的"
