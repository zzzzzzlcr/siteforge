"""第二种产物形态：**步进循环型** —— 每轮读页面 → 有什么做什么 → 循环。

## 为什么要有它（2026-09-24 用户点破「男/女 ⇒ 第二问会变」+ 仓里的两个活样本）
现在那条模板（`agent/template.py::render`）产的是「**固定步骤表**」：一串状态 + 每状态几步 +
每步一个 `when`。那一族在**单页漏斗 / 问卷**上会栽在同一个形状上 —— 它只记下**走过的那一条路**
✗，而这类站的下一问常常**取决于上一个答案** ⇒ `when` 不成立 ⇒ **整组静默跳过** ✗。
（2026-09-24 实测：`solarincentives` 那一趟从第 6 步起 **19 步全被跳过**，一趟白跑。）

## 活样本（两份都在生产跑着，这份模板逐条照它们的形状）
- `goldenagesouls.py`（14220 字节，2026-09-17 重写上线）：
  `run(max_steps=60)` + `for step in range(1, max_steps + 1)`——每轮 `read()` → 填空输入 →
  选题 → 点 Next → 分清「忙」与「真空转」→ 到顶**如实报失败**；**成功只看 URL**。
- `forms/sites/lastingpowerofattorney.before.py`：同族的稳定性三招 ——
  **按文字定位**（那些按钮没有稳定 CSS ⇒ JS 找到 → 打标记 → 再 `cdp click`）、
  **推进判据 = 等下一屏那句话**、**遮挡反复清**（点完复读确认）。
- 诚实那条来自 `docs/执行事实契约-2026-09-18.md`：`read()` **读不到必须返回 `None`** ✗
  不许当成「做完了」（`datewhirl.py:175` 连瞎三周、日志里成功 0 次，就是这么来的）。

⇒ 生成器要填的只有**四格**：`READ_PAGE_JS` / `value_for`（每格填什么）/ `pick_option`（挑哪个，
**可以随机**）/ 成功判据；骨架一个字不用动。
"""

from __future__ import annotations

import json

from agent.template import _lit, _provenance_literal

__all__ = ["render_loop", "LOOP_PROVENANCE_KEEP", "DEFAULT_READ_JS"]

#: 这一族也要交代出处（与 `template.render` 同一套 key —— 一条口径）。
LOOP_PROVENANCE_KEEP = ("site", "entry_url", "explored_at", "from", "note")

#: 默认的「读这一屏」JS：**只读，不点**。`goldenagesouls.read()` 那一套的最小版 ——
#: 它读回 `{url, head, progress, inputs:[[testid,value]], options:[…], nextVisible, nextTxt}`。
#: ⚠️ 选项如果没有 `data-testid`，就用 `"\u0001" + 文字` 交回去 —— 调用方看到这个前缀
#: 就走「按文字打标记再点」那条路（那一族按钮**没有稳定的 CSS 选择器**）。
#: ⚠️ 这里面**不许**出现 python 式 `#` 注释（`tests/test_loop_template.py` 拿 `node --check`
#: 真验它的语法 —— 2026-09-24 我自己塞过一行，那会让浏览器 eval 报错、`read()` 恒 None）。
DEFAULT_READ_JS = r"""(function(){
  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}
  function txt(e){return ((e.textContent||'').replace(/\s+/g,' ').trim());}
  function tid(e){var t=e.getAttribute('data-testid')||e.id||e.getAttribute('name');
    if(t)return t;
    var p=(e.getAttribute('placeholder')||e.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
    return p?("\u0002"+p):'';}
  var inputs=[];
  document.querySelectorAll('input,select,textarea').forEach(function(e){
    if(!vis(e))return; var t=tid(e); if(!t)return;
    inputs.push([t, (e.value||'').trim()]);});
  var options=[], nextVisible=false, nextTxt='';
  document.querySelectorAll('button,label,[role=button],a[href]').forEach(function(e){
    if(!vis(e))return; var s=txt(e); if(!s)return;
    if(/^\s*(next|continue|get results|submit)\s*$/i.test(s)){nextVisible=true;nextTxt=s;return;}
    var t=tid(e);
    if(t) options.push(t); else options.push("\u0001"+s);});
  var pm=(document.body.innerText.match(/\b\d{1,3}\s*%/)||[])[0]; var prog=pm?pm:'';
  var head=(document.querySelector('h1,h2,h3')||document.body).innerText||'';
  return JSON.stringify({url:location.href, head:head.replace(/\s+/g,' ').trim().slice(0,300),
                         progress:prog, inputs:inputs, options:options, nextVisible:nextVisible,
                         nextTxt:nextTxt});})()"""


