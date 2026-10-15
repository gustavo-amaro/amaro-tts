"""Roda o VoxCPM2 na Intel Arc (XPU) via monkeypatch do seletor de device.

Uso:
  .venv/bin/python tts.py "Texto a falar" -o saida.wav
  .venv/bin/python tts.py "Texto" --ref minha_voz.wav -o clone.wav
"""
import argparse
import sys

import soundfile as sf
import torch

from app.tts.synthesizer import patch_xpu

patch_xpu()

from voxcpm import VoxCPM  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("-o", "--output", default="out.wav")
    p.add_argument("--ref", help="áudio de referência para clonar voz")
    p.add_argument("--prompt-wav", help="áudio para clonagem 'ultimate'")
    p.add_argument("--prompt-text", help="transcrição exata de --prompt-wav")
    p.add_argument("--cfg", type=float, default=2.0)
    p.add_argument("--steps", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="auto", help="auto/xpu/cpu")
    args = p.parse_args()

    torch.manual_seed(args.seed)
    model = VoxCPM.from_pretrained(
        "openbmb/VoxCPM2", load_denoiser=False, optimize=False, device=args.device
    )
    wav = model.generate(
        text=args.text,
        reference_wav_path=args.ref,
        prompt_wav_path=args.prompt_wav,
        prompt_text=args.prompt_text,
        cfg_value=args.cfg,
        inference_timesteps=args.steps,
    )
    sf.write(args.output, wav, model.tts_model.sample_rate)
    print(f"salvo: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
