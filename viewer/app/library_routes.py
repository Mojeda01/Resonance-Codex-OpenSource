"""Library pages: browse source PDFs, view the original page next to its OCR text and
summary, and run/monitor Marker OCR from the browser."""
from pathlib import Path

from flask import (Blueprint, abort, current_app, jsonify, render_template, request,
                   send_file, send_from_directory, url_for)

from app import library, store

lib_bp = Blueprint("library", __name__, url_prefix="/library")

LOCAL = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _book_or_404(slug):
    book = library.get_book(slug)
    if book is None:
        abort(404)
    return book


def _guard():
    """Anything that starts or stops processes: this machine only, and not forgeable
    by another website (a custom header can't be sent cross-origin without CORS)."""
    if request.remote_addr not in LOCAL:
        abort(403)
    if request.headers.get("X-RKP") != "1":
        abort(403)


def _state_file() -> Path:
    return Path(current_app.config["OUT_DIR"]) / ".ocr_job.json"


# ---- pages -------------------------------------------------------------------------------

@lib_bp.route("/")
def index():
    rows = []
    for b in library.books():
        try:
            rows.append(library.overview(b))
        except Exception as e:            # unreadable PDF: show it, flagged, instead of failing the page
            rows.append({"book": b, "total": 0, "done": 0, "summarised": 0, "error": str(e)})
    return render_template("library.html", rows=rows, job=library.manager.status(_state_file()))


@lib_bp.route("/<slug>")
def book(slug):
    b = _book_or_404(slug)
    total = library.page_count(b)
    done = sorted(library.done_pages(b))
    summ = sorted(library.summarised_pages(b))
    data = {"slug": slug, "total": total, "done": done, "summarised": summ,
            "urls": {"page": url_for("library.page", slug=slug, n=0).rsplit("/", 1)[0] + "/__N__"}}
    return render_template("library_book.html", b=b, total=total, ndone=len(done), nsumm=len(summ), data=data,
                           job=library.manager.status(_state_file()))


@lib_bp.route("/<slug>/page/<int:n>")
def page(slug, n):
    b = _book_or_404(slug)
    total = library.page_count(b)
    if not 0 <= n < total:
        abort(404)
    pid = f"p{n:05d}"
    md = library.read_markdown(b, n)
    summary = store.load(b.domain, pid) if b.domain else None
    payload = {
        "slug": slug, "n": n, "pid": pid, "total": total,
        "ocr": library.rewrite_image_links(md, slug) if md is not None else None,
        "summary": summary.summary if summary else None,
    }
    return render_template(
        "library_page.html", b=b, n=n, pid=pid, total=total, has_ocr=md is not None, payload=payload,
        summary=summary, prev_n=n - 1 if n else None, next_n=n + 1 if n + 1 < total else None,
        summary_url=(url_for("main.page", domain=b.domain, page_id=pid) if summary else None),
        job=library.manager.status(_state_file()))


@lib_bp.route("/<slug>/render/<int:n>.png")
def render(slug, n):
    b = _book_or_404(slug)
    path = library.render_page(b, n)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=3600)


@lib_bp.route("/<slug>/image/<name>")
def image(slug, name):
    b = _book_or_404(slug)
    if not library.SAFE_IMAGE.match(name):
        abort(404)
    return send_from_directory(b.out, name, max_age=3600)


# ---- job API -----------------------------------------------------------------------------

@lib_bp.route("/api/job")
def api_job():
    return jsonify(library.manager.status(_state_file()))


@lib_bp.route("/api/<slug>/done")
def api_done(slug):
    b = _book_or_404(slug)
    return jsonify({"done": sorted(library.done_pages(b))})


@lib_bp.route("/<slug>/ocr/start", methods=["POST"])
def ocr_start(slug):
    _guard()
    b = _book_or_404(slug)
    body = request.get_json(silent=True) or {}
    total = library.page_count(b)
    done = library.done_pages(b)
    redo = bool(body.get("redo"))
    which = body.get("select", "missing")
    try:
        if which == "missing":
            pages = [i for i in range(total) if i not in done]
        elif which == "range":
            a, z = int(body.get("start", 0)), int(body.get("end", total - 1))
            pages = [i for i in range(max(0, a), min(total - 1, z) + 1) if redo or i not in done]
        elif which == "pages":
            pages = sorted({int(i) for i in body.get("pages", []) if 0 <= int(i) < total})
        else:
            abort(400)
        cap = int(body.get("max_pages") or 0)
        pause = min(300.0, max(0.0, float(body.get("pause_s") or 0)))
        timeout = min(3600, max(60, int(body.get("timeout_s") or 900)))
    except (TypeError, ValueError):
        abort(400)
    if cap > 0:
        pages = pages[:cap]
    mode = body.get("mode", "default")
    if mode not in library.MODES:
        abort(400)
    ok, msg = library.manager.start(
        b, pages, redo=redo, pause_s=pause, mode=mode, timeout_s=timeout,
        state_file=_state_file(), script=Path(current_app.config["CONVERT_SCRIPT"]))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@lib_bp.route("/ocr/stop", methods=["POST"])
