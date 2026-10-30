"""说话 TTS：实时默认 CosyVoice 3；其它场景可 breeze / voicedesign。

instruction 由固定抽卡文案 + 情绪语气组成（见 media_cards.speak_instructions）。
"""
from __future__ import annotations

import io
import logging
import uuid
import wave
from typing import Callable, Optional

import httpx

from .config import get_settings

log = logging.getLogger("tts_gen")


def cosyvoice_health(*, timeout: float = 2.0) -> dict:
    """读网关 /health；失败返回空 dict。"""
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/")
    if not base:
        return {}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base}/health")
            if r.status_code >= 400:
                return {}
            body = r.json() if r.content else {}
            return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def cosyvoice_truly_warm(*, prompt_wav: str = "") -> bool:
    """模型已加载，且（无指定音色 / 该音色已在 spk_cached）。"""
    h = cosyvoice_health()
    if not h.get("model_loaded"):
        return False
    ref = (prompt_wav or "").strip()
    if not ref:
        return True
    cached = h.get("spk_cached") or []
    if not isinstance(cached, list):
        return False
    import os

    want = os.path.realpath(ref)
    return any(os.path.realpath(str(p)) == want for p in cached if p)


def cancel_cosyvoice(*, req_id: str = "") -> dict:
    """打断网关当前（或指定）合成。"""
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/")
    if not base:
        return {"ok": False, "error": "no url"}
    try:
        data = {}
        if (req_id or "").strip():
            data["req_id"] = req_id.strip()
        with httpx.Client(timeout=3.0) as client:
            r = client.post(f"{base}/v1/cancel", data=data)
            if r.status_code < 400:
                return r.json() if r.content else {"ok": True}
            return {"ok": False, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def keepalive_cosyvoice() -> dict:
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/")
    if not base:
        return {"ok": False}
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.post(f"{base}/v1/keepalive")
            return r.json() if r.content and r.status_code < 400 else {"ok": r.status_code < 400}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


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


def _cosyvoice_wav(
    *,
    text: str,
    instructions: str,
    timeout: float,
    prompt_wav: str = "",
) -> Optional[bytes]:
    """Fun-CosyVoice3 网关：固定参考声线 + instruction 控情绪。"""
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:8885").rstrip("/")
    if not base:
        return None
    url = f"{base}/v1/audio/speech"
    data = {
        "text": text[:500],
        "instruction": (instructions or "")[:800],
    }
    if (prompt_wav or "").strip():
        data["prompt_wav"] = prompt_wav.strip()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, data=data)
            if r.status_code >= 400 or not r.content or len(r.content) < 200:
                return None
            if r.content[:4] == b"RIFF":
                return r.content
            return _pcm16_mono_to_wav(r.content, 24000)
    except Exception:
        return None


def iter_cosyvoice_pcm(
    *,
    text: str,
    instructions: str,
    timeout: float = 0,
    prompt_wav: str = "",
    req_id: str = "",
    should_cancel: Optional[Callable[[], bool]] = None,
):
    """真流式：yield (pcm_s16le_bytes, sample_rate)。按网关长度帧解析，首块尽早返回。

    should_cancel 为真时主动关流并调网关 /v1/cancel，不让旧合成占锁。
    """
    settings = get_settings()
    base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/")
    if not base:
        return
    url = f"{base}/v1/audio/speech/stream"
    to = timeout or float(settings.tts_timeout_seconds or 300)
    text = (text or "").strip()
    if not text:
        return
    rid = (req_id or "").strip() or f"tts_{uuid.uuid4().hex[:12]}"
    data = {
        "text": text[:500],
        "instruction": (instructions or "")[:800],
        "req_id": rid,
    }
    if (prompt_wav or "").strip():
        data["prompt_wav"] = prompt_wav.strip()
    hop = int(getattr(settings, "voice_cosy_first_hop", 12) or 0)
    if hop > 0:
        data["first_hop"] = str(hop)
    cancelled = False
    try:
        with httpx.Client(timeout=to) as client:
            with client.stream("POST", url, data=data) as r:
                if r.status_code >= 400:
                    log.warning(
                        "cosyvoice stream http=%s body=%s",
                        r.status_code,
                        (r.read()[:200] if hasattr(r, "read") else b"")[:200],
                    )
                    return
                sr = int(r.headers.get("x-sample-rate") or "24000")
                framed = (r.headers.get("x-frame") or "").lower() == "lenprefixed"
                buf = bytearray()
                got = 0
                for chunk in r.iter_bytes():
                    if should_cancel and should_cancel():
                        cancelled = True
                        break
                    if not chunk:
                        continue
                    if not framed:
                        got += 1
                        yield chunk, sr
                        continue
                    buf.extend(chunk)
                    while len(buf) >= 4:
                        n = int.from_bytes(buf[0:4], "big")
                        if n <= 0 or n > 8_000_000:
                            buf.clear()
                            break
                        if len(buf) < 4 + n:
                            break
                        pcm = bytes(buf[4 : 4 + n])
                        del buf[: 4 + n]
                        if pcm:
                            got += 1
                            yield pcm, sr
                if cancelled:
                    log.info("cosyvoice stream abort local cancel req=%s", rid)
                elif got == 0:
                    log.warning("cosyvoice stream empty text=%s", text[:40])
    except Exception as e:
        log.warning("cosyvoice stream exception: %s", e)
        return
    finally:
        if cancelled or (should_cancel and should_cancel()):
            try:
                cancel_cosyvoice(req_id=rid)
            except Exception:
                pass


