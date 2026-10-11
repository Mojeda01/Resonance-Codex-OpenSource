"""Cross-page structure: numbered results ("Theorem 18.29"), named definitions
("**Definition (graphical game).**") and where each is defined / mentioned."""
import os
import re
from dataclasses import dataclass, field

from app import store

KINDS = ("Theorem|Lemma|Proposition|Corollary|Definition|Claim|Example|Algorithm|"
         "Assumption|Conjecture|Problem|Exercise|Section|Chapter|Figure|Table")
NUMBERED = re.compile(rf"(?<![\w])({KINDS})\s+(\d+(?:\.\d+)*)(?![\w.]*\d)")
# Bold label that introduces a result, optionally numbered and/or named.
LABEL = re.compile(rf"\*\*({KINDS})(?:\s+(\d+(?:\.\d+)*))?\s*(?:\(([^()\n]{{3,60}})\))?\.?\*\*")
# A numbered label opening a line: "Theorem 3.1.", "Theorem 3.1 (Name)", "### Lemma 2"
LINE_DEF = re.compile(rf"^[ \t>#-]*(?:\*\*)?({KINDS})\s+(\d+(?:\.\d+)*)(?:\*\*)?[ \t]*(?:[.:(]|$)", re.M)
KIND_ORDER = ["Theorem", "Lemma", "Proposition", "Corollary", "Definition", "Claim", "Algorithm",
              "Example", "Assumption", "Conjecture", "Problem", "Exercise", "Section", "Chapter",
              "Figure", "Table"]


@dataclass
class Entity:
    slug: str
    kind: str
    label: str                  # "Theorem 18.29" or the term itself
    name: str = ""              # optional name of a numbered result
    numbered: bool = True
    defined: list = field(default_factory=list)
    mentioned: list = field(default_factory=list)

    @property
    def used_on(self):
        return [p for p in self.mentioned if p not in self.defined]

    @property
    def sort_key(self):
        nums = tuple(int(x) for x in re.findall(r"\d+", self.label)) if self.numbered else ()
        return (KIND_ORDER.index(self.kind), nums, self.label.lower())


def _slug(s):
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


def _add(lst, v):
    if v not in lst:
        lst.append(v)


ABBR = {"Theorem": "Thm", "Lemma": "Lem", "Proposition": "Prop", "Corollary": "Cor",
        "Definition": "Def", "Example": "Ex", "Algorithm": "Alg", "Assumption": "Asm",
        "Conjecture": "Conj", "Exercise": "Ex.", "Section": "§", "Figure": "Fig", "Table": "Tab",
        "Chapter": "Ch", "Claim": "Claim", "Problem": "Prob"}
# Kinds that make a good one-line description of a page, best first.
LABEL_KINDS = ["Theorem", "Proposition", "Lemma", "Corollary", "Definition", "Claim",
               "Algorithm", "Conjecture", "Assumption", "Example", "Problem"]
NOMATH = re.compile(r"no (?:formal |new )?(?:mathematical|mathematics)|contains no (?:math|formal)", re.I)
NOMATH_KINDS = [("table of contents", "table of contents"), ("front matter", "front matter"),
                ("dedication", "dedication"), ("copyright", "copyright"), ("preface", "preface"),
                ("bibliograph", "bibliography"), ("references", "references"), ("index", "index"),
                ("acknowledg", "acknowledgments"), ("blank", "blank page"), ("exercise", "exercises")]


BORING_HEAD = re.compile(
    r"formal summary|scope|notation|status|remark|setting|statement|assumption|proof|"
    r"definitions?\b|stated|summary|overview|content|conclusion|result|^the page|this page|"
    r"^\W*$", re.I)


def short(e: Entity) -> str:
    if e.numbered:
        base = f"{ABBR.get(e.kind, e.kind)} {e.label.split(' ', 1)[1]}"
        return f"{base} · {e.name}" if e.name else base
    return f"{ABBR.get(e.kind, e.kind)} · {e.label}"


