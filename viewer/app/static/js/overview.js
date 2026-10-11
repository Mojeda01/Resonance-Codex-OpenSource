// Overview: per-domain mini heatmaps, read-progress bars and "random unread".
(() => {
  const DATA = JSON.parse(document.getElementById('ov-data').textContent);
  const seenAll = (() => { try { return JSON.parse(localStorage.getItem('kb:seen')) || {}; } catch { return {}; } })();
  const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

  const METRICS = {
    tier: 'dataset tier', read: 'read / unread', think: 'thinking ÷ summary', math: 'math share', tok: 'summary tokens',
  };
  Object.entries(DATA).forEach(([d, D]) => {
    const seen = new Set(seenAll[d] || []);
    const box = document.querySelector(`[data-heat="${d}"]`);
    const info = document.querySelector(`[data-info="${d}"]`);
    const sel = document.querySelector(`.heat-metric[data-domain="${d}"]`);
    const url = id => D.urls.replace('__ID__', id);
    Object.entries(METRICS).forEach(([k, v]) => sel.append(new Option(v, k)));

    function draw(key) {
      box.replaceChildren();
      const vals = D.pages.map(p => p[key]).filter(v => typeof v === 'number').sort((a, b) => a - b);
      const rank = v => {
        let lo = 0, hi = vals.length;
        while (lo < hi) { const m = (lo + hi) >> 1; vals[m] < v ? lo = m + 1 : hi = m; }
        return vals.length ? lo / vals.length : 0;
      };
      D.chapters.forEach(c => {
        const row = el('div', 'hmrow'); const l = el('span', 'hl'); l.textContent = c.label; row.append(l);
        const cells = el('div', 'cells');
        D.pages.filter(p => p.ch === c.label).forEach(p => {
          const a = el('a', 'cell');
          a.href = url(p.id);
          if (key === 'tier') a.classList.add('t-' + p.tier);
          else if (key === 'read') a.classList.add(seen.has(p.id) ? 't-clean' : 't-unread');
          else a.style.setProperty('--v', rank(p[key]).toFixed(3));
          if (p.tier === 'excluded' && key !== 'tier') a.classList.add('faded');
          a.addEventListener('mouseenter', () => {
            const val = key === 'tier' ? p.tier + (p.why.length ? ` (${p.why.join(', ')})` :'')
              : key === 'read' ? (seen.has(p.id) ? 'read' : 'unread') : +p[key].toFixed(3);
            info.textContent = `${p.id} · ${p.label || '—'} · ${METRICS[key]}: ${val}`;
          });
          cells.append(a);
        });
        row.append(cells); box.append(row);
      });
    }
    sel.addEventListener('change', () => draw(sel.value));
    sel.value = 'tier'; draw('tier');

    // progress
    const total = D.pages.length, n = D.pages.filter(p => seen.has(p.id)).length;
    const prog = document.querySelector(`.prog[data-domain="${d}"]`);
    if (prog) {
      prog.firstElementChild.style.width = (100 * n / total) + '%';
      prog.nextElementSibling.textContent = `${n} / ${total}`;
    }
    // random unread
    const rnd = document.querySelector(`.rnd.unread[data-domain="${d}"]`);
    rnd?.addEventListener('click', e => {
      e.preventDefault();
      const pool = D.pages.filter(p => !seen.has(p.id) && p.tier !== 'excluded');
      const pick = (pool.length ? pool : D.pages)[Math.floor(Math.random() * (pool.length || D.pages.length))];
      location.href = url(pick.id);
    });
  });
})();
