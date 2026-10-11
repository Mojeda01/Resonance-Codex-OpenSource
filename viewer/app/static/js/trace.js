// Reasoning-trace viewer: rendered text with highlighted hedges, a heat rail beside it,
// and a minimal loop check. Built lazily the first time the section is opened.
(() => {
  const details = document.getElementById('trace');
  const root = document.getElementById('trace-root');
  const D = JSON.parse(document.getElementById('trace-data').textContent);
  const main = document.querySelector('main');
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const fmt = n => Math.round(n).toLocaleString('en-US');
  let built = false;

  const LABELS = { hedge: 'hedges', source: 'source doubt', revise: 'changes of mind' };
  const ANCH = Object.fromEntries(Object.entries(D.lexicon).map(([k, v]) => [k, new RegExp(`^(?:${v})$`, 'i')]));
  const ALL = new RegExp(Object.values(D.lexicon).map(v => `(?:${v})`).join('|'), 'gi');

  // Wrap lexicon matches in <mark>. Runs before MathJax, so a match inside raw \( \) math is skipped.
  function highlight(node) {
    const inMath = s => {
      const o = (s.match(/\\\(|\\\[|\$\$/g) || []).length, c = (s.match(/\\\)|\\\]/g) || []).length;
      return o > c || ((s.match(/\$/g) || []).length % 2 === 1);
    };
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.parentElement.closest('code, pre, mark') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(n => {
      const t = n.nodeValue; const frag = document.createDocumentFragment(); let last = 0, m, hit = false;
      ALL.lastIndex = 0;
      while ((m = ALL.exec(t))) {
        if (inMath(t.slice(0, m.index))) continue;
        const kind = Object.keys(ANCH).find(k => ANCH[k].test(m[0])) || 'hedge';
        frag.append(t.slice(last, m.index));
        const mk = el('mark', 'hl-' + kind, m[0]); frag.append(mk);
        last = m.index + m[0].length; hit = true;
      }
      if (hit) { frag.append(t.slice(last)); n.replaceWith(frag); }
    });
  }

  function build() {
    built = true;
    const S = D.stats;

    // ---- header: numbers and the loop verdict ----
    const head = el('div', 'tv-head');
    const chips = [
      [`${fmt(S.tokens)} tokens · ${S.paragraphs} paragraphs · ${fmt(S.words)} words`],
      [`hedging ${S.per100}/100 words`, S.per100 > S.domain_median * 1.5 ? 'warn' : '', `domain median ${S.domain_median}`],
      [`${S.hedge} hedges · ${S.source} source doubts · ${S.revise} changes of mind`],
      [`loop check: ${S.loop} (${S.repeats} of ${S.paragraphs} paragraphs repeat earlier ones)`, S.loop === 'high' ? 'warn' : '',
       'a paragraph counts as a repeat when ≥30% of its 4-word phrases already appeared earlier'],
    ];
    chips.forEach(([t, cls, title]) => { const c = el('span', 'chip ' + (cls || ''), t); if (title) c.title = title; head.append(c); });
    root.append(head);

    // ---- recurring topics (clicking flashes every paragraph that mentions it) ----
    if (D.topics.length) {
      const row = el('div', 'tv-topics'); row.append(el('span', 'dim', 'keeps coming back to:'));
      D.topics.forEach(tp => {
        const b = el('button', 'chip topic', `${tp.token} ×${tp.n}`);
        b.type = 'button'; b.title = `paragraphs ${tp.paras.join(', ')}`;
        b.addEventListener('click', () => {
          const els = tp.paras.map(i => document.getElementById('tp-' + i));
          els[0]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          els.forEach(e => { e.classList.remove('flash'); void e.offsetWidth; e.classList.add('flash'); });
        });
        row.append(b);
      });
      root.append(row);
    }

    // ---- toggles ----
    const bar = el('div', 'tv-bar');
    const view = { hedge: true, source: true, revise: true, dim: false, flagged: false };
    const frame = el('div', 'tv');
    const applyView = () => {
      Object.keys(LABELS).forEach(k => frame.classList.toggle('no-' + k, !view[k]));
      frame.classList.toggle('dim-repeats', view.dim);
      frame.classList.toggle('flagged-only', view.flagged);
    };
    [['hedge', 'highlight hedges'], ['source', 'highlight source doubt'], ['revise', 'highlight changes of mind'],
     ['dim', 'dim repeated paragraphs'], ['flagged', 'only hot paragraphs']].forEach(([k, label]) => {
      const l = el('label'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!view[k];
      cb.addEventListener('change', () => { view[k] = cb.checked; applyView(); });
      l.append(cb, ' ' + label); bar.append(l);
    });
    root.append(bar);

    // ---- text column + rail ----
    const text = el('div', 'tv-text'), rail = el('div', 'tv-rail');
    const segs = [];
    D.paragraphs.forEach(p => {
      const box = el('div', 'tp'); box.id = 'tp-' + p.i;
      if (p.heat >= 0.45) box.classList.add('hot');
      if (p.repeat_of) box.classList.add('repeat');
      const meta = el('div', 'tp-meta');
      meta.append(el('span', 'tp-n', '¶' + p.i), el('span', 'dim', `~${fmt(p.tok)} tok`));
      if (p.repeat_of) {
        const a = el('a', 'rep-link', `↻ repeats ¶${p.repeat_of} (${Math.round(100 * p.repeat_score)}%)`);
        a.href = '#tp-' + p.repeat_of;
        a.addEventListener('click', e => { e.preventDefault(); document.getElementById('tp-' + p.repeat_of).scrollIntoView({ behavior: 'smooth', block: 'center' }); });
        meta.append(a);
      }
      const body = el('div', 'tp-body');
      box.append(meta, body); text.append(box);
      window.renderMarkdownInto(body, p.text, { trace: true });
      highlight(body);

      const seg = el('button', 'seg'); seg.type = 'button';
      seg.style.flexGrow = Math.max(p.tok, 20);
      seg.style.setProperty('--h', p.heat);
      if (p.repeat_of) seg.classList.add('rep');
      if (p.source) seg.classList.add('src');
      if (p.revise) seg.classList.add('rev');
      const first = p.text.replace(/\s+/g, ' ').slice(0, 90);
      seg.title = `¶${p.i} · ~${fmt(p.tok)} tokens · ${p.hedge} hedges, ${p.source} source doubts, ${p.revise} revisions` +
        (p.repeat_of ? ` · repeats ¶${p.repeat_of}` : '') + `\n${first}…`;
      seg.addEventListener('click', () => box.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      rail.append(seg); segs.push([box, seg]);
    });
    frame.append(text, rail); root.append(frame);
    root.append(Object.assign(el('p', 'dim tv-key'), { innerHTML:
      'rail: segment height = estimated tokens, colour = hedging heat (hedges + source doubt per 100 words), ' +
      '<i class="kk rep"></i> hatched = repeats an earlier paragraph, <i class="kk src"></i> violet edge = source doubt, ' +
      '<i class="kk rev"></i> blue tick = change of mind. Token figures are estimated from paragraph length.' }));
    applyView();
    window.typesetMath([text]);

    // ---- which paragraphs are on screen ----
    if ('IntersectionObserver' in window) {
      const map = new Map(segs.map(([b, s]) => [b, s]));
      const io = new IntersectionObserver(es => es.forEach(e => map.get(e.target)?.classList.toggle('vis', e.isIntersecting)),
        { root: main, threshold: 0 });
      segs.forEach(([b]) => io.observe(b));
    }
  }

  const open = () => { if (!built) build(); };
  details.addEventListener('toggle', () => { if (details.open) open(); });
  window.addEventListener('load', () => {            // render.js loads after this script
    if (location.hash.startsWith('#tp-')) { details.open = true; open(); document.querySelector(location.hash)?.scrollIntoView(); }
  });
  window.__openTrace = () => { details.open = true; open(); };
})();
