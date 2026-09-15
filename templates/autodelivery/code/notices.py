"""Уведомления о событиях площадки: что именно сказать владельцу.

Здесь только тексты и решения, без сети — иначе проверить их можно было бы
только дождавшись настоящей продажи.

ЧЕГО НЕ ДЕЛАЕМ. Не пишем о своих же действиях: продавец знает, что он
только что ответил в чат, и уведомление об этом превращает поток в шум, а
шум перестают читать. Отличаем по номеру пользователя.
"""
from __future__ import annotations

# Сколько текста сообщения показывать. Длинные полотна в уведомлении
# бесполезны: читать их всё равно идут в чат.
TEXT_LIMIT = 300

DEAL_URL = "https://playerok.com/deal/"

# Какие события бот показывает. Имена классов, а не значения перечисления:
# так разбор читается и проверяется без установленной библиотеки.
KINDS = {
    "ItemPaidEvent": "buy",
    "NewDealEvent": "buy",
    "NewMessageEvent": "message",
    "DealConfirmedEvent": "confirmed",
    "DealHasProblemEvent": "problem",
    "DealProblemResolvedEvent": "resolved",
    "NewReviewEvent": "review",
    "DealRolledBackEvent": "refund",
    "ItemSentEvent": "sent",
}

# Что включено по умолчанию. «Отправлено» выключено: это наше же действие,
# бот сам помечает сделку отправленной, и сообщать о нём себе же незачем.
DEFAULT = ("buy", "message", "confirmed", "problem", "resolved", "review",
           "refund")


def kind_of(event) -> str:
    """Какого рода событие. Пусто — незнакомое."""
    return KINDS.get(type(event).__name__, "")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _name(node, *fields) -> str:
    for field in fields:
        got = _text(getattr(node, field, ""))

        if got:
            return got

    return ""


def _who(node) -> str:
    """Имя пользователя из объекта, где бы оно ни лежало."""
    user = getattr(node, "user", None)

    return _name(user, "username", "name") or "покупатель"


def _mine(node, me_id: str) -> bool:
    """Наше ли это действие."""
    user = getattr(node, "user", None)
    who = _text(getattr(user, "id", ""))

    return bool(who) and who == _text(me_id)


def _item(deal) -> str:
    item = getattr(deal, "item", None)

    return _name(item, "name", "title") or "товар"


def _link(deal) -> str:
    deal_id = _text(getattr(deal, "id", ""))

    return f"\n{DEAL_URL}{deal_id}" if deal_id else ""


def describe(event, me_id: str = "", enabled=None) -> str:
    """Событие → текст для владельца. Пусто — писать не о чем."""
    kind = kind_of(event)

    if not kind or kind not in (DEFAULT if enabled is None else enabled):
        return ""

    if kind == "message":
        return _message(event, me_id)

    deal = getattr(event, "deal", None)

    if deal is None:
        return ""

    # Сделка, где покупатель — мы сами, к торговле отношения не имеет.
    if _mine(deal, me_id):
        return ""

    who = _who(deal)
    what = _item(deal)
    link = _link(deal)

    if kind == "buy":
        return f"💰 Покупка: «{what}»\nПокупатель: {who}{link}"

    if kind == "confirmed":
        return f"✅ Заказ подтверждён: «{what}»\nПокупатель: {who}{link}"

    if kind == "problem":
        why = _text(getattr(deal, "status_description", ""))

        return (f"⚠️ Проблема по заказу «{what}»\nПокупатель: {who}"
                + (f"\n{why}" if why else "") + link)

    if kind == "resolved":
        return f"👌 Проблема решена: «{what}»{link}"

    if kind == "refund":
        return f"↩️ Возврат по заказу «{what}»\nПокупатель: {who}{link}"

    if kind == "review":
        return f"⭐ Новый отзыв по «{what}»\nОт: {who}{link}"

    if kind == "sent":
        return f"📦 Отправлено: «{what}»{link}"

    return ""


def _message(event, me_id: str) -> str:
    """Сообщение в чате.

    Свои не показываем: продавец знает, что сам только что ответил.
    """
    message = getattr(event, "message", None)

    if message is None or _mine(message, me_id):
        return ""

    body = _text(getattr(message, "text", ""))
    images = getattr(message, "images", None) or []

    if not body and images:
        body = "(картинка)"

    if not body:
        return ""

    if len(body) > TEXT_LIMIT:
        body = body[:TEXT_LIMIT] + "…"

    return f"💬 {_who(message)}:\n{body}"
