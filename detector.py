from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF

MATH_FONT = re.compile(
    r"(CMMI|CMSY|CMEX|CMBSY|CMMIB|MSAM|MSBM|EUFM|EUSM|EURM|RSFS|"
    r"MathematicalPi|Math(Italic|Symbol|Extension)?|"
    r"^R?MT(MI|SY|EX)|MT-Extra|"
    r"^Symbol(New)?([-,]|$)|StandardSymL|"
    r"Universal-Greek|LINE\d|"
    r"STIXMath|XITSMath|LatinModernMath|CambriaMath|AsanaMath|"
    r"TeXGyre\w*Math|Euclid(Symbol|Extra|MathOne|MathTwo))",
    re.I,
)

EQNUM = re.compile(r"^\(\s*\d+(\.\d+)*[a-z]?\s*\)$")

# Glyphs that are mathematical regardless of the font reporting them.
MATH_GLYPHS = set("∫∑∏√∞≤≥≠≈≡±∓×÷∂∇∈∉⊂⊆⊃⊇∪∩∅→←↔⇒⇐⇔∀∃¬∧∨⊕⊗≪≫⌈⌉⌊⌋⟨⟩∝∼≃≅⊥∥")

GREEK = set("αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")

DECORATIVE = set("⋆★☆✦✧♦♣♥♠●○■□▪▫†‡§¶•◦‣⁃—–")
ORNAMENT_FONT = re.compile(r"(ZapfDingbats|ZztexOrnaments|Wingdings|Webdings)", re.I)

ITALIC_FLAG = 1 << 1
SERIF_FLAG = 1 << 2
MONO_FLAG = 1 << 3
BOLD_FLAG = 1 << 4

@dataclass
class Calibration: 
    body_size: float
    col_left: float
    col_right: float
    header_y: float
    footer_y: float
    line_height: float
    page_width: float
    page_height: float

    @property
    def col_width(self) -> float:
        return self.col_right - self.col_left

@dataclass
class Span:
    t: str
    f: str
    sz: float
    m: bool
    bbox: list[float]

@dataclass
class Line:
    kind: str
    text: str
    math_ratio: float
    bbox: list[float]
    page: int
    spans: list[Span] = field(default_factory=list)

def strip_subset(name: str) -> str:
    return name.split("+",1)[-1]

def is_math_span(span: dict) -> bool:
    text = span["text"]
    if not text.strip():
        return False
    
    stripped = text.strip()

    if ORNAMENT_FONT.search(strip_subset(span["font"])):
        return False
    if stripped and all(c in DECORATIVE or c.isspace() for c in stripped):
        return False

    if MATH_FONT.search(strip_subset(span["font"])):
        return True 

    if any(c in MATH_GLYPHS for c in text):
        return True

    if span["flags"] & ITALIC_FLAG and len(stripped) <= 2 and stripped.isalpha():
        return True

    if len(stripped) <= 2 and any(c in GREEK for c in stripped):
        return True

    return False

def iter_lines(page: fitz.Page) -> Iterator[tuple[dict, list[dict]]]:
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if spans:
                yield line, spans 

def join_spans(spans: list[dict], gap_frac: float = 0.18) -> str:
    out = []
    for i, s, in enumerate(spans):
        if i:
            prev = spans[i - 1]
            gap = s["bbox"][0] - prev["bbox"][2]
            needs = gap > gap_frac * max(s["size"], 1)
            touching = prev["text"].endswith((" ", "-", "\u00ad")) or s["text"].startswith(" ")
            if needs and not touching:
                out.append(" ")
        out.append(s["text"])
    return "".join(out)

def clean_text(s: str) -> str:
    s = re.sub(r"\u00ad\s*", "", s)
    s = re.sub(r"(\w)-\s+(?=[a-z])", r"\1", s)
    return re.sub(r"[ \t]{2,}", " ", s)

def merge_math_runs(spans: list[dict]) -> list[tuple[bool, str, list[float]]]:
    flags = [is_math_span(s) for s in spans]

    for i in range(1, len(spans) - 1):
        if flags[i]:
            continue
        t = spans[i]["text"].strip()
        if t and not any(c.isalpha() for c in t) and len(t) <= 6:
            if flags[i-1] and any (flags[i+1:]):
                flags[i] = True 
    
    runs: list[tuple[bool,str,list[float]]] = []
    for flags, s, in zip(flags,spans):
        if runs and runs[-1][0] == flag:
            prev = runs[-1]
            bb = [min(prev[2][0], s["bbox"][0]), min(prev[2][1], s["bbox"][1]),
                  max(prev[2][2], s["bbox"][2]), max(prev[2][3], s["bbox"][3])]
            runs[-1] = (flag, prev[1] + s["text"], bb)
        else:
            runs.append((flag, s["text"], list(s["bbox"])))
    return runs

