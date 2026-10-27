#!/usr/bin/env python3
"""General persona singing pipeline (AI-callable).

Accepted soft baseline (2026-10-26, 番茄花试听通过):
  Seed-VC stable pitch+3 + deharsh post (HP/LP/EQ/comp/echo/loudnorm).
  Demo: chat_app/tmp/sing-fanqiehua-xueer.mp3

Modes:
  compose: lyrics(+caption) -> ACE single-singer -> Seed-VC lock -> soften
  cover:   song_path -> karaoke lead -> Seed-VC one-pass -> soften

Never: manual per-song singer time ranges.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

os.environ.setdefault("COMFYUI_SEEDVC_MODEL_DIR", "/data/ai/models/audio/seed_vc")

# 已知曲名 → 已缓存源曲（cover）；未命中则走 compose 歌词
SONG_ALIASES = {
    "番茄花": "/data/ai/tmp/sing-fanqiehua/fanqiehua_src.mp3",
}

SOFTEN_AF = (
    "highpass=f=90,"
    "lowpass=f=11000,"
    "equalizer=f=3200:t=q:w=1.1:g=-3.5,"
    "equalizer=f=6500:t=q:w=1.2:g=-4.0,"
    "acompressor=threshold=-18dB:ratio=2.2:attack=12:release=120:makeup=2,"
    "aecho=0.8:0.7:28:0.12,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


def load_audio(path: Path) -> dict:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(samples.T.copy()).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": int(sample_rate)}


def save_wav(path: Path, audio: dict, subtype: str = "PCM_24") -> None:
    wave = audio["waveform"][0].detach().float().cpu().clamp(-1, 1).numpy().T
    if wave.ndim == 1:
        wave = wave[:, None]
    if wave.shape[1] == 1:
        wave = np.repeat(wave, 2, axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), wave, int(audio["sample_rate"]), subtype=subtype)


def rms(audio: dict) -> float:
    w = audio["waveform"][0].detach().float().cpu().numpy()
    return float(np.sqrt(np.mean(np.square(w))))


def duration_sec(path: Path) -> float:
    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate or 1)


def soften(in_wav: Path, out_wav: Path) -> None:
    """Accepted de-electronic post (soft baseline)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(in_wav), "-af", SOFTEN_AF, str(out_wav),
        ],
        check=True,
    )


def to_mp3(wav: Path, mp3: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(mp3),
        ],
        check=True,
    )


def separate_lead(
    source: Path,
    out_vocal: Path,
    out_instrumental: Path | None = None,
    device: str = "cuda",
) -> dict:
    """General lead extract via karaoke MelBand-RoFormer (not song-specific)."""
    os.environ.setdefault("PYMSS_MODEL_DIR", "/data/ai/models/audio/pymss")
    sys.path.insert(0, "/data/ai/apps/ComfyUI")
    sys.path.insert(0, "/data/ai/staging/seedvc/comfy-mss")
    from comfy_mss.nodes.separate import separate_model_audio

    samples, sr = sf.read(str(source), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(samples.T.copy())
    value = {
        "waveform": waveform.unsqueeze(0),
        "sample_rate": sr,
        "pymss_source_path": str(source),
    }
    outputs, names = separate_model_audio(
        audio=value,
        model_name="[karaoke] mel_band_roformer_karaoke_becruily.ckpt",
        model_kind="mss",
        params={"batch_size": 1, "normalize": False, "standardize": False},
        download_missing=False,
        source="modelscope",
        device=device,
        device_ids_raw="0",
        use_tta=False,
        debug=False,
    )
    name_l = [str(n).lower() for n in names]
    idx = next(i for i, n in enumerate(name_l) if n.startswith("vocal"))
    audio = {
        "waveform": outputs[idx]["waveform"].detach().cpu().clamp(-1, 1),
        "sample_rate": int(outputs[idx]["sample_rate"]),
    }
    peak = float(audio["waveform"].abs().max())
    if peak > 1e-6:
        audio["waveform"] = audio["waveform"] * (0.70 / peak)
    save_wav(out_vocal, audio)
    instr_path = None
    if out_instrumental is not None:
        # karaoke 模型通常另有 instrumental / other
        j = next(
            (i for i, n in enumerate(name_l) if "instrument" in n or n in ("other", "accompaniment", "no_vocals")),
            None,
        )
        if j is None:
            # 源曲减人声粗伴奏
            src = load_audio(source)
            v = audio["waveform"]
            # 对齐长度
            sw = src["waveform"]
            min_t = min(sw.shape[-1], v.shape[-1])
            inst_w = (sw[..., :min_t] - v[..., :min_t]).clamp(-1, 1)
            inst = {"waveform": inst_w, "sample_rate": int(src["sample_rate"])}
        else:
            inst = {
                "waveform": outputs[j]["waveform"].detach().cpu().clamp(-1, 1),
                "sample_rate": int(outputs[j]["sample_rate"]),
            }
        save_wav(out_instrumental, inst)
        instr_path = str(out_instrumental)
    return {
        "names": list(map(str, names)),
        "rms": rms(audio),
        "path": str(out_vocal),
        "instrumental": instr_path,
    }


def mix_vocal_instrumental(vocal: Path, instrumental: Path, out_wav: Path, vocal_gain: float = 1.0, inst_gain: float = 0.85) -> None:
    """带伴奏混回：人声已 soft，伴奏不走 deharsh。"""
    v = load_audio(vocal)
    i = load_audio(instrumental)
    sr = int(v["sample_rate"])
    if int(i["sample_rate"]) != sr:
        # 简单：要求同采样率；不同则 ffmpeg 混
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(vocal), "-i", str(instrumental),
                "-filter_complex",
                f"[0:a]volume={vocal_gain}[v];[1:a]volume={inst_gain}[b];[v][b]amix=inputs=2:duration=longest:dropout_transition=0[a]",
                "-map", "[a]", str(out_wav),
            ],
            check=True,
        )
        return
    vw = v["waveform"]
    iw = i["waveform"]
    min_t = min(vw.shape[-1], iw.shape[-1])
    mixed = (vw[..., :min_t] * float(vocal_gain) + iw[..., :min_t] * float(inst_gain)).clamp(-1, 1)
    peak = float(mixed.abs().max())
    if peak > 0.99:
        mixed = mixed * (0.95 / peak)
    save_wav(out_wav, {"waveform": mixed, "sample_rate": sr})


