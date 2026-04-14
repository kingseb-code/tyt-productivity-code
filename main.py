import asyncio
import logging
import os
from datetime import time as dt_time, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from briefing import run_briefing
import wordpress as wp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/openclaw.log"),
    ],
)
logger = logging.getLogger(__name__)


# ── Core commands ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("OpenClaw is running. Send /status to check.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("OpenClaw: Online")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Fetching your briefing...")
    text = await asyncio.to_thread(run_briefing)
    await update.message.reply_text(text, parse_mode="Markdown")


# ── WordPress commands ─────────────────────────────────────────────────────────

async def cmd_wp_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*WordPress Maintenance Commands*\n\n"
        "/wp\\_status \u2014 Quick health check on all critical pages\n"
        "/wp\\_report \u2014 Full maintenance scan (read-only, no changes made)\n"
        "/wp\\_update \u2014 Full maintenance + update all plugins\n"
        "/wp\\_create\\_page \u2014 Create a new page with AI-drafted content\n"
        "  _Usage:_ /wp\\_create\\_page Title | Brief description\n"
        "  _Example:_ /wp\\_create\\_page About Us | Family business selling handmade goods\n\n"
        "Scheduled jobs (auto-run when WP\\_URL is set):\n"
        "\u2022 Daily health check at 08:00 UTC \u2014 alerts only if issues found\n"
        "\u2022 Quarterly full maintenance \u2014 runs every 91 days\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_wp_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick health check on all critical WooCommerce pages."""
    await update.message.reply_text("Checking site health\u2026")
    result = await asyncio.to_thread(wp.run_quick_health_check)
    await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_wp_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full maintenance scan — no changes made."""
    await update.message.reply_text(
        "Running full maintenance scan (read-only \u2014 no changes will be made)\u2026"
    )
    result = await asyncio.to_thread(wp.run_full_maintenance, False)
    for chunk in _split_message(result):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_wp_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full maintenance run with plugin updates applied."""
    await update.message.reply_text(
        "Running full maintenance with plugin updates\u2026 (this may take a few minutes)"
    )
    result = await asyncio.to_thread(wp.run_full_maintenance, True)
    for chunk in _split_message(result):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_wp_create_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new WordPress page. Usage: /wp_create_page Title | Brief description"""
    raw = update.message.text.split(maxsplit=1)
    text = raw[1] if len(raw) > 1 else ""

    if "|" not in text:
        await update.message.reply_text(
            "Usage: /wp\\_create\\_page Title | Brief description\n\n"
            "Example:\n"
            "/wp\\_create\\_page About Us | Family business selling handmade goods since 2010",
            parse_mode="Markdown",
        )
        return

    title, description = (part.strip() for part in text.split("|", 1))
    if not title or not description:
        await update.message.reply_text("Please provide both a title and a description.")
        return

    config = wp.load_wp_config()
    if not config.url:
        await update.message.reply_text("WP\\_URL is not configured in .env", parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"Drafting content for *{title}*\u2026", parse_mode="Markdown"
    )
    content = await asyncio.to_thread(wp.draft_page_content, title, description)

    await update.message.reply_text("Creating page as draft in WordPress\u2026")
    result = await asyncio.to_thread(wp.create_page, config, title, content, "draft")

    if result["success"]:
        await update.message.reply_text(
            f"Page created as draft!\n\n"
            f"*Title:* {title}\n"
            f"*Page ID:* {result['id']}\n"
            f"*Edit URL:* {result['edit_url']}\n\n"
            "Review the content and publish it from your WordPress admin.",
            parse_mode="Markdown",
        )
    else:
        err = result.get("error", "Unknown error")
        await update.message.reply_text(f"Failed to create page: {err}")


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def _daily_health_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily site health check — always sends a status message."""
    logger.info("Running daily WordPress health check...")
    result = await asyncio.to_thread(wp.run_quick_health_check)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=result,
        parse_mode="Markdown",
    )


async def _quarterly_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quarterly full maintenance — checks health, updates plugins, runs security scan."""
    logger.info("Running quarterly WordPress maintenance...")
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="Starting quarterly WordPress maintenance\u2026",
    )
    result = await asyncio.to_thread(wp.run_full_maintenance, True)
    for chunk in _split_message(result):
        await context.bot.send_message(chat_id=CHAT_ID, text=chunk, parse_mode="Markdown")


# ── Utilities ──────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long string into chunks that fit within Telegram's 4096-char limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── Startup ────────────────────────────────────────────────────────────────────

async def on_startup(application: Application) -> None:
    await application.bot.send_message(
        chat_id=CHAT_ID,
        text="OpenClaw started successfully.",
    )
    logger.info("Startup message sent to Telegram.")

    if os.getenv("WP_URL"):
        # Daily health check at 08:00 UTC — silent unless issues found
        application.job_queue.run_daily(
            _daily_health_check,
            time=dt_time(8, 0, 0),
            name="wp_daily_health",
        )
        # Quarterly full maintenance — every 91 days
        application.job_queue.run_repeating(
            _quarterly_maintenance,
            interval=timedelta(days=91),
            first=timedelta(days=91),
            name="wp_quarterly_maintenance",
        )
        logger.info("WordPress jobs scheduled: daily health check + quarterly maintenance.")
    else:
        logger.info("WP_URL not set — WordPress maintenance jobs not scheduled.")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    # Core
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("briefing", cmd_briefing))

    # WordPress maintenance
    app.add_handler(CommandHandler("wp_help", cmd_wp_help))
    app.add_handler(CommandHandler("wp_status", cmd_wp_status))
    app.add_handler(CommandHandler("wp_report", cmd_wp_report))
    app.add_handler(CommandHandler("wp_update", cmd_wp_update))
    app.add_handler(CommandHandler("wp_create_page", cmd_wp_create_page))

    logger.info("OpenClaw starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
