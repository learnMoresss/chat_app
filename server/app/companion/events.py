"""统一陪伴事件流。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .. import db
from ..security import iso, new_id


def next_seq(user_id: str, persona_id: str) -> int:
    row = db.fetchone(
        "SELECT MAX(seq) AS m FROM companion_events WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    try:
        return int(row["m"] or 0) + 1 if row else 1
    except Exception:
        return 1


def emit_event(
    *,
    user_id: str,
    persona_id: str,
    kind: str,
    source: str = "chat",
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    eid = new_id("cev_")
    seq = next_seq(user_id, persona_id)
    db.execute(
        """
        INSERT INTO companion_events(id, user_id, persona_id, kind, source, payload_json, seq, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            eid,
            user_id,
            persona_id,
            kind,
            source,
            json.dumps(payload or {}, ensure_ascii=False),
            seq,
            iso(),
        ),
    )
    return eid
