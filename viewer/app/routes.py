import csv
import io

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for

from app import metrics, store, trace as trace_lib, xref

bp = Blueprint("main", __name__)


@bp.app_context_processor
def nav():
    doms = store.domains()
    groups = {}
    for d in doms:
        ix = xref.build(d)
        groups[d] = [{"key": c["key"], "label": c["label"], "count": c["count"],
                      "pages": [{"page_id": pid, "label": ix["meta"][pid]["label"],
                                 "flags": ix["meta"][pid]["flags"]} for pid in c["pages"]]}
                     for c in ix["chapters"]]
    return {"nav_domains": doms, "nav_groups": groups,
            "nav_counts": {d: len(xref.build(d)["order"]) for d in doms}}


@bp.route("/")
def index():
    doms = store.domains()
    cards, heat = {}, {}
    for d in doms:
        ix, m = xref.build(d), metrics.build(d)
        ents = ix["entities"].values()
        top = sorted((e for e in ents if e.kind in xref.LABEL_KINDS and e.defined),
                     key=lambda e: -len(e.used_on))[:6]
        weakest = sorted((c for c in m["chapters"] if c["pages"] >= 5),
                         key=lambda c: c["clean"] / c["pages"])[:3]
        cards[d] = {
            "t": m["totals"], "chapters": ix["chapters"], "mchapters": m["chapters"], "top": top,
            "weakest": weakest, "first": ix["order"][0] if ix["order"] else None,
            "n_results": sum(1 for e in ents if e.kind in ("Theorem", "Lemma", "Proposition", "Corollary", "Claim") and e.defined),
            "n_defs": sum(1 for e in ents if e.kind == "Definition" and e.defined),
            "queue": [(k, v) for k, v in m["totals"]["reasons"].items()],
        }
        heat[d] = {
            "urls": url_for("main.page", domain=d, page_id="__ID__"),
            "chapters": [{"label": c["label"], "pages": c["pages"]} for c in m["chapters"]],
            "pages": [{"id": r["page_id"], "ch": r["chapter"], "tier": r["tier"], "label": r["label"],
                       "think": r["think_ratio"], "math": r["math_share"], "tok": r["summary_tokens"],
                       "why": r["reasons"]} for r in (m["rows"][p] for p in m["order"] if p in m["rows"])],
        }
    overall = {k: sum(c["t"]["tiers"][k] for c in cards.values()) for k in ("clean", "review", "excluded")}
    tokens = {k: sum(c["t"]["tier_tokens"][k] for c in cards.values()) for k in ("clean", "review", "excluded")}
    return render_template("index.html", cards=cards, heat=heat, overall=overall, tokens=tokens,
                           shared=xref.shared_terms(doms) if len(doms) > 1 else [])


@bp.route("/<domain>/<page_id>")
def page(domain, page_id):
    if domain not in store.domains():
        abort(404)
    summary = store.load(domain, page_id)
    if summary is None:
        abort(404)
    words = len(summary.summary.split())
    return render_template("page.html", s=summary, domain=domain, page_id=page_id,
                           words=words, refs=_refs(domain, page_id),
                           xp=_page_xref(domain, page_id), pm=_page_metrics(domain, page_id),
                           tr=_trace(domain, summary),
                           **_position(domain, page_id))


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    domain = request.args.get("domain") or None
    if domain and domain not in store.domains():
        abort(404)
    everything = store.search(q, None, limit=10**6) if q else []
    counts = {}
    for h in everything:
        counts[h["domain"]] = counts.get(h["domain"], 0) + 1
    allhits = [h for h in everything if not domain or h["domain"] == domain]
    return render_template("search.html", q=q, domain=domain, hits=allhits[:100], total=len(allhits),
                           counts=counts, all_domains=store.domains())


@bp.route("/<domain>/random", defaults={"kind": "page"})
@bp.route("/<domain>/random/<kind>")
def random_page(domain, kind):
    import random
    ix = xref.build(domain)
    if kind == "definition":
        pool = [e for e in ix["entities"].values() if e.kind == "Definition" and e.defined]
    elif kind == "result":
        pool = [e for e in ix["entities"].values() if e.kind in ("Theorem", "Lemma", "Proposition", "Corollary") and e.defined]
    elif kind == "page":
        pool = None
    else:
        abort(404)
    if pool:
        e = random.choice(pool)
        return redirect(url_for("main.ref", domain=domain, slug=e.slug))
    ps = ix["order"]
    if not ps:
        abort(404)
    return redirect(url_for("main.page", domain=domain, page_id=random.choice(ps)))


def _refs(domain, page_id):
    """Auto-link table for one page: [{t: text, u: url, term: bool}]."""
    return [{"t": r["t"], "term": r["term"],
             "u": url_for("main.ref", domain=domain, slug=r["s"])}
            for r in xref.page_links(domain, page_id)]


