import pathlib


class Config:
    BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
    KNOWLEDGE_DIR = BASE_DIR / "deepSeek" / "knowledge"
    MATERIAL_DIR = BASE_DIR / "material"          # source PDFs
    OUT_DIR = BASE_DIR / "out"                    # OCR output, one folder per book
    LIBRARY_FILE = BASE_DIR / "library.json"      # optional: names, domains, legacy layout
    CONVERT_SCRIPT = BASE_DIR / "convert_page.py"
    DEEPSEEK_DIR = BASE_DIR / "deepSeek"
    DEEPSEEK_KEY_FILE = BASE_DIR / "deepSeek" / "key.md"      # read server-side only, never sent to the browser
    PROMPTS_DIR = BASE_DIR / "deepSeek" / "prompts"
    TEMPLATES_AUTO_RELOAD = True
    WARM_CACHE = True


class Dev(Config):
    DEBUG = True


class Prod(Config):
    DEBUG = False
