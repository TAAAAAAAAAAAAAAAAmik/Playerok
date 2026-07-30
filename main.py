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

import auth
import config
import playerok_client
from database import init_db, is_seen, mark_seen, get_stats
from notifier import send, format_deal, format_problem

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

ASK_TOKEN = 0

_polling_task: asyncio.Task | None = None


# ── Access control ────────────────────────────────────────────────────────────

def _is_owner(update: Update) -> bool:
    """Only the configured chat may drive the bot.

    Commands carry a live Playerok session: without this check any stranger who
    finds the bot could replace the token via /login or drop it via /logout.
    """
    chat = update.effective_chat
    return bool(chat and str(chat.id) == str(config.TELEGRAM_CHAT_ID))


async def _reject(update: Update):
    logger.warning("Отклонён доступ из чата %s", getattr(update.effective_chat, "id", "?"))
    if update.message:
        await update.message.reply_text("⛔ Этот бот приватный.")


# ── Polling ───────────────────────────────────────────────────────────────────

async def poll_once() -> dict:
    """Fetch deals and notify about anything not seen before.

    Returns counters so /check can report what happened.
    """
    deals = await playerok_client.fetch_deals()
    account_id = await playerok_client.get_account_id()

    # On an empty database every existing deal looks new, so the first pass
    # only records state instead of sending a burst of notifications.
    warmup = not config.NOTIFY_ON_FIRST_RUN and get_stats()["orders"] == 0
    counters = {"deals": 0, "problems": 0, "warmed_up": 0}

    for deal in reversed(deals):  # oldest first, so notifications arrive in order
        deal_id = getattr(deal, "id", None)
        if not deal_id:
            continue

        if not is_seen("seen_orders", deal_id):
            if warmup:
                counters["warmed_up"] += 1
            else:
                await send(format_deal(deal, account_id))
                counters["deals"] += 1
            mark_seen("seen_orders", deal_id)

        if getattr(deal, "has_problem", False) and not is_seen("seen_complaints", deal_id):
            if not warmup:
                await send(format_problem(deal, account_id))
                counters["problems"] += 1
            mark_seen("seen_complaints", deal_id)

    if warmup and counters["warmed_up"]:
        await send(
            f"👀 Отслеживаю аккаунт. Запомнил {counters['warmed_up']} существующих "
            f"сделок без уведомлений — сообщу только о новых."
        )

    return counters


async def polling_loop():
    logger.info("Опрос запущен, интервал %d с", config.POLL_INTERVAL)
    failures = 0
    while True:
        try:
            await poll_once()
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            failures += 1
            logger.error("Ошибка опроса (%d подряд): %s", failures, e)
            # A stale session is the likeliest cause — drop it so the next pass
            # rebuilds it from disk, and tell the owner once it looks persistent.
            playerok_client.reset()
            if failures == 3:
                await send(
                    "⚠️ <b>Опрос Playerok не проходит.</b>\n\n"
                    f"<code>{e}</code>\n\n"
                    "Скорее всего истёк токен — обновите его через /login."
                )
        await asyncio.sleep(config.POLL_INTERVAL)


def start_polling():
    global _polling_task
    if _polling_task is None or _polling_task.done():
        _polling_task = asyncio.create_task(polling_loop())


def stop_polling():
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
    _polling_task = None


# ── /login ────────────────────────────────────────────────────────────────────

LOGIN_HELP = (
    "🔑 <b>Вход в Playerok</b>\n\n"
    "У Playerok нет входа по email и коду — сайт работает на JWT из cookie, "
    "поэтому нужен он же.\n\n"
    "Где взять:\n"
    "1. Откройте <b>playerok.com</b> в браузере на компьютере и войдите\n"
    "2. F12 → вкладка <b>Application</b> (в Firefox — <b>Storage</b>)\n"
    "3. Cookies → playerok.com → скопируйте значение <code>token</code>\n\n"
    "Пришлите его одним сообщением. Если Playerok начнёт требовать "
    "DDoS-Guard, добавьте вторым словом значение cookie <code>__ddg5_</code>.\n\n"
    "Сообщение с токеном я удалю сразу после проверки. /cancel — отмена."
)


