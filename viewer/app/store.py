import json
import pathlib
from dataclasses import dataclass, field

from flask import current_app


@dataclass
class Page:
    page_id: str
    domain: str
    truncated: bool = False


@dataclass
class Summary:
    page_id: str
    domain: str
    source: str
    model: str
    summary: str
    reasoning: str | None
    usage: dict = field(default_factory=dict)
    truncated: bool = False
    finish_reason: str = ""

    @property
    def completion_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def reasoning_tokens(self) -> int:
        return (self.usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)

    @property
    def corpus_tokens(self) -> int:
        return self.completion_tokens - self.reasoning_tokens


def root() -> pathlib.Path:
    return current_app.config["KNOWLEDGE_DIR"]


def domains() -> list[str]:
    r = root()
    if not r.is_dir():
        return []
    return sorted(d.name for d in r.iterdir() if (d / "summaries").is_dir())


def pages(domain: str) -> list[Page]:
    d = root() / domain / "summaries"
    if not d.is_dir():
        return []
    out = [Page(f.stem, domain) for f in d.glob("*.json")]
    out += [Page(f.name.removesuffix(".json.truncated"), domain, True)
            for f in d.glob("*.json.truncated")]
    return sorted(out, key=lambda p: p.page_id)


def load(domain: str, page_id: str) -> Summary | None:
    base = root() / domain / "summaries"
    for name, trunc in ((f"{page_id}.json", False), (f"{page_id}.json.truncated", True)):
        path = base / name
        if not path.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        return Summary(
            page_id=d.get("page_id", page_id),
            domain=domain,
            source=d.get("source", ""),
            model=d.get("model", ""),
            summary=d.get("summary") or "",
            reasoning=d.get("reasoning"),
            usage=d.get("usage") or {},
            truncated=trunc,
            finish_reason=d.get("finish_reason") or "",
        )
    return None


def stats(domain: str) -> dict:
    ps = pages(domain)
    totals = {"files": len(ps), "truncated": sum(p.truncated for p in ps),
              "corpus": 0, "thinking": 0, "prompt": 0}
    for p in ps:
        s = load(domain, p.page_id)
        if not s:
            continue
        totals["corpus"] += s.corpus_tokens
        totals["thinking"] += s.reasoning_tokens
        totals["prompt"] += s.usage.get("prompt_tokens", 0)
    return totals


def neighbors(domain: str, page_id: str) -> tuple[Page | None, Page | None]:
    ps = pages(domain)
    for i, p in enumerate(ps):
        if p.page_id == page_id:
            return (ps[i - 1] if i else None, ps[i + 1] if i + 1 < len(ps) else None)
    return None, None


def search(query: str, domain: str | None = None, limit: int = 100) -> list[dict]:
    """Case-insensitive AND-of-terms search over summaries; returns ranked hits with snippets."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    hits = []
    for d in ([domain] if domain else domains()):
        for p in pages(d):
            s = load(d, p.page_id)
            if not s:
                continue
            text = s.summary
            low = text.lower()
            if not all(t in low or t in p.page_id.lower() for t in terms):
                continue
            count = sum(low.count(t) for t in terms) + 5 * sum(t in p.page_id.lower() for t in terms)
            i = max(0, min((low.find(t) for t in terms if t in low), default=0) - 80)
            snippet = " ".join(text[i:i + 240].split())
            hits.append({"domain": d, "page_id": p.page_id, "truncated": p.truncated,
                         "count": count, "snippet": snippet, "terms": terms})
    hits.sort(key=lambda h: -h["count"])
    return hits[:limit]