def seedvc_lock_soft(
    source: Path,
    reference: Path,
    out_path: Path,
    *,
    diffusion_steps: int = 45,
    pitch_shift: int = 3,
    reference_seconds: float = 18.0,
    cfg: float = 0.65,
) -> dict:
    """One-pass Seed-VC with accepted soft defaults (pitch+3, milder cfg)."""
    sys.path.insert(0, "/data/ai/apps/ComfyUI")
    sys.path.insert(0, "/data/ai/staging/seedvc/ComfyUI-Timbre-Transfer")
    import nodes

    model = nodes._get_model("cuda", "fp16", False)
    t0 = time.time()
    converted = nodes.SeedVCVoiceConversion().convert(
        load_audio(source),
        load_audio(reference),
        1.0,
        int(diffusion_steps),
        float(cfg),
        float(reference_seconds),
        False,
        int(pitch_shift),
        1.0,
        20261026,
        0.0,
        True,
        -1.5,
        model=model,
        authorization_confirmed=True,
    )[0]
    elapsed = time.time() - t0
    save_wav(out_path, converted)
    return {"path": str(out_path), "rms": rms(converted), "seconds": elapsed}


def ace_compose(toml_path: Path, log_path: Path) -> Path:
    env = os.environ.copy()
    env["PYTHONPATH"] = "/data/ai/apps/ace-step:" + env.get("PYTHONPATH", "")
    env["CUDA_VISIBLE_DEVICES"] = env.get("CUDA_VISIBLE_DEVICES", "0")
    py = "/data/ai/venvs/ace-step-candidate/bin/python"
    with open(log_path, "w") as log:
        subprocess.run(
            [py, "/data/ai/apps/ace-step/cli.py", "-c", str(toml_path)],
            cwd="/data/ai/apps/ace-step",
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    import toml

    cfg = toml.load(toml_path)
    save_dir = Path(cfg["save_dir"])
    wavs = sorted(save_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wavs:
        raise RuntimeError(f"ACE produced no wav in {save_dir}")
    return wavs[0]


def _looks_like_gibberish_vocal(wav: Path) -> bool:
    """粗门禁：过静 / 过短则失败（完整歌词 ASR 另线）。"""
    try:
        if duration_sec(wav) < 4.0:
            return True
        audio = load_audio(wav)
        if rms(audio) < 0.02:
            return True
    except Exception:
        return True
    return False


def resolve_song(prompt: str) -> Path | None:
    key = re.sub(r"\s+", "", (prompt or "").strip())
    for name, path in SONG_ALIASES.items():
        if name in key or key in name:
            p = Path(path)
            if p.is_file():
                return p
    # 直接路径
    p = Path(prompt.strip())
    if p.is_file():
        return p
    return None


def run_job(
    *,
    out_dir: str | Path,
    persona_ref: str | Path,
    arrangement: str = "a_cappella",
    prompt: str = "",
    ace_toml: str | Path | None = None,
    song: str | Path | None = None,
    pitch_shift: int = 3,
    diffusion_steps: int = 45,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref = Path(persona_ref)
    if not ref.is_file():
        raise FileNotFoundError(f"persona_ref missing: {ref}")
    arrangement = arrangement if arrangement in ("a_cappella", "with_backing") else "a_cappella"
    report: dict = {
        "arrangement": arrangement,
        "persona_ref": str(ref),
        "baseline": "stable_soft_pitch3_deharsh",
    }

    song_path = Path(song) if song else resolve_song(prompt)
    instrumental_path: Path | None = None
    if song_path is not None and song_path.is_file():
        mode = "cover"
        report["mode"] = mode
        report["song"] = str(song_path)
        instr_out = out / "cover_instrumental.wav" if arrangement == "with_backing" else None
        sep = separate_lead(song_path, out / "cover_lead.wav", out_instrumental=instr_out)
        report["separate"] = sep
        if sep["rms"] < 0.02:
            raise RuntimeError(f"cover lead too quiet: {sep}")
        if sep.get("instrumental"):
            instrumental_path = Path(sep["instrumental"])
        locked = seedvc_lock_soft(
            Path(sep["path"]),
            ref,
            out / "persona_vocal_raw.wav",
            diffusion_steps=diffusion_steps,
            pitch_shift=pitch_shift,
        )
        report["seedvc"] = locked
    else:
        mode = "compose"
        report["mode"] = mode
        if not ace_toml:
            raise RuntimeError(
                "compose 需要 --ace-toml 与可辨认歌词；已知曲请用曲名走 cover。"
                "质量门：时长≥4s 且 RMS≥0.02，否则失败不交付。"
            )
        ace_wav = ace_compose(Path(ace_toml), out / "ace.log")
        report["ace_wav"] = str(ace_wav)
        source_vocal = out / "compose_source.wav"
        if arrangement == "with_backing":
            sep = separate_lead(
                ace_wav,
                out / "compose_lead.wav",
                out_instrumental=out / "compose_instrumental.wav",
            )
            report["separate"] = sep
            source_vocal = Path(sep["path"])
            if sep.get("instrumental"):
                instrumental_path = Path(sep["instrumental"])
            if sep["rms"] < 0.02:
                raise RuntimeError(f"compose lead too quiet: {sep}")
        else:
            save_wav(source_vocal, load_audio(ace_wav))
        if _looks_like_gibberish_vocal(source_vocal):
            raise RuntimeError("compose quality gate failed: vocal too short/quiet (need ≥4s & RMS≥0.02)")
        locked = seedvc_lock_soft(
            source_vocal,
            ref,
            out / "persona_vocal_raw.wav",
            diffusion_steps=diffusion_steps,
            pitch_shift=pitch_shift,
        )
        report["seedvc"] = locked

    soft_vocal = out / "persona_vocal_soft.wav"
    soften(Path(locked["path"]), soft_vocal)
    final_wav = out / "persona_sing.wav"
    if arrangement == "with_backing" and instrumental_path is not None and instrumental_path.is_file():
        mix_vocal_instrumental(soft_vocal, instrumental_path, final_wav)
        report["mix"] = {"vocal": str(soft_vocal), "instrumental": str(instrumental_path)}
    else:
        # 清唱：直接用柔化人声
        import shutil

        shutil.copyfile(soft_vocal, final_wav)
    final_mp3 = out / "persona_sing.mp3"
    to_mp3(final_wav, final_mp3)
    report["final_wav"] = str(final_wav)
    report["final_mp3"] = str(final_mp3)
    report["final_rms"] = rms(load_audio(final_wav))
    report["duration_sec"] = duration_sec(final_wav)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["compose", "cover", "auto"], default="auto")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--persona-ref", type=Path, required=True)
    ap.add_argument("--arrangement", choices=["a_cappella", "with_backing"], default="a_cappella")
    ap.add_argument("--prompt", type=str, default="", help="曲名或歌词；auto 时曲名命中别名则 cover")
    ap.add_argument("--ace-toml", type=Path, help="compose: ACE config")
    ap.add_argument("--song", type=Path, help="cover: source song")
    ap.add_argument("--pitch-shift", type=int, default=3)
    ap.add_argument("--diffusion-steps", type=int, default=45)
    args = ap.parse_args()

    song = args.song
    ace = args.ace_toml
    if args.mode == "cover" and song is None:
        song = resolve_song(args.prompt)
        if song is None:
            raise SystemExit("cover requires --song or known --prompt alias")
    if args.mode == "compose" and ace is None:
        raise SystemExit("compose requires --ace-toml")

    report = run_job(
        out_dir=args.out_dir,
        persona_ref=args.persona_ref,
        arrangement=args.arrangement,
        prompt=args.prompt,
        ace_toml=ace,
        song=song,
        pitch_shift=args.pitch_shift,
        diffusion_steps=args.diffusion_steps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