def _page_xref(domain, page_id):
    ix = xref.build(domain)
    info = ix["by_page"].get(page_id, {"defines": [], "mentions": []})
    ents = ix["entities"]
    defines = sorted((ents[s] for s in info["defines"]), key=lambda e: e.sort_key)
    mentions = sorted((ents[s] for s in info["mentions"] if s not in info["defines"]),
                      key=lambda e: e.sort_key)
    return {"defines": defines, "mentions": mentions}


def _check_domain(domain):
    if domain not in store.domains():
        abort(404)


@bp.route("/<domain>/index")
def xref_index(domain):
    _check_domain(domain)
    ents = sorted(xref.build(domain)["entities"].values(), key=lambda e: e.sort_key)
    groups = {}
    for e in ents:
        groups.setdefault(e.kind, []).append(e)
    return render_template("xref_index.html", domain=domain, groups=groups, total=len(ents))


@bp.route("/<domain>/ref/<slug>")
def ref(domain, slug):
    _check_domain(domain)
    e = xref.build(domain)["entities"].get(slug)
    if e is None:
        abort(404)

    def rows(pids):
        out = []
        for pid in pids:
            s = store.load(domain, pid)
            out.append({"page_id": pid, "snippet": xref.snippet(s.summary, e.label) if s else ""})
        return out

    return render_template("xref_ref.html", domain=domain, e=e,
                           defined=rows(e.defined), used=rows(e.used_on))


READ_CHUNK = 20


