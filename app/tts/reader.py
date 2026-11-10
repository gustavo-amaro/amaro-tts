"""Read-aloud: generates audio ahead of the cursor and keeps the chunk cache on disk."""
import os
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from ..events import Publisher
from .config import SynthesisConfig
from .worker import Task

LOOKAHEAD = 8  # chunks generated ahead of the cursor (~1.5 min of audio)
URGENT_AHEAD = 2  # reader chunks that jump ahead of exports
CACHE_LIMIT_BYTES = 2 * 1024**3
CACHE_CHECK_EVERY = 20  # saves between cache-limit checks


class ReaderTask(Task):
    def __init__(self, reader: "ReaderQueue", doc_id: str, idx: int, text: str, key: str, settings: dict,
                 voice: Path | None):
        super().__init__(text, settings, voice, label=f"reader {doc_id[:8]}#{idx}")
        self.reader, self.doc_id, self.idx, self.key = reader, doc_id, idx, key

    def started(self):
        self.reader.publish(self.doc_id, {"type": "generating", "id": self.idx})

    def done(self, wav: np.ndarray, sample_rate: int):
        self.reader.save(self, wav, sample_rate)

    def failed(self, error: str):
        self.reader.mark_failed(self, error)


class ReaderQueue:
    def __init__(self, docs_dir: Path, config: SynthesisConfig, wakeup: threading.Condition, publish: Publisher):
        self._docs_dir = docs_dir
        self._config = config
        self._wakeup = wakeup
        self.publish = publish
        self._job: dict | None = None  # {"doc_id", "texts", "cursor"}
        self._failed: set[tuple[str, str, int]] = set()
        self._saves = 0

    # ---------- queries ----------
    def audio_path(self, doc_id: str, idx: int, key: str | None = None) -> Path:
        return self._docs_dir / doc_id / "audio" / (key or self._config.key()) / f"{idx}.ogg"

    def is_ready(self, doc_id: str, idx: int) -> bool:
        return self.audio_path(doc_id, idx).exists()

    def is_failed(self, doc_id: str, idx: int) -> bool:
        return (doc_id, self._config.key(), idx) in self._failed

    def ready_ahead(self, doc_id: str, cursor: int, limit: int = LOOKAHEAD * 2) -> list[int]:
        return [i for i in range(cursor, cursor + limit) if self.is_ready(doc_id, i)]

    # ---------- scheduling ----------
    def set_cursor(self, doc_id: str, texts: list[str], cursor: int):
        with self._wakeup:
            self._job = {"doc_id": doc_id, "texts": texts, "cursor": cursor}
            self._wakeup.notify_all()

    def next_task(self, ahead: int) -> ReaderTask | None:
        """First chunk without audio between cursor and cursor+ahead. Called with `wakeup` held."""
        job = self._job
        if not job:
            return None
        key = self._config.key()
        doc_id, texts = job["doc_id"], job["texts"]
        for i in range(job["cursor"], min(job["cursor"] + ahead, len(texts))):
            if (doc_id, key, i) in self._failed:
                continue
            if not self.audio_path(doc_id, i, key).exists():
                return ReaderTask(self, doc_id, i, texts[i], key, self._config.snapshot(), self._config.voice())
        return None

    # ---------- results ----------
    def save(self, task: ReaderTask, wav: np.ndarray, sample_rate: int):
        path = self.audio_path(task.doc_id, task.idx, task.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{task.idx}.tmp.ogg")
        sf.write(tmp, wav, sample_rate, format="OGG", subtype="OPUS")
        os.replace(tmp, path)
        self.publish(task.doc_id, {"type": "chunk_ready", "id": task.idx, "duration": round(len(wav) / sample_rate, 2)})
        self._saves += 1
        if self._saves % CACHE_CHECK_EVERY == 0:
            self._enforce_cache_limit(task.doc_id)

    def mark_failed(self, task: ReaderTask, error: str):
        with self._wakeup:
            self._failed.add((task.doc_id, task.key, task.idx))
        self.publish(task.doc_id, {"type": "chunk_error", "id": task.idx, "error": error})

    def _enforce_cache_limit(self, keep_doc: str):
        files = [(p.stat().st_mtime, p.stat().st_size, p) for p in self._docs_dir.glob("*/audio/*/*.ogg")]
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= CACHE_LIMIT_BYTES:
                break
            if p.parts[-4] == keep_doc and time.time() - p.stat().st_mtime < 3600:
                continue
            p.unlink(missing_ok=True)
            total -= size
