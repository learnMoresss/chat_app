"""曲库 / 歌单 API。

曲库（music_tracks）按 **账号 user_id** 共享，与 persona 无关；
各人设一起听 / 点歌都从同一池抽。listen_sessions 只挂临时 session 歌单。
网络歌单（kind=network）预留：先登记 URL，后续再同步进池或外链播放。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .. import db
from .. import music_library as lib
from ..security import current_user, decode_access_token, iso, new_id

_optional_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/v1/music", tags=["music"])


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="library", description="library | network")
    source_url: str = Field(default="", max_length=2048)


class PlaylistAddTrack(BaseModel):
    track_id: str
    position: Optional[int] = None


def _demo_fanqiehua() -> Optional[Path]:
    cands = [
        Path("/data/ai/tmp/sing-fanqiehua/fanqiehua_src.mp3"),
        Path(__file__).resolve().parents[3] / "tmp" / "sing-fanqiehua-lead.wav",
        lib.cache_dir() / "fanqiehua_src.mp3",
    ]
    for p in cands:
        if p.is_file():
            return p
    return None


@router.post("/scan")
def scan_library(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    cache_n = lib.scan_directory(
        user_id=user["id"], root=lib.cache_dir(), source="cache", root_label="cache"
    )
    nas = lib.scan_nas_roots(user_id=user["id"])
    demo = _demo_fanqiehua()
    if demo is not None:
        lib.register_cache_file(user_id=user["id"], path=demo, title="番茄花")
    return {
        "cache_scanned": cache_n,
        "nas": nas,
        "tracks": lib.count_tracks(user_id=user["id"]),
        "scope": "account",
    }


@router.get("/library")
def get_library_stats(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return lib.library_stats(user_id=user["id"])


@router.get("/tracks")
def get_tracks(
    user: Dict[str, Any] = Depends(current_user),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
) -> Dict[str, Any]:
    items = lib.list_tracks(user_id=user["id"], limit=limit, offset=offset, q=q)
    total = lib.count_tracks(user_id=user["id"], q=q)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "scope": "account",
    }


def _user_from_audio_auth(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    access_token: str = Query("", alias="access_token"),
    token: str = Query("", alias="token"),
) -> Dict[str, Any]:
    """播放器可用 Header Bearer，或 URL ?access_token=（iOS AVPlayer 更稳）。"""
    raw = (creds.credentials if creds else "") or (access_token or token or "").strip()
    if not raw:
        raise HTTPException(401, "需要登录")
    payload = decode_access_token(raw)
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (payload["sub"],))
    if user is None or user["is_disabled"]:
        raise HTTPException(401, "账号不可用")
    return {"id": user["id"], "username": user["username"]}


def _audio_media_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "mpga": "audio/mpeg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
    }.get(ext, "application/octet-stream")


@router.get("/tracks/{track_id}/audio")
def stream_track(track_id: str, user: Dict[str, Any] = Depends(_user_from_audio_auth)):
    """鉴权拉曲：本地文件直出；smb:// 经 AI 主机拉到 cache 再出。"""
    row = db.fetchone(
        "SELECT * FROM music_tracks WHERE id = ? AND user_id = ?",
        (track_id, user["id"]),
    )
    if row is None:
        raise HTTPException(404, "曲目不存在")
    path = Path(str(row["path"] or ""))
    if not path.is_file() and not path.is_absolute():
        # 相对路径：相对 server 工作目录
        cand = Path.cwd() / path
        if cand.is_file():
            path = cand
    if path.is_file():
        return FileResponse(path, media_type=_audio_media_type(path), filename=path.name)
    p = str(row["path"] or "")
    if p.startswith("smb://"):
        dest = lib.cache_dir() / f"{track_id}_{Path(str(row['rel_path'] or 't.mp3')).name}"
        if not dest.is_file():
            try:
                _fetch_smb_to(p, dest)
            except Exception as exc:
                raise HTTPException(502, f"NAS 取歌失败: {exc}") from exc
        if dest.is_file():
            from ..security import iso as _iso

            db.execute(
                "UPDATE music_tracks SET path=?, source='cache', updated_at=? WHERE id=?",
                (str(dest), _iso(), track_id),
            )
            return FileResponse(dest, media_type=_audio_media_type(dest), filename=dest.name)
    raise HTTPException(404, "音频文件不可用")


