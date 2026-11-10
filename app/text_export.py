"""Use case "text -> downloadable audio": validates and splits the text, hands generation to the queue."""
import re
from pathlib import Path

from .errors import InvalidInput, NotFound
from .text.chunking import chunk_plain_text
from .tts.exports import ExportQueue


class TextExportService:
    def __init__(self, queue: ExportQueue, max_chars: int):
        self._queue = queue
        self._max_chars = max_chars

    def create(self, text: str, style: str | None) -> dict:
        text = text.replace("\r\n", "\n").strip()
        if not text:
            raise InvalidInput("text_empty", "empty text")
        if len(text) > self._max_chars:
            raise InvalidInput("text_too_long", f"text too long (max. {self._max_chars} characters)",
                               max=self._max_chars)
        texts, paras = chunk_plain_text(text)
        if not texts:
            raise InvalidInput("text_unreadable", "no readable text")
        title = " ".join(text.split()[:8])[:60]
        style = (style or "").strip().strip("()") or None
        return self._queue.create(title, texts, paras, style)

    def list(self) -> list[dict]:
        return self._queue.list()

    def audio(self, job_id: str) -> tuple[Path, str]:
        """Path of the finished MP3 and a safe filename for download."""
        job = self._queue.get(job_id)
        path = self._queue.audio_path(job_id)
        if not job or job["status"] != "done" or not path.exists():
            raise NotFound("audio_unavailable", "audio not available")
        name = re.sub(r"[^\w\- ]+", "", job["title"]).strip()[:50] or "audio"
        return path, f"{name}.mp3"

    def delete(self, job_id: str):
        if not self._queue.get(job_id):
            raise NotFound("export_not_found", "export not found")
        self._queue.delete(job_id)
