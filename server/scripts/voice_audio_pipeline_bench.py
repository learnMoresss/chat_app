#!/usr/bin/env python3
"""音频链路压测：ASR → 写死回复文本 → CosyVoice TTS（不经 DSH）。

用法（在 AI 主机 chat_app/server 下）:
  .venv/bin/python scripts/voice_audio_pipeline_bench.py \\
      --audio /tmp/voice_latency/short.wav \\
      --reply '在的，我听得到。'
"""
from __future__ import annotations

import argparse
import io
import json
import time
import wave
from pathlib import Path

import httpx


def _wav_dur(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def asr_http(url: str, audio: Path, language: str, timeout: float) -> dict:
    t0 = time.perf_counter()
    with audio.open("rb") as f:
        r = httpx.post(
            url,
            files={"file": (audio.name, f, "application/octet-stream")},
            data={"model": "sensevoice-small", "language": language},
            timeout=timeout,
        )
    total_ms = round((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    body = r.json()
    return {
        "text": (body.get("text") or "").strip(),
        "client_ms": total_ms,
        "server": {k: body.get(k) for k in ("device", "model", "decode_ms", "total_ms", "loaded_once")},
    }


def tts_full(url: str, text: str, instruction: str, timeout: float) -> dict:
    t0 = time.perf_counter()
    r = httpx.post(
        url,
        data={"text": text, "instruction": instruction},
        timeout=timeout,
    )
    total_ms = round((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    data = r.content
    dur = None
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as w:
            dur = round(w.getnframes() / float(w.getframerate()), 3)
    return {"client_ms": total_ms, "bytes": len(data), "audio_s": dur, "data": data}


def tts_stream_first(url: str, text: str, instruction: str, timeout: float) -> dict:
    """测首块 PCM 时间；要求网关 /v1/audio/speech/stream。"""
    t0 = time.perf_counter()
    first_ms = None
    chunks = 0
    total_bytes = 0
    sample_rate = None
    with httpx.stream(
        "POST",
        url,
        data={"text": text, "instruction": instruction},
        timeout=timeout,
    ) as r:
        r.raise_for_status()
        sample_rate = r.headers.get("x-sample-rate")
        for chunk in r.iter_bytes():
            if not chunk:
                continue
            if first_ms is None:
                first_ms = round((time.perf_counter() - t0) * 1000)
            chunks += 1
            total_bytes += len(chunk)
    last_ms = round((time.perf_counter() - t0) * 1000)
    return {
        "first_pcm_ms": first_ms,
        "total_ms": last_ms,
        "chunks": chunks,
        "bytes": total_bytes,
        "sample_rate": sample_rate,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--reply", default="在的，我听得到。")
    ap.add_argument("--instruction", default="用自然口语、短句回答，像微信语音。")
    ap.add_argument("--asr-url", default="http://127.0.0.1:8886/v1/audio/transcriptions")
    ap.add_argument("--tts-url", default="http://127.0.0.1:8885/v1/audio/speech")
    ap.add_argument("--tts-stream-url", default="http://127.0.0.1:8885/v1/audio/speech/stream")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--out", type=Path, default=Path("/tmp/voice_latency/bench_out.wav"))
    ap.add_argument("--skip-stream", action="store_true")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    if not args.audio.is_file():
        raise SystemExit(f"audio missing: {args.audio}")

    audio_s = round(_wav_dur(args.audio), 3)
    print(json.dumps({"phase": "meta", "audio": str(args.audio), "audio_s": audio_s, "reply": args.reply}, ensure_ascii=False))

    # warm / multi-run ASR
    asr_runs = []
    for i in range(max(1, args.runs)):
        row = asr_http(args.asr_url, args.audio, args.language, timeout=120.0)
        asr_runs.append(row)
        print(json.dumps({"phase": "asr", "run": i + 1, **{k: v for k, v in row.items() if k != "text"}, "text": row["text"]}, ensure_ascii=False))

    # mock DSH: fixed reply
    print(json.dumps({"phase": "dsh_mock", "text": args.reply, "note": "hardcoded, no LLM"}, ensure_ascii=False))

    tts = tts_full(args.tts_url, args.reply, args.instruction, timeout=180.0)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(tts["data"])
    print(
        json.dumps(
            {
                "phase": "tts_full",
                "client_ms": tts["client_ms"],
                "audio_s": tts["audio_s"],
                "bytes": tts["bytes"],
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )

    stream = None
    if not args.skip_stream:
        try:
            stream = tts_stream_first(args.tts_stream_url, args.reply, args.instruction, timeout=180.0)
            print(json.dumps({"phase": "tts_stream", **stream}, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"phase": "tts_stream", "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))

    best_asr = min(r["client_ms"] for r in asr_runs)
    e2e_no_dsh = best_asr + tts["client_ms"]
    first_audio_est = None
    if stream and stream.get("first_pcm_ms") is not None:
        first_audio_est = best_asr + int(stream["first_pcm_ms"])
    print(
        json.dumps(
            {
                "phase": "summary",
                "asr_best_ms": best_asr,
                "dsh_ms": 0,
                "tts_full_ms": tts["client_ms"],
                "tts_first_pcm_ms": None if not stream else stream.get("first_pcm_ms"),
                "e2e_full_wav_ms": e2e_no_dsh,
                "e2e_first_pcm_est_ms": first_audio_est,
                "gate_asr_under_1s": best_asr < 1000,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
