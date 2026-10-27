from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import current_user, iso

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_admin(user: Dict[str, Any]) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员")


class RegistrationModeBody(BaseModel):
    mode: Literal["open", "invite", "closed"]


class InviteCreate(BaseModel):
    expires_days: Optional[int] = Field(default=7, ge=0, le=365)
    count: int = Field(default=1, ge=1, le=20)


class UserDisableBody(BaseModel):
    disabled: bool


@router.get("/settings")
def get_settings_admin(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _require_admin(user)
    row = db.fetchone("SELECT value FROM server_settings WHERE key = 'registration_mode'")
    mode = (row["value"] if row else "open") or "open"
    allow = db.fetchone("SELECT value FROM server_settings WHERE key = 'capability_allow_dangerous'")
    users = db.fetchone("SELECT COUNT(*) AS c FROM users")
    invites = db.fetchone("SELECT COUNT(*) AS c FROM invites WHERE used_by IS NULL")
    return {
        "registration_mode": mode,
        "capability_allow_dangerous": (allow["value"] if allow else "") or "",
        "user_count": int(users["c"] if users else 0),
        "unused_invites": int(invites["c"] if invites else 0),
    }


@router.put("/settings/registration-mode")
def set_registration_mode(
    body: RegistrationModeBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    db.execute(
        """
        INSERT INTO server_settings(key, value) VALUES('registration_mode', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (body.mode,),
    )
    return {"registration_mode": body.mode}


@router.get("/users")
def list_users(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    _require_admin(user)
    rows = db.fetchall(
        """
        SELECT id, username, display_name, is_admin, is_disabled, created_at
        FROM users
        ORDER BY created_at
        """
    )
    return [
        {
            "id": r["id"],
            "username": r["username"],
            "display_name": r["display_name"],
            "is_admin": bool(r["is_admin"]),
            "is_disabled": bool(r["is_disabled"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.patch("/users/{user_id}")
def set_user_disabled(
    user_id: str,
    body: UserDisableBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    target = db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target["id"] == user["id"] and body.disabled:
        raise HTTPException(status_code=400, detail="不能停用自己")
    if target["is_admin"] and body.disabled:
        admins = db.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_admin = 1 AND is_disabled = 0")
        if int(admins["c"] if admins else 0) <= 1:
            raise HTTPException(status_code=400, detail="不能停用唯一管理员")
    db.execute("UPDATE users SET is_disabled = ? WHERE id = ?", (1 if body.disabled else 0, user_id))
    if body.disabled:
        db.execute("DELETE FROM devices WHERE user_id = ?", (user_id,))
    row = db.fetchone("SELECT id, username, display_name, is_admin, is_disabled, created_at FROM users WHERE id = ?", (user_id,))
    assert row is not None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "is_disabled": bool(row["is_disabled"]),
        "created_at": row["created_at"],
    }


@router.get("/invites")
def list_invites(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    _require_admin(user)
    rows = db.fetchall(
        """
        SELECT i.code, i.created_at, i.expires_at, i.used_by,
               u.username AS used_by_username
        FROM invites i
        LEFT JOIN users u ON u.id = i.used_by
        ORDER BY i.created_at DESC
        LIMIT 200
        """
    )
    now = iso()
    out = []
    for r in rows:
        expired = bool(r["expires_at"] and r["expires_at"] < now)
        out.append(
            {
                "code": r["code"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "used_by": r["used_by"],
                "used_by_username": r["used_by_username"],
                "status": "used" if r["used_by"] else ("expired" if expired else "unused"),
            }
        )
    return out


@router.post("/invites")
def create_invites(
    body: InviteCreate,
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    _require_admin(user)
    now = datetime.now(timezone.utc)
    expires = None
    if body.expires_days and body.expires_days > 0:
        expires = (now + timedelta(days=body.expires_days)).isoformat()
    created = []
    for _ in range(body.count):
        code = secrets.token_urlsafe(10).replace("-", "").replace("_", "")[:12].upper()
        db.execute(
            """
            INSERT INTO invites(code, created_by, used_by, expires_at, created_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (code, user["id"], expires, now.isoformat()),
        )
        created.append(
            {
                "code": code,
                "created_at": now.isoformat(),
                "expires_at": expires,
                "used_by": None,
                "used_by_username": None,
                "status": "unused",
            }
        )
    return created


@router.delete("/invites/{code}")
def revoke_invite(code: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _require_admin(user)
    row = db.fetchone("SELECT * FROM invites WHERE code = ?", (code,))
    if row is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    if row["used_by"]:
        raise HTTPException(status_code=400, detail="已使用的邀请码不能作废")
    db.execute("DELETE FROM invites WHERE code = ?", (code,))
    return {"ok": True}


class DangerousAllowBody(BaseModel):
    """逗号分隔能力 id；空=全部高危拒绝；*=全部放行。"""

    allow: str = Field(default="", max_length=2000)


@router.put("/settings/capability-allow-dangerous")
def set_capability_allow_dangerous(
    body: DangerousAllowBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    val = (body.allow or "").strip()
    db.execute(
        """
        INSERT INTO server_settings(key, value) VALUES('capability_allow_dangerous', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (val,),
    )
    return {"capability_allow_dangerous": val}


class UserCapsBody(BaseModel):
    """capability_id → enabled；只改系统已有项。"""

    grants: Dict[str, bool] = Field(default_factory=dict)


@router.get("/capabilities/catalog")
def product_capability_catalog(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """系统能力目录：有则 system_available=true（可分配给子用户）。"""
    _require_admin(user)
    from ..product_capabilities import catalog_for_admin

    return {"capabilities": catalog_for_admin()}


@router.get("/users/{user_id}/capabilities")
def get_user_capabilities(user_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _require_admin(user)
    target = db.fetchone("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    from ..product_capabilities import list_for_user

    return {
        "user_id": user_id,
        "username": target["username"],
        "capabilities": list_for_user(user_id, is_admin=bool(target["is_admin"])),
    }


@router.put("/users/{user_id}/capabilities")
def put_user_capabilities(
    user_id: str,
    body: UserCapsBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    target = db.fetchone("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    from ..product_capabilities import set_user_grants

    caps = set_user_grants(user_id, body.grants or {})
    return {"user_id": user_id, "username": target["username"], "capabilities": caps}


@router.get("/wake-buffers")
def list_wake_buffers(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """M4：管理台可见只记录/跳过缓冲（当前登录用户名下）。"""
    from ..wake_buffer import list_buffers_for_user

    items = list_buffers_for_user(user["id"])
    return {"items": items}


@router.get("/wake-buffers/{conv_id}")
def peek_wake_buffer(conv_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    from ..wake_buffer import peek

    return {"conv_id": conv_id, "entries": peek(user["id"], conv_id)}
