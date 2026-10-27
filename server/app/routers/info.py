from __future__ import annotations

from fastapi import APIRouter

from .. import __version__, db
from ..config import get_settings
from ..schemas import ServerInfo, TunnelStatus, TunnelProviderInfo
from ..tunnel import TunnelManager, load_state, provider_catalog

router = APIRouter(tags=["info"])


def _tunnel_snapshot() -> tuple[str, bool, str]:
    settings = get_settings()
    st = load_state(settings.data_dir)
    provider = str(st.get("provider") or settings.public_tunnel_provider or "none")
    active = bool(st.get("active"))
    url = str(st.get("public_url") or "").rstrip("/")
    return provider, active, url


@router.get("/api/info", response_model=ServerInfo)
def server_info() -> ServerInfo:
    settings = get_settings()
    row = db.fetchone("SELECT value FROM server_settings WHERE key = 'registration_mode'")
    mode = (row["value"] if row else settings.registration_mode) or "open"
    if mode not in ("open", "invite", "closed"):
        mode = "open"
    provider, active, tunnel_url = _tunnel_snapshot()
    public = tunnel_url or settings.public_base_url.rstrip("/")
    return ServerInfo(
        name=settings.server_name,
        version=__version__,
        registration_mode=mode,  # type: ignore[arg-type]
        min_app_version=settings.min_app_version,
        public_base_url=public,
        tunnel_provider=provider,
        tunnel_active=active,
        tunnel_public_url=tunnel_url,
    )


@router.get("/api/tunnel", response_model=TunnelStatus)
def tunnel_status() -> TunnelStatus:
    """公开：当前穿透状态 + 可选提供方列表（无密钥）。"""
    settings = get_settings()
    mgr = TunnelManager(settings.data_dir, local_port=settings.port)
    st = mgr.status()
    providers = [TunnelProviderInfo(**p) for p in provider_catalog()]
    return TunnelStatus(
        provider=str(st.get("provider") or "none"),
        active=bool(st.get("active")),
        public_url=str(st.get("public_url") or ""),
        pid=st.get("pid"),
        error=str(st.get("error") or ""),
        updated_at=int(st.get("updated_at") or 0),
        providers=providers,
    )
