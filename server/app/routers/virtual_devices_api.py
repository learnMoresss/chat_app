"""虚拟电脑/手机 API：生命周期 + 观看/控制租约 + 操控动作（供 DSH）。

隔离原则：
- 用户 JWT：只能碰自己的设备
- DSH 服务令牌：必须带 X-Chat-User-Id，别名 desktop/phone 只解析该用户设备
- 控制动作建议带 X-Lease-Token（人设/会话级 fencing）
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..config import get_settings
from ..security import current_user, decode_access_token
from .. import db
from .. import virtual_devices as vd
from .. import device_actions as act
from ..vd_ticket import resolve_ticket

router = APIRouter(prefix="/api/v1/virtual-devices", tags=["virtual-devices"])
_optional_bearer = HTTPBearer(auto_error=False)

_DESKTOP_ALIAS = ("_", "default", "desktop", "pc", "host", "vm")
_PHONE_ALIAS = ("android", "phone", "mobile")

_LIVE_HTML = """<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Device Live</title>
<style>html,body{margin:0;background:#111;height:100%;color:#aaa;font:14px sans-serif}
img{display:block;max-width:100%;max-height:100vh;margin:0 auto}
#st{position:fixed;left:8px;bottom:8px;opacity:.7}</style></head><body>
<img id=s alt=screen><div id=st>live</div>
<script>
const i=document.getElementById('s'), st=document.getElementById('st');
const q=location.search||'';
function tick(){i.src='shot.png'+q+(q?'&':'?')+'t='+Date.now()}
i.onload=()=>{st.textContent='ok';setTimeout(tick,700)};
i.onerror=()=>{st.textContent='retry';setTimeout(tick,1500)};
tick();
</script></body></html>"""


def _actor(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    x_dsh_token: Optional[str] = Header(default=None, alias="X-DSH-Token"),
    x_chat_user_id: Optional[str] = Header(default=None, alias="X-Chat-User-Id"),
    x_persona_id: Optional[str] = Header(default=None, alias="X-Persona-Id"),
    x_conv_id: Optional[str] = Header(default=None, alias="X-Conv-Id"),
    x_lease_token: Optional[str] = Header(default=None, alias="X-Lease-Token"),
    x_vd_ticket: Optional[str] = Header(default=None, alias="X-Vd-Ticket"),
    access_token: str = Query(default="", alias="access_token"),
    token: str = Query(default="", alias="token"),
) -> Dict[str, Any]:
    """用户 JWT 或 DSH 网关服务令牌均可。live 页可用 ?access_token= 打开。"""
    settings = get_settings()
    raw = (
        (creds.credentials if creds else "")
        or (x_dsh_token or "")
        or (access_token or "").strip()
        or (token or "").strip()
    )
    svc = (settings.dsh_gateway_token or "").strip()
    if svc and raw and raw == svc:
        ticket = resolve_ticket(x_vd_ticket or "")
        if ticket:
            # 票据硬绑定：忽略可伪造的 X-Chat-User-Id
            return {
                "id": ticket["user_id"],
                "is_service": True,
                "persona_id": ticket.get("persona_id") or (x_persona_id or "").strip(),
                "conv_id": ticket.get("conv_id") or (x_conv_id or "").strip(),
                "lease_token": (x_lease_token or "").strip(),
                "ticket": True,
            }
        uid = (x_chat_user_id or "").strip()
        if not uid:
            raise HTTPException(
                status_code=400,
                detail="DSH 必须带 X-Vd-Ticket 或 X-Chat-User-Id（禁止跨用户乱解析）",
            )
        return {
            "id": uid,
            "is_service": True,
            "persona_id": (x_persona_id or "").strip(),
            "conv_id": (x_conv_id or "").strip(),
            "lease_token": (x_lease_token or "").strip(),
        }
    if not raw:
        raise HTTPException(status_code=401, detail="需要登录或 DSH 服务令牌")
    payload = decode_access_token(raw)
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (payload["sub"],))
    if user is None or user["is_disabled"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    return {
        "id": user["id"],
        "is_service": False,
        "username": user["username"],
        "persona_id": (x_persona_id or "").strip(),
        "conv_id": (x_conv_id or "").strip(),
        "lease_token": (x_lease_token or "").strip(),
    }


class CreateBody(BaseModel):
    label: str = ""
    kind: str = "desktop"
    persona_id: str = ""


class LeaseBody(BaseModel):
    role: str = Field(description="view | control")
    ttl_sec: int = 120
    force: bool = False
    persona_id: str = ""
    conv_id: str = ""


class HeartbeatBody(BaseModel):
    token: str
    ttl_sec: int = 120


class TokenBody(BaseModel):
    token: str


class UsbRegisterBody(BaseModel):
    serial: str = Field(min_length=1, max_length=128)
    label: str = ""
    persona_id: str = ""


class EnsureBody(BaseModel):
    kind: str = "desktop"
    persona_id: str = ""
    label: str = ""
    start: bool = False
    # True=没有就创建（仅管理/显式）；默认 False，走聊天批准
    create: bool = False
    conv_id: str = ""
    reason: str = ""


class RequestCreateBody(BaseModel):
    kind: str = "desktop"
    persona_id: str = ""
    conv_id: str = ""
    label: str = ""
    reason: str = ""


@router.get("")
def list_mine(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """当前登录用户的设备。管理员额外带 all（全站未销毁实例）。"""
    items = vd.list_devices(user["id"])
    out: Dict[str, Any] = {
        "devices": items,
        "items": items,
        "scope": "mine",
        "user_id": user["id"],
    }
    if user.get("is_admin"):
        all_items = vd.list_all_devices(include_destroyed=False)
        out["all"] = all_items
        out["all_count"] = len(all_items)
    return out


@router.get("/admin/all")
def list_admin_all(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员")
    items = vd.list_all_devices(include_destroyed=False)
    return {"devices": items, "items": items, "total": len(items)}


@router.post("")
def create(body: CreateBody, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """管理台手动创建：必须绑定人设；同人设同 kind 已有则复用。"""
    pid = (body.persona_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="必须指定 persona_id，设备与人设挂钩")
    existing = vd.find_persona_device(user["id"], persona_id=pid, kind=body.kind)
    if existing is not None:
        return existing
    return vd.ensure_user_device(
        user["id"],
        kind=body.kind,
        persona_id=pid,
        label=body.label,
        create=True,
    )


@router.post("/ensure")
async def ensure_device(body: EnsureBody, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    """查找人设设备；没有则发聊天批准卡片（默认不自动创建）。"""
    from ..product_capabilities import is_enabled_for_user

    need = "virtual_phone" if body.kind == "android" else "virtual_desktop"
    urow = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (actor["id"],))
    is_admin = bool(urow and urow["is_admin"]) or bool(actor.get("is_admin"))
    if not is_enabled_for_user(actor["id"], need, is_admin=is_admin):
        raise HTTPException(status_code=403, detail=f"{need} 能力未开通")
    pid = (body.persona_id or actor.get("persona_id") or "").strip()
    cid = (body.conv_id or actor.get("conv_id") or "").strip()
    try:
        d = vd.ensure_user_device(
            actor["id"],
            kind=body.kind,
            persona_id=pid,
            label=body.label,
            create=bool(body.create) and (not actor.get("is_service") or is_admin),
        )
    except HTTPException as exc:
        if getattr(exc, "status_code", None) == 404 and cid and pid:
            out = await vd.emit_device_create_request(
                user_id=actor["id"],
                persona_id=pid,
                conv_id=cid,
                kind=body.kind,
                reason=body.reason or "需要用设备帮你操作",
                label=body.label,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "awaiting_user_approval",
                    "message": "已向用户发送创建设备申请，批准后才能使用",
                    **out,
                },
            ) from exc
        raise
    if body.start and d.get("status") != "running" and d.get("backend") != "usb_adb":
        d = vd.start_device(d["id"], actor["id"])
    return d


@router.post("/request-create")
async def request_create(body: RequestCreateBody, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    """AI/服务主动申请创建设备（聊天卡片等用户批准）。"""
    from ..product_capabilities import is_enabled_for_user

    need = "virtual_phone" if body.kind == "android" else "virtual_desktop"
    urow = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (actor["id"],))
    is_admin = bool(urow and urow["is_admin"])
    if not is_enabled_for_user(actor["id"], need, is_admin=is_admin):
        raise HTTPException(status_code=403, detail=f"{need} 能力未开通")
    pid = (body.persona_id or actor.get("persona_id") or "").strip()
    cid = (body.conv_id or actor.get("conv_id") or "").strip()
    if not pid or not cid:
        raise HTTPException(status_code=400, detail="需要 persona_id 与 conv_id")
    return await vd.emit_device_create_request(
        user_id=actor["id"],
        persona_id=pid,
        conv_id=cid,
        kind=body.kind,
        reason=body.reason,
        label=body.label,
    )


@router.post("/requests/{request_id}/approve")
def approve_request(request_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return vd.approve_device_request(user_id=user["id"], request_id=request_id)


@router.post("/requests/{request_id}/deny")
def deny_request(request_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return vd.deny_device_request(user_id=user["id"], request_id=request_id)


@router.get("/adb-devices")
def adb_devices(actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    """列出主机当前 adb 设备（扩展用；产品主路径是 redroid 虚拟机）。"""
    try:
        return {"ok": True, "devices": act.list_adb_devices()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/register-usb")
def register_usb(body: UsbRegisterBody, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    return vd.register_usb_phone(
        actor["id"],
        serial=body.serial,
        label=body.label,
        persona_id=body.persona_id or actor.get("persona_id") or "",
    )


@router.get("/{device_id}")
def get_one(device_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return vd.get_device(device_id, user["id"])


@router.get("/{device_id}/live")
@router.get("/{device_id}/live/")
@router.get("/{device_id}/live/vnc.html")
@router.get("/{device_id}/live/index.html")
def live_page(device_id: str, actor: Dict[str, Any] = Depends(_actor)) -> HTMLResponse:
    """多实例可寻址观看页（截图轮询，走 API 隧道，不依赖每台单独 -R）。"""
    _resolve_device(device_id, actor, prefer_running=True, auto_start=False)
    return HTMLResponse(_LIVE_HTML, headers={"Cache-Control": "no-store"})


@router.get("/{device_id}/live/shot.png")
def live_shot(device_id: str, actor: Dict[str, Any] = Depends(_actor)) -> Response:
    d = _resolve_device(device_id, actor, prefer_running=True, auto_start=True)
    try:
        if _is_android(d):
            png = act.android_screenshot_png(act.android_adb_serial(d.get("meta")))
        else:
            backend = (d.get("backend") or "").strip()
            if backend not in ("docker_novnc", "docker"):
                raise HTTPException(status_code=400, detail=f"live 不支持后端 {backend or 'unknown'}")
            png = act.docker_desktop_screenshot_png(_desktop_container(d))
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/start")
def start(device_id: str, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    d = _resolve_device(device_id, actor, prefer_running=False, auto_start=False)
    if d.get("backend") == "usb_adb":
        return d
    return vd.start_device(d["id"], d["user_id"])


@router.post("/{device_id}/stop")
def stop(
    device_id: str,
    force: bool = Query(False),
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _resolve_device(device_id, actor, prefer_running=False, auto_start=False)
    if d.get("backend") == "usb_adb":
        # 真机不断电，只清控制租约
        db.execute(
            "DELETE FROM device_leases WHERE device_id = ? AND role = ?",
            (d["id"], vd.LEASE_CONTROL),
        )
        return {**d, "note": "usb 真机仅释放控制权，不断开设备"}
    return vd.stop_device(d["id"], d["user_id"], force=force)


@router.delete("/{device_id}")
def destroy(
    device_id: str,
    force: bool = Query(False),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    return vd.destroy_device(device_id, user["id"], force=force)


@router.get("/{device_id}/leases")
def leases(device_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return vd.list_leases(device_id, user["id"])


@router.post("/{device_id}/leases")
def acquire(
    device_id: str,
    body: LeaseBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _resolve_device(device_id, actor, prefer_running=True, auto_start=True)
    return vd.acquire_lease(
        d["id"],
        actor["id"],
        role=body.role,
        ttl_sec=body.ttl_sec,
        force=body.force,
        persona_id=body.persona_id or actor.get("persona_id") or "",
        conv_id=body.conv_id or actor.get("conv_id") or "",
    )


@router.post("/{device_id}/leases/{lease_id}/heartbeat")
def heartbeat(
    device_id: str,
    lease_id: str,
    body: HeartbeatBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    _ = device_id
    return vd.heartbeat_lease(lease_id, actor["id"], body.token, ttl_sec=body.ttl_sec)


@router.post("/{device_id}/leases/{lease_id}/release")
def release(
    device_id: str,
    lease_id: str,
    body: TokenBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    _ = device_id
    return vd.release_lease(lease_id, actor["id"], body.token)


class ClickBody(BaseModel):
    x: int
    y: int
    button: int = 1


class TypeBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class KeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)


class UiFindBody(BaseModel):
    text: str = ""
    content_desc: str = ""
    resource_id: str = ""
    interactive_only: bool = False


class UiTapBody(BaseModel):
    ref: str = Field(default="", max_length=32)
    text: str = ""
    content_desc: str = ""


def _row_out(row: Any) -> Dict[str, Any]:
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "kind": row["kind"],
        "backend": row["backend"],
        "status": row["status"],
        "external_id": row["external_id"],
        "meta": meta,
        "owner_persona_id": (row["owner_persona_id"] if "owner_persona_id" in keys else "") or "",
        "source": (row["source"] if "source" in keys else "") or "",
    }


def _resolve_device(
    device_id: str,
    actor: Dict[str, Any],
    *,
    prefer_running: bool = True,
    auto_start: bool = False,
) -> Dict[str, Any]:
    """用户 JWT 按归属；DSH 必须限定 X-Chat-User-Id，别名只解析该用户设备。"""
    uid = actor["id"]
    if not actor.get("is_service"):
        return vd.get_device(device_id, uid)

    key = (device_id or "").strip().lower()
    row = None
    pid = (actor.get("persona_id") or "").strip()
    if key in _DESKTOP_ALIAS:
        if pid:
            found = vd.find_persona_device(uid, persona_id=pid, kind="desktop")
            if found and (not prefer_running or found.get("status") == "running"):
                row = db.fetchone("SELECT * FROM virtual_devices WHERE id = ?", (found["id"],))
            elif found:
                row = db.fetchone("SELECT * FROM virtual_devices WHERE id = ?", (found["id"],))
        if row is None and not pid:
            order = (
                "ORDER BY CASE backend WHEN 'docker_novnc' THEN 0 "
                "WHEN 'aio_sandbox' THEN 1 ELSE 2 END, updated_at DESC"
            )
            if prefer_running:
                row = db.fetchone(
                    f"""
                    SELECT * FROM virtual_devices
                    WHERE user_id = ? AND kind != 'android' AND status = 'running'
                      AND backend NOT IN ('mock','host_x11')
                    {order}
                    LIMIT 1
                    """,
                    (uid,),
                )
    elif key in _PHONE_ALIAS:
        if prefer_running:
            row = db.fetchone(
                """
                SELECT * FROM virtual_devices
                WHERE user_id = ? AND kind = 'android' AND status = 'running'
                  AND (
                    IFNULL(owner_persona_id,'') = ?
                    OR ? = ''
                  )
                ORDER BY CASE backend WHEN 'redroid' THEN 0 WHEN 'docker_android' THEN 1 ELSE 2 END,
                         updated_at DESC
                LIMIT 1
                """,
                (uid, (actor.get("persona_id") or "").strip(), (actor.get("persona_id") or "").strip()),
            )
        if row is None:
            pid = (actor.get("persona_id") or "").strip()
            if pid:
                found = vd.find_persona_device(uid, persona_id=pid, kind="android")
                if found:
                    row = db.fetchone("SELECT * FROM virtual_devices WHERE id = ?", (found["id"],))
            if row is None:
                row = db.fetchone(
                    """
                    SELECT * FROM virtual_devices
                    WHERE user_id = ? AND kind = 'android'
                      AND status IN ('stopped','running','unavailable')
                      AND backend IN ('redroid','docker_android','usb_adb')
                      AND IFNULL(owner_persona_id,'') = ?
                    ORDER BY CASE backend WHEN 'redroid' THEN 0 WHEN 'docker_android' THEN 1 ELSE 2 END,
                             updated_at DESC
                    LIMIT 1
                    """,
                    (uid, pid),
                )
    else:
        row = db.fetchone(
            "SELECT * FROM virtual_devices WHERE id = ? AND user_id = ?",
            (device_id, uid),
        )

    if row is None or row["status"] == "destroyed":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "device_not_provisioned",
                "message": "设备不存在：需用户批准创建后才能使用（POST /request-create）",
            },
        )

    out = _row_out(row)
    if out["backend"] == "host_x11":
        raise HTTPException(status_code=403, detail="已禁用控真机桌面，请使用 docker_novnc 虚拟电脑")
    if auto_start and out["status"] != "running" and out["backend"] not in ("mock", "usb_adb"):
        started = vd.start_device(out["id"], out["user_id"])
        out["status"] = started.get("status") or "running"
        out["meta"] = started.get("meta") or out["meta"]
        out["external_id"] = started.get("external_id") or out.get("external_id")
    return out


def _owned_or_service(device_id: str, actor: Dict[str, Any]) -> Dict[str, Any]:
    return _resolve_device(device_id, actor, prefer_running=True, auto_start=True)


def _desktop_container(d: Dict[str, Any]) -> str:
    return act.docker_container_name(d.get("meta"), external_id=str(d.get("external_id") or ""))


def _require_control_if_token(d: Dict[str, Any], actor: Dict[str, Any], *, mutating: bool) -> None:
    """有 X-Lease-Token 时强制校验；DSH 写操作若无 token 则先租约并校验人设/会话围栏。"""
    tok = (actor.get("lease_token") or "").strip()
    if not tok:
        if mutating and actor.get("is_service"):
            pid = (actor.get("persona_id") or "").strip()
            cid = (actor.get("conv_id") or "").strip()
            existing = vd.active_control_lease(d["id"])
            if existing is None:
                vd.acquire_lease(
                    d["id"],
                    actor["id"],
                    role="control",
                    ttl_sec=180,
                    persona_id=pid,
                    conv_id=cid,
                )
                return
            if existing["user_id"] != actor["id"]:
                raise HTTPException(status_code=403, detail="设备控制权属于其他用户")
            ex_p = (existing.get("persona_id") or "").strip()
            ex_c = (existing.get("conv_id") or "").strip()
            if (ex_p and pid and ex_p != pid) or (ex_c and cid and ex_c != cid):
                raise HTTPException(
                    status_code=409,
                    detail="控制权被同一账号其他人设/会话占用（需 force 或带匹配 X-Lease-Token）",
                )
            # 同轨：续心跳窗口
            vd.acquire_lease(
                d["id"],
                actor["id"],
                role="control",
                ttl_sec=180,
                persona_id=pid or ex_p,
                conv_id=cid or ex_c,
            )
        return
    vd.require_control_token(d["id"], actor["id"], tok)


@router.post("/{device_id}/actions/screenshot")
def action_screenshot(device_id: str, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    try:
        if d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android"):
            png = act.android_screenshot_png(act.android_adb_serial(d.get("meta")))
        else:
            backend = (d.get("backend") or "").strip()
            if backend in ("docker_novnc", "docker"):
                png = act.docker_desktop_screenshot_png(_desktop_container(d))
            else:
                raise HTTPException(status_code=403, detail=f"不支持的桌面后端: {backend or 'unknown'}")
        return act.action_result_screenshot(png)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/actions/click")
def action_click(
    device_id: str,
    body: ClickBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    _require_control_if_token(d, actor, mutating=True)
    try:
        if d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android"):
            act.android_tap(body.x, body.y, act.android_adb_serial(d.get("meta")))
        else:
            backend = (d.get("backend") or "").strip()
            if backend in ("docker_novnc", "docker"):
                act.docker_desktop_click(_desktop_container(d), body.x, body.y, button=body.button)
            else:
                raise HTTPException(status_code=403, detail=f"不支持的桌面后端: {backend or 'unknown'}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/actions/type")
def action_type(
    device_id: str,
    body: TypeBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    _require_control_if_token(d, actor, mutating=True)
    try:
        if d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android"):
            act.android_text(body.text, act.android_adb_serial(d.get("meta")))
        else:
            backend = (d.get("backend") or "").strip()
            if backend in ("docker_novnc", "docker"):
                act.docker_desktop_type(_desktop_container(d), body.text)
            else:
                raise HTTPException(status_code=403, detail=f"不支持的桌面后端: {backend or 'unknown'}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/actions/key")
def action_key(
    device_id: str,
    body: KeyBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    _require_control_if_token(d, actor, mutating=True)
    try:
        if d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android"):
            act.android_key(body.key, act.android_adb_serial(d.get("meta")))
            return {"ok": True}
        backend = (d.get("backend") or "").strip()
        if backend in ("docker_novnc", "docker"):
            act.docker_desktop_key(_desktop_container(d), body.key)
        else:
            raise HTTPException(status_code=403, detail=f"不支持的桌面后端: {backend or 'unknown'}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/{device_id}/actions/geometry")
def action_geometry(device_id: str, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    if d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android"):
        return {"ok": True, "width": 1080, "height": 1920, "kind": "android"}
    backend = (d.get("backend") or "").strip()
    if backend in ("docker_novnc", "docker"):
        g = act.docker_desktop_geometry(_desktop_container(d))
    else:
        raise HTTPException(status_code=403, detail=f"不支持的桌面后端: {backend or 'unknown'}")
    return {"ok": True, **g, "kind": "desktop"}


def _is_android(d: Dict[str, Any]) -> bool:
    return d.get("kind") == "android" or d.get("backend") in ("usb_adb", "redroid", "docker_android")


@router.post("/{device_id}/actions/ui-dump")
def action_ui_dump(
    device_id: str,
    interactive_only: bool = Query(True),
    limit: int = Query(80, ge=1, le=200),
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    """安卓 uiautomator 控件树；桌面为可见窗口树（wmctrl/xdotool）。"""
    d = _owned_or_service(device_id, actor)
    try:
        if _is_android(d):
            serial = act.android_adb_serial(d.get("meta"))
            elements = act.android_ui_elements(
                serial, interactive_only=interactive_only, limit=limit
            )
            return {
                "ok": True,
                "kind": "android",
                "serial": serial,
                "count": len(elements),
                "elements": elements,
            }
        backend = (d.get("backend") or "").strip()
        if backend not in ("docker_novnc", "docker"):
            raise HTTPException(status_code=400, detail=f"ui-dump 不支持后端 {backend or 'unknown'}")
        elements = act.docker_desktop_windows(_desktop_container(d), limit=limit)
        return {"ok": True, "kind": "desktop", "count": len(elements), "elements": elements}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/actions/ui-find")
def action_ui_find(
    device_id: str,
    body: UiFindBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    d = _owned_or_service(device_id, actor)
    try:
        if _is_android(d):
            serial = act.android_adb_serial(d.get("meta"))
            hits = act.android_find_elements(
                serial,
                text=body.text,
                content_desc=body.content_desc,
                resource_id=body.resource_id,
                interactive_only=body.interactive_only,
            )
            return {"ok": True, "kind": "android", "count": len(hits), "elements": hits}
        backend = (d.get("backend") or "").strip()
        if backend not in ("docker_novnc", "docker"):
            raise HTTPException(status_code=400, detail=f"ui-find 不支持后端 {backend or 'unknown'}")
        hits = act.docker_desktop_find_windows(
            _desktop_container(d), text=body.text or body.content_desc
        )
        return {"ok": True, "kind": "desktop", "count": len(hits), "elements": hits}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{device_id}/actions/ui-tap")
def action_ui_tap(
    device_id: str,
    body: UiTapBody,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    """安卓按 @eN/text；桌面按 @wN/窗口标题（优于瞎点坐标）。"""
    d = _owned_or_service(device_id, actor)
    _require_control_if_token(d, actor, mutating=True)
    try:
        if _is_android(d):
            serial = act.android_adb_serial(d.get("meta"))
            if body.ref:
                return act.android_tap_ref(serial, body.ref)
            hits = act.android_find_elements(
                serial, text=body.text, content_desc=body.content_desc, interactive_only=True
            )
            if not hits:
                raise RuntimeError("未找到匹配控件")
            hit = hits[0]
            act.android_tap(int(hit["center"][0]), int(hit["center"][1]), serial)
            return {
                "ok": True,
                "kind": "android",
                "ref": hit["ref"],
                "x": hit["center"][0],
                "y": hit["center"][1],
            }
        backend = (d.get("backend") or "").strip()
        if backend not in ("docker_novnc", "docker"):
            raise HTTPException(status_code=400, detail=f"ui-tap 不支持后端 {backend or 'unknown'}")
        cname = _desktop_container(d)
        if body.ref:
            out = act.docker_desktop_tap_ref(cname, body.ref)
            out["kind"] = "desktop"
            return out
        hits = act.docker_desktop_find_windows(cname, text=body.text or body.content_desc)
        if not hits:
            raise RuntimeError("未找到匹配窗口")
        out = act.docker_desktop_tap_ref(cname, str(hits[0].get("ref") or ""))
        out["kind"] = "desktop"
        return out
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
