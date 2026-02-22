"""
Claude API integration for generating image prompts from a vibe description.
"""

import logging
import time

from anthropic import Anthropic, RateLimitError, APIStatusError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a prompt engineer for Qwen-Image, an AI text-to-image generation model.
Generate vivid, detailed image generation prompts optimized for photorealistic output.

Guidelines:
- Each prompt must be self-contained and richly descriptive.
- Focus on: realistic fabric textures, natural lighting, true-to-life colors, sharp details, and professional composition.
- Art style: photorealistic, professional fashion photography, ultra-high detail, 8K quality, studio lighting.
- Every prompt MUST begin with "photorealistic, professional fashion photography, " to anchor the style.
- Do NOT include text, watermarks, or UI elements in prompt descriptions.
- Do NOT include any artistic or illustrated styles — output must look like a real photograph.

CRITICAL gender rules for outfit categories:
- "female" outfits MUST only contain women's clothing: sarees, lehengas, gowns, dresses, salwar kameez, skirts, blouses, feminine tops, heels, sandals, women's jewelry, dupattas, etc.
- "male" outfits MUST only contain men's clothing: sherwanis, kurta-pajama, suits, blazers, trousers, shirts, dhotis, turbans, men's shoes/juttis, men's watches, etc.
- NEVER mix genders — a male prompt must NEVER include sarees, lehengas, gowns, dupattas, heels, or any women's garments.
- NEVER mix genders — a female prompt must NEVER include sherwanis, suits with trousers, dhotis, turbans, or any men's garments.
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

1. "backgrounds" — {num_assets} unique portrait photography backdrop scenes (9:16 vertical portrait format).
   Each background MUST be framed as a professional portrait photography location — the kind of spot where
   a photographer would position a person to take a stunning portrait photo.
   MUST have: clear central space for a subject to stand, beautiful depth-of-field bokeh in the background,
   professional-grade natural or ambient lighting (golden hour, soft diffused light, dramatic rim lighting, etc.),
   eye-level or slightly low camera angle for a flattering portrait perspective.
   The locations must match the vibe aesthetic — e.g. beach vibes get tropical shores, urban vibes get city streets,
   royal vibes get palace interiors, etc. Choose locations that naturally fit the theme.
   NO people in the scene.
   Style: photorealistic photograph, shallow depth of field, cinematic lighting, sharp focus on foreground, 8K quality.

2. "female" — {num_assets} WOMEN'S outfit/costume prompts (9:16 vertical portrait format).
   Photorealistic product photography of a COMPLETE WOMEN'S clothing ensemble on a plain/neutral background.
   FULL-LENGTH from head accessory to footwear — MUST show the entire outfit top to bottom including shoes/sandals/heels.
   NO face or person — clothing only, displayed as if on an invisible mannequin or flat lay.
   ONLY women's garments: sarees, lehengas, gowns, dresses, salwar kameez, skirts, feminine tops, women's jewelry, dupattas, heels, etc.
   NEVER include any men's clothing items like sherwanis, suits with trousers, dhotis, or turbans.
   Include all accessories and footwear. The entire outfit must be visible, never cropped at knee or waist.

3. "male" — {num_assets} MEN'S outfit/costume prompts (9:16 vertical portrait format).
   Photorealistic product photography of a COMPLETE MEN'S clothing ensemble on a plain/neutral background.
   FULL-LENGTH from headwear to footwear — MUST show the entire outfit top to bottom including shoes/juttis.
   NO face or person — clothing only, displayed as if on an invisible mannequin or flat lay.
   ONLY men's garments: sherwanis, kurta-pajama, suits, blazers, trousers, shirts, dhotis, turbans, men's shoes/juttis, etc.
   NEVER include any women's clothing items like sarees, lehengas, gowns, dupattas, heels, or women's jewelry.
   Include all accessories and footwear. The entire outfit must be visible, never cropped at knee or waist.

IMPORTANT: Every prompt MUST start with "photorealistic, professional fashion photography, " to ensure realistic output.
Each prompt should be 3-5 sentences of vivid, photorealistic visual description with specific fabric textures, colors, and material details."""

        tool_schema = {
            "name": "generate_prompts",
            "description": "Generate structured image generation prompts for each category.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "backgrounds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Photorealistic background/environment scene prompts",
                    },
                    "female": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Women's outfit prompts ONLY — must contain exclusively feminine garments, never men's clothing",
                    },
                    "male": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Men's outfit prompts ONLY — must contain exclusively masculine garments, never women's clothing",
                    },
                },
                "required": ["backgrounds", "female", "male"],
            },
        }

        logger.info(
            "Generating prompts for vibe='%s', num_assets=%d", vibe_name, num_assets
        )

        max_retries = 5
        last_error = None

        for attempt in range(max_retries):
            try:
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

            except RateLimitError as e:
                last_error = e
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                logger.warning(
                    "Rate limited on attempt %d/%d for vibe='%s', retrying in %ds",
                    attempt + 1, max_retries, vibe_name, wait,
                )
                time.sleep(wait)

            except APIStatusError as e:
                if e.status_code == 529:  # overloaded
                    last_error = e
                    wait = 2 ** attempt
                    logger.warning(
                        "API overloaded on attempt %d/%d for vibe='%s', retrying in %ds",
                        attempt + 1, max_retries, vibe_name, wait,
                    )
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError(
            f"Claude API failed after {max_retries} retries: {last_error}"
        )
