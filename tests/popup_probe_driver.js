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
    getBoundingClientRect() { return p.rect || { width: 100, height: 40, top: 10, bottom: 50 }; },
    //: 「落点是它自己/祖先/后代」那条问的就是这个 —— 假 DOM 里只认它自己（够这一格用）
    contains(other) { return other === e; },
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
  // （88×54），可疑度全落在 `data-click="close"` 上 —— 可是**落点是盖着它的那一层**
  // （合成页里就是 `#page-cover`：`position:fixed; inset:0`，z-index 压在 creative 上）。
  // 探针必须返空：点下去只是「点了个寂寞」（`cdp click` 照样回 ok），宁可不做。
  // ⚠️ 这一格钉的是 `_AD_CREATIVE_JS` 里 `elementsFromPoint` 那一摞判据（自家/祖先/后代）——
  //    2026-10-16 变异体实测：把那一摞整段删掉，**其余九格全绿**，只有这一格会红。
  // ⚠️ 而这一摞是**用例自己摆的**（假 DOM 里没有真浏览器的完整命中链）：这一格说的是
  //    「这一摞里**没有**我 ⇒ 不认」这条字面判据，**不等于**「真站上被盖着一定返空」——
  //    真站上「被盖着」归 `_covered_by`（它判的是 `stack[0]`）与 Task 5 的复核兜。
  ['广告 creative：可见但**被盖着**（落点那一摞里没有它）不许认', () => {
    const pg = page([
      ['button', { id: 'bx-close-inside-pop', attrs: { 'data-click': 'close' },
                   rect: { width: 88, height: 54, top: 4, bottom: 58 } }],
      ['div', { id: 'page-cover', cls: 'page-cover', rect: rect_full }],   // 它身上一个 close 词都没有
    ]);
    pg.document.elementsFromPoint = () => [pg.nodes[1]];   // 落点是**盖着那一下**的那一层
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

  // ★ Ruling 26：上面那格是 **26 字** ⇒ 光靠「≤24 字」那道长度闸就能让它返空 ⇒
  // **它分辨不出「锚定」和「长度闸」**（把 aria-label 改回**子串**匹配，那格照样绿）。
  // 这一格 **12 字**（≤24）：只有**锚定**能拦住它 —— 子串的话它会答 `#keep-open2|Do not close`。
  ['广告 creative：aria-label 是散文（12 字 ≤24）—— 「Do not close」不许认', () => {
    const pg = page([['button', { id: 'keep-open2',
                                  attrs: { 'aria-label': 'Do not close' },
                                  rect: { width: 88, height: 24, top: 160, bottom: 184 } }]]);
    pg.document.elementsFromPoint = () => [pg.nodes[0]];
    return pg;
  }, ''],
];

const results = [];
let failed = 0;
for (const [name, build, want] of CASES) {
  const pg = build();
  let got, err = '';
  try {
    got = new Function('document', 'window', 'return ' + PROBE)(pg.document, pg.window);
  } catch (e) {
    got = '<抛了>'; err = String(e && e.message || e);
  }
  const ok = got === want;
  if (!ok) failed++;
  results.push({ name, got: got, want: want, ok: ok, err: err });
}
process.stdout.write(JSON.stringify({ total: results.length, failed: failed, results: results }) + '\n');
