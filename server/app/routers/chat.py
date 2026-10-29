from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from .. import memory as mem
from ..dsh_gateway import DshNotConfigured, dsh_interrupt, dsh_turn_stream, require_dsh

logger = logging.getLogger(__name__)

# 用户可见的失败提示（技术细节只打日志）
_FRIENDLY_CHAT_FAIL = "暂时没法回复，稍后再试试吧"
_URL_ONLY_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

# 连发最新优先：会话生成代号；过期轮丢弃输出
_CONV_GENERATION: Dict[str, int] = {}


def _bump_generation(conv_id: str) -> int:
    n = int(_CONV_GENERATION.get(conv_id) or 0) + 1
    _CONV_GENERATION[conv_id] = n
    return n


def _current_generation(conv_id: str) -> int:
    return int(_CONV_GENERATION.get(conv_id) or 0)


def _generation_alive(conv_id: str, gen: int) -> bool:
    return _current_generation(conv_id) == int(gen)


def _pending_user_texts(user_id: str, conv_id: str) -> str:
    """合并上一轮 bot 之后尚未被回答的用户消息（最新意图优先）。

    必须走 inbound 整形：图片/语音在库里是 JSON 壳，直接拼会把 OCR 结果盖掉，
    模型只看到 files/caption，就会幻觉「图是糊的」。
    """
    try:
        last_bot = db.fetchone(
            """
            SELECT seq FROM messages
            WHERE user_id=? AND conv_id=? AND mine=0
              AND kind IN ('text','image','voice','video','file','link')
            ORDER BY seq DESC LIMIT 1
            """,
            (user_id, conv_id),
        )
        after = int(last_bot["seq"]) if last_bot else 0
        rows = db.fetchall(
            """
            SELECT text, kind FROM messages
            WHERE user_id=? AND conv_id=? AND mine=1 AND seq>?
              AND kind IN ('text','image','voice','video','file','link')
            ORDER BY seq ASC
            """,
            (user_id, conv_id, after),
        )
        parts: List[str] = []
        for r in rows:
            shaped = inbound_from_message(
                kind=(r["kind"] or "text"),
                text=(r["text"] or ""),
                mine=True,
            )
            if shaped and shaped.strip():
                parts.append(shaped.strip())
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        # 多条：前面简要，最后一条完整（最新意图）
        head = "；".join(p[:40] for p in parts[:-1])
        return f"（用户连发）{head}\n最新一条：{parts[-1]}"
    except Exception:
        return ""


def _system_warn_text(body: str = _FRIENDLY_CHAT_FAIL) -> str:
    return json.dumps({"v": 1, "level": "warn", "body": body}, ensure_ascii=False)
from ..llm import build_messages, pick_max_tokens
from .. import stickers as stk
from ..media_cards import (
    extract_media_intents,
    fixed_voice_prompt,
    recent_persona_voice_secs,
)
from ..model_route import resolve_dsh_route, usage_model_label
from ..routers.channels import list_visible, resolve_channel_for_persona
from ..routers.prefs import bump_echo_streak, get_shape_flags
from ..shape import (
    inbound_from_message,
    looks_like_format_residue,
    shape_inbound_user_text,
    shape_outbound,
    split_reply_parts,
    typing_delay_seconds,
)
from ..schemas import (
    ChannelOut,
    ConversationOut,
    MessageOut,
    PersonaOut,
    SendMessageRequest,
    SendMessageResponse,
    SyncResponse,
)
from ..security import current_user, iso, new_id
from ..ws_hub import hub

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _persona_out(row, memories: Optional[List] = None) -> PersonaOut:
    try:
        avatar = row["avatar_file_id"] or ""
    except Exception:
        avatar = ""
    try:
        pref = (row["voice_reply_pref"] or "sometimes").strip() or "sometimes"
    except Exception:
        pref = "sometimes"
    if pref not in ("never", "sometimes", "always"):
        pref = "sometimes"
    return PersonaOut(
        id=row["id"],
        name=row["name"],
        color=int(row["color"]),
        bio=row["bio"],
        channel_id=row["channel_id"] or "",
        state=row["state"],
        is_bot=bool(row["is_bot"]),
        verify=row["verify"] or "",
        remark=row["remark"] or "",
        greet=bool(row["greet"]),
        moments_on=bool(row["moments_on"]),
        avatar_file_id=avatar,
        voice_reply_pref=pref,
        memories=memories or [],
    )


def _sync_me(user_id: str) -> Dict[str, Any]:
    row = db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if row is None:
        return {"id": user_id}
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "avatar_file_id": (row["avatar_file_id"] or "") if "avatar_file_id" in keys else "",
        "signature": (row["signature"] or "") if "signature" in keys else "",
        "moments_cover_file_id": (row["moments_cover_file_id"] or "") if "moments_cover_file_id" in keys else "",
    }


def _conv_out(row) -> ConversationOut:
    from ..preview_text import sanitize_stored_preview

    keys = row.keys()
    bg_name = (row["bg_name"] or "默认") if "bg_name" in keys else "默认"
    bg_file_id = (row["bg_file_id"] or "") if "bg_file_id" in keys else ""
    hidden = bool(int(row["hidden"] or 0)) if "hidden" in keys else False
    return ConversationOut(
        id=row["id"],
        persona_id=row["persona_id"],
        group_id=row["group_id"],
        preview=sanitize_stored_preview(row["preview"] or ""),
        last_time=row["last_time"],
        unread=int(row["unread"]),
        pinned=bool(row["pinned"]),
        muted=bool(row["muted"]),
        draft=row["draft"] or "",
        last_seq=int(row["last_seq"]),
        bg_name=bg_name or "默认",
        bg_file_id=bg_file_id or "",
        hidden=hidden,
    )


def _msg_out(row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        client_id=row["client_id"],
        seq=int(row["seq"]),
        conv_id=row["conv_id"],
        sender_id=row["sender_id"],
        mine=bool(row["mine"]),
        kind=row["kind"],
        text=row["text"] or "",
        quote_id=row["quote_id"],
        seconds=int(row["seconds"] or 0),
        bytes=int(row["bytes"] or 0),
        state=row["state"],
        created_at=row["created_at"],
    )


