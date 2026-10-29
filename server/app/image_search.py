"""网络搜图：Bing / DuckDuckGo → 直链列表（依赖 better-bing-image-downloader）。"""
from __future__ import annotations

import logging
import tempfile
from typing import List, Optional
from urllib.parse import urlparse

from .config import get_settings

log = logging.getLogger("image_search")

_BAD_HOST_FRAGMENTS = (
    "stock.adobe.com",
    "shutterstock.com",
    "gettyimages.",
    "alamy.com",
    "istockphoto.com",
    "dreamstime.com",
    "588ku.com",
    "nipic.com",
    "nipic.cn",
    "ooopic.com",
    "vcg.com",
    "58pic.com",
)


def _host_ok(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return not any(b in host for b in _BAD_HOST_FRAGMENTS)


def _dedupe(urls: List[str], *, limit: int) -> List[str]:
    seen = set()
    out: List[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        if not _host_ok(u):
            continue
        key = u.split("?")[0].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _search_bing(query: str, *, limit: int, timeout: float, image_filter: str) -> List[str]:
    from better_bing_image_downloader import Bing

    tmp = tempfile.mkdtemp(prefix="bbid-search-")
    engine = Bing(
        query=query,
        limit=max(limit, 1),
        output_dir=tmp,
        adult="off",
        timeout=int(max(5, timeout)),
        filter=image_filter or "",
        verbose=False,
        badsites=list(_BAD_HOST_FRAGMENTS),
        name="x",
        max_workers=1,
        force_replace=False,
        mkt="zh-CN",
    )
    links: List[str] = []
    page = 0
    # Bing PAGE_SIZE ≈ 35；多翻几页凑够 limit
    while len(links) < limit and page < 4:
        try:
            html = engine._fetch_page(page)  # noqa: SLF001 — 库无公开 search_urls
        except Exception as exc:
            log.warning("bing page %s failed: %s", page, exc)
            break
        batch = engine._extract_links(html) or []  # noqa: SLF001
        if not batch:
            break
        links.extend(batch)
        page += 1
    return _dedupe(links, limit=limit)


def _search_ddg(query: str, *, limit: int, timeout: float) -> List[str]:
    from better_bing_image_downloader import DuckDuckGo

    tmp = tempfile.mkdtemp(prefix="bbid-ddg-")
    engine = DuckDuckGo(
        query=query,
        limit=max(limit, 1),
        output_dir=tmp,
        timeout=int(max(5, timeout)),
        verbose=False,
        badsites=list(_BAD_HOST_FRAGMENTS),
        name="x",
        max_workers=1,
        force_replace=False,
        safe_search="moderate",
        region="wt-wt",
    )
    links: List[str] = []
    try:
        vqd = engine._fetch_vqd()  # noqa: SLF001
    except Exception as exc:
        log.warning("ddg vqd failed: %s", exc)
        return []
    offset = 0
    for _ in range(4):
        if len(links) >= limit:
            break
        try:
            batch = engine._fetch_page(vqd, offset) or []  # noqa: SLF001
        except Exception as exc:
            log.warning("ddg page offset=%s failed: %s", offset, exc)
            break
        if not batch:
            break
        links.extend(batch)
        offset += len(batch)
    return _dedupe(links, limit=limit)

def search_image_urls(
    query: str,
    *,
    limit: Optional[int] = None,
    engine: Optional[str] = None,
) -> List[str]:
    """搜图返回直链。Bing 空结果时自动回落 DuckDuckGo。"""
    q = (query or "").strip()
    if not q:
        return []
    s = get_settings()
    lim = int(limit if limit is not None else (s.image_search_limit or 9))
    lim = max(1, min(lim, 24))
    eng = (engine or s.image_search_engine or "bing").strip().lower()
    timeout = float(s.image_search_timeout_seconds or 25)
    image_filter = (s.image_search_filter or "clipart").strip()

    urls: List[str] = []
    try:
        if eng in ("duckduckgo", "ddg"):
            urls = _search_ddg(q, limit=lim, timeout=timeout)
        else:
            urls = _search_bing(q, limit=lim, timeout=timeout, image_filter=image_filter)
            if len(urls) < max(3, lim // 2):
                more = _search_ddg(q, limit=lim, timeout=timeout)
                urls = _dedupe(urls + more, limit=lim)
    except Exception as exc:
        log.warning("search_image_urls failed (%s): %s", eng, exc)
        if eng not in ("duckduckgo", "ddg"):
            try:
                urls = _search_ddg(q, limit=lim, timeout=timeout)
            except Exception as exc2:
                log.warning("ddg fallback failed: %s", exc2)
                urls = []
    return urls
