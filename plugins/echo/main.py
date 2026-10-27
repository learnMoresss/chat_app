# 集群节点：本进程是能力实例，由 worker-agent 按需启停。
# 单机部署时也是「一节点集群」里的一个数据面进程。

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import time
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

PLUGIN_ID = "echo"
TOKEN = ""
app = FastAPI(title="plugin-echo")
_jobs: Dict[str, Dict[str, Any]] = {}


def _auth(authorization: Optional[str]) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


class JobIn(BaseModel):
    capability: str = "echo"
    input: Dict[str, Any] = Field(default_factory=dict)
    files: list = Field(default_factory=list)
    upload: Optional[Dict[str, Any]] = None
    user_id: str = ""
    timeout_seconds: int = 30


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {"ok": True, "plugin_id": PLUGIN_ID, "state": "ready"}


@app.get("/manifest")
def manifest(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "id": PLUGIN_ID,
        "version": "0.1.0",
        "capabilities": ["echo"],
        "input": {"text": "string"},
        "output": {"text": "string"},
    }


@app.post("/unload")
def unload(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {"ok": True, "state": "unloading"}


@app.post("/jobs")
async def create_job(
    body: JobIn, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _auth(authorization)
    if body.capability != "echo":
        raise HTTPException(status_code=400, detail="unsupported capability")
    job_id = secrets.token_hex(8)
    text = str(body.input.get("text") or "")
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "input_text": text,
        "result": None,
        "created_at": time.time(),
    }
    asyncio.create_task(_run(job_id, text))
    return {"job_id": job_id, "status": "running"}


async def _run(job_id: str, text: str) -> None:
    # 模拟分片：先 progress，再两段 partial，最后 done
    await asyncio.sleep(0.05)
    job = _jobs.get(job_id)
    if not job or job["status"] == "cancelled":
        return
    out = f"[echo] {text}"
    job["result"] = out
    job["status"] = "done"


@app.get("/jobs/{job_id}")
def get_job(job_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
    }


@app.delete("/jobs/{job_id}")
def cancel_job(job_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="not found")
    job["status"] = "cancelled"
    return {"job_id": job_id, "status": "cancelled"}


@app.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str, authorization: Optional[str] = Header(default=None)
) -> StreamingResponse:
    _auth(authorization)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="not found")

    async def gen():
        text = job.get("input_text") or ""
        yield _sse("progress", {"pct": 10, "message": "starting"})
        await asyncio.sleep(0.05)
        mid = max(1, len(text) // 2) if text else 0
        partial1 = f"[echo] {text[:mid]}"
        yield _sse("partial", {"text": partial1})
        await asyncio.sleep(0.05)
        while job["status"] == "running":
            await asyncio.sleep(0.05)
        if job["status"] == "cancelled":
            yield _sse("failed", {"error": "cancelled"})
            return
        full = job.get("result") or f"[echo] {text}"
        yield _sse("partial", {"text": full})
        yield _sse("done", {"result": {"text": full}})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def main() -> None:
    global TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    TOKEN = args.token
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
