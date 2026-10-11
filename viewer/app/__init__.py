from flask import Flask

from config import Dev


def create_app(config_object=Dev):
    app = Flask(__name__)
    app.config.from_object(config_object)

    from app.routes import bp
    app.register_blueprint(bp)
    import time
    from pathlib import Path
    app.config["STARTED_AT"] = time.time()

    @app.context_processor
    def code_staleness():
        """True when Python files changed after this server started. The auto-reloader is off (a
        restart would orphan a running OCR job), so new routes only exist after a manual restart."""
        files = list(Path(__file__).parent.glob("*.py")) + list((Path(app.config["DEEPSEEK_DIR"])).glob("distill_core.py"))
        newest = max((f.stat().st_mtime for f in files), default=0)
        return {"code_stale": newest > app.config["STARTED_AT"] + 1}

    from app.library_routes import lib_bp
    app.register_blueprint(lib_bp)

    # Build the cross-reference index in the background so the first page load is fast.
    import threading

    def warm():
        with app.app_context():
            from app import metrics, store, xref
            for d in store.domains():
                xref.build(d)
                metrics.build(d)

    if app.config.get("WARM_CACHE", True):
        threading.Thread(target=warm, daemon=True).start()
    return app
