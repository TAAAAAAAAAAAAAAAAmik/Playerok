"""
Команда /token — прислать боту куку сессии Playerok прямо в чат.

Токен слетает: он живёт ограниченное время и привязан к сессии. Раньше его
меняли на сервере — правка `.env` и перезапуск службы. Теперь достаточно
прислать новое значение боту: он проверит его запросом к аккаунту, сохранит
и сразу начнёт им пользоваться.

Присланное сообщение бот удаляет: в переписке остаётся ключ от аккаунта, а
история Telegram живёт долго.
"""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import credentials
import playerok_client as api

logger = logging.getLogger(__name__)

ASK_TOKEN = 0

PROMPT = (
    "🔑 <b>Пришлите токен Playerok.</b>\n\n"
    "Где взять: откройте playerok.com в браузере → DevTools (F12) → "
    "Application → Cookies → <code>playerok.com</code> → значение <code>token</code>.\n\n"
    "Можно прислать как есть или в виде <code>token=eyJ…</code> — разберусь.\n"
    "Сообщение с токеном я удалю сразу после проверки.\n\n"
    "Отменить — /cancel"
)


def _is_owner(update: Update) -> bool:
    """
    Токен принимаем только от хозяина бота. Без этой проверки любой, кто
    нашёл бота, мог бы подменить сессию аккаунта.
    """
    if not config.TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("Эта команда только для хозяина бота.")
        return ConversationHandler.END

    await update.message.reply_text(PROMPT, parse_mode=ParseMode.HTML)
    return ASK_TOKEN


async def got_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    # Убираем сообщение сразу: даже если токен окажется негодным, светить его
    # в истории незачем. Право на удаление есть не всегда — это не повод падать.
    try:
        await update.message.delete()
    except Exception as e:
        logger.debug("Не смог удалить сообщение с токеном: %s", e)

    if len(raw) < 20:
        await update.message.chat.send_message(
            "❌ Это не похоже на токен — он длинный и начинается с <code>eyJ</code>.\n"
            "Пришлите ещё раз или /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return ASK_TOKEN

    previous = credentials.load()
    credentials.save(raw)

    checking = await update.message.chat.send_message("⏳ Проверяю токен…")
    try:
        viewer = await api.fetch_viewer()
    except Exception as e:
        # Негодный токен не оставляем: с ним бот сломается тише и непонятнее,
        # чем если вернуть прежний.
        if previous:
            credentials.save(previous)
        else:
            credentials.clear()
        logger.warning("Токен не принят: %s", e)
        await checking.edit_text(
            f"❌ Токен не подошёл: <code>{e}</code>\n\n"
            + ("Вернул прежний." if previous else "Прежнего не было, сессии нет.")
            + "\n\nПопробуйте ещё раз: /token",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    await checking.edit_text(
        "✅ <b>Токен принят.</b>\n\n"
        f"👤 {viewer.get('username', '—')}\n"
        f"🆔 <code>{viewer.get('id', '—')}</code>\n\n"
        "<i>Действует сразу, перезапускать бота не нужно.</i>",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("↩️ Токен не менял.")
    return ConversationHandler.END


def build_token_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("token", cmd_token)],
        states={
            ASK_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_token),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("token", cmd_token)],
        allow_reentry=True,
        conversation_timeout=600,
    )
