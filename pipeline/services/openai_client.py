"""OpenAI client — prompt enrichment for 3D-printable object descriptions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a 3D-printing expert assistant. The user will give you a short description of an
object they want to 3D print. Your job is to:

1. Produce a detailed, vivid description of the object optimised for text-to-3D AI generation.
   Focus on shape, proportions, pose, surface detail, and aesthetic style.
   Keep it under 200 words.
2. Extract structured metadata.

Respond in this exact JSON format (no markdown fences):
{
  "enriched_prompt": "...",
  "object_name": "short name, e.g. Cinderella figurine",
  "suggested_scale_mm": 120,
  "suggested_material": "PLA",
  "art_style": "Disney-inspired cartoon" or "realistic" or "low-poly" etc.,
  "orientation_notes": "brief note on best print orientation"
}
"""


@dataclass
class InterpretResult:
    enriched_prompt: str
    object_name: str
    suggested_scale_mm: float
    suggested_material: str
    art_style: str
    orientation_notes: str


async def interpret_request(
    raw_request: str,
    api_key: str,
    model: str = "gpt-4o",
    base_url: str = "https://api.openai.com/v1",
) -> InterpretResult:
    """Use an OpenAI-compatible LLM to enrich a raw user request."""
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    log.info("Sending to %s: %r", model, raw_request)
    resp = await client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_request},
        ],
        temperature=0.7,
        max_tokens=600,
    )

    import json

    text = resp.choices[0].message.content or "{}"
    data = json.loads(text)
    log.info("LLM response: %s", data)

    return InterpretResult(
        enriched_prompt=data.get("enriched_prompt", raw_request),
        object_name=data.get("object_name", "unknown"),
        suggested_scale_mm=float(data.get("suggested_scale_mm", 100)),
        suggested_material=data.get("suggested_material", "PLA"),
        art_style=data.get("art_style", ""),
        orientation_notes=data.get("orientation_notes", ""),
    )
