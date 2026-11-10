import io
import threading
import time

import numpy as np
import pymupdf
import pytest
import soundfile as sf

from app.tts.config import SynthesisConfig

SR = 16000
CHUNK_SECONDS = 0.1


class FakeSynth:
    """Model-free synthesizer: returns 0.1 s of audio and fails on texts containing "FALHA"."""

    def __init__(self, load_error: Exception | None = None):
        self.sample_rate = SR
        self.load_error = load_error
        self.calls: list[tuple[str, dict, object]] = []

    def load(self):
        if self.load_error:
            raise self.load_error

    def generate(self, text, settings, voice):
        self.calls.append((text, settings, voice))
        if "FALHA" in text:
            raise RuntimeError("boom")
        return np.full(int(SR * CHUNK_SECONDS), 0.1, dtype=np.float32)


class Recorder:
    """Publisher that records the published events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, channel, event):
        self.events.append((channel, event))

    def of(self, channel, type_=None):
        return [e for c, e in self.events if c == channel and (type_ is None or e["type"] == type_)]


def wait_until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def notifies(wakeup: threading.Condition, action) -> bool:
    """True if `action` wakes a thread waiting on `wakeup`."""
    ready, woke = threading.Event(), threading.Event()

    def waiter():
        with wakeup:
            ready.set()
            if wakeup.wait(timeout=2):
                woke.set()

    t = threading.Thread(target=waiter)
    t.start()
    ready.wait()
    action()
    t.join()
    return woke.is_set()


def make_pdf(pages: list[list[str]]) -> bytes:
    """One text block per item, well separated vertically."""
    doc = pymupdf.open()
    for blocks in pages:
        page = doc.new_page()
        for i, text in enumerate(blocks):
            page.insert_text((72, 72 + 120 * i), text)
    return doc.tobytes()


def wav_bytes(seconds: float, sr: int = 8000, channels: int = 2) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros((int(sr * seconds), channels), dtype=np.float32), sr, format="WAV")
    return buf.getvalue()


def fake_wav() -> np.ndarray:
    return np.full(int(SR * CHUNK_SECONDS), 0.1, dtype=np.float32)


@pytest.fixture
def wakeup():
    return threading.Condition()


@pytest.fixture
def publish():
    return Recorder()


@pytest.fixture
def synthesis(tmp_path, wakeup):
    return SynthesisConfig(tmp_path, wakeup)
