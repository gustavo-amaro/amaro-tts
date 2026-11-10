"""Document storage: data/docs/<id>/{doc.pdf, meta.json, chunks.json, progress.json}."""
import json
from pathlib import Path

from ..errors import Conflict, NotFound


class DocumentRepository:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _folder(self, doc_id: str) -> Path:
        return self._root / doc_id

    def pdf_path(self, doc_id: str) -> Path:
        return self._folder(doc_id) / "doc.pdf"

    def save_pdf(self, doc_id: str, content: bytes):
        self._folder(doc_id).mkdir(parents=True, exist_ok=True)
        self.pdf_path(doc_id).write_bytes(content)

    # ---------- metadata ----------
    def exists(self, doc_id: str) -> bool:
        return (self._folder(doc_id) / "meta.json").exists()

    def read_meta(self, doc_id: str) -> dict:
        p = self._folder(doc_id) / "meta.json"
        if not p.exists():
            raise NotFound("doc_not_found", "document not found")
        return json.loads(p.read_text(encoding="utf-8"))

    def write_meta(self, meta: dict):
        (self._folder(meta["id"]) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def all_metas(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in self._root.glob("*/meta.json")]

    # ---------- extracted content ----------
    def read_chunks(self, doc_id: str) -> dict:
        p = self._folder(doc_id) / "chunks.json"
        if not p.exists():
            raise Conflict("doc_processing", "document is still being processed")
        return json.loads(p.read_text(encoding="utf-8"))

    def write_chunks(self, doc_id: str, data: dict):
        (self._folder(doc_id) / "chunks.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ---------- reading progress ----------
    def read_progress(self, doc_id: str) -> int:
        p = self._folder(doc_id) / "progress.json"
        return json.loads(p.read_text(encoding="utf-8"))["chunk"] if p.exists() else 0

    def write_progress(self, doc_id: str, chunk: int):
        (self._folder(doc_id) / "progress.json").write_text(json.dumps({"chunk": chunk}), encoding="utf-8")
