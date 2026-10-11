const $ = (s, r = document) => r.querySelector(s);
const typing = e => /^(INPUT|TEXTAREA)$/.test(e.target.tagName) || e.target.isContentEditable;

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  sget(k, d) { try { return JSON.parse(sessionStorage.getItem(k)) ?? d; } catch { return d; } },
  sset(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const main = $('main');
const curDomain = document.body.dataset.domain, curPage = document.body.dataset.page;
const links = [...document.querySelectorAll('#sidebar a.pg')];
const linkFor = (d, p) => links.find(a => a.dataset.domain === d && a.pathname.endsWith('/' + p));

// ---- sidebar: filter (id, label, chapter) + open the active page's groups ----
const filter = $('#filter');
const chapters = [...document.querySelectorAll('#sidebar details.chap')];
filter?.addEventListener('input', () => {
  const terms = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  links.forEach(a => { a.hidden = terms.length > 0 && !terms.every(t => a.dataset.name.includes(t)); });
  chapters.forEach(c => {
    const any = [...c.querySelectorAll('a.pg')].some(a => !a.hidden);
    c.hidden = !any;
    if (terms.length && any) c.open = true;
  });
  if (terms.length) document.querySelectorAll('#sidebar details.dom').forEach(d => { d.open = true; });
});
{
  const on = $('#sidebar a.on');
  for (let d = on?.closest('details'); d; d = d.parentElement.closest('details')) d.open = true;
  on?.scrollIntoView({ block: 'center' });
}

// ---- mobile: slide-in sidebar ----
const setNav = open => document.body.classList.toggle('nav-open', open);
$('#menu')?.addEventListener('click', () => setNav(!document.body.classList.contains('nav-open')));
$('#scrim')?.addEventListener('click', () => setNav(false));
document.querySelectorAll('#sidebar a').forEach(a => a.addEventListener('click', () => setNav(false)));

// ---- recent pages + "continue reading" ----
if (curDomain && curPage) {
  const last = store.get('kb:last', {}); last[curDomain] = curPage; store.set('kb:last', last);
  let recent = store.get('kb:recent', []).filter(r => !(r.d === curDomain && r.p === curPage));
  recent.unshift({ d: curDomain, p: curPage, t: Date.now() });
  store.set('kb:recent', recent.slice(0, 10));
  const seen = store.get('kb:seen', {});
  const set = new Set(seen[curDomain] || []); set.add(curPage);
  seen[curDomain] = [...set]; store.set('kb:seen', seen);
}
const label = (d, p) => { const a = linkFor(d, p); return a ? a.dataset.label : ''; };
function recentList(el, max) {
  const recent = store.get('kb:recent', []).filter(r => linkFor(r.d, r.p)).slice(0, max);
  if (!recent.length || !el) return;
  el.hidden = false;
  el.innerHTML = '<h4>recent</h4>';
  recent.forEach(r => {
    const a = document.createElement('a');
    a.href = linkFor(r.d, r.p).href; a.className = 'rc';
    a.textContent = `${r.d}/${r.p}`;
    const lb = label(r.d, r.p);
    if (lb) { const s = document.createElement('span'); s.className = 'dim'; s.textContent = ' ' + lb; a.append(s); }
    el.append(a);
  });
}
recentList($('#recent'), 5);
recentList($('#recent-home'), 8);
{
  const last = store.get('kb:last', {});
  let newest = null;
  document.querySelectorAll('a.continue').forEach(a => {
    const p = last[a.dataset.domain], l = p && linkFor(a.dataset.domain, p);
    if (l) { a.href = l.href; a.textContent = 'continue at ' + p; a.hidden = false; }
  });
  const recent = store.get('kb:recent', []).find(r => linkFor(r.d, r.p));
  const any = $('#continue-any');
  if (any && recent) {
    any.href = linkFor(recent.d, recent.p).href; any.hidden = false;
    any.textContent = `continue: ${recent.d}/${recent.p}` + (label(recent.d, recent.p) ? ' — ' + label(recent.d, recent.p) : '');
  }
}

// ---- go-to palette (g / Ctrl+K): page number, id, topic or chapter ----
const pal = $('#palette'), palQ = $('#pal-q'), palList = $('#pal-list');
let palItems = [], palSel = 0;
function renderPalette() {
  const terms = palQ.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const num = terms.length === 1 && /^\d+$/.test(terms[0]) ? parseInt(terms[0], 10) : null;
  const scored = [];
  for (const a of links) {
    const id = a.pathname.split('/').pop();
    let score = 0;
    if (!terms.length) score = a.dataset.domain === curDomain ? 1 : 0.5;
    else if (num !== null && parseInt(id.replace(/\D/g, ''), 10) === num) score = 100 + (a.dataset.domain === curDomain);
    else if (terms.every(t => a.dataset.name.includes(t))) score = 10 + (a.dataset.domain === curDomain) - id.length * 0.001;
    if (score) scored.push([score, a]);
  }
  scored.sort((x, y) => y[0] - x[0]);
  palItems = scored.slice(0, 14).map(x => x[1]);
  palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  palList.replaceChildren(...palItems.map((a, i) => {
    const li = document.createElement('li');
    li.className = i === palSel ? 'sel' : '';
    li.innerHTML = '<span class="pid"></span> <span class="dim"></span>';
    li.firstChild.textContent = `${a.dataset.domain}/${a.pathname.split('/').pop()}`;
    li.lastChild.textContent = `${a.dataset.chapter} · ${a.dataset.label}`;
    li.addEventListener('mousedown', e => { e.preventDefault(); location.href = a.href; });
    return li;
  }));
}
const openPalette = () => { setNav(false); pal.hidden = false; palQ.value = ''; palSel = 0; renderPalette(); palQ.focus(); };
const closeModals = () => { pal.hidden = true; $('#help').hidden = true; };
palQ?.addEventListener('input', () => { palSel = 0; renderPalette(); });
palQ?.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { palSel = Math.max(palSel - 1, 0); renderPalette(); e.preventDefault(); }
  else if (e.key === 'Enter' && palItems[palSel]) location.href = palItems[palSel].href;
  else if (e.key === 'Escape') closeModals();
});
['open-palette', 'open-palette-2'].forEach(id => document.getElementById(id)?.addEventListener('click', openPalette));
$('#open-help')?.addEventListener('click', () => { $('#help').hidden = false; });
document.querySelectorAll('.modal').forEach(m => m.addEventListener('mousedown', e => { if (e.target === m) closeModals(); }));

