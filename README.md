# Resonance Codex

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-2.x-EE4C2C?logo=pytorch&logoColor=white)
![Marker](https://img.shields.io/badge/OCR-Marker-4B5563)
![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-4D6BFE)
![MathJax](https://img.shields.io/badge/Math-LaTeX%20%2F%20MathJax-0F9D58)
![Runs](https://img.shields.io/badge/runs-locally-success)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

A local pipeline for turning technical PDFs (textbooks, papers) into searchable, page-level study notes:

1. **OCR** each PDF page by page into Markdown with [Marker](https://github.com/datalab-to/marker) (`convert_page.py`, `run.py`).
2. **Summarise** each page into a formal, LaTeX-preserving summary with the DeepSeek API (`deepSeek/`).
3. **Browse** everything in a local Flask web app (`viewer/`): per-domain page view, full-text search, index of terms and results, token/structure analysis, problem-page detection, and a Library page that can launch OCR and summarisation jobs from the browser.

Everything runs locally. The only network call is to the DeepSeek API when you summarise.

## Layout

```
convert_page.py   OCR a single PDF page (one fresh process per page, to keep memory low)
run.py            loop convert_page.py over a whole PDF, resumable
detector.py       PyMuPDF heuristics for math-heavy pages
check_marker.sh   monitor a running Marker job
deepSeek/         summarisation core, CLI, prompts, token counter
viewer/           Flask web app
VastAI_scripts/   optional: find cheap rented GPUs for OCR
library.json      optional book metadata (title, knowledge domain)
```

Your data lives in directories that are git-ignored and created on demand:

```
material/              put source PDFs here
out/<book-slug>/pages/ OCR output (pNNNNN.md) and extracted images
deepSeek/knowledge/<domain>/summaries/   page summaries (pNNNNN.json)
```

## Setup

Requires Python 3.11+ (developed on 3.14).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt     # large: includes torch and marker-pdf
```

`requirements.txt` is a full freeze of the author's environment. If a pin fails on your platform, install the core packages (`marker-pdf`, `flask`, `pypdfium2`, `pymupdf`, `openai`, `requests`) and let pip resolve the rest.

### API key

```bash
cp deepSeek/key.md.example deepSeek/key.md
# edit deepSeek/key.md and paste your DeepSeek key
```

`key.md` is git-ignored and is only read server-side; it is never sent to the browser. Never commit it.

## Usage

### Web app (easiest)

```bash
python viewer/run.py
```

Open <http://127.0.0.1:5001>. Drop PDFs into `material/`, open **Library & OCR**, and start OCR and summarisation jobs from there. Edit `library.json` to give a book a title or to attach it to a knowledge domain:

```json
{
  "books": [
    { "slug": "my-book", "pdf": "MyBook.pdf", "title": "My Book", "domain": "my-book" }
  ]
}
```

### Command line

```bash
# OCR a PDF (resumable; Ctrl-C finishes the current page)
python run.py material/MyBook.pdf out/my-book

# Summarise the OCR pages
cd deepSeek
python distillator.py --pages ../out/my-book/pages --domain my-book --workers 5

# Token usage per domain
python token_counter.py
```

### Prompts

The system prompt for a domain is `deepSeek/prompts/<domain>.md`, falling back to `_default.md`. The included prompts are tuned for mathematical and economic texts; copy `_default.md` to `<your-domain>.md` and adapt it for other material. Prompt versions are archived automatically in `<domain>.history/`.

## Cost and privacy notes

- Summarising a whole textbook means one API request per page. Use the estimate in the Library page before starting a run.
- The viewer binds to `127.0.0.1` only. Don't expose it publicly: it can start jobs that spend API credit.
- Only use PDFs you have the right to process; this repo ships no book content.
