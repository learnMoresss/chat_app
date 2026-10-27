#!/usr/bin/env bash
# 由 systemd → sg docker -c "exec …" 调用；此处再 exec uvicorn，保证同 cgroup。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
cd "$ROOT"

fuser -k 8000/tcp >/dev/null 2>&1 || true
sleep 0.5

export PATH="${HOME}/opt/platform-tools:${ROOT}/.venv/bin:/usr/bin:${PATH:-}"
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
