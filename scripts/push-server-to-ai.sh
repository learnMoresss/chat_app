#!/usr/bin/env bash
# 将 FastAPI server/ 同步到 AI 主机（不含 venv、本地 db、.env 密钥可另拷）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${REMOTE_DIR:-/home/joy/chat_app/server}"
# 产品 API + DSH 都跑在 AI 主机；Mac 只负责改代码再 push。
SSH_CONFIG="${SSH_CONFIG:-/Users/blacktom06/Desktop/code/公网访问-119.45.30.73/ssh-config}"
SSH_HOST="${SSH_HOST:-local-ai-public}"

ssh -F "$SSH_CONFIG" "$SSH_HOST" "mkdir -p '$REMOTE'"
rsync -az --delete \
  -e "ssh -F $SSH_CONFIG" \
  --exclude .venv \
  --exclude data \
  --exclude '__pycache__' \
  --exclude .env \
  --exclude '*.pyc' \
  "$ROOT/server/" "$SSH_HOST:$REMOTE/"
echo "[push-server] -> $SSH_HOST:$REMOTE"
echo "下一步在服务器: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
