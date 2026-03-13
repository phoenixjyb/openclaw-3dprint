"""Meshy.ai client — text-to-3D model generation via REST API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

MESHY_BASE = "https://api.meshy.ai/v2"


@dataclass
class MeshResult:
    task_id: str
    model_url: str
    thumbnail_url: str
    model_local_path: str
    thumbnail_local_path: str


async def create_text_to_3d_task(
    prompt: str,
    api_key: str,
    art_style: str = "realistic",
    topology: str = "triangle",
    target_polycount: int = 30000,
) -> str:
    """Submit a text-to-3D generation task, return the task ID."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{MESHY_BASE}/text-to-3d",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "mode": "refine",
                "prompt": prompt,
                "art_style": art_style,
                "topology": topology,
                "target_polycount": target_polycount,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data["result"]
        log.info("Created Meshy task: %s", task_id)
        return task_id


async def poll_task(
    task_id: str,
    api_key: str,
    poll_interval: int = 10,
    timeout: int = 600,
    progress_callback=None,
) -> dict:
    """Poll a Meshy task until it completes or times out."""
    elapsed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while elapsed < timeout:
            resp = await client.get(
                f"{MESHY_BASE}/text-to-3d/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "UNKNOWN")
            progress = data.get("progress", 0)

            log.info("Task %s: status=%s progress=%s%%", task_id, status, progress)

            if progress_callback:
                await progress_callback(status, progress)

            if status == "SUCCEEDED":
                return data
            if status in ("FAILED", "EXPIRED"):
                raise RuntimeError(f"Meshy task {task_id} {status}: {data.get('message', '')}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    raise TimeoutError(f"Meshy task {task_id} timed out after {timeout}s")


async def download_model(
    task_data: dict,
    staging_dir: Path,
    api_key: str,
    preferred_format: str = "stl",
) -> MeshResult:
    """Download the generated 3D model and thumbnail to local staging."""
    task_id = task_data["id"]
    model_urls = task_data.get("model_urls", {})

    model_url = (
        model_urls.get(preferred_format)
        or model_urls.get("glb")
        or model_urls.get("obj")
        or model_urls.get("stl")
    )
    if not model_url:
        raise ValueError(f"No downloadable model URL in task {task_id}: {model_urls}")

    actual_format = preferred_format if model_urls.get(preferred_format) else "glb"
    thumbnail_url = task_data.get("thumbnail_url", "")

    staging_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        model_path = staging_dir / f"{task_id}.{actual_format}"
        log.info("Downloading model → %s", model_path)
        resp = await client.get(model_url)
        resp.raise_for_status()
        model_path.write_bytes(resp.content)

        thumb_path = staging_dir / f"{task_id}_thumb.png"
        if thumbnail_url:
            log.info("Downloading thumbnail → %s", thumb_path)
            resp = await client.get(thumbnail_url)
            resp.raise_for_status()
            thumb_path.write_bytes(resp.content)

    return MeshResult(
        task_id=task_id,
        model_url=model_url,
        thumbnail_url=thumbnail_url,
        model_local_path=str(model_path),
        thumbnail_local_path=str(thumb_path) if thumbnail_url else "",
    )


async def generate_and_download(
    prompt: str,
    api_key: str,
    staging_dir: Path,
    art_style: str = "realistic",
    poll_interval: int = 10,
    poll_timeout: int = 600,
    progress_callback=None,
) -> MeshResult:
    """Full flow: create task → poll → download."""
    task_id = await create_text_to_3d_task(prompt, api_key, art_style=art_style)
    task_data = await poll_task(
        task_id, api_key,
        poll_interval=poll_interval,
        timeout=poll_timeout,
        progress_callback=progress_callback,
    )
    return await download_model(task_data, staging_dir, api_key)
