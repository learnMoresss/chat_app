from __future__ import annotations

import asyncio
from typing import Any, Dict


def emit_job_event(user_id: str, event: str, payload: Dict[str, Any]) -> None:
    """推任务事件到用户 WebSocket。可从同步路由线程调用。"""
    try:
        from ..ws_hub import get_main_loop, hub

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(hub.send_user(user_id, event, payload))
            return
        except RuntimeError:
            pass
        main = get_main_loop()
        if main is not None and main.is_running():
            asyncio.run_coroutine_threadsafe(
                hub.send_user(user_id, event, payload), main
            )
    except Exception:
        pass


def job_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("id"),
        "capability": job.get("capability"),
        "plugin_id": job.get("plugin_id"),
        "status": job.get("status"),
        "progress_pct": job.get("progress_pct") or 0,
        "progress_msg": job.get("progress_msg") or "",
        "result": job.get("result"),
        "error": job.get("error") or "",
        "conversation_id": job.get("conversation_id") or "",
        "message_id": job.get("message_id") or "",
    }


def emit_message_event(user_id: str, message: Dict[str, Any]) -> None:
    """推聊天消息（含任务落库后的更新）。"""
    emit_job_event(user_id, "message", message)
