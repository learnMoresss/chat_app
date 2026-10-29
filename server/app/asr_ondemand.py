"""SenseVoice ASR 按需启停：点打电话时拉起 :8886，闲置后停掉，不常驻。"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Optional

import httpx

from .config import get_settings

log = logging.getLogger("asr_ondemand")

_lock = threading.Lock()
_stop_timer: Optional[threading.Timer] = None
_last_used: float = 0.0
_starting: bool = False

PROCESS_IDLE_SECONDS = 180.0
ENSURE_TIMEOUT = 120.0

_START_SCRIPT = "/data/ai/services/asr-gateways/start-funasr.sh"
_LOCAL_START = os.path.join(
    os.path.dirname(__file__), "..", "deploy", "asr-gateways", "start-funasr.sh"
)


def _asr_base() -> str:
    settings = get_settings()
    url = (settings.asr_transcriptions_url or "http://127.0.0.1:8886").strip().rstrip("/")
    for suffix in ("/v1/audio/transcriptions", "/v1/audio", "/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url or "http://127.0.0.1:8886"


def is_live(*, timeout: float = 1.2) -> bool:
    base = _asr_base()
    try:
        with httpx.Client(timeout=timeout) as client:
            for path in ("/health", "/", "/v1/models"):
                try:
                    r = client.get(f"{base}{path}")
                    if r.status_code < 500:
                        return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def can_start() -> bool:
    if os.path.isfile(_START_SCRIPT):
        return True
    if os.path.isfile(os.path.abspath(_LOCAL_START)):
        return True
    # 网关代码 + 环境在即可
    return os.path.isfile("/data/ai/envs/funasr-voice/bin/python") and os.path.isdir(
        "/data/ai/services/asr-gateways"
    )


def _run(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _start() -> None:
    script = _START_SCRIPT if os.path.isfile(_START_SCRIPT) else os.path.abspath(_LOCAL_START)
    if os.path.isfile(script):
        log.info("asr ondemand start via %s", script)
        _run(["bash", script], timeout=30.0)
        return
    # 兜底：直接起 uvicorn
    log.info("asr ondemand start via uvicorn fallback")
    _run(
        [
            "bash",
            "-lc",
            r"""
set +e
export CUDA_VISIBLE_DEVICES="${FUNASR_GPU:-0}"
export FUNASR_DEVICE=cuda:0
export FUNASR_MODEL="${FUNASR_MODEL:-iic/SenseVoiceSmall}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-/data/ai/models/audio/modelscope-cache}"
mkdir -p "$MODELSCOPE_CACHE" /data/ai/logs/audio
cd /data/ai/services/asr-gateways
if curl -sf -m 2 http://127.0.0.1:8886/health >/dev/null; then exit 0; fi
pkill -f 'funasr_gateway:app' 2>/dev/null || true
pkill -f 'funasr_gateway.py' 2>/dev/null || true
sleep 1
setsid nohup /data/ai/envs/funasr-voice/bin/python -c \
  'import uvicorn; uvicorn.run("funasr_gateway:app", host="127.0.0.1", port=8886)' \
  >>/data/ai/logs/audio/funasr-gateway.log 2>&1 < /dev/null &
""",
        ],
        timeout=30.0,
    )


def _stop() -> None:
    _run(
        [
            "bash",
            "-lc",
            "pkill -f 'funasr_gateway:app' 2>/dev/null || true; "
            "pkill -f 'funasr_gateway.py' 2>/dev/null || true; "
            "systemctl --user stop chat-asr-sensevoice.service 2>/dev/null || true",
        ],
        timeout=30.0,
    )
    log.info("asr ondemand stopped")


def _wait_ready(timeout: float = ENSURE_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_live(timeout=2.0):
            return True
        time.sleep(1.0)
    return False


def ensure_asr(*, timeout: float = ENSURE_TIMEOUT) -> bool:
    """点打电话时调用：已活着则续命；否则拉起并等到 health。"""
    global _last_used, _stop_timer, _starting
    with _lock:
        if _stop_timer:
            _stop_timer.cancel()
            _stop_timer = None
        if is_live():
            _last_used = time.time()
            return True
        if not can_start():
            log.warning("asr cannot start: missing gateway/env")
            return False
        if _starting:
            # 另一个 warm 正在起，外面再等
            pass
        else:
            _starting = True
            try:
                _start()
            finally:
                _starting = False
    ok = _wait_ready(timeout=timeout)
    with _lock:
        if ok:
            _last_used = time.time()
            log.info("asr ondemand ready")
        else:
            log.warning("asr ondemand start timeout")
    return ok


def touch_asr() -> None:
    """识别成功后续命，推迟闲置停机。"""
    global _last_used, _stop_timer
    with _lock:
        _last_used = time.time()
        if _stop_timer:
            _stop_timer.cancel()
            _stop_timer = None


def release_asr(*, unload_now: bool = False) -> None:
    """挂断后调用：默认延迟停；unload_now 立刻停。"""
    global _last_used, _stop_timer
    with _lock:
        _last_used = time.time()
        if _stop_timer:
            _stop_timer.cancel()
            _stop_timer = None
        if unload_now:
            _stop()
            return
        timer = threading.Timer(PROCESS_IDLE_SECONDS, _delayed_stop)
        timer.daemon = True
        _stop_timer = timer
        timer.start()


def _delayed_stop() -> None:
    global _stop_timer
    with _lock:
        if time.time() - _last_used < PROCESS_IDLE_SECONDS - 1:
            return
        if is_live():
            _stop()
        _stop_timer = None
