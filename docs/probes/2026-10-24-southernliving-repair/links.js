JSON.stringify((() => {
  const out = {sel: null, links: []};
  const el = document.querySelector('#utility-nav_1-0 > ul:nth-of-type(1) > li:nth-of-type(5) > a:nth-of-type(1)');
  if (el) {
    const r = el.getBoundingClientRect();
    out.sel = {text: (el.innerText || "").trim(), href: el.href, target: el.target,
               rect: [r.x, r.y, r.width, r.height], vis: !!(r.width && r.height)};
  }
  const links = Array.from(document.querySelectorAll('a')).filter(a => /subscribe/i.test(a.innerText || ""));
  out.links = links.slice(0, 12).map(a => {
    const r = a.getBoundingClientRect();
    return {text: (a.innerText || "").trim().slice(0, 40), href: a.href, target: a.target,
            id: a.id, cls: String(a.className).slice(0, 60),
            rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
            top: a.closest('header, #utility-nav_1-0, [class*=utility], nav') ?
                 (a.closest('header, [class*=utility], nav') || {}).tagName : null};
  });
  return out;
})())
