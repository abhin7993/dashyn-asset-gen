"""
DashynAssetGen — RunPod Serverless Handler

Receives a vibe description, generates image prompts via Claude API,
runs them through Qwen-Image T2I via ComfyUI, and returns a base64-encoded
zip of organized assets.
"""

import base64
import logging
import os
import tempfile
import time
import zipfile

import runpod

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

# Resolutions
BG_WIDTH, BG_HEIGHT = 1024, 1024
COSTUME_WIDTH, COSTUME_HEIGHT = 768, 1024


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
    # We'll let the handler return this error per-job rather than crashing

# Step 2: Wait for ComfyUI server
try:
    wait_for_comfyui(timeout=300)
except RuntimeError as e:
    logger.error("ComfyUI startup failed: %s", e)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def handler(job):
    """Main RunPod serverless handler.

    Input JSON:
        {
            "vibe_name": "string",
            "vibe_description": "string",
            "num_assets": integer
        }

    Returns:
        {
            "zip_base64": "...",
            "vibe_name": "...",
            "total_images": N
        }
    """
    job_input = job.get("input", {})

    # --- Extract & validate input ---
    vibe_name = job_input.get("vibe_name")
    vibe_description = job_input.get("vibe_description")
    num_assets = job_input.get("num_assets", 2)

    if not vibe_name:
        return {"error": "vibe_name is required", "status": "failed"}
    if not vibe_description:
        return {"error": "vibe_description is required", "status": "failed"}
    if not isinstance(num_assets, int) or num_assets < 1:
        return {"error": "num_assets must be a positive integer", "status": "failed"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "error": "ANTHROPIC_API_KEY environment variable not set",
            "status": "failed",
        }

    logger.info(
        "Job started: vibe='%s', num_assets=%d (total images=%d)",
        vibe_name,
        num_assets,
        num_assets * 3,
    )

    # --- Generate prompts via Claude API ---
    try:
        generator = PromptGenerator(api_key=api_key)
        prompts = generator.generate_prompts(vibe_name, vibe_description, num_assets)
    except Exception as e:
        logger.error("Prompt generation failed: %s", e)
        return {"error": f"Prompt generation failed: {e}", "status": "failed"}

    logger.info(
        "Prompts generated: %d backgrounds, %d female, %d male",
        len(prompts.get("backgrounds", [])),
        len(prompts.get("female", [])),
        len(prompts.get("male", [])),
    )

    # --- Generate images via ComfyUI ---
    client = ComfyUIClient(COMFY_BASE_URL)
    builder = WorkflowBuilder()

    # Build task list: (category_folder, filename, prompt, width, height)
    tasks = []
    for i, prompt_text in enumerate(prompts.get("backgrounds", [])):
        tasks.append(("backgrounds", f"bg_{i + 1}.png", prompt_text, BG_WIDTH, BG_HEIGHT))
    for i, prompt_text in enumerate(prompts.get("female", [])):
        tasks.append(("female", f"female_{i + 1}.png", prompt_text, COSTUME_WIDTH, COSTUME_HEIGHT))
    for i, prompt_text in enumerate(prompts.get("male", [])):
        tasks.append(("male", f"male_{i + 1}.png", prompt_text, COSTUME_WIDTH, COSTUME_HEIGHT))

    with tempfile.TemporaryDirectory() as tmpdir:
        generated = []  # (category, filename, filepath)
        warnings = []

        for idx, (category, filename, prompt_text, w, h) in enumerate(tasks):
            logger.info(
                "[%d/%d] Generating %s/%s (%dx%d)",
                idx + 1, len(tasks), category, filename, w, h,
            )
            logger.info("  Prompt: %.100s...", prompt_text)

            try:
                workflow = builder.build_t2i_workflow(
                    prompt=prompt_text, width=w, height=h
                )
                result = client.run_workflow(workflow, timeout=COMFY_TIMEOUT_PER_IMAGE)

                # Save image to category subfolder
                cat_dir = os.path.join(tmpdir, category)
                os.makedirs(cat_dir, exist_ok=True)
                filepath = os.path.join(cat_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(result["image_data"])

                generated.append((category, filename, filepath))
                logger.info("  Saved: %s/%s", category, filename)

            except Exception as e:
                msg = f"Failed to generate {category}/{filename}: {e}"
                logger.warning(msg)
                warnings.append(msg)

        if not generated:
            return {
                "error": "All image generations failed",
                "details": warnings,
                "status": "failed",
            }

        # --- Create zip ---
        zip_path = os.path.join(tmpdir, f"{vibe_name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for category, filename, filepath in generated:
                arcname = f"{category}/{filename}"
                zf.write(filepath, arcname)

        logger.info(
            "Zip created: %s (%.1f MB, %d images)",
            zip_path,
            os.path.getsize(zip_path) / (1024 * 1024),
            len(generated),
        )

        # --- Base64 encode ---
        with open(zip_path, "rb") as f:
            zip_base64 = base64.b64encode(f.read()).decode("utf-8")

    # --- Return response ---
    response = {
        "zip_base64": zip_base64,
        "vibe_name": vibe_name,
        "total_images": len(generated),
    }

    if warnings:
        response["warnings"] = warnings

    logger.info(
        "Job complete: %d/%d images generated", len(generated), len(tasks)
    )
    return response


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
runpod.serverless.start({"handler": handler})
