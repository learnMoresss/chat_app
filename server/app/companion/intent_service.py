"""行动意图：由 followups 演进，带 source=chat|world。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id


class IntentService:
    def create(
        self,
        *,
        user_id: str,
        persona_id: str,
        action_kind: str,
        reason_summary: str = "",
        source: str = "chat",
        trigger_event_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        not_before: str = "",
        expires_at: str = "",
        status: str = "pending",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        iid = new_id("ai_")
        key = idempotency_key or f"{user_id}:{persona_id}:{action_kind}:{iid}"
        now = iso()
        try:
            db.execute(
                """
                INSERT INTO action_intents(
                  id, user_id, persona_id, source, trigger_event_id, action_kind,
                  reason_summary, payload_json, not_before, expires_at, status,
                  cancel_reason, idempotency_key, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'',?,?,?)
                """,
                (
                    iid,
                    user_id,
                    persona_id,
                    source,
                    trigger_event_id,
                    action_kind,
                    reason_summary,
                    json.dumps(payload or {}, ensure_ascii=False),
                    not_before,
                    expires_at,
                    status,
                    key,
                    now,
                    now,
                ),
            )
        except Exception:
            row = db.fetchone(
                "SELECT * FROM action_intents WHERE idempotency_key=?",
                (key,),
            )
            if not row:
                return {"id": iid, "status": status}
            # 已取消/失败/完成的同键允许复活为新一轮机会
            if (row["status"] or "") in ("cancelled", "failed", "done", "expired"):
                now2 = iso()
                db.execute(
                    """
                    UPDATE action_intents SET
                      source=?, trigger_event_id=?, action_kind=?, reason_summary=?,
                      payload_json=?, not_before=?, expires_at=?, status=?,
                      cancel_reason='', updated_at=?
                    WHERE id=?
                    """,
                    (
                        source,
                        trigger_event_id,
                        action_kind,
                        reason_summary,
                        json.dumps(payload or {}, ensure_ascii=False),
                        not_before,
                        expires_at,
                        status,
                        now2,
                        row["id"],
                    ),
                )
                return self.get(user_id, persona_id, row["id"]) or dict(row)
            return dict(row)
        return self.get(user_id, persona_id, iid) or {"id": iid, "status": status}

    def get(self, user_id: str, persona_id: str, intent_id: str) -> Optional[Dict[str, Any]]:
        row = db.fetchone(
            "SELECT * FROM action_intents WHERE id=? AND user_id=? AND persona_id=?",
            (intent_id, user_id, persona_id),
        )
        return dict(row) if row else None

    def list_open(self, user_id: str, persona_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        rows = db.fetchall(
            """
            SELECT * FROM action_intents
            WHERE user_id=? AND persona_id=?
              AND status IN ('pending', 'observed', 'ready', 'executing')
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, persona_id, limit),
        )
        return [dict(r) for r in rows]

    def cancel(
        self,
        user_id: str,
        persona_id: str,
        intent_id: str,
        *,
        reason: str = "",
    ) -> None:
        db.execute(
            """
            UPDATE action_intents
            SET status='cancelled', cancel_reason=?, updated_at=?
            WHERE id=? AND user_id=? AND persona_id=?
              AND status IN ('pending', 'observed', 'ready')
            """,
            (reason, iso(), intent_id, user_id, persona_id),
        )

    def cancel_world_intents(self, user_id: str, persona_id: str, *, reason: str = "worldline_off") -> int:
        now = iso()
        # sqlite rowcount via execute wrapper may not return; count before
        rows = db.fetchall(
            """
            SELECT id FROM action_intents
            WHERE user_id=? AND persona_id=? AND source='world'
              AND status IN ('pending', 'observed', 'ready')
            """,
            (user_id, persona_id),
        )
        for r in rows:
            db.execute(
                """
                UPDATE action_intents
                SET status='cancelled', cancel_reason=?, updated_at=?
                WHERE id=?
                """,
                (reason, now, r["id"]),
            )
        return len(rows)

    def expire_due(self) -> int:
        now = iso()
        rows = db.fetchall(
            """
            SELECT id FROM action_intents
            WHERE status IN ('pending', 'observed', 'ready')
              AND expires_at != '' AND expires_at < ?
            """,
            (now,),
        )
        for r in rows:
            db.execute(
                """
                UPDATE action_intents
                SET status='cancelled', cancel_reason='expired', updated_at=?
                WHERE id=?
                """,
                (now, r["id"]),
            )
        return len(rows)

    def mark(self, intent_id: str, status: str, *, reason: str = "") -> None:
        db.execute(
            """
            UPDATE action_intents
            SET status=?, cancel_reason=?, updated_at=?
            WHERE id=?
            """,
            (status, reason, iso(), intent_id),
        )

    def due_executable(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        now = iso()
        rows = db.fetchall(
            """
            SELECT * FROM action_intents
            WHERE status IN ('pending', 'ready')
              AND (not_before = '' OR not_before <= ?)
              AND (expires_at = '' OR expires_at > ?)
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (now, now, limit),
        )
        return [dict(r) for r in rows]
