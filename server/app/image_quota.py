"""生图日配额（账号级）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from . import db
from .config import get_settings


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def image_daily_limit() -> int:
    # 环境可调；默认 40
    try:
        from os import environ

        return max(0, int(environ.get("IMAGE_DAILY_LIMIT", "40")))
    except Exception:
        return 40


def count_today_images(user_id: str) -> int:
    day = _today()
    row = db.fetchone(
        """
        SELECT COUNT(*) AS n FROM media_files
        WHERE user_id = ? AND source IN ('image_gen','video_gen','video_gen_fallback_image')
          AND created_at LIKE ?
        """,
        (user_id, f"{day}%"),
    )
    return int(row["n"] if row else 0)


def check_can_generate(user_id: str) -> Dict[str, Any]:
    limit = image_daily_limit()
    used = count_today_images(user_id)
    return {
        "ok": limit <= 0 or used < limit,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used) if limit > 0 else 999,
    }
