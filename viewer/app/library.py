"""Library: source PDFs, Marker OCR output, and the OCR job runner.

The runner deliberately keeps run.py's design: ONE page at a time, each in a fresh
`convert_page.py` subprocess, so Marker's models are loaded and then released for every
page. That is what keeps memory use low on a modest machine. Nothing here keeps a model
resident, and no job ever runs more than one conversion at once.
"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from flask import current_app

PAGE_FILE = re.compile(r"^p(\d{5})\.md$")
SAFE_IMAGE = re.compile(r"^[\w.\-]+\.(?:jpe?g|png|webp|gif)$", re.I)
MODES = {"default": [], "force_ocr": ["--force-ocr"], "text_layer": ["--no-ocr"]}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"


@dataclass
class Book:
    slug: str
    title: str
    pdf: Path
    domain: str | None        # knowledge domain holding this book's summaries, if any
    out: Path                 # `<out>/pages/pNNNNN.md`, images next to `pages/`
    legacy: bool = False      # out/ itself, from before books got their own folder

    @property
    def pages_dir(self) -> Path:
        return self.out / "pages"


# ---- registry ----------------------------------------------------------------------------

def books() -> list[Book]:
    cfg = current_app.config
    material, out_root = Path(cfg["MATERIAL_DIR"]), Path(cfg["OUT_DIR"])
    declared = {}
    lib = Path(cfg["LIBRARY_FILE"])
    if lib.is_file():
        try:
            for b in json.loads(lib.read_text(encoding="utf-8")).get("books", []):
                declared[b["pdf"]] = b
        except (ValueError, KeyError):
            pass
    found = []
    pdfs = sorted(material.glob("*.pdf")) if material.is_dir() else []
    for pdf in pdfs:
        d = declared.get(pdf.name, {})
        slug = d.get("slug") or _slug(pdf.stem)
        legacy = bool(d.get("legacy_out"))
        found.append(Book(slug=slug, title=d.get("title") or pdf.stem.replace("_", " "), pdf=pdf,
                          domain=d.get("domain"), out=out_root if legacy else out_root / slug,
                          legacy=legacy))
    return found


def get_book(slug: str) -> Book | None:
    return next((b for b in books() if b.slug == slug), None)


# ---- page counts & status ------------------------------------------------------------------

_pdf_lock = threading.Lock()        # pdfium is not thread-safe
_count_cache: dict = {}


def page_count(book: Book) -> int:
    key = (str(book.pdf), book.pdf.stat().st_mtime_ns)
    if key not in _count_cache:
        with _pdf_lock:
            doc = pdfium.PdfDocument(str(book.pdf))
            _count_cache[key] = len(doc)
            doc.close()
    return _count_cache[key]


def done_pages(book: Book) -> set[int]:
    if not book.pages_dir.is_dir():
        return set()
    return {int(m[1]) for f in book.pages_dir.iterdir() if (m := PAGE_FILE.match(f.name))}


def summarised_pages(book: Book) -> set[int]:
    if not book.domain:
        return set()
    from app import store
    d = store.root() / book.domain / "summaries"
    if not d.is_dir():
        return set()
    out = set()
    for f in d.iterdir():
        m = re.match(r"^p(\d{5})\.json", f.name)
        if m:
            out.add(int(m[1]))
    return out


def overview(book: Book) -> dict:
    total = page_count(book)
    done, summ = done_pages(book), summarised_pages(book)
    return {"book": book, "total": total, "done": len(done), "summarised": len(summ)}


# ---- rendering the original page ------------------------------------------------------------

def render_page(book: Book, n: int, scale: float = 1.6) -> Path | None:
    """PNG of PDF page n (cached under out/.cache). Returns None if out of range."""
    if not 0 <= n < page_count(book):
        return None
    cache = Path(current_app.config["OUT_DIR"]) / ".cache" / "render" / book.slug / f"p{n:05d}@{scale}.png"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with _pdf_lock:
            doc = pdfium.PdfDocument(str(book.pdf))
            try:
                doc[n].render(scale=scale).to_pil().save(cache)
            finally:
                doc.close()
    return cache


def read_markdown(book: Book, n: int) -> str | None:
    f = book.pages_dir / f"p{n:05d}.md"
    return f.read_text(encoding="utf-8") if f.is_file() else None


def rewrite_image_links(md: str, slug: str) -> str:
    """Marker writes `(pNNNNN_name.jpeg)`; point those at the image route."""
    def sub(m):
        name = m[1]
        return f"](/library/{slug}/image/{name})" if SAFE_IMAGE.match(name) else m[0]
    return re.sub(r"\]\(([^)\s/]+)\)", sub, md)


# ---- the OCR job runner ---------------------------------------------------------------------

def _external_ocr_pids() -> list[int]:
    """A Python interpreter already running convert_page.py (e.g. started by hand with run.py).
    Only real interpreters count, not an editor or shell that merely mentions the file."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me, found = os.getpid(), []
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        try:
            argv = shlex.split(cmd)
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == me or not argv or not os.path.basename(argv[0]).lower().startswith("python"):
            continue
        if any(os.path.basename(a) == "convert_page.py" for a in argv[1:3]):
            found.append(pid)
    return found


