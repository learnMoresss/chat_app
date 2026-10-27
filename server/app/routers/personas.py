from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, memory as mem
from ..schemas import PersonaCreate, PersonaOut, PersonaPatch
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/personas", tags=["personas"])


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


def _owned(user_id: str, persona_id: str):
    row = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="人设不存在")
    return row


@router.post("", response_model=PersonaOut)
def create_persona(body: PersonaCreate, user: Dict[str, Any] = Depends(current_user)) -> PersonaOut:
    pid = new_id("p_")
    now = iso()
    db.execute(
        """
        INSERT INTO personas(id, user_id, name, color, bio, channel_id, state, is_bot, verify, remark, greet, moments_on, created_at, avatar_file_id)
        VALUES (?, ?, ?, ?, ?, ?, 'friend', ?, '', '', ?, ?, ?, ?)
        """,
        (
            pid,
            user["id"],
            body.name.strip(),
            body.color,
            body.bio,
            body.channel_id,
            1 if body.is_bot else 0,
            1 if body.greet else 0,
            1 if body.moments_on else 0,
            now,
            body.avatar_file_id or "",
        ),
    )
    conv_id = new_id("c_")
    db.execute(
        """
        INSERT INTO conversations(id, user_id, persona_id, preview, last_time, unread, pinned, muted, draft, last_seq)
        VALUES (?, ?, ?, '', ?, 0, 0, 0, '', 0)
        """,
        (conv_id, user["id"], pid, now),
    )
    row = _owned(user["id"], pid)
    try:
        from ..schedules import sync_persona_proactive

        sync_persona_proactive(
            user["id"],
            pid,
            greet=bool(body.greet),
            moments_on=bool(body.moments_on),
        )
    except Exception:
        pass
    try:
        from .. import stickers as stk

        stk.ensure_persona_stickers(
            user_id=user["id"],
            persona_id=pid,
            persona_name=body.name or "",
        )
    except Exception:
        pass
    return _persona_out(row)


@router.get("", response_model=List[PersonaOut])
def list_personas(user: Dict[str, Any] = Depends(current_user)) -> List[PersonaOut]:
    rows = db.fetchall(
        "SELECT * FROM personas WHERE user_id = ? ORDER BY created_at",
        (user["id"],),
    )
    out: List[PersonaOut] = []
    for r in rows:
        memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], r["id"])]
        out.append(_persona_out(r, memories))
    return out


@router.get("/{persona_id}", response_model=PersonaOut)
def get_persona(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> PersonaOut:
    row = _owned(user["id"], persona_id)
    memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], persona_id)]
    return _persona_out(row, memories)


