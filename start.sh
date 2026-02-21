#!/bin/bash
set -e

# Use tcmalloc for better memory management (if available)
TCMALLOC="$(ldconfig -p 2>/dev/null | grep -Po "libtcmalloc.so.\d" | head -n 1)" || true
if [ -n "$TCMALLOC" ]; then
    export LD_PRELOAD="$TCMALLOC"
    echo "Using tcmalloc: $TCMALLOC"
fi

# Set ComfyUI-Manager to offline mode (if available)
comfy-manager-set-mode offline 2>/dev/null || true

# Default log level
export COMFY_LOG_LEVEL="${COMFY_LOG_LEVEL:-WARNING}"

echo "=== DashynAssetGen Container Starting ==="
echo "Starting ComfyUI server..."

python /comfyui/main.py \
    --disable-auto-launch \
    --disable-metadata \
    --log-stdout \
    --highvram \
    --extra-model-paths-config /comfyui/extra_model_paths.yaml &

echo "Starting RunPod handler..."
python -u /handler.py
