"""
Диалог /delete — удаление объявления.

Всё в две кнопки: бот показывает свои объявления списком, пользователь жмёт
нужное и подтверждает. Удаление необратимо, поэтому подтверждение спрашивается
всегда, а в тексте видно, что именно удаляется.
"""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

import playerok_client as api

logger = logging.getLogger(__name__)

PICK_ITEM, CONFIRM_DELETE = range(2)

PAGE_SIZE = 8

# Подписи статусов: в ответе они английские, а человеку нужно понятное слово.
STATUS_LABELS = {
    "APPROVED": "на продаже",
    "APPROVING": "на модерации",
    "PENDING_APPROVAL": "ждёт модерации",
    "DRAFT": "черновик",
    "EXPIRED": "истёк",
    "SOLD": "продан",
}


def _label(item: dict) -> str:
    """Строка кнопки: название, цена и статус — чтобы не удалить не то."""
    name = (item.get("name") or "без названия").strip()
    if len(name) > 30:
        name = name[:29] + "…"
    status = STATUS_LABELS.get(item.get("status"), item.get("status") or "")
    return f"{name} · {item.get('price', '?')} ₽ · {status}"


def _keyboard(items: list[dict], page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    rows = [
        [InlineKeyboardButton(_label(item), callback_data=f"del:pick:{start + i}")]
        for i, item in enumerate(items[start:start + PAGE_SIZE])
    ]

    nav = []
    if page:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"del:page:{page - 1}"))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton("Ещё ➡️", callback_data=f"del:page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="del:cancel")])
    return InlineKeyboardMarkup(rows)


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = await update.message.reply_text("⏳ Смотрю ваши объявления…")

    try:
        items = await api.fetch_my_items()
    except Exception as e:
        logger.exception("Не смог получить объявления")
        await message.edit_text(f"❌ <code>{e}</code>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    if not items:
        await message.edit_text("📭 Объявлений нет — удалять нечего.")
        return ConversationHandler.END

    ctx.user_data["del_items"] = items
    await message.edit_text(
        f"🗑 <b>Удаление объявления</b>\n\nВсего: {len(items)}. Выберите, что удалить:",
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(items, 0),
    )
    return PICK_ITEM


async def turn_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[2])
    items = ctx.user_data.get("del_items", [])
    await query.edit_message_reply_markup(reply_markup=_keyboard(items, page))
    return PICK_ITEM


async def item_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = ctx.user_data.get("del_items", [])
    index = int(query.data.split(":")[2])
    if index >= len(items):  # список устарел, например после /delete в другом окне
        await query.edit_message_text("🤷 Этого объявления больше нет. Наберите /delete снова.")
        return ConversationHandler.END

    item = items[index]
    ctx.user_data["del_chosen"] = item

    await query.edit_message_text(
        "🗑 <b>Удалить объявление?</b>\n\n"
        f"📛 {item.get('name')}\n"
        f"💵 {item.get('price')} ₽\n"
        f"📍 {STATUS_LABELS.get(item.get('status'), item.get('status'))}\n\n"
        "<i>Удаление необратимо — вернуть объявление будет нельзя.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Да, удалить", callback_data="del:yes")],
            [InlineKeyboardButton("↩️ Нет, оставить", callback_data="del:cancel")],
        ]),
    )
    return CONFIRM_DELETE


async def confirmed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    item = ctx.user_data.get("del_chosen") or {}
    await query.edit_message_text("⏳ Удаляю…")

    try:
        await api.remove_item(item["id"])
    except Exception as e:
        logger.exception("Не смог удалить объявление")
        await query.edit_message_text(
            f"❌ <code>{e}</code>\n\nОбъявление осталось на месте.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    ctx.user_data.pop("del_items", None)
    ctx.user_data.pop("del_chosen", None)
    await query.edit_message_text(
        f"✅ <b>Удалено:</b> {item.get('name')}", parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("del_items", None)
    ctx.user_data.pop("del_chosen", None)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("↩️ Ничего не удалено.")
    else:
        await update.message.reply_text("↩️ Ничего не удалено.")
    return ConversationHandler.END


def build_delete_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("delete", cmd_delete)],
        states={
            PICK_ITEM: [
                CallbackQueryHandler(turn_page, pattern=r"^del:page:"),
                CallbackQueryHandler(item_chosen, pattern=r"^del:pick:"),
                CallbackQueryHandler(cancel, pattern=r"^del:cancel$"),
            ],
            CONFIRM_DELETE: [
                CallbackQueryHandler(confirmed, pattern=r"^del:yes$"),
                CallbackQueryHandler(cancel, pattern=r"^del:cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("delete", cmd_delete)],
        allow_reentry=True,
        conversation_timeout=300,
    )
