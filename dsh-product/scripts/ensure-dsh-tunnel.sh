#!/usr/bin/env bash
# 本机 FastAPI → AI 主机 8787（DSH gateway）
set -euo pipefail
SSH_CONFIG="${SSH_CONFIG:-/Users/blacktom06/Desktop/code/公网访问-119.45.30.73/ssh-config}"
SSH_HOST="${SSH_HOST:-local-ai-public}"
PORT="${DSH_GATEWAY_LOCAL_PORT:-8787}"
# 僵死转发：health 失败则重建
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "port $PORT healthy"
    exit 0
  fi
  echo "port $PORT stale, recreating tunnel"
  pkill -f "${PORT}:127.0.0.1:8787" 2>/dev/null || true
  sleep 1
fi
ssh -F "$SSH_CONFIG" -fN -o ExitOnForwardFailure=yes -L "$PORT:127.0.0.1:8787" "$SSH_HOST"
echo "tunnel $PORT -> $SSH_HOST:8787"
