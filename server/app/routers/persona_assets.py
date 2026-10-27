"""人设抽卡：语音（TTS）/ 形象（QwenImage 2.1）；支持多抽与固定。"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..draw_diversity import image_variant, voice_variant
from ..image_gen import generate_image_bytes
from ..media_cards import save_image_bytes
from ..security import current_user, iso, new_id
from ..tts_gen import synthesize_voice_wav

router = APIRouter(prefix="/api/v1/personas", tags=["persona-assets"])


class DrawBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    count: int = Field(default=3, ge=1, le=6)
    sample_text: str = ""
    # 客户端多抽逐条请求时传入，保证同批走不同变体轴
    batch_id: str = ""
    slot_index: int = Field(default=0, ge=0, le=32)
    # 默认参考人设（名字/简介/性别），形象抽「长什么样」
    ref_persona: bool = True


def _batch_salt(batch_id: str) -> int:
    bid = (batch_id or "").strip()
    if not bid:
        return random.randint(0, 10_000)
    return int(hashlib.md5(bid.encode("utf-8")).hexdigest()[:6], 16) % 10_000


def _persona_row(user_id: str, persona_id: str):
    row = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="人设不存在")
    return row


def _resolve_file_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_file():
        return path
    settings = get_settings()
    candidates = [settings.data_dir.parent / raw, settings.data_dir / raw, Path.cwd() / raw]
    parts = path.parts
    if "files" in parts:
        i = parts.index("files")
        candidates.insert(0, settings.data_dir.joinpath(*parts[i:]))
    if path.name:
        root = settings.data_dir / "files"
        if root.is_dir():
            for user_dir in root.iterdir():
                if user_dir.is_dir():
                    candidates.append(user_dir / path.name)
    for c in candidates:
        if c.is_file():
            return c
    return path


def _load_ref_image_bytes(user_id: str, persona_id: str) -> Tuple[Optional[bytes], str]:
    """优先固定形象，其次人设头像。返回 (bytes, file_id)。"""
    fixed = db.fetchone(
        """
        SELECT file_id FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'image' AND fixed = 1
        LIMIT 1
        """,
        (user_id, persona_id),
    )
    persona = db.fetchone(
        "SELECT avatar_file_id FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    candidates = []
    if fixed and (fixed["file_id"] or ""):
        candidates.append(fixed["file_id"])
    if persona and (persona["avatar_file_id"] or ""):
        candidates.append(persona["avatar_file_id"])
    # 再退：最近一张成功形象
    recent = db.fetchone(
        """
        SELECT file_id FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'image' AND file_id != ''
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if recent and (recent["file_id"] or ""):
        candidates.append(recent["file_id"])

    seen = set()
    for fid in candidates:
        if not fid or fid in seen:
            continue
        seen.add(fid)
        media = db.fetchone(
            "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
            (fid, user_id),
        )
        if not media:
            continue
        path = _resolve_file_path(media["path"] or "")
        if path.is_file():
            data = path.read_bytes()
            if data and len(data) > 100:
                return data, fid
    return None, ""


class AssetOut(BaseModel):
    id: str
    persona_id: str
    kind: str
    file_id: str
    prompt: str
    sample_text: str = ""
    fixed: bool
    created_at: str
    meta: Dict[str, Any] = Field(default_factory=dict)


def _owned_persona(user_id: str, persona_id: str) -> None:
    _persona_row(user_id, persona_id)


