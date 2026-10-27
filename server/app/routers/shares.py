from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..security import current_user, iso, new_id, utcnow

router = APIRouter(prefix="/api/v1/shares", tags=["shares"])


class ShareCreate(BaseModel):
    file_id: str
    expires_hours: int = Field(default=24, ge=1, le=168)
    max_downloads: int = Field(default=20, ge=1, le=1000)


@router.post("")
def create_share(body: ShareCreate, user=Depends(current_user)) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM media_files WHERE id = ? AND user_id = ?",
        (body.file_id, user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")
    token = secrets.token_urlsafe(24)
    sid = new_id("sh_")
    exp = (utcnow() + timedelta(hours=body.expires_hours)).isoformat()
    now = iso()
    db.execute(
        """
        INSERT INTO share_links (
          id, user_id, file_id, token, expires_at, max_downloads, downloads,
          revoked, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (sid, user["id"], body.file_id, token, exp, body.max_downloads, now),
    )
    base = get_settings().public_base_url.rstrip("/")
    return {
        "id": sid,
        "url": f"{base}/api/v1/shares/public/{token}",
        "expires_at": exp,
        "max_downloads": body.max_downloads,
    }


@router.get("")
def list_shares(user=Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT id, file_id, expires_at, max_downloads, downloads, revoked, created_at, token
        FROM share_links WHERE user_id = ? ORDER BY created_at DESC LIMIT 100
        """,
        (user["id"],),
    )
    base = get_settings().public_base_url.rstrip("/")
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "url": f"{base}/api/v1/shares/public/{r['token']}",
                "expires_at": r["expires_at"],
                "max_downloads": r["max_downloads"],
                "downloads": r["downloads"],
                "revoked": bool(r["revoked"]),
                "created_at": r["created_at"],
            }
        )
    return out


@router.delete("/{share_id}")
def revoke_share(share_id: str, user=Depends(current_user)) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT id FROM share_links WHERE id = ? AND user_id = ?",
        (share_id, user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.execute("UPDATE share_links SET revoked = 1 WHERE id = ?", (share_id,))
    return {"ok": True}


@router.get("/public/{token}")
def public_download(token: str) -> FileResponse:
    row = db.fetchone("SELECT * FROM share_links WHERE token = ?", (token,))
    if not row or row["revoked"]:
        raise HTTPException(status_code=404, detail="链接无效或已作废")
    if row["expires_at"] < iso():
        raise HTTPException(status_code=410, detail="链接已过期")
    if int(row["downloads"]) >= int(row["max_downloads"]):
        raise HTTPException(status_code=410, detail="下载次数已用完")
    f = db.fetchone("SELECT * FROM media_files WHERE id = ?", (row["file_id"],))
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = Path(f["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件缺失")
    db.execute(
        "UPDATE share_links SET downloads = downloads + 1 WHERE id = ?",
        (row["id"],),
    )
    return FileResponse(
        path,
        media_type=f["content_type"] or "application/octet-stream",
        filename=path.name,
    )
