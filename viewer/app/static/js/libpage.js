// Library page viewer: OCR text and summary rendered with the same math pipeline as the rest of the app.
(() => {
  const P = JSON.parse(document.getElementById('lp-data').textContent);
  const $ = s => document.querySelector(s);
  const hdr = { 'Content-Type': 'application/json', 'X-RKP': '1' };

  window.addEventListener('load', () => {            // render.js is loaded after this script
    const ocr = $('#ocr-body');
    if (ocr && P.ocr != null) {
      window.renderMarkdownInto(ocr, P.ocr, { safeHtml: true });
      $('#ocr-raw').textContent = P.ocr;
    }
    const sum = $('#sum-body');
    if (sum && P.summary != null) window.renderMarkdownInto(sum, P.summary, { safeHtml: true });
    window.typesetMath([ocr, sum].filter(Boolean));
  });

  // earlier versions of this page's summary (kept in summaries/history/ whenever one is replaced)
  (async () => {
    try {
      const j = await (await fetch(`/library/${P.slug}/summary/${P.n}`)).json();
      if (!j.versions || !j.versions.length) return;
      const sel = $('#sum-versions'); sel.hidden = false;
      sel.append(new Option('current', 'current'));
      j.versions.forEach(v => sel.append(new Option(new Date(v.saved * 1000).toLocaleString() + (v.truncated ? ' (cut off)' : ''), v.name)));
      sel.addEventListener('change', async () => {
        const r = await (await fetch(`/library/${P.slug}/summary/${P.n}?v=${encodeURIComponent(sel.value)}`)).json();
        const box = $('#sum-body') || (() => { const d = document.createElement('div'); d.className = 'body'; d.id = 'sum-body'; sel.closest('.srcpanel').append(d); return d; })();
        window.renderMarkdownInto(box, r.summary || '', { safeHtml: true }); window.typesetMath([box]);
        $('#sum-meta').textContent = [r.prompt_hash && `prompt ${r.prompt_hash}`, r.finish_reason, r.generated_at].filter(Boolean).join(' · ');
      });
    } catch {}
  })();

  $('#toggle-raw')?.addEventListener('click', e => {
    const raw = $('#ocr-raw'), body = $('#ocr-body');
    raw.hidden = !raw.hidden; body.hidden = !raw.hidden;
    e.target.textContent = raw.hidden ? 'show raw' : 'show rendered';
  });

  // re-OCR just this page
  $('#redo-page')?.addEventListener('click', async () => {
    const msg = $('#redo-msg');
    if (!confirm(`Re-run OCR for ${P.pid}? The existing output is kept as a .prev file until the new one is written.`)) return;
    msg.textContent = 'starting…';
    try {
      const r = await fetch(`/library/${P.slug}/ocr/start`, { method: 'POST', headers: hdr, body: JSON.stringify({
        select: 'pages', pages: [P.n], redo: true, mode: $('#redo-mode').value }) });
      const j = await r.json(); msg.textContent = j.message || (j.ok ? 'started' : 'could not start');
      if (j.ok) poll();
    } catch { msg.textContent = 'request failed'; }
  });

  // job banner + reload when this page's conversion finishes
  let was = false;
  async function poll() {
    try {
      const j = await (await fetch('/library/api/job')).json();
      const b = $('#job-banner');
      if (j.active) {
        b.hidden = false; was = true;
        b.innerHTML = `OCR running on <b>${j.slug}</b> — page p${String(j.current ?? 0).padStart(5, '0')} (${j.done} done). `
          + `<a href="/library/${j.slug}">control panel</a>`;
        setTimeout(poll, 2500);
      } else {
        b.hidden = true;
        if (was) { was = false; location.reload(); }
      }
    } catch { setTimeout(poll, 5000); }
  }
  poll();
})();