def ocr_stop():
    _guard()
    return jsonify({"ok": library.manager.stop()})


@lib_bp.route("/ocr/cancel", methods=["POST"])
def ocr_cancel():
    _guard()
    return jsonify({"ok": library.manager.cancel()})


# ======================================================================================
# DeepSeek summarisation: page, prompt, estimate, job control, streaming test, versions
# ======================================================================================
import json as _json
import re as _re
from flask import Response, stream_with_context

from app import summarise as sm


def _sfile() -> Path:
    return Path(current_app.config["OUT_DIR"]) / ".summarise_job.json"


def _prompts() -> Path:
    return Path(current_app.config["PROMPTS_DIR"])


def _prompt_info(b):
    c = sm.core()
    dom = sm.effective_domain(b)
    text, src = c.load_prompt(_prompts(), dom)
    return {"text": text, "hash": c.prompt_hash(text) if text else None, "source": src, "domain": dom,
            "versions": c.prompt_versions(_prompts(), dom)}


@lib_bp.route("/<slug>/summarise")
def summarise_page(slug):
    b = _book_or_404(slug)
    done = library.done_pages(b)
    info = _prompt_info(b)
    return render_template(
        "library_summarise.html", b=b, domain=None, dom=sm.effective_domain(b), key_ok=sm.key_found(),
        ndone=len(done), nsumm=len(library.summarised_pages(b)), total=library.page_count(b), prompt=info,
        prefill_n=request.args.get("n", type=int), job=sm.manager.status(_sfile()),
        settings=sm.core().Settings())


@lib_bp.route("/api/summarise/job")
def api_sjob():
    return jsonify(sm.manager.status(_sfile()))


@lib_bp.route("/api/<slug>/prompt")
def api_prompt(slug):
    return jsonify(_prompt_info(_book_or_404(slug)))


@lib_bp.route("/<slug>/prompt", methods=["POST"])
def save_prompt(slug):
    _guard()
    b = _book_or_404(slug)
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if len(text) < 20:
        abort(400)
    h = sm.core().save_prompt(_prompts(), sm.effective_domain(b), text)
    return jsonify({"ok": True, "hash": h, **{k: v for k, v in _prompt_info(b).items() if k == "versions"}})


@lib_bp.route("/api/<slug>/prompt/<h>")
def api_prompt_version(slug, h):
    b = _book_or_404(slug)
    text = sm.core().read_prompt_version(_prompts(), sm.effective_domain(b), h)
    if text is None:
        abort(404)
    return jsonify({"hash": h, "text": text})


@lib_bp.route("/api/<slug>/flagged")
def api_flagged(slug):
    return jsonify({"reasons": sm.flagged(_book_or_404(slug))})


def _selection(b, args, redo):
    """Pages for a start/estimate request."""
    select = args.get("select", "missing")
    if select == "pages":
        pages = sorted({int(i) for i in args.get("pages", []) if sm.nonempty(b, int(i))})
        return pages
    last = sm.manager.status(_sfile())
    failed = [int(k) for k in (last.get("failed") or {})] if isinstance(last.get("failed"), dict) else []
    return sm.candidates(b, select, int(args.get("start", 0)), int(args.get("end", 10**9)), failed, redo)


@lib_bp.route("/api/<slug>/estimate", methods=["POST"])
def api_estimate(slug):
    b = _book_or_404(slug)
    body = request.get_json(silent=True) or {}
    try:
        pages = _selection(b, body, bool(body.get("redo")))
    except (TypeError, ValueError):
        abort(400)
    text = (body.get("prompt") or _prompt_info(b)["text"])
    return jsonify(sm.estimate(b, pages, text))


@lib_bp.route("/<slug>/summarise/start", methods=["POST"])
def summarise_start(slug):
    _guard()
    b = _book_or_404(slug)
    body = request.get_json(silent=True) or {}
    c = sm.core()
    key = c.load_key(Path(current_app.config["DEEPSEEK_KEY_FILE"]))
    if not key:
        return jsonify({"ok": False, "message": "no API key found in deepSeek/key.md"}), 400
    info = _prompt_info(b)
    if not info["text"]:
        return jsonify({"ok": False, "message": "this book has no prompt yet"}), 400
    try:
        redo = bool(body.get("redo"))
        pages = _selection(b, body, redo)
        workers = min(8, max(1, int(body.get("workers") or 5)))
        max_tokens = min(64000, max(1000, int(body.get("max_tokens") or 20000)))
        cap = int(body.get("max_pages") or 0)
    except (TypeError, ValueError):
        abort(400)
    if cap > 0:
        pages = pages[:cap]
    settings = c.Settings(max_tokens=max_tokens)
    settings.retry_tokens = max(settings.retry_tokens, max_tokens)
    h = c.archive_prompt(_prompts(), sm.effective_domain(b), info["text"])
    ok, msg = sm.manager.start(b, pages, redo=redo, workers=workers, settings=settings, system=info["text"],
                               p_hash=h, state_file=_sfile(), key=key)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@lib_bp.route("/summarise/stop", methods=["POST"])