// ---- position scrubber: drag to jump anywhere in the domain ----
const scrub = $('#scrub');
if (scrub) {
  const dl = links.filter(a => a.dataset.domain === scrub.dataset.domain);
  const out = $('#scrub-out');
  const show = () => {
    const a = dl[scrub.value - 1];
    out.textContent = `${scrub.value} / ${dl.length}` + (a ? ` · ${a.pathname.split('/').pop()} ${a.dataset.label}` : '');
  };
  scrub.addEventListener('input', show);
  scrub.addEventListener('change', () => { const a = dl[scrub.value - 1]; if (a) location.href = a.href; });
}

// ---- "back to where you were" after following a cross-reference ----
const here = location.pathname + location.search;
let stack = store.sget('kb:stack', []);
{
  const i = stack.map(e => e.url).lastIndexOf(here);
  if (i >= 0) stack = stack.slice(0, i);            // came back (by any route): unwind
  store.sset('kb:stack', stack);
  const restore = store.sget('kb:restore', null);
  if (restore && restore.url === here) {
    store.sset('kb:restore', null);
    const go = () => { if (main) main.scrollTop = restore.y; };
    go(); window.mathReady?.then(go);
    setTimeout(go, 1500);
  }
  const top = stack[stack.length - 1];
  const bar = $('#backbar');
  if (top && bar) {
    bar.hidden = false;
    bar.innerHTML = '<a href="#" id="back-link"></a>';
    $('#back-link').textContent = '\u2190 back to ' + top.text;
    $('#back-link').addEventListener('click', e => { e.preventDefault(); goBack(); });
  }
}
function goBack() {
  const top = stack[stack.length - 1];
  if (!top) return;
  store.sset('kb:stack', stack.slice(0, -1));
  store.sset('kb:restore', { url: top.url, y: top.y });
  location.href = top.url;
}
document.addEventListener('click', e => {
  const a = e.target.closest('main a[href]');
  if (!a || !curPage || a.closest('.pager, .skips, .crumbs, #backbar, .scrubber')) return;
  if (a.origin !== location.origin || a.getAttribute('href').startsWith('#')) return;
  if (a.pathname + a.search === here) return;
  stack.push({ url: here, y: main ? main.scrollTop : 0, text: `${curDomain}/${curPage}` });
  store.sset('kb:stack', stack.slice(-20));
}, true);

