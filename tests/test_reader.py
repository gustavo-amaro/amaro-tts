import os
import time

import pytest
import soundfile as sf

from app.tts import reader as reader_mod
from app.tts.reader import ReaderQueue
from conftest import SR, fake_wav, notifies, wav_bytes

TEXTS = [f"Trecho {i}." for i in range(10)]


@pytest.fixture
def reader(tmp_path, synthesis, wakeup, publish):
    return ReaderQueue(tmp_path / "docs", synthesis, wakeup, publish)


def touch(path, mtime=None, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_no_task_without_cursor(reader):
    assert reader.next_task(5) is None


def test_set_cursor_notifies_and_next_task_starts_at_cursor(reader, wakeup, synthesis):
    assert notifies(wakeup, lambda: reader.set_cursor("doc", TEXTS, 3))
    task = reader.next_task(2)
    assert (task.doc_id, task.idx, task.text) == ("doc", 3, "Trecho 3.")
    assert task.key == synthesis.key()
    assert task.settings == synthesis.settings and task.settings is not synthesis.settings
    assert task.voice is None
    assert task.label == "reader doc#3"


def test_next_task_skips_existing_and_respects_window(reader):
    reader.set_cursor("doc", TEXTS, 0)
    touch(reader.audio_path("doc", 0))
    assert reader.next_task(2).idx == 1
    touch(reader.audio_path("doc", 1))
    assert reader.next_task(2) is None
    assert reader.next_task(3).idx == 2


def test_next_task_stops_at_end_of_document(reader):
    reader.set_cursor("doc", TEXTS, 9)
    touch(reader.audio_path("doc", 9))
    assert reader.next_task(5) is None


def test_task_uses_reference_voice(reader, synthesis):
    synthesis.set_voice(wav_bytes(1))
    reader.set_cursor("doc", TEXTS, 0)
    assert reader.next_task(1).voice == synthesis.voice_path


def test_audio_path_depends_on_settings_key(reader, synthesis):
    old = reader.audio_path("doc", 0)
    synthesis.update(steps=12)
    assert reader.audio_path("doc", 0) != old
    assert reader.audio_path("doc", 0, key="k").parent.name == "k"


def test_started_publishes_generating(reader, publish):
    reader.set_cursor("doc", TEXTS, 0)
    reader.next_task(1).started()
    assert publish.of("doc") == [{"type": "generating", "id": 0}]


def test_done_saves_audio_and_publishes(reader, publish):
    reader.set_cursor("doc", TEXTS, 4)
    reader.next_task(1).done(fake_wav(), SR)
    path = reader.audio_path("doc", 4)
    assert sf.info(path).format == "OGG"
    assert not list(path.parent.glob(".*tmp*"))
    assert publish.of("doc", "chunk_ready") == [{"type": "chunk_ready", "id": 4, "duration": 0.1}]
    assert reader.is_ready("doc", 4) and not reader.is_ready("doc", 5)
    assert reader.ready_ahead("doc", 0) == [4]
    assert reader.ready_ahead("doc", 0, limit=4) == []


def test_failed_is_tracked_skipped_and_published(reader, publish, synthesis):
    reader.set_cursor("doc", TEXTS, 0)
    reader.next_task(1).failed("boom")
    assert reader.is_failed("doc", 0)
    assert publish.of("doc", "chunk_error") == [{"type": "chunk_error", "id": 0, "error": "boom"}]
    assert reader.next_task(2).idx == 1
    synthesis.update(steps=3)  # different key: retry
    assert not reader.is_failed("doc", 0)
    assert reader.next_task(2).idx == 0


def test_cache_limit_removes_oldest_but_keeps_recent_current_doc(reader, tmp_path, monkeypatch):
    monkeypatch.setattr(reader_mod, "CACHE_LIMIT_BYTES", 0)
    monkeypatch.setattr(reader_mod, "CACHE_CHECK_EVERY", 1)
    docs, old = tmp_path / "docs", time.time() - 7200
    other_old = docs / "other" / "audio" / "k" / "0.ogg"
    cur_old = docs / "doc" / "audio" / "k" / "1.ogg"
    cur_recent = docs / "doc" / "audio" / "k" / "2.ogg"
    touch(other_old, old)
    touch(cur_old, old + 1)
    touch(cur_recent)
    reader.set_cursor("doc", TEXTS, 0)
    reader.next_task(1).done(fake_wav(), SR)
    assert not other_old.exists() and not cur_old.exists()
    assert cur_recent.exists() and reader.is_ready("doc", 0)


def test_cache_under_limit_keeps_everything(reader, tmp_path, monkeypatch):
    monkeypatch.setattr(reader_mod, "CACHE_CHECK_EVERY", 1)
    other = tmp_path / "docs" / "other" / "audio" / "k" / "0.ogg"
    touch(other, time.time() - 7200)
    reader.set_cursor("doc", TEXTS, 0)
    reader.next_task(1).done(fake_wav(), SR)
    assert other.exists()
