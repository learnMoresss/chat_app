"""一起听歌：邀请 / 接受 / 会话状态 / 切歌。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from .. import music_curate
from ..security import current_user, iso, new_id
from ..ws_hub import hub

router = APIRouter(prefix="/api/v1/listen", tags=["listen"])


class InviteBody(BaseModel):
    conv_id: str
    mood: str = ""
    reason: str = ""
    context: str = ""
    limit: int = 12
    # 非空则按用户勾选建歌单，跳过 mood 随机抽歌
    track_ids: List[str] = []


class AcceptBody(BaseModel):
    pass


class ProgressBody(BaseModel):
    track_index: Optional[int] = None
    position_ms: Optional[int] = None
    status: Optional[str] = None  # active | ended | paused


def _session_out(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
    except Exception:
        d["meta"] = {}
        d.pop("meta_json", None)
    return d


def _tracks_for_playlist(playlist_id: str, user_id: str) -> List[Dict[str, Any]]:
    try:
        rows = db.fetchall(
            """
            SELECT t.id, t.title, t.artist, t.duration_sec, t.source, t.path,
                   COALESCE(t.lyrics, '') AS lyrics, i.position
            FROM music_playlist_items i
            JOIN music_tracks t ON t.id = i.track_id
            WHERE i.playlist_id = ? AND t.user_id = ?
            ORDER BY i.position ASC
            """,
            (playlist_id, user_id),
        )
    except Exception:
        rows = db.fetchall(
            """
            SELECT t.id, t.title, t.artist, t.duration_sec, t.source, t.path, i.position
            FROM music_playlist_items i
            JOIN music_tracks t ON t.id = i.track_id
            WHERE i.playlist_id = ? AND t.user_id = ?
            ORDER BY i.position ASC
            """,
            (playlist_id, user_id),
        )
    out = []
    for r in rows:
        d = dict(r)
        d.setdefault("lyrics", "")
        out.append(d)
    return out


def _owned_session(user_id: str, session_id: str):
    row = db.fetchone(
        "SELECT * FROM listen_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    if row is None:
        raise HTTPException(404, "一起听会话不存在")
    return row


def _refill_session_tracks(row, user_id: str) -> List[Dict[str, Any]]:
    """歌单空时（曲库扫描前留下的旧会话）按心情重抽并挂回同一 playlist_id。"""
    pid = (row["playlist_id"] or "").strip()
    tracks = _tracks_for_playlist(pid, user_id) if pid else []
    if tracks:
        return tracks
    mood = (row["mood"] or "").strip() or "随心情"
    curated = music_curate.curate_playlist(
        user_id=user_id,
        name=f"一起听 · {mood}",
        affect={},
        hint=mood,
        context_text=row["reason"] or mood,
        limit=12,
    )
    new_tracks = curated.get("tracks") or []
    if not new_tracks:
        return []
    # 把新抽的曲目写入原 playlist（若无则用新 playlist）
    now = iso()
    if not pid:
        pid = curated["playlist_id"]
        db.execute(
            "UPDATE listen_sessions SET playlist_id=?, updated_at=? WHERE id=?",
            (pid, now, row["id"]),
        )
    else:
        # 丢弃空壳，改用新 playlist，避免重复空 items
        new_pid = curated["playlist_id"]
        db.execute(
            "UPDATE listen_sessions SET playlist_id=?, updated_at=? WHERE id=?",
            (new_pid, now, row["id"]),
        )
        pid = new_pid
    return _tracks_for_playlist(pid, user_id)


async def close_listen_invite_cards(
    *,
    user_id: str,
    conv_id: str = "",
    session_id: str = "",
) -> int:
    """把聊天里的一起听卡片标成 ended，推 WS 更新，避免反复点「进入」重开旧场。"""
    from ..routers.chat import _msg_out

    cid = (conv_id or "").strip()
    sid = (session_id or "").strip()
    if not cid and not sid:
        return 0
    if cid:
        rows = db.fetchall(
            """
            SELECT * FROM messages
            WHERE user_id = ? AND conv_id = ? AND kind = 'link'
            ORDER BY seq DESC LIMIT 80
            """,
            (user_id, cid),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM messages
            WHERE user_id = ? AND kind = 'link'
              AND text LIKE ?
            ORDER BY seq DESC LIMIT 40
            """,
            (user_id, f"%{sid}%"),
        )
    n = 0
    now = iso()
    for r in rows:
        raw = (r["text"] or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            meta = json.loads(raw)
        except Exception:
            continue
        if not isinstance(meta, dict) or meta.get("card") != "listen_invite":
            continue
        msid = str(meta.get("session_id") or "").strip()
        if sid and msid != sid:
            continue
        if (meta.get("status") or "").strip() == "ended":
            continue
        meta["status"] = "ended"
        body = str(meta.get("body") or "一起听")
        if "已结束" not in body:
            meta["body"] = f"{body} · 已结束"
        text = json.dumps(meta, ensure_ascii=False)
        db.execute(
            "UPDATE messages SET text = ?, updated_at = ? WHERE id = ?",
            (text, now, r["id"]),
        )
        row2 = db.fetchone("SELECT * FROM messages WHERE id = ?", (r["id"],))
        if row2 is not None:
            try:
                await hub.send_user(user_id, "message", _msg_out(row2).model_dump())
            except Exception:
                pass
        n += 1
    return n


async def end_open_sessions_for_conv(*, user_id: str, conv_id: str) -> None:
    """同会话开新邀请前，结束仍存活的旧场。"""
    rows = db.fetchall(
        """
        SELECT id FROM listen_sessions
        WHERE user_id = ? AND conv_id = ?
          AND status IN ('invited', 'active', 'paused')
        """,
        (user_id, conv_id),
    )
    now = iso()
    for r in rows:
        db.execute(
            "UPDATE listen_sessions SET status='ended', updated_at=? WHERE id=?",
            (now, r["id"]),
        )
        try:
            from ..listen_runtime import stop_listen_react_loop

            stop_listen_react_loop(r["id"])
        except Exception:
            pass
    try:
        db.execute(
            "UPDATE conversations SET listen_until = '' WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
    except Exception:
        pass
    await close_listen_invite_cards(user_id=user_id, conv_id=conv_id)


async def end_open_sessions_for_user(*, user_id: str) -> None:
    """账号级：同时只允许一场一起听，创建前清掉全部开放场。"""
    rows = db.fetchall(
        """
        SELECT id, conv_id FROM listen_sessions
        WHERE user_id = ?
          AND status IN ('invited', 'active', 'paused')
        """,
        (user_id,),
    )
    now = iso()
    conv_ids: List[str] = []
    for r in rows:
        db.execute(
            "UPDATE listen_sessions SET status='ended', updated_at=? WHERE id=?",
            (now, r["id"]),
        )
        try:
            from ..listen_runtime import stop_listen_react_loop

            stop_listen_react_loop(r["id"])
        except Exception:
            pass
        cid = (r["conv_id"] or "").strip()
        if cid and cid not in conv_ids:
            conv_ids.append(cid)
    for cid in conv_ids:
        try:
            db.execute(
                "UPDATE conversations SET listen_until = '' WHERE id = ? AND user_id = ?",
                (cid, user_id),
            )
        except Exception:
            pass
        try:
            await close_listen_invite_cards(user_id=user_id, conv_id=cid)
        except Exception:
            pass


def _playlist_from_track_ids(*, user_id: str, track_ids: List[str], name: str) -> Dict[str, Any]:
    """按用户勾选顺序建 session 歌单；非法/非本人曲目跳过。"""
    seen = set()
    ordered: List[str] = []
    for raw in track_ids:
        tid = (raw or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    if not ordered:
        raise HTTPException(400, "请至少选择一首歌")
    if len(ordered) > 50:
        raise HTTPException(400, "一次最多选 50 首")
    tracks: List[Dict[str, Any]] = []
    for tid in ordered:
        row = None
        try:
            row = db.fetchone(
                "SELECT id, title, artist, duration_sec, source, path, COALESCE(lyrics, '') AS lyrics FROM music_tracks WHERE id = ? AND user_id = ?",
                (tid, user_id),
            )
        except Exception:
            row = db.fetchone(
                "SELECT id, title, artist, duration_sec, source, path FROM music_tracks WHERE id = ? AND user_id = ?",
                (tid, user_id),
            )
        if row is None:
            continue
        d = dict(row)
        d.setdefault("lyrics", "")
        tracks.append(d)
    if not tracks:
        raise HTTPException(400, "所选曲目无效或不属于当前账号")
    now = iso()
    pid = new_id("mpl_")
    db.execute(
        """
        INSERT INTO music_playlists(id, user_id, name, kind, source_url, meta_json, created_at, updated_at)
        VALUES (?,?,?,'session','','{}',?,?)
        """,
        (pid, user_id, name.strip() or "一起听 · 自选", now, now),
    )
    for i, tr in enumerate(tracks):
        db.execute(
            """
            INSERT INTO music_playlist_items(id, playlist_id, track_id, position, created_at)
            VALUES (?,?,?,?,?)
            """,
            (new_id("mpli_"), pid, tr["id"], i, now),
        )
    return {
        "playlist_id": pid,
        "name": name.strip() or "一起听 · 自选",
        "tracks": tracks,
        "terms": [],
    }


async def create_listen_invite(uid: str, body: InviteBody, *, push_ws: bool = True) -> Dict[str, Any]:
    """核心邀请逻辑（HTTP 与聊天 ⟦一起听⟧ 共用）。"""
    conv = db.fetchone(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
        (body.conv_id, uid),
    )
    if conv is None:
        raise HTTPException(404, "会话不存在")
    # 账号级清场：同时只允许一场
    try:
        await end_open_sessions_for_user(user_id=uid)
    except Exception:
        try:
            await end_open_sessions_for_conv(user_id=uid, conv_id=body.conv_id)
        except Exception:
            pass
    persona_id = (conv["persona_id"] or "").strip()
    track_ids = [str(x).strip() for x in (body.track_ids or []) if str(x).strip()]
    if track_ids:
        mood = (body.mood or "").strip() or "自选歌单"
        curated = _playlist_from_track_ids(
            user_id=uid,
            track_ids=track_ids,
            name=f"一起听 · {mood}",
        )
        reason = (body.reason or "").strip() or f"已选 {len(curated['tracks'])} 首"
        meta_extra: Dict[str, Any] = {"source": "manual_pick"}
    else:
        affect = {}
        try:
            from ..companion.affect import AffectService

            if persona_id:
                affect = AffectService().get_or_init(uid, persona_id)
        except Exception:
            affect = {}
        mood = (body.mood or "").strip() or "随心情"
        curated = music_curate.curate_playlist(
            user_id=uid,
            name=f"一起听 · {mood}",
            affect=affect if isinstance(affect, dict) else {},
            hint=mood,
            context_text=body.context or body.reason,
            limit=max(6, min(20, int(body.limit or 12))),
        )
        if not (curated.get("tracks") or []):
            curated = music_curate.curate_playlist(
                user_id=uid,
                name=f"一起听 · {mood}",
                affect={},
                hint="",
                context_text="",
                limit=max(6, min(20, int(body.limit or 12))),
            )
        if not (curated.get("tracks") or []):
            raise HTTPException(503, "曲库为空或抽歌失败，请先在运维扫描曲库")
        reason = (body.reason or "").strip() or f"按「{mood}」从曲库抽了 {len(curated['tracks'])} 首"
        meta_extra = {"terms": curated.get("terms") or []}
    now = iso()
    sid = new_id("ls_")
    db.execute(
        """
        INSERT INTO listen_sessions(
          id, user_id, conv_id, persona_id, playlist_id, status, mood, reason,
          track_index, position_ms, invited_by, meta_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,'invited',?,?,0,0,'ai',?,?,?)
        """,
        (
            sid,
            uid,
            body.conv_id,
            persona_id,
            curated["playlist_id"],
            mood,
            reason,
            json.dumps(meta_extra, ensure_ascii=False),
            now,
            now,
        ),
    )
    row = _owned_session(uid, sid)
    payload = {
        "session": _session_out(row),
        "tracks": curated["tracks"],
        "playlist_id": curated["playlist_id"],
    }
    if push_ws:
        await hub.send_user(
            uid,
            "listen_invite",
            {
                "session_id": sid,
                "conv_id": body.conv_id,
                "persona_id": persona_id,
                "mood": mood,
                "reason": reason,
                "track_count": len(curated["tracks"]),
                "preview": curated["tracks"][0]["title"] if curated["tracks"] else "",
            },
        )
    return payload


@router.post("/invite")
async def invite_listen(body: InviteBody, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    from ..product_capabilities import is_enabled_for_user

    if not is_enabled_for_user(user["id"], "listen", is_admin=bool(user.get("is_admin"))):
        raise HTTPException(status_code=403, detail="一起听能力未开通")
    return await create_listen_invite(user["id"], body, push_ws=True)


@router.get("/sessions")
def list_sessions(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT * FROM listen_sessions
        WHERE user_id = ? AND status IN ('invited','active','paused')
        ORDER BY updated_at DESC LIMIT 30
        """,
        (user["id"],),
    )
    return [_session_out(r) for r in rows]


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    row = _owned_session(user["id"], session_id)
    tracks = _refill_session_tracks(row, user["id"])
    row = _owned_session(user["id"], session_id)
    return {"session": _session_out(row), "tracks": tracks}


@router.post("/sessions/{session_id}/accept")
async def accept_session(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    row = _owned_session(user["id"], session_id)
    if (row["status"] or "") == "ended":
        raise HTTPException(410, "这场一起听已结束，请重新发起")
    now = iso()
    db.execute(
        "UPDATE listen_sessions SET status='active', updated_at=? WHERE id=?",
        (now, session_id),
    )
    row = _owned_session(user["id"], session_id)
    tracks = _refill_session_tracks(row, user["id"])
    row = _owned_session(user["id"], session_id)
    await hub.send_user(
        user["id"],
        "listen_update",
        {"session_id": session_id, "status": "active", "conv_id": row["conv_id"]},
    )
    try:
        from ..listen_runtime import schedule_listen_react, start_listen_react_loop

        schedule_listen_react(
            user_id=user["id"],
            session_id=session_id,
            reason="刚开始一起听",
            force=True,
            delay=2.0,
        )
        start_listen_react_loop(user_id=user["id"], session_id=session_id)
    except Exception:
        pass
    return {"session": _session_out(row), "tracks": tracks}


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: ProgressBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    row = _owned_session(user["id"], session_id)
    prev_idx = int(row["track_index"] or 0)
    idx = prev_idx if body.track_index is None else int(body.track_index)
    pos = int(row["position_ms"] or 0) if body.position_ms is None else int(body.position_ms)
    status = (body.status or row["status"] or "active").strip()
    if status not in ("invited", "active", "paused", "ended"):
        raise HTTPException(400, "非法 status")
    db.execute(
        """
        UPDATE listen_sessions
        SET track_index=?, position_ms=?, status=?, updated_at=?
        WHERE id=?
        """,
        (idx, pos, status, iso(), session_id),
    )
    row = _owned_session(user["id"], session_id)
    await hub.send_user(
        user["id"],
        "listen_update",
        {
            "session_id": session_id,
            "status": status,
            "track_index": idx,
            "position_ms": pos,
            "conv_id": row["conv_id"],
        },
    )
    # 收起小窗只 patch 进度，不 ended → 会话仍 active，AI 仍知情；切歌可出评价气泡
    if status in ("active", "paused") and body.track_index is not None and idx != prev_idx:
        try:
            from ..listen_runtime import schedule_listen_react

            schedule_listen_react(
                user_id=user["id"],
                session_id=session_id,
                reason="切到下一首",
                track_changed=True,
                delay=1.5,
            )
        except Exception:
            pass
    # 真正退出：同会话所有存活场一并结束，避免旧 active 僵尸仍注入「正在听」
    if status == "ended":
        conv_id = str(row["conv_id"] or "")
        try:
            from ..listen_runtime import stop_listen_react_loop

            stop_listen_react_loop(session_id)
        except Exception:
            pass
        try:
            # 同会话其它 invited/active/paused 一并 ended
            db.execute(
                """
                UPDATE listen_sessions
                SET status='ended', updated_at=?
                WHERE user_id=? AND conv_id=? AND status IN ('invited','active','paused')
                """,
                (iso(), user["id"], conv_id),
            )
            for r2 in db.fetchall(
                "SELECT id FROM listen_sessions WHERE user_id=? AND conv_id=? AND status='ended'",
                (user["id"], conv_id),
            ):
                try:
                    stop_listen_react_loop(r2["id"])
                except Exception:
                    pass
        except Exception:
            pass
        try:
            db.execute(
                "UPDATE conversations SET listen_until = '' WHERE id = ? AND user_id = ?",
                (conv_id, user["id"]),
            )
        except Exception:
            pass
        try:
            from ..system_notices import emit_system_notice

            await emit_system_notice(
                user_id=user["id"],
                conv_id=conv_id,
                text="你结束了一起听",
                client_id=f"sys_listen_end_{session_id}",
            )
        except Exception:
            pass
        try:
            await close_listen_invite_cards(user_id=user["id"], conv_id=conv_id)
        except Exception:
            pass
    return {"session": _session_out(row)}
