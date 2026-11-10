"""Single thread that keeps the model on the GPU and runs synthesis tasks in priority order."""
import sys
import threading
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from .synthesizer import Synthesizer


class Task(ABC):
    """A chunk to synthesize. Each source (reader, export) decides what to do with the result."""

    def __init__(self, text: str, settings: dict, voice: Path | None, label: str):
        self.text = text
        self.settings = settings
        self.voice = voice
        self.label = label

    def started(self):
        pass

    @abstractmethod
    def done(self, wav: np.ndarray, sample_rate: int): ...

    @abstractmethod
    def failed(self, error: str): ...


TaskSource = Callable[[], Task | None]


class TtsWorker:
    def __init__(self, synthesizer: Synthesizer, wakeup: threading.Condition, sources: Sequence[TaskSource]):
        """`sources` are polled in order (with `wakeup` held); the first task found wins."""
        self._synth = synthesizer
        self._wakeup = wakeup
        self._sources = sources
        self.status = "loading"

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tts-worker").start()

    def _next_task(self) -> Task | None:
        if self.status != "ready":
            return None
        for source in self._sources:
            if task := source():
                return task
        return None

    def _run(self):
        try:
            self._synth.load()
            self.status = "ready"
        except Exception:
            traceback.print_exc()
            self.status = "error"
            return
        while True:
            with self._wakeup:
                task = self._next_task()
                while task is None:
                    self._wakeup.wait()
                    task = self._next_task()
            self._execute(task)

    def _execute(self, task: Task):
        try:
            t0 = time.time()
            task.started()
            wav = self._synth.generate(task.text, task.settings, task.voice)
            sr = self._synth.sample_rate
            print(f"[engine] {task.label} {len(wav) / sr:.1f}s audio in {time.time() - t0:.1f}s", file=sys.stderr)
            task.done(wav, sr)
        except Exception as e:
            traceback.print_exc()
            task.failed(str(e))
