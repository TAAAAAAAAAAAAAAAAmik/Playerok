import html
import logging

from telegram import Bot
from telegram.constants import ParseMode

import config

logger = logging.getLogger(__name__)
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    return _bot


async def send(text: str):
    try:
        await get_bot().send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Telegram send error: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(value) -> str:
    """Escape for HTML parse mode — item names and usernames are user input."""
    return html.escape(str(value if value is not None else ""))


def _name(enum_value) -> str:
    """PlayerokAPI returns enum members; fall back to str for plain values."""
    return getattr(enum_value, "name", None) or str(enum_value or "")


_STATUS_EMOJI = {
    "PENDING": "⏳",
    "PAID": "💰",
    "SENT": "📤",
    "CONFIRMED": "✅",
    "CONFIRMED_AUTOMATICALLY": "✅",
    "ROLLED_BACK": "↩️",
}

_STATUS_RU = {
    "PENDING": "ожидает оплаты",
    "PAID": "оплачена",
    "SENT": "товар отправлен",
    "CONFIRMED": "подтверждена",
    "CONFIRMED_AUTOMATICALLY": "подтверждена автоматически",
    "ROLLED_BACK": "отменена",
}


def _when(value) -> str:
    return str(value)[:19].replace("T", " ") if value else "—"


def _item_url(item) -> str:
    slug = getattr(item, "slug", "") or ""
    if not slug:
        return config.PLAYEROK_BASE_URL
    return config.ITEM_URL_TEMPLATE.format(slug=slug)


def is_sale(deal, account_id) -> bool:
    """True when we are the seller.

    `direction` (IN/OUT) is undocumented in PlayerokAPI, so the side is derived
    from the item's owner instead, which is unambiguous.
    """
    owner = getattr(getattr(deal, "item", None), "user", None)
    return bool(owner and account_id and owner.id == account_id)


# ── Message formatting ────────────────────────────────────────────────────────

def format_deal(deal, account_id) -> str:
    item = getattr(deal, "item", None)
    counterparty = getattr(deal, "user", None)

    status = _name(getattr(deal, "status", ""))
    emoji = _STATUS_EMOJI.get(status, "📦")
    status_ru = _STATUS_RU.get(status, status or "неизвестен")

    sale = is_sale(deal, account_id)
    title = "Новая продажа!" if sale else "Новая покупка!"
    side_label = "Покупатель" if sale else "Продавец"

    item_name = esc(getattr(item, "name", None) or "Без названия")
    price = getattr(item, "price", None)
    price_line = f"{price} ₽" if price is not None else "—"

    lines = [
        f"{emoji} <b>{title}</b>",
        "",
        f"📦 Товар: <a href='{esc(_item_url(item))}'>{item_name}</a>",
        f"💵 Сумма: <b>{esc(price_line)}</b>",
        f"👤 {side_label}: <b>{esc(getattr(counterparty, 'username', None) or 'неизвестен')}</b>",
        f"🔖 Статус: <b>{esc(status_ru)}</b>",
        f"🕐 Создана: {esc(_when(getattr(deal, 'created_at', None)))}",
        f"🆔 Сделка: <code>{esc(getattr(deal, 'id', '—'))}</code>",
    ]

    description = getattr(deal, "status_description", None)
    if description:
        lines.append(f"💬 {esc(description)}")

    comment = getattr(deal, "comment_from_buyer", None)
    if comment:
        lines.append(f"📝 Комментарий покупателя: {esc(comment)}")

    if getattr(deal, "has_problem", False):
        lines += ["", "⚠️ <b>По сделке заявлена проблема.</b>"]

    return "\n".join(lines)


def format_problem(deal, account_id) -> str:
    """Playerok exposes no complaints API — a dispute surfaces as has_problem."""
    item = getattr(deal, "item", None)
    counterparty = getattr(deal, "user", None)
    sale = is_sale(deal, account_id)

    lines = [
        "⚠️ <b>Проблема по сделке!</b>",
        "",
        f"📦 Товар: <b>{esc(getattr(item, 'name', None) or 'Без названия')}</b>",
        f"👤 {'Покупатель' if sale else 'Продавец'}: "
        f"<b>{esc(getattr(counterparty, 'username', None) or 'неизвестен')}</b>",
        f"🔖 Статус сделки: <b>{esc(_STATUS_RU.get(_name(getattr(deal, 'status', '')), '—'))}</b>",
        f"🕐 Создана: {esc(_when(getattr(deal, 'created_at', None)))}",
        f"🆔 Сделка: <code>{esc(getattr(deal, 'id', '—'))}</code>",
    ]

    description = getattr(deal, "status_description", None)
    if description:
        lines.append(f"💬 {esc(description)}")

    return "\n".join(lines)
