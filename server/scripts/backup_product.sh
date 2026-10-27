#!/usr/bin/env bash
# M7：备份产品数据目录与关键配置（不含密钥明文建议另管）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-$ROOT/backups/chat_backup_$STAMP.tgz}"
mkdir -p "$(dirname "$OUT")"
cd "$ROOT"
tar czf "$OUT" \
  --exclude='./.venv' \
  --exclude='./admin-ui/node_modules' \
  --exclude='./web/dist/assets' \
  data .env.example gateway.yaml.example 2>/dev/null || \
tar czf "$OUT" data
echo "backup_ok $OUT"
