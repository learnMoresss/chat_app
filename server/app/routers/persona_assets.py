"""人设抽卡：语音（TTS）/ 形象（Z-Image / Qwen）；支持多抽与固定。

直连本机 TTS / Comfy，不经 DSH。
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..draw_diversity import image_variant, voice_variant
from ..image_gen import generate_image_bytes
from ..media_cards import save_image_bytes
from ..media_store import put_bytes
from ..security import current_user, iso, new_id
from ..tts_gen import resolve_tts_engine, synthesize_voice_wav, warm_tts_engine

logger = logging.getLogger("chat.persona_assets")

router = APIRouter(prefix="/api/v1/personas", tags=["persona-assets"])


class DrawBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    count: int = Field(default=3, ge=1, le=6)
    sample_text: str = ""
    # 客户端多抽逐条请求时传入，保证同批走不同变体轴
    batch_id: str = ""
    slot_index: int = Field(default=0, ge=0, le=32)
    # 形象抽卡默认只用提示词；true 才附加人设短锚点
    ref_persona: bool = False
    # look=形象抽卡入库；avatar=只出图做头像，不进形象资产列表
    purpose: str = Field(default="look", max_length=16)


class FromMediaBody(BaseModel):
    """把已上传的 media file 登记为音色/形象候选。"""

    kind: str = Field(min_length=1, max_length=16)
    file_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(default="", max_length=2000)
    sample_text: str = Field(default="", max_length=500)


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
    persona = _persona_row(user["id"], persona_id)
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
    avatar_fid = ""
    try:
        avatar_fid = (persona["avatar_file_id"] or "").strip()
    except Exception:
        avatar_fid = ""
    out: List[AssetOut] = []
    for r in rows:
        item = _row_out(r)
        # 头像用途 / 当前头像文件：不进形象列表
        role = str((item.meta or {}).get("role") or "").lower()
        purpose = str((item.meta or {}).get("purpose") or "").lower()
        if role == "avatar" or purpose == "avatar":
            continue
        if item.kind == "image" and avatar_fid and item.file_id == avatar_fid:
            continue
        out.append(item)
    return out


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
        eng = resolve_tts_engine(for_draw=True)
        t0 = time.perf_counter()
        try:
            # 按需 ensure→合成；冷启可能 >60s，timeout 要盖住启动+推理
            wav = synthesize_voice_wav(
                text=sample,
                instructions=instructions,
                seed=int(meta.get("seed") or 0) or None,
                timeout=max(180.0, float(settings.tts_timeout_seconds or 300)),
                keep_loaded=True,
                engine=eng,
            )
        except Exception:
            wav = None
        synth_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "draw_voice persona=%s slot=%s engine=%s synth_ms=%s ok=%s",
            persona_id,
            vi,
            eng,
            synth_ms,
            bool(wav),
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
            "engine": eng,
            "label": {
                "cosyvoice": "cosyvoice-hot",
                "breeze": "breeze-tts-2",
                "voicedesign": "qwen3-voicedesign",
            }.get(eng, eng),
            "base_prompt": prompt,
            "synth_ms": synth_ms,
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
    _persona_row(user["id"], persona_id)  # 鉴权：必须是自己的人设
    prompt = body.prompt.strip()
    purpose = (body.purpose or "look").strip().lower()
    if purpose not in ("look", "avatar"):
        purpose = "look"
    for_avatar = purpose == "avatar"
    # 形象抽卡：默认严格按提示词文生图，不注入人设 bio（避免群像/跑题）
    ref_persona = bool(body.ref_persona) and not for_avatar
    pname = ""
    pbio = ""
    if ref_persona:
        persona = _persona_row(user["id"], persona_id)
        pname = (persona["remark"] or persona["name"] or "").strip()
        pbio = (persona["bio"] or "").strip()
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
        # 头像 / 提示含「正方形/头像」时强制 1:1
        want_square = for_avatar or any(k in prompt for k in ("正方形", "头像", "1:1", "square"))
        w, h = (768, 768) if want_square else (768, 1024)
        use_ratio = "1:1" if want_square else ratio
        # 抽长相默认文生图 → Z-Image（BF16）；失败再 Qwen t2i
        neg = str(meta.get("negative") or "")
        png = generate_image_bytes(
            final_prompt,
            backend="zimage",
            width=w,
            height=h,
            ratio=use_ratio,
            seed=int(meta.get("seed") or 0) or None,
            ref_image=None,
            negative=neg,
            timeout=float(get_settings().image_gen_timeout_seconds or 120),
        )
        if not png:
            # zimage 失败再试 qwen t2i
            png = generate_image_bytes(
                final_prompt,
                backend="qwen",
                width=w,
                height=h,
                ratio=use_ratio,
                seed=int(meta.get("seed") or 0) or None,
                ref_image=None,
                negative=neg,
                timeout=float(get_settings().image_gen_timeout_seconds or 120),
            )
        if not png:
            continue
        fid = save_image_bytes(
            user_id=user["id"],
            data=png,
            source="persona_avatar" if for_avatar else "persona_look",
            suffix="png",
        )
        if not fid:
            continue
        now = iso()
        meta = {
            **meta,
            "engine": "zimage-bf16",
            "role": "avatar" if for_avatar else "look_base",
            "purpose": purpose,
            "base_prompt": prompt,
            "mode": "t2i",
        }
        if for_avatar:
            # 只出文件给头像用，不入库形象资产
            out.append(
                AssetOut(
                    id=new_id("av_"),
                    persona_id=persona_id,
                    kind="avatar",
                    file_id=fid,
                    prompt=prompt,
                    sample_text="",
                    fixed=False,
                    created_at=now,
                    meta=meta,
                )
            )
            continue
        aid = new_id("pa_")
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
        raise HTTPException(
            status_code=503,
            detail=("头像" if for_avatar else "形象") + "生成失败，请确认 Comfy Z-Image / QwenImage 可用",
        )
    return out


def _audio_to_wav_bytes(data: bytes, filename: str) -> Optional[bytes]:
    """尽量转成 16k 单声道 wav（CosyVoice 克隆更稳）；失败返回 None。"""
    import subprocess
    import tempfile

    if not data:
        return None
    suffix = Path(filename or "").suffix.lower() or ".bin"
    if suffix == ".wav" and data[:4] == b"RIFF":
        return data
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in{suffix}"
        dst = Path(td) / "out.wav"
        src.write_bytes(data)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(src),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    str(dst),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
        except Exception:
            logger.exception("ffmpeg voice normalize failed name=%s", filename)
            return None
        if dst.is_file():
            out = dst.read_bytes()
            return out if out and out[:4] == b"RIFF" else None
    return None


@router.post("/{persona_id}/assets/from-media", response_model=AssetOut)
def asset_from_media(
    persona_id: str,
    body: FromMediaBody,
    user: Dict[str, Any] = Depends(current_user),
) -> AssetOut:
    """上传登记：音色参考音频 / 形象参考图。"""
    _owned_persona(user["id"], persona_id)
    kind = (body.kind or "").strip().lower()
    if kind not in ("voice", "image"):
        raise HTTPException(status_code=400, detail="kind 仅支持 voice / image")
    fid = (body.file_id or "").strip()
    media = db.fetchone(
        "SELECT * FROM media_files WHERE id = ? AND user_id = ?",
        (fid, user["id"]),
    )
    if not media:
        raise HTTPException(status_code=404, detail="媒体不存在")
    path = Path(str(media["path"] or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件缺失")
    prompt = (body.prompt or "").strip()
    sample = (body.sample_text or "").strip()
    meta: Dict[str, Any] = {
        "source": "upload",
        "diversity": "none",
        "base_prompt": prompt,
        "original_file_id": fid,
        "original_name": path.name,
    }
    store_fid = fid
    if kind == "voice":
        raw = path.read_bytes()
        name = path.name or "voice.bin"
        wav = _audio_to_wav_bytes(raw, name)
        if wav and wav != raw:
            store_fid, _ = put_bytes(
                user_id=user["id"],
                data=wav,
                content_type="audio/wav",
                source="persona_voice_upload",
                filename="ref.wav",
            )
        elif not wav and raw[:4] != b"RIFF":
            raise HTTPException(
                status_code=400,
                detail="音频需为 wav/mp3/m4a，且可被转成 wav；请换一段清晰人声再试",
            )
        if not prompt:
            prompt = "上传参考音色"
        meta.update(
            {
                "role": "voice_ref",
                "engine": "upload",
                "label": "上传",
            }
        )
    else:
        ctype = (media["content_type"] or "").lower()
        if not ctype.startswith("image/") and path.suffix.lower() not in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
        ):
            raise HTTPException(status_code=400, detail="请上传图片文件")
        if not prompt:
            prompt = "上传形象"
        meta.update(
            {
                "role": "look_base",
                "purpose": "look",
                "engine": "upload",
                "mode": "upload",
            }
        )
    aid = new_id("pa_")
    now = iso()
    db.execute(
        """
        INSERT INTO persona_assets(
          id, user_id, persona_id, kind, file_id, path, prompt, sample_text, fixed, meta_json, created_at
        ) VALUES (?, ?, ?, ?, ?, '', ?, ?, 0, ?, ?)
        """,
        (
            aid,
            user["id"],
            persona_id,
            kind,
            store_fid,
            prompt,
            sample,
            json.dumps(meta, ensure_ascii=False),
            now,
        ),
    )
    row = db.fetchone("SELECT * FROM persona_assets WHERE id = ?", (aid,))
    if not row:
        raise HTTPException(status_code=500, detail="入库失败")
    return _row_out(row)


@router.post("/{persona_id}/assets/warm-voice")
def warm_voice(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """抽卡前进页预热：按需拉起 Breeze（约 1 分钟冷启），热态后抽卡只付合成时间。"""
    _owned_persona(user["id"], persona_id)
    t0 = time.perf_counter()
    info = warm_tts_engine(for_realtime=False, for_draw=True)
    info["warm_ms"] = int((time.perf_counter() - t0) * 1000)
    # 预热成功后显式 keep：拉长闲置窗口，进页后立刻抽不至于再冷启
    try:
        from .. import tts_ondemand

        eng = resolve_tts_engine(for_draw=True)
        if info.get("ok"):
            tts_ondemand.release_engine(
                eng,
                unload_now=False,
                idle_seconds=float(getattr(tts_ondemand, "DRAW_IDLE_SECONDS", 600.0)),
            )
    except Exception:
        pass
    logger.info(
        "warm_voice persona=%s ok=%s engine=%s warm_ms=%s",
        persona_id,
        info.get("ok"),
        info.get("engine"),
        info.get("warm_ms"),
    )
    return info


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
    # 形象固定 ≠ 头像：只写入 persona_assets.fixed，不改 personas.avatar_file_id
    # 自拍/出镜图走 fixed look；聊天列表头像仍用单独上传/生成的头像
    # 语音固定 → 写入 voice_profiles，并异步建唱歌声线（ready 后才能唱）
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
            ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 'pending', ?, ?)
            """,
            (
                vid,
                user["id"],
                persona_id,
                f"fixed_{asset_id}.wav",
                path,
                f"queued:{asset_id}",
                now,
            ),
        )
        try:
            from ..voice_line import start_voice_line_train

            start_voice_line_train(
                user_id=user["id"], persona_id=persona_id, asset_id=asset_id
            )
        except Exception:
            logger.exception("start voice_line train failed asset=%s", asset_id)
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
        SELECT * FROM persona_assets
        WHERE id = ? AND user_id = ? AND persona_id = ?
        """,
        (asset_id, user["id"], persona_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="资产不存在")
    kind = (row["kind"] or "").strip().lower()
    was_fixed = bool(row["fixed"])
    note = f"persona_asset:{asset_id}"
    db.execute("DELETE FROM persona_assets WHERE id = ?", (asset_id,))
    # 删掉固定项 = 真正取消固定：清掉关联音色指针
    if kind == "voice" and was_fixed:
        try:
            db.execute(
                """
                DELETE FROM voice_profiles
                WHERE user_id = ? AND persona_id = ?
                  AND (embed_note = ? OR embed_note LIKE ?)
                """,
                (user["id"], persona_id, note, f"%{asset_id}%"),
            )
        except Exception:
            logger.exception("clear voice_profiles after delete failed")
    return {"ok": True, "id": asset_id, "cleared_fixed": was_fixed}
