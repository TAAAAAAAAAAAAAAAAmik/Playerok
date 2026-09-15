"""Уведомления о событиях площадки: что именно сказать владельцу.

Здесь только тексты и решения, без сети — иначе проверить их можно было бы
только дождавшись настоящей продажи.

ПОВТОРЫ. Библиотека помнит показанное внутри объекта слушателя, и эта
память исчезает вместе с ним — при обрыве связи или перезапуске бота
недавние события приходят заново. Поэтому помним и сами, по опознавателю
события, и память эта переживает перезапуск.

ЧЕГО НЕ ДЕЛАЕМ. Не пишем о своих же действиях: продавец знает, что он
только что ответил в чат, и уведомление об этом превращает поток в шум, а
шум перестают читать. Отличаем по номеру пользователя.
"""
from __future__ import annotations

import json
import os
import tempfile

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


def identity(event) -> str:
    """Опознаватель события: что считать тем же самым.

    У сообщения — его номер. У событий сделки — вид плюс номер сделки:
    одна сделка проходит через покупку, подтверждение и отзыв, и это
    разные события, а вот два «подтверждено» по одной сделке — повтор.
    """
    kind = kind_of(event)

    if not kind:
        return ""

    if kind == "message":
        message = getattr(event, "message", None)
        number = _text(getattr(message, "id", ""))

        return f"message:{number}" if number else ""

    deal = getattr(event, "deal", None)
    number = _text(getattr(deal, "id", ""))

    return f"{kind}:{number}" if number else ""


class Seen:
    """Показанные события. Переживает перезапуск.

    Без записи на диск перезапуск бота — а он перезапускается сторожем при
    каждом сбое — снова показывал бы последние события. Для продавца это
    выглядит как бот, который дублирует сообщения.
    """

    def __init__(self, path: str = "", limit: int = 400):
        self.path = path
        self.limit = limit
        self.ids = self._read()

    def _read(self) -> list:
        if not self.path:
            return []

        try:
            with open(self.path, encoding="utf-8") as f:
                return [str(x) for x in (json.load(f).get("ids") or [])]
        except (OSError, ValueError):
            return []

    def __contains__(self, key) -> bool:
        return str(key) in self.ids

    def add(self, key) -> None:
        key = str(key)

        if not key or key in self.ids:
            return

        self.ids.append(key)
        self.ids = self.ids[-self.limit:]
        self._write()

    def fresh(self, event) -> bool:
        """Новое ли это событие. Заодно запоминает его.

        Событие без опознавателя считаем новым: пропустить настоящую
        покупку хуже, чем показать её дважды.
        """
        key = identity(event)

        if not key:
            return True

        if key in self.ids:
            return False

        self.add(key)

        return True

    def _write(self) -> None:
        if not self.path:
            return

        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"ids": self.ids}, f)

            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
