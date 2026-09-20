// Task 10 Step 2.5 的**执行夹具**（`node` + 假 `document` / 假 `fetch`，不是 headless 浏览器）。
//
// 用法：node console_js_driver.js <console.html> <payloads.json>
// 打印**一行 JSON**（观测结果）到 stdout，退出码 0；自己出错就非 0 退出（不吞）。
//
// 它做的事：把页面里那段**原样的** JS（`agent/console.html` 一个字节都不改）在一个
// 假 DOM / 假 fetch 里跑起来，然后**按一个真人会走的顺序**驱动它。两个场景（`payload.scenario`）：
//   · `repaint`（默认）：开页 →（`/live` + `/runs` 各来一份）→ 点「停」（服务回 404 人话）
//     → `/live` 变了（重画）→ `/runs` 取不到（左边那一栏说真话）→ `/live` 又变（**又一次重画**）
//   · `again`：开页（这一趟到头了）→ 点「重新来一遍」→ `/again` 回新 job
//   · `artifact`：开页（**还没有**产物）→ `/live` 里出现产物那一格 → 又三次重画
//     （量「写进去了」之后**还在不在** —— Task 11 / §十五）
//   · `run` / `run-refused`：开一趟（Task 12）—— 人把表填了、按一下；`/run` 收下了 / 回了错
//   · `window`：窗口那两下（Task 12）—— 按「关掉这个窗口」/「重开窗口，接着走」
//   · `gate-facts`：闸口摊开的事实（Task 14 修复轮 1）—— `#rounds` 那两块文字上没上屏
// 打完这些之后，把屏幕上**那几个元素此刻的文本**交回去，外加一个数：**重画了几次**。
//
// ⚠️ 射程（写在 `tests/test_console_js.py` 的模块 docstring 里，这里只留一句）：
//    它看得见「哪个值、什么顺序、被写进哪个元素」；看不见像素、看不见真浏览器的
//    事件循环、也看不见任何依赖真实布局（`scrollHeight` 之类被桩成 0）的东西。
"use strict";

const fs = require("fs");
const vm = require("vm");

const [htmlPath, dataPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, "utf8");
const payload = JSON.parse(fs.readFileSync(dataPath, "utf8"));

// ── 页面里那段脚本（**原样切出来**，不改一个字节）────────────────────────
const open = html.indexOf("<script>");
const close = html.lastIndexOf("</script>");
if (open < 0 || close < 0) { throw new Error("console.html 里找不到那一段 <script>"); }
const source = html.slice(open + "<script>".length, close);

// ── 假 DOM ──────────────────────────────────────────────────────────────
const els = {};
let newEls = 0;
//: **重画次数**（Task 10 修复轮 1 / C1）：`paintTimeline()` 每画一次就把 `#timeline` 的
//: `innerHTML` 写一次，而它只由 `paint()` 调用 ⇒ 这个数 = `paint()` 的次数 = **重画次数**。
//: ⚠️ 别拿「`/live` 被 fetch 了几次」当它：页面每 3 拍**无条件** fetch，
//: 而重画由 `if (key !== seen)` 单独决定 —— 载荷一旦不变，fetch 照数，重画是 0
//: （实测：三种载荷下 fetch 都是 10，重画是 10 / 3 / 2）。
//: 几个**时刻上的读数**收在 `paintMarks`（`afterLoad` / `afterStop` / `afterRunsBroken` / `end`）——
//: 「活过几次重画」那几个数是它们**两两相减**得来的，不是估的（修复轮 2 / D2）。
let timelineWrites = 0;
function el(id) {
  if (!els[id]) {
    els[id] = {
      id: id, innerHTML: "", textContent: "", value: "", hidden: false, className: "",
      disabled: false, placeholder: "", src: "", scrollTop: 0, scrollHeight: 0,
      clientHeight: 0, listeners: {}, children: [],
      addEventListener: function (type, fn) { this.listeners[type] = fn; },
      appendChild: function (node) {
        this.children.push(node);
        this.innerHTML += node.innerHTML || node.textContent || "";
      },
      getAttribute: function () { return null; },
    };
    if (id === "timeline") {
      //: `#timeline` 的 `innerHTML` 换成一对带计数的取值器（见 `timelineWrites`）
      let wrote = "";
      Object.defineProperty(els[id], "innerHTML", {
        get: function () { return wrote; },
        set: function (v) { wrote = v; timelineWrites += 1; },
        configurable: true,
      });
    }
  }
  return els[id];
}
//: 静态标记里它带着 `hidden`（`console.html:196`：`<p class="notice err" id="errBox" hidden>`）——
//: 假 DOM 得从同一个起点出发，否则「开页时它是空的」这句话量的是夹具不是页面
//: （`tests/test_console_js.py` 里有一条断言钉着那处标记真的带 `hidden`）。
el("errBox").hidden = true;

