"""实时语音通话：ASR →（mock/DSH）流式分句 → CosyVoice PCM 块流 → WS 推送。

语音字幕/播放走 WS；聊天里插入类似微信的「语音通话」气泡（start/hangup）。
TTS 按音频块推送 voice_audio；支持 cancel 打断进行中的 turn。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import db
from .asr_gen import transcribe_audio_path
from .config import get_settings
from .dsh_gateway import dsh_enabled, dsh_turn_stream
from .media_cards import (
    build_sentence_instruct,
    filter_cosy_marks,
    fixed_voice_prompt,
    fixed_voice_style_hint,
    fixed_voice_wav_path,
    parse_emotion_tag,
    save_audio_bytes,
)
from .tts_gen import (
    iter_cosyvoice_pcm,
    pcm16_mono_to_wav,
    resolve_tts_engine,
    synthesize_voice_wav,
    warm_tts_engine,
)
from .ws_hub import hub

log = logging.getLogger("voice_realtime")

_SENTENCE_END = re.compile(r"([。！？!?；;\n])")
_FIRST_BREAK = re.compile(r"([，、,])")
_ready_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}
_READY_TTL = 8.0
_turns_lock = asyncio.Lock()
_user_turn_ts: Dict[str, float] = {}
_MIN_TURN_GAP = 0.8
# 语音通话独立上下文（不进文字聊天）
_voice_sessions: Dict[str, List[Dict[str, str]]] = {}
_VOICE_SESS_LIMIT = 24
# 记忆 snapshot 缓存：key=user:persona → {ts, system, instructions, query}
_mem_cache: Dict[str, Dict[str, Any]] = {}
_MEM_TTL = 120.0
# 通话会话：气泡消息 + 可取消 turn
_call_lock = threading.Lock()
_call_sessions: Dict[str, Dict[str, Any]] = {}


def pop_ready_sentences(
    buf: str,
    *,
    first_phrase: bool = False,
    min_chars: int = 6,
) -> Tuple[str, List[str]]:
    """从流式缓冲弹出已完整的句子；返回 (剩余, 句子列表)。

    first_phrase=True：仅当还没有任何整句时，允许按逗号提前切出首短句（加速首声）。
    """
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
    # 首句加速：还没切出整句时，按逗号切一段够长的短句
    if first_phrase and not out and raw:
        m2 = _FIRST_BREAK.search(raw)
        if m2:
            end = m2.end()
            piece = raw[:end].strip()
            bare = re.sub(r"[^\w\u4e00-\u9fff]+", "", piece, flags=re.UNICODE)
            if len(bare) >= max(4, int(min_chars or 6)):
                out.append(piece)
                raw = raw[end:]
    return raw, out


def _probe_http_ok(url: str, *, timeout: float = 1.2) -> bool:
    """轻量健康探针：2xx / 404 都算活着（有的网关只有根路径或 /health）。"""
    u = (url or "").rstrip("/")
    if not u:
        return False
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            for path in ("/health", "/", "/v1/models"):
                try:
                    r = client.get(f"{u}{path}")
                    if r.status_code < 500:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def readiness(*, force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _ready_cache["payload"] and now - float(_ready_cache["ts"] or 0) < _READY_TTL:
        return dict(_ready_cache["payload"])

    from . import asr_ondemand, tts_ondemand
    from .dsh_gateway import dsh_enabled as _dsh

    settings = get_settings()
    engine = resolve_tts_engine(for_realtime=True)
    urls = tts_ondemand._urls()
    base = urls.get(engine) or ""
    tts_up = bool(base and tts_ondemand._port_up(base))
    tts_standby = (not tts_up) and tts_ondemand.can_start(engine)
    tts_ok = tts_up or tts_standby

    asr_url = (settings.asr_transcriptions_url or "").strip()
    asr_live = asr_ondemand.is_live()
    asr_standby = (not asr_live) and asr_ondemand.can_start()
    # 通话 ready 必须 ASR 真活着；待命中算 connecting（warm 会乐观拉起）
    asr_ok = asr_live
    if asr_live:
        asr_backend = "sensevoice"
        asr_model = (settings.asr_model or "SenseVoiceSmall").strip() or "SenseVoiceSmall"
    elif asr_standby:
        asr_backend = "standby"
        asr_model = (settings.asr_model or "SenseVoiceSmall").strip() or "SenseVoiceSmall"
    elif asr_url:
        asr_backend = "down"
        asr_model = (settings.asr_model or "").strip()
    else:
        asr_backend = "none"
        asr_model = ""

    # CosyVoice TTS 也要真活着才 ready（待命中同样走 connecting）
    tts_ready = tts_up
    if tts_up and base:
        # 再探一下 /health，避免端口占着但挂了
        if not _probe_http_ok(base):
            tts_ready = False
            tts_up = False

    dsh_ok = _dsh()
    mock_on = bool((getattr(settings, "voice_realtime_mock_reply", None) or "").strip())
    ready = tts_ready and asr_ok and (dsh_ok or mock_on)
    reason = ""
    if not asr_ok:
        reason = "识别服务启动中…" if asr_standby else "识别服务未启动"
    elif not tts_ready:
        reason = "语音合成启动中…" if tts_standby else "语音合成未就绪"
    elif not (dsh_ok or mock_on):
        reason = "思考服务未就绪"
    payload = {
        "ready": ready,
        "connecting": not ready,
        "tts": tts_up,
        "tts_standby": tts_standby,
        "asr_configured": bool(asr_url) or asr_standby or asr_live,
        "asr_live": asr_live,
        "asr_standby": asr_standby,
        "asr_backend": asr_backend,
        "asr_model": asr_model,
        "dsh": dsh_ok,
        "mock_llm": mock_on,
        "engine": engine,
        "mode": "call",
        "auto_silence_ms": int(getattr(settings, "voice_auto_silence_ms", 550) or 550),
        "reason": reason,
    }
    _ready_cache["ts"] = now
    _ready_cache["payload"] = payload
    return dict(payload)


_HOLD_TRAILERS = (
    "然后",
    "就是",
    "那个",
    "因为",
    "所以",
    "但是",
    "还有",
    "我想",
    "我觉得",
    "其实",
    "嗯",
    "呃",
    "啊",
    "呢",
    "这个",
    "那个",
)
_DONE_ENDINGS = ("吗", "呢", "吧", "啊", "了", "呀", "嘛", "哦", "哈", "。", "？", "！", "?", "!", "…", "~")


def looks_unfinished(text: str) -> bool:
    """规则版句尾判断：像没说完则 True（应 hold，不调 LLM）。"""
    t = (text or "").strip()
    if not t:
        return True
    bare = t.rstrip("，,、…~～ ")
    if len(bare) < 2:
        return True
    for w in _HOLD_TRAILERS:
        if bare.endswith(w):
            return True
    if bare.endswith(("，", ",", "、")):
        return True
    if t.endswith(("。", "？", "！", "?", "!")):
        return False
    if t.endswith(("吗", "吧", "了", "呀", "嘛")):
        return False
    # 「呢/啊」偏短句更像拖音；有明确疑问/祈使词才算说完
    if t.endswith(("呢", "啊", "哦", "哈")) and len(bare) <= 4:
        return True
    return False


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


def warm_in_background(*, user_id: str = "", persona_id: str = "", conv_id: str = "") -> None:
    """进程内预热（同步，宜丢线程池）。

    乐观路径：一点打电话就并行拉起 SenseVoice（不常驻）+ CosyVoice；
    客户端「接通中」loading 盖住冷启动时间。
    """
    from . import asr_ondemand

    try:
        # 1) 先踢 ASR（模型加载可能几秒～十几秒）
        asr_ok = asr_ondemand.ensure_asr()
        log.info("asr warm: live=%s", asr_ok)
    except Exception as e:
        log.warning("asr warm failed: %s", e)

    try:
        prompt_wav = ""
        pid = (persona_id or "").strip()
        if not pid and user_id and conv_id:
            conv = db.fetchone(
                "SELECT persona_id FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            if conv:
                pid = (conv["persona_id"] or "").strip()
        if user_id and pid:
            prompt_wav = fixed_voice_wav_path(user_id=user_id, persona_id=pid) or ""
            # 通话入口预取记忆上下文，首轮少一次同步等待
            try:
                prefetch_memory_context(user_id=user_id, persona_id=pid, query="")
            except Exception:
                log.exception("prefetch memory failed")
        r = warm_tts_engine(prompt_wav=prompt_wav)
        log.info("tts warm: %s", r)
        readiness(force=True)
    except Exception as e:
        log.warning("tts warm failed: %s", e)


def _fmt_call_dur(sec: int) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _voice_call_card(
    *,
    status: str,
    duration_sec: int = 0,
    call_id: str = "",
    body: str = "",
) -> str:
    dur = max(0, int(duration_sec))
    if not body:
        if status == "ended":
            body = f"语音通话 {_fmt_call_dur(dur)}" if dur > 0 else "语音通话"
        elif status == "canceled":
            body = "已取消"
        elif status == "answered":
            body = "语音通话"
        else:
            body = "语音通话"
    return json.dumps(
        {
            "v": 1,
            "card": "voice_call",
            "media": "audio",
            "status": status,
            "duration_sec": dur,
            "call_id": call_id or "",
            "body": body,
        },
        ensure_ascii=False,
    )


def is_turn_cancelled(user_id: str, conv_id: str, turn_id: str) -> bool:
    key = _sess_key(user_id, conv_id)
    with _call_lock:
        sess = _call_sessions.get(key)
        if not sess:
            return False
        cancelled: Set[str] = sess.get("cancelled_turns") or set()
        return bool(turn_id and turn_id in cancelled)


async def cancel_turn(*, user_id: str, conv_id: str, turn_id: str = "") -> Dict[str, Any]:
    """打断当前 AI 说话：标记 turn 取消，后续 PCM/LLM 不再推送。"""
    key = _sess_key(user_id, conv_id)
    tid = ""
    with _call_lock:
        sess = _call_sessions.setdefault(
            key,
            {
                "started_at": time.time(),
                "msg_id": "",
                "call_id": "",
                "active_turn_id": "",
                "cancelled_turns": set(),
            },
        )
        tid = (turn_id or "").strip() or str(sess.get("active_turn_id") or "")
        if tid:
            cancelled: Set[str] = sess.setdefault("cancelled_turns", set())
            cancelled.add(tid)
        sess["active_turn_id"] = ""
    if tid:
        await _push(
            user_id,
            "voice_cancelled",
            {"conv_id": conv_id, "turn_id": tid, "reason": "barge_in"},
        )
    return {"ok": True, "turn_id": tid, "conv_id": conv_id}


def find_active_call_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """账号级：语音实时通话 + 视频/通话信令，同时只能一路。"""
    uid = (user_id or "").strip()
    if not uid:
        return None
    prefix = f"{uid}:"
    with _call_lock:
        for key, sess in list(_call_sessions.items()):
            if not key.startswith(prefix):
                continue
            if sess.get("ended"):
                continue
            conv = key.split(":", 1)[-1]
            return {
                "media": "audio",
                "conv_id": conv,
                "call_id": str(sess.get("call_id") or ""),
                "source": "voice_realtime",
                "status": "active",
            }
    row = db.fetchone(
        """
        SELECT id, conv_id, media, status FROM call_sessions
        WHERE user_id = ? AND status IN ('ringing', 'active')
        ORDER BY created_at DESC LIMIT 1
        """,
        (uid,),
    )
    if row is None:
        return None
    return {
        "media": (row["media"] or "audio"),
        "conv_id": row["conv_id"] or "",
        "call_id": row["id"] or "",
        "source": "calls",
        "status": row["status"] or "",
    }


async def start_call(*, user_id: str, conv_id: str) -> Dict[str, Any]:
    """点击语音通话：插微信式通话气泡（接听中），AI 默认已接。"""
    from .routers.chat import _insert_message
    from .security import new_id

    gate = gate_for_conv(user_id=user_id, conv_id=conv_id)
    if gate.get("need_fixed_voice"):
        return {**gate, "ok": False}

    key = _sess_key(user_id, conv_id)
    with _call_lock:
        existing = _call_sessions.get(key)
        if existing and existing.get("msg_id") and not existing.get("ended"):
            return {
                "ok": True,
                "call_id": existing.get("call_id") or "",
                "message_id": existing.get("msg_id") or "",
                "already": True,
                **gate,
            }

    busy = find_active_call_for_user(user_id)
    if busy and (busy.get("conv_id") or "") != conv_id:
        kind = "视频通话" if (busy.get("media") or "") == "video" else "语音通话"
        return {
            "ok": False,
            "error": f"当前已有{kind}进行中，请先挂断",
            "error_code": "call_busy",
            "busy": busy,
            **gate,
        }

    call_id = new_id("vcall_")
    client_id = new_id("callcli_")
    card = _voice_call_card(status="answered", call_id=call_id, body="语音通话")
    msg = _insert_message(
        user_id=user_id,
        conv_id=conv_id,
        client_id=client_id,
        sender_id=user_id,
        mine=True,
        kind="link",
        text=card,
        seconds=0,
        state="sent",
    )
    with _call_lock:
        _call_sessions[key] = {
            "started_at": time.time(),
            "msg_id": msg.id,
            "call_id": call_id,
            "client_id": client_id,
            "active_turn_id": "",
            "cancelled_turns": set(),
            "ended": False,
        }
    await hub.send_user(user_id, "message", msg.model_dump())
    return {
        "ok": True,
        "call_id": call_id,
        "message_id": msg.id,
        "message": msg.model_dump(),
        **gate,
    }


async def hangup_call(
    *,
    user_id: str,
    conv_id: str,
    duration_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """挂断：取消进行中 turn、写通话时长到气泡、清语音上下文。"""
    from .security import iso as _iso

    key = _sess_key(user_id, conv_id)
    with _call_lock:
        sess = _call_sessions.get(key) or {}
        started = float(sess.get("started_at") or 0)
        msg_id = str(sess.get("msg_id") or "")
        call_id = str(sess.get("call_id") or "")
        active = str(sess.get("active_turn_id") or "")
        if active:
            sess.setdefault("cancelled_turns", set()).add(active)
        if sess:
            sess["ended"] = True
            sess["active_turn_id"] = ""

    if active:
        await _push(
            user_id,
            "voice_cancelled",
            {"conv_id": conv_id, "turn_id": active, "reason": "hangup"},
        )

    if duration_sec is None:
        duration_sec = int(max(0, time.time() - started)) if started else 0
    duration_sec = max(0, int(duration_sec))

    out_msg = None
    if msg_id:
        row = db.fetchone(
            "SELECT text FROM messages WHERE id = ? AND user_id = ?",
            (msg_id, user_id),
        )
        if row is not None:
            try:
                card = json.loads(row["text"] or "{}")
            except Exception:
                card = {}
            if not isinstance(card, dict):
                card = {}
            card.update(
                {
                    "v": 1,
                    "card": "voice_call",
                    "media": "audio",
                    "status": "ended",
                    "duration_sec": duration_sec,
                    "call_id": call_id or card.get("call_id") or "",
                    # 微信文案：通话时长 00:08
                    "body": f"通话时长 {_fmt_call_dur(duration_sec)}"
                    if duration_sec > 0
                    else "语音通话",
                }
            )
            text = json.dumps(card, ensure_ascii=False)
            now = _iso()
            db.execute(
                "UPDATE messages SET text = ?, seconds = ?, updated_at = ? WHERE id = ?",
                (text, duration_sec, now, msg_id),
            )
            from .preview_text import conversation_preview

            preview = conversation_preview(kind="link", text=text, seconds=duration_sec)
            db.execute(
                "UPDATE conversations SET preview = ?, last_time = ? WHERE id = ? AND user_id = ?",
                (preview, now, conv_id, user_id),
            )
            full = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
            if full is not None:
                from .routers.chat import _msg_out

                out_msg = _msg_out(full)
                await hub.send_user(user_id, "message", out_msg.model_dump())

    clear_voice_session(user_id=user_id, conv_id=conv_id)
    with _call_lock:
        _call_sessions.pop(key, None)

    # 挂断后延迟停 ASR（不常驻）
    try:
        from . import asr_ondemand

        asr_ondemand.release_asr()
    except Exception:
        pass

    return {
        "ok": True,
        "conv_id": conv_id,
        "call_id": call_id,
        "duration_sec": duration_sec,
        "message": out_msg.model_dump() if out_msg else None,
    }


async def run_greeting(
    *,
    user_id: str,
    conv_id: str,
    turn_id: str,
) -> None:
    """AI 默认接听后的短问候（固定音色 CosyVoice），然后客户端再开麦。"""
    gate = gate_for_conv(user_id=user_id, conv_id=conv_id)
    if gate.get("need_fixed_voice"):
        await _push(
            user_id,
            "voice_error",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "error": gate.get("error") or "需要先固定音色",
                "error_code": "need_fixed_voice",
            },
        )
        return
    persona_id = (gate.get("persona_id") or "").strip()
    key = _sess_key(user_id, conv_id)
    with _call_lock:
        sess = _call_sessions.setdefault(
            key,
            {
                "started_at": time.time(),
                "msg_id": "",
                "call_id": "",
                "active_turn_id": "",
                "cancelled_turns": set(),
            },
        )
        sess["active_turn_id"] = turn_id

    settings = get_settings()
    engine = resolve_tts_engine(for_realtime=True)
    prompt_wav = fixed_voice_wav_path(user_id=user_id, persona_id=persona_id) or ""
    style_hint = fixed_voice_style_hint(user_id=user_id, persona_id=persona_id)
    raw = "[平静]喂，我在呢。"
    tag, spoken = parse_emotion_tag(raw)
    spoken = filter_cosy_marks(spoken) or "喂，我在呢。"
    instr = build_sentence_instruct(
        engine=engine,
        style_hint=style_hint,
        emo_tag=tag,
        affect={},
    )

    await _push(user_id, "voice_status", {"turn_id": turn_id, "phase": "speaking", "conv_id": conv_id})
    await _push(
        user_id,
        "voice_sentence",
        {
            "turn_id": turn_id,
            "conv_id": conv_id,
            "index": 1,
            "text": spoken,
            "emo": tag or "",
            "file_id": "",
            "mime": "audio/wav",
            "streaming": True,
            "greeting": True,
        },
    )

    loop = asyncio.get_running_loop()
    chunk_q: asyncio.Queue = asyncio.Queue(maxsize=32)

    def _produce() -> None:
        try:
            if engine == "cosyvoice":
                for pcm, sr in iter_cosyvoice_pcm(
                    text=spoken,
                    instructions=instr,
                    prompt_wav=prompt_wav,
                ):
                    if is_turn_cancelled(user_id, conv_id, turn_id):
                        break
                    asyncio.run_coroutine_threadsafe(
                        chunk_q.put(("pcm", pcm, int(sr))), loop
                    ).result(timeout=120)
            else:
                wav = synthesize_voice_wav(
                    text=spoken,
                    instructions=instr,
                    keep_loaded=True,
                    for_realtime=True,
                    engine=engine,
                )
                if wav and len(wav) > 44 and wav[:4] == b"RIFF":
                    import wave
                    import io as _io

                    with wave.open(_io.BytesIO(wav), "rb") as w:
                        sr = int(w.getframerate())
                        pcm = w.readframes(w.getnframes())
                    asyncio.run_coroutine_threadsafe(
                        chunk_q.put(("pcm", pcm, sr)), loop
                    ).result(timeout=120)
            asyncio.run_coroutine_threadsafe(chunk_q.put(("end", None, None)), loop).result(
                timeout=30
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                chunk_q.put(("err", str(e)[:160], None)), loop
            ).result(timeout=30)

    threading.Thread(target=_produce, name=f"greet-{turn_id}", daemon=True).start()
    seq = 0
    try:
        while True:
            if is_turn_cancelled(user_id, conv_id, turn_id):
                break
            kind, a, b = await chunk_q.get()
            if kind == "end":
                break
            if kind == "err":
                raise RuntimeError(a or "tts failed")
            pcm = a or b""
            if not pcm:
                continue
            seq += 1
            await _push(
                user_id,
                "voice_audio",
                {
                    "turn_id": turn_id,
                    "conv_id": conv_id,
                    "index": 1,
                    "seq": seq,
                    "sample_rate": int(b or 24000),
                    "format": "s16le",
                    "channels": 1,
                    "pcm_b64": base64.b64encode(pcm).decode("ascii"),
                    "greeting": True,
                },
            )
    except Exception as e:
        log.exception("voice greeting failed")
        await _push(
            user_id,
            "voice_error",
            {"turn_id": turn_id, "conv_id": conv_id, "error": str(e)[:120] or "接通失败"},
        )
        return
    finally:
        with _call_lock:
            sess = _call_sessions.get(key)
            if sess and sess.get("active_turn_id") == turn_id:
                sess["active_turn_id"] = ""

    if not is_turn_cancelled(user_id, conv_id, turn_id):
        await _push(
            user_id,
            "voice_audio_end",
            {"turn_id": turn_id, "conv_id": conv_id, "index": 1, "seqs": seq, "greeting": True},
        )
        await _push(
            user_id,
            "voice_done",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "reply": spoken,
                "sentences": 1,
                "greeting": True,
            },
        )


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


def _mem_key(user_id: str, persona_id: str) -> str:
    return f"{user_id}:{persona_id}"


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
    conv = db.fetchone(
        "SELECT persona_id FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if conv and (conv["persona_id"] or "").strip():
        _mem_cache.pop(_mem_key(user_id, conv["persona_id"].strip()), None)


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
        "不要列举能力清单；接住对方刚说的话。\n"
        "第一句不超过 10 个字，先接住再展开。\n"
        "每句开头可用情绪标签之一："
        "[平静][轻快][低落][好奇][害羞][无奈][兴奋][担忧]，标签本身不要念出来。"
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


def prefetch_memory_context(*, user_id: str, persona_id: str, query: str = "") -> Tuple[str, str]:
    """同步预取并写入缓存。"""
    system, instructions = _voice_memory_context(
        user_id=user_id, persona_id=persona_id, query=query or ""
    )
    _mem_cache[_mem_key(user_id, persona_id)] = {
        "ts": time.time(),
        "system": system,
        "instructions": instructions,
        "query": query or "",
    }
    return system, instructions


def get_cached_memory_context(
    *,
    user_id: str,
    persona_id: str,
) -> Optional[Tuple[str, str]]:
    hit = _mem_cache.get(_mem_key(user_id, persona_id))
    if not hit:
        return None
    if time.time() - float(hit.get("ts") or 0) > _MEM_TTL:
        return None
    return str(hit.get("system") or ""), str(hit.get("instructions") or "")


async def _persist_voice_memory(
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
    conv_id: str = "",
    turn_id: str = "",
) -> None:
    """语音终稿归档 + 抽取入队 + affect 回写；失败静默。"""
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
    # affect 回写：与文字聊天同一套规则，下一轮基线才有起伏
    try:
        from .companion.affect_rules import propose_after_reply

        propose_after_reply(
            user_id=user_id,
            persona_id=persona_id,
            user_text=user_text.strip(),
            event_id=f"voice:{turn_id or conv_id}:{int(time.time())}",
        )
    except Exception:
        log.exception("voice affect propose failed")


async def run_voice_turn(
    *,
    user_id: str,
    conv_id: str,
    audio_path: Path,
    turn_id: str,
    prefix_text: str = "",
) -> None:
    """后台跑完一整轮；事件经 WS 推送。"""
    from . import asr_ondemand

    t_turn0 = time.perf_counter()
    timing: Dict[str, Any] = {"turn_id": turn_id, "conv_id": conv_id}
    key = _sess_key(user_id, conv_id)
    with _call_lock:
        sess = _call_sessions.setdefault(
            key,
            {
                "started_at": time.time(),
                "msg_id": "",
                "call_id": "",
                "active_turn_id": "",
                "cancelled_turns": set(),
            },
        )
        sess["active_turn_id"] = turn_id

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

    # 通话只用 SenseVoice；未就绪则提示，绝不回退 whisper
    if not asr_ondemand.is_live():
        # 再乐观踢一次（用户可能开麦很快）
        ok = await asyncio.to_thread(asr_ondemand.ensure_asr, timeout=25.0)
        if not ok:
            await _push(
                user_id,
                "voice_error",
                {
                    "turn_id": turn_id,
                    "conv_id": conv_id,
                    "error": "识别服务未就绪，请稍后再试",
                    "error_code": "asr_down",
                },
            )
            return

    await _push(user_id, "voice_status", {"turn_id": turn_id, "phase": "asr", "conv_id": conv_id})

    asr = await asyncio.to_thread(
        transcribe_audio_path, audio_path, language="zh", allow_fallback=False
    )
    asr = (asr or "").strip()
    timing["asr_ms"] = round((time.perf_counter() - t_turn0) * 1000)
    asr_ondemand.touch_asr()
    if not asr:
        await _push(
            user_id,
            "voice_error",
            {"turn_id": turn_id, "conv_id": conv_id, "error": "没听清，再说一次吧"},
        )
        return

    prefix = (prefix_text or "").strip()
    if prefix:
        joiner = "" if prefix[-1] in "，,、。！？!? " else "，"
        asr = f"{prefix}{joiner}{asr}"
        timing["prefix"] = True

    settings = get_settings()
    hold_on = bool(getattr(settings, "voice_turn_hold", True))
    if hold_on and looks_unfinished(asr):
        timing["hold"] = True
        log.info(
            "voice hold turn=%s asr_ms=%s text=%s",
            turn_id,
            timing["asr_ms"],
            asr[:60],
        )
        await _push(
            user_id,
            "voice_hold",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "text": asr,
                "reason": "unfinished",
            },
        )
        with _call_lock:
            sess = _call_sessions.get(key)
            if sess and sess.get("active_turn_id") == turn_id:
                sess["active_turn_id"] = ""
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
    engine = resolve_tts_engine(for_realtime=True)
    prompt_wav = fixed_voice_wav_path(user_id=user_id, persona_id=persona_id) or ""
    style_hint = fixed_voice_style_hint(user_id=user_id, persona_id=persona_id)
    emo_tags_on = bool(getattr(settings, "voice_emotion_tags", True))
    first_min = int(getattr(settings, "voice_first_phrase_min_chars", 6) or 6)
    if prompt_wav:
        log.info("voice turn ref_wav=%s persona=%s", prompt_wav, persona_id)
    mock_reply = (getattr(settings, "voice_realtime_mock_reply", None) or "").strip()
    mock_stream_ms = int(getattr(settings, "voice_realtime_mock_stream_ms", 18) or 0)

    if not mock_reply and not dsh_enabled():
        await _push(user_id, "voice_error", {"turn_id": turn_id, "error": "思考服务未就绪"})
        return

    await _push(user_id, "voice_status", {"turn_id": turn_id, "phase": "thinking", "conv_id": conv_id})

    phrase_q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _END = object()
    sentence_idx = 0
    full_text_parts: List[str] = []
    last_spoken = ""
    first_pcm_ms: Optional[int] = None
    first_phrase_ms: Optional[int] = None
    llm_first_tok_ms: Optional[int] = None

    async def _stream_sentence(sentence: str, *, emo_tag: str = "") -> None:
        """短语进 CosyVoice 流：每块 PCM 立刻 WS，不等整句。"""
        nonlocal sentence_idx, last_spoken, first_pcm_ms
        if is_turn_cancelled(user_id, conv_id, turn_id):
            return
        sentence = sentence.strip()
        if len(sentence) < 1:
            return
        if sentence == last_spoken:
            log.info("voice skip duplicate sentence turn=%s text=%s", turn_id, sentence[:40])
            return
        last_spoken = sentence
        sentence_idx += 1
        idx = sentence_idx
        full_text_parts.append(sentence)
        instr = build_sentence_instruct(
            engine=engine,
            style_hint=style_hint,
            emo_tag=emo_tag if emo_tags_on else "",
            affect=affect,
        )
        await _push(
            user_id,
            "voice_status",
            {
                "turn_id": turn_id,
                "phase": "speaking",
                "conv_id": conv_id,
                "index": idx,
                "emo": emo_tag or "",
            },
        )
        await _push(
            user_id,
            "voice_sentence",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "index": idx,
                "text": sentence,
                "emo": emo_tag or "",
                "file_id": "",
                "mime": "audio/wav",
                "streaming": True,
            },
        )

        loop = asyncio.get_running_loop()
        chunk_q: asyncio.Queue = asyncio.Queue(maxsize=32)
        tts_text = filter_cosy_marks(sentence)

        def _produce() -> None:
            try:
                if engine == "cosyvoice":
                    for pcm, sr in iter_cosyvoice_pcm(
                        text=tts_text,
                        instructions=instr,
                        prompt_wav=prompt_wav,
                    ):
                        if is_turn_cancelled(user_id, conv_id, turn_id):
                            break
                        asyncio.run_coroutine_threadsafe(
                            chunk_q.put(("pcm", pcm, int(sr))), loop
                        ).result(timeout=120)
                else:
                    wav = synthesize_voice_wav(
                        text=tts_text,
                        instructions=instr,
                        keep_loaded=True,
                        for_realtime=True,
                        engine=engine,
                    )
                    if wav and len(wav) > 44 and wav[:4] == b"RIFF":
                        import wave
                        import io as _io

                        with wave.open(_io.BytesIO(wav), "rb") as w:
                            sr = int(w.getframerate())
                            pcm = w.readframes(w.getnframes())
                        asyncio.run_coroutine_threadsafe(
                            chunk_q.put(("pcm", pcm, sr)), loop
                        ).result(timeout=120)
                asyncio.run_coroutine_threadsafe(chunk_q.put(("end", None, None)), loop).result(
                    timeout=30
                )
            except Exception as e:
                asyncio.run_coroutine_threadsafe(
                    chunk_q.put(("err", str(e)[:160], None)), loop
                ).result(timeout=30)

        threading.Thread(target=_produce, name=f"tts-{turn_id}-{idx}", daemon=True).start()

        seq = 0
        pcm_all = bytearray()
        sample_rate = 24000
        t0 = time.perf_counter()
        gap_ms = int(getattr(settings, "voice_sentence_gap_ms", 380) or 0)
        if idx > 1 and gap_ms > 0:
            gap_pcm = b"\x00\x00" * max(1, int(24000 * gap_ms / 1000))
            seq += 1
            await _push(
                user_id,
                "voice_audio",
                {
                    "turn_id": turn_id,
                    "conv_id": conv_id,
                    "index": idx,
                    "seq": seq,
                    "sample_rate": 24000,
                    "format": "s16le",
                    "channels": 1,
                    "gap_ms": gap_ms,
                    "pcm_b64": base64.b64encode(gap_pcm).decode("ascii"),
                },
            )
        while True:
            if is_turn_cancelled(user_id, conv_id, turn_id):
                break
            kind, a, b = await chunk_q.get()
            if kind == "end":
                break
            if kind == "err":
                raise RuntimeError(a or "tts failed")
            pcm = a or b""
            sample_rate = int(b or 24000)
            if not pcm:
                continue
            if is_turn_cancelled(user_id, conv_id, turn_id):
                break
            seq += 1
            pcm_all.extend(pcm)
            is_first_real = (idx == 1 and seq == 1) or (idx > 1 and seq == 2)
            if is_first_real:
                ms = round((time.perf_counter() - t0) * 1000)
                if first_pcm_ms is None:
                    first_pcm_ms = round((time.perf_counter() - t_turn0) * 1000)
                log.info(
                    "voice first_pcm turn=%s idx=%s tts_ms=%s turn_ms=%s bytes=%s emo=%s",
                    turn_id,
                    idx,
                    ms,
                    first_pcm_ms,
                    len(pcm),
                    emo_tag or "-",
                )
            await _push(
                user_id,
                "voice_audio",
                {
                    "turn_id": turn_id,
                    "conv_id": conv_id,
                    "index": idx,
                    "seq": seq,
                    "sample_rate": sample_rate,
                    "format": "s16le",
                    "channels": 1,
                    "pcm_b64": base64.b64encode(pcm).decode("ascii"),
                },
            )

        await _push(
            user_id,
            "voice_audio_end",
            {
                "turn_id": turn_id,
                "conv_id": conv_id,
                "index": idx,
                "seqs": seq,
                "sample_rate": sample_rate,
            },
        )
        if pcm_all:
            try:
                wav = pcm16_mono_to_wav(bytes(pcm_all), sample_rate)
                await asyncio.to_thread(
                    save_audio_bytes,
                    user_id=user_id,
                    data=wav,
                    source="voice_realtime",
                )
            except Exception:
                log.exception("voice archive wav failed")

    async def _tts_worker() -> None:
        while True:
            item = await phrase_q.get()
            if item is _END:
                break
            if is_turn_cancelled(user_id, conv_id, turn_id):
                while not phrase_q.empty():
                    try:
                        phrase_q.get_nowait()
                    except Exception:
                        break
                break
            try:
                if isinstance(item, tuple) and len(item) == 2:
                    text, tag = item
                    await _stream_sentence(str(text), emo_tag=str(tag or ""))
                else:
                    await _stream_sentence(str(item))
            except Exception as e:
                log.exception("voice stream sentence failed")
                await _push(
                    user_id,
                    "voice_error",
                    {
                        "turn_id": turn_id,
                        "conv_id": conv_id,
                        "error": str(e)[:120] or "合成失败",
                    },
                )
                while not phrase_q.empty():
                    try:
                        phrase_q.get_nowait()
                    except Exception:
                        break
                break

    worker = asyncio.create_task(_tts_worker())
    buf_parts: List[str] = []
    pending = ""
    phrases_queued = 0

    async def _enqueue_phrase(s: str) -> None:
        nonlocal phrases_queued, first_phrase_ms
        s = (s or "").strip()
        if not s:
            return
        tag, clean = ("", s)
        if emo_tags_on:
            tag, clean = parse_emotion_tag(s)
            clean = filter_cosy_marks(clean)
        else:
            clean = filter_cosy_marks(s)
        if not clean:
            return
        if phrases_queued == 0 and first_phrase_ms is None:
            first_phrase_ms = round((time.perf_counter() - t_turn0) * 1000)
        await phrase_q.put((clean, tag))
        phrases_queued += 1

    try:
        if mock_reply:
            # 写死文本：按字模拟流式，测首句提前切 / TTS，不调真实 LLM
            timing["llm"] = "mock"
            for ch in mock_reply:
                if llm_first_tok_ms is None:
                    llm_first_tok_ms = round((time.perf_counter() - t_turn0) * 1000)
                buf_parts.append(ch)
                pending += ch
                pending, ready = pop_ready_sentences(
                    pending,
                    first_phrase=(phrases_queued == 0),
                    min_chars=first_min,
                )
                for s in ready:
                    await _enqueue_phrase(s)
                if mock_stream_ms > 0:
                    await asyncio.sleep(mock_stream_ms / 1000.0)
            tail = pending.strip()
            if tail:
                await _enqueue_phrase(tail)
            elif phrases_queued == 0:
                await _enqueue_phrase(mock_reply)
        else:
            timing["llm"] = "dsh"
            t_mem0 = time.perf_counter()
            cached = get_cached_memory_context(user_id=user_id, persona_id=persona_id)
            if cached:
                dsh_system, dsh_instructions = cached
                timing["mem_cache"] = True
                # 后台按本轮 ASR 刷新，供下一轮
                def _refresh() -> None:
                    try:
                        prefetch_memory_context(
                            user_id=user_id, persona_id=persona_id, query=asr
                        )
                    except Exception:
                        log.exception("mem refresh failed")

                threading.Thread(target=_refresh, name=f"mem-{turn_id}", daemon=True).start()
            else:
                dsh_system, dsh_instructions = await asyncio.to_thread(
                    prefetch_memory_context,
                    user_id=user_id,
                    persona_id=persona_id,
                    query=asr,
                )
                timing["mem_cache"] = False
            timing["mem_ms"] = round((time.perf_counter() - t_mem0) * 1000)

            hist = _history(user_id, conv_id)
            voice_provider = (getattr(settings, "voice_dsh_provider", None) or "deepseek-official").strip()
            voice_model = (getattr(settings, "voice_dsh_model", None) or "deepseek-chat").strip()
            async for ev in dsh_turn_stream(
                user_id=user_id,
                conversation_id=conv_id,
                text=asr,
                instructions=dsh_instructions,
                system=dsh_system,
                history=hist,
                mode="chat",
                persona_id=persona_id,
                provider=voice_provider,
                model=voice_model,
            ):
                if is_turn_cancelled(user_id, conv_id, turn_id):
                    break
                kind = ev.get("event") or ""
                data = ev.get("data") or {}
                if kind == "error":
                    raise RuntimeError(data.get("error") or "思考失败")
                if kind == "delta":
                    piece = str(data.get("text") or "")
                    if not piece:
                        continue
                    if llm_first_tok_ms is None:
                        llm_first_tok_ms = round((time.perf_counter() - t_turn0) * 1000)
                    buf_parts.append(piece)
                    pending += piece
                    pending, ready = pop_ready_sentences(
                        pending,
                        first_phrase=(phrases_queued == 0),
                        min_chars=first_min,
                    )
                    for s in ready:
                        await _enqueue_phrase(s)
                if kind == "done":
                    break
            tail = pending.strip()
            if tail:
                await _enqueue_phrase(tail)
            elif phrases_queued == 0 and buf_parts:
                await _enqueue_phrase("".join(buf_parts).strip() or "……")
    except Exception as e:
        log.exception("voice turn text producer failed")
        await _push(
            user_id,
            "voice_error",
            {"turn_id": turn_id, "conv_id": conv_id, "error": str(e)[:120] or "思考失败"},
        )
        await phrase_q.put(_END)
        await worker
        return

    await phrase_q.put(_END)
    await worker

    with _call_lock:
        sess = _call_sessions.get(key)
        if sess and sess.get("active_turn_id") == turn_id:
            sess["active_turn_id"] = ""

    if is_turn_cancelled(user_id, conv_id, turn_id):
        timing["cancelled"] = True
        timing["total_ms"] = round((time.perf_counter() - t_turn0) * 1000)
        log.info("voice_timing cancelled %s", timing)
        return

    full = "".join(full_text_parts).strip() or "……"
    _append_voice_turn(user_id, conv_id, role="assistant", content=full)
    if not mock_reply:
        await _persist_voice_memory(
            user_id=user_id,
            persona_id=persona_id,
            user_text=asr,
            assistant_text=full,
            conv_id=conv_id,
            turn_id=turn_id,
        )
    else:
        # mock 轮也回写 affect，方便听情感基线变化
        try:
            from .companion.affect_rules import propose_after_reply

            propose_after_reply(
                user_id=user_id,
                persona_id=persona_id,
                user_text=asr,
                event_id=f"voice-mock:{turn_id}",
            )
        except Exception:
            pass

    timing.update(
        {
            "llm_first_tok_ms": llm_first_tok_ms,
            "first_phrase_ms": first_phrase_ms,
            "first_pcm_ms": first_pcm_ms,
            "total_ms": round((time.perf_counter() - t_turn0) * 1000),
            "sentences": len(full_text_parts),
            "mock": bool(mock_reply),
        }
    )
    log.info("voice_timing %s", timing)
    await _push(user_id, "voice_timing", timing)

    await _push(
        user_id,
        "voice_done",
        {
            "turn_id": turn_id,
            "conv_id": conv_id,
            "reply": full,
            "sentences": len(full_text_parts),
            "mock": bool(mock_reply),
            "timing": timing,
        },
    )
