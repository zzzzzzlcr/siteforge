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
function fakeFetch(url) {
  const q = queues[url];
  seen.push(url);
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

async function repaintScenario(out) {
  out.paintMarks = {};
  out.paintMarks.afterLoad = timelineWrites;        // 开页那一次
  out.afterLoad = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    secondHint: el("secondHint").innerHTML, runs: el("runs").innerHTML };

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
                       sayBoxPlaceholder: el("sayBox").placeholder };
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
  else if (payload.scenario === "artifact") { await artifactScenario(out); }
  else { await repaintScenario(out); }
  out.paints = timelineWrites;                      // **重画了几次**（C1：别拿 fetch 数代替）
  if (out.paintMarks) { out.paintMarks.end = timelineWrites; }
  out.urls = seen;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { console.error("夹具自己挂了：" + (e && e.stack || e)); process.exit(1); });
