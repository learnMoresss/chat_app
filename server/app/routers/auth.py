from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import db
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateMeRequest,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    current_user,
    hash_password,
    hash_token,
    iso,
    new_id,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 登录失败限速（内存；多 worker 时各自计数，仍能挡扫号）
# - IP+用户名：10 分钟 8 次
# - 同一 IP：10 分钟 40 次（防换用户名爆破）
_login_fails: Dict[str, List[float]] = defaultdict(list)
_LOGIN_WINDOW = 600.0
_LOGIN_MAX = 8
_LOGIN_IP_MAX = 40
# 注册：同一 IP 1 小时最多 5 次（开放注册时防批量开号）
_register_hits: Dict[str, List[float]] = defaultdict(list)
_REGISTER_WINDOW = 3600.0
_REGISTER_MAX = 5


def _client_ip(request: Request) -> str:
    # 跳板反代常见 X-Forwarded-For；取最左非空
    xf = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xf:
        return xf
    return request.client.host if request.client else "unknown"


def _login_key(request: Request, username: str) -> str:
    return f"{_client_ip(request)}|{username.strip().lower()}"


def _prune(hits: List[float], window: float) -> List[float]:
    now = time.time()
    return [t for t in hits if now - t < window]


def _check_login_rate(request: Request, username: str) -> None:
    key = _login_key(request, username)
    ip = _client_ip(request)
    _login_fails[key] = _prune(_login_fails[key], _LOGIN_WINDOW)
    _login_fails[f"ip|{ip}"] = _prune(_login_fails[f"ip|{ip}"], _LOGIN_WINDOW)
    if len(_login_fails[key]) >= _LOGIN_MAX or len(_login_fails[f"ip|{ip}"]) >= _LOGIN_IP_MAX:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")


def _record_login_fail(request: Request, username: str) -> None:
    now = time.time()
    _login_fails[_login_key(request, username)].append(now)
    _login_fails[f"ip|{_client_ip(request)}"].append(now)
    # 轻微拖慢爆破节奏（失败才睡）
    time.sleep(0.4)


def _clear_login_fail(request: Request, username: str) -> None:
    _login_fails.pop(_login_key(request, username), None)


def _check_register_rate(request: Request) -> None:
    ip = _client_ip(request)
    key = f"reg|{ip}"
    _register_hits[key] = _prune(_register_hits[key], _REGISTER_WINDOW)
    if len(_register_hits[key]) >= _REGISTER_MAX:
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")


def _record_register(request: Request) -> None:
    _register_hits[f"reg|{_client_ip(request)}"].append(time.time())


def _registration_mode() -> str:
    row = db.fetchone("SELECT value FROM server_settings WHERE key = 'registration_mode'")
    return (row["value"] if row else "open") or "open"


def _user_public(user_row) -> dict:
    avatar = ""
    signature = ""
    cover = ""
    try:
        keys = user_row.keys()
        if "avatar_file_id" in keys:
            avatar = user_row["avatar_file_id"] or ""
        if "signature" in keys:
            signature = user_row["signature"] or ""
        if "moments_cover_file_id" in keys:
            cover = user_row["moments_cover_file_id"] or ""
    except (KeyError, IndexError, TypeError):
        pass
    return {
        "id": user_row["id"],
        "username": user_row["username"],
        "display_name": user_row["display_name"],
        "is_admin": bool(user_row["is_admin"]),
        "avatar_file_id": avatar,
        "signature": signature,
        "moments_cover_file_id": cover,
    }


