"""管理员：启停公网穿透。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..security import current_user
from ..tunnel import TunnelManager, provider_catalog

router = APIRouter(prefix="/api/v1/admin/tunnel", tags=["admin-tunnel"])


def _admin(user: Dict[str, Any]) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员")


class TunnelStartBody(BaseModel):
    provider: str = Field(description="cloudflared|ngrok|localhost_run|pinggy|ssh_jump|none")


@router.get("")
def tunnel_admin_status(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _admin(user)
    s = get_settings()
    mgr = TunnelManager(s.data_dir, local_port=s.port)
    st = mgr.status()
    return {
        **st,
        "providers": provider_catalog(),
        "configured_provider": s.public_tunnel_provider,
        "public_base_url": (st.get("public_url") or s.public_base_url or "").rstrip("/"),
    }


@router.post("/start")
def tunnel_admin_start(
    body: TunnelStartBody,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _admin(user)
    s = get_settings()
    mgr = TunnelManager(s.data_dir, local_port=s.port)
    st = mgr.start(
        body.provider,
        ngrok_authtoken=s.public_tunnel_ngrok_authtoken,
        cloudflared_token=s.public_tunnel_cloudflared_token,
        ssh_config=s.public_tunnel_ssh_config,
        ssh_host=s.public_tunnel_ssh_host,
        ssh_remote_port=s.public_tunnel_ssh_remote_port,
        wait_url_sec=30.0,
    )
    if not st.get("active") and body.provider not in ("none", "lan", "off", ""):
        raise HTTPException(status_code=502, detail=st.get("error") or "tunnel_start_failed")
    return st


@router.post("/stop")
def tunnel_admin_stop(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _admin(user)
    s = get_settings()
    return TunnelManager(s.data_dir, local_port=s.port).stop()
