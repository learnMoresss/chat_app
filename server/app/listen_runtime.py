"""一起听运行时：会话上下文（给聊天）+ 听歌评价气泡（节流、轻量补全）。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

from . import db
from .security import iso, new_id

logger = logging.getLogger("chat.listen")

# 同会话评价最短间隔（秒）；切歌可再触发
_REACT_COOLDOWN_SEC = 90
# 同曲播放中后台 tick 间隔
_TICK_INTERVAL_SEC = 80.0
_tick_tasks: Dict[str, asyncio.Task] = {}
_tick_stop: Set[str] = set()


def active_session(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        """
        SELECT * FROM listen_sessions
        WHERE user_id = ? AND conv_id = ? AND status IN ('active','paused')
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    if row is None:
        return None
    d = dict(row)
    # 若有更新的「已结束」场，说明用户已明确退出，忽略更早残留的 active 僵尸场
    ended = db.fetchone(
        """
        SELECT updated_at FROM listen_sessions
        WHERE user_id = ? AND conv_id = ? AND status = 'ended'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    if ended is not None:
        try:
            if str(ended["updated_at"] or "") >= str(d.get("updated_at") or ""):
                return None
        except Exception:
            pass
    return d


def _track_at(playlist_id: str, user_id: str, index: int) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        """
        SELECT t.id, t.title, t.artist
        FROM music_playlist_items i
        JOIN music_tracks t ON t.id = i.track_id
        WHERE i.playlist_id = ? AND t.user_id = ? AND i.position = ?
        LIMIT 1
        """,
        (playlist_id, user_id, max(0, int(index))),
    )
    return dict(row) if row is not None else None


def _recent_ended(user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """最近刚结束的一起听（用于明确告知模型对方已退出）。"""
    row = db.fetchone(
        """
        SELECT * FROM listen_sessions
        WHERE user_id = ? AND conv_id = ? AND status = 'ended'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    if row is None:
        return None
    d = dict(row)
    updated = (d.get("updated_at") or "").strip()
    if not updated:
        return d
    try:
        from datetime import datetime, timezone

        t0 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - t0).total_seconds()
        if age > 3600:
            return None
    except Exception:
        pass
    return d


def listen_context_hint(user_id: str, conv_id: str) -> str:
    """注入聊天 instructions：收起全屏/小窗仍算在听；真正 ended 则明确已退出。"""
    s = active_session(user_id, conv_id)
    if not s:
        ended = _recent_ended(user_id, conv_id)
        if ended:
            return (
                "【重要】对方已经结束一起听，音乐已停。"
                "禁止再说「还在听」「这首歌」「共听」；当作普通聊天，不要提曲目。"
            )
        return ""
    idx = int(s.get("track_index") or 0)
    track = _track_at(str(s.get("playlist_id") or ""), user_id, idx)
    title = (track or {}).get("title") or "歌"
    artist = (track or {}).get("artist") or ""
    mood = (s.get("mood") or "").strip()
    status = s.get("status") or "active"
    who = "正在一起听" if status == "active" else "一起听已暂停"
    line = f"《{title}》" + (f"（{artist}）" if artist else "")
    mood_bit = f"，主题「{mood}」" if mood else ""
    return (
        f"（你们{who}：当前曲目 {line}{mood_bit}。"
        "对方可能只是收起了听歌页，音乐仍在播；聊天时要知道你们在共听，可偶尔接一句听感，"
        "不要装作没在听，也不要每句都提歌。"
        "只有对方明确退出/关闭一起听后才算结束。）"
    )


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(row.get("meta_json") or "{}")
    except Exception:
        return {}


def _should_react(row: Dict[str, Any], *, track_changed: bool, force: bool) -> bool:
    if force:
        return True
    meta = _meta(row)
    last_at = (meta.get("last_react_at") or "").strip()
    last_idx = meta.get("last_react_track")
    idx = int(row.get("track_index") or 0)
    if track_changed and last_idx is not None and int(last_idx) == idx:
        # 同曲重复 patch 不刷
        pass
    if track_changed and (last_idx is None or int(last_idx) != idx):
        return True
    if not last_at:
        return True
    try:
        from datetime import datetime, timezone

        t0 = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - t0).total_seconds()
        return age >= _REACT_COOLDOWN_SEC
    except Exception:
        return True


def _mark_reacted(session_id: str, row: Dict[str, Any]) -> None:
    meta = _meta(row)
    meta["last_react_at"] = iso()
    meta["last_react_track"] = int(row.get("track_index") or 0)
    db.execute(
        "UPDATE listen_sessions SET meta_json=?, updated_at=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), iso(), session_id),
    )


