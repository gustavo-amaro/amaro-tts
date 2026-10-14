"""VoxCPM2 residente na GPU Intel (XPU) + worker que gera áudio à frente do cursor de leitura."""
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

LOOKAHEAD = 8  # trechos gerados à frente do cursor (~1,5 min de áudio)
URGENT_AHEAD = 2  # trechos do leitor que passam na frente das exportações
PAUSE_PARAGRAPH = 0.30  # silêncio entre parágrafos no áudio exportado (s)
PAUSE_CHUNK = 0.12
CACHE_LIMIT_BYTES = 2 * 1024**3
DEFAULT_SETTINGS = {"steps": 6, "cfg": 2.0, "seed": 42}


def patch_xpu():
    """VoxCPM só conhece cuda/mps/cpu; força a XPU (Intel Arc) quando disponível."""
    import voxcpm.model.voxcpm2 as vx2

    def _resolve(device, configured_device="cuda"):
        if device in (None, "auto", "xpu") and torch.xpu.is_available():
            return "xpu"
        return device or "cpu"

    vx2.resolve_runtime_device = _resolve


class Engine:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.voice_path = data_dir / "voice.wav"
        self.settings_path = data_dir / "settings.json"
        self.settings = dict(DEFAULT_SETTINGS)
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text()))
        self.model = None
        self.status = "loading"
        self.on_event = lambda doc_id, event: None
        self._cond = threading.Condition()
        self._job = None  # {"doc_id", "texts", "cursor"}
        self._failed: set[tuple[str, str, int]] = set()
        self._saves = 0
        self._voice_hash = self._hash_voice()
        self.exports_dir = data_dir / "exports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self._exports: dict[str, dict] = {}  # jobs pendentes, em ordem de chegada
        self._resume_exports()

    def _hash_voice(self) -> str:
        if not self.voice_path.exists():
            return "novoice"
        return hashlib.sha1(self.voice_path.read_bytes()).hexdigest()[:8]

    # ---------- configuração ----------
    def settings_key(self) -> str:
        s = self.settings
        return f"s{s['steps']}_c{s['cfg']}_r{s['seed']}_{self._voice_hash}"

    def update_settings(self, **kw):
        with self._cond:
            self.settings.update({k: v for k, v in kw.items() if k in DEFAULT_SETTINGS and v is not None})
            self.settings_path.write_text(json.dumps(self.settings))
            self._cond.notify_all()

    def voice_changed(self):
        with self._cond:
            self._voice_hash = self._hash_voice()
            self._cond.notify_all()

    # ---------- cache ----------
    def audio_path(self, doc_id: str, idx: int, key: str | None = None) -> Path:
        return self.data_dir / "docs" / doc_id / "audio" / (key or self.settings_key()) / f"{idx}.ogg"

    def is_ready(self, doc_id: str, idx: int) -> bool:
        return self.audio_path(doc_id, idx).exists()

    def is_failed(self, doc_id: str, idx: int) -> bool:
        return (doc_id, self.settings_key(), idx) in self._failed

    def _enforce_cache_limit(self, keep_doc: str):
        files = [(p.stat().st_mtime, p.stat().st_size, p) for p in (self.data_dir / "docs").glob("*/audio/*/*.ogg")]
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= CACHE_LIMIT_BYTES:
                break
            if p.parts[-4] == keep_doc and time.time() - p.stat().st_mtime < 3600:
                continue
            p.unlink(missing_ok=True)
            total -= size

    # ---------- agendamento ----------
    def set_cursor(self, doc_id: str, texts: list[str], cursor: int):
        with self._cond:
            self._job = {"doc_id": doc_id, "texts": texts, "cursor": cursor}
            self._cond.notify_all()

    def ready_ahead(self, doc_id: str, cursor: int, limit: int = LOOKAHEAD * 2) -> list[int]:
        return [i for i in range(cursor, cursor + limit) if self.is_ready(doc_id, i)]

    def _reader_task(self, ahead: int):
        job = self._job
        if not job:
            return None
        key = self.settings_key()
        texts = job["texts"]
        for i in range(job["cursor"], min(job["cursor"] + ahead, len(texts))):
            if (job["doc_id"], key, i) in self._failed:
                continue
            if not self.audio_path(job["doc_id"], i, key).exists():
                voice = self.voice_path if self.voice_path.exists() else None
                return {"kind": "reader", "doc_id": job["doc_id"], "idx": i, "text": texts[i],
                        "key": key, "settings": dict(self.settings), "voice": voice}
        return None

    def _export_task(self):
        for job in self._exports.values():
            i = job["done"]
            voice = self.exports_dir / job["id"] / "voice.wav"
            text = job["texts"][i]
            if job.get("style"):
                text = f"({job['style']}){text}"
            return {"kind": "export", "job": job["id"], "idx": i, "text": text,
                    "settings": job["settings"], "voice": voice if voice.exists() else None}
        return None

    def _next_task(self):
        """Prioridade: próximos trechos do leitor > exportações > resto do buffer do leitor."""
        if self.model is None:
            return None
        return self._reader_task(URGENT_AHEAD) or self._export_task() or self._reader_task(LOOKAHEAD)

    # ---------- exportação (texto -> arquivo de áudio) ----------
    def _job_dir(self, job_id: str) -> Path:
        return self.exports_dir / job_id

    def _save_job(self, job: dict):
        tmp = self._job_dir(job["id"]) / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False))
        os.replace(tmp, tmp.with_suffix(""))

    def _public_job(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in ("texts", "paras")}

    def _resume_exports(self):
        for p in sorted(self.exports_dir.glob("*/job.json"), key=lambda p: p.stat().st_mtime):
            job = json.loads(p.read_text())
            if job["status"] in ("queued", "running"):
                parts = self._job_dir(job["id"]) / "parts"
                done = 0
                while (parts / f"{done}.wav").exists():
                    done += 1
                job["done"] = done
                self._exports[job["id"]] = job

    def create_export(self, job_id: str, title: str, texts: list[str], paras: list[int], style: str | None,
                      settings: dict) -> dict:
        folder = self._job_dir(job_id)
        (folder / "parts").mkdir(parents=True, exist_ok=True)
        if self.voice_path.exists():  # congela a voz atual para este job
            shutil.copy(self.voice_path, folder / "voice.wav")
        job = {"id": job_id, "title": title, "created": time.time(), "status": "queued",
               "total": len(texts), "done": 0, "texts": texts, "paras": paras, "style": style,
               "settings": settings, "voice": self.voice_path.exists(), "duration": None, "error": None}
        with self._cond:
            self._save_job(job)
            self._exports[job_id] = job
            self._cond.notify_all()
        return self._public_job(job)

    def list_exports(self) -> list[dict]:
        jobs = [json.loads(p.read_text()) for p in self.exports_dir.glob("*/job.json")]
        with self._cond:
            for j in jobs:  # progresso em memória é mais recente que o do disco
                if j["id"] in self._exports:
                    j.update(done=self._exports[j["id"]]["done"], status=self._exports[j["id"]]["status"])
        return sorted((self._public_job(j) for j in jobs), key=lambda j: j["created"], reverse=True)

    def get_export(self, job_id: str) -> dict | None:
        p = self._job_dir(job_id) / "job.json"
        if not p.exists():
            return None
        with self._cond:
            if job_id in self._exports:
                return self._public_job(self._exports[job_id])
        return self._public_job(json.loads(p.read_text()))

    def delete_export(self, job_id: str):
        with self._cond:  # o worker só grava partes segurando o lock, então não há corrida
            self._exports.pop(job_id, None)
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def _export_chunk_done(self, task: dict, wav):
        job_id, idx = task["job"], task["idx"]
        with self._cond:
            job = self._exports.get(job_id)
            if job is None or job["done"] != idx:  # cancelado no meio da geração
                return
            sf.write(self._job_dir(job_id) / "parts" / f"{idx}.wav", wav, self.sample_rate, subtype="PCM_16")
            job["done"] = idx + 1
            job["status"] = "running"
            finished = job["done"] == job["total"]
            if finished:
                self._exports.pop(job_id)
            self._save_job(job)
        self.on_event("exports", {"type": "export_progress", "job": job_id, "done": job["done"], "total": job["total"]})
        if finished:
            self._finalize_export(job)

    def _finalize_export(self, job: dict):
        folder = self._job_dir(job["id"])
        sr = self.sample_rate
        pieces = []
        for i in range(job["total"]):
            if i:
                gap = PAUSE_PARAGRAPH if job["paras"][i] != job["paras"][i - 1] else PAUSE_CHUNK
                pieces.append(np.zeros(int(sr * gap), dtype=np.float32))
            pieces.append(sf.read(folder / "parts" / f"{i}.wav", dtype="float32")[0])
        audio = np.concatenate(pieces)
        tmp = folder / "audio.tmp.mp3"
        sf.write(tmp, audio, sr, format="MP3", subtype="MPEG_LAYER_III")
        os.replace(tmp, folder / "audio.mp3")
        shutil.rmtree(folder / "parts", ignore_errors=True)
        job.update(status="done", duration=round(len(audio) / sr, 1))
        self._save_job(job)
        print(f"[engine] exportação {job['id']} pronta: {job['duration']}s", file=sys.stderr)
        self.on_event("exports", {"type": "export_done", "job": job["id"], "duration": job["duration"]})

    def _export_failed(self, task: dict, error: str):
        with self._cond:
            job = self._exports.pop(task["job"], None)
            if job is None:
                return
            job.update(status="error", error=error)
            self._save_job(job)
        self.on_event("exports", {"type": "export_error", "job": job["id"], "error": error})

    # ---------- worker ----------
    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tts-worker").start()

    def _load(self):
        patch_xpu()
        from voxcpm import VoxCPM

        t0 = time.time()
        self.model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, optimize=False)
        self.sample_rate = self.model.tts_model.sample_rate
        print(f"[engine] modelo carregado em {time.time() - t0:.0f}s", file=sys.stderr)
        self.status = "ready"

    def _run(self):
        try:
            self._load()
        except Exception:
            traceback.print_exc()
            self.status = "error"
            return
        while True:
            with self._cond:
                task = self._next_task()
                while task is None:
                    self._cond.wait()
                    task = self._next_task()
            try:
                t0 = time.time()
                if task["kind"] == "reader":
                    self.on_event(task["doc_id"], {"type": "generating", "id": task["idx"]})
                wav = self._synth(task)
                gen = time.time() - t0
                dur = len(wav) / self.sample_rate
                label = task.get("doc_id", task.get("job", ""))[:8]
                print(f"[engine] {task['kind']} {label}#{task['idx']} {dur:.1f}s áudio em {gen:.1f}s", file=sys.stderr)
                if task["kind"] == "reader":
                    self._reader_chunk_done(task, wav, dur)
                else:
                    self._export_chunk_done(task, wav)
            except Exception as e:
                traceback.print_exc()
                if task["kind"] == "reader":
                    with self._cond:
                        self._failed.add((task["doc_id"], task["key"], task["idx"]))
                    self.on_event(task["doc_id"], {"type": "chunk_error", "id": task["idx"], "error": str(e)})
                else:
                    self._export_failed(task, str(e))

    def _synth(self, task: dict):
        settings = task["settings"]
        torch.manual_seed(settings["seed"])
        return self.model.generate(
            text=task["text"],
            reference_wav_path=str(task["voice"]) if task["voice"] else None,
            cfg_value=settings["cfg"],
            inference_timesteps=settings["steps"],
        )

    def _reader_chunk_done(self, task: dict, wav, dur: float):
        doc_id, idx = task["doc_id"], task["idx"]
        path = self.audio_path(doc_id, idx, task["key"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{idx}.tmp.ogg")
        sf.write(tmp, wav, self.sample_rate, format="OGG", subtype="OPUS")
        os.replace(tmp, path)
        self.on_event(doc_id, {"type": "chunk_ready", "id": idx, "duration": round(dur, 2)})
        self._saves += 1
        if self._saves % 20 == 0:
            self._enforce_cache_limit(doc_id)
