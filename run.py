# run.py
import signal, subprocess, sys, time
import pypdfium2 as pdfium
from pathlib import Path

stop = False
def handler(*_):
    global stop
    stop = True
    print("\n[stop] finishing current page...", flush=True)
signal.signal(signal.SIGINT, handler)

pdf, out_dir = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "out")
n = len(pdfium.PdfDocument(pdf))

for p in range(n):
    if stop:
        break
    if (Path(out_dir) / "pages" / f"p{p:05d}.md").exists():
        continue
    t = time.time()
    subprocess.run([sys.executable, "convert_page.py", pdf, str(p), out_dir])
    print(f"[{p+1}/{n}] {time.time()-t:.1f}s", flush=True)
