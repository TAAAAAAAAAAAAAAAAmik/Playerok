"""
Диалог /create — создание товара на Playerok.

Бот держит открытый мастер Playerok в браузере и показывает его пункты
кнопками: игра, категория, способ передачи, характеристики, размещение.
Текстом вводится только то, что списком не предложишь: название, описание,
цена и значения полей товара — причём подписи полей бот берёт с того же
экрана мастера, так что спрашивает ровно то, что нужно категории.

Порядок шагов повторяет мастер: фото идут перед названием, поля данных —
после цены.
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
from selenium_creator import CreationError, ProductDraft
from wizard_session import WizardSession

logger = logging.getLogger(__name__)

(
    PICK_GAME,
    SEARCH_GAME,
    PICK_CATEGORY,
    PICK_OBTAINING,
    PICK_ATTRIBUTE,
    UPLOAD_PHOTOS,
    ENTER_NAME,
    ENTER_DESCRIPTION,
    ENTER_PRICE,
    ENTER_DATA_FIELD,
    CONFIRM,
) = range(11)

PAGE_SIZE = 8
# Браузер отвечает не мгновенно: шаг мастера с загрузкой файла может занять
# полминуты. Дольше ждать нет смысла — лучше честно сказать об этом.
STEP_TIMEOUT = int(os.getenv("WIZARD_STEP_TIMEOUT", "60"))


# ── Служебное ─────────────────────────────────────────────────────────────────

async def _run(func, *args):
    """Выполняет шаг мастера в потоке: Selenium синхронный."""
    return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=STEP_TIMEOUT)


def _keyboard(items: list[dict], prefix: str, page: int = 0,
              extra: list[list[InlineKeyboardButton]] | None = None):
    start = page * PAGE_SIZE
    rows = [
        [InlineKeyboardButton(item["name"], callback_data=f"{prefix}:{start + i}")]
        for i, item in enumerate(items[start:start + PAGE_SIZE])
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}page:{page - 1}"))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton("Ещё ➡️", callback_data=f"{prefix}page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.extend(extra or [])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")])
    return InlineKeyboardMarkup(rows)


async def _edit(query, text: str, keyboard=None):
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:  # текст не изменился или сообщение устарело
        logger.debug("Не смог обновить сообщение: %s", e)


async def _fail(target, error: Exception, ctx=None):
    """Сообщает об ошибке и закрывает сеанс мастера."""
    WizardSession.get().close()
    text = (
        f"⏱ Мастер не ответил за {STEP_TIMEOUT} с. Попробуйте /create снова."
        if isinstance(error, asyncio.TimeoutError)
        else f"❌ <code>{error}</code>\n\nПопробуйте /create снова."
    )
    if hasattr(target, "edit_message_text"):
        await _edit(target, text)
    else:
        await target.reply_text(text, parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ── Шаг 1: игра ───────────────────────────────────────────────────────────────

async def cmd_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    message = await update.message.reply_text(
        "⏳ Открываю мастер Playerok… (первый запуск — до полуминуты)"
    )

    session = WizardSession.get()
    try:
        games = await _run(session.games)
    except Exception as e:
        logger.exception("Мастер не открылся")
        return await _fail(message, e)

    if not games:
        return await _fail(message, CreationError("Мастер открылся, но список игр пуст"))

    ctx.user_data["games"] = games
    await message.edit_text(
        "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(
            games, "game", 0,
            extra=[[InlineKeyboardButton("🔎 Найти по названию", callback_data="gamesearch:1")]],
        ),
    )
    return PICK_GAME


async def game_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(
        query, "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        _keyboard(
            ctx.user_data["games"], "game", int(query.data.split(":")[1]),
            extra=[[InlineKeyboardButton("🔎 Найти по названию", callback_data="gamesearch:1")]],
        ),
    )
    return PICK_GAME


async def game_search_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, "🔎 Введите название игры или приложения:")
    return SEARCH_GAME


async def game_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = await update.message.reply_text("⏳ Ищу в мастере…")
    try:
        games = await _run(WizardSession.get().games, update.message.text.strip())
    except Exception as e:
        return await _fail(message, e)

    if not games:
        await message.edit_text("Ничего не нашлось. Введите другое название:")
        return SEARCH_GAME

    ctx.user_data["games"] = games
    await message.edit_text(
        "🎮 <b>Шаг 1 из 9.</b> Выберите игру:",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(games, "game", 0),
    )
    return PICK_GAME


# ── Шаги 2–4: категория, способ передачи, характеристики ──────────────────────

async def game_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[1])
    game = ctx.user_data["games"][index]["name"]
    ctx.user_data["game"] = game

    await _edit(query, f"🎮 {game}\n\n⏳ Загружаю категории…")
    try:
        categories = await _run(WizardSession.get().pick_game, index)
    except Exception as e:
        return await _fail(query, e)

    if not categories:
        return await _fail(query, CreationError("У этой игры нет категорий"))

    ctx.user_data["categories"] = categories
    await _edit(query, f"🎮 {game}\n\n🗂 <b>Шаг 2 из 9.</b> Выберите категорию:",
                _keyboard(categories, "cat", 0))
    return PICK_CATEGORY


async def category_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, "🗂 <b>Шаг 2 из 9.</b> Выберите категорию:",
                _keyboard(ctx.user_data["categories"], "cat", int(query.data.split(":")[1])))
    return PICK_CATEGORY


async def category_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[1])
    category = ctx.user_data["categories"][index]["name"]
    ctx.user_data["category"] = category

    await _edit(query, f"🗂 {category}\n\n⏳ Загружаю способы передачи…")
    try:
        obtaining = await _run(WizardSession.get().pick_category, index)
    except Exception as e:
        return await _fail(query, e)

    if not obtaining:
        ctx.user_data["obtaining"] = "—"
        return await _ask_photos(query, ctx)

    ctx.user_data["obtaining_types"] = obtaining
    await _edit(query, f"🗂 {category}\n\n📤 <b>Шаг 3 из 9.</b> Как покупатель получит товар?",
                _keyboard(obtaining, "obt", 0))
    return PICK_OBTAINING


async def obtaining_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, "📤 <b>Шаг 3 из 9.</b> Способ передачи:",
                _keyboard(ctx.user_data["obtaining_types"], "obt", int(query.data.split(":")[1])))
    return PICK_OBTAINING


async def obtaining_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[1])
    obtaining = ctx.user_data["obtaining_types"][index]["name"]
    ctx.user_data["obtaining"] = obtaining

    await _edit(query, f"📤 {obtaining}\n\n⏳ Смотрю характеристики…")
    try:
        attributes = await _run(WizardSession.get().pick_obtaining, index)
    except Exception as e:
        return await _fail(query, e)

    if not attributes:
        ctx.user_data["attributes"] = []
        return await _ask_photos(query, ctx)

    ctx.user_data["attribute_options"] = attributes
    await _edit(query, "⚙️ <b>Шаг 4 из 9.</b> Выберите характеристику:",
                _keyboard(attributes, "attr", 0))
    return PICK_ATTRIBUTE


async def attribute_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, "⚙️ <b>Шаг 4 из 9.</b> Выберите характеристику:",
                _keyboard(ctx.user_data["attribute_options"], "attr",
                          int(query.data.split(":")[1])))
    return PICK_ATTRIBUTE


async def attribute_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[1])
    attribute = ctx.user_data["attribute_options"][index]["name"]

    try:
        await _run(WizardSession.get().pick_attribute, index)
    except Exception as e:
        return await _fail(query, e)

    ctx.user_data.setdefault("attributes", []).append(attribute)
    return await _ask_photos(query, ctx)


# ── Шаг 5: фото ───────────────────────────────────────────────────────────────

async def _ask_photos(query, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["images"] = []
    await _edit(
        query,
        "🖼 <b>Шаг 5 из 9.</b> Пришлите фотографии товара.\n"
        "<i>Без фото мастер дальше не пустит.</i>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")]]),
    )
    return UPLOAD_PHOTOS


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
        await update.message.reply_text("❌ Это не изображение.")
        return UPLOAD_PHOTOS

    await tg_file.download_to_drive(path)
    ctx.user_data.setdefault("images", []).append(path)
    await update.message.reply_text(
        f"🖼 Принято ({len(ctx.user_data['images'])}).",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Готово", callback_data="photos:done")]]
        ),
    )
    return UPLOAD_PHOTOS


async def photos_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    images = ctx.user_data.get("images") or []
    if not images:
        await query.answer("Сначала пришлите хотя бы одно фото", show_alert=True)
        return UPLOAD_PHOTOS

    await _edit(query, f"🖼 Загружаю {len(images)} фото в мастер…")
    try:
        await _run(WizardSession.get().upload_images, images)
    except Exception as e:
        return await _fail(query, e)

    await _edit(query, "📛 <b>Шаг 6 из 9.</b> Введите название товара:")
    return ENTER_NAME


# ── Шаги 6–8: название, описание, цена ────────────────────────────────────────

async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("📝 <b>Шаг 7 из 9.</b> Введите описание товара:",
                                    parse_mode=ParseMode.HTML)
    return ENTER_DESCRIPTION


async def got_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["description"] = update.message.text.strip()
    message = await update.message.reply_text("⏳ Заполняю карточку…")

    try:
        await _run(WizardSession.get().fill_about,
                   ctx.user_data["name"], ctx.user_data["description"])
    except Exception as e:
        return await _fail(message, e)

    await message.edit_text("💵 <b>Шаг 8 из 9.</b> Введите цену в рублях:",
                            parse_mode=ParseMode.HTML)
    return ENTER_PRICE


async def got_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace(",", ".")
    try:
        price = int(float(raw))
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Нужно положительное число. Введите цену:")
        return ENTER_PRICE

    ctx.user_data["price"] = price
    message = await update.message.reply_text("⏳ Проставляю цену…")

    try:
        labels = await _run(WizardSession.get().fill_price, price)
    except Exception as e:
        return await _fail(message, e)

    ctx.user_data["field_labels"] = labels
    ctx.user_data["field_index"] = 0
    ctx.user_data["data_fields"] = {}
    return await _ask_data_field(message, ctx)


# ── Шаг 9: данные товара и размещение ─────────────────────────────────────────

async def _ask_data_field(message, ctx: ContextTypes.DEFAULT_TYPE):
    labels = ctx.user_data["field_labels"]
    index = ctx.user_data["field_index"]

    if index >= len(labels):
        return await _ask_placement(message, ctx)

    await message.reply_text(
        f"🔑 <b>Шаг 9 из 9.</b> {labels[index]}:\n"
        "<i>это увидит покупатель после оплаты</i>",
        parse_mode=ParseMode.HTML,
    )
    return ENTER_DATA_FIELD


async def got_data_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    label = ctx.user_data["field_labels"][ctx.user_data["field_index"]]
    ctx.user_data["data_fields"][label] = update.message.text.strip()
    ctx.user_data["field_index"] += 1
    return await _ask_data_field(update.message, ctx)


async def _ask_placement(message, ctx: ContextTypes.DEFAULT_TYPE):
    sending = await message.reply_text("⏳ Сохраняю данные товара…")
    try:
        await _run(WizardSession.get().fill_data_fields, ctx.user_data["data_fields"])
    except Exception as e:
        return await _fail(sending, e)

    await sending.edit_text(
        _summary(ctx.user_data) + "\n\nКак выставляем?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Выставить бесплатно", callback_data="place:free")],
            [InlineKeyboardButton("📝 Сохранить черновиком", callback_data="place:later")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")],
        ]),
    )
    return CONFIRM


def _summary(data: dict) -> str:
    attributes = ", ".join(data.get("attributes", [])) or "—"
    fields = ", ".join(f"{k}: {v}" for k, v in data.get("data_fields", {}).items()) or "—"
    return (
        "🧾 <b>Товар заполнен</b>\n\n"
        f"🎮 {data['game']}\n"
        f"🗂 {data['category']}\n"
        f"📤 {data.get('obtaining', '—')}\n"
        f"⚙️ {attributes}\n"
        f"📛 <b>{data['name']}</b>\n"
        f"📝 {data['description']}\n"
        f"💵 <b>{data['price']} ₽</b>\n"
        f"🖼 Фото: {len(data.get('images', []))}\n"
        f"🔑 {fields}"
    )


async def placement_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    placement = query.data.split(":", 1)[1]

    await _edit(query, "⏳ Завершаю…")
    session = WizardSession.get()
    try:
        detail = await _run(session.publish, placement)
    except Exception as e:
        return await _fail(query, e)

    session.close()
    ctx.user_data.clear()
    await query.message.reply_text(
        ("📝 <b>Товар сохранён черновиком.</b>\nВыставить можно в личном кабинете."
         if placement == "later"
         else "🎉 <b>Товар выставлен на продажу.</b>") + f"\n\n<i>{detail}</i>",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# ── Отмена ────────────────────────────────────────────────────────────────────

async def cancel_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    WizardSession.get().close()
    ctx.user_data.clear()
    await _edit(query, "↩️ Создание отменено, мастер закрыт.")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    WizardSession.get().close()
    ctx.user_data.clear()
    await update.message.reply_text("↩️ Создание отменено, мастер закрыт.")
    return ConversationHandler.END


def build_create_conversation() -> ConversationHandler:
    text_only = filters.TEXT & ~filters.COMMAND
    cancel_handler = CallbackQueryHandler(cancel_button, pattern="^cancel:")

    return ConversationHandler(
        entry_points=[CommandHandler("create", cmd_create)],
        states={
            PICK_GAME: [
                CallbackQueryHandler(game_chosen, pattern=r"^game:\d+$"),
                CallbackQueryHandler(game_page, pattern=r"^gamepage:\d+$"),
                CallbackQueryHandler(game_search_prompt, pattern="^gamesearch:"),
                cancel_handler,
            ],
            SEARCH_GAME: [MessageHandler(text_only, game_search), cancel_handler],
            PICK_CATEGORY: [
                CallbackQueryHandler(category_chosen, pattern=r"^cat:\d+$"),
                CallbackQueryHandler(category_page, pattern=r"^catpage:\d+$"),
                cancel_handler,
            ],
            PICK_OBTAINING: [
                CallbackQueryHandler(obtaining_chosen, pattern=r"^obt:\d+$"),
                CallbackQueryHandler(obtaining_page, pattern=r"^obtpage:\d+$"),
                cancel_handler,
            ],
            PICK_ATTRIBUTE: [
                CallbackQueryHandler(attribute_chosen, pattern=r"^attr:\d+$"),
                CallbackQueryHandler(attribute_page, pattern=r"^attrpage:\d+$"),
                cancel_handler,
            ],
            UPLOAD_PHOTOS: [
                CallbackQueryHandler(photos_done, pattern="^photos:done$"),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, got_image),
                cancel_handler,
            ],
            ENTER_NAME: [MessageHandler(text_only, got_name), cancel_handler],
            ENTER_DESCRIPTION: [MessageHandler(text_only, got_description), cancel_handler],
            ENTER_PRICE: [MessageHandler(text_only, got_price), cancel_handler],
            ENTER_DATA_FIELD: [MessageHandler(text_only, got_data_field), cancel_handler],
            CONFIRM: [
                CallbackQueryHandler(placement_chosen, pattern="^place:"),
                cancel_handler,
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("create", cmd_create)],
        allow_reentry=True,
        conversation_timeout=1800,
    )
