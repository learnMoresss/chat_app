"""朋友圈 API（用户发动态；角色自发走 schedules kind=moment）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/moments", tags=["moments"])


class MomentIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    media_ids: List[str] = Field(default_factory=list)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    reply_to: Optional[str] = None


def _moment_out(row, *, likes: List[str], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        media = json.loads(row["media_json"] or "[]")
    except Exception:
        media = []
    return {
        "id": row["id"],
        "author_id": row["author_id"],
        "author_kind": row["author_kind"],
        "text": row["text"] or "",
        "media": media,
        "likes": likes,
        "comments": comments,
        "created_at": row["created_at"],
    }


def _load_likes(moment_id: str) -> List[str]:
    rows = db.fetchall(
        "SELECT actor_id FROM moment_likes WHERE moment_id = ? ORDER BY created_at",
        (moment_id,),
    )
    return [r["actor_id"] for r in rows]


def _load_comments(moment_id: str) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, actor_id, text, reply_to, created_at FROM moment_comments
        WHERE moment_id = ? ORDER BY created_at ASC
        """,
        (moment_id,),
    )
    return [
        {
            "id": r["id"],
            "actor_id": r["actor_id"],
            "text": r["text"],
            "reply_to": r["reply_to"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("")
def list_moments(
    limit: int = Query(default=30, ge=1, le=100),
    before: str = "",
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if before:
        rows = db.fetchall(
            """
            SELECT * FROM moments
            WHERE user_id = ? AND created_at < ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user["id"], before, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM moments WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user["id"], limit),
        )
    items = [
        _moment_out(r, likes=_load_likes(r["id"]), comments=_load_comments(r["id"]))
        for r in rows
    ]
    return {"moments": items}


@router.post("")
def create_moment(body: MomentIn, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    text = (body.text or "").strip()
    if not text and not body.media_ids:
        raise HTTPException(status_code=400, detail="动态不能为空")
    for mid in body.media_ids:
        row = db.fetchone(
            "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
            (mid, user["id"]),
        )
        if row is None:
            raise HTTPException(status_code=400, detail=f"媒体不存在: {mid}")
    mid = new_id("mom_")
    now = iso()
    db.execute(
        """
        INSERT INTO moments(id, user_id, author_id, author_kind, text, media_json, created_at)
        VALUES (?, ?, ?, 'user', ?, ?, ?)
        """,
        (mid, user["id"], user["id"], text, json.dumps(body.media_ids, ensure_ascii=False), now),
    )
    row = db.fetchone("SELECT * FROM moments WHERE id = ?", (mid,))
    assert row is not None
    return _moment_out(row, likes=[], comments=[])


@router.post("/{moment_id}/like")
def toggle_like(moment_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    row = db.fetchone(
        "SELECT id FROM moments WHERE id = ? AND user_id = ?",
        (moment_id, user["id"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="动态不存在")
    existing = db.fetchone(
        "SELECT moment_id FROM moment_likes WHERE moment_id = ? AND actor_id = ?",
        (moment_id, user["id"]),
    )
    if existing:
        db.execute(
            "DELETE FROM moment_likes WHERE moment_id = ? AND actor_id = ?",
            (moment_id, user["id"]),
        )
        return {"liked": False}
    db.execute(
        "INSERT INTO moment_likes(moment_id, user_id, actor_id, created_at) VALUES (?, ?, ?, ?)",
        (moment_id, user["id"], user["id"], iso()),
    )
    return {"liked": True}


@router.post("/{moment_id}/comments")
def add_comment(
    moment_id: str,
    body: CommentIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT id FROM moments WHERE id = ? AND user_id = ?",
        (moment_id, user["id"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="动态不存在")
    cid = new_id("mc_")
    now = iso()
    db.execute(
        """
        INSERT INTO moment_comments(id, moment_id, user_id, actor_id, text, reply_to, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cid, moment_id, user["id"], user["id"], body.text.strip(), body.reply_to, now),
    )
    return {
        "id": cid,
        "actor_id": user["id"],
        "text": body.text.strip(),
        "reply_to": body.reply_to,
        "created_at": now,
    }


@router.delete("/{moment_id}")
def delete_moment(moment_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    row = db.fetchone(
        "SELECT id FROM moments WHERE id = ? AND user_id = ?",
        (moment_id, user["id"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="动态不存在")
    db.execute("DELETE FROM moment_likes WHERE moment_id = ?", (moment_id,))
    db.execute("DELETE FROM moment_comments WHERE moment_id = ?", (moment_id,))
    db.execute("DELETE FROM moments WHERE id = ?", (moment_id,))
    return {"ok": True}


def post_persona_moment(
    *,
    user_id: str,
    persona_id: str,
    text: str,
    media_ids: Optional[List[str]] = None,
) -> Optional[str]:
    """角色自发动态（调度器/意图执行器调用）。只记发布事件，不把文案升格为事实记忆。"""
    from ..media_cards import extract_media_intents, generate_or_mood

    raw = (text or "").strip()
    if not raw and not media_ids:
        return None
    cleaned, intents = extract_media_intents(raw)
    ids = list(media_ids or [])
    if not ids:
        for it in intents:
            if it.get("kind") != "image":
                continue
            fid = generate_or_mood(
                user_id=user_id,
                caption=str(it.get("prompt") or cleaned or "随手一拍"),
                persona_name="",
                width=768,
                height=768,
            )
            if fid:
                ids.append(fid)
                break
    body = (cleaned or raw).strip()
    if not body and not ids:
        return None
    mid = new_id("mom_")
    db.execute(
        """
        INSERT INTO moments(id, user_id, author_id, author_kind, text, media_json, created_at)
        VALUES (?, ?, ?, 'persona', ?, ?, ?)
        """,
        (mid, user_id, persona_id, body[:2000], json.dumps(ids, ensure_ascii=False), iso()),
    )
    try:
        from ..companion.events import emit_event

        emit_event(
            user_id=user_id,
            persona_id=persona_id,
            kind="moment_published",
            source="world",
            payload={"moment_id": mid, "text_len": len(body)},
        )
    except Exception:
        pass
    return mid
