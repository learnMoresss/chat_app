#!/usr/bin/env python3
"""对比飞桨 / Unlimited-OCR / DeepSeek官方 / 本地QwenVL 对画面内容的识别。"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

import httpx

CAT = Path("/tmp/test_scene.jpg")
TEXT_IMG = Path(
    "data/files/u_908639b6beaee9cca4108a20/mf_53f65f52924fe3c1b54a1865_scaled_1000047230.jpg"
)
USER_PNG = Path(
    "data/files/u_908639b6beaee9cca4108a20/mf_ab21316b57b73ac70f70736b_png.png"
)


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def paddle(p: Path):
    data = json.dumps({"base64_img": _b64(p), "preprocess": False}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9899/general/base64",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    t = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read())
    texts = j.get("rec_texts") or []
    return int((time.time() - t) * 1000), " | ".join(texts[:8])[:200] or "(无文字)"


def unlimited(p: Path, prompt: str = "<image>document parsing."):
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    payload = {
        "model": "baidu/Unlimited-OCR",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{_b64(p)}"},
                    },
                ],
            }
        ],
        "max_tokens": 512,
        "temperature": 0,
    }
    t = time.time()
    r = httpx.post("http://127.0.0.1:9898/v1/chat/completions", json=payload, timeout=180)
    ms = int((time.time() - t) * 1000)
    if r.status_code >= 400:
        return ms, f"ERR {r.status_code} {(r.text or '')[:160]}"
    j = r.json()
    c = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return ms, str(c).replace("\n", " ")[:240]


def deepseek(p: Path):
    from app.vision_gen import _describe_via_deepseek_official

    t = time.time()
    out = _describe_via_deepseek_official(p, hint="这是什么")
    return int((time.time() - t) * 1000), (out or "")[:240]


def local_vl(p: Path):
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    payload = {
        "model": "qwen3.8-flash-next-iq3_xxs",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "用中文一句话说这是什么；若是动物请说明品种/颜色/姿态。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{_b64(p)}"},
                    },
                ],
            }
        ],
        "max_tokens": 120,
        "temperature": 0.2,
    }
    t = time.time()
    try:
        r = httpx.post(
            "http://127.0.0.1:9292/v1/chat/completions",
            headers={
                "Authorization": "Bearer 651a0adc081dea27e395592fdf7d95bc1045cf13b7dfc3c2c69eb893942bc272"
            },
            json=payload,
            timeout=120,
        )
        ms = int((time.time() - t) * 1000)
        if r.status_code >= 400:
            return ms, f"ERR {r.status_code} {(r.text or '')[:120]}"
        j = r.json()
        c = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return ms, str(c).replace("\n", " ")[:240]
    except Exception as e:
        return int((time.time() - t) * 1000), f"ERR {type(e).__name__}: {e}"


def main() -> None:
    for name, p in [("CAT", CAT), ("TEXT_SCREEN", TEXT_IMG), ("USER_PNG", USER_PNG)]:
        if not p.is_file():
            print("missing", p)
            continue
        print(f"\n==== {name} {p.name} size={p.stat().st_size}")
        for eng, fn in [
            ("paddle", paddle),
            ("unlimited", unlimited),
            ("deepseek", deepseek),
            ("local_qwen_vl", local_vl),
        ]:
            try:
                ms, out = fn(p)
                print(f"  {eng:14s} {ms:5d}ms  {out}")
            except Exception as e:
                print(f"  {eng:14s} ERR {type(e).__name__}: {e}")

    print("\n==== Unlimited scene prompt on CAT")
    ms, out = unlimited(
        CAT,
        prompt="<image>用中文描述图里是什么动物/物体，颜色和姿态。",
    )
    print(f"  {ms}ms  {out}")


if __name__ == "__main__":
    main()
