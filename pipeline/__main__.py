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

    # Start printer monitor (persistent MQTT listener for all prints)
    monitor = None
    mqtt_proxy_proc = None
    if (
        settings.printer_monitor_enabled
        and settings.bambu_printer_ip
        and settings.bambu_printer_serial
        and settings.bambu_printer_access_code
    ):
        # Start MQTT proxy if configured (workaround for macOS local network restrictions)
        if settings.printer_mqtt_proxy_port:
            import subprocess
            import pathlib
            proxy_script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mqtt-proxy.py"
            if proxy_script.exists():
                proxy_log = pathlib.Path.home() / "Library" / "Logs" / "openclaw-3dprint" / "mqtt-proxy.log"
                proxy_log.parent.mkdir(parents=True, exist_ok=True)
                proxy_log_fh = open(proxy_log, "a")
                mqtt_proxy_proc = subprocess.Popen(
                    ["/usr/bin/python3", str(proxy_script)],
                    env={
                        **__import__("os").environ,
                        "PRINTER_IP": settings.bambu_printer_ip,
                        "LOCAL_PORT": str(settings.printer_mqtt_proxy_port),
                    },
                    stdout=proxy_log_fh,
                    stderr=proxy_log_fh,
                )
                await asyncio.sleep(1.0)  # let proxy bind
                log.info("MQTT proxy started (pid=%d, port=%d)", mqtt_proxy_proc.pid, settings.printer_mqtt_proxy_port)
            else:
                log.warning("MQTT proxy script not found at %s", proxy_script)

        chat_id = settings.monitor_chat_id
        if chat_id:
            from pipeline.services.printer_monitor import PrinterMonitor

            # Broadcast notifications to both Telegram and Feishu
            if has_feishu:
                async def _monitor_notify(cid: int, text: str) -> None:
                    await _send_message(cid, text)
                    try:
                        await feishu_bot._send_message(cid, text)
                    except Exception:
                        log.debug("Feishu monitor notify failed", exc_info=True)
            else:
                _monitor_notify = _send_message

            monitor = PrinterMonitor(
                printer_ip=settings.bambu_printer_ip,
                serial=settings.bambu_printer_serial,
                access_code=settings.bambu_printer_access_code,
                notify_chat_id=chat_id,
                send_message=_monitor_notify,
                progress_interval=settings.printer_monitor_progress_pct,
                mqtt_proxy_port=settings.printer_mqtt_proxy_port,
            )
            await monitor.start()
            tg_app.bot_data["printer_monitor"] = monitor
            log.info("Printer monitor started (notify chat_id=%d, feishu=%s)", chat_id, has_feishu)
        else:
            log.warning("Printer monitor enabled but no chat_id configured")
    else:
        log.info("Printer monitor disabled or missing printer config")

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
        if monitor:
            await monitor.stop()
        if mqtt_proxy_proc:
            mqtt_proxy_proc.terminate()
            mqtt_proxy_proc.wait(timeout=5)
            log.info("MQTT proxy stopped")
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

    async def _handle_printer_status(request):
        monitor = tg_app.bot_data.get("printer_monitor")
        if not monitor:
            return web.json_response({"error": "printer monitor not running"}, status=503)
        snap = await monitor.request_status()
        if not snap:
            return web.json_response({"error": "no data from printer"}, status=503)
        return web.json_response({
            "state": snap.state,
            "progress": snap.progress,
            "layer": snap.layer,
            "total_layers": snap.total_layers,
            "remaining_time_min": snap.remaining_time_min,
            "job_name": snap.job_name,
            "nozzle_temp": snap.nozzle_temp,
            "nozzle_target": snap.nozzle_target,
            "bed_temp": snap.bed_temp,
            "bed_target": snap.bed_target,
            "wifi_signal": snap.wifi_signal,
            "ams_trays": [
                {"slot": s + 1, "type": t, "brand": b, "color": c, "remain": r}
                for s, t, b, c, r in snap.ams_trays
            ],
            "hms_alerts": [{"attr": a, "code": c} for a, c in snap.hms_alerts],
        })

    http_app.router.add_get("/api/health", _handle_health)
    http_app.router.add_post("/api/print", _handle_print)
    http_app.router.add_post("/api/jobs/{job_id}/approve", _handle_approve)
    http_app.router.add_post("/api/jobs/{job_id}/reject", _handle_reject)
    http_app.router.add_get("/api/jobs", _handle_list_jobs)
    http_app.router.add_get("/api/jobs/{job_id}", _handle_job_status)
    http_app.router.add_get("/api/printer", _handle_printer_status)


if __name__ == "__main__":
    main()
