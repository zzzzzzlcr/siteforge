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
//    / getAttribute / innerText / id / className / shadowRoot）────────────────────
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
    shadowRoot: null,
    children: [],
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    getBoundingClientRect() { return p.rect || { width: 100, height: 40, top: 10, bottom: 50 }; },
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
    document: { getElementsByTagName: () => all, body: { innerText: '' } },
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