const documentListeners = {};
const doc = {
  getElementById: el,
  createElement: function (tag) { const e = el("made-" + tag + "-" + (++newEls)); e.tagName = tag; return e; },
  addEventListener: function (t, fn) { (documentListeners[t] = documentListeners[t] || []).push(fn); },
  body: el("body"),
};

// ── 假 fetch：一个 URL 一条队列，取完之后**重复最后一条**（页面每 3 秒拉一次）──
const queues = {};
for (const key of Object.keys(payload.responses || {})) {
  queues[key] = (payload.responses[key] || []).slice();
}
const seen = [];
//: 每一次请求的**正文**（Task 12）：开一趟那一条判据量的是「发出去的是不是 `/run` 要的那几格」——
//: 只数 URL 的话，「地址对了、字段名全错」这种改法照绿（而它到了服务那边就是一份空载荷）。
const sent = [];
function fakeFetch(url, opts) {
  const q = queues[url];
  seen.push(url);
  sent.push({ url: url, body: (opts && opts.body) ? String(opts.body) : "" });
  if (!q || !q.length) { return Promise.reject(new Error("夹具没给这个 URL 准备响应：" + url)); }
  const item = q.length > 1 ? q.shift() : q[0];
  return Promise.resolve({
    ok: item.status === undefined || item.status < 400,
    status: item.status === undefined ? 200 : item.status,
    text: function () { return Promise.resolve(JSON.stringify(item.body)); },
  });
}

// ── 假时钟：`setInterval` 的回调**抓在手上**，由夹具自己驱动（不真等秒）──
let ticker = null;
const sandbox = {
  document: doc, fetch: fakeFetch, location: { search: payload.search || "" },
  setInterval: function (fn) { ticker = fn; return 1; },
  setTimeout: setTimeout, URLSearchParams: URLSearchParams, history: { replaceState: function () {} },
  JSON: JSON, Date: Date, Math: Math, console: console, Promise: Promise,
  encodeURIComponent: encodeURIComponent, isNaN: isNaN, String: String, Array: Array,
  Object: Object, Boolean: Boolean, Number: Number, Error: Error, RegExp: RegExp,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

const settle = () => new Promise((r) => setTimeout(r, 0));
const ticks = async (n) => { for (let i = 0; i < n; i++) { ticker(); await settle(); } };

//: **像浏览器那样**触发一个监听器：`this` 绑到那个元素上。
//: ⚠️ 别写成 `el(id).listeners.click()` —— 那样 `this` 是那个 `listeners` 对象，
//: 于是页面里 `this.value` / `this.disabled` 读到的是 `undefined`：
//: 那不是浏览器里的形状，量出来的东西也就不是这一屏的行为。
const fire = (id, type) => { el(id).listeners[type].call(el(id)); };

async function repaintScenario(out) {
  out.paintMarks = {};
  out.paintMarks.afterLoad = timelineWrites;        // 开页那一次
  out.afterLoad = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    secondHint: el("secondHint").innerHTML, runs: el("runs").innerHTML,
                    winState: el("winState").textContent,
                    closeHidden: el("btnCloseWindow").hidden,
                    reopenHidden: el("btnReopen").hidden };

  el("btnStop").listeners.click();                  // 人按「停」（服务回 404）
  await settle();
  out.paintMarks.afterStop = timelineWrites;        // 「停」之后的那个数（第三个数的减法要用它）
  out.afterStop = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    stopHint: el("stopHint").innerHTML, statusPill: el("statePill").textContent };
  out.stopRequests = seen.filter((u) => u.endsWith("/stop")).length;

  await ticks(15);                                  // 走满一轮：`/live` 每 3 拍、`/runs` 每 15 拍
  out.afterRunsFailure = { runs: el("runs").innerHTML, errBox: el("errBox").textContent,
                           errHidden: el("errBox").hidden };
  out.paintMarks.afterRunsBroken = timelineWrites;  // 「左边那一栏坏掉」那一刻的重画数
  await ticks(9);                                   // 之后**又三次重画**（`/live` 每 3 拍就变）
  out.afterRepaint = { runs: el("runs").innerHTML, errBox: el("errBox").textContent,
                       errHidden: el("errBox").hidden, secondHint: el("secondHint").innerHTML,
                       statusPill: el("statePill").textContent,
                       sayBoxPlaceholder: el("sayBox").placeholder,
                       winState: el("winState").textContent,
                       closeHidden: el("btnCloseWindow").hidden,
                       reopenHidden: el("btnReopen").hidden };
}

