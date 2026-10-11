import json
import pathlib
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE / "knowledge"

totals = defaultdict(lambda: defaultdict(int))

for f in sorted(ROOT.rglob("summaries/*.json")):
    domain = f.parent.parent.name
    d = json.loads(f.read_text(encoding="utf-8"))
    u = d.get("usage") or {}
    t = totals[domain]
    t["files"] += 1
    t["prompt"] += u.get("prompt_tokens", 0)
    t["completion"] += u.get("completion_tokens", 0)
    t["total"] += u.get("total_tokens", 0)
    t["cached"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    t["reasoning"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    t["summary_chars"] += len(d.get("summary") or "")

g = defaultdict(int)
for domain, t in sorted(totals.items()):
    print(f"\n{domain}")
    print(f"  files            {t['files']:>12,}")
    print(f"  prompt tokens    {t['prompt']:>12,}  (cached {t['cached']:,})")
    print(f"  completion       {t['completion']:>12,}")
    print(f"  of which thinking{t['reasoning']:>12,}")
    print(f"  total billed     {t['total']:>12,}")
    corpus = t["completion"] - t["reasoning"]
    print(f"  corpus tokens    {corpus:>12,}  ({t['summary_chars']:,} chars)")
    for k, v in t.items():
        g[k] += v

if len(totals) > 1:
    print(f"\nALL DOMAINS")
    print(f"  files            {g['files']:>12,}")
    print(f"  total billed     {g['total']:>12,}")
    print(f"  corpus tokens    {g['completion'] - g['reasoning']:>12,}")
