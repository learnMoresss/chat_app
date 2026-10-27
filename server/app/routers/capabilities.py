from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import httpx

from ..gateway import jobs as job_mod
from ..gateway.registry import list_capabilities_for_user, load_gateway_config, sync_workers_from_config
from ..gateway.signed_urls import make_upload_url
from ..routers.media import register_upload_slot
from ..security import current_user

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


class JobCreate(BaseModel):
    capability: str
    plugin_id: str = "echo"
    input: Dict[str, Any] = Field(default_factory=dict)
    persona_id: str = ""
    conversation_id: str = ""
    timeout_seconds: int = 120
    # download 等需要回传文件时为 true，网关签发一次性上传链接
    need_upload: bool = False


@router.get("/capabilities")
def capabilities(user=Depends(current_user)) -> Dict[str, Any]:
    sync_workers_from_config()
    items = list_capabilities_for_user(
        user_id=user["id"], is_admin=bool(user["is_admin"])
    )
    return {"capabilities": items}


@router.get("/gateway/status")
def gateway_status(user=Depends(current_user)) -> Dict[str, Any]:
    """只读：管理员看集群配置摘要；普通用户只看自己能用的能力数。"""
    sync_workers_from_config()
    cfg = load_gateway_config()
    caps = list_capabilities_for_user(
        user_id=user["id"], is_admin=bool(user["is_admin"])
    )
    if not user["is_admin"]:
        return {"capabilities_count": len(caps), "capabilities": caps}
    return {
        "workers": [
            {
                "id": w["id"],
                "name": w.get("name"),
                "base_url": w.get("base_url"),
                "enabled": w.get("enabled", True),
            }
            for w in cfg.get("workers") or []
        ],
        "grants": cfg.get("grants") or [],
        "capabilities": caps,
    }


@router.get("/gateway/health")
def gateway_health(user=Depends(current_user)) -> Dict[str, Any]:
    """探测各 worker 与本进程；普通用户只看能力可用性摘要。"""
    sync_workers_from_config()
    cfg = load_gateway_config()
    caps = list_capabilities_for_user(
        user_id=user["id"], is_admin=bool(user["is_admin"])
    )
    workers_out = []
    if user["is_admin"]:
        for w in cfg.get("workers") or []:
            item: Dict[str, Any] = {
                "id": w.get("id"),
                "name": w.get("name"),
                "base_url": w.get("base_url"),
                "enabled": w.get("enabled", True),
                "ok": False,
                "detail": "",
            }
            if not item["enabled"]:
                item["detail"] = "disabled"
                workers_out.append(item)
                continue
            try:
                with httpx.Client(timeout=3.0) as client:
                    r = client.get(
                        f"{str(w.get('base_url') or '').rstrip('/')}/health",
                        headers={"Authorization": f"Bearer {w.get('token')}"},
                    )
                    item["ok"] = r.status_code < 400
                    item["detail"] = r.text[:200] if r.text else str(r.status_code)
            except Exception as e:
                item["detail"] = str(e)
            workers_out.append(item)
    return {
        "ok": True,
        "capabilities_count": len(caps),
        "workers": workers_out,
    }


@router.get("/jobs")
def list_jobs(
    limit: int = 50,
    capability: Optional[str] = None,
    user=Depends(current_user),
) -> Dict[str, Any]:
    items = job_mod.list_jobs(
        user_id=user["id"], limit=limit, capability=capability or None
    )
    return {"jobs": items}


@router.post("/jobs")
def create_job(body: JobCreate, user=Depends(current_user)) -> Dict[str, Any]:
    sync_workers_from_config()
    try:
        from ..capability_policy import assert_allowed

        assert_allowed(body.capability)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    upload = None
    if body.need_upload or (
        body.capability == "media.download" and body.input.get("action") == "download"
    ):
        file_id = register_upload_slot(user_id=user["id"], content_type="video/mp4")
        upload = {
            "file_id": file_id,
            "url": make_upload_url(file_id=file_id, user_id=user["id"], ttl_seconds=3600),
        }
    try:
        return job_mod.create_job(
            user_id=user["id"],
            is_admin=bool(user["is_admin"]),
            capability=body.capability,
            plugin_id=body.plugin_id,
            input_data=body.input,
            persona_id=body.persona_id,
            conversation_id=body.conversation_id,
            timeout_seconds=body.timeout_seconds,
            upload=upload,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user=Depends(current_user)) -> Dict[str, Any]:
    data = job_mod.refresh_job_from_plugin(job_id, user_id=user["id"])
    if not data:
        raise HTTPException(status_code=404, detail="not found")
    return data


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, user=Depends(current_user)) -> Dict[str, Any]:
    data = job_mod.cancel_job(job_id, user_id=user["id"])
    if not data:
        raise HTTPException(status_code=404, detail="not found")
    return data


@router.post("/jobs/{job_id}/interrupt")
def interrupt_job(job_id: str, user=Depends(current_user)) -> Dict[str, Any]:
    """M5：显式中断进行中任务（与 DELETE 同效果，便于客户端）。"""
    data = job_mod.cancel_job(job_id, user_id=user["id"])
    if not data:
        raise HTTPException(status_code=404, detail="not found")
    return data
