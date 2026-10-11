// Math is claimed as marked tokens, so the markdown lexer never sees it
// (no `_`/`*` emphasis, no escaping, no <, > or & mangling inside formulas).

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Environments that open their own display-math mode (must NOT sit inside \[ \]).
const STANDALONE = 'equation|align|alignat|flalign|gather|multline|eqnarray|math|displaymath';
// Environments that need an enclosing math mode.
const NEEDS_MODE = 'aligned|alignedat|gathered|split|cases|dcases|rcases|array|subarray|' +
  '[pbBvV]?matrix|smallmatrix|[pbBvV]?smallmatrix|subequations';
const ENV = `(?:${STANDALONE}|${NEEDS_MODE})\\*?`;
const envRe = new RegExp(`^\\\\begin\\{(${ENV})\\}[\\s\\S]+?\\\\end\\{\\1\\}`);
const standaloneRe = new RegExp(`^(?:${STANDALONE})\\*?$`);

// Content: any escaped char, or anything that isn't an unescaped delimiter.
const DOLLAR_INLINE = /^\$(?!\s)((?:\\[\s\S]|[^$\\\n])+?)(?<!\s)\$(?!\d)/;
const DOLLAR_DISPLAY = /^\$\$((?:\\[\s\S]|[^$\\]|\$(?!\$))+?)\$\$/;
const BRACKET_DISPLAY = /^\\\[([\s\S]+?)\\\]/;
const PAREN_INLINE = /^\\\(([\s\S]+?)\\\)/;

