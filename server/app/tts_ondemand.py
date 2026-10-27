"""TTS 按需启停：用哪个拉哪个，合成后卸模型，闲置停进程。"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Dict, Optional

import httpx

from .config import get_settings

log = logging.getLogger("tts_ondemand")

_lock = threading.Lock()
_stop_timers: Dict[str, threading.Timer] = {}
_last_used: Dict[str, float] = {}

PROCESS_IDLE_SECONDS = 180.0
ENSURE_TIMEOUT = 240.0

ENGINE_IDS = ("breeze", "voicedesign", "customvoice", "indextts", "cosyvoice")


def _urls() -> Dict[str, str]:
    s = get_settings()
    return {
        "breeze": (getattr(s, "tts_breeze_url", None) or "http://127.0.0.1:8883").rstrip("/"),
        "voicedesign": (getattr(s, "tts_voicedesign_url", None) or "http://127.0.0.1:8882").rstrip("/"),
        "customvoice": (s.tts_customvoice_url or "http://127.0.0.1:8881").rstrip("/"),
        "indextts": (getattr(s, "tts_indextts_url", None) or "http://127.0.0.1:8884").rstrip("/"),
        "cosyvoice": (getattr(s, "tts_cosyvoice_url", None) or "http://127.0.0.1:8885").rstrip("/"),
    }


def _port_up(base: str, paths: Optional[list] = None, timeout: float = 1.5) -> bool:
    paths = paths or ["/health", "/v1/model", "/v1/models"]
    try:
        with httpx.Client(timeout=timeout) as client:
            for p in paths:
                try:
                    r = client.get(f"{base}{p}")
                    if r.status_code < 500:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def _post_unload(base: str) -> None:
    try:
        with httpx.Client(timeout=30.0) as client:
            client.post(f"{base}/unload")
    except Exception as e:
        log.debug("unload %s failed: %s", base, e)


def unload_others(except_engine: str) -> None:
    urls = _urls()
    for eng, base in urls.items():
        if eng == except_engine:
            continue
        if _port_up(base):
            _post_unload(base)


def _run(cmd: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _start_customvoice() -> None:
    unload_others("customvoice")
    _run(
        [
            "bash",
            "-lc",
            r"""
set +e
systemctl --user stop comfyui.service 2>/dev/null || true
if [ -f /data/ai/config/gateway.env ]; then
  set -a; . /data/ai/config/gateway.env; set +a
  curl -s -m 5 -X POST -H "Authorization: Bearer ${AI_GATEWAY_API_KEY}" \
    http://127.0.0.1:9292/api/models/unload >/dev/null || true
fi
export HOST=127.0.0.1 PORT=8881
export TTS_CUSTOMVOICE_MODEL_PATH=/data/ai/models/audio/qwen3-tts-1.7b-customvoice
export QWEN_TTS_DEVICE=cuda:1 QWEN_TTS_DTYPE=float16 QWEN_TTS_ATTN=sdpa PYTHONUNBUFFERED=1
if curl -sf -m 2 http://127.0.0.1:8881/health >/dev/null; then exit 0; fi
systemctl --user reset-failed qwen3-tts-api.service 2>/dev/null || true
if ! systemctl --user start qwen3-tts-api.service; then
  pkill -f '/data/ai/apps/qwen3-audio-api/main.py' 2>/dev/null || true
  sleep 1
  cd /data/ai/apps/qwen3-audio-api
  setsid nohup /data/ai/envs/qwen3-tts/bin/python /data/ai/apps/qwen3-audio-api/main.py \
    >>/data/ai/logs/audio/qwen3-tts-api-ondemand.log 2>&1 < /dev/null &
