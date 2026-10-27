"""会话内系统消息：列表居中灰字 + 进模型上下文，让人设明确知道状态变化。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from . import db
from .security import iso, new_id

logger = logging.getLogger("chat.system_notices")


def _insert_system_row(
    *,
    user_id: str,
    conv_id: str,
    text: str,
    client_id: str,
) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text or not conv_id:
        return None
    existing = db.fetchone(
        "SELECT * FROM messages WHERE user_id = ? AND client_id = ?",
        (user_id, client_id),
    )
    if existing is not None:
        return dict(existing)

    from .routers.chat import _insert_message

    msg = _insert_message(
        user_id=user_id,
        conv_id=conv_id,
        client_id=client_id,
        sender_id="system",
        mine=False,
        kind="system",
        text=text,
        state="sent",
    )
    return msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)


async def emit_system_notice(
    *,
    user_id: str,
    conv_id: str,
    text: str,
    client_id: str = "",
) -> Optional[Dict[str, Any]]:
    """落库并推 WS；client_id 稳定则可幂等（同事件不重复）。"""
    cid = (client_id or "").strip() or new_id("syscli_")
    payload = _insert_system_row(
        user_id=user_id, conv_id=conv_id, text=text, client_id=cid
    )
    if not payload:
        return None
    try:
        from .ws_hub import hub

        await hub.send_user(user_id, "message", payload)
    except Exception:
        logger.exception("system notice ws failed")
    return payload


def emit_system_notice_soon(
    *,
    user_id: str,
    conv_id: str,
    text: str,
    client_id: str = "",
) -> None:
    """同步上下文（设备 stop 等）里调用：有事件环则 create_task。"""

    async def _run() -> None:
        await emit_system_notice(
            user_id=user_id, conv_id=conv_id, text=text, client_id=client_id
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        try:
            payload = _insert_system_row(
                user_id=user_id,
                conv_id=conv_id,
                text=text,
                client_id=(client_id or "").strip() or new_id("syscli_"),
            )
            if payload:
                logger.info("system notice stored offline conv=%s", conv_id)
        except Exception:
            logger.exception("system notice sync insert failed")


def conv_for_device(device: Dict[str, Any], user_id: str) -> str:
    cid = (device.get("bind_conv_id") or "").strip()
    if cid:
        return cid
    pid = (device.get("owner_persona_id") or "").strip()
    if not pid:
        return ""
    row = db.fetchone(
        """
        SELECT id FROM conversations
        WHERE user_id = ? AND persona_id = ?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user_id, pid),
    )
    return (row["id"] if row else "") or ""


def notice_listen_ended(*, user_id: str, conv_id: str, session_id: str) -> None:
    emit_system_notice_soon(
        user_id=user_id,
        conv_id=conv_id,
        text="你结束了一起听",
        client_id=f"sys_listen_end_{session_id}",
    )


def notice_device_stopped(*, user_id: str, device: Dict[str, Any]) -> None:
    kind = (device.get("kind") or "desktop").strip()
    label = "手机" if kind == "android" else "电脑"
    conv_id = conv_for_device(device, user_id)
    if not conv_id:
        return
    did = (device.get("id") or "").strip() or "vd"
    emit_system_notice_soon(
        user_id=user_id,
        conv_id=conv_id,
        text=f"你关闭了{label}",
        client_id=f"sys_vd_stop_{did}_{new_id('t')}",
    )
