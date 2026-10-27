from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

from ..config import get_settings


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_download_url(*, file_id: str, user_id: str, ttl_seconds: int = 600) -> str:
    """插件与 App 用的短期下载链接（单机也走 URL，不传本机路径）。"""
    settings = get_settings()
    exp = int(time.time()) + ttl_seconds
    payload = f"dl|{file_id}|{user_id}|{exp}"
    sig = _sign(payload)
    qs = urlencode({"file_id": file_id, "user_id": user_id, "exp": exp, "sig": sig})
    return f"{settings.public_base_url.rstrip('/')}/api/v1/media/download?{qs}"


def make_upload_url(*, file_id: str, user_id: str, ttl_seconds: int = 600) -> str:
    settings = get_settings()
    exp = int(time.time()) + ttl_seconds
    payload = f"ul|{file_id}|{user_id}|{exp}"
    sig = _sign(payload)
    qs = urlencode({"file_id": file_id, "user_id": user_id, "exp": exp, "sig": sig})
    return f"{settings.public_base_url.rstrip('/')}/api/v1/media/upload?{qs}"


def verify_download(*, file_id: str, user_id: str, exp: int, sig: str) -> bool:
    if int(exp) < int(time.time()):
        return False
    payload = f"dl|{file_id}|{user_id}|{exp}"
    return hmac.compare_digest(_sign(payload), sig)


def verify_upload(*, file_id: str, user_id: str, exp: int, sig: str) -> bool:
    if int(exp) < int(time.time()):
        return False
    payload = f"ul|{file_id}|{user_id}|{exp}"
    return hmac.compare_digest(_sign(payload), sig)