def render_loop(site, success_text, success_urls, fills, provenance, *,
                read_js: str = "", pick: str = "random", max_steps: int = 60,
                idle_limit: int = 6, busy_timeout: int = 90) -> str:
    """把「怎么读这一屏、每格填什么、选项挑哪个」渲染成一条**步进循环型**的完整 py。

    `success_urls` 是**主判据**（这一族只看网址 —— 老手的原话：「正文判据是这一族栽跟头的
    老地方」，见 `lastingpowerofattorney.before.py` 的头注释）。`success_text` 给了就**加一条**
    兜底判据（URL 判据不成立、纯 SPA 不改地址时用得上），没给就不写。
    `pick`：`random`（反检测那一族）或 `first`。
    `fills`：`{字段名: {kind, label, fallback}}` —— 形状与 `template.render` **同源**。
    """
    if not str(site or "").strip():
        raise ValueError("site 不能空")
    urls = [u for u in ([success_urls] if isinstance(success_urls, str) else (success_urls or []))
            if str(u or "").strip()]
    if not urls:
        raise ValueError("成功判据不能空：这一族**只看网址** —— 没有网址判据的产物"
                         "会「跑到底再说自己成功」，那是这一套里最忌讳的那类谎")
    if str(pick) not in ("random", "first"):
        raise ValueError("pick 只能是 random 或 first（给的是 %r）" % (pick,))
    for name, spec in (fills or {}).items():
        if not isinstance(spec, dict) or not str(spec.get("kind") or "").strip():
            raise ValueError("FILLS[%r] 没给 kind —— 填什么由它决定，没它这一格填不了" % (name,))
    src = _LOOP_SKELETON
    for token, value in (("__SITE__", _lit(str(site))),
                         ("__URL_MARKERS__", json.dumps(urls, ensure_ascii=False)),
                         ("__TEXTS__", json.dumps([success_text] if str(success_text or "").strip()
                                                  else [], ensure_ascii=False)),
                         ("__FILLS__", _lit(fills or {})),
                         ("__PICK__", _lit(str(pick))),
                         ("__MAX_STEPS__", str(int(max_steps))),
                         ("__IDLE_LIMIT__", str(int(idle_limit))),
                         ("__BUSY_TIMEOUT__", str(int(busy_timeout))),
                         ("__READ_JS__", json.dumps(read_js or DEFAULT_READ_JS)),
                         ("__PROVENANCE__", _provenance_literal(provenance))):
        src = src.replace(token, value)
    return src