def _normalize_smb_url(url: str) -> tuple[str, str, str]:
    """解析 smb://host/share/rel；兼容错误重复前缀 smb://h/s//h/s/rel。"""
    import re

    m = re.match(r"smb://([^/]+)/([^/]+)/(.*)", (url or "").strip())
    if not m:
        raise RuntimeError("bad smb url")
    nas, share, rel = m.group(1), m.group(2), m.group(3)
    # 修复扫描 bug：rel 里又嵌了 host/share
    dup = f"{nas}/{share}/"
    while rel.startswith("/") or rel.startswith(dup) or rel.startswith(f"/{dup}"):
        if rel.startswith("/"):
            rel = rel[1:]
            continue
        if rel.startswith(dup):
            rel = rel[len(dup) :]
            continue
        if rel.startswith(f"/{dup}"):
            rel = rel[len(dup) + 1 :]
            continue
        break
    rel = rel.lstrip("/")
    if not rel:
        raise RuntimeError("empty smb rel")
    return nas, share, rel


def _fetch_smb_to(url: str, dest: Path) -> None:
    """优先本机 smbprotocol；无协议库时再经 SSH 到 AI 主机拉取。"""
    import subprocess

    from ..config import get_settings

    s = get_settings()
    nas, share, rel = _normalize_smb_url(url)
    pw = (s.music_nas_password or "").strip() or "2012sylL"
    user = (s.music_nas_user or "syl").strip()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 本机直拉（生产 API 就在 AI 主机）
    try:
        from smbclient import open_file, register_session

        register_session(nas, username=user, password=pw)
        rel_win = rel.replace("/", "\\")
        src = "\\\\" + nas + "\\" + share + "\\" + rel_win
        dest.write_bytes(open_file(src, "rb").read())
        if dest.is_file() and dest.stat().st_size > 32:
            return
    except Exception:
        pass

    cfg = (s.sing_ssh_config or "").strip()
    host = (s.sing_ssh_host or "local-ai-public").strip()
    if not cfg:
        raise RuntimeError("本机拉取 NAS 失败且无 SSH 回退")
    remote = f"/data/ai/tmp/music-fetch/{dest.name}"
    script = f"""
from pathlib import Path
from smbclient import register_session, open_file
host={nas!r}; share={share!r}; rel={rel!r}.replace('/','\\\\\\\\')
register_session(host, username={user!r}, password={pw!r})
src=r'\\\\\\\\%s\\\\%s\\\\%s'%(host,share,rel)
Path({remote!r}).parent.mkdir(parents=True, exist_ok=True)
Path({remote!r}).write_bytes(open_file(src,'rb').read())
print({remote!r})
"""
    subprocess.run(
        ["ssh", "-F", cfg, host, "pip3 install -q smbprotocol 2>/dev/null; python3 -"],
        input=script,
        text=True,
        check=True,
        timeout=180,
        capture_output=True,
    )
    subprocess.run(
        ["scp", "-F", cfg, f"{host}:{remote}", str(dest)],
        check=True,
        timeout=180,
        capture_output=True,
    )


