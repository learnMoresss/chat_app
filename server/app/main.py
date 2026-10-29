from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .config import get_settings
from .gateway.jobs import interrupt_running_jobs_on_startup
from .gateway.reaper import start_reaper
from .gateway.registry import sync_workers_from_config
from .scheduler_loop import start_scheduler
from .ws_hub import set_main_loop

_voice_log_handler = logging.StreamHandler()
_voice_log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
for _name in ("voice_realtime", "asr_ondemand", "asr_gen"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.INFO)
    _lg.addHandler(_voice_log_handler)
    _lg.propagate = False

from .routers import (
    admin,
    auth,
    calls_api,
    capabilities,
    channels,
    chat,
    companion_api,
    devices,
    dev_sim,
    extract,
    info,
    listen_api,
    media,
    moments,
    music_api,
    persona_assets,
    personas,
    place_api,
    prefs,
    schedules_api,
    shares,
    stickers_api,
    tunnel_admin,
    usage,
    virtual_devices_api,
    voice_realtime_api,
    ws,
)

# React + antd 构建产物（admin-ui: npm run build）
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
# 兼容旧静态页（未构建时回退）
WEB_LEGACY = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        set_main_loop(asyncio.get_running_loop())
        db.connect()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            sync_workers_from_config()
            interrupt_running_jobs_on_startup()
        except Exception:
            pass
        start_reaper(interval_seconds=60.0)
        start_scheduler(interval_seconds=30.0)
        # TTS 预热（实时语音首包）；失败不阻断启动
        try:
            from . import voice_realtime as _vr

            asyncio.get_running_loop().run_in_executor(None, _vr.warm_in_background)
        except Exception:
            pass
        # 可选：启动时拉起公网穿透（无自有 VPS 时用 cloudflared/ngrok 等）
        if settings.public_tunnel_autostart and settings.public_tunnel_provider not in (
            "",
            "none",
            "lan",
            "off",
        ):
            try:
                from .tunnel import TunnelManager

                def _boot_tunnel() -> None:
                    TunnelManager(settings.data_dir, local_port=settings.port).start(
                        settings.public_tunnel_provider,
                        ngrok_authtoken=settings.public_tunnel_ngrok_authtoken,
                        cloudflared_token=settings.public_tunnel_cloudflared_token,
                        ssh_config=settings.public_tunnel_ssh_config,
                        ssh_host=settings.public_tunnel_ssh_host,
                        ssh_remote_port=settings.public_tunnel_ssh_remote_port,
                        wait_url_sec=20.0,
                    )

                asyncio.get_running_loop().run_in_executor(None, _boot_tunnel)
            except Exception:
                pass
        yield

    app = FastAPI(title=settings.server_name, version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_zh(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """校验失败返回可读中文，避免 App 直接弹出英文 schema 错误。"""
        msgs: list[str] = []
        for err in exc.errors():
            loc = err.get("loc") or ()
            field = str(loc[-1]) if loc else ""
            typ = str(err.get("type") or "")
            if field in ("password", "new_password") and "too_short" in typ:
                msgs.append("密码至少 8 位")
            elif field == "username" and "too_short" in typ:
                msgs.append("用户名至少 2 个字符")
            elif field in ("password", "new_password") and "too_long" in typ:
                msgs.append("密码太长")
            else:
                raw = str(err.get("msg") or "").strip()
                if raw and "String should" not in raw:
                    msgs.append(raw)
                elif field:
                    msgs.append(f"{field} 填写有误")
        detail = "；".join(dict.fromkeys(msgs)) if msgs else "填写有误，请检查后再试"
        return JSONResponse(status_code=422, content={"detail": detail})
    app.include_router(info.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(devices.router)
    app.include_router(channels.router)
    app.include_router(personas.router)
    app.include_router(persona_assets.router)
    app.include_router(usage.router)
    app.include_router(extract.router)
    app.include_router(prefs.router)
    app.include_router(chat.router)
    app.include_router(dev_sim.router)
    app.include_router(music_api.router)
    app.include_router(listen_api.router)
    app.include_router(place_api.router)
    app.include_router(calls_api.router)
    app.include_router(voice_realtime_api.router)
    app.include_router(ws.router)
    app.include_router(capabilities.router)
    app.include_router(media.router)
    app.include_router(shares.router)
    app.include_router(schedules_api.router)
    app.include_router(moments.router)
    app.include_router(stickers_api.router)
    app.include_router(virtual_devices_api.router)
    app.include_router(companion_api.router)
    app.include_router(tunnel_admin.router)

    @app.get("/health")
    def health() -> dict:
        from .config import get_settings
        from .tunnel import load_state
        from .dsh_gateway import dsh_enabled

        s = get_settings()
        tun = load_state(s.data_dir)
        return {
            "ok": True,
            "version": __version__,
            "dsh_configured": dsh_enabled(),
            "tunnel_provider": tun.get("provider") or "none",
            "tunnel_active": bool(tun.get("active")),
            "tunnel_public_url": tun.get("public_url") or "",
        }

    dist_index = WEB_DIST / "index.html"
    if dist_index.is_file():
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        def web_index() -> FileResponse:
            return FileResponse(dist_index)

        @app.get("/favicon.svg")
        def favicon() -> FileResponse:
            fav = WEB_DIST / "favicon.svg"
            if fav.is_file():
                return FileResponse(fav)
            return FileResponse(dist_index)
    elif WEB_LEGACY.is_dir() and (WEB_LEGACY / "index.html").is_file():
        @app.get("/")
        def web_index_legacy() -> FileResponse:
            return FileResponse(WEB_LEGACY / "index.html")

        app.mount("/web", StaticFiles(directory=str(WEB_LEGACY), html=True), name="web")

    # 语音通话网页测试（半双工：LLM 流式分句 → TTS 流式出块 → 浏览器播）
    voice_call_page = WEB_LEGACY / "voice-call.html"
    if voice_call_page.is_file():
        @app.get("/voice-call")
        def voice_call() -> FileResponse:
            return FileResponse(voice_call_page)

    # dist 模式下也挂 /web，方便 /web/voice-call.html（legacy 分支已挂过则跳过）
    if dist_index.is_file() and WEB_LEGACY.is_dir():
        app.mount("/web", StaticFiles(directory=str(WEB_LEGACY), html=True), name="web")

    return app


app = create_app()
