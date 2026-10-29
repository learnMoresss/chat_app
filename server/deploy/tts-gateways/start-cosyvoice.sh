#!/usr/bin/env bash
# 真 CosyVoice 网关。默认 18885，避开 product-gateway 占着的假 8885 兼容桩。
set -euo pipefail
PORT="${COSYVOICE_PORT:-18885}"
pkill -f "uvicorn.run(\"cosyvoice_gateway:app\".*port=${PORT}" 2>/dev/null || true
pkill -f "cosyvoice_gateway:app.*${PORT}" 2>/dev/null || true
# 兼容旧写法
if [[ "$PORT" != "8885" ]]; then
  pkill -f 'uvicorn.run("cosyvoice_gateway:app", host="127.0.0.1", port='"${PORT}" 2>/dev/null || true
fi
sleep 1
export CUDA_VISIBLE_DEVICES="${COSYVOICE_GPU:-1}"
# FP16 默认开：降低 RTF，减轻句中卡顿
export COSYVOICE_FP16="${COSYVOICE_FP16:-1}"
# 通话可能很长；30 分钟空闲才卸，避免「以为已预热其实模型已卸」
export COSYVOICE_IDLE_UNLOAD_SECONDS="${COSYVOICE_IDLE_UNLOAD_SECONDS:-1800}"
export PYTHONPATH="/data/ai/services/tts-gateways:/data/ai/apps/CosyVoice:/data/ai/apps/CosyVoice/third_party/Matcha-TTS:${PYTHONPATH:-}"
cd /data/ai/services/tts-gateways
setsid nohup /data/ai/envs/novelreel-cosyvoice/bin/python -c \
  "import uvicorn; uvicorn.run('cosyvoice_gateway:app', host='127.0.0.1', port=${PORT})" \
  >>/data/ai/logs/audio/cosyvoice-gateway.log 2>&1 < /dev/null &
echo "cosyvoice gateway pid=$! port=${PORT} COSYVOICE_FP16=${COSYVOICE_FP16} IDLE=${COSYVOICE_IDLE_UNLOAD_SECONDS}"
