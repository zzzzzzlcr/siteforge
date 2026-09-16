(function () {
  // ═══ R3 能力探针：observe 契约能不能在这些页面上取齐 ═══
  // 自己实现穿透（cdp eval 不注入 __cdpQ）—— 生产版会走 Go 内核的 internal 包
  var RS = [];
  (function walk(r) {
    RS.push(r);
    var all; try { all = r.querySelectorAll('*'); } catch (e) { return; }
    for (var i = 0; i < all.length; i++) {
      var sr = all[i].shadowRoot; if (sr) walk(sr);
    }
  })(document);

  function qsa(sel) {
    var out = [];
    for (var i = 0; i < RS.length; i++) {
      var f; try { f = RS[i].querySelectorAll(sel); } catch (e) { continue; }
      for (var j = 0; j < f.length; j++) out.push(f[j]);
    }
    return out;
  }
  function txt(el, n) { return (el && el.textContent ? el.textContent : '').replace(/\s+/g, ' ').trim().slice(0, n || 60); }
  function vis(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    var s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  }
  function shadowDepth(el) {
    var d = 0, n = el;
    while (n) {
      var root = n.getRootNode ? n.getRootNode() : null;
      if (root && root.host) { d++; n = root.host; } else break;
    }
    return d;
  }
  // 合成树祖先链：parentElement 出不了 shadow 边界，必须经 getRootNode().host 跳
  function composedAncestors(el) {
    var out = [], n = el;
    while (n) {
      out.push(n);
      var r = n.getRootNode ? n.getRootNode() : null;
      n = (r && r.host) ? r.host : (n.parentElement || null);
    }
    return out;
  }
  // ── 命中测试：探针发现 elementsFromPoint **不穿透 shadow**（返回的是 host），
  //    所以「元素不在命中栈里」不能直接判遮挡。要看命中栈顶是不是它的合成树祖先。
  function occludedBy(el) {
    var r = el.getBoundingClientRect();
    var x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return 'offscreen';
    var stack = document.elementsFromPoint(x, y) || [];
    var anc = composedAncestors(el);
    for (var i = 0; i < stack.length; i++) {
      if (anc.indexOf(stack[i]) !== -1) return null;   // 命中的是它自己或它的祖先 → 没被挡
    }
    var t = stack[0];
    return t ? (t.tagName.toLowerCase() + (t.id ? '#' + t.id : '')) : 'unknown';
  }
  var RAND = /(^|[-_])[0-9a-f]{8,}($|[-_])|^[a-z]*\d{6,}$/i;
  function pathSel(el) {
    var parts = [], n = el, hops = 0;
    while (n && n.tagName && hops < 4 && n.parentElement) {
      var tag = n.tagName.toLowerCase();
      if (n.id && !RAND.test(n.id)) { parts.unshift('#' + n.id); break; }
      var idx = 1, sib = n;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === n.tagName) idx++;
      parts.unshift(tag + ':nth-of-type(' + idx + ')');
      n = n.parentElement; hops++;
    }
    return parts.join(' > ');
  }
  function candidates(el) {
    var out = [];
    if (el.id && !RAND.test(el.id)) out.push('#' + el.id);
    if (el.name) out.push(el.tagName.toLowerCase() + '[name="' + el.name + '"]');
    ['data-testid', 'data-test', 'data-id', 'data-value'].forEach(function (a) {
      var v = el.getAttribute && el.getAttribute(a);
      if (v && !RAND.test(v)) out.push(el.tagName.toLowerCase() + '[' + a + '="' + v + '"]');
    });
    var cls = (el.className && typeof el.className === 'string' ? el.className : '')
      .split(/\s+/).filter(function (c) { return c && !RAND.test(c); }).slice(0, 2);
    if (cls.length) out.push(el.tagName.toLowerCase() + '.' + cls.join('.'));
    out.push(pathSel(el));
    return out.filter(function (s, i, a) { return s && a.indexOf(s) === i; });
  }
  function stability(el, cands) {
    var c = cands[0] || '';
    if (/^#/.test(c) || /\[(name|data-)/.test(c)) return 'high';
    if (/:nth-of-type/.test(c)) {
      var depth = (c.match(/>/g) || []).length;
      return depth <= 2 ? 'medium' : 'low';
    }
    if (/^[a-z]+\.[a-z]/.test(c)) return 'medium';
    return 'low';
  }
  function region(el) {
    // 探针发现：走 parentElement 在 shadow 里会断（到 shadow root 顶就 null），
    // 于是所有 shadow 元素都退化成 'body'。必须走合成树。
    var chain = composedAncestors(el);
    for (var i = 0; i < chain.length; i++) {
      var n = chain[i];
      if (!n.tagName) continue;
      var t = n.tagName.toLowerCase();
      if (t === 'header' || t === 'nav' || t === 'footer' || t === 'aside' || t === 'main') return t;
      var cn = (typeof n.className === 'string' ? n.className : '').toLowerCase();
      if (/hero|banner|jumbotron/.test(cn)) return 'hero';
      if (n.getAttribute && n.getAttribute('role') === 'dialog') return 'dialog';
    }
    return 'body';
  }
  function lum(c) {
    var m = /rgba?\(([^)]+)\)/.exec(c || ''); if (!m) return null;
    var p = m[1].split(',').map(function (x) { return parseFloat(x); });
    if (p.length > 3 && p[3] === 0) return null;
    var f = p.slice(0, 3).map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  }
  function contrastBand(el) {
    var s = getComputedStyle(el), fg = lum(s.color), n = el;
    var bg = null;
    while (n && !bg) { bg = lum(getComputedStyle(n).backgroundColor); n = n.parentElement; }
    if (fg === null || bg === null) return null;
    var hi = Math.max(fg, bg), lo = Math.min(fg, bg);
    var ratio = (hi + 0.05) / (lo + 0.05);
    return ratio >= 4.5 ? 'high' : ratio >= 3 ? 'medium' : 'low';
  }
  function nearbyText(el) {
    var out = [], n = el.previousElementSibling, k = 0;
    while (n && k < 2) { var t = txt(n, 40); if (t) { out.push(t); k++; } n = n.previousElementSibling; }
    n = el.nextElementSibling; k = 0;
    while (n && k < 1) { var t2 = txt(n, 40); if (t2) { out.push(t2); k++; } n = n.nextElementSibling; }
    return out;
  }

  // ── 可动作元素 ──
  var SEL = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=option],[role=tab],[role=checkbox],[role=radio],[onclick]';
  var cands = qsa(SEL).filter(vis);
  var areas = cands.map(function (e) { var r = e.getBoundingClientRect(); return r.width * r.height; })
    .sort(function (a, b) { return a - b; });
  var med = areas.length ? areas[Math.floor(areas.length / 2)] : 1;

  var actions = cands.slice(0, 200).map(function (el) {
    var r = el.getBoundingClientRect(), c = candidates(el);
    var tag = el.tagName.toLowerCase();
    var peers = cands.filter(function (o) { return o.tagName === el.tagName && region(o) === region(el); }).length;
    return {
      selector: c[0], alternates: c.slice(1), stability: stability(el, c),
      text: txt(el, 50), role: el.getAttribute('role') || tag, tag: el.tagName,
      type: el.type || null, visible: true, occluded_by: occludedBy(el),
      shadow_depth: shadowDepth(el), frame_path: ['main'],
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      region: region(el), above_fold: r.top < innerHeight,
      relative_size: med ? Math.round((r.width * r.height / med) * 10) / 10 : null,
      peer_count: peers, z_index: getComputedStyle(el).zIndex,
      contrast: contrastBand(el), nearby_text: nearbyText(el)
    };
  });

  // ── 表单字段 ──
  var fields = qsa('input,select,textarea').filter(vis).slice(0, 100).map(function (el) {
    var lab = el.getAttribute('aria-label') || '';
    if (!lab && el.id) { var l = qsa('label[for="' + el.id + '"]')[0]; if (l) lab = txt(l, 40); }
    if (!lab) { var pl = el.closest ? el.closest('label') : null; if (pl) lab = txt(pl, 40); }
    return {
      selector: candidates(el)[0], alternates: candidates(el).slice(1),
      stability: stability(el, candidates(el)),
      label: lab || '', hint: el.name || el.id || '', placeholder: el.placeholder || '',
      type: el.type || el.tagName.toLowerCase(), required: !!el.required,
      shadow_depth: shadowDepth(el), frame_path: ['main']
    };
  });

  // ── 选项组 ──
  var groups = [];
  qsa('[role=radiogroup],fieldset,.opts,[class*=option],[class*=choice]').forEach(function (g) {
    var opts = Array.prototype.slice.call(g.querySelectorAll('button,[role=radio],[role=option],label,input[type=radio],input[type=checkbox]'))
      .filter(vis).map(function (o) { return txt(o, 40); }).filter(Boolean);
    if (opts.length >= 2) {
      groups.push({ scope: candidates(g)[0], role: 'option', options: opts.slice(0, 12), shadow_depth: shadowDepth(g) });
    }
  });

  // ── 遮挡物 ──
  var obs = [];
  qsa('div,section,aside').filter(function (el) {
    if (!vis(el)) return false;
    var s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') return false;
    var z = parseInt(s.zIndex, 10); if (!(z > 100)) return false;
    var r = el.getBoundingClientRect();
    return r.width * r.height > innerWidth * innerHeight * 0.08;
  }).slice(0, 6).forEach(function (el) {
    var btn = Array.prototype.slice.call(el.querySelectorAll('button,a')).filter(vis)[0];
    obs.push({ kind: /cookie|consent|gdpr|privacy/i.test(txt(el, 120) + el.id + el.className) ? 'cookie-banner' : 'overlay',
      selector: candidates(el)[0], dismiss_selector: btn ? candidates(btn)[0] : null, text: txt(el, 60) });
  });

  // ── 正文：探针发现两个坑 ──
  //   ① innerText 不穿 shadow（document.body.innerText 在 shadow 页上几乎为空）
  //   ② **ShadowRoot 根本没有 innerText**（那是 HTMLElement 的属性）——
  //      第一版写 r.innerText 拿到 undefined，页面正文只剩 10 个字符
  //   正确做法：逐 root 收集，shadow root 取它子元素的 innerText
  var perRoot = RS.map(function (r) {
    if (r.body) { try { return r.body.innerText || ''; } catch (e) { return ''; } }
    var out = [];
    for (var i = 0; i < r.children.length; i++) {
      var c = r.children[i];
      try { out.push(c.innerText || c.textContent || ''); } catch (e) { }
    }
    return out.join(' ');
  });
  var pageText = perRoot.join(' ').replace(/\s+/g, ' ').trim();

  return JSON.stringify({
    ok: true,
    url: location.href, title: document.title,
    page_text: pageText.slice(0, 600),
    page_text_len_raw: perRoot.reduce(function (a, s) { return a + s.length; }, 0),
    shadow_roots: RS.length - 1,
    counts: { actions: actions.length, fields: fields.length, option_groups: groups.length, obstructions: obs.length },
    obstructions: obs, actions: actions, fields: fields, option_groups: groups
  });
})()
