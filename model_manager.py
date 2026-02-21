"""
Model download and cache management for Qwen-Image on RunPod network volume.
"""

import logging
import os

logger = logging.getLogger(__name__)

# All models from Comfy-Org/Qwen-Image_ComfyUI (public, no auth needed)
# Files are under split_files/ subdirectory in the repo
REPO_ID = "Comfy-Org/Qwen-Image_ComfyUI"

MODELS = [
    {
        "filename": "qwen_image_fp8_e4m3fn.safetensors",
        "repo_path": "split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors",
        "subdir": "diffusion_models",
    },
    {
        "filename": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "repo_path": "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "subdir": "text_encoders",
    },
    {
        "filename": "qwen_image_vae.safetensors",
        "repo_path": "split_files/vae/qwen_image_vae.safetensors",
        "subdir": "vae",
    },
]

VOLUME_BASE = "/runpod-volume/models"


def ensure_models_available(volume_base=VOLUME_BASE):
    """Check if all required model files exist on the network volume.
    Downloads any missing files from HuggingFace.

    Args:
        volume_base: Base path for model storage on the network volume.

    Returns:
        list[str]: Actions taken (e.g., "downloaded X", "found X").

    Raises:
        RuntimeError: If a download fails.
    """
    actions = []

    for model in MODELS:
        target_dir = os.path.join(volume_base, model["subdir"])
        target_path = os.path.join(target_dir, model["filename"])

        if os.path.exists(target_path):
            size_mb = os.path.getsize(target_path) / (1024 * 1024)
            logger.info("Model found: %s (%.1f MB)", target_path, size_mb)
            actions.append(f"found {model['filename']} ({size_mb:.0f} MB)")
            continue

        # Model not found — download from HuggingFace
        logger.info(
            "Model not found: %s — downloading from %s",
            target_path,
            REPO_ID,
        )

        os.makedirs(target_dir, exist_ok=True)

        try:
            from huggingface_hub import hf_hub_download

            hf_token = os.environ.get("HF_TOKEN")

            downloaded_path = hf_hub_download(
                repo_id=REPO_ID,
                filename=model["repo_path"],
                local_dir=target_dir,
                local_dir_use_symlinks=False,
                token=hf_token,
            )

            # hf_hub_download preserves repo subdir structure, so move file
            # from split_files/subdir/filename to just filename in target_dir
            import shutil
            if downloaded_path != target_path and os.path.exists(downloaded_path):
                shutil.move(downloaded_path, target_path)
                # Clean up empty split_files directory
                split_dir = os.path.join(target_dir, "split_files")
                if os.path.exists(split_dir):
                    shutil.rmtree(split_dir, ignore_errors=True)

            logger.info("Downloaded model to: %s", target_path)
            actions.append(f"downloaded {model['filename']}")

        except Exception as e:
            raise RuntimeError(
                f"Model download failed for {model['filename']} "
                f"from {REPO_ID}: {e}. "
                f"Check HF_TOKEN environment variable."
            ) from e

    return actions