//: 产物那一趟（Task 11 / §十五）：开页时**还没有**产物 → 跑着跑着 `/live` 里出现了那一格
//: → 之后**还继续重画**（「写进去了」与「还在不在」是两件事 —— Task 7 那一族）。
//: 两个场景（有产物 / 到头了但没产物）走的**是同一段驱动**：差别只在载荷里那一格。
async function artifactScenario(out) {
  out.paintMarks = {};
  out.paintMarks.afterLoad = timelineWrites;        // 开页那一次（还没有产物）
  out.afterLoad = { timeline: el("timeline").innerHTML };
  await ticks(4);                                   // 第二次 `/live` 里就有产物了
  out.paintMarks.afterArtifact = timelineWrites;
  out.afterArtifact = { timeline: el("timeline").innerHTML };
  await ticks(9);                                   // 之后**又三次重画**（正文每次都在变）
  out.afterRepaint = { timeline: el("timeline").innerHTML };
}

//: 「开一趟」（Task 12）：**运营在面板上开新活**那条路 —— 原先面板上根本没有这一跳。
//: 走的是真人那条路：选「老站」→ 那一格露出来 → 把四格填了 → 按「开一趟」。
//: 量三样：① 那一跳**发到哪**（必须正好是 `POST /run`，不是 `/job/<id>/run`）；
//: ② 正文**是不是 `/run` 要的那几格**（`sent`，只数 URL 的话字段名全错也照绿）；
//: ③ 拿到 job id 之后**这一屏跟不跟着走**（`whoJob` + 新那一趟的 `/live`）。
async function runScenario(out) {
  out.afterLoad = { who: el("whoJob").textContent,
                    evidenceHidden: el("runEvidenceField").hidden,
                    //: 「停」那个按钮上现在写的是什么（Task 12 只改了措辞那一个选项）
                    stopLabel: el("btnStop").textContent };
  // ① 选「老站」→ 失败证据那一格**露出来**；再选回「新站」→ 收回去。
  el("runMode").value = "fix";
  fire("runMode", "change");
  out.afterPickFix = { evidenceHidden: el("runEvidenceField").hidden };
  el("runMode").value = "build";
  fire("runMode", "change");
  out.afterPickBuild = { evidenceHidden: el("runEvidenceField").hidden };
  // ② 人选回「老站」，把四格填上，按「开一趟」
  el("runMode").value = "fix";
  fire("runMode", "change");
  el("runUrl").value = payload.run.url;
  el("runGoal").value = payload.run.goal;
  el("runSuccess").value = payload.run.success_text;
  el("runEvidence").value = payload.run.evidence;
  fire("btnRun", "click");
  await settle();
  await settle();                                   // `pickJob` 里那两次 fetch 也落地
  out.afterRun = { who: el("whoJob").textContent, notices: el("notices").innerHTML,
                   errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                   timeline: el("timeline").innerHTML };
  await ticks(3);                                   // 跟着新那一趟：`/live` 每 3 拍拉一次
  out.afterFollow = { who: el("whoJob").textContent, timeline: el("timeline").innerHTML };
}

