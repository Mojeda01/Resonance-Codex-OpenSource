
import json
import pathlib
import sys
import time

from openai import OpenAI, APIStatusError, APIConnectionError

HERE = pathlib.Path(__file__).resolve().parent
KEY_FILE = HERE / "key.md"
PAGES = (HERE / ".." / "out" / "pages").resolve()
OUT = HERE / "knowledge" / "algo-game-theory" / "summaries"

MODEL = "deepseek-v4-flash"
MAX_RETRIES = 5
MAX_TOKENS = 8192

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
            print(f"  retry {attempt + 1}/{MAX_RETRIES} after {wait}s ({e.__class__.__name__})")
            time.sleep(wait)

def main():
    if not PAGES.is_dir():
        sys.exit(f"missing {PAGES}")
    OUT.mkdir(parents=True, exist_ok=True)

    pages = sorted(PAGES.glob("*.md"))
    print(f"{len(pages)} pages in {PAGES}")

    for i, page in enumerate(pages, 1):
        dest = OUT / f"{page.stem}.json"
        if dest.exists():
            continue

        body = page.read_text(encoding="utf-8").strip()
        if not body:
            print(f"[{i}/{len(pages)}] {page.name}: empty, skipped")
            continue

        print(f"[{i}/{len(pages)}] {page.name}", flush=True)
        resp = summarise(page.name, body)
        if resp.choices[0].finish_reason == "length":
            print("  truncated, retrying at 16384", flush=True)
            resp = summarise(page.name, body, max_tokens=16384)
        msg = resp.choices[0].message

        dest.write_text(
            json.dumps(
                {
                    "source": str(page.relative_to(HERE.parent)),
                    "page_id": page.stem,
                    "model": resp.model,
                    "summary": msg.content,
                    "reasoning": getattr(msg, "reasoning_content", None),
                    "usage": resp.usage.model_dump() if resp.usage else None,
                    "finish_reason": resp.choices[0].finish_reason,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if resp.choices[0].finish_reason == "length":
            dest.rename(dest.with_suffix(".json.truncated"))
            print("  still truncated — saved as .truncated, excluded from corpus")

if __name__ == "__main__":
    main()