fi
""",
        ],
        timeout=90.0,
    )


def _stop_customvoice() -> None:
    _run(
        [
            "bash",
            "-lc",
            "systemctl --user stop qwen3-tts-api.service 2>/dev/null || true; "
            "pkill -f '/data/ai/apps/qwen3-audio-api/main.py' 2>/dev/null || true",
        ],
        timeout=60.0,
    )


def _start_breeze() -> None:
    unload_others("breeze")
    _run(
        [
            "bash",
            "-lc",
            r"""
set +e
ROOT=/data/ai/services/breeze-tts
PY=/data/ai/envs/breeze-tts/bin/python
CKPT=/data/ai/models/audio/breeze-tts-2
LOG=/data/ai/logs/audio/breeze-tts-api.log
if [ ! -x "$PY" ] || [ ! -d "$ROOT" ] || [ ! -d "$CKPT" ]; then
  echo "breeze not installed" >&2
  exit 1
fi
if curl -sf -m 2 http://127.0.0.1:8883/v1/model >/dev/null; then exit 0; fi
pkill -f 'breeze_infer.api' 2>/dev/null || true
sleep 1
cd "$ROOT"
export CUDA_VISIBLE_DEVICES="${BREEZE_GPU:-1}"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
setsid nohup "$PY" -m breeze_infer.api "$CKPT" --host 0.0.0.0 --port 8883 \
  >>"$LOG" 2>&1 < /dev/null &
""",
        ],
        timeout=30.0,
    )


def _stop_breeze() -> None:
    _run(["bash", "-lc", "pkill -f 'breeze_infer.api' 2>/dev/null || true"], timeout=30.0)


def _start_voicedesign() -> None:
    unload_others("voicedesign")
    if _port_up(_urls()["voicedesign"]):
        return
    _run(
        [
            "bash",
            "-lc",
            r"""
set +e
cd /data/ai/services/dramaclaw-voicedesign
export VOICE_DESIGN_IDLE_UNLOAD_SECONDS=120
if curl -sf -m 2 http://127.0.0.1:8882/health >/dev/null; then exit 0; fi
pkill -f 'voicedesign_service:app' 2>/dev/null || true
sleep 1
setsid nohup /data/ai/envs/qwen3-tts-voicedesign/bin/uvicorn voicedesign_service:app \
  --host 0.0.0.0 --port 8882 --workers 1 \
  >>/data/ai/logs/audio/voicedesign-ondemand.log 2>&1 < /dev/null &
