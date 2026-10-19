// 弹窗探针的执行夹具（`node` + 假 DOM）—— `tests/test_popup_hooks_js.py` 用它。
//
// 为什么要有它：产物里那段探针是**真 JS**，而 `tests/test_template.py` 那些替身
// **根本不执行 JS**（它只按脚本里有没有 `privacy` 这个词回一句预设答复）。
// 于是「第一个答应的是谁」「名字有没有跟着答复回来」「答复的形状对不对」这些
// ——**只有跑起来才看得见**，2026-10-10 就是这么逮到一个真 bug 的：
// 钩子外面多套了一层函数 ⇒ 那句 `return` 只返回给了空壳，探针一路走到兜底的 `return ''`
// （替身测试全绿，真站上一个弹层都点不掉）。
//
// 用法：node popup_probe_driver.js <probe.js>   →  stdout 最后一行是一句 JSON：
//   {"total":N,"failed":M,"results":[{"name","got","want","ok"}, ...]}
// ⚠️ 探针从**文件**读进来（用例从产物里切出来再落盘的那个），这一份里不抄第二遍。

'use strict';
const fs = require('fs');

const probePath = process.argv[2];
if (!probePath) {
  console.error('用法：node popup_probe_driver.js <probe.js>');
  process.exit(2);
}
// 与产物 `_ev` 的包法一致：`(function(){…})()`（`return` 才有地方可返）
const PROBE = '(function(){' + fs.readFileSync(probePath, 'utf8') + '})()';

// ── 假 DOM（只实现探针真会碰到的那几样：getElementsByTagName / getBoundingClientRect
//    / getAttribute / **attributes** / **contains** / innerText / id / className / shadowRoot
//    ＋ `document.elementsFromPoint`（落点那把尺子 —— 「广告 creative」那一行要用））──────
let all = [];
const parent = new Map();

function el(tag, p) {
  p = p || {};
  const e = {
    tagName: String(tag).toUpperCase(),
    id: p.id || '',
    className: p.cls || '',
    innerText: p.text || '',
    value: p.value || '',
    attrs: p.attrs || {},
    //: 真 DOM 的 `attributes` 是一串 {name,value}（钩子那几条路读的就是它）
    attributes: Object.keys(p.attrs || {}).map(k => ({ name: k, value: String(p.attrs[k]) })),
    shadowRoot: null,
    children: [],
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    //: ★ 2026-10-19：`_MARK_CONSENT_BY_TEXT_JS` 会打/清 `data-sf-consent` —— 假 DOM 得真的存住它，
    //: 不然「标记上了没有」只能靠 mock 自说自话。
    setAttribute(k, v) {
      this.attrs[k] = String(v);
      this.attributes = this.attributes.filter(a => a.name !== k)
        .concat([{ name: k, value: String(v) }]);
    },
    removeAttribute(k) {
      delete this.attrs[k];
      this.attributes = this.attributes.filter(a => a.name !== k);
    },
    getBoundingClientRect() { return p.rect || { width: 100, height: 40, top: 10, bottom: 50 }; },
    //: 「落点是它自己/祖先/后代」那条问的就是这个 —— ★ 2026-10-17（终审 Ruling 27）：
    //: 原来这里是**恒假桩**（`other === e`），于是「祖先/后代」那半**从来没被走过**：
    //: 落点那一条只被「那一摞里没有我」钉着。现在按**真祖先/后代语义**走（`under`）。
    contains(other) { return other === e || under(other, e); },
    getElementsByTagName() { return all.filter(x => under(x, e)); },
    querySelectorAll() { return all.filter(x => under(x, e)); },
  };
  all.push(e);
  return e;
}
function under(x, root) {
  let p = parent.get(x);
  while (p) { if (p === root) return true; p = parent.get(p); }
  return false;
}
function attach(box, node) { parent.set(node, box); box.children.push(node); return node; }

//: 每一格：[名字, 造页面（返回 {document, window}）, 期望的答复]
//: 期望值就是**真站上该看到的那三个字面**：`"<钩子名>|<选择器|TEXT>|<按钮文字>"`
const rect_full = { width: 390, height: 844, top: 0, bottom: 844 };

