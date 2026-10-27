#!/usr/bin/env python3
"""公网穿透控制：list | status | start <provider> | stop

示例：
  python scripts/tunnel_ctl.py list
  python scripts/tunnel_ctl.py start cloudflared
  python scripts/tunnel_ctl.py start ngrok
  python scripts/tunnel_ctl.py start localhost_run
  python scripts/tunnel_ctl.py start pinggy
  python scripts/tunnel_ctl.py start ssh_jump
  python scripts/tunnel_ctl.py status
  python scripts/tunnel_ctl.py stop
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tunnel import TunnelManager, provider_catalog  # noqa: E402


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or (ROOT / "data")).expanduser()


def _port() -> int:
    return int(os.environ.get("PORT") or os.environ.get("PUBLIC_TUNNEL_LOCAL_PORT") or "8000")


def main() -> int:
    ap = argparse.ArgumentParser(description="Chat API 公网穿透")
    ap.add_argument("action", choices=["list", "status", "start", "stop"])
    ap.add_argument("provider", nargs="?", default="", help="cloudflared|ngrok|localhost_run|pinggy|ssh_jump|none")
    args = ap.parse_args()

    mgr = TunnelManager(_data_dir(), local_port=_port())

    if args.action == "list":
        print(json.dumps(provider_catalog(), ensure_ascii=False, indent=2))
        return 0

    if args.action == "status":
        print(json.dumps(mgr.status(), ensure_ascii=False, indent=2))
        return 0

    if args.action == "stop":
        print(json.dumps(mgr.stop(), ensure_ascii=False, indent=2))
        return 0

    # start
    provider = (args.provider or os.environ.get("PUBLIC_TUNNEL_PROVIDER") or "cloudflared").strip()
    st = mgr.start(
        provider,
        ngrok_authtoken=os.environ.get("NGROK_AUTHTOKEN") or os.environ.get("PUBLIC_TUNNEL_NGROK_AUTHTOKEN") or "",
        cloudflared_token=os.environ.get("CLOUDFLARE_TUNNEL_TOKEN") or os.environ.get("PUBLIC_TUNNEL_CLOUDFLARED_TOKEN") or "",
        ssh_config=os.environ.get("PUBLIC_TUNNEL_SSH_CONFIG") or "",
        ssh_host=os.environ.get("PUBLIC_TUNNEL_SSH_HOST") or "",
        ssh_remote_port=int(os.environ.get("PUBLIC_TUNNEL_SSH_REMOTE_PORT") or "0"),
    )
    print(json.dumps(st, ensure_ascii=False, indent=2))
    if st.get("public_url"):
        print(f"\n→ App 服务器地址填: {st['public_url']}", file=sys.stderr)
    return 0 if st.get("active") or provider == "none" else 1


if __name__ == "__main__":
    raise SystemExit(main())
