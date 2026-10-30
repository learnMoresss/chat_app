#!/usr/bin/env python3
"""压测生图 / TTS 冷热 / 唱歌：从启动到返回的耗时。

用法（在 server 目录、已配置环境）:
  python -m scripts.bench_media_latency
  python scripts/bench_media_latency.py --skip-sing --skip-image
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bench_media")


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def bench_tts(*, warm_first: bool = True) -> dict:
    from app.tts_gen import synthesize_voice_wav, warm_tts_engine
    from app.tts_ondemand import release_engine
    from app.config import get_settings

    eng = (get_settings().tts_engine or "breeze").strip().lower()
    out: dict = {"engine": eng}
    # 强制冷启：先释放再测
    try:
        release_engine(eng, unload_now=True)
    except Exception:
        pass
    time.sleep(0.5)

    if warm_first:
        t0 = time.perf_counter()
        info = warm_tts_engine(for_realtime=False)
        out["warm_ms"] = _ms(t0)
        out["warm_info"] = info
        log.info("tts warm_ms=%s info=%s", out["warm_ms"], info)

    instr = "年轻女声，语气自然轻松，中文普通话"
    sample = "你好呀，今天天气真不错。"

    t0 = time.perf_counter()
    wav1 = synthesize_voice_wav(text=sample, instructions=instr, keep_loaded=True)
    out["cold_or_first_synth_ms"] = _ms(t0)
    out["cold_ok"] = bool(wav1)
    log.info("tts first synth_ms=%s ok=%s bytes=%s", out["cold_or_first_synth_ms"], bool(wav1), len(wav1 or b""))

    t0 = time.perf_counter()
    wav2 = synthesize_voice_wav(text="嗯嗯，我在听。", instructions=instr, keep_loaded=True)
    out["hot_synth_ms"] = _ms(t0)
    out["hot_ok"] = bool(wav2)
    log.info("tts hot synth_ms=%s ok=%s", out["hot_synth_ms"], bool(wav2))
    return out


def bench_image() -> dict:
    from app.media_cards import generate_or_mood

    # 用假 user，走 put_bytes 落盘；失败也记时
    uid = "bench_media_user"
    t0 = time.perf_counter()
    try:
        fid = generate_or_mood(
            user_id=uid,
            caption="一只橘猫坐在窗台上，阳光，写实",
            persona_name="bench",
            backend="zimage",
            width=512,
            height=512,
        )
    except Exception as exc:
        return {"ok": False, "ms": _ms(t0), "error": str(exc)[:200]}
    return {"ok": bool(fid), "ms": _ms(t0), "file_id": fid or ""}


def bench_sing(*, user_id: str, persona_id: str) -> dict:
    from app.sing_gen import synthesize_sing

    t0 = time.perf_counter()
    try:
        sung = synthesize_sing(
            user_id=user_id,
            persona_id=persona_id,
            prompt="小星星",
            timeout=600,
        )
    except Exception as exc:
        return {"ok": False, "ms": _ms(t0), "error": str(exc)[:240]}
    return {
        "ok": bool(sung and sung.get("bytes")),
        "ms": _ms(t0),
        "seconds": (sung or {}).get("seconds"),
        "bytes": len((sung or {}).get("bytes") or b""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tts", action="store_true")
    ap.add_argument("--skip-image", action="store_true")
    ap.add_argument("--skip-sing", action="store_true")
    ap.add_argument("--no-warm", action="store_true", help="TTS 不做 warm，测真冷启")
    ap.add_argument("--user-id", default="", help="唱歌压测需要有固定音色的账号")
    ap.add_argument("--persona-id", default="", help="唱歌压测人设 id")
    args = ap.parse_args()

    report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not args.skip_tts:
        report["tts"] = bench_tts(warm_first=not args.no_warm)
    if not args.skip_image:
        report["image"] = bench_image()
        log.info("image %s", report["image"])
    if not args.skip_sing:
        if not args.user_id or not args.persona_id:
            report["sing"] = {"skipped": True, "reason": "need --user-id and --persona-id with fixed voice"}
            log.warning("sing skipped: pass --user-id/--persona-id")
        else:
            report["sing"] = bench_sing(user_id=args.user_id, persona_id=args.persona_id)
            log.info("sing %s", report["sing"])

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
