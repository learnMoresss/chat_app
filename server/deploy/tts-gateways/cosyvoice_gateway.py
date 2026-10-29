"""On-demand Fun-CosyVoice3 HTTP gateway (instruction + fixed prompt wav).

- /v1/audio/speech：整包 WAV（聊天气泡）
- /v1/audio/speech/stream：长度前缀 PCM 帧流式
- /v1/warm：通话入口预热（加载模型 + 缓存声线声学特征；不计用户等待）

声线缓存只存声学特征，情绪 instruct 每轮现算，避免缓存死旧指令。
"""
from __future__ import annotations

import gc
import io
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import torch
import torchaudio
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse

COSY_ROOT = Path(os.environ.get("COSYVOICE_ROOT", "/data/ai/apps/CosyVoice"))
MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL_DIR", "/data/ai/models/audio/fun-cosyvoice3-0.5b-2512"
)
PROMPT_WAV = os.environ.get(
    "COSYVOICE_PROMPT_WAV", str(COSY_ROOT / "asset/zero_shot_prompt.wav")
)
IDLE = float(os.environ.get("COSYVOICE_IDLE_UNLOAD_SECONDS", "300"))
# 官方路径：AutoModel(fp16=True) → autocast；不开 TensorRT
FP16 = os.environ.get("COSYVOICE_FP16", "0").strip().lower() in ("1", "true", "yes", "on")
# CosyVoice3：无 load_jit；load_trt 需预编译 .plan（默认关，2080Ti/cuDNN 易失败）
LOAD_TRT = os.environ.get("COSYVOICE_LOAD_TRT", "0").strip().lower() in ("1", "true", "yes", "on")
# 首块更小 hop → 更早出声（音质接缝需 A/B）；后续块仍按 stream_scale_factor 放大
FIRST_HOP = max(8, int(os.environ.get("COSYVOICE_FIRST_HOP", "12") or 12))
DEFAULT_SPK_ID = "call_default_ref"

app = FastAPI(title="CosyVoice on-demand gateway")
_lock = threading.Lock()
_model = None
_timer: threading.Timer | None = None
# prompt_wav path -> acoustic-only feats (no prompt_text / instruct)
_spk_feats: Dict[str, Dict[str, Any]] = {}


def _arm():
    global _timer
    if IDLE <= 0:
        return
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(IDLE, lambda: (_lock and _unload()))
    _timer.daemon = True
    _timer.start()


