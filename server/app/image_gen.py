"""本机生图：优先 Z-Image（ComfyUI），其次 Qwen-Image 2.1（qwen-image-web）。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

import httpx

from .config import get_settings


def generate_image_bytes(
    prompt: str,
    *,
    backend: str = "",
    width: int = 768,
    height: int = 768,
    timeout: float = 180.0,
    ratio: str = "",
    seed: int | None = None,
    ref_image: bytes | None = None,
) -> Optional[bytes]:
    """返回 PNG/JPEG 字节；失败返回 None。

    ratio 如 "3:4"：走 Qwen Web 时优先用画幅。
    ref_image：有则走图生图，尽量保持同一张脸/人设。
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    settings = get_settings()
    choice = (backend or settings.image_gen_backend or "auto").strip().lower()
    order = []
    if choice in ("zimage", "z-image", "comfy"):
        order = ["zimage"]
    elif choice in ("qwen", "qwen21", "qwen_web", "qwen-image"):
        order = ["qwen"]
    else:
        # 有参考图时优先 qwen（支持 i2i）；否则聊天要快 → zimage 再 qwen
        order = ["qwen", "zimage"] if ref_image else ["zimage", "qwen"]

    for name in order:
        try:
            if name == "zimage":
                # Z-Image 路径暂不接参考图
                if ref_image:
                    continue
                data = _gen_zimage(
                    prompt,
                    width=width,
                    height=height,
                    timeout=timeout,
                    seed=seed,
                )
            else:
                data = _gen_qwen_web(
                    prompt,
                    timeout=timeout,
                    ratio=ratio or "3:4",
                    ref_image=ref_image,
                )
            if data:
                return data
        except Exception:
            continue
    return None


def _gen_zimage(
    prompt: str,
    *,
    width: int,
    height: int,
    timeout: float,
    seed: int | None = None,
) -> Optional[bytes]:
    base = (get_settings().image_gen_comfy_url or "").rstrip("/")
    if not base:
        return None
    width = max(16, round(int(width) / 16) * 16)
    height = max(16, round(int(height) / 16) * 16)
    if seed is None:
        seed = int(uuid.uuid4().int % (2**31))
    else:
        seed = int(seed) % (2**31)
    workflow = {
        "1": {
            "class_type": "DiffusionModelLoaderKJ",
            "inputs": {
                "model_name": "z_image_turbo_bf16.safetensors",
                "weight_dtype": "fp16",
                "compute_dtype": "fp16",
                "patch_cublaslinear": False,
                "sage_attention": "disabled",
                "enable_fp16_accumulation": False,
            },
        },
        "2": {
            "class_type": "CLIPLoaderGGUF",
            "inputs": {"clip_name": "Qwen3-4B-ZImage-Heretic-Genesis-Q8.gguf", "type": "lumina2"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "7": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "seed": seed,
                "steps": 8,
                "cfg": 1,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "chat_app/Z-Image", "images": ["9", 0]},
        },
    }
    client_id = str(uuid.uuid4())
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{base}/prompt", json={"prompt": workflow, "client_id": client_id})
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"]))
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI 无 prompt_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.2)
            h = client.get(f"{base}/history/{prompt_id}")
            h.raise_for_status()
            hist = h.json().get(prompt_id) or {}
            status = (hist.get("status") or {}).get("status_str")
            if status == "error":
                raise RuntimeError("Z-Image 生成失败")
            images = ((hist.get("outputs") or {}).get("10") or {}).get("images") or []
            if not images:
                # 有的图挂在 9
                images = ((hist.get("outputs") or {}).get("9") or {}).get("images") or []
            if not images:
                continue
            info = images[0]
            params = {
                "filename": info["filename"],
                "subfolder": info.get("subfolder") or "",
                "type": info.get("type") or "output",
            }
            view = client.get(f"{base}/view", params=params)
            view.raise_for_status()
            if view.content:
                return view.content
        raise RuntimeError("Z-Image 超时")


def _gen_qwen_web(
    prompt: str,
    *,
    timeout: float,
    ratio: str = "3:4",
    ref_image: bytes | None = None,
) -> Optional[bytes]:
    base = (get_settings().image_gen_qwen_url or "").rstrip("/")
    if not base:
        return None
    allowed = {"1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"}
    ratio = (ratio or "3:4").strip()
    if ratio not in allowed:
        ratio = "3:4"
    # edit：保留未提及内容（脸/发型/人设），只改镜头；i2i 会锁构图不利于换视角
    mode = "edit" if ref_image else "t2i"
    with httpx.Client(timeout=timeout) as client:
        # qwen-image-web: multipart /api/jobs ，steps 仅允许 8；seed 由服务端每次 urandom
        files: dict = {
            "prompt": (None, prompt),
            "mode": (None, mode),
            "ratio": (None, ratio),
            "edge": (None, "1024"),
            "steps": (None, "8"),
        }
        if ref_image:
            files["image"] = ("ref.png", ref_image, "image/png")
        r = client.post(f"{base}/api/jobs", files=files)
        if r.status_code >= 400:
            raise RuntimeError(r.text[:200] or f"qwen http {r.status_code}")
        job_id = (r.json() or {}).get("id")
        if not job_id:
            raise RuntimeError("qwen 无 job id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            st = client.get(f"{base}/api/jobs/{job_id}")
            st.raise_for_status()
            data = st.json()
            if data.get("error"):
                raise RuntimeError(str(data["error"])[:180])
            if data.get("done"):
                img = client.get(f"{base}/api/jobs/{job_id}/image")
                img.raise_for_status()
                if img.content:
                    return img.content
                raise RuntimeError("qwen 完成但无图")
        raise RuntimeError("qwen 超时")
