#!/usr/bin/env python3
"""轻量对象存储（S3 PutObject 子集）：给公网跳板 / 本机直传大文件用。

手机 PUT 到此服务，不经 chat-api 反向隧道；chat-api 再 GET 入库。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

DATA = Path(os.environ.get("S3LITE_DATA", "/var/lib/s3lite"))
ACCESS = os.environ.get("S3LITE_ACCESS_KEY", "huanxin")
SECRET = os.environ.get("S3LITE_SECRET_KEY", "change-me")
BUCKET = os.environ.get("S3LITE_BUCKET", "chat-media")
TTL = int(os.environ.get("S3LITE_URL_TTL", "900"))

app = FastAPI(title="s3lite", docs_url=None, redoc_url=None)
(DATA / BUCKET).mkdir(parents=True, exist_ok=True)


def _sign(method: str, key: str, exp: int) -> str:
    msg = f"{method}\n{BUCKET}\n{key}\n{exp}".encode()
    return hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _check(method: str, key: str, exp: str | None, sig: str | None) -> None:
    if not exp or not sig:
        raise HTTPException(403, "missing signature")
    try:
        exp_i = int(exp)
    except ValueError as e:
        raise HTTPException(403, "bad exp") from e
    if exp_i < int(time.time()):
        raise HTTPException(403, "expired")
    if not hmac.compare_digest(_sign(method, key, exp_i), sig):
        raise HTTPException(403, "bad signature")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "s3lite", "bucket": BUCKET}


@app.post("/v1/presign")
async def presign(request: Request) -> dict:
    """内部签发：需要 X-S3Lite-Admin = SECRET。"""
    if request.headers.get("X-S3Lite-Admin") != SECRET:
        raise HTTPException(403, "forbidden")
    body = await request.json()
    key = str(body.get("key") or "").lstrip("/")
    if not key or ".." in key:
        raise HTTPException(400, "bad key")
    exp = int(time.time()) + TTL
    put_sig = _sign("PUT", key, exp)
    get_sig = _sign("GET", key, exp)
    base = str(body.get("public_base") or "").rstrip("/")
    return {
        "key": key,
        "bucket": BUCKET,
        "expires": exp,
        "put_url": f"{base}/{BUCKET}/{key}?exp={exp}&sig={put_sig}" if base else "",
        "get_url": f"{base}/{BUCKET}/{key}?exp={exp}&sig={get_sig}" if base else "",
        "put_sig": put_sig,
        "get_sig": get_sig,
    }


@app.put(f"/{BUCKET}/{{key:path}}")
async def put_object(key: str, request: Request, exp: str | None = None, sig: str | None = None) -> dict:
    _check("PUT", key, exp, sig)
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty")
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(400, "too large")
    dest = DATA / BUCKET / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"ok": True, "key": key, "size": len(data)}


@app.get(f"/{BUCKET}/{{key:path}}")
def get_object(key: str, exp: str | None = None, sig: str | None = None) -> Response:
    _check("GET", key, exp, sig)
    dest = DATA / BUCKET / key
    if not dest.is_file():
        raise HTTPException(404, "missing")
    return FileResponse(dest)
