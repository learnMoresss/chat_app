"""短期情绪：锚点 + 时间衰减；提案经校验后提交。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id

DIMS = ("joy", "sadness", "excitement", "worry", "annoyance", "curiosity")
DEFAULT_BASELINE = {d: 35 for d in DIMS}
HALF_LIFE_MINUTES = 40.0
MAX_DELTA_NORMAL = 10
MAX_DELTA_IMPORTANT = 25


def _parse_ts(s: str) -> datetime:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except Exception:
        return datetime.now(timezone.utc)


def _decay(value: float, baseline: float, heat_at: str, half_life_min: float = HALF_LIFE_MINUTES) -> float:
    t0 = _parse_ts(heat_at)
    now = datetime.now(timezone.utc)
    elapsed_min = max(0.0, (now - t0).total_seconds() / 60.0)
    return baseline + (value - baseline) * (2 ** (-elapsed_min / max(1.0, half_life_min)))


class AffectService:
    def get_or_init(self, user_id: str, persona_id: str) -> Dict[str, Any]:
        row = db.fetchone(
            "SELECT * FROM persona_affect WHERE user_id = ? AND persona_id = ?",
            (user_id, persona_id),
        )
        if row:
            return self.current_view(dict(row))
        now = iso()
        vals = {**DEFAULT_BASELINE}
        db.execute(
            """
            INSERT INTO persona_affect(
              user_id, persona_id, joy, sadness, excitement, worry, annoyance, curiosity,
              baseline_json, heat_at, state_version, reason, source_event_id, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                persona_id,
                vals["joy"],
                vals["sadness"],
                vals["excitement"],
                vals["worry"],
                vals["annoyance"],
                vals["curiosity"],
                "{}",
                now,
                1,
                "init",
                "",
                now,
            ),
        )
        return self.get_or_init(user_id, persona_id)

    def current_view(self, row: Dict[str, Any]) -> Dict[str, Any]:
        heat_at = row.get("heat_at") or iso()
        out = {
            "user_id": row["user_id"],
            "persona_id": row["persona_id"],
            "state_version": int(row.get("state_version") or 1),
            "heat_at": heat_at,
            "reason": row.get("reason") or "",
            "source_event_id": row.get("source_event_id") or "",
            "updated_at": row.get("updated_at") or "",
        }
        for d in DIMS:
            base = float(DEFAULT_BASELINE[d])
            raw = float(row.get(d) or base)
            out[d] = int(round(max(0, min(100, _decay(raw, base, heat_at)))))
        return out

    def propose(
        self,
        *,
        user_id: str,
        persona_id: str,
        deltas: Dict[str, float],
        event_id: str,
        reason: str = "",
        expected_version: Optional[int] = None,
        important: bool = False,
    ) -> Dict[str, Any]:
        cur = self.get_or_init(user_id, persona_id)
        if expected_version is not None and int(cur["state_version"]) != int(expected_version):
            raise ValueError("affect_version_conflict")
        if event_id:
            dup = db.fetchone(
                """
                SELECT 1 FROM persona_affect_events
                WHERE user_id = ? AND persona_id = ? AND event_id = ?
                """,
                (user_id, persona_id, event_id),
            )
            if dup:
                return cur

        cap = MAX_DELTA_IMPORTANT if important else MAX_DELTA_NORMAL
        row = db.fetchone(
            "SELECT * FROM persona_affect WHERE user_id = ? AND persona_id = ?",
            (user_id, persona_id),
        )
        assert row
        heat_at = row["heat_at"] or iso()
        new_vals = {}
        for d in DIMS:
            base = float(DEFAULT_BASELINE[d])
            decayed = _decay(float(row[d] or base), base, heat_at)
            delta = float(deltas.get(d) or 0.0)
            delta = max(-cap, min(cap, delta))
            new_vals[d] = max(0.0, min(100.0, decayed + delta))

        now = iso()
        ver = int(row["state_version"] or 1) + 1
        db.execute(
            """
            UPDATE persona_affect SET
              joy=?, sadness=?, excitement=?, worry=?, annoyance=?, curiosity=?,
              heat_at=?, state_version=?, reason=?, source_event_id=?, updated_at=?
            WHERE user_id=? AND persona_id=?
            """,
            (
                new_vals["joy"],
                new_vals["sadness"],
                new_vals["excitement"],
                new_vals["worry"],
                new_vals["annoyance"],
                new_vals["curiosity"],
                now,
                ver,
                (reason or "")[:200],
                event_id or "",
                now,
                user_id,
                persona_id,
            ),
        )
        db.execute(
            """
            INSERT INTO persona_affect_events(
              id, user_id, persona_id, event_id, deltas_json, reason, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                new_id("ae_"),
                user_id,
                persona_id,
                event_id or new_id("ev_"),
                json.dumps(deltas, ensure_ascii=False),
                (reason or "")[:200],
                now,
            ),
        )
        return self.get_or_init(user_id, persona_id)

    def reset(self, user_id: str, persona_id: str) -> Dict[str, Any]:
        now = iso()
        db.execute("DELETE FROM persona_affect WHERE user_id=? AND persona_id=?", (user_id, persona_id))
        return self.get_or_init(user_id, persona_id)

    def history(self, user_id: str, persona_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        rows = db.fetchall(
            """
            SELECT id, event_id, deltas_json, reason, created_at
            FROM persona_affect_events
            WHERE user_id=? AND persona_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, persona_id, limit),
        )
        return [dict(r) for r in rows]
