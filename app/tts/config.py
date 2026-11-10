"""Synthesis parameters persisted on disk: settings.json and the reference voice (voice.wav)."""
import hashlib
import io
import json
import threading
from pathlib import Path

import soundfile as sf

from ..errors import InvalidInput

DEFAULT_SETTINGS = {"steps": 6, "cfg": 2.0, "seed": 42}
VOICE_MAX_SECONDS = 30


class SynthesisConfig:
    def __init__(self, data_dir: Path, wakeup: threading.Condition):
        """`wakeup` is the lock shared with the worker; changes here alter what it should generate."""
        self.voice_path = data_dir / "voice.wav"
        self._settings_path = data_dir / "settings.json"
        self._wakeup = wakeup
        self.settings = dict(DEFAULT_SETTINGS)
        if self._settings_path.exists():
            self.settings.update(json.loads(self._settings_path.read_text(encoding="utf-8")))
        self._voice_hash = self._hash_voice()

    def _hash_voice(self) -> str:
        if not self.voice_path.exists():
            return "novoice"
        return hashlib.sha1(self.voice_path.read_bytes()).hexdigest()[:8]

    def key(self) -> str:
        """Identifies the settings+voice combination; audio generated under another key is stale."""
        s = self.settings
        return f"s{s['steps']}_c{s['cfg']}_r{s['seed']}_{self._voice_hash}"

    def snapshot(self) -> dict:
        return dict(self.settings)

    def has_voice(self) -> bool:
        return self.voice_path.exists()

    def voice(self) -> Path | None:
        return self.voice_path if self.voice_path.exists() else None

    def update(self, **kw):
        with self._wakeup:
            self.settings.update({k: v for k, v in kw.items() if k in DEFAULT_SETTINGS and v is not None})
            self._settings_path.write_text(json.dumps(self.settings), encoding="utf-8")
            self._wakeup.notify_all()

    def set_voice(self, content: bytes):
        try:
            wav, sr = sf.read(io.BytesIO(content), always_2d=True)
        except Exception:
            raise InvalidInput("audio_unsupported", "unsupported audio format (use wav, flac, ogg or mp3)")
        sf.write(self.voice_path, wav.mean(axis=1)[: sr * VOICE_MAX_SECONDS], sr)  # mono
        self._voice_changed()

    def delete_voice(self):
        self.voice_path.unlink(missing_ok=True)
        self._voice_changed()

    def _voice_changed(self):
        with self._wakeup:
            self._voice_hash = self._hash_voice()
            self._wakeup.notify_all()
