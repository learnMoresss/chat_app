#!/usr/bin/env python3
"""公网跳板媒体暂存：手机直传至此（不经 SSH 反向隧道），AI 主机再拉取并删除。"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

AUTH_ME_URL = os.environ.get("STAGING_AUTH_ME_URL", "http://127.0.0.1:18000/api/v1/auth/me")
STAGING_KEY = os.environ.get("STAGING_KEY", "").strip()
DATA_DIR = Path(os.environ.get("STAGING_DATA_DIR", "/var/lib/chat-upload-staging"))
MAX_BYTES = int(os.environ.get("STAGING_MAX_BYTES", str(32 * 1024 * 1024)))
TTL_SEC = int(os.environ.get("STAGING_TTL_SEC", "3600"))

app = FastAPI(title="chat-upload-staging", docs_url=None, redoc_url=None)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _meta_path(sid: str) -> Path:
    return DATA_DIR / f"{sid}.json"


def _bin_path(sid: str) -> Path:
    return DATA_DIR / f"{sid}.bin"


def _cleanup_expired() -> None:
    now = time.time()
    for meta in DATA_DIR.glob("*.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            created = float(info.get("created_at", 0))
            if now - created > TTL_SEC:
                sid = meta.stem
                meta.unlink(missing_ok=True)
                _bin_path(sid).unlink(missing_ok=True)
        except Exception:
            continue


async def _auth_user(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(AUTH_ME_URL, headers={"Authorization": authorization})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"鉴权通道不可用：{e}") from e
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="登录已失效")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"鉴权失败（{r.status_code}）")
    data = r.json()
    user = data.get("user") if isinstance(data, dict) and "user" in data else data
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=502, detail="鉴权响应无效")
    return user


def _require_key(x_staging_key: str | None) -> None:
    if not STAGING_KEY or x_staging_key != STAGING_KEY:
        raise HTTPException(status_code=403, detail="forbidden")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "chat-upload-staging", "files": len(list(DATA_DIR.glob("*.bin")))}


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    kind: str = Form(default="image"),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _cleanup_expired()
    user = await _auth_user(authorization)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="单文件不能超过 32MB")
    sid = secrets.token_hex(16)
    ctype = file.content_type or "application/octet-stream"
    if kind == "image" and not ctype.startswith("image/"):
        ctype = "image/jpeg"
    name = (file.filename or "bin").replace("/", "_")
    _bin_path(sid).write_bytes(data)
    _meta_path(sid).write_text(
        json.dumps(
            {
                "id": sid,
                "user_id": user["id"],
                "filename": name,
                "content_type": ctype,
                "kind": kind,
                "size": len(data),
                "created_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"staging_id": sid, "size": len(data), "content_type": ctype, "name": name}


@app.get("/internal/take/{staging_id}")
def take(
    staging_id: str,
    user_id: str,
    x_staging_key: str | None = Header(default=None),
) -> Response:
    """AI 主机拉取并删除；仅应暴露在跳板 loopback（经 SSH -L）。"""
    _require_key(x_staging_key)
    meta_p = _meta_path(staging_id)
    bin_p = _bin_path(staging_id)
    if not meta_p.is_file() or not bin_p.is_file():
        raise HTTPException(status_code=404, detail="staging missing")
    try:
        info = json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail="meta corrupt") from e
    if str(info.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="owner mismatch")
    data = bin_p.read_bytes()
    meta_p.unlink(missing_ok=True)
    bin_p.unlink(missing_ok=True)
    return Response(
        content=data,
        media_type=info.get("content_type") or "application/octet-stream",
        headers={
            "X-Staging-Filename": info.get("filename") or "bin",
            "X-Staging-Content-Type": info.get("content_type") or "application/octet-stream",
            "X-Staging-Kind": str(info.get("kind") or "file"),
            "X-Staging-Size": str(len(data)),
        },
    )


@app.get("/internal/meta/{staging_id}")
def meta(
    staging_id: str,
    user_id: str,
    x_staging_key: str | None = Header(default=None),
) -> JSONResponse:
    _require_key(x_staging_key)
    meta_p = _meta_path(staging_id)
    if not meta_p.is_file():
        raise HTTPException(status_code=404, detail="staging missing")
    info = json.loads(meta_p.read_text(encoding="utf-8"))
    if str(info.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="owner mismatch")
    return JSONResponse(info)
