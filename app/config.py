"""Caminhos e limites da aplicação."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT.parent / "data"
DOCS_DIR = DATA_DIR / "docs"
EXPORTS_DIR = DATA_DIR / "exports"

EXPORT_MAX_CHARS = 50_000
