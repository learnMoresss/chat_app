from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db
from ..security import current_user

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.get("/summary")
def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """用户自己的用量汇总（对照 Open WebUI Settings → Usage）。"""
    rows = db.fetchall(
        """
        SELECT * FROM usage_logs
        WHERE user_id = ? AND created_at >= datetime('now', ?)
        ORDER BY created_at DESC
        """,
        (user["id"], f"-{days} days"),
    )
    total_calls = len(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    tokens = sum(int(r["total_tokens"] or 0) for r in rows)
    by_model: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    by_client: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    cache_reads = 0
    contexts = 0
    hit_samples: List[float] = []
    for r in rows:
        m = r["model"] or "unknown"
        c = r["client"] or "app"
        by_model[m]["calls"] += 1
        by_model[m]["tokens"] += int(r["total_tokens"] or 0)
        by_client[c]["calls"] += 1
        by_client[c]["tokens"] += int(r["total_tokens"] or 0)
        try:
            cr = int(r["cache_read_tokens"] or 0)
            ct = int(r["context_tokens"] or 0)
        except (KeyError, IndexError, TypeError):
            cr, ct = 0, 0
        cache_reads += cr
        contexts += ct
        try:
            rate = float(r["cache_hit_rate"] or 0)
        except (KeyError, IndexError, TypeError):
            rate = 0.0
        if rate > 0 or cr > 0:
            hit_samples.append(rate)
    return {
        "days": days,
        "calls": total_calls,
        "ok_calls": ok,
        "error_calls": total_calls - ok,
        "total_tokens": tokens,
        "total_context_tokens": contexts,
        "total_cache_read_tokens": cache_reads,
        "avg_cache_hit_rate": round(sum(hit_samples) / len(hit_samples), 2) if hit_samples else 0,
        "by_model": [{"model": k, **v} for k, v in sorted(by_model.items(), key=lambda x: -x[1]["tokens"])],
        "by_client": [{"client": k, **v} for k, v in sorted(by_client.items(), key=lambda x: -x[1]["calls"])],
    }


@router.get("/logs")
def usage_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    conv_id: str = Query(default=""),
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    cid = (conv_id or "").strip()
    if cid:
        rows = db.fetchall(
            """
            SELECT id, persona_id, channel_id, conv_id, model, prompt_tokens, completion_tokens,
                   total_tokens, client, status, error, created_at, estimated,
                   input_tokens, cache_read_tokens, cache_write_tokens, context_tokens,
                   cache_hit_rate, prompt_chars, history_messages, model_calls, dsh_session_id,
                   metrics_json
            FROM usage_logs
            WHERE user_id = ? AND conv_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user["id"], cid, limit, offset),
        )
    else:
        rows = db.fetchall(
            """
            SELECT id, persona_id, channel_id, conv_id, model, prompt_tokens, completion_tokens,
                   total_tokens, client, status, error, created_at, estimated,
                   input_tokens, cache_read_tokens, cache_write_tokens, context_tokens,
                   cache_hit_rate, prompt_chars, history_messages, model_calls, dsh_session_id,
                   metrics_json
            FROM usage_logs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user["id"], limit, offset),
        )
    out: List[Dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        try:
            import json as _json

            item["metrics"] = _json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
            item.pop("metrics_json", None)
        out.append(item)
    return out


@router.get("/turns/{conv_id}")
def usage_turns(
    conv_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """某会话最近若干轮：上下文 token、缓存命中率。"""
    rows = usage_logs(limit=limit, offset=0, conv_id=conv_id, user=user)
    if not rows:
        return {"conv_id": conv_id, "turns": [], "avg_cache_hit_rate": 0, "total_context_tokens": 0}
    hits = [float(r.get("cache_hit_rate") or 0) for r in rows if (r.get("cache_read_tokens") or 0) or (r.get("context_tokens") or 0)]
    ctx = sum(int(r.get("context_tokens") or r.get("prompt_tokens") or 0) for r in rows)
    return {
        "conv_id": conv_id,
        "turns": rows,
        "avg_cache_hit_rate": round(sum(hits) / len(hits), 2) if hits else 0,
        "total_context_tokens": ctx,
        "turn_count": len(rows),
    }


@router.get("/admin/summary")
def admin_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员")
    rows = db.fetchall(
        """
        SELECT u.username, l.model, l.client, l.total_tokens, l.status,
               l.context_tokens, l.cache_read_tokens, l.cache_hit_rate
        FROM usage_logs l
        JOIN users u ON u.id = l.user_id
        WHERE l.created_at >= datetime('now', ?)
        """,
        (f"-{days} days",),
    )
    by_user: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "tokens": 0,
            "context_tokens": 0,
            "cache_read_tokens": 0,
            "_hit_sum": 0.0,
            "_hit_n": 0,
        }
    )
    by_model: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    total_ctx = 0
    total_cache = 0
    hit_samples: List[float] = []
    for r in rows:
        u = by_user[r["username"]]
        u["calls"] += 1
        u["tokens"] += int(r["total_tokens"] or 0)
        ct = int(r["context_tokens"] or 0)
        cr = int(r["cache_read_tokens"] or 0)
        u["context_tokens"] += ct
        u["cache_read_tokens"] += cr
        total_ctx += ct
        total_cache += cr
        try:
            rate = float(r["cache_hit_rate"] or 0)
        except (TypeError, ValueError):
            rate = 0.0
        if rate > 0 or cr > 0:
            u["_hit_sum"] += rate
            u["_hit_n"] += 1
            hit_samples.append(rate)
        m = r["model"] or "unknown"
        by_model[m]["calls"] += 1
        by_model[m]["tokens"] += int(r["total_tokens"] or 0)
    users_out = []
    for k, v in sorted(by_user.items(), key=lambda x: -x[1]["tokens"]):
        n = int(v.pop("_hit_n") or 0)
        hs = float(v.pop("_hit_sum") or 0)
        users_out.append(
            {
                "username": k,
                "calls": v["calls"],
                "tokens": v["tokens"],
                "context_tokens": v["context_tokens"],
                "cache_read_tokens": v["cache_read_tokens"],
                "avg_cache_hit_rate": round(hs / n, 2) if n else 0,
            }
        )
    return {
        "days": days,
        "calls": len(rows),
        "total_tokens": sum(int(r["total_tokens"] or 0) for r in rows),
        "total_context_tokens": total_ctx,
        "total_cache_read_tokens": total_cache,
        "avg_cache_hit_rate": round(sum(hit_samples) / len(hit_samples), 2) if hit_samples else 0,
        "by_user": users_out,
        "by_model": [{"model": k, **v} for k, v in sorted(by_model.items(), key=lambda x: -x[1]["tokens"])],
    }