@bp.route("/<domain>/read", defaults={"chunk": 0})
@bp.route("/<domain>/read/<int:chunk>")
def read(domain, chunk):
    _check_domain(domain)
    ps = store.pages(domain)
    n_chunks = max(1, -(-len(ps) // READ_CHUNK))
    if chunk >= n_chunks:
        abort(404)
    items = []
    for p in ps[chunk * READ_CHUNK:(chunk + 1) * READ_CHUNK]:
        s = store.load(domain, p.page_id)
        if s:
            items.append({"p": p, "s": s, "refs": _refs(domain, p.page_id),
                          "xp": _page_xref(domain, p.page_id)})
    return render_template("reader.html", domain=domain, items=items, chunk=chunk,
                           n_chunks=n_chunks, first=chunk * READ_CHUNK + 1, total=len(ps))


def _page_ref(domain, pid, prefix=False):
    m = xref.build(domain)["meta"][pid]
    text = f"{domain}/{pid}" if prefix else pid
    return {"url": url_for("main.page", domain=domain, page_id=pid), "text": text, "label": m["label"]}


def _position(domain, page_id):
    """Prev/next (continuing across domains), skip links, breadcrumb and scrubber data."""
    ix = xref.build(domain)
    order, meta = ix["order"], ix["meta"]
    i = order.index(page_id) if page_id in order else 0
    doms = store.domains()
    di = doms.index(domain)

    prev_p = _page_ref(domain, order[i - 1]) if i else None
    next_p = _page_ref(domain, order[i + 1]) if i + 1 < len(order) else None
    if prev_p is None and di:
        o = xref.build(doms[di - 1])["order"]
        prev_p = _page_ref(doms[di - 1], o[-1], True) if o else None
    if next_p is None and di + 1 < len(doms):
        o = xref.build(doms[di + 1])["order"]
        next_p = _page_ref(doms[di + 1], o[0], True) if o else None

    def find(pred, rng):
        for j in rng:
            if pred(meta[order[j]]):
                return _page_ref(domain, order[j])

    n = len(order)
    chapters = ix["chapters"]
    ci = next(k for k, c in enumerate(chapters) if c["key"] == meta[page_id]["chapter"])
    ch = chapters[ci]
    prev_ch = ch["first"] if ch["first"] != page_id else (chapters[ci - 1]["first"] if ci else None)
    skips = {
        "prev_def": find(lambda m: m["defines"], range(i - 1, -1, -1)),
        "next_def": find(lambda m: m["defines"], range(i + 1, n)),
        "prev_trunc": find(lambda m: "truncated" in m["flags"], range(i - 1, -1, -1)),
        "next_trunc": find(lambda m: "truncated" in m["flags"], range(i + 1, n)),
        "prev_ch": _page_ref(domain, prev_ch) if prev_ch else None,
        "next_ch": _page_ref(domain, chapters[ci + 1]["first"]) if ci + 1 < len(chapters) else None,
    }
    return {"prev_p": prev_p, "next_p": next_p, "skips": skips, "meta": meta[page_id],
            "pos": i + 1, "total": n}


REASONS = {
    "truncated": "Truncated (output hit the limit)", "empty": "Empty summary",
    "no content": "No content (front/back matter)", "outlier": "Statistical outliers",
    "deferred proof": "Proof deferred or incomplete", "source doubt": "Reasoning trace doubts the source",
    "flat prose": "Flat prose (no structure)",
}


@bp.route("/<domain>/problems")
def problems(domain):
    _check_domain(domain)
    m = metrics.build(domain)
    kind = request.args.get("kind", "")
    kind = {"nomath": "no content"}.get(kind, kind)          # old links
    sections = []
    for k, title in REASONS.items():
        if kind and kind != k:
            continue
        rows = [{"page_id": pid, "chapter_label": r["chapter"], "label": r["label"], "tier": r["tier"]}
                for pid in m["order"] if (r := m["rows"].get(pid)) and k in r["reasons"]]
        if rows or kind:
            sections.append({"kind": k, "title": title, "rows": rows})
    return render_template("problems.html", domain=domain, sections=sections, kind=kind,
                           reasons=REASONS, totals=m["totals"]["reasons"])


@bp.route("/<domain>/dataset.jsonl")
def dataset(domain):
    """Export pages as JSONL. ?tier=clean (default) | review | usable (clean+review) | all"""
    _check_domain(domain)
    import json
    tier = request.args.get("tier", "clean")
    with_trace = request.args.get("reasoning") == "1"
    m = metrics.build(domain)
    allowed = {"clean": {"clean"}, "review": {"review"}, "usable": {"clean", "review"},
               "all": {"clean", "review", "excluded"}}.get(tier)
    if allowed is None:
        abort(400)
    lines = []
    for pid in m["order"]:
        r = m["rows"].get(pid)
        s = store.load(domain, pid)
        if not r or not s or r["tier"] not in allowed:
            continue
        rec = {"domain": domain, "page_id": pid, "chapter": r["chapter"], "label": r["label"],
               "source": s.source, "model": s.model, "tier": r["tier"], "reasons": r["reasons"],
               "summary_tokens": r["summary_tokens"], "text": s.summary}
        if with_trace:
            rec["reasoning"] = s.reasoning
        lines.append(json.dumps(rec, ensure_ascii=False))
    return Response("\n".join(lines) + ("\n" if lines else ""), mimetype="application/x-ndjson",
                    headers={"Content-Disposition": f"attachment; filename={domain}-{tier}.jsonl"})


def _page_metrics(domain, page_id):
    """This page's numbers vs. the median of the content pages in its chapter."""
    m = metrics.build(domain)
    r = m["rows"].get(page_id)
    if not r:
        return None
    med = metrics.chapter_medians(domain, r["chapter_key"])
    bars = []
    for key, title in (("input_est", "input tokens (est.)"), ("summary_tokens", "summary tokens"),
                       ("reasoning_tokens", "thinking tokens")):
        top = max(r[key], med[key], 1) * 1.15
        bars.append({"title": title, "value": r[key], "median": round(med[key]),
                     "pct": round(100 * r[key] / top, 1), "mpct": round(100 * med[key] / top, 1)})
    top = max(r["think_ratio"], med["think_ratio"], 1) * 1.15
    bars.append({"title": "thinking ÷ summary", "value": r["think_ratio"], "median": round(med["think_ratio"], 1),
                 "pct": round(100 * r["think_ratio"] / top, 1), "mpct": round(100 * med["think_ratio"] / top, 1)})
    top = max(r["math_share"], med["math_share"], .05) * 1.15
    bars.append({"title": "math share of text", "value": f"{round(100 * r['math_share'])}%",
                 "median": f"{round(100 * med['math_share'])}%",
                 "pct": round(100 * r["math_share"] / top, 1), "mpct": round(100 * med["math_share"] / top, 1)})
    notes = [o for o in m["outliers"] if o["page_id"] == page_id]
    return {"r": r, "bars": bars, "outliers": notes,
            "budget_limit": m["budget"]["limit_est"]}


@bp.route("/<domain>/analysis")
def analysis(domain):
    _check_domain(domain)
    m = metrics.build(domain)
    cols = ["page_id", "chapter", "label", "input_est", "summary_tokens", "reasoning_tokens", "think_ratio",
            "compression", "budget_pct", "math_share", "display_math", "defs", "results", "proofs", "examples",
            "remarks", "tables", "list_items", "uncertainty", "gap_count", "cut_count", "outlier",
            "truncated", "nomath", "flat"]
    data = {
        "domain": domain,
        "pages": [{c: m["rows"][pid][c] for c in cols} for pid in m["order"] if pid in m["rows"]],
        "chapters": m["chapters"],
        "urls": {"page": url_for("main.page", domain=domain, page_id="__ID__")},
    }
    return render_template("analysis.html", domain=domain, m=m, data=data,
                           rows=m["rows"], qa=m["quality"])


@bp.route("/<domain>/metrics.csv")
def metrics_csv(domain):
    _check_domain(domain)
    m = metrics.build(domain)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(metrics.CSV_COLUMNS)
    for pid in m["order"]:
        r = m["rows"].get(pid)
        if r:
            w.writerow([r.get(c, "") for c in metrics.CSV_COLUMNS])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={domain}-metrics.csv"})


def _trace(domain, summary):
    if not summary.reasoning:
        return None
    t = trace_lib.analyze(summary.reasoning, summary.reasoning_tokens)
    rows = metrics.build(domain)["rows"]
    t["stats"]["domain_median"] = round(
        sorted(r["hedge_per100"] for r in rows.values())[len(rows) // 2], 2) if rows else 0
    return t
