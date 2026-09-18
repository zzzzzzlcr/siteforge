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
    for pair in ('"continue": "/reply"', '"say": "/say"', '"stop": "/stop"',
                 '"again": "/again"'):
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
    push = re.search(r"out\.push\((.*?)\);", body, re.S)
    assert push, "`errorText` 里没有把每一项拼出来（`out.push(...)` 不在）"
    expr = push.group(1)
    assert "path.join(" in expr, "推进去的那句话没有用上 `loc` 的真值（那就成了一句静态话术）"
    assert "msg" in expr, "推进去的那句话没有用上 `msg` 的真值"
    assert "+" in expr, "它没有把这两样**拼起来**（拼一句固定的话不算）"


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
    """把 JS 注释抠掉 —— 判据只看**真写进 DOM 的表达式**。

    ⚠️ 不抠的话，赋值语句里一句合法的 `// …**着重**…` 注释会被判成违规
    —— 复审点名的**误杀**：这个文件到处是 `**着重**` 的注释写法，将来写进去就红。
    `(?<!:)//` 是为了不把字符串里的 `http://` 当成行注释。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


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
