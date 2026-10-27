"""虚拟设备与观看/控制租约（fencing）。产品网关权威；模型不能抢权限。"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from . import db
from .desktop_backend import DesktopInstance, get_desktop_backend

LEASE_VIEW = "view"
LEASE_CONTROL = "control"
DEFAULT_LEASE_SEC = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _row_device(r: Any) -> Dict[str, Any]:
    meta = {}
    try:
        meta = json.loads(r["meta_json"] or "{}")
    except Exception:
        meta = {}
    keys = r.keys() if hasattr(r, "keys") else []
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "label": r["label"],
        "kind": r["kind"],
        "backend": r["backend"],
        "external_id": r["external_id"],
        "status": r["status"],
        "view_url": r["view_url"],
        "control_url": r["control_url"],
        "meta": meta,
        "owner_persona_id": (r["owner_persona_id"] if "owner_persona_id" in keys else "") or "",
        "bind_conv_id": (r["bind_conv_id"] if "bind_conv_id" in keys else "") or "",
        "source": (r["source"] if "source" in keys else "") or meta.get("source") or "",
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def _row_lease(r: Any) -> Dict[str, Any]:
    keys = r.keys() if hasattr(r, "keys") else []
    return {
        "id": r["id"],
        "device_id": r["device_id"],
        "user_id": r["user_id"],
        "role": r["role"],
        "token": r["token"],
        "expires_at": r["expires_at"],
        "created_at": r["created_at"],
        "heartbeat_at": r["heartbeat_at"],
        "persona_id": (r["persona_id"] if "persona_id" in keys else "") or "",
        "conv_id": (r["conv_id"] if "conv_id" in keys else "") or "",
    }


def expire_stale_leases(device_id: Optional[str] = None) -> int:
    now = _iso(_now())
    if device_id:
        before = db.fetchall(
            "SELECT id FROM device_leases WHERE device_id = ? AND expires_at < ?",
            (device_id, now),
        )
        db.execute(
            "DELETE FROM device_leases WHERE device_id = ? AND expires_at < ?",
            (device_id, now),
        )
    else:
        before = db.fetchall("SELECT id FROM device_leases WHERE expires_at < ?", (now,))
        db.execute("DELETE FROM device_leases WHERE expires_at < ?", (now,))
    return len(before)


def list_devices(user_id: str) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT * FROM virtual_devices
        WHERE user_id = ? AND status != 'destroyed'
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    return [_row_device(r) for r in rows]


def list_all_devices(*, include_destroyed: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
    """管理台：全站设备（附 username）。"""
    limit = max(1, min(int(limit), 500))
    if include_destroyed:
        rows = db.fetchall(
            """
            SELECT d.*, u.username AS username
            FROM virtual_devices d
            LEFT JOIN users u ON u.id = d.user_id
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    else:
        rows = db.fetchall(
            """
            SELECT d.*, u.username AS username
            FROM virtual_devices d
            LEFT JOIN users u ON u.id = d.user_id
            WHERE d.status != 'destroyed'
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    out: List[Dict[str, Any]] = []
    for r in rows:
        item = _row_device(r)
        try:
            item["username"] = r["username"] or ""
        except Exception:
            item["username"] = ""
        out.append(item)
    return out


def get_device(device_id: str, user_id: str) -> Dict[str, Any]:
    r = db.fetchone(
        "SELECT * FROM virtual_devices WHERE id = ? AND user_id = ?",
        (device_id, user_id),
    )
    if r is None or r["status"] == "destroyed":
        raise HTTPException(status_code=404, detail="虚拟设备不存在")
    return _row_device(r)


def create_device(user_id: str, *, label: str = "", kind: str = "desktop") -> Dict[str, Any]:
    kind = (kind or "desktop").strip().lower()
    # M6：有 KVM 则起 docker-android；否则标记 unavailable
    if kind == "android":
        import os

        kvm_ok = os.path.exists("/dev/kvm")
        now = _iso(_now())
        did = f"vd_{secrets.token_hex(12)}"
        if not kvm_ok:
            meta = {"backend": "redroid", "kvm": False, "reason": "no_kvm"}
            db.execute(
                """
                INSERT INTO virtual_devices(
                  id, user_id, label, kind, backend, external_id, status,
                  view_url, control_url, meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'android', 'redroid', ?, 'unavailable', '', '', ?, ?, ?)
                """,
                (
                    did,
                    user_id,
                    label or "虚拟安卓",
                    f"android_{secrets.token_hex(6)}",
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return get_device(did, user_id)
        from .desktop_backend import get_android_backend

        backend = get_android_backend()
        try:
            inst = backend.create(label=label or "android")
        except Exception as exc:  # noqa: BLE001
            meta = {"backend": backend.name, "kvm": True, "reason": str(exc)[:300]}
            db.execute(
                """
                INSERT INTO virtual_devices(
                  id, user_id, label, kind, backend, external_id, status,
                  view_url, control_url, meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'android', ?, ?, 'unavailable', '', '', ?, ?, ?)
                """,
                (
                    did,
                    user_id,
                    label or "虚拟安卓",
                    backend.name,
                    f"android_{secrets.token_hex(6)}",
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return seal_view_urls(did, user_id)
        db.execute(
            """
            INSERT INTO virtual_devices(
              id, user_id, label, kind, backend, external_id, status,
              view_url, control_url, meta_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'android', ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                did,
                user_id,
                label or "虚拟安卓",
                backend.name,
                inst.external_id,
                inst.view_url,
                inst.control_url,
                json.dumps(inst.meta or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return seal_view_urls(did, user_id)

    backend = get_desktop_backend()
    inst = backend.create(label=label or "desktop")
    now = _iso(_now())
    did = f"vd_{secrets.token_hex(12)}"
    db.execute(
        """
        INSERT INTO virtual_devices(
          id, user_id, label, kind, backend, external_id, status,
          view_url, control_url, meta_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
        """,
        (
            did,
            user_id,
            label or "虚拟电脑",
            kind,
            backend.name,
            inst.external_id,
            inst.view_url,
            inst.control_url,
            json.dumps(inst.meta or {}, ensure_ascii=False),
            now,
            now,
        ),
    )
    return seal_view_urls(did, user_id)


def _api_live_url(device_id: str) -> str:
    """经产品 API 反代观看（走 chat-api 公网隧道，不依赖每实例单独 -R 端口）。"""
    from .config import get_settings

    s = get_settings()
    base = (s.public_base_url or "").rstrip("/")
    if base.endswith("/chat-api"):
        root = base
    elif base:
        root = f"{base}/chat-api"
    else:
        root = "http://127.0.0.1:8000"
    return f"{root}/api/v1/virtual-devices/{device_id}/live/vnc.html"


def _merge_meta(old_json: str, new_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        old = json.loads(old_json or "{}")
    except Exception:
        old = {}
    if not isinstance(old, dict):
        old = {}
    merged = {**old, **(new_meta or {})}
    return merged


def _apply_instance(device_id: str, user_id: str, inst: DesktopInstance, status: str) -> Dict[str, Any]:
    now = _iso(_now())
    row = db.fetchone(
        "SELECT meta_json, kind, backend FROM virtual_devices WHERE id = ? AND user_id = ?",
        (device_id, user_id),
    )
    merged = _merge_meta(row["meta_json"] if row else "{}", inst.meta)
    # 安卓/桌面观看统一走 API live 反代（多实例可寻址）
    view = _api_live_url(device_id)
    db.execute(
        """
        UPDATE virtual_devices
        SET status = ?, view_url = ?, control_url = ?, meta_json = ?, updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            status,
            view,
            view,
            json.dumps(merged, ensure_ascii=False),
            now,
            device_id,
            user_id,
        ),
    )
    return get_device(device_id, user_id)


def seal_view_urls(device_id: str, user_id: str) -> Dict[str, Any]:
    """创建后把 view_url 改成带 device_id 的 API 反代地址。"""
    view = _api_live_url(device_id)
    now = _iso(_now())
    db.execute(
        """
        UPDATE virtual_devices
        SET view_url = ?, control_url = ?, updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (view, view, now, device_id, user_id),
    )
    return get_device(device_id, user_id)


def _backend_for(device: Dict[str, Any]):
    if (device.get("kind") or "") == "android" or (device.get("backend") or "").startswith("docker_android"):
        from .desktop_backend import get_android_backend

        return get_android_backend()
    return get_desktop_backend()


def stop_device(device_id: str, user_id: str, *, force: bool = False) -> Dict[str, Any]:
    d = get_device(device_id, user_id)
    expire_stale_leases(device_id)
    ctrl = db.fetchone(
        "SELECT * FROM device_leases WHERE device_id = ? AND role = ?",
        (device_id, LEASE_CONTROL),
    )
    if ctrl is not None and not force:
        raise HTTPException(
            status_code=409,
            detail="有活跃控制租约，先释放或 force=1；停机不会销毁设备记录",
        )
    backend = _backend_for(d)
    try:
        backend.stop(d["external_id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"后端停机失败: {e}") from e
    # 停机后清控制租约，保留观看记录到过期
    db.execute(
        "DELETE FROM device_leases WHERE device_id = ? AND role = ?",
        (device_id, LEASE_CONTROL),
    )
    now = _iso(_now())
    db.execute(
        "UPDATE virtual_devices SET status = 'stopped', updated_at = ? WHERE id = ?",
        (now, device_id),
    )
    out = get_device(device_id, user_id)
    try:
        from .system_notices import notice_device_stopped

        notice_device_stopped(user_id=user_id, device=out)
    except Exception:
        pass
    return out


def start_device(device_id: str, user_id: str) -> Dict[str, Any]:
    d = get_device(device_id, user_id)
    if d["status"] == "running":
        return d
    backend = _backend_for(d)
    try:
        inst = backend.start(d["external_id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"后端开机失败: {e}") from e
    return _apply_instance(device_id, user_id, inst, "running")


def destroy_device(device_id: str, user_id: str, *, force: bool = False) -> Dict[str, Any]:
    d = get_device(device_id, user_id)
    expire_stale_leases(device_id)
    any_lease = db.fetchone("SELECT id FROM device_leases WHERE device_id = ?", (device_id,))
    if any_lease is not None and not force:
        raise HTTPException(status_code=409, detail="仍有租约，先释放或 force=1")
    backend = _backend_for(d)
    try:
        backend.destroy(d["external_id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"后端销毁失败: {e}") from e
    db.execute("DELETE FROM device_leases WHERE device_id = ?", (device_id,))
    now = _iso(_now())
    db.execute(
        "UPDATE virtual_devices SET status = 'destroyed', updated_at = ? WHERE id = ?",
        (now, device_id),
    )
    return {"ok": True, "id": device_id}


def active_control_lease(device_id: str) -> Optional[Dict[str, Any]]:
    expire_stale_leases(device_id)
    r = db.fetchone(
        "SELECT * FROM device_leases WHERE device_id = ? AND role = ?",
        (device_id, LEASE_CONTROL),
    )
    return _row_lease(r) if r else None


def acquire_lease(
    device_id: str,
    user_id: str,
    *,
    role: str,
    ttl_sec: int = DEFAULT_LEASE_SEC,
    force: bool = False,
    persona_id: str = "",
    conv_id: str = "",
) -> Dict[str, Any]:
    if role not in (LEASE_VIEW, LEASE_CONTROL):
        raise HTTPException(status_code=400, detail="role 须为 view 或 control")
    d = get_device(device_id, user_id)
    # 人设绑定设备：其他 persona 不得强占（除非 force）
    bound = (d.get("owner_persona_id") or "").strip()
    pid = (persona_id or "").strip()
    if bound and pid and bound != pid and role == LEASE_CONTROL and not force:
        raise HTTPException(status_code=403, detail="该设备已绑定其他人设，禁止串控")
    if d["status"] != "running" and role == LEASE_CONTROL:
        raise HTTPException(status_code=409, detail="设备未运行，无法接管")

    expire_stale_leases(device_id)
    now = _now()
    expires = _iso(now + timedelta(seconds=max(30, ttl_sec)))
    now_s = _iso(now)
    cid = (conv_id or "").strip()

    if role == LEASE_CONTROL:
        existing = db.fetchone(
            "SELECT * FROM device_leases WHERE device_id = ? AND role = ?",
            (device_id, LEASE_CONTROL),
        )
        if existing is not None:
            same_user = existing["user_id"] == user_id
            keys = existing.keys() if hasattr(existing, "keys") else []
            ex_persona = (existing["persona_id"] if "persona_id" in keys else "") or ""
            ex_conv = (existing["conv_id"] if "conv_id" in keys else "") or ""
            # 同用户但不同人设/会话：默认拒绝，避免多人设互相抢
            if same_user and (
                (ex_persona and pid and ex_persona != pid)
                or (ex_conv and cid and ex_conv != cid)
            ):
                if not force:
                    raise HTTPException(
                        status_code=409,
                        detail="控制权被同一账号其他人设/会话占用（需 force 或先释放）",
                    )
                db.execute("DELETE FROM device_leases WHERE id = ?", (existing["id"],))
            elif same_user:
                # 同用户同轨续租：换新 token（fencing）
                token = secrets.token_urlsafe(24)
                db.execute(
                    """
                    UPDATE device_leases
                    SET token = ?, expires_at = ?, heartbeat_at = ?,
                        persona_id = ?, conv_id = ?
                    WHERE id = ?
                    """,
                    (token, expires, now_s, pid or ex_persona, cid or ex_conv, existing["id"]),
                )
                r = db.fetchone("SELECT * FROM device_leases WHERE id = ?", (existing["id"],))
                out = _row_lease(r)
                out["view_url"] = d["view_url"]
                out["control_url"] = d["control_url"]
                return out
            else:
                if not force:
                    raise HTTPException(
                        status_code=409,
                        detail="控制权已被占用（fencing）",
                    )
                db.execute("DELETE FROM device_leases WHERE id = ?", (existing["id"],))

    lid = f"ls_{secrets.token_hex(12)}"
    token = secrets.token_urlsafe(24)
    db.execute(
        """
        INSERT INTO device_leases(
          id, device_id, user_id, role, token, expires_at, created_at, heartbeat_at,
          persona_id, conv_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (lid, device_id, user_id, role, token, expires, now_s, now_s, pid, cid),
    )
    r = db.fetchone("SELECT * FROM device_leases WHERE id = ?", (lid,))
    out = _row_lease(r)
    out["view_url"] = d["view_url"]
    out["control_url"] = d["control_url"] if role == LEASE_CONTROL else d["view_url"]
    return out


def require_control_token(device_id: str, user_id: str, token: str) -> Dict[str, Any]:
    """动作前校验控制租约 token（防跨会话/跨人设串控）。"""
    expire_stale_leases(device_id)
    r = db.fetchone(
        "SELECT * FROM device_leases WHERE device_id = ? AND role = ?",
        (device_id, LEASE_CONTROL),
    )
    if r is None:
        raise HTTPException(status_code=409, detail="无控制租约：先 POST .../leases role=control")
    if r["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="控制租约不属于当前用户")
    if (token or "").strip() and r["token"] != token.strip():
        raise HTTPException(status_code=403, detail="租约 token 无效（可能已被接管）")
    if _parse(r["expires_at"]) < _now():
        db.execute("DELETE FROM device_leases WHERE id = ?", (r["id"],))
        raise HTTPException(status_code=410, detail="控制租约已过期")
    return _row_lease(r)


def _norm_kind(kind: str) -> str:
    k = (kind or "desktop").strip().lower()
    if k in ("android", "phone", "mobile"):
        return "android"
    return "desktop"


def find_persona_device(
    user_id: str,
    *,
    persona_id: str,
    kind: str = "desktop",
) -> Optional[Dict[str, Any]]:
    """查找人设已绑定、未销毁的设备（不创建）。一人设每种 kind 最多一台。"""
    uid = (user_id or "").strip()
    pid = (persona_id or "").strip()
    if not uid or not pid:
        return None
    want = _norm_kind(kind)
    if want == "android":
        row = db.fetchone(
            """
            SELECT * FROM virtual_devices
            WHERE user_id = ? AND kind = 'android' AND status != 'destroyed'
              AND IFNULL(owner_persona_id,'') = ?
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT 1
            """,
            (uid, pid),
        )
    else:
        row = db.fetchone(
            """
            SELECT * FROM virtual_devices
            WHERE user_id = ? AND kind != 'android' AND status != 'destroyed'
              AND backend NOT IN ('mock','host_x11')
              AND IFNULL(owner_persona_id,'') = ?
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, updated_at DESC
            LIMIT 1
            """,
            (uid, pid),
        )
    if row is None:
        return None
    d = _row_device(row)
    vu = str(d.get("view_url") or "")
    if f"/virtual-devices/{d['id']}/live/" not in vu:
        return seal_view_urls(d["id"], uid)
    return d


def ensure_user_device(
    user_id: str,
    *,
    kind: str = "desktop",
    persona_id: str = "",
    label: str = "",
    create: bool = False,
) -> Dict[str, Any]:
    """只查找人设已绑定设备；默认不自动创建（防膨胀）。

    create=True 仅管理台/明确批准后调用。
    """
    _ = label
    uid = (user_id or "").strip()
    if not uid or db.fetchone("SELECT id FROM users WHERE id = ?", (uid,)) is None:
        raise HTTPException(status_code=404, detail="用户不存在，无法 ensure 设备")
    pid = (persona_id or "").strip()
    want = _norm_kind(kind)
    if not pid:
        raise HTTPException(
            status_code=400,
            detail="设备必须绑定人设：请传 persona_id；新人设不会默认创建设备",
        )
    found = find_persona_device(uid, persona_id=pid, kind=want)
    if found is not None:
        return found
    if not create:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "device_not_provisioned",
                "message": "该人设还没有虚拟设备，需用户在聊天里批准创建",
                "kind": want,
                "persona_id": pid,
            },
        )
    created = create_device(
        uid,
        label=label or ("虚拟手机" if want == "android" else "虚拟电脑"),
        kind=want,
    )
    db.execute(
        "UPDATE virtual_devices SET owner_persona_id = ?, source = 'user_approved' WHERE id = ? AND user_id = ?",
        (pid, created["id"], uid),
    )
    return get_device(created["id"], uid)


def _req_out(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "persona_id": row["persona_id"] or "",
        "conv_id": row["conv_id"] or "",
        "kind": row["kind"] or "desktop",
        "label": row["label"] or "",
        "status": row["status"] or "pending",
        "message_id": row["message_id"] or "",
        "device_id": row["device_id"] or "",
        "reason": row["reason"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def emit_device_create_request(
    *,
    user_id: str,
    persona_id: str,
    conv_id: str,
    kind: str = "desktop",
    reason: str = "",
    label: str = "",
) -> Dict[str, Any]:
    """AI 申请创建设备：落 pending + 聊天卡片，等用户批准。已有设备则直接返回。"""
    from .msg_store import insert_message
    from .security import new_id
    from .ws_hub import hub

    uid = (user_id or "").strip()
    pid = (persona_id or "").strip()
    cid = (conv_id or "").strip()
    want = _norm_kind(kind)
    if not uid or not pid:
        raise HTTPException(status_code=400, detail="需要 user_id 与 persona_id")
    existing = find_persona_device(uid, persona_id=pid, kind=want)
    if existing is not None:
        return {"ok": True, "already": True, "device": existing}

    # 同人设同 kind 已有 pending 则复用，不刷屏
    pend = db.fetchone(
        """
        SELECT * FROM device_create_requests
        WHERE user_id = ? AND persona_id = ? AND kind = ? AND status = 'pending'
        ORDER BY created_at DESC LIMIT 1
        """,
        (uid, pid, want),
    )
    if pend is not None:
        return {"ok": True, "pending": True, "request": _req_out(pend)}

    rid = f"dcr_{secrets.token_hex(10)}"
    now = _iso(_now())
    kind_label = "虚拟手机" if want == "android" else "虚拟电脑"
    lab = (label or kind_label).strip()[:64]
    why = (reason or "想用设备帮你办事").strip()[:200]
    db.execute(
        """
        INSERT INTO device_create_requests(
          id, user_id, persona_id, conv_id, kind, label, status,
          message_id, device_id, reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', '', '', ?, ?, ?)
        """,
        (rid, uid, pid, cid, want, lab, why, now, now),
    )

    card = {
        "v": 1,
        "card": "device_create_request",
        "request_id": rid,
        "kind": want,
        "label": lab,
        "persona_id": pid,
        "reason": why,
        "status": "pending",
        "body": f"申请创建{lab}（需你批准后才会占用资源）",
    }
    msg_id = ""
    if cid:
        conv = db.fetchone(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (cid, uid),
        )
        if conv is not None:
            row = insert_message(
                user_id=uid,
                conv_id=cid,
                client_id=new_id("botcli_"),
                sender_id=pid,
                mine=False,
                kind="link",
                text=json.dumps(card, ensure_ascii=False),
                state="sent",
            )
            if row:
                msg_id = row["id"]
                db.execute(
                    "UPDATE device_create_requests SET message_id = ?, updated_at = ? WHERE id = ?",
                    (msg_id, now, rid),
                )
                payload = {
                    "id": row["id"],
                    "client_id": row["client_id"],
                    "seq": row["seq"],
                    "conv_id": row["conv_id"],
                    "sender_id": row["sender_id"],
                    "mine": bool(row["mine"]),
                    "kind": row["kind"],
                    "text": row["text"],
                    "quote_id": row["quote_id"],
                    "seconds": row["seconds"],
                    "bytes": row["bytes"],
                    "state": row["state"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "meta": card,
                }
                await hub.send_user(uid, "message", payload)
                await hub.send_user(
                    uid,
                    "device_create_request",
                    {"request_id": rid, "conv_id": cid, "kind": want, "label": lab},
                )

    req = db.fetchone("SELECT * FROM device_create_requests WHERE id = ?", (rid,))
    return {"ok": True, "pending": True, "request": _req_out(req) if req else {"id": rid}}


def approve_device_request(*, user_id: str, request_id: str) -> Dict[str, Any]:
    """用户批准：为人设创建绑定设备。"""
    uid = (user_id or "").strip()
    rid = (request_id or "").strip()
    row = db.fetchone(
        "SELECT * FROM device_create_requests WHERE id = ? AND user_id = ?",
        (rid, uid),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    if (row["status"] or "") == "approved" and (row["device_id"] or ""):
        return {"ok": True, "device": get_device(row["device_id"], uid), "request": _req_out(row)}
    if (row["status"] or "") != "pending":
        raise HTTPException(status_code=409, detail=f"申请状态不可批准：{row['status']}")
    pid = (row["persona_id"] or "").strip()
    want = _norm_kind(row["kind"] or "desktop")
    # 一人设一种设备：已有则直接绑定成功
    found = find_persona_device(uid, persona_id=pid, kind=want)
    if found is None:
        found = ensure_user_device(
            uid,
            kind=want,
            persona_id=pid,
            label=row["label"] or "",
            create=True,
        )
    now = _iso(_now())
    db.execute(
        """
        UPDATE device_create_requests
        SET status = 'approved', device_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (found["id"], now, rid),
    )
    _patch_request_card_status(uid, row["message_id"] or "", "approved", found["id"])
    r2 = db.fetchone("SELECT * FROM device_create_requests WHERE id = ?", (rid,))
    return {"ok": True, "device": found, "request": _req_out(r2) if r2 else _req_out(row)}


def deny_device_request(*, user_id: str, request_id: str) -> Dict[str, Any]:
    uid = (user_id or "").strip()
    rid = (request_id or "").strip()
    row = db.fetchone(
        "SELECT * FROM device_create_requests WHERE id = ? AND user_id = ?",
        (rid, uid),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    if (row["status"] or "") != "pending":
        return {"ok": True, "request": _req_out(row)}
    now = _iso(_now())
    db.execute(
        "UPDATE device_create_requests SET status = 'denied', updated_at = ? WHERE id = ?",
        (now, rid),
    )
    _patch_request_card_status(uid, row["message_id"] or "", "denied", "")
    r2 = db.fetchone("SELECT * FROM device_create_requests WHERE id = ?", (rid,))
    return {"ok": True, "request": _req_out(r2) if r2 else _req_out(row)}


def _patch_request_card_status(user_id: str, message_id: str, status: str, device_id: str) -> None:
    mid = (message_id or "").strip()
    if not mid:
        return
    m = db.fetchone(
        "SELECT text FROM messages WHERE id = ? AND user_id = ?",
        (mid, user_id),
    )
    if m is None:
        return
    try:
        card = json.loads(m["text"] or "{}")
        if not isinstance(card, dict):
            return
        card["status"] = status
        if device_id:
            card["device_id"] = device_id
        db.execute(
            "UPDATE messages SET text = ?, updated_at = ? WHERE id = ?",
            (json.dumps(card, ensure_ascii=False), _iso(_now()), mid),
        )
    except Exception:
        pass


def register_usb_phone(
    user_id: str,
    *,
    serial: str,
    label: str = "",
    persona_id: str = "",
) -> Dict[str, Any]:
    """扩展：登记 USB adb serial（非产品主路径；主路径 ensure redroid）。"""
    serial = (serial or "").strip()
    if not serial:
        raise HTTPException(status_code=400, detail="需要 adb serial")
    # 同用户同 serial 复用
    rows = db.fetchall(
        """
        SELECT * FROM virtual_devices
        WHERE user_id = ? AND kind = 'android' AND status != 'destroyed'
        """,
        (user_id,),
    )
    for r in rows:
        d = _row_device(r)
        if (d.get("meta") or {}).get("adb_serial") == serial or d.get("external_id") == serial:
            return d
    now = _iso(_now())
    did = f"vd_{secrets.token_hex(12)}"
    meta = {
        "backend": "usb_adb",
        "source": "usb",
        "adb_serial": serial,
        "kvm": False,
    }
    pid = (persona_id or "").strip()
    db.execute(
        """
        INSERT INTO virtual_devices(
          id, user_id, label, kind, backend, external_id, status,
          view_url, control_url, meta_json, created_at, updated_at,
          owner_persona_id, bind_conv_id, source
        ) VALUES (?, ?, ?, 'android', 'usb_adb', ?, 'running', '', '', ?, ?, ?, ?, '', 'usb')
        """,
        (
            did,
            user_id,
            label or f"USB真机 {serial[-6:]}",
            serial,
            json.dumps(meta, ensure_ascii=False),
            now,
            now,
            pid,
        ),
    )
    return get_device(did, user_id)


def heartbeat_lease(lease_id: str, user_id: str, token: str, *, ttl_sec: int = DEFAULT_LEASE_SEC) -> Dict[str, Any]:
    r = db.fetchone("SELECT * FROM device_leases WHERE id = ?", (lease_id,))
    if r is None or r["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="租约不存在")
    if r["token"] != token:
        raise HTTPException(status_code=403, detail="租约 token 无效（可能已被接管）")
    if _parse(r["expires_at"]) < _now():
        db.execute("DELETE FROM device_leases WHERE id = ?", (lease_id,))
        raise HTTPException(status_code=410, detail="租约已过期")
    now = _now()
    expires = _iso(now + timedelta(seconds=max(30, ttl_sec)))
    now_s = _iso(now)
    db.execute(
        "UPDATE device_leases SET expires_at = ?, heartbeat_at = ? WHERE id = ?",
        (expires, now_s, lease_id),
    )
    r2 = db.fetchone("SELECT * FROM device_leases WHERE id = ?", (lease_id,))
    return _row_lease(r2)


def release_lease(lease_id: str, user_id: str, token: str) -> Dict[str, Any]:
    r = db.fetchone("SELECT * FROM device_leases WHERE id = ?", (lease_id,))
    if r is None or r["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="租约不存在")
    if r["token"] != token:
        raise HTTPException(status_code=403, detail="租约 token 无效")
    db.execute("DELETE FROM device_leases WHERE id = ?", (lease_id,))
    return {"ok": True, "id": lease_id}


def list_leases(device_id: str, user_id: str) -> List[Dict[str, Any]]:
    get_device(device_id, user_id)
    expire_stale_leases(device_id)
    rows = db.fetchall(
        "SELECT * FROM device_leases WHERE device_id = ? ORDER BY created_at",
        (device_id,),
    )
    return [_row_lease(r) for r in rows]
