#!/usr/bin/env bash
# 备份回滚演练：复制 chat.db → 校验 →（可选）回滚演练副本。
# 用法：
#   ./scripts/backup_drill.sh              # 只备份+校验
#   ./scripts/backup_drill.sh --restore-dry # 在临时目录试还原，不动生产库
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CHAT_DATA_DIR:-$ROOT/data}"
DB="$DATA/chat.db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${CHAT_BACKUP_DIR:-$DATA/backups}"
mkdir -p "$BACKUP_DIR"
if [[ ! -f "$DB" ]]; then
  echo "missing db: $DB" >&2
  exit 1
fi
DEST="$BACKUP_DIR/chat-$STAMP.db"
cp -a "$DB" "$DEST"
# 完整性
python3 - <<PY
import sqlite3, sys
c=sqlite3.connect("$DEST")
r=c.execute("PRAGMA integrity_check").fetchone()
print("integrity", r[0])
if r[0] != "ok":
  sys.exit(2)
n=c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
print("objects", n)
PY
echo "backup_ok $DEST"
if [[ "${1:-}" == "--restore-dry" ]]; then
  TMP="$(mktemp -d)"
  cp -a "$DEST" "$TMP/chat.db"
  python3 - <<PY
import sqlite3
c=sqlite3.connect("$TMP/chat.db")
print("restore_dry_ok users", c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
PY
  rm -rf "$TMP"
  echo "restore_dry_done (production untouched)"
fi
