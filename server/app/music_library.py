"""曲库：外置 NAS 根扫描 + 本地 cache 登记 + 标题解析。"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .config import get_settings
from .security import iso, new_id

log = logging.getLogger("music_library")

AUDIO_EXTS_DEFAULT = {".mp3", ".flac", ".m4a", ".wav", ".aac", ".ogg"}


def cache_dir() -> Path:
    s = get_settings()
    raw = (s.music_cache_dir or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = Path(s.data_dir) / "music_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _exts() -> set:
    s = get_settings()
    parts = [f".{x.strip().lstrip('.').lower()}" for x in (s.music_audio_exts or "").split(",") if x.strip()]
    return set(parts) if parts else set(AUDIO_EXTS_DEFAULT)


def _duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            text=True,
            timeout=60,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def _title_from_name(name: str) -> Tuple[str, str]:
    stem = Path(name).stem
    stem = re.sub(r"^\d+[\s._-]+", "", stem)
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return t.strip() or stem, a.strip()
    if "-" in stem and stem.count("-") == 1:
        a, t = stem.split("-", 1)
        if len(a) < 40 and len(t) > 0:
            return t.strip(), a.strip()
    return stem.strip(), ""


def ensure_nas_mount() -> Optional[Path]:
    """尝试挂载飞牛 SMB 到 MUSIC_NAS_MOUNT；失败返回已存在挂载或 None。"""
    s = get_settings()
    raw_mount = (s.music_nas_mount or "").strip()
    mount = Path(raw_mount) if raw_mount else Path.home() / "mnt" / "fnos-music"
    host = (s.music_nas_smb_host or "").strip()
    user = (s.music_nas_user or "").strip()
    password = (s.music_nas_password or "").strip()
    try:
        if mount.is_dir() and any(mount.iterdir()):
            return mount
    except Exception:
        pass
    if not host or not user:
        log.warning("NAS host/user 未配置")
        return mount if mount.is_dir() else None
    candidates = []
    if password:
        candidates.append(password)
    if not password:
        candidates.extend(["2012syl", "2012syll", "2012sylL"])
    shares = ["我的文件", "存储空间1", "vol1", "home"]
    try:
        mount.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.warning("cannot create mount dir %s: %s", mount, exc)
        return None
    # Linux 无 mount_smbfs：跳过挂载，交给 smbprotocol 直扫
    mount_bin = shutil.which("mount_smbfs") or shutil.which("mount.cifs")
    if not mount_bin:
        log.info("无 mount_smbfs/mount.cifs，改用 smbprotocol 扫 NAS")
        return None
    for share in shares:
        for pw in candidates:
            try:
                subprocess.run(["umount", str(mount)], capture_output=True, timeout=10)
            except Exception:
                pass
            if mount_bin.endswith("mount_smbfs"):
                url = f"//{user}:{pw}@{host}/{share}"
                cmd = [mount_bin, url, str(mount)]
            else:
                # mount.cifs //host/share mount -o user=,password=
                cmd = [
                    "sudo",
                    "-n",
                    mount_bin,
                    f"//{host}/{share}",
                    str(mount),
                    "-o",
                    f"username={user},password={pw},iocharset=utf8",
                ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except FileNotFoundError as exc:
                log.warning("mount tool missing: %s", exc)
                return None
            if r.returncode == 0 and mount.is_dir():
                os.environ["MUSIC_NAS_PASSWORD"] = pw
                log.info("NAS mounted share=%s at %s", share, mount)
                return mount
            log.debug("mount fail share=%s: %s", share, (r.stderr or r.stdout or "")[:200])
    try:
        if mount.is_dir() and any(mount.iterdir()):
            return mount
    except Exception:
        pass
    return None


def _upsert_track(
    *,
    user_id: str,
    source: str,
    path: Path,
    root_label: str = "",
    rel_path: str = "",
) -> str:
    title, artist = _title_from_name(path.name)
    now = iso()
    existing = db.fetchone(
        "SELECT id FROM music_tracks WHERE user_id = ? AND path = ?",
        (user_id, str(path)),
    )
    dur = _duration(path)
    if existing:
        db.execute(
            """
            UPDATE music_tracks
            SET title = ?, artist = ?, duration_sec = ?, root_label = ?, rel_path = ?,
                source = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, artist, dur, root_label, rel_path, source, now, existing["id"]),
        )
        return str(existing["id"])
    tid = new_id("mt_")
    db.execute(
        """
        INSERT INTO music_tracks(
          id, user_id, source, title, artist, path, rel_path, duration_sec,
          root_label, meta_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,'{}',?,?)
        """,
        (tid, user_id, source, title, artist, str(path), rel_path, dur, root_label, now, now),
    )
    return tid


