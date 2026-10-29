#!/usr/bin/env bash
# 本机部署开源 Unlimited-OCR（vLLM 官方镜像）
# GPU1 约需 8GB+；默认监听 127.0.0.1:9898
set -euo pipefail

NAME="${UNLIMITED_OCR_CONTAINER:-chat-unlimited-ocr}"
PORT="${UNLIMITED_OCR_PORT:-9898}"
GPU="${UNLIMITED_OCR_GPU:-1}"
MODEL="${UNLIMITED_OCR_MODEL:-baidu/Unlimited-OCR}"
IMAGE="${UNLIMITED_OCR_IMAGE:-vllm/vllm-openai:unlimited-ocr}"
HF_CACHE="${UNLIMITED_OCR_HF_CACHE:-/data/ai/cache/huggingface}"
MEM_UTIL="${UNLIMITED_OCR_GPU_MEM:-0.45}"

mkdir -p "$HF_CACHE"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

echo "pull $IMAGE ..."
docker pull "$IMAGE"

echo "start $NAME on GPU=$GPU port=$PORT model=$MODEL"
exec docker run -d --name "$NAME" --restart unless-stopped \
  --gpus "device=${GPU}" \
  --network host \
  --ipc host \
  -e HUGGING_FACE_HUB_CACHE=/root/.cache/huggingface \
  -e HF_HOME=/root/.cache/huggingface \
  -v "${HF_CACHE}:/root/.cache/huggingface" \
  "$IMAGE" \
  "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --trust-remote-code \
  --served-model-name "$MODEL" \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization "$MEM_UTIL" \
  --max-model-len 8192
