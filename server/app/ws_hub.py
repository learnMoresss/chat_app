from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

# 主事件循环：同步代码（线程池里的路由）用 run_coroutine_threadsafe 推 WS
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _main_loop


class Hub:
    def __init__(self) -> None:
        self._rooms: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        # 调用方须已 accept；此处只登记房间，避免二次 accept 崩连接
        async with self._lock:
            self._rooms.setdefault(user_id, set()).add(ws)

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            sockets = self._rooms.get(user_id)
            if not sockets:
                return
            sockets.discard(ws)
            if not sockets:
                self._rooms.pop(user_id, None)

    async def send_user(self, user_id: str, event: str, payload: Dict[str, Any]) -> None:
        message = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        async with self._lock:
            sockets = list(self._rooms.get(user_id, set()))
        dead: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)


hub = Hub()
