"""Document use cases: upload, extraction, opening, pages and reading cursor."""
import hashlib
import time
from collections.abc import Callable
from typing import Protocol

from ..errors import InvalidInput, NotFound
from ..events import Publisher
from .repository import DocumentRepository

Extractor = Callable[[str, Callable[[int, int], None]], dict]  # (pdf path, progress) -> {"pages", "chunks"}


class ReadAhead(Protocol):
    """What the service needs from the reader's audio generator."""

    def is_ready(self, doc_id: str, idx: int) -> bool: ...

    def ready_ahead(self, doc_id: str, cursor: int) -> list[int]: ...

    def set_cursor(self, doc_id: str, texts: list[str], cursor: int) -> None: ...


class DocumentService:
    def __init__(self, repo: DocumentRepository, reader: ReadAhead, extractor: Extractor, publish: Publisher):
        self._repo = repo
        self._reader = reader
        self._extract = extractor
        self._publish = publish
        self._loaded: dict[str, dict] = {}  # doc_id -> {"pages", "chunks", "texts"}

    def _content(self, doc_id: str) -> dict:
        if doc_id not in self._loaded:
            data = self._repo.read_chunks(doc_id)
            data["texts"] = [c["text"] for c in data["chunks"]]
            self._loaded[doc_id] = data
        return self._loaded[doc_id]

    def list(self) -> list[dict]:
        metas = self._repo.all_metas()
        for m in metas:
            m.pop("page_first", None)
            m["progress"] = self._repo.read_progress(m["id"])
        return sorted(metas, key=lambda m: m.get("opened", 0), reverse=True)

    def upload(self, filename: str | None, content: bytes) -> dict:
        """Stores the PDF. If the returned status is "extracting", the caller must run `extract` in the background."""
        if not content.startswith(b"%PDF"):
            raise InvalidInput("not_pdf", "file is not a PDF")
        doc_id = hashlib.sha1(content).hexdigest()[:16]
        if self._repo.exists(doc_id) and self._repo.read_meta(doc_id)["status"] == "ready":
            return {"id": doc_id, "status": "ready"}
        self._repo.save_pdf(doc_id, content)
        self._repo.write_meta({"id": doc_id, "name": filename or "document.pdf", "status": "extracting",
                               "opened": time.time()})
        return {"id": doc_id, "status": "extracting"}

    def extract(self, doc_id: str):
        meta = self._repo.read_meta(doc_id)
        t0 = time.time()

        def progress(i, n):
            self._publish(doc_id, {"type": "extract_progress", "done": i, "total": n})

        try:
            data = self._extract(str(self._repo.pdf_path(doc_id)), progress)
        except Exception as e:
            meta.update(status="error", error=str(e))
            self._repo.write_meta(meta)
            self._publish(doc_id, {"type": "extract_error", "error": str(e)})
            return
        self._repo.write_chunks(doc_id, data)
        meta.update(
            status="ready",
            pages=len(data["pages"]),
            chunks=len(data["chunks"]),
            page_first=[p["first"] for p in data["pages"]],
            extract_seconds=round(time.time() - t0, 2),
        )
        self._repo.write_meta(meta)
        self._publish(doc_id, {"type": "extract_done", "pages": meta["pages"], "chunks": meta["chunks"]})

    def open(self, doc_id: str) -> dict:
        meta = self._repo.read_meta(doc_id)
        if meta["status"] == "ready":
            meta["opened"] = time.time()
            self._repo.write_meta(meta)
        return {**meta, "progress": self._repo.read_progress(doc_id)}

    def page(self, doc_id: str, n: int) -> dict:
        data = self._content(doc_id)
        if not 0 <= n < len(data["pages"]):
            raise NotFound("page_not_found", "page does not exist")
        page = data["pages"][n]
        ids = range(page["first"], page["last"] + 1)
        return {
            "page": n,
            "text": page["text"],
            "chunks": [{"id": i, "start": data["chunks"][i]["start"], "end": data["chunks"][i]["end"]} for i in ids],
            "ready": [i for i in ids if self._reader.is_ready(doc_id, i)],
        }

    def set_cursor(self, doc_id: str, chunk: int) -> dict:
        texts = self._content(doc_id)["texts"]
        chunk = max(0, min(chunk, len(texts) - 1))
        self._repo.write_progress(doc_id, chunk)
        self._reader.set_cursor(doc_id, texts, chunk)
        return {"chunk": chunk, "ready": self._reader.ready_ahead(doc_id, chunk)}
