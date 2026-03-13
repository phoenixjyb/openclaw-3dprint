"""Stage: Slice the 3D model — supports local (PrusaSlicer/OrcaSlicer) or remote (Windows SSH).

Modes:
  - slicer_mode=local: Run slicer CLI locally (e.g., PrusaSlicer on macOS)
  - slicer_mode=remote: SSH to a Windows PC and run Bambu Studio CLI
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path, PureWindowsPath

from pipeline.models.job import JobStage, PrintJob
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


def _parse_slice_output(stdout: str) -> dict:
    """Extract estimated print time and filament usage from slicer output."""
    info: dict = {}

    time_match = re.search(
        r"total estimated time:\s*(.+)", stdout, re.IGNORECASE
    )
    if time_match:
        info["estimated_time"] = time_match.group(1).strip()

    filament_match = re.search(
        r"total filament used \[g\]:\s*([\d.]+)", stdout, re.IGNORECASE
    )
    if filament_match:
        info["filament_g"] = float(filament_match.group(1))

    if "estimated_time" not in info:
        pat = r"estimated printing time.*?(\d+h\s*\d+m|\d+m\s*\d+s)"
        m2 = re.search(pat, stdout, re.IGNORECASE)
        if m2:
            info["estimated_time"] = m2.group(1).strip()

    return info


async def _run_local(job: PrintJob, settings: Settings) -> str:
    """Slice using a local slicer binary (PrusaSlicer / OrcaSlicer)."""
    model_path = job.artifacts.model_local_path
    if not model_path:
        raise ValueError("No model file to slice")

    input_path = Path(model_path)
    output_3mf = input_path.with_suffix(".3mf")

    # Build slicer CLI command
    cmd_parts = [settings.slicer_path, "--export-3mf", "-o", str(output_3mf)]

    if settings.slicer_printer_profile:
        cmd_parts.extend(["--load", settings.slicer_printer_profile])
    if settings.slicer_filament_profile:
        cmd_parts.extend(["--load", settings.slicer_filament_profile])
    if settings.slicer_process_profile:
        cmd_parts.extend(["--load", settings.slicer_process_profile])

    cmd_parts.append(str(input_path))

    log.info("Local slicer command: %s", " ".join(cmd_parts))

    proc = await asyncio.create_subprocess_exec(
        *cmd_parts,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=600)
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise RuntimeError(
            f"Local slicer failed (exit {proc.returncode}):\n{stderr[:1000]}"
        )

    info = _parse_slice_output(stdout + "\n" + stderr)
    return _finalize(job, str(output_3mf), info)


async def _run_remote(job: PrintJob, settings: Settings) -> str:
    """Slice using Bambu Studio CLI on a remote Windows PC via SSH."""
    from pipeline.services.windows_ssh import WindowsSSH

    remote_model = job.artifacts.windows_remote_path
    if not remote_model:
        raise ValueError("No remote model path set")

    model_win_path = PureWindowsPath(remote_model)
    output_3mf = model_win_path.with_suffix(".3mf")

    slicer = settings.remote_slicer_path or settings.slicer_path
    profiles_dir = settings.remote_slicer_profiles_dir

    cmd_parts = [f'"{slicer}"']

    if profiles_dir and settings.slicer_printer_profile:
        machine_json = f'"{profiles_dir}\\\\machine\\\\{settings.slicer_printer_profile}.json"'
        cmd_parts.append(f"--load-settings {machine_json}")
    if profiles_dir and settings.slicer_filament_profile:
        filament_json = f'"{profiles_dir}\\\\filament\\\\{settings.slicer_filament_profile}.json"'
        cmd_parts.append(f"--load-filaments {filament_json}")
    if profiles_dir and settings.slicer_process_profile:
        process_json = f'"{profiles_dir}\\\\process\\\\{settings.slicer_process_profile}.json"'
        cmd_parts.append(f"--load-settings {process_json}")

    cmd_parts.extend([
        "--slice 0",
        f'--export-3mf "{output_3mf}"',
        f'"{remote_model}"',
    ])

    cmd = " ".join(cmd_parts)
    log.info("Remote slicer command: %s", cmd)

    def _do_slice():
        with WindowsSSH(
            host=settings.windows_host,
            user=settings.windows_user,
            port=settings.windows_port,
            key_path=settings.windows_ssh_key,
            connect_timeout=settings.windows_connect_timeout,
        ) as ssh:
            stdout, stderr, exit_code = ssh.exec(cmd, timeout=600)
            return stdout, stderr, exit_code

    stdout, stderr, exit_code = (
        await asyncio.get_event_loop().run_in_executor(None, _do_slice)
    )

    if exit_code != 0:
        raise RuntimeError(
            f"Remote slicer failed (exit {exit_code}):\n{stderr[:1000]}"
        )

    info = _parse_slice_output(stdout + "\n" + stderr)
    return _finalize(job, str(output_3mf), info)


def _finalize(job: PrintJob, output_path: str, info: dict) -> str:
    """Store slice results in job artifacts and return summary."""
    job.artifacts.sliced_file_path = output_path
    job.artifacts.estimated_print_time = info.get("estimated_time", "unknown")
    job.artifacts.estimated_filament_g = info.get("filament_g")

    job.advance(JobStage.AWAITING_SLICE_APPROVAL)

    filament_g = job.artifacts.estimated_filament_g
    filament_str = f"{filament_g:.1f}g" if filament_g else "unknown"
    output_name = Path(output_path).name
    summary = (
        f"✂️ *Slicing Complete!*\n\n"
        f"**Output:** `{output_name}`\n"
        f"**Est. print time:** {job.artifacts.estimated_print_time}\n"
        f"**Filament:** {filament_str}\n\n"
        f"Approve to start printing."
    )
    return summary


async def run(job: PrintJob, settings: Settings) -> str:
    """Slice the model. Dispatches to local or remote based on slicer_mode."""
    job.advance(JobStage.SLICING)

    mode = settings.slicer_mode.lower()
    if mode == "local":
        return await _run_local(job, settings)
    elif mode == "remote":
        return await _run_remote(job, settings)
    else:
        raise ValueError(f"Unknown slicer_mode: {mode!r}. Use 'local' or 'remote'.")