@router.get("/export.csv")
def export_csv(
    days: int = Query(default=30, ge=1, le=365),
    scope: str = Query(default="me"),  # me | all
    user: Dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    """导出用量 CSV。管理员可用 scope=all 导出全站。"""
    if scope == "all":
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="需要管理员")
        rows = db.fetchall(
            """
            SELECT u.username, l.model, l.client, l.prompt_tokens, l.completion_tokens,
                   l.total_tokens, l.context_tokens, l.cache_read_tokens, l.cache_write_tokens,
                   l.cache_hit_rate, l.history_messages, l.status, l.error, l.created_at
            FROM usage_logs l
            JOIN users u ON u.id = l.user_id
            WHERE l.created_at >= datetime('now', ?)
            ORDER BY l.created_at DESC
            """,
            (f"-{days} days",),
        )
        headers = [
            "username",
            "model",
            "client",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "context_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cache_hit_rate",
            "history_messages",
            "status",
            "error",
            "created_at",
        ]
    else:
        rows = db.fetchall(
            """
            SELECT model, client, prompt_tokens, completion_tokens, total_tokens,
                   context_tokens, cache_read_tokens, cache_write_tokens, cache_hit_rate,
                   history_messages, status, error, created_at
            FROM usage_logs
            WHERE user_id = ? AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            """,
            (user["id"], f"-{days} days"),
        )
        headers = [
            "model",
            "client",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "context_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cache_hit_rate",
            "history_messages",
            "status",
            "error",
            "created_at",
        ]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([r[h] for h in headers])
    buf.seek(0)
    filename = f"usage_{scope}_{days}d.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