def _owned_conv(user_id: str, conv_id: str):
    row = db.fetchone(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def _next_seq(user_id: str, conv_id: str) -> int:
    row = db.fetchone(
        "SELECT last_seq FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    return int(row["last_seq"]) + 1 if row else 1


def _insert_message(
    *,
    user_id: str,
    conv_id: str,
    client_id: str,
    sender_id: str,
    mine: bool,
    kind: str,
    text: str,
    quote_id: Optional[str] = None,
    seconds: int = 0,
    bytes_: int = 0,
    state: str = "sent",
) -> MessageOut:
    existing = db.fetchone(
        "SELECT * FROM messages WHERE user_id = ? AND client_id = ?",
        (user_id, client_id),
    )
    if existing:
        return _msg_out(existing)

    seq = _next_seq(user_id, conv_id)
    msg_id = new_id("msg_")
    now = iso()
    db.execute(
        """
        INSERT INTO messages(id, client_id, seq, user_id, conv_id, sender_id, mine, kind, text, quote_id, seconds, bytes, state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            msg_id,
            client_id,
            seq,
            user_id,
            conv_id,
            sender_id,
            1 if mine else 0,
            kind,
            text,
            quote_id,
            seconds,
            bytes_,
            state,
            now,
            now,
        ),
    )
    from ..preview_text import conversation_preview

    preview = conversation_preview(kind=kind, text=text, seconds=seconds)
    db.execute(
        """
        UPDATE conversations
        SET last_seq = ?, preview = ?, last_time = ?, unread = CASE WHEN ? = 0 THEN unread + 1 ELSE unread END
        WHERE id = ? AND user_id = ?
        """,
        (seq, preview, now, 1 if mine else 0, conv_id, user_id),
    )
    row = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    assert row is not None
    return _msg_out(row)


@router.get("/sync", response_model=SyncResponse)
def sync(user: Dict[str, Any] = Depends(current_user)) -> SyncResponse:
    uid = user["id"]
    personas_rows = db.fetchall("SELECT * FROM personas WHERE user_id = ? ORDER BY created_at", (uid,))
    personas: List[PersonaOut] = []
    try:
        from ..schedules import sync_persona_proactive
    except Exception:
        sync_persona_proactive = None  # type: ignore
    for p in personas_rows:
        if sync_persona_proactive is not None:
            try:
                sync_persona_proactive(
                    uid,
                    p["id"],
                    greet=bool(p["greet"]),
                    moments_on=bool(p["moments_on"]),
                )
            except Exception:
                pass
        mems = db.fetchall(
            "SELECT id, text FROM memories WHERE user_id = ? AND persona_id = ? ORDER BY updated_at",
            (uid, p["id"]),
        )
        personas.append(_persona_out(p, [{"id": m["id"], "text": m["text"]} for m in mems]))
    convs = [
        _conv_out(r)
        for r in db.fetchall(
            """
            SELECT * FROM conversations
            WHERE user_id = ?
              AND IFNULL(hidden, 0) = 0
              AND IFNULL(deleted_at, '') = ''
            ORDER BY pinned DESC, last_time DESC
            """,
            (uid,),
        )
    ]
    # 每个会话最近 50 条，足够首屏；更早的走分页接口
    messages: List[MessageOut] = []
    for c in convs:
        rows = db.fetchall(
            """
            SELECT * FROM (
              SELECT * FROM messages WHERE user_id = ? AND conv_id = ? ORDER BY seq DESC LIMIT 50
            ) ORDER BY seq ASC
            """,
            (uid, c.id),
        )
        messages.extend(_msg_out(r) for r in rows)
    channels = [
        ChannelOut(
            id=c.id,
            name=c.name,
            model=c.model,
            base_url=c.base_url,
            key_filled=c.key_filled,
            shared=c.shared,
        )
        for c in list_visible(uid)
    ]
    return SyncResponse(
        personas=personas,
        conversations=convs,
        messages=messages,
        channels=channels,
        me=_sync_me(user["id"]),
    )


@router.get("/conversations/{conv_id}/messages", response_model=List[MessageOut])
def list_messages(
    conv_id: str,
    before_seq: Optional[int] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    user: Dict[str, Any] = Depends(current_user),
) -> List[MessageOut]:
    _owned_conv(user["id"], conv_id)
    if before_seq is None:
        rows = db.fetchall(
            """
            SELECT * FROM (
              SELECT * FROM messages WHERE user_id = ? AND conv_id = ? ORDER BY seq DESC LIMIT ?
            ) ORDER BY seq ASC
            """,
            (user["id"], conv_id, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM (
              SELECT * FROM messages
              WHERE user_id = ? AND conv_id = ? AND seq < ?
              ORDER BY seq DESC LIMIT ?
            ) ORDER BY seq ASC
            """,
            (user["id"], conv_id, before_seq, limit),
        )
    return [_msg_out(r) for r in rows]


def _update_message_text(
    msg_id: str,
    text: str,
    state: str = "sent",
    *,
    kind: Optional[str] = None,
) -> Optional[MessageOut]:
    now = iso()
    if kind:
        db.execute(
            "UPDATE messages SET text = ?, state = ?, kind = ?, updated_at = ? WHERE id = ?",
            (text, state, kind, now, msg_id),
        )
    else:
        db.execute(
            "UPDATE messages SET text = ?, state = ?, updated_at = ? WHERE id = ?",
            (text, state, now, msg_id),
        )
    row = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    if row is None:
        return None
    from ..preview_text import conversation_preview

    preview = conversation_preview(kind=row["kind"] or "text", text=text, seconds=int(row["seconds"] or 0))
    db.execute(
        "UPDATE conversations SET preview = ?, last_time = ? WHERE id = ?",
        (preview, now, row["conv_id"]),
    )
    return _msg_out(row)


async def _set_typing(user_id: str, conv_id: str, active: bool, name: str = "") -> None:
    await hub.send_user(
        user_id,
        "typing",
        {"conv_id": conv_id, "active": active, "name": name},
    )


def _log_usage(
    *,
    user_id: str,
    persona_id: str,
    channel_id: str,
    conv_id: str,
    model: str,
    client: str,
    status: str,
    error: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated: bool = False,
    metrics: Optional[Dict[str, Any]] = None,
) -> str:
    """写入用量；返回 usage_logs.id（失败返回空串）。"""
    uid = ""
    try:
        import json as _json

        m = dict(metrics or {})
        input_tokens = int(m.get("inputTokens") or m.get("input_tokens") or 0)
        cache_read = int(m.get("cacheReadTokens") or m.get("cache_read_tokens") or 0)
        cache_write = int(m.get("cacheWriteTokens") or m.get("cache_write_tokens") or 0)
        context_tokens = int(m.get("contextTokens") or m.get("context_tokens") or 0)
        if context_tokens <= 0:
            context_tokens = input_tokens + cache_read + cache_write
        cache_hit = float(m.get("cacheHitRate") or m.get("cache_hit_rate") or 0)
        if cache_hit <= 0 and context_tokens > 0 and cache_read > 0:
            cache_hit = round(100.0 * cache_read / context_tokens, 2)
        # prompt_tokens：优先记「本轮上下文总量」，便于对照缓存
        if context_tokens > 0:
            prompt_tokens = max(prompt_tokens, context_tokens)
        if int(m.get("outputTokens") or m.get("output_tokens") or 0) > 0:
            completion_tokens = max(
                completion_tokens, int(m.get("outputTokens") or m.get("output_tokens") or 0)
            )
        uid = new_id("ul_")
        db.execute(
            """
            INSERT INTO usage_logs(
              id, user_id, persona_id, channel_id, conv_id, model,
              prompt_tokens, completion_tokens, total_tokens, client, status, error, created_at, estimated,
              input_tokens, cache_read_tokens, cache_write_tokens, context_tokens, cache_hit_rate,
              prompt_chars, history_messages, model_calls, dsh_session_id, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                user_id,
                persona_id,
                channel_id,
                conv_id,
                model,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                client,
                status,
                error[:500],
                iso(),
                1 if estimated else 0,
                input_tokens,
                cache_read,
                cache_write,
                context_tokens,
                cache_hit,
                int(m.get("promptChars") or m.get("prompt_chars") or 0),
                int(m.get("historyMessages") or m.get("history_messages") or 0),
                int(m.get("modelCalls") or m.get("model_calls") or 0),
                str(m.get("dshSessionId") or m.get("dsh_session_id") or "")[:120],
                _json.dumps(m, ensure_ascii=False)[:8000],
            ),
        )
    except Exception:
        return ""
    return uid


async def _generate_reply(
    *,
    user_id: str,
    conv_id: str,
    persona_id: str,
    user_text: str = "",
    client: str = "app",
    internal: bool = False,
    memory_receipt: Optional[Dict[str, Any]] = None,
    generation: int = 0,
) -> None:
    """微信逻辑：先「对方正在输入」，有正文后才落库并推气泡。

    internal=True：主动问候等系统触发，指令只进模型上下文，不落用户气泡。
    memory_receipt：显式记忆操作已前置提交的回执（成功才能对用户说记住了/改好了）。
    generation：会话生成代号；若已被更新的消息顶替则丢弃本轮输出。
    """
    gen = int(generation or 0)
    if gen and not _generation_alive(conv_id, gen):
        return

    persona = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if persona is None:
        return

    display = persona["remark"] or persona["name"] or "对方"
    # 连发：合并上一轮 bot 之后的用户消息，回答最新意图
    if not internal:
        caller_text = (user_text or "").strip()
        merged = _pending_user_texts(user_id, conv_id)
        if merged:
            # 调用方已带 OCR/整形（[对方发来…]），合并结果若仍是 JSON 壳或更短，保留调用方
            merged_s = merged.strip()
            caller_rich = caller_text.startswith("[对方发来") or "图中文字" in caller_text
            merged_raw_shell = merged_s.startswith("{") and '"files"' in merged_s
            if caller_rich and (merged_raw_shell or len(merged_s) < len(caller_text)):
                user_text = caller_text
            else:
                user_text = merged

    # qq-bridge：免打扰时段可跳过模型（prefs.quiet_blocks_chat=1）→ 只记缓冲
    if not internal:
        try:
            from ..schedules import _in_quiet

            pref = db.fetchone("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))
            blocks = False
            if pref is not None:
                try:
                    blocks = bool(int(pref["quiet_blocks_chat"] or 0))
                except Exception:
                    blocks = bool(pref["quiet_blocks_chat"]) if "quiet_blocks_chat" in pref.keys() else False
            if blocks and _in_quiet(user_id):
                from ..wake_buffer import record_skipped

                record_skipped(user_id, conv_id, text=user_text or "", reason="quiet_hours")
                await _set_typing(user_id, conv_id, False, display)
                return
        except Exception:
            pass

    # 不在排队阶段就亮「正在输入」，避免 DSH 堵车时所有会话假输入
    # 真正开始出字 / 进 running 后再 set typing

    async def _fail(*, detail: str, channel_id: str = "", model: str = "") -> None:
        logger.warning("chat_fail user=%s conv=%s detail=%s", user_id, conv_id, detail)
        bot = _insert_message(
            user_id=user_id,
            conv_id=conv_id,
            client_id=new_id("botcli_"),
            sender_id="system",
            mine=False,
            kind="system",
            text=_system_warn_text(),
            state="sent",
        )
        await _set_typing(user_id, conv_id, False, display)
        await hub.send_user(user_id, "message", bot.model_dump())
        _log_usage(
            user_id=user_id,
            persona_id=persona_id,
            channel_id=channel_id,
            conv_id=conv_id,
            model=model,
            client=client,
            status="error",
            error=detail,
        )

    try:
        require_dsh()
    except DshNotConfigured as exc:
        await _fail(detail=f"DSH not configured: {exc}")
        return

    channel = resolve_channel_for_persona(user_id, persona["channel_id"] or "")
    if channel is None:
        # 用量展示用；思考环只走 DSH，不要求渠道 Key；默认官方
        channel = {
            "id": "",
            "name": "",
            "model": "deepseek-official/deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "key_enc": "",
        }
    dsh_provider, dsh_model = resolve_dsh_route(channel)
    # 本地 Qwen：健康检查失败直接报错，不回退官方
    if dsh_provider == "local-qwen":
        try:
            import httpx as _httpx

            base = str(channel.get("base_url") or "http://127.0.0.1:9292/v1").rstrip("/")
            if not base.endswith("/v1"):
                base = f"{base}/v1"
            with _httpx.Client(timeout=8.0) as _c:
                hr = _c.get(f"{base}/models")
                if hr.status_code >= 400:
                    raise RuntimeError(f"local_qwen_unhealthy:{hr.status_code}")
        except Exception as exc:
            await _fail(
                detail=f"本地 Qwen 不可用（不回退其他模型）: {exc}",
                channel_id=str(channel.get("id") or ""),
                model=usage_model_label(dsh_provider, dsh_model),
            )
            return

    rows = db.fetchall(
        """
        SELECT * FROM (
          SELECT * FROM messages
          WHERE user_id = ? AND conv_id = ?
            AND kind IN ('text', 'image', 'voice', 'video', 'file', 'link', 'system')
          ORDER BY seq DESC LIMIT 30
        ) ORDER BY seq ASC
        """,
        (user_id, conv_id),
    )
    history: List[Dict[str, str]] = []
    recent_assistant: List[str] = []
    for r in rows:
        try:
            if "context_skip" in r.keys() and r["context_skip"]:
                continue
        except (KeyError, IndexError, TypeError):
            pass
        kind = r["kind"] or "text"
        content = inbound_from_message(
            kind=kind,
            text=r["text"] or "",
            mine=bool(r["mine"]),
        )
        if not content:
            continue
        # 系统事件当作用户侧事实喂给模型，避免人设装作还在听歌/用设备
        if kind == "system":
            history.append({"role": "user", "content": content})
            continue
        role = "user" if r["mine"] else "assistant"
        history.append({"role": role, "content": content})
        if role == "assistant":
            recent_assistant.append(content)

    shape_on, split_on = get_shape_flags(user_id)

    eraser_rows = db.fetchall(
        """
        SELECT id, pattern, mode, enabled, hits FROM eraser_rules
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at ASC
        """,
        (user_id, persona_id),
    )
    eraser_rules = [
        {
            "id": er["id"],
            "pattern": er["pattern"],
            "mode": er["mode"],
            "enabled": bool(er["enabled"]),
        }
        for er in eraser_rows
    ]

    def _bump_eraser(rule: dict) -> None:
        rid = rule.get("id")
        if not rid:
            return
        try:
            db.execute("UPDATE eraser_rules SET hits = hits + 1 WHERE id = ?", (rid,))
        except Exception:
            pass

    # 单一装配：companion 快照（记忆+情绪+成长）→ instructions；system 不再重复塞记忆
    evidence_block = ""
    mems: List[str] = []
    affect_hint = ""
    growth_hint = ""
    affect_state: Dict[str, Any] = {}
    turn_snap: Optional[Dict[str, Any]] = None
    try:
        from ..companion.budget import ContextBudgeter, approx_tokens
        from ..companion.snapshot import build_companion_snapshot
        from ..evidence import list_person_notes

        pref = db.fetchone("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))
        inj_on = True
        if pref is not None and "memory_inject" in pref.keys():
            try:
                v = pref["memory_inject"]
                inj_on = True if v is None else bool(int(v))
            except Exception:
                inj_on = True
        q = user_text or (history[-1]["content"] if history else "")
        snap = build_companion_snapshot(user_id, persona_id, query=q) if inj_on else None
        turn_snap = snap
        if snap:
            mems = [m.get("text") or "" for m in (snap.get("memories") or []) if m.get("text")]
            a = snap.get("affect") or {}
            if isinstance(a, dict):
                affect_state = a
            affect_hint = snap.get("affect_strategy") or (
                f"当前情绪锚点：愉悦{a.get('joy', 35)} 低落{a.get('sadness', 35)} "
                f"兴奋{a.get('excitement', 35)} 担忧{a.get('worry', 35)} "
                f"不悦{a.get('annoyance', 35)} 好奇{a.get('curiosity', 35)}"
                + (f"（{a.get('reason')}）" if a.get("reason") else "")
            )
            if snap.get("growth_mode"):
                growth_hint = f"人格模式：{snap.get('growth_mode')}"
            world_hint = (snap.get("world_presence") or "").strip()
            if world_hint:
                affect_hint = (affect_hint + "\n" + world_hint).strip()
            if memory_receipt and memory_receipt.get("handled"):
                st = memory_receipt.get("status") or ""
                if st == "ok":
                    affect_hint += (
                        f"\n记忆操作回执：{memory_receipt.get('message') or '已提交'}。"
                        f"可如实告知用户（{memory_receipt.get('text') or ''}）。"
                    )
                elif st == "pending_confirm":
                    affect_hint += (
                        f"\n记忆操作未完成：{memory_receipt.get('message') or '需确认'}。"
                        "不要假装已经记住或改好。"
                    )
            notes = list_person_notes(user_id, persona_id)[:3]
            lines = []
            for n in notes:
                lines.append(f"- [person_note] {n['text']}")
            for m in (snap.get("memories") or [])[:8]:
                lines.append(f"- [memory] {m.get('text') or ''}")
            if lines:
                evidence_block = "本轮证据（人物档+记忆，单一管道）：\n" + "\n".join(lines)
            # 集合查询：列出全部不占 8 条预算
            uq = (user_text or "").strip()
            if any(k in uq for k in ("列出全部", "有哪些", "全部编号", "都有什么")):
                try:
                    from ..companion.store import MemoryStore

                    all_rows = MemoryStore().list_active(user_id, persona_id, limit=200)
                    if all_rows:
                        listing = "\n".join(
                            f"- {r.get('text') or ''}" for r in all_rows[:80]
                        )
                        evidence_block = (
                            (evidence_block + "\n") if evidence_block else ""
                        ) + f"集合查询结果（完整列表节选）：\n{listing}"
                except Exception:
                    pass
            # 预算检查点（不足则裁近文在 build 侧靠 history 切片；此处只记日志）
            br = ContextBudgeter().check_final(
                system=(persona["bio"] or "")[:800],
                instructions=evidence_block + affect_hint,
                recent="".join((h.get("content") or "") for h in history[-6:]),
                user_text=q,
            )
            if not br.ok:
                # 超软上限：砍记忆行
                while lines and approx_tokens(evidence_block) > 1200:
                    lines.pop()
                    evidence_block = (
                        "本轮证据（人物档+记忆，单一管道）：\n" + "\n".join(lines) if lines else ""
                    )
                mems = mems[:5]
    except Exception:
        evidence_block = ""
        try:
            hit = mem.search_memories(
                user_id,
                persona_id,
                user_text or (history[-1]["content"] if history else ""),
                limit=6,
            )
            mems = [m["text"] for m in hit]
            if mems:
                evidence_block = "相关记忆：\n- " + "\n- ".join(mems[:6])
        except Exception:
            mems = []
    try:
        from ..schedules import upcoming_for_persona

        sched_hints = upcoming_for_persona(user_id, persona_id)
    except Exception:
        sched_hints = []
    # 成长锁定时用 baseline bio
    bio_for_turn = persona["bio"] or ""
    try:
        from ..companion.growth import GrowthService

        gst = GrowthService().get_state(user_id, persona_id)
        if gst.get("mode") == "initial_locked" and gst.get("baseline_bio"):
            bio_for_turn = gst["baseline_bio"]
        elif gst.get("baseline_bio") and not bio_for_turn:
            bio_for_turn = gst["baseline_bio"]
    except Exception:
        pass
    _caps_enabled = set()
    _has_fixed_voice = False
    try:
        from ..product_capabilities import enabled_ids_for_user

        _urow = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        _caps_enabled = enabled_ids_for_user(
            user_id, is_admin=bool(_urow and _urow["is_admin"])
        )
        _has_fixed_voice = bool(fixed_voice_prompt(user_id=user_id, persona_id=persona_id))
    except Exception:
        _caps_enabled = set()
        _has_fixed_voice = False
    messages = build_messages(
        persona_bio=bio_for_turn,
        persona_name=persona["name"],
        history=history[-6:],
        memories=mems,
        schedule_hints=sched_hints,
        include_memory_in_system=False,
        affect_hint=affect_hint,
        growth_hint=growth_hint,
        enabled_caps=_caps_enabled,
        has_fixed_voice=_has_fixed_voice,
    )
    # 主动问候：内部指令只喂本轮，不当用户气泡
    if internal and user_text.strip():
        messages = list(messages) + [{"role": "user", "content": user_text.strip()}]

    bot_msg_id: Optional[str] = None
    buf: List[str] = []
    usage_out: Dict[str, Any] = {}
    _ = pick_max_tokens(history, user_text)  # 保留长度启发式供日后 gateway 传参
    try:
        system = ""
        hist_payload: List[Dict[str, str]] = []
        if messages:
            if messages[0].get("role") == "system":
                system = messages[0].get("content") or ""
                hist_payload = [
                    {"role": m["role"], "content": m["content"]}
                    for m in messages[1:-1]
                    if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
                ]
            else:
                hist_payload = [
                    {"role": m["role"], "content": m["content"]}
                    for m in messages[:-1]
                    if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
                ]
        turn_text = user_text.strip() or (
            history[-1]["content"] if history else "（继续）"
        )
        # qq-bridge：未唤醒缓冲 → 真醒时附摘要前缀（私聊多数回合缓冲为空）
        try:
            from ..wake_buffer import consume_summary

            prefix = consume_summary(user_id, conv_id)
            if prefix:
                turn_text = prefix + turn_text
        except Exception:
            pass
        if gen and not _generation_alive(conv_id, gen):
            await _set_typing(user_id, conv_id, False, display)
            return
        # 记忆只走 instructions 一侧，避免 system + evidence 双份常驻
        mem_patch = evidence_block or ""
        # 已注册能力才注入暗号；未开通/本机没有 → 明确告知勿装能用
        try:
            from ..product_capabilities import prompt_hints as _cap_hints

            _urow2 = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
            vp = fixed_voice_prompt(user_id=user_id, persona_id=persona_id)
            cap_block = _cap_hints(
                user_id,
                is_admin=bool(_urow2 and _urow2["is_admin"]),
                has_fixed_voice=bool(vp),
            )
            if cap_block:
                mem_patch = ((mem_patch + "\n") if mem_patch else "") + cap_block
        except Exception:
            pass
        # M4：主动问候后的 listen 跟随窗
        try:
            crow = db.fetchone(
                "SELECT listen_until FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            until = ""
            if crow is not None and "listen_until" in crow.keys():
                until = (crow["listen_until"] or "").strip()
            if until:
                from datetime import datetime, timezone

                end = datetime.fromisoformat(until.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < end:
                    mem_patch = (
                        (mem_patch + "\n") if mem_patch else ""
                    ) + "（跟随窗内：对方刚收到你的主动消息，请短接话、接住连发，勿重新开场寒暄。）"
        except Exception:
            pass
        # 一起听：全屏收起 / 小窗时仍 active，聊天要知情
        try:
            from ..listen_runtime import listen_context_hint

            lh = listen_context_hint(user_id, conv_id)
            if lh:
                mem_patch = ((mem_patch + "\n") if mem_patch else "") + lh
        except Exception:
            pass
        # 设备长任务走 agent 轨（仅当虚拟电脑/手机已注册）
        _device_re = re.compile(
            r"(开(下|一)?机|虚拟(机|电脑|手机)|帮我(点|打开|操作)|看下(电脑|手机)|redroid|桌面|手机屏幕)",
            re.I,
        )
        _vd_ok = ("virtual_desktop" in _caps_enabled) or ("virtual_phone" in _caps_enabled)
        dsh_mode = "agent" if (_vd_ok and _device_re.search(turn_text)) else "chat"
        if dsh_mode == "agent":
            # 先落一条人设口吻确认，再进长操作
            ack = _insert_message(
                user_id=user_id,
                conv_id=conv_id,
                client_id=new_id("botcli_"),
                sender_id=persona_id,
                mine=False,
                kind="text",
                text="行，我弄一下～",
                state="sent",
            )
            await hub.send_user(user_id, "message", ack.model_dump())
            await hub.send_user(
                user_id,
                "device_progress",
                {"conv_id": conv_id, "phase": "start", "text": "准备操作设备…"},
            )
        try:
            async for ev in dsh_turn_stream(
                user_id=user_id,
                conversation_id=conv_id,
                text=turn_text,
                instructions=mem_patch,
                system=system,
                history=hist_payload,
                mode=dsh_mode,
                persona_id=persona_id or "",
                provider=dsh_provider,
                model=dsh_model,
            ):
                kind = ev.get("event") or ""
                data = ev.get("data") or {}
                if kind == "status":
                    phase = str(data.get("phase") or "")
                    if phase == "running":
                        await _set_typing(user_id, conv_id, True, display)
                    if dsh_mode == "agent" and phase:
                        await hub.send_user(
                            user_id,
                            "device_progress",
                            {
                                "conv_id": conv_id,
                                "phase": phase,
                                "text": str(data.get("text") or phase),
                                "busy": data.get("busy"),
                            },
                        )
                    continue
                if kind == "error":
                    raise RuntimeError(data.get("error") or "dsh stream error")
                if kind == "delta":
                    if gen and not _generation_alive(conv_id, gen):
                        # 已被更新的用户消息顶替：丢弃本轮
                        buf.clear()
                        break
                    piece = str(data.get("text") or "")
                    if not piece:
                        continue
                    if not buf:
                        await _set_typing(user_id, conv_id, True, display)
                    buf.append(piece)
                    if bot_msg_id is None:
                        bot = _insert_message(
                            user_id=user_id,
                            conv_id=conv_id,
                            client_id=new_id("botcli_"),
                            sender_id=persona_id,
                            mine=False,
                            kind="text",
                            text=piece,
                            state="sending",
                        )
                        bot_msg_id = bot.id
                        await _set_typing(user_id, conv_id, False, display)
                        await hub.send_user(user_id, "message", bot.model_dump())
                        if dsh_mode == "agent":
                            await hub.send_user(
                                user_id,
                                "device_progress",
                                {"conv_id": conv_id, "phase": "delta", "text": piece[:80]},
                            )
                    else:
                        await hub.send_user(
                            user_id,
                            "message_delta",
                            {"id": bot_msg_id, "conv_id": conv_id, "delta": piece},
                        )
                elif kind == "done":
                    final = str(data.get("finalResponse") or "").strip()
                    if final and not buf:
                        buf.append(final)
                    metrics = data.get("metrics") or data.get("usage") or {}
                    if not isinstance(metrics, dict):
                        metrics = {}
                    # 网关顶层 provider/model 写入 metrics，供 usage 记账
                    if data.get("provider"):
                        metrics["provider"] = data.get("provider")
                    if data.get("model"):
                        metrics["model"] = data.get("model")
                    if metrics:
                        usage_out["metrics"] = metrics
                        usage_out["prompt_tokens"] = int(
                            metrics.get("contextTokens")
                            or metrics.get("promptTokensEst")
                            or max(1, len(turn_text) // 4)
                        )
                        usage_out["completion_tokens"] = int(
                            metrics.get("outputTokens")
                            or max(1, len("".join(buf)) // 2)
                        )
                        usage_out["estimated"] = str(metrics.get("source") or "") == "estimate"
                    else:
                        usage_out["prompt_tokens"] = max(1, len(turn_text) // 4)
                        usage_out["completion_tokens"] = max(1, len("".join(buf)) // 2)
                        usage_out["estimated"] = True
                        usage_out["metrics"] = {
                            "promptChars": len(turn_text),
                            "promptTokensEst": usage_out["prompt_tokens"],
                            "outputTokens": usage_out["completion_tokens"],
                            "contextTokens": usage_out["prompt_tokens"],
                            "cacheHitRate": 0,
                            "source": "estimate",
                            "provider": dsh_provider,
                            "model": dsh_model,
                        }
        except Exception as exc:
            await _fail(
                detail=f"DSH gateway error: {exc}",
                channel_id=str(channel["id"] or ""),
                model=usage_model_label(dsh_provider, dsh_model),
            )
            return
        # 以网关回包的真实模型为准
        _m = usage_out.get("metrics") if isinstance(usage_out.get("metrics"), dict) else {}
        if isinstance(_m, dict):
            if _m.get("provider"):
                dsh_provider = str(_m.get("provider") or dsh_provider)
            if _m.get("model"):
                dsh_model = str(_m.get("model") or dsh_model)
        actual_model_label = usage_model_label(dsh_provider, dsh_model)
        if gen and not _generation_alive(conv_id, gen):
            # 过期轮：若已落了 sending 气泡，改成系统轻提示或撤回为空
            if bot_msg_id is not None:
                try:
                    _update_message_text(bot_msg_id, "", state="sent", kind="text")
                except Exception:
                    pass
            await _set_typing(user_id, conv_id, False, display)
            return
        raw_text = "".join(buf).strip() or "……"
        if shape_on:
            text, shape_stats = shape_outbound(
                raw_text,
                recent_assistant=recent_assistant[-8:],
                eraser_rules=eraser_rules,
                on_eraser_hit=_bump_eraser,
            )
            if not text.strip():
                text = "……"
            echo_hit = "echo" in shape_stats.steps
            streak = bump_echo_streak(user_id, echo_hit)
            if echo_hit and streak >= 2:
                await hub.send_user(
                    user_id,
                    "shape_hint",
                    {
                        "kind": "echo",
                        "persona_id": persona_id,
                        "conv_id": conv_id,
                        "text": "最近开头总在重复，建议写一条课程记忆（例如：不要总用同一个开场）。",
                    },
                )
        else:
            text = raw_text
            bump_echo_streak(user_id, False)
        # qq-bridge 出站闸门思想：撕掉（（草稿））后再落库/推送
        try:
            from ..outbound_gate import strip_drafts

            text, _draft = strip_drafts(text)
            if not text.strip():
                text = "……"
        except Exception:
            pass

        text, media_intents = extract_media_intents(text)
        sticker_cooled = False
        voice_cooled = False
        try:
            sticker_cooled = stk.recent_persona_sticker_secs(
                user_id=user_id, conv_id=conv_id, within_seconds=90
            )
        except Exception:
            sticker_cooled = False
        try:
            from ..config import get_settings as _gs

            voice_cooled = recent_persona_voice_secs(
                user_id=user_id,
                conv_id=conv_id,
                within_seconds=float(_gs().voice_reply_cooldown_seconds or 45),
            )
        except Exception:
            voice_cooled = False
        try:
            # 命中情绪就配图；90s 冷却防刷。同时看用户原话（角色回复未必带关键词）。
            media_intents = stk.maybe_augment_sticker_intents(
                text,
                media_intents,
                cooled=sticker_cooled,
                user_id=user_id,
                probability=1.0,
                extra_text=user_text,
            )
        except Exception:
            pass
        # 无固定音色 / 冷却中 / 偏好 never：普通语音标记并回文字；唱歌走独立门禁
        voice_prompt_fixed = None
        try:
            voice_prompt_fixed = fixed_voice_prompt(user_id=user_id, persona_id=persona_id)
        except Exception:
            voice_prompt_fixed = None
        voice_pref = "sometimes"
        persona_avatar_id = ""
        try:
            prow = db.fetchone(
                "SELECT voice_reply_pref, avatar_file_id FROM personas WHERE id = ? AND user_id = ?",
                (persona_id, user_id),
            )
            if prow is not None:
                voice_pref = (prow["voice_reply_pref"] or "sometimes").strip() or "sometimes"
                keys = prow.keys() if hasattr(prow, "keys") else []
                if "avatar_file_id" in keys:
                    persona_avatar_id = (prow["avatar_file_id"] or "").strip()
        except Exception:
            voice_pref = "sometimes"
        if voice_pref not in ("never", "sometimes", "always"):
            voice_pref = "sometimes"
        if voice_pref == "always":
            voice_cooled = False  # 仍受固定音色与字数闸
        sing_enabled = False
        sing_cooled = False
        try:
            from ..config import get_settings as _gs2
            from ..product_capabilities import is_enabled_for_user as _cap_on

            _urow3 = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
            _is_adm = bool(_urow3 and _urow3["is_admin"])
            sing_enabled = _cap_on(user_id, "sing", is_admin=_is_adm)
            sing_cooled = recent_persona_voice_secs(
                user_id=user_id,
                conv_id=conv_id,
                within_seconds=float(_gs2().sing_cooldown_seconds or 120),
            )
        except Exception:
            sing_enabled = False
            sing_cooled = False
        # 产品能力门禁：未注册的暗号 → 去掉并准备人设口吻告知
        try:
            from ..product_capabilities import filter_media_intents

            _urow4 = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
            media_intents, _cap_denies = filter_media_intents(
                media_intents,
                user_id=user_id,
                is_admin=bool(_urow4 and _urow4["is_admin"]),
                has_fixed_voice=bool(voice_prompt_fixed),
            )
            for line in _cap_denies:
                text = (text + "\n" + line).strip() if text.strip() else line
        except Exception:
            _cap_denies = []
        if voice_pref == "never" or not voice_prompt_fixed or voice_cooled:
            kept: list = []
            for intent in media_intents:
                if intent.get("kind") == "voice":
                    line = str(intent.get("prompt") or "").strip()
                    if line:
                        text = (text + "\n" + line).strip() if text.strip() else line
                else:
                    kept.append(intent)
            media_intents = kept
        if not sing_enabled or sing_cooled:
            kept_s: list = []
            for intent in media_intents:
                if intent.get("kind") == "sing":
                    # 冷却中：丢掉唱标记，不并入长歌词刷屏；未开通已在 filter 处理
                    pass
                else:
                    kept_s.append(intent)
            media_intents = kept_s
        parts = split_reply_parts(text) if split_on else ([text] if text.strip() else [])
        if not parts and not media_intents:
            parts = ["……"]

        # 第一条：复用流式气泡或新建
        if parts:
            first = parts[0]
            if bot_msg_id is None:
                bot = _insert_message(
                    user_id=user_id,
                    conv_id=conv_id,
                    client_id=new_id("botcli_"),
                    sender_id=persona_id,
                    mine=False,
                    kind="text",
                    text=first,
                    state="sent",
                )
                await _set_typing(user_id, conv_id, False, display)
                await hub.send_user(user_id, "message", bot.model_dump())
            else:
                final = _update_message_text(bot_msg_id, first, state="sent")
                if final:
                    await hub.send_user(user_id, "message", final.model_dump())

            # 后续分句：打字间隔后再落库推送
            for part in parts[1:]:
                await _set_typing(user_id, conv_id, True, display)
                await asyncio.sleep(typing_delay_seconds(part))
                more = _insert_message(
                    user_id=user_id,
                    conv_id=conv_id,
                    client_id=new_id("botcli_"),
                    sender_id=persona_id,
                    mine=False,
                    kind="text",
                    text=part,
                    state="sent",
                )
                await _set_typing(user_id, conv_id, False, display)
                await hub.send_user(user_id, "message", more.model_dump())
        elif bot_msg_id is not None:
            # 只有媒体意图：把空流式气泡改成省略或删不掉则写成「……」
            final = _update_message_text(bot_msg_id, "……", state="sent")
            if final:
                await hub.send_user(user_id, "message", final.model_dump())

        # 媒体意图：统一调度（检索 topK → flash 选片 → 发送），不把目录塞进主 prompt
        try:
            stk.ensure_persona_stickers(
                user_id=user_id, persona_id=persona_id, persona_name=display
            )
        except Exception:
            pass
        from ..config import get_settings as _get_settings
        from ..media_dispatch import DispatchCtx, dispatch_intents

        voice_max = int(_get_settings().voice_reply_max_chars or 80)
        sing_max = int(_get_settings().sing_prompt_max_chars or 200)
        if media_intents:
            try:
                await dispatch_intents(
                    DispatchCtx(
                        user_id=user_id,
                        conv_id=conv_id,
                        persona_id=persona_id,
                        display=display,
                        user_text=user_text or "",
                        persona_avatar_id=persona_avatar_id or "",
                        affect_state=affect_state if isinstance(affect_state, dict) else {},
                        sticker_cooled=sticker_cooled,
                        voice_cooled=voice_cooled,
                        voice_prompt_fixed=voice_prompt_fixed,
                        sing_enabled=sing_enabled,
                        sing_cooled=sing_cooled,
                        voice_max=voice_max,
                        sing_max=sing_max,
                    ),
                    media_intents,
                )
            except Exception:
                # 媒体失败不应吞掉已发出的文字回复，也不要一直「正在输入」
                logger.exception(
                    "media_dispatch failed user=%s conv=%s", user_id, conv_id
                )
                try:
                    await _set_typing(user_id, conv_id, False, display)
                except Exception:
                    pass

        prompt_tokens = int(usage_out.get("prompt_tokens") or 0)
        completion_tokens = int(usage_out.get("completion_tokens") or 0)
        estimated = bool(usage_out.get("estimated"))
        metrics = usage_out.get("metrics") if isinstance(usage_out.get("metrics"), dict) else {}
        if prompt_tokens <= 0 and completion_tokens <= 0:
            estimated = True
            completion_tokens = max(1, len(text) // 2)
            prompt_tokens = max(1, sum(len(m.get("content") or "") for m in messages) // 4)
            metrics = {
                **metrics,
                "promptTokensEst": prompt_tokens,
                "outputTokens": completion_tokens,
                "contextTokens": prompt_tokens,
                "cacheHitRate": 0,
                "source": "estimate",
            }
        usage_id = _log_usage(
            user_id=user_id,
            persona_id=persona_id,
            channel_id=channel["id"],
            conv_id=conv_id,
            model=actual_model_label,
            client=client,
            status="ok",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated=estimated,
            metrics=metrics,
        )
        # 推给前端：本轮上下文 / 缓存命中
        try:
            await hub.send_user(
                user_id,
                "turn_metrics",
                {
                    "conv_id": conv_id,
                    "persona_id": persona_id,
                    "usage_id": usage_id,
                    "context_tokens": int(metrics.get("contextTokens") or prompt_tokens),
                    "input_tokens": int(metrics.get("inputTokens") or 0),
                    "output_tokens": int(metrics.get("outputTokens") or completion_tokens),
                    "cache_read_tokens": int(metrics.get("cacheReadTokens") or 0),
                    "cache_write_tokens": int(metrics.get("cacheWriteTokens") or 0),
                    "cache_hit_rate": float(metrics.get("cacheHitRate") or 0),
                    "prompt_chars": int(metrics.get("promptChars") or 0),
                    "history_messages": int(metrics.get("historyMessages") or 0),
                    "model_calls": int(metrics.get("modelCalls") or 0),
                    "resumed": bool(metrics.get("resumed")),
                    "source": str(metrics.get("source") or ("estimate" if estimated else "dsh")),
                },
            )
        except Exception:
            pass
        # 聊完后抽取入队（可重试）；主动问候不抽
        affect_delta: Dict[str, Any] = {}
        if (not internal) and user_text and text and not text.startswith("{"):
            try:
                from ..companion.affect_rules import propose_after_reply

                ev_id = ""
                # 用本轮用户消息 id 幂等；internal 无则用 bot
                try:
                    last_user = db.fetchone(
                        """
                        SELECT id FROM messages
                        WHERE user_id=? AND conv_id=? AND mine=1
                        ORDER BY seq DESC LIMIT 1
                        """,
                        (user_id, conv_id),
                    )
                    ev_id = str((last_user["id"] if last_user else "") or bot_msg_id or "")
                except Exception:
                    ev_id = bot_msg_id or ""
                affect_delta = propose_after_reply(
                    user_id=user_id,
                    persona_id=persona_id,
                    user_text=user_text,
                    event_id=ev_id,
                )
                if affect_delta.get("affect"):
                    affect_state = affect_delta["affect"]
            except Exception:
                affect_delta = {}
            try:
                from ..companion.proactive import schedule_chat_followups, ProactivePlanner

                schedule_chat_followups(user_id, persona_id, user_text)
                ProactivePlanner().plan(user_id, persona_id, trigger="chat_reply")
            except Exception:
                pass
            try:
                from ..companion.runtime import CompanionRuntime

                skip_ex = bool(
                    isinstance(memory_receipt, dict)
                    and memory_receipt.get("handled")
                    and (memory_receipt.get("status") or "") in ("ok", "updated", "created", "remembered")
                )
                # handled 即跳过；兼容多种 status
                if isinstance(memory_receipt, dict) and memory_receipt.get("handled"):
                    skip_ex = True
                CompanionRuntime().enqueue_extract_after_reply(
                    user_id=user_id,
                    persona_id=persona_id,
                    user_text=user_text,
                    assistant_text=text,
                    skip_explicit_text=skip_ex,
                    provider=dsh_provider,
                    model=dsh_model,
                )
            except Exception:
                pass
        # 监控用回合过程（只读看板）；失败静默，不影响主路径
        if (not internal) and user_text and text and not text.startswith("{"):
            try:
                from ..companion.turn_trace import record_turn_trace

                mems_used = []
                if turn_snap:
                    for m in turn_snap.get("memories") or []:
                        mems_used.append(
                            {
                                "id": m.get("id") or "",
                                "text": (m.get("text") or "")[:200],
                                "heat": m.get("heat"),
                            }
                        )
                record_turn_trace(
                    user_id=user_id,
                    persona_id=persona_id,
                    conv_id=conv_id,
                    user_text=user_text,
                    assistant_text=text,
                    bot_message_id=bot_msg_id or "",
                    explicit=memory_receipt if isinstance(memory_receipt, dict) else {},
                    memories_used=mems_used,
                    world_presence=(turn_snap or {}).get("world_presence") or "",
                    affect_strategy=(turn_snap or {}).get("affect_strategy") or affect_hint or "",
                    affect=(affect_delta.get("affect") if affect_delta else None)
                    or (turn_snap or {}).get("affect")
                    or affect_state
                    or {},
                    snapshot_id=(turn_snap or {}).get("snapshot_id") or "",
                    state_version=int((turn_snap or {}).get("state_version") or 0),
                    meta={
                        "client": client,
                        "model": actual_model_label,
                        "affect_deltas": (affect_delta or {}).get("deltas") or {},
                        "affect_changed": bool((affect_delta or {}).get("changed")),
                    },
                )
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        detail = f"model error: {exc}"
        fail_model = usage_model_label(dsh_provider, dsh_model) if "dsh_provider" in locals() else str(
            (channel or {}).get("model") or "dsh"
        )
        if bot_msg_id is None:
            await _fail(detail=detail, channel_id=channel["id"], model=fail_model)
        else:
            logger.warning("chat_fail user=%s conv=%s detail=%s", user_id, conv_id, detail)
            final = _update_message_text(
                bot_msg_id,
                _system_warn_text(),
                state="sent",
                kind="system",
            )
            await _set_typing(user_id, conv_id, False, display)
            if final:
                await hub.send_user(user_id, "message", final.model_dump())
            _log_usage(
                user_id=user_id,
                persona_id=persona_id,
                channel_id=channel["id"],
                conv_id=conv_id,
                model=fail_model,
                client=client,
                status="error",
                error=detail,
            )


class ConversationPatch(BaseModel):
    draft: Optional[str] = Field(default=None, max_length=4000)
    bg_name: Optional[str] = Field(default=None, max_length=32)
    bg_file_id: Optional[str] = Field(default=None, max_length=64)
    pinned: Optional[bool] = None
    muted: Optional[bool] = None
    hidden: Optional[bool] = None


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
def patch_conversation(
    conv_id: str,
    body: ConversationPatch,
    user: Dict[str, Any] = Depends(current_user),
) -> ConversationOut:
    row = _owned_conv(user["id"], conv_id)
    keys = row.keys()
    draft = row["draft"] or ""
    bg_name = (row["bg_name"] or "默认") if "bg_name" in keys else "默认"
    bg_file_id = (row["bg_file_id"] or "") if "bg_file_id" in keys else ""
    pinned = int(row["pinned"])
    muted = int(row["muted"])
    hidden = int(row["hidden"] or 0) if "hidden" in keys else 0
    if body.draft is not None:
        draft = body.draft
    if body.bg_name is not None:
        bg_name = (body.bg_name or "默认").strip() or "默认"
    if body.bg_file_id is not None:
        fid = (body.bg_file_id or "").strip()
        if fid:
            media = db.fetchone(
                "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
                (fid, user["id"]),
            )
            if media is None:
                raise HTTPException(status_code=400, detail="背景图不存在")
            bg_file_id = fid
            if bg_name == "默认":
                bg_name = "相册图片"
        else:
            bg_file_id = ""
    if body.pinned is not None:
        pinned = 1 if body.pinned else 0
    if body.muted is not None:
        muted = 1 if body.muted else 0
    if body.hidden is not None and "hidden" in keys:
        hidden = 1 if body.hidden else 0
    db.execute(
        """
        UPDATE conversations
        SET draft = ?, bg_name = ?, bg_file_id = ?, pinned = ?, muted = ?, hidden = ?
        WHERE id = ? AND user_id = ?
        """,
        (draft, bg_name, bg_file_id, pinned, muted, hidden, conv_id, user["id"]),
    )
    row = db.fetchone(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user["id"]),
    )
    assert row is not None
    return _conv_out(row)


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    """微信式删除聊天：清消息 + 列表消失；人设保留。软删避免 sync/缓存把会话加回来。"""
    _owned_conv(user["id"], conv_id)
    now = iso()
    db.execute("DELETE FROM messages WHERE user_id = ? AND conv_id = ?", (user["id"], conv_id))
    keys = db.fetchone("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    if keys is not None and "deleted_at" in keys.keys():
        # 更新 last_time，让 sync/delta 能把 hidden=1 推到客户端，避免「删了又回来」
        db.execute(
            """
            UPDATE conversations
            SET deleted_at = ?, hidden = 1, preview = '', unread = 0, draft = '',
                last_seq = 0, last_time = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, now, conv_id, user["id"]),
        )
    else:
        db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user["id"]),
        )
    return {"ok": True}


