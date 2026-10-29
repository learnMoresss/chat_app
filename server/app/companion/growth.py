"""人格成长：三模式 + 修订 + epoch。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id

MODES = ("adaptive", "initial_locked", "snapshot_locked")
TRAITS = ("extraversion", "playfulness", "exploration", "proactivity")


class GrowthService:
    def get_state(self, user_id: str, persona_id: str) -> Dict[str, Any]:
        row = db.fetchone(
            "SELECT * FROM persona_growth WHERE user_id=? AND persona_id=?",
            (user_id, persona_id),
        )
        if not row:
            return self._init(user_id, persona_id)
        return self._view(dict(row))

    def _init(self, user_id: str, persona_id: str) -> Dict[str, Any]:
        persona = db.fetchone(
            "SELECT name, bio FROM personas WHERE id=? AND user_id=?",
            (persona_id, user_id),
        )
        bio = (persona["bio"] if persona else "") or ""
        now = iso()
        traits = {t: 50 for t in TRAITS}
        db.execute(
            """
            INSERT INTO persona_growth(
              user_id, persona_id, mode, growth_epoch, baseline_bio, traits_json,
              active_revision_id, frozen_lesson_ids_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                persona_id,
                "adaptive",
                1,
                bio,
                json.dumps(traits, ensure_ascii=False),
                "",
                "[]",
                now,
            ),
        )
        return self.get_state(user_id, persona_id)

    def _view(self, row: Dict[str, Any]) -> Dict[str, Any]:
        try:
            traits = json.loads(row.get("traits_json") or "{}")
        except Exception:
            traits = {t: 50 for t in TRAITS}
        try:
            lessons = json.loads(row.get("frozen_lesson_ids_json") or "[]")
        except Exception:
            lessons = []
        return {
            "user_id": row["user_id"],
            "persona_id": row["persona_id"],
            "mode": row.get("mode") or "adaptive",
            "growth_epoch": int(row.get("growth_epoch") or 1),
            "baseline_bio": row.get("baseline_bio") or "",
            "traits": traits,
            "active_revision_id": row.get("active_revision_id") or "",
            "frozen_lesson_ids": lessons,
            "updated_at": row.get("updated_at") or "",
        }

    def set_mode(
        self,
        user_id: str,
        persona_id: str,
        mode: str,
        *,
        freeze_current: bool = False,
    ) -> Dict[str, Any]:
        st = self.get_state(user_id, persona_id)
        if mode not in MODES:
            raise ValueError("bad_mode")
        # 关主开关语义：initial_locked
        now = iso()
        epoch = int(st["growth_epoch"]) + 1
        frozen = st.get("frozen_lesson_ids") or []
        if freeze_current or mode == "snapshot_locked":
            # 冻结当前 traits 为一个 revision
            rid = new_id("gr_")
            db.execute(
                """
                INSERT INTO persona_growth_revisions(
                  id, user_id, persona_id, traits_json, lesson_ids_json, note, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    rid,
                    user_id,
                    persona_id,
                    json.dumps(st["traits"], ensure_ascii=False),
                    json.dumps(frozen, ensure_ascii=False),
                    "snapshot_lock",
                    now,
                ),
            )
            active = rid
            if mode != "snapshot_locked":
                mode = "snapshot_locked"
        elif mode == "initial_locked":
            active = ""
            frozen = []
        else:
            active = st.get("active_revision_id") or ""

        db.execute(
            """
            UPDATE persona_growth SET mode=?, growth_epoch=?, active_revision_id=?,
              frozen_lesson_ids_json=?, updated_at=?
            WHERE user_id=? AND persona_id=?
            """,
            (
                mode,
                epoch,
                active,
                json.dumps(frozen, ensure_ascii=False),
                now,
                user_id,
                persona_id,
            ),
        )
        return self.get_state(user_id, persona_id)

    def effective_traits(self, user_id: str, persona_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        st = self.get_state(user_id, persona_id)
        if st["mode"] == "initial_locked":
            return {t: 50 for t in TRAITS}, st
        if st["mode"] == "snapshot_locked" and st.get("active_revision_id"):
            rev = db.fetchone(
                "SELECT traits_json FROM persona_growth_revisions WHERE id=?",
                (st["active_revision_id"],),
            )
            if rev:
                try:
                    return json.loads(rev["traits_json"] or "{}"), st
                except Exception:
                    pass
        return st.get("traits") or {t: 50 for t in TRAITS}, st

    def apply_candidate(
        self,
        *,
        user_id: str,
        persona_id: str,
        trait_deltas: Dict[str, float],
        evidence_ids: List[str],
        note: str = "",
        epoch: Optional[int] = None,
    ) -> Dict[str, Any]:
        st = self.get_state(user_id, persona_id)
        if st["mode"] != "adaptive":
            raise PermissionError("growth_locked")
        if epoch is not None and int(epoch) != int(st["growth_epoch"]):
            raise ValueError("growth_epoch_conflict")
        if len(set(evidence_ids or [])) < 3:
            raise ValueError("need_three_evidence_roots")
        traits = dict(st.get("traits") or {})
        for t, d in (trait_deltas or {}).items():
            if t not in TRAITS:
                continue
            # 单维日变更粗限 ±3
            d = max(-3.0, min(3.0, float(d)))
            base = float(traits.get(t, 50))
            # 偏离初始 50 最多 20
            nxt = max(30.0, min(70.0, base + d))
            traits[t] = nxt
        now = iso()
        rid = new_id("gr_")
        db.execute(
            """
            INSERT INTO persona_growth_revisions(
              id, user_id, persona_id, traits_json, lesson_ids_json, note, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                rid,
                user_id,
                persona_id,
                json.dumps(traits, ensure_ascii=False),
                json.dumps(evidence_ids, ensure_ascii=False),
                (note or "")[:300],
                now,
            ),
        )
        db.execute(
            """
            UPDATE persona_growth SET traits_json=?, active_revision_id=?, updated_at=?
            WHERE user_id=? AND persona_id=?
            """,
            (json.dumps(traits, ensure_ascii=False), rid, now, user_id, persona_id),
        )
        # 证据修订链（阶段5）：before/after + evidence_ids 可撤回
        try:
            db.execute(
                """
                INSERT INTO growth_evidence_revisions(
                  id, user_id, persona_id, before_json, after_json, evidence_ids_json, note, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    new_id("ger_"),
                    user_id,
                    persona_id,
                    json.dumps(st.get("traits") or {}, ensure_ascii=False),
                    json.dumps(traits, ensure_ascii=False),
                    json.dumps(evidence_ids or [], ensure_ascii=False),
                    (note or "")[:300],
                    now,
                ),
            )
        except Exception:
            pass
        return self.get_state(user_id, persona_id)

    def rollback(self, user_id: str, persona_id: str, revision_id: str = "") -> Dict[str, Any]:
        st = self.get_state(user_id, persona_id)
        now = iso()
        epoch = int(st["growth_epoch"]) + 1
        if not revision_id:
            # 回初始
            traits = {t: 50 for t in TRAITS}
            db.execute(
                """
                UPDATE persona_growth SET mode='adaptive', growth_epoch=?, traits_json=?,
                  active_revision_id='', frozen_lesson_ids_json='[]', updated_at=?
                WHERE user_id=? AND persona_id=?
                """,
                (epoch, json.dumps(traits, ensure_ascii=False), now, user_id, persona_id),
            )
            return self.get_state(user_id, persona_id)
        rev = db.fetchone(
            "SELECT * FROM persona_growth_revisions WHERE id=? AND user_id=? AND persona_id=?",
            (revision_id, user_id, persona_id),
        )
        if not rev:
            raise KeyError("revision_not_found")
        db.execute(
            """
            UPDATE persona_growth SET growth_epoch=?, traits_json=?, active_revision_id=?, updated_at=?
            WHERE user_id=? AND persona_id=?
            """,
            (epoch, rev["traits_json"], revision_id, now, user_id, persona_id),
        )
        return self.get_state(user_id, persona_id)