class OcrManager:
    """At most one job, one page at a time. Safe to poll from request threads."""

    def __init__(self):
        self.lock = threading.RLock()
        self.job: dict | None = None
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None

    # -- status ---------------------------------------------------------------------------
    def status(self, state_file: Path | None = None) -> dict:
        with self.lock:
            if self.job:
                j = self.job
                return {k: (list(v) if isinstance(v, deque) else v) for k, v in j.items() if k != "pages"} | {
                    "remaining": len(j["pages"]) - j["index"], "total_pages": len(j["pages"]),
                    "active": j["status"] in ("running", "stopping")}
        if state_file and state_file.is_file():          # last job from before a restart
            try:
                j = json.loads(state_file.read_text(encoding="utf-8"))
                if j.get("status") in ("running", "stopping"):
                    j["status"] = "interrupted"
                j["active"] = False
                return j
            except ValueError:
                pass
        return {"status": "idle", "active": False}

    # -- control --------------------------------------------------------------------------
    def start(self, book: Book, pages: list[int], *, redo: bool, pause_s: float, mode: str,
              timeout_s: int, state_file: Path, script: Path) -> tuple[bool, str]:
        with self.lock:
            if self.job and self.job["status"] in ("running", "stopping"):
                return False, "an OCR job is already running"
            others = _external_ocr_pids()
            if others:
                return False, f"another convert_page.py is running (pid {others[0]}); not starting a second one"
            if not pages:
                return False, "nothing to do: every selected page already has OCR output"
            self.job = {
                "id": int(time.time()), "slug": book.slug, "status": "running", "pages": pages, "index": 0,
                "done": 0, "skipped": 0, "failed": [], "current": None, "redo": redo, "mode": mode,
                "pause_s": pause_s, "started": time.time(), "finished": None, "page_started": None,
                "durations": [], "log": deque(maxlen=60), "stop": False,
            }
            self.thread = threading.Thread(target=self._run, args=(self.job, book, timeout_s, state_file, script),
                                           daemon=True)
            self.thread.start()
        return True, f"started {len(pages)} page(s)"

    def stop(self) -> bool:
        """Finish the current page, then stop."""
        with self.lock:
            if not self.job or self.job["status"] != "running":
                return False
            self.job["stop"] = True
            self.job["status"] = "stopping"
            return True

    def cancel(self) -> bool:
        """Stop now: terminate the page being converted."""
        with self.lock:
            if not self.job or self.job["status"] not in ("running", "stopping"):
                return False
            self.job["stop"] = True
            self.job["status"] = "stopping"
            self.job["cancelled"] = True
            proc = self.proc
        if proc and proc.poll() is None:
            self._kill(proc)
        return True

    @staticmethod
    def _kill(proc: subprocess.Popen):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    # -- worker ---------------------------------------------------------------------------
    def _save(self, job: dict, state_file: Path):
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            snap = {k: (list(v) if isinstance(v, deque) else v) for k, v in job.items() if k != "pages"}
            state_file.write_text(json.dumps(snap), encoding="utf-8")
        except OSError:
            pass

    def _log(self, job: dict, msg: str):
        job["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")

    def _run(self, job: dict, book: Book, timeout_s: int, state_file: Path, script: Path):
        flags = MODES.get(job["mode"], [])
        book.pages_dir.mkdir(parents=True, exist_ok=True)
        self._log(job, f"start {book.slug}: {len(job['pages'])} page(s), mode={job['mode']}")
        for i, n in enumerate(job["pages"]):
            if job["stop"]:
                break
            with self.lock:
                job["index"] = i
            dst = book.pages_dir / f"p{n:05d}.md"
            backup = None
            if dst.exists():
                if not job["redo"]:
                    job["skipped"] += 1
                    continue
                backup = dst.with_name(dst.name + ".prev")      # keep the old output until the new one lands
                os.replace(dst, backup)
            job["current"], job["page_started"] = n, time.time()
            self._log(job, f"p{n:05d}: converting")
            self._save(job, state_file)
            ok, tail = self._convert(book, n, flags, timeout_s, script)
            elapsed = time.time() - job["page_started"]
            if ok and dst.exists():
                job["done"] += 1
                job["durations"].append(round(elapsed, 1))
                self._log(job, f"p{n:05d}: done in {elapsed:.0f}s")
            else:
                if backup and backup.exists() and not dst.exists():
                    os.replace(backup, dst)                      # put the previous output back
                job["failed"].append(n)
                self._log(job, f"p{n:05d}: FAILED after {elapsed:.0f}s")
                for line in tail[-6:]:
                    self._log(job, "   " + line[:160])
            self._save(job, state_file)
            waited = 0.0
            while waited < job["pause_s"] and not job["stop"]:   # optional cool-down between pages
                time.sleep(0.25); waited += 0.25
        with self.lock:
            job["index"] = len(job["pages"]) if not job["stop"] else job["index"]
            job["status"] = "cancelled" if job.get("cancelled") else "stopped" if job["stop"] else "finished"
            job["current"], job["finished"] = None, time.time()
            self.proc = None
        self._log(job, f"{job['status']}: {job['done']} done, {len(job['failed'])} failed")
        self._save(job, state_file)

    def _convert(self, book: Book, n: int, flags: list[str], timeout_s: int, script: Path) -> tuple[bool, list[str]]:
        cmd = [sys.executable, str(script), str(book.pdf), str(n), str(book.out), *flags]
        try:
            proc = subprocess.Popen(cmd, cwd=str(script.parent), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True)
        except OSError as e:
            return False, [str(e)]
        with self.lock:
            self.proc = proc
        try:
            out, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            out = "timed out after %ds" % timeout_s
        with self.lock:
            self.proc = None
        lines = [l for l in (out or "").splitlines() if l.strip()]
        return proc.returncode == 0, lines


manager = OcrManager()
