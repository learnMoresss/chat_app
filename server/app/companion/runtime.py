"""CompanionRuntime：聊天入口转事件；人设串行锁。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .events import emit_event
from .memory_write import MemoryWriteService, looks_like_explicit_memory_op
from .raw_records import archive_raw

_locks: Dict[str, asyncio.Lock] = {}


def _persona_lock(user_id: str, persona_id: str) -> asyncio.Lock:
    key = f"{user_id}:{persona_id}"
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


class CompanionRuntime:
    def __init__(self) -> None:
        self.memory_write = MemoryWriteService()

    async def on_user_message(
        self,
        *,
        user_id: str,
        persona_id: str,
        conv_id: str,
        text: str,
        message_id: str = "",
        kind: str = "chat",
    ) -> Dict[str, Any]:
        """归档 raw → 事件 → 显式记忆（若命中）。返回 explicit 结果供回复前使用。"""
        async with _persona_lock(user_id, persona_id):
            archive_raw(
                user_id=user_id,
                persona_id=persona_id,
                conv_id=conv_id,
                kind=kind,
                role="user",
                text=text,
                source_ref=message_id,
            )
            eid = emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="user_message",
                source="chat",
                payload={"message_id": message_id, "text": (text or "")[:500]},
            )
            explicit: Dict[str, Any] = {"handled": False, "status": "skip"}
            if looks_like_explicit_memory_op(text):
                explicit = self.memory_write.apply_explicit(
                    user_id=user_id,
                    persona_id=persona_id,
                    text=text,
                    source_message_id=message_id,
                )
                if explicit.get("handled"):
                    emit_event(
                        user_id=user_id,
                        persona_id=persona_id,
                        kind="memory_explicit",
                        source="chat",
                        payload=explicit,
                    )
            return {"event_id": eid, "explicit": explicit}

    def enqueue_extract_after_reply(
        self,
        *,
        user_id: str,
        persona_id: str,
        user_text: str,
        assistant_text: str,
        source_message_id: str = "",
        skip_explicit_text: bool = False,
        provider: str = "",
        model: str = "",
    ) -> Optional[str]:
        try:
            return self.memory_write.enqueue_extract(
                user_id=user_id,
                persona_id=persona_id,
                user_text=user_text,
                assistant_text=assistant_text,
                source_message_id=source_message_id,
                skip_explicit_text=skip_explicit_text,
                provider=provider,
                model=model,
            )
        except Exception:
            return None
