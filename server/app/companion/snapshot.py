"""有界陪伴快照：委托 ContextCompiler（唯一组装）。"""
from __future__ import annotations

from typing import Any, Dict

from .context_compiler import compile_context


def build_companion_snapshot(
    user_id: str,
    persona_id: str,
    *,
    query: str = "",
) -> Dict[str, Any]:
    return compile_context(user_id, persona_id, query=query)
