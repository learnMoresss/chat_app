"""实时语音通话：ASR → DSH 流式分句 → 逐句 CosyVoice(默认) → WS 推送。

语音回合只走 WS（字幕/播放），不写入文字会话 messages。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .asr_gen import transcribe_audio_path
from .config import get_settings
from .dsh_gateway import dsh_enabled, dsh_turn_stream
from .media_cards import fixed_voice_prompt, save_audio_bytes, speak_instructions
from .tts_gen import resolve_tts_engine, synthesize_voice_wav, warm_tts_engine
from .ws_hub import hub

log = logging.getLogger("voice_realtime")

_SENTENCE_END = re.compile(r"([。！？!?；;\n])")
_ready_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}
_READY_TTL = 8.0
_turns_lock = asyncio.Lock()
_user_turn_ts: Dict[str, float] = {}
_MIN_TURN_GAP = 0.8
# 语音通话独立上下文（不进文字聊天）
_voice_sessions: Dict[str, List[Dict[str, str]]] = {}
_VOICE_SESS_LIMIT = 24


def pop_ready_sentences(buf: str) -> Tuple[str, List[str]]:
    """从流式缓冲弹出已完整的句子；返回 (剩余, 句子列表)。"""
    raw = buf or ""
    out: List[str] = []
    while True:
        m = _SENTENCE_END.search(raw)
        if not m:
            break
        end = m.end()
        piece = raw[:end].strip()
        raw = raw[end:]
        if len(piece) >= 2:
            out.append(piece)
        elif piece and out:
            out[-1] = out[-1] + piece
    return raw, out


def readiness(*, force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _ready_cache["payload"] and now - float(_ready_cache["ts"] or 0) < _READY_TTL:
        return dict(_ready_cache["payload"])

    from . import tts_ondemand
    from .dsh_gateway import dsh_enabled as _dsh

    settings = get_settings()
    engine = resolve_tts_engine(for_realtime=True)
    urls = tts_ondemand._urls()
    base = urls.get(engine) or ""
    tts_up = bool(base and tts_ondemand._port_up(base))
    # CosyVoice 按需启动：进程未起也算可连接（warm / 首句会 ensure）
    tts_ok = tts_up or tts_ondemand.can_start(engine)
    http_asr = bool((settings.asr_transcriptions_url or "").strip())
    local_whisper = bool((settings.asr_whisper_python or "").strip())
    asr_ok = http_asr or local_whisper
    if http_asr:
        asr_backend = "openai_compatible"
        asr_model = (settings.asr_model or "whisper-1").strip() or "whisper-1"
    elif local_whisper:
        asr_backend = "whisper_local"
        asr_model = (settings.asr_whisper_model or "medium").strip() or "medium"
    else:
        asr_backend = "none"
        asr_model = ""
    dsh_ok = _dsh()
    ready = tts_ok and dsh_ok and asr_ok
    payload = {
        "ready": ready,
        "connecting": not ready,
        "tts": tts_up,
        "tts_standby": bool(tts_ok and not tts_up),
        "asr_configured": asr_ok,
        "asr_backend": asr_backend,
        "asr_model": asr_model,
        "dsh": dsh_ok,
        "engine": engine,
        "mode": "call",
    }
    _ready_cache["ts"] = now
    _ready_cache["payload"] = payload
    return dict(payload)


def gate_for_conv(*, user_id: str, conv_id: str) -> Dict[str, Any]:
    """进通话前：会话是否存在 + 人设是否已固定音色。"""
    conv = db.fetchone(
        "SELECT id, persona_id FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if conv is None:
        return {
            "ok": False,
            "error": "会话不存在",
            "error_code": "conv_missing",
            "has_fixed_voice": False,
            "need_fixed_voice": True,
            "persona_id": "",
        }
    persona_id = (conv["persona_id"] or "").strip()
    has = bool(persona_id and fixed_voice_prompt(user_id=user_id, persona_id=persona_id))
    return {
        "ok": has,
        "has_fixed_voice": has,
        "need_fixed_voice": not has,
        "persona_id": persona_id,
        "error": "" if has else "请先给人设固定一个音色，才能语音通话",
        "error_code": "" if has else "need_fixed_voice",
    }


def warm_in_background() -> None:
    """进程内预热（同步，宜丢线程池）。"""
    try:
        r = warm_tts_engine()
        log.info("tts warm: %s", r)
        readiness(force=True)
    except Exception as e:
        log.warning("tts warm failed: %s", e)


async def _rate_ok(user_id: str) -> bool:
    async with _turns_lock:
        last = _user_turn_ts.get(user_id) or 0.0
        now = time.time()
        if now - last < _MIN_TURN_GAP:
            return False
        _user_turn_ts[user_id] = now
        return True


def _sess_key(user_id: str, conv_id: str) -> str:
    return f"{user_id}:{conv_id}"


def _append_voice_turn(user_id: str, conv_id: str, *, role: str, content: str) -> None:
    text = (content or "").strip()
    if not text:
        return
    key = _sess_key(user_id, conv_id)
    buf = _voice_sessions.setdefault(key, [])
    buf.append({"role": role, "content": text[:2000]})
    if len(buf) > _VOICE_SESS_LIMIT:
        del buf[: len(buf) - _VOICE_SESS_LIMIT]


def clear_voice_session(*, user_id: str, conv_id: str) -> None:
    _voice_sessions.pop(_sess_key(user_id, conv_id), None)


def _history(user_id: str, conv_id: str, limit: int = 24) -> List[Dict[str, str]]:
    """文字会话近期上下文 + 本通电话语音回合（语音不入库）。"""
    import json

    rows = db.fetchall(
        """
        SELECT mine, kind, text FROM messages
        WHERE user_id = ? AND conv_id = ?
        ORDER BY seq DESC LIMIT ?
        """,
        (user_id, conv_id, max(16, limit)),
    )
    out: List[Dict[str, str]] = []
    for r in reversed(rows or []):
        role = "user" if r["mine"] else "assistant"
        text = (r["text"] or "").strip()
        kind = (r["kind"] or "").strip()
        if kind == "voice":
            if not text.startswith("{"):
                continue
            try:
                meta = json.loads(text)
            except Exception:
                continue
            # 旧版通话写入的气泡：不再当作文字上下文
            if meta.get("realtime"):
                continue
            text = str(meta.get("asr") or meta.get("caption") or "").strip()
        elif kind != "text":
            continue
        if not text:
            continue
        out.append({"role": role, "content": text[:2000]})
    sess = list(_voice_sessions.get(_sess_key(user_id, conv_id)) or [])
    merged = out + sess
    if len(merged) > limit:
        merged = merged[-limit:]
    return merged


async def _push(user_id: str, event: str, payload: Dict[str, Any]) -> None:
    await hub.send_user(user_id, event, payload)


def _voice_memory_context(
    *,
    user_id: str,
    persona_id: str,
    query: str,
) -> Tuple[str, str]:
    """与文字聊天同一套 companion 记忆管道：读记忆进 instructions，不写 messages。

    返回 (system, instructions)。
    """
    persona = db.fetchone(
        "SELECT id, name, bio FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    name = ((persona["name"] if persona else "") or "对方").strip() or "对方"
    bio = ((persona["bio"] if persona else "") or "").strip()
    try:
        from .companion.growth import GrowthService

        gst = GrowthService().get_state(user_id, persona_id)
        if gst.get("mode") == "initial_locked" and gst.get("baseline_bio"):
            bio = gst["baseline_bio"]
        elif gst.get("baseline_bio") and not bio:
            bio = gst["baseline_bio"]
    except Exception:
        pass

    system = (
        f"你是{name}。{bio or '自然地聊天。'}\n"
        "这是语音通话，像打电话：短句口语、有口气，不要 Markdown、不要 emoji、"
        "不要列举能力清单；接住对方刚说的话。"
    )
    instructions = ""
    try:
        from .companion.snapshot import build_companion_snapshot
        from .evidence import list_person_notes

        pref = db.fetchone("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))
        inj_on = True
        if pref is not None and "memory_inject" in pref.keys():
            try:
                v = pref["memory_inject"]
                inj_on = True if v is None else bool(int(v))
            except Exception:
                inj_on = True
        if not inj_on:
            return system, instructions

        snap = build_companion_snapshot(user_id, persona_id, query=query or "")
        lines: List[str] = []
        try:
            for n in list_person_notes(user_id, persona_id)[:3]:
                lines.append(f"- [person_note] {n['text']}")
        except Exception:
            pass
        for m in (snap.get("memories") or [])[:8]:
            t = (m.get("text") or "").strip()
            if t:
                lines.append(f"- [memory] {t}")
        a = snap.get("affect") or {}
        affect_hint = ""
        if isinstance(a, dict) and a:
            affect_hint = (
                f"当前情绪锚点：愉悦{a.get('joy', 35)} 低落{a.get('sadness', 35)} "
                f"兴奋{a.get('excitement', 35)} 担忧{a.get('worry', 35)} "
                f"不悦{a.get('annoyance', 35)} 好奇{a.get('curiosity', 35)}"
                + (f"（{a.get('reason')}）" if a.get("reason") else "")
            )
        parts: List[str] = []
        if lines:
            parts.append("本轮证据（人物档+记忆，单一管道）：\n" + "\n".join(lines))
        if affect_hint:
            parts.append(affect_hint)
        if snap.get("growth_mode"):
            parts.append(f"人格模式：{snap.get('growth_mode')}")
        instructions = "\n".join(parts)
    except Exception:
        try:
            from . import memory as mem

            hit = mem.search_memories(user_id, persona_id, query or "", limit=6)
            mems = [m["text"] for m in hit if m.get("text")]
            if mems:
                instructions = "相关记忆：\n- " + "\n- ".join(mems[:6])
        except Exception:
            instructions = ""
    return system, instructions


async def _persist_voice_memory(
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
    conv_id: str = "",
) -> None:
    """语音终稿归档 + 抽取入队；失败静默。"""
    if not persona_id or not (user_text or "").strip() or not (assistant_text or "").strip():
        return
    try:
        from .companion.raw_records import archive_raw
        from .companion.runtime import CompanionRuntime

        archive_raw(
            user_id=user_id,
            persona_id=persona_id,
            conv_id=conv_id,
            kind="voice",
            role="user",
            text=user_text.strip(),
            meta={"assistant_preview": assistant_text.strip()[:200]},
        )
        CompanionRuntime().enqueue_extract_after_reply(
            user_id=user_id,
            persona_id=persona_id,
            user_text=user_text.strip(),
            assistant_text=assistant_text.strip(),
        )
    except Exception:
        log.exception("voice memory extract enqueue failed")


async def run_voice_turn(
    *,
    user_id: str,
    conv_id: str,
    audio_path: Path,
    turn_id: str,
) -> None:
    """后台跑完一整轮；事件经 WS 推送。"""
    conv = db.fetchone(
        "SELECT id, persona_id FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if conv is None:
        await _push(user_id, "voice_error", {"turn_id": turn_id, "error": "会话不存在"})
        return
    persona_id = (conv["persona_id"] or "").strip()
    voice_prompt = fixed_voice_prompt(user_id=user_id, persona_id=persona_id) if persona_id else None
    if not voice_prompt:
        await _push(
            user_id,
            "voice_error",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "error": "请先给人设固定一个音色，才能语音通话",
                "error_code": "need_fixed_voice",
                "persona_id": persona_id,
            },
        )
        return

    if not await _rate_ok(user_id):
        await _push(user_id, "voice_error", {"turn_id": turn_id, "error": "说太快了，稍等一下"})
        return

    await _push(user_id, "voice_status", {"turn_id": turn_id, "phase": "asr", "conv_id": conv_id})

    asr = await asyncio.to_thread(transcribe_audio_path, audio_path, language="zh")
    asr = (asr or "").strip()
    if not asr:
        await _push(
            user_id,
            "voice_error",
            {"turn_id": turn_id, "conv_id": conv_id, "error": "没听清，再说一次吧"},
        )
        return

    await _push(
        user_id,
        "voice_asr",
        {"turn_id": turn_id, "conv_id": conv_id, "text": asr},
    )
    _append_voice_turn(user_id, conv_id, role="user", content=asr)

    affect: Dict[str, Any] = {}
    try:
        from .companion.affect import AffectService

        a = AffectService().get_or_init(user_id, persona_id)
        if isinstance(a, dict):
            affect = a
    except Exception:
        pass
    instr = speak_instructions(voice_prompt, affect)

    if not dsh_enabled():
        await _push(user_id, "voice_error", {"turn_id": turn_id, "error": "思考服务未就绪"})
        return

    await _push(user_id, "voice_status", {"turn_id": turn_id, "phase": "thinking", "conv_id": conv_id})

    hist = _history(user_id, conv_id)
    dsh_system, dsh_instructions = await asyncio.to_thread(
        _voice_memory_context,
        user_id=user_id,
        persona_id=persona_id,
        query=asr,
    )
    buf_parts: List[str] = []
    pending = ""
    sentence_idx = 0
    full_text_parts: List[str] = []

    async def _emit_sentence(sentence: str) -> None:
        nonlocal sentence_idx
        sentence = sentence.strip()
        if len(sentence) < 1:
            return
        sentence_idx += 1
        idx = sentence_idx
        full_text_parts.append(sentence)
        await _push(
            user_id,
            "voice_status",
            {"turn_id": turn_id, "phase": "speaking", "conv_id": conv_id, "index": idx},
        )
        wav = await asyncio.to_thread(
            synthesize_voice_wav,
            text=sentence,
            instructions=instr,
            keep_loaded=True,
            for_realtime=True,
        )
        fid = ""
        if wav:
            fid = await asyncio.to_thread(
                save_audio_bytes,
                user_id=user_id,
                data=wav,
                source="voice_realtime",
            ) or ""
        await _push(
            user_id,
            "voice_sentence",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "index": idx,
                "text": sentence,
                "file_id": fid,
                "mime": "audio/wav",
            },
        )

    try:
        async for ev in dsh_turn_stream(
            user_id=user_id,
            conversation_id=conv_id,
            text=asr,
            instructions=dsh_instructions,
            system=dsh_system,
            history=hist,
            mode="chat",
            persona_id=persona_id,
        ):
            kind = ev.get("event") or ""
            data = ev.get("data") or {}
            if kind == "error":
                raise RuntimeError(data.get("error") or "思考失败")
            if kind == "delta":
                piece = str(data.get("text") or "")
                if not piece:
                    continue
                buf_parts.append(piece)
                pending += piece
                pending, ready = pop_ready_sentences(pending)
                for s in ready:
                    await _emit_sentence(s)
            if kind == "done":
                break
    except Exception as e:
        log.exception("voice turn dsh failed")
        await _push(
            user_id,
            "voice_error",
            {"turn_id": turn_id, "conv_id": conv_id, "error": str(e)[:120] or "思考失败"},
        )
        return

    tail = pending.strip()
    if tail:
        await _emit_sentence(tail)
    elif not full_text_parts and buf_parts:
        await _emit_sentence("".join(buf_parts).strip() or "……")

    full = "".join(full_text_parts).strip() or "……"
    _append_voice_turn(user_id, conv_id, role="assistant", content=full)
    # 与文字聊天同路径抽记忆；不写 messages，聊天列表不出现通话内容
    await _persist_voice_memory(
        user_id=user_id,
        persona_id=persona_id,
        user_text=asr,
        assistant_text=full,
        conv_id=conv_id,
    )

    await _push(
        user_id,
        "voice_done",
        {
            "turn_id": turn_id,
            "conv_id": conv_id,
            "reply": full,
            "sentences": len(full_text_parts),
        },
    )
