"""Tests the VoxCPM integration without loading the model: VoxCPM.from_pretrained is replaced."""
import runpy
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import torch
import voxcpm
import voxcpm.model.voxcpm2 as vx2

from app.tts.synthesizer import VoxCPMSynthesizer, patch_xpu

ROOT = Path(__file__).resolve().parent.parent


class FakeModel:
    instances: list["FakeModel"] = []

    def __init__(self, **kwargs):
        self.load_kwargs = kwargs
        self.tts_model = type("TtsModel", (), {"sample_rate": 24000})()
        self.generate_kwargs = None
        FakeModel.instances.append(self)

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return np.zeros(2400, dtype=np.float32)


class FakeVoxCPM:
    @staticmethod
    def from_pretrained(model_id, **kwargs):
        return FakeModel(model_id=model_id, **kwargs)


@pytest.fixture(autouse=True)
def fake_voxcpm(monkeypatch):
    # make sure patch_xpu's global monkeypatch is undone after the test
    monkeypatch.setattr(vx2, "resolve_runtime_device", vx2.resolve_runtime_device)
    monkeypatch.setattr(voxcpm, "VoxCPM", FakeVoxCPM)
    FakeModel.instances.clear()


@pytest.mark.parametrize("available, device, expected", [
    (True, None, "xpu"), (True, "auto", "xpu"), (True, "xpu", "xpu"), (True, "cpu", "cpu"),
    (False, None, "cpu"), (False, "cuda", "cuda"),
])
def test_patch_xpu_resolves_device(monkeypatch, available, device, expected):
    monkeypatch.setattr(torch.xpu, "is_available", lambda: available)
    patch_xpu()
    assert vx2.resolve_runtime_device(device) == expected


def test_load_and_generate(tmp_path):
    synth = VoxCPMSynthesizer("modelo/teste")
    synth.load()
    model = FakeModel.instances[0]
    assert model.load_kwargs == {"model_id": "modelo/teste", "load_denoiser": False, "optimize": False}
    assert synth.sample_rate == 24000

    wav = synth.generate("olá", {"seed": 7, "cfg": 1.5, "steps": 4}, tmp_path / "v.wav")
    assert len(wav) == 2400
    assert torch.initial_seed() == 7
    assert model.generate_kwargs == {"text": "olá", "reference_wav_path": str(tmp_path / "v.wav"),
                                     "cfg_value": 1.5, "inference_timesteps": 4}
    synth.generate("oi", {"seed": 1, "cfg": 2.0, "steps": 6}, None)
    assert model.generate_kwargs["reference_wav_path"] is None


def test_tts_cli(tmp_path, monkeypatch, capsys):
    out = tmp_path / "saida.wav"
    monkeypatch.setattr(sys, "argv", ["tts.py", "Texto", "-o", str(out), "--ref", "ref.wav", "--steps", "3"])
    runpy.run_path(str(ROOT / "tts.py"), run_name="__main__")
    model = FakeModel.instances[0]
    assert model.load_kwargs["device"] == "auto"
    assert model.generate_kwargs == {"text": "Texto", "reference_wav_path": "ref.wav", "prompt_wav_path": None,
                                     "prompt_text": None, "cfg_value": 2.0, "inference_timesteps": 3}
    assert sf.info(out).samplerate == 24000
    assert f"saved: {out}" in capsys.readouterr().err


def test_tts_import_does_not_run_cli():
    module = runpy.run_path(str(ROOT / "tts.py"), run_name="tts")
    assert callable(module["main"]) and FakeModel.instances == []
