from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..security import current_user

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.get("")
def list_devices(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, device_name, created_at, last_active_at
        FROM devices
        WHERE user_id = ?
        ORDER BY last_active_at DESC
        """,
        (user["id"],),
    )
    return [
        {
            "id": r["id"],
            "device_name": r["device_name"],
            "created_at": r["created_at"],
            "last_active_at": r["last_active_at"],
            "current": r["id"] == user.get("device_id"),
        }
        for r in rows
    ]


@router.delete("/{device_id}")
def revoke_device(device_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM devices WHERE id = ? AND user_id = ?",
        (device_id, user["id"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    return {"ok": True, "revoked_current": device_id == user.get("device_id")}


@router.post("/prune")
def prune_devices(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """清理多余登录会话：同名设备只留最近活跃的一条；保留当前设备。"""
    uid = user["id"]
    current_id = user.get("device_id") or ""
    rows = db.fetchall(
        """
        SELECT id, device_name, last_active_at
        FROM devices
        WHERE user_id = ?
        ORDER BY last_active_at DESC
        """,
        (uid,),
    )
    keep: set[str] = set()
    if current_id:
        keep.add(current_id)
    seen_names: set[str] = set()
    for r in rows:
        name = (r["device_name"] or "App").strip() or "App"
        rid = r["id"]
        if rid == current_id:
            seen_names.add(name)
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        keep.add(rid)
    deleted = 0
    for r in rows:
        if r["id"] in keep:
            continue
        db.execute("DELETE FROM devices WHERE id = ? AND user_id = ?", (r["id"], uid))
        deleted += 1
    return {"ok": True, "kept": len(keep), "deleted": deleted}
