#!/usr/bin/env bash
# 设备 API 封装：自动带隔离 Header + VD_TICKET（API 硬绑定用户）。
# 用法:
#   set -a; . "$DSH_HOME/product-vd/env.sh"; set +a
#   vd-api POST phone/actions/ui-dump
#   vd-api POST phone/actions/ui-tap '{"text":"设置"}'
set -euo pipefail

API_BASE="${CHAT_API_BASE:-${PRODUCT_GATEWAY_URL:-http://127.0.0.1:8000}}"
API_BASE="${API_BASE%/}"
TOKEN="${DSH_GATEWAY_TOKEN:-}"
UID_H="${CHAT_USER_ID:-}"
PID_H="${CHAT_PERSONA_ID:-}"
CID_H="${CHAT_CONV_ID:-}"
TICKET="${VD_TICKET:-}"

if [[ -z "$TOKEN" ]]; then
  echo '{"ok":false,"error":"DSH_GATEWAY_TOKEN missing"}' >&2
  exit 2
fi
if [[ -z "$TICKET" && -z "$UID_H" ]]; then
  echo '{"ok":false,"error":"VD_TICKET/CHAT_USER_ID missing — source product-vd/env.sh"}' >&2
  exit 2
fi

method="${1:-GET}"
path="${2:-}"
body="${3:-}"
if [[ -z "$path" ]]; then
  echo "usage: vd-api METHOD path [json_body]" >&2
  exit 2
fi
path="${path#/}"
url="$API_BASE/api/v1/virtual-devices/$path"

args=(
  -sS
  -X "$method"
  -H "Authorization: Bearer $TOKEN"
)
if [[ -n "$TICKET" ]]; then
  args+=(-H "X-Vd-Ticket: $TICKET")
fi
if [[ -n "$UID_H" ]]; then
  args+=(-H "X-Chat-User-Id: $UID_H")
fi
args+=(-H "X-Persona-Id: $PID_H" -H "X-Conv-Id: $CID_H")
if [[ -n "$body" ]]; then
  args+=(-H "Content-Type: application/json" -d "$body")
fi
exec curl "${args[@]}" "$url"
