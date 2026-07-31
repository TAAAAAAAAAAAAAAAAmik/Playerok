"""The /newitem wizard: build a Playerok listing step by step in Telegram.

create_item needs a chain of ids that can only be discovered by walking the
catalogue — game, category, obtaining type — plus whatever options and data
fields that category defines. So the flow is a conversation that resolves each
step before asking the next.

Creating leaves the item in drafts. Publishing is deliberately a separate,
confirmed step because a priority status can cost money.
"""
import logging

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

import playerok_client
from notifier import esc

logger = logging.getLogger(__name__)

(
    GAME_QUERY,
    GAME_PICK,
    CATEGORY_PICK,
    OBTAINING_PICK,
    NAME,
    PRICE,
    DESCRIPTION,
    OPTION_PICK,
    DATA_FIELD,
    PHOTOS,
    PUBLISH_PICK,
) = range(11)

# Telegram rejects callback_data over 64 bytes, so payloads carry a list index
# rather than a Playerok id.
CANCEL_HINT = "\n\n/cancel — отменить создание"


def _keyboard(labels: list[str], prefix: str, per_row: int = 1) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i, label in enumerate(labels):
        row.append(InlineKeyboardButton(label[:60], callback_data=f"{prefix}:{i}"))
        if len(row) == per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def _draft(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    return ctx.user_data.setdefault("new_item", {})


async def _fail(update: Update, ctx: ContextTypes.DEFAULT_TYPE, e: Exception, where: str):
    logger.error("Ошибка на шаге %s: %s", where, e)
    playerok_client.reset()
    target = update.effective_chat
    await target.send_message(
        f"❌ Ошибка на шаге «{esc(where)}»:\n<code>{esc(e)}</code>\n\n"
        f"Создание отменено, черновик не создан.",
        parse_mode=ParseMode.HTML,
    )
    ctx.user_data.pop("new_item", None)
    return ConversationHandler.END


# ── Step 1: game ──────────────────────────────────────────────────────────────

async def cmd_newitem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("new_item", None)
    await update.message.reply_text(
        "🎮 <b>Новое объявление</b>\n\nНапишите название игры или приложения:" + CANCEL_HINT,
        parse_mode=ParseMode.HTML,
    )
    return GAME_QUERY


async def got_game_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    status = await update.message.reply_text("🔍 Ищу...")

    try:
        games = await playerok_client.search_games(query)
    except Exception as e:
        return await _fail(update, ctx, e, "поиск игры")

    if not games:
        await status.edit_text("Ничего не нашёл. Попробуйте другое название:" + CANCEL_HINT)
        return GAME_QUERY

    _draft(ctx)["games"] = games
    await status.edit_text(
        "Выберите игру:",
        reply_markup=_keyboard([g.name for g in games], "game"),
    )
    return GAME_PICK


async def picked_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    draft = _draft(ctx)
    game = draft["games"][int(q.data.split(":")[1])]

    await q.edit_message_text(f"🎮 Игра: <b>{esc(game.name)}</b>\n\n⏳ Загружаю категории...",
                              parse_mode=ParseMode.HTML)
    try:
        full = await playerok_client.get_game(game.id)
    except Exception as e:
        return await _fail(update, ctx, e, "загрузка категорий")

    categories = list(getattr(full, "categories", None) or [])
    if not categories:
        await q.edit_message_text(
            f"У «{esc(game.name)}» нет доступных категорий. Попробуйте другую игру:"
            + CANCEL_HINT,
            parse_mode=ParseMode.HTML,
        )
        return GAME_QUERY

    draft.update({"game": game, "categories": categories})
    await q.edit_message_text(
        f"🎮 Игра: <b>{esc(game.name)}</b>\n\nВыберите категорию:",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard([c.name for c in categories], "cat"),
    )
    return CATEGORY_PICK


# ── Step 2: category and obtaining type ───────────────────────────────────────

async def picked_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    draft = _draft(ctx)
    category = draft["categories"][int(q.data.split(":")[1])]
    draft["category"] = category

    await q.edit_message_text("⏳ Загружаю способы получения...")
    try:
        types_ = await playerok_client.get_obtaining_types(category.id)
    except Exception as e:
        return await _fail(update, ctx, e, "способы получения")

    if not types_:
        return await _fail(
            update, ctx,
            RuntimeError("категория не предлагает ни одного способа получения"),
            "способы получения",
        )

    if len(types_) == 1:
        draft["obtaining"] = types_[0]
        return await _ask_name(q.message.chat, ctx)

    draft["obtaining_types"] = types_
    await q.edit_message_text(
        f"📦 Категория: <b>{esc(category.name)}</b>\n\nКак покупатель получит товар?",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard([t.name for t in types_], "obt"),
    )
    return OBTAINING_PICK


async def picked_obtaining(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    draft = _draft(ctx)
    draft["obtaining"] = draft["obtaining_types"][int(q.data.split(":")[1])]
    await q.edit_message_text(f"✅ Способ: <b>{esc(draft['obtaining'].name)}</b>",
                              parse_mode=ParseMode.HTML)
    return await _ask_name(q.message.chat, ctx)


# ── Step 3: text fields ───────────────────────────────────────────────────────

async def _ask_name(chat, ctx: ContextTypes.DEFAULT_TYPE):
    await chat.send_message("✏️ Название объявления:" + CANCEL_HINT)
    return NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("Слишком коротко. Ещё раз:")
        return NAME
    _draft(ctx)["name"] = name
    await update.message.reply_text("💵 Цена в рублях (только число):")
    return PRICE


async def got_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await update.message.reply_text("Нужно целое число больше нуля. Ещё раз:")
        return PRICE
    _draft(ctx)["price"] = int(raw)
    await update.message.reply_text("📝 Описание товара:")
    return DESCRIPTION


async def got_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _draft(ctx)["description"] = update.message.text.strip()
    return await _next_option(update.effective_chat, ctx)


# ── Step 4: category options ──────────────────────────────────────────────────

def _option_groups(category) -> list:
    """Group the category's options by their `group` field.

    Options arrive as a flat list where several entries can share a group — that
    is how a selector's alternatives are expressed — so they are grouped here
    and offered one group at a time.
    """
    grouped: dict[str, list] = {}
    for option in getattr(category, "options", None) or []:
        grouped.setdefault(option.group or option.field or option.id, []).append(option)
    return list(grouped.items())


async def _next_option(chat, ctx: ContextTypes.DEFAULT_TYPE):
    draft = _draft(ctx)
    groups = draft.setdefault("option_groups", _option_groups(draft["category"]))
    index = draft.setdefault("option_index", 0)
    draft.setdefault("chosen_options", [])

    if index >= len(groups):
        return await _next_data_field(chat, ctx)

    group_name, options = groups[index]
    if len(options) == 1:
        # Nothing to choose — carry the single option through as-is.
        draft["chosen_options"].append(options[0])
        draft["option_index"] = index + 1
        return await _next_option(chat, ctx)

    labels = [o.label or o.value or "—" for o in options]
    await chat.send_message(
        f"⚙️ <b>{esc(group_name)}</b> — выберите вариант:",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(labels, "opt"),
    )
    return OPTION_PICK


async def picked_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    draft = _draft(ctx)
    _, options = draft["option_groups"][draft["option_index"]]
    chosen = options[int(q.data.split(":")[1])]
    draft["chosen_options"].append(chosen)
    draft["option_index"] += 1
    await q.edit_message_text(f"⚙️ {esc(chosen.label or chosen.value)}",
                             parse_mode=ParseMode.HTML)
    return await _next_option(q.message.chat, ctx)


# ── Step 5: data fields ───────────────────────────────────────────────────────

async def _next_data_field(chat, ctx: ContextTypes.DEFAULT_TYPE):
    draft = _draft(ctx)

    if "data_fields" not in draft:
        try:
            draft["data_fields"] = await playerok_client.get_item_data_fields(
                draft["category"].id, draft["obtaining"].id
            )
        except Exception as e:
            logger.error("Не удалось получить поля категории: %s", e)
            draft["data_fields"] = []
        draft["field_index"] = 0
        draft["filled_fields"] = []

    fields = draft["data_fields"]
    index = draft["field_index"]

    if index >= len(fields):
        return await _ask_photos(chat, ctx)

    field = fields[index]
    required = "обязательное" if getattr(field, "required", False) else "можно пропустить, отправив «-»"
    await chat.send_message(
        f"🧾 <b>{esc(field.label)}</b>\n<i>{esc(required)}</i>",
        parse_mode=ParseMode.HTML,
    )
    return DATA_FIELD


async def got_data_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    draft = _draft(ctx)
    field = draft["data_fields"][draft["field_index"]]
    value = update.message.text.strip()

    required = bool(getattr(field, "required", False))
    if value == "-":
        # The skip marker must never reach Playerok as a literal value.
        if required:
            await update.message.reply_text("Это поле обязательное — «-» не подойдёт. Введите значение:")
            return DATA_FIELD
        value = ""
    elif not value and required:
        await update.message.reply_text("Это поле обязательное. Введите значение:")
        return DATA_FIELD

    if value:
        field.value = value
        draft["filled_fields"].append(field)

    draft["field_index"] += 1
    return await _next_data_field(update.effective_chat, ctx)


# ── Step 6: photos ────────────────────────────────────────────────────────────

async def _ask_photos(chat, ctx: ContextTypes.DEFAULT_TYPE):
    _draft(ctx).setdefault("photos", [])
    await chat.send_message(
        "🖼 Пришлите фото товара — можно несколько.\n"
        "Когда закончите, отправьте /done. Без фото — тоже /done."
    )
    return PHOTOS


async def got_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # largest available size
    try:
        file = await photo.get_file()
        data = bytes(await file.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"Не смог скачать фото: <code>{esc(e)}</code>",
                                        parse_mode=ParseMode.HTML)
        return PHOTOS

    photos = _draft(ctx).setdefault("photos", [])
    photos.append(data)
    await update.message.reply_text(f"📎 Фото добавлено ({len(photos)}). Ещё или /done.")
    return PHOTOS


async def done_photos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    draft = _draft(ctx)
    status = await update.message.reply_text("⏳ Создаю черновик...")

    try:
        item = await playerok_client.create_item(
            category_id=draft["category"].id,
            obtaining_type_id=draft["obtaining"].id,
            name=draft["name"],
            price=draft["price"],
            description=draft["description"],
            options=draft.get("chosen_options", []),
            data_fields=draft.get("filled_fields", []),
            attachments=draft.get("photos", []),
        )
    except Exception as e:
        return await _fail(update, ctx, e, "создание черновика")

    draft["item"] = item
    summary = (
        f"✅ <b>Черновик создан</b>\n\n"
        f"📦 {esc(draft['name'])}\n"
        f"💵 {draft['price']} ₽\n"
        f"🎮 {esc(draft['game'].name)} → {esc(draft['category'].name)}\n"
        f"🆔 <code>{esc(item.id)}</code>\n\n"
        f"⏳ Загружаю варианты публикации..."
    )
    await status.edit_text(summary, parse_mode=ParseMode.HTML)

    try:
        statuses = await playerok_client.get_priority_statuses(item.id, draft["price"])
    except Exception as e:
        logger.error("Не получил статусы приоритета: %s", e)
        await update.effective_chat.send_message(
            f"⚠️ Черновик создан, но варианты публикации не загрузились:\n"
            f"<code>{esc(e)}</code>\n\nОпубликуйте его на сайте вручную.",
            parse_mode=ParseMode.HTML,
        )
        ctx.user_data.pop("new_item", None)
        return ConversationHandler.END

    if not statuses:
        await update.effective_chat.send_message(
            "⚠️ Playerok не предложил вариантов публикации. "
            "Черновик сохранён — опубликуйте его на сайте."
        )
        ctx.user_data.pop("new_item", None)
        return ConversationHandler.END

    draft["priority_statuses"] = statuses
    labels = [
        f"{s.name} — {'бесплатно' if not s.price else f'{s.price} ₽'}"
        for s in statuses
    ]
    await update.effective_chat.send_message(
        "🚀 <b>Публикация</b>\n\n"
        "Выберите вариант. Платный списывается с баланса Playerok.\n"
        "Можно ничего не выбирать — /cancel оставит объявление в черновиках.",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(labels, "prio"),
    )
    return PUBLISH_PICK


# ── Step 7: publish ───────────────────────────────────────────────────────────

async def picked_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    draft = _draft(ctx)
    status = draft["priority_statuses"][int(q.data.split(":")[1])]

    cost = "бесплатно" if not status.price else f"{status.price} ₽ с баланса"
    await q.edit_message_text(f"⏳ Публикую ({esc(cost)})...", parse_mode=ParseMode.HTML)

    try:
        item = await playerok_client.publish_item(draft["item"].id, status.id)
    except Exception as e:
        await q.message.chat.send_message(
            f"❌ Опубликовать не удалось:\n<code>{esc(e)}</code>\n\n"
            f"Черновик сохранён, объявление не выставлено и деньги не списаны.",
            parse_mode=ParseMode.HTML,
        )
        ctx.user_data.pop("new_item", None)
        return ConversationHandler.END

    ctx.user_data.pop("new_item", None)
    await q.message.chat.send_message(
        f"🎉 <b>Объявление опубликовано!</b>\n\n"
        f"📦 {esc(getattr(item, 'name', draft['name']))}\n"
        f"💵 {getattr(item, 'price', draft['price'])} ₽\n"
        f"🔖 Статус: <b>{esc(getattr(getattr(item, 'status', None), 'name', '—'))}</b>\n"
        f"🆔 <code>{esc(getattr(item, 'id', '—'))}</code>",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    draft = ctx.user_data.pop("new_item", None)
    if draft and draft.get("item"):
        await update.message.reply_text(
            "↩️ Отменено. Объявление осталось в черновиках на Playerok."
        )
    else:
        await update.message.reply_text("↩️ Создание отменено.")
    return ConversationHandler.END


def build_handler(owner_check) -> ConversationHandler:
    """Wire the wizard up. `owner_check` gates entry to the configured chat."""

    async def entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not owner_check(update):
            await update.message.reply_text("⛔ Этот бот приватный.")
            return ConversationHandler.END
        return await cmd_newitem(update, ctx)

    text = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("newitem", entry)],
        states={
            GAME_QUERY:     [MessageHandler(text, got_game_query)],
            GAME_PICK:      [CallbackQueryHandler(picked_game, pattern=r"^game:\d+$")],
            CATEGORY_PICK:  [CallbackQueryHandler(picked_category, pattern=r"^cat:\d+$")],
            OBTAINING_PICK: [CallbackQueryHandler(picked_obtaining, pattern=r"^obt:\d+$")],
            NAME:           [MessageHandler(text, got_name)],
            PRICE:          [MessageHandler(text, got_price)],
            DESCRIPTION:    [MessageHandler(text, got_description)],
            OPTION_PICK:    [CallbackQueryHandler(picked_option, pattern=r"^opt:\d+$")],
            DATA_FIELD:     [MessageHandler(text, got_data_field)],
            PHOTOS: [
                MessageHandler(filters.PHOTO, got_photo),
                CommandHandler("done", done_photos),
            ],
            PUBLISH_PICK:   [CallbackQueryHandler(picked_priority, pattern=r"^prio:\d+$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
