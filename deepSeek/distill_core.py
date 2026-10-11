"""DeepSeek summarisation core, shared by the web app and the `distillator.py` command line.

Importing this module has NO side effects: no key is read, no client is created, nothing exits.
The API key is only ever read from a file by `load_key`, and is never logged or returned to a UI.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"

USER_TMPL = """Source file: {name}

Produce the formal summary described in your instructions for the page below.

--- BEGIN PAGE ---
{body}
--- END PAGE ---"""


@dataclass
class Settings:
    model: str = MODEL
    temperature: float = 0.2
    max_tokens: int = 20000
    retry_tokens: int = 32000      # one more attempt when a page is cut off by the length limit
    max_retries: int = 5           # API errors, exponential backoff
    thinking: bool = True

    def as_record(self, max_tokens: int | None = None) -> dict:
        return {"temperature": self.temperature, "max_tokens": max_tokens or self.max_tokens,
                "thinking": self.thinking}


# ---- key ---------------------------------------------------------------------------------------

def load_key(path: pathlib.Path) -> str | None:
    """First non-comment line of the key file (backticks stripped), or None. Never logged."""
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip().strip("`")
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return None


def make_client(key: str):
    from openai import OpenAI          # imported lazily so the web app starts without it
    return OpenAI(api_key=key, base_url=BASE_URL)


# ---- prompts (one file per domain, with history) -------------------------------------------------

def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:10]


def _safe(domain: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", domain)


def prompt_file(prompts_dir: pathlib.Path, domain: str) -> pathlib.Path:
    return pathlib.Path(prompts_dir) / f"{_safe(domain)}.md"


def history_dir(prompts_dir: pathlib.Path, domain: str) -> pathlib.Path:
    return pathlib.Path(prompts_dir) / f"{_safe(domain)}.history"


def load_prompt(prompts_dir: pathlib.Path, domain: str) -> tuple[str, str]:
    """(text, source) where source is 'domain' or 'default'."""
    f = prompt_file(prompts_dir, domain)
    if f.is_file():
        return f.read_text(encoding="utf-8").strip(), "domain"
    d = pathlib.Path(prompts_dir) / "_default.md"
    return (d.read_text(encoding="utf-8").strip() if d.is_file() else ""), "default"


def archive_prompt(prompts_dir: pathlib.Path, domain: str, text: str) -> str:
    """Remember a prompt text under its hash so any summary can be traced back to it."""
    h = prompt_hash(text)
    hd = history_dir(prompts_dir, domain)
    hd.mkdir(parents=True, exist_ok=True)
    f = hd / f"{h}.md"
    if not f.exists():
        f.write_text(text.strip() + "\n", encoding="utf-8")
    return h


def save_prompt(prompts_dir: pathlib.Path, domain: str, text: str) -> str:
    text = text.strip()
    pathlib.Path(prompts_dir).mkdir(parents=True, exist_ok=True)
    current, _ = load_prompt(prompts_dir, domain)
    if current:
        archive_prompt(prompts_dir, domain, current)       # keep what we are replacing
    h = archive_prompt(prompts_dir, domain, text)
    prompt_file(prompts_dir, domain).write_text(text + "\n", encoding="utf-8")
    return h


def prompt_versions(prompts_dir: pathlib.Path, domain: str) -> list[dict]:
    hd = history_dir(prompts_dir, domain)
    if not hd.is_dir():
        return []
    cur = prompt_hash(load_prompt(prompts_dir, domain)[0])
    rows = [{"hash": f.stem, "saved": f.stat().st_mtime, "current": f.stem == cur,
             "preview": f.read_text(encoding="utf-8")[:90].replace("\n", " ")} for f in hd.glob("*.md")]
    return sorted(rows, key=lambda r: -r["saved"])


def read_prompt_version(prompts_dir: pathlib.Path, domain: str, h: str) -> str | None:
    if not re.fullmatch(r"[0-9a-f]{10}", h):
        return None
    f = history_dir(prompts_dir, domain) / f"{h}.md"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else None


# ---- the API call ---------------------------------------------------------------------------------

def _messages(system: str, name: str, body: str) -> list[dict]:
    return [{"role": "system", "content": system},
            {"role": "user", "content": USER_TMPL.format(name=name, body=body)}]


def _kwargs(settings: Settings, max_tokens: int | None) -> dict:
    kw = dict(model=settings.model, max_tokens=max_tokens or settings.max_tokens,
              temperature=settings.temperature)
    if settings.thinking:
        kw["extra_body"] = {"thinking": {"type": "enabled"}}
    return kw


def summarise(client, system: str, name: str, body: str, settings: Settings,
              max_tokens: int | None = None, on_retry: Callable[[int, int, Exception], None] | None = None):
    """One non-streaming call with retries on API/connection errors (same policy as before)."""
    from openai import APIConnectionError, APIStatusError
    for attempt in range(settings.max_retries):
        try:
            return client.chat.completions.create(messages=_messages(system, name, body),
                                                  **_kwargs(settings, max_tokens))
        except (APIStatusError, APIConnectionError) as e:
            if attempt == settings.max_retries - 1:
                raise
            if on_retry:
                on_retry(attempt + 1, 2 ** attempt, e)
            time.sleep(2 ** attempt)


def stream_summary(client, system: str, name: str, body: str, settings: Settings,
                   max_tokens: int | None = None) -> Iterator[dict]:
    """Streaming call. Yields {"type": "reasoning"|"content", "text"} deltas, then one
    {"type": "done", "record": {...}} with the assembled result. Closing the generator
    closes the HTTP stream, which stops generation."""
    stream = client.chat.completions.create(
        messages=_messages(system, name, body), stream=True,
        stream_options={"include_usage": True}, **_kwargs(settings, max_tokens))
    reasoning, content, usage, finish, model = [], [], None, None, settings.model
    try:
        for chunk in stream:
            model = getattr(chunk, "model", None) or model
            if getattr(chunk, "usage", None):
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            ch = chunk.choices[0]
            delta = ch.delta
            r = getattr(delta, "reasoning_content", None)
            if r:
                reasoning.append(r)
                yield {"type": "reasoning", "text": r}
            if delta.content:
                content.append(delta.content)
                yield {"type": "content", "text": delta.content}
            if ch.finish_reason:
                finish = ch.finish_reason
    finally:
        try:
            stream.close()
        except Exception:
            pass
    yield {"type": "done", "record": {
        "model": model, "summary": "".join(content), "reasoning": "".join(reasoning) or None,
        "usage": usage, "finish_reason": finish}}


# ---- saving, with history -------------------------------------------------------------------------

def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%dT%H%M%S")


def existing_summary_files(summ_dir: pathlib.Path, stem: str) -> list[pathlib.Path]:
    return [p for p in (summ_dir / f"{stem}.json", summ_dir / f"{stem}.json.truncated") if p.exists()]


def has_summary(summ_dir: pathlib.Path, stem: str) -> bool:
    return bool(existing_summary_files(summ_dir, stem))


def archive_existing(summ_dir: pathlib.Path, stem: str) -> list[pathlib.Path]:
    """Move any current summary of this page into summaries/history/ (never delete it)."""
    moved = []
    for p in existing_summary_files(summ_dir, stem):
        hd = summ_dir / "history"
        hd.mkdir(parents=True, exist_ok=True)
        tag = ".truncated" if p.name.endswith(".truncated") else ""
        dest = hd / f"{stem}.{_stamp()}{tag}.json"
        n = 1
        while dest.exists():
            dest = hd / f"{stem}.{_stamp()}-{n}{tag}.json"; n += 1
        p.replace(dest)
        moved.append(dest)
    return moved


def history_versions(summ_dir: pathlib.Path, stem: str) -> list[dict]:
    hd = summ_dir / "history"
    if not hd.is_dir():
        return []
    rows = []
    for f in sorted(hd.glob(f"{stem}.*.json"), reverse=True):
        rows.append({"name": f.name, "truncated": ".truncated." in f.name, "saved": f.stat().st_mtime})
    return rows


def save_summary(summ_dir: pathlib.Path, stem: str, record: dict, *, source: str, settings: Settings,
                 max_tokens: int, p_hash: str, archive: bool = True) -> pathlib.Path:
    """Atomic write. The previous summary (if any) moves to history/ first."""
    summ_dir.mkdir(parents=True, exist_ok=True)
    if archive:
        archive_existing(summ_dir, stem)
    truncated = record.get("finish_reason") == "length"
    dest = summ_dir / f"{stem}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "source": source, "page_id": stem, "model": record.get("model") or settings.model,
        "summary": record.get("summary"), "reasoning": record.get("reasoning"),
        "usage": record.get("usage"), "finish_reason": record.get("finish_reason"),
        # provenance: how this summary was produced
        "prompt_hash": p_hash, **settings.as_record(max_tokens),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    if truncated:
        final = dest.with_suffix(".json.truncated")
        dest.replace(final)
        return final
    return dest


def process_page(client, system: str, settings: Settings, page: pathlib.Path, summ_dir: pathlib.Path, *,
                 source: str, p_hash: str, redo: bool = False, max_tokens: int | None = None,
                 retry_tokens: int | None = None, log: Callable[[str], None] = lambda m: None) -> dict:
    """Summarise one OCR page. Returns {"status": ok|truncated|empty|skipped|failed, ...}."""
    stem = page.stem
    if has_summary(summ_dir, stem) and not redo:
        return {"status": "skipped"}
    body = page.read_text(encoding="utf-8").strip()
    if not body:
        return {"status": "empty"}
    used = max_tokens or settings.max_tokens
    try:
        def retry(i, wait, e):
            log(f"{page.name}: retry {i}/{settings.max_retries} in {wait}s ({e.__class__.__name__})")
        resp = summarise(client, system, page.name, body, settings, used, retry)
        if resp.choices[0].finish_reason == "length":
            used = retry_tokens or settings.retry_tokens
            log(f"{page.name}: truncated, retrying at {used}")
            resp = summarise(client, system, page.name, body, settings, used, retry)
    except Exception as e:                                   # noqa: BLE001 - reported, page stays un-summarised
        return {"status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:300]}"}
    ch = resp.choices[0]
    record = {"model": resp.model, "summary": ch.message.content,
              "reasoning": getattr(ch.message, "reasoning_content", None),
              "usage": resp.usage.model_dump() if resp.usage else None, "finish_reason": ch.finish_reason}
    save_summary(summ_dir, stem, record, source=source, settings=settings, max_tokens=used, p_hash=p_hash)
    usage = record["usage"] or {}
    return {"status": "truncated" if ch.finish_reason == "length" else "ok",
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)}
