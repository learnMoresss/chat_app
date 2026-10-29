"""入站图片/视频描述：本机 Unlimited-OCR + Rapid/飞桨（无云端）。"""
from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import get_settings

log = logging.getLogger("vision_gen")


def describe_image_path(path: str | Path, *, hint: str = "") -> Optional[str]:
    """入站识图：本机 Unlimited 看内容；无结果再飞桨抠字。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return None
    engine = (get_settings().inbound_image_engine or "unlimited").strip().lower()
    if engine in ("unlimited", "uocr", "baidu-unlimited", "auto"):
        summary = _describe_via_unlimited(p, hint=hint)
        if summary:
            return summary
    summary, _lines = ocr_image_path(p, hint=hint)
    if summary:
        return summary
    return _heuristic(p, hint=hint)


def ocr_image_path(path: str | Path, *, hint: str = "") -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """OCR：返回 (摘要文本, lines[{text, box, score}])。飞桨/Rapid（:9899）。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return None, []
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
    if hint:
        return f"图中无明显文字，对方说：{hint[:100]}", []
    h = _heuristic(p, hint=hint)
    return (h or "图中无明显文字"), []


def _strip_uocr_markup(raw: str) -> str:
    """去掉 Unlimited 的 <|det|> / <|ref|> 壳，留下可读正文。"""
    import re

    t = raw or ""
    # <|det|>text [box]<|/det|>正文 或纯 det
    t = re.sub(
        r"<\|det\|>\w+\s*\[[^\]]*\]<\|/det\|>",
        " ",
        t,
    )
    t = re.sub(r"<\|/?[^|]+\|>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 无有效内容的占位
    if not t or t in {"image", "Image"}:
        return ""
    if "无法进行准确的文本识别" in t and len(t) < 80:
        return ""
    return t


def _describe_via_unlimited(path: Path, *, hint: str = "") -> Optional[str]:
    """本机 Unlimited-OCR：优先抠字；自然图若无字则返回空让上层回落。"""
    try:
        from . import unlimited_ocr

        if not unlimited_ocr.configured():
            return None
        # 先走文档解析（强项）；自然图通常只有 det 框
        lines = unlimited_ocr.ocr_image_lines(path)
        texts = [str(x.get("text") or "").strip() for x in lines if (x.get("text") or "").strip()]
        texts = [x for x in texts if x.lower() not in {"image", "non-text", "[non-text]"}]
        if texts:
            text = "图中文字：" + "；".join(texts[:24])
            if hint:
                text = f"{text}（对方说：{hint[:80]}）"
            return text[:800]
        # 再试自定义描述；若仍是空壳 det，视为无结果
        prompt = (
            "<image>请用中文简要描述画面主体、场景和关键文字。"
            "若几乎无字，就一句话说明看见了什么。"
        )
        if hint:
            prompt += f" 对方附言：{hint[:100]}"
        raw = unlimited_ocr.describe_image(path, prompt=prompt)
        text = _strip_uocr_markup(raw or "")
        if not text:
            return None
        if hint and hint not in text:
            text = f"{text}（对方说：{hint[:80]}）"
        return text[:800]
    except Exception as exc:
        log.warning("unlimited describe failed: %s", exc)
        return None


def _deepseek_vision_key() -> str:
    s = get_settings()
    return (
        (s.deepseek_api_key_official or "").strip()
        or (s.deepseek_api_key or "").strip()
    )


def _describe_via_local_vl(path: Path, *, hint: str = "", prompt: str = "") -> Optional[str]:
    """本机 VL 侧车（默认 SmolVLM2 @ :9293）OpenAI chat+image。"""
    s = get_settings()
    base = (s.vision_chat_url or "").strip().rstrip("/")
    if not base:
        return None
    model = (s.vision_model or "").strip()
    if not model:
        return None
    token = (s.vision_api_token or "").strip()
    timeout = float(s.vision_timeout_seconds or 45)
    raw = _prepare_image_bytes(path)
    if len(raw) < 32:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime == "image/jpg":
        mime = "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    text_prompt = (prompt or "").strip() or (
        "这是表情包贴纸。只用中文输出一个短标签，最多12个字，"
        "例如「橘猫歪头」「小狗哭哭」。不要写「主体/动作/情绪」，不要解释。"
    )
    if hint:
        text_prompt += f"\n参考：{hint[:80]}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 64,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base}/chat/completions",
                headers=headers,
                json=payload,
            )
        if r.status_code >= 400:
            log.warning(
                "local vl http %s: %s",
                r.status_code,
                (r.text or "")[:200],
            )
            return None
        j = r.json() if r.content else {}
        msg = ((j.get("choices") or [{}])[0].get("message") or {})
        content = str(msg.get("content") or "").strip()
        if not content:
            return None
        # 压成短标签
        content = content.replace("\n", " ").strip()
        return content[:120]
    except Exception as exc:
        log.warning("local vl failed: %s", exc)
        return None


def _local_vl_ready() -> bool:
    """侧车是否可响应（避免模型下载中把标注拖死）。"""
    s = get_settings()
    base = (s.vision_chat_url or "").strip().rstrip("/")
    if not base:
        return False
    # .../v1 -> healthz on root
    root = base[:-3] if base.endswith("/v1") else base
    token = (s.vision_api_token or "").strip()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=1.5) as client:
            r = client.get(f"{root}/healthz", headers=headers)
        return r.status_code < 400
    except Exception:
        return False


def caption_sticker_path(path: str | Path, *, hint: str = "") -> Optional[str]:
    """贴纸语义短标签：本地 VL。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return None
    if not _local_vl_ready():
        return None
    return _describe_via_local_vl(p, hint=hint)


def _describe_via_deepseek_official(path: Path, *, hint: str = "") -> Optional[str]:
    """DeepSeek 官方 chat/completions 多模态识图。"""
    key = _deepseek_vision_key()
    if not key:
        log.warning("deepseek vision: missing DEEPSEEK_API_KEY_OFFICIAL")
        return None
    s = get_settings()
    base = (s.deepseek_vision_base_url or "https://api.deepseek.com").rstrip("/")
    model = (s.deepseek_vision_model or "deepseek-chat").strip() or "deepseek-chat"
    timeout = float(s.deepseek_vision_timeout_seconds or 45)
    raw = _prepare_image_bytes(path)
    if len(raw) < 32:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime == "image/jpg":
        mime = "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    prompt = (
        "请用中文简要描述这张图片：主体、场景、以及图中可见的关键文字。"
        "控制在 120 字以内，不要客套。"
    )
    if hint:
        prompt += f"\n对方附言：{hint[:120]}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 280,
        "temperature": 0.2,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if r.status_code >= 400:
            log.warning(
                "deepseek vision http %s: %s",
                r.status_code,
                (r.text or "")[:240],
            )
            return None
        j = r.json() if r.content else {}
        msg = ((j.get("choices") or [{}])[0].get("message") or {})
        content = str(msg.get("content") or "").strip()
        if not content:
            # deepseek-flash 等偶发只填 reasoning_content
            reason = str(msg.get("reasoning_content") or "").strip()
            if reason:
                # 取末段非空行作摘要，避免整段思维链灌进气泡
                lines = [ln.strip() for ln in reason.splitlines() if ln.strip()]
                content = lines[-1] if lines else reason
                if len(content) < 12 and len(lines) >= 2:
                    content = " ".join(lines[-3:])
        text = content.strip()
        if not text:
            return None
        return text[:800]
    except Exception as exc:
        log.warning("deepseek vision failed: %s", exc)
        return None


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
