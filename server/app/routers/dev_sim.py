"""开发用：模拟 AI 发送说话/唱歌语音条到会话（推 WS）。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..media_cards import save_audio_bytes
from ..routers.chat import _insert_message
from ..security import new_id
from ..ws_hub import hub

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


class SimVoiceBody(BaseModel):
    conv_id: str = Field(min_length=4, max_length=64)
    kind: Literal["speak", "sing"] = "speak"
    audio_path: str = ""
    caption: str = ""
    seconds: int = 0


def _audio_seconds(path: Path) -> int:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            text=True,
            timeout=30,
        ).strip()
        return max(1, int(round(float(out))))
    except Exception:
        try:
            return max(1, int(path.stat().st_size / 40000))
        except Exception:
            return 8


def _default_sample(kind: str) -> Path:
    root = Path(__file__).resolve().parents[3] / "tmp"
    if kind == "sing":
        return root / "sing-fanqiehua-xueer.mp3"
    return root / "sing-xueer-speak.mp3"


def _resolve_user(request: Request, x_dev_token: Optional[str]) -> Dict[str, Any]:
    """Bearer JWT（正常）或 X-Dev-Token + 会话归属用户。"""
    settings = get_settings()
    dev = (settings.dev_sim_token or "").strip()
    if dev and x_dev_token and x_dev_token.strip() == dev:
        return {"id": "", "dev": True}
    # 走现有 JWT
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要登录或 X-Dev-Token")
    token = auth.split(" ", 1)[1].strip()
    from ..security import decode_access_token

    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="token 无效") from exc
    uid = str(payload.get("sub") or payload.get("user_id") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="token 无用户")
    return {"id": uid}


@router.post("/sim-voice")
async def sim_voice(
    body: SimVoiceBody,
    request: Request,
    x_dev_token: Optional[str] = Header(default=None, alias="X-Dev-Token"),
) -> Dict[str, Any]:
    """把本地样例音频作为人设语音/唱歌气泡推给会话。"""
    actor = _resolve_user(request, x_dev_token)
    conv = db.fetchone("SELECT * FROM conversations WHERE id = ?", (body.conv_id,))
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    uid = str(conv["user_id"])
    if not actor.get("dev") and actor.get("id") != uid:
        # 非 dev token 只能推自己的会话
        raise HTTPException(status_code=403, detail="无权操作该会话")
    persona_id = (conv["persona_id"] or "").strip()
    if not persona_id:
        raise HTTPException(status_code=400, detail="会话无人设")

    path = Path(body.audio_path).expanduser() if body.audio_path.strip() else _default_sample(body.kind)
    if not path.is_file():
        alt = Path(__file__).resolve().parents[3] / "tmp" / path.name
        if alt.is_file():
            path = alt
        else:
            raise HTTPException(status_code=400, detail=f"音频不存在: {path}")

    data = path.read_bytes()
    if len(data) < 500:
        raise HTTPException(status_code=400, detail="音频过小")
    suf = path.suffix.lstrip(".").lower() or "mp3"
    ctype = "audio/mpeg" if suf == "mp3" else "audio/wav" if suf == "wav" else f"audio/{suf}"
    fid = save_audio_bytes(
        user_id=uid,
        data=data,
        source="dev_sim_sing" if body.kind == "sing" else "dev_sim_speak",
        suffix=suf,
        content_type=ctype,
    )
    if not fid:
        raise HTTPException(status_code=500, detail="落盘失败")

    secs = int(body.seconds) if body.seconds > 0 else _audio_seconds(path)
    payload: Dict[str, Any] = {
        "v": 1,
        "files": [fid],
        "caption": (body.caption or "").strip(),
        "seconds": secs,
    }
    if body.kind == "sing":
        payload["kind_extra"] = "sing"
        payload["sing"] = {"arrangement": "a_cappella", "mode": "sim", "prompt": "sim"}

    msg = _insert_message(
        user_id=uid,
        conv_id=body.conv_id,
        client_id=new_id("simcli_"),
        sender_id=persona_id,
        mine=False,
        kind="voice",
        text=json.dumps(payload, ensure_ascii=False),
        state="sent",
        seconds=secs,
    )
    await hub.send_user(uid, "message", msg.model_dump())
    return {"ok": True, "message": msg.model_dump(), "file_id": fid, "kind": body.kind}
