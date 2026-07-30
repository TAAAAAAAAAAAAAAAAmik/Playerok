"""
Диалог /create — создание товара на Playerok.

Всё, что можно предложить списком, выбирается кнопками: игра, категория,
способ передачи, характеристики, размещение. Списки тянутся из API, поэтому
названия всегда совпадают с сайтом. Текстом остаётся только то, что списком
не предложишь: название, описание, цена и значения полей товара.
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
import playerok_client as api
from api_creator import ApiCreationError
from api_creator import create_product as create_product_api
from selenium_creator import CreationError, ProductDraft
from selenium_creator import create_product as create_product_browser

logger = logging.getLogger(__name__)

(
    PICK_GAME,
    SEARCH_GAME,
    PICK_CATEGORY,
    PICK_OBTAINING,
    PICK_OPTIONS,
    ENTER_NAME,
    ENTER_DESCRIPTION,
    ENTER_DATA_FIELD,
    ENTER_PRICE,
    UPLOAD_PHOTOS,
    CONFIRM,
) = range(11)

PAGE_SIZE = 8


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def _list_keyboard(items: list[dict], prefix: str, page: int = 0,
                   extra: list[list[InlineKeyboardButton]] | None = None):
    """Кнопки-строки по названиям с листанием: в callback уходит индекс."""
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]

    rows = [
        [InlineKeyboardButton(item.get("name") or item.get("label") or "?",
                              callback_data=f"{prefix}:{start + i}")]
        for i, item in enumerate(chunk)
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{prefix}page:{page - 1}"))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton("Ещё ➡️", callback_data=f"{prefix}page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.extend(extra or [])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")])
    return InlineKeyboardMarkup(rows)


async def _edit(query, text: str, keyboard=None):
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Шаг 1: игра ───────────────────────────────────────────────────────────────

async def cmd_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    message = await update.message.reply_text("⏳ Загружаю список игр и приложений…")

    try:
        games = await api.search_games(count=48)
    except Exception as e:
        await message.edit_text(f"❌ Не смог получить список игр:\n<code>{e}</code>",
                                parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    ctx.user_data["games"] = games
    await message.edit_text(
        "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        parse_mode=ParseMode.HTML,
        reply_markup=_list_keyboard(
            games, "game", 0,
            extra=[[InlineKeyboardButton("🔎 Найти по названию", callback_data="gamesearch:1")]],
        ),
    )
    return PICK_GAME


async def game_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    await _edit(
        query, "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        _list_keyboard(
            ctx.user_data["games"], "game", page,
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
    message = await update.message.reply_text("⏳ Ищу…")
    try:
        games = await api.search_games(update.message.text.strip(), count=48)
    except Exception as e:
        await message.edit_text(f"❌ Ошибка поиска:\n<code>{e}</code>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    if not games:
        await message.edit_text("Ничего не нашлось. Введите другое название:")
        return SEARCH_GAME

    ctx.user_data["games"] = games
    await message.edit_text(
        "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        parse_mode=ParseMode.HTML,
        reply_markup=_list_keyboard(games, "game", 0),
    )
    return PICK_GAME


# ── Шаг 2: категория ──────────────────────────────────────────────────────────

async def game_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game = ctx.user_data["games"][int(query.data.split(":")[1])]
    ctx.user_data["game"] = game

    await _edit(query, f"🎮 {game['name']}\n\n⏳ Загружаю категории…")
    try:
        page = await api.fetch_game(slug=game.get("slug", ""), game_id=game["id"])
        categories = page.get("categories") or []
    except Exception as e:
        await _edit(query, f"❌ Не смог получить категории:\n<code>{e}</code>")
        return ConversationHandler.END

    if not categories:
        await _edit(query, "❌ У этой игры нет категорий для продажи.")
        return ConversationHandler.END

    ctx.user_data["categories"] = categories
    await _edit(
        query,
        f"🎮 {game['name']}\n\n🗂 <b>Шаг 2 из 9.</b> Выберите категорию:",
        _list_keyboard(categories, "cat", 0),
    )
    return PICK_CATEGORY


async def category_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    await _edit(
        query,
        f"🎮 {ctx.user_data['game']['name']}\n\n🗂 <b>Шаг 2 из 9.</b> Выберите категорию:",
        _list_keyboard(ctx.user_data["categories"], "cat", page),
    )
    return PICK_CATEGORY


# ── Шаг 3: способ передачи ────────────────────────────────────────────────────

async def category_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chosen = ctx.user_data["categories"][int(query.data.split(":")[1])]

    await _edit(query, f"🗂 {chosen['name']}\n\n⏳ Загружаю способы передачи…")
    try:
        # Полные данные категории нужны ради options (характеристик).
        category = await api.fetch_category(category_id=chosen["id"])
        obtaining_types = await api.fetch_obtaining_types(category["id"])
    except Exception as e:
        await _edit(query, f"❌ Не смог получить данные категории:\n<code>{e}</code>")
        return ConversationHandler.END

    ctx.user_data["category"] = category
    ctx.user_data["obtaining_types"] = obtaining_types

    if not obtaining_types:
        ctx.user_data["obtaining"] = {"id": "", "name": "—"}
        return await _ask_options(query, ctx)

    await _edit(
        query,
        f"🗂 {category['name']}\n\n📤 <b>Шаг 3 из 9.</b> Как покупатель получит товар?",
        _list_keyboard(obtaining_types, "obt", 0),
    )
    return PICK_OBTAINING


# ── Шаг 4: характеристики ─────────────────────────────────────────────────────

def _option_groups(category: dict) -> list[tuple[str, list[dict]]]:
    """Опции категории, разложенные по группам («Количество» и т.п.)."""
    groups: dict[str, list[dict]] = {}
    for option in category.get("options") or []:
        groups.setdefault(option.get("group") or "Характеристики", []).append(option)
    return list(groups.items())


async def _ask_options(query, ctx: ContextTypes.DEFAULT_TYPE):
    """Спрашивает следующую незаполненную группу характеристик."""
    groups = ctx.user_data.setdefault(
        "option_groups", _option_groups(ctx.user_data["category"])
    )
    index = ctx.user_data.get("group_index", 0)

    if index >= len(groups):
        await _edit(query, "📛 <b>Шаг 5 из 9.</b> Введите название товара:")
        return ENTER_NAME

    group_name, options = groups[index]
    ctx.user_data["current_options"] = options
    await _edit(
        query,
        f"⚙️ <b>Шаг 4 из 9.</b> {group_name}:",
        _list_keyboard(
            [{"name": o.get("label") or o.get("value")} for o in options], "opt", 0,
            extra=[[InlineKeyboardButton("⏭ Пропустить", callback_data="optskip:1")]],
        ),
    )
    return PICK_OPTIONS


async def obtaining_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["obtaining"] = ctx.user_data["obtaining_types"][int(query.data.split(":")[1])]
    return await _ask_options(query, ctx)


async def obtaining_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(
        query, "📤 <b>Шаг 3 из 9.</b> Как покупатель получит товар?",
        _list_keyboard(ctx.user_data["obtaining_types"], "obt", int(query.data.split(":")[1])),
    )
    return PICK_OBTAINING


async def option_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    option = ctx.user_data["current_options"][int(query.data.split(":")[1])]
    if option.get("field"):
        ctx.user_data.setdefault("attributes", {})[option["field"]] = option.get("value")
        ctx.user_data.setdefault("attribute_labels", []).append(
            option.get("label") or option.get("value")
        )
    ctx.user_data["group_index"] = ctx.user_data.get("group_index", 0) + 1
    return await _ask_options(query, ctx)


async def option_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["group_index"] = ctx.user_data.get("group_index", 0) + 1
    return await _ask_options(query, ctx)


async def option_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    options = ctx.user_data["current_options"]
    await _edit(
        query, "⚙️ <b>Шаг 4 из 9.</b> Выберите значение:",
        _list_keyboard(
            [{"name": o.get("label") or o.get("value")} for o in options],
            "opt", int(query.data.split(":")[1]),
            extra=[[InlineKeyboardButton("⏭ Пропустить", callback_data="optskip:1")]],
        ),
    )
    return PICK_OPTIONS


# ── Шаги 5–7: название, описание, поля данных ─────────────────────────────────

async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("📝 <b>Шаг 6 из 9.</b> Введите описание товара:",
                                    parse_mode=ParseMode.HTML)
    return ENTER_DESCRIPTION


async def got_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["description"] = update.message.text.strip()

    try:
        fields = await api.fetch_data_fields(
            ctx.user_data["category"]["id"], ctx.user_data["obtaining"]["id"]
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Не смог получить поля товара:\n<code>{e}</code>", parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Поля OBTAINING_DATA заполняет покупатель — спрашиваем только ITEM_DATA.
    ctx.user_data["item_fields"] = [f for f in fields if f.get("type") == "ITEM_DATA"]
    ctx.user_data["field_index"] = 0
    ctx.user_data["data_field_values"] = []
    return await _ask_data_field(update, ctx)


async def _ask_data_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fields = ctx.user_data["item_fields"]
    index = ctx.user_data["field_index"]

    if index >= len(fields):
        await update.message.reply_text(
            "💵 <b>Шаг 8 из 9.</b> Введите цену в рублях:", parse_mode=ParseMode.HTML
        )
        return ENTER_PRICE

    field = fields[index]
    await update.message.reply_text(
        f"🔑 <b>Шаг 7 из 9.</b> {field.get('label', 'Значение')}:\n"
        "<i>это увидит покупатель после оплаты</i>",
        parse_mode=ParseMode.HTML,
    )
    return ENTER_DATA_FIELD


async def got_data_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data["item_fields"][ctx.user_data["field_index"]]
    ctx.user_data["data_field_values"].append(
        {"fieldId": field["id"], "value": update.message.text.strip()}
    )
    ctx.user_data["field_index"] += 1
    return await _ask_data_field(update, ctx)


# ── Шаг 8: цена, шаг 9: фото ──────────────────────────────────────────────────

async def got_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace(",", ".")
    try:
        price = int(float(raw))
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Нужно положительное число. Введите цену ещё раз:")
        return ENTER_PRICE

    ctx.user_data["price"] = price
    ctx.user_data["images"] = []
    await update.message.reply_text(
        "🖼 <b>Шаг 9 из 9.</b> Пришлите фотографии товара.\n"
        "Когда закончите — нажмите кнопку ниже.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Готово", callback_data="photos:done")],
             [InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")]]
        ),
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


# ── Подтверждение и запуск ────────────────────────────────────────────────────

def _draft_from(user_data: dict) -> ProductDraft:
    return ProductDraft(
        game=user_data["game"]["name"],
        category=user_data["category"]["name"],
        obtaining_type=user_data["obtaining"]["name"],
        name=user_data["name"],
        description=user_data["description"],
        price=user_data["price"],
        images=user_data.get("images", []),
        placement=user_data.get("placement", "free"),
        # Всё выбрано кнопками — искать по названиям не нужно.
        game_id=user_data["game"]["id"],
        category_id=user_data["category"]["id"],
        obtaining_type_id=user_data["obtaining"]["id"],
        attribute_values=user_data.get("attributes", {}),
        data_field_values=user_data.get("data_field_values", []),
        attributes=user_data.get("attribute_labels", []),
    )


def _summary(user_data: dict, draft: ProductDraft) -> str:
    attributes = ", ".join(user_data.get("attribute_labels", [])) or "—"
    return (
        "🧾 <b>Товар готов к созданию</b>\n\n"
        f"🎮 Игра: <b>{draft.game}</b>\n"
        f"🗂 Категория: <b>{draft.category}</b>\n"
        f"📤 Способ передачи: <b>{draft.obtaining_type}</b>\n"
        f"⚙️ Характеристики: {attributes}\n"
        f"📛 Название: <b>{draft.name}</b>\n"
        f"📝 Описание: {draft.description}\n"
        f"🔑 Полей заполнено: <b>{len(draft.data_field_values)}</b>\n"
        f"💵 Цена: <b>{draft.price} ₽</b>\n"
        f"🖼 Фотографий: <b>{len(draft.images)}</b>"
    )


async def photos_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = _draft_from(ctx.user_data)
    await _edit(
        query,
        _summary(ctx.user_data, draft) + "\n\nКак выставляем?",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Выставить бесплатно", callback_data="create:free")],
            [InlineKeyboardButton("📝 Сохранить черновиком", callback_data="create:later")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")],
        ]),
    )
    return CONFIRM


async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # На сайте по умолчанию предлагается платный «Премиум» — выбор явный.
    ctx.user_data["placement"] = query.data.split(":", 1)[1]
    draft = _draft_from(ctx.user_data)
    chat_id = query.message.chat_id
    via_api = config.CREATE_MODE != "browser"

    await _edit(query, "⚡ Создаю товар…" if via_api else "🖥 Прохожу мастер в браузере…")

    progress: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

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
                        await ctx.bot.send_photo(chat_id, img, caption=text,
                                                 parse_mode=ParseMode.HTML)
                else:
                    await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error("Не смог отправить прогресс шага %s: %s", step.number, e)

    reporter = asyncio.create_task(report())
    try:
        if via_api:
            await create_product_api(draft, progress.put_nowait)
        else:
            await asyncio.to_thread(
                create_product_browser, draft,
                lambda s: loop.call_soon_threadsafe(progress.put_nowait, s),
            )
        await progress.put(None)
        await reporter
        await ctx.bot.send_message(
            chat_id,
            "📝 <b>Товар сохранён черновиком.</b>\nВыставить можно в личном кабинете."
            if draft.placement == "later"
            else "🎉 <b>Товар создан и отправлен на публикацию.</b>",
            parse_mode=ParseMode.HTML,
        )
    except (CreationError, ApiCreationError) as e:
        await progress.put(None)
        await reporter
        hint = (
            "Можно повторить через браузер: <code>CREATE_MODE=browser</code> в .env"
            if via_api
            else f"Скриншоты шагов — в <code>{config.DEBUG_DIR}</code>"
        )
        await ctx.bot.send_message(
            chat_id,
            f"❌ <b>Создание остановилось.</b>\n<code>{e}</code>\n\n{hint}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await progress.put(None)
        await reporter
        logger.exception("Создание товара упало")
        await ctx.bot.send_message(chat_id, f"❌ Ошибка: <code>{e}</code>",
                                   parse_mode=ParseMode.HTML)

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await _edit(query, "↩️ Создание отменено.")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("↩️ Создание товара отменено.")
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
            SEARCH_GAME: [MessageHandler(text_only, game_search)],
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
            PICK_OPTIONS: [
                CallbackQueryHandler(option_chosen, pattern=r"^opt:\d+$"),
                CallbackQueryHandler(option_page, pattern=r"^optpage:\d+$"),
                CallbackQueryHandler(option_skip, pattern="^optskip:"),
                cancel_handler,
            ],
            ENTER_NAME: [MessageHandler(text_only, got_name)],
            ENTER_DESCRIPTION: [MessageHandler(text_only, got_description)],
            ENTER_DATA_FIELD: [MessageHandler(text_only, got_data_field)],
            ENTER_PRICE: [MessageHandler(text_only, got_price)],
            UPLOAD_PHOTOS: [
                CallbackQueryHandler(photos_done, pattern="^photos:done$"),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, got_image),
                cancel_handler,
            ],
            CONFIRM: [
                CallbackQueryHandler(confirm, pattern="^create:"),
                cancel_handler,
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
