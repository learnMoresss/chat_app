"""整次输入唯一组装：snapshot_id + 分节预算。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..security import new_id
from .budget import ContextBudgeter, approx_tokens
from .retrieve_schemes import SchemeRetriever, default_scheme
from .retriever import MemoryRetriever
from .affect import AffectService
from .growth import GrowthService
from .worldline import presence_hint, worldline_enabled, get_world_state


def _pause_read(user_id: str, persona_id: str) -> bool:
    pol = db.fetchone(
        "SELECT pause_memory_read FROM companion_policy WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    return bool(pol and int(pol["pause_memory_read"] or 0))


def compile_context(
    user_id: str,
    persona_id: str,
    *,
    query: str = "",
) -> Dict[str, Any]:
    """唯一快照；pause_memory_read 时 memories 为空。"""
    persona = db.fetchone(
        "SELECT id, name, remark, bio, worldline_enabled FROM personas WHERE id=? AND user_id=?",
        (persona_id, user_id),
    )
    if not persona:
        raise KeyError("persona_not_found")

    snapshot_id = new_id("snap_")
    growth = GrowthService().get_state(user_id, persona_id)
    affect = AffectService().get_or_init(user_id, persona_id)
    baseline = growth.get("baseline_bio") or (persona["bio"] or "")
    name = (persona["remark"] or persona["name"] or "").strip()

    memories: List[Dict[str, Any]] = []
    mem_report_ok = True
    mem_tokens = 0
    mem_limit = 1200
    trimmed: List[str] = []
    if not _pause_read(user_id, persona_id):
        retriever = SchemeRetriever(scheme=default_scheme())
        packed, mem_report = retriever.retrieve_for_turn(
            user_id, persona_id, query or (persona["bio"] or "")[:40]
        )
        store_heat = MemoryRetriever().store.effective_heat
        memories = [
            {
                "id": m["id"],
                "text": m.get("text") or "",
                "heat": store_heat(m),
            }
            for m in packed
        ]
        mem_report_ok = mem_report.ok
        mem_tokens = mem_report.total
        mem_limit = mem_report.limit
        trimmed = list(mem_report.trimmed[:20])

    # 情绪 → 表达策略短句（替代纯数字堆叠时可并存）
    a = affect
    affect_strategy = (
        f"表达策略：结合当前情绪（愉悦{a.get('joy', 35)} 低落{a.get('sadness', 35)} "
        f"兴奋{a.get('excitement', 35)} 担忧{a.get('worry', 35)} "
        f"不悦{a.get('annoyance', 35)} 好奇{a.get('curiosity', 35)}）自然回应；"
        "先回应对方刚说的事。"
    )
    if a.get("reason"):
        affect_strategy += f"近期因素：{a.get('reason')}。"

    world_presence = ""
    world_state: Dict[str, Any] = {}
    wl_on = False
    try:
        wl_on = bool(int(persona["worldline_enabled"])) if "worldline_enabled" in persona.keys() else worldline_enabled(user_id, persona_id)
    except Exception:
        wl_on = worldline_enabled(user_id, persona_id)
    if wl_on:
        world_presence = presence_hint(user_id, persona_id)
        st = get_world_state(user_id, persona_id)
        if st:
            world_state = {
                "weather": st.get("weather"),
                "weather_source": st.get("weather_source"),
                "current_activity": st.get("current_activity"),
                "interrupt_policy": st.get("interrupt_policy"),
                "location": st.get("location"),
            }

    sticker_by = []
    try:
        from .. import stickers as stk

        sticker_by = stk.persona_sticker_summary(user_id=user_id, persona_id=persona_id)
    except Exception:
        sticker_by = []

    sections = {
        "persona": approx_tokens(baseline[:800]),
        "affect": approx_tokens(affect_strategy),
        "world": approx_tokens(world_presence),
        "memory": mem_tokens,
    }
    budgeter = ContextBudgeter()
    # 超预算时优先砍记忆
    while sections["memory"] > 0 and sum(sections.values()) > budgeter.config.total_input_soft // 2:
        if memories:
            memories.pop()
            sections["memory"] = sum(approx_tokens(m["text"]) for m in memories)
        else:
            break

    state_version = int(affect.get("state_version") or 1) * 1000 + int(growth.get("growth_epoch") or 1)
    return {
        "snapshot_id": snapshot_id,
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
        "affect_strategy": affect_strategy,
        "worldline_enabled": wl_on,
        "world_presence": world_presence,
        "world_state": world_state,
        "stickers": {"by_emotion": sticker_by, "hint": ""},
        "memories": memories,
        "memory_budget": {
            "ok": mem_report_ok,
            "tokens": sections["memory"],
            "limit": mem_limit,
            "trimmed": trimmed,
            "estimated": True,
        },
        "section_tokens": sections,
        "token_estimate": sum(sections.values()),
        "state_version": state_version,
        "pause_memory_read": _pause_read(user_id, persona_id),
    }


# 兼容旧名
def build_companion_snapshot(
    user_id: str,
    persona_id: str,
    *,
    query: str = "",
) -> Dict[str, Any]:
    return compile_context(user_id, persona_id, query=query)
