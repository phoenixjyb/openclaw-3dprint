"""Pipeline entry point — run with: python -m pipeline"""

import asyncio
import logging
import sys

from pipeline.utils.config import load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("openclaw-3dprint")

    try:
        settings = load_settings()
    except Exception as e:
        log.error("Failed to load config: %s", e)
        sys.exit(1)

    mode = settings.bot_mode.lower()
    log.info("Starting pipeline bot (mode=%s) …", mode)

    if mode == "telegram":
        from pipeline.bot import create_bot

        bot = create_bot(settings)
        bot.run_polling(drop_pending_updates=True)

    elif mode == "feishu":
        from pipeline.feishu_bot import create_feishu_bot

        bot = create_feishu_bot(settings)
        asyncio.run(bot.start())

    elif mode == "dual":
        asyncio.run(_run_dual(settings, log))

    else:
        log.error("Unknown BOT_MODE: %s (use 'telegram', 'feishu', or 'dual')", mode)
        sys.exit(1)


async def _run_dual(settings, log) -> None:
    """Run Telegram bot + HTTP API server together in one event loop."""
    from aiohttp import web

    from pipeline.bot import (
        _request_approval,
        _send_message,
        _send_photo,
        create_bot,
    )
    from pipeline.feishu_bot import FeishuBot
    from pipeline.orchestrator import Orchestrator

    tg_app = create_bot(settings)

    # In dual mode we don't use run_polling(), so post_init never fires.
    # Manually create the orchestrator and wire it up.
    import pipeline.bot as _bot_mod
    _bot_mod._bot_app = tg_app  # so send_message/send_photo/request_approval can use it

    orch = Orchestrator(
        settings=settings,
        send_message=_send_message,
        send_photo=_send_photo,
        request_approval=_request_approval,
    )
    tg_app.bot_data["orchestrator"] = orch
    tg_app.bot_data["settings"] = settings
    tg_app.bot_data["app"] = tg_app

    has_feishu = bool(
        settings.feishu_app_id
        and settings.feishu_app_secret
        and settings.feishu_chat_id
    )
    if has_feishu:
        feishu_bot = FeishuBot(settings)
        http_app = feishu_bot._build_app()
    else:
        http_app = web.Application()
        _setup_http_api(http_app, tg_app, settings)

    runner = web.AppRunner(http_app)
    await runner.setup()
    port = settings.feishu_api_port
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info("HTTP API running on http://127.0.0.1:%d", port)

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot polling started")

    if has_feishu:
        try:
            await feishu_bot.feishu.send_text(
                feishu_bot.chat_id,
                "🖨 OpenClaw 3D Print started (Telegram + HTTP API dual mode)\n"
                f"API: http://127.0.0.1:{port}",
            )
        except Exception:
            pass

    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()
        if has_feishu:
            await feishu_bot.feishu.close()


def _setup_http_api(http_app, tg_app, settings):
    """Set up HTTP API routes that feed into the Telegram bot's orchestrator."""
    import json

    from aiohttp import web

    from pipeline.printer_queue import get_printer_queue

    async def _handle_health(request):
        q = get_printer_queue()
        return web.json_response({
            "status": "ok",
            "bot": "dual-telegram",
            "printer_queue": q.status(),
        })

    async def _handle_print(request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return web.json_response({"error": "missing 'prompt'"}, status=400)

        orch = tg_app.bot_data.get("orchestrator")
        if not orch:
            return web.json_response({"error": "orchestrator not ready"}, status=503)

        uid = 0
        if settings.telegram_allowed_user_ids:
            uid = int(settings.telegram_allowed_user_ids.split(",")[0].strip())
        job = orch.create_job(user_id=uid, chat_id=uid, raw_request=prompt)
        asyncio.create_task(orch.run_pipeline(job))
        return web.json_response({"job_id": job.id, "status": "started"})

    async def _handle_approve(request):
        job_id = request.match_info["job_id"]
        orch = tg_app.bot_data.get("orchestrator")
        if not orch:
            return web.json_response({"error": "not ready"}, status=503)
        job = orch.jobs.get(job_id)
        if not job:
            return web.json_response({"error": "not found"}, status=404)
        await orch.resolve_approval(job_id, True)
        return web.json_response({"job_id": job_id, "action": "approved"})

    async def _handle_reject(request):
        job_id = request.match_info["job_id"]
        orch = tg_app.bot_data.get("orchestrator")
        if not orch:
            return web.json_response({"error": "not ready"}, status=503)
        job = orch.jobs.get(job_id)
        if not job:
            return web.json_response({"error": "not found"}, status=404)
        await orch.resolve_approval(job_id, False)
        return web.json_response({"job_id": job_id, "action": "rejected"})

    async def _handle_list_jobs(request):
        orch = tg_app.bot_data.get("orchestrator")
        if not orch:
            return web.json_response({"jobs": []})
        jobs = [{"id": j.id, "stage": j.stage.value, "request": j.raw_request}
                for j in orch.jobs.values()]
        return web.json_response({"jobs": jobs})

    async def _handle_job_status(request):
        job_id = request.match_info["job_id"]
        orch = tg_app.bot_data.get("orchestrator")
        if not orch:
            return web.json_response({"error": "not ready"}, status=503)
        job = orch.jobs.get(job_id)
        if not job:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({
            "id": job.id, "stage": job.stage.value,
            "request": job.raw_request, "summary": job.summary(),
        })

    http_app.router.add_get("/api/health", _handle_health)
    http_app.router.add_post("/api/print", _handle_print)
    http_app.router.add_post("/api/jobs/{job_id}/approve", _handle_approve)
    http_app.router.add_post("/api/jobs/{job_id}/reject", _handle_reject)
    http_app.router.add_get("/api/jobs", _handle_list_jobs)
    http_app.router.add_get("/api/jobs/{job_id}", _handle_job_status)


if __name__ == "__main__":
    main()
