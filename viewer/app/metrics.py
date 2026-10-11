"""Per-page token and text-structure metrics for the DeepSeek summaries, plus
chapter/domain aggregates, outlier detection and quality checks. Cached per domain."""
import math
import re
import statistics as st
from collections import Counter, defaultdict

from app import store, xref
from app import trace as trace_lib

SUMMARY_NOTE = "summary tokens = completion tokens − reasoning tokens"

# ---- text-structure patterns ----------------------------------------------------------
FENCE = re.compile(r"^[ \t]*(```|~~~)[\s\S]*?(?:^[ \t]*\1[^\n]*$|\Z)", re.M)
DISPLAY = [re.compile(r"(?<!\\)\$\$[\s\S]+?(?<!\\)\$\$"), re.compile(r"\\\[[\s\S]+?\\\]"),
           re.compile(r"\\begin\{([a-zA-Z*]+)\}[\s\S]+?\\end\{\1\}")]
INLINE = [re.compile(r"\\\([\s\S]+?\\\)"), re.compile(r"(?<![\\$])\$(?!\s)(?:\\.|[^$\\\n])+?(?<!\s)\$(?!\d)")]
RESULT = re.compile(r"\*\*(?:Theorem|Lemma|Proposition|Corollary|Claim)\b", re.I)
DEFN = re.compile(r"\*\*Definitions?\b", re.I)
PROOF = re.compile(r"\*\*Proofs?\b|^\s*Proofs?\.", re.I | re.M)
EXAMPLE = re.compile(r"\*\*Examples?\b", re.I)
REMARK = re.compile(r"\*\*Remarks?\b|\*\*Notes?\b", re.I)

GAP_PATTERNS = {   # things the page itself says are missing -> real gaps
    "deferred/incomplete proof": re.compile(r"proof[^.\n]{0,80}(?:incomplete|not (?:given|shown|included|completed|reproduced|supplied)|is deferred|continues|is cut)", re.I),
    "cut at page boundary": re.compile(r"cut off|truncated|ends mid|page boundary|mid-(?:sentence|proof|formula)|continues (?:on|onto|in) the (?:next|following) page", re.I),
}
RELIES = re.compile(r"(?:assumed|taken|used|carried) (?:from|over from) (?:the )?(?:earlier|previous|preceding)|not (?:re)?defined on this page|defined (?:earlier|elsewhere)", re.I)
# Section vocabulary the model tends to use (as headings or bold lead-ins).
SECTION_NAMES = {
    "scope": r"scope|overview|context|setting", "notation": r"notation|assumptions?|assumed|conventions?",
    "status": r"status|stated conclusion|content of the page",
    "definitions": r"definitions?", "statements": r"theorems?|lemmas?|propositions?|corollar(?:y|ies)|claims?|results?",
    "proofs": r"proofs?", "remarks": r"remarks?|comments?|observations?", "examples": r"examples?",
}
SECTION_RX = {k: re.compile(rf"\b(?:{pat})\b") for k, pat in SECTION_NAMES.items()}
LEADS = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$|^\*\*([^*\n]{2,60}?)\.?\*\*", re.M)
UNCERTAIN = re.compile(r"unclear|illegible|garbled|can'?t read|cannot read|unreadable|ocr (?:error|artifact|noise|mistake|damage|glitch)|misrecogni|misprint|typo|ambiguous|"
                       r"uncertain|not sure|corrupt|missing (?:symbol|character)|hard to (?:read|tell)")
SELF_CORR = re.compile(r"\bwait\b|\bhmm\b|\bactually\b|\bon second thought\b|\blet me re")

OUTLIER_METRICS = {
    "think_ratio": ("thinking ÷ summary tokens", "model reasoned unusually {dir} for the length of output"),
    "compression": ("summary ÷ input tokens", "summary is unusually {dir} relative to the page's input"),
    "summary_tokens": ("summary tokens", "unusually {dir} summary"),
    "reasoning_tokens": ("thinking tokens", "unusually {dir} amount of thinking"),
}


def _strip_code(text):
    return FENCE.sub("", text)


def _math_stats(text):
    t = _strip_code(text)
    disp = inline = math_chars = 0
    for rx in DISPLAY:
        for m in rx.finditer(t):
            disp += 1; math_chars += len(m[0])
        t = rx.sub(" ", t)
    for rx in INLINE:
        for m in rx.finditer(t):
            inline += 1; math_chars += len(m[0])
        t = rx.sub(" ", t)
    return disp, inline, math_chars, t        # t = text with math removed


