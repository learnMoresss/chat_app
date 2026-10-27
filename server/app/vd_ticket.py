"""DSH 轮次隔离票据：gateway 写入，API 校验，防止伪造 X-Chat-User-Id。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _ticket_dir() -> Path:
    home = os.environ.get("DSH_HOME") or os.environ.get("CHAT_DSH_HOME") or ""
    if home:
        return Path(home) / "product-vd" / "tickets"
    # 与 gateway 默认 / systemd Environment 一致
    candidates = [
        Path.home() / "chat_app" / "dsh-product" / ".dsh-home" / "product-vd" / "tickets",
        Path("/home/joy/chat_app/dsh-product/.dsh-home/product-vd/tickets"),
    ]
    for p in candidates:
        if p.is_dir() or p.parent.is_dir():
            return p
    return candidates[0]


def resolve_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    raw = (ticket or "").strip()
    if not raw or len(raw) < 16 or "/" in raw or ".." in raw:
        return None
    path = _ticket_dir() / f"{raw}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    exp = float(data.get("exp") or 0)
    if exp and time.time() > exp:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    uid = str(data.get("userId") or "").strip()
    if not uid:
        return None
    return {
        "user_id": uid,
        "persona_id": str(data.get("personaId") or "").strip(),
        "conv_id": str(data.get("conversationId") or "").strip(),
    }