def _page_label(info, ents, text):
    defs = [ents[s] for s in info["defines"]]
    for kind in LABEL_KINDS:
        cands = [e for e in defs if e.kind == kind]
        if cands:
            named = [e for e in cands if e.name or not e.numbered]
            return short(sorted(named or cands, key=lambda e: e.sort_key)[0])
    head = text[:500].lower()
    if NOMATH.search(head):
        for kw, name in NOMATH_KINDS:
            if kw in head:
                return name
        return "no math content"
    if defs:
        return short(sorted(defs, key=lambda e: e.sort_key)[0])
    for h in re.findall(r"^#{1,4}[ \t]+(.+?)[ \t]*$", text, re.M):
        h = re.sub(r"[*_`$\\]", "", h)
        if not BORING_HEAD.search(h):
            return h[:60]
    return ""


def _chapters(order, by_page, ents):
    """Assign each page a chapter: best non-decreasing path through per-page votes
    (the leading number of dotted references like Theorem 18.29 / Section 2.5.1)."""
    votes = []
    for pid in order:
        v: dict[int, int] = {}
        info = by_page[pid]
        for key, w in (("defines", 4), ("mentions", 1)):
            for slug in info[key]:
                e = ents[slug]
                m = re.search(r"\b(\d+)\.\d+", e.label) if e.numbered and e.kind != "Chapter" else None
                if m and (key == "defines" or slug not in info["defines"]):
                    c = int(m[1]); v[c] = v.get(c, 0) + w
        votes.append(v)
    ks = sorted({c for v in votes for c in v})
    if len(ks) < 3:
        return None
    n, K = len(order), len(ks)
    dp = [[0] * K for _ in range(n)]
    back = [[0] * K for _ in range(n)]
    for i in range(n):
        best, arg = 0, 0
        for k in range(K):
            if i:
                if dp[i - 1][k] > best:
                    best, arg = dp[i - 1][k], k
            dp[i][k] = votes[i].get(ks[k], 0) + (best if i else 0)
            back[i][k] = arg
    k = max(range(K), key=lambda j: (dp[-1][j], -j))
    path = [0] * n
    for i in range(n - 1, -1, -1):
        path[i] = ks[k]
        k = back[i][k]
    return path


_cache: dict = {}


def _signature(domain):
    d = store.root() / domain / "summaries"
    n, m = 0, 0
    for e in os.scandir(d):
        n += 1
        m = max(m, e.stat().st_mtime_ns)
    return n, m


def build(domain: str) -> dict:
    sig = _signature(domain)
    hit = _cache.get(domain)
    if hit and hit["sig"] == sig:
        return hit
    ents: dict[str, Entity] = {}
    by_page: dict[str, dict] = {}
    texts: dict[str, str] = {}
    page_info: dict[str, dict] = {}
    totals = {"files": 0, "truncated": 0, "corpus": 0, "thinking": 0, "prompt": 0}

    def ent(slug, kind, label, numbered, name=""):
        e = ents.get(slug)
        if not e:
            e = ents[slug] = Entity(slug, kind, label, name, numbered)
        elif name and not e.name:
            e.name = name
        return e

    for p in store.pages(domain):
        s = store.load(domain, p.page_id)
        if not s:
            continue
        text, pid = s.summary, p.page_id
        texts[pid] = text
        page_info[pid] = {"truncated": p.truncated, "empty": not text.strip(),
                          "nomath": bool(NOMATH.search(text[:600]))}
        totals["files"] += 1
        totals["truncated"] += p.truncated
        totals["corpus"] += s.corpus_tokens
        totals["thinking"] += s.reasoning_tokens
        totals["prompt"] += s.usage.get("prompt_tokens", 0)
        info = by_page[pid] = {"defines": [], "mentions": []}
        for m in NUMBERED.finditer(text):
            e = ent(_slug(f"{m[1]} {m[2]}"), m[1], f"{m[1]} {m[2]}", True)
            _add(e.mentioned, pid); _add(info["mentions"], e.slug)
        for m in LINE_DEF.finditer(text):
            e = ent(_slug(f"{m[1]} {m[2]}"), m[1], f"{m[1]} {m[2]}", True)
            _add(e.defined, pid); _add(info["defines"], e.slug)
        for m in LABEL.finditer(text):
            kind, num, name = m[1], m[2], (m[3] or "").strip()
            if num:
                e = ent(_slug(f"{kind} {num}"), kind, f"{kind} {num}", True, name)
            elif name and not re.search(r"\b(page|footnote|text)\b", name, re.I):
                e = ent("term-" + _slug(name), kind, name, False)
            else:
                continue
            _add(e.defined, pid); _add(e.mentioned, pid)
            _add(info["defines"], e.slug); _add(info["mentions"], e.slug)

    # Named terms are "used" wherever the phrase occurs in running text.
    for e in ents.values():
        if e.numbered or len(e.label) < 4 or re.search(r"[$\\*]", e.label):
            continue
        rx = re.compile(rf"(?<!\w){re.escape(e.label)}(?!\w)", re.I)
        for pid, text in texts.items():
            if rx.search(text):
                _add(e.mentioned, pid); _add(by_page[pid]["mentions"], e.slug)

    order = sorted(by_page)
    path = _chapters(order, by_page, ents)
    meta, chapters = {}, []
    for i, pid in enumerate(order):
        pi = page_info[pid]
        if path:
            key, label = f"ch{path[i]}", f"Ch. {path[i]}"
        else:
            b = i // 25
            key = f"b{b}"
            label = f"{order[b * 25]}–{order[min(len(order), b * 25 + 25) - 1]}"
        flags = [f for f in ("truncated", "empty", "nomath") if pi[f]]
        meta[pid] = {"label": _page_label(by_page[pid], ents, texts[pid]), "chapter": key,
                     "chapter_label": label, "flags": flags,
                     "defines": bool(by_page[pid]["defines"])}
        if not chapters or chapters[-1]["key"] != key:
            chapters.append({"key": key, "label": label, "first": pid, "pages": []})
        chapters[-1]["pages"].append(pid)
    for c in chapters:
        c["count"] = len(c["pages"])
        c["last"] = c["pages"][-1]
    out = {"sig": sig, "entities": ents, "by_page": by_page, "order": order,
           "texts_lower": {k: v.lower() for k, v in texts.items()},
           "meta": meta, "chapters": chapters, "stats": totals}
    _cache[domain] = out
    return out


