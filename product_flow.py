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

import api_creator
import catalog
import config
import templates
from selenium_creator import CreationError, ProductDraft
from wizard_session import WizardSession

logger = logging.getLogger(__name__)

(
    PICK_MODE,
    PICK_TEMPLATE,
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
) = range(13)

# Скидка, с которой выставляются товары по умолчанию.
DEFAULT_DISCOUNT = int(os.getenv("PLAYEROK_DISCOUNT", "27"))

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
    """Спрашивает, создавать товар с нуля или копией сохранённого."""
    ctx.user_data.clear()
    saved = templates.all_templates()

    rows = [[InlineKeyboardButton("🆕 Создать товар", callback_data="mode:new")]]
    if saved:
        rows.append([InlineKeyboardButton(
            f"📋 Шаблонная копия ({len(saved)})", callback_data="mode:copy")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")])

    await update.message.reply_text(
        "🛠 <b>Создание товара</b>\n\n"
        "«Шаблонная копия» повторяет уже созданный товар, но с новым "
        "комментарием покупателю — чтобы объявление не считалось дублем.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return PICK_MODE


async def mode_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, "⏳ Открываю мастер Playerok… (первый запуск — до полуминуты)")
    return await _start_wizard(query, ctx)


async def mode_copy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    saved = templates.all_templates()
    if not saved:
        return await _fail(query, CreationError("Сохранённых шаблонов пока нет"))

    ctx.user_data["templates"] = saved
    await _edit(
        query,
        "📋 <b>Выберите товар для копии.</b>\n"
        "Комментарий покупателю бот заменит на новый.",
        _keyboard([{"name": f"{t['name'][:40]} · {t['price']} ₽"} for t in saved],
                  "tpl", 0),
    )
    return PICK_TEMPLATE


async def _start_wizard(query, ctx: ContextTypes.DEFAULT_TYPE):
    """Открывает мастер и показывает список игр."""
    session = WizardSession.get()
    try:
        games = await _run(session.games)
    except Exception as e:
        logger.exception("Мастер не открылся")
        return await _fail(query, e)

    if not games:
        return await _fail(query, CreationError("Мастер открылся, но список игр пуст"))

    ctx.user_data["games"] = games
    await _edit(
        query, "🎮 <b>Шаг 1 из 9.</b> Выберите игру или приложение:",
        _keyboard(
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


# ── Копия по шаблону ──────────────────────────────────────────────────────────

def _index_of(items: list[dict], wanted: str) -> int:
    """Позиция пункта с таким названием: сначала точное, потом частичное."""
    target = " ".join(wanted.split()).casefold()
    names = [" ".join(i["name"].split()).casefold() for i in items]
    if target in names:
        return names.index(target)
    for i, name in enumerate(names):
        if target in name or name in target:
            return i
    return -1


async def _copy_via_api(query, ctx, template: dict, fields: dict) -> bool:
    """
    Создаёт копию запросами. Идентификаторы берём из каталога — он
    наполняется сам, пока бот ходит по мастеру. Нет данных в каталоге —
    возвращаем False, и копия пойдёт через браузер.
    """
    ids = catalog.resolve(template["game"], template["category"],
                          template.get("obtaining", ""))
    if not ids.get("category_id") or not ids.get("obtaining_id"):
        logger.info("Каталог не знает связку — копирую через браузер")
        return False

    category_id, obtaining_id = ids["category_id"], ids["obtaining_id"]

    # Характеристики: подписи из шаблона переводим в {field: value}.
    known = catalog.options(category_id)
    attribute_values = {}
    for wanted in template.get("attributes", []):
        option = next(
            (o for o in known
             if wanted.casefold() in f"{o.get('label')} {o.get('value')}".casefold()),
            None,
        )
        if not option or not option.get("field"):
            logger.info("Характеристика «%s» не в каталоге — иду браузером", wanted)
            return False
        attribute_values[option["field"]] = option.get("value")

    # Поля товара: подписи → fieldId.
    known_fields = [f for f in catalog.data_fields(category_id, obtaining_id)
                    if f.get("type") == "ITEM_DATA"]
    data_field_values = []
    for label, value in fields.items():
        found = next(
            (f for f in known_fields
             if (f.get("label") or "").casefold() == label.casefold()),
            None,
        )
        if not found:
            logger.info("Поле «%s» не в каталоге — иду браузером", label)
            return False
        data_field_values.append({"fieldId": found["id"], "value": value})

    draft = ProductDraft(
        game=template["game"],
        category=template["category"],
        obtaining_type=template.get("obtaining", ""),
        name=template["name"],
        description=template["description"],
        price=template["price"],
        images=template.get("images", []),
        placement="later",  # окончательный выбор пользователь сделает кнопкой
        game_id=ids.get("game_id", ""),
        category_id=category_id,
        obtaining_type_id=obtaining_id,
        attribute_values=attribute_values,
        data_field_values=data_field_values,
    )

    await _edit(query, "⚡ Создаю копию запросами…")
    try:
        item = await api_creator.create_product(draft)
    except Exception as e:
        logger.warning("Запросами не вышло (%s) — иду браузером", e)
        return False

    ctx.user_data["api_item"] = item
    await _edit(
        query,
        _summary(ctx.user_data)
        + "\n\n<i>Комментарий заменён на новый. Товар создан запросами "
          "и лежит в черновиках.</i>\n\nВыставляем?",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Выставить бесплатно", callback_data="place:free")],
            [InlineKeyboardButton("📝 Оставить черновиком", callback_data="place:later")],
        ]),
    )
    return True


async def template_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Повторяет сохранённый товар: те же данные, новый комментарий."""
    query = update.callback_query
    await query.answer()
    template = ctx.user_data["templates"][int(query.data.split(":")[1])]

    fields = templates.vary_data_fields(template.get("data_fields", {}))
    ctx.user_data.update({
        "game": template["game"],
        "category": template["category"],
        "obtaining": template.get("obtaining", ""),
        "attributes": template.get("attributes", []),
        "name": template["name"],
        "description": template["description"],
        "price": template["price"],
        "images": template.get("images", []),
        "data_fields": fields,
        "discount": template.get("discount", DEFAULT_DISCOUNT),
        "from_template": True,
    })

    # Быстрый путь: если каталог знает эту связку — создаём запросами.
    if await _copy_via_api(query, ctx, template, fields):
        return CONFIRM

    session = WizardSession.get()
    try:
        await _edit(query, f"📋 Копирую «{template['name'][:40]}»…\n⏳ Открываю мастер…")
        games = await _run(session.games, template["game"])

        index = _index_of(games, template["game"])
        if index < 0:
            raise CreationError(f"Игра «{template['game']}» не нашлась в мастере")
        await _edit(query, f"🎮 {template['game']}\n⏳ Категория…")
        categories = await _run(session.pick_game, index)

        index = _index_of(categories, template["category"])
        if index < 0:
            raise CreationError(f"Категория «{template['category']}» не нашлась")
        await _edit(query, f"🗂 {template['category']}\n⏳ Способ передачи…")
        obtaining = await _run(session.pick_category, index)

        attributes = []
        if obtaining and template.get("obtaining"):
            index = _index_of(obtaining, template["obtaining"])
            if index < 0:
                raise CreationError(f"Способ «{template['obtaining']}» не нашёлся")
            await _edit(query, f"📤 {template['obtaining']}\n⏳ Характеристики…")
            attributes = await _run(session.pick_obtaining, index)

        for wanted in template.get("attributes", []):
            index = _index_of(attributes, wanted)
            if index < 0:
                raise CreationError(f"Характеристика «{wanted}» не нашлась")
            await _run(session.pick_attribute, index)

        if not template.get("images"):
            raise CreationError("В шаблоне нет фотографий — создайте товар заново")

        await _edit(query, "🖼 Загружаю фотографии…")
        await _run(session.upload_images, template["images"])

        await _edit(query, "📝 Заполняю карточку…")
        await _run(session.fill_about, template["name"], template["description"])

        await _edit(query, f"💵 Цена {template['price']} ₽…")
        labels = await _run(session.fill_price, template["price"],
                            ctx.user_data["discount"])

        # Подписи полей берём с экрана, значения — из шаблона с новым
        # комментарием; чего нет в шаблоне, заполняем прежним значением.
        values = {}
        for label in labels:
            match = next((v for k, v in fields.items() if k.casefold() == label.casefold()), "")
            values[label] = match or templates.vary_comment()
        ctx.user_data["data_fields"] = values

        await _edit(query, "🔑 Заполняю данные товара…")
        await _run(session.fill_data_fields, values)
    except Exception as e:
        return await _fail(query, e)

    await _edit(
        query,
        _summary(ctx.user_data) + "\n\n<i>Комментарий заменён на новый.</i>\n\nКак выставляем?",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Выставить бесплатно", callback_data="place:free")],
            [InlineKeyboardButton("📝 Сохранить черновиком", callback_data="place:later")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel:1")],
        ]),
    )
    return CONFIRM


async def template_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    saved = ctx.user_data["templates"]
    await _edit(
        query, "📋 <b>Выберите товар для копии:</b>",
        _keyboard([{"name": f"{t['name'][:40]} · {t['price']} ₽"} for t in saved],
                  "tpl", int(query.data.split(":")[1])),
    )
    return PICK_TEMPLATE


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

    ctx.user_data["discount"] = DEFAULT_DISCOUNT
    try:
        labels = await _run(WizardSession.get().fill_price, price, DEFAULT_DISCOUNT)
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
        f"💵 <b>{data['price']} ₽</b>"
        + (f" (скидка {data['discount']}%)" if data.get("discount") else "") + "\n"
        f"🖼 Фото: {len(data.get('images', []))}\n"
        f"🔑 {fields}"
    )


async def placement_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    placement = query.data.split(":", 1)[1]

    await _edit(query, "⏳ Завершаю…")

    item = ctx.user_data.get("api_item")
    if item:
        # Товар уже создан запросами — публикуем так же.
        try:
            if placement == "later":
                detail = "Оставлен черновиком"
            else:
                published = await api_creator.publish_free(item)
                detail = f"Опубликован, статус: {published.get('status', '—')}"
        except Exception as e:
            return await _fail(query, e)

        templates.save(ctx.user_data)
        ctx.user_data.clear()
        await query.message.reply_text(
            ("📝 <b>Товар сохранён черновиком.</b>" if placement == "later"
             else "🎉 <b>Товар выставлен на продажу.</b>") + f"\n\n<i>{detail}</i>",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    session = WizardSession.get()
    try:
        detail = await _run(session.publish, placement)
    except Exception as e:
        return await _fail(query, e)

    # Товар получился — запоминаем его как шаблон для будущих копий.
    try:
        templates.save(ctx.user_data)
    except Exception as e:
        logger.warning("Не смог сохранить шаблон: %s", e)

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
            PICK_MODE: [
                CallbackQueryHandler(mode_new, pattern="^mode:new$"),
                CallbackQueryHandler(mode_copy, pattern="^mode:copy$"),
                cancel_handler,
            ],
            PICK_TEMPLATE: [
                CallbackQueryHandler(template_chosen, pattern=r"^tpl:\d+$"),
                CallbackQueryHandler(template_page, pattern=r"^tplpage:\d+$"),
                cancel_handler,
            ],
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