def scan_directory(*, user_id: str, root: Path, source: str, root_label: str = "") -> int:
    if not root.is_dir():
        return 0
    exts = _exts()
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in exts:
                continue
            try:
                rel = str(p.relative_to(root))
            except Exception:
                rel = fn
            _upsert_track(
                user_id=user_id,
                source=source,
                path=p,
                root_label=root_label or root.name,
                rel_path=rel,
            )
            n += 1
    return n


def _scan_nas_via_ai_host(*, user_id: str, roots: List[str]) -> Dict[str, Any]:
    """本机到不了 NAS 时，经 AI 主机 smbprotocol 列目录并登记 smb:// 路径。"""
    s = get_settings()
    cfg = (getattr(s, "sing_ssh_config", None) or "").strip()
    host = (getattr(s, "sing_ssh_host", None) or "local-ai-public").strip()
    if not cfg:
        return {"error": "无 SSH 配置，无法远程扫 NAS", "total": 0, "roots": {}}
    pw_candidates = []
    if (s.music_nas_password or "").strip():
        pw_candidates.append((s.music_nas_password or "").strip())
    pw_candidates.extend(["2012syl", "2012syll", "2012sylL"])
    nas_host = (s.music_nas_smb_host or "192.168.1.9").strip()
    user = (s.music_nas_user or "syl").strip()
    last_err = ""
    data: Dict[str, Any] = {}
    for pw in pw_candidates:
        script = textwrap.dedent(
            f"""
            import json
            try:
                from smbclient import register_session, listdir, walk
            except Exception as e:
                print(json.dumps({{"error":"no_smbprotocol:"+str(e)}})); raise SystemExit(0)
            host={nas_host!r}; user={user!r}; pw={pw!r}; roots={roots!r}
            exts={{'.mp3','.flac','.m4a','.wav','.aac','.ogg'}}
            out={{"shares":{{}},"files":[],"pw_ok":False}}
            # 飞牛：根目录本身就是「音乐」「新音乐」共享
            shares = list(dict.fromkeys(list(roots) + ['音乐','新音乐','我的文件','存储空间1','vol1','home','Public']))
            try:
                register_session(host, username=user, password=pw)
                out['pw_ok']=True
            except Exception as e:
                out['errors']=[f'auth:{{e}}']
                print(json.dumps(out, ensure_ascii=False)); raise SystemExit(0)
            for share in shares:
                try:
                    root=r'\\\\%s\\%s'%(host,share)
                    names=list(listdir(root))
                    out['shares'][share]=names[:40]
                    root_norm=root.rstrip('\\\\')
                    for dirpath, dirs, files in walk(root):
                        for f in files:
                            low=f.lower()
                            if not any(low.endswith(e) for e in exts):
                                continue
                            dp=(dirpath or '').rstrip('\\\\')
                            if dp==root_norm:
                                rel_dir=''
                            elif dp.startswith(root_norm+'\\\\'):
                                rel_dir=dp[len(root_norm)+1:]
                            else:
                                rel_dir=dp
                            rel=(rel_dir+'\\\\'+f if rel_dir else f).replace('\\\\','/').lstrip('/')
                            out['files'].append({{'share':share,'rel':rel,'name':f,'root':share}})
                except Exception as e:
                    out.setdefault('errors',[]).append(f"{{share}}:{{e}}")
            print(json.dumps(out, ensure_ascii=False))
            """
        )
        put = subprocess.run(
            ["ssh", "-F", cfg, host, "cat > /data/ai/tmp/scan_nas.py"],
            input=script,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if put.returncode != 0:
            last_err = f"upload failed: {put.stderr[:200]}"
            continue
        run = subprocess.run(
            [
                "ssh",
                "-F",
                cfg,
                host,
                "pip3 install -q smbprotocol 2>/dev/null; python3 /data/ai/tmp/scan_nas.py",
            ],
            capture_output=True,
            text=True,
            timeout=240,
        )
        try:
            data = json.loads((run.stdout or "").strip().splitlines()[-1])
        except Exception as exc:
            last_err = f"bad json: {exc}; {(run.stderr or '')[:160]}"
            continue
        if data.get("pw_ok"):
            # persist working password for this process
            os.environ["MUSIC_NAS_PASSWORD"] = pw
            break
        last_err = str(data.get("error") or data.get("errors") or "auth/share fail")
        data = {}
    if not data.get("pw_ok"):
        return {"error": last_err or "NAS 远程扫描失败", "total": 0, "roots": {}}
    total = 0
    roots_report: Dict[str, Any] = {}
    for f in data.get("files") or []:
        name = f.get("name") or "track"
        root_label = f.get("root") or "nas"
        virtual = f"smb://{nas_host}/{f.get('share')}/{f.get('rel')}"
        title, artist = _title_from_name(name)
        now = iso()
        existing = db.fetchone(
            "SELECT id FROM music_tracks WHERE user_id = ? AND path = ?",
            (user_id, virtual),
        )
        if existing:
            db.execute(
                "UPDATE music_tracks SET title=?, artist=?, root_label=?, source='nas', updated_at=? WHERE id=?",
                (title, artist, root_label, now, existing["id"]),
            )
        else:
            tid = new_id("mt_")
            db.execute(
                """
                INSERT INTO music_tracks(
                  id, user_id, source, title, artist, path, rel_path, duration_sec,
                  root_label, meta_json, created_at, updated_at
                ) VALUES (?,?, 'nas', ?,?,?,?,0,?,?,?,?)
                """,
                (
                    tid,
                    user_id,
                    title,
                    artist,
                    virtual,
                    f.get("rel") or name,
                    root_label,
                    json.dumps({"share": f.get("share")}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        total += 1
        roots_report.setdefault(root_label, {"ok": True, "count": 0})
        roots_report[root_label]["count"] = roots_report[root_label].get("count", 0) + 1
    return {"via": "ai_host_smb", "shares": data.get("shares"), "roots": roots_report, "total": total}


def _scan_nas_smbprotocol_local(*, user_id: str, roots: List[str]) -> Dict[str, Any]:
    """在 AI 主机本机用 smbprotocol 直扫（无需再 SSH）。"""
    try:
        from smbclient import listdir, register_session, walk
    except Exception as exc:
        return {"error": f"no_smbprotocol:{exc}", "total": 0, "roots": {}}
    s = get_settings()
    nas_host = (s.music_nas_smb_host or "192.168.1.9").strip()
    user = (s.music_nas_user or "syl").strip()
    pw_candidates: List[str] = []
    if (s.music_nas_password or "").strip():
        pw_candidates.append((s.music_nas_password or "").strip())
    pw_candidates.extend(["2012syl", "2012syll", "2012sylL"])
    last_err = ""
    pw_ok = ""
    for pw in pw_candidates:
        try:
            register_session(nas_host, username=user, password=pw)
            pw_ok = pw
            break
        except Exception as exc:
            last_err = str(exc)
    if not pw_ok:
        return {"error": last_err or "NAS 认证失败", "total": 0, "roots": {}}
    os.environ["MUSIC_NAS_PASSWORD"] = pw_ok
    exts = _exts()
    shares = list(dict.fromkeys(list(roots) + ["音乐", "新音乐", "我的文件", "存储空间1", "vol1", "home", "Public"]))
    files: List[Dict[str, str]] = []
    share_names: Dict[str, Any] = {}
    errors: List[str] = []
    for share in shares:
        try:
            root = rf"\\{nas_host}\{share}"
            names = list(listdir(root))
            share_names[share] = names[:40]
            root_norm = root.rstrip("\\")
            for dirpath, _dirs, fnames in walk(root):
                for f in fnames:
                    low = f.lower()
                    if not any(low.endswith(e) for e in exts):
                        continue
                    dp = (dirpath or "").rstrip("\\")
                    if dp == root_norm:
                        rel_dir = ""
                    elif dp.startswith(root_norm + "\\"):
                        rel_dir = dp[len(root_norm) + 1 :]
                    else:
                        # 兜底：去掉 UNC 前缀，避免把 \\host\share 写进 rel
                        rel_dir = dp
                        for prefix in (root_norm + "\\", root + "\\", root):
                            if rel_dir.startswith(prefix):
                                rel_dir = rel_dir[len(prefix) :]
                                break
                    rel = f"{rel_dir}/{f}" if rel_dir else f
                    rel = rel.replace("\\", "/").lstrip("/")
                    files.append({"share": share, "rel": rel, "name": f, "root": share})
        except Exception as exc:
            errors.append(f"{share}:{exc}")
    total = 0
    roots_report: Dict[str, Any] = {}
    for f in files:
        name = f.get("name") or "track"
        root_label = f.get("root") or "nas"
        virtual = f"smb://{nas_host}/{f.get('share')}/{f.get('rel')}"
        title, artist = _title_from_name(name)
        now = iso()
        existing = db.fetchone(
            "SELECT id FROM music_tracks WHERE user_id = ? AND path = ?",
            (user_id, virtual),
        )
        if existing:
            db.execute(
                "UPDATE music_tracks SET title=?, artist=?, root_label=?, source='nas', updated_at=? WHERE id=?",
                (title, artist, root_label, now, existing["id"]),
            )
        else:
            tid = new_id("mt_")
            db.execute(
                """
                INSERT INTO music_tracks(
                  id, user_id, source, title, artist, path, rel_path, duration_sec,
                  root_label, meta_json, created_at, updated_at
                ) VALUES (?,?, 'nas', ?,?,?,?,0,?,?,?,?)
                """,
                (
                    tid,
                    user_id,
                    title,
                    artist,
                    virtual,
                    f.get("rel") or name,
                    root_label,
                    json.dumps({"share": f.get("share")}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        total += 1
        roots_report.setdefault(root_label, {"ok": True, "count": 0})
        roots_report[root_label]["count"] = roots_report[root_label].get("count", 0) + 1
    out: Dict[str, Any] = {
        "via": "local_smbprotocol",
        "shares": share_names,
        "roots": roots_report,
        "total": total,
    }
    if errors:
        out["errors"] = errors[:20]
    return out


def scan_nas_roots(*, user_id: str) -> Dict[str, Any]:
    s = get_settings()
    mount = ensure_nas_mount()
    roots = [x.strip() for x in (s.music_nas_roots or "音乐,新音乐").split(",") if x.strip()]
    report: Dict[str, Any] = {"mount": str(mount) if mount else "", "roots": {}, "total": 0}
    if mount:
        for name in roots:
            candidates = [
                mount / name,
                mount / "我的文件" / name,
                mount / "存储空间1" / name,
            ]
            hit = next((c for c in candidates if c.is_dir()), None)
            if hit is None:
                report["roots"][name] = {"ok": False, "count": 0, "path": ""}
                continue
            cnt = scan_directory(user_id=user_id, root=hit, source="nas", root_label=name)
            report["roots"][name] = {"ok": True, "count": cnt, "path": str(hit)}
            report["total"] += cnt
        if report["total"] > 0:
            return report
    # 优先本机 smbprotocol（AI 主机局域网）
    local = _scan_nas_smbprotocol_local(user_id=user_id, roots=roots)
    if int(local.get("total") or 0) > 0 or not local.get("error"):
        report["remote"] = local
        report["total"] = int(local.get("total") or 0)
        report["roots"] = local.get("roots") or report["roots"]
        if local.get("error") and report["total"] <= 0:
            report["error"] = local["error"]
        if report["total"] > 0:
            return report
    # 本机挂不上时再经 SSH 扫（Mac 开发机场景）
    remote = _scan_nas_via_ai_host(user_id=user_id, roots=roots)
    report["remote"] = remote
    report["total"] = int(remote.get("total") or 0)
    report["roots"] = remote.get("roots") or report["roots"]
    if remote.get("error"):
        report["error"] = remote["error"]
    return report


def register_cache_file(*, user_id: str, path: Path, title: str = "") -> str:
    path = path.resolve()
    tid = _upsert_track(user_id=user_id, source="cache", path=path, root_label="cache", rel_path=path.name)
    if title.strip():
        db.execute(
            "UPDATE music_tracks SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip(), iso(), tid),
        )
    return tid


def resolve_track_by_prompt(*, user_id: str, prompt: str) -> Optional[Dict[str, Any]]:
    """歌单名精确 → 曲目标题包含 → None。"""
    q = re.sub(r"\s+", "", (prompt or "").strip())
    if not q:
        return None
    # 歌单：若 playlist 名命中，取第一首
    pl = db.fetchone(
        "SELECT id, name FROM music_playlists WHERE user_id = ? AND replace(name, ' ', '') = ?",
        (user_id, q),
    )
    if pl is None:
        # 模糊歌单名
        rows = db.fetchall(
            "SELECT id, name FROM music_playlists WHERE user_id = ?",
            (user_id,),
        )
        for r in rows:
            if q in re.sub(r"\s+", "", r["name"] or ""):
                pl = r
                break
    if pl is not None:
        item = db.fetchone(
            """
            SELECT t.* FROM music_playlist_items i
            JOIN music_tracks t ON t.id = i.track_id
            WHERE i.playlist_id = ?
            ORDER BY i.position ASC LIMIT 1
            """,
            (pl["id"],),
        )
        if item is not None:
            return dict(item)

    tracks = db.fetchall(
        "SELECT * FROM music_tracks WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    )
    best = None
    for t in tracks:
        title = re.sub(r"\s+", "", (t["title"] or ""))
        if not title:
            continue
        if q == title or q in title or title in q:
            best = dict(t)
            if q == title:
                return best
    return best


def count_tracks(*, user_id: str, q: str = "") -> int:
    """账号级曲库总数（跨人设共用）。"""
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        row = db.fetchone(
            """
            SELECT COUNT(*) AS n FROM music_tracks
            WHERE user_id = ? AND (title LIKE ? OR artist LIKE ? OR rel_path LIKE ?)
            """,
            (user_id, like, like, like),
        )
    else:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM music_tracks WHERE user_id = ?",
            (user_id,),
        )
    return int(row["n"] if row else 0)


def list_tracks(
    *,
    user_id: str,
    limit: int = 200,
    offset: int = 0,
    q: str = "",
) -> List[Dict[str, Any]]:
    """账号级曲库列表；与 persona 无关。"""
    limit = max(1, min(int(limit or 200), 2000))
    offset = max(0, int(offset or 0))
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        rows = db.fetchall(
            """
            SELECT id, source, title, artist, path, rel_path, duration_sec, root_label, updated_at
            FROM music_tracks
            WHERE user_id = ? AND (title LIKE ? OR artist LIKE ? OR rel_path LIKE ?)
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, like, like, like, limit, offset),
        )
    else:
        rows = db.fetchall(
            """
            SELECT id, source, title, artist, path, rel_path, duration_sec, root_label, updated_at
            FROM music_tracks WHERE user_id = ?
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
    return [dict(r) for r in rows]


def library_stats(*, user_id: str) -> Dict[str, Any]:
    """后台总览：总量 + 按 source / root 分布。"""
    total = count_tracks(user_id=user_id)
    by_source = db.fetchall(
        """
        SELECT source, COUNT(*) AS n FROM music_tracks
        WHERE user_id = ? GROUP BY source ORDER BY n DESC
        """,
        (user_id,),
    )
    by_root = db.fetchall(
        """
        SELECT root_label, COUNT(*) AS n FROM music_tracks
        WHERE user_id = ? GROUP BY root_label ORDER BY n DESC LIMIT 20
        """,
        (user_id,),
    )
    return {
        "total": total,
        "scope": "account",
        "note": "曲库按账号共享，所有人设一起听/点歌都从这里抽；会话只挂临时歌单。",
        "by_source": [{"source": r["source"], "count": int(r["n"])} for r in by_source],
        "by_root": [{"root": r["root_label"], "count": int(r["n"])} for r in by_root],
    }
