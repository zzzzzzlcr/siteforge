JSON.stringify((() => {
  const f = document.querySelector('#hustle-field-first_name-module-6');
  const r = f ? f.getBoundingClientRect() : null;
  const modal = document.querySelector('.hustle-ui.module-6, #hustle-module-6, [id*=hustle][id*=6]');
  const buttons = Array.from(document.querySelectorAll('.hustle-button, button, [role=button]'))
    .filter(b => { const q = b.getBoundingClientRect(); return q.width > 0 && q.height > 0; })
    .map(b => ({t: (b.innerText || '').trim().slice(0, 30), id: b.id, cls: String(b.className).slice(0, 60)}))
    .slice(0, 20);
  const g = Hustle.getModule(6);
  return {
    field_rect: r ? [r.x | 0, r.y | 0, r.width | 0, r.height | 0] : null,
    modalId: modal ? modal.id : null,
    isShown: g ? g.isShown : null,
    cookies: document.cookie,
    buttons,
  };
})())
