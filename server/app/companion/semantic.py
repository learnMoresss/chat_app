"""语义检索通道（阶段5）：优先 sqlite-vec；不可用则空列表。"""
from __future__ import annotations

import os
from typing import Any, Dict, List

_ENABLED = (os.environ.get("MEMORY_SEMANTIC") or "").strip() in ("1", "true", "yes")


def semantic_available() -> bool:
    if not _ENABLED:
        return False
    try:
        import sqlite_vec  # noqa: F401

        return True
    except Exception:
        return False


def semantic_search(
    user_id: str,
    persona_id: str,
    query: str,
    *,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """返回记忆行；无模型/无扩展时返回 []，不影响词面通道。"""
    if not semantic_available() or not (query or "").strip():
        return []
    # 占位：嵌入模型与 vec 表需运维侧装好后接入；当前安全空实现
    _ = (user_id, persona_id, limit)
    return []
