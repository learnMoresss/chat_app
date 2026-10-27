"""AI ⟦视频：…⟧：生图 + ffmpeg 拼短片（无独立视频模型时的可交付路径）。"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .image_gen import generate_image_bytes
from .media_cards import _load_media_bytes, save_image_bytes

log = logging.getLogger("video_gen")


def save_video_bytes(
    *,
    user_id: str,
    data: bytes,
    source: str = "video_gen",
    suffix: str = "mp4",
    content_type: str = "video/mp4",
) -> Optional[str]:
    if not data or len(data) < 64:
        return None
    from .media_store import put_bytes

    ext = suffix.lstrip(".") or "mp4"
    fid, _ = put_bytes(
        user_id=user_id,
        data=data,
        content_type=content_type,
        source=source,
        filename=f"{ext}.{ext}",
    )
    return fid


def generate_short_video(
    *,
    user_id: str,
    prompt: str,
    persona_name: str = "",
    ref_file_id: str = "",
    seconds: float = 3.0,
) -> Optional[str]:
    """返回 video media file_id；失败 None。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg missing; cannot build video")
        return None
    ref = _load_media_bytes(user_id, ref_file_id) if ref_file_id else None
    frames: list[bytes] = []
    for i in range(3):
        cap = prompt if i == 0 else f"{prompt}，镜头变化 {i + 1}"
        raw = generate_image_bytes(
            cap,
            width=768,
            height=768,
            ref_image=ref if i == 0 else None,
        )
        if raw:
            frames.append(raw)
    if not frames:
        # 至少落一张图当封面，由上层降级
        return None
    with tempfile.TemporaryDirectory(prefix="vdgen_") as td:
        tdir = Path(td)
        paths = []
        for i, raw in enumerate(frames):
            p = tdir / f"f{i:02d}.png"
            # 统一成 png：非 png 也先写出再让 ffmpeg 读
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                p.write_bytes(raw)
            else:
                jpg = tdir / f"f{i:02d}.jpg"
                jpg.write_bytes(raw)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(jpg), str(p)],
                    check=False,
                    capture_output=True,
                    timeout=60,
                )
                if not p.is_file():
                    continue
            paths.append(p)
        if not paths:
            return None
        # 每帧停留 seconds/len
        per = max(0.6, float(seconds) / max(1, len(paths)))
        list_file = tdir / "list.txt"
        lines = []
        for p in paths:
            lines.append(f"file '{p}'")
            lines.append(f"duration {per:.2f}")
        lines.append(f"file '{paths[-1]}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        out = tdir / "out.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-vf",
            "scale=720:720:force_original_aspect_ratio=decrease,pad=720:720:(ow-iw)/2:(oh-ih)/2",
            "-r",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except Exception as exc:
            log.warning("ffmpeg video failed: %s", exc)
            # 降级：存首帧图
            fid = save_image_bytes(
                user_id=user_id,
                data=frames[0],
                source="video_gen_fallback_image",
                suffix="png",
                content_type="image/png",
            )
            return fid
        if not out.is_file() or out.stat().st_size < 64:
            return None
        return save_video_bytes(user_id=user_id, data=out.read_bytes())
