#!/usr/bin/env python3
"""Command-line summariser. The web app (Library > Summarise) uses the same core, so
both behave identically; this wrapper just lets you run it from a terminal.

    python distillator.py --pages ../out/pages --domain advanced_trade [--workers 5] [--redo]

The system prompt comes from prompts/<domain>.md (falling back to prompts/_default.md).
The API key is read from key.md.
"""
import argparse
import concurrent.futures as cf
import pathlib
import sys
import threading

import distill_core as core

HERE = pathlib.Path(__file__).resolve().parent
print_lock = threading.Lock()


def log(msg: str):
    with print_lock:
        print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", default=str(HERE.parent / "out" / "pages"), help="directory of OCR pages (*.md)")
    ap.add_argument("--domain", default="advanced_trade", help="knowledge domain to write summaries into")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--redo", action="store_true", help="re-summarise pages that already have a summary (old ones go to history/)")
    a = ap.parse_args()

    pages_dir = pathlib.Path(a.pages).resolve()
    if not pages_dir.is_dir():
        sys.exit(f"missing {pages_dir}")
    key = core.load_key(HERE / "key.md")
    if not key:
        sys.exit(f"no key found in {HERE / 'key.md'}")
    system, src = core.load_prompt(HERE / "prompts", a.domain)
    if not system:
        sys.exit("no prompt found (prompts/<domain>.md or prompts/_default.md)")
    p_hash = core.archive_prompt(HERE / "prompts", a.domain, system)
    print(f"prompt {p_hash} ({src})")

    settings = core.Settings()
    client = core.make_client(key)
    out = HERE / "knowledge" / a.domain / "summaries"
    pages = [p for p in sorted(pages_dir.glob("*.md")) if a.redo or not core.has_summary(out, p.stem)]
    print(f"{len(pages)} pages to process, {a.workers} workers")

    def work(page):
        try:
            rel = str(page.relative_to(HERE.parent))
        except ValueError:
            rel = str(page)
        r = core.process_page(client, system, settings, page, out, source=rel, p_hash=p_hash, redo=a.redo, log=log)
        s = r["status"]
        if s == "ok":
            log(f"{page.name}: ok ({r['completion_tokens']:,} completion tokens)")
        elif s == "truncated":
            log(f"{page.name}: still truncated — excluded from corpus")
        elif s == "failed":
            log(f"{page.name}: FAILED {r['error']}")
        elif s == "empty":
            log(f"{page.name}: empty, skipped")

    with cf.ThreadPoolExecutor(max_workers=a.workers) as pool:
        done = 0
        for _ in cf.as_completed([pool.submit(work, p) for p in pages]):
            done += 1
            if done % 25 == 0:
                log(f"--- {done}/{len(pages)}")
    print("done")


if __name__ == "__main__":
    main()
