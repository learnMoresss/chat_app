"""AI 主机 product-gateway（DSH）——产品唯一思考环。无本地 LLM 回退。"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import get_settings


class DshNotConfigured(RuntimeError):
    """未配置或未启用 DSH 网关。"""


def dsh_enabled() -> bool:
    s = get_settings()
    return bool(s.dsh_gateway_enabled and s.dsh_gateway_url.strip() and s.dsh_gateway_token.strip())


def require_dsh() -> None:
    if not dsh_enabled():
        raise DshNotConfigured(
            "必须配置 DSH 网关：DSH_GATEWAY_ENABLED=1、DSH_GATEWAY_URL、DSH_GATEWAY_TOKEN（无本地模型回退）"
        )


def _headers() -> Dict[str, str]:
    s = get_settings()
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {s.dsh_gateway_token}",
    }


def _payload(
    *,
    user_id: str,
    conversation_id: str,
    text: str,
    instructions: str = "",
    system: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    mode: str = "chat",
    persona_id: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "userId": user_id,
        "conversationId": conversation_id,
        "text": text,
        "mode": "agent" if mode == "agent" else "chat",
    }
    if instructions.strip():
        payload["instructions"] = instructions.strip()
    if system.strip():
        payload["system"] = system.strip()
    if history:
        payload["history"] = history
    if (persona_id or "").strip():
        payload["personaId"] = persona_id.strip()
    return payload


async def dsh_turn(
    *,
    user_id: str,
    conversation_id: str,
    text: str,
    instructions: str = "",
    system: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    mode: str = "chat",
    persona_id: str = "",
) -> Dict[str, Any]:
    require_dsh()
    s = get_settings()
    base = s.dsh_gateway_url.rstrip("/")
    timeout = httpx.Timeout(s.dsh_gateway_timeout_seconds, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base}/v1/dsh/turn",
            json=_payload(
                user_id=user_id,
                conversation_id=conversation_id,
                text=text,
                instructions=instructions,
                system=system,
                history=history,
                mode=mode,
                persona_id=persona_id,
            ),
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "dsh gateway error")
    return data


async def dsh_interrupt(*, user_id: str, conversation_id: str = "") -> Dict[str, Any]:
    """打断网关当前 agent/会话（用户关机、插话时）。"""
    require_dsh()
    s = get_settings()
    base = s.dsh_gateway_url.rstrip("/")
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base}/v1/dsh/interrupt",
            json={"userId": user_id, "conversationId": conversation_id},
            headers=_headers(),
        )
        if r.status_code >= 400:
            return {"ok": False, "error": r.text[:200]}
        return r.json() if r.content else {"ok": True}


async def dsh_complete(
    *,
    user_id: str,
    conversation_id: str,
    text: str,
    system: str = "",
    instructions: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    persona_id: str = "",
) -> str:
    data = await dsh_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        text=text,
        system=system,
        instructions=instructions,
        history=history,
        persona_id=persona_id,
    )
    return str(data.get("finalResponse") or "").strip()


async def dsh_turn_stream(
    *,
    user_id: str,
    conversation_id: str,
    text: str,
    instructions: str = "",
    system: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    mode: str = "chat",
    persona_id: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    require_dsh()
    s = get_settings()
    base = s.dsh_gateway_url.rstrip("/")
    timeout = httpx.Timeout(s.dsh_gateway_timeout_seconds, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{base}/v1/dsh/turn/stream",
            json=_payload(
                user_id=user_id,
                conversation_id=conversation_id,
                text=text,
                instructions=instructions,
                system=system,
                history=history,
                mode=mode,
                persona_id=persona_id,
            ),
            headers=_headers(),
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"dsh stream {resp.status_code}: {body}")
            event = "message"
            buf = ""
            async for line in resp.aiter_lines():
                if line is None:
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip() or "message"
                elif line.startswith("data:"):
                    buf = line[5:].strip()
                elif line == "" and buf:
                    import json

                    try:
                        data = json.loads(buf)
                    except Exception:
                        data = {"raw": buf}
                    yield {"event": event, "data": data}
                    buf = ""
                    event = "message"
