from __future__ import annotations

import json
from typing import Dict, List, Optional

from . import db
from .companion.retriever import MemoryRetriever
from .companion.store import MemoryStore
from .dsh_gateway import dsh_complete, require_dsh
from .security import iso, new_id

EXTRACT_PROMPT = """从下面这段对话中，只抽出关于「主人」（用户）的长期有用事实，每条一行短句。
要求：
- 最多 3 条；没有值得记的就输出空数组 []
- 不要记一次性闲聊、情绪发泄、模型自我介绍
- 不要重复已有记忆里已经有的意思
- 只输出 JSON 数组，例如 ["主人喜欢安静","主人在上海"]

已有记忆：
{existing}

对话：
{dialog}
"""


def search_memories(user_id: str, persona_id: str, query: str, limit: int = 6) -> List[Dict[str, str]]:
    """FTS/词面检索；空召回合法，禁止「未命中塞最近几条」。"""
    packed, _ = MemoryRetriever().retrieve_for_turn(user_id, persona_id, query or "")
    out: List[Dict[str, str]] = []
    for m in packed[:limit]:
        out.append({"id": m["id"], "text": m.get("text") or ""})
    return out


def list_memories(user_id: str, persona_id: str) -> List[Dict[str, str]]:
    rows = MemoryStore().list_active(user_id, persona_id, limit=200)
    return [
        {"id": r["id"], "text": r.get("text") or "", "updated_at": r.get("updated_at") or ""}
        for r in rows
    ]


def add_memory(user_id: str, persona_id: str, text: str) -> Dict[str, str]:
    text = text.strip()
    if not text:
        raise ValueError("记忆不能为空")
    # 简单去重：相同正文不重复插
    exist = db.fetchone(
        "SELECT id, text, updated_at FROM memories WHERE user_id = ? AND persona_id = ? AND text = ?",
        (user_id, persona_id, text),
    )
    if exist:
        return {"id": exist["id"], "text": exist["text"], "updated_at": exist["updated_at"]}
    row = MemoryStore().create(user_id=user_id, persona_id=persona_id, text=text, status="active")
    return {
        "id": row["id"],
        "text": row.get("text") or text,
        "updated_at": row.get("updated_at") or iso(),
    }


def update_memory(user_id: str, persona_id: str, memory_id: str, text: str) -> Optional[Dict[str, str]]:
    text = text.strip()
    store = MemoryStore()
    old = store.get(user_id, persona_id, memory_id)
    if old is None:
        return None
    try:
        new = store.correct(
            user_id=user_id,
            persona_id=persona_id,
            memory_id=memory_id,
            new_text=text,
        )
        return {"id": new["id"], "text": new.get("text") or text, "updated_at": new.get("updated_at") or iso()}
    except Exception:
        now = iso()
        db.execute(
            "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?",
            (text, now, memory_id),
        )
        return {"id": memory_id, "text": text, "updated_at": now}


def delete_memory(user_id: str, persona_id: str, memory_id: str) -> bool:
    return MemoryStore().delete_hard(user_id=user_id, persona_id=persona_id, memory_id=memory_id)


async def extract_and_store(
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> List[str]:
    """Mem0 风格：一轮结束后抽事实写入（失败则静默跳过）。只走 DSH。"""
    _ = (base_url, api_key, model)
    # 暂停写记忆：全局 prefs 或人设 companion_policy
    pref = db.fetchone(
        "SELECT pause_memory_extract FROM user_prefs WHERE user_id=?",
        (user_id,),
    )
    if pref is not None:
        try:
            if "pause_memory_extract" in pref.keys() and int(pref["pause_memory_extract"] or 0):
                return []
        except Exception:
            pass
    pol = db.fetchone(
        "SELECT pause_memory_write FROM companion_policy WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    if pol and int(pol["pause_memory_write"] or 0):
        return []
    require_dsh()
    existing = list_memories(user_id, persona_id)[:12]
    existing_block = "\n".join(f"- {m['text']}" for m in existing) or "（无）"
    dialog = f"主人：{user_text}\n{assistant_text}"
    prompt = EXTRACT_PROMPT.format(existing=existing_block, dialog=dialog)
    system = "你只输出 JSON 数组，不要其它文字。"
    try:
        raw = await dsh_complete(
            user_id=user_id,
            conversation_id=f"sys-mem-{persona_id}-{new_id('')}",
            text=prompt,
            system=system,
        )
    except Exception:
        return []

    facts: List[str] = []
    try:
        cleaned = raw.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if isinstance(data, list):
            facts = [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        for line in raw.splitlines():
            line = line.strip(" -\t")
            if 4 <= len(line) <= 80 and not line.startswith("["):
                facts.append(line)

    saved: List[str] = []
    for fact in facts[:3]:
        if any(fact in m["text"] or m["text"] in fact for m in existing):
            continue
        try:
            add_memory(user_id, persona_id, fact)
            saved.append(fact)
        except ValueError:
            continue
    return saved
