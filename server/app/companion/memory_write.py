"""显式记忆操作（记住/更正/忘掉）与抽取入队。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from .. import db
from .. import memory as mem
from ..security import iso, new_id
from .store import MemoryStore

_EXPLICIT_RE = re.compile(
    r"(记住|请记住|记一下|忘掉|别记|更正|改成|改名叫|本来叫|不叫)",
)
_TAIL_PARTICLE = re.compile(r"[哈啊哦呀吧嗯哦嘿]+$")


def looks_like_explicit_memory_op(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_EXPLICIT_RE.search(t))


def _parse_forget(text: str) -> Optional[str]:
    m = re.search(r"(?:忘掉|别记|忘记)(?:一下)?[：:，,\s]*(.+)$", text)
    if m:
        return m.group(1).strip(" 。.!！")[:80]
    return None


def _parse_correct(text: str) -> Optional[Tuple[str, str]]:
    t = text.strip()
    m = re.search(
        r"(?:本来叫|不叫)\s*([\u4e00-\u9fffA-Za-z0-9]{1,12}).*?(?:叫|是)\s*([\u4e00-\u9fffA-Za-z0-9]{1,12})",
        t,
    )
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"改名叫\s*([\u4e00-\u9fffA-Za-z0-9]{1,12})", t)
    if m:
        return "", m.group(1)
    m = re.search(r"更正(?:一下)?[：:，,\s]*(.+)$", t)
    if m:
        return "", m.group(1).strip(" 。.!！")[:120]
    return None


def _normalize_remember_body(body: str) -> Optional[str]:
    body = (body or "").strip(" 。.!！，,")
    body = _TAIL_PARTICLE.sub("", body).strip(" ，,。")
    bare = re.sub(r"[哈啊哦呀吧嗯\s]", "", body)
    if len(bare) < 3:
        return None
    if not body.startswith("主人"):
        body = ("主人" + body[1:]) if body.startswith("我") else f"主人：{body}"
    return body[:120]


def _parse_remember(text: str) -> Optional[str]:
    t = text.strip()
    if mem.is_retracted_text(t):
        return None
    # 「正文，记住哈」——正文在记住之前
    m = re.search(r"^(.+?)[，,。！!]?\s*(?:请)?记住[哈啊哦呀吧嗯]*$", t)
    if m:
        got = _normalize_remember_body(m.group(1))
        if got:
            return got
    # 「记住：正文」
    m = re.search(r"(?:请)?记住[：:，,\s]+(.+)$", t)
    if m:
        got = _normalize_remember_body(m.group(1))
        if got:
            return got
    m = re.search(r"(?:跟你说个事|告诉你|再记一条)[：:，,\s]*(.+?)(?:，?\s*(?:请)?记住|$)", t)
    if m and ("记住" in t or "记一条" in t):
        got = _normalize_remember_body(m.group(1))
        if got:
            return got
    return None


def _find_memory_by_hint(user_id: str, persona_id: str, hint: str) -> Optional[Dict[str, Any]]:
    hint = (hint or "").strip()
    if not hint:
        return None
    store = MemoryStore()
    for row in store.list_active(user_id, persona_id, limit=80):
        if hint in (row.get("text") or ""):
            return row
    try:
        rows = db.fetchall(
            """
            SELECT memory_id FROM memory_facts_fts
            WHERE memory_facts_fts MATCH ? AND user_id=? AND persona_id=?
            LIMIT 5
            """,
            (f'"{hint}"', user_id, persona_id),
        )
        for r in rows:
            full = store.get(user_id, persona_id, r["memory_id"])
            if full and full.get("status") in ("active", "legacy_unverified", "candidate"):
                return full
    except Exception:
        pass
    return None


def _pet_kind(text: str) -> str:
    if "狗" in text:
        return "狗"
    if "猫" in text:
        return "猫"
    return "宠物"


class MemoryWriteService:
    """规则优先的显式操作；不确定则 pending_confirm。"""

    def apply_explicit(
        self,
        *,
        user_id: str,
        persona_id: str,
        text: str,
        source_message_id: str = "",
    ) -> Dict[str, Any]:
        t = (text or "").strip()
        if not looks_like_explicit_memory_op(t):
            return {"handled": False, "status": "skip"}

        # 反悔：整句不写
        if mem.is_retracted_text(t) and ("记住" in t or "记一下" in t):
            return {
                "handled": True,
                "status": "ok",
                "op": "skip_retract",
                "message": "好，当没说过",
            }

        store = MemoryStore()

        if "忘掉" in t or "别记" in t or "忘记" in t:
            forget_hint = _parse_forget(t) or ""
            target = _find_memory_by_hint(user_id, persona_id, forget_hint) if forget_hint else None
            if not target:
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "没找到要忘掉的那条，请说明具体内容",
                }
            store.delete_hard(user_id=user_id, persona_id=persona_id, memory_id=target["id"])
            self._invalidate_extract_jobs(user_id, persona_id)
            return {
                "handled": True,
                "status": "ok",
                "op": "forget",
                "memory_id": target["id"],
                "message": "已忘掉",
            }

        corr = _parse_correct(t)
        if corr is not None or "更正" in t or "改名" in t or "本来叫" in t or "不叫" in t:
            old_hint, new_val = corr or ("", "")
            kind = _pet_kind(t)
            if new_val and ("叫" in t or "狗" in t or "猫" in t or "改名" in t):
                new_text = f"主人养的{kind}叫{new_val}"
            elif new_val:
                new_text = new_val if str(new_val).startswith("主人") else f"主人：{new_val}"
            else:
                rem = _parse_remember(t)
                if not rem:
                    return {
                        "handled": True,
                        "status": "pending_confirm",
                        "message": "更正内容不够明确，请再说一次要改成什么",
                    }
                new_text = rem

            if not mem.is_storable_fact(new_text):
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "更正内容不够明确，请再说一次要改成什么",
                }

            target = None
            if old_hint:
                target = _find_memory_by_hint(user_id, persona_id, old_hint)
            if not target:
                subj, attr = mem.infer_entity_key(new_text)
                for row in store.list_active(user_id, persona_id, limit=50):
                    tx = row.get("text") or ""
                    if "叫" in tx and (kind in tx or "宠物" in tx or "名字" in tx):
                        target = row
                        break
                    if subj and attr and (row.get("subject") or "") == subj and (row.get("attribute") or "") == attr:
                        target = row
                        break
            if target:
                row = store.correct(
                    user_id=user_id,
                    persona_id=persona_id,
                    memory_id=target["id"],
                    new_text=mem.normalize_fact_text(new_text) or new_text[:120],
                )
                # 回填实体键
                subj, attr = mem.infer_entity_key(row.get("text") or new_text)
                if subj or attr:
                    try:
                        db.execute(
                            "UPDATE memory_facts SET subject=?, attribute=? WHERE id=?",
                            (subj, attr, row["id"]),
                        )
                    except Exception:
                        pass
                self._invalidate_extract_jobs(user_id, persona_id)
                self._tag_source(row["id"], "user_explicit")
                return {
                    "handled": True,
                    "status": "ok",
                    "op": "correct",
                    "memory_id": row["id"],
                    "message": "已更正",
                    "text": row.get("text"),
                }
            saved = mem.upsert_fact(
                user_id, persona_id, new_text, source_message_id=source_message_id
            )
            if not saved:
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "更正内容不够明确，请再说一次要改成什么",
                }
            if saved.get("id"):
                self._tag_source(saved["id"], "user_explicit")
            self._invalidate_extract_jobs(user_id, persona_id)
            return {
                "handled": True,
                "status": "ok",
                "op": "remember",
                "memory_id": saved.get("id") or "",
                "message": "已记住",
                "text": saved.get("text"),
            }

        rem = _parse_remember(t)
        if rem or "记住" in t or "记一条" in t:
            if not rem:
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "要记住什么？请再说具体一点",
                }
            if not mem.is_storable_fact(rem):
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "要记住什么？请再说具体一点",
                }
            saved = mem.upsert_fact(
                user_id, persona_id, rem, source_message_id=source_message_id
            )
            if not saved:
                return {
                    "handled": True,
                    "status": "pending_confirm",
                    "message": "要记住什么？请再说具体一点",
                }
            if saved.get("id"):
                self._tag_source(saved["id"], "user_explicit")
            self._invalidate_extract_jobs(user_id, persona_id)
            return {
                "handled": True,
                "status": "ok",
                "op": "remember",
                "memory_id": saved.get("id") or "",
                "message": "已记住",
                "text": saved.get("text") or rem,
            }

        return {"handled": False, "status": "skip"}

    def enqueue_extract(
        self,
        *,
        user_id: str,
        persona_id: str,
        user_text: str,
        assistant_text: str,
        source_message_id: str = "",
        skip_explicit_text: bool = False,
        provider: str = "",
        model: str = "",
    ) -> str:
        jid = new_id("job_")
        now = iso()
        payload = {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "source_message_id": source_message_id,
            "skip_explicit_text": bool(skip_explicit_text),
            "provider": provider or "",
            "model": model or "",
        }
        db.execute(
            """
            INSERT INTO companion_background_jobs(
              id, user_id, persona_id, kind, status, payload_json, cursor_json,
              attempts, max_attempts, last_error, not_before, created_at, updated_at
            ) VALUES (?,?,?,?, 'pending', ?, '{}', 0, 5, '', ?, ?, ?)
            """,
            (
                jid,
                user_id,
                persona_id,
                "extract",
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )
        return jid

    def _tag_source(self, memory_id: str, kind: str) -> None:
        try:
            db.execute(
                "UPDATE memory_facts SET source_kind=? WHERE id=?",
                (kind, memory_id),
            )
        except Exception:
            pass

    def _invalidate_extract_jobs(self, user_id: str, persona_id: str) -> None:
        now = iso()
        db.execute(
            """
            UPDATE companion_background_jobs
            SET status='cancelled', last_error='superseded_by_explicit', updated_at=?
            WHERE user_id=? AND persona_id=? AND kind='extract'
              AND status IN ('pending', 'retrying')
            """,
            (now, user_id, persona_id),
        )
