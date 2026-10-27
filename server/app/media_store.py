"""媒体落盘：按 sha256 默认去重，同账号同内容只保留一份磁盘文件。"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import db
from .config import get_settings
from .security import iso, new_id

logger = logging.getLogger("chat.media_store")


def files_root() -> Path:
    root = get_settings().data_dir / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    return (name or "bin").replace("/", "_").replace("\\", "_")[:180]


def find_by_hash(*, user_id: str, sha: str, size: int) -> Optional[Dict[str, Any]]:
    if not sha:
        return None
    row = db.fetchone(
        """
        SELECT * FROM media_files
        WHERE user_id = ? AND sha256 = ? AND size = ?
          AND source != 'pending' AND COALESCE(sha256, '') != ''
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (user_id, sha, int(size)),
    )
    if row is None:
        return None
    path = Path(str(row["path"] or ""))
    if not path.is_file():
        return None
    return dict(row)


def put_bytes(
    *,
    user_id: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    source: str = "upload",
    filename: str = "",
    file_id: str = "",
    force_new_id: bool = False,
) -> Tuple[str, bool]:
    """写入媒体；同 user + sha256 + size 命中则复用已有 file_id（不占新磁盘）。

    返回 (file_id, deduped)。
    - 默认：命中则直接返回旧 id，不写新行。
    - force_new_id / 预分配 file_id：仍建/更新该 id 行，但 path 指向已有文件（硬链或同 path）。
    """
    if not data:
        raise ValueError("空文件")
    sha = sha256_bytes(data)
    ctype = (content_type or "application/octet-stream").strip() or "application/octet-stream"
    hit = find_by_hash(user_id=user_id, sha=sha, size=len(data))

    # 无指定 id：直接复用
    if hit and not file_id and not force_new_id:
        return str(hit["id"]), True

    user_dir = files_root() / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    fid = (file_id or "").strip() or new_id("mf_")
    name = _safe_name(filename) if filename else ""
    dest = user_dir / (f"{fid}_{name}" if name else fid)

    if hit:
        canon = Path(str(hit["path"]))
        # 预分配/强制新 id：指向同一份文件，不复制字节
        if dest.resolve() != canon.resolve():
            try:
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                try:
                    dest.hardlink_to(canon)
                except OSError:
                    # 跨设备等：仍写 path 指到 canonical，不占双份
                    dest = canon
            except Exception:
                dest = canon
        existing = db.fetchone("SELECT id FROM media_files WHERE id = ?", (fid,))
        now = iso()
        if existing:
            db.execute(
                """
                UPDATE media_files
                SET path=?, content_type=?, size=?, sha256=?, source=?
                WHERE id=? AND user_id=?
                """,
                (str(dest.resolve() if dest.exists() else canon.resolve()), ctype, len(data), sha, source, fid, user_id),
            )
        else:
            db.execute(
                """
                INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fid,
                    user_id,
                    str(dest.resolve() if dest.exists() else canon.resolve()),
                    ctype,
                    len(data),
                    sha,
                    source,
                    now,
                ),
            )
        return fid, True

    dest.write_bytes(data)
    now = iso()
    existing = db.fetchone("SELECT id FROM media_files WHERE id = ?", (fid,))
    if existing:
        db.execute(
            """
            UPDATE media_files
            SET path=?, content_type=?, size=?, sha256=?, source=?
            WHERE id=? AND user_id=?
            """,
            (str(dest.resolve()), ctype, len(data), sha, source, fid, user_id),
        )
    else:
        db.execute(
            """
            INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (fid, user_id, str(dest.resolve()), ctype, len(data), sha, source, now),
        )
    return fid, False


def backfill_and_collapse(*, user_id: str = "", limit: int = 5000) -> Dict[str, int]:
    """补算空 sha，并把同 hash 多余副本的 path 收到最早一份，删掉多余磁盘文件。"""
    scanned = 0
    hashed = 0
    collapsed = 0
    freed = 0
    sql = """
      SELECT id, user_id, path, size, sha256 FROM media_files
      WHERE source != 'pending'
    """
    params: list = []
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)
    sql += " ORDER BY created_at ASC LIMIT ?"
    params.append(max(1, min(int(limit), 20000)))
    rows = db.fetchall(sql, params)
    # 1) backfill
    for r in rows:
        scanned += 1
        sha = (r["sha256"] or "").strip()
        path = Path(str(r["path"] or ""))
        if sha and len(sha) == 64:
            continue
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except Exception:
            continue
        digest = sha256_bytes(data)
        db.execute(
            "UPDATE media_files SET sha256=?, size=? WHERE id=?",
            (digest, len(data), r["id"]),
        )
        hashed += 1

    # 2) collapse by (user_id, sha256)
    groups = db.fetchall(
        """
        SELECT user_id, sha256, COUNT(*) AS n
        FROM media_files
        WHERE sha256 != '' AND source != 'pending'
          AND (? = '' OR user_id = ?)
        GROUP BY user_id, sha256
        HAVING n > 1
        """,
        (user_id, user_id),
    )
    for g in groups:
        members = db.fetchall(
            """
            SELECT id, path, size FROM media_files
            WHERE user_id = ? AND sha256 = ? AND source != 'pending'
            ORDER BY created_at ASC
            """,
            (g["user_id"], g["sha256"]),
        )
        if len(members) < 2:
            continue
        canon = members[0]
        canon_path = Path(str(canon["path"] or ""))
        if not canon_path.is_file():
            continue
        canon_resolved = str(canon_path.resolve())
        for m in members[1:]:
            p = Path(str(m["path"] or ""))
            if p.is_file():
                try:
                    same = p.resolve() == canon_path.resolve()
                except Exception:
                    same = False
                if not same:
                    try:
                        freed += p.stat().st_size
                        p.unlink()
                    except Exception:
                        pass
            if str(m["path"]) != canon_resolved:
                db.execute(
                    "UPDATE media_files SET path=? WHERE id=?",
                    (canon_resolved, m["id"]),
                )
                collapsed += 1
    return {
        "scanned": scanned,
        "hashed": hashed,
        "collapsed": collapsed,
        "freed_bytes": freed,
    }