@router.patch("/{persona_id}", response_model=PersonaOut)
def patch_persona(
    persona_id: str,
    body: PersonaPatch,
    user: Dict[str, Any] = Depends(current_user),
) -> PersonaOut:
    row = _owned(user["id"], persona_id)
    name = body.name if body.name is not None else row["name"]
    bio = body.bio if body.bio is not None else row["bio"]
    channel_id = body.channel_id if body.channel_id is not None else row["channel_id"]
    remark = body.remark if body.remark is not None else row["remark"]
    greet = int(body.greet) if body.greet is not None else row["greet"]
    moments_on = int(body.moments_on) if body.moments_on is not None else row["moments_on"]
    color = int(body.color) if body.color is not None else int(row["color"])
    try:
        avatar = (
            body.avatar_file_id
            if body.avatar_file_id is not None
            else (row["avatar_file_id"] or "")
        )
    except Exception:
        avatar = body.avatar_file_id or ""
    try:
        cur_pref = (row["voice_reply_pref"] or "sometimes").strip() or "sometimes"
    except Exception:
        cur_pref = "sometimes"
    pref = body.voice_reply_pref if body.voice_reply_pref is not None else cur_pref
    pref = (pref or "sometimes").strip()
    if pref not in ("never", "sometimes", "always"):
        raise HTTPException(status_code=400, detail="voice_reply_pref 须为 never|sometimes|always")
    if avatar:
        owned = db.fetchone(
            "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
            (avatar, user["id"]),
        )
        if not owned:
            raise HTTPException(status_code=400, detail="头像文件不存在")
    db.execute(
        """
        UPDATE personas
        SET name = ?, bio = ?, channel_id = ?, remark = ?, greet = ?, moments_on = ?, color = ?,
            avatar_file_id = ?, voice_reply_pref = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            name,
            bio,
            channel_id or "",
            remark or "",
            greet,
            moments_on,
            color,
            avatar or "",
            pref,
            persona_id,
            user["id"],
        ),
    )
    try:
        from ..schedules import sync_persona_proactive

        sync_persona_proactive(
            user["id"],
            persona_id,
            greet=bool(greet),
            moments_on=bool(moments_on),
        )
    except Exception:
        pass
    row = _owned(user["id"], persona_id)
    memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], persona_id)]
    return _persona_out(row, memories)


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class MemoryOut(BaseModel):
    id: str
    text: str
    updated_at: str = ""


@router.get("/{persona_id}/memories", response_model=List[MemoryOut])
def get_memories(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[MemoryOut]:
    _owned(user["id"], persona_id)
    return [MemoryOut(**m) for m in mem.list_memories(user["id"], persona_id)]


@router.post("/{persona_id}/memories", response_model=MemoryOut)
def create_memory(
    persona_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> MemoryOut:
    _owned(user["id"], persona_id)
    try:
        item = mem.add_memory(user["id"], persona_id, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MemoryOut(**item)


@router.patch("/{persona_id}/memories/{memory_id}", response_model=MemoryOut)
def edit_memory(
    persona_id: str,
    memory_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> MemoryOut:
    _owned(user["id"], persona_id)
    item = mem.update_memory(user["id"], persona_id, memory_id, body.text)
    if item is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return MemoryOut(**item)


@router.delete("/{persona_id}/memories/{memory_id}")
def remove_memory(
    persona_id: str,
    memory_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    if not mem.delete_memory(user["id"], persona_id, memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.get("/{persona_id}/person-notes")
def get_person_notes(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[dict]:
    _owned(user["id"], persona_id)
    from ..evidence import list_person_notes

    return list_person_notes(user["id"], persona_id)


@router.post("/{persona_id}/person-notes")
def create_person_note(
    persona_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import add_person_note

    try:
        return add_person_note(user["id"], persona_id, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{persona_id}/person-notes/{note_id}")
def remove_person_note(
    persona_id: str,
    note_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import delete_person_note

    delete_person_note(user["id"], note_id)
    return {"ok": True}


@router.get("/{persona_id}/memory-injections")
def recent_injections(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> List[dict]:
    _owned(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT id, evidence_json, char_count, created_at, conv_id
        FROM memory_injections
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC LIMIT 20
        """,
        (user["id"], persona_id),
    )
    out = []
    for r in rows:
        try:
            ev = json.loads(r["evidence_json"] or "[]")
        except Exception:
            ev = []
        out.append(
            {
                "id": r["id"],
                "evidence": ev,
                "char_count": r["char_count"],
                "created_at": r["created_at"],
                "conv_id": r["conv_id"],
            }
        )
    return out


@router.post("/{persona_id}/memory-nightly")
def run_memory_nightly(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import run_nightly

    return run_nightly(user["id"], persona_id)


class EraserIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="word", pattern="^(word|sentence|regex)$")
    enabled: bool = True


class EraserPatch(BaseModel):
    pattern: Optional[str] = Field(default=None, max_length=200)
    mode: Optional[str] = Field(default=None, pattern="^(word|sentence|regex)$")
    enabled: Optional[bool] = None


class EraserOut(BaseModel):
    id: str
    pattern: str
    mode: str
    enabled: bool
    hits: int = 0


def _eraser_out(row) -> EraserOut:
    return EraserOut(
        id=row["id"],
        pattern=row["pattern"],
        mode=row["mode"] or "word",
        enabled=bool(row["enabled"]),
        hits=int(row["hits"] or 0),
    )


