"""结构化跟进意图 + outbox 幂等。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id

STATES = (
    "pending",
    "awakened",
    "silent",
    "queued",
    "persisted",
    "delivered",
    "failed",
    "cancelled",
    "expired",
)


class FollowupService:
    def create(
        self,
        *,
        user_id: str,
        persona_id: str,
        reason: str,
        source: str = "",
        window_start: str = "",
        window_end: str = "",
        expire_at: str = "",
        cancel_when: str = "",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        if idempotency_key:
            exist = db.fetchone(
                "SELECT * FROM companion_followups WHERE idempotency_key=?",
                (idempotency_key,),
            )
            if exist:
                return dict(exist)
        fid = new_id("fu_")
        now = iso()
        key = idempotency_key or fid
        db.execute(
            """
            INSERT INTO companion_followups(
              id, user_id, persona_id, reason, source, window_start, window_end,
              expire_at, cancel_when, state, idempotency_key, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fid,
                user_id,
                persona_id,
                (reason or "")[:500],
                (source or "")[:200],
                window_start,
                window_end,
                expire_at,
                cancel_when,
                "pending",
                key,
                now,
                now,
            ),
        )
        return dict(db.fetchone("SELECT * FROM companion_followups WHERE id=?", (fid,)))

    def list_open(self, user_id: str, persona_id: str) -> List[Dict[str, Any]]:
        rows = db.fetchall(
            """
            SELECT * FROM companion_followups
            WHERE user_id=? AND persona_id=?
              AND state IN ('pending','awakened','queued','persisted')
            ORDER BY created_at DESC LIMIT 50
            """,
            (user_id, persona_id),
        )
        return [dict(r) for r in rows]

    def cancel(self, user_id: str, persona_id: str, followup_id: str) -> bool:
        row = db.fetchone(
            "SELECT id FROM companion_followups WHERE id=? AND user_id=? AND persona_id=?",
            (followup_id, user_id, persona_id),
        )
        if not row:
            return False
        db.execute(
            "UPDATE companion_followups SET state='cancelled', updated_at=? WHERE id=?",
            (iso(), followup_id),
        )
        return True

    def mark(self, followup_id: str, state: str) -> None:
        if state not in STATES:
            return
        db.execute(
            "UPDATE companion_followups SET state=?, updated_at=? WHERE id=?",
            (state, iso(), followup_id),
        )

    def expire_due(self) -> int:
        """过期 pending → expired；返回变更数。"""
        now = iso()
        rows = db.fetchall(
            """
            SELECT id FROM companion_followups
            WHERE state = 'pending' AND expire_at != '' AND expire_at <= ?
            """,
            (now,),
        )
        n = 0
        for r in rows:
            db.execute(
                "UPDATE companion_followups SET state='expired', updated_at=? WHERE id=?",
                (now, r["id"]),
            )
            n += 1
        return n

    def on_device_task(
        self,
        *,
        user_id: str,
        persona_id: str,
        event: str,
        task_key: str,
        ok: bool,
    ) -> None:
        """设备任务开始/成功/失败：失败不写成功记忆；可取消 cancel_when 匹配的跟进。"""
        if event == "failed" or not ok:
            # 取消依赖该任务成功的跟进
            rows = db.fetchall(
                """
                SELECT id, cancel_when FROM companion_followups
                WHERE user_id=? AND persona_id=?
                  AND state IN ('pending','awakened','queued')
                """,
                (user_id, persona_id),
            )
            for r in rows:
                cw = r["cancel_when"] or ""
                if task_key and task_key in cw:
                    self.cancel(user_id, persona_id, r["id"])
            return
        if event == "success" and task_key:
            # 幂等：同任务成功只建一条跟进提醒
            self.create(
                user_id=user_id,
                persona_id=persona_id,
                reason=f"任务完成跟进：{task_key}",
                source=f"device:{task_key}",
                idempotency_key=f"task-ok:{user_id}:{persona_id}:{task_key}",
            )