async def maybe_listen_react(
    *,
    user_id: str,
    session_id: str,
    reason: str = "tick",
    track_changed: bool = False,
    force: bool = False,
) -> None:
    """听歌中触发一句短评价气泡。

    故意走独立 conversationId + 短补全，不复用主会话 DSH session，
    避免把 5万+ 缓存前缀算进「听歌气泡」用量。
    """
    row = db.fetchone(
        "SELECT * FROM listen_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    if row is None:
        return
    d = dict(row)
    if (d.get("status") or "") not in ("active", "paused"):
        return
    if not _should_react(d, track_changed=track_changed, force=force):
        return
    conv_id = (d.get("conv_id") or "").strip()
    persona_id = (d.get("persona_id") or "").strip()
    if not conv_id or not persona_id:
        return
    persona = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if persona is None:
        return

    idx = int(d.get("track_index") or 0)
    track = _track_at(str(d.get("playlist_id") or ""), user_id, idx)
    title = (track or {}).get("title") or "这首"
    artist = (track or {}).get("artist") or ""
    mood = (d.get("mood") or "").strip()
    cue = f"《{title}》" + (f" · {artist}" if artist else "")
    prompt = (
        f"你们正在一起听{cue}"
        + (f"，主题「{mood}」" if mood else "")
        + f"。触发：{reason}。"
        "用 1 句很短的口语聊听感或吐槽或接话，像微信气泡；不要提系统、歌单、邀请。"
    )

    try:
        from .dsh_gateway import dsh_complete, require_dsh
        from .llm import build_messages
        from .routers.chat import _insert_message, _log_usage
        from .ws_hub import hub

        require_dsh()
        bio = (persona["bio"] or "")[:600]
        messages = build_messages(
            persona_bio=bio,
            persona_name=persona["name"] or "对方",
            history=[],
            include_memory_in_system=False,
            enabled_caps=set(),
            has_fixed_voice=False,
        )
        system = ""
        if messages and messages[0].get("role") == "system":
            system = messages[0].get("content") or ""

        # 独立会话键：不 resume 主聊天的巨型 prompt cache
        light_conv = f"sys-listen-{persona_id}"
        text = await dsh_complete(
            user_id=user_id,
            conversation_id=light_conv,
            text=prompt,
            system=system,
            instructions="只回一句很短口语，不要解释、不要括号系统说明、不要表情包标记。",
            history=[],
            persona_id=persona_id,
        )
        text = (text or "").strip()
        if not text:
            return
        # 去掉可能的系统括号残留
        if text.startswith("【") and "】" in text[:40]:
            text = text.split("】", 1)[-1].strip()
        if len(text) > 72:
            text = text[:72].rstrip() + "…"

        # 成功且有文案后再占冷却，避免失败也锁 90s
        _mark_reacted(session_id, d)

        bot = _insert_message(
            user_id=user_id,
            conv_id=conv_id,
            client_id=new_id("listencli_"),
            sender_id=persona_id,
            mine=False,
            kind="text",
            text=text,
            state="sent",
        )
        try:
            db.execute(
                "UPDATE messages SET context_skip=1 WHERE id=? AND user_id=?",
                (bot.id, user_id),
            )
        except Exception:
            pass
        await hub.send_user(user_id, "message", bot.model_dump())
        _log_usage(
            user_id=user_id,
            persona_id=persona_id,
            channel_id="",
            conv_id=conv_id,
            model="dsh/deepseek-flash",
            client="listen",
            status="ok",
            metrics={
                "promptChars": len(prompt),
                "historyMessages": 0,
                "source": "listen_light",
                "dshSessionId": light_conv,
            },
        )
    except Exception:
        logger.exception("listen_react failed session=%s", session_id)


def schedule_listen_react(
    *,
    user_id: str,
    session_id: str,
    reason: str = "tick",
    track_changed: bool = False,
    force: bool = False,
    delay: float = 1.2,
) -> None:
    async def _run() -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await maybe_listen_react(
            user_id=user_id,
            session_id=session_id,
            reason=reason,
            track_changed=track_changed,
            force=force,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass


def stop_listen_react_loop(session_id: str) -> None:
    """ended 时取消后台 tick。"""
    sid = (session_id or "").strip()
    if not sid:
        return
    _tick_stop.add(sid)
    t = _tick_tasks.pop(sid, None)
    if t is not None and not t.done():
        t.cancel()


def start_listen_react_loop(*, user_id: str, session_id: str) -> None:
    """accept 后周期性同曲评价（受 cooldown；切歌另走 schedule）。"""
    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    if not sid or not uid:
        return
    _tick_stop.discard(sid)
    old = _tick_tasks.pop(sid, None)
    if old is not None and not old.done():
        old.cancel()

    async def _loop() -> None:
        # 首句由 accept 的 force 触发；稍后再进周期
        await asyncio.sleep(_TICK_INTERVAL_SEC)
        while sid not in _tick_stop:
            row = db.fetchone(
                "SELECT status FROM listen_sessions WHERE id = ? AND user_id = ?",
                (sid, uid),
            )
            if row is None or (row["status"] or "") not in ("active", "paused"):
                break
            await maybe_listen_react(
                user_id=uid,
                session_id=sid,
                reason="听着听着",
                track_changed=False,
                force=False,
            )
            await asyncio.sleep(_TICK_INTERVAL_SEC)
        _tick_tasks.pop(sid, None)
        _tick_stop.discard(sid)

    try:
        loop = asyncio.get_running_loop()
        _tick_tasks[sid] = loop.create_task(_loop())
    except RuntimeError:
        pass


def schedule_listen_reply_to_user(*, user_id: str, session_id: str) -> None:
    """用户在听歌页发言后接一句。"""
    schedule_listen_react(
        user_id=user_id,
        session_id=session_id,
        reason="对方刚说了一句",
        force=True,
        delay=1.0,
    )
