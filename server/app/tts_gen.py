"""说话 TTS：实时默认 CosyVoice 3；其它场景可 breeze / voicedesign。

instruction 由固定抽卡文案 + 情绪语气组成（见 media_cards.speak_instructions）。
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
    data = {
        "text": text[:2000],
        "instruction": instructions[:4000],
        "cfg_scale": str(cfg_scale),
    }
    if seed is not None:
        data["seed"] = str(int(seed) & 0x7FFFFFFF)
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, data=data)
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


def _cosyvoice_wav(*, text: str, instructions: str, timeout: float) -> Optional[bytes]:
    """Fun-CosyVoice3 网关：固定参考声线 + instruction 控情绪。"""
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:8885").rstrip("/")
    if not base:
        return None
    url = f"{base}/v1/audio/speech"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                url,
                data={
                    "text": text[:500],
                    "instruction": (instructions or "")[:800],
                },
            )
            if r.status_code >= 400 or not r.content or len(r.content) < 200:
                return None
            if r.content[:4] == b"RIFF":
                return r.content
            return _pcm16_mono_to_wav(r.content, 24000)
    except Exception:
        return None


def resolve_tts_engine(*, for_realtime: bool = False) -> str:
    settings = get_settings()
    if for_realtime:
        rt = (getattr(settings, "tts_realtime_engine", None) or "").strip().lower()
        if rt:
            return rt
    return (settings.tts_engine or "breeze").strip().lower() or "breeze"


def synthesize_voice_wav(
    *,
    text: str,
    instructions: str,
    timeout: float = 0,
    seed: Optional[int] = None,
    keep_loaded: bool = False,
    engine: Optional[str] = None,
    for_realtime: bool = False,
) -> Optional[bytes]:
    """按音色描述 instructions 合成一段 WAV；失败返回 None。

    keep_loaded=True：合成后不立刻 unload（实时逐句对话用）。
    engine / for_realtime：指定引擎；实时默认走 tts_realtime_engine。
    """
    import random

    text = (text or "").strip()
    instructions = (instructions or "").strip()
    if not text or not instructions:
        return None
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
    eng = (engine or resolve_tts_engine(for_realtime=for_realtime)).strip().lower()
    use_seed = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
    cfg = round(3.2 + random.random() * 1.6, 2)
    from . import tts_ondemand

    def _with_engine(name: str, fn):
        try:
            tts_ondemand.ensure_engine(name)
            return fn()
        finally:
            tts_ondemand.release_engine(name, unload_now=not keep_loaded)

    if eng == "cosyvoice":
        wav = _with_engine(
            "cosyvoice",
            lambda: _cosyvoice_wav(text=text, instructions=instructions, timeout=to),
        )
        if wav:
            return wav
        # 回退 breeze，保证通话不哑
        return _with_engine(
            "breeze",
            lambda: _breeze_wav(
                text=text, instructions=instructions, timeout=to, seed=use_seed, cfg_scale=cfg
            ),
        )

    if eng == "breeze":
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
    if eng == "voicedesign":
        return _with_engine(
            "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
        )
    # 未知引擎：试 cosy → breeze
    return _with_engine(
        "cosyvoice",
        lambda: _cosyvoice_wav(text=text, instructions=instructions, timeout=to),
    ) or _with_engine(
        "breeze",
        lambda: _breeze_wav(
            text=text, instructions=instructions, timeout=to, seed=use_seed, cfg_scale=cfg
        ),
    )


def warm_tts_engine(*, for_realtime: bool = True) -> dict:
    """启动预热：ensure 引擎 + 极短合成，压掉冷启动。"""
    from . import tts_ondemand

    engine = resolve_tts_engine(for_realtime=for_realtime)
    try:
        tts_ondemand.ensure_engine(engine)
    except Exception as e:
        return {"ok": False, "engine": engine, "error": str(e)}
    wav = synthesize_voice_wav(
        text="嗯。",
        instructions="自然短促地应答一声，像微信语音。",
        timeout=120,
        keep_loaded=True,
        engine=engine,
        for_realtime=for_realtime,
    )
    return {
        "ok": bool(wav),
        "engine": engine,
        "warmed": bool(wav),
        "error": None if wav else "预热合成失败",
    }
