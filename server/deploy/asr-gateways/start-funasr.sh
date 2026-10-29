#!/usr/bin/env bash
set -euo pipefail
pkill -f 'funasr_gateway:app' 2>/dev/null || true
pkill -f 'funasr_gateway.py' 2>/dev/null || true
sleep 1
export CUDA_VISIBLE_DEVICES="${FUNASR_GPU:-0}"
export FUNASR_DEVICE=cuda:0
export FUNASR_MODEL="${FUNASR_MODEL:-iic/SenseVoiceSmall}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-/data/ai/models/audio/modelscope-cache}"
mkdir -p "$MODELSCOPE_CACHE" /data/ai/logs/audio
cd /data/ai/services/asr-gateways
setsid nohup /data/ai/envs/funasr-voice/bin/python -c \
  'import uvicorn; uvicorn.run("funasr_gateway:app", host="127.0.0.1", port=8886)' \
  >>/data/ai/logs/audio/funasr-gateway.log 2>&1 < /dev/null &
echo "funasr gateway pid=$! gpu=${CUDA_VISIBLE_DEVICES}"
