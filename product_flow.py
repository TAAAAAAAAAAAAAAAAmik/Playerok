"""
Диалог /create — создание товара на Playerok через браузер (Selenium).

Бот собирает данные по шагам мастера, показывает черновик на подтверждение
и затем прогоняет 9 шагов в браузере, отправляя прогресс и скриншоты.
"""
import asyncio
import logging
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from selenium_creator import CreationError, ProductDraft, create_product

logger = logging.getLogger(__name__)

(
    GAME,
    CATEGORY,
    OBTAINING,
    OPTIONS,
    NAME,
    DESCRIPTION,
    DATA_FIELDS,
    PRICE,
    IMAGES,
    CONFIRM,
) = range(10)

SKIP = {"-", "нет", "skip", "пропустить"}


def _parse_pairs(text: str) -> dict[str, str]:
    """Разбирает строки вида `Поле = значение` в словарь."""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            pairs[key] = value
    return pairs


def _draft_from(user_data: dict) -> ProductDraft:
    return ProductDraft(
        game=user_data["game"],
        category=user_data["category"],
        obtaining_type=user_data["obtaining"],
        name=user_data["name"],
        description=user_data["description"],
        price=user_data["price"],
        attributes=user_data.get("attributes", []),
        data_fields=user_data.get("data_fields", {}),
        images=user_data.get("images", []),
        placement=user_data.get("placement", "free"),
    )


PLACEMENT_NAMES = {
    "free": "Обычный (бесплатно)",
    "premium": "Премиум (платно)",
    "later": "Выставить позже (черновик)",
}


def _summary(d: ProductDraft) -> str:
    attributes = ", ".join(d.attributes) or "—"
    fields = ", ".join(f"{k}={v}" for k, v in d.data_fields.items()) or "—"
    return (
        "🧾 <b>Черновик товара</b>\n\n"
        f"🎮 Игра: <b>{d.game}</b>\n"
        f"🗂 Категория: <b>{d.category}</b>\n"
        f"📤 Способ передачи: <b>{d.obtaining_type}</b>\n"
        f"⚙️ Характеристики: {attributes}\n"
        f"📛 Название: <b>{d.name}</b>\n"
        f"📝 Описание: {d.description}\n"
        f"🔑 Данные товара: {fields}\n"
        f"💵 Цена: <b>{d.price} ₽</b>\n"
        f"🖼 Изображений: <b>{len(d.images)}</b>\n"
        f"🚀 Размещение: <b>{PLACEMENT_NAMES.get(d.placement, d.placement)}</b>"
    )


# ── Шаги опроса ───────────────────────────────────────────────────────────────

async def cmd_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "🛠 <b>Создание товара</b> (9 шагов мастера Playerok)\n\n"
        "В любой момент — /cancel.\n\n"
        "1️⃣ Введите название игры или приложения (например: <code>Telegram</code>):",
        parse_mode=ParseMode.HTML,
    )
    return GAME


async def got_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["game"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ Категория товара (например: <code>Подарки (NFT)</code>):",
        parse_mode=ParseMode.HTML,
    )
    return CATEGORY


async def got_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["category"] = update.message.text.strip()
    await update.message.reply_text(
        "3️⃣ Способ передачи (например: <code>По @username</code>):",
        parse_mode=ParseMode.HTML,
    )
    return OBTAINING


