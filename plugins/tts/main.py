# TTS 插件：包装 Breeze / VoiceDesign HTTP（与 server.app.tts 路径对齐）。
from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

PLUGIN_ID = "tts"
TOKEN = ""
BREEZE_URL = os.environ.get("TTS_BREEZE_URL", "http://127.0.0.1:8883").rstrip("/")
app = FastAPI(title="plugin-tts")
_loaded = False


def _auth(authorization: Optional[str]) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str = ""
    emotion: str = ""


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "state": "ready" if _loaded else "idle",
        "backend": BREEZE_URL,
    }


@app.get("/manifest")
def manifest(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "id": PLUGIN_ID,
        "version": "0.1.0",
        "capabilities": ["tts"],
        "endpoints": ["/v1/audio/speech"],
    }


@app.post("/unload")
def unload(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    global _loaded
    _loaded = False
    return {"ok": True, "state": "unloading"}


@app.post("/v1/audio/speech")
async def speech(
    body: SpeakIn, authorization: Optional[str] = Header(default=None)
) -> Response:
    _auth(authorization)
    global _loaded
    _loaded = True
    # Breeze 常见接口：POST /v1/audio/speech 或 /synthesize；按环境探测
    candidates = [
        f"{BREEZE_URL}/v1/audio/speech",
        f"{BREEZE_URL}/synthesize",
        f"{BREEZE_URL}/tts",
    ]
    last_err = ""
    for url in candidates:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    url,
                    json={
                        "input": body.text,
                        "text": body.text,
                        "voice": body.voice or "default",
                        "emotion": body.emotion or "",
                    },
                )
            if r.status_code < 400 and r.content and len(r.content) > 64:
                ctype = r.headers.get("content-type") or "audio/wav"
                return Response(content=r.content, media_type=ctype)
            last_err = f"{url} -> {r.status_code}"
        except Exception as exc:
            last_err = str(exc)
    raise HTTPException(502, f"TTS 后端不可用: {last_err}")


def main() -> None:
    global TOKEN, BREEZE_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9102)
    ap.add_argument("--token", default="dev")
    ap.add_argument("--breeze-url", default=BREEZE_URL)
    args = ap.parse_args()
    TOKEN = args.token
    BREEZE_URL = args.breeze_url.rstrip("/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
