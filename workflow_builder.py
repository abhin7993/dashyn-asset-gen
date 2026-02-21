"""
Builds ComfyUI API-format workflow JSON for Qwen-Image text-to-image generation.
"""

import random


class WorkflowBuilder:
    """Constructs ComfyUI workflow JSONs for Qwen-Image T2I."""

    def __init__(
        self,
        unet_model="qwen_image_fp8_e4m3fn.safetensors",
        clip_model="qwen_2.5_vl_7b_fp8_scaled.safetensors",
        vae_model="qwen_image_vae.safetensors",
        steps=15,
        cfg=1.0,
        sampler_name="euler",
        scheduler="simple",
        auraflow_shift=3.1,
    ):
        self.unet_model = unet_model
        self.clip_model = clip_model
        self.vae_model = vae_model
        self.steps = steps
        self.cfg = cfg
        self.sampler_name = sampler_name
        self.scheduler = scheduler
        self.auraflow_shift = auraflow_shift

    def build_t2i_workflow(self, prompt, width=1024, height=1024, seed=None):
        """Build a text-to-image workflow for Qwen-Image.

        Args:
            prompt: Text prompt for image generation.
            width: Output image width (576 for 9:16 ratio).
            height: Output image height (1024 for 9:16 ratio).
            seed: Random seed. If None, generates a random one.

        Returns:
            dict: ComfyUI API-format workflow JSON.
        """
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        return {
            # Load diffusion model
            "1": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": self.unet_model,
                    "weight_dtype": "fp8_e4m3fn",
                },
            },
            # Model sampling config for Qwen
            "2": {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {
                    "shift": self.auraflow_shift,
                    "model": ["1", 0],
                },
            },
            # Load text encoder
            "3": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": self.clip_model,
                    "type": "qwen_image",
                    "device": "default",
                },
            },
            # Positive prompt
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt,
                    "clip": ["3", 0],
                },
            },
            # Negative prompt (empty — CFG=1.0 ignores this)
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": "",
                    "clip": ["3", 0],
                },
            },
            # Empty latent (SD3 format for Qwen)
            "6": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
            },
            # Sampler
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": self.steps,
                    "cfg": self.cfg,
                    "sampler_name": self.sampler_name,
                    "scheduler": self.scheduler,
                    "denoise": 1.0,
                    "model": ["2", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                },
            },
            # Load VAE
            "8": {
                "class_type": "VAELoader",
                "inputs": {
                    "vae_name": self.vae_model,
                },
            },
            # Decode latent to image
            "9": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["7", 0],
                    "vae": ["8", 0],
                },
            },
            # Save output
            "10": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "dashyn_asset",
                    "images": ["9", 0],
                },
            },
        }
