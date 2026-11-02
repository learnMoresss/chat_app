"""人设唱歌声线：固定音色后异步建「声线包」，训好(ready)才能翻唱。

当前落地：
- pending → 收集固定音色 + 同批试听样本，拼成 voice_pack.wav，状态 ready
- 后续可换成 RVC 微调 / Seed-VC finetune / ReSinger，不改门禁语义
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .security import iso

logger = logging.getLogger("chat.voice_line")

READY = "ready"
PENDING = "pending"
FAILED = "failed"
# 历史：固定音色但未建声线包
LEGACY_FIXED = "fixed"


def voices_root() -> Path:
    return Path("/data/ai/voices")


def persona_voice_dir(*, user_id: str, persona_id: str) -> Path:
    return voices_root() / user_id / persona_id


def get_voice_profile(*, user_id: str, persona_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        """
        SELECT * FROM voice_profiles
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    return dict(row) if row else None


def sing_voice_ready(*, user_id: str, persona_id: str) -> bool:
    """唱歌门禁：必须有 ready 声线（或兼容旧 fixed 且已有 pack）。"""
    vp = get_voice_profile(user_id=user_id, persona_id=persona_id)
    if not vp:
        return False
    status = str(vp.get("embed_status") or "").strip().lower()
    if status == READY:
        return True
    # 过渡：旧 fixed 若已有 pack 文件也放行一次
    if status == LEGACY_FIXED:
        pack = persona_voice_dir(user_id=user_id, persona_id=persona_id) / "voice_pack.wav"
        return pack.is_file() and pack.stat().st_size > 1000
    return False


def resolve_sing_ref(*, user_id: str, persona_id: str) -> Optional[Path]:
    """翻唱参考音：优先声线包，其次 voice_profiles.path。"""
    pack = persona_voice_dir(user_id=user_id, persona_id=persona_id) / "voice_pack.wav"
    if pack.is_file() and pack.stat().st_size > 1000:
        return pack
    vp = get_voice_profile(user_id=user_id, persona_id=persona_id)
    if not vp:
        return None
    p = Path(str(vp.get("path") or ""))
    if p.is_file():
        return p
    return None


def _media_path(user_id: str, file_id: str) -> Optional[Path]:
    row = db.fetchone(
        "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if not row:
        return None
    p = Path(str(row["path"] or ""))
    return p if p.is_file() else None


def _collect_sample_paths(*, user_id: str, persona_id: str, asset_id: str) -> List[Path]:
    """固定资产 + 同 batch 其它试听 wav。"""
    row = db.fetchone(
        """
        SELECT * FROM persona_assets
        WHERE id = ? AND user_id = ? AND persona_id = ? AND kind = 'voice'
        """,
        (asset_id, user_id, persona_id),
    )
    if not row:
        return []
    paths: List[Path] = []
    fid = str(row["file_id"] or "")
    if fid:
        p = _media_path(user_id, fid)
        if p:
            paths.append(p)
    batch = ""
    try:
        meta = json.loads(row["meta_json"] or "{}")
        if isinstance(meta, dict):
            batch = str(meta.get("batch") or "").strip()
    except Exception:
        batch = ""
    if batch:
        sibs = db.fetchall(
            """
            SELECT file_id, meta_json FROM persona_assets
            WHERE user_id = ? AND persona_id = ? AND kind = 'voice'
            ORDER BY created_at DESC LIMIT 20
            """,
            (user_id, persona_id),
        )
        for s in sibs:
            try:
                m = json.loads(s["meta_json"] or "{}")
            except Exception:
                continue
            if not isinstance(m, dict) or str(m.get("batch") or "") != batch:
                continue
            sfid = str(s["file_id"] or "")
            if not sfid or sfid == fid:
                continue
            sp = _media_path(user_id, sfid)
            if sp and sp not in paths:
                paths.append(sp)
            if len(paths) >= 6:
                break
    return paths


def _concat_wavs(parts: List[Path], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        # 统一成 wav
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(parts[0]), "-ar", "44100", "-ac", "1", str(dest),
            ],
            check=True,
        )
        return
    cmd: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in parts:
        cmd += ["-i", str(p)]
    n = len(parts)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    cmd += ["-filter_complex", filt, "-map", "[a]", "-ar", "44100", "-ac", "1", str(dest)]
    subprocess.run(cmd, check=True)


def _set_status(*, user_id: str, persona_id: str, status: str, note: str = "") -> None:
    db.execute(
        """
        UPDATE voice_profiles
        SET embed_status = ?, embed_note = ?
        WHERE user_id = ? AND persona_id = ?
          AND id = (
            SELECT id FROM voice_profiles
            WHERE user_id = ? AND persona_id = ?
            ORDER BY created_at DESC LIMIT 1
          )
        """,
        (status, note[:500], user_id, persona_id, user_id, persona_id),
    )


def train_voice_line_sync(*, user_id: str, persona_id: str, asset_id: str) -> Dict[str, Any]:
    """同步建声线包（供线程调用）。"""
    _set_status(user_id=user_id, persona_id=persona_id, status=PENDING, note=f"training:{asset_id}")
    parts = _collect_sample_paths(user_id=user_id, persona_id=persona_id, asset_id=asset_id)
    if not parts:
        _set_status(user_id=user_id, persona_id=persona_id, status=FAILED, note="no_samples")
        return {"ok": False, "error": "no_samples"}
    out_dir = persona_voice_dir(user_id=user_id, persona_id=persona_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pack = out_dir / "voice_pack.wav"
    try:
        _concat_wavs(parts, pack)
    except Exception as exc:
        logger.exception("voice pack concat failed")
        _set_status(user_id=user_id, persona_id=persona_id, status=FAILED, note=str(exc)[:200])
        return {"ok": False, "error": str(exc)}
    meta = {
        "asset_id": asset_id,
        "parts": [str(p) for p in parts],
        "pack": str(pack),
        "bytes": pack.stat().st_size if pack.is_file() else 0,
        "engine": "voice_pack_v1",
        "updated_at": iso(),
    }
    (out_dir / "voice_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _set_status(
        user_id=user_id,
        persona_id=persona_id,
        status=READY,
        note=f"persona_asset:{asset_id}|pack:{pack.name}",
    )
    logger.info(
        "voice_line ready user=%s persona=%s parts=%s pack=%s",
        user_id,
        persona_id,
        len(parts),
        pack,
    )
    return {"ok": True, **meta}


def start_voice_line_train(*, user_id: str, persona_id: str, asset_id: str) -> None:
    """固定音色后异步开训。"""
    _set_status(user_id=user_id, persona_id=persona_id, status=PENDING, note=f"queued:{asset_id}")

    def _run() -> None:
        try:
            train_voice_line_sync(user_id=user_id, persona_id=persona_id, asset_id=asset_id)
        except Exception:
            logger.exception("voice_line train crashed")
            try:
                _set_status(user_id=user_id, persona_id=persona_id, status=FAILED, note="train_crash")
            except Exception:
                pass

    threading.Thread(target=_run, name=f"voice-line-{persona_id[-6:]}", daemon=True).start()
