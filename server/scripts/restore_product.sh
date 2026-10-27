#!/usr/bin/env bash
# M7：从 backup_product.sh 产出的 tgz 还原 data/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TGZ="${1:?usage: restore_product.sh <backup.tgz>}"
cd "$ROOT"
test -f "$TGZ"
echo "WARNING: will replace ./data — Ctrl+C within 3s to abort"
sleep 3
rm -rf data.bak_restore 2>/dev/null || true
mv data data.bak_restore 2>/dev/null || true
tar xzf "$TGZ" -C "$ROOT"
echo "restore_ok (previous data at data.bak_restore if any)"