def _enrich_meta(meta: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """补齐列表展示用的特色描述。"""
    if kind != "voice":
        return meta
    if (meta.get("variant_desc") or "").strip():
        return meta
    label = (meta.get("variant") or "").strip()
    extra = (meta.get("variant_extra") or "").strip()
    if label and extra:
        meta = {**meta, "variant_desc": f"{label}：{extra.rstrip('。')}"}
    elif label:
        # 旧数据只有 label：用内置表补一句
        from ..draw_diversity import _VOICE_VARIANTS

        for v in _VOICE_VARIANTS:
            if v["label"] == label:
                meta = {
                    **meta,
                    "variant_extra": v["extra"],
                    "variant_desc": f"{label}：{v['extra'].rstrip('。')}",
                }
                break
    return meta


def _row_out(r) -> AssetOut:
    meta: Dict[str, Any] = {}
    try:
        meta = json.loads(r["meta_json"] or "{}")
    except Exception:
        meta = {}
    meta = _enrich_meta(meta, (r["kind"] or "").lower())
    return AssetOut(
        id=r["id"],
        persona_id=r["persona_id"],
        kind=r["kind"],
        file_id=r["file_id"] or "",
        prompt=r["prompt"] or "",
        sample_text=r["sample_text"] or "",
        fixed=bool(r["fixed"]),
        created_at=r["created_at"],
        meta=meta,
    )


def _save_audio_bytes(*, user_id: str, data: bytes, source: str = "persona_voice") -> Optional[str]:
    if not data:
        return None
    from ..media_store import put_bytes

    fid, _ = put_bytes(
        user_id=user_id,
        data=data,
        content_type="audio/wav",
        source=source,
        filename="wav.wav",
    )
    return fid


@router.get("/{persona_id}/assets", response_model=List[AssetOut])
def list_assets(
    persona_id: str,
    kind: str = "",
    user: Dict[str, Any] = Depends(current_user),
) -> List[AssetOut]:
    _owned_persona(user["id"], persona_id)
    sql = """
      SELECT * FROM persona_assets
      WHERE user_id = ? AND persona_id = ?
    """
    params: List[Any] = [user["id"], persona_id]
    k = (kind or "").strip().lower()
    if k in ("voice", "image"):
        sql += " AND kind = ?"
        params.append(k)
    sql += " ORDER BY fixed DESC, created_at DESC LIMIT 80"
    rows = db.fetchall(sql, params)
    return [_row_out(r) for r in rows]


@router.post("/{persona_id}/assets/draw/voice", response_model=List[AssetOut])
def draw_voice(
    persona_id: str,
    body: DrawBody,
    user: Dict[str, Any] = Depends(current_user),
) -> List[AssetOut]:
    persona = _persona_row(user["id"], persona_id)
    settings = get_settings()
    sample = (body.sample_text or "").strip() or settings.tts_default_sample_text
    prompt = body.prompt.strip()
    out: List[AssetOut] = []
    batch_salt = _batch_salt(body.batch_id)
    pname = (persona["remark"] or persona["name"] or "").strip()
    pbio = (persona["bio"] or "").strip()
    for i in range(int(body.count)):
        # 单条请求：用 slot_index；批量：用循环下标
        vi = int(body.slot_index) + i if int(body.count) == 1 else i
        instructions, meta = voice_variant(
            prompt, vi, salt=batch_salt, name=pname, bio=pbio
        )
        wav = synthesize_voice_wav(
            text=sample,
            instructions=instructions,
            seed=int(meta.get("seed") or 0) or None,
        )
        if not wav:
            continue
        fid = _save_audio_bytes(user_id=user["id"], data=wav)
        if not fid:
            continue
        aid = new_id("pa_")
        now = iso()
        meta = {
            **meta,
            "engine": (get_settings().tts_engine or "breeze"),
            "label": "breeze-tts-2" if (get_settings().tts_engine or "breeze") == "breeze" else "qwen3-voicedesign",
            "base_prompt": prompt,
        }
        db.execute(
            """
            INSERT INTO persona_assets(
              id, user_id, persona_id, kind, file_id, path, prompt, sample_text, fixed, meta_json, created_at
            ) VALUES (?, ?, ?, 'voice', ?, '', ?, ?, 0, ?, ?)
            """,
            (
                aid,
                user["id"],
                persona_id,
                fid,
                prompt,
                sample,
                json.dumps(meta, ensure_ascii=False),
                now,
            ),
        )
        row = db.fetchone("SELECT * FROM persona_assets WHERE id = ?", (aid,))
        if row:
            out.append(_row_out(row))
    if not out:
        raise HTTPException(status_code=503, detail="音色生成失败，请确认 TTS 服务可用")
    return out


@router.post("/{persona_id}/assets/draw/image", response_model=List[AssetOut])
def draw_image(
    persona_id: str,
    body: DrawBody,
    user: Dict[str, Any] = Depends(current_user),
) -> List[AssetOut]:
    persona = _persona_row(user["id"], persona_id)
    prompt = body.prompt.strip()
    ref_persona = bool(body.ref_persona)
    pname = (persona["remark"] or persona["name"] or "").strip()
    pbio = (persona["bio"] or "").strip()
    # 形象抽卡：文生图抽长相方案；参考人设=锁性别/简介，不用头像 edit（否则脸被锁死抽不出新长相）
    out: List[AssetOut] = []
    batch_salt = _batch_salt(body.batch_id)
    for i in range(int(body.count)):
        vi = int(body.slot_index) + i if int(body.count) == 1 else i
        final_prompt, ratio, meta = image_variant(
            prompt,
            vi,
            salt=batch_salt,
            name=pname,
            bio=pbio,
            ref_persona=ref_persona,
        )
        # 头像场景：提示含「正方形/头像」时强制 1:1
        want_square = any(k in prompt for k in ("正方形", "头像", "1:1", "square"))
        w, h = (768, 768) if want_square else (768, 1024)
        use_ratio = "1:1" if want_square else ratio
        png = generate_image_bytes(
            final_prompt,
            backend="qwen",
            width=w,
            height=h,
            ratio=use_ratio,
            seed=int(meta.get("seed") or 0) or None,
            ref_image=None,
        )
        if not png:
            continue
        fid = save_image_bytes(user_id=user["id"], data=png, source="persona_look", suffix="png")
        if not fid:
            continue
        aid = new_id("pa_")
        now = iso()
        meta = {
            **meta,
            "engine": "qwen-image-2.1",
            "role": "look_base",
            "base_prompt": prompt,
            "mode": "t2i",
        }
        db.execute(
            """
            INSERT INTO persona_assets(
              id, user_id, persona_id, kind, file_id, path, prompt, sample_text, fixed, meta_json, created_at
            ) VALUES (?, ?, ?, 'image', ?, '', ?, '', 0, ?, ?)
            """,
            (
                aid,
                user["id"],
                persona_id,
                fid,
                prompt,
                json.dumps(meta, ensure_ascii=False),
                now,
            ),
        )
        row = db.fetchone("SELECT * FROM persona_assets WHERE id = ?", (aid,))
        if row:
            out.append(_row_out(row))
    if not out:
        raise HTTPException(status_code=503, detail="形象生成失败，请确认 QwenImage 服务可用")
    return out


@router.post("/{persona_id}/assets/{asset_id}/fix", response_model=AssetOut)
def fix_asset(
    persona_id: str,
    asset_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> AssetOut:
    """固定为该人设的音色或原始形象（同 kind 互斥）。"""
    _owned_persona(user["id"], persona_id)
    row = db.fetchone(
        """
        SELECT * FROM persona_assets
        WHERE id = ? AND user_id = ? AND persona_id = ?
        """,
        (asset_id, user["id"], persona_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    kind = row["kind"]
    db.execute(
        """
        UPDATE persona_assets SET fixed = 0
        WHERE user_id = ? AND persona_id = ? AND kind = ?
        """,
        (user["id"], persona_id, kind),
    )
    db.execute(
        "UPDATE persona_assets SET fixed = 1 WHERE id = ?",
        (asset_id,),
    )
    # 形象固定 → 同步为人设头像，供聊天显示与后续图生图
    if kind == "image" and (row["file_id"] or ""):
        db.execute(
            "UPDATE personas SET avatar_file_id = ? WHERE id = ? AND user_id = ?",
            (row["file_id"], persona_id, user["id"]),
        )
    # 语音固定 → 写入 voice_profiles 指针（便于通话/唱歌复用）
    if kind == "voice" and (row["file_id"] or ""):
        media = db.fetchone(
            "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
            (row["file_id"], user["id"]),
        )
        path = (media["path"] if media else "") or ""
        now = iso()
        vid = new_id("vp_")
        db.execute(
            """
            INSERT INTO voice_profiles(
              id, user_id, persona_id, filename, path, duration_sec, sample_rate, channels,
              embed_status, embed_note, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 'fixed', ?, ?)
            """,
            (
                vid,
                user["id"],
                persona_id,
                f"fixed_{asset_id}.wav",
                path,
                f"persona_asset:{asset_id}",
                now,
            ),
        )
    fixed = db.fetchone("SELECT * FROM persona_assets WHERE id = ?", (asset_id,))
    assert fixed is not None
    return _row_out(fixed)


@router.delete("/{persona_id}/assets/{asset_id}")
def delete_asset(
    persona_id: str,
    asset_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    _owned_persona(user["id"], persona_id)
    row = db.fetchone(
        """
        SELECT id FROM persona_assets
        WHERE id = ? AND user_id = ? AND persona_id = ?
        """,
        (asset_id, user["id"], persona_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    db.execute("DELETE FROM persona_assets WHERE id = ?", (asset_id,))
    return {"ok": True, "id": asset_id}