async def got_obtaining(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["obtaining"] = update.message.text.strip()
    await update.message.reply_text(
        "4️⃣ Характеристики — как они подписаны на сайте, по одной в строке "
        "(например: <code>100 звёзд</code>).\n"
        "Если характеристик нет, отправьте <code>-</code>",
        parse_mode=ParseMode.HTML,
    )
    return OPTIONS


async def got_options(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["attributes"] = (
        [] if text.lower() in SKIP else [t.strip() for t in text.splitlines() if t.strip()]
    )
    await update.message.reply_text("5️⃣ Название товара:")
    return NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("6️⃣ Описание товара:")
    return DESCRIPTION


async def got_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["description"] = update.message.text.strip()
    await update.message.reply_text(
        "7️⃣ Данные товара — то, что покупатель получит после оплаты.\n"
        "Формат: <code>Поле = значение</code>, по одной в строке.\n"
        "Если полей нет, отправьте <code>-</code>",
        parse_mode=ParseMode.HTML,
    )
    return DATA_FIELDS


async def got_data_fields(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["data_fields"] = {} if text.lower() in SKIP else _parse_pairs(text)
    await update.message.reply_text("8️⃣ Цена в рублях (целое число):")
    return PRICE


async def got_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace(",", ".")
    try:
        price = int(float(raw))
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Нужно положительное число. Введите цену ещё раз:")
        return PRICE

    ctx.user_data["price"] = price
    ctx.user_data["images"] = []
    await update.message.reply_text(
        "9️⃣ Пришлите изображения товара (можно несколько).\n"
        "Когда закончите — /done. Без картинок — /skip"
    )
    return IMAGES


async def got_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1] if update.message.photo else None
    doc = update.message.document

    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    if photo:
        tg_file = await photo.get_file()
        path = os.path.join(config.UPLOAD_DIR, f"{uuid.uuid4().hex}.jpg")
    elif doc and (doc.mime_type or "").startswith("image/"):
        tg_file = await doc.get_file()
        ext = os.path.splitext(doc.file_name or "")[1] or ".jpg"
        path = os.path.join(config.UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
    else:
        await update.message.reply_text("❌ Это не изображение. Пришлите картинку или /done")
        return IMAGES

    await tg_file.download_to_drive(path)
    ctx.user_data.setdefault("images", []).append(path)
    await update.message.reply_text(
        f"🖼 Принято ({len(ctx.user_data['images'])}). Ещё картинку или /done"
    )
    return IMAGES


async def images_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    draft = _draft_from(ctx.user_data)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 Выставить бесплатно", callback_data="create:free")],
            [InlineKeyboardButton("📝 Сохранить черновиком", callback_data="create:later")],
            [InlineKeyboardButton("❌ Отмена", callback_data="create:cancel")],
        ]
    )
    await update.message.reply_text(
        _summary(draft) + "\n\nКак выставляем?",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    return CONFIRM


# ── Запуск мастера ────────────────────────────────────────────────────────────

async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "create:cancel":
        await query.edit_message_text("↩️ Создание отменено.")
        ctx.user_data.clear()
        return ConversationHandler.END

    # На девятом шаге сайт по умолчанию предлагает платный «Премиум» —
    # выбор пользователя передаём в мастер явно.
    ctx.user_data["placement"] = query.data.split(":", 1)[1]
    draft = _draft_from(ctx.user_data)
    chat_id = query.message.chat_id
    await query.edit_message_text(
        "🖥 Запускаю браузер и прохожу мастер создания…", parse_mode=ParseMode.HTML
    )

    loop = asyncio.get_running_loop()
    progress: asyncio.Queue = asyncio.Queue()

    def on_step(step):
        # Вызывается из потока Selenium — возвращаем событие в event loop.
        loop.call_soon_threadsafe(progress.put_nowait, step)

    async def report():
        while True:
            step = await progress.get()
            if step is None:
                return
            icon = "✅" if step.ok else "❌"
            text = f"{icon} <b>Шаг {step.number}/9</b> — {step.title}\n{step.detail}"
            try:
                if step.screenshot and os.path.exists(step.screenshot):
                    with open(step.screenshot, "rb") as img:
                        await ctx.bot.send_photo(
                            chat_id, img, caption=text, parse_mode=ParseMode.HTML
                        )
                else:
                    await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error("Не смог отправить прогресс шага %s: %s", step.number, e)

    reporter = asyncio.create_task(report())
    try:
        await asyncio.to_thread(create_product, draft, on_step)
        await progress.put(None)
        await reporter
        done_text = (
            "📝 <b>Товар сохранён черновиком.</b>\n"
            "Выставить его можно в личном кабинете Playerok."
            if draft.placement == "later"
            else "🎉 <b>Товар создан и отправлен на публикацию.</b>\n"
            "Проверьте его в личном кабинете Playerok."
        )
        await ctx.bot.send_message(chat_id, done_text, parse_mode=ParseMode.HTML)
    except CreationError as e:
        await progress.put(None)
        await reporter
        await ctx.bot.send_message(
            chat_id,
            f"❌ <b>Мастер остановился.</b>\n<code>{e}</code>\n\n"
            f"Скриншоты и HTML шагов лежат в <code>{config.DEBUG_DIR}</code> — "
            "по ним поправим селекторы.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await progress.put(None)
        await reporter
        logger.exception("Создание товара упало")
        await ctx.bot.send_message(
            chat_id, f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML
        )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("↩️ Создание товара отменено.")
    return ConversationHandler.END


def build_create_conversation() -> ConversationHandler:
    text_only = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("create", cmd_create)],
        states={
            GAME: [MessageHandler(text_only, got_game)],
            CATEGORY: [MessageHandler(text_only, got_category)],
            OBTAINING: [MessageHandler(text_only, got_obtaining)],
            OPTIONS: [MessageHandler(text_only, got_options)],
            NAME: [MessageHandler(text_only, got_name)],
            DESCRIPTION: [MessageHandler(text_only, got_description)],
            DATA_FIELDS: [MessageHandler(text_only, got_data_fields)],
            PRICE: [MessageHandler(text_only, got_price)],
            IMAGES: [
                CommandHandler("done", images_done),
                CommandHandler("skip", images_done),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, got_image),
            ],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^create:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
