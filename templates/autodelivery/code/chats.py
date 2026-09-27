"""Чаты площадки так, как их читает продавец с телефона.

Уведомление говорит «покупатель написал», а ответить на него было негде:
бот умел писать в чат сам, а продавец — нет, и за каждым «как
активировать» приходилось идти в браузер. Здесь то, что нужно, чтобы
ответить прямо из бота: список чатов, переписка и подпись под каждой
строкой, кто её написал.

Сети тут нет: на вход приходят объекты площадки, на выход — строки. Так
проверяется без единого живого чата, а заодно видно, что показываем мы
ровно то, что пришло.
"""
from __future__ import annotations

import time

# Сколько текста сообщения показывать в списке чатов. Строка списка
# должна оставаться строкой: на телефоне их помещается десяток.
PREVIEW = 40

# Сколько — в самой переписке. Здесь можно больше: её открыли, чтобы
# прочитать.
BODY = 300


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _name(node, *fields) -> str:
    for field in fields:
        got = _text(getattr(node, field, ""))

        if got:
            return got

    return ""


def who_of(chat, me_id: str = "") -> str:
    """С кем этот чат. Мы сами из участников вычеркнуты.

    Площадка отдаёт обоих собеседников, и без вычёркивания список чатов
    наполовину состоит из собственного ника продавца.
    """
    for user in getattr(chat, "users", None) or []:
        if _text(getattr(user, "id", "")) == _text(me_id):
            continue

        name = _name(user, "username", "name")

        if name:
            return name

    return "покупатель"


def unread_of(chat) -> int:
    """Сколько непрочитанных. Ноль — всё прочитано."""
    try:
        return int(getattr(chat, "unread_messages_counter", 0) or 0)
    except (TypeError, ValueError):
        return 0


def item_of(chat) -> str:
    """Товар, о котором чат. Пусто — сделки в чате нет."""
    for deal in getattr(chat, "deals", None) or []:
        name = _name(getattr(deal, "item", None), "name", "title")

        if name:
            return name

    return ""


def last_of(chat) -> str:
    """Последнее сообщение чата одной строкой."""
    message = getattr(chat, "last_message", None)
    body = _text(getattr(message, "text", ""))

    if not body and (getattr(message, "images", None) or []):
        return "(картинка)"

    return shorten(body, PREVIEW)


def shorten(text: str, limit: int) -> str:
    """Обрезать по границе, добавив многоточие."""
    text = " ".join(_text(text).split())

    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def mine(message, me_id: str) -> bool:
    """Это наше сообщение?"""
    who = _text(getattr(getattr(message, "user", None), "id", ""))

    return bool(who) and who == _text(me_id)


def label_of(chat, me_id: str = "") -> str:
    """Подпись кнопки чата: с кем и сколько непрочитанных."""
    unread = unread_of(chat)
    mark = f"🔴 {unread} · " if unread else ""

    return shorten(f"{mark}{who_of(chat, me_id)}", 30)


def line_of(chat, me_id: str = "") -> str:
    """Строка списка: с кем, о чём и что сказано последним."""
    parts = [f"{'🔴' if unread_of(chat) else '▫️'} {who_of(chat, me_id)}"]
    item = item_of(chat)

    if item:
        parts.append(f"«{shorten(item, 28)}»")

    last = last_of(chat)

    if last:
        parts.append(f"— {last}")

    return " ".join(parts)


def when_of(message) -> str:
    """Когда написано: «14:03» сегодня, «22.09 14:03» раньше.

    Дата у площадки приходит строкой ISO, а продавцу нужно время. Не
    разобралась — показываем как есть: чужой формат честнее выдуманного.
    """
    raw = _text(getattr(message, "created_at", ""))

    if not raw:
        return ""

    try:
        stamp = time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return raw[:16]

    today = time.localtime()

    if (stamp.tm_year, stamp.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", stamp)

    return time.strftime("%d.%m %H:%M", stamp)


def talk_of(messages, me_id: str = "", limit: int = 10) -> list:
    """Переписка строками, старые сверху.

    Площадка отдаёт свежие первыми — как в ленте. В переписке это читается
    задом наперёд, поэтому переворачиваем: последнее сообщение должно
    оказаться последним.
    """
    rows = list(messages or [])[:limit]
    rows.reverse()
    out = []

    for message in rows:
        body = _text(getattr(message, "text", ""))

        if not body and (getattr(message, "images", None) or []):
            body = "(картинка)"

        if not body:
            continue

        who = "Я" if mine(message, me_id) else _name(
            getattr(message, "user", None), "username", "name") or "покупатель"
        when = when_of(message)
        head = f"{who}, {when}" if when else who
        out.append(f"{head}:\n{shorten(body, BODY)}")

    return out
