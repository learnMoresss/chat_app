from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import current_user, iso

router = APIRouter(prefix="/api/v1/prefs", tags=["prefs"])


class PrefsOut(BaseModel):
    active_persona_id: Optional[str] = None
    shape_enabled: bool = True
    split_send: bool = True
    echo_streak: int = 0
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    quiet_blocks_chat: bool = False
    memory_inject: bool = True
    pause_proactive: bool = False
    pause_memory_extract: bool = False


class PrefsIn(BaseModel):
    active_persona_id: Optional[str] = None
    shape_enabled: Optional[bool] = None
    split_send: Optional[bool] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    quiet_blocks_chat: Optional[bool] = None
    memory_inject: Optional[bool] = None
    pause_proactive: Optional[bool] = None
    pause_memory_extract: Optional[bool] = None


def _prefs_row(user_id: str):
    return db.fetchone("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))


def _flag(row, key: str, default: bool = False) -> bool:
    if row is None:
        return default
    try:
        if key not in row.keys():
            return default
        v = row[key]
        if v is None:
            return default
        return bool(int(v))
    except Exception:
        return default


def _out(row) -> PrefsOut:
    if row is None:
        return PrefsOut()
    shape = True
    split = True
    streak = 0
    quiet_start, quiet_end = "23:00", "08:00"
    quiet_blocks = False
    mem_inject = True
    try:
        shape = bool(row["shape_enabled"]) if "shape_enabled" in row.keys() else True
        split = bool(row["split_send"]) if "split_send" in row.keys() else True
        streak = int(row["echo_streak"] or 0) if "echo_streak" in row.keys() else 0
        if "quiet_start" in row.keys() and row["quiet_start"]:
            quiet_start = row["quiet_start"]
        if "quiet_end" in row.keys() and row["quiet_end"]:
            quiet_end = row["quiet_end"]
        if "quiet_blocks_chat" in row.keys():
            quiet_blocks = bool(int(row["quiet_blocks_chat"] or 0))
        if "memory_inject" in row.keys():
            memory_raw = row["memory_inject"]
            mem_inject = True if memory_raw is None else bool(int(memory_raw))
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return PrefsOut(
        active_persona_id=row["active_persona_id"],
        shape_enabled=shape,
        split_send=split,
        echo_streak=streak,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        quiet_blocks_chat=quiet_blocks,
        memory_inject=mem_inject,
        pause_proactive=_flag(row, "pause_proactive", False),
        pause_memory_extract=_flag(row, "pause_memory_extract", False),
    )


def get_shape_flags(user_id: str) -> tuple[bool, bool]:
    """返回 (shape_enabled, split_send)。缺省开整形、开分句。"""
    row = _prefs_row(user_id)
    out = _out(row)
    return out.shape_enabled, out.split_send


def bump_echo_streak(user_id: str, hit: bool) -> int:
    """回声命中则 +1，否则清零；返回当前 streak。"""
    row = _prefs_row(user_id)
    now = iso()
    cur = 0
    if row is not None:
        try:
            cur = int(row["echo_streak"] or 0) if "echo_streak" in row.keys() else 0
        except (KeyError, IndexError, TypeError):
            cur = 0
    nxt = (cur + 1) if hit else 0
    if row is None:
        db.execute(
            """
            INSERT INTO user_prefs(user_id, active_persona_id, updated_at, shape_enabled, split_send, echo_streak)
            VALUES (?, NULL, ?, 1, 1, ?)
            """,
            (user_id, now, nxt),
        )
    else:
        db.execute(
            "UPDATE user_prefs SET echo_streak = ?, updated_at = ? WHERE user_id = ?",
            (nxt, now, user_id),
        )
    return nxt


@router.get("", response_model=PrefsOut)
def get_prefs(user: Dict[str, Any] = Depends(current_user)) -> PrefsOut:
    return _out(_prefs_row(user["id"]))


@router.put("", response_model=PrefsOut)
def put_prefs(body: PrefsIn, user: Dict[str, Any] = Depends(current_user)) -> PrefsOut:
    pid = body.active_persona_id
    if pid:
        p = db.fetchone(
            "SELECT id FROM personas WHERE id = ? AND user_id = ?",
            (pid, user["id"]),
        )
        if p is None:
            raise HTTPException(status_code=404, detail="人设不存在")
    now = iso()
    row = _prefs_row(user["id"])
    cur = _out(row)
    shape = cur.shape_enabled if body.shape_enabled is None else body.shape_enabled
    split = cur.split_send if body.split_send is None else body.split_send
    quiet_start = cur.quiet_start if body.quiet_start is None else body.quiet_start.strip()
    quiet_end = cur.quiet_end if body.quiet_end is None else body.quiet_end.strip()
    quiet_blocks = cur.quiet_blocks_chat if body.quiet_blocks_chat is None else body.quiet_blocks_chat
    mem_inject = cur.memory_inject if body.memory_inject is None else body.memory_inject
    pause_pro = cur.pause_proactive if body.pause_proactive is None else body.pause_proactive
    pause_ext = cur.pause_memory_extract if body.pause_memory_extract is None else body.pause_memory_extract
    active = pid if body.active_persona_id is not None else cur.active_persona_id
    if row is None:
        db.execute(
            """
            INSERT INTO user_prefs(
              user_id, active_persona_id, updated_at, shape_enabled, split_send, echo_streak,
              quiet_start, quiet_end, quiet_blocks_chat, memory_inject,
              pause_proactive, pause_memory_extract
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                active,
                now,
                1 if shape else 0,
                1 if split else 0,
                quiet_start,
                quiet_end,
                1 if quiet_blocks else 0,
                1 if mem_inject else 0,
                1 if pause_pro else 0,
                1 if pause_ext else 0,
            ),
        )
    else:
        db.execute(
            """
            UPDATE user_prefs
            SET active_persona_id = ?, updated_at = ?, shape_enabled = ?, split_send = ?,
                quiet_start = ?, quiet_end = ?, quiet_blocks_chat = ?, memory_inject = ?,
                pause_proactive = ?, pause_memory_extract = ?
            WHERE user_id = ?
            """,
            (
                active,
                now,
                1 if shape else 0,
                1 if split else 0,
                quiet_start,
                quiet_end,
                1 if quiet_blocks else 0,
                1 if mem_inject else 0,
                1 if pause_pro else 0,
                1 if pause_ext else 0,
                user["id"],
            ),
        )
    # 全局暂停开关同步到各定时任务（可暂停 / 可恢复）
    if body.pause_proactive is not None:
        try:
            from .. import schedules as sched

            sched.pause_all_for_user(user["id"], paused=bool(pause_pro))
        except Exception:
            pass
    return _out(_prefs_row(user["id"]))