function page(elems) {
  all = []; parent.clear();
  const nodes = elems.map(([t, p]) => el(t, p));
  return {
    nodes,
    document: {
      getElementsByTagName: () => all,
      body: { innerText: '' },
      //: 默认「这一点上没有东西」；要验落点那一格的用例自己摆（`pg.document.elementsFromPoint = () => […]`）
      elementsFromPoint: () => [],
    },
    window: { innerWidth: 390, innerHeight: 844 },
  };
}

const CASES = [
  ['同意条：按钮带 id', () => {
    const pg = page([['div', { id: 'onetrust-banner-sdk', cls: 'ot-sdk-container', text: 'This website uses cookies' }]]);
    attach(pg.nodes[0], el('button', { id: 'onetrust-accept-btn-handler', text: 'Accept Cookies' }));
    return pg;
  }, '同意条|#onetrust-accept-btn-handler|Accept Cookies'],

  ['同意条：按钮只有文字', () => {
    const pg = page([['div', { cls: 'cookie-consent', text: 'We use cookies' }]]);
    attach(pg.nodes[0], el('button', { text: 'Accept All' }));
    return pg;
  }, '同意条|TEXT|Accept All'],

  ['盖住整页的模态：报的是模态自己的名字', () => {
    const pg = page([['div', { cls: 'MuiDialog-root MuiModal-root', attrs: { role: 'dialog' }, rect: rect_full }]]);
    attach(pg.nodes[0], el('button', { attrs: { 'aria-label': 'close' } }));
    return pg;
  }, '盖住整页的模态|TEXT|close'],

  ['干净页面：一条都不答应', () => page([['div', { text: 'Get Started' }]]), ''],

  ['两者同时在：同意条优先（表序 = 优先级）', () => {
    const pg = page([
      ['div', { cls: 'MuiDialog-root', attrs: { role: 'dialog' }, rect: rect_full }],
      ['div', { cls: 'cookie-banner', text: 'We use cookies' }],
    ]);
    attach(pg.nodes[0], el('button', { attrs: { 'aria-label': 'Close' } }));
    attach(pg.nodes[1], el('button', { id: 'accept-all', text: 'Accept' }));
    return pg;
  }, '同意条|#accept-all|Accept'],

  // ★ 2026-10-16（Ruling 22）：`data-click="close"` 那一型 —— **只认可见的那一颗**。
  // 现场就是这样：同页 6 个候选（3 个 campaign × inside/outside），**5 个 0×0 的隐藏件
  // 排在文档顺序前面**，只有 1 个可见 ⇒ 当年「直接 `cdp click '[data-click="close"]'`」
  // 点的是文档顺序第一个 = 点空气（blavity README 的 1 号用例）。
  ['广告 creative：6 个候选里只认**可见**那一颗（隐藏件排在文档顺序前面）', () => {
    const hid = i => ['button', { id: 'bx-close-inside-hidden-' + i,
                                  attrs: { 'data-click': 'close' },
                                  rect: { width: 0, height: 0, top: 0, bottom: 0 } }];
    const pg = page([hid(1), hid(2), hid(3), hid(4), hid(5),
                     ['button', { id: 'bx-close-inside-1', attrs: { 'data-click': 'close' },
                                  rect: { width: 88, height: 54, top: 225, bottom: 279 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[5]];      // 可见那一颗在顶上
    return pg;
  }, '广告 creative|#bx-close-inside-1|'],

  // ★ 2026-10-16（Task 7 = blavity 验证矩阵第 4 格「读那半」）：候选**看得见**、尺寸也过闸
  // （88×54），可疑度全落在 `data-click="close"` 上 —— 可是**落点最顶上那个不是它**
  // （合成页里就是 `#page-cover`：`position:fixed; inset:0`，z-index 压在 creative 上）。
  // 探针必须返空：点下去只是「点了个寂寞」（`cdp click` 照样回 ok），宁可不做。
  // ⚠️ ★ 2026-10-17（终审 Ruling 27）：这一摞**换形状了** —— 候选**在**那一摞里
  //    （真浏览器就是这么给的：`elementsFromPoint` 给整摞、**最顶上那个在前**），
  //    只是**盖着它的那一层排在它前面**。原来是「那一摞里压根没有我」，那只能钉住旧判据
  //    （旧判据是 `复数 elementsFromPoint + any(自己/祖先/后代)`：被盖着的候选**仍在摞里**
  //    ⇒ 它照样会认）；换成这个形状之后，**只有新判据**（整摞 + 跳过 `pointer-events:none`
  //    + 看第一个**别的**元素 —— 与 `_COVER_JS` 同一把尺子）才返空。
  // ⚠️ 而这一摞是**用例自己摆的**（假 DOM 里没有真浏览器的完整命中链）：这一格说的是
  //    「最顶上那个不是它 ⇒ 不认」这条字面判据，**不等于**「真站上被盖着一定返空」——
  //    真站上「被盖着」归 `_covered_by` 与复核兜。
  ['广告 creative：可见但**被盖着**（最顶上那个不是它）不许认', () => {
    const pg = page([
      ['button', { id: 'bx-close-inside-pop', attrs: { 'data-click': 'close' },
                   rect: { width: 88, height: 54, top: 4, bottom: 58 } }],
      ['div', { id: 'page-cover', cls: 'page-cover', rect: rect_full }],   // 它身上一个 close 词都没有
    ]);
    //: 真浏览器的次序：**最顶上那个在前**（盖着它的那一层压在 creative 上）
    pg.document.elementsFromPoint = () => [pg.nodes[1], pg.nodes[0]];
    return pg;
  }, ''],

  // 负控：**容器**（300×300，id/class 里带 close）不许被当成关闭键 —— ②③ 那两条路
  // 收窄不了的形状，只能靠尺寸闸（≤120×120，照 2026-10-09 那一刀的界）。
  ['广告 creative：超界的容器（300×300，class/aria 带 close）不许点', () => {
    const pg = page([['div', { id: 'close-banner-wrapper', cls: 'close-banner-wrapper',
                               attrs: { 'aria-label': 'close' },
                               rect: { width: 300, height: 300, top: 0, bottom: 300 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[0]];
    return pg;
  }, ''],

  // ★ 2026-10-16（Ruling 23）：`aria-label` 是**散文**，要**锚定**（`^(close|dismiss|关闭)`）——
  // 子串那条只许用在 `id`/`class`（它们是**标识符**，`#cwCloseButton` 这种驼峰只能靠子串）。
  // 小件、可见、落点是它自己 ⇒ 尺寸闸拦不住它，只有「锚定」这一条能拦。
  ['广告 creative：aria-label 是散文 —— 「Do not close this window」不许认', () => {
    const pg = page([['button', { id: 'keep-open',
                                  attrs: { 'aria-label': 'Do not close this window' },
                                  rect: { width: 88, height: 24, top: 100, bottom: 124 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[0]];
    return pg;
  }, ''],

  // ★ Ruling 26 那一格的**订正**（★ 2026-10-17 终审 M1）：上面那格是 **24 字**（不是 26 ——
  // 当初把引号也数进去了），而长度闸是 `≤24` ⇒ **24 ≤ 24 过闸** ⇒ 「光靠长度闸就能返空」
  // 这条推导**不成立**。真正成立的是**支配关系**：那格逮得住「子串 + 长度闸 ≥24」的实现，
  // 这一格（**12 字**）逮得住「子串 + 长度闸 ≥12」的；两道合起来覆盖 ≥12 的全部
  // ⇒ 这条**覆盖面严格更宽**，任何「子串」实现都逃不掉（只有**锚定**能同时过这两格）。
  // 子串的话它会答 `#keep-open2|Do not close`。
  ['广告 creative：aria-label 是散文（12 字 ≤24）—— 「Do not close」不许认', () => {
    const pg = page([['button', { id: 'keep-open2',
                                  attrs: { 'aria-label': 'Do not close' },
                                  rect: { width: 88, height: 24, top: 160, bottom: 184 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[0]];
    return pg;
  }, ''],

  // ★ 2026-10-17（终审 Ruling 28-①）：`_VIS_JS` 原来**只判矩形** ⇒ `visibility:hidden`
  // 的关闭键被当成可见（真站实测：wildnutrition 那一屏有两颗这样的键进了候选）。
  // 这一格：矩形完全正常、`data-click="close"` 也在，**只有 computedStyle 说它藏着**
  // ⇒ 必须返空。⚠️ 假 DOM 里 `getComputedStyle` **默认不存在**（node 里就是 undefined）——
  // 探针那两处都得 try/catch 得住；这一格**临时挂上**它（见下面 loop 里的安装/卸载），
  // 别的格照旧走「没有这个 API」那条路。
  ['广告 creative：`visibility:hidden` 的关闭键不许认', () => {
    const pg = page([['button', { id: 'bx-close-inside-hidden',
                                  attrs: { 'data-click': 'close' },
                                  rect: { width: 88, height: 54, top: 300, bottom: 354 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[0]];
    pg.getComputedStyle = () => ({ visibility: 'hidden', display: 'block' });
    return pg;
  }, ''],

  // ─────────── ★ 2026-10-19：同意控件的 tag 白名单（div/span）───────────
  // 真事：compareinsulation 的 CookieScript 把 accept/reject 做成 `<div>`，三条钩子
  // 只认 button/a/input ⇒ 同意层看得见、关不掉（实测 §2.3）。这一格就是那个形状。
  ['同意条：按钮是 div（CookieScript 形状，带 id）', () => {
    const pg = page([['div', { id: 'cookiescript_injected', cls: 'cookiescript',
                               text: 'This website uses cookies' }]]);
    attach(pg.nodes[0], el('div', { id: 'cookiescript_accept', text: 'ACCEPT ALL' }));
    return pg;
  }, '同意条|#cookiescript_accept|ACCEPT ALL'],

  // ★ 2026-10-19 真站逼出来的形状：accept/reject 外面还套着一层 `#cookiescript_buttons`，
  // 它的**合并文字**是 `"ACCEPT ALL\nDECLINE ALL"`（22 字）—— ≤40 的短文本闸拦不住它，
  // 钩子会先挑中 wrapper，点下去是点空气。必须再加一道「div/span 只认**叶子**（子元素里
  // 没有文字）」：wrapper 跳过，落到真正的 `#cookiescript_accept`。
  ['同意条：wrapper 的合并文字也很短 —— 不许点 wrapper', () => {
    const pg = page([['div', { id: 'cookiescript_injected', cls: 'cookiescript',
                               text: 'This website uses cookies' }]]);
    const wrap = attach(pg.nodes[0], el('div', {
      id: 'cookiescript_buttons', text: 'ACCEPT ALL\nDECLINE ALL' }));
    attach(wrap, el('div', { id: 'cookiescript_accept', text: 'ACCEPT ALL' }));
    attach(wrap, el('div', { id: 'cookiescript_reject', text: 'DECLINE ALL' }));
    return pg;
  }, '同意条|#cookiescript_accept|ACCEPT ALL'],

  // 没有 id 的 div/span 必须走「按文字打标记」那条路（`_MARK_CONSENT_BY_TEXT_JS`）——
  // 所以那条路的 tag 闸也得一起放，否则钩子答得出来、动作落不下去。
  ['同意条：按钮是 span（没有 id，按文字打标记）', () => {
    const pg = page([['div', { cls: 'cookie-consent', text: 'We use cookies' }]]);
    attach(pg.nodes[0], el('span', { text: 'Accept all' }));
    return pg;
  }, '同意条|TEXT|Accept all'],

  // 模态那条也有同一道 tag 闸：关闭键做成 `<span>Close</span>` 时，原来在 tag 那一步就没了。
  ['盖住整页的模态：关闭键是 span', () => {
    const pg = page([['div', { cls: 'MuiDialog-root', attrs: { role: 'dialog' },
                               rect: rect_full }]]);
    attach(pg.nodes[0], el('span', { text: 'Close' }));
    return pg;
  }, '盖住整页的模态|TEXT|Close'],

  // 与同意条同一道「只认叶子」：wrapper 自己带 id、文字也是 "Close"（很短）——
  // 不跳过它就会答 `#close-wrapper|Close`，点下去是点空气；叶子 span 才是那颗键。
  ['盖住整页的模态：关闭键包在 wrapper 里 —— 不许点 wrapper', () => {
    const pg = page([['div', { cls: 'MuiDialog-root', attrs: { role: 'dialog' },
                               rect: rect_full }]]);
    const wrap = attach(pg.nodes[0], el('div', { id: 'close-wrapper', text: 'Close' }));
    attach(wrap, el('span', { text: 'Close' }));
    return pg;
  }, '盖住整页的模态|TEXT|Close'],

  // ★ 负控（新白名单的代价）：同意容器里的**整段说明文字**也会命中 accept/reject 那些词。
  // 放进 div/span 之后，必须靠「短文本」那道闸把它挡在外面 —— 否则会去点一段散文。
  ['同意条：容器里的长散文不许认（div/span 新白名单）', () => {
    const pg = page([['div', { id: 'cookiescript_injected', cls: 'cookiescript',
                               text: 'This website uses cookies' }]]);
    attach(pg.nodes[0], el('div', { text: 'By clicking ACCEPT ALL you agree to the storing '
                                        + 'of cookies on this site.' }));
    return pg;
  }, ''],
];

const results = [];
let failed = 0;
for (const [name, build, want] of CASES) {
  const pg = build();
  //: ★ 2026-10-17（终审 Ruling 28-①）：`getComputedStyle` 是**浏览器的全局函数**（产物那两段
  //: JS 直接裸调它），而 node 里**没有**这个全局 —— 所以默认那几格走的正是「探针得 try/catch
  //: 得住」那条路。要演 `visibility:hidden` 的格自己挂一个（跑完**卸载**，别漏给下一格）。
  const had = Object.prototype.hasOwnProperty.call(globalThis, 'getComputedStyle');
  const saved = globalThis.getComputedStyle;
  if (pg.getComputedStyle) globalThis.getComputedStyle = pg.getComputedStyle;
  let got, err = '';
  try {
    got = new Function('document', 'window', 'return ' + PROBE)(pg.document, pg.window);
  } catch (e) {
    got = '<抛了>'; err = String(e && e.message || e);
  } finally {
    if (had) globalThis.getComputedStyle = saved; else delete globalThis.getComputedStyle;
  }
  const ok = got === want;
  if (!ok) failed++;
  results.push({ name, got: got, want: want, ok: ok, err: err });
}
// ★ 2026-10-19：`_MARK_CONSENT_BY_TEXT_JS` 也是同一条路（钩子答 `TEXT` ⇒ 按文字打标记）。
// 第二个参数给了就顺手跑一遍：页面里放一颗没有 id 的 `<div>Accept all</div>`，
// 看它能不能被标记上。只测**动作那半**（读半在探针那几格）。
let marker = null;
const markerPath = process.argv[3];
if (markerPath) {
  const markerJs = '(function(){' + fs.readFileSync(markerPath, 'utf8') + '})()';
  const pg = page([['div', { cls: 'cookie-consent', text: 'We use cookies' }]]);
  const hit = attach(pg.nodes[0], el('div', { text: 'Accept all' }));
  let got, err = '';
  try {
    got = new Function('document', 'window', 'return ' + markerJs)(pg.document, pg.window);
  } catch (e) {
    got = '<抛了>'; err = String(e && e.message || e);
  }
  marker = { got: got, attr: hit.getAttribute('data-sf-consent'), err: err };
}

process.stdout.write(JSON.stringify({ total: results.length, failed: failed, results: results,
                                      marker: marker }) + '\n');
