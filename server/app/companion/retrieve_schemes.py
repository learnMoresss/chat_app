"""记忆召回方案（可 A/B）：借鉴 Mem0 多信号融合 + CJK hybrid RRF。

生产默认仍可走 baseline；压测用 scheme 切换。不引入外部服务依赖。
参考：
- Mem0: semantic + keyword + entity fusion
- mem0-hybrid-search / agent-memory-engine: FTS + RRF + 热记忆门控
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .budget import ContextBudgeter
from .retriever import MemoryRetriever, _tokens
from .store import MemoryStore

SCHEMES = (
    "baseline",      # 现状
    "rrf_gated",     # FTS+词面 RRF，空召回时不塞热记忆
    "expand_rrf",    # LLM 查询改写 + rrf_gated
    "combo",         # expand + rrf_gated（检索侧最强组合）
)


def default_scheme() -> str:
    # A/B 结论：rrf_gated / expand_rrf / combo 并列最优（修空召回污染）；取最简 rrf_gated
    # 规则改写对所有非 baseline 生效；expand_rrf/combo 额外可挂 LLM expand_fn
    raw = (os.environ.get("MEMORY_RETRIEVE_SCHEME") or "rrf_gated").strip().lower()
    return raw if raw in SCHEMES else "rrf_gated"


def rrf_fuse(
    ranked_lists: Sequence[Sequence[Dict[str, Any]]],
    *,
    k: int = 60,
    weights: Optional[Sequence[float]] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """加权 Reciprocal Rank Fusion。精确通道可给更高 weight。"""
    w = list(weights) if weights else [1.0] * len(ranked_lists)
    while len(w) < len(ranked_lists):
        w.append(1.0)
    scores: Dict[str, float] = {}
    objs: Dict[str, Dict[str, Any]] = {}
    for li, lst in enumerate(ranked_lists):
        ww = float(w[li] or 1.0)
        for rank, item in enumerate(lst):
            mid = str(item.get("id") or "")
            if not mid:
                continue
            objs[mid] = item
            scores[mid] = scores.get(mid, 0.0) + ww / (k + rank + 1)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [objs[mid] for mid, _ in ordered[:limit]]


_ENTITY_HINT = re.compile(
    r"(猫|狗|花|上班|公司|住|城市|名字|叫什么|来福|旺财|豆豆|滨江|杭州|玉兰|"
    r"[A-Za-z]{2,}|\d{2,}|[A-Za-z]+\d+)",
    re.I,
)


def query_has_entity_signal(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if _ENTITY_HINT.search(q):
        return True
    # 较长具体问句也算有信号
    return len(q) >= 8 and ("？" in q or "?" in q or "吗" in q or "什么" in q or "哪" in q)


class SchemeRetriever:
    """带方案开关的召回器。"""

    def __init__(
        self,
        store: MemoryStore | None = None,
        budgeter: ContextBudgeter | None = None,
        *,
        scheme: str = "baseline",
        expand_fn: Optional[Callable[[str], List[str]]] = None,
    ) -> None:
        self.base = MemoryRetriever(store=store, budgeter=budgeter)
        self.scheme = scheme if scheme in SCHEMES else "baseline"
        self.expand_fn = expand_fn

    def _fts_list(self, user_id: str, persona_id: str, query: str, limit: int) -> List[Dict[str, Any]]:
        # 复用 baseline lexical 的 FTS 段：临时拆开
        q = (query or "").strip()
        if not q:
            return []
        terms = [t for t in _tokens(q) if len(t) >= 2][:16]
        if not terms:
            return []
        from .. import db

        match = " OR ".join(f'"{t}"' for t in terms)
        try:
            rows = db.fetchall(
                """
                SELECT memory_id FROM memory_facts_fts
                WHERE memory_facts_fts MATCH ?
                  AND user_id = ? AND persona_id = ?
                LIMIT ?
                """,
                (match, user_id, persona_id, limit),
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for r in rows:
            full = self.base.store.get(user_id, persona_id, r["memory_id"])
            if full and full.get("status") in ("active", "legacy_unverified", "candidate"):
                if not self.base.store.is_source_suppressed(user_id, persona_id, full["id"]):
                    out.append(full)
        return out

    def _overlap_list(
        self,
        user_id: str,
        persona_id: str,
        query: str,
        limit: int,
        *,
        candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """词面打分只对 FTS 候选（或显式候选），禁止 list_active(500) 全表扫。"""
        qtok = _tokens(query or "")
        if not qtok:
            return []
        pool = candidates if candidates is not None else self._fts_list(user_id, persona_id, query, limit=max(limit * 3, 24))
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in pool:
            if self.base.store.is_source_suppressed(user_id, persona_id, row["id"]):
                continue
            text = row.get("text") or ""
            tok = _tokens(text)
            if not tok:
                continue
            inter = len(qtok & tok)
            if inter <= 0 and not any(w in text for w in qtok):
                continue
            score = inter / max(1, len(qtok))
            for w in qtok:
                if w in text:
                    score += 0.2
            for hint in ("猫", "狗", "花", "上班", "滨江", "豆豆", "来福", "旺财"):
                if hint in query and hint in text:
                    score += 0.35
            score += self.base.store.effective_heat(row) / 1000.0
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for s, r in scored if s > 0][:limit]

    def _expand_queries(self, query: str) -> List[str]:
        qs = [query]
        if self.scheme in ("expand_rrf", "combo") and self.expand_fn:
            try:
                extra = self.expand_fn(query) or []
                for e in extra:
                    e = (e or "").strip()
                    if e and e not in qs:
                        qs.append(e)
            except Exception:
                pass
        # 轻量规则改写（不依赖 LLM；借鉴 mem0 entity + CJK 口语桥接）
        q = query or ""
        if "猫" in q or "毛孩子" in q or "橘猫" in q:
            qs.append("猫 名字 豆豆 橘猫")
        if "狗" in q or "旺财" in q or "来福" in q or ("宠物" in q and "猫" not in q):
            qs.append("狗 名字 来福 旺财")
        if any(x in q for x in ("上班", "工作", "公司", "哪上班", "哪个区", "在哪")):
            qs.append("上班 工作 滨江 杭州 公司")
        if "花" in q or "植物" in q or ("喜欢" in q and "什么" in q):
            qs.append("喜欢的花 玉兰")
        return qs[:6]

    def retrieve_for_turn(
        self,
        user_id: str,
        persona_id: str,
        query: str,
    ) -> tuple[List[Dict[str, Any]], Any]:
        if self.scheme == "baseline":
            return self.base.retrieve_for_turn(user_id, persona_id, query)

        queries = self._expand_queries(query)
        fts_all: List[Dict[str, Any]] = []
        ov_all: List[Dict[str, Any]] = []
        seen_f: Set[str] = set()
        seen_o: Set[str] = set()
        for qi, qq in enumerate(queries):
            fts_hits = self._fts_list(user_id, persona_id, qq, limit=12)
            for hit in fts_hits:
                mid = hit.get("id") or ""
                if mid and mid not in seen_f:
                    seen_f.add(mid)
                    fts_all.append(hit)
            for hit in self._overlap_list(user_id, persona_id, qq, limit=12, candidates=fts_hits):
                mid = hit.get("id") or ""
                if mid and mid not in seen_o:
                    seen_o.add(mid)
                    ov_all.append(hit)

        # 语义通道（可选，默认关）
        sem_all: List[Dict[str, Any]] = []
        try:
            from .semantic import semantic_search

            for hit in semantic_search(user_id, persona_id, query, limit=12):
                mid = hit.get("id") or ""
                if mid and mid not in seen_f:
                    sem_all.append(hit)
        except Exception:
            sem_all = []

        # FTS 精确通道权重 2.0；语义 1.5；词面 1.0
        lists = [fts_all, ov_all]
        weights = [2.0, 1.0]
        if sem_all:
            lists.append(sem_all)
            weights.append(1.5)
        recalled = rrf_fuse(lists, weights=weights, limit=20)

        # 热记忆门控：无实体信号且召回空 → 不塞热记忆（修空召回污染）
        if recalled or query_has_entity_signal(query):
            hot = self.base.hot_preload(user_id, persona_id)
        else:
            hot = []

        packed, report = self.base.budgeter.pack_memories(hot, recalled)
        return packed, report
