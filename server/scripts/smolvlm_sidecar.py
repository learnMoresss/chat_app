#!/usr/bin/env python3
"""轻量本地 VL 侧车：SmolVLM2-500M，OpenAI /v1/chat/completions + image_url。

默认监听 127.0.0.1:9293，供 chat-api VISION_CHAT_URL 使用。
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import re
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MODEL_ID = os.environ.get(
    "SMOLVLM_MODEL",
    "HuggingFaceTB/SmolVLM2-500M-Video-Instruct",
)
HOST = os.environ.get("SMOLVLM_HOST", "127.0.0.1")
PORT = int(os.environ.get("SMOLVLM_PORT", "9293"))
TOKEN = (os.environ.get("SMOLVLM_API_TOKEN") or "").strip()
DEVICE = os.environ.get("SMOLVLM_DEVICE", "cuda:0")

app = FastAPI(title="smolvlm-sidecar")
_model = None
_processor = None


def _load():
    global _model, _processor
    if _model is not None:
        return
    import torch
    from transformers import AutoProcessor

    _processor = AutoProcessor.from_pretrained(MODEL_ID)
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    # 兼容不同 transformers 版本的模型类名
    model = None
    last_err: Exception | None = None
    for cls_name in (
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
        "Idefics3ForConditionalGeneration",
    ):
        try:
            cls = getattr(__import__("transformers", fromlist=[cls_name]), cls_name)
            model = cls.from_pretrained(
                MODEL_ID,
                torch_dtype=dtype,
                device_map=DEVICE if torch.cuda.is_available() else "cpu",
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            model = None
    if model is None:
        raise RuntimeError(f"load SmolVLM failed: {last_err}")
    _model = model
    _model.eval()

class ChatMessage(BaseModel):
    role: str = "user"
    content: Any = ""


class ChatReq(BaseModel):
    model: str = ""
    messages: List[ChatMessage] = Field(default_factory=list)
    max_tokens: int = 64
    temperature: float = 0.2


def _auth(authorization: Optional[str]) -> None:
    if not TOKEN:
        return
    raw = (authorization or "").strip()
    if raw.startswith("Bearer "):
        raw = raw[7:].strip()
    if raw != TOKEN:
        raise HTTPException(401, "unauthorized")


def _parse_content(content: Any) -> tuple[str, Optional[bytes]]:
    if isinstance(content, str):
        return content.strip(), None
    if not isinstance(content, list):
        return str(content or ""), None
    text_parts: List[str] = []
    img: Optional[bytes] = None
    for part in content:
        if not isinstance(part, dict):
            continue
        typ = part.get("type")
        if typ == "text":
            text_parts.append(str(part.get("text") or ""))
        elif typ == "image_url":
            url = ((part.get("image_url") or {}) if isinstance(part.get("image_url"), dict) else {}).get("url") or ""
            m = re.match(r"^data:image/[^;]+;base64,(.+)$", str(url), flags=re.S)
            if m:
                try:
                    img = base64.b64decode(m.group(1))
                except Exception:
                    img = None
    return " ".join(text_parts).strip(), img


@app.get("/v1/models")
def models(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": MODEL_ID}


@app.post("/v1/chat/completions")
def chat(req: ChatReq, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    _load()
    import torch
    from PIL import Image

    assert _model is not None and _processor is not None
    # 取最后一条 user
    prompt = "Describe this image briefly in Chinese."
    image_bytes: Optional[bytes] = None
    for msg in reversed(req.messages):
        if msg.role != "user":
            continue
        prompt, image_bytes = _parse_content(msg.content)
        break
    if not image_bytes:
        raise HTTPException(400, "需要 image_url data URI")
    if not prompt:
        prompt = "用中文一句话描述这张图，最多16个字。"

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = _processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = _processor(text=text, images=[image], return_tensors="pt")
    inputs = {k: v.to(_model.device) if hasattr(v, "to") else v for k, v in inputs.items()}
    max_new = max(8, min(int(req.max_tokens or 64), 128))
    with torch.inference_mode():
        out = _model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    # trim prompt tokens
    trimmed = out[:, inputs["input_ids"].shape[-1] :]
    content = _processor.batch_decode(trimmed, skip_special_tokens=True)[0].strip()
    return {
        "id": "smolvlm-local",
        "object": "chat.completion",
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