def calibrate(doc: fitz.Document, sample: int = 60) -> Calibration:
    n = doc.page_count
    start = min(int(n * 0.15),25)
    stop = min(start + sample, n)

    sizes: collections.Counter[float] = collections.Counter()
    lefts: collections.Counter[float] = collections.Counter()
    rights: collections.Counter[float] = collections.Counter()
    heights: list[float] = []
    pw = ph = 0.0

    for i in range(start, stop):
        page = doc[i]
        pw, ph = page.rect.width, page.rect.height
        for line, spans in iter_lines(page):
            for s in spans:
                sizes[round(s["size"], 1)] += len(s["text"])
            x0 = round(min(s["bbox"][0] for s in spans))
            x1 = round(max(s["bbox"][2] for s in spans))
            lefts[x0] += 1
            rights[x1] += 1
            heights.append(line["bbox"][3] - line["bbox"][1])

    if not sizes:
        raise RuntimeError(
            "no extractable text — document is likely scanned; OCR required"
        )
    
    body_size = sizes.most_common(1)[0][0]

    col_left = float(lefts.most_common(1)[0][0])
    right_vals = sorted(rights.elements())
    col_right = float(right_vals[int(len(right_vals) * 0.90)])

    line_height = statistics.median(heights) if heights else body_size * 1.2
    
    return Calibration(
        body_size=body_size,
        col_left=col_left,
        col_right=col_right,
        header_y=ph * 0.07,
        footer_y=ph * 0.93,
        line_height=line_height,
        page_width=pw,
        page_height=ph,
    )

def classify_line(line: dict, spans: list[dict], cal: Calibration, page_no: int) -> Line | None:
    text = clean_text(join_spans(spans)) 
    if not text.strip():
        return None

    y0, y1 = line["bbox"][1], line["bbox"][3]
    if y1 < cal.header_y or y0 > cal.footer_y:
        return None

    y0, y1 = line["bbox"][1], line["bbox"][3]
    if y1 < cal.header_y or y0 > cal.footer_y:
        return None  # running head / folio

    math_chars = sum(len(s["text"]) for s in spans if is_math_span(s))
    ratio = math_chars / max(len(text.strip()), 1)

    x0 = min(s["bbox"][0] for s in spans)
    x1 = max(s["bbox"][2] for s in spans)
    w = cal.col_width

    indented = (x0 - cal.col_left) > 0.12 * w
    short = (x1 - x0) < 0.85 * w
    centred = abs((x0 - cal.col_left) - (cal.col_right - x1)) < 0.08 * w

    last = spans[-1]["text"].strip()
    has_eqnum = bool(EQNUM.match(last)) and (cal.col_right - spans[-1]["bbox"][2]) < 0.05 * w

    toks = text.split()
    figureish = (
        len(toks) > 4
        and all(len(t) <= 2 for t in toks)
        and not any(c in MATH_GLYPHS for c in text)
    )

    if figureish:
        kind = "figure"
    elif has_eqnum or (ratio > 0.5 and (indented or centred) and short):
        kind = "display"
    elif ratio > 0.15:
        kind = "inline_mixed"
    else:
        kind = "text"

    return Line(
        kind=kind,
        text=text,
        math_ratio=round(ratio, 3),
        bbox=[round(v, 2) for v in line["bbox"]],
        page=page_no,
        spans=[
            Span(
                t=s["text"],
                f=strip_subset(s["font"]),
                sz=round(s["size"], 1),
                m=is_math_span(s),
                bbox=[round(v, 2) for v in s["bbox"]],
            )
            for s in spans
        ],
    )

def merge_display_blocks(lines: list[Line], cal: Calibration) -> list[Line]:
    out: list[Line] = []
    for ln in lines:
        if (
            out
            and ln.kind == "display"
            and out[-1].kind == "display"
            and ln.page == out[-1].page
            and (ln.bbox[1] - out[-1].bbox[3]) < 1.5 * cal.line_height
        ):
            prev = out[-1]
            prev.text += " " + ln.text
            prev.bbox = [
                min(prev.bbox[0], ln.bbox[0]),
                min(prev.bbox[1], ln.bbox[1]),
                max(prev.bbox[2], ln.bbox[2]),
                max(prev.bbox[3], ln.bbox[3]),
            ]
            prev.spans.extend(ln.spans)
            prev.math_ratio = round(max(prev.math_ratio, ln.math_ratio), 3)
        else:
            out.append(ln)
    return out

