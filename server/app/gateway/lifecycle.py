from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .. import db
from ..security import iso, new_id
from .agent_client import AgentClient
from .registry import agent_for, enabled_workers


def ensure_local_instance(plugin_id: str, *, vram_mb: int = 0) -> Dict[str, Any]:
    """
    若已有 ready 实例则复用；否则命令某个 worker-agent start。
    返回 plugin_instances 行字典（含 base_url、token）。
    """
    row = db.fetchone(
        """
        SELECT * FROM plugin_instances
        WHERE plugin_id = ? AND kind = 'local' AND state = 'ready'
        ORDER BY last_used_at DESC
        LIMIT 1
        """,
        (plugin_id,),
    )
    if row:
        return dict(row)

    workers = enabled_workers()
    if not workers:
        raise RuntimeError("没有可用的 worker-agent，请配置 gateway.yaml")

    last_err = ""
    for w in workers:
        client = agent_for(w)
        try:
            started = client.start_instance(plugin_id, vram_mb=vram_mb or None)
        except Exception as e:
            last_err = str(e)
            continue
        now = iso()
        iid = started["instance_id"]
        db.execute(
            """
            INSERT INTO plugin_instances (
              id, worker_id, plugin_id, kind, base_url, token, gpu_index,
              state, vram_mb, max_concurrency, busy_count, keep_warm,
              last_used_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, 'ready', ?, 1, 0, 0, ?, ?, ?)
            """,
            (
                iid,
                w["id"],
                plugin_id,
                started["base_url"],
                started["token"],
                started.get("gpu_index"),
                int(vram_mb or 0),
                now,
                now,
                now,
            ),
        )
        return dict(db.fetchone("SELECT * FROM plugin_instances WHERE id = ?", (iid,)))

    raise RuntimeError(last_err or "无法启动插件实例")


def touch_instance(instance_id: str) -> None:
    db.execute(
        "UPDATE plugin_instances SET last_used_at = ?, updated_at = ? WHERE id = ?",
        (iso(), iso(), instance_id),
    )


def mark_busy(instance_id: str, delta: int) -> None:
    db.execute(
        """
        UPDATE plugin_instances
        SET busy_count = CASE
              WHEN busy_count + ? < 0 THEN 0
              ELSE busy_count + ?
            END,
            updated_at = ?
        WHERE id = ?
        """,
        (delta, delta, iso(), instance_id),
    )


def stop_instance_record(instance_id: str) -> None:
    row = db.fetchone("SELECT * FROM plugin_instances WHERE id = ?", (instance_id,))
    if not row:
        return
    row = dict(row)
    if row["kind"] != "local" or not row.get("worker_id"):
        db.execute("DELETE FROM plugin_instances WHERE id = ?", (instance_id,))
        return
    w = db.fetchone("SELECT * FROM workers WHERE id = ?", (row["worker_id"],))
    if w:
        try:
            AgentClient(w["base_url"], w["token"]).stop_instance(instance_id)
        except Exception:
            pass
    db.execute("DELETE FROM plugin_instances WHERE id = ?", (instance_id,))


def reap_idle_instances() -> int:
    """按 idle_ttl 卸掉空闲本地实例。第一批：简单按 last_used_at + 默认 600s。"""
    rows = db.fetchall(
        """
        SELECT * FROM plugin_instances
        WHERE kind = 'local' AND state = 'ready' AND busy_count = 0 AND keep_warm = 0
        """
    )
    n = 0
    now = time.time()
    for r in rows:
        r = dict(r)
        # last_used_at 是 iso；解析失败则跳过
        try:
            from datetime import datetime

            last = datetime.fromisoformat(r["last_used_at"]).timestamp()
        except Exception:
            continue
        ttl = int(r.get("idle_ttl_seconds") or 600)
        if now - last < ttl:
            continue
        stop_instance_record(r["id"])
        n += 1
    return n
