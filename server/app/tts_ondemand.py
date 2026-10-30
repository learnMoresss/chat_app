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

PROCESS_IDLE_SECONDS = 120.0
# 抽卡页预热后希望多留一会，避免刚点「抽音色」又冷启；但别太久占 GPU
DRAW_IDLE_SECONDS = 300.0
ENSURE_TIMEOUT = 240.0

ENGINE_IDS = ("breeze", "voicedesign", "customvoice", "indextts", "cosyvoice")


def _urls() -> Dict[str, str]:
    s = get_settings()
    return {
        # 8883 被 dramaclaw-tts（qwen gateway）占用；Breeze 独立端口
        "breeze": (getattr(s, "tts_breeze_url", None) or "http://127.0.0.1:8890").rstrip("/"),
        "voicedesign": (getattr(s, "tts_voicedesign_url", None) or "http://127.0.0.1:8882").rstrip("/"),
        "customvoice": (s.tts_customvoice_url or "http://127.0.0.1:8881").rstrip("/"),
        "indextts": (getattr(s, "tts_indextts_url", None) or "http://127.0.0.1:8884").rstrip("/"),
        "cosyvoice": (getattr(s, "tts_cosyvoice_url", None) or "http://127.0.0.1:18885").rstrip("/"),
    }


def _port_up(base: str, paths: Optional[list] = None, timeout: float = 1.5) -> bool:
    paths = paths or ["/health", "/v1/model", "/v1/models"]
    try:
        with httpx.Client(timeout=timeout) as client:
            for p in paths:
                try:
                    r = client.get(f"{base}{p}")
                    # 只认 2xx：404/405 不能当“引擎已就绪”（8883 上 dramaclaw 会误伤 Breeze）
                    if not (200 <= r.status_code < 300):
                        continue
                    # 8885 上常见 BaseHTTP 假 CosyVoice：health 200 但不会真合成
                    srv = (r.headers.get("server") or "").lower()
                    if "basehttp" in srv:
                        return False
                    if ":8885" in base or ":18885" in base:
                        try:
                            # 真 FastAPI：GET /v1/warm → 405；假桩 → 404
                            w = client.get(f"{base}/v1/warm")
                            if w.status_code == 404:
                                return False
                        except Exception:
                            return False
                    # Breeze：只认 /v1/model（含 model_digest/frame_rate）；禁止拿 /health 冒充
                    if ":8890" in base:
                        if p != "/v1/model":
                            continue
                        body = (r.text or "").lower()
                        if (
                            "model_digest" not in body
                            and "frame_rate" not in body
                            and "breeze" not in body
                        ):
                            continue
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


def _run(
    cmd: list[str],
    timeout: float = 120.0,
    env: Optional[dict] = None,
    *,
    detach_stdio: bool = False,
) -> subprocess.CompletedProcess:
    """跑 shell。启动常驻服务时必须 detach_stdio=True：
    capture_output 会让 nohup 子进程继承管道，父进程等到 timeout 后杀进程组，
    刚拉起的 Breeze 会被一起杀掉，表现为「没启动就直接合成失败」。
    """
    if detach_stdio:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            env=env,
            start_new_session=True,
        )
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


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
  ps -ef | awk '/qwen3-audio-api\/main.py/ && !/awk/ {print $2}' | xargs -r kill 2>/dev/null || true
  sleep 1
  cd /data/ai/apps/qwen3-audio-api
  nohup /data/ai/envs/qwen3-tts/bin/python /data/ai/apps/qwen3-audio-api/main.py \
    >>/data/ai/logs/audio/qwen3-tts-api-ondemand.log 2>&1 < /dev/null &
  disown 2>/dev/null || true