def _unload():
    global _model, _timer, _spk_feats
    with _lock:
        if _timer:
            _timer.cancel()
            _timer = None
        if _model is not None:
            del _model
            _model = None
        _spk_feats = {}
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _get_model():
    global _model
    if _model is not None:
        return _model
    sys.path.insert(0, str(COSY_ROOT))
    sys.path.insert(0, str(COSY_ROOT / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel

    print(
        f"[cosyvoice] loading AutoModel fp16={FP16} load_trt={LOAD_TRT} "
        f"first_hop={FIRST_HOP} dir={MODEL_DIR}",
        flush=True,
    )
    try:
        _model = AutoModel(
            model_dir=MODEL_DIR, fp16=FP16, load_trt=LOAD_TRT, load_vllm=False
        )
    except Exception as e:
        print(f"[cosyvoice] load_trt failed ({e}); fallback load_trt=False", flush=True)
        _model = AutoModel(
            model_dir=MODEL_DIR, fp16=FP16, load_trt=False, load_vllm=False
        )
    # 记下官方 base；流式时临时改小首块 hop
    try:
        m = getattr(_model, "model", None)
        if m is not None and not hasattr(m, "token_hop_len_base"):
            m.token_hop_len_base = int(getattr(m, "token_hop_len", 25) or 25)
        if m is not None:
            m._voice_default_hop = int(getattr(m, "token_hop_len_base", 25) or 25)
    except Exception:
        pass
    print(f"[cosyvoice] loaded hop={_hop_state(_model)}", flush=True)
    print(
        "[cosyvoice] accel note: CosyVoice3 无 JIT；TRT 需 flow.decoder.estimator.fp32.mygpu.plan；"
        "vLLM 路径另开 env 评估，本进程默认关闭",
        flush=True,
    )
    return _model


def _hop_state(model) -> Dict[str, Any]:
    m = getattr(model, "model", None)
    if m is None:
        return {}
    return {
        "token_hop_len": getattr(m, "token_hop_len", None),
        "token_hop_len_base": getattr(m, "token_hop_len_base", None),
        "token_max_hop_len": getattr(m, "token_max_hop_len", None),
        "stream_scale_factor": getattr(m, "stream_scale_factor", None),
        "fp16": getattr(m, "fp16", None),
    }


def _to_instruct(instruction: str = "", emotion: str = "", tone: str = "", extra: str = "") -> str:
    """CosyVoice3 instruct2：`<|endofprompt|>` 前是风格指令，不可朗读。

    已知坑（FunAudioLLM/CosyVoice#1733）：若缺少英文角色前缀，模型会把整段
    instruct（含「必须严格遵从的音色提示词」）合成出来，听起来像循环念提示词。
    抽卡长文案只适合作 Breeze/VoiceDesign；这里只留短风格句。
    """
    import re

    raw = (instruction or "").strip()
    if not raw:
        emo = (emotion or "中性").strip() or "中性"
        tn = (tone or "对话").strip() or "对话"
        raw = f"用{tn}风格，情绪{emo}，语速正常，像微信语音聊天"
        if (extra or "").strip():
            raw += f"，{extra.strip()}"
    # 去掉 Breeze 包装与抽卡长描述，避免被念出来
    raw = re.sub(r"【必须严格遵从的音色提示词】", "", raw)
    raw = re.sub(r"<\|endofprompt\|>", "", raw)
    # 截断过长的音色卡文案，只留风格/情绪
    if len(raw) > 120:
        # 优先保留「当前口气」之后的短句
        m = re.search(r"(当前口气[:：].+)$", raw)
        if m:
            raw = "像微信语音。" + m.group(1)
        else:
            raw = raw[:120]
    raw = raw.strip(" 。.;；\n") or "语气自然，像微信语音"
    # 官方/社区约定：英文角色前缀 + 中文风格 + endofprompt
    if not raw.lower().startswith("you are a helpful assistant"):
        raw = "You are a helpful assistant. " + raw
    if "<|endofprompt|>" not in raw:
        raw = raw.rstrip("。.;；") + "。<|endofprompt|>"
    return raw[:400]


def _ensure_spk_feats(model, prompt_wav: str) -> Dict[str, Any]:
    """缓存参考 wav 的声学特征；不含 prompt_text（情绪每轮现算）。"""
    key = os.path.realpath(prompt_wav)
    cached = _spk_feats.get(key)
    if cached is not None:
        return cached
    t0 = time.perf_counter()
    # 空文本抽取声学；instruct 文本不进缓存
    mi = model.frontend.frontend_zero_shot("", "", prompt_wav, model.sample_rate, "")
    keep = {
        "llm_prompt_speech_token": mi["llm_prompt_speech_token"],
        "llm_prompt_speech_token_len": mi["llm_prompt_speech_token_len"],
        "flow_prompt_speech_token": mi["flow_prompt_speech_token"],
        "flow_prompt_speech_token_len": mi["flow_prompt_speech_token_len"],
        "prompt_speech_feat": mi["prompt_speech_feat"],
        "prompt_speech_feat_len": mi["prompt_speech_feat_len"],
        "llm_embedding": mi["llm_embedding"],
        "flow_embedding": mi["flow_embedding"],
    }
    _spk_feats[key] = keep
    print(
        f"[cosyvoice] spk_feats_cached path={key} ms={round((time.perf_counter()-t0)*1000)} "
        f"flow_token={int(keep['flow_prompt_speech_token_len'])}",
        flush=True,
    )
    return keep


def _build_instruct2_input(model, text: str, instruct: str, prompt_wav: str) -> Dict[str, Any]:
    """声学走缓存；text + instruct(prompt_text) 每轮现算。"""
    feats = _ensure_spk_feats(model, prompt_wav)
    tts_text_token, tts_text_token_len = model.frontend._extract_text_token(text)
    prompt_text_token, prompt_text_token_len = model.frontend._extract_text_token(instruct)
    model_input = {
        **feats,
        "text": tts_text_token,
        "text_len": tts_text_token_len,
        "prompt_text": prompt_text_token,
        "prompt_text_len": prompt_text_token_len,
    }
    # 与 frontend_instruct2 一致：去掉 llm 侧 prompt speech token
    model_input.pop("llm_prompt_speech_token", None)
    model_input.pop("llm_prompt_speech_token_len", None)
    return model_input


def _iter_instruct2_stream(
    model,
    text: str,
    instruct: str,
    prompt_wav: str,
    speed: float = 1.0,
    first_hop: int = 0,
):
    """按 CosyVoice text_normalize 分句后流式 tts；首块用更小 hop。"""
    m = getattr(model, "model", None)
    default_hop = 25
    if m is not None:
        default_hop = int(
            getattr(m, "_voice_default_hop", None)
            or getattr(m, "token_hop_len_base", None)
            or 25
        )
    use_hop = int(first_hop or FIRST_HOP or 0)
    for piece in model.frontend.text_normalize(text, split=True, text_frontend=True):
        # 单请求局部：首块更小 hop，结束后恢复，不污染后续请求
        if m is not None and use_hop > 0 and use_hop < default_hop:
            m.token_hop_len_base = use_hop
            m.token_hop_len = use_hop
        hop_before = _hop_state(model)
        t_pre0 = time.perf_counter()
        model_input = _build_instruct2_input(model, piece, instruct, prompt_wav)
        pre_ms = round((time.perf_counter() - t_pre0) * 1000)
        print(
            f"[cosyvoice] piece={piece!r} pre_ms={pre_ms} hop_before={hop_before} "
            f"first_hop={use_hop}",
            flush=True,
        )
        t_infer0 = time.perf_counter()
        first = True
        n = 0
        audio_total = 0.0
        try:
            for out in model.model.tts(**model_input, stream=True, speed=speed):
                n += 1
                now = time.perf_counter()
                speech = out["tts_speech"]
                audio_s = float(speech.shape[-1]) / float(model.sample_rate)
                audio_total += audio_s
                if first:
                    first = False
                    print(
                        f"[cosyvoice] first_yield_ms={round((now-t_infer0)*1000)} "
                        f"first_audio_s={round(audio_s,3)} hop_now={_hop_state(model)}",
                        flush=True,
                    )
                else:
                    print(
                        f"[cosyvoice] chunk={n} since_infer_ms={round((now-t_infer0)*1000)} "
                        f"audio_s={round(audio_s,3)}",
                        flush=True,
                    )
                yield out
        finally:
            if m is not None:
                m.token_hop_len_base = default_hop
                m.token_hop_len = default_hop
        wall = time.perf_counter() - t_infer0
        rtf = (wall / audio_total) if audio_total > 0 else None
        print(
            f"[cosyvoice] piece_done chunks={n} hop_after={_hop_state(model)} "
            f"audio_s={round(audio_total,3)} wall_s={round(wall,3)} "
            f"rtf={round(rtf,3) if rtf else None}",
            flush=True,
        )


@app.get("/health")
def health():
    hop = {}
    try:
        if _model is not None:
            hop = _hop_state(_model)
    except Exception:
        pass
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "prompt_wav": PROMPT_WAV,
        "engine": "fun-cosyvoice3-0.5b",
        "fp16_config": FP16,
        "spk_cached": list(_spk_feats.keys()),
        "hop": hop,
    }


@app.post("/unload")
def unload():
    _unload()
    return {"ok": True, "model_loaded": False}


@app.post("/v1/warm")
def warm(prompt_wav: str = Form("")):
    """通话进页预热：加载模型 + 缓存固定声线声学特征（不计用户开口后的等待）。"""
    ref = (prompt_wav or "").strip() or PROMPT_WAV
    if not os.path.isfile(ref):
        raise HTTPException(500, f"prompt wav missing: {ref}")
    t0 = time.perf_counter()
    with _lock:
        model = _get_model()
        _ensure_spk_feats(model, ref)
        # 一次极短推理把 CUDA kernel 热开（可选）
        try:
            instruct = _to_instruct("自然短促地应答一声。")
            for _ in _iter_instruct2_stream(model, "嗯。", instruct, ref, speed=1.0):
                break
        except Exception as e:
            print(f"[cosyvoice] warm probe warn: {e}", flush=True)
        hop = _hop_state(model)
        _arm()
    return {
        "ok": True,
        "warm_ms": round((time.perf_counter() - t0) * 1000),
        "prompt_wav": os.path.realpath(ref),
        "hop": hop,
        "spk_cached": True,
    }


@app.post("/v1/audio/speech")
async def speech(
    text: str = Form(...),
    instruction: str = Form(""),
    emotion: str = Form("中性"),
    tone: str = Form("对话"),
    speed: str = Form("正常"),
    extra: str = Form(""),
    prompt_wav: str = Form(""),
):
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    speed_map = {"慢": 0.85, "正常": 1.0, "快": 1.15}
    sp = speed_map.get((speed or "正常").strip(), 1.0)
    instruct = _to_instruct(instruction, emotion, tone, extra)
    ref = (prompt_wav or "").strip() or PROMPT_WAV
    if not os.path.isfile(ref):
        raise HTTPException(500, f"prompt wav missing: {ref}")
    try:
        with _lock:
            model = _get_model()
            chunks = []
            for j in _iter_instruct2_stream(model, text[:500], instruct, ref, speed=sp):
                chunks.append(j["tts_speech"])
            if not chunks:
                raise RuntimeError("no audio")
            wav = torch.cat(chunks, dim=1)
            buf = io.BytesIO()
            torchaudio.save(buf, wav.cpu(), model.sample_rate, format="wav")
            data = buf.getvalue()
            _arm()
        return Response(content=data, media_type="audio/wav")
    except Exception as e:
        raise HTTPException(502, f"cosyvoice failed: {e}") from e


@app.post("/v1/audio/speech/stream")
async def speech_stream(
    text: str = Form(...),
    instruction: str = Form(""),
    emotion: str = Form("中性"),
    tone: str = Form("对话"),
    speed: str = Form("正常"),
    extra: str = Form(""),
    prompt_wav: str = Form(""),
    first_hop: str = Form(""),
):
    """真流式：每块 PCM s16le 立刻下发。帧=4字节大端长度+PCM。"""
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    speed_map = {"慢": 0.85, "正常": 1.0, "快": 1.15}
    sp = speed_map.get((speed or "正常").strip(), 1.0)
    instruct = _to_instruct(instruction, emotion, tone, extra)
    ref = (prompt_wav or "").strip() or PROMPT_WAV
    if not os.path.isfile(ref):
        raise HTTPException(500, f"prompt wav missing: {ref}")
    try:
        hop_override = int(str(first_hop or "").strip() or "0")
    except Exception:
        hop_override = 0

    sr_hdr = "24000"
    try:
        with _lock:
            model = _get_model()
            sr_hdr = str(int(model.sample_rate))
            _ensure_spk_feats(model, ref)
    except Exception:
        pass

    def gen():
        t0 = time.perf_counter()
        first = None
        n = 0
        with _lock:
            model = _get_model()
            sr = int(model.sample_rate)
            hop0 = _hop_state(model)
            print(f"[cosyvoice-stream] start hop={hop0} text={text[:40]!r}", flush=True)
            for j in _iter_instruct2_stream(
                model, text[:500], instruct, ref, speed=sp, first_hop=hop_override
            ):
                wav = j["tts_speech"]
                pcm = (wav.clamp(-1, 1).squeeze(0).cpu() * 32767.0).short().numpy().tobytes()
                if not pcm:
                    continue
                n += 1
                audio_s = len(pcm) / 2 / sr
                now = time.perf_counter()
                if first is None:
                    first = now
                    print(
                        f"[cosyvoice-stream] first_pcm_ms={round((first-t0)*1000)} "
                        f"first_audio_s={round(audio_s,3)} sr={sr}",
                        flush=True,
                    )
                print(
                    f"[cosyvoice-stream] chunk={n} ms={round((now-t0)*1000)} "
                    f"audio_s={round(audio_s,3)} bytes={len(pcm)}",
                    flush=True,
                )
                yield struct.pack(">I", len(pcm)) + pcm
            print(
                f"[cosyvoice-stream] last_ms={round((time.perf_counter()-t0)*1000)} "
                f"chunks={n} hop_end={_hop_state(model)}",
                flush=True,
            )
            _arm()

    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": sr_hdr,
            "X-Audio-Format": "s16le",
            "X-Frame": "lenprefixed",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8885")))
