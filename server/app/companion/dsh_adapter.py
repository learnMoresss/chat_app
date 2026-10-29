"""按用途隔离的 DSH 会话封装。"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from ..dsh_gateway import dsh_complete, dsh_turn, dsh_turn_stream
from ..security import new_id

PURPOSES = ("chat", "mem", "plan", "replan", "reflect", "share")


def conversation_id_for(
    *,
    purpose: str,
    persona_id: str,
    chat_conv_id: str = "",
    salt: str = "",
) -> str:
    p = purpose if purpose in PURPOSES else "chat"
    if p == "chat" and chat_conv_id:
        return chat_conv_id
    tail = salt or new_id("")[:8]
    return f"sys-{p}-{persona_id}-{tail}"


class DshAdapter:
    async def complete(
        self,
        *,
        purpose: str,
        user_id: str,
        persona_id: str,
        text: str,
        system: str = "",
        chat_conv_id: str = "",
    ) -> str:
        cid = conversation_id_for(purpose=purpose, persona_id=persona_id, chat_conv_id=chat_conv_id)
        return await dsh_complete(
            user_id=user_id,
            conversation_id=cid,
            text=text,
            system=system,
        )

    async def turn(
        self,
        *,
        purpose: str,
        user_id: str,
        persona_id: str,
        text: str,
        instructions: str = "",
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        mode: str = "chat",
        chat_conv_id: str = "",
    ) -> Dict[str, Any]:
        cid = conversation_id_for(
            purpose=purpose,
            persona_id=persona_id,
            chat_conv_id=chat_conv_id,
        )
        return await dsh_turn(
            user_id=user_id,
            conversation_id=cid,
            text=text,
            instructions=instructions,
            system=system,
            history=history,
            mode=mode,
            persona_id=persona_id,
        )

    def turn_stream(
        self,
        *,
        purpose: str,
        user_id: str,
        persona_id: str,
        text: str,
        instructions: str = "",
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        mode: str = "chat",
        chat_conv_id: str = "",
    ) -> AsyncIterator[Dict[str, Any]]:
        cid = conversation_id_for(
            purpose=purpose,
            persona_id=persona_id,
            chat_conv_id=chat_conv_id,
        )
        return dsh_turn_stream(
            user_id=user_id,
            conversation_id=cid,
            text=text,
            instructions=instructions,
            system=system,
            history=history,
            mode=mode,
            persona_id=persona_id,
        )