def _issue_tokens(user_row, device_name: str) -> TokenResponse:
    device_id = new_id("dev_")
    refresh = create_refresh_token()
    now = iso()
    db.execute(
        """
        INSERT INTO devices(id, user_id, device_name, refresh_token_hash, created_at, last_active_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (device_id, user_row["id"], device_name or "App", hash_token(refresh), now, now),
    )
    access = create_access_token(user_id=user_row["id"], device_id=device_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=_user_public(user_row),
    )


def _seed_user(user_id: str, display_name: str) -> None:
    """注册后不再预置默认人设；会话列表空着，由用户自己创建人设。"""
    _ = (user_id, display_name)


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, request: Request) -> TokenResponse:
    _check_register_rate(request)
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(body.password or "") < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    mode = _registration_mode()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="当前关闭注册")
    if mode == "invite":
        code = body.invite_code.strip()
        if not code:
            raise HTTPException(status_code=400, detail="需要邀请码")
        invite = db.fetchone("SELECT * FROM invites WHERE code = ?", (code,))
        if invite is None or invite["used_by"]:
            time.sleep(0.3)
            raise HTTPException(status_code=400, detail="邀请码无效")
        if invite["expires_at"] and invite["expires_at"] < iso():
            raise HTTPException(status_code=400, detail="邀请码已过期")

    existing = db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        time.sleep(0.2)
        raise HTTPException(status_code=409, detail="用户名已被占用")

    count_row = db.fetchone("SELECT COUNT(*) AS c FROM users")
    is_first = (count_row["c"] if count_row else 0) == 0
    user_id = new_id("u_")
    display = body.display_name.strip() or username
    now = iso()
    db.execute(
        """
        INSERT INTO users(id, username, password_hash, display_name, is_admin, is_disabled, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (user_id, username, hash_password(body.password), display, 1 if is_first else 0, now),
    )
    if mode == "invite" and body.invite_code.strip():
        db.execute(
            "UPDATE invites SET used_by = ? WHERE code = ?",
            (user_id, body.invite_code.strip()),
        )
    _seed_user(user_id, display)
    _record_register(request)
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    assert user is not None
    return _issue_tokens(user, body.device_name)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request) -> TokenResponse:
    _check_login_rate(request, body.username)
    user = db.fetchone("SELECT * FROM users WHERE username = ?", (body.username.strip(),))
    if user is None or not verify_password(body.password, user["password_hash"]):
        _record_login_fail(request, body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user["is_disabled"]:
        raise HTTPException(status_code=403, detail="账号已停用")
    _clear_login_fail(request, body.username)
    return _issue_tokens(user, body.device_name)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest) -> TokenResponse:
    token_hash = hash_token(body.refresh_token)
    device = db.fetchone("SELECT * FROM devices WHERE refresh_token_hash = ?", (token_hash,))
    if device is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效")
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (device["user_id"],))
    if user is None or user["is_disabled"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    # 轮换刷新令牌
    new_refresh = create_refresh_token()
    db.execute(
        "UPDATE devices SET refresh_token_hash = ?, last_active_at = ? WHERE id = ?",
        (hash_token(new_refresh), iso(), device["id"]),
    )
    access = create_access_token(user_id=user["id"], device_id=device["id"])
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        user=_user_public(user),
    )


@router.post("/logout")
def logout(body: RefreshRequest) -> dict:
    token_hash = hash_token(body.refresh_token)
    db.execute("DELETE FROM devices WHERE refresh_token_hash = ?", (token_hash,))
    return {"ok": True}


@router.get("/me")
def get_me(user: dict = Depends(current_user)) -> dict:
    row = db.fetchone("SELECT * FROM users WHERE id = ?", (user["id"],))
    if row is None:
        raise HTTPException(status_code=401, detail="账号不可用")
    return _user_public(row)


@router.get("/me/capabilities")
def get_my_capabilities(user: dict = Depends(current_user)) -> dict:
    """当前用户已注册（系统有 + 已启用）的产品能力。"""
    from ..product_capabilities import list_for_user

    items = list_for_user(user["id"], is_admin=bool(user.get("is_admin")))
    return {
        "capabilities": items,
        "registered": [c["id"] for c in items if c.get("registered")],
    }


def _owned_media_or_empty(user_id: str, file_id: str) -> str:
    fid = (file_id or "").strip()
    if not fid:
        return ""
    media = db.fetchone(
        "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
        (fid, user_id),
    )
    if media is None:
        raise HTTPException(status_code=400, detail="媒体文件不存在")
    return fid


@router.patch("/me")
def update_me(body: UpdateMeRequest, user: dict = Depends(current_user)) -> dict:
    row = db.fetchone("SELECT * FROM users WHERE id = ?", (user["id"],))
    if row is None:
        raise HTTPException(status_code=401, detail="账号不可用")
    display = row["display_name"]
    if body.display_name is not None:
        display = body.display_name.strip() or display
    keys = row.keys()
    avatar = (row["avatar_file_id"] or "") if "avatar_file_id" in keys else ""
    signature = (row["signature"] or "") if "signature" in keys else ""
    cover = (row["moments_cover_file_id"] or "") if "moments_cover_file_id" in keys else ""
    if body.avatar_file_id is not None:
        avatar = _owned_media_or_empty(user["id"], body.avatar_file_id)
    if body.signature is not None:
        signature = body.signature.strip()[:120]
    if body.moments_cover_file_id is not None:
        cover = _owned_media_or_empty(user["id"], body.moments_cover_file_id)
    db.execute(
        """
        UPDATE users
        SET display_name = ?, avatar_file_id = ?, signature = ?, moments_cover_file_id = ?
        WHERE id = ?
        """,
        (display, avatar, signature, cover, user["id"]),
    )
    updated = db.fetchone("SELECT * FROM users WHERE id = ?", (user["id"],))
    return _user_public(updated)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: dict = Depends(current_user),
) -> dict:
    row = db.fetchone("SELECT * FROM users WHERE id = ?", (user["id"],))
    if row is None:
        raise HTTPException(status_code=401, detail="账号不可用")
    if not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(body.new_password), user["id"]),
    )
    # 踢掉其他设备，保留当前
    db.execute(
        "DELETE FROM devices WHERE user_id = ? AND id != ?",
        (user["id"], user["device_id"]),
    )
    return {"ok": True}
