// Resolved by MathJax's pageReady hook. render.js awaits it, so typesetting
// works no matter which of the two (async) scripts finishes first.
window.mathReady = new Promise(resolve => { window.__resolveMath = resolve; });

window.MathJax = {
  tex: {
    inlineMath: [['\\(', '\\)']],
    displayMath: [['\\[', '\\]']],
    processEscapes: false,      // render.js tokenizes math itself
    processEnvironments: true,
    processRefs: true,
    tags: 'none',
    maxMacros: 10000,
    maxBuffer: 20 * 1024,
    macros: {
      R: '\\mathbb{R}', N: '\\mathbb{N}', Z: '\\mathbb{Z}',
      Q: '\\mathbb{Q}', C: '\\mathbb{C}', E: '\\mathbb{E}',
      P: '\\mathbb{P}', F: '\\mathbb{F}',
      argmax: '\\operatorname*{arg\\,max}',
      argmin: '\\operatorname*{arg\\,min}',
      st: '\\text{s.t.}',
      // Common LaTeX/macros that LLM output assumes exist.
      bm: ['\\boldsymbol{#1}', 1],
      mathbbm: ['\\mathbb{#1}', 1],
      abs: ['\\left|#1\\right|', 1],
      norm: ['\\left\\|#1\\right\\|', 1],
      set: ['\\left\\{#1\\right\\}', 1],
      ceil: ['\\left\\lceil#1\\right\\rceil', 1],
      floor: ['\\left\\lfloor#1\\right\\rfloor', 1],
      inner: ['\\left\\langle#1\\right\\rangle', 1],
      Var: '\\operatorname{Var}', Cov: '\\operatorname{Cov}',
      tr: '\\operatorname{tr}', rank: '\\operatorname{rank}',
      diag: '\\operatorname{diag}', sgn: '\\operatorname{sgn}',
      softmax: '\\operatorname{softmax}', KL: '\\mathrm{KL}',
      defeq: '\\mathrel{:=}', eps: '\\varepsilon', mathds: ['\\mathbb{#1}', 1],
      dd: '\\mathrm{d}', vect: ['\\boldsymbol{#1}', 1],
      tfrac: ['{\\textstyle\\frac{#1}{#2}}', 2],
      dfrac: ['{\\displaystyle\\frac{#1}{#2}}', 2]
    },
    packages: {
      '[+]': ['ams', 'boldsymbol', 'mathtools', 'braket', 'cancel', 'color',
              'mhchem', 'enclose', 'extpfeil', 'noundefined', 'noerrors']
    }
  },
  loader: {
    load: ['[tex]/ams', '[tex]/boldsymbol', '[tex]/mathtools', '[tex]/braket',
           '[tex]/cancel', '[tex]/color', '[tex]/mhchem', '[tex]/enclose',
           '[tex]/extpfeil', '[tex]/noundefined', '[tex]/noerrors'],
    // A failed extension download must not take all math down with it.
    failed(err) { console.warn('MathJax component failed:', err.package || err); }
  },
  chtml: { scale: 1.0, matchFontHeight: false, displayAlign: 'left', displayIndent: '0' },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
    renderActions: { addMenu: [] }
  },
  startup: {
    typeset: false,
    pageReady() {
      return MathJax.startup.defaultPageReady().then(() => window.__resolveMath());
    }
  }
};
