"""借鉴 qq-bridge 出站闸门思想：只放出用户可见正文。

- 撕掉双层括号草稿 `（（…））` / `((...))`（角色心里话，不对用户说）
- 去掉内部标记行
不接 QQ。
"""
from __future__ import annotations

import re
from typing import Tuple

_DRAFT_RE = re.compile(r"（（.*?））|\(\(.*?\)\)", re.DOTALL)
_INTERNAL_LINE_RE = re.compile(
    r"^\s*(?:\[内部\]|\[debug\]|⟦内部：.*?⟧)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_drafts(text: str) -> Tuple[str, str]:
    """返回 (对用户可见, 撕掉的草稿拼接)。"""
    if not text:
        return "", ""
    drafts = [m.group(0) for m in _DRAFT_RE.finditer(text)]
    visible = _DRAFT_RE.sub("", text)
    visible = _INTERNAL_LINE_RE.sub("", visible)
    visible = re.sub(r"\n{3,}", "\n\n", visible).strip()
    draft_joined = "\n".join(d.strip("（）()") for d in drafts if d.strip())
    return visible, draft_joined