fi
exit 0
""",
        ],
        timeout=90.0,
        detach_stdio=True,
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
    # detach_stdio：避免 capture 管道拖死 nohup，timeout 杀进程组时带走刚启动的 Breeze
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
  echo "breeze not installed" >>"$LOG" 2>/dev/null
  exit 1
fi
PORT="${BREEZE_PORT:-8890}"
if curl -sf -m 2 "http://127.0.0.1:${PORT}/v1/model" >/dev/null; then exit 0; fi
# 只杀占用目标端口的旧进程，避免误伤
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
fi
ps -ef | awk '/breeze_infer\.api/ && !/awk/ {print $2}' | xargs -r kill 2>/dev/null || true
sleep 1
cd "$ROOT" || exit 1
export CUDA_VISIBLE_DEVICES="${BREEZE_GPU:-1}"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1
nohup "$PY" -m breeze_infer.api "$CKPT" --host 127.0.0.1 --port "$PORT" \
  >>"$LOG" 2>&1 < /dev/null &
echo "started pid=$! port=$PORT $(date -Is)" >>"$LOG"
disown 2>/dev/null || true
exit 0
""",
        ],
        timeout=20.0,
        detach_stdio=True,
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
cd /data/ai/services/dramaclaw-voicedesign || exit 1
export VOICE_DESIGN_IDLE_UNLOAD_SECONDS=120
if curl -sf -m 2 http://127.0.0.1:8882/health >/dev/null; then exit 0; fi
ps -ef | awk '/voicedesign_service:app/ && !/awk/ {print $2}' | xargs -r kill 2>/dev/null || true
sleep 1
nohup /data/ai/envs/qwen3-tts-voicedesign/bin/uvicorn voicedesign_service:app \
  --host 127.0.0.1 --port 8882 --workers 1 \
  >>/data/ai/logs/audio/voicedesign-ondemand.log 2>&1 < /dev/null &
disown 2>/dev/null || true
exit 0
""",
        ],
        timeout=30.0,
        detach_stdio=True,
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
    # 默认 18885：8885 常被 product-gateway 的 BaseHTTP 假 CosyVoice 占着
    env = dict(os.environ)
    if "COSYVOICE_PORT" not in env:
        # 从 URL 解析端口
        try:
            from urllib.parse import urlparse

            u = urlparse(_urls()["cosyvoice"])
            if u.port:
                env["COSYVOICE_PORT"] = str(u.port)
        except Exception:
            env["COSYVOICE_PORT"] = "18885"
    _run(
        ["bash", "/data/ai/services/tts-gateways/start-cosyvoice.sh"],
        timeout=30.0,
        env=env,
    )


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
    t0 = time.time()
    cold = False
    # 启动脚本在锁内跑完即返回；就绪轮询放锁外，避免冷启 1 分钟卡住其它 TTS
    with _lock:
        t = _stop_timers.pop(engine, None)
        if t:
            t.cancel()
        base = _urls()[engine]
        already = _port_up(base)
        if already:
            _last_used[engine] = time.time()
            log.info(
                "ensure_engine engine=%s cold=False ensure_ms=%s",
                engine,
                int((time.time() - t0) * 1000),
            )
            return
        unload_others(engine)
        cold = True
        starter = _STARTERS.get(engine)
        if not starter:
            raise RuntimeError(f"{engine} 无启动器")
        try:
            starter()
        except Exception as e:
            log.warning("start %s failed: %s", engine, e)
            raise RuntimeError(f"{engine} 启动失败: {e}") from e
    wait = 420.0 if engine == "breeze" else ENSURE_TIMEOUT
    if not _wait_ready(engine, timeout=wait):
        raise RuntimeError(f"{engine} 启动超时，请稍后重试")
    with _lock:
        _last_used[engine] = time.time()
    ensure_ms = int((time.time() - t0) * 1000)
    log.info(
        "ensure_engine engine=%s cold=%s ensure_ms=%s",
        engine,
        cold,
        ensure_ms,
    )


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


def release_engine(
    engine: str,
    *,
    unload_now: bool = True,
    idle_seconds: Optional[float] = None,
) -> None:
    if engine not in ENGINE_IDS:
        return
    delay = float(PROCESS_IDLE_SECONDS if idle_seconds is None else idle_seconds)
    with _lock:
        _last_used[engine] = time.time()
        base = _urls()[engine]
        if unload_now and _port_up(base):
            _post_unload(base)
        old = _stop_timers.pop(engine, None)
        if old:
            old.cancel()
        timer = threading.Timer(delay, _delayed_stop, args=(engine,))
        timer.daemon = True
        _stop_timers[engine] = timer
        timer.start()
