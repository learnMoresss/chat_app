"""后台任务：抽取入队消费（挂 30s scheduler）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from .. import db
from ..security import iso


def due_jobs(*, limit: int = 8) -> List[Dict[str, Any]]:
    now = iso()
    rows = db.fetchall(
        """
        SELECT * FROM companion_background_jobs
        WHERE status IN ('pending', 'retrying')
          AND (not_before = '' OR not_before <= ?)
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (now, limit),
    )
    return [dict(r) for r in rows]


async def process_one(job: Dict[str, Any]) -> None:
    jid = job["id"]
    now = iso()
    db.execute(
        "UPDATE companion_background_jobs SET status='processing', updated_at=? WHERE id=?",
        (now, jid),
    )
    kind = job.get("kind") or ""
    try:
        if kind == "extract":
            await _run_extract(job)
        else:
            raise RuntimeError(f"unknown_job_kind:{kind}")
        db.execute(
            """
            UPDATE companion_background_jobs
            SET status='succeeded', last_error='', updated_at=?
            WHERE id=?
            """,
            (iso(), jid),
        )
    except Exception as exc:  # noqa: BLE001
        attempts = int(job.get("attempts") or 0) + 1
        max_a = int(job.get("max_attempts") or 5)
        status = "failed" if attempts >= max_a else "retrying"
        delay = min(3600, 30 * (2 ** max(0, attempts - 1)))
        nb = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        db.execute(
            """
            UPDATE companion_background_jobs
            SET status=?, attempts=?, last_error=?, not_before=?, updated_at=?
            WHERE id=?
            """,
            (status, attempts, str(exc)[:500], nb if status == "retrying" else "", iso(), jid),
        )


async def _run_extract(job: Dict[str, Any]) -> None:
    from .. import memory as mem

    payload = json.loads(job.get("payload_json") or "{}")
    user_text = str(payload.get("user_text") or "")
    assistant_text = str(payload.get("assistant_text") or "")
    if not user_text or not assistant_text:
        return
    skip_explicit = bool(payload.get("skip_explicit_text"))
    saved = await mem.extract_and_store(
        user_id=job["user_id"],
        persona_id=job["persona_id"],
        user_text=user_text,
        assistant_text=assistant_text,
        skip_explicit_text=skip_explicit,
        provider=str(payload.get("provider") or ""),
        dsh_model=str(payload.get("model") or ""),
    )
    # 显式已写入则允许抽取空结果；否则「记住」空抽触发重试
    if skip_explicit:
        return
    if not saved and ("记住" in user_text or "更正" in user_text):
        raise RuntimeError("explicit_remember_empty_extract")


async def process_due(*, limit: int = 8) -> int:
    n = 0
    for job in due_jobs(limit=limit):
        await process_one(job)
        n += 1
    return n
