import logging
from telegram import Bot
from telegram.constants import ParseMode
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, PLAYEROK_BASE_URL

logger = logging.getLogger(__name__)
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


async def send(text: str):
    try:
        await get_bot().send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Telegram send error: %s", e)


def _status_emoji(status: str) -> str:
    mapping = {
        "PAID": "💰",
        "COMPLETED": "✅",
        "PENDING": "⏳",
        "CANCELLED": "❌",
        "IN_PROGRESS": "🔄",
        "DISPUTE": "⚠️",
    }
    return mapping.get(status.upper(), "📦")


def format_order(order: dict) -> str:
    item = order.get("item") or {}
    buyer = order.get("buyer") or {}
    status = order.get("status", "")
    emoji = _status_emoji(status)

    price_block = item.get("price") or {}
    currency = (price_block.get("currency") or {}).get("symbol", "₽")
    price = price_block.get("value", "?")

    item_name = item.get("name") or item.get("title", "Без названия")
    item_slug = item.get("slug", "")
    buyer_name = buyer.get("username") or buyer.get("login", "Неизвестен")
    order_id = order.get("id", "?")
    created_at = order.get("createdAt", "")[:19].replace("T", " ") if order.get("createdAt") else "?"

    item_url = f"{PLAYEROK_BASE_URL}/lots/{item_slug}" if item_slug else PLAYEROK_BASE_URL

    lines = [
        f"{emoji} <b>Новая покупка!</b>",
        f"",
        f"📦 Товар: <a href='{item_url}'>{item_name}</a>",
        f"💵 Сумма: <b>{price} {currency}</b>",
        f"👤 Покупатель: <b>{buyer_name}</b>",
        f"🔖 Статус: <b>{status}</b>",
        f"🕐 Дата: {created_at}",
        f"🆔 ID: <code>{order_id}</code>",
    ]
    return "\n".join(lines)


def format_complaint(complaint: dict) -> str:
    deal = complaint.get("deal") or {}
    item = deal.get("item") or {}
    buyer = deal.get("buyer") or {}

    complaint_id = complaint.get("id", "?")
    status = complaint.get("status", "")
    reason = complaint.get("reason", "Не указана")
    created_at = complaint.get("createdAt", "")[:19].replace("T", " ") if complaint.get("createdAt") else "?"
    deal_id = deal.get("id", "?")
    item_name = item.get("name") or item.get("title", "Без названия")
    buyer_name = buyer.get("username") or buyer.get("login", "Неизвестен")

    lines = [
        f"⚠️ <b>Новая жалоба!</b>",
        f"",
        f"📦 Товар: <b>{item_name}</b>",
        f"👤 Покупатель: <b>{buyer_name}</b>",
        f"📝 Причина: <b>{reason}</b>",
        f"🔖 Статус жалобы: <b>{status}</b>",
        f"🕐 Дата: {created_at}",
        f"🆔 ID жалобы: <code>{complaint_id}</code>",
        f"🔗 ID сделки: <code>{deal_id}</code>",
        f"",
        f"👉 <a href='{PLAYEROK_BASE_URL}/deals/{deal_id}'>Открыть сделку</a>",
    ]
    return "\n".join(lines)
