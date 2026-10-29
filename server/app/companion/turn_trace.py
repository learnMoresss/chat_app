"""主路径回合过程快照：供世界线监控看板只读回放。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id


def record_turn_trace(
    *,
    user_id: str,
    persona_id: str,
    conv_id: str = "",
    user_text: str = "",
    assistant_text: str = "",
    user_message_id: str = "",
    bot_message_id: str = "",
    explicit: Optional[Dict[str, Any]] = None,
    memories_used: Optional[List[Dict[str, Any]]] = None,
    world_presence: str = "",
    affect_strategy: str = "",
    affect: Optional[Dict[str, Any]] = None,
    snapshot_id: str = "",
    state_version: int = 0,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    tid = new_id("tt_")
    db.execute(
        """
        INSERT INTO companion_turn_traces(
          id, user_id, persona_id, conv_id, user_text, assistant_text,
          user_message_id, bot_message_id, explicit_json, memories_used_json,
          world_presence, affect_strategy, affect_json, snapshot_id,
          state_version, meta_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            tid,
            user_id,
            persona_id,
            conv_id or "",
            (user_text or "")[:4000],
            (assistant_text or "")[:8000],
            user_message_id or "",
            bot_message_id or "",
            json.dumps(explicit or {}, ensure_ascii=False),
            json.dumps(memories_used or [], ensure_ascii=False),
            (world_presence or "")[:2000],
            (affect_strategy or "")[:2000],
            json.dumps(affect or {}, ensure_ascii=False),
            snapshot_id or "",
            int(state_version or 0),
            json.dumps(meta or {}, ensure_ascii=False),
            iso(),
        ),
    )
    # 每人设保留最近 50 条
    try:
        rows = db.fetchall(
            """
            SELECT id FROM companion_turn_traces
            WHERE user_id=? AND persona_id=?
            ORDER BY created_at DESC
            """,
            (user_id, persona_id),
        )
        for r in rows[50:]:
            db.execute("DELETE FROM companion_turn_traces WHERE id=?", (r["id"],))
    except Exception:
        pass
    return tid


def _row_out(row: Any) -> Dict[str, Any]:
    def _j(key: str, default: Any):
        try:
            return json.loads(row[key] or ("{}" if isinstance(default, dict) else "[]"))
        except Exception:
            return default

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "persona_id": row["persona_id"],
        "conv_id": row["conv_id"] or "",
        "user_text": row["user_text"] or "",
        "assistant_text": row["assistant_text"] or "",
        "user_message_id": row["user_message_id"] or "",
        "bot_message_id": row["bot_message_id"] or "",
        "explicit": _j("explicit_json", {}),
        "memories_used": _j("memories_used_json", []),
        "world_presence": row["world_presence"] or "",
        "affect_strategy": row["affect_strategy"] or "",
        "affect": _j("affect_json", {}),
        "snapshot_id": row["snapshot_id"] or "",
        "state_version": int(row["state_version"] or 0),
        "meta": _j("meta_json", {}),
        "created_at": row["created_at"] or "",
    }


def list_turn_traces(
    user_id: str,
    persona_id: str,
    *,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT * FROM companion_turn_traces
        WHERE user_id=? AND persona_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, persona_id, max(1, min(50, int(limit)))),
    )
    return [_row_out(r) for r in rows]


def latest_turn_trace(user_id: str, persona_id: str) -> Optional[Dict[str, Any]]:
    rows = list_turn_traces(user_id, persona_id, limit=1)
    return rows[0] if rows else None
