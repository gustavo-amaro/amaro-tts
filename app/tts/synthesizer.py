"""Abstração do modelo de TTS e implementação com VoxCPM2 na GPU Intel (XPU)."""
import sys
import time
from pathlib import Path
from typing import Protocol

import numpy as np
import torch


class Synthesizer(Protocol):
    sample_rate: int

    def load(self) -> None: ...

    def generate(self, text: str, settings: dict, voice: Path | None) -> np.ndarray: ...


def patch_xpu():
    """VoxCPM só conhece cuda/mps/cpu; força a XPU (Intel Arc) quando disponível."""
    import voxcpm.model.voxcpm2 as vx2

    def _resolve(device, configured_device="cuda"):
        if device in (None, "auto", "xpu") and torch.xpu.is_available():
            return "xpu"
        return device or "cpu"

    vx2.resolve_runtime_device = _resolve


class VoxCPMSynthesizer:
    def __init__(self, model_id: str = "openbmb/VoxCPM2"):
        self.model_id = model_id
        self.sample_rate = 0
        self._model = None

    def load(self):
        patch_xpu()
        from voxcpm import VoxCPM

        t0 = time.time()
        self._model = VoxCPM.from_pretrained(self.model_id, load_denoiser=False, optimize=False)
        self.sample_rate = self._model.tts_model.sample_rate
        print(f"[engine] modelo carregado em {time.time() - t0:.0f}s", file=sys.stderr)

    def generate(self, text: str, settings: dict, voice: Path | None) -> np.ndarray:
        torch.manual_seed(settings["seed"])
        return self._model.generate(
            text=text,
            reference_wav_path=str(voice) if voice else None,
            cfg_value=settings["cfg"],
            inference_timesteps=settings["steps"],
        )
