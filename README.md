# DashynAssetGen

RunPod serverless worker that generates themed image asset packs. Takes a vibe description as input, generates optimized prompts via Claude API, runs them through Qwen-Image T2I via ComfyUI, and returns a base64-encoded zip of organized assets.

## Output Structure

```
{vibe_name}.zip
├── backgrounds/     → num_assets scenic/environmental images (1024x1024)
├── female/          → num_assets female outfit images (768x1024)
└── male/            → num_assets male outfit images (768x1024)
```

Total images = `num_assets × 3`

## Environment Variables

Set these in the RunPod dashboard:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for prompt generation |
| `HF_TOKEN` | Only if models not on volume | HuggingFace token for model downloads |

## Network Volume Setup

Attach a network volume named `DashynAssetGen` at `/runpod-volume`. Pre-populate with model files:

```
/runpod-volume/models/
  diffusion_models/
    qwen_image_fp8_e4m3fn.safetensors
  text_encoders/
    qwen_2.5_vl_7b_fp8_scaled.safetensors
  vae/
    qwen_image_vae.safetensors
```

If models are missing, the worker will attempt to download them from HuggingFace on first run (requires `HF_TOKEN`).

## API Usage

### Request

```json
{
    "input": {
        "vibe_name": "mughal_royale",
        "vibe_description": "Mughal era royal court, rich jewel tones, gold embroidery, ornate architecture",
        "num_assets": 2
    }
}
```

### Response

```json
{
    "zip_base64": "<base64-encoded zip>",
    "vibe_name": "mughal_royale",
    "total_images": 6
}
```

## Local Testing

```bash
# With test_input.json (RunPod handler reads it automatically):
python handler.py --test
```

## Deployment

1. Build and push Docker image:
   ```bash
   docker build -t dashyn-asset-gen .
   ```

2. Push to your container registry (e.g., GHCR):
   ```bash
   docker tag dashyn-asset-gen ghcr.io/<username>/dashyn-asset-gen:latest
   docker push ghcr.io/<username>/dashyn-asset-gen:latest
   ```

3. Create a RunPod Serverless endpoint:
   - Container image: `ghcr.io/<username>/dashyn-asset-gen:latest`
   - GPU: 24GB+ VRAM recommended (e.g., RTX 4090, A5000)
   - Network Volume: `DashynAssetGen` mounted at `/runpod-volume`
   - Environment Variables: `ANTHROPIC_API_KEY`

## Architecture

```
handler.py          → RunPod serverless entry point, orchestrates the full flow
prompt_generator.py → Claude API (claude-haiku-4-5) for generating image prompts
workflow_builder.py → Builds ComfyUI API-format workflow JSON for Qwen-Image T2I
comfyui_client.py   → HTTP client for local ComfyUI server
model_manager.py    → Checks/downloads model files on network volume
start.sh            → Container startup: ComfyUI server (bg) + handler (fg)
```
