"""天气：默认模拟；可选真实源（失败标 unknown）。"""
from __future__ import annotations

import hashlib
import os
from datetime import date
from typing import Any, Dict, Optional

_SIM_POOL = ("晴", "多云", "阴", "小雨", "中雨", "阵雨", "晴转多云")


def simulated_weather(*, user_id: str, persona_id: str, day: Optional[str] = None) -> Dict[str, Any]:
    d = day or date.today().isoformat()
    seed = f"{user_id}|{persona_id}|{d}".encode()
    h = int(hashlib.sha256(seed).hexdigest()[:8], 16)
    label = _SIM_POOL[h % len(_SIM_POOL)]
    outdoor_ok = label not in ("小雨", "中雨", "阵雨")
    return {
        "label": label,
        "source": "sim",
        "outdoor_ok": outdoor_ok,
        "day": d,
    }


async def fetch_real_weather(*, location: str = "") -> Dict[str, Any]:
    """可选真实天气。默认关闭；设 WORLD_WEATHER_REAL=1 且有 location 才尝试。"""
    if (os.environ.get("WORLD_WEATHER_REAL") or "").strip() not in ("1", "true", "yes"):
        return {"label": "未知", "source": "unknown", "outdoor_ok": True}
    loc = (location or "").strip()
    if not loc:
        return {"label": "未知", "source": "unknown", "outdoor_ok": True}
    # Open-Meteo geocoding + forecast（免 key）；失败标未知
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            g = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": loc, "count": 1, "language": "zh"},
            )
            g.raise_for_status()
            data = g.json()
            results = data.get("results") or []
            if not results:
                return {"label": "未知", "source": "unknown", "outdoor_ok": True}
            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            w = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "weather_code",
                    "timezone": "auto",
                },
            )
            w.raise_for_status()
            code = int((w.json().get("current") or {}).get("weather_code") or 0)
            label, outdoor_ok = _code_to_label(code)
            return {"label": label, "source": "open-meteo", "outdoor_ok": outdoor_ok}
    except Exception:
        return {"label": "未知", "source": "unknown", "outdoor_ok": True}


def _code_to_label(code: int) -> tuple[str, bool]:
    # WMO weather interpretation
    if code == 0:
        return "晴", True
    if code in (1, 2):
        return "晴转多云", True
    if code == 3:
        return "阴", True
    if code in (51, 53, 55, 61, 63, 80, 81):
        return "小雨", False
    if code in (65, 82):
        return "中雨", False
    return "多云", True


async def resolve_weather(
    *,
    user_id: str,
    persona_id: str,
    location: str = "",
) -> Dict[str, Any]:
    if (os.environ.get("WORLD_WEATHER_REAL") or "").strip() in ("1", "true", "yes"):
        real = await fetch_real_weather(location=location)
        if real.get("source") != "unknown":
            return real
    return simulated_weather(user_id=user_id, persona_id=persona_id)
