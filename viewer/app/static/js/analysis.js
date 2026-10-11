// Dependency-free charts for the analysis page. All colours come from CSS variables,
// so light/dark themes apply automatically.
(() => {
  const D = JSON.parse(document.getElementById('analysis-data').textContent);
  const pageUrl = id => D.urls.page.replace('__ID__', id);
  const fmt = n => Math.round(n).toLocaleString('en-US');
  const $ = s => document.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const NS = 'http://www.w3.org/2000/svg';
  const sv = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent?.append(e); return e;
  };
  const median = xs => { const a = [...xs].sort((x, y) => x - y); return a.length ? (a[a.length >> 1] + a[(a.length - 1) >> 1]) / 2 : 0; };
  const quantile = (xs, q) => { const a = [...xs].sort((x, y) => x - y); return a.length ? a[Math.min(a.length - 1, Math.floor(q * (a.length - 1)))] : 0; };

  // ---- tokens by chapter (stacked horizontal bars) ----
  const tokensEl = $('#chart-chapters');
  function drawTokens(mode) {
    tokensEl.replaceChildren();
    const get = c => mode === 'mean' ? [c.mean_summary, c.mean_reasoning] : [c.summary_tokens, c.reasoning_tokens];
    const max = Math.max(...D.chapters.map(c => get(c)[0] + get(c)[1]), 1);
    D.chapters.forEach(c => {
      const [s, t] = get(c);
      const row = el('a', 'hrow');
      row.href = pageUrl(c.first);
      row.title = `${c.label}: ${fmt(c.summary_tokens)} summary + ${fmt(c.reasoning_tokens)} thinking over ${c.pages} pages`;
      row.append(el('span', 'hl', c.label));
      const track = el('div', 'htrack');
      track.append(Object.assign(el('i', 's1'), { style: `width:${100 * s / max}%` }),
                   Object.assign(el('i', 's2'), { style: `width:${100 * t / max}%` }));
      row.append(track, el('span', 'hv dim', `${fmt(s)} + ${fmt(t)}`));
      tokensEl.append(row);
    });
  }
  document.querySelectorAll('.seg').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.seg').forEach(x => x.classList.toggle('on', x === b));
    drawTokens(b.dataset.mode);
  }));
  drawTokens('mean');

  // ---- page heatmap ----
  const METRICS = {
    think_ratio: 'thinking ÷ summary', reasoning_tokens: 'thinking tokens', summary_tokens: 'summary tokens',
    input_est: 'input tokens (est.)', compression: 'summary ÷ input', budget_pct: '% of output budget',
    math_share: 'math share of text', display_math: 'display equations', defs: 'definitions',
    results: 'theorem-like results', proofs: 'proofs', uncertainty: 'doubt in reasoning trace', outlier: 'outlier score',
  };
  const sel = $('#heat-metric');
  Object.entries(METRICS).forEach(([k, v]) => sel.append(new Option(v, k)));
  const heat = $('#heatmap'), info = $('#heat-info');
  function drawHeat(key) {
    heat.replaceChildren();
    const vals = D.pages.map(p => p[key]).filter(v => v != null);
    const sorted = [...vals].sort((a, b) => a - b);
    const rank = v => {                     // percentile rank, ties share the midpoint
      let lo = 0, hi = sorted.length;
      while (lo < hi) { const m = (lo + hi) >> 1; sorted[m] < v ? lo = m + 1 : hi = m; }
      let hi2 = lo; while (hi2 < sorted.length && sorted[hi2] === v) hi2++;
      return sorted.length ? (lo + hi2) / 2 / sorted.length : 0;
    };
    D.chapters.forEach(c => {
      const row = el('div', 'hmrow'); row.append(el('span', 'hl', c.label));
      const cells = el('div', 'cells');
      D.pages.filter(p => p.chapter === c.label).forEach(p => {
        const a = el('a', 'cell' + (p.truncated ? ' trunc' : '') + (p.outlier ? ' out' : '') + (p.nomath ? ' faded' : ''));
        a.href = pageUrl(p.page_id);
        a.style.setProperty('--v', (p[key] ? rank(p[key]) : 0).toFixed(3));
        a.addEventListener('mouseenter', () => {
          info.textContent = `${p.page_id}  ·  ${p.label || '—'}  ·  ${METRICS[key]}: ${typeof p[key] === 'number' ? +p[key].toFixed(3) : p[key]}`;
        });
        cells.append(a);
      });
      row.append(cells); heat.append(row);
    });
  }
  sel.addEventListener('change', () => drawHeat(sel.value));
  sel.value = 'think_ratio'; drawHeat('think_ratio');

  // ---- scatter: input vs thinking (log-log) ----
  {
    const W = 760, H = 380, m = { l: 54, r: 14, t: 12, b: 38 };
    const pts = D.pages.filter(p => p.input_est > 0 && p.reasoning_tokens > 0);
    const lx = v => Math.log10(v + 1);
    const xs = pts.map(p => lx(p.input_est)), ys = pts.map(p => lx(p.reasoning_tokens));
    const x0 = Math.floor(Math.min(...xs)), x1 = Math.ceil(Math.max(...xs));
    const y0 = Math.floor(Math.min(...ys)), y1 = Math.ceil(Math.max(...ys));
    const X = v => m.l + (lx(v) - x0) / (x1 - x0) * (W - m.l - m.r);
    const Y = v => H - m.b - (lx(v) - y0) / (y1 - y0) * (H - m.t - m.b);
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart' }, $('#scatter-chart'));
    for (let e = x0; e <= x1; e++) {
      sv('line', { x1: X(10 ** e), x2: X(10 ** e), y1: m.t, y2: H - m.b, class: 'grid' }, svg);
      sv('text', { x: X(10 ** e), y: H - m.b + 16, class: 'tick', 'text-anchor': 'middle' }, svg).textContent = fmt(10 ** e);
    }
    for (let e = y0; e <= y1; e++) {
      sv('line', { y1: Y(10 ** e), y2: Y(10 ** e), x1: m.l, x2: W - m.r, class: 'grid' }, svg);
      sv('text', { x: m.l - 8, y: Y(10 ** e) + 4, class: 'tick', 'text-anchor': 'end' }, svg).textContent = fmt(10 ** e);
    }
    sv('text', { x: (W + m.l) / 2, y: H - 4, class: 'tick', 'text-anchor': 'middle' }, svg).textContent = 'input tokens (estimated)';
    sv('text', { x: 12, y: H / 2, class: 'tick', transform: `rotate(-90 12 ${H / 2})`, 'text-anchor': 'middle' }, svg).textContent = 'thinking tokens';
    // median ratio guide line
    const ratio = median(pts.filter(p => !p.nomath).map(p => p.reasoning_tokens / Math.max(1, p.input_est)));
    sv('line', { x1: X(10 ** x0), y1: Y(10 ** x0 * ratio), x2: X(10 ** x1), y2: Y(10 ** x1 * ratio), class: 'guide' }, svg);
    pts.forEach(p => {
      const c = sv('circle', { cx: X(p.input_est), cy: Y(p.reasoning_tokens), r: p.outlier ? 4.2 : 3,
        class: 'dot' + (p.truncated ? ' trunc' : p.outlier ? ' out' : p.nomath ? ' faded' : '') }, svg);
      sv('title', {}, c).textContent = `${p.page_id} ${p.label}\ninput≈${fmt(p.input_est)}  thinking ${fmt(p.reasoning_tokens)}  summary ${fmt(p.summary_tokens)}`;
      c.addEventListener('click', () => { location.href = pageUrl(p.page_id); });
    });
  }

  // ---- histograms ----
  [['think_ratio', 'thinking ÷ summary', 1], ['compression', 'summary ÷ input', 3], ['summary_tokens', 'summary tokens', 0]].forEach(([key, title]) => {
    const vals = D.pages.filter(p => !p.nomath && p[key] > 0).map(p => p[key]);
    if (vals.length < 5) return;
    const lo = quantile(vals, .01), hi = quantile(vals, .99), bins = 28;
    const counts = Array(bins).fill(0);
    vals.forEach(v => { const i = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / (hi - lo || 1) * bins))); counts[i]++; });
    const W = 300, H = 120, top = Math.max(...counts);
    const wrap = el('figure', 'hist'); wrap.append(el('figcaption', null, title));
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H + 18}`, class: 'chart' }, wrap);
    counts.forEach((c, i) => {
      const h = c / top * (H - 6), bw = W / bins;
      const r = sv('rect', { x: i * bw + 1, y: H - h, width: bw - 2, height: h, class: 'bar', rx: 2 }, svg);
      sv('title', {}, r).textContent = `${(lo + (hi - lo) * i / bins).toFixed(2)} – ${(lo + (hi - lo) * (i + 1) / bins).toFixed(2)}: ${c} pages`;
    });
    const med = median(vals), mx = Math.max(0, Math.min(W, (med - lo) / (hi - lo || 1) * W));
    sv('line', { x1: mx, x2: mx, y1: 0, y2: H, class: 'median' }, svg);
    sv('text', { x: 2, y: H + 13, class: 'tick' }, svg).textContent = (+lo.toFixed(2)).toLocaleString();
    sv('text', { x: W - 2, y: H + 13, class: 'tick', 'text-anchor': 'end' }, svg).textContent = (+hi.toFixed(2)).toLocaleString();
    sv('text', { x: Math.min(W - 4, mx + 4), y: 11, class: 'tick' }, svg).textContent = `median ${+med.toFixed(2)}`;
    $('#hists').append(wrap);
  });

  // ---- structure fingerprint ----
  {
    const parts = ['defs', 'results', 'proofs', 'examples', 'remarks', 'tables'];
    const root = $('#chart-structure');
    D.chapters.forEach(c => {
      const total = parts.reduce((s, k) => s + c[k], 0) || 1;
      const row = el('a', 'hrow'); row.href = pageUrl(c.first);
      row.title = parts.map(k => `${k}: ${c[k]}`).join(', ') + `, display eq: ${c.display_math}`;
      row.append(el('span', 'hl', c.label));
      const track = el('div', 'htrack');
      parts.forEach(k => track.append(Object.assign(el('i', 'c-' + k), { style: `width:${100 * c[k] / total}%` })));
      row.append(track, el('span', 'hv dim', `${total} elems · ${(c.display_math / Math.max(1, c.pages)).toFixed(1)} eq/page · ${Math.round(100 * c.math_share)}% math`));
      root.append(row);
    });
  }

  // ---- sortable, filterable table ----
  {
    const cols = [['page_id', 'page'], ['chapter', 'chapter'], ['label', 'label'], ['input_est', 'input'], ['summary_tokens', 'summary'],
      ['reasoning_tokens', 'thinking'], ['think_ratio', 'think÷sum'], ['compression', 'sum÷in'], ['budget_pct', 'budget%'],
      ['math_share', 'math'], ['display_math', 'disp eq'], ['defs', 'def'], ['results', 'res'], ['proofs', 'proof'],
      ['tables', 'tbl'], ['uncertainty', 'doubt'], ['gap_count', 'gaps'], ['cut_count', 'cut'], ['outlier', 'outlier']];
    const tbl = $('#tbl'), filter = $('#tbl-filter'), content = $('#tbl-content');
    let sortKey = null, dir = 1;
    const head = el('thead'); const hr = el('tr');
    cols.forEach(([k, t]) => {
      const th = el('th', null, t); th.dataset.k = k;
      th.addEventListener('click', () => { dir = sortKey === k ? -dir : (typeof D.pages[0][k] === 'string' ? 1 : -1); sortKey = k; draw(); });
      hr.append(th);
    });
    head.append(hr); tbl.append(head);
    const body = el('tbody'); tbl.append(body);
    function draw() {
      const q = filter.value.trim().toLowerCase();
      let rows = D.pages.filter(p => (!content.checked || !p.nomath) &&
        (!q || `${p.page_id} ${p.chapter} ${p.label}`.toLowerCase().includes(q)));
      if (sortKey) rows = [...rows].sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * dir);
      hr.querySelectorAll('th').forEach(th => th.className = th.dataset.k === sortKey ? (dir > 0 ? 'asc' : 'desc') : '');
      body.replaceChildren(...rows.map(p => {
        const tr = el('tr', (p.truncated ? 'trunc ' : '') + (p.outlier ? 'outl' : ''));
        cols.forEach(([k]) => {
          const td = el('td'); let v = p[k];
          if (k === 'page_id') td.innerHTML = `<a href="${pageUrl(v)}">${v}</a>`;
          else if (k === 'math_share') td.textContent = Math.round(100 * v) + '%';
          else if (typeof v === 'number') { td.textContent = Number.isInteger(v) ? fmt(v) : +v.toFixed(2); td.className = 'num'; }
          else td.textContent = v;
          if (k === 'label') td.className = 'dim lbl';
          tr.append(td);
        });
        return tr;
      }));
    }
    filter.addEventListener('input', draw); content.addEventListener('change', draw);
    draw();
  }
})();
