"""Feishu bot — HTTP API + Feishu messaging for the 3D-print pipeline.

This module provides:
  - An HTTP API server (aiohttp) that receives commands.
  - Feishu message sending for pipeline status updates.
  - Text-based approval flow.

Architecture:
  User (Feishu) → Agent → HTTP API → Pipeline Orchestrator
  Pipeline Orchestrator → Feishu API → User (Feishu)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from pipeline.feishu_client import FeishuClient
from pipeline.orchestrator import Orchestrator
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


class FeishuBot:
    """HTTP API server + Feishu messaging for the pipeline."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feishu = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        self.chat_id = settings.feishu_chat_id
        self.orchestrator = Orchestrator(
            settings=settings,
            send_message=self._send_message,
            send_photo=self._send_photo,
            request_approval=self._request_approval,
        )
        self._app: web.Application | None = None

    async def _send_message(self, chat_id: int, text: str) -> None:
        """Send a text message via Feishu. chat_id param is ignored; uses configured chat."""
        clean = _strip_markdown(text)
        try:
            await self.feishu.send_text(self.chat_id, clean)
        except Exception:
            log.exception("Feishu send_message failed")

    async def _send_photo(
        self, chat_id: int, photo_path: str, caption: str
    ) -> None:
        clean = _strip_markdown(caption)
        try:
            if Path(photo_path).exists():
                await self.feishu.send_image(
                    self.chat_id, photo_path, clean
                )
            else:
                await self.feishu.send_text(self.chat_id, clean)
        except Exception:
            log.exception("Feishu send_photo failed")

    async def _request_approval(
        self, chat_id: int, job_id: str, text: str
    ) -> None:
        clean = _strip_markdown(text)
        approval_msg = (
            f"{clean}\n\n"
            f"---\n"
            f"To approve: tell me \"approve {job_id}\"\n"
            f"To reject: tell me \"reject {job_id}\""
        )
        try:
            await self.feishu.send_text(self.chat_id, approval_msg)
        except Exception:
            log.exception("Feishu request_approval failed")

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/print", self._handle_print)
        app.router.add_post(
            "/api/jobs/{job_id}/approve", self._handle_approve
        )
        app.router.add_post(
            "/api/jobs/{job_id}/reject", self._handle_reject
        )
        app.router.add_get("/api/jobs", self._handle_list_jobs)
        app.router.add_get(
            "/api/jobs/{job_id}", self._handle_job_status
        )
        app.router.add_get("/api/health", self._handle_health)
        return app

    async def _handle_health(self, request: web.Request) -> web.Response:
        from pipeline.printer_queue import get_printer_queue
        q = get_printer_queue()
        return web.json_response({
            "status": "ok",
            "bot": "feishu",
            "printer_queue": q.status(),
        })

    async def _handle_print(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "invalid JSON"}, status=400
            )

        prompt = body.get("prompt", "").strip()
        if not prompt:
            return web.json_response(
                {"error": "missing 'prompt' field"}, status=400
            )

        # If the caller (e.g. an OpenClaw agent) already enriched the prompt,
        # pass it through to skip the built-in LLM interpretation stage.
        enriched_prompt = body.get("enriched_prompt", "").strip() or None

        # If a model file already exists on disk (e.g. retry after failure),
        # pass it through to skip mesh generation.
        model_path = body.get("model_path", "").strip() or None

        job = self.orchestrator.create_job(
            user_id=0,
            chat_id=0,
            raw_request=prompt,
            enriched_prompt=enriched_prompt,
            model_path=model_path,
        )

        asyncio.create_task(self.orchestrator.run_pipeline(job))

        return web.json_response({
            "job_id": job.id,
            "status": "started",
            "message": f"Pipeline started for: {prompt}",
        })

    async def _handle_approve(
        self, request: web.Request
    ) -> web.Response:
        job_id = request.match_info["job_id"]
        job = self.orchestrator.jobs.get(job_id)
        if not job:
            return web.json_response(
                {"error": f"job {job_id} not found"}, status=404
            )
        await self.orchestrator.resolve_approval(job_id, True)
        return web.json_response({
            "job_id": job_id,
            "action": "approved",
        })

    async def _handle_reject(
        self, request: web.Request
    ) -> web.Response:
        job_id = request.match_info["job_id"]
        job = self.orchestrator.jobs.get(job_id)
        if not job:
            return web.json_response(
                {"error": f"job {job_id} not found"}, status=404
            )
        await self.orchestrator.resolve_approval(job_id, False)
        return web.json_response({
            "job_id": job_id,
            "action": "rejected",
        })

    async def _handle_list_jobs(
        self, request: web.Request
    ) -> web.Response:
        jobs = []
        for job in self.orchestrator.jobs.values():
            jobs.append({
                "id": job.id,
                "stage": job.stage.value,
                "request": job.raw_request,
            })
        return web.json_response({"jobs": jobs})

    async def _handle_job_status(
        self, request: web.Request
    ) -> web.Response:
        job_id = request.match_info["job_id"]
        job = self.orchestrator.jobs.get(job_id)
        if not job:
            return web.json_response(
                {"error": f"job {job_id} not found"}, status=404
            )
        return web.json_response({
            "id": job.id,
            "stage": job.stage.value,
            "request": job.raw_request,
            "summary": job.summary(),
        })

    async def start(self) -> None:
        """Start the HTTP API server."""
        self._app = self._build_app()
        runner = web.AppRunner(self._app)
        await runner.setup()
        port = self.settings.feishu_api_port
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        log.info("Feishu bot HTTP API running on http://127.0.0.1:%d", port)

        try:
            await self.feishu.send_text(
                self.chat_id,
                "🖨 OpenClaw 3D Print pipeline started!\n\n"
                "Send me a print request.\n\n"
                "Pipeline API: http://127.0.0.1:" + str(port),
            )
        except Exception as e:
            log.warning("Failed to send startup message: %s", e)

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await runner.cleanup()
            await self.feishu.close()


def _strip_markdown(text: str) -> str:
    """Remove Telegram-style markdown for plain text."""
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def create_feishu_bot(settings: Settings) -> FeishuBot:
    """Factory for the Feishu bot."""
    return FeishuBot(settings)
