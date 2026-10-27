#!/usr/bin/env bash
# 按 .env / 环境变量拉起公网穿透。无 PUBLIC_TUNNEL_PROVIDER 或为 none 则退出 0。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROVIDER="${PUBLIC_TUNNEL_PROVIDER:-none}"
case "$PROVIDER" in
  ""|none|lan|off)
    echo "public tunnel: skipped (provider=$PROVIDER)"
    exit 0
    ;;
esac

export DATA_DIR="${DATA_DIR:-./data}"
export PORT="${PORT:-8000}"
exec python3 scripts/tunnel_ctl.py start "$PROVIDER"
