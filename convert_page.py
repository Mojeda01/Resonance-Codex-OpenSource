# convert_page.py
import signal, sys
from pathlib import Path
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.config.parser import ConfigParser
from marker.output import text_from_rendered

def main(pdf, page, out_dir, *flags):
    page = int(page)
    out = Path(out_dir); (out / "pages").mkdir(parents=True, exist_ok=True)
    dst = out / "pages" / f"p{page:05d}.md"
    if dst.exists():
        return
    opts = {"output_format": "markdown", "page_range": str(page)}
    if "--force-ocr" in flags:      # OCR the whole page even if it has a text layer
        opts["force_ocr"] = True
    if "--no-ocr" in flags:         # trust the PDF text layer only (fast, light on memory)
        opts["disable_ocr"] = True
    cfg = ConfigParser(opts)
    conv = PdfConverter(
        config=cfg.generate_config_dict(),
        artifact_dict=create_model_dict(),
        processor_list=cfg.get_processors(),
        renderer=cfg.get_renderer(),
    )
    text, _, images = text_from_rendered(conv(pdf))
    prefix = f"p{page:05d}_"
    for name, img in images.items():
        img.save(out / (prefix + name))
        text = text.replace(f"({name})", f"({prefix}{name})")
    dst.write_text(text, encoding="utf-8")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    main(*sys.argv[1:])
