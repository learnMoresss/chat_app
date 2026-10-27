# 集群节点：本进程是能力实例，由 worker-agent 按需启停。
# 单机部署时也是「一节点集群」里的一个数据面进程。

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

PLUGIN_ID = "download"
TOKEN = ""
# 单文件上限（MB）与时长上限（秒），可由环境变量覆盖
MAX_MB = int(os.environ.get("DOWNLOAD_MAX_MB", "500"))
MAX_DURATION = int(os.environ.get("DOWNLOAD_MAX_DURATION", "3600"))
app = FastAPI(title="plugin-download")
_jobs: Dict[str, Dict[str, Any]] = {}
_meta: Dict[str, Any] = {"yt_dlp": "", "ffmpeg": False, "last_upgrade_at": None}


def _auth(authorization: Optional[str]) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None


def _yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return str(__version__)
    except Exception:
        return ""


def _maybe_upgrade_ytdlp() -> None:
    """启动时尝试升级 yt-dlp（失败不阻断服务）。每天最多一次。"""
    stamp = Path(tempfile.gettempdir()) / "chat-app-ytdlp-upgrade.stamp"
    try:
        if stamp.is_file() and time.time() - stamp.stat().st_mtime < 86400:
            return
        subprocess.run(
            [os.environ.get("DOWNLOAD_PYTHON", "python3"), "-m", "pip", "install", "-U", "yt-dlp"],
            check=False,
            capture_output=True,
            timeout=300,
        )
        stamp.write_text(str(int(time.time())), encoding="utf-8")
        _meta["last_upgrade_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _meta["yt_dlp"] = _yt_dlp_version()
    except Exception:
        pass


def _refuse_ssrf(url: str) -> None:
    """只允许 http/https；解析后拒绝本机与内网地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只允许 http/https 链接")
    host = parsed.hostname
    if not host:
        raise ValueError("链接无效")
    if host.lower() in ("localhost", "metadata.google.internal"):
        raise ValueError("拒绝本机 / 元数据地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"无法解析主机: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError("拒绝内网地址")


class JobIn(BaseModel):
    capability: str = "media.download"
    input: Dict[str, Any] = Field(default_factory=dict)
    files: list = Field(default_factory=list)
    upload: Optional[Dict[str, Any]] = None
    user_id: str = ""
    timeout_seconds: int = 600


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "state": "ready",
        "ffmpeg": _meta.get("ffmpeg", _ffmpeg_ok()),
        "yt_dlp": _meta.get("yt_dlp") or _yt_dlp_version(),
        "max_mb": MAX_MB,
        "max_duration": MAX_DURATION,
        "last_upgrade_at": _meta.get("last_upgrade_at"),
    }


@app.get("/manifest")
def manifest(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _auth(authorization)
    return {
        "id": PLUGIN_ID,
        "version": "0.2.0",
        "capabilities": ["media.download"],
        "actions": ["probe", "download"],
        "limits": {"max_mb": MAX_MB, "max_duration": MAX_DURATION},
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
    if body.capability != "media.download":
        raise HTTPException(status_code=400, detail="unsupported capability")
    action = str(body.input.get("action") or "probe")
    url = str(body.input.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url")
    try:
        _refuse_ssrf(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job_id = secrets.token_hex(8)
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "action": action,
        "url": url,
        "upload": body.upload,
        "input": body.input,
        "result": None,
        "error": "",
        "progress": 0,
        "created_at": time.time(),
    }
    asyncio.create_task(_run(job_id))
    return {"job_id": job_id, "status": "running"}


async def _run(job_id: str) -> None:
    job = _jobs[job_id]
    try:
        if job["action"] == "probe":
            info = await asyncio.to_thread(_probe, job["url"])
            job["result"] = info
            job["status"] = "done"
            job["progress"] = 100
        elif job["action"] == "download":
            if not _ffmpeg_ok():
                raise RuntimeError("本机未找到 ffmpeg，无法合并音视频")
            # 先探测再下，拦超长
            info = await asyncio.to_thread(_probe, job["url"])
            job["progress"] = 5
            _enforce_limits(info)
            path = await asyncio.to_thread(_download, job["url"], job)
            size = path.stat().st_size
            if size > MAX_MB * 1024 * 1024:
                raise RuntimeError(f"文件超过上限 {MAX_MB}MB（实际 {size // (1024 * 1024)}MB）")
            upload = job.get("upload") or {}
            upload_url = upload.get("url")
            if not upload_url:
                raise RuntimeError("下载需要 input 之外的 upload.url（网关签发）")
            await _upload_file(upload_url, path)
            job["result"] = {
                "file_uploaded": True,
                "file_id": upload.get("file_id"),
                "filename": path.name,
                "size": size,
                "title": job.get("title") or info.get("title") or path.stem,
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "webpage_url": info.get("webpage_url") or job["url"],
            }
            job["status"] = "done"
            job["progress"] = 100
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            raise RuntimeError(f"未知 action: {job['action']}")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)


def _enforce_limits(info: Dict[str, Any]) -> None:
    dur = info.get("duration")
    if dur is not None and int(dur) > MAX_DURATION:
        raise RuntimeError(f"时长超过上限 {MAX_DURATION}s（实际 {int(dur)}s）")
    # filesize 在 probe 时可能只有估算
    for key in ("filesize", "filesize_approx"):
        fs = info.get(key)
        if fs and int(fs) > MAX_MB * 1024 * 1024:
            raise RuntimeError(f"文件超过上限 {MAX_MB}MB")


def _probe(url: str) -> Dict[str, Any]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    out = {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "extractor": info.get("extractor"),
        "webpage_url": info.get("webpage_url") or url,
        "ext": info.get("ext"),
        "filesize": info.get("filesize") or info.get("filesize_approx"),
    }
    _enforce_limits(out)
    return out


def _download(url: str, job: Dict[str, Any]) -> Path:
    import yt_dlp

    tmp = Path(tempfile.mkdtemp(prefix="ytdlp_"))
    outtmpl = str(tmp / "%(title).80B.%(ext)s")

    def hook(d: Dict[str, Any]) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                # 5–95% 预留给探测与上传
                job["progress"] = 5 + int(done * 90 / total)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        job["title"] = info.get("title")
        prepared = Path(ydl.prepare_filename(info))
        if prepared.suffix != ".mp4":
            alt = prepared.with_suffix(".mp4")
            if alt.is_file():
                prepared = alt
        if not prepared.is_file():
            files = list(tmp.glob("*"))
            if not files:
                raise RuntimeError("下载完成但找不到文件")
            prepared = files[0]
        # 简单验 MP4 ftyp
        head = prepared.read_bytes()[:12]
        if b"ftyp" not in head and prepared.suffix.lower() == ".mp4":
            raise RuntimeError("文件头不像 MP4（可能下到了报错页）")
        return prepared


async def _upload_file(url: str, path: Path) -> None:
    data = path.read_bytes()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            url,
            files={"file": (path.name, data, "video/mp4")},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"上传失败: {r.status_code} {r.text}")


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
            await asyncio.sleep(0.3)
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
    _meta["ffmpeg"] = _ffmpeg_ok()
    _meta["yt_dlp"] = _yt_dlp_version()
    if not _meta["ffmpeg"]:
        # 仍启动，下载时再报错，方便健康页看到
        pass
    threading.Thread(target=_maybe_upgrade_ytdlp, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
