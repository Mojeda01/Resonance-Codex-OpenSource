from app import create_app

app = create_app()

if __name__ == "__main__":
    # No reloader: a code-change restart would orphan a running OCR job. Restart manually.
    app.run(host="127.0.0.1", port=5001, use_reloader=False)