//: 「开一趟」**被服务拒了**（Task 12）：服务回 400 + 一句人话 ⇒ 那句话**原样**上屏、
//: **活过之后三次重画**，而且这一屏**不许**跟着换趟（它压根没拿到 job id）。
async function runRefusedScenario(out) {
  out.afterLoad = { who: el("whoJob").textContent };
  el("runUrl").value = "";                          // 人什么都没填就按了
  fire("btnRun", "click");
  await settle();
  out.afterRun = { who: el("whoJob").textContent, errBox: el("errBox").textContent,
                   errHidden: el("errBox").hidden, errClass: el("errBox").className,
                   disabled: el("btnRun").disabled };
  await ticks(9);                                   // 三次重画之后那句话还在不在
  out.afterRepaint = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                       who: el("whoJob").textContent, timeline: el("timeline").innerHTML };
}

//: 窗口那两下（Task 12）：人按「关掉这个窗口」→ 服务回 200 + 一句人话（服务说成了）；
//: 那句话**活过之后三次重画**；再按「重开窗口，接着走」→ 走的是 `/job/<id>/reopen`。
async function windowScenario(out) {
  out.afterLoad = { state: el("winState").innerHTML, hint: el("winHint").innerHTML,
                    closeHidden: el("btnCloseWindow").hidden,
                    reopenHidden: el("btnReopen").hidden };
  fire("btnCloseWindow", "click");
  await settle();
  out.afterClose = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                     errClass: el("errBox").className,
                     disabled: el("btnCloseWindow").disabled };
  await ticks(9);                                   // 三次重画之后那句话还在不在
  out.afterRepaint = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                       state: el("winState").innerHTML,
                       closeHidden: el("btnCloseWindow").hidden,
                       reopenHidden: el("btnReopen").hidden };
  fire("btnReopen", "click");
  await settle();
  out.afterReopen = { errBox: el("errBox").textContent, disabled: el("btnReopen").disabled };
}

//: 失败列表（Task 13 ③）：**看一趟失败 → 把它的证据填进「开一趟」**那条路。
//: 走的是真人那条路：写站名 → 按「查失败」→ 挑一条 → 按「照这条修」。
//: 量四样：① 那一栏长出的是**人话**（不是码）；② 那两跳发到哪（`/failures` 与它下面那条）；
//: ③ **填了三格、没有开跑**（`sent` 里不许有 `POST /run` —— 「什么算成功」只有人知道）；
//: ④ 填好的那几格**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
async function failuresScenario(out) {
  out.afterLoad = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden,
                    hint: el("fixHint").innerHTML };
  el("failSite").value = payload.failures.site;
  fire("btnFail", "click");
  await settle();
  await settle();
  out.afterQuery = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden,
                     disabled: el("btnFail").disabled };
  el("failPick").value = payload.failures.pick;
  fire("btnFixFrom", "click");
  await settle();
  await settle();
  out.afterFix = { mode: el("runMode").value, url: el("runUrl").value,
                   evidence: el("runEvidence").value,
                   evidenceHidden: el("runEvidenceField").hidden,
                   errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                   errClass: el("errBox").className,
                   disabled: el("btnFixFrom").disabled,
                   //: ★ 这一趟**开没开**：`POST /run` 发出去过没有（人还没按「开一趟」）
                   ranAlready: sent.filter((s) => s.url === "/run").length };
  await ticks(9);                                   // 三次重画之后那几格还在不在
  out.afterRepaint = { evidence: el("runEvidence").value, mode: el("runMode").value,
                       url: el("runUrl").value, fails: el("fails").innerHTML,
                       errBox: el("errBox").textContent, errHidden: el("errBox").hidden };
  fire("btnRun", "click");                          // ★ 人自己按「开一趟」
  await settle();
  await settle();
  out.afterRun = { who: el("whoJob").textContent };
}

