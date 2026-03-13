"""Stage: LLM interpretation — enrich a raw request into a 3D-generation prompt."""

from __future__ import annotations

import logging

from pipeline.models.job import JobStage, PrintJob
from pipeline.services.openai_client import interpret_request
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


async def run(job: PrintJob, settings: Settings) -> str:
    """Run LLM interpretation. Returns a human-readable summary for approval."""
    job.advance(JobStage.INTERPRETING)

    result = await interpret_request(
        raw_request=job.raw_request,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )

    job.artifacts.enriched_prompt = result.enriched_prompt
    job.artifacts.object_name = result.object_name
    job.artifacts.suggested_scale_mm = result.suggested_scale_mm
    job.artifacts.suggested_material = result.suggested_material

    job.advance(JobStage.AWAITING_INTERPRET_APPROVAL)

    summary = (
        f"🤖 *LLM Interpretation Complete*\n\n"
        f"**Object:** {result.object_name}\n"
        f"**Scale:** {result.suggested_scale_mm:.0f}mm\n"
        f"**Material:** {result.suggested_material}\n"
        f"**Style:** {result.art_style}\n"
        f"**Orientation:** {result.orientation_notes}\n\n"
        f"**3D Prompt:**\n_{result.enriched_prompt}_\n\n"
        f"Approve to generate 3D model, or reject to re-prompt."
    )
    return summary
