"""Runs VoxCPM2 on Intel Arc (XPU) by monkeypatching the device selector.

Usage:
  .venv/bin/python tts.py "Text to speak" -o output.wav
  .venv/bin/python tts.py "Text" --ref my_voice.wav -o clone.wav
"""
import argparse
import sys

import soundfile as sf
import torch

from app.tts.synthesizer import patch_xpu

patch_xpu()

from voxcpm import VoxCPM  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Synthesize speech with VoxCPM2.")
    p.add_argument("text", help="text to speak")
    p.add_argument("-o", "--output", default="out.wav", help="output WAV file")
    p.add_argument("--ref", help="reference audio for voice cloning")
    p.add_argument("--prompt-wav", help="audio for 'ultimate' cloning")
    p.add_argument("--prompt-text", help="exact transcript of --prompt-wav")
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
    print(f"saved: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
