"""腾讯位置 WebService：逆地理 / 周边。统一缓存 + 限流，避免刷爆个人配额。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import get_settings

# ---- 配额硬闸门（计划约定）----
NEARBY_PER_USER_DAY = 30
REVERSE_PER_USER_DAY = 60
GLOBAL_QPS = 2.0
NEARBY_CACHE_SEC = 6 * 3600
REVERSE_CACHE_SEC = 24 * 3600
EXPLORE_DEDUP_SEC = 120  # 同用户同 geohash 短窗只打一次 explore

_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Any]] = {}
_user_day: Dict[str, Dict[str, Any]] = {}  # uid -> {day, nearby, reverse}
_last_global_ts = 0.0
_explore_dedup: Dict[str, float] = {}  # f"{uid}:{gh5}" -> ts


class PlaceQuotaError(Exception):
    def __init__(self, message: str = "查附近额度用完了，明天再试"):
        super().__init__(message)
        self.message = message


class PlaceConfigError(Exception):
    pass


def _key() -> str:
    s = get_settings()
    k = (getattr(s, "tencent_map_key", None) or "").strip()
    if not k:
        raise PlaceConfigError("未配置 TENCENT_MAP_KEY")
    return k


def _day_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _geohash(lat: float, lng: float, precision: int = 5) -> str:
    """简易 geohash（base32），用于缓存桶。"""
    _BASE = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_min, lat_max = -90.0, 90.0
    lng_min, lng_max = -180.0, 180.0
    bits = []
    even = True
    while len(bits) < precision * 5:
        if even:
            mid = (lng_min + lng_max) / 2
            if lng >= mid:
                bits.append(1)
                lng_min = mid
            else:
                bits.append(0)
                lng_max = mid
        else:
            mid = (lat_min + lat_max) / 2
            if lat >= mid:
                bits.append(1)
                lat_min = mid
            else:
                bits.append(0)
                lat_max = mid
        even = not even
    out = []
    for i in range(0, len(bits), 5):
        idx = 0
        for b in bits[i : i + 5]:
            idx = (idx << 1) | b
        out.append(_BASE[idx])
    return "".join(out)


def _cache_get(key: str) -> Optional[Any]:
    with _lock:
        row = _cache.get(key)
        if not row:
            return None
        exp, val = row
        if exp < time.time():
            _cache.pop(key, None)
            return None
        return val


def _cache_set(key: str, val: Any, ttl: float) -> None:
    with _lock:
        _cache[key] = (time.time() + ttl, val)


def _throttle_global() -> None:
    global _last_global_ts
    with _lock:
        now = time.time()
        wait = (1.0 / GLOBAL_QPS) - (now - _last_global_ts)
        if wait > 0:
            time.sleep(wait)
        _last_global_ts = time.time()


def _bump_user(user_id: str, kind: str) -> None:
    """kind: nearby | reverse。超限抛 PlaceQuotaError。"""
    day = _day_key()
    with _lock:
        st = _user_day.get(user_id) or {"day": day, "nearby": 0, "reverse": 0}
        if st.get("day") != day:
            st = {"day": day, "nearby": 0, "reverse": 0}
        if kind == "nearby" and int(st["nearby"]) >= NEARBY_PER_USER_DAY:
            _user_day[user_id] = st
            raise PlaceQuotaError("今天查附近的次数用完了，明天再试～")
        if kind == "reverse" and int(st["reverse"]) >= REVERSE_PER_USER_DAY:
            _user_day[user_id] = st
            raise PlaceQuotaError("今天定位解析次数用完了，明天再试～")
        st[kind] = int(st[kind]) + 1
        _user_day[user_id] = st


def _http_get(url: str) -> Dict[str, Any]:
    import json

    _throttle_global()
    req = Request(url, headers={"User-Agent": "chat-app-place/1.0"})
    with urlopen(req, timeout=12) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _offline_reverse(lat_f: float, lng_f: float) -> Dict[str, Any]:
    from .place_offline import reverse_approx

    approx = reverse_approx(lat_f, lng_f) or {}
    return {
        "name": str(approx.get("name") or "选中位置"),
        "address": str(approx.get("address") or f"{lat_f:.5f}, {lng_f:.5f}"),
        "lat": lat_f,
        "lng": lng_f,
        "ad_info": {},
        "approx": True,
    }


def reverse_geocode(*, user_id: str, lat: float, lng: float) -> Dict[str, Any]:
    """逆地理。缓存命中不扣用户日配额。腾讯失败/额度满时离线区片名兜底，保证卡片能显示地名。"""
    lat_f, lng_f = float(lat), float(lng)
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        raise ValueError("坐标无效")
    ck = f"rev:{_geohash(lat_f, lng_f, 6)}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    try:
        _bump_user(user_id, "reverse")
    except PlaceQuotaError:
        out = _offline_reverse(lat_f, lng_f)
        _cache_set(ck, out, REVERSE_CACHE_SEC)
        return out

    try:
        key = _key()
        qs = urlencode(
            {
                "location": f"{lat_f},{lng_f}",
                "key": key,
                "get_poi": "0",
            }
        )
        data = _http_get(f"https://apis.map.qq.com/ws/geocoder/v1/?{qs}")
        if int(data.get("status") or -1) != 0:
            msg = str(data.get("message") or "逆地理失败")
            if "上限" in msg or int(data.get("status") or 0) == 121:
                out = _offline_reverse(lat_f, lng_f)
                _cache_set(ck, out, min(REVERSE_CACHE_SEC, 3600))
                return out
            raise RuntimeError(msg)

        result = data.get("result") or {}
        addr = (result.get("address") or "").strip()
        fmt = result.get("formatted_addresses") or {}
        name = (fmt.get("recommend") or fmt.get("rough") or addr or "位置").strip()
        out = {
            "name": name,
            "address": addr,
            "lat": lat_f,
            "lng": lng_f,
            "ad_info": result.get("ad_info") or {},
        }
        _cache_set(ck, out, REVERSE_CACHE_SEC)
        return out
    except PlaceConfigError:
        out = _offline_reverse(lat_f, lng_f)
        _cache_set(ck, out, REVERSE_CACHE_SEC)
        return out
    except Exception:
        out = _offline_reverse(lat_f, lng_f)
        _cache_set(ck, out, min(REVERSE_CACHE_SEC, 3600))
        return out


def nearby_pois(
    *,
    user_id: str,
    lat: float,
    lng: float,
    keyword: str = "",
    radius: int = 2000,
    page_size: int = 15,
) -> Dict[str, Any]:
    """周边：无 keyword 走 explore；有 keyword 走 search。强缓存。"""
    lat_f, lng_f = float(lat), float(lng)
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        raise ValueError("坐标无效")
    kw = (keyword or "").strip()[:40]
    radius = max(200, min(int(radius or 2000), 5000))
    page_size = max(1, min(int(page_size or 15), 20))
    gh = _geohash(lat_f, lng_f, 5)
    mode = f"kw:{kw}" if kw else "explore"
    ck = f"nb:{gh}:{mode}:{radius}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    # explore 短窗去重：选点页连点不重复打腾讯
    if not kw:
        dedup_k = f"{user_id}:{gh}"
        with _lock:
            last = _explore_dedup.get(dedup_k) or 0.0
            if time.time() - last < EXPLORE_DEDUP_SEC:
                # 仍无缓存则允许一次；有短窗标记则返回空列表提示稍候（极少见）
                pass
            else:
                _explore_dedup[dedup_k] = time.time()

    _bump_user(user_id, "nearby")
    key = _key()
    boundary = f"nearby({lat_f},{lng_f},{radius})"
    if kw:
        qs = urlencode(
            {
                "keyword": kw,
                "boundary": boundary,
                "page_size": str(page_size),
                "page_index": "1",
                "key": key,
            }
        )
        url = f"https://apis.map.qq.com/ws/place/v1/search?{qs}"
    else:
        qs = urlencode(
            {
                "boundary": boundary,
                "policy": "1",
                "page_size": str(page_size),
                "page_index": "1",
                "key": key,
            }
        )
        url = f"https://apis.map.qq.com/ws/place/v1/explore?{qs}"

    data = _http_get(url)
    status = int(data.get("status") or -1)
    if status != 0:
        msg = str(data.get("message") or "周边检索失败")
        if "上限" in msg or status == 121:
            raise PlaceQuotaError("地图服务今日额度已满，稍后再试")
        raise RuntimeError(msg)

    items: List[Dict[str, Any]] = []
    for p in data.get("data") or []:
        if not isinstance(p, dict):
            continue
        loc = p.get("location") or {}
        items.append(
            {
                "id": str(p.get("id") or ""),
                "title": str(p.get("title") or ""),
                "address": str(p.get("address") or ""),
                "category": str(p.get("category") or ""),
                "lat": float(loc.get("lat") or 0),
                "lng": float(loc.get("lng") or 0),
                "distance": int(p.get("_distance") or 0),
            }
        )
    out = {
        "lat": lat_f,
        "lng": lng_f,
        "keyword": kw,
        "radius": radius,
        "items": items,
        "count": len(items),
    }
    _cache_set(ck, out, NEARBY_CACHE_SEC)
    return out


def format_pois_for_persona(items: List[Dict[str, Any]], *, limit: int = 8) -> str:
    lines = []
    for i, p in enumerate(items[:limit], 1):
        title = p.get("title") or ""
        cat = p.get("category") or ""
        dist = p.get("distance")
        addr = p.get("address") or ""
        bits = [f"{i}. {title}"]
        if dist is not None:
            bits.append(f"{dist}m")
        if cat:
            bits.append(str(cat).split(":")[-1])
        if addr:
            bits.append(addr)
        lines.append(" · ".join(bits))
    return "\n".join(lines)


def latest_location_in_conv(*, user_id: str, conv_id: str) -> Optional[Dict[str, Any]]:
    """取会话最近一条位置卡片坐标。"""
    import json

    from . import db

    rows = db.fetchall(
        """
        SELECT text, kind FROM messages
        WHERE user_id = ? AND conv_id = ?
        ORDER BY created_at DESC LIMIT 50
        """,
        (user_id, conv_id),
    )
    for r in rows:
        text = r["text"] or ""
        if not text.strip().startswith("{"):
            continue
        try:
            meta = json.loads(text)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("card") != "location":
            continue
        try:
            lat = float(meta.get("lat"))
            lng = float(meta.get("lng"))
        except Exception:
            continue
        return {
            "lat": lat,
            "lng": lng,
            "name": str(meta.get("name") or "位置"),
            "address": str(meta.get("address") or ""),
        }
    return None
