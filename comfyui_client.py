"""
ComfyUI HTTP API wrapper for submitting workflows and retrieving images.
"""

import json
import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """HTTP client for interacting with a local ComfyUI server."""

    def __init__(self, base_url="http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")

    def check_server(self):
        """Check if ComfyUI server is responding.

        Returns:
            bool: True if server is healthy.
        """
        try:
            resp = requests.get(f"{self.base_url}/system_stats", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def submit_workflow(self, workflow_json):
        """Submit a workflow to ComfyUI without waiting for completion.

        Returns:
            str: prompt_id for tracking.

        Raises:
            RuntimeError: If submission fails.
        """
        client_id = str(uuid.uuid4())

        resp = requests.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow_json, "client_id": client_id},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"ComfyUI /prompt returned {resp.status_code}: {resp.text}"
            )

        result = resp.json()
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"No prompt_id in ComfyUI response: {result}")

        logger.info("Submitted workflow, prompt_id=%s", prompt_id)
        return prompt_id

    def wait_and_fetch(self, prompt_id, timeout=300):
        """Wait for a submitted workflow to complete and fetch the image.

        Args:
            prompt_id: The prompt_id from submit_workflow().
            timeout: Max seconds to wait.

        Returns:
            dict with keys: image_data (bytes), filename (str).

        Raises:
            RuntimeError: If workflow fails or times out.
        """
        image_info = self._poll_history(prompt_id, timeout)

        image_data = self._fetch_image(
            image_info["filename"],
            image_info.get("subfolder", ""),
            image_info.get("type", "output"),
        )

        return {"image_data": image_data, "filename": image_info["filename"]}

    def run_workflow(self, workflow_json, timeout=300):
        """Submit a workflow and wait for the output image (convenience method).

        Args:
            workflow_json: ComfyUI API-format workflow dict.
            timeout: Max seconds to wait for completion.

        Returns:
            dict with keys: image_data (bytes), filename (str).

        Raises:
            RuntimeError: If workflow fails or times out.
        """
        prompt_id = self.submit_workflow(workflow_json)
        return self.wait_and_fetch(prompt_id, timeout)

    def _poll_history(self, prompt_id, timeout):
        """Poll /history until the workflow completes.

        Returns:
            dict with image info (filename, subfolder, type).

        Raises:
            RuntimeError: On timeout or execution error.
        """
        start = time.time()

        while time.time() - start < timeout:
            time.sleep(1)

            try:
                resp = requests.get(
                    f"{self.base_url}/history/{prompt_id}", timeout=10
                )
            except requests.RequestException as e:
                logger.warning("History poll failed: %s", e)
                continue

            if resp.status_code != 200:
                continue

            history = resp.json()
            if prompt_id not in history:
                continue

            entry = history[prompt_id]

            # Check for execution errors
            if entry.get("status", {}).get("status_str") == "error":
                messages = entry.get("status", {}).get("messages", [])
                raise RuntimeError(f"ComfyUI execution error: {messages}")

            # Find output images from any SaveImage node
            outputs = entry.get("outputs", {})
            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    img = images[0]
                    logger.info(
                        "Workflow complete, output: %s", img.get("filename")
                    )
                    return {
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    }

            # Output exists but no images found — treat as error
            if outputs:
                raise RuntimeError(
                    f"Workflow completed but no images in output: {outputs}"
                )

        raise RuntimeError(
            f"ComfyUI workflow timed out after {timeout}s (prompt_id={prompt_id})"
        )

    def _fetch_image(self, filename, subfolder, img_type):
        """Download an image from ComfyUI's /view endpoint.

        Returns:
            bytes: Raw image data.
        """
        resp = requests.get(
            f"{self.base_url}/view",
            params={
                "filename": filename,
                "subfolder": subfolder,
                "type": img_type,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch image {filename}: HTTP {resp.status_code}"
            )

        return resp.content