// ---- table of contents with heading anchors ----
const body = $('#body');
const heads = body ? [...body.querySelectorAll('h1, h2, h3, h4')] : [];
if (heads.length >= 3) {
  const used = new Set();
  const ol = document.createElement('ul');
  heads.forEach(h => {
    let id = (h.textContent || 'section').toLowerCase().trim()
      .replace(/[^\w]+/g, '-').replace(/^-|-$/g, '') || 'section';
    while (used.has(id)) id += '-x';
    used.add(id); h.id = id;
    const a = document.createElement('a');
    a.className = 'anchor'; a.href = '#' + id; a.textContent = '#';
    h.append(a);
    const li = document.createElement('li');
    li.className = 'l' + h.tagName[1];
    const l = document.createElement('a');
    l.href = '#' + id; l.textContent = h.firstChild.textContent;
    li.append(l); ol.append(li);
  });
  const toc = $('#toc');
  toc.innerHTML = '<summary>contents</summary>';
  const det = document.createElement('details');
  det.open = heads.length <= 12;
  det.innerHTML = '<summary>contents (' + heads.length + ')</summary>';
  det.append(ol); toc.replaceChildren(det); toc.hidden = false;
  // If math typesetting shifts layout, re-honour a deep link.
  window.mathReady?.then(() => location.hash && document.getElementById(location.hash.slice(1))?.scrollIntoView());
}

// ---- highlight search terms (from ?q=) in the page and in result snippets ----
function highlight(root, terms) {
  terms = terms.filter(Boolean).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!terms.length) return;
  const re = new RegExp('(' + terms.join('|') + ')', 'gi');
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest('mjx-container, code, pre, script, style, mark')
      ? NodeFilter.FILTER_REJECT : re.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
  });
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(n => {
    re.lastIndex = 0;
    const span = document.createElement('span');
    span.innerHTML = n.nodeValue.replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(re, '<mark>$1</mark>');
    n.replaceWith(...span.childNodes);
  });
}
const qTerms = (new URLSearchParams(location.search).get('q') || '').toLowerCase().split(/\s+/);
document.querySelectorAll('.snip').forEach(p => highlight(p, p.dataset.terms.split(' ')));
if (body && qTerms[0]) {
  // Highlight after markdown is in the DOM; MathJax output is skipped by the walker.
  highlight(body, qTerms);
  $('mark')?.scrollIntoView({ block: 'center' });
}

// ---- copy markdown ----
$('#copy-md')?.addEventListener('click', async e => {
  const src = window.__summaryMarkdown || '';
  try { await navigator.clipboard.writeText(src); e.target.textContent = 'copied'; }
  catch { e.target.textContent = 'copy failed'; }
  setTimeout(() => (e.target.textContent = 'copy markdown'), 1500);
});

// ---- keyboard ----
const go = sel => { const a = $(sel); if (a) a.click(); };
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'Escape') { closeModals(); setNav(false); }
  if (typing(e)) { if (e.key === 'Escape') e.target.blur(); return; }
  switch (e.key) {
    case '/': e.preventDefault(); $('#q')?.focus(); break;
    case 'f': e.preventDefault(); setNav(true); filter?.focus(); break;
    case 'g': e.preventDefault(); openPalette(); break;
    case '?': $('#help').hidden = false; break;
    case 'j': case 'ArrowRight': go('#next'); break;
    case 'k': case 'ArrowLeft': go('#prev'); break;
    case 'b': goBack(); break;
    default: if ('[]nNtT'.includes(e.key)) go(`.skips a[data-key="${e.key}"]`);
  }
});

// ---- theme toggle + reading progress ----
{
  const root = document.documentElement;
  const dark = () => root.dataset.theme !== 'light';
  $('#theme')?.addEventListener('click', () => {
    const next = dark() ? 'light' : 'dark';
    root.dataset.theme = next; store.set('kb:theme', next);
  });
  const bar = $('#progress');
  if (bar && main) {
    const upd = () => {
      const h = main.scrollHeight - main.clientHeight;
      bar.style.width = h > 0 ? (100 * main.scrollTop / h).toFixed(1) + '%' : '0';
    };
    main.addEventListener('scroll', upd, { passive: true }); upd();
    window.mathReady?.then(upd);
  }
}