@router.post("/conversations/{conv_id}/clean-residue")
def clean_residue(conv_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    """把像程序壳 / think / 伪造协议的助手消息标成 context_skip，不再进模型上下文。"""
    _owned_conv(user["id"], conv_id)
    rows = db.fetchall(
        """
        SELECT id, text, mine, kind FROM messages
        WHERE user_id = ? AND conv_id = ?
        ORDER BY seq DESC LIMIT 200
        """,
        (user["id"], conv_id),
    )
    marked = 0
    for r in rows:
        if r["mine"]:
            continue
        if (r["kind"] or "text") != "text":
            continue
        try:
            if "context_skip" in r.keys() and r["context_skip"]:
                continue
        except (KeyError, IndexError, TypeError):
            pass
        if looks_like_format_residue(r["text"] or ""):
            db.execute("UPDATE messages SET context_skip = 1 WHERE id = ?", (r["id"],))
            marked += 1
    return {"ok": True, "marked": marked}


@router.post("/conversations/{conv_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conv_id: str,
    body: SendMessageRequest,
    background: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> SendMessageResponse:
    conv = _owned_conv(user["id"], conv_id)
    kind = body.kind if body.kind in (
        "text",
        "image",
        "voice",
        "file",
        "video",
        "system",
        "link",
        "location",
        "forward",
    ) else "text"
    text = body.text.strip()
    media_ids = [m for m in (body.media_ids or []) if m]
    # 校验媒体：本账号，或全局表情组可读
    for mid in media_ids:
        if not stk.media_readable_by_user(file_id=mid, user_id=user["id"]):
            raise HTTPException(status_code=400, detail=f"媒体不存在: {mid}")

    voice_asr = ""
    # 纯 URL 文本 → 链接分享卡（OG）；与更多菜单「解析/下载」任务卡区分
    if kind == "text" and text and _URL_ONLY_RE.match(text):
        from ..og_fetch import fetch_og

        og = await asyncio.to_thread(fetch_og, text)
        kind = "link"
        text = json.dumps(
            {
                "v": 1,
                "card": "og",
                "url": og.get("url") or text,
                "title": og.get("title") or text,
                "description": og.get("description") or "",
                "image": og.get("image") or "",
            },
            ensure_ascii=False,
        )
    elif kind == "link" and text and not text.startswith("{"):
        from ..og_fetch import fetch_og

        url = text
        og = await asyncio.to_thread(fetch_og, url)
        text = json.dumps(
            {
                "v": 1,
                "card": "og",
                "url": og.get("url") or url,
                "title": og.get("title") or url,
                "description": og.get("description") or "",
                "image": og.get("image") or "",
            },
            ensure_ascii=False,
        )

    if kind in ("image", "video", "file", "voice") and media_ids:
        payload: Dict[str, Any] = {
            "v": 1,
            "files": media_ids,
            "caption": body.text.strip(),
            "seconds": body.seconds,
        }
        if kind == "voice":
            try:
                from pathlib import Path

                from ..asr_gen import transcribe_audio_path

                row = db.fetchone(
                    "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
                    (media_ids[0], user["id"]),
                )
                if row and (row["path"] or ""):
                    voice_asr = (
                        await asyncio.to_thread(transcribe_audio_path, Path(row["path"])) or ""
                    ).strip()
                    if voice_asr:
                        payload["asr"] = voice_asr[:2000]
            except Exception:
                voice_asr = ""
        text = json.dumps(payload, ensure_ascii=False)
    elif not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 喂给模型的文案：媒体/表情/注入一律入站整形，不把 JSON 塞进上下文、不改气泡
    ocr_meta_lines: list = []
    if kind == "image" and media_ids:
        n = len(media_ids)
        cap = body.text.strip()
        descs: list[str] = []
        try:
            from pathlib import Path

            from ..vision_gen import ocr_image_path

            for mid in media_ids[:4]:
                row = db.fetchone(
                    "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
                    (mid, user["id"]),
                )
                if row and (row["path"] or ""):
                    d, lines = await asyncio.to_thread(
                        ocr_image_path, Path(row["path"]), hint=cap
                    )
                    if d:
                        descs.append(d)
                    if lines:
                        ocr_meta_lines.append(
                            {
                                "file_id": mid,
                                "lines": [
                                    {
                                        "text": x.get("text"),
                                        "score": x.get("score"),
                                        "box": x.get("box"),
                                    }
                                    for x in lines[:40]
                                ],
                            }
                        )
        except Exception:
            descs = []
            ocr_meta_lines = []
        if descs:
            joined = "；".join(descs)
            user_text_for_model = f"[对方发来{n}张图片] {joined}" + (
                f"（附言：{cap}）" if cap and cap not in joined else ""
            )
        else:
            joined = ""
            user_text_for_model = f"[对方发来{n}张图片]" + (f"：{cap}" if cap else "")
        if ocr_meta_lines:
            # 坐标写入 instructions，不污染气泡正文
            box_hint = []
            for block in ocr_meta_lines:
                for ln in block.get("lines") or []:
                    t = (ln.get("text") or "").strip()
                    if not t:
                        continue
                    box_hint.append(f"{t}@{ln.get('box')}")
            if box_hint:
                user_text_for_model += (
                    "\n[OCR坐标] " + "；".join(box_hint[:24])
                )
        # 把 OCR 摘要写回消息 JSON，供连发合并 / 历史回放；客户端忽略未知字段
        if joined:
            try:
                payload_obj = json.loads(text) if text.startswith("{") else {
                    "v": 1,
                    "files": media_ids,
                    "caption": cap,
                    "seconds": body.seconds,
                }
                if isinstance(payload_obj, dict):
                    payload_obj["ocr"] = joined[:4000]
                    text = json.dumps(payload_obj, ensure_ascii=False)
            except Exception:
                pass
    elif kind == "video" and media_ids:
        cap = body.text.strip()
        desc = ""
        try:
            from pathlib import Path

            from ..vision_gen import describe_video_path

            row = db.fetchone(
                "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
                (media_ids[0], user["id"]),
            )
            if row and (row["path"] or ""):
                desc = (
                    await asyncio.to_thread(
                        describe_video_path, Path(row["path"]), hint=cap
                    )
                    or ""
                )
        except Exception:
            desc = ""
        user_text_for_model = (
            f"[对方发来一段视频] {desc}".strip()
            + (f"（附言：{cap}）" if cap and cap not in (desc or "") else "")
        )
    elif kind == "file" and media_ids:
        cap = body.text.strip()
        file_summary = ""
        try:
            from pathlib import Path

            from ..doc_parse import parse_inbound_file

            row = db.fetchone(
                "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
                (media_ids[0], user["id"]),
            )
            if row and (row["path"] or ""):
                fname = Path(row["path"]).name
                parsed = await asyncio.to_thread(
                    parse_inbound_file, Path(row["path"]), hint=cap, filename=fname
                )
                file_summary = (parsed.get("summary") or "").strip()
                # 坐标/行级信息塞进 instructions 风格后缀，便于模型定位表格
                lines = parsed.get("lines") or []
                if lines and parsed.get("source") == "unlimited":
                    box_hint = []
                    for ln in lines[:30]:
                        t = (ln.get("text") or "").strip()
                        if t:
                            box_hint.append(f"{t[:80]}@{ln.get('box')}")
                    if box_hint:
                        file_summary = (file_summary + "\n[文档版式] " + "；".join(box_hint[:20])).strip()
        except Exception:
            file_summary = ""
        if file_summary:
            user_text_for_model = f"[对方发来一个文件] {file_summary}"
        else:
            user_text_for_model = "[对方发来一个文件]" + (f"：{cap}" if cap else "")
    elif kind == "voice":
        user_text_for_model = (
            f"[对方语音] {voice_asr}" if voice_asr else "[对方发来一段语音]"
        )
    elif kind == "link":
        try:
            card = json.loads(text) if text.startswith("{") else {}
        except Exception:
            card = {}
        title = (card.get("title") or body.text or "").strip()
        url = (card.get("url") or body.text or "").strip()
        user_text_for_model = shape_inbound_user_text(
            f"[对方分享了链接] {title} {url}".strip(), kind="link"
        )
    elif kind == "location":
        try:
            loc = json.loads(body.text) if (body.text or "").strip().startswith("{") else {}
        except Exception:
            loc = {}
        name = (loc.get("name") or loc.get("address") or "位置").strip()
        lat = loc.get("lat")
        lng = loc.get("lng")
        text = json.dumps(
            {
                "v": 1,
                "card": "location",
                "name": name,
                "address": loc.get("address") or "",
                "lat": lat,
                "lng": lng,
            },
            ensure_ascii=False,
        )
        user_text_for_model = shape_inbound_user_text(
            f"[对方分享了位置] {name} ({lat},{lng})", kind="link"
        )
    elif kind == "forward":
        try:
            fwd = json.loads(body.text) if (body.text or "").strip().startswith("{") else {}
        except Exception:
            fwd = {}
        items = fwd.get("items") or fwd.get("messages") or []
        text = json.dumps(
            {
                "v": 1,
                "card": "forward",
                "title": fwd.get("title") or "聊天记录",
                "items": items[:50],
                "count": len(items),
            },
            ensure_ascii=False,
        )
        user_text_for_model = shape_inbound_user_text(
            f"[对方转发了{len(items)}条聊天记录]", kind="link"
        )
    else:
        user_text_for_model = shape_inbound_user_text(body.text.strip(), kind="text")

    mine = _insert_message(
        user_id=user["id"],
        conv_id=conv_id,
        client_id=body.client_id,
        sender_id=user["id"],
        mine=True,
        kind=kind,
        text=text,
        quote_id=body.quote_id,
        seconds=body.seconds,
        bytes_=body.bytes,
    )
    await hub.send_user(user["id"], "message", mine.model_dump())

    # 用户聊天图不自动进全局表情库；需 DSH/管理台显式 collect

    if conv["persona_id"]:
        memory_receipt: Optional[Dict[str, Any]] = None
        try:
            from ..companion.runtime import CompanionRuntime

            rt = await CompanionRuntime().on_user_message(
                user_id=user["id"],
                persona_id=conv["persona_id"],
                conv_id=conv_id,
                text=user_text_for_model,
                message_id=mine.id,
                kind="chat",
            )
            memory_receipt = rt.get("explicit")
        except Exception:
            memory_receipt = None
        try:
            from ..schedules import reschedule_on_user_message

            reschedule_on_user_message(user["id"], conv["persona_id"])
        except Exception:
            pass
        # 用户提起已完结事项 → 取消对应跟进意图
        try:
            from ..companion.proactive import cancel_matching_followups

            cancel_matching_followups(user["id"], conv["persona_id"], user_text_for_model)
        except Exception:
            pass
        # 用户插话：打断该会话上卡住的 DSH（chat/agent 均可）
        try:
            await dsh_interrupt(user_id=user["id"], conversation_id=conv_id)
        except Exception:
            pass
        # 关机/停止语义：顺带 force stop 虚拟设备，避免报错纠缠
        low = (user_text_for_model or "").lower()
        if any(k in low for k in ("关机", "别弄了", "停一下", "停止操作", "取消操作", "别操作")):
            try:
                from .. import virtual_devices as vdev

                for d in vdev.list_devices(user["id"]):
                    if (d.get("status") or "") in ("running", "starting", "busy"):
                        try:
                            # stop_device 内会发「你关闭了电脑/手机」系统消息
                            vdev.stop_device(d["id"], user["id"], force=True)
                        except Exception:
                            pass
            except Exception:
                pass
        gen = _bump_generation(conv_id)
        background.add_task(
            _generate_reply,
            user_id=user["id"],
            conv_id=conv_id,
            persona_id=conv["persona_id"],
            user_text=user_text_for_model,
            client="app",
            memory_receipt=memory_receipt,
            generation=gen,
        )

    return SendMessageResponse(message=mine, replies=[])


@router.post("/conversations/{conv_id}/read")
def mark_read(conv_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    _owned_conv(user["id"], conv_id)
    db.execute(
        "UPDATE conversations SET unread = 0 WHERE id = ? AND user_id = ?",
        (conv_id, user["id"]),
    )
    return {"ok": True}


@router.get("/messages/since", response_model=List[MessageOut])
def messages_since(
    after: str = Query(default="", description="ISO 时间，只拉此之后的消息"),
    user: Dict[str, Any] = Depends(current_user),
) -> List[MessageOut]:
    if after:
        rows = db.fetchall(
            """
            SELECT * FROM messages
            WHERE user_id = ? AND (created_at > ? OR IFNULL(updated_at, created_at) > ?)
            ORDER BY IFNULL(updated_at, created_at) ASC LIMIT 500
            """,
            (user["id"], after, after),
        )
    else:
        rows = []
    return [_msg_out(r) for r in rows]


@router.get("/sync/delta")
def sync_delta(
    after: str = Query(..., description="上次同步游标 ISO 时间"),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """增量同步：拉 after 之后变更的会话与消息，代替整包 sync。"""
    uid = user["id"]
    now = iso()
    # 含已软删会话（hidden=1），客户端靠 hidden/deletedConvIds 从列表剔除
    convs = [
        _conv_out(r)
        for r in db.fetchall(
            """
            SELECT * FROM conversations
            WHERE user_id = ? AND last_time > ?
            ORDER BY last_time DESC
            LIMIT 200
            """,
            (uid, after),
        )
    ]
    msgs = [
        _msg_out(r)
        for r in db.fetchall(
            """
            SELECT * FROM messages
            WHERE user_id = ? AND (created_at > ? OR IFNULL(updated_at, created_at) > ?)
            ORDER BY IFNULL(updated_at, created_at) ASC
            LIMIT 500
            """,
            (uid, after, after),
        )
    ]
    return {
        "cursor": now,
        "conversations": [c.model_dump() for c in convs],
        "messages": [m.model_dump() for m in msgs],
    }