// An unescaped `#` is illegal in math and is what OCR leaves where an `=` was lost
// at a line break. Real hashes are written \#.
// `x^*'` is a "double exponent" error in MathJax; move the prime inside the exponent.
const fixPrimes = tex => tex.replace(/\^(\{[^{}]*\}|\\[A-Za-z]+|[^\s{}\\^_])('+)/g,
  (m, a, p) => `^{${a.startsWith('{') ? a.slice(1, -1) : a}${p}}`);
const fixTex = tex => fixPrimes(tex.replace(/(?<!\\)#/g, '='));

function displayHtml(tex) {
  return `<div class="math-display">\\[${esc(fixTex(tex.trim()))}\\]</div>`;
}
function envHtml(env, raw) {
  return standaloneRe.test(env)
    ? `<div class="math-display">${esc(fixTex(raw))}</div>`
    : displayHtml(raw);
}

const mathExtension = {
  extensions: [
    {
      name: 'mathBlock',
      level: 'block',
      // Only at line starts: a mid-line match would split the paragraph.
      start(src) { return src.match(/^[ \t]*(?:\$\$|\\\[|\\begin\{)/m)?.index; },
      tokenizer(src) {
        const lead = /^[ \t]*/.exec(src)[0].length;
        const s = src.slice(lead);
        // Display math must own its line(s); otherwise the inline rule takes it.
        const eol = len => /^[ \t]*(?:\n|$)/.test(s.slice(len));
        let m;
        if ((m = DOLLAR_DISPLAY.exec(s)) && eol(m[0].length)) {
          return { type: 'mathBlock', raw: src.slice(0, lead + m[0].length), html: displayHtml(m[1]) };
        }
        if ((m = BRACKET_DISPLAY.exec(s)) && eol(m[0].length)) {
          return { type: 'mathBlock', raw: src.slice(0, lead + m[0].length), html: displayHtml(m[1]) };
        }
        if ((m = envRe.exec(s)) && eol(m[0].length)) {
          return { type: 'mathBlock', raw: src.slice(0, lead + m[0].length), html: envHtml(m[1], m[0]) };
        }
      },
      renderer(t) { return t.html + '\n'; }
    },
    {
      name: 'mathInline',
      level: 'inline',
      start(src) { return src.match(/\$|\\\(|\\\[|\\begin\{/)?.index; },
      tokenizer(src) {
        let m;
        // Mid-paragraph display math, e.g. "so $$x^2$$ follows".
        if ((m = DOLLAR_DISPLAY.exec(src)) || (m = BRACKET_DISPLAY.exec(src))) {
          return { type: 'mathInline', raw: m[0], html: `\\[${esc(fixTex(m[1].trim()))}\\]` };
        }
        if ((m = envRe.exec(src))) {
          return { type: 'mathInline', raw: m[0], html: envHtml(m[1], m[0]) };
        }
        if ((m = PAREN_INLINE.exec(src))) {
          return { type: 'mathInline', raw: m[0], html: `\\(${esc(fixTex(m[1].trim()))}\\)` };
        }
        if ((m = DOLLAR_INLINE.exec(src))) {
          return { type: 'mathInline', raw: m[0], html: `\\(${esc(fixTex(m[1]))}\\)` };
        }
      },
      renderer(t) { return t.html; }
    }
  ]
};

// A multi-line $$...$$ must reach marked as ONE block. Blank lines inside it would
// end the paragraph (leaving a dangling $$), and a $$ opened mid-sentence is never
// a block. So: drop blank lines, put the formula on its own lines, and stack
// blank-line-separated equations in a gathered environment instead of
// concatenating them into one unreadable row.
function normalizeDisplayMath(md) {
  return md.split(/(^[ \t]*(?:```|~~~)[\s\S]*?(?:^[ \t]*(?:```|~~~)[^\n]*$|(?![\s\S])))/m)
    .map((seg, i) => i % 2 ? seg : seg.replace(/(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$/g, (all, tex) => {
      if (!tex.includes('\n')) return all;
      const chunks = tex.split(/\n[ \t]*\n/).map(c => c.trim()).filter(Boolean);
      const body = chunks.length > 1 && !chunks.some(c => /\\\\|\\begin\{/.test(c))
        ? `\\begin{gathered}\n${chunks.join(' \\\\\n')}\n\\end{gathered}`
        : chunks.join('\n');
      return `\n\n$$\n${body}\n$$\n\n`;
    })).join('');
}

// Markdown must never see a line break inside a formula: a line that is just `=` or `-` under a text
// line is a setext heading underline, `-`/`+` start list items, blank lines end paragraphs, `|` splits
// table cells. TeX treats a newline as a space, so flatten every multi-line formula onto one line.
const FLAT = new RegExp(
  '(?<!\\\\)\\\\\\[[\\s\\S]{1,12000}?\\\\\\]' +                       // \[ ... \]
  '|(?<!\\\\)\\$\\$[\\s\\S]{1,12000}?(?<!\\\\)\\$\\$' +                 // $$ ... $$
  `|\\\\begin\\{(${ENV})\\}[\\s\\S]{1,12000}?\\\\end\\{\\1\\}` +             // \begin{env} ... \end{env}
  '|(?<!\\\\)\\\\\\([\\s\\S]{1,1500}?\\\\\\)', 'g');                      // \( ... \)
function flattenMath(md) {
  return md.split(/(^[ \t]*(?:```|~~~)[\s\S]*?(?:^[ \t]*(?:```|~~~)[^\n]*$|(?![\s\S])))/m)
    .map((seg, i) => i % 2 ? seg : seg.replace(FLAT, m => m.includes('\n')
      ? m.replace(/(?<!\\)%[^\n]*/g, '').replace(/\s*\n\s*/g, ' ') : m)).join('');
}

// GFM splits table rows on every `|`, which shreds |x| and \| inside math.
// \vert / \Vert are identical in math mode, so swap them on table-ish lines.
function protectPipes(md) {
  let fenced = false;
  return md.split('\n').map(line => {
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; return line; }
    if (fenced || !line.includes('|') || !line.includes('$') || line.includes('`')) return line;
    return line.replace(/\$\$?(?:\\[\s\S]|[^$\\])+?\$\$?/g, span =>
      span.replace(/\\\|/g, '\\Vert{}').replace(/(?<!\\)\|/g, '\\vert{}'));
  }).join('\n');
}

const hasMarked = typeof marked !== 'undefined';
if (hasMarked) marked.use(mathExtension);

// Shared by the summary bodies and the trace viewer. `opts.trace`: model scratch text, so raw HTML is
// shown as text and single newlines are kept.
function renderMarkdownInto(el, src, opts = {}) {
  if (!hasMarked) { el.textContent = src; return false; }   // CDN down: show raw text
  try {
    const o = { gfm: true, breaks: !!opts.trace };
    // trace / safeHtml: text from a model or from OCR, so raw HTML is shown as text, never executed
    if (opts.trace || opts.safeHtml) { const r = new marked.Renderer(); r.html = t => esc(typeof t === 'string' ? t : t.text); o.renderer = r; }
    el.innerHTML = marked.parse(protectPipes(flattenMath(normalizeDisplayMath(src))), o);
    if (!opts.trace && !opts.safeHtml) window.xrefLink?.(el);
    return true;
  } catch (e) {
    console.error('markdown:', e);
    el.textContent = src;
    return false;
  }
}
window.renderMarkdownInto = renderMarkdownInto;
window.typesetMath = els => window.mathReady
  ? window.mathReady.then(() => MathJax.typesetPromise(els)).catch(e => console.error('MathJax:', e))
  : Promise.resolve();

const bodies = [...document.querySelectorAll('[data-markdown]')];
bodies.forEach(el => {
  const src = el.dataset.markdown;
  el.removeAttribute('data-markdown');
  if (el.id === 'body') window.__summaryMarkdown = src;
  renderMarkdownInto(el, src);
});
window.typesetMath(bodies);
