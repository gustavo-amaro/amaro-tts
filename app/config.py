"""Application paths and limits."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
# AMAROTTS_DATA_DIR lets a second instance (a demo, for example) keep its own library and settings
DATA_DIR = Path(os.environ.get("AMAROTTS_DATA_DIR") or ROOT.parent / "data")
DOCS_DIR = DATA_DIR / "docs"
EXPORTS_DIR = DATA_DIR / "exports"

EXPORT_MAX_CHARS = 50_000
