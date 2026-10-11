// Turns known cross-references in rendered markdown into links.
// Runs before MathJax, so math is still raw \( \) / \[ \] text: skip any match
// that sits inside an open math delimiter.
window.xrefLink = function (root) {
  let refs;
  try { refs = JSON.parse(root.dataset.refs || '[]'); } catch { return; }
  if (!refs.length) return;
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const byText = new Map(refs.map(r => [r.t.toLowerCase(), r]));
  const alt = refs.map(r => r.t).sort((a, b) => b.length - a.length).map(esc).join('|');
  const re = new RegExp(`(?<![\\w])(${alt})(?![\\w])`, 'gi');
  const seenTerms = new Set();
  const inMath = s => {
    const open = (s.match(/\\\(|\\\[|\$/g) || []).length;
    const close = (s.match(/\\\)|\\\]/g) || []).length;
    const dollars = (s.match(/\$/g) || []).length;
    return (open - dollars) > close || dollars % 2 === 1;
  };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest('a, code, pre, h1, h2, h3, h4')
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
  });
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const text = node.nodeValue;
    const frag = document.createDocumentFragment();
    let last = 0, changed = false, m;
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      const r = byText.get(m[1].toLowerCase());
      if (!r || inMath(text.slice(0, m.index))) continue;
      // Terms: link the first occurrence only, to keep prose readable.
      if (r.term) { if (seenTerms.has(r.t)) continue; seenTerms.add(r.t); }
      frag.append(text.slice(last, m.index));
      const a = document.createElement('a');
      a.href = r.u; a.className = 'xr' + (r.term ? ' term' : '');
      a.textContent = m[1];
      frag.append(a);
      last = m.index + m[0].length; changed = true;
    }
    if (changed) { frag.append(text.slice(last)); node.replaceWith(frag); }
  });
};
