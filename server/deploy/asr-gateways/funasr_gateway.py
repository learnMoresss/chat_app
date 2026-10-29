"""SenseVoiceSmall 常驻 ASR（OpenAI 兼容 transcriptions）。GPU0 / FP16。"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

MODEL_ID = os.environ.get("FUNASR_MODEL", "iic/SenseVoiceSmall")
MODEL_DIR = os.environ.get("FUNASR_MODEL_DIR", "").strip()
DEVICE = os.environ.get("FUNASR_DEVICE", "cuda:0")
# 单卡可见时用 cuda:0
if os.environ.get("CUDA_VISIBLE_DEVICES") is not None and ":" in DEVICE:
    # 物理 GPU 已由 CUDA_VISIBLE_DEVICES 映射，进程内固定 cuda:0
    DEVICE = "cuda:0"

app = FastAPI(title="FunASR SenseVoice gateway")
_model = None
_loaded_at = 0.0
_infer_count = 0


def _get_model():
    global _model, _loaded_at
    if _model is not None:
        return _model
    from funasr import AutoModel

    t0 = time.perf_counter()
    kw = dict(
        model=MODEL_DIR or MODEL_ID,
        device=DEVICE,
        disable_update=True,
    )
    # SenseVoice 常用 vad 关闭，通话短句自行切
    try:
        _model = AutoModel(**kw, vad_model=None)
    except TypeError:
        _model = AutoModel(**kw)
    _loaded_at = time.time()
    print(f"[funasr] loaded model={MODEL_DIR or MODEL_ID} device={DEVICE} load_ms={round((time.perf_counter()-t0)*1000)}", flush=True)
    return _model


@app.on_event("startup")
def _warmup():
    _get_model()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_DIR or MODEL_ID,
        "device": DEVICE,
        "cuda": torch.cuda.is_available(),
        "model_loaded": _model is not None,
        "infer_count": _infer_count,
        "torch_dtype_probe": str(torch.get_default_dtype()),
    }


def _extract_text(res) -> str:
    if res is None:
        return ""
    if isinstance(res, dict):
        return str(res.get("text") or res.get("preds") or "").strip()
    if isinstance(res, list) and res:
        item = res[0]
        if isinstance(item, dict):
            return str(item.get("text") or item.get("preds") or "").strip()
        return str(item).strip()
    return str(res).strip()


def _strip_sensevoice_tags(text: str) -> str:
    # SenseVoice 可能带 <|zh|><|NEUTRAL|>... 等标记
    import re
    return re.sub(r"<\|[^|>]+\|>", "", text or "").strip()


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(""),
    language: str = Form("zh"),
):
    global _infer_count
    t_all = time.perf_counter()
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    raw = await file.read()
    if not raw or len(raw) < 64:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(raw)
        path = tmp.name
    try:
        m = _get_model()
        t0 = time.perf_counter()
        # language 提示；SenseVoice 用 language=zh / auto
        lang = (language or "zh").strip() or "zh"
        try:
            res = m.generate(input=path, language=lang, use_itn=True)
        except TypeError:
            res = m.generate(input=path)
        decode_ms = round((time.perf_counter() - t0) * 1000)
        text = _strip_sensevoice_tags(_extract_text(res))
        _infer_count += 1
        total_ms = round((time.perf_counter() - t_all) * 1000)
        # 确认实际设备
        dev = DEVICE
        if torch.cuda.is_available():
            try:
                dev = f"cuda:{torch.cuda.current_device()}({torch.cuda.get_device_name(torch.cuda.current_device())})"
            except Exception:
                pass
        return {
            "text": text,
            "model": MODEL_DIR or MODEL_ID,
            "device": dev,
            "decode_ms": decode_ms,
            "total_ms": total_ms,
            "loaded_once": True,
            "infer_count": _infer_count,
        }
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8886")))
