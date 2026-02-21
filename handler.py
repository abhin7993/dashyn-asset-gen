"""
DashynAssetGen — RunPod Serverless Handler (Multi-mode)

Supports three modes:
  - generate_prompts: Calls Claude API to produce prompt texts (fast, no GPU work)
  - render_image: Takes ONE prompt, generates ONE image via ComfyUI (parallelizable)
  - full (default): Original all-in-one pipeline for backward compatibility

The GUI uses generate_prompts + render_image to fan out across all available workers.
"""

import base64
import io
import logging
import os
import time

import runpod
from PIL import Image

from comfyui_client import ComfyUIClient
from model_manager import ensure_models_available
from prompt_generator import PromptGenerator
from workflow_builder import WorkflowBuilder

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("handler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COMFY_BASE_URL = "http://127.0.0.1:8188"
COMFY_TIMEOUT_PER_IMAGE = 300  # seconds

# Resolutions (all 9:16 ratio)
BG_WIDTH, BG_HEIGHT = 576, 1024
COSTUME_WIDTH, COSTUME_HEIGHT = 576, 1024

JPEG_QUALITY = 95  # high quality, keeps payload under RunPod's 1MB/chunk limit


# ---------------------------------------------------------------------------
# Cold-start initialization
# ---------------------------------------------------------------------------
def wait_for_comfyui(timeout=300):
    """Block until ComfyUI server is ready."""
    client = ComfyUIClient(COMFY_BASE_URL)
    start = time.time()
    while time.time() - start < timeout:
        if client.check_server():
            logger.info("ComfyUI server is ready (%.1fs)", time.time() - start)
            return
        time.sleep(0.5)
    raise RuntimeError(f"ComfyUI server did not start within {timeout}s")


logger.info("=== DashynAssetGen cold start ===")

# Step 1: Ensure models are on the network volume
try:
    model_actions = ensure_models_available()
    for action in model_actions:
        logger.info("Model: %s", action)
except RuntimeError as e:
    logger.error("Model setup failed: %s", e)

# Step 2: Wait for ComfyUI server
try:
    wait_for_comfyui(timeout=300)
except RuntimeError as e:
    logger.error("ComfyUI startup failed: %s", e)


# ---------------------------------------------------------------------------
# Mode: generate_prompts — Claude API call only, returns prompt texts
# ---------------------------------------------------------------------------
def _generate_prompts(job_input):
    vibe_name = job_input.get("vibe_name")
    vibe_description = job_input.get("vibe_description")
    num_assets = job_input.get("num_assets", 2)

    if not vibe_name:
        yield {"type": "error", "error": "vibe_name is required"}
        return
    if not vibe_description:
        yield {"type": "error", "error": "vibe_description is required"}
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield {"type": "error", "error": "ANTHROPIC_API_KEY not set"}
        return

    logger.info("Generating prompts: vibe='%s', num_assets=%d", vibe_name, num_assets)

    try:
        generator = PromptGenerator(api_key=api_key)
        prompts = generator.generate_prompts(vibe_name, vibe_description, num_assets)
    except Exception as e:
        logger.error("Prompt generation failed: %s", e)
        yield {"type": "error", "error": f"Prompt generation failed: {e}"}
        return

    logger.info(
        "Prompts ready: %d backgrounds, %d female, %d male",
        len(prompts.get("backgrounds", [])),
        len(prompts.get("female", [])),
        len(prompts.get("male", [])),
    )

    yield {
        "type": "prompts",
        "vibe_name": vibe_name,
        "prompts": prompts,
    }


# ---------------------------------------------------------------------------
# Mode: render_image — ONE prompt → ONE image via ComfyUI
# ---------------------------------------------------------------------------
def _render_image(job_input):
    vibe_name = job_input.get("vibe_name", "unknown")
    category = job_input.get("category", "unknown")
    prompt = job_input.get("prompt", "")
    width = job_input.get("width", 576)
    height = job_input.get("height", 1024)

    if not prompt:
        yield {"type": "error", "error": "prompt is required"}
        return

    logger.info("Rendering image: vibe='%s', category='%s', %dx%d", vibe_name, category, width, height)

    try:
        client = ComfyUIClient(COMFY_BASE_URL)
        builder = WorkflowBuilder()

        workflow = builder.build_t2i_workflow(prompt=prompt, width=width, height=height)
        prompt_id = client.submit_workflow(workflow)
        result = client.wait_and_fetch(prompt_id, timeout=COMFY_TIMEOUT_PER_IMAGE)

        img = Image.open(io.BytesIO(result["image_data"]))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        logger.info("  Rendered: %s/%s (%.1f KB)", category, vibe_name, len(img_b64) / 1024)

        yield {
            "type": "image",
            "category": category,
            "image_base64": img_b64,
            "vibe_name": vibe_name,
        }

    except Exception as e:
        logger.error("Render failed: %s/%s: %s", category, vibe_name, e)
        yield {"type": "error", "error": f"Render failed: {e}", "category": category, "vibe_name": vibe_name}


# ---------------------------------------------------------------------------
# Mode: full — original all-in-one pipeline (backward compatibility)
# ---------------------------------------------------------------------------
def _full_pipeline(job_input):
    vibe_name = job_input.get("vibe_name")
    vibe_description = job_input.get("vibe_description")
    num_assets = job_input.get("num_assets", 2)

    if not vibe_name:
        yield {"type": "error", "error": "vibe_name is required"}
        return
    if not vibe_description:
        yield {"type": "error", "error": "vibe_description is required"}
        return
    if not isinstance(num_assets, int) or num_assets < 1:
        yield {"type": "error", "error": "num_assets must be a positive integer"}
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield {"type": "error", "error": "ANTHROPIC_API_KEY not set"}
        return

    logger.info(
        "Full pipeline: vibe='%s', num_assets=%d (total=%d)",
        vibe_name, num_assets, num_assets * 3,
    )

    # Generate prompts
    try:
        generator = PromptGenerator(api_key=api_key)
        prompts = generator.generate_prompts(vibe_name, vibe_description, num_assets)
    except Exception as e:
        logger.error("Prompt generation failed: %s", e)
        yield {"type": "error", "error": f"Prompt generation failed: {e}"}
        return

    # Build task list
    tasks = []
    for i, pt in enumerate(prompts.get("backgrounds", [])):
        tasks.append(("backgrounds", f"bg_{i + 1}.jpg", pt, BG_WIDTH, BG_HEIGHT))
    for i, pt in enumerate(prompts.get("female", [])):
        tasks.append(("female", f"female_{i + 1}.jpg", pt, COSTUME_WIDTH, COSTUME_HEIGHT))
    for i, pt in enumerate(prompts.get("male", [])):
        tasks.append(("male", f"male_{i + 1}.jpg", pt, COSTUME_WIDTH, COSTUME_HEIGHT))

    yield {
        "type": "progress",
        "stage": "prompts_ready",
        "vibe_name": vibe_name,
        "total_images": len(tasks),
    }

    # Submit all workflows
    client = ComfyUIClient(COMFY_BASE_URL)
    builder = WorkflowBuilder()
    submitted = []
    warnings = []

    for idx, (category, filename, prompt_text, w, h) in enumerate(tasks):
        try:
            workflow = builder.build_t2i_workflow(prompt=prompt_text, width=w, height=h)
            prompt_id = client.submit_workflow(workflow)
            submitted.append((category, filename, prompt_id))
        except Exception as e:
            msg = f"Failed to queue {category}/{filename}: {e}"
            logger.warning(msg)
            warnings.append(msg)

    # Collect results and stream each image
    success_count = 0
    for idx, (category, filename, prompt_id) in enumerate(submitted):
        try:
            result = client.wait_and_fetch(prompt_id, timeout=COMFY_TIMEOUT_PER_IMAGE)
            img = Image.open(io.BytesIO(result["image_data"]))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY)
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            yield {
                "type": "image",
                "category": category,
                "filename": filename,
                "image_base64": img_b64,
                "index": idx + 1,
                "total": len(submitted),
                "vibe_name": vibe_name,
            }
            success_count += 1
        except Exception as e:
            warnings.append(f"Failed {category}/{filename}: {e}")

    if success_count == 0:
        yield {"type": "error", "error": "All generations failed", "details": warnings}
        return

    yield {
        "type": "complete",
        "vibe_name": vibe_name,
        "total_images": success_count,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Main handler — routes by mode
# ---------------------------------------------------------------------------
def handler(job):
    """RunPod serverless generator handler with mode routing.

    Modes:
        generate_prompts — Claude API → prompt texts (fast, no GPU)
        render_image     — one prompt → one JPEG image (parallelizable across workers)
        full (default)   — all-in-one pipeline
    """
    job_input = job.get("input", {})
    mode = job_input.get("mode", "full")

    if mode == "generate_prompts":
        yield from _generate_prompts(job_input)
    elif mode == "render_image":
        yield from _render_image(job_input)
    else:
        yield from _full_pipeline(job_input)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
runpod.serverless.start({"handler": handler, "return_aggregate_stream": True})
