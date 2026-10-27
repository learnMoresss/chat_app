#!/usr/bin/env python3
"""轻量 OCR HTTP：兼容 /general/base64（rec_texts + det_polygons）。

用 rapidocr-onnxruntime（PP-OCR ONNX），替代拉不到的 paddle docker 镜像。
默认绑定 127.0.0.1:9899。
"""
from __future__ import annotations

import base64
import io
import os
from typing import Any, Dict, List

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="chat-ocr", version="1.0")
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


class Base64In(BaseModel):
    base64_img: str = Field(..., min_length=8)
    preprocess: bool = False


def _run_ocr(raw: bytes) -> List[Dict[str, Any]]:
    engine = _get_engine()
    # RapidOCR 接受 path / ndarray / bytes path via PIL
    from PIL import Image
    import numpy as np

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    arr = np.array(im)
    result, _elapse = engine(arr)
    out: List[Dict[str, Any]] = []
    if not result:
        return out
    for item in result:
        # [[box], text, score]
        if not item or len(item) < 2:
            continue
        box, text = item[0], str(item[1] or "").strip()
        if not text:
            continue
        score = float(item[2]) if len(item) > 2 else 0.0
        out.append({"text": text, "score": score, "box": box})
    return out


@app.get("/health")
def health() -> dict:
    return {"ok": True, "engine": "rapidocr-onnxruntime"}


@app.post("/general/base64")
def general_base64(body: Base64In) -> dict:
    b64 = body.base64_img.strip()
    if "," in b64 and b64.lower().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)
    lines = _run_ocr(raw)
    return {
        "rec_texts": [x["text"] for x in lines],
        "rec_scores": [x["score"] for x in lines],
        "det_polygons": [x["box"] for x in lines],
        "results": [
            {
                "rec_texts": [x["text"] for x in lines],
                "rec_scores": [x["score"] for x in lines],
                "det_polygons": [x["box"] for x in lines],
            }
        ],
    }


def main() -> None:
    import uvicorn

    host = os.environ.get("OCR_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("OCR_BIND_PORT", "9899"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
