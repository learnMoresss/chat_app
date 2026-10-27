"""说话 TTS（确定路线）：Breeze TTS 2 + 人设固定音色 instruction；失败回退 VoiceDesign。

日常聊天说话不用 IndexTTS。instruction 由固定抽卡文案 + 情绪语气组成（见 media_cards.speak_instructions）。
"""
from __future__ import annotations

import io
import wave
from typing import Optional

import httpx

from .config import get_settings


def _pcm16_mono_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _breeze_wav(
    *,
    text: str,
    instructions: str,
    timeout: float,
    seed: Optional[int] = None,
    cfg_scale: float = 4.0,
) -> Optional[bytes]:
    settings = get_settings()
    base = (settings.tts_breeze_url or settings.tts_speech_url or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/v1/audio/speech"
    # 提示词优先：instruction 里用户/抽卡文案在前；seed/cfg 只做听感扰动
    data = {
        "text": text[:2000],
        "instruction": instructions[:4000],
        "cfg_scale": str(cfg_scale),
    }
    if seed is not None:
        data["seed"] = str(int(seed) & 0x7FFFFFFF)
    try:
        with httpx.Client(timeout=timeout) as client:
            # 官方 API 用 multipart；Voice Design = text + instruction + cfg_scale
            r = client.post(
                url,
                data=data,
            )
            if r.status_code >= 400 or not r.content or len(r.content) < 200:
                return None
            ctype = (r.headers.get("content-type") or "").lower()
            if "wav" in ctype or r.content[:4] == b"RIFF":
                return r.content
            return _pcm16_mono_to_wav(r.content, 24000)
    except Exception:
        return None


def _voicedesign_wav(*, text: str, instructions: str, timeout: float) -> Optional[bytes]:
    settings = get_settings()
    base = (settings.tts_voicedesign_url or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/v1/audio/speech"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                url,
                json={
                    "model": "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
                    "voice": "default",
                    "input": text[:2000],
                    "task_type": "VoiceDesign",
                    "instructions": instructions[:4000],
                },
            )
            if r.status_code >= 400 or not r.content or len(r.content) < 200:
                return None
            return r.content
    except Exception:
        return None


def synthesize_voice_wav(
    *,
    text: str,
    instructions: str,
    timeout: float = 0,
    seed: Optional[int] = None,
) -> Optional[bytes]:
    """按音色描述 instructions 合成一段 WAV；失败返回 None。

    同一提示词可传不同 seed / 轻微 cfg 扰动，抽卡听感拉开；instructions 始终优先遵从。
    """
    import random

    text = (text or "").strip()
    instructions = (instructions or "").strip()
    if not text or not instructions:
        return None
    # 截断过狠会导致「发音不完整」：保留完整短句，长句按标点切前段
    if len(text) > 120:
        for sep in ("。", "！", "？", "；", "\n", ".", "!", "?"):
            i = text.find(sep)
            if 8 <= i <= 120:
                text = text[: i + 1]
                break
        else:
            text = text[:120]
    settings = get_settings()
    to = timeout or float(settings.tts_timeout_seconds or 300)
    engine = (settings.tts_engine or "breeze").strip().lower()
    use_seed = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
    cfg = round(3.2 + random.random() * 1.6, 2)  # 3.2～4.8
    from . import tts_ondemand

    def _with_engine(name: str, fn):
        try:
            tts_ondemand.ensure_engine(name)
            return fn()
        finally:
            tts_ondemand.release_engine(name, unload_now=True)

    if engine == "breeze":
        wav = _with_engine(
            "breeze",
            lambda: _breeze_wav(
                text=text, instructions=instructions, timeout=to, seed=use_seed, cfg_scale=cfg
            ),
        )
        if wav:
            return wav
        return _with_engine(
            "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
        )
    if engine == "voicedesign":
        return _with_engine(
            "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
        )
    return _with_engine(
        "breeze",
        lambda: _breeze_wav(
            text=text, instructions=instructions, timeout=to, seed=use_seed, cfg_scale=cfg
        ),
    ) or _with_engine(
        "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
    )