#: 固定骨架。⚠️ 与 `template.render` **同一个生产契约**（`forms/common.py::CDPHelper` + `report_url`
#: + `fix.REQUIRED_CLI_FLAGS` 那四个）；**自测**那三个（`--trace` / `--delay` / `--no-report`）也必须在 ——
#: 少了它们，产物在自测里**一上来就 argparse 退出**（2026-09-24 实测：`unrecognized arguments:
#: --trace … --no-report`，那一刻报告只剩「进程退了（退出码 2）」）。
_LOOP_SKELETON = '''#!/usr/bin/env python3
"""Site script for __SITE__ — 步进循环型：每轮读页面 → 有什么做什么 → 循环。

（由 siteforge 生成：骨架固定，只填了「怎么读 / 填什么 / 挑哪个 / 判成功」四格。）
形状照 `goldenagesouls.py`（生产在跑的那份）；诚实那条照 `docs/执行事实契约-2026-09-18.md`：
**读不到就是读不到**，绝不折算成「做完了」。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

SITE = __SITE__
SUCCESS_URL_MARKERS = __URL_MARKERS__
SUCCESS_TEXTS = __TEXTS__
FILLS = __FILLS__
PICK = __PICK__
MAX_STEPS = __MAX_STEPS__
IDLE_LIMIT = __IDLE_LIMIT__
BUSY_TIMEOUT = __BUSY_TIMEOUT__
READ_PAGE_JS = __READ_JS__
#: 蜜罐：这些字样命中的输入框**永远不填**（真人看不见它、机器人会填 ⇒ 整单判垃圾）。
SKIP_FIELDS = ("company", "company_url", "website", "url", "fax")
#: 页面「在处理」的文案（忙 ≠ 空转；与 goldenagesouls 同一套思路）。
BUSY_MARKERS = ("almost done", "processing your", "setting up", "just a moment",
                "getting things ready", "hold on", "calculating", "please wait")
PROVENANCE = __PROVENANCE__
#: 选项没给 `data-testid` 时的前缀：`\\x01文字` = 「按这段文字去页面上找」。
BY_TEXT = "\\x01"
#: 输入框没给 `testid`/`id`/`name` 时的前缀：`\\x02placeholder` = 「按占位符去页面上找」。
#: ★ 2026-09-24 第一次真站试跑逼出来的：那一屏的邮编格**只有 placeholder**（形状同 LPA 的
#: `input[placeholder="e.g. RG24 8PE"]`）⇒ 读取器原来直接丢掉它 ⇒ 只剩 Continue ⇒ 空点 55 次。
BY_PLACEHOLDER = "\\x02"

#: 连着点几次「Next」页面还是那一屏 ⇒ 当**空转**，如实收摊（第一版没有这道闸，
#: 真站上才会空点 55 次 Continue 把步数耗光 ✗）。
SAME_NEXT_LIMIT = 3


class Filler:
    def __init__(self, ws_url, form_file, correlation_id, task_id="",
                 trace_path="", no_report=False):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id
        self.log = setup_logger("loop")
        self._filled = set()
        #: ★ 自测那两格（`selftest.ARTIFACT_FLAGS`）：少了它们，产物在自测里**一上来就 argparse
        #: 退出**（2026-09-24 实测：`unrecognized arguments: --trace … --no-report` ⇒ 报告只剩
        #: 「进程退了（退出码 2）」）。`--no-report` 是硬规矩（自测不是生产任务，不该在生产那边
        #: 留记录）；`--trace` 是自测判「每一步成没成」的读数（没有它只剩退出码）。
        self.trace_path = trace_path
        self.no_report = bool(no_report)

    def _dly(self, a=0.6, b=1.8):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step, reason=""):
        if self.no_report:
            return None                     # 自测那一遍：**永不上报**
        return report_url(self.cdp, self.tid, step, self.log, reason)

    def _trace(self, **row):
        """落一行 trace（JSON Lines）。自测/面板按它读「这一步成没成」——没有它只剩退出码。"""
        if not self.trace_path:
            return
        try:
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\\n")
        except OSError:
            pass                            # 旁路，坏了不带塌这一趟

    def read(self):
        """读这一屏。**读不到返回 None** —— 那是「看不见」，不是「做完了」。

        ⚠️ `None` 是一等值（`docs/执行事实契约-2026-09-18.md` 第 ② 条）：`datewhirl.py:175`
        就是把「什么都没看见」判成「做完了」，连瞎三周、日志里成功 0 次。
        """
        raw = (self.cdp.eval(READ_PAGE_JS, "") or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    def is_success(self, url):
        low = (url or "").lower()
        return any(str(m).lower() in low for m in SUCCESS_URL_MARKERS)

    # ---------- 填什么（生成器填的那一格）----------

    def fill(self, token, value):
        """填一格：有 `data-testid` 就用它；没有就**按 placeholder/aria-label 打标记再填**。

        ★ 2026-09-24 第一次真站试跑逼出来的：那一屏的邮编格**只有 placeholder**（形状同 LPA 的
        `input[placeholder="e.g. RG24 8PE"]`）⇒ 读取器原来直接丢掉它 ✗ ⇒ 「没东西可填」⇒
        只剩 Continue ⇒ **空点 55 次** ✗。做法与「按文字点」同一条：找用 JS、填还是走 `cdp.form`。
        """
        token = str(token or "")
        if token.startswith(BY_PLACEHOLDER):
            want = token[len(BY_PLACEHOLDER):]
            js = ("(function(){var t=" + json.dumps(want) + ";"
                  "var es=document.querySelectorAll('input,textarea,select');"
                  "for(var i=0;i<es.length;i++){var e=es[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var p=(e.getAttribute('placeholder')||'').replace(/\\\\s+/g,' ').trim();"
                  "var al=(e.getAttribute('aria-label')||'').replace(/\\\\s+/g,' ').trim();"
                  "if(p===t||al===t){var old=document.querySelectorAll('[data-sf-fill]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-fill');}"
                  "e.setAttribute('data-sf-fill','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            self.cdp.form('[data-sf-fill="1"]', value=value)
            return True
        self.cdp.form('[data-testid="%s"]' % token, value=value)
        return True


    def value_for(self, token, page):
        """这一格填什么：`FILLS[name].kind` 决定用哪一类值，取不到就用池子（兜底）。"""
        hay = str(token or "").lower()
        if hay.startswith(BY_PLACEHOLDER):
            hay = hay[len(BY_PLACEHOLDER):]
        if any(s in hay for s in SKIP_FIELDS):
            return ""                       # 蜜罐：绝不填
        p = self.form_data if isinstance(self.form_data, dict) else {}
        for name, spec in (FILLS or {}).items():
            spec = spec or {}
            kind = str(spec.get("kind") or "")
            label = str(spec.get("label") or name).lower()
            if (label and label in hay) or (kind and kind in hay):
                for key in (name, kind):
                    got = p.get(key)
                    if got:
                        return str(got)
                return self._pool(kind or name)
        return ""

    def _pool(self, kind):
        kind = (kind or "").lower()
        if "first" in kind or "last" in kind or "name" in kind:
            return random.choice(("James", "Sarah", "David", "Emma", "Michael", "Laura"))
        if "email" in kind:
            return "user%d@%s" % (random.randint(100, 9999),
                                  random.choice(("gmail.com", "outlook.com", "yahoo.com")))
        if "phone" in kind or "mobile" in kind:
            return "07" + "".join(str(random.randint(0, 9)) for _ in range(9))
        if "post" in kind or "zip" in kind:
            return random.choice(("SW1A 1AA", "M1 1AA", "B1 1AA", "LS1 1AA"))
        return ""

    # ---------- 挑哪个（生成器填的那一格）----------

    def pick_option(self, options):
        """选项里挑一个：`random`（反检测那一族）或 `first`。没有选项 ⇒ None。"""
        cand = [o for o in (options or []) if str(o or "").strip()]
        if not cand:
            return None
        return random.choice(cand) if PICK == "random" else cand[0]

    def click(self, token):
        """点一个「选项/按钮」：有 `data-testid` 就用它；没有就**按文字打标记再点**。

        ★ 稳定性那一招（`lastingpowerofattorney.before.py` 的原话：「这些按钮**没有稳定的
        CSS 选择器**」—— 选项 id 是 `option-<epoch毫秒>`、类名一长串）⇒ 找用 JS、点还是
        `cdp click`（真人手势那一层不能省）。
        """
        token = str(token or "")
        if token.startswith(BY_TEXT):
            text = token[len(BY_TEXT):]
            payload = json.dumps(text)
            js = ("(function(){var t=" + payload + ";var bs=document.querySelectorAll('button,label');"
                  "for(var i=0;i<bs.length;i++){var e=bs[i];"
                  "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
                  "var s=(e.textContent||'').replace(/\\\\s+/g,' ').trim();"
                  "if(s===t){var old=document.querySelectorAll('[data-sf-pick]');"
                  "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-sf-pick');}"
                  "e.setAttribute('data-sf-pick','1');return 'marked';}}return 'none';})()")
            if "marked" not in (self.cdp.eval(js, "") or ""):
                return False
            self.cdp.click('[data-sf-pick="1"]')
            return True
        self.cdp.click('[data-testid="%s"]' % token)
        return True

    # ---------- 主循环 ----------

    def run(self, max_steps=MAX_STEPS):
        try:
            self.log.info("[%s] loop: site=%s provenance=%s", self.cid, SITE,
                          PROVENANCE.get("site"))
            answered = 0
            idle = 0
            busy_since = None
            for step in range(1, max_steps + 1):
                self._dly(0.6, 1.8)
                page = self.read()
                if page is None:
                    idle += 1
                    self.log.info("[%s] step %d: 页面读不到（%d）", self.cid, step, idle)
                    self._trace(step=step, action="read", ok=None, progress=None,
                                progress_why="页面读不到")
                    if idle >= IDLE_LIMIT:
                        self.log.error("[%s] 连续 %d 步读不到页面 —— 如实报失败", self.cid, idle)
                        self._rpt("max_steps", "page unreadable")
                        return False
                    continue

                url = page.get("url", "")
                if self.is_success(url):
                    self.log.info("[%s] SUCCESS(url): %s", self.cid, url[:90])
                    self._trace(step=step, action="success", ok=True, progress=True,
                                url=url[:120])
                    self._rpt("success", url[:60])
                    return True
                text = (page.get("head") or "")
                if SUCCESS_TEXTS and any(str(t).lower() in text.lower() for t in SUCCESS_TEXTS):
                    self.log.info("[%s] SUCCESS(text): %s", self.cid, text[:60])
                    self._trace(step=step, action="success", ok=True, progress=True,
                                url=url[:120], progress_why=text[:60])
                    self._rpt("success", text[:60])
                    return True

                # ① 有空的可填 ⇒ 填第一个（「空了才填」⇒ 一格/两格的形态切换天然不怕）
                todo = [t for t, v in (page.get("inputs") or []) if t and not v]
                if todo:
                    tid = todo[0]
                    val = self.value_for(tid, page)
                    if val and (tid, val) not in self._filled:
                        if not self.fill(tid, val):
                            continue           # 填不上就下一轮再看（绝不谎报填了）
                        self._filled.add((tid, val))
                        self.log.info("[%s] step %d: 填了 %s", self.cid, step, tid)
                        self._trace(step=step, action="form", ok=True, progress=False,
                                    target={"testid": tid}, note="填了 %s" % tid)
                        idle = 0
                        busy_since = None
                        continue

                # ② 有题可选 ⇒ 挑一个（随机或第一个）
                pick = self.pick_option(page.get("options"))
                if pick:
                    if self.click(pick):
                        answered += 1
                        idle = 0
                        busy_since = None
                        self.log.info("[%s] step %d [%s] %s -> %s", self.cid, step,
                                      page.get("progress", "?"), (page.get("head") or "")[:48],
                                      str(pick)[:32])
                        self._trace(step=step, action="click", ok=True, progress=True,
                                    target={"token": str(pick)[:48]},
                                    note="答了第 %d 题" % answered)
                        continue

                # ③ 只剩 Next ⇒ 点它
                if page.get("nextVisible"):
                    sig = "%s|%s|%d" % (page.get("progress"), (page.get("head") or "")[:60],
                                        len(page.get("options") or []))
                    if sig == getattr(self, "_last_next_sig", ""):
                        self._same_next = getattr(self, "_same_next", 0) + 1
                    else:
                        self._same_next = 0
                    self._last_next_sig = sig
                    if self._same_next >= SAME_NEXT_LIMIT:
                        self.log.error("[%s] 连着 %d 次点 Next、页面还是那一屏 —— 如实报失败",
                                       self.cid, self._same_next)
                        self._rpt("max_steps", "next without change")
                        return False
                    self.click(BY_TEXT + str(page.get("nextTxt") or "Next"))
                    idle = 0
                    busy_since = None
                    self.log.info("[%s] step %d: Next（%s）", self.cid, step,
                                  (page.get("head") or "")[:40])
                    self._trace(step=step, action="click", ok=True, progress=None,
                                progress_why="点了 Next，页面变没变由下一轮读出来",
                                target={"text": str(page.get("nextTxt") or "Next")}, note="Next")
                    continue

                # ④ 没得点：先分清「页面在处理」还是「真的没事干」
                head = page.get("head") or ""
                if any(m in head.lower() for m in BUSY_MARKERS):
                    if busy_since is None:
                        busy_since = time.time()
                    waited = time.time() - busy_since
                    if waited >= BUSY_TIMEOUT:
                        self.log.error("[%s] 页面在「处理」态停了 %.0f 秒 —— 如实报失败",
                                       self.cid, waited)
                        self._rpt("max_steps", "busy %.0fs" % waited)
                        return False
                    idle = 0                    # 忙 ≠ 空转
                    self.log.info("[%s] step %d: 页面在处理中（%.0fs/%ds）", self.cid, step,
                                  waited, BUSY_TIMEOUT)
                    self._trace(step=step, action="wait", ok=None, progress=None,
                                progress_why="页面在处理中 %.0fs" % waited)
                    continue

                # ⑤ 真的没事干
                busy_since = None
                idle += 1
                self.log.info("[%s] step %d: 没有可动作的东西（%d）%s", self.cid, step, idle,
                              head[:50])
                self._trace(step=step, action="none", ok=None, progress=False,
                            progress_why="没有可动作的东西（%d）" % idle)
                if idle >= IDLE_LIMIT:
                    self.log.error("[%s] 连 %d 步没有可动作的东西（答过 %d 题）—— 如实报失败",
                                   self.cid, idle, answered)
                    self._rpt("max_steps", "stalled after %d answers" % answered)
                    return False

            self.log.error("[%s] 走满 %d 步还没到成功 —— 如实报失败", self.cid, max_steps)
            self._rpt("max_steps")
            return False
        except Exception as exc:                    # noqa: BLE001 —— 外面世界
            self.log.error("[%s] Error: %s", self.cid, exc)
            return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-url", required=True)
    ap.add_argument("--form-file", required=True)
    ap.add_argument("--correlation-id", required=True)
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--trace", default="")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()
    log = setup_logger("loop")
    log.setLevel(a.log_level)
    f = Filler(a.ws_url, a.form_file, a.correlation_id, a.task_id,
               trace_path=a.trace, no_report=a.no_report)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
'''