def page_links(domain: str, page_id: str) -> list[dict]:
    """Link targets for the client-side auto-linker on one page (url resolved by caller)."""
    ix = build(domain)
    res = []
    for slug in ix["by_page"].get(page_id, {}).get("mentions", []):
        e = ix["entities"][slug]
        if not e.numbered and page_id in e.defined:
            continue                 # don't link a term at its own definition
        if e.numbered and not (e.defined or len(e.mentioned) > 1):
            continue                 # a one-off mention has nothing to link to
        res.append({"t": e.label, "s": slug, "term": not e.numbered})
    return res


def snippet(text: str, label: str, width=110) -> str:
    m = re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.I)
    i = m.start() if m else 0
    a = max(0, i - width)
    return ("…" if a else "") + " ".join(text[a:i + width + len(label)].split()) + "…"


_shared_cache: dict = {}


def shared_terms(domains: list[str], limit: int = 24) -> list[dict]:
    """Named concepts defined in one domain that also appear in another's text."""
    builds = {d: build(d) for d in domains}
    key = tuple((d, builds[d]["sig"]) for d in domains)
    if key in _shared_cache:
        return _shared_cache[key][:limit]
    terms = {}
    for d, ix in builds.items():
        for e in ix["entities"].values():
            if e.numbered or not e.defined or len(e.label) < 5 or re.search(r"[$\\*]", e.label):
                continue
            terms.setdefault(e.label.lower(), {}).setdefault(d, e)
    out = []
    for term, defs in terms.items():
        rx = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
        per = {}
        for d, ix in builds.items():
            pages = [pid for pid, t in ix["texts_lower"].items() if term in t and rx.search(t)]
            if pages:
                per[d] = pages
        multiword = len(term.split()) >= 2
        # Single generic words ("sequence", "feasible") only count when both books define them.
        if len(per) >= 2 and (multiword or len(defs) >= 2) and not re.search(r"unnumbered|informal|as stated|proof|deferred|page", term):
            out.append({"term": term, "defined_in": {d: e.slug for d, e in defs.items()},
                        "pages": {d: v[:6] for d, v in per.items()}, "counts": {d: len(v) for d, v in per.items()},
                        "total": sum(len(v) for v in per.values())})
    out.sort(key=lambda x: (-len(x["defined_in"]), -x["total"], x["term"]))
    _shared_cache[key] = out
    return out[:limit]
