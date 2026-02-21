# =============================================================================
# DashynAssetGen - Qwen-Image T2I Asset Generator
# Based on official RunPod worker-comfyui base image
# Models loaded from Network Volume at runtime
# =============================================================================
# Pin to 5.4.1 with CUDA 12.4.1 for broad GPU driver compatibility
FROM runpod/worker-comfyui:5.4.1-base-cuda12.4.1

# Install custom nodes for Qwen-Image T2I support
RUN comfy-node-install ComfyUI_RH_Qwen-Image || true

# Install additional Python dependencies
RUN pip install --no-cache-dir anthropic huggingface_hub

# Configure model paths for network volume
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# Copy application code
COPY handler.py /handler.py
COPY comfyui_client.py /comfyui_client.py
COPY prompt_generator.py /prompt_generator.py
COPY model_manager.py /model_manager.py
COPY workflow_builder.py /workflow_builder.py

# Copy startup script
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Remove default test_input.json and add ours
RUN rm -f /test_input.json || true
COPY test_input.json /test_input.json

CMD ["/start.sh"]
