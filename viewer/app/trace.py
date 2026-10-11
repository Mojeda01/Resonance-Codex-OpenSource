"""Analysis of one reasoning trace: per-paragraph hedging heat and a minimal loop check.
Rule-based on purpose (transparent, no dependencies). The patterns are shared with the
browser, which uses them to highlight words inside the rendered text."""
import re
from collections import defaultdict

# Words of uncertainty about what the answer should be.
HEDGE = (r"\b(?:likely|unlikely|probably|perhaps|maybe|possibly|presumably|apparently|seems?|might|"
         r"could be|may be|not sure|i think|i guess|guess|uncertain|unclear|ambiguous|not certain|"
         r"hard to (?:tell|say|read))\b")
# Words of doubt about the source text itself (OCR damage etc.).
SOURCE = (r"\b(?:garbled|ocr|mangled|scrambled|misprint|typo|illegible|unreadable|corrupt\w*|"
          r"odd|strange|weird|incorrect|missing|truncated|cut off)\b")
# The model changing its mind.
REVISE = r"\b(?:wait|hmm+|actually|on second thought|but wait|hold on|let me re\w*)\b"

_RX = {k: re.compile(v, re.I) for k, v in (("hedge", HEDGE), ("source", SOURCE), ("revise", REVISE))}
LEXICON = {"hedge": HEDGE, "source": SOURCE, "revise": REVISE}

WORD = re.compile(r"[a-z0-9']+")
# Math-ish tokens and equation references the model keeps coming back to.
FOCUS = re.compile(r"\([0-9]+(?:\.[0-9]+)*[a-z']?\)|[A-Za-z\\]*[\^_]\{[^}\s]{1,40}\}|[A-Za-z]+\^[A-Za-z0-9]+|\$[^$\n]{2,40}\$")
HEAT_CAP = 8.0           # uncertainty words per 100 words that count as "fully hot"
MIN_PARA = 120           # merge paragraphs shorter than this into their neighbour (chars)
SHINGLE = 4


def _paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) < 4 and len(text) > 1500:      # one long block: cut into ~700-char chunks on sentences
        sents = re.split(r"(?<=[.!?])\s+", text.strip())
        paras, cur = [], ""
        for s in sents:
            cur = (cur + " " + s).strip()
            if len(cur) >= 700:
                paras.append(cur); cur = ""
        if cur:
            paras.append(cur)
    merged: list[str] = []
    for p in paras:                               # fold tiny fragments ("Let's write.") into the previous one
        if merged and len(p) < MIN_PARA:
            merged[-1] += "\n\n" + p
        else:
            merged.append(p)
    return merged


def _shingles(words):
    return {hash(" ".join(words[i:i + SHINGLE])) for i in range(max(0, len(words) - SHINGLE + 1))}


def analyze(text: str, reasoning_tokens: int = 0) -> dict:
    paras = _paragraphs(text)
    total_chars = max(1, sum(len(p) for p in paras))
    out = []
    sh, inv = [], defaultdict(list)
    for i, p in enumerate(paras):
        words = WORD.findall(p.lower())
        counts = {k: len(rx.findall(p)) for k, rx in _RX.items()}
        n = max(1, len(words))
        dens = 100 * (counts["hedge"] + counts["source"]) / n
        s = _shingles(words)
        sh.append(s)
        # containment against every earlier paragraph, via the inverted index
        hits = defaultdict(int)
        for h in s:
            for j in set(inv[h]):
                hits[j] += 1
        best_j, best = None, 0.0
        for j, shared in hits.items():
            c = shared / max(1, min(len(s), len(sh[j])))
            if c > best:
                best_j, best = j, c
        for h in s:
            inv[h].append(i)
        repeat = len(s) >= 12 and best >= 0.3
        out.append({
            "i": i + 1, "text": p, "chars": len(p), "words": len(words),
            "tok": round(reasoning_tokens * len(p) / total_chars) if reasoning_tokens else 0,
            "hedge": counts["hedge"], "source": counts["source"], "revise": counts["revise"],
            "density": round(dens, 2), "heat": round(min(1.0, dens / HEAT_CAP), 3),
            "repeat_of": best_j + 1 if repeat else None, "repeat_score": round(best, 2) if repeat else 0,
        })

    # topics revisited: math-ish tokens that recur across several paragraphs
    where = defaultdict(set)
    for q in out:
        for m in FOCUS.findall(q["text"]):
            where[m.strip(".,;:")].add(q["i"])
    topics = sorted(({"token": t, "n": len(ps), "paras": sorted(ps)}
                     for t, ps in where.items() if len(ps) >= 3 and len(t) >= 4),
                    key=lambda x: (-x["n"], x["token"]))[:8]

    n_rep = sum(1 for q in out if q["repeat_of"])
    share = n_rep / max(1, len(out))
    words_all = max(1, sum(q["words"] for q in out))
    tot = {k: sum(q[k] for q in out) for k in ("hedge", "source", "revise")}
    return {
        "paragraphs": out, "topics": topics, "lexicon": LEXICON,
        "stats": {
            "paragraphs": len(out), "words": words_all, "tokens": reasoning_tokens,
            **tot, "per100": round(100 * (tot["hedge"] + tot["source"]) / words_all, 2),
            "revise_per100": round(100 * tot["revise"] / words_all, 2),
            "repeats": n_rep, "repeat_share": round(100 * share),
            "loop": "high" if share > 0.25 else "moderate" if share > 0.1 else "low",
            "hottest": max(out, key=lambda q: q["density"])["i"] if out else None,
        },
    }


def hedge_density(text: str) -> float:
    """Uncertainty words per 100 words, for the domain-wide comparison in metrics.py."""
    words = max(1, len(text.split()))
    low = text.lower()
    return 100 * (len(_RX["hedge"].findall(low)) + len(_RX["source"].findall(low))) / words
