"""原始档案：聊天 / 语音终稿 / 工具结果，不依赖抽取成功。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .. import db
from ..security import iso, new_id


def archive_raw(
    *,
    user_id: str,
    persona_id: str = "",
    conv_id: str = "",
    kind: str = "chat",
    role: str = "user",
    text: str = "",
    source_ref: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    rid = new_id("raw_")
    db.execute(
        """
        INSERT INTO raw_records(
          id, user_id, persona_id, conv_id, kind, role, text, source_ref, meta_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rid,
            user_id,
            persona_id or "",
            conv_id or "",
            kind or "chat",
            role or "user",
            (text or "")[:20000],
            source_ref or "",
            json.dumps(meta or {}, ensure_ascii=False),
            iso(),
        ),
    )
    return rid
