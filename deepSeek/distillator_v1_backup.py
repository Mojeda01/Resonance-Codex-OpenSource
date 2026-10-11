#!/usr/bin/env python3
"""Summarise ../out/pages/*.md via DeepSeek v4-flash into knowledge/algo-game-theory/summaries/."""

import concurrent.futures as cf
import json
import pathlib
import sys
import threading
import time

from openai import OpenAI, APIStatusError, APIConnectionError

HERE = pathlib.Path(__file__).resolve().parent
KEY_FILE = HERE / "key.md"
PAGES = (HERE / ".." / "out" / "pages").resolve()
OUT = HERE / "knowledge" / "advanced_trade" / "summaries"

MODEL = "deepseek-v4-flash"
MAX_RETRIES = 5
MAX_TOKENS = 20000
RETRY_TOKENS = 32000
WORKERS = 5

print_lock = threading.Lock()


def log(msg: str):
    with print_lock:
        print(msg, flush=True)


SYSTEM = """You are a research mathematician writing reference notes on algorithmic game theory.

You are given one page of a technical text. Produce a formal, self-contained summary of its mathematical content.

Requirements:
- Reproduce every definition, theorem, lemma, proposition and corollary on the page, stated precisely, with hypotheses intact. Do not paraphrase a hypothesis away.
- Reproduce every formula in LaTeX. Inline as $...$, displayed as $$...$$. Never describe an equation in words when you can write it.
- Where a proof appears, give the full argument, not a sketch. Preserve the logical structure: what is assumed, what is constructed, where each hypothesis is used, how the contradiction or construction closes. If the page's proof is itself partial or deferred, say so explicitly.
- Preserve the author's notation exactly. If notation is introduced on this page, define it. If notation is used but defined earlier in the text, note that it is assumed.
- State complexity results with their exact model of computation and bound.
- Where an equilibrium concept, mechanism or algorithm is defined, give the formal definition before any intuition.
- Add a short 'remarks' section only for genuine mathematical context: relationship to adjacent results, why a hypothesis is necessary, standard counterexamples.
- Register: formal mathematical prose. No hedging, no pedagogical padding, no restatement of the obvious.
- If the page is front matter, an index, a bibliography or otherwise carries no mathematical content, say so in one sentence and leave the remaining fields empty.

Write in Markdown. Do not wrap the response in a code fence."""

USER_TMPL = """Source file: {name}

Produce the formal summary described in your instructions for the page below.

--- BEGIN PAGE ---
{body}
--- END PAGE ---"""


def load_key() -> str:
    if not KEY_FILE.exists():
        sys.exit(f"missing {KEY_FILE}")
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip().strip("`")
        if line and not line.startswith("#"):
            return line
    sys.exit(f"no key found in {KEY_FILE}")


client = OpenAI(api_key=load_key(), base_url="https://api.deepseek.com")


def summarise(name: str, body: str, max_tokens: int = MAX_TOKENS):
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": USER_TMPL.format(name=name, body=body)},
                ],
                max_tokens=max_tokens,
                temperature=0.2,
                extra_body={"thinking": {"type": "enabled"}},
            )
        except (APIStatusError, APIConnectionError) as e:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** attempt
            log(f"  {name}: retry {attempt + 1}/{MAX_RETRIES} in {wait}s ({e.__class__.__name__})")
            time.sleep(wait)


def process(page: pathlib.Path):
    dest = OUT / f"{page.stem}.json"
    if dest.exists():
        return

    body = page.read_text(encoding="utf-8").strip()
    if not body:
        log(f"{page.name}: empty, skipped")
        return

    try:
        resp = summarise(page.name, body)
        if resp.choices[0].finish_reason == "length":
            log(f"{page.name}: truncated, retrying at {RETRY_TOKENS}")
            resp = summarise(page.name, body, max_tokens=RETRY_TOKENS)
    except Exception as e:
        log(f"{page.name}: FAILED {e.__class__.__name__}: {e}")
        return

    choice = resp.choices[0]
    msg = choice.message

    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {
                "source": str(page.relative_to(HERE.parent)),
                "page_id": page.stem,
                "model": resp.model,
                "summary": msg.content,
                "reasoning": getattr(msg, "reasoning_content", None),
                "usage": resp.usage.model_dump() if resp.usage else None,
                "finish_reason": choice.finish_reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(dest)

    if choice.finish_reason == "length":
        dest.rename(dest.with_suffix(".json.truncated"))
        log(f"{page.name}: still truncated — excluded from corpus")
    else:
        log(f"{page.name}: ok ({resp.usage.completion_tokens:,} completion tokens)")


def main():
    if not PAGES.is_dir():
        sys.exit(f"missing {PAGES}")
    OUT.mkdir(parents=True, exist_ok=True)

    pages = [p for p in sorted(PAGES.glob("*.md")) if not (OUT / f"{p.stem}.json").exists()]
    print(f"{len(pages)} pages to process, {WORKERS} workers")

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(process, p) for p in pages]
        done = 0
        for _ in cf.as_completed(futures):
            done += 1
            if done % 25 == 0:
                log(f"--- {done}/{len(pages)}")

    print("done")


if __name__ == "__main__":
    main()
