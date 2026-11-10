import json

import pytest
import soundfile as sf

from app.errors import InvalidInput
from app.tts.config import DEFAULT_SETTINGS, VOICE_MAX_SECONDS, SynthesisConfig
from conftest import notifies, wav_bytes


def test_defaults_without_file(synthesis):
    assert synthesis.settings == DEFAULT_SETTINGS
    assert synthesis.key() == "s6_c2.0_r42_novoice"
    assert not synthesis.has_voice()
    assert synthesis.voice() is None


def test_loads_persisted_settings(tmp_path, wakeup):
    (tmp_path / "settings.json").write_text(json.dumps({"steps": 9}), encoding="utf-8")
    assert SynthesisConfig(tmp_path, wakeup).settings == {**DEFAULT_SETTINGS, "steps": 9}


def test_update_persists_ignores_unknown_and_none_and_notifies(tmp_path, synthesis, wakeup):
    assert notifies(wakeup, lambda: synthesis.update(steps=10, cfg=None, bogus=1))
    assert synthesis.settings == {**DEFAULT_SETTINGS, "steps": 10}
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == synthesis.settings
    assert synthesis.key().startswith("s10_")


def test_snapshot_is_a_copy(synthesis):
    snap = synthesis.snapshot()
    snap["steps"] = 99
    assert synthesis.settings["steps"] == DEFAULT_SETTINGS["steps"]


def test_set_voice_saves_mono_truncated_and_changes_key(synthesis, wakeup):
    before = synthesis.key()
    assert notifies(wakeup, lambda: synthesis.set_voice(wav_bytes(VOICE_MAX_SECONDS + 10, channels=2)))
    info = sf.info(synthesis.voice_path)
    assert info.channels == 1
    assert info.duration == pytest.approx(VOICE_MAX_SECONDS)
    assert synthesis.has_voice() and synthesis.voice() == synthesis.voice_path
    assert synthesis.key() != before and not synthesis.key().endswith("novoice")


def test_set_voice_rejects_invalid_audio(synthesis):
    with pytest.raises(InvalidInput):
        synthesis.set_voice(b"isto nao e audio")
    assert not synthesis.has_voice()


def test_delete_voice(synthesis, wakeup):
    synthesis.set_voice(wav_bytes(1))
    assert notifies(wakeup, synthesis.delete_voice)
    assert not synthesis.has_voice()
    assert synthesis.key().endswith("novoice")
    synthesis.delete_voice()  # no voice: no error
