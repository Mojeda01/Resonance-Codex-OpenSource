"""Web-app side of DeepSeek summarisation: job runner, cost estimate, flagged pages, test streaming.
The API call itself lives in deepSeek/distill_core.py (shared with distillator.py)."""
import json
import re
import statistics
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path

from flask import current_app

from app import library, metrics


def core():
    """distill_core lives next to distillator.py (not a package), so import it by path."""
    d = str(Path(current_app.config["DEEPSEEK_DIR"]))
    if d not in sys.path:
        sys.path.insert(0, d)
    import distill_core
    return distill_core


def effective_domain(book) -> str:
    return book.domain or book.slug


def summaries_dir(book) -> Path:
    from app import store
    return store.root() / effective_domain(book) / "summaries"


def key_found() -> bool:
    return core().load_key(Path(current_app.config["DEEPSEEK_KEY_FILE"])) is not None


def source_rel(book, n: int, base: Path | None = None) -> str:
    """Page path relative to the project root. Pass `base` when called from a worker thread
    (there is no Flask context there)."""
    base = base or Path(current_app.config["BASE_DIR"])
    p = book.pages_dir / f"p{n:05d}.md"
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


# ---- which pages --------------------------------------------------------------------------------

def page_index(name: str) -> int | None:
    m = re.fullmatch(r"p(\d{5})\.md", name)
    return int(m[1]) if m else None


def candidates(book, select: str, start=0, end=10**9, last_failed=(), redo=False) -> list[int]:
    """OCR'd pages with non-empty text that match the selection (missing | range | failed)."""
    c = core()
    sd = summaries_dir(book)
    out = []
    for f in sorted(book.pages_dir.glob("p*.md")):
        n = page_index(f.name)
        if n is None:
            continue
        if select == "range" and not start <= n <= end:
            continue
        if select == "failed" and n not in last_failed:
            continue
        if not redo and c.has_summary(sd, f.stem):
            continue
        if not nonempty(book, n):
            continue
        out.append(n)
    return out