def render(lines: list[Line]) -> str:
    parts: list[str] = []
    buf: list[str] = []

    for i, ln in enumerate(lines):
        if ln.kind == "figure":
            continue
        if ln.kind == "display":
            if buf:
                parts.append(" ".join(buf))
                buf = []
            parts.append(f"\n\n$$[MATH:{i}]$$\n\n")
        else:
            out, run = [], []
            for sp in ln.spans:
                if sp.m:
                    run.append(sp.t)
                else:
                    if run:
                        out.append(f"$[m:{i}:{''.join(run)}]$")
                        run = []
                    out.append(sp.t)
            if run:
                out.append(f"$[m:{i}:{''.join(run)}]$")
            buf.append(clean_text("".join(out)))

    if buf:
        parts.append(" ".join(buf))

    return "".join(parts)

def extract(path: Path, page_range: tuple[int, int] | None = None) -> dict:
    doc = fitz.open(path)
    cal = calibrate(doc)

    lo, hi = page_range or (0, doc.page_count)
    lo, hi = max(0, lo), min(doc.page_count, hi)

    lines: list[Line] = []
    for i in range(lo, hi):
        page = doc[i]
        for raw_line, spans in iter_lines(page):
            ln = classify_line(raw_line, spans, cal, i)
            if ln:
                lines.append(ln)

    lines = merge_display_blocks(lines, cal)
    doc.close()

    counts = collections.Counter(l.kind for l in lines)
    return {
        "source": path.name,
        "pages": [lo, hi],
        "calibration": asdict(cal),
        "counts": dict(counts),
        "lines": [asdict(l) for l in lines],
        "rendered": render(lines),
    }

def cmd_fonts(paths: list[Path], sample: int = 30) -> None:
    for p in paths:
        try:
            doc = fitz.open(p)
        except Exception as e:
            print(f"\n{p.name}\n  OPEN FAILED: {e}")
            continue

        start = min(int(doc.page_count * 0.15), 25)
        c: collections.Counter[str] = collections.Counter()
        for i in range(start, min(start + sample, doc.page_count)):
            for _, spans in iter_lines(doc[i]):
                for s in spans:
                    c[strip_subset(s["font"])] += len(s["text"])

        print(f"\n{p.name}  ({doc.page_count} pages)")
        if not c:
            print("  NO TEXT SPANS — scanned or image-only, needs OCR")
        for f, n in c.most_common(15):
            flag = "  <-- MATH" if MATH_FONT.search(f) else ""
            print(f"  {n:9,d}  {f}{flag}")
        doc.close()


def cmd_calibrate(paths: list[Path]) -> None:
    for p in paths:
        doc = fitz.open(p)
        try:
            cal = calibrate(doc)
            print(f"\n{p.name}")
            for k, v in asdict(cal).items():
                print(f"  {k:14s} {v}")
        except RuntimeError as e:
            print(f"\n{p.name}\n  {e}")
        doc.close()


def cmd_extract(paths: list[Path], outdir: Path, page_range, preview: bool) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for p in paths:
        try:
            result = extract(p, page_range)
        except Exception as e:
            print(f"{p.name}: FAILED — {e}", file=sys.stderr)
            continue

        dest = outdir / f"{p.stem}.lines.json"
        dest.write_text(json.dumps(result, ensure_ascii=False, indent=1))

        c = result["counts"]
        total = sum(c.values())
        print(
            f"{p.name}: {total:,} lines  "
            f"text={c.get('text',0):,} inline={c.get('inline_mixed',0):,} "
            f"display={c.get('display',0):,}  -> {dest}"
        )

        if preview:
            print("-" * 70)
            print(result["rendered"][:2000])
            print("-" * 70)


def parse_range(s: str | None):
    if not s:
        return None
    lo, _, hi = s.partition(":")
    return (int(lo), int(hi))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fonts", help="inventory fonts — run this first")
    f.add_argument("pdfs", nargs="+", type=Path)
    f.add_argument("--sample", type=int, default=30)

    c = sub.add_parser("calibrate", help="show derived layout constants")
    c.add_argument("pdfs", nargs="+", type=Path)

    e = sub.add_parser("extract", help="classify lines and emit JSON")
    e.add_argument("pdfs", nargs="+", type=Path)
    e.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    e.add_argument("--pages", help="LO:HI page slice, e.g. 40:60")
    e.add_argument("--preview", action="store_true")

    a = ap.parse_args()

    if a.cmd == "fonts":
        cmd_fonts(a.pdfs, a.sample)
    elif a.cmd == "calibrate":
        cmd_calibrate(a.pdfs)
    elif a.cmd == "extract":
        cmd_extract(a.pdfs, a.outdir, parse_range(a.pages), a.preview)


if __name__ == "__main__":
    main()
