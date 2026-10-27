"""位置：逆地理 / 附近（腾讯 WebService，经 place_tencent 限流缓存）。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import place_tencent as place
from ..security import current_user

router = APIRouter(prefix="/api/v1/place", tags=["place"])


@router.get("/reverse")
def place_reverse(
    lat: float = Query(...),
    lng: float = Query(...),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    try:
        return place.reverse_geocode(user_id=user["id"], lat=lat, lng=lng)
    except place.PlaceConfigError as e:
        raise HTTPException(503, str(e)) from e
    except place.PlaceQuotaError as e:
        raise HTTPException(429, e.message) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"逆地理失败：{e}") from e


@router.get("/nearby")
def place_nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    keyword: str = Query(""),
    radius: int = Query(2000, ge=200, le=5000),
    page_size: int = Query(15, ge=1, le=20),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    try:
        return place.nearby_pois(
            user_id=user["id"],
            lat=lat,
            lng=lng,
            keyword=keyword,
            radius=radius,
            page_size=page_size,
        )
    except place.PlaceConfigError as e:
        raise HTTPException(503, str(e)) from e
    except place.PlaceQuotaError as e:
        raise HTTPException(429, e.message) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"附近检索失败：{e}") from e
