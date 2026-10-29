"""本机 Unlimited-OCR（vLLM OpenAI 兼容）：图片 / 扫面文档 → markdown + 行框。

服务启动见 scripts/unlimited_ocr_serve.sh；默认 http://127.0.0.1:9898/v1
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import get_settings

log = logging.getLogger("unlimited_ocr")

_REF_RE = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.DOTALL)
_DET_RE = re.compile(r"<\|det\|>(.*?)<\|/det\|>", re.DOTALL)
_GROUND_PAIR = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|>\s*<\|det\|>(.*?)<\|/det\|>",
    re.DOTALL,
)


def configured() -> bool:
    s = get_settings()
    return bool((s.unlimited_ocr_base_url or "").strip())


def _base_url() -> str:
    return (get_settings().unlimited_ocr_base_url or "").rstrip("/")


def _model() -> str:
    return (get_settings().unlimited_ocr_model or "baidu/Unlimited-OCR").strip()


def _timeout() -> float:
    return float(get_settings().unlimited_ocr_timeout_seconds or 180)


def _data_url(path: Path) -> str:
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime == "image/jpg":
        mime = "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


_DET_INLINE = re.compile(
    r"<\|det\|>(\w+)\s*\[([^\]]+)\]<\|/det\|>(.*?)(?=<\|det\|>|$)",
    re.DOTALL,
)


def _clean_markdown(raw: str) -> str:
    if not raw:
        return ""
    # 优先拼 ref 文本；没有 grounding 则去掉特殊 token
    refs = _REF_RE.findall(raw)
    if refs:
        text = "\n".join(r.strip() for r in refs if r.strip())
    else:
        # Unlimited 常见：<|det|>text [x1,y1,x2,y2]<|/det|>正文
        chunks = [m.group(3).strip() for m in _DET_INLINE.finditer(raw) if m.group(3).strip()]
        if chunks:
            text = "\n".join(chunks)
        else:
            text = _DET_RE.sub("", raw)
            text = re.sub(r"<\|/?[^|]+\|>", "", text)
    return text.strip()


def _lines_from_grounding(raw: str) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for text, det in _GROUND_PAIR.findall(raw or ""):
        t = (text or "").strip()
        if not t:
            continue
        box: Any = []
        try:
            import ast

            parsed = ast.literal_eval((det or "").strip())
            box = parsed
        except Exception:
            box = (det or "").strip()
        lines.append({"text": t, "score": 1.0, "box": box, "type": "text"})
    if not lines:
        for m in _DET_INLINE.finditer(raw or ""):
            t = (m.group(3) or "").strip()
            if not t:
                continue
            box: Any = []
            try:
                coords = [float(x.strip()) for x in m.group(2).split(",")]
                if len(coords) == 4:
                    x1, y1, x2, y2 = coords
                    box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                else:
                    box = coords
            except Exception:
                box = m.group(2)
            lines.append({"text": t, "score": 1.0, "box": box, "type": m.group(1) or "text"})
    if lines:
        return lines
    md = _clean_markdown(raw)
    return [
        {"text": ln.strip(), "score": 1.0, "box": [], "type": "text"}
        for ln in md.splitlines()
        if ln.strip()
    ][:200]


def _chat(
    images: List[Path],
    *,
    multi: bool = False,
    prompt: str = "",
    max_tokens: int = 1024,
) -> str:
    if not images:
        return ""
    base = _base_url()
    if not base:
        raise RuntimeError("unlimited_ocr_not_configured")
    n = len(images)
    if multi or n > 1:
        use_prompt = (prompt or "").strip() or (("<image>" * n) + "Multi page parsing.")
        window = 1024
    else:
        use_prompt = (prompt or "").strip() or "<image>document parsing."
        window = 128
    content: List[Dict[str, Any]] = [{"type": "text", "text": use_prompt}]
    for p in images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(p)}})
    payload = {
        "model": _model(),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max(64, min(int(max_tokens or 1024), 2048)),
        "temperature": 0.0,
        "skip_special_tokens": False,
        "vllm_xargs": {"ngram_size": 35, "window_size": window},
    }
    url = f"{base}/chat/completions"
    with httpx.Client(timeout=_timeout()) as client:
        r = client.post(url, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"unlimited_ocr_http_{r.status_code}:{(r.text or '')[:240]}")
    j = r.json() if r.content else {}
    try:
        return str(j["choices"][0]["message"]["content"] or "")
    except Exception as exc:
        raise RuntimeError(f"unlimited_ocr_bad_response:{j}") from exc


def describe_image(path: str | Path, *, prompt: str = "") -> str:
    """本机 Unlimited 通用描述（自定义 prompt；限制 max_tokens）。"""
    p = Path(path)
    if not p.is_file():
        return ""
    return (
        _chat(
            [p],
            prompt=prompt
            or "<image>用中文简要描述画面主体、场景和关键文字。",
            max_tokens=512,
        )
        or ""
    ).strip()


def _pdf_to_images(path: Path, *, max_pages: int = 20) -> List[Path]:
    """用 pdftoppm 渲前几页；临时目录由调用方清理。"""
    bin_path = shutil.which("pdftoppm")
    if not bin_path:
        return []
    td = Path(tempfile.mkdtemp(prefix="uocr_pdf_"))
    prefix = td / "page"
    try:
        subprocess.run(
            [bin_path, "-jpeg", "-r", "144", "-f", "1", "-l", str(max_pages), str(path), str(prefix)],
            check=False,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:
        log.warning("pdftoppm failed: %s", exc)
        return []
    pages = sorted(td.glob("page*.jpg")) + sorted(td.glob("page*.jpeg"))
    return pages


def parse_file(
    path: str | Path,
    *,
    file_name: str = "",
    max_pages: int = 20,
) -> Dict[str, Any]:
    """图片直接识别；PDF 渲页后多图一次请求；其它格式先尽量转图或报错。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    name = (file_name or p.name or "doc.bin").lower()
    ext = Path(name).suffix.lower() or p.suffix.lower()
    tmp_pages: List[Path] = []
    cleanup_root: Optional[Path] = None
    try:
        if ext in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif"}:
            images = [p]
            multi = False
        elif ext == ".pdf":
            tmp_pages = _pdf_to_images(p, max_pages=max_pages)
            if not tmp_pages:
                raise RuntimeError("pdf_rasterize_failed")
            cleanup_root = tmp_pages[0].parent
            images = tmp_pages
            multi = len(images) > 1
        else:
            # 办公文档：无本地渲染器时抛错，由上层保留 native 文本
            raise RuntimeError(f"unsupported_for_local_ocr:{ext}")

        raw = _chat(images, multi=multi)
        markdown = _clean_markdown(raw)
        lines = _lines_from_grounding(raw)
        return {
            "markdown": markdown,
            "text": markdown,
            "lines": lines,
            "raw": raw,
            "pages": len(images),
        }
    finally:
        if cleanup_root and cleanup_root.exists():
            shutil.rmtree(cleanup_root, ignore_errors=True)


def ocr_image_lines(path: str | Path) -> List[Dict[str, Any]]:
    if not configured():
        return []
    try:
        out = parse_file(path, file_name=Path(path).name or "image.jpg")
        return list(out.get("lines") or [])
    except Exception as exc:
        log.warning("unlimited image ocr failed: %s", exc)
        return []


def parse_document_markdown(path: str | Path, *, file_name: str = "") -> Optional[str]:
    if not configured():
        return None
    try:
        out = parse_file(path, file_name=file_name or Path(path).name)
        return (out.get("markdown") or out.get("text") or "").strip() or None
    except Exception as exc:
        log.warning("unlimited doc parse failed: %s", exc)
        return None


def health() -> Dict[str, Any]:
    base = _base_url()
    if not base:
        return {"ok": False, "reason": "not_configured"}
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{base}/models")
        return {"ok": r.status_code < 400, "status": r.status_code, "body": (r.text or "")[:200]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
