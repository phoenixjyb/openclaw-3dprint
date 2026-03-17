"""Stage: Send sliced file to printer and monitor progress.

Supports two send methods:
  - bambu_send_method=ftp: Direct FTPS upload + MQTT command (Mac, no Windows needed)
  - bambu_send_method=studio: Bambu Studio CLI on Windows via SSH
"""

from __future__ import annotations

import asyncio
import logging

from pipeline.models.job import JobStage, PrintJob
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


async def _send_via_ftp(job: PrintJob, settings: Settings) -> None:
    """Upload .3mf via FTPS and start print via MQTT — direct to printer."""
    from pipeline.services.bambu_printer import start_print_mqtt, upload_file_ftp

    sliced_path = job.artifacts.sliced_file_path
    if not sliced_path:
        raise ValueError("No sliced file path")

    filename = await upload_file_ftp(
        printer_ip=settings.bambu_printer_ip,
        access_code=settings.bambu_printer_access_code,
        local_path=sliced_path,
        ftp_proxy_url=settings.bambu_ftp_proxy_url,
    )

    await start_print_mqtt(
        printer_ip=settings.bambu_printer_ip,
        access_code=settings.bambu_printer_access_code,
        serial=settings.bambu_printer_serial,
        filename=filename,
        mqtt_proxy_port=settings.printer_mqtt_proxy_port,
    )
    log.info("Print started via FTP+MQTT: %s", filename)


async def _send_via_studio(job: PrintJob, settings: Settings) -> None:
    """Use Bambu Studio CLI on Windows to send the sliced file to the printer."""
    from pipeline.services.windows_ssh import WindowsSSH

    sliced_path = job.artifacts.sliced_file_path
    if not sliced_path:
        raise ValueError("No sliced file path")

    slicer = settings.remote_slicer_path or settings.slicer_path
    send_cmd = (
        f'"{slicer}" '
        f'--send "{sliced_path}" '
        f'--printer-ip "{settings.bambu_printer_ip}" '
        f'--printer-serial "{settings.bambu_printer_serial}" '
        f'--access-code "{settings.bambu_printer_access_code}"'
    )

    def _do_send():
        with WindowsSSH(
            host=settings.windows_host,
            user=settings.windows_user,
            port=settings.windows_port,
            key_path=settings.windows_ssh_key,
            connect_timeout=settings.windows_connect_timeout,
        ) as ssh:
            stdout, stderr, exit_code = ssh.exec(send_cmd, timeout=120)
            return stdout, stderr, exit_code

    stdout, stderr, exit_code = await asyncio.get_event_loop().run_in_executor(None, _do_send)

    if exit_code != 0:
        raise RuntimeError(f"Failed to send to printer (exit {exit_code}):\n{stderr[:1000]}")

    log.info("Print job sent via Studio: %s", stdout[:300])


async def _monitor_via_mqtt(
    settings: Settings,
    progress_callback=None,
    poll_interval: int = 30,
    timeout: int = 86400,
) -> None:
    """Monitor print progress via Bambu MQTT if available."""
    if not settings.bambu_printer_ip or not settings.bambu_printer_serial:
        log.info("No Bambu MQTT config — skipping live monitoring")
        return

    try:
        from pipeline.services.bambu_mqtt import BambuMQTT
    except ImportError:
        log.warning("paho-mqtt not installed — skipping monitoring")
        return

    mqtt = BambuMQTT(
        printer_ip=settings.bambu_printer_ip,
        serial=settings.bambu_printer_serial,
        access_code=settings.bambu_printer_access_code,
    )

    try:
        mqtt.connect()
        elapsed = 0
        last_reported_pct = -1

        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status = mqtt.status
            current_pct = int(status.progress // 10) * 10
            if current_pct != last_reported_pct and progress_callback:
                await progress_callback(status)
                last_reported_pct = current_pct

            if status.state == "FINISH":
                log.info("Print finished!")
                if progress_callback:
                    await progress_callback(status)
                return
            elif status.state == "FAILED":
                raise RuntimeError(f"Print failed: {status.error}")
    finally:
        mqtt.disconnect()


async def run(
    job: PrintJob,
    settings: Settings,
    progress_callback=None,
) -> str:
    """Send the sliced file to the printer and optionally monitor."""
    job.advance(JobStage.PRINTING)

    method = settings.bambu_send_method.lower()
    if method == "ftp":
        await _send_via_ftp(job, settings)
    elif method == "studio":
        await _send_via_studio(job, settings)
    else:
        raise ValueError(
            f"Unknown bambu_send_method: {method!r}. Use 'ftp' or 'studio'."
        )

    # Monitor if MQTT configured
    if settings.bambu_printer_ip:
        async def _update_progress(status):
            job.artifacts.print_progress_pct = status.progress
            remaining = status.remaining_time_min
            job.artifacts.print_eta = f"{remaining // 60}h {remaining % 60}m" if remaining else ""
            if progress_callback:
                await progress_callback(status)

        try:
            await _monitor_via_mqtt(settings, _update_progress)
        except Exception as e:
            log.warning("MQTT monitoring error (print may still be running): %s", e)

    job.advance(JobStage.DONE)

    summary = (
        f"✅ *Print Complete!*\n\n"
        f"**Object:** {job.artifacts.object_name}\n"
        f"**Job:** `{job.id}`\n\n"
        f"🎉 Go collect your print!"
    )
    return summary
