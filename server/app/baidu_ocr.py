"""兼容旧导入：转调本机 unlimited_ocr。"""
from __future__ import annotations

from .unlimited_ocr import (  # noqa: F401
    configured,
    ocr_image_lines,
    parse_document_markdown,
    parse_file,
)
