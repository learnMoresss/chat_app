"""借鉴 sj244/dsh-qq-bridge：未唤醒只记录 + 下次真醒附摘要前缀。

私聊默认每条必醒；缓冲用于：免打扰跳过、限流、DSH 暂不可用等「本回合不调模型」场景。
不接 QQ。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import db

BUFFER_MAX = 200
RECENT_LIMIT_DEFAULT = 8
ENTRY_MAX_CHARS = 500
SUMMARY_MAX_CHARS = 1200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(user_id: str, conv_id: str) -> Tuple[str, str]:
    return user_id, conv_id


def _load(user_id: str, conv_id: str) -> List[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT value FROM server_settings WHERE key = ?",
        (f"wake_buf:{user_id}:{conv_id}",),
    )
    if not row:
        return []
    try:
        data = json.loads(row["value"] or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(user_id: str, conv_id: str, items: List[Dict[str, Any]]) -> None:
    key = f"wake_buf:{user_id}:{conv_id}"
    payload = json.dumps(items[-BUFFER_MAX:], ensure_ascii=False)
    existing = db.fetchone("SELECT key FROM server_settings WHERE key = ?", (key,))
    if existing:
        db.execute("UPDATE server_settings SET value = ? WHERE key = ?", (payload, key))
    else:
        db.execute(
            "INSERT INTO server_settings(key, value) VALUES (?, ?)",
            (key, payload),
        )


def record_skipped(
    user_id: str,
    conv_id: str,
    *,
    text: str,
    reason: str = "skipped",
) -> None:
    """本回合不调模型时写入缓冲（零模型调用）。"""
    items = _load(user_id, conv_id)
    body = (text or "").strip().replace("\n", " ")
    if len(body) > ENTRY_MAX_CHARS:
        body = body[: ENTRY_MAX_CHARS - 1] + "…"
    items.append({"t": _now(), "text": body, "reason": reason})
    if len(items) > BUFFER_MAX:
        items = items[-BUFFER_MAX:]
    _save(user_id, conv_id, items)


def consume_summary(
    user_id: str,
    conv_id: str,
    *,
    limit: int = RECENT_LIMIT_DEFAULT,
) -> str:
    """真醒前取出摘要前缀并清空缓冲（对齐 qq-bridge：投递成功后整清）。"""
    items = _load(user_id, conv_id)
    if not items:
        return ""
    take = items[-max(1, limit) :]
    omitted = max(0, len(items) - len(take))
    lines: List[str] = []
    used = 0
    # 从新往旧装，再正序输出
    for it in reversed(take):
        line = f"- {it.get('text') or ''}"
        if used + len(line) + 1 > SUMMARY_MAX_CHARS:
            omitted += 1
            continue
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    if not lines:
        _save(user_id, conv_id, [])
        return ""
    if omitted > 99:
        omit_s = "更早的大量消息已省略"
    elif omitted > 0:
        omit_s = f"更早的 {omitted} 条已省略"
    else:
        omit_s = ""
    head = f"[未回复期间聊天记录 · 最近 {len(lines)} 条]"
    if omit_s:
        head += f"（{omit_s}）"
    block = head + "\n" + "\n".join(lines) + "\n[记录结束]\n"
    _save(user_id, conv_id, [])
    return block


def buffer_count(user_id: str, conv_id: str) -> int:
    return len(_load(user_id, conv_id))


def peek(user_id: str, conv_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    """管理台只读：不消费缓冲。"""
    items = _load(user_id, conv_id)
    return items[-max(1, limit) :] if items else []


def list_buffers_for_user(user_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    """列出该用户所有非空 wake 缓冲键。"""
    rows = db.fetchall(
        "SELECT key, value FROM server_settings WHERE key LIKE ?",
        (f"wake_buf:{user_id}:%",),
    )
    out: List[Dict[str, Any]] = []
    prefix = f"wake_buf:{user_id}:"
    for r in rows:
        key = r["key"] or ""
        if not key.startswith(prefix):
            continue
        conv_id = key[len(prefix) :]
        try:
            items = json.loads(r["value"] or "[]")
            if not isinstance(items, list) or not items:
                continue
        except Exception:
            continue
        out.append(
            {
                "conv_id": conv_id,
                "count": len(items),
                "latest_reason": (items[-1] or {}).get("reason") or "",
                "latest_text": ((items[-1] or {}).get("text") or "")[:120],
            }
        )
        if len(out) >= limit:
            break
    return out