def pcm16_mono_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    return _pcm16_mono_to_wav(pcm, sample_rate)


def resolve_tts_engine(*, for_realtime: bool = False, for_draw: bool = False) -> str:
    """选引擎。

    - 实时通话：tts_realtime_engine（默认 cosyvoice）
    - 音色抽卡/试听：breeze（VoiceDesign 风格描述 → 合成）
    - 其它：tts_engine
    """
    settings = get_settings()
    if for_realtime:
        rt = (getattr(settings, "tts_realtime_engine", None) or "").strip().lower()
        if rt:
            return rt
    if for_draw:
        return "breeze"
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
    prompt_wav: str = "",
) -> Optional[bytes]:
    """按音色描述 instructions 合成一段 WAV；失败返回 None。

    keep_loaded=True：合成后不立刻 unload（实时逐句对话用）。
    engine / for_realtime：指定引擎；实时默认走 tts_realtime_engine。
    prompt_wav：固定/上传参考音色 wav，供 CosyVoice 克隆。
    """
    import random

    text = (text or "").strip()
    instructions = (instructions or "").strip() or "自然说话。"
    if not text:
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
    ref = (prompt_wav or "").strip()
    from . import tts_ondemand

    def _with_engine(name: str, fn):
        try:
            tts_ondemand.ensure_engine(name)
            return fn()
        finally:
            # 抽卡 keep_loaded：拉长闲置，减少「预热完又被卸」
            idle = getattr(tts_ondemand, "DRAW_IDLE_SECONDS", 600.0) if keep_loaded else None
            tts_ondemand.release_engine(
                name,
                unload_now=not keep_loaded,
                idle_seconds=idle,
            )

    if eng == "cosyvoice":
        wav = _with_engine(
            "cosyvoice",
            lambda: _cosyvoice_wav(
                text=text, instructions=instructions, timeout=to, prompt_wav=ref
            ),
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
        # breeze 挂了时依次试 voicedesign / cosy，避免抽卡整批 503 卡死感
        wav = _with_engine(
            "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
        )
        if wav:
            return wav
        return _with_engine(
            "cosyvoice",
            lambda: _cosyvoice_wav(
                text=text, instructions=instructions, timeout=to, prompt_wav=ref
            ),
        )
    if eng == "voicedesign":
        return _with_engine(
            "voicedesign", lambda: _voicedesign_wav(text=text, instructions=instructions, timeout=to)
        )
    # 未知引擎：试 cosy → breeze
    return _with_engine(
        "cosyvoice",
        lambda: _cosyvoice_wav(
            text=text, instructions=instructions, timeout=to, prompt_wav=ref
        ),
    ) or _with_engine(
        "breeze",
        lambda: _breeze_wav(
            text=text, instructions=instructions, timeout=to, seed=use_seed, cfg_scale=cfg
        ),
    )


def warm_tts_engine(
    *,
    for_realtime: bool = True,
    for_draw: bool = False,
    prompt_wav: str = "",
) -> dict:
    """通话/抽卡进页预热：ensure 引擎；CosyVoice 走 /v1/warm（模型+声线声学缓存）。

    预热发生在用户点开通话/抽卡时，不计入「说完→首声」等待。
    prompt_wav：固定音色试听 wav，供 CosyVoice 克隆。
    """
    from . import tts_ondemand

    engine = resolve_tts_engine(for_realtime=for_realtime, for_draw=for_draw)
    try:
        tts_ondemand.ensure_engine(engine)
    except Exception as e:
        return {"ok": False, "engine": engine, "error": str(e)}

    if engine == "cosyvoice":
        settings = get_settings()
        base = (getattr(settings, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/")
        ref = (prompt_wav or "").strip()
        # 网关真热（模型+音色缓存）才跳过；本地 TTL 不可信（IDLE 卸载后仍显示 ok）
        if cosyvoice_truly_warm(prompt_wav=ref):
            keepalive_cosyvoice()
            return {
                "ok": True,
                "engine": engine,
                "warmed": True,
                "via": "cosyvoice_health_hit",
                "prompt_wav": ref or None,
                "error": None,
            }
        try:
            data = {}
            if ref:
                data["prompt_wav"] = ref
            with httpx.Client(timeout=180.0) as client:
                r = client.post(f"{base}/v1/warm", data=data)
            if r.status_code < 400:
                body = r.json() if r.content else {}
                return {
                    "ok": True,
                    "engine": engine,
                    "warmed": True,
                    "via": "cosyvoice_/v1/warm",
                    "prompt_wav": ref or None,
                    "detail": body,
                    "error": None,
                }
            return {
                "ok": False,
                "engine": engine,
                "error": f"warm http={r.status_code}",
            }
        except Exception as e:
            return {"ok": False, "engine": engine, "error": f"warm failed: {e}"}

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
