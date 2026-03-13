"""Stage: 3D mesh generation via Meshy.ai or Tripo3D."""

from __future__ import annotations

import logging
from pathlib import Path

from pipeline.models.job import JobStage, PrintJob
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


async def run(
    job: PrintJob,
    settings: Settings,
    progress_callback=None,
) -> tuple[str, str | None]:
    """Generate the 3D model. Returns (summary_text, thumbnail_path_or_None)."""
    job.advance(JobStage.GENERATING)

    prompt = job.artifacts.enriched_prompt or job.raw_request
    staging = Path(settings.staging_dir) / job.id
    staging.mkdir(parents=True, exist_ok=True)

    provider = settings.mesh_provider.lower()

    if provider == "tripo":
        if not settings.tripo_api_key:
            raise RuntimeError("TRIPO_API_KEY is required when mesh_provider=tripo")
        from pipeline.services.tripo_client import generate_and_download as tripo_gen

        result = await tripo_gen(
            prompt=prompt,
            api_key=settings.tripo_api_key,
            staging_dir=staging,
            poll_interval=settings.mesh_poll_interval,
            poll_timeout=settings.mesh_poll_timeout,
            progress_callback=progress_callback,
        )
    elif provider == "meshy":
        if not settings.meshy_api_key:
            raise RuntimeError("MESHY_API_KEY is required when mesh_provider=meshy")
        from pipeline.services.meshy_client import generate_and_download as meshy_gen

        result = await meshy_gen(
            prompt=prompt,
            api_key=settings.meshy_api_key,
            staging_dir=staging,
            art_style="realistic",
            poll_interval=settings.mesh_poll_interval,
            poll_timeout=settings.mesh_poll_timeout,
            progress_callback=progress_callback,
        )
    else:
        raise ValueError(f"Unknown mesh_provider: {provider!r}. Use 'tripo' or 'meshy'.")

    job.artifacts.meshy_task_id = result.task_id
    job.artifacts.model_local_path = result.model_local_path
    job.artifacts.thumbnail_local_path = result.thumbnail_local_path
    job.artifacts.model_format = Path(result.model_local_path).suffix.lstrip(".")

    job.advance(JobStage.AWAITING_MODEL_APPROVAL)

    summary = (
        f"🎨 *3D Model Generated!* ({provider})\n\n"
        f"**Task ID:** `{result.task_id}`\n"
        f"**Format:** {job.artifacts.model_format}\n"
        f"**File:** `{Path(result.model_local_path).name}`\n\n"
        f"Approve to proceed with slicing, or reject to regenerate."
    )
    thumb = result.thumbnail_local_path if result.thumbnail_local_path else None
    return summary, thumb
