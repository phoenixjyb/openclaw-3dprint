"""Telegram bot — entry point for user interaction and approval flow."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from pipeline.orchestrator import Orchestrator
from pipeline.utils.config import Settings

log = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Application:
    """Build and configure the Telegram bot application."""

    app = Application.builder().token(settings.telegram_bot_token).build()

    async def post_init(application: Application) -> None:
        orch = Orchestrator(
            settings=settings,
            send_message=_send_message,
            send_photo=_send_photo,
            request_approval=_request_approval,
        )
        application.bot_data["orchestrator"] = orch
        application.bot_data["settings"] = settings
        application.bot_data["app"] = application
        log.info("Pipeline bot initialized.")

    app.post_init = post_init

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CommandHandler("print", _cmd_print))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("cancel", _cmd_cancel))
    app.add_handler(CallbackQueryHandler(_callback_approval, pattern=r"^(approve|reject):"))

    return app


def _check_auth(settings: Settings, user_id: int) -> bool:
    allowed = settings.allowed_user_ids
    if not allowed:
        return True
    return user_id in allowed


_bot_app: Application | None = None


async def _send_message(chat_id: int, text: str) -> None:
    if _bot_app:
        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )


async def _send_photo(chat_id: int, photo_path: str, caption: str) -> None:
    if _bot_app and Path(photo_path).exists():
        with open(photo_path, "rb") as f:
            await _bot_app.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=caption[:1024],
                parse_mode=ParseMode.MARKDOWN,
            )


async def _request_approval(chat_id: int, job_id: str, text: str) -> None:
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{job_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{job_id}"),
        ]
    ])
    if _bot_app:
        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _bot_app
    _bot_app = context.application
    await update.message.reply_text(
        "🖨 *OpenClaw 3D Print Pipeline*\n\n"
        "Use `/print <description>` to start a 3D print job.\n\n"
        "Commands:\n"
        "  /print <description> — start a print job\n"
        "  /status — check active jobs\n"
        "  /cancel <job\\_id> — cancel a job\n"
        "  /help — show this message",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_start(update, context)


async def _cmd_print(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _bot_app
    _bot_app = context.application
    settings: Settings = context.bot_data["settings"]
    orch: Orchestrator = context.bot_data["orchestrator"]

    if not _check_auth(settings, update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /print <what you want to 3D print>")
        return

    job = orch.create_job(
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        raw_request=text,
    )
    asyncio.create_task(orch.run_pipeline(job))


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = context.bot_data["orchestrator"]

    if not orch.jobs:
        await update.message.reply_text("No active jobs.")
        return

    lines = []
    for job in orch.jobs.values():
        lines.append(job.summary())
    await update.message.reply_text(
        "\n\n---\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orch: Orchestrator = context.bot_data["orchestrator"]
    job_id = context.args[0] if context.args else ""

    if not job_id:
        await update.message.reply_text("Usage: /cancel <job_id>")
        return

    job = orch.jobs.get(job_id)
    if not job:
        await update.message.reply_text(f"Job `{job_id}` not found.")
        return

    await orch.resolve_approval(job_id, False)
    await update.message.reply_text(f"Cancellation requested for job `{job_id}`.")


async def _callback_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard approval/rejection callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action, job_id = data.split(":", 1)
    approved = action == "approve"

    orch: Orchestrator = context.bot_data["orchestrator"]

    emoji = "✅" if approved else "❌"
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_text(
        text=f"{query.message.text}\n\n{emoji} {'Approved' if approved else 'Rejected'}",
        parse_mode=ParseMode.MARKDOWN,
    )

    await orch.resolve_approval(job_id, approved)
