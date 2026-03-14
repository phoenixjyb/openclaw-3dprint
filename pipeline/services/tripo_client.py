"""Tripo3D client — text-to-3D model generation via official SDK."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tripo3d import TaskStatus, TripoClient

log = logging.getLogger(__name__)


@dataclass
class TripoResult:
    task_id: str
    model_local_path: str
    thumbnail_local_path: str


async def generate_and_download(
    prompt: str,
    api_key: str,
    staging_dir: Path,
    poll_interval: float = 5.0,
    poll_timeout: float = 600.0,
    progress_callback=None,
) -> TripoResult:
    """Full flow: text-to-model → wait → convert to STL → download."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    output_dir = str(staging_dir)

    async with TripoClient(api_key=api_key) as client:
        log.info("Tripo3D: creating text-to-model task for: %s", prompt[:100])
        task_id = await client.text_to_model(
            prompt=prompt,
            model_version="v2.5-20250123",
            texture=True,
            pbr=True,
        )
        log.info("Tripo3D: task created: %s", task_id)

        task = await client.wait_for_task(
            task_id,
            polling_interval=poll_interval,
            timeout=poll_timeout,
            verbose=True,
        )

        if task.status != TaskStatus.SUCCESS:
            raise RuntimeError(
                f"Tripo3D task {task_id} failed: {task.status}"
            )

        log.info("Tripo3D: converting to STL…")
        convert_task_id = await client.convert_model(
            original_model_task_id=task_id,
            format="STL",
            flatten_bottom=True,
            pivot_to_center_bottom=True,
        )
        convert_task = await client.wait_for_task(
            convert_task_id,
            polling_interval=poll_interval,
            timeout=poll_timeout,
            verbose=True,
        )
        if convert_task.status != TaskStatus.SUCCESS:
            raise RuntimeError(
                f"Tripo3D STL conversion failed: {convert_task.status}"
            )

        log.info("Tripo3D: downloading model files…")
        files = await client.download_task_models(convert_task, output_dir)

        thumb_path = await client.download_rendered_image(
            task, output_dir, filename=f"{task_id}_thumb.png"
        )

        stl_path = files.get("model", "")
        if not stl_path:
            for _key, path in files.items():
                if path.endswith(".stl"):
                    stl_path = path
                    break
        if not stl_path:
            stl_path = next(iter(files.values()), "")

    return TripoResult(
        task_id=task_id,
        model_local_path=stl_path,
        thumbnail_local_path=thumb_path or "",
    )
