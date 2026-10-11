// Library book page: OCR job controls, live progress, and the page grid.
(() => {
  const D = JSON.parse(document.getElementById('lib-data').textContent);
  const $ = s => document.querySelector(s);
  const hdr = { 'Content-Type': 'application/json', 'X-RKP': '1' };
  const pageUrl = n => D.urls.page.replace('__N__', n);
  const pad = n => 'p' + String(n).padStart(5, '0');

  // ---- grid ----
  const done = new Set(D.done), summ = new Set(D.summarised), failed = new Set();
  const box = $('#libcells'); const cells = [];
  for (let n = 0; n < D.total; n++) {
    const a = document.createElement('a'); a.href = pageUrl(n); a.className = 'cell lc';
    a.title = pad(n); cells.push(a); box.append(a);
  }
  let running = null;
  function paint() {
    cells.forEach((a, n) => {
      a.classList.toggle('done', done.has(n));
      a.classList.toggle('sum', summ.has(n));
      a.classList.toggle('bad', failed.has(n) && !done.has(n));
      a.classList.toggle('run', n === running);
    });
    $('#n-done').textContent = done.size;
  }
  paint();

  // ---- form ----
  $('#f-select').addEventListener('change', e => { $('#f-range').hidden = e.target.value !== 'range'; });
  const body = () => ({
    select: $('#f-select').value, start: +$('#f-start').value, end: +$('#f-end').value,
    mode: $('#f-mode').value, pause_s: +$('#f-pause').value, max_pages: +$('#f-max').value,
    timeout_s: +$('#f-timeout').value, redo: $('#f-redo').checked,
  });
  const msg = t => { $('#job-msg').textContent = t; };
  async function post(url, data) {
    const r = await fetch(url, { method: 'POST', headers: hdr, body: data ? JSON.stringify(data) : '{}' });
    let j = {}; try { j = await r.json(); } catch {}
    return { ok: r.ok && j.ok !== false, j, status: r.status };
  }
  $('#btn-start').addEventListener('click', async () => {
    msg('starting…');
    const r = await post(`/library/${D.slug}/ocr/start`, body());
    msg(r.j.message || (r.ok ? 'started' : `could not start (${r.status})`));
    poll();
  });
  $('#btn-stop').addEventListener('click', async () => { await post('/library/ocr/stop'); msg('will stop after the current page'); poll(); });
  $('#btn-cancel').addEventListener('click', async () => {
    if (!confirm('Cancel now? The page being converted is abandoned (finished pages are kept).')) return;
    await post('/library/ocr/cancel'); msg('cancelling…'); poll();
  });

  // ---- live status ----
  const fmt = s => s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
  let timer = null, lastDone = -1;
  async function poll() {
    clearTimeout(timer);
    let j;
    try { j = await (await fetch('/library/api/job')).json(); } catch { timer = setTimeout(poll, 5000); return; }
    const mine = j.slug === D.slug;
    $('#btn-start').disabled = !!j.active;
    $('#btn-stop').disabled = !(j.active && mine && j.status === 'running');
    $('#btn-cancel').disabled = !(j.active && mine);
    const jb = $('#job-box');
    if (j.status && j.status !== 'idle') {
      jb.hidden = false;
      const other = mine ? '' : ` (book: ${j.slug})`;
      $('#job-status').textContent = j.status + other;
      const total = j.total_pages || 0;
      const n = j.done + (j.skipped || 0) + (j.failed ? j.failed.length : 0);
      $('#job-bar').style.width = total ? (100 * n / total) + '%' : '0';
      const avg = (j.durations || []).slice(-5);
      const per = avg.length ? avg.reduce((a, b) => a + b, 0) / avg.length : 0;
      const left = Math.max(0, total - n);
      $('#job-detail').textContent = `${j.done} done · ${(j.failed || []).length} failed · ${left} left`
        + (j.current != null ? ` · converting ${pad(j.current)}` : '');
      $('#job-eta').textContent = per ? `${per.toFixed(0)} s/page (last ${avg.length})` + (j.active ? ` · about ${fmt(per * left)} remaining` : '') : '';
      $('#job-log').textContent = (j.log || []).join('\n');
      if (mine) { running = j.active ? j.current : null; (j.failed || []).forEach(n => failed.add(n)); }
    }
    if (mine && j.done !== lastDone) {
      lastDone = j.done;
      try { (await (await fetch(`/library/api/${D.slug}/done`)).json()).done.forEach(n => done.add(n)); } catch {}
    }
    paint();
    if (j.active) timer = setTimeout(poll, 2000);
  }
  poll();
})();
