#!/bin/sh
set -eu

: "${DSH_HOME:=/dsh-home}"
: "${PRODUCT_ROOT:=/app}"
export DSH_HOME PRODUCT_ROOT

PROFILE="${1:-companion}"
shift || true

# 首次启动：从 sdk 模板物化 companion（不覆盖已有 profile）
if [ ! -f "$DSH_HOME/profiles/companion/package.json" ]; then
  echo "[entrypoint] init companion from sdk template under $DSH_HOME"
  npx --no-install dsh companion --from-default-profile sdk --dump-config >/dev/null
fi

PATCH="$PRODUCT_ROOT/profiles/companion.product.patch.yml"
if [ -f "$PATCH" ]; then
  set -- --patch "$PATCH" "$@"
fi

case "$PROFILE" in
  sdk|companion)
    # SDK JSON-RPC on stdio when driven by client; for long-running use smoke/gateway.
    exec npx --no-install dsh companion "$@"
    ;;
  web)
    exec npx --no-install dsh web "$@"
    ;;
  smoke)
    exec node "$PRODUCT_ROOT/scripts/p0-smoke.mjs" "$@"
    ;;
  *)
    exec npx --no-install dsh --profile "$PROFILE" "$@"
    ;;
esac
