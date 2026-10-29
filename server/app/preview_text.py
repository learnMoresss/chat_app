"""会话列表 preview：媒体消息存的是 JSON，列表要显示可读摘要。"""
from __future__ import annotations

import json
from typing import Any, Optional


def conversation_preview(*, kind: str, text: str, seconds: int = 0) -> str:
    kind = (kind or "text").strip().lower()
    raw = (text or "").strip()
    if kind in ("image", "video", "file", "voice", "link") and raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            files = data.get("files") or []
            cap = (data.get("caption") or "").strip()
            if kind == "image":
                n = len(files) if isinstance(files, list) else 1
                base = f"[图片×{n}]" if n > 1 else "[图片]"
                return f"{base} {cap}".strip()[:200] if cap else base
            if kind == "video":
                return f"[视频] {cap}".strip()[:200] if cap else "[视频]"
            if kind == "file":
                return f"[文件] {cap}".strip()[:200] if cap else "[文件]"
            if kind == "voice":
                sec = int(data.get("seconds") or seconds or 0)
                if str(data.get("kind_extra") or "") == "sing":
                    return f"[唱歌] {sec}\"" if sec > 0 else "[唱歌]"
                return f"[语音] {sec}\"" if sec > 0 else "[语音]"
            if kind == "link":
                if str(data.get("card") or "") == "voice_call":
                    body = (data.get("body") or "").strip()
                    if body:
                        return body[:200]
                    dur = int(data.get("duration_sec") or seconds or 0)
                    if dur > 0:
                        m, s = divmod(dur, 60)
                        return f"通话时长 {m:02d}:{s:02d}"
                    return "语音通话"
                title = (data.get("title") or cap or data.get("body") or "").strip()
                return (title or "[链接]")[:200]
    if kind == "voice":
        return f"[语音] {seconds}\"" if seconds > 0 else "[语音]"
    if kind == "image":
        return "[图片]"
    if kind == "video":
        return "[视频]"
    if kind == "file":
        return "[文件]"
    if kind == "link":
        return "[链接]"
    if kind == "system":
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("body"):
                    return str(data["body"])[:200]
            except Exception:
                pass
        return raw[:200] or "[系统]"
    return raw[:200]


def sanitize_stored_preview(preview: Optional[str]) -> str:
    """读库时兜底：旧数据可能已把 JSON 写进 preview。"""
    raw = (preview or "").strip()
    if not raw.startswith("{"):
        return raw[:200]
    try:
        data = json.loads(raw)
    except Exception:
        return raw[:200]
    if not isinstance(data, dict) or "files" not in data:
        return raw[:200]
    return conversation_preview(kind="image", text=raw)