""",
        ],
        timeout=30.0,
    )


def _start_indextts() -> None:
    unload_others("indextts")
    _run(["bash", "/data/ai/services/tts-gateways/start-indextts.sh"], timeout=30.0)


def _stop_indextts() -> None:
    _run(
        [
            "bash",
            "-lc",
            "pkill -f 'indextts_gateway:app' 2>/dev/null || true; pkill -f indextts_gateway.py 2>/dev/null || true",
        ],
        timeout=30.0,
    )


def _start_cosyvoice() -> None:
    unload_others("cosyvoice")
    _run(["bash", "/data/ai/services/tts-gateways/start-cosyvoice.sh"], timeout=30.0)


def _stop_cosyvoice() -> None:
    _run(
        [
            "bash",
            "-lc",
            "pkill -f 'cosyvoice_gateway:app' 2>/dev/null || true; pkill -f cosyvoice_gateway.py 2>/dev/null || true",
        ],
        timeout=30.0,
    )


def _wait_ready(engine: str, timeout: float = ENSURE_TIMEOUT) -> bool:
    urls = _urls()
    base = urls[engine]
    paths = {
        "breeze": ["/v1/model", "/health"],
        "voicedesign": ["/health"],
        "customvoice": ["/health", "/v1/models"],
        "indextts": ["/health"],
        "cosyvoice": ["/health"],
    }.get(engine, ["/health"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_up(base, paths=paths, timeout=2.0):
            return True
        time.sleep(1.5)
    return False


def can_start(engine: str) -> bool:
    if engine == "customvoice":
        return os.path.isfile("/data/ai/apps/qwen3-audio-api/main.py")
    if engine == "voicedesign":
        return os.path.isdir("/data/ai/services/dramaclaw-voicedesign")
    if engine == "breeze":
        ckpt = "/data/ai/models/audio/breeze-tts-2"
        if not os.path.isdir("/data/ai/services/breeze-tts"):
            return False
        # 主模型分片齐了才算就绪（audio_tokenizer 单独大文件不够）
        shards = (
            "model-00001-of-00002.safetensors",
            "model-00002-of-00002.safetensors",
        )
        try:
            for name in shards:
                path = os.path.join(ckpt, name)
                if not os.path.isfile(path) or os.path.getsize(path) < 100 * 1024 * 1024:
                    return False
            return True
        except OSError:
            return False
    if engine == "indextts":
        return os.path.isdir("/data/ai/apps/index-tts/checkpoints") and os.path.isfile(
            "/data/ai/services/tts-gateways/start-indextts.sh"
        )
    if engine == "cosyvoice":
        return os.path.isdir("/data/ai/models/audio/fun-cosyvoice3-0.5b-2512") and os.path.isfile(
            "/data/ai/services/tts-gateways/start-cosyvoice.sh"
        )
    return False


def engine_status(engine: str) -> Dict[str, str]:
    urls = _urls()
    base = urls.get(engine) or ""
    if not base:
        return {"status": "soon", "status_text": "未配置"}
    if _port_up(base):
        return {"status": "ready", "status_text": "可试听（按需加载权重）"}
    if can_start(engine):
        return {"status": "standby", "status_text": "按需待命 · 点合成时自动启动"}
    if engine == "breeze":
        return {
            "status": "soon",
            "status_text": "权重下载中（未完成不能合成）。请先用其它引擎。",
        }
    return {"status": "offline", "status_text": "无法启动"}


_STARTERS = {
    "customvoice": _start_customvoice,
    "breeze": _start_breeze,
    "voicedesign": _start_voicedesign,
    "indextts": _start_indextts,
    "cosyvoice": _start_cosyvoice,
}
_STOPPERS = {
    "customvoice": _stop_customvoice,
    "breeze": _stop_breeze,
    "indextts": _stop_indextts,
    "cosyvoice": _stop_cosyvoice,
}


def ensure_engine(engine: str) -> None:
    if engine not in ENGINE_IDS:
        return
    with _lock:
        t = _stop_timers.pop(engine, None)
        if t:
            t.cancel()
        unload_others(engine)
        base = _urls()[engine]
        if not _port_up(base):
            starter = _STARTERS.get(engine)
            if not starter:
                raise RuntimeError(f"{engine} 无启动器")
            starter()
            # Breeze 冷启动加载权重很慢
            wait = 420.0 if engine == "breeze" else ENSURE_TIMEOUT
            if not _wait_ready(engine, timeout=wait):
                raise RuntimeError(f"{engine} 启动超时，请稍后重试")
        _last_used[engine] = time.time()


def _delayed_stop(engine: str) -> None:
    with _lock:
        last = _last_used.get(engine, 0)
        if time.time() - last < PROCESS_IDLE_SECONDS - 1:
            return
        base = _urls().get(engine) or ""
        if base and _port_up(base):
            _post_unload(base)
        stopper = _STOPPERS.get(engine)
        if stopper:
            stopper()
        _stop_timers.pop(engine, None)
        log.info("ondemand stopped/unloaded %s", engine)


def release_engine(engine: str, *, unload_now: bool = True) -> None:
    if engine not in ENGINE_IDS:
        return
    with _lock:
        _last_used[engine] = time.time()
        base = _urls()[engine]
        if unload_now and _port_up(base):
            _post_unload(base)
        old = _stop_timers.pop(engine, None)
        if old:
            old.cancel()
        timer = threading.Timer(PROCESS_IDLE_SECONDS, _delayed_stop, args=(engine,))
        timer.daemon = True
        _stop_timers[engine] = timer
        timer.start()
