"""实时语音：就绪检查 + 通话气泡 + 问候 + 打断 + 上传音频开一轮。"""
from __future__ import annotations

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
    duration_sec: Optional[int] = Field(None, ge=0, le=86400)


class _CancelBody(BaseModel):
    conv_id: str = Field(..., min_length=1)
    turn_id: str = Field("", description="空则取消当前 active turn")


class _StartBody(BaseModel):
    conv_id: str = Field(..., min_length=1)


class _GreetingBody(BaseModel):
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
        if gate.get("need_fixed_voice"):
            payload["ready"] = False
            payload["connecting"] = False
    return payload


@router.post("/warm")
async def voice_warm(
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
    conv_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """触发预热（可重复）；有 conv_id 时按该会话固定音色预热 CosyVoice 参考声。"""
    cid = (conv_id or "").strip()
    uid = user["id"]

    def _warm() -> None:
        vr.warm_in_background(user_id=uid, conv_id=cid)

    background_tasks.add_task(_warm)
    return {"ok": True, **vr.readiness()}


@router.post("/start")
async def voice_start(
    body: _StartBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """点击语音通话：插入微信式通话气泡，AI 视为已接听。"""
    out = await vr.start_call(user_id=user["id"], conv_id=body.conv_id.strip())
    if not out.get("ok"):
        code = out.get("error_code") or "start_failed"
        status = 409 if code == "call_busy" else 400
        raise HTTPException(
            status,
            detail={
                "error": out.get("error") or "无法开始通话",
                "error_code": code,
                "persona_id": out.get("persona_id") or "",
                "busy": out.get("busy"),
            },
        )
    return out


@router.post("/greeting")
async def voice_greeting(
    background_tasks: BackgroundTasks,
    body: _GreetingBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """接通问候：固定音色短句，播完后客户端再开麦。"""
    gate = vr.gate_for_conv(user_id=user["id"], conv_id=body.conv_id.strip())
    if gate.get("need_fixed_voice"):
        raise HTTPException(
            400,
            detail={
                "error": gate.get("error") or "需要先固定音色",
                "error_code": "need_fixed_voice",
                "persona_id": gate.get("persona_id") or "",
            },
        )
    turn_id = new_id("vgreet_")
    uid = user["id"]
    cid = body.conv_id.strip()
    background_tasks.add_task(vr.run_greeting, user_id=uid, conv_id=cid, turn_id=turn_id)
    return {"ok": True, "turn_id": turn_id, "conv_id": cid}


@router.post("/cancel")
async def voice_cancel(
    body: _CancelBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """用户插话打断：停服务端 LLM/TTS 推送。"""
    return await vr.cancel_turn(
        user_id=user["id"],
        conv_id=body.conv_id.strip(),
        turn_id=(body.turn_id or "").strip(),
    )


@router.post("/hangup")
async def voice_hangup(
    body: _HangupBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """挂断：清语音上下文 + 更新通话气泡时长。"""
    return await vr.hangup_call(
        user_id=user["id"],
        conv_id=body.conv_id.strip(),
        duration_sec=body.duration_sec,
    )


@router.post("/turn")
async def voice_turn(
    background_tasks: BackgroundTasks,
    conv_id: str = Form(...),
    file: UploadFile = File(...),
    prefix_text: str = Form(""),
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
    suffix = Path(file.filename or "voice.wav").suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    path = Path(tmp.name)
    turn_id = new_id("vturn_")
    prefix = (prefix_text or "").strip()

    async def _run() -> None:
        try:
            await vr.run_voice_turn(
                user_id=user["id"],
                conv_id=conv_id,
                audio_path=path,
                turn_id=turn_id,
                prefix_text=prefix,
            )
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    background_tasks.add_task(_run)
    return {"ok": True, "turn_id": turn_id, "conv_id": conv_id}
