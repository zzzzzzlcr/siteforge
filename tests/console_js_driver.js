// Task 10 Step 2.5 的**执行夹具**（`node` + 假 `document` / 假 `fetch`，不是 headless 浏览器）。
//
// 用法：node console_js_driver.js <console.html> <payloads.json>
// 打印**一行 JSON**（观测结果）到 stdout，退出码 0；自己出错就非 0 退出（不吞）。
//
// 它做的事：把页面里那段**原样的** JS（`agent/console.html` 一个字节都不改）在一个
// 假 DOM / 假 fetch 里跑起来，然后**按一个真人会走的顺序**驱动它：
//   开页 →（`/live` + `/runs` 各来一份）→ 点「停」（服务回 404 人话）→ `/live` 变了（重画）
//   → `/runs` 取不到（左边那一栏说真话）→ `/live` 又变（**又一次重画**）
// 打完这些之后，把屏幕上**那几个元素此刻的文本**交回去。
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

(async () => {
  vm.runInContext(source, sandbox, { filename: "console.html" });
  await settle();                                   // 开页那两次 fetch

  const out = {};
  out.afterLoad = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    secondHint: el("secondHint").innerHTML, runs: el("runs").innerHTML };
  out.stopRequests = 0;

  el("btnStop").listeners.click();                  // 人按「停」（服务回 404）
  await settle();
  out.afterStop = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    stopHint: el("stopHint").innerHTML, statusPill: el("statePill").textContent };
  out.stopRequests = seen.filter((u) => u.endsWith("/stop")).length;

  await ticks(15);                                  // 走满一轮：`/live` 每 3 拍、`/runs` 每 15 拍
  out.afterRunsFailure = { runs: el("runs").innerHTML, errBox: el("errBox").textContent,
                           errHidden: el("errBox").hidden };
  await ticks(9);                                   // 之后**又三次重画**（`/live` 每 3 拍就变）
  out.afterRepaint = { runs: el("runs").innerHTML, errBox: el("errBox").textContent,
                       errHidden: el("errBox").hidden, secondHint: el("secondHint").innerHTML,
                       statusPill: el("statePill").textContent,
                       sayBoxPlaceholder: el("sayBox").placeholder };
  out.urls = seen;
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { console.error("夹具自己挂了：" + (e && e.stack || e)); process.exit(1); });
