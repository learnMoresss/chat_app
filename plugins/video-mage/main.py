# 集群节点：本进程是能力实例，由 worker-agent 按需启停。
# 当前：ffmpeg 抽帧兜底（Mage-VL 权重实测通过前的可联调实现）。
# Mage-VL 可用后，将 _understand 换成模型推理，接口不变。

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

PLUGIN_ID = "video-mage"
TOKEN = ""
MODE = "ffmpeg_fallback"  # 或 mage_vl（实测通过后切换）
app = FastAPI(title="plugin-video-mage")
_jobs: Dict[str, Dict[str, Any]] = {}


def _auth(authorization: Optional[str]) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


class JobIn(BaseModel):
    capability: str = "video.understand"
    input: Dict[str, Any] = Field(default_factory=dict)
    files: list = Field(default_factory=list)
    upload: Optional[Dict[str, Any]] = None
    user_id: str = ""
    timeout_seconds: int = 300


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "state": "ready",
        "mode": MODE,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


@app.get("/manifest")
def manifest(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "id": PLUGIN_ID,
        "version": "0.1.0",
        "capabilities": ["video.understand", "vision"],
        "mode": MODE,
    }


@app.post("/unload")
def unload(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {"ok": True, "state": "unloading", "mode": MODE}


@app.post("/jobs")
async def create_job(
    body: JobIn, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _auth(authorization)
    if body.capability not in ("video.understand", "vision"):
        raise HTTPException(status_code=400, detail="unsupported capability")
    if not body.files and not body.input.get("url"):
        raise HTTPException(status_code=400, detail="需要 files[].url 或 input.url")
    job_id = secrets.token_hex(8)
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "body": body.model_dump(),
        "result": None,
        "error": "",
        "progress": 0,
        "partials": [],
    }
    asyncio.create_task(_run(job_id))
    return {"job_id": job_id, "status": "running"}


async def _run(job_id: str) -> None:
    job = _jobs[job_id]
    body = job["body"]
    try:
        url = None
        files = body.get("files") or []
        if files:
            url = files[0].get("url")
        url = url or (body.get("input") or {}).get("url")
        if not url:
            raise RuntimeError("缺少视频地址")
        local = await asyncio.to_thread(_fetch, url)
        job["progress"] = 20
        segments = await asyncio.to_thread(_ffmpeg_segments, local)
        job["progress"] = 70
        text = _format_first_person(segments)
        job["partials"] = segments
        job["result"] = {
            "text": text,
            "mode": MODE,
            "segments": segments,
        }
        job["status"] = "done"
        job["progress"] = 100
        try:
            local.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)


def _fetch(url: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="vmage_")) / "in.bin"
    with httpx.Client(timeout=120.0, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        tmp.write_bytes(r.content)
    return tmp


def _ffmpeg_segments(path: Path, max_frames: int = 16, max_seconds: int = 300) -> List[Dict[str, Any]]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("需要 ffmpeg")
    # 探测时长
    dur = 0.0
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nk=1:nw=1",
                str(path),
            ],
            text=True,
            timeout=30,
        ).strip()
        dur = float(out or 0)
    except Exception:
        dur = 60.0
    use = min(dur, float(max_seconds)) if dur > 0 else float(max_seconds)
    n = max(1, min(max_frames, int(use // 8) or 1))
    out_dir = path.parent / "frames"
    out_dir.mkdir(exist_ok=True)
    # 均匀抽帧
    vf = f"fps={max(n / max(use, 1.0), 0.1)},scale=512:-2"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-t",
            str(int(use)),
            "-i",
            str(path),
            "-vf",
            vf,
            str(out_dir / "f_%03d.jpg"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    frames = sorted(out_dir.glob("f_*.jpg"))[:max_frames]
    segs = []
    for i, fp in enumerate(frames):
        t0 = round(i * use / max(len(frames), 1), 1)
        t1 = round((i + 1) * use / max(len(frames), 1), 1)
        segs.append(
            {
                "t0": t0,
                "t1": t1,
                "note": f"抽到一帧（{fp.stat().st_size} bytes）",
                "frame": fp.name,
            }
        )
    return segs


def _format_first_person(segments: List[Dict[str, Any]]) -> str:
    if not segments:
        return "[对方发来的视频，你看了一下，画面很短或打不开。]"
    parts = [
        f"{s['t0']:.0f}–{s['t1']:.0f} 秒附近有画面"
        for s in segments[:8]
    ]
    return (
        "[对方发来的视频，你看得很清楚："
        + "；".join(parts)
        + f"。共采样 {len(segments)} 帧"
        + ("（当前为抽帧兜底，完整 Mage-VL 描述待接入）" if MODE == "ffmpeg_fallback" else "")
        + "]"
    )


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
        "error": job.get("error") or "",
        "progress": job.get("progress") or 0,
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
        last = -1
        while job["status"] == "running":
            pct = int(job.get("progress") or 0)
            if pct != last:
                yield f"event: progress\ndata: {json.dumps({'pct': pct})}\n\n"
                last = pct
            await asyncio.sleep(0.2)
        if job["status"] == "done":
            yield f"event: done\ndata: {json.dumps({'result': job.get('result')}, ensure_ascii=False)}\n\n"
        else:
            yield f"event: failed\ndata: {json.dumps({'error': job.get('error') or job['status']})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


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
