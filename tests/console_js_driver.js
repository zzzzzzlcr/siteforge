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
      //: 真浏览器上每个元素都有它（面板会 `box.focus()`）—— 假 DOM 少了这一嘴就是假 DOM 的缺，
      //: 不是生产的缺（2026-09-21：少了它这条用例报的是 `box.focus is not a function`）。
      focus: function () {},
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
  //: ★ 「停用站也照修」那一格**真勾上**（2026-09-21）：夹具那边因此要求载荷里是 `true` ——
  //: 只钉「那一格在载荷里」的话，页面把它**写死 False** 这种改法照绿（而闸就形同虚设）。
  el("runAllowDisabled").checked = true;
  fire("btnRun", "click");
  await settle();
  await settle();                                   // `pickJob` 里那两次 fetch 也落地
  out.afterRun = { who: el("whoJob").textContent, notices: el("notices").innerHTML,
                   errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                   timeline: el("timeline").innerHTML };
  await ticks(3);                                   // 跟着新那一趟：`/live` 每 3 拍拉一次
  out.afterFollow = { who: el("whoJob").textContent, timeline: el("timeline").innerHTML };
}

//: 「上传到后台」那三下（2026-09-22）：预检（绝不写）→ 确认 → 回滚。**按这一趟**。
//: ⚠️ 量的是**发出去的正文**（`sent`）：只钉「按钮在」的话，按钮发错地方/发空照绿。
//: ⚠️ 也量「确认」在预检之前是**按不动**的（票没到手就传 = 没防覆盖那道闸）。
async function pyUploadScenario(out) {
  out.beforeAny = { commitDisabled: el("btnPyCommit").disabled,
                    rollbackDisabled: el("btnPyRollback").disabled,
                    facts: el("pyUploadFacts").innerHTML,
                    note: el("pyUploadNote").innerHTML };
  el("pyUploadOperator").value = payload.operator || "值班员 A";
  fire("btnPyPrepare", "click");
  await settle(); await settle();
  out.afterPrepare = { commitDisabled: el("btnPyCommit").disabled,
                       rollbackDisabled: el("btnPyRollback").disabled,
                       facts: el("pyUploadFacts").innerHTML,
                       note: el("pyUploadNote").innerHTML,
                       errBox: el("errBox").textContent };
  fire("btnPyCommit", "click");
  await settle(); await settle();
  out.afterCommit = { rollbackDisabled: el("btnPyRollback").disabled,
                      note: el("pyUploadNote").innerHTML,
                      errBox: el("errBox").textContent };
  fire("btnPyRollback", "click");
  await settle(); await settle();
  out.afterRollback = { note: el("pyUploadNote").innerHTML,
                        errBox: el("errBox").textContent };
  out.uploads = sent.filter((s) => s.url.indexOf("/upload/") >= 0)
                    .map((s) => ({url: s.url, body: s.body}));
}
//: ⚠️ 量的是**发出去的正文**（`sent` 里最后那一跳 `/run`），不是输入框里那几个字：
//: 「填了但没发出去」这种改法照旧绿是这一片最怕的形状。
async function runStepsScenario(out) {
  el("runMode").value = "fix";
  fire("runMode", "change");
  el("runUrl").value = payload.run.url;
  el("runGoal").value = payload.run.goal;
  el("runSuccess").value = payload.run.success_text;
  el("runEvidence").value = payload.run.evidence;
  el("runNote").value = payload.run.note;
  el("runSteps").value = payload.run.steps_text;
  //: 与 `run` 那条场景同一格（夹具要求载荷里必须是 `true`）。⚠️ 这一格**不是**这一步的主角，
  //: 但它在这一趟里也得是对的 —— 少勾一格就等于量了另一份载荷。
  el("runAllowDisabled").checked = true;
  fire("btnRun", "click");
  await settle();
  await settle();
  const lastRun = [...sent].reverse().find((s) => s.url === "/run");
  out.runBody = lastRun ? lastRun.body : "";
  out.noteBox = el("runNote").value;
  out.stepsBox = el("runSteps").value;
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

//: `/live` 回 404（地址里带的是这个服务已经没有的 job）—— 2026-09-21 用户实测撞到。
//: 钉两件：① 服务那句人话**原样**在；② **下一步**也在（「把 ?job= 去掉 / 从左栏挑一趟」），
//: 而且这句话活过之后三次重画（不许被下一次轮询擦掉）。
async function live404Scenario(out) {
  out.afterLoad = { errBox: el("errBox").textContent, errHidden: el("errBox").hidden,
                    pill: el("statePill").textContent };
  await ticks(3);
  out.afterRepaint = { errBox: el("errBox").textContent, pill: el("statePill").textContent };
}

//: 页面现场那一栏（2026-09-21）：三份正文各画一次 —— **有读数 / 读不到 / 这一趟还没跑过**。
//: 量的就是「三种在屏幕上长不长成一个样」（这一栏存在的全部理由：读不到 ≠ 页面上没有）。
async function pageViewScenario(out) {
  out.withData = { facts: el("pageFacts").innerHTML, missed: el("pageMissed").innerHTML,
                   hint: el("pageHint").innerHTML, tool: el("pageTool").innerHTML };
  await ticks(2);                                   // 第二份正文：两块都读不到
  out.unreadable = { facts: el("pageFacts").innerHTML, missed: el("pageMissed").innerHTML };
  await ticks(2);                                   // 第三份：这一趟还没跑过那一趟
  out.notYet = { facts: el("pageFacts").innerHTML, missed: el("pageMissed").innerHTML,
                 hint: el("pageHint").innerHTML, tool: el("pageTool").innerHTML };
  //: ★「指它」（2026-09-21）：人看得见的那一眼，点一下 ⇒ **预填**进底下那个输入框。
  //: ⚠️ 只预填、**不发**：这一下**没有**替人按任何键（服务可以替人「停」，绝不替人「走」）。
  //: 那一按钮是**动态长出来**的，而假 DOM 不建子节点 ⇒ 拿一个带 `getAttribute` 的假元素
  //: 直接喂给委托在 `#pageMissed` 上的那个监听器（与 `fire` 同一个做法：`this` 绑到宿主上）。
  //: 点的这一条是**它读到了却没点**的那种（cookie 横幅上的 `Accept all`，没有 href）——
  //: 正是卡住那一趟的那一条；没有 href 的那一支也要走通。
  const fakeBtn = { getAttribute: (k) => ({"data-tag": "BUTTON", "data-cls": "",
                                          "data-txt": "Accept all",
                                          "data-href": ""})[k] || "" };
  //: ⚠️ 还要记**它有没有发东西**：这一下只预填 ⇒ 请求数**一个都不许多**。
  //: 比的是**点之前与点之后的差**（这一屏本来就在轮询 `live`，拿「一个请求都没有」去卡是错的）。
  const beforePoint = sent.length;
  //: ⚠️ 监听的宿主**必须是两栏共同的父亲**（`#pagePanel`）：挂在其中一栏上，另一栏那几条
  //:   就点不动（真鼠标实测栽过）。宿主写错 ⇒ 这里 `listeners.click` 是 undefined ⇒ 崩 ⇒ 红。
  el("pagePanel").listeners.click.call(el("pagePanel"), {target: fakeBtn});
  out.afterPoint = { sayBox: el("sayBox").value,
                     beforePoint: beforePoint, afterPoint: sent.length };
  //: ★ 工具算出来的那一条（`cdp observe` 的 `dismiss_selector`）：`data-sel` 那一支，
  //: 拼出来的句子里要**给选择器**（那才是能直接拿去点/拿去写进脚本的东西）。
  const fakeSel = { getAttribute: (k) => ({"data-sel": "button#onetrust-accept-btn-handler",
                                          "data-txt": "Accept all"})[k] || "" };
  el("sayBox").value = "";
  el("pagePanel").listeners.click.call(el("pagePanel"), {target: fakeSel});
  out.afterSel = { sayBox: el("sayBox").value };
}

//: 失败列表（Task 13 ③）：**看一趟失败 → 把它的证据填进「开一趟」**那条路。
//: 走的是真人那条路：写站名 → 按「查失败」→ 挑一条 → 按「照这条修」。
//: 量四样：① 那一栏长出的是**人话**（不是码）；② 那两跳发到哪（`/failures` 与它下面那条）；
//: ③ **填了三格、没有开跑**（`sent` 里不许有 `POST /run` —— 「什么算成功」只有人知道）；
//: ④ 填好的那几格**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
//: 失败列表**量到真没有**（默认只查今天）—— 2026-09-21 用户实测撞到的那一格。
//: 钉：① 那一栏照样说「这一段没有」（量到的），② **怎么让它不空**，
//: ③ 空下拉上按「照这条修」时那句话里有没有「为什么空」。
async function failuresEmptyScenario(out) {
  out.afterLoad = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden };
  el("failSite").value = payload.failures.site;      // 与 `failuresScenario` 同一条路：先写站名再查
  fire("btnFail", "click");
  await settle();
  await settle();
  out.afterQuery = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden };
  //: 把窗**真的**拉开一次（「近 30 天」）—— 钉的是「这一格不是摆设」：
  //: 原先它只有「今天 / 今天和昨天」⇒ 运营最多往回看一天，翻不到要修的那条失败记录。
  el("failSince").value = "d30";
  fire("btnFail", "click");
  await settle();
  await settle();
  out.afterWiden = { fails: el("fails").innerHTML };
  el("failSince").value = "";
  fire("btnFixFrom", "click");
  await settle();
  out.afterFixFrom = { errBox: el("errBox").textContent };
}

