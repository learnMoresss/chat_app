"""入站语音转写：优先 OpenAI 兼容 /v1/audio/transcriptions，其次本机 whisper 子进程。"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from .config import get_settings


def transcribe_audio_path(path: str | Path, *, language: str = "zh") -> Optional[str]:
    """返回转写文本；失败返回 None（不抛，避免堵发送）。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 64:
        return None
    text = _via_http(p, language=language)
    if text:
        return text
    return _via_local_whisper(p, language=language)


def _via_http(path: Path, *, language: str) -> Optional[str]:
    settings = get_settings()
    base = (settings.asr_transcriptions_url or "").rstrip("/")
    if not base:
        return None
    url = (
        base
        if base.endswith("/v1/audio/transcriptions")
        else f"{base}/v1/audio/transcriptions"
    )
    to = float(settings.asr_timeout_seconds or 120)
    headers = {}
    token = (settings.asr_api_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with path.open("rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            data = {
                "model": settings.asr_model or "whisper-1",
                "language": language,
            }
            with httpx.Client(timeout=to) as client:
                r = client.post(url, headers=headers, files=files, data=data)
            if r.status_code >= 400:
                return None
            j = r.json()
            text = (j.get("text") or "").strip()
            if text.startswith("[asr-stub]"):
                return None
            return text or None
    except Exception:
        return None


def _via_local_whisper(path: Path, *, language: str) -> Optional[str]:
    settings = get_settings()
    py = (settings.asr_whisper_python or "").strip()
    if not py:
        return None
    model = (settings.asr_whisper_model or "medium").strip() or "medium"
    to = float(settings.asr_timeout_seconds or 120)
    # 一次性加载模型较慢，但无需常驻服务；结果写临时 txt 再读
    script = r"""
import sys
from pathlib import Path
import whisper
audio = sys.argv[1]
model_name = sys.argv[2]
lang = sys.argv[3]
out = Path(sys.argv[4])
m = whisper.load_model(model_name)
kw = {"task": "transcribe"}
if lang:
    kw["language"] = lang
r = m.transcribe(audio, **kw)
text = (r.get("text") or "").strip()
out.write_text(text, encoding="utf-8")
"""
    try:
        with tempfile.TemporaryDirectory(prefix="asr_") as td:
            out = Path(td) / "out.txt"
            proc = subprocess.run(
                [py, "-c", script, str(path.resolve()), model, language or "", str(out)],
                capture_output=True,
                text=True,
                timeout=to,
                check=False,
            )
            if proc.returncode != 0 or not out.is_file():
                return None
            text = out.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:
        return None
