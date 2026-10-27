from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Optional


def list_gpus() -> List[Dict[str, Any]]:
    """封装 nvidia-smi；无卡或命令失败返回空列表。"""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    gpus: List[Dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_total_mb": int(float(parts[2])),
                    "memory_free_mb": int(float(parts[3])),
                    "utilization": int(float(parts[4])),
                }
            )
        except ValueError:
            continue
    return gpus


def pick_gpu(vram_mb: int, preferred: Optional[int] = None) -> Optional[int]:
    if vram_mb <= 0:
        return None
    gpus = list_gpus()
    if not gpus:
        return None
    # 余量 512MB，避免刚好顶满
    need = vram_mb + 512
    candidates = [g for g in gpus if g["memory_free_mb"] >= need]
    if preferred is not None:
        for g in candidates:
            if g["index"] == preferred:
                return preferred
    if not candidates:
        return None
    candidates.sort(key=lambda g: g["memory_free_mb"], reverse=True)
    return int(candidates[0]["index"])