@router.get("/playlists")
def get_playlists(
    user: Dict[str, Any] = Depends(current_user),
    kind: Optional[str] = Query(None, description="library | session | network；空=全部"),
) -> List[Dict[str, Any]]:
    if kind:
        rows = db.fetchall(
            """
            SELECT id, name, kind, source_url, created_at, updated_at
            FROM music_playlists WHERE user_id = ? AND kind = ?
            ORDER BY updated_at DESC
            """,
            (user["id"], kind.strip()),
        )
    else:
        rows = db.fetchall(
            """
            SELECT id, name, kind, source_url, created_at, updated_at
            FROM music_playlists WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user["id"],),
        )
    out = []
    for r in rows:
        cnt = db.fetchone(
            "SELECT COUNT(*) AS n FROM music_playlist_items WHERE playlist_id = ?",
            (r["id"],),
        )
        out.append({**dict(r), "track_count": int(cnt["n"] if cnt else 0)})
    return out


@router.post("/playlists")
def create_playlist(body: PlaylistCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    kind = (body.kind or "library").strip().lower()
    if kind not in ("library", "network"):
        raise HTTPException(400, "kind 仅支持 library / network（session 由一起听自动生成）")
    if kind == "network" and not (body.source_url or "").strip():
        raise HTTPException(400, "网络歌单需要 source_url")
    now = iso()
    pid = new_id("mpl_")
    db.execute(
        """
        INSERT INTO music_playlists(id, user_id, name, kind, source_url, meta_json, created_at, updated_at)
        VALUES (?,?,?,?,?,'{}',?,?)
        """,
        (
            pid,
            user["id"],
            body.name.strip(),
            kind,
            (body.source_url or "").strip(),
            now,
            now,
        ),
    )
    return {
        "id": pid,
        "name": body.name.strip(),
        "kind": kind,
        "source_url": (body.source_url or "").strip(),
    }


@router.get("/playlists/{playlist_id}/items")
def list_playlist_items(
    playlist_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    pl = db.fetchone(
        "SELECT id, name, kind FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    rows = db.fetchall(
        """
        SELECT i.id AS item_id, i.position, i.created_at AS added_at,
               t.id, t.title, t.artist, t.source, t.rel_path, t.duration_sec, t.root_label
        FROM music_playlist_items i
        JOIN music_tracks t ON t.id = i.track_id
        WHERE i.playlist_id = ?
        ORDER BY i.position ASC, i.created_at ASC
        """,
        (playlist_id,),
    )
    return {
        "playlist_id": playlist_id,
        "name": pl["name"],
        "kind": pl["kind"],
        "items": [dict(r) for r in rows],
    }


@router.post("/playlists/{playlist_id}/items")
def add_playlist_item(
    playlist_id: str,
    body: PlaylistAddTrack,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    pl = db.fetchone(
        "SELECT id, kind FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    if (pl["kind"] or "") == "network":
        raise HTTPException(400, "网络歌单暂不支持手动加曲（待同步实现）")
    tr = db.fetchone(
        "SELECT id FROM music_tracks WHERE id = ? AND user_id = ?",
        (body.track_id, user["id"]),
    )
    if tr is None:
        raise HTTPException(404, "曲目不存在")
    exist = db.fetchone(
        """
        SELECT id FROM music_playlist_items
        WHERE playlist_id = ? AND track_id = ?
        """,
        (playlist_id, body.track_id),
    )
    if exist is not None:
        return {"id": exist["id"], "position": None, "deduped": True}
    pos = body.position
    if pos is None:
        row = db.fetchone(
            "SELECT COALESCE(MAX(position), -1) AS m FROM music_playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        )
        pos = int(row["m"] if row else -1) + 1
    iid = new_id("mpli_")
    db.execute(
        """
        INSERT INTO music_playlist_items(id, playlist_id, track_id, position, created_at)
        VALUES (?,?,?,?,?)
        """,
        (iid, playlist_id, body.track_id, pos, iso()),
    )
    db.execute(
        "UPDATE music_playlists SET updated_at = ? WHERE id = ?",
        (iso(), playlist_id),
    )
    return {"id": iid, "position": pos, "deduped": False}


@router.delete("/playlists/{playlist_id}/items/{item_id}")
def remove_playlist_item(
    playlist_id: str,
    item_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    pl = db.fetchone(
        "SELECT id FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    row = db.fetchone(
        "SELECT id FROM music_playlist_items WHERE id = ? AND playlist_id = ?",
        (item_id, playlist_id),
    )
    if row is None:
        raise HTTPException(404, "歌单项不存在")
    db.execute("DELETE FROM music_playlist_items WHERE id = ?", (item_id,))
    db.execute(
        "UPDATE music_playlists SET updated_at = ? WHERE id = ?",
        (iso(), playlist_id),
    )
    return {"ok": True}


@router.delete("/playlists/{playlist_id}/tracks/{track_id}")
def remove_track_from_playlist(
    playlist_id: str,
    track_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    """按曲目 ID 从歌单移除（可一次删多条同曲）。"""
    pl = db.fetchone(
        "SELECT id FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    before = db.fetchone(
        """
        SELECT COUNT(*) AS n FROM music_playlist_items
        WHERE playlist_id = ? AND track_id = ?
        """,
        (playlist_id, track_id),
    )
    n = int(before["n"] if before else 0)
    if n <= 0:
        raise HTTPException(404, "歌单中无此曲")
    db.execute(
        "DELETE FROM music_playlist_items WHERE playlist_id = ? AND track_id = ?",
        (playlist_id, track_id),
    )
    db.execute(
        "UPDATE music_playlists SET updated_at = ? WHERE id = ?",
        (iso(), playlist_id),
    )
    return {"ok": True, "removed": n}


@router.post("/playlists/{playlist_id}/sync")
def sync_network_playlist(
    playlist_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """网络歌单：拉取 source_url（m3u / 每行歌名），按标题匹配账号曲库并写入 items。"""
    import httpx

    pl = db.fetchone(
        "SELECT * FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    if (pl["kind"] or "") != "network":
        raise HTTPException(400, "仅 network 歌单可 sync")
    url = (pl["source_url"] or "").strip()
    if not url:
        raise HTTPException(400, "缺少 source_url")
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(url)
        if r.status_code >= 400:
            raise HTTPException(502, f"拉取歌单失败 HTTP {r.status_code}")
        text = r.text or ""
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"拉取歌单失败: {exc}") from exc

    titles: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # m3u 可能是 URL：取文件名
        if "://" in line:
            name = line.rstrip("/").split("/")[-1]
            name = name.rsplit(".", 1)[0]
            titles.append(name[:80])
        else:
            titles.append(line[:80])
        if len(titles) >= 200:
            break

    db.execute("DELETE FROM music_playlist_items WHERE playlist_id = ?", (playlist_id,))
    now = iso()
    matched = 0
    missing: List[str] = []
    for i, title in enumerate(titles):
        hit = lib.resolve_track_by_prompt(user_id=user["id"], prompt=title) if hasattr(lib, "resolve_track_by_prompt") else None
        if not hit:
            # 模糊：LIKE
            row = db.fetchone(
                """
                SELECT id FROM music_tracks
                WHERE user_id = ? AND (title LIKE ? OR artist LIKE ?)
                LIMIT 1
                """,
                (user["id"], f"%{title[:20]}%", f"%{title[:20]}%"),
            )
            hit = dict(row) if row else None
        if not hit:
            missing.append(title)
            continue
        tid = hit.get("id") if isinstance(hit, dict) else hit["id"]
        db.execute(
            """
            INSERT INTO music_playlist_items(id, playlist_id, track_id, position, created_at)
            VALUES (?,?,?,?,?)
            """,
            (new_id("mpli_"), playlist_id, tid, matched, now),
        )
        matched += 1
    db.execute(
        "UPDATE music_playlists SET updated_at=?, meta_json=? WHERE id=?",
        (
            now,
            __import__("json").dumps(
                {"synced_at": now, "source_titles": len(titles), "missing": missing[:20]},
                ensure_ascii=False,
            ),
            playlist_id,
        ),
    )
    return {
        "ok": True,
        "matched": matched,
        "source_titles": len(titles),
        "missing": missing[:30],
    }


@router.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    pl = db.fetchone(
        "SELECT id, kind FROM music_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user["id"]),
    )
    if pl is None:
        raise HTTPException(404, "歌单不存在")
    if (pl["kind"] or "") == "session":
        raise HTTPException(400, "会话临时歌单请结束一起听后自动清理")
    db.execute("DELETE FROM music_playlist_items WHERE playlist_id = ?", (playlist_id,))
    db.execute("DELETE FROM music_playlists WHERE id = ?", (playlist_id,))
    return {"ok": True}


@router.delete("/tracks/{track_id}")
def delete_track(track_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    """从账号曲库删除曲目（同时从所有歌单移除引用；不删磁盘文件）。"""
    row = db.fetchone(
        "SELECT id FROM music_tracks WHERE id = ? AND user_id = ?",
        (track_id, user["id"]),
    )
    if row is None:
        raise HTTPException(404, "曲目不存在")
    db.execute("DELETE FROM music_playlist_items WHERE track_id = ?", (track_id,))
    db.execute("DELETE FROM music_tracks WHERE id = ?", (track_id,))
    return {"ok": True}
