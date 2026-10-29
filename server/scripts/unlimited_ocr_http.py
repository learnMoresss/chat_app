#!/usr/bin/env python3
"""本机 Unlimited-OCR HTTP（Transformers），OpenAI chat.completions 兼容子集。

用法（GPU1 例）：
  CUDA_VISIBLE_DEVICES=1 HF_ENDPOINT=https://hf-mirror.com \\
    /data/ai/envs/torch-cu128/bin/python scripts/unlimited_ocr_http.py --port 9898
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

MODEL_ID = os.environ.get("UNLIMITED_OCR_MODEL", "baidu/Unlimited-OCR")
DEVICE = os.environ.get("UNLIMITED_OCR_DEVICE", "cuda")

app = FastAPI(title="unlimited-ocr-local")
_bundle: dict[str, Any] = {}


class ChatReq(BaseModel):
    model: str = MODEL_ID
    messages: List[dict] = Field(default_factory=list)
    max_tokens: int = 8192
    temperature: float = 0.0
    skip_special_tokens: bool = False
    vllm_xargs: dict | None = None


def _load():
    if _bundle.get("model") is not None:
        return
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    kw = dict(trust_remote_code=True, use_safetensors=True)
    try:
        model = AutoModel.from_pretrained(MODEL_ID, dtype=torch.bfloat16, **kw)
    except TypeError:
        model = AutoModel.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16, **kw)
    model = model.eval()
    if DEVICE.startswith("cuda") and torch.cuda.is_available():
        model = model.cuda()
    _bundle["tok"] = tok
    _bundle["model"] = model


def _save_data_url(url: str, dest: Path) -> None:
    m = re.match(r"^data:([^;]+);base64,(.+)$", url, re.DOTALL)
    if not m:
        raise ValueError("only data:image/...;base64 supported")
    dest.write_bytes(base64.b64decode(m.group(2)))


def _infer(images: List[Path], prompt: str) -> str:
    _load()
    model = _bundle["model"]
    tok = _bundle["tok"]
    out_dir = Path(tempfile.mkdtemp(prefix="uocr_out_"))
    try:
        # eval_mode=True 才会 return 文本；save_results 只写盘不返回
        if len(images) <= 1:
            text = model.infer(
                tok,
                prompt=prompt or "<image>document parsing.",
                image_file=str(images[0]),
                output_path=str(out_dir),
                base_size=1024,
                image_size=640,
                crop_mode=True,
                max_length=8192,
                no_repeat_ngram_size=35,
                ngram_window=128,
                eval_mode=True,
                save_results=False,
            )
        else:
            text = model.infer_multi(
                tok,
                prompt=prompt or (("<image>" * len(images)) + "Multi page parsing."),
                image_files=[str(p) for p in images],
                output_path=str(out_dir),
                image_size=1024,
                max_length=8192,
                no_repeat_ngram_size=35,
                ngram_window=1024,
                eval_mode=True,
                save_results=False,
            )
        if isinstance(text, (list, tuple)):
            text = "\n".join(str(x) for x in text)
        return str(text or "")
    finally:
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID, "loaded": _bundle.get("model") is not None}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/chat/completions")
def chat(req: ChatReq):
    try:
        _load()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"model_load_failed:{exc}") from exc

    images: List[Path] = []
    prompt = "<image>document parsing."
    tmpdir = Path(tempfile.mkdtemp(prefix="uocr_in_"))
    try:
        for msg in req.messages:
            content = msg.get("content")
            if isinstance(content, str):
                prompt = content
                continue
            if not isinstance(content, list):
                continue
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    url = ((part.get("image_url") or {}) if isinstance(part.get("image_url"), dict) else {}).get("url") or ""
                    dest = tmpdir / f"img_{len(images)}.jpg"
                    _save_data_url(str(url), dest)
                    images.append(dest)
            if texts:
                prompt = "".join(texts)
        if not images:
            raise HTTPException(status_code=400, detail="no_image")
        text = _infer(images, prompt)
        return {
            "id": "uocr-local",
            "object": "chat.completion",
            "model": req.model or MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9898)
    ap.add_argument("--preload", action="store_true")
    args = ap.parse_args()
    if args.preload:
        _load()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
