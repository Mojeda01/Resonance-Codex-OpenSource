// Summarise page: prompt editor, live-streamed single-page test, book run with estimate, flagged re-runs.
(() => {
  const D = JSON.parse(document.getElementById('sum-data').textContent);
  const $ = s => document.querySelector(s);
  const HDR = { 'Content-Type': 'application/json', 'X-RKP': '1' };
  const fmt = n => Math.round(n).toLocaleString('en-US');
  const pad = n => 'p' + String(n).padStart(5, '0');
  const SLUG = D.slug;
  const api = (path, data) => fetch(path, { method: 'POST', headers: HDR, body: JSON.stringify(data || {}) });
  const render = (el, md) => window.renderMarkdownInto && window.renderMarkdownInto(el, md, { safeHtml: true });

  // =============================== prompt ===============================
  const pt = $('#prompt-text');
  let saved = pt.value, savedHash = D.hash;
  function promptInfo() {
    const dirty = pt.value.trim() !== saved.trim();
    $('#prompt-hash').textContent = (savedHash ? `saved version ${savedHash}` : 'not saved yet')
      + (dirty ? ' · edited (unsaved)' : '') + ` · ~${fmt(pt.value.length / 3.6)} tokens`;
  }
  function fillVersions(vs) {
    const sel = $('#prompt-versions');
    sel.replaceChildren(new Option('…', ''));
    vs.forEach(v => sel.append(new Option(`${v.hash}${v.current ? ' (current)' : ''} · ${new Date(v.saved * 1000).toLocaleString()} · ${v.preview.slice(0, 40)}`, v.hash)));
  }
  fillVersions(D.versions || []); promptInfo();
  pt.addEventListener('input', () => { promptInfo(); queueEstimate(); });
  $('#prompt-revert').addEventListener('click', () => { pt.value = saved; promptInfo(); queueEstimate(); });
  $('#prompt-versions').addEventListener('change', async e => {
    if (!e.target.value) return;
    const j = await (await fetch(`/library/api/${SLUG}/prompt/${e.target.value}`)).json();
    pt.value = j.text; promptInfo(); queueEstimate(); $('#prompt-msg').textContent = 'loaded — save to make it current';
  });
  $('#prompt-save').addEventListener('click', async () => {
    const r = await api(`/library/${SLUG}/prompt`, { text: pt.value });
    if (!r.ok) { $('#prompt-msg').textContent = 'could not save (prompt too short?)'; return; }
    const j = await r.json(); saved = pt.value; savedHash = j.hash; fillVersions(j.versions || []);
    $('#prompt-msg').textContent = 'saved'; promptInfo();
  });

  // =============================== live test ===============================
  let ctl = null, testId = null, sumText = '';
  const tmsg = t => { $('#t-msg').textContent = t; };
  async function loadStored(n) {
    const box = $('#t-cur'), meta = $('#t-cur-meta');
    try {
      const r = await fetch(`/library/${SLUG}/summary/${n}?v=current`);
      if (!r.ok) throw 0;
      const j = await r.json();
      render(box, j.summary || '');
      meta.textContent = [j.prompt_hash && `prompt ${j.prompt_hash}`, j.finish_reason].filter(Boolean).join(' · ');
      window.typesetMath?.([box]);
    } catch { box.innerHTML = '<p class="dim">none yet</p>'; meta.textContent = ''; }
  }

  // The stream can deliver thousands of tokens in a burst. Touching the DOM (or reading layout) per token
  // froze the page for tens of seconds, so tokens are only buffered here and painted at most every 100 ms.
  const nearBottom = el => el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
  function makePainter() {
    const think = $('#t-think'), sumEl = $('#t-sum');
    const node = document.createTextNode(''); think.replaceChildren(node);
    let tbuf = [], thinkTok = 0, sumTok = 0, sumDirty = false, lastRender = 0, timer = null;
    function paint(force) {
      timer = null;
      if (tbuf.length) {
        const stick = nearBottom(think);                 // one layout read per paint, not per token
        node.appendData(tbuf.join('')); tbuf = [];       // appends to the existing text node: no re-layout of old text
        if (stick) think.scrollTop = think.scrollHeight;
        $('#t-think-n').textContent = `~${fmt(thinkTok)} tokens`;
      }
      if (sumDirty && (force || performance.now() - lastRender > 400)) {
        sumDirty = false; lastRender = performance.now();
        const stick = nearBottom(sumEl), top = sumEl.scrollTop;   // keep the reader's scroll position across re-renders
        render(sumEl, sumText);
        sumEl.scrollTop = stick ? sumEl.scrollHeight : top;
        $('#t-sum-n').textContent = `~${fmt(sumTok)} tokens`;
      }
      if (sumDirty) schedule();
    }
    function schedule() { if (!timer) timer = setTimeout(() => paint(false), 100); }
    return {
      think(t) { tbuf.push(t); thinkTok++; schedule(); },
      content(t) { sumText += t; sumTok++; sumDirty = true; schedule(); },
      flush() { clearTimeout(timer); paint(true); },
    };
  }

  async function runTest() {
    const n = parseInt($('#t-n').value, 10);
    if (!(n >= 0 && n < D.total)) { tmsg('enter a valid page number'); return; }
    $('#t-grid').hidden = false; $('#t-save').disabled = true; testId = null;
    $('#t-sum').innerHTML = ''; sumText = ''; $('#t-think-n').textContent = ''; $('#t-sum-n').textContent = '';
    $('#t-stats').textContent = ''; loadStored(n);
    const painter = makePainter(); let wrote = false; const t0 = Date.now();
    const tick = setInterval(() => { $('#t-stats').textContent = `${((Date.now() - t0) / 1000).toFixed(0)} s elapsed`; }, 500);
    ctl = new AbortController();
    $('#t-run').disabled = true; $('#t-stop').disabled = false; tmsg('connecting…');
    try {
      const resp = await fetch(`/library/${SLUG}/summarise/test`, { method: 'POST', headers: HDR, signal: ctl.signal,
        body: JSON.stringify({ n, prompt: pt.value, max_tokens: +$('#t-max').value }) });
      if (!resp.ok) { const j = await resp.json().catch(() => ({})); tmsg(j.message || `request failed (${resp.status})`); return; }
      const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n'); buf = parts.pop();
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          const ev = JSON.parse(part.slice(6));
          if (ev.type === 'start') tmsg(`thinking… (prompt ${ev.prompt_hash})`);
          else if (ev.type === 'reasoning') painter.think(ev.text);
          else if (ev.type === 'content') { if (!wrote) { wrote = true; tmsg('writing the summary…'); } painter.content(ev.text); }
          else if (ev.type === 'done') {
            testId = ev.id; painter.flush(); window.typesetMath?.([$('#t-sum')]);
            const u = ev.usage || {};
            tmsg(`finished (${ev.finish_reason})`);
            $('#t-stats').textContent = `${((Date.now() - t0) / 1000).toFixed(0)} s · ${fmt(u.prompt_tokens || 0)} prompt, `
              + `${fmt(u.completion_tokens || 0)} completion (${fmt(((u.completion_tokens_details || {}).reasoning_tokens) || 0)} thinking)`
              + (ev.finish_reason === 'length' ? ' · CUT OFF by the token limit — raise max tokens' : '');
            $('#t-save').disabled = !sumText.trim();
          } else if (ev.type === 'error') tmsg('error: ' + ev.message);
        }
        await new Promise(r => setTimeout(r, 0));        // let the browser paint and handle input between bursts
      }
    } catch (e) {
      tmsg(e.name === 'AbortError' ? 'stopped (the request was closed, generation stops)' : 'connection problem: ' + e.message);
    } finally {
      painter.flush(); clearInterval(tick); ctl = null; $('#t-run').disabled = false; $('#t-stop').disabled = true;
    }
  }
  $('#t-run').addEventListener('click', runTest);
  $('#t-stop').addEventListener('click', () => ctl && ctl.abort());
  $('#t-save').addEventListener('click', async () => {
    if (!testId) return;
    const n = parseInt($('#t-n').value, 10);
    if (!confirm(`Save this as the summary of ${pad(n)}? Any existing summary moves to history.`)) return;
    const r = await api(`/library/${SLUG}/summarise/test/save`, { id: testId }); const j = await r.json();
    tmsg(j.ok ? `saved (${j.file})` : (j.message || 'could not save'));
    if (j.ok) { $('#t-save').disabled = true; loadStored(n); }
  });
  if (D.prefill != null) loadStored(D.prefill);

  // =============================== run ===============================
  const rmsg = t => { $('#r-msg').textContent = t; };
  const body = () => ({
    select: $('#r-select').value, start: +$('#r-start').value, end: +$('#r-end').value,
    redo: $('#r-redo').checked, workers: +$('#r-workers').value, max_tokens: +$('#r-max').value,
    max_pages: +$('#r-cap').value,
  });
  $('#r-select').addEventListener('change', e => {
    $('#r-range').hidden = e.target.value !== 'range'; $('#r-redo-l').hidden = e.target.value !== 'range'; queueEstimate();
  });
  ['r-start', 'r-end', 'r-redo', 'r-cap'].forEach(id => $('#' + id).addEventListener('input', queueEstimate));

  // estimate: tokens (and dollars if you entered rates); informational only, it never blocks a run
  const rates = () => { try { return JSON.parse(localStorage.getItem('kb:rates')) || {}; } catch { return {}; } };
  $('#rate-in').value = rates().in ?? ''; $('#rate-out').value = rates().out ?? '';
  ['rate-in', 'rate-out'].forEach(id => $('#' + id).addEventListener('input', () => {
    try { localStorage.setItem('kb:rates', JSON.stringify({ in: $('#rate-in').value, out: $('#rate-out').value })); } catch {}
    showEstimate();
  }));
  let est = null, estTimer = null;
  function queueEstimate() { clearTimeout(estTimer); estTimer = setTimeout(refreshEstimate, 350); }
  async function refreshEstimate() {
    try {
      const b = body(); delete b.max_pages;
      const r = await api(`/library/api/${SLUG}/estimate`, { ...b, prompt: pt.value });
      est = await r.json();
      if (+$('#r-cap').value > 0 && est.pages > +$('#r-cap').value) {         // honour the page cap in the numbers
        const f = +$('#r-cap').value / est.pages;
        est = { ...est, pages: +$('#r-cap').value, prompt_tokens: est.prompt_tokens * f, completion_p50: est.completion_p50 * f, completion_p90: est.completion_p90 * f };
      }
    } catch { est = null; }
    showEstimate();
  }
  function showEstimate() {
    if (!est) return;
    $('#e-pages').textContent = fmt(est.pages);
    $('#e-prompt').textContent = fmt(est.prompt_tokens);
    $('#e-comp').textContent = `${fmt(est.completion_p50)} → ${fmt(est.completion_p90)}`;
    const r = rates(), ri = parseFloat(r.in), ro = parseFloat(r.out);
    if (ri >= 0 && ro >= 0 && (r.in !== '' && r.out !== '' && r.in != null && r.out != null)) {
      const lo = est.prompt_tokens / 1e6 * ri + est.completion_p50 / 1e6 * ro;
      const hi = est.prompt_tokens / 1e6 * ri + est.completion_p90 / 1e6 * ro;
      $('#e-cost').textContent = `$${lo.toFixed(2)} – $${hi.toFixed(2)}`;
    } else $('#e-cost').textContent = '—';
    $('#e-note').textContent = est.pages
      ? `Completion per page ≈ ${fmt(est.per_page_p50)} typical / ${fmt(est.per_page_p90)} heavy, from ${est.basis}. Most of it is thinking tokens.`
      : 'No pages to do with these settings.';
  }

  async function start(extra) {
    const b = { ...body(), ...extra };
    rmsg('starting…');
    const r = await api(`/library/${SLUG}/summarise/start`, b); let j = {}; try { j = await r.json(); } catch {}
    rmsg(j.message || (r.ok ? 'started' : `could not start (${r.status})`)); poll();
    return r.ok;
  }
  $('#r-start-btn').addEventListener('click', () => start({}));
  $('#r-retry').addEventListener('click', () => start({ select: 'failed' }));
  $('#r-stop').addEventListener('click', async () => { await api('/library/summarise/stop'); rmsg('will stop once in-flight pages finish'); poll(); });

  const dur = s => s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
  let timer = null, wasActive = false;
  async function poll() {
    clearTimeout(timer);
    let j; try { j = await (await fetch('/library/api/summarise/job')).json(); } catch { timer = setTimeout(poll, 5000); return; }
    const mine = j.slug === SLUG;
    $('#r-start-btn').disabled = !!j.active || $('#r-start-btn').dataset.nokey === '1';
    $('#r-stop').disabled = !(j.active && mine && j.status === 'running');
    const box = $('#j-box');
    if (j.status && j.status !== 'idle') {
      box.hidden = false;
      const failed = j.failed || {}, nfail = Object.keys(failed).length;
      const total = j.total_pages || 0, n = j.done + (j.skipped || 0) + (j.empty || 0) + nfail;
      $('#j-status').textContent = j.status + (mine ? '' : ` (book: ${j.slug})`);
      $('#j-bar').style.width = total ? (100 * n / total) + '%' : '0';
      $('#j-detail').textContent = `${j.done} done · ${nfail} failed · ${(j.truncated || []).length} truncated · ${Math.max(0, total - n)} left`
        + ` · ${fmt(j.prompt_tokens || 0)} prompt / ${fmt(j.completion_tokens || 0)} completion tokens so far`;
      const ds = (j.durations || []).slice(-8), per = ds.length ? ds.reduce((a, b) => a + b, 0) / ds.length : 0;
      $('#j-eta').textContent = per && j.active ? `${per.toFixed(0)} s per page per worker · about ${dur(per * Math.max(0, total - n) / (j.workers || 1))} remaining` : '';
      const fl = Object.entries(j.in_flight || {});
      $('#j-flight').textContent = fl.length ? 'in flight: ' + fl.map(([p, s]) => `${pad(+p)} (${Math.round(s)}s)`).join(', ') : '';
      $('#j-failed').innerHTML = nfail ? '<h3 class="sub">failed</h3>' + Object.entries(failed).map(([p, e]) =>
        `<div class="xrow"><a href="/library/${SLUG}/page/${p}">${pad(+p)}</a> <span class="dim"></span></div>`).join('') : '';
      Object.entries(failed).forEach(([p, e], i) => { $('#j-failed').querySelectorAll('.xrow .dim')[i].textContent = e; });
      $('#r-retry').hidden = !(nfail && !j.active);
      $('#j-log').textContent = (j.log || []).join('\n');
    }
    if (j.active) { wasActive = true; timer = setTimeout(poll, 2000); }
    else if (wasActive) { wasActive = false; refreshEstimate(); loadFlagged(); }
  }

  // =============================== flagged ===============================
  let flagged = {};
  async function loadFlagged() {
    const list = $('#f-list');
    try { flagged = (await (await fetch(`/library/api/${SLUG}/flagged`)).json()).reasons || {}; } catch { flagged = {}; }
    const keys = Object.keys(flagged);
    if (!keys.length) { list.innerHTML = '<span class="dim">No flagged pages among the OCR\'d pages (or no summaries to analyse yet).</span>'; $('#f-go').disabled = true; return; }
    list.replaceChildren(...keys.map(k => {
      const l = document.createElement('label'); l.className = 'chk';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = k;
      cb.addEventListener('change', updFlag);
      l.append(cb, ` ${k} (${flagged[k].length})`); return l;
    }));
    updFlag();
  }
  const flagPages = () => [...new Set([...document.querySelectorAll('#f-list input:checked')].flatMap(c => flagged[c.value]))].sort((a, b) => a - b);
  function updFlag() { const n = flagPages().length; $('#f-go').disabled = !n; $('#f-go').textContent = n ? `Re-summarise ${n} page${n === 1 ? '' : 's'}` : 'Re-summarise selected'; }
  $('#f-go').addEventListener('click', async () => {
    const pages = flagPages(); if (!pages.length) return;
    $('#f-msg').textContent = 'starting…';
    const r = await api(`/library/${SLUG}/summarise/start`, { select: 'pages', pages, redo: true, workers: +$('#r-workers').value, max_tokens: +$('#r-max').value });
    let j = {}; try { j = await r.json(); } catch {}
    $('#f-msg').textContent = j.message || (r.ok ? 'started' : 'could not start'); poll();
  });

  // ---- init ----
  if ($('#r-start-btn').disabled) $('#r-start-btn').dataset.nokey = '1';
  refreshEstimate(); poll(); loadFlagged();
})();
