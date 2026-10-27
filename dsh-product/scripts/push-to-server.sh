#!/usr/bin/env bash
# 本机 → AI 主机：只同步产品插件/配置/脚本（不传 node_modules / vendor / .dsh-home）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-/home/joy/chat_app/dsh-product}"
SSH_CONFIG="${SSH_CONFIG:-/Users/blacktom06/Desktop/code/公网访问-119.45.30.73/ssh-config}"
SSH_HOST="${SSH_HOST:-local-ai-public}"

echo "[push] $ROOT → $SSH_HOST:$REMOTE_DIR"

ssh -F "$SSH_CONFIG" "$SSH_HOST" "mkdir -p '$REMOTE_DIR'/{packages,profiles,scripts,docker}"

rsync -az --delete \
  -e "ssh -F $SSH_CONFIG" \
  --exclude node_modules \
  --exclude .dsh-home \
  --exclude '*.sqlite' \
  --exclude '*.jsonl' \
  --exclude .DS_Store \
  "$ROOT/packages/" "$SSH_HOST:$REMOTE_DIR/packages/"

rsync -az \
  -e "ssh -F $SSH_CONFIG" \
  "$ROOT/profiles/" "$SSH_HOST:$REMOTE_DIR/profiles/"

rsync -az \
  -e "ssh -F $SSH_CONFIG" \
  "$ROOT/scripts/" "$SSH_HOST:$REMOTE_DIR/scripts/"

rsync -az \
  -e "ssh -F $SSH_CONFIG" \
  "$ROOT/docker/" "$SSH_HOST:$REMOTE_DIR/docker/"

rsync -az \
  -e "ssh -F $SSH_CONFIG" \
  "$ROOT/package.json" "$ROOT/package-lock.json" "$ROOT/.npmrc" \
  "$SSH_HOST:$REMOTE_DIR/"

echo "[push] done. On server (native):"
echo "  export PATH=~/opt/node-v24.11.0-linux-x64/bin:\$PATH"
echo "  cd $REMOTE_DIR && export DSH_HOME=\$PWD/.dsh-home PRODUCT_ROOT=\$PWD"
echo "  node scripts/p0-smoke.mjs run"