//: **量不到**那一趟（Task 13）：服务回 502 + 一句人话 ⇒ 那一栏说「这一栏没读到」，
//: 而且**不许**把「没读到」写成「这个站没有失败」—— 那是这一片从头到尾在治的形状。
//: 量两样：① 那句话说出来了；② 它**活过之后三次重画**（这一栏不跟着 `paint()` 重画）。
async function failuresUnmeasuredScenario(out) {
  el("failSite").value = payload.failures.site;
  fire("btnFail", "click");
  await settle();
  await settle();
  out.afterQuery = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden,
                     disabled: el("btnFail").disabled };
  await ticks(9);
  out.afterRepaint = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden };
}

//: 闸口摊开的事实那一趟（Task 14 修复轮 1）：`/live` 停在闸上、`rounds` 里有两张卡 ——
//: 量的是**屏幕上**看得见什么（`#rounds` 那一块），不是载荷里有什么。
//: 三样一起量：
//:   ① 第一道闸那一轮（`done` 是 `null`）说的那句 —— 它原先写的是「第一道闸之前**什么都没跑过**」，
//:      `intake` 不设闸之后**那句话是假的**（复审 2026-09-20 §4.4 的 M4：改掉它，全量 **0 红**）；
//:   ② 脚下这一轮摊开的**原始事实**（`card.facts`，`成功判据` 在里面 —— 复审 §4.2：
//:      它原先只到得了 API 载荷，运营那一屏**一格都不读**，于是从屏幕上消失了）；
//:   ③ 那两样**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
async function gateFactsScenario(out) {
  out.paintMarks = {};
  out.paintMarks.afterLoad = timelineWrites;
  out.afterLoad = { rounds: el("rounds").innerHTML };
  await ticks(9);                                   // 之后**又三次重画**（正文每次都在变）
  out.paintMarks.afterRepaint = timelineWrites;
  out.afterRepaint = { rounds: el("rounds").innerHTML };
}

//: 「重新来一遍」那一趟（Task 10 修复轮 1 / N-3）：运营**按下去**，屏幕上总得发生点什么。
async function againScenario(out) {
  out.afterLoad = { who: el("whoJob").textContent, notices: el("notices").innerHTML,
                    againHidden: el("btnAgain").hidden };
  el("btnAgain").listeners.click();                 // 人按「重新来一遍」（服务回新 job）
  await settle();
  await settle();                                   // `pickJob` 里那两次 fetch 也落地
  out.afterAgain = { who: el("whoJob").textContent, notices: el("notices").innerHTML,
                     errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                     timeline: el("timeline").innerHTML };
}

(async () => {
  vm.runInContext(source, sandbox, { filename: "console.html" });
  await settle();                                   // 开页那两次 fetch

  const out = {};
  if (payload.scenario === "again") { await againScenario(out); }
  else if (payload.scenario === "gate-facts") { await gateFactsScenario(out); }
  else if (payload.scenario === "artifact") { await artifactScenario(out); }
  else if (payload.scenario === "run") { await runScenario(out); }
  else if (payload.scenario === "run-refused") { await runRefusedScenario(out); }
  else if (payload.scenario === "window") { await windowScenario(out); }
  else if (payload.scenario === "failures") { await failuresScenario(out); }
  else if (payload.scenario === "failures-unmeasured") { await failuresUnmeasuredScenario(out); }
  else { await repaintScenario(out); }
  out.paints = timelineWrites;                      // **重画了几次**（C1：别拿 fetch 数代替）
  if (out.paintMarks) { out.paintMarks.end = timelineWrites; }
  out.urls = seen;
  out.sent = sent;                                  // 每一次请求的**正文**（Task 12 起）
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { console.error("夹具自己挂了：" + (e && e.stack || e)); process.exit(1); });
