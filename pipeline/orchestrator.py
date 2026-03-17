"""Pipeline orchestrator — wires stages together with approval gates.

Supports two slicer modes:
  - slicer_mode=local: Skip Windows transfer, slice locally, send via FTP
  - slicer_mode=remote: Transfer to Windows, slice remotely, send via Studio
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Callable, Coroutine

from pipeline.models.job import JobStage, PrintJob
from pipeline.printer_queue import PrinterQueue, get_printer_queue
from pipeline.stages import llm_interpret, mesh_generate, print_job, slice
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


class Orchestrator:
    """Manages the lifecycle of PrintJob instances through the pipeline."""

    def __init__(
        self,
        settings: Settings,
        send_message: Callable[[int, str], Coroutine],
        send_photo: Callable[[int, str, str], Coroutine],
        request_approval: Callable[[int, str, str], Coroutine],
        printer_queue: PrinterQueue | None = None,
    ):
        self.settings = settings
        self.send_message = send_message
        self.send_photo = send_photo
        self.request_approval = request_approval
        self.jobs: dict[str, PrintJob] = {}
        self._approval_futures: dict[str, asyncio.Future] = {}
        self.printer_queue = printer_queue or get_printer_queue()

    def create_job(self, user_id: int, chat_id: int, raw_request: str,
                   enriched_prompt: str | None = None,
                   model_path: str | None = None) -> PrintJob:
        job = PrintJob(user_id=user_id, chat_id=chat_id, raw_request=raw_request)
        if enriched_prompt:
            job.artifacts.enriched_prompt = enriched_prompt
            job.artifacts.object_name = raw_request[:80]
        if model_path:
            job.artifacts.model_local_path = model_path
        self.jobs[job.id] = job
        log.info("Created job %s: %r (enriched=%s, model=%s)",
                 job.id, raw_request, bool(enriched_prompt), bool(model_path))
        return job

    async def resolve_approval(self, job_id: str, approved: bool) -> None:
        """Called by the bot when user taps approve/reject."""
        fut = self._approval_futures.pop(job_id, None)
        if fut and not fut.done():
            fut.set_result(approved)

    async def _wait_for_approval(self, job: PrintJob, summary: str) -> bool:
        """Send approval request and wait for user response."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._approval_futures[job.id] = fut

        await self.request_approval(job.chat_id, job.id, summary)

        try:
            approved = await asyncio.wait_for(fut, timeout=3600)
        except asyncio.TimeoutError:
            msg = f"⏰ Job `{job.id}` approval timed out (1h). Cancelling."
            await self.send_message(job.chat_id, msg)
            job.advance(JobStage.CANCELLED)
            return False

        return approved

    async def run_pipeline(self, job: PrintJob) -> None:
        """Execute the full pipeline with approval gates between each stage."""
        try:
            slicer_mode = self.settings.slicer_mode.lower()

            # ── Stage: LLM Interpretation (skipped if enriched prompt provided) ──
            if job.artifacts.enriched_prompt:
                log.info("Job %s: skipping LLM — enriched prompt already provided", job.id)
                await self.send_message(
                    job.chat_id,
                    f"🚀 *Starting pipeline for job `{job.id}`*\n\n"
                    f"Request: _{job.raw_request}_\n\n"
                    f"Mode: slicer={slicer_mode}\n"
                    f"Using pre-enriched prompt (LLM step skipped).\n"
                    f"Stage 1: Generating 3D model…",
                )
            else:
                await self.send_message(
                    job.chat_id,
                    f"🚀 *Starting pipeline for job `{job.id}`*\n\n"
                    f"Request: _{job.raw_request}_\n\n"
                    f"Mode: slicer={slicer_mode}\n"
                    f"Stage 1: LLM interpretation…",
                )

                summary = await llm_interpret.run(job, self.settings)
                approved = await self._wait_for_approval(job, summary)
                if not approved:
                    job.advance(JobStage.CANCELLED)
                    await self.send_message(
                        job.chat_id, f"❌ Job `{job.id}` cancelled at LLM stage."
                    )
                    return

            # ── Stage: 3D Model Generation (skip if model already provided) ──
            if job.artifacts.model_local_path:
                log.info("Job %s: skipping mesh gen — model already at %s",
                         job.id, job.artifacts.model_local_path)
                await self.send_message(
                    job.chat_id,
                    "Stage 2: 3D model already provided — skipping generation ✅",
                )
            else:
                await self.send_message(job.chat_id, "Stage 2: Generating 3D model… ⏳")

                async def _mesh_progress(status, progress):
                    if progress % 25 == 0:
                        await self.send_message(job.chat_id, f"🔄 Mesh gen: {status} ({progress}%)")

                summary, thumbnail = await mesh_generate.run(job, self.settings, _mesh_progress)

                if thumbnail:
                    await self.send_photo(job.chat_id, thumbnail, summary)
                approval_text = "Approve the model above?" if thumbnail else summary
                approved = await self._wait_for_approval(job, approval_text)
                if not approved:
                    job.advance(JobStage.CANCELLED)
                    await self.send_message(
                        job.chat_id, f"❌ Job `{job.id}` cancelled at model generation."
                    )
                    return

            # ── Stage: Windows Transfer (remote mode only) ───────────
            if slicer_mode == "remote":
                from pipeline.stages import windows_prepare

                await self.send_message(
                    job.chat_id, "Stage 3: Transferring file to Windows PC… 💻",
                )
                summary = await windows_prepare.run(job, self.settings)
                approved = await self._wait_for_approval(job, summary)
                if not approved:
                    job.advance(JobStage.CANCELLED)
                    await self.send_message(
                        job.chat_id, f"❌ Job `{job.id}` cancelled at transfer stage."
                    )
                    return

            # ── Stage: Slicing ───────────────────────────────────────
            slicer_label = "local slicer" if slicer_mode == "local" else "remote slicer"
            await self.send_message(
                job.chat_id, f"Stage {'3' if slicer_mode == 'local' else '4'}: "
                f"Slicing with {slicer_label}… ✂️"
            )
            summary = await slice.run(job, self.settings)
            approved = await self._wait_for_approval(job, summary)
            if not approved:
                job.advance(JobStage.CANCELLED)
                await self.send_message(
                    job.chat_id, f"❌ Job `{job.id}` cancelled at slicing stage."
                )
                return

            # ── Stage: Print (queued) ────────────────────────────────
            q = self.printer_queue
            qs = q.status()
            if qs["active_job"] or qs["queue_length"] > 0:
                pos = qs["queue_length"] + 1
                await self.send_message(
                    job.chat_id,
                    f"🔄 Printer busy — your job is #{pos} in queue. Waiting…",
                )

            await q.acquire(job.id)
            try:
                await self.send_message(job.chat_id, "Sending to printer… 🖨")

                async def _print_progress(status):
                    pct = status.progress
                    remaining = status.remaining_time_min
                    eta = f"{remaining // 60}h {remaining % 60}m" if remaining else "?"
                    await self.send_message(
                        job.chat_id,
                        f"🖨 Printing: {pct:.0f}% — ETA: {eta}",
                    )

                summary = await print_job.run(job, self.settings, _print_progress)
                await self.send_message(job.chat_id, summary)
            finally:
                q.release(job.id)

            log.info("Job %s completed successfully!", job.id)

        except Exception as e:
            log.error("Job %s failed: %s\n%s", job.id, e, traceback.format_exc())
            self.printer_queue.release(job.id)
            try:
                job.advance(JobStage.FAILED, error=str(e))
            except ValueError:
                job.stage = JobStage.FAILED
                job.error = str(e)
            await self.send_message(
                job.chat_id,
                f"💥 *Job `{job.id}` failed at stage {job.stage.value}*\n\n"
                f"Error: `{str(e)[:500]}`",
            )
