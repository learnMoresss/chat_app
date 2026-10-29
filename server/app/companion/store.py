"""事实记忆权威库：类型、来源、修订、纠错、抑制、温度锚点。"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .. import db
from ..security import iso, new_id
from .cjk_fts import fts_index_body

# 阶段5：默认关闭旧 memories 双写；设 MEMORY_LEGACY_DUAL_WRITE=1 可临时打开
_DUAL_WRITE = (os.environ.get("MEMORY_LEGACY_DUAL_WRITE") or "0").strip() in (
    "1",
    "true",
    "yes",
)

STATUSES = ("candidate", "active", "superseded", "expired", "deleted", "legacy_unverified")
TYPES = ("fact", "preference", "event", "lesson", "inference")


def _now() -> str:
    return iso()


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


class MemoryStore:
    """短事务提交；模型提案不直接持有写锁外的连接。"""

    def ensure_legacy_row_migrated(self, row: Any) -> Dict[str, Any]:
        """旧 memories 行视为 legacy_unverified 事实。"""
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "persona_id": row["persona_id"],
            "text": row["text"],
            "mem_type": "fact",
            "status": "legacy_unverified",
            "subject": "",
            "attribute": "",
            "source_message_id": "",
            "source_revision": 0,
            "root_evidence_id": row["id"],
            "fact_period_id": row["id"],
            "storage_revision": 1,
            "heat_anchor": 20.0,
            "heat_at": row["updated_at"] if "updated_at" in row.keys() else _now(),
            "valid_from": row["updated_at"] if "updated_at" in row.keys() else _now(),
            "valid_to": "",
            "supersedes": "",
            "meta_json": "{}",
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else _now(),
            "created_at": row["updated_at"] if "updated_at" in row.keys() else _now(),
        }

    def get(self, user_id: str, persona_id: str, memory_id: str) -> Optional[Dict[str, Any]]:
        row = db.fetchone(
            """
            SELECT * FROM memory_facts
            WHERE id = ? AND user_id = ? AND persona_id = ?
            """,
            (memory_id, user_id, persona_id),
        )
        if row:
            return dict(row)
        legacy = db.fetchone(
            "SELECT * FROM memories WHERE id = ? AND user_id = ? AND persona_id = ?",
            (memory_id, user_id, persona_id),
        )
        return self.ensure_legacy_row_migrated(legacy) if legacy else None

    def list_active(
        self,
        user_id: str,
        persona_id: str,
        *,
        limit: int = 100,
        include_legacy: bool = True,
    ) -> List[Dict[str, Any]]:
        rows = db.fetchall(
            """
            SELECT * FROM memory_facts
            WHERE user_id = ? AND persona_id = ?
              AND status IN ('active', 'legacy_unverified', 'candidate')
            ORDER BY updated_at DESC LIMIT ?
            """,
            (user_id, persona_id, limit),
        )
        out = [dict(r) for r in rows]
        if include_legacy and len(out) < limit:
            # 尚未迁移的旧表
            ids = {x["id"] for x in out}
            legacy = db.fetchall(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND persona_id = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (user_id, persona_id, limit),
            )
            for r in legacy:
                if r["id"] in ids:
                    continue
                out.append(self.ensure_legacy_row_migrated(r))
                if len(out) >= limit:
                    break
        return out

    def create(
        self,
        *,
        user_id: str,
        persona_id: str,
        text: str,
        mem_type: str = "fact",
        subject: str = "",
        attribute: str = "",
        source_message_id: str = "",
        source_revision: int = 1,
        status: str = "active",
        policy_version: int = 0,
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("记忆不能为空")
        if mem_type not in TYPES:
            mem_type = "fact"
        if status not in STATUSES:
            status = "active"
        mid = new_id("mf_")
        period = mid
        now = _now()
        db.execute(
            """
            INSERT INTO memory_facts(
              id, user_id, persona_id, text, mem_type, status, subject, attribute,
              source_message_id, source_revision, root_evidence_id, fact_period_id,
              storage_revision, heat_anchor, heat_at, valid_from, valid_to, supersedes,
              meta_json, policy_version, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mid,
                user_id,
                persona_id,
                text,
                mem_type,
                status,
                subject,
                attribute,
                source_message_id,
                int(source_revision),
                mid,
                period,
                1,
                20.0,
                now,
                now,
                "",
                "",
                "{}",
                int(policy_version),
                now,
                now,
            ),
        )
        # 可选双写旧表（默认关）
        if _DUAL_WRITE:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO memories(id, user_id, persona_id, text, updated_at) VALUES (?,?,?,?,?)",
                    (mid, user_id, persona_id, text, now),
                )
            except Exception:
                pass
        self._fts_upsert(mid, user_id, persona_id, text)
        return self.get(user_id, persona_id, mid) or {"id": mid, "text": text}

    def correct(
        self,
        *,
        user_id: str,
        persona_id: str,
        memory_id: str,
        new_text: str,
        expected_revision: Optional[int] = None,
        policy_version: int = 0,
    ) -> Dict[str, Any]:
        """明确纠正：结束旧 period，新建 active。"""
        old = self.get(user_id, persona_id, memory_id)
        if not old:
            raise KeyError("资产不存在")
        if expected_revision is not None and int(old.get("storage_revision") or 1) != int(expected_revision):
            raise ConflictError("revision_conflict")
        now = _now()
        # 旧行若在 memory_facts
        exists = db.fetchone("SELECT id FROM memory_facts WHERE id = ?", (memory_id,))
        if exists:
            db.execute(
                """
                UPDATE memory_facts SET status = 'superseded', valid_to = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, memory_id),
            )
        new = self.create(
            user_id=user_id,
            persona_id=persona_id,
            text=new_text,
            mem_type=old.get("mem_type") or "fact",
            subject=old.get("subject") or "",
            attribute=old.get("attribute") or "",
            source_message_id=old.get("source_message_id") or "",
            source_revision=int(old.get("source_revision") or 1) + 1,
            status="active",
            policy_version=policy_version,
        )
        db.execute(
            "UPDATE memory_facts SET supersedes = ? WHERE id = ?",
            (memory_id, new["id"]),
        )
        self._suppress_source(user_id, persona_id, memory_id, reason="corrected")
        return self.get(user_id, persona_id, new["id"]) or new

    def delete_hard(
        self,
        *,
        user_id: str,
        persona_id: str,
        memory_id: str,
        policy_version: int = 0,
    ) -> bool:
        row = self.get(user_id, persona_id, memory_id)
        if not row:
            return False
        now = _now()
        db.execute(
            """
            UPDATE memory_facts SET status = 'deleted', updated_at = ?, policy_version = ?
            WHERE id = ? AND user_id = ? AND persona_id = ?
            """,
            (now, int(policy_version), memory_id, user_id, persona_id),
        )
        db.execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ? AND persona_id = ?",
            (memory_id, user_id, persona_id),
        )
        self._suppress_source(user_id, persona_id, memory_id, reason="deleted")
        self._fts_delete(memory_id)
        return True

    def effective_heat(self, row: Dict[str, Any], *, half_life_days: float = 7.0) -> float:
        try:
            anchor = float(row.get("heat_anchor") or 20.0)
            heat_at = row.get("heat_at") or _now()
            # parse ISO
            try:
                t0 = datetime.fromisoformat(str(heat_at).replace("Z", "+00:00"))
            except Exception:
                return anchor
            now = datetime.now(timezone.utc)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=timezone.utc)
            elapsed_days = max(0.0, (now - t0).total_seconds() / 86400.0)
            return _clamp(anchor * (2 ** (-elapsed_days / max(0.1, half_life_days))))
        except Exception:
            return float(row.get("heat_anchor") or 20.0)

    def apply_heat(
        self,
        *,
        user_id: str,
        persona_id: str,
        memory_id: str,
        gain: float,
        root_fragment_id: str,
        policy_version: int = 0,
    ) -> Dict[str, Any]:
        """合格加热；同 fragment 幂等。"""
        row = self.get(user_id, persona_id, memory_id)
        if not row or row.get("status") in ("deleted", "superseded", "expired"):
            raise KeyError("not_active")
        # 幂等
        existed = db.fetchone(
            """
            SELECT 1 FROM memory_heat_events
            WHERE user_id = ? AND persona_id = ? AND fact_period_id = ? AND root_fragment_id = ?
            """,
            (user_id, persona_id, row.get("fact_period_id") or memory_id, root_fragment_id),
        )
        if existed:
            return row
        eff = self.effective_heat(row)
        new_heat = _clamp(eff + float(gain))
        now = _now()
        db.execute(
            """
            INSERT INTO memory_heat_events(
              id, user_id, persona_id, fact_period_id, root_fragment_id, gain, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                new_id("he_"),
                user_id,
                persona_id,
                row.get("fact_period_id") or memory_id,
                root_fragment_id,
                float(gain),
                now,
            ),
        )
        if db.fetchone("SELECT id FROM memory_facts WHERE id = ?", (memory_id,)):
            db.execute(
                """
                UPDATE memory_facts SET heat_anchor = ?, heat_at = ?, updated_at = ?, policy_version = ?
                WHERE id = ?
                """,
                (new_heat, now, now, int(policy_version), memory_id),
            )
        return self.get(user_id, persona_id, memory_id) or row

    def is_source_suppressed(self, user_id: str, persona_id: str, source_id: str) -> bool:
        row = db.fetchone(
            """
            SELECT 1 FROM memory_source_suppress
            WHERE user_id = ? AND persona_id = ? AND source_id = ?
            """,
            (user_id, persona_id, source_id),
        )
        return bool(row)

    def _suppress_source(self, user_id: str, persona_id: str, source_id: str, reason: str) -> None:
        db.execute(
            """
            INSERT OR REPLACE INTO memory_source_suppress(
              user_id, persona_id, source_id, reason, created_at
            ) VALUES (?,?,?,?,?)
            """,
            (user_id, persona_id, source_id, reason, _now()),
        )

    def _fts_upsert(self, mid: str, user_id: str, persona_id: str, text: str) -> None:
        try:
            body = fts_index_body(text)
            db.execute("DELETE FROM memory_facts_fts WHERE memory_id = ?", (mid,))
            db.execute(
                """
                INSERT INTO memory_facts_fts(memory_id, user_id, persona_id, body)
                VALUES (?,?,?,?)
                """,
                (mid, user_id, persona_id, body),
            )
        except Exception:
            pass

    def _fts_delete(self, mid: str) -> None:
        try:
            db.execute("DELETE FROM memory_facts_fts WHERE memory_id = ?", (mid,))
        except Exception:
            pass


class ConflictError(Exception):
    pass
