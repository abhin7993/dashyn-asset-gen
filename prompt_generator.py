"""
Claude API integration for generating image prompts from a vibe description.
"""

import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a prompt engineer for Qwen-Image, an AI text-to-image generation model.
Generate vivid, detailed image generation prompts optimized for high-quality output.

Guidelines:
- Each prompt must be self-contained and richly descriptive.
- Focus on: visual style, lighting, mood, color palette, composition, specific details.
- Art style: semi-realistic digital art, high detail, professional quality.
- Do NOT include text, watermarks, or UI elements in prompt descriptions.
"""


class PromptGenerator:
    """Generates structured image prompts via the Claude API."""

    def __init__(self, api_key, model="claude-haiku-4-5-20251001"):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate_prompts(self, vibe_name, vibe_description, num_assets):
        """Generate prompts for all three asset categories in a single API call.

        Args:
            vibe_name: Name of the vibe/theme.
            vibe_description: Detailed description of the aesthetic.
            num_assets: Number of prompts to generate per category.

        Returns:
            dict with keys: backgrounds (list[str]), female (list[str]), male (list[str])
        """
        user_prompt = f"""\
Vibe: "{vibe_name}"
Description: {vibe_description}

Generate image prompts for this vibe across three categories:

1. "backgrounds" — {num_assets} unique background/environment scenes (9:16 vertical portrait format).
   These should be varied environments matching the vibe aesthetic. No people in the scene.
   Focus on architecture, landscapes, interiors, or atmospheric settings. Composed for vertical framing.

2. "female" — {num_assets} female outfit/costume prompts (9:16 vertical portrait format).
   FULL-LENGTH outfit from head to toe displayed on a plain/neutral background. Fashion photography style.
   MUST show the COMPLETE clothing ensemble from top to bottom including footwear/shoes/sandals.
   NO face or person — clothing only, displayed as if on an invisible mannequin or laid flat.
   Include all accessories and footwear. The entire outfit must be visible, never cropped at knee or waist.

3. "male" — {num_assets} male outfit/costume prompts (9:16 vertical portrait format).
   Same style as female — FULL-LENGTH outfit from head to toe on neutral background, clothing only, no face.
   MUST show complete outfit including footwear/shoes. Never crop at knee or waist.
   Include all accessories and footwear.

Each prompt should be 2-4 sentences of vivid visual description."""

        tool_schema = {
            "name": "generate_prompts",
            "description": "Generate structured image generation prompts for each category.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "backgrounds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Background/environment scene prompts",
                    },
                    "female": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Female outfit/costume prompts",
                    },
                    "male": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Male outfit/costume prompts",
                    },
                },
                "required": ["backgrounds", "female", "male"],
            },
        }

        logger.info(
            "Generating prompts for vibe='%s', num_assets=%d", vibe_name, num_assets
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": "generate_prompts"},
        )

        # Extract structured output from tool call
        for block in response.content:
            if block.type == "tool_use" and block.name == "generate_prompts":
                prompts = block.input
                logger.info(
                    "Generated prompts: %d backgrounds, %d female, %d male",
                    len(prompts.get("backgrounds", [])),
                    len(prompts.get("female", [])),
                    len(prompts.get("male", [])),
                )
                return prompts

        raise RuntimeError(
            "Claude API did not return expected tool_use response"
        )