@router.get("/{persona_id}/erasers", response_model=List[EraserOut])
def list_erasers(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[EraserOut]:
    _owned(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT * FROM eraser_rules
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at ASC
        """,
        (user["id"], persona_id),
    )
    return [_eraser_out(r) for r in rows]


@router.post("/{persona_id}/erasers", response_model=EraserOut)
def create_eraser(
    persona_id: str,
    body: EraserIn,
    user: Dict[str, Any] = Depends(current_user),
) -> EraserOut:
    _owned(user["id"], persona_id)
    rid = new_id("er_")
    now = iso()
    db.execute(
        """
        INSERT INTO eraser_rules(id, user_id, persona_id, pattern, mode, enabled, hits, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (rid, user["id"], persona_id, body.pattern.strip(), body.mode, 1 if body.enabled else 0, now),
    )
    row = db.fetchone("SELECT * FROM eraser_rules WHERE id = ?", (rid,))
    assert row is not None
    return _eraser_out(row)


@router.patch("/{persona_id}/erasers/{eraser_id}", response_model=EraserOut)
def patch_eraser(
    persona_id: str,
    eraser_id: str,
    body: EraserPatch,
    user: Dict[str, Any] = Depends(current_user),
) -> EraserOut:
    _owned(user["id"], persona_id)
    row = db.fetchone(
        "SELECT * FROM eraser_rules WHERE id = ? AND user_id = ? AND persona_id = ?",
        (eraser_id, user["id"], persona_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    pattern = body.pattern.strip() if body.pattern is not None else row["pattern"]
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern 不能为空")
    mode = body.mode if body.mode is not None else row["mode"]
    enabled = row["enabled"] if body.enabled is None else (1 if body.enabled else 0)
    db.execute(
        "UPDATE eraser_rules SET pattern = ?, mode = ?, enabled = ? WHERE id = ?",
        (pattern, mode, enabled, eraser_id),
    )
    updated = db.fetchone("SELECT * FROM eraser_rules WHERE id = ?", (eraser_id,))
    assert updated is not None
    return _eraser_out(updated)


@router.delete("/{persona_id}/erasers/{eraser_id}")
def delete_eraser(
    persona_id: str,
    eraser_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    row = db.fetchone(
        "SELECT id FROM eraser_rules WHERE id = ? AND user_id = ? AND persona_id = ?",
        (eraser_id, user["id"], persona_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.execute("DELETE FROM eraser_rules WHERE id = ?", (eraser_id,))
    return {"ok": True}


@router.post("/{persona_id}/open-chat")
def open_chat(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    """从通讯录发消息：复用会话；若已删聊天则清空软删标记，回到空会话。"""
    _owned(user["id"], persona_id)
    now = iso()
    row = db.fetchone(
        """
        SELECT * FROM conversations
        WHERE user_id = ? AND persona_id = ?
        ORDER BY last_time DESC LIMIT 1
        """,
        (user["id"], persona_id),
    )
    if row is None:
        cid = new_id("c_")
        db.execute(
            """
            INSERT INTO conversations(id, user_id, persona_id, preview, last_time, unread, pinned, muted, draft, last_seq)
            VALUES (?, ?, ?, '', ?, 0, 0, 0, '', 0)
            """,
            (cid, user["id"], persona_id, now),
        )
        row = db.fetchone("SELECT * FROM conversations WHERE id = ?", (cid,))
    else:
        keys = row.keys()
        deleted = (row["deleted_at"] or "").strip() if "deleted_at" in keys else ""
        hidden = int(row["hidden"] or 0) if "hidden" in keys else 0
        if deleted or hidden:
            db.execute("DELETE FROM messages WHERE user_id = ? AND conv_id = ?", (user["id"], row["id"]))
            sets = ["preview = ''", "unread = 0", "draft = ''", "last_seq = 0", "last_time = ?"]
            args: list = [now]
            if "deleted_at" in keys:
                sets.append("deleted_at = ''")
            if "hidden" in keys:
                sets.append("hidden = 0")
            db.execute(
                f"UPDATE conversations SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                (*args, row["id"], user["id"]),
            )
            row = db.fetchone("SELECT * FROM conversations WHERE id = ?", (row["id"],))
    assert row is not None
    from ..routers.chat import _conv_out

    return {"conversation": _conv_out(row).model_dump()}


@router.delete("/{persona_id}")
def delete_persona(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    """删除人设：通讯录消失，并清掉对应会话与消息。"""
    _owned(user["id"], persona_id)
    convs = db.fetchall(
        "SELECT id FROM conversations WHERE user_id = ? AND persona_id = ?",
        (user["id"], persona_id),
    )
    for c in convs:
        db.execute(
            "DELETE FROM messages WHERE user_id = ? AND conv_id = ?",
            (user["id"], c["id"]),
        )
    db.execute(
        "DELETE FROM conversations WHERE user_id = ? AND persona_id = ?",
        (user["id"], persona_id),
    )
    db.execute("DELETE FROM memories WHERE user_id = ? AND persona_id = ?", (user["id"], persona_id))
    try:
        db.execute(
            "DELETE FROM persona_assets WHERE user_id = ? AND persona_id = ?",
            (user["id"], persona_id),
        )
    except Exception:
        pass
    db.execute("DELETE FROM personas WHERE id = ? AND user_id = ?", (persona_id, user["id"]))
    return {"ok": True}
