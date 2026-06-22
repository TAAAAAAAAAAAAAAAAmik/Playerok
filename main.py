import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

import config
import auth
from database import init_db, is_seen, mark_seen, get_stats
from playerok_client import (
    request_auth_code,
    login_with_code,
    fetch_orders,
    fetch_complaints,
)
from notifier import send, format_order, format_complaint

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ConversationHandler states
ASK_EMAIL, ASK_CODE = range(2)

# Background polling task handle
_polling_task: asyncio.Task | None = None


# ── Polling ───────────────────────────────────────────────────────────────────

async def poll_once():
    orders = await fetch_orders()
    for order in orders:
        oid = order.get("id")
        if oid and not is_seen("seen_orders", oid):
            await send(format_order(order))
            mark_seen("seen_orders", oid)

    complaints = await fetch_complaints()
    for complaint in complaints:
        cid = complaint.get("id")
        if cid and not is_seen("seen_complaints", cid):
            await send(format_complaint(complaint))
            mark_seen("seen_complaints", cid)


async def polling_loop():
    logger.info("Polling started (interval: %ds)", config.POLL_INTERVAL)
    while True:
        try:
            await poll_once()
        except Exception as e:
            logger.error("Polling error: %s", e)
        await asyncio.sleep(config.POLL_INTERVAL)


def start_polling():
    global _polling_task
    if _polling_task is None or _polling_task.done():
        _polling_task = asyncio.get_event_loop().create_task(polling_loop())


def stop_polling():
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        _polling_task = None


# ── /login conversation ───────────────────────────────────────────────────────

async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if auth.is_authenticated():
        await update.message.reply_text(
            "✅ Вы уже авторизованы.\n/logout — выйти из аккаунта."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📧 Введите email от аккаунта Playerok:",
        parse_mode=ParseMode.HTML,
    )
    return ASK_EMAIL


async def got_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()

    if "@" not in email:
        await update.message.reply_text("❌ Похоже, это не email. Попробуйте ещё раз:")
        return ASK_EMAIL

    ctx.user_data["email"] = email
    msg = await update.message.reply_text("⏳ Отправляю код на почту...")

    try:
        await request_auth_code(email)
        await msg.edit_text(
            f"✉️ Код отправлен на <b>{email}</b>\n\nВведите код из письма:",
            parse_mode=ParseMode.HTML,
        )
        return ASK_CODE
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка при отправке кода:\n<code>{e}</code>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END


async def got_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    email = ctx.user_data.get("email", "")

    msg = await update.message.reply_text("⏳ Проверяю код...")

    try:
        result = await login_with_code(email, code)
        auth.save_token(result["token"])
        username = result.get("username", "")

        await msg.edit_text(
            f"✅ <b>Авторизация успешна!</b>\n\n"
            f"👤 Аккаунт: <b>{username}</b>\n\n"
            f"Бот начнёт уведомлять вас о новых покупках и жалобах.",
            parse_mode=ParseMode.HTML,
        )
        start_polling()
        return ConversationHandler.END

    except Exception as e:
        await msg.edit_text(
            f"❌ Неверный код или ошибка:\n<code>{e}</code>\n\n"
            f"Попробуйте /login снова.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END


async def cancel_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("↩️ Авторизация отменена.")
    return ConversationHandler.END


# ── Other commands ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    is_auth = auth.is_authenticated()
    status_line = "✅ Авторизован, мониторинг активен." if is_auth else "🔐 Не авторизован. Используйте /login."

    await update.message.reply_text(
        "👋 <b>Playerok Monitor Bot</b>\n\n"
        f"{status_line}\n\n"
        "/login — войти в аккаунт Playerok\n"
        "/status — статистика\n"
        "/check — проверить прямо сейчас\n"
        "/logout — выйти из аккаунта",
        parse_mode=ParseMode.HTML,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    polling_status = "🟢 Работает" if (_polling_task and not _polling_task.done()) else "🔴 Остановлен"
    auth_status = "✅ Авторизован" if auth.is_authenticated() else "❌ Не авторизован"

    await update.message.reply_text(
        f"📊 <b>Статус бота</b>\n\n"
        f"🔐 Аккаунт: {auth_status}\n"
        f"🔄 Мониторинг: {polling_status}\n"
        f"⏱ Интервал: <b>{config.POLL_INTERVAL}с</b>\n\n"
        f"📦 Обработано покупок: <b>{stats['orders']}</b>\n"
        f"⚠️ Обработано жалоб: <b>{stats['complaints']}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth.is_authenticated():
        await update.message.reply_text("🔐 Сначала войдите через /login")
        return

    await update.message.reply_text("🔍 Проверяю Playerok...")
    try:
        await poll_once()
        await update.message.reply_text("✅ Готово.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stop_polling()
    auth.clear_token()
    await update.message.reply_text("👋 Выход выполнен. Мониторинг остановлен.")


# ── Entry point ───────────────────────────────────────────────────────────────

async def on_startup(app: Application):
    init_db()
    auth.load_token()
    if auth.is_authenticated():
        logger.info("Token loaded from disk — starting polling")
        start_polling()
    else:
        logger.info("Not authenticated — waiting for /login")


def validate_config():
    missing = []
    if not config.TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not config.TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


def main():
    validate_config()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_email)],
            ASK_CODE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel_login)],
    )

    app.add_handler(login_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("logout", cmd_logout))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
