"""人设唱歌（确定路线）：sing-pipeline + Seed-VC soft 基线 → 语音条。

在 AI 主机本机直接跑流水线（不再错误地 SSH 回自己）。
音色跟「已固定」人设参考音走；无样音时用 SING_DEFAULT_PERSONA_REF。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import db
from .config import get_settings

log = logging.getLogger("sing_gen")

_ARR_RE = re.compile(r"^(a_cappella|with_backing|清唱|带伴奏)\s*[|｜]\s*(.+)$", re.S)


def parse_sing_prompt(raw: str) -> Tuple[str, str]:
    """返回 (arrangement, title_or_lyrics)。arrangement 默认 a_cappella。"""
    text = (raw or "").strip()
    m = _ARR_RE.match(text)
    if m:
        arr, body = m.group(1).strip(), m.group(2).strip()
        if arr in ("清唱", "a_cappella"):
            return "a_cappella", body
        return "with_backing", body
    return "a_cappella", text


def resolve_persona_ref_local(*, user_id: str, persona_id: str) -> Optional[Path]:
    row = db.fetchone(
        """
        SELECT path FROM voice_profiles
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row and (row["path"] or "").strip():
        p = Path(str(row["path"]))
        if p.is_file():
            return p
    row2 = db.fetchone(
        """
        SELECT path, file_id FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'voice' AND fixed = 1
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row2 is None:
        return None
    path = (row2["path"] or "").strip()
    if path and Path(path).is_file():
        return Path(path)
    fid = (row2["file_id"] or "").strip()
    if not fid:
        return None
    mf = db.fetchone("SELECT path FROM media_files WHERE id = ? AND user_id = ?", (fid, user_id))
    if mf and (mf["path"] or "").strip() and Path(mf["path"]).is_file():
        return Path(mf["path"])
    return None


# 流水线内置别名外的本机缓存曲（cover，无需 ACE）
_LOCAL_SONG_ALIASES = {
    "番茄花": "/data/ai/tmp/sing-fanqiehua/fanqiehua_src.mp3",
}


def _pick_python() -> str:
    # cover/Seed-VC 需要带 comfy_aimdo 的环境；ACE compose 子进程自带 ace-step venv
    for cand in (
        "/data/ai/envs/comfy-cu128/bin/python",
        "/data/ai/envs/torch-cu128/bin/python",
        "/data/ai/venvs/ace-step-candidate/bin/python",
        "/data/ai/apps/ComfyUI/.venv/bin/python",
        "/data/ai/apps/ComfyUI/venv/bin/python",
        "python3",
    ):
        if cand == "python3" or Path(cand).is_file():
            return cand
    return "python3"


def _resolve_local_alias(body: str) -> Optional[Path]:
    key = re.sub(r"\s+", "", (body or "").strip())
    if not key:
        return None
    for name, path in _LOCAL_SONG_ALIASES.items():
        if name in key or key in name:
            p = Path(path)
            if p.is_file():
                return p
    return None


def _write_ace_toml(*, out_dir: Path, lyrics: str, arrangement: str) -> Path:
    """compose 模式：按提示词写一份 ACE text2music 配置。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    ace_dir = out_dir / "ace"
    ace_dir.mkdir(parents=True, exist_ok=True)
    text = (lyrics or "").strip()
    if not text:
        text = "啦啦啦 轻轻唱给你听"
    # 若用户只给了短曲名/一句，包成简单歌词结构
    if "[verse]" not in text and "[chorus]" not in text:
        lines = [ln.strip() for ln in text.replace("，", "\n").replace(",", "\n").splitlines() if ln.strip()]
        if len(lines) <= 1:
            # 单句/曲名：扩成可唱两段，避免 ACE 产出过短
            seed = lines[0] if lines else text
            body = (
                f"[verse]\n{seed}\n{seed}\n\n"
                f"[chorus]\n{seed}\n轻轻唱给你听\n"
            )
        else:
            mid = max(1, len(lines) // 2)
            body = "[verse]\n" + "\n".join(lines[:mid]) + "\n\n[chorus]\n" + "\n".join(lines[mid:])
    else:
        body = text
    if arrangement == "a_cappella":
        caption = (
            "a cappella chinese female solo singing, dry close-mic vocal only, "
            "no instruments, no piano, no drums, no pad, pure human voice, clear lyrics"
        )
        instrumental = "false"
    else:
        caption = (
            "chinese female pop singing with soft backing track, clear lead vocal, "
            "light drums and pad, intimate soft young woman singing"
        )
        instrumental = "false"
    toml_path = out_dir / "compose.toml"
    toml_path.write_text(
        "\n".join(
            [
                'project_root = "/data/ai/apps/ace-step"',
                'checkpoint_dir = "/data/ai/apps/ace-step/checkpoints"',
                'config_path = "/data/ai/apps/ace-step/checkpoints/acestep-v15-turbo"',
                'lm_model_path = "acestep-5Hz-lm-1.7B"',
                'backend = "pt"',
                'device = "cuda:0"',
                "offload_to_cpu = false",
                f'save_dir = "{ace_dir}"',
                'audio_format = "wav"',
                'task_type = "text2music"',
                f'caption = "{caption}"',
                'lyrics = """',
                body.strip(),
                '"""',
                "duration = 24",
                f"instrumental = {instrumental}",
                'vocal_language = "zh"',
                "bpm = 72",
                'keyscale = "C"',
                'timesignature = "4"',
                "thinking = false",
                "use_cot_caption = false",
                "use_cot_lyrics = false",
                "use_cot_metas = false",
                "use_random_seed = true",
                "batch_size = 1",
                "inference_steps = 8",
                "guidance_scale = 5.0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return toml_path


def _run_pipeline_local(
    *,
    out_dir: Path,
    persona_ref: Path,
    arrangement: str,
    body: str,
    song_path: Optional[Path],
    timeout: float,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    py = _pick_python()
    if (song_path is None or not song_path.is_file()) and body:
        alias = _resolve_local_alias(body)
        if alias is not None:
            song_path = alias

    extra = ""
    mode = "auto"
    if song_path is not None and song_path.is_file():
        extra = f" --song {shlex.quote(str(song_path))}"
        mode = "cover"
    else:
        # 无源曲 → compose：自动写 ACE toml
        toml = _write_ace_toml(out_dir=out_dir, lyrics=body, arrangement=arrangement)
        extra = f" --ace-toml {shlex.quote(str(toml))}"
        mode = "compose"

    py_path = (
        "/data/ai/apps/ComfyUI:"
        "/data/ai/staging/seedvc/comfy-mss:"
        "/data/ai/staging/seedvc/ComfyUI-Timbre-Transfer"
    )
    cmd = (
        f"export COMFYUI_SEEDVC_MODEL_DIR=/data/ai/models/audio/seed_vc; "
        f"export PYTHONPATH={shlex.quote(py_path)}:${{PYTHONPATH:-}}; "
        f"cd /data/ai/apps/sing-pipeline && {shlex.quote(py)} sing_pipeline.py "
        f"--mode {mode} --out-dir {shlex.quote(str(out_dir))} "
        f"--persona-ref {shlex.quote(str(persona_ref))} "
        f"--arrangement {shlex.quote(arrangement)} "
        f"--prompt {shlex.quote(body)}"
        f"{extra}"
    )
    env = os.environ.copy()
    env.setdefault("COMFYUI_SEEDVC_MODEL_DIR", "/data/ai/models/audio/seed_vc")
    env["PYTHONPATH"] = py_path + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        ["bash", "-lc", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        log.warning("sing local failed: %s %s", (proc.stderr or "")[-800:], (proc.stdout or "")[-400:])
        raise RuntimeError(f"sing_pipeline exit {proc.returncode}")
    # stdout 可能混有 torch/库日志，用 raw_decode 提取第一段 JSON
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("sing_pipeline empty output")
    decoder = json.JSONDecoder()
    # 优先从最后一个 '{' 往前试（报告在末尾）；再扫全文
    starts = [i for i, ch in enumerate(raw) if ch == "{"]
    for start in reversed(starts[-8:] if len(starts) > 8 else starts):
        try:
            obj, _ = decoder.raw_decode(raw, start)
            if isinstance(obj, dict) and (obj.get("final_mp3") or obj.get("final_wav") or obj.get("mode")):
                return obj
        except json.JSONDecodeError:
            continue
    # 落盘 report.json 兜底
    report_file = out_dir / "report.json"
    if report_file.is_file():
        try:
            return json.loads(report_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    raise RuntimeError(f"sing_pipeline non-json output: {raw[-400:]}")


def synthesize_sing(
    *,
    user_id: str,
    persona_id: str,
    prompt: str,
    timeout: float = 0,
) -> Optional[Dict[str, Any]]:
    """合成人设唱歌。成功返回 {bytes, content_type, suffix, seconds, meta}。"""
    arrangement, body = parse_sing_prompt(prompt)
    if not body:
        return None
    settings = get_settings()
    if not bool(getattr(settings, "sing_enabled", True)):
        return None
    to = timeout or float(getattr(settings, "sing_timeout_seconds", 600) or 600)
    job = f"job_{int(time.time())}_{persona_id[-6:]}"
    out_dir = Path(f"/data/ai/tmp/sing-jobs/{job}")

    # 硬门：必须已固定人设音色，禁止回落默认样音
    local_ref = resolve_persona_ref_local(user_id=user_id, persona_id=persona_id)
    if local_ref is None or not local_ref.is_file():
        log.warning("sing: refuse without fixed persona voice user=%s persona=%s", user_id, persona_id)
        return None
    persona_ref = local_ref
    ref_source = "persona_fixed"

    song_path: Optional[Path] = None
    track_id = ""
    try:
        from . import music_library as mlib

        hit = mlib.resolve_track_by_prompt(user_id=user_id, prompt=body)
        if hit and (hit.get("path") or "").strip():
            track_id = str(hit.get("id") or "")
            path_s = str(hit["path"])
            p = Path(path_s)
            if p.is_file():
                song_path = p
            elif path_s.startswith("smb://"):
                # 本机拉 NAS
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dest = out_dir / "source_nas.mp3"
                    fetch = (
                        "python3 - <<'PY'\n"
                        "import os,re\n"
                        "from pathlib import Path\n"
                        f"url={path_s!r}; out={str(dest)!r}\n"
                        "m=re.match(r'smb://([^/]+)/([^/]+)/(.*)', url)\n"
                        "if not m: raise SystemExit('bad smb url')\n"
                        "host,share,rel=m.group(1),m.group(2),m.group(3).replace('/','\\\\\\\\')\n"
                        "pw=os.environ.get('MUSIC_NAS_PASSWORD') or open('/data/ai/tmp/.fnos_smb_pw').read().strip() if os.path.isfile('/data/ai/tmp/.fnos_smb_pw') else '2012syl'\n"
                        "from smbclient import register_session, open_file\n"
                        f"register_session(host, username={(settings.music_nas_user or 'syl')!r}, password=pw)\n"
                        "src=r'\\\\\\\\%s\\\\%s\\\\%s'%(host,share,rel)\n"
                        "Path(out).write_bytes(open_file(src,'rb').read())\n"
                        "print(out)\n"
                        "PY"
                    )
                    fr = subprocess.run(
                        ["bash", "-lc", "pip3 install -q smbprotocol 2>/dev/null; " + fetch],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    if fr.returncode == 0 and dest.is_file():
                        song_path = dest
                except Exception as exc:
                    log.warning("smb fetch failed: %s", exc)
    except Exception as exc:
        log.debug("music resolve skip: %s", exc)

    if song_path is not None and arrangement == "a_cappella" and not _ARR_RE.match((prompt or "").strip()):
        arrangement = "with_backing"

    try:
        report = _run_pipeline_local(
            out_dir=out_dir,
            persona_ref=persona_ref,
            arrangement=arrangement,
            body=body,
            song_path=song_path,
            timeout=to,
        )
        remote_mp3 = report.get("final_mp3") or str(out_dir / "persona_sing.mp3")
        mp3 = Path(remote_mp3)
        if not mp3.is_file():
            # 常见落盘名兜底
            for cand in out_dir.glob("*.mp3"):
                mp3 = cand
                break
        if not mp3.is_file():
            log.warning("sing output mp3 missing in %s", out_dir)
            return None
        data = mp3.read_bytes()
        if len(data) < 1000:
            return None
        secs = int(round(float(report.get("duration_sec") or 0)))
        return {
            "bytes": data,
            "content_type": "audio/mpeg",
            "suffix": "mp3",
            "seconds": secs,
            "meta": {
                "kind_extra": "sing",
                "sing": {
                    "arrangement": arrangement,
                    "mode": report.get("mode"),
                    "prompt": body[:120],
                    "job": job,
                    "ref_source": ref_source,
                    "track_id": track_id,
                    "runner": "local",
                },
            },
        }
    except Exception as exc:
        log.warning("synthesize_sing failed: %s", exc)
        return None