async function failuresScenario(out) {
  out.afterLoad = { fails: el("fails").innerHTML, actHidden: el("failAct").hidden,
                    hint: el("fixHint").innerHTML };
  el("failSite").value = payload.failures.site;
  //: 夹具说了挑哪一档时间窗就挑哪一档（不说 = 今天那一档，一个参数都不带）。
  //: `failures-wide` 那一趟靠这一行把窗拉开，量「查列表」与「照这条修」是不是**同一段窗**。
  if (payload.failures.since) { el("failSince").value = payload.failures.since; }
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

//: ★ 第 ① 件事的另一半：**照这条修之后人又改了「站点网址」那一格**。
//: 走的是与 `failuresScenario` 一样的那条路，只在最后多一步「人改网址」。
//: 量的是**发出去的 `/run` 正文**（`sent`）：页面**照旧把两格原样发出去**
//: （人填的网址没被改回去、那个键连着它对账用的那一格一起在）——
//: 「这个键还算不算数」判在**服务**那一层（见 `test_service_fixsource` 同名那两条）。
async function failuresUrlEditedScenario(out) {
  el("failSite").value = payload.failures.site;
  fire("btnFail", "click");
  await settle();
  await settle();
  el("failPick").value = payload.failures.pick;
  fire("btnFixFrom", "click");
  await settle();
  await settle();
  out.afterFix = { url: el("runUrl").value };
  el("runUrl").value = payload.editedUrl;          // ★ 人把那格改了
  fire("btnRun", "click");
  await settle();
  await settle();
  out.afterRun = { who: el("whoJob").textContent };
}

//: 合规闸那一栏（2026-09-21）：写站名 → 按「查配置」→ 摆结果。
//: 量两样：① 服务那句 `say` **原样**上屏；② 那一栏**读不到**时不许被画成「合规」
//:（这一屏最怕的形状：读不回来 = 「这份配置没问题」）。
async function configcheckScenario(out) {
  out.afterLoad = { box: el("check").innerHTML };
  el("checkSite").value = payload.check.site;
  fire("btnCheck", "click");
  await settle();
  await settle();
  out.afterCheck = { box: el("check").innerHTML, disabled: el("btnCheck").disabled };
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

//: ★ 榜单 → 失败单 → 原因（Task 4）：那条链的**三下**，按一个真人会走的顺序走一遍。
//: 量六样，前四样各对应 brief 的一条硬要求：
//:   ① 榜单那一栏长出的是**人话**（`#rank` 里是服务给的那句 `say`）；
//:   ② 挑一个站 ⇒ **它的失败单**（走的是既有那一栏的那条路：`#failSite` + `/failures`）；
//:   ③ ★ **脏键**（榜单上那个带 query / 粘报错的键）⇒「**按这个键查不到**」**说出来**，
//:      而且**不是**留白、也**不是**「这个站没有失败」（brief §2 R1/R2）；
//:   ④ ★ 挑一单 ⇒ **原因行**；`exit=unknown` 那一单上屏的是「**没报上来**」（brief §2 R3）；
//:   ⑤ 没有原因的那一单 ⇒ 服务那句 `note`（「还没有原因」）**上屏**，不许空着；
//:   ⑥ 上面那些**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
async function rankScenario(out) {
  //: 挑一个键、按一下、把那一栏读回来（每一步都走真人那条路：`#rankPick` → 按钮）。
  async function pick(key) {
    el("rankPick").value = key;
    fire("btnRankGo", "click");
    await settle();
    await settle();
    return { rankNote: el("rankNote").innerHTML, fails: el("fails").innerHTML };
  }
  out.afterLoad = { rank: el("rank").innerHTML, actHidden: el("rankAct").hidden,
                    note: el("rankNote").innerHTML };
  // ① 看榜单：那一格**空着**（= 看今天）—— 于是那一跳的 URL 上**没有** `date`。
  fire("btnRank", "click");
  await settle();
  await settle();
  out.afterRank = { rank: el("rank").innerHTML, actHidden: el("rankAct").hidden,
                    disabled: el("btnRank").disabled,
                    //: ★ F9：**读的那一行 = 挑的那一格**（同一个字节）——
                    //: 两个去处都走 `esc()`，所以这一格是判据的一半。
                    pick: el("rankPick").innerHTML };
  // ② 干净键：查得到，两个数**对得上**
  out.afterClean = await pick(payload.rank.clean);
  out.afterClean.siteBox = el("failSite").value;
  out.afterClean.disabled = el("btnRankGo").disabled;
  // ③ ★ 长而脏的键：后端**查得到**、而且比榜单那个数**多**（复审量的真形状：1 → 6）
  out.afterMore = await pick(payload.rank.long);
  // ④ 构造的那个键：比榜单那个数**少**（复审那一天的真数据里没有这个方向）
  out.afterFewer = await pick(payload.rank.fewer);
  // ⑤ ★ **真的查不到**的那条：榜单上那个「没有配置」的键（后端回业务码 404）
  out.afterNoConfig = await pick(payload.rank.noConfig);
  //: ★ 那句「按这个键查不到」**本身就是一条要活过重画的读数**（Task 7 那一族）——
  //: 这一屏栽过的是「写进去了，下一次重画就没了」。就地量一次，别等最后那一次：
  //: 后面那几步会把它换成别的话（那是**对的**：附注说的是**最后那一下**）。
  await ticks(9);
  out.afterNoConfigRepaint = { rankNote: el("rankNote").innerHTML,
                               fails: el("fails").innerHTML };
  // ⑥ 服务**没给那个数**的那一行 ⇒ 对不了账也要说出来（不是静默）
  out.afterNoCount = await pick(payload.rank.noCount);
  // ⑦ 挑**有原因**的那一单 ⇒ 原因行
  await pick(payload.rank.clean);
  el("failPick").value = payload.rank.withDiag;
  fire("btnDiag", "click");
  await settle();
  await settle();
  out.afterDiag = { diag: el("diag").innerHTML, disabled: el("btnDiag").disabled };
  // ⑧ 挑**还没有原因**的那一单 ⇒ 服务那句 `note` 上屏
  el("failPick").value = payload.rank.noDiag;
  fire("btnDiag", "click");
  await settle();
  await settle();
  out.afterNoDiag = { diag: el("diag").innerHTML };
  // ⑨ 活过之后三次重画
  await ticks(9);
  out.afterRepaint = { rank: el("rank").innerHTML, diag: el("diag").innerHTML,
                       rankNote: el("rankNote").innerHTML, fails: el("fails").innerHTML };
}

//: 闸口摊开的事实那一趟（Task 14 修复轮 1）：`/live` 停在闸上、`rounds` 里有两张卡 ——
//: 量的是**屏幕上**看得见什么（`#rounds` 那一块），不是载荷里有什么。
//: 三样一起量：
//:   ① 第一道闸那一轮（`done` 是 `null`）说的那句 —— 它原先写的是「第一道闸之前**什么都没跑过**」，
//:      `intake` 不设闸之后**那句话是假的**（复审 2026-09-20 §4.4 的 M4：改掉它，全量 **0 红**）；
//:   ② 脚下这一轮摊开的**原始事实**（`card.facts`，`成功判据` 在里面 —— 复审 §4.2：
//:      它原先只到得了 API 载荷，运营那一屏**一格都不读**，于是从屏幕上消失了）；
//:   ③ 那两样**活过之后三次重画**（Task 7 那一族：写进去 ≠ 还在）。
//: 外加 `#roundsSub` 那一格（修复轮 2 / F14）：那一句**承诺**
//: （「它要开真窗口 / 动真页面之前，每一步都会先停下来问你一次」）在不在屏上。
async function gateFactsScenario(out) {
  out.paintMarks = {};
  out.paintMarks.afterLoad = timelineWrites;
  out.afterLoad = { rounds: el("rounds").innerHTML, roundsSub: el("roundsSub").innerHTML };
  await ticks(9);                                   // 之后**又三次重画**（正文每次都在变）
  out.paintMarks.afterRepaint = timelineWrites;
  out.afterRepaint = { rounds: el("rounds").innerHTML, roundsSub: el("roundsSub").innerHTML };
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
  else if (payload.scenario === "run-steps") { await runStepsScenario(out); }
  else if (payload.scenario === "py-upload") { await pyUploadScenario(out); }
  else if (payload.scenario === "run-refused") { await runRefusedScenario(out); }
  else if (payload.scenario === "window") { await windowScenario(out); }
  else if (payload.scenario === "page-view") { await pageViewScenario(out); }
  else if (payload.scenario === "live-404") { await live404Scenario(out); }
  else if (payload.scenario === "failures") { await failuresScenario(out); }
  else if (payload.scenario === "failures-empty") { await failuresEmptyScenario(out); }
  else if (payload.scenario === "failures-url-edited") { await failuresUrlEditedScenario(out); }
  //: 服务没给那个站键那一趟 —— **同一段驱动**（差别只在载荷里那条证据少了 `site`）：
  //: 走的路一个字不差，量的就是「页面会不会自己编一个键出来」。
  else if (payload.scenario === "failures-no-key") { await failuresScenario(out); }
  //: 拉宽时间窗那一趟 —— **同一段驱动**（差别只在载荷里那些 URL 带了 `&since=`）。
  else if (payload.scenario === "failures-wide") { await failuresScenario(out); }
  else if (payload.scenario === "failures-unmeasured") { await failuresUnmeasuredScenario(out); }
  //: 这三趟走**同一段驱动**，差别只在载荷里那份答复（干净 / 不合规 / 读不到）。
  else if (payload.scenario === "configcheck") { await configcheckScenario(out); }
  else if (payload.scenario === "configcheck-unclean") { await configcheckScenario(out); }
  else if (payload.scenario === "configcheck-unreadable") { await configcheckScenario(out); }
  else if (payload.scenario === "rank-diag") { await rankScenario(out); }
  else { await repaintScenario(out); }
  out.paints = timelineWrites;                      // **重画了几次**（C1：别拿 fetch 数代替）
  if (out.paintMarks) { out.paintMarks.end = timelineWrites; }
  out.urls = seen;
  out.sent = sent;                                  // 每一次请求的**正文**（Task 12 起）
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((e) => { console.error("夹具自己挂了：" + (e && e.stack || e)); process.exit(1); });
