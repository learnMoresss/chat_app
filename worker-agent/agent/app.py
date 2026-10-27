from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__, gpus as gpu_mod
from .config import get_settings
from .plugins_loader import list_public
from .process_manager import manager

# 集群数据面：本进程是 worker-agent，只管本机插件生命周期与 GPU 探活。

app = FastAPI(title="chat_app worker-agent", version=__version__)


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    expected = f"Bearer {settings.worker_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class StartBody(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plugin_id: str
    vram_mb: Optional[int] = None
    preferred_gpu: Optional[int] = None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "version": __version__, "role": "worker-agent"}


@app.get("/v1/plugins")
def plugins(_: None = Depends(require_token)) -> Dict[str, Any]:
    return {"plugins": list_public()}


@app.get("/v1/gpus")
def list_gpus(_: None = Depends(require_token)) -> Dict[str, Any]:
    return {"gpus": gpu_mod.list_gpus()}


@app.get("/v1/instances")
def instances(_: None = Depends(require_token)) -> Dict[str, Any]:
    return {"instances": manager.list_instances()}


@app.get("/v1/instances/{instance_id}")
def get_instance(instance_id: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    data = manager.get(instance_id)
    if not data:
        raise HTTPException(status_code=404, detail="not found")
    return data


@app.post("/v1/instances/start")
def start_instance(body: StartBody, _: None = Depends(require_token)) -> Dict[str, Any]:
    try:
        data = manager.start(
            body.plugin_id,
            vram_mb=body.vram_mb,
            preferred_gpu=body.preferred_gpu,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"request_id": body.request_id, **data}


@app.post("/v1/instances/{instance_id}/unload")
def unload_instance(instance_id: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    try:
        return manager.unload(instance_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/v1/instances/{instance_id}/stop")
def stop_instance(instance_id: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    try:
        return manager.stop(instance_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