async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await _reject(update)
        return ConversationHandler.END

    await update.message.reply_text(LOGIN_HELP, parse_mode=ParseMode.HTML)
    return ASK_TOKEN


async def got_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    token = parts[0].strip()
    ddg5 = parts[1].strip() if len(parts) > 1 else ""

    # The message contains a live credential — remove it from the chat.
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение с токеном: %s", e)

    status = await update.effective_chat.send_message("⏳ Проверяю токен...")

    try:
        info = await playerok_client.login(token, ddg5)
    except Exception as e:
        playerok_client.reset()
        await status.edit_text(
            f"❌ Токен не принят:\n<code>{e}</code>\n\nПопробуйте /login снова.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    auth.save(token, ddg5)
    await status.edit_text(
        f"✅ <b>Готово!</b>\n\n"
        f"👤 Аккаунт: <b>{info['username']}</b>\n"
        f"📧 {info['email']}\n\n"
        f"Буду сообщать о новых сделках и проблемах по ним.",
        parse_mode=ParseMode.HTML,
    )
    start_polling()
    return ConversationHandler.END


async def cancel_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("↩️ Отменено.")
    return ConversationHandler.END


# ── Other commands ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reject(update)

    state = "✅ Авторизован, мониторинг активен." if auth.is_authenticated() \
        else "🔐 Не авторизован. Начните с /login."
    await update.message.reply_text(
        "👋 <b>Playerok Monitor</b>\n\n"
        f"{state}\n\n"
        "/login — указать токен Playerok\n"
        "/status — состояние и счётчики\n"
        "/check — проверить прямо сейчас\n"
        "/logout — забыть токен и остановить опрос",
        parse_mode=ParseMode.HTML,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reject(update)

    stats = get_stats()
    running = _polling_task and not _polling_task.done()
    await update.message.reply_text(
        f"📊 <b>Статус</b>\n\n"
        f"🔐 Токен: {'✅ есть' if auth.is_authenticated() else '❌ нет'}\n"
        f"🔄 Опрос: {'🟢 работает' if running else '🔴 остановлен'}\n"
        f"⏱ Интервал: <b>{config.POLL_INTERVAL} с</b>\n\n"
        f"📦 Известных сделок: <b>{stats['orders']}</b>\n"
        f"⚠️ Отмеченных проблем: <b>{stats['complaints']}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reject(update)

    if not auth.is_authenticated():
        await update.message.reply_text("🔐 Сначала /login")
        return

    status = await update.message.reply_text("🔍 Проверяю Playerok...")
    try:
        counters = await poll_once()
    except Exception as e:
        playerok_client.reset()
        await status.edit_text(
            f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML
        )
        return

    if counters["deals"] or counters["problems"]:
        await status.edit_text(
            f"✅ Новых сделок: {counters['deals']}, проблем: {counters['problems']}"
        )
    elif counters["warmed_up"]:
        await status.edit_text(f"✅ Запомнил {counters['warmed_up']} сделок.")
    else:
        await status.edit_text("✅ Нового нет.")


async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reject(update)

    stop_polling()
    playerok_client.reset()
    auth.clear()
    await update.message.reply_text("👋 Токен удалён, опрос остановлен.")


# ── Entry point ───────────────────────────────────────────────────────────────

async def on_startup(app: Application):
    init_db()
    auth.load()
    if auth.is_authenticated():
        logger.info("Токен найден на диске — запускаю опрос")
        start_polling()
    else:
        logger.info("Токена нет — жду /login")


def validate_config():
    missing = [
        name for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if not getattr(config, name)
    ]
    if missing:
        logger.error("Не заданы переменные окружения: %s", ", ".join(missing))
        sys.exit(1)


def main():
    validate_config()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_token)]},
        fallbacks=[CommandHandler("cancel", cancel_login)],
    ))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("logout", cmd_logout))

    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
