"""简易 Open Graph / 页面标题抓取，供聊天链接分享卡使用。"""
from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

import httpx

_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:(title|description|image)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:(title|description|image)["\']',
    re.I,
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)
_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)


def fetch_og(url: str, *, timeout: float = 8.0) -> Dict[str, Any]:
    """返回 {title, description, image, url}；失败时至少带回 url。"""
    out: Dict[str, Any] = {"url": url, "title": "", "description": "", "image": ""}
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return out
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ChatAppLinkBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            res = client.get(url)
            if res.status_code >= 400:
                out["title"] = parsed.netloc
                return out
            ctype = (res.headers.get("content-type") or "").lower()
            if "html" not in ctype and "text" not in ctype and ctype:
                out["title"] = parsed.netloc
                return out
            html = res.text[:200_000]
    except Exception:
        out["title"] = urlparse(url).netloc or url
        return out

    found: Dict[str, str] = {}
    for m in _OG_RE.finditer(html):
        found[m.group(1).lower()] = m.group(2).strip()
    for m in _OG_RE_ALT.finditer(html):
        found[m.group(2).lower()] = m.group(1).strip()

    title = found.get("title") or ""
    if not title:
        tm = _TITLE_RE.search(html)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
    desc = found.get("description") or ""
    if not desc:
        dm = _DESC_RE.search(html)
        if dm:
            desc = dm.group(1).strip()
    image = found.get("image") or ""
    if image and not image.startswith(("http://", "https://", "data:")):
        image = urljoin(url, image)

    out["title"] = (title or urlparse(url).netloc or url)[:200]
    out["description"] = desc[:400]
    out["image"] = image[:800]
    return out
