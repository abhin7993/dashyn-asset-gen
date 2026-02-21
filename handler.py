"""
DashynAssetGen — RunPod Serverless Handler (Streaming)

Receives a vibe description, generates image prompts via Claude API,
runs them through Qwen-Image T2I via ComfyUI, and streams each image
back individually as JPEG base64 via RunPod's generator protocol.
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
# Handler (generator — yields each image as it's generated)
# ---------------------------------------------------------------------------
def handler(job):
    """RunPod serverless generator handler.

    Input JSON:
        {
            "vibe_name": "string",
            "vibe_description": "string",
            "num_assets": integer
        }

    Yields (via RunPod streaming):
        {"type": "progress", "stage": "prompts_ready", ...}
        {"type": "image", "category": "...", "image_base64": "...", ...}  (per image)
        {"type": "complete", "total_images": N, ...}
    """
    job_input = job.get("input", {})

    # --- Extract & validate input ---
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
        yield {"type": "error", "error": "ANTHROPIC_API_KEY environment variable not set"}
        return

    logger.info(
        "Job started: vibe='%s', num_assets=%d (total images=%d)",
        vibe_name, num_assets, num_assets * 3,
    )

    # --- Generate prompts via Claude API ---
    try:
        generator = PromptGenerator(api_key=api_key)
        prompts = generator.generate_prompts(vibe_name, vibe_description, num_assets)
    except Exception as e:
        logger.error("Prompt generation failed: %s", e)
        yield {"type": "error", "error": f"Prompt generation failed: {e}"}
        return

    logger.info(
        "Prompts generated: %d backgrounds, %d female, %d male",
        len(prompts.get("backgrounds", [])),
        len(prompts.get("female", [])),
        len(prompts.get("male", [])),
    )

    # --- Build task list ---
    tasks = []
    for i, prompt_text in enumerate(prompts.get("backgrounds", [])):
        tasks.append(("backgrounds", f"bg_{i + 1}.jpg", prompt_text, BG_WIDTH, BG_HEIGHT))
    for i, prompt_text in enumerate(prompts.get("female", [])):
        tasks.append(("female", f"female_{i + 1}.jpg", prompt_text, COSTUME_WIDTH, COSTUME_HEIGHT))
    for i, prompt_text in enumerate(prompts.get("male", [])):
        tasks.append(("male", f"male_{i + 1}.jpg", prompt_text, COSTUME_WIDTH, COSTUME_HEIGHT))

    yield {
        "type": "progress",
        "stage": "prompts_ready",
        "vibe_name": vibe_name,
        "total_images": len(tasks),
    }

    # --- Phase 1: Submit ALL workflows to ComfyUI queue ---
    client = ComfyUIClient(COMFY_BASE_URL)
    builder = WorkflowBuilder()
    submitted = []  # (category, filename, prompt_id)
    warnings = []

    for idx, (category, filename, prompt_text, w, h) in enumerate(tasks):
        logger.info(
            "[%d/%d] Queuing %s/%s (%dx%d)",
            idx + 1, len(tasks), category, filename, w, h,
        )
        try:
            workflow = builder.build_t2i_workflow(prompt=prompt_text, width=w, height=h)
            prompt_id = client.submit_workflow(workflow)
            submitted.append((category, filename, prompt_id))
        except Exception as e:
            msg = f"Failed to queue {category}/{filename}: {e}"
            logger.warning(msg)
            warnings.append(msg)

    logger.info("Queued %d/%d workflows, collecting results...", len(submitted), len(tasks))

    # --- Phase 2: Collect results and stream each image ---
    success_count = 0
    for idx, (category, filename, prompt_id) in enumerate(submitted):
        logger.info(
            "[%d/%d] Waiting for %s/%s (prompt_id=%s)",
            idx + 1, len(submitted), category, filename, prompt_id,
        )
        try:
            result = client.wait_and_fetch(prompt_id, timeout=COMFY_TIMEOUT_PER_IMAGE)

            # Convert PNG from ComfyUI → JPEG for smaller payload
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
            logger.info("  Streamed: %s/%s (%.1f KB)", category, filename, len(img_b64) / 1024)

        except Exception as e:
            msg = f"Failed to generate {category}/{filename}: {e}"
            logger.warning(msg)
            warnings.append(msg)

    if success_count == 0:
        yield {"type": "error", "error": "All image generations failed", "details": warnings}
        return

    yield {
        "type": "complete",
        "vibe_name": vibe_name,
        "total_images": success_count,
        "warnings": warnings,
    }

    logger.info("Job complete: %d/%d images streamed", success_count, len(tasks))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
runpod.serverless.start({"handler": handler, "return_aggregate_stream": True})
