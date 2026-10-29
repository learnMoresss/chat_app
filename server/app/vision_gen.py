"""入站图片/视频描述：优先本机 Unlimited-OCR，回落 Paddle/Rapid；无字再启发式。"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import get_settings

log = logging.getLogger("vision_gen")


def describe_image_path(path: str | Path, *, hint: str = "") -> Optional[str]:
    """返回给人看的短摘要（含 OCR 文本）；详细坐标见 ocr_image_path。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return None
    summary, _lines = ocr_image_path(p, hint=hint)
    if summary:
        return summary
    return _heuristic(p, hint=hint)


def ocr_image_path(path: str | Path, *, hint: str = "") -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """OCR：返回 (摘要文本, lines[{text, box, score}])。优先 Unlimited。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return None, []
    lines = _ocr_via_unlimited(p)
    if not lines:
        lines = _ocr_via_paddle(p)
    if lines:
        texts = [str(x.get("text") or "").strip() for x in lines if (x.get("text") or "").strip()]
        joined = "；".join(texts[:24])
        summary = f"图中文字：{joined}" if joined else None
        if summary and hint:
            summary = f"{summary}（对方说：{hint[:80]}）"
        elif not summary and hint:
            summary = f"图中无明显文字，对方说：{hint[:100]}"
        elif not summary:
            summary = "图中无明显文字"
        return summary, lines
    # OCR 服务不可用或无结果
    if hint:
        return f"图中无明显文字，对方说：{hint[:100]}", []
    h = _heuristic(p, hint=hint)
    return (h or "图中无明显文字"), []


def _ocr_via_unlimited(path: Path) -> List[Dict[str, Any]]:
    try:
        from . import unlimited_ocr

        if not unlimited_ocr.configured():
            return []
        return unlimited_ocr.ocr_image_lines(path)
    except Exception as exc:
        log.warning("unlimited image ocr failed: %s", exc)
        return []


def describe_video_path(path: str | Path, *, hint: str = "") -> Optional[str]:
    """抽首帧再 OCR；无 ffmpeg 则返回占位。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("ffmpeg"):
            return (hint or "一段视频").strip()[:200]
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "f.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(p),
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    str(frame),
                ],
                check=False,
                capture_output=True,
                timeout=60,
            )
            if frame.is_file():
                return describe_image_path(frame, hint=hint or "视频画面")
    except Exception as exc:
        log.debug("video describe failed: %s", exc)
    return (hint or "一段视频").strip()[:200]


def _prepare_image_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            long_side = max(w, h)
            if long_side > 1600:
                scale = 1600 / float(long_side)
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=88)
            return buf.getvalue()
    except Exception:
        return raw


def _ocr_via_paddle(path: Path) -> List[Dict[str, Any]]:
    """优先 HTTP OCR（OCR_BASE_URL），失败再 RapidOCR 进程内。"""
    s = get_settings()
    base = (s.ocr_base_url or "").rstrip("/")
    timeout = float(s.ocr_timeout_seconds or 30)
    raw = _prepare_image_bytes(path)
    if base:
        try:
            b64 = base64.b64encode(raw).decode("ascii")
            url = f"{base}/general/base64"
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json={"base64_img": b64, "preprocess": False})
            if r.status_code < 400:
                j = r.json() if r.content else {}
                lines = _parse_paddle_payload(j)
                if lines:
                    return lines
            else:
                log.warning("ocr http %s: %s", r.status_code, (r.text or "")[:200])
        except Exception as exc:
            log.warning("ocr http failed: %s", exc)
    return _ocr_via_rapid(raw)


def _ocr_via_rapid(raw: bytes) -> List[Dict[str, Any]]:
    """进程内 rapidocr-onnxruntime（PP-OCR），docker 不可用时兜底。"""
    try:
        from io import BytesIO

        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        log.debug("rapidocr unavailable: %s", exc)
        return []
    try:
        engine = getattr(_ocr_via_rapid, "_engine", None)
        if engine is None:
            engine = RapidOCR()
            setattr(_ocr_via_rapid, "_engine", engine)
        im = Image.open(BytesIO(raw)).convert("RGB")
        result, _elapse = engine(np.array(im))
        out: List[Dict[str, Any]] = []
        if not result:
            return out
        for item in result:
            if not item or len(item) < 2:
                continue
            text = str(item[1] or "").strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "score": float(item[2]) if len(item) > 2 else 0.0,
                    "box": item[0],
                }
            )
        return out
    except Exception as exc:
        log.warning("rapidocr failed: %s", exc)
        return []


def _parse_paddle_payload(j: Any) -> List[Dict[str, Any]]:
    """兼容 media-saber-paddle-ocr / paddleocr-fastapi 等常见返回。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(j, dict):
        return out
    # xylplm: { results: [ { rec_texts, rec_scores, det_polygons } ] } 或扁平
    results = j.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict) and "rec_texts" in first:
            texts = first.get("rec_texts") or []
            scores = first.get("rec_scores") or []
            polys = first.get("det_polygons") or first.get("dt_polys") or []
            for i, t in enumerate(texts):
                text = str(t or "").strip()
                if not text:
                    continue
                score = float(scores[i]) if i < len(scores) else 0.0
                box = polys[i] if i < len(polys) else []
                out.append({"text": text, "score": score, "box": box})
            return out
        # paddleocr-fastapi: results: [{text, confidence, box}, ...]
        for item in results:
            if not isinstance(item, dict):
                continue
            # nested list from hubserving
            if isinstance(item, list):
                continue
            text = str(item.get("text") or item.get("rec_text") or "").strip()
            if not text:
                continue
            score = float(item.get("confidence") or item.get("score") or 0)
            box = item.get("box") or item.get("text_region") or item.get("det_polygons") or []
            out.append({"text": text, "score": score, "box": box})
        if out:
            return out
        # hubserving: results: [[{text, confidence, text_region}, ...]]
        if results and isinstance(results[0], list):
            for item in results[0]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                out.append(
                    {
                        "text": text,
                        "score": float(item.get("confidence") or 0),
                        "box": item.get("text_region") or [],
                    }
                )
    # flat rec_texts at top
    if not out and "rec_texts" in j:
        texts = j.get("rec_texts") or []
        scores = j.get("rec_scores") or []
        polys = j.get("det_polygons") or []
        for i, t in enumerate(texts):
            text = str(t or "").strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "score": float(scores[i]) if i < len(scores) else 0.0,
                    "box": polys[i] if i < len(polys) else [],
                }
            )
    return out


def _heuristic(path: Path, *, hint: str) -> Optional[str]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            mode = im.mode
        base = f"一张约 {w}x{h} 的{mode}图片"
    except Exception:
        base = "一张图片"
    if hint:
        return f"{base}，对方说：{hint[:100]}"
    return base
