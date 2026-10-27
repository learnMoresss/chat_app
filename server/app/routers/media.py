from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import get_settings
from ..gateway.signed_urls import make_download_url, verify_download, verify_upload
from ..media_store import files_root, put_bytes
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/media", tags=["media"])


def _files_root() -> Path:
    return files_root()


@router.get("/download")
def download(
    file_id: str = Query(...),
    user_id: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
) -> FileResponse:
    if not verify_download(file_id=file_id, user_id=user_id, exp=exp, sig=sig):
        raise HTTPException(status_code=403, detail="invalid or expired signature")
    row = db.fetchone(
        "SELECT * FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=path.name,
    )


@router.post("/upload")
async def upload(
    file_id: str = Query(...),
    user_id: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
    file: UploadFile = File(...),
) -> dict:
    if not verify_upload(file_id=file_id, user_id=user_id, exp=exp, sig=sig):
        raise HTTPException(status_code=403, detail="invalid or expired signature")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    ctype = file.content_type or "application/octet-stream"
    fid, deduped = put_bytes(
        user_id=user_id,
        data=data,
        content_type=ctype,
        source="plugin",
        filename=file.filename or file_id,
        file_id=file_id,
    )
    return {"ok": True, "file_id": fid, "size": len(data), "deduped": deduped}


def register_upload_slot(*, user_id: str, content_type: str = "application/octet-stream") -> str:
    """预分配 file_id，供签发上传链接。"""
    file_id = new_id("mf_")
    dest = _files_root() / user_id / file_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    db.execute(
        """
        INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
        VALUES (?, ?, ?, ?, 0, '', 'pending', ?)
        """,
        (file_id, user_id, str(dest), content_type, iso()),
    )
    return file_id


@router.post("/chat-upload")
async def chat_upload(
    file: UploadFile = File(...),
    kind: str = Form(default="image"),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """登录用户上传聊天图片/视频/文件（Bearer）。同内容默认复用已有 file_id。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="单文件不能超过 80MB")
    ctype = file.content_type or "application/octet-stream"
    if kind == "image" and not ctype.startswith("image/"):
        ctype = "image/jpeg"
    if kind == "video" and not ctype.startswith("video/"):
        ctype = "video/mp4"
    name = (file.filename or "bin").replace("/", "_")
    file_id, deduped = put_bytes(
        user_id=user["id"],
        data=data,
        content_type=ctype,
        source="chat",
        filename=name,
    )
    return {
        "file_id": file_id,
        "size": len(data),
        "content_type": ctype,
        "name": name,
        "deduped": deduped,
        "url": make_download_url(file_id=file_id, user_id=user["id"], ttl_seconds=86400 * 7),
        "auth_url": f"/api/v1/media/files/{file_id}",
    }


@router.get("/files")
def list_own_files(user=Depends(current_user), limit: int = 100) -> Dict[str, Any]:
    """列表按 sha256 去重视图：同内容只展示最新一条，避免媒体库刷屏。"""
    limit = max(1, min(int(limit), 200))
    rows = db.fetchall(
        """
        SELECT id, content_type, size, source, created_at, path, sha256
        FROM media_files
        WHERE user_id = ? AND source != 'pending'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user["id"], min(limit * 8, 2000)),
    )
    seen: set[str] = set()
    files: list[Dict[str, Any]] = []
    for r in rows:
        key = (r["sha256"] or "").strip() or f"id:{r['id']}"
        if key in seen:
            continue
        seen.add(key)
        files.append(
            {
                "id": r["id"],
                "content_type": r["content_type"],
                "size": r["size"],
                "source": r["source"],
                "created_at": r["created_at"],
                "name": Path(r["path"]).name,
                "sha256": (r["sha256"] or "")[:16],
                "auth_url": f"/api/v1/media/files/{r['id']}",
            }
        )
        if len(files) >= limit:
            break
    return {"files": files}


@router.post("/dedup")
def dedup_own_files(user=Depends(current_user)) -> Dict[str, Any]:
    """补算 hash 并折叠同内容多余磁盘副本（管理员/本人）。"""
    from ..media_store import backfill_and_collapse

    return backfill_and_collapse(user_id=user["id"])


def _resolve_media_path(raw: str) -> Path:
    """兼容相对路径、绝对路径，以及从别的机器拷库后留下的宿主前缀（如 /Users/...）。"""
    path = Path(raw)
    if path.is_file():
        return path
    settings = get_settings()
    candidates = [
        settings.data_dir.parent / raw,
        settings.data_dir / raw,
        Path.cwd() / raw,
    ]
    # 任意前缀下的 .../data/files/<uid>/<name> → 本机 data_dir/files/...
    parts = path.parts
    if "files" in parts:
        i = parts.index("files")
        remapped = settings.data_dir.joinpath(*parts[i:])
        candidates.insert(0, remapped)
    name = path.name
    if name:
        # 按文件名在 data/files 下搜一层用户目录
        files_root = settings.data_dir / "files"
        if files_root.is_dir():
            for user_dir in files_root.iterdir():
                if user_dir.is_dir():
                    candidates.append(user_dir / name)
    for c in candidates:
        if c.is_file():
            return c
    return path


@router.get("/files/{file_id}")
def get_own_file(file_id: str, user=Depends(current_user)) -> FileResponse:
    from .. import stickers as stk

    row = stk.resolve_media_row(file_id=file_id, user_id=user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = _resolve_media_path(row["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=path.name,
    )
