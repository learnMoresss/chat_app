"""中文词面检索 + 可选向量位；空召回合法，禁止无关兜底。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from .. import db
from .budget import ContextBudgeter
from .store import MemoryStore


def _tokens(text: str) -> Set[str]:
    parts = re.findall(r"[\u4e00-\u9fff]{1,}|[a-zA-Z0-9_]{2,}", (text or "").lower())
    out: Set[str] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out.add(p)
        # 两字短词：对 CJK 长串做 bigram，缓解 FTS 中文分词不足
        if len(p) >= 2 and all("\u4e00" <= ch <= "\u9fff" for ch in p):
            for i in range(len(p) - 1):
                out.add(p[i : i + 2])
    return out


class MemoryRetriever:
    def __init__(self, store: MemoryStore | None = None, budgeter: ContextBudgeter | None = None) -> None:
        self.store = store or MemoryStore()
        self.budgeter = budgeter or ContextBudgeter()

    def lexical(
        self,
        user_id: str,
        persona_id: str,
        query: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        # FTS5 trigram / 简单 MATCH；失败则回退 token 重叠（仍不兜底最近条）
        fts_hits: List[Dict[str, Any]] = []
        try:
            # 短词用引号短语；构造安全 query
            terms = [t for t in _tokens(q) if len(t) >= 2][:12]
            if terms:
                # OR 连接
                match = " OR ".join(f'"{t}"' for t in terms)
                rows = db.fetchall(
                    """
                    SELECT memory_id FROM memory_facts_fts
                    WHERE memory_facts_fts MATCH ?
                      AND user_id = ? AND persona_id = ?
                    LIMIT ?
                    """,
                    (match, user_id, persona_id, limit),
                )
                for r in rows:
                    full = self.store.get(user_id, persona_id, r["memory_id"])
                    if full and full.get("status") in ("active", "legacy_unverified", "candidate"):
                        if not self.store.is_source_suppressed(user_id, persona_id, full["id"]):
                            fts_hits.append(full)
        except Exception:
            fts_hits = []

        if fts_hits:
            return fts_hits[:limit]

        # FTS 未命中：不再全表扫 500；空召回合法
        return []

    def hot_preload(self, user_id: str, persona_id: str, *, limit: int = 3) -> List[Dict[str, Any]]:
        rows = self.store.list_active(user_id, persona_id, limit=80)
        ranked = sorted(
            (
                r
                for r in rows
                if not self.store.is_source_suppressed(user_id, persona_id, r["id"])
                and r.get("status") in ("active", "legacy_unverified")
            ),
            key=lambda r: self.store.effective_heat(r),
            reverse=True,
        )
        # 进入热区：>=60 且默认至少返回 top；不足则空
        hot = [r for r in ranked if self.store.effective_heat(r) >= 40][:limit]
        return hot

    def retrieve_for_turn(
        self,
        user_id: str,
        persona_id: str,
        query: str,
    ) -> tuple[List[Dict[str, Any]], Any]:
        hot = self.hot_preload(user_id, persona_id)
        recalled = self.lexical(user_id, persona_id, query, limit=20)
        packed, report = self.budgeter.pack_memories(hot, recalled)
        return packed, report
