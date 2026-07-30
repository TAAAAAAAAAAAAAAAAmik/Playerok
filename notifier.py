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
    """Сделка из запроса `deals`: цена в item.price числом, покупатель в user."""
    item = order.get("item") or {}
    buyer = order.get("user") or {}
    status = order.get("status", "")
    emoji = _status_emoji(status)

    price = item.get("price", "?")
    currency = "₽"

    item_name = item.get("name", "Без названия")
    item_slug = item.get("slug", "")
    buyer_name = buyer.get("username", "Неизвестен")
    order_id = order.get("id", "?")
    created_at = order.get("createdAt", "")[:19].replace("T", " ") if order.get("createdAt") else "?"

    item_url = f"{PLAYEROK_BASE_URL}/products/{item_slug}" if item_slug else PLAYEROK_BASE_URL

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


def format_complaint(deal: dict) -> str:
    """
    Сделка с поднятым флагом hasProblem. Отдельной сущности «жалоба» в API
    Playerok нет — покупатель сообщает о проблеме по конкретной сделке.
    """
    item = deal.get("item") or {}
    buyer = deal.get("user") or {}

    deal_id = deal.get("id", "?")
    status = deal.get("status", "")
    description = deal.get("statusDescription") or "Покупатель сообщил о проблеме"
    created_at = deal.get("createdAt", "")[:19].replace("T", " ") if deal.get("createdAt") else "?"
    item_name = item.get("name", "Без названия")
    item_slug = item.get("slug", "")
    buyer_name = buyer.get("username", "Неизвестен")

    item_url = f"{PLAYEROK_BASE_URL}/products/{item_slug}" if item_slug else PLAYEROK_BASE_URL

    lines = [
        f"⚠️ <b>Проблема по сделке!</b>",
        f"",
        f"📦 Товар: <a href='{item_url}'>{item_name}</a>",
        f"👤 Покупатель: <b>{buyer_name}</b>",
        f"📝 Что не так: {description}",
        f"🔖 Статус сделки: <b>{status}</b>",
        f"🕐 Дата: {created_at}",
        f"🆔 ID сделки: <code>{deal_id}</code>",
    ]
    return "\n".join(lines)
