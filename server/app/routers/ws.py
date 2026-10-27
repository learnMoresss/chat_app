from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException

from ..security import decode_access_token
from .. import db
from ..ws_hub import hub

router = APIRouter(tags=["ws"])


@router.websocket("/api/v1/ws")
async def websocket_endpoint(ws: WebSocket, token: str = "") -> None:
    """先 accept 再鉴权，避免未 accept 时 uvicorn 记成 403、客户端无法区分。"""
    await ws.accept()
    if not token:
        await ws.send_json({"event": "auth_error", "payload": {"detail": "需要登录"}})
        await ws.close(code=4401)
        return
    try:
        payload = decode_access_token(token)
    except HTTPException as exc:
        await ws.send_json(
            {"event": "auth_error", "payload": {"detail": str(exc.detail), "code": 401}}
        )
        await ws.close(code=4401)
        return
    except Exception:
        await ws.send_json({"event": "auth_error", "payload": {"detail": "令牌无效", "code": 401}})
        await ws.close(code=4401)
        return

    user = db.fetchone("SELECT * FROM users WHERE id = ?", (payload["sub"],))
    if user is None or user["is_disabled"]:
        await ws.send_json({"event": "auth_error", "payload": {"detail": "账号不可用", "code": 401}})
        await ws.close(code=4401)
        return

    user_id = user["id"]
    await hub.connect(user_id, ws)
    try:
        await ws.send_json({"event": "ready", "payload": {"user_id": user_id}})
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=45.0)
            except asyncio.TimeoutError:
                await ws.send_json({"event": "ping", "payload": {}})
                continue
            if data in ("ping", '{"type":"ping"}'):
                await ws.send_json({"event": "pong", "payload": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(user_id, ws)
