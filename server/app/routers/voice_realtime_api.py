"""实时语音：就绪检查 + 上传音频开一轮（ASR→DSH 分句→逐句 TTS→WS）。"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .. import voice_realtime as vr
from ..security import current_user, new_id

router = APIRouter(prefix="/api/v1/voice/realtime", tags=["voice-realtime"])


class _HangupBody(BaseModel):
    conv_id: str = Field(..., min_length=1)


@router.get("/ready")
def voice_ready(
    conv_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    payload = vr.readiness()
    if conv_id:
        gate = vr.gate_for_conv(user_id=user["id"], conv_id=conv_id)
        payload.update(gate)
        # 没固定音色时不算可通话
        if gate.get("need_fixed_voice"):
            payload["ready"] = False
            payload["connecting"] = False
    return payload


@router.post("/warm")
async def voice_warm(
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """触发预热（可重复）；App 显示「连接中」时调用。"""
    _ = user

    def _warm() -> None:
        vr.warm_in_background()

    background_tasks.add_task(_warm)
    return {"ok": True, **vr.readiness()}


@router.post("/hangup")
def voice_hangup(
    body: _HangupBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """挂断：清掉本通电话的语音上下文（仍不碰文字消息）。"""
    conv_id = body.conv_id.strip()
    vr.clear_voice_session(user_id=user["id"], conv_id=conv_id)
    return {"ok": True, "conv_id": conv_id}


@router.post("/turn")
async def voice_turn(
    background_tasks: BackgroundTasks,
    conv_id: str = Form(...),
    file: UploadFile = File(...),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    gate = vr.gate_for_conv(user_id=user["id"], conv_id=conv_id)
    if gate.get("need_fixed_voice"):
        raise HTTPException(
            400,
            detail={
                "error": gate.get("error") or "需要先固定音色",
                "error_code": "need_fixed_voice",
                "persona_id": gate.get("persona_id") or "",
            },
        )
    data = await file.read()
    if not data or len(data) < 200:
        raise HTTPException(400, "录音太短")
    suffix = Path(file.filename or "voice.m4a").suffix or ".m4a"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    path = Path(tmp.name)
    turn_id = new_id("vturn_")

    async def _run() -> None:
        try:
            await vr.run_voice_turn(
                user_id=user["id"],
                conv_id=conv_id,
                audio_path=path,
                turn_id=turn_id,
            )
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    background_tasks.add_task(_run)
    return {"ok": True, "turn_id": turn_id, "conv_id": conv_id}
