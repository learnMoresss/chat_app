"""入站文档解析：优先抽原生文本，不足再走百度 Unlimited-OCR。"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

log = logging.getLogger("doc_parse")

TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yml", ".yaml", ".xml", ".html", ".htm"}
OFFICE_EXTS = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".xlsx", ".xls", ".ods"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}

# 原生抽到这么多有效字就认为够用，不再打 OCR
_MIN_NATIVE_CHARS = 40


def parse_inbound_file(path: str | Path, *, hint: str = "", filename: str = "") -> Dict[str, Any]:
    """返回 {summary, text, source, lines}。source: native|unlimited|none。"""
    p = Path(path)
    name = (filename or p.name or "file.bin").strip()
    if not p.is_file():
        return {"summary": None, "text": "", "source": "none", "lines": []}

    ext = Path(name).suffix.lower() or p.suffix.lower()
    native = _extract_native(p, ext=ext)
    native_clean = _normalize(native)
    if _enough(native_clean):
        summary = _summary_from_text(native_clean, hint=hint, label="文件正文")
        return {"summary": summary, "text": native_clean, "source": "native", "lines": _lines_from_text(native_clean)}

    # 扫面 PDF / 图片 → 本机 Unlimited；办公二进制无渲染器时跳过
    if ext in IMAGE_EXTS | PDF_EXTS or (not native_clean and ext in IMAGE_EXTS | PDF_EXTS):
        from . import unlimited_ocr

        if unlimited_ocr.configured():
            try:
                out = unlimited_ocr.parse_file(p, file_name=name)
                text = _normalize(out.get("markdown") or out.get("text") or "")
                if text:
                    summary = _summary_from_text(text, hint=hint, label="文档解析")
                    return {
                        "summary": summary,
                        "text": text,
                        "source": "unlimited",
                        "lines": list(out.get("lines") or []),
                    }
            except Exception as exc:
                log.warning("doc unlimited failed: %s", exc)

    if native_clean:
        summary = _summary_from_text(native_clean, hint=hint, label="文件正文(较少)")
        return {"summary": summary, "text": native_clean, "source": "native", "lines": _lines_from_text(native_clean)}

    if hint:
        return {
            "summary": f"对方发来文件「{name}」，未能解析正文（附言：{hint[:100]}）",
            "text": "",
            "source": "none",
            "lines": [],
        }
    return {
        "summary": f"对方发来文件「{name}」，未能解析正文",
        "text": "",
        "source": "none",
        "lines": [],
    }


def _enough(text: str) -> bool:
    # 去掉空白后有效字数
    compact = re.sub(r"\s+", "", text or "")
    return len(compact) >= _MIN_NATIVE_CHARS


def _normalize(text: str) -> str:
    t = (text or "").replace("\x00", "").strip()
    if len(t) > 24000:
        t = t[:24000] + "\n…(已截断)"
    return t


def _summary_from_text(text: str, *, hint: str, label: str) -> str:
    preview = re.sub(r"\s+", " ", text).strip()
    if len(preview) > 800:
        preview = preview[:800] + "…"
    base = f"{label}：{preview}" if preview else f"{label}为空"
    if hint and hint not in base:
        return f"{base}（对方说：{hint[:80]}）"
    return base


def _lines_from_text(text: str) -> list:
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            out.append({"text": s, "score": 1.0, "box": [], "type": "text"})
    return out[:200]


def _extract_native(path: Path, *, ext: str) -> str:
    try:
        if ext in TEXT_EXTS or ext == "":
            return _read_text_file(path)
        if ext in PDF_EXTS:
            return _pdf_text(path)
        if ext == ".docx":
            return _docx_text(path)
        if ext == ".pptx":
            return _pptx_text(path)
        if ext in {".xlsx", ".xls", ".ods"}:
            # 表格二进制不硬解，交给 Unlimited
            return ""
        if ext in {".doc", ".rtf", ".odt", ".ppt"}:
            return ""
        # 未知后缀：尝试当文本
        return _read_text_file(path)
    except Exception as exc:
        log.debug("native extract failed %s: %s", path, exc)
        return ""


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return ""


def _pdf_text(path: Path) -> str:
    bin_path = shutil.which("pdftotext")
    if not bin_path:
        return ""
    try:
        r = subprocess.run(
            [bin_path, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        out = (r.stdout or b"").decode("utf-8", errors="ignore")
        return out
    except Exception as exc:
        log.debug("pdftotext failed: %s", exc)
        return ""


def _docx_text(path: Path) -> str:
    """不依赖 python-docx：读 word/document.xml 抽文本。"""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts: list[str] = []
    for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    # 段落换行
    paras = []
    for p in root.findall(".//w:p", ns):
        texts = [t.text or "" for t in p.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paras.append(line)
    return "\n".join(paras) if paras else "".join(parts)


def _pptx_text(path: Path) -> str:
    paras: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        for name in names:
            xml = zf.read(name)
            root = ET.fromstring(xml)
            for t in root.iter():
                if t.tag.endswith("}t") and (t.text or "").strip():
                    paras.append(t.text.strip())
    return "\n".join(paras)
