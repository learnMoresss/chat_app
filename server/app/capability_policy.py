"""高危能力 fail-closed：默认拒绝，管理台 server_settings 放行。"""
from __future__ import annotations

from typing import Set

from . import db

# 能力 id / 插件名包含这些关键词时视为高危
DANGEROUS_KEYWORDS = (
    "shell",
    "exec",
    "desktop_control",
    "adb",
    "u2",
    "filesystem_write",
    "sudo",
)


def allowed_dangerous() -> Set[str]:
    row = db.fetchone("SELECT value FROM server_settings WHERE key = 'capability_allow_dangerous'")
    if not row or not row["value"]:
        return set()
    return {x.strip() for x in str(row["value"]).split(",") if x.strip()}


def is_dangerous(capability_id: str) -> bool:
    cid = (capability_id or "").lower()
    return any(k in cid for k in DANGEROUS_KEYWORDS)


def assert_allowed(capability_id: str) -> None:
    if not is_dangerous(capability_id):
        return
    allow = allowed_dangerous()
    if capability_id in allow or "*" in allow:
        return
    raise PermissionError(
        f"高危能力未放行：{capability_id}（管理台设置 capability_allow_dangerous）"
    )
