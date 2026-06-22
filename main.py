import asyncio
import logging
import sys
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

import config
from database import init_db, is_seen, mark_seen
from playerok_client import build_client, fetch_new_orders, fetch_new_complaints
from notifier import send, format_order, format_complaint

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def poll_playerok():
    """Single polling iteration — fetch orders and complaints, notify on new ones."""
    async with build_client() as client:
        orders = await fetch_new_orders(client)
        for order in orders:
            oid = order.get("id")
            if oid and not is_seen("seen_orders", oid):
                await send(format_order(order))
                mark_seen("seen_orders", oid)

        complaints = await fetch_new_complaints(client)
        for complaint in complaints:
            cid = complaint.get("id")
            if cid and not is_seen("seen_complaints", cid):
                await send(format_complaint(complaint))
                mark_seen("seen_complaints", cid)


async def polling_loop():
    """Background loop that polls Playerok on an interval."""
    logger.info("Polling loop started (interval: %ds)", config.POLL_INTERVAL)
    while True:
        try:
            await poll_playerok()
        except Exception as e:
            logger.error("Polling error: %s", e)
        await asyncio.sleep(config.POLL_INTERVAL)


# ── Telegram command handlers ────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Playerok Monitor Bot</b>\n\n"
        "Я слежу за новыми покупками и жалобами на Playerok.\n\n"
        "/status — текущий статус\n"
        "/check — проверить прямо сейчас",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM seen_orders")
    orders_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM seen_complaints")
    complaints_count = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"📊 <b>Статус бота</b>\n\n"
        f"🔄 Интервал проверки: <b>{config.POLL_INTERVAL}с</b>\n"
        f"📦 Обработано покупок: <b>{orders_count}</b>\n"
        f"⚠️ Обработано жалоб: <b>{complaints_count}</b>",
        parse_mode="HTML",
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Проверяю Playerok...")
    try:
        await poll_playerok()
        await update.message.reply_text("✅ Проверка завершена.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def validate_config():
    missing = []
    if not config.TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not config.TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not config.PLAYEROK_TOKEN:
        missing.append("PLAYEROK_TOKEN")
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


async def main():
    validate_config()
    init_db()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))

    # Start polling loop as background task
    loop = asyncio.get_event_loop()
    loop.create_task(polling_loop())

    logger.info("Bot starting...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
