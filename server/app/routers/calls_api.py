"""语音/视频通话：信令骨架（WebSocket 事件 + 会话表）。媒体通道后续接 WebRTC。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import current_user, iso, new_id
from ..ws_hub import hub

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])


class CallCreate(BaseModel):
    conv_id: str
    media: str = Field(default="audio", description="audio | video")


class CallSignal(BaseModel):
    type: str = Field(description="offer|answer|ice|hangup|ringing|accept|reject")
    payload: Dict[str, Any] = Field(default_factory=dict)


def _owned(user_id: str, call_id: str):
    row = db.fetchone(
        "SELECT * FROM call_sessions WHERE id = ? AND user_id = ?",
        (call_id, user_id),
    )
    if row is None:
        raise HTTPException(404, "通话不存在")
    return row


@router.post("")
async def create_call(body: CallCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    media = (body.media or "audio").strip().lower()
    if media not in ("audio", "video"):
        raise HTTPException(400, "media 仅 audio|video")
    conv = db.fetchone(
        "SELECT id, persona_id FROM conversations WHERE id = ? AND user_id = ?",
        (body.conv_id, user["id"]),
    )
    if conv is None:
        raise HTTPException(404, "会话不存在")
    # 与实时语音通话互斥：账号同时只能一路
    from .. import voice_realtime as vr

    busy = vr.find_active_call_for_user(user["id"])
    if busy:
        kind = "视频通话" if (busy.get("media") or "") == "video" else "语音通话"
        raise HTTPException(
            409,
            detail={
                "error": f"当前已有{kind}进行中，请先挂断",
                "error_code": "call_busy",
                "busy": busy,
            },
        )
    now = iso()
    cid = new_id("call_")
    db.execute(
        """
        INSERT INTO call_sessions(id, user_id, conv_id, persona_id, media, status, meta_json, created_at, updated_at)
        VALUES (?,?,?,?,?,'ringing','{}',?,?)
        """,
        (cid, user["id"], body.conv_id, conv["persona_id"] or "", media, now, now),
    )
    await hub.send_user(
        user["id"],
        "call_invite",
        {
            "call_id": cid,
            "conv_id": body.conv_id,
            "persona_id": conv["persona_id"],
            "media": media,
            "status": "ringing",
        },
    )
    return {"id": cid, "status": "ringing", "media": media, "conv_id": body.conv_id}


@router.get("")
def list_calls(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, conv_id, persona_id, media, status, created_at, updated_at
        FROM call_sessions WHERE user_id = ?
        ORDER BY created_at DESC LIMIT 30
        """,
        (user["id"],),
    )
    return [dict(r) for r in rows]


@router.post("/{call_id}/signal")
async def signal_call(
    call_id: str,
    body: CallSignal,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    row = _owned(user["id"], call_id)
    typ = (body.type or "").strip().lower()
    if typ not in ("offer", "answer", "ice", "hangup", "ringing", "accept", "reject"):
        raise HTTPException(400, "非法 signal type")
    now = iso()
    status = row["status"]
    if typ == "accept":
        status = "active"
    elif typ in ("hangup", "reject"):
        status = "ended"
    db.execute(
        "UPDATE call_sessions SET status=?, updated_at=? WHERE id=?",
        (status, now, call_id),
    )
    await hub.send_user(
        user["id"],
        "call_signal",
        {
            "call_id": call_id,
            "type": typ,
            "payload": body.payload or {},
            "status": status,
            "conv_id": row["conv_id"],
        },
    )
    return {"ok": True, "status": status}


@router.get("/{call_id}")
def get_call(call_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return dict(_owned(user["id"], call_id))
