"""每轮记忆证据注入：3–8 条、总长 ≤600 字（自研，对标 ROADMAP）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from . import db
from . import memory as mem
from .security import iso, new_id

MAX_CHARS = 600
MIN_ITEMS = 3
MAX_ITEMS = 8


def list_person_notes(user_id: str, persona_id: str) -> List[Dict[str, str]]:
    rows = db.fetchall(
        """
        SELECT id, text, updated_at FROM person_notes
        WHERE user_id = ? AND persona_id = ?
        ORDER BY updated_at DESC
        """,
        (user_id, persona_id),
    )
    return [{"id": r["id"], "text": r["text"], "updated_at": r["updated_at"]} for r in rows]


def add_person_note(user_id: str, persona_id: str, text: str) -> Dict[str, str]:
    text = (text or "").strip()
    if not text:
        raise ValueError("人物档不能为空")
    nid = new_id("pn_")
    now = iso()
    db.execute(
        """
        INSERT INTO person_notes(id, user_id, persona_id, text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (nid, user_id, persona_id, text, now, now),
    )
    return {"id": nid, "text": text, "updated_at": now}


def delete_person_note(user_id: str, note_id: str) -> None:
    db.execute("DELETE FROM person_notes WHERE id = ? AND user_id = ?", (note_id, user_id))


def build_evidence(
    user_id: str,
    persona_id: str,
    query: str,
    *,
    conv_id: str = "",
    enabled: bool = True,
) -> Tuple[str, List[Dict[str, str]]]:
    """返回 (注入文本块, 条目列表)。"""
    if not enabled:
        return "", []
    notes = list_person_notes(user_id, persona_id)
    # 与 companion 同一检索管道；空召回合法，禁止凑最近几条
    hits = mem.search_memories(user_id, persona_id, query or "", limit=MAX_ITEMS)
    items: List[Dict[str, str]] = []
    for n in notes[:4]:
        items.append({"kind": "person_note", "id": n["id"], "text": n["text"]})
    for h in hits:
        if len(items) >= MAX_ITEMS:
            break
        if any(x["text"] == h["text"] for x in items):
            continue
        items.append({"kind": "memory", "id": h["id"], "text": h["text"]})

    lines: List[str] = []
    used = 0
    kept: List[Dict[str, str]] = []
    for it in items[:MAX_ITEMS]:
        line = f"- [{it['kind']}] {it['text']}"
        if used + len(line) + 1 > MAX_CHARS:
            break
        lines.append(line)
        kept.append(it)
        used += len(line) + 1

    block = ""
    if lines:
        block = "本轮证据（人物档+记忆，已裁剪）：\n" + "\n".join(lines)

    try:
        db.execute(
            """
            INSERT INTO memory_injections(id, user_id, persona_id, conv_id, evidence_json, char_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("inj_"),
                user_id,
                persona_id,
                conv_id,
                json.dumps(kept, ensure_ascii=False),
                used,
                iso(),
            ),
        )
    except Exception:
        pass
    return block, kept


def run_nightly(user_id: str, persona_id: str = "") -> Dict[str, Any]:
    """夜间简化流水线：降权重复 / 摘要条数。"""
    if persona_id:
        persona_ids = [persona_id]
    else:
        persona_ids = [r["id"] for r in db.fetchall("SELECT id FROM personas WHERE user_id = ?", (user_id,))]
    detail: Dict[str, Any] = {"personas": []}
    for pid in persona_ids:
        rows = mem.list_memories(user_id, pid)
        # 简单「遗忘」：同文去重已在 add；这里删极短噪声
        dropped = 0
        for r in rows:
            t = (r.get("text") or "").strip()
            if len(t) < 4:
                mem.delete_memory(user_id, pid, r["id"])
                dropped += 1
        detail["personas"].append({"persona_id": pid, "count": len(rows), "dropped_noise": dropped})
    rid = new_id("mr_")
    summary = f"nightly personas={len(detail['personas'])}"
    db.execute(
        """
        INSERT INTO memory_runs(id, user_id, persona_id, kind, status, summary, detail_json, created_at)
        VALUES (?, ?, ?, 'nightly', 'ok', ?, ?, ?)
        """,
        (rid, user_id, persona_id or "*", summary, json.dumps(detail, ensure_ascii=False), iso()),
    )
    return {"id": rid, "summary": summary, "detail": detail}
