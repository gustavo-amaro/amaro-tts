"""Persistência dos documentos: data/docs/<id>/{doc.pdf, meta.json, chunks.json, progress.json}."""
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

    # ---------- metadados ----------
    def exists(self, doc_id: str) -> bool:
        return (self._folder(doc_id) / "meta.json").exists()

    def read_meta(self, doc_id: str) -> dict:
        p = self._folder(doc_id) / "meta.json"
        if not p.exists():
            raise NotFound("documento não encontrado")
        return json.loads(p.read_text())

    def write_meta(self, meta: dict):
        (self._folder(meta["id"]) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))

    def all_metas(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in self._root.glob("*/meta.json")]

    # ---------- conteúdo extraído ----------
    def read_chunks(self, doc_id: str) -> dict:
        p = self._folder(doc_id) / "chunks.json"
        if not p.exists():
            raise Conflict("documento ainda em processamento")
        return json.loads(p.read_text())

    def write_chunks(self, doc_id: str, data: dict):
        (self._folder(doc_id) / "chunks.json").write_text(json.dumps(data, ensure_ascii=False))

    # ---------- progresso de leitura ----------
    def read_progress(self, doc_id: str) -> int:
        p = self._folder(doc_id) / "progress.json"
        return json.loads(p.read_text())["chunk"] if p.exists() else 0

    def write_progress(self, doc_id: str, chunk: int):
        (self._folder(doc_id) / "progress.json").write_text(json.dumps({"chunk": chunk}))
