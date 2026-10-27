# ASR 插件：包装产品侧 asr_gen（HTTP transcriptions / 本机 whisper）。
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

PLUGIN_ID = "asr"
TOKEN = ""
app = FastAPI(title="plugin-asr")
_loaded = False

# 允许从仓库根引用 server.app
_ROOT = Path(__file__).resolve().parents[2]
_SERVER = _ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def _auth(authorization: Optional[str]) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "state": "ready" if _loaded else "idle",
        "model": "asr_gen",
    }


@app.get("/manifest")
def manifest(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "id": PLUGIN_ID,
        "version": "0.2.0",
        "capabilities": ["asr"],
        "openai_compatible": ["/v1/audio/transcriptions"],
    }


@app.post("/unload")
def unload(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    global _loaded
    _loaded = False
    return {"ok": True, "state": "unloading"}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    authorization: Optional[str] = Header(default=None),
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    language: Optional[str] = Form(default="zh"),
) -> Dict[str, Any]:
    _auth(authorization)
    global _loaded
    _loaded = True
    data = await file.read()
    name = file.filename or "audio.wav"
    tmp = Path(tempfile.gettempdir()) / f"asr-plugin-{name}"
    tmp.write_bytes(data)
    text = ""
    try:
        from app.asr_gen import transcribe_audio_path

        text = (transcribe_audio_path(tmp, language=language or "zh") or "").strip()
    except Exception as exc:
        text = f"[asr-error] {exc}"
    if not text:
        text = f"[asr-empty] 收到 {len(data)} 字节，未能转写"
    return {"text": text, "model": model}


def main() -> None:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--token", default="dev")
    args = ap.parse_args()
    TOKEN = args.token
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
