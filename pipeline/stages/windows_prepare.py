"""Stage: Wake Windows PC and transfer the model file (remote slicer mode only).

This stage is only used when slicer_mode=remote. It wakes the Windows PC via WOL
and transfers the model file via SFTP for remote slicing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pipeline.models.job import JobStage, PrintJob
from pipeline.services.windows_ssh import WindowsSSH
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


async def _transfer_file(settings: Settings, local_path: str) -> str:
    """SCP the model file to Windows staging directory. Returns remote path."""
    filename = Path(local_path).name
    remote_path = f"{settings.windows_stl_staging_dir}\\{filename}"

    sftp_remote = settings.windows_stl_staging_dir.replace("\\", "/") + "/" + filename

    def _do_transfer():
        with WindowsSSH(
            host=settings.windows_host,
            user=settings.windows_user,
            port=settings.windows_port,
            key_path=settings.windows_ssh_key,
            connect_timeout=settings.windows_connect_timeout,
        ) as ssh:
            ssh.ensure_directory(settings.windows_stl_staging_dir)
            ssh.upload_file(local_path, sftp_remote)

    await asyncio.get_event_loop().run_in_executor(None, _do_transfer)
    return remote_path


async def run(job: PrintJob, settings: Settings) -> str:
    """Transfer model file to Windows. Returns summary for approval."""
    job.advance(JobStage.TRANSFERRING)

    local_path = job.artifacts.model_local_path
    if not local_path:
        raise ValueError("No model file to transfer")

    remote_path = await _transfer_file(settings, local_path)
    job.artifacts.windows_remote_path = remote_path

    job.advance(JobStage.AWAITING_TRANSFER_APPROVAL)

    summary = (
        f"💻 *Windows Ready & File Transferred*\n\n"
        f"**File:** `{Path(local_path).name}`\n"
        f"**Remote path:** `{remote_path}`\n\n"
        f"Approve to start slicing."
    )
    return summary
