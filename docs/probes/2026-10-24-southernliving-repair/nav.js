JSON.stringify((() => {
  const nav = document.querySelector('#utility-nav_1-0');
  if (!nav) return {has: false};
  const lis = Array.from(nav.querySelectorAll(':scope > ul > li'));
  const items = lis.map((li, i) => {
    const a = li.querySelector('a');
    const r = li.getBoundingClientRect();
    return {
      i: i + 1,
      cls: String(li.className).slice(0, 70),
      text: (li.innerText || '').trim().slice(0, 40),
      href: a ? a.href : null,
      acls: a ? String(a.className).slice(0, 50) : null,
      aid: a ? a.id : null,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      display: getComputedStyle(li).display,
    };
  });
  return {has: true, w: innerWidth, h: innerHeight, items};
})())
