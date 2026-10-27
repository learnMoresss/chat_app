"""有界陪伴快照：供 DSH companion 插件与看板同源读取。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import db
from .affect import AffectService
from .budget import approx_tokens
from .growth import GrowthService
from .retriever import MemoryRetriever


def build_companion_snapshot(
    user_id: str,
    persona_id: str,
    *,
    query: str = "",
) -> Dict[str, Any]:
    persona = db.fetchone(
        "SELECT id, name, remark, bio FROM personas WHERE id=? AND user_id=?",
        (persona_id, user_id),
    )
    if not persona:
        raise KeyError("persona_not_found")

    growth = GrowthService().get_state(user_id, persona_id)
    affect = AffectService().get_or_init(user_id, persona_id)
    retriever = MemoryRetriever()
    memories, mem_report = retriever.retrieve_for_turn(user_id, persona_id, query or (persona["bio"] or "")[:40])

    # 成长锁定时不注入推断教训（此处简化：mode 非 adaptive 时只带 baseline）
    baseline = growth.get("baseline_bio") or (persona["bio"] or "")
    if growth.get("mode") == "initial_locked":
        baseline = growth.get("baseline_bio") or (persona["bio"] or "")

    name = (persona["remark"] or persona["name"] or "").strip()
    mem_texts = [{"id": m["id"], "text": m.get("text") or "", "heat": MemoryRetriever().store.effective_heat(m)} for m in memories]
    sticker_by = []
    try:
        from .. import stickers as stk

        sticker_by = stk.persona_sticker_summary(user_id=user_id, persona_id=persona_id)
    except Exception:
        sticker_by = []
    sticker_line = "、".join(f"{x['emotion']}×{x['count']}" for x in sticker_by[:10]) if sticker_by else "（空，可上网搜图 collect）"
    blob = baseline + "".join(m["text"] for m in mem_texts) + str(affect) + sticker_line
    state_version = int(affect.get("state_version") or 1) * 1000 + int(growth.get("growth_epoch") or 1)

    return {
        "persona_id": persona_id,
        "persona_name": name,
        "baseline_bio": baseline[:800],
        "growth_mode": growth.get("mode"),
        "growth_epoch": growth.get("growth_epoch"),
        "traits": growth.get("traits"),
        "affect": {
            "joy": affect.get("joy"),
            "sadness": affect.get("sadness"),
            "excitement": affect.get("excitement"),
            "worry": affect.get("worry"),
            "annoyance": affect.get("annoyance"),
            "curiosity": affect.get("curiosity"),
            "reason": affect.get("reason"),
            "state_version": affect.get("state_version"),
        },
        # hint 不再注入主对话（目录由 media_dispatch 按需检索）
        "stickers": {"by_emotion": sticker_by, "hint": ""},
        "memories": mem_texts,
        "memory_budget": {
            "ok": mem_report.ok,
            "tokens": mem_report.total,
            "limit": mem_report.limit,
            "trimmed": mem_report.trimmed[:20],
            "estimated": True,
        },
        "token_estimate": approx_tokens(blob),
        "state_version": state_version,
    }
