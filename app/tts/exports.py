"""Persistent export queue (text -> MP3 file), resumed after a server restart."""
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from ..events import Publisher
from .config import SynthesisConfig
from .worker import Task

CHANNEL = "exports"
PAUSE_PARAGRAPH = 0.30  # silence between paragraphs in the exported audio (s)
PAUSE_CHUNK = 0.12


class ExportTask(Task):
    def __init__(self, queue: "ExportQueue", job_id: str, idx: int, text: str, settings: dict, voice: Path | None):
        super().__init__(text, settings, voice, label=f"export {job_id[:8]}#{idx}")
        self.queue, self.job_id, self.idx = queue, job_id, idx

    def done(self, wav: np.ndarray, sample_rate: int):
        self.queue.chunk_done(self, wav, sample_rate)

    def failed(self, error: str):
        self.queue.chunk_failed(self, error)


class ExportQueue:
    def __init__(self, exports_dir: Path, config: SynthesisConfig, wakeup: threading.Condition, publish: Publisher):
        self._dir = exports_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._config = config
        self._wakeup = wakeup
        self._publish = publish
        self._pending: dict[str, dict] = {}  # pending jobs, in arrival order
        self._resume()

    # ---------- persistence ----------
    def _job_dir(self, job_id: str) -> Path:
        return self._dir / job_id

    def _save(self, job: dict):
        tmp = self._job_dir(job["id"]) / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, tmp.with_suffix(""))

    @staticmethod
    def _public(job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in ("texts", "paras")}

    def _resume(self):
        for p in sorted(self._dir.glob("*/job.json"), key=lambda p: p.stat().st_mtime):
            job = json.loads(p.read_text(encoding="utf-8"))
            if job["status"] in ("queued", "running"):
                parts = self._job_dir(job["id"]) / "parts"
                done = 0
                while (parts / f"{done}.wav").exists():
                    done += 1
                job["done"] = done
                self._pending[job["id"]] = job

    # ---------- operations ----------
    def create(self, title: str, texts: list[str], paras: list[int], style: str | None) -> dict:
        job_id = uuid.uuid4().hex[:12]
        folder = self._job_dir(job_id)
        (folder / "parts").mkdir(parents=True, exist_ok=True)
        voice = self._config.voice()
        if voice:  # freeze the current voice for this job
            shutil.copy(voice, folder / "voice.wav")
        job = {"id": job_id, "title": title, "created": time.time(), "status": "queued",
               "total": len(texts), "done": 0, "texts": texts, "paras": paras, "style": style,
               "settings": self._config.snapshot(), "voice": voice is not None, "duration": None, "error": None}
        with self._wakeup:
            self._save(job)
            self._pending[job_id] = job
            self._wakeup.notify_all()
        return self._public(job)

    def list(self) -> list[dict]:
        # read under the lock: on Windows, os.replace in _save fails while another thread has the file open
        with self._wakeup:
            jobs = [json.loads(p.read_text(encoding="utf-8")) for p in self._dir.glob("*/job.json")]
            for j in jobs:  # in-memory progress is newer than what is on disk
                if j["id"] in self._pending:
                    j.update(done=self._pending[j["id"]]["done"], status=self._pending[j["id"]]["status"])
        return sorted((self._public(j) for j in jobs), key=lambda j: j["created"], reverse=True)

    def get(self, job_id: str) -> dict | None:
        p = self._job_dir(job_id) / "job.json"
        if not p.exists():
            return None
        with self._wakeup:
            if job_id in self._pending:
                return self._public(self._pending[job_id])
            return self._public(json.loads(p.read_text(encoding="utf-8")))

    def audio_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "audio.mp3"

    def delete(self, job_id: str):
        with self._wakeup:  # the worker only writes parts while holding the lock, so there is no race
            self._pending.pop(job_id, None)
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    # ---------- worker ----------
    def next_task(self) -> ExportTask | None:
        """Next chunk of the oldest job. Called with `wakeup` held."""
        for job in self._pending.values():
            i = job["done"]
            voice = self._job_dir(job["id"]) / "voice.wav"
            text = job["texts"][i]
            if job.get("style"):
                text = f"({job['style']}){text}"
            return ExportTask(self, job["id"], i, text, job["settings"], voice if voice.exists() else None)
        return None

    def chunk_done(self, task: ExportTask, wav: np.ndarray, sample_rate: int):
        job_id, idx = task.job_id, task.idx
        with self._wakeup:
            job = self._pending.get(job_id)
            if job is None or job["done"] != idx:  # cancelled mid-generation
                return
            sf.write(self._job_dir(job_id) / "parts" / f"{idx}.wav", wav, sample_rate, subtype="PCM_16")
            job["done"] = idx + 1
            job["status"] = "running"
            finished = job["done"] == job["total"]
            if finished:
                self._pending.pop(job_id)
            self._save(job)
        self._publish(CHANNEL, {"type": "export_progress", "job": job_id, "done": job["done"], "total": job["total"]})
        if finished:
            self._finalize(job, sample_rate)

    def chunk_failed(self, task: ExportTask, error: str):
        with self._wakeup:
            job = self._pending.pop(task.job_id, None)
            if job is None:
                return
            job.update(status="error", error=error)
            self._save(job)
        self._publish(CHANNEL, {"type": "export_error", "job": job["id"], "error": error})

    def _finalize(self, job: dict, sr: int):
        folder = self._job_dir(job["id"])
        pieces = []
        for i in range(job["total"]):
            if i:
                gap = PAUSE_PARAGRAPH if job["paras"][i] != job["paras"][i - 1] else PAUSE_CHUNK
                pieces.append(np.zeros(int(sr * gap), dtype=np.float32))
            pieces.append(sf.read(folder / "parts" / f"{i}.wav", dtype="float32")[0])
        audio = np.concatenate(pieces)
        tmp = folder / "audio.tmp.mp3"
        sf.write(tmp, audio, sr, format="MP3", subtype="MPEG_LAYER_III")
        os.replace(tmp, self.audio_path(job["id"]))
        shutil.rmtree(folder / "parts", ignore_errors=True)
        with self._wakeup:
            job.update(status="done", duration=round(len(audio) / sr, 1))
            self._save(job)
        print(f"[engine] export {job['id']} done: {job['duration']}s", file=sys.stderr)
        self._publish(CHANNEL, {"type": "export_done", "job": job["id"], "duration": job["duration"]})
