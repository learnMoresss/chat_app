#!/usr/bin/env python3
"""模拟 AI 发说话/唱歌语音条。

用法（需 server 已启动且用户已登录拿 token，或走本脚本 login）：

  cd server
  .venv/bin/python scripts/sim_voice_push.py \\
    --base http://127.0.0.1:8000 \\
    --user syl --password '你的密码' \\
    --conv c_ded362f9788be3e630e5ad1f \\
    --kind speak

  --kind sing  推唱歌样例
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="", help="已有 access_token；空则用 --user/--password 或 --dev-token")
    ap.add_argument("--dev-token", default=os.environ.get("DEV_SIM_TOKEN", ""), help="与 server DEV_SIM_TOKEN 一致")
    ap.add_argument("--user", default="syl")
    ap.add_argument("--password", default="")
    ap.add_argument("--conv", required=True, help="conversations.id")
    ap.add_argument("--kind", choices=["speak", "sing"], default="speak")
    ap.add_argument("--audio", default="", help="可选本地音频路径")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    token = args.token.strip()
    with httpx.Client(timeout=60.0) as client:
        headers = {}
        if args.dev_token.strip():
            headers["X-Dev-Token"] = args.dev_token.strip()
        elif not token:
            if not args.password:
                print("需要 --dev-token 或 --token 或 --password", file=sys.stderr)
                return 2
            r = client.post(
                f"{base}/api/v1/auth/login",
                json={"username": args.user, "password": args.password, "device_name": "sim_voice"},
            )
            if r.status_code >= 400:
                print("login failed", r.status_code, r.text[:300], file=sys.stderr)
                return 1
            token = r.json().get("access_token") or ""
            if not token:
                print("no access_token", r.text[:300], file=sys.stderr)
                return 1
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Bearer {token}"
        body = {"conv_id": args.conv, "kind": args.kind}
        if args.audio:
            body["audio_path"] = str(Path(args.audio).resolve())
        r = client.post(f"{base}/api/v1/dev/sim-voice", headers=headers, json=body)
        print(r.status_code, r.text[:800])
        return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