def summarise_stop():
    _guard()
    return jsonify({"ok": sm.manager.stop()})


# ---- streamed single-page test ---------------------------------------------------------

def _sse(obj) -> str:
    return "data: " + _json.dumps(obj, ensure_ascii=False) + "\n\n"


@lib_bp.route("/<slug>/summarise/test", methods=["POST"])
def summarise_test(slug):
    _guard()
    b = _book_or_404(slug)
    body = request.get_json(silent=True) or {}
    c = sm.core()
    key = c.load_key(Path(current_app.config["DEEPSEEK_KEY_FILE"]))
    if not key:
        return jsonify({"ok": False, "message": "no API key found in deepSeek/key.md"}), 400
    try:
        n = int(body.get("n"))
        max_tokens = min(64000, max(1000, int(body.get("max_tokens") or 20000)))
    except (TypeError, ValueError):
        abort(400)
    page_text = library.read_markdown(b, n)
    if not page_text or not page_text.strip():
        return jsonify({"ok": False, "message": f"p{n:05d} has no OCR text"}), 400
    system = (body.get("prompt") or "").strip() or _prompt_info(b)["text"]
    if not system:
        return jsonify({"ok": False, "message": "no prompt"}), 400
    settings = c.Settings(max_tokens=max_tokens)
    p_hash = c.archive_prompt(_prompts(), sm.effective_domain(b), system)
    client = c.make_client(key)
    name = f"p{n:05d}.md"

    @stream_with_context
    def gen():
        yield _sse({"type": "start", "n": n, "prompt_hash": p_hash, "max_tokens": max_tokens})
        stream = c.stream_summary(client, system, name, page_text.strip(), settings, max_tokens)
        try:
            for ev in stream:
                if ev["type"] == "done":
                    rec = ev["record"]
                    tid = sm.remember_test({"slug": slug, "n": n, "record": rec, "prompt_hash": p_hash,
                                            "max_tokens": max_tokens})
                    yield _sse({"type": "done", "id": tid, "finish_reason": rec["finish_reason"],
                                "usage": rec["usage"], "model": rec["model"]})
                else:
                    yield _sse(ev)
        except GeneratorExit:
            stream.close()                       # client stopped: close the HTTP stream to stop generation
            raise
        except Exception as e:                   # noqa: BLE001
            yield _sse({"type": "error", "message": f"{e.__class__.__name__}: {str(e)[:300]}"})
        finally:
            stream.close()

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@lib_bp.route("/<slug>/summarise/test/save", methods=["POST"])
def summarise_test_save(slug):
    _guard()
    b = _book_or_404(slug)
    t = sm.recall_test((request.get_json(silent=True) or {}).get("id", ""))
    if not t or t["slug"] != slug:
        return jsonify({"ok": False, "message": "that test result is no longer available; run it again"}), 404
    c = sm.core()
    path = c.save_summary(sm.summaries_dir(b), f"p{t['n']:05d}", t["record"], source=sm.source_rel(b, t["n"]),
                          settings=c.Settings(), max_tokens=t["max_tokens"], p_hash=t["prompt_hash"])
    return jsonify({"ok": True, "file": path.name})


# ---- summary versions (current + history) for the viewer -------------------------------------

_HIST = _re.compile(r"^p\d{5}\.[0-9T-]+(?:\.truncated)?\.json$")


@lib_bp.route("/<slug>/summary/<int:n>")
def summary_versions(slug, n):
    b = _book_or_404(slug)
    c = sm.core()
    sd = sm.summaries_dir(b)
    stem = f"p{n:05d}"
    v = request.args.get("v")
    if v == "current":
        files = c.existing_summary_files(sd, stem)
        if not files:
            abort(404)
        d = _json.loads(files[0].read_text(encoding="utf-8"))
        return jsonify({"name": "current", "summary": d.get("summary"), "finish_reason": d.get("finish_reason"),
                        "prompt_hash": d.get("prompt_hash"), "generated_at": d.get("generated_at"),
                        "usage": d.get("usage")})
    if v:
        if not _HIST.match(v) or not v.startswith(stem + "."):
            abort(404)
        f = sd / "history" / v
        if not f.is_file():
            abort(404)
        d = _json.loads(f.read_text(encoding="utf-8"))
        return jsonify({"name": v, "summary": d.get("summary"), "finish_reason": d.get("finish_reason"),
                        "prompt_hash": d.get("prompt_hash"), "generated_at": d.get("generated_at"),
                        "usage": d.get("usage")})
    return jsonify({"versions": c.history_versions(sd, stem),
                    "has_current": c.has_summary(sd, stem)})
