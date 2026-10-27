"""消息落库小工具：聊天路由与网关任务共用，避免循环导入。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from . import db
from .security import iso, new_id


def next_seq(user_id: str, conv_id: str) -> int:
    row = db.fetchone(
        "SELECT last_seq FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    return int(row["last_seq"]) + 1 if row else 1


def insert_message(
    *,
    user_id: str,
    conv_id: str,
    client_id: str,
    sender_id: str,
    mine: bool,
    kind: str,
    text: str,
    quote_id: Optional[str] = None,
    seconds: int = 0,
    bytes_: int = 0,
    state: str = "sent",
) -> Optional[Dict[str, Any]]:
    existing = db.fetchone(
        "SELECT * FROM messages WHERE user_id = ? AND client_id = ?",
        (user_id, client_id),
    )
    if existing:
        return dict(existing)

    owned = db.fetchone(
        "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if owned is None:
        return None

    seq = next_seq(user_id, conv_id)
    msg_id = new_id("msg_")
    now = iso()
    db.execute(
        """
        INSERT INTO messages(
          id, client_id, seq, user_id, conv_id, sender_id, mine, kind, text,
          quote_id, seconds, bytes, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            msg_id,
            client_id,
            seq,
            user_id,
            conv_id,
            sender_id,
            1 if mine else 0,
            kind,
            text,
            quote_id,
            seconds,
            bytes_,
            state,
            now,
            now,
        ),
    )
    from .preview_text import conversation_preview

    preview = conversation_preview(kind=kind, text=text, seconds=seconds)
    db.execute(
        """
        UPDATE conversations
        SET last_seq = ?, preview = ?, last_time = ?,
            unread = CASE WHEN ? = 0 THEN unread + 1 ELSE unread END
        WHERE id = ? AND user_id = ?
        """,
        (seq, preview, now, 1 if mine else 0, conv_id, user_id),
    )
    row = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    return dict(row) if row else None


def update_message(
    msg_id: str,
    *,
    text: Optional[str] = None,
    kind: Optional[str] = None,
    state: Optional[str] = None,
    seconds: Optional[int] = None,
    bytes_: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    row = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    if row is None:
        return None
    now = iso()
    new_text = text if text is not None else row["text"]
    new_kind = kind if kind is not None else row["kind"]
    new_state = state if state is not None else row["state"]
    new_secs = seconds if seconds is not None else int(row["seconds"] or 0)
    new_bytes = bytes_ if bytes_ is not None else int(row["bytes"] or 0)
    db.execute(
        """
        UPDATE messages
        SET text = ?, kind = ?, state = ?, seconds = ?, bytes = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_text, new_kind, new_state, new_secs, new_bytes, now, msg_id),
    )
    from .preview_text import conversation_preview

    preview = conversation_preview(kind=new_kind, text=new_text, seconds=new_secs)
    db.execute(
        "UPDATE conversations SET preview = ?, last_time = ? WHERE id = ?",
        (preview, now, row["conv_id"]),
    )
    updated = db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
    return dict(updated) if updated else None


def row_to_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "seq": int(row["seq"]),
        "conv_id": row["conv_id"],
        "sender_id": row["sender_id"],
        "mine": bool(row["mine"]),
        "kind": row["kind"],
        "text": row["text"],
        "quote_id": row["quote_id"],
        "seconds": int(row["seconds"] or 0),
        "bytes": int(row["bytes"] or 0),
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": (row["updated_at"] if "updated_at" in row else row["created_at"]) or row["created_at"],
    }


def job_card_text(*, capability: str, input_data: Dict[str, Any], status: str, result: Any = None, error: str = "") -> tuple[str, str, int, int]:
    """返回 (kind, text, seconds, bytes)。"""
    url = str(input_data.get("url") or "")
    action = str(input_data.get("action") or "")
    if status in ("queued", "running"):
        if capability == "media.download" and action == "probe":
            return "link", "解析中…", 0, 0
        if capability == "video.understand":
            return "link", "视频理解中…", 0, 0
        return "link", "下载中…", 0, 0
    if status == "failed":
        return "link", f"失败：{error or '未知错误'}", 0, 0
    if status == "cancelled":
        return "link", "已取消", 0, 0

    # done
    res = result if isinstance(result, dict) else {}
    title = str(res.get("title") or url or "完成")
    if capability == "media.download" and action != "probe":
        fid = res.get("file_id")
        payload = {
            "v": 1,
            "files": [fid] if fid else [],
            "caption": title,
            "job": True,
            "url": url,
            "duration": res.get("duration"),
            "size": res.get("size"),
        }
        return "video", json.dumps(payload, ensure_ascii=False), int(res.get("duration") or 0), int(res.get("size") or 0)
    if capability == "media.download" and action == "probe":
        return "link", title, 0, 0
    if capability == "video.understand":
        summary = str(res.get("summary") or res.get("text") or title)
        return "link", summary[:2000], 0, 0
    return "link", title, 0, 0