def nonempty(book, n: int) -> bool:
    f = book.pages_dir / f"p{n:05d}.md"
    try:
        return bool(f.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def flagged(book) -> dict:
    """Review-queue reasons -> OCR'd pages in this book that have them."""
    domain = effective_domain(book)
    try:
        rows = metrics.build(domain)["rows"]
    except Exception:
        return {}
    have = library.done_pages(book)
    out: dict[str, list[int]] = {}
    for pid, r in rows.items():
        n = int(pid[1:])
        if n not in have:
            continue
        for reason in r["reasons"]:
            if reason != "no content":
                out.setdefault(reason, []).append(n)
    return {k: sorted(v) for k, v in sorted(out.items())}


# ---- estimate (tokens only; it never blocks anything) ----------------------------------------------

def estimate(book, pages: list[int], system_text: str) -> dict:
    chars = 0
    for n in pages:
        try:
            chars += len((book.pages_dir / f"p{n:05d}.md").read_text(encoding="utf-8"))
        except OSError:
            pass
    per_system = len(system_text) / 3.6 + 40
    prompt = chars / 3.3 + len(pages) * per_system
    comps, basis = [], "default"
    domain = effective_domain(book)
    try:
        rows = list(metrics.build(domain)["rows"].values())
        comps = [r["completion"] for r in rows if r["completion"]]
        basis = "this book's earlier summaries"
    except Exception:
        pass
    if len(comps) < 20:
        from app import store
        for d in store.domains():
            comps += [r["completion"] for r in metrics.build(d)["rows"].values() if r["completion"]]
        basis = "summaries of all books" if comps else "default"
    if not comps:
        comps = [7000] * 5 + [12000]
    comps.sort()
    p50 = comps[len(comps) // 2]
    p90 = comps[int(len(comps) * 0.9)]
    n = len(pages)
    return {"pages": n, "prompt_tokens": round(prompt), "completion_p50": p50 * n, "completion_p90": p90 * n,
            "per_page_p50": p50, "per_page_p90": p90, "basis": basis}


# ---- job runner ---------------------------------------------------------------------------------------

class DistillManager:
    """One job at a time; N worker threads each handling one page (an API call) at a time."""

    def __init__(self):
        self.lock = threading.RLock()
        self.job: dict | None = None

    def status(self, state_file: Path | None = None) -> dict:
        with self.lock:
            if self.job:
                j = self.job
                snap = {k: (list(v) if isinstance(v, deque) else v) for k, v in j.items()
                        if k not in ("pages", "stop", "next")}
                snap["in_flight"] = {str(k): round(time.time() - v, 1) for k, v in j["in_flight"].items()}
                snap["active"] = j["status"] in ("running", "stopping")
                snap["remaining"] = len(j["pages"]) - j["next"]
                return snap
        if state_file and state_file.is_file():
            try:
                j = json.loads(state_file.read_text(encoding="utf-8"))
                if j.get("status") in ("running", "stopping"):
                    j["status"] = "interrupted"
                j["active"] = False
                return j
            except ValueError:
                pass
        return {"status": "idle", "active": False}

    def start(self, book, pages, *, redo, workers, settings, system, p_hash, state_file, key) -> tuple[bool, str]:
        c = core()
        with self.lock:
            if self.job and self.job["status"] in ("running", "stopping"):
                return False, "a summarise job is already running"
            if not pages:
                return False, "nothing to do: no matching pages"
            self.job = {
                "id": int(time.time()), "slug": book.slug, "domain": effective_domain(book), "status": "running",
                "pages": pages, "next": 0, "total_pages": len(pages), "workers": workers, "redo": redo,
                "done": 0, "truncated": [], "failed": {}, "skipped": 0, "empty": 0, "in_flight": {},
                "prompt_tokens": 0, "completion_tokens": 0, "durations": [], "prompt_hash": p_hash,
                "max_tokens": settings.max_tokens, "started": time.time(), "finished": None,
                "log": deque(maxlen=80), "stop": False,
            }
            job = self.job
            client = c.make_client(key)
            sd = summaries_dir(book)
            base = Path(current_app.config["BASE_DIR"])
            threads = [threading.Thread(target=self._worker, args=(job, book, client, c, settings, system, p_hash, sd, state_file, base),
                                        daemon=True) for _ in range(workers)]
            for t in threads:
                t.start()
            threading.Thread(target=self._finish, args=(job, threads, state_file), daemon=True).start()
        self._log(job, f"start {book.slug} -> {job['domain']}: {len(pages)} page(s), {workers} worker(s), prompt {p_hash}")
        return True, f"started {len(pages)} page(s)"

    def stop(self) -> bool:
        """Let in-flight pages finish (an API call can't be recalled), then stop."""
        with self.lock:
            if not self.job or self.job["status"] != "running":
                return False
            self.job["stop"] = True
            self.job["status"] = "stopping"
            return True

    def _log(self, job, msg):
        job["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")

    def _save(self, job, state_file):
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            snap = {k: (list(v) if isinstance(v, deque) else v) for k, v in job.items()
                    if k not in ("pages", "stop", "in_flight")}
            snap["total_pages"] = len(job["pages"])
            state_file.write_text(json.dumps(snap), encoding="utf-8")
        except OSError:
            pass

    def _worker(self, job, book, client, c, settings, system, p_hash, sd, state_file, base):
        while True:
            with self.lock:
                if job["stop"] or job["next"] >= len(job["pages"]):
                    return
                n = job["pages"][job["next"]]
                job["next"] += 1
                job["in_flight"][n] = time.time()
            t0 = time.time()
            page = book.pages_dir / f"p{n:05d}.md"
            try:
                r = c.process_page(client, system, settings, page, sd, source=source_rel(book, n, base), p_hash=p_hash,
                                   redo=job["redo"], max_tokens=job["max_tokens"],
                                   log=lambda m: self._log(job, m))
            except Exception as e:                       # noqa: BLE001
                r = {"status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:200]}"}
            dt = time.time() - t0
            with self.lock:
                job["in_flight"].pop(n, None)
                s = r["status"]
                if s in ("ok", "truncated"):
                    job["done"] += 1
                    job["prompt_tokens"] += r.get("prompt_tokens", 0)
                    job["completion_tokens"] += r.get("completion_tokens", 0)
                    job["durations"].append(round(dt, 1))
                    if s == "truncated":
                        job["truncated"].append(n)
                    self._log(job, f"p{n:05d}: {s} in {dt:.0f}s ({r.get('completion_tokens', 0):,} completion tokens)")
                elif s == "failed":
                    job["failed"][str(n)] = r["error"]
                    self._log(job, f"p{n:05d}: FAILED {r['error']}")
                elif s == "empty":
                    job["empty"] += 1
                    self._log(job, f"p{n:05d}: empty OCR text, skipped")
                else:
                    job["skipped"] += 1
            self._save(job, state_file)

    def _finish(self, job, threads, state_file):
        for t in threads:
            t.join()
        with self.lock:
            job["status"] = "stopped" if job["stop"] else "finished"
            job["finished"] = time.time()
        self._log(job, f"{job['status']}: {job['done']} done, {len(job['failed'])} failed, {len(job['truncated'])} truncated")
        self._save(job, state_file)


manager = DistillManager()


# ---- test lab: results of streamed single-page runs, held until saved ----------------------------------

_tests: "OrderedDict[str, dict]" = OrderedDict()
_tests_lock = threading.Lock()


def remember_test(entry: dict) -> str:
    tid = uuid.uuid4().hex[:12]
    with _tests_lock:
        _tests[tid] = entry
        while len(_tests) > 6:
            _tests.popitem(last=False)
    return tid


def recall_test(tid: str) -> dict | None:
    with _tests_lock:
        return _tests.get(tid)