def structure(text):
    disp, inline, math_chars, prose = _math_stats(text)
    code_free = _strip_code(text)
    lines = code_free.split("\n")
    prose_clean = re.sub(r"[#*_>|`~-]+", " ", prose)
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", prose_clean)
    total = max(1, len(code_free))
    return {
        "headings": sum(bool(re.match(r"^\s{0,3}#{1,6}\s", l)) for l in lines),
        "defs": len(DEFN.findall(code_free)),
        "results": len(RESULT.findall(code_free)),
        "proofs": len(PROOF.findall(code_free)),
        "examples": len(EXAMPLE.findall(code_free)),
        "remarks": len(REMARK.findall(code_free)),
        "tables": sum(bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", l)) for l in lines),
        "list_items": sum(bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s", l)) for l in lines),
        "quotes": sum(l.lstrip().startswith(">") for l in lines),
        "display_math": disp, "inline_math": inline,
        "math_share": round(math_chars / total, 4),
        "prose_words": len(words),
        "summary_chars": len(text),
    }, prose_clean


def first_heading(text):
    m = re.search(r"^\s{0,3}#{1,2}\s+(.+?)\s*$", text, re.M)
    return re.sub(r"[*_`]", "", m[1]).strip().lower() if m else ""


def _shingles(words, k=6):
    ws = [w.lower() for w in words]
    return {hash(" ".join(ws[i:i + k])) for i in range(max(0, len(ws) - k + 1))}


def _robust_z(values):
    xs = [math.log1p(v) for v in values]
    med = st.median(xs)
    mad = st.median(abs(x - med) for x in xs) or 1e-9
    return [0.6745 * (x - med) / mad for x in xs]


def _med(xs):
    xs = list(xs)
    return st.median(xs) if xs else 0


def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0
    i = (len(xs) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


_cache: dict = {}


def build(domain: str) -> dict:
    sig = xref._signature(domain)
    hit = _cache.get(domain)
    if hit and hit["sig"] == sig:
        return hit
    ix = xref.build(domain)
    rows: dict[str, dict] = {}
    shingles: dict[str, set] = {}

    for pid in ix["order"]:
        s = store.load(domain, pid)
        if not s:
            continue
        u = s.usage
        prompt = u.get("prompt_tokens", 0)
        cached = u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        comp, think = s.completion_tokens, s.reasoning_tokens
        summ = max(0, comp - think)
        text = s.summary
        stx, prose = structure(text)
        trace = (s.reasoning or "").lower()
        meta = ix["meta"][pid]
        gaps = {k: len(rx.findall(text)) for k, rx in GAP_PATTERNS.items()}
        leads = [(m[1] or m[2]).lower() for m in LEADS.finditer(_strip_code(text))]
        secs = sorted(k for k, rx in SECTION_RX.items() if any(rx.search(l) for l in leads))
        info = ix["by_page"][pid]
        undef = [xref_e.label for slug in info["mentions"]
                 for xref_e in [ix["entities"][slug]]
                 if xref_e.numbered and xref_e.kind in xref.LABEL_KINDS and not xref_e.defined]
        unc = len(UNCERTAIN.findall(trace))
        rows[pid] = {
            "page_id": pid, "chapter": meta["chapter_label"], "chapter_key": meta["chapter"],
            "label": meta["label"], "finish": s.finish_reason, "truncated": s.truncated,
            "nomath": "nomath" in meta["flags"], "empty": "empty" in meta["flags"],
            "prompt": prompt, "cached_pct": round(100 * cached / prompt, 1) if prompt else 0,
            "completion": comp, "reasoning_tokens": think, "summary_tokens": summ,
            "think_ratio": round(think / summ, 2) if summ else 0,
            "first_heading": first_heading(text),
            "trace_chars": len(trace), "uncertainty": unc, "hedge_per100": round(trace_lib.hedge_density(trace), 2),
            "uncertainty_density": round(1000 * unc / max(1, len(trace)), 2),
            "self_corrections": len(SELF_CORR.findall(trace)),
            "gaps": gaps, "gap_count": gaps["deferred/incomplete proof"], "cut_count": gaps["cut at page boundary"], "undef_refs": undef,
            "relies_earlier": len(RELIES.findall(text)), "sections": secs, "n_leads": len(leads),
            **stx,
        }
        if not meta["flags"] or meta["flags"] == ["truncated"]:
            w = prose.split()
            if len(w) >= 60:
                shingles[pid] = _shingles(w)

    # input size estimate: prompt minus the fixed instruction overhead (smallest prompt seen)
    base = min((r["prompt"] for r in rows.values() if r["prompt"]), default=0)
    for r in rows.values():
        r["input_est"] = max(0, r["prompt"] - base)
        r["compression"] = round(r["summary_tokens"] / r["input_est"], 3) if r["input_est"] >= 50 else 0

    # ---- outliers (robust z on log scale), judged only among pages with real content
    cohort = [r for r in rows.values() if not r["nomath"] and not r["empty"] and r["summary_tokens"] > 0
              and not r["truncated"]]
    outliers = []
    for key, (title, note) in OUTLIER_METRICS.items():
        pool = [r for r in cohort if (r["input_est"] >= 50 or key != "compression")]
        if len(pool) < 20:
            continue
        zs = _robust_z([r[key] for r in pool])
        for r, z in zip(pool, zs):
            if abs(z) >= 3.5:
                r.setdefault("outlier_hits", []).append(key)
                outliers.append({"page_id": r["page_id"], "metric": key, "title": title,
                                 "value": r[key], "z": round(z, 1),
                                 "dir": "high" if z > 0 else "low",
                                 "note": note.format(dir="high" if z > 0 else "low"),
                                 "median": round(_med(p[key] for p in pool), 2)})
    outliers.sort(key=lambda o: -abs(o["z"]))
    for r in rows.values():
        r["outlier"] = max((abs(o["z"]) for o in outliers if o["page_id"] == r["page_id"]), default=0)

    p90 = _quantile([r["uncertainty_density"] for r in rows.values()], .9)
    for r in rows.values():
        r["doubt_high"] = r["uncertainty"] >= 3 and r["uncertainty_density"] >= p90

    # ---- budget / truncation
    length = [r for r in rows.values() if r["finish"] == "length"]
    limit = max((r["completion"] for r in length), default=None)
    comps = [r["completion"] for r in rows.values()]
    budget = {
        "limit_est": limit, "n_length": len(length),
        "p50": round(_quantile(comps, .5)), "p95": round(_quantile(comps, .95)),
        "p99": round(_quantile(comps, .99)), "max": max(comps, default=0),
        "suggest": round(_quantile(comps, .99) * 1.15 / 100) * 100,
        "near_limit": sorted(({"page_id": r["page_id"], "completion": r["completion"],
                               "pct": round(100 * r["completion"] / limit)}
                              for r in rows.values() if limit and r["finish"] != "length"
                              and r["completion"] >= 0.8 * limit), key=lambda x: -x["pct"]),
        "by_chapter": Counter(r["chapter"] for r in length),
    }
    if limit:
        for r in rows.values():
            r["budget_pct"] = round(100 * r["completion"] / limit)
    else:
        for r in rows.values():
            r["budget_pct"] = 0

    # ---- section vocabulary coverage; "flat" = long page with no headings or bold lead-ins
    content = [r for r in rows.values() if not r["nomath"] and not r["empty"]]
    template = {
        "coverage": {k: round(100 * sum(k in r["sections"] for r in content) / max(1, len(content)))
                     for k in SECTION_NAMES},
        "flat": [r["page_id"] for r in content if r["summary_tokens"] >= 400 and r["n_leads"] == 0],
        "avg_leads": round(_med(r["n_leads"] for r in content), 1),
    }
    flat = set(template["flat"])
    for r in rows.values():
        r["flat"] = r["page_id"] in flat

    # ---- near-duplicates via shared shingles
    inv = defaultdict(list)
    for pid, sh in shingles.items():
        for h in sh:
            inv[h].append(pid)
    pair = Counter()
    for pids in inv.values():
        if 1 < len(pids) <= 6:
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    pair[(pids[i], pids[j])] += 1
    dups = []
    for (a, b), shared in pair.items():
        j = shared / (len(shingles[a]) + len(shingles[b]) - shared)
        if j >= 0.35:
            dups.append({"a": a, "b": b, "score": round(j, 2)})
    dups.sort(key=lambda d: -d["score"])
    dup_of = defaultdict(list)
    for d in dups:
        dup_of[d["a"]].append(d["b"]); dup_of[d["b"]].append(d["a"])
    for r in rows.values():
        r["dups"] = dup_of.get(r["page_id"], [])

    # ---- dataset tiers: what can go into a knowledge dataset as-is?
    #   excluded = nothing usable; review = usable but something deserves a human look; clean = neither
    for r in rows.values():
        excl = [k for k, on in (("empty", r["empty"]),
                                ("truncated", r["truncated"] or r["finish"] == "length"),
                                ("no content", r["nomath"] and not r["empty"])) if on]
        rev = [k for k, on in (("outlier", r["outlier"] > 0), ("deferred proof", r["gap_count"] > 0),
                               ("source doubt", r["doubt_high"]), ("flat prose", r["flat"])) if on]
        r["reasons"] = excl + rev
        r["tier"] = "excluded" if excl else "review" if rev else "clean"

    # ---- aggregates per chapter and domain
    chapters = []
    for c in ix["chapters"]:
        rs = [rows[p] for p in c["pages"] if p in rows]
        real = [r for r in rs if not r["nomath"] and not r["empty"]]
        n = max(1, len(rs))
        chapters.append({
            "key": c["key"], "label": c["label"], "pages": len(rs), "first": c["first"], "last": c["last"],
            "prompt": sum(r["prompt"] for r in rs), "input_est": sum(r["input_est"] for r in rs),
            "summary_tokens": sum(r["summary_tokens"] for r in rs),
            "reasoning_tokens": sum(r["reasoning_tokens"] for r in rs),
            "mean_summary": round(sum(r["summary_tokens"] for r in rs) / n),
            "mean_reasoning": round(sum(r["reasoning_tokens"] for r in rs) / n),
            "med_think_ratio": round(_med(r["think_ratio"] for r in real), 2),
            "med_compression": round(_med(r["compression"] for r in real if r["compression"]), 3),
            "math_share": round(sum(r["math_share"] for r in real) / max(1, len(real)), 3),
            "truncated": sum(r["truncated"] for r in rs),
            "clean": sum(r["tier"] == "clean" for r in rs), "review": sum(r["tier"] == "review" for r in rs),
            "excluded": sum(r["tier"] == "excluded" for r in rs),
            "cut": sum(1 for r in rs if r["cut_count"]),
            "nomath": sum(r["nomath"] for r in rs),
            **{k: sum(r[k] for r in rs) for k in ("defs", "results", "proofs", "examples", "remarks",
                                                    "display_math", "inline_math", "tables", "list_items", "quotes", "headings")},
        })
    allr = list(rows.values())
    real_all = [r for r in allr if not r["nomath"] and not r["empty"]]
    totals = {
        "pages": len(allr), "prompt": sum(r["prompt"] for r in allr),
        "summary_tokens": sum(r["summary_tokens"] for r in allr),
        "reasoning_tokens": sum(r["reasoning_tokens"] for r in allr),
        "think_ratio": round(sum(r["reasoning_tokens"] for r in allr) / max(1, sum(r["summary_tokens"] for r in allr)), 1),
        "med_think_ratio": round(_med(r["think_ratio"] for r in real_all), 2),
        "med_compression": round(_med(r["compression"] for r in real_all if r["compression"]), 3),
        "cached_pct": round(_med(r["cached_pct"] for r in allr), 1),
        "baseline_prompt": base, "outliers": len({o["page_id"] for o in outliers}),
        "truncated": sum(r["truncated"] for r in allr), "dups": len(dups),
        "gaps": sum(1 for r in allr if r["gap_count"]), "cut": sum(1 for r in allr if r["cut_count"]),
        "undef": sum(1 for r in allr if r["undef_refs"]),
    }
    tiers = {t: sum(r["tier"] == t for r in allr) for t in ("clean", "review", "excluded")}
    tier_tokens = {t: sum(r["summary_tokens"] for r in allr if r["tier"] == t) for t in tiers}
    spent = sum(r["prompt"] + r["completion"] for r in allr)
    totals.update({
        "tiers": tiers, "tier_tokens": tier_tokens, "spent": spent,
        "clean_share": round(100 * tiers["clean"] / max(1, len(allr))),
        "usable_share": round(100 * (tiers["clean"] + tiers["review"]) / max(1, len(allr))),
        "cost_ratio": round(spent / max(1, tier_tokens["clean"]), 1),
        "reasons": dict(Counter(k for r in allr for k in r["reasons"]).most_common()),
    })
    quality = {
        "gaps": sorted((r for r in allr if r["gap_count"]), key=lambda r: -r["gap_count"]),
        "uncertain": sorted((r for r in allr if r["uncertainty"]), key=lambda r: -r["uncertainty_density"])[:30],
        "undef": sorted((r for r in allr if r["undef_refs"]), key=lambda r: -len(r["undef_refs"]))[:30],
    }
    out = {"sig": sig, "rows": rows, "chapters": chapters, "totals": totals, "outliers": outliers,
           "budget": budget, "template": template, "dups": dups, "quality": quality,
           "order": ix["order"]}
    _cache[domain] = out
    return out


def chapter_medians(domain, chapter_key):
    m = build(domain)
    rs = [r for r in m["rows"].values() if r["chapter_key"] == chapter_key and not r["nomath"] and not r["empty"]]
    keys = ("input_est", "summary_tokens", "reasoning_tokens", "think_ratio", "math_share", "display_math",
            "defs", "results", "proofs")
    return {k: _med(r[k] for r in rs) for k in keys}


CSV_COLUMNS = ["page_id", "chapter", "label", "finish", "truncated", "nomath", "prompt", "input_est",
               "cached_pct", "completion", "reasoning_tokens", "summary_tokens", "think_ratio", "compression",
               "budget_pct", "summary_chars", "prose_words", "math_share", "display_math", "inline_math",
               "headings", "defs", "results", "proofs", "examples", "remarks", "tables", "list_items",
               "quotes", "trace_chars", "uncertainty", "self_corrections", "gap_count", "cut_count", "outlier",
               "flat", "relies_earlier"]
