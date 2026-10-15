"""Caso de uso "texto -> áudio para download": valida e divide o texto, delega a geração à fila."""
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
            raise InvalidInput("texto vazio")
        if len(text) > self._max_chars:
            raise InvalidInput(f"texto muito longo (máx. {self._max_chars} caracteres)")
        texts, paras = chunk_plain_text(text)
        if not texts:
            raise InvalidInput("nenhum texto legível")
        title = " ".join(text.split()[:8])[:60]
        style = (style or "").strip().strip("()") or None
        return self._queue.create(title, texts, paras, style)

    def list(self) -> list[dict]:
        return self._queue.list()

    def audio(self, job_id: str) -> tuple[Path, str]:
        """Caminho do MP3 pronto e um nome de arquivo seguro para download."""
        job = self._queue.get(job_id)
        path = self._queue.audio_path(job_id)
        if not job or job["status"] != "done" or not path.exists():
            raise NotFound("áudio não disponível")
        name = re.sub(r"[^\w\- ]+", "", job["title"]).strip()[:50] or "audio"
        return path, f"{name}.mp3"

    def delete(self, job_id: str):
        if not self._queue.get(job_id):
            raise NotFound("não encontrado")
        self._queue.delete(job_id)
