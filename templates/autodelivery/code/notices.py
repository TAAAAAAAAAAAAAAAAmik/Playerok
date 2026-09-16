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
import re
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
DEFAULT = ("buy", "message", "support", "system", "confirmed", "problem",
           "resolved", "review", "refund")

# Служебные сообщения площадки: «{{ITEM_PAID}}», «{{DEAL_CONFIRMED}}» и
# подобные. Библиотека превращает их в события сделки — но только если
# сумела дочитать саму сделку. Не сумела (сеть моргнула, площадка ответила
# ошибкой) — и то же сообщение доезжает до нас обычным текстом.
#
# Переслать такое владельцу значит прислать ему «{{ITEM_PAID}}» вместо
# «Покупка». Поэтому узнаём их по виду и молчим: настоящее событие о
# покупке придёт своим путём, из опроса сделок.
SYSTEM_TEXT = re.compile(r"^\s*\{\{[A-Z_]+\}\}\s*$")

# Типы чатов площадки: ChatTypes.PM / NOTIFICATIONS / SUPPORT. Сравниваем
# по имени, а не по числу: числа у перечислений меняются молча.
CHAT_SUPPORT = "SUPPORT"
CHAT_SYSTEM = "NOTIFICATIONS"


def kind_of(event, support_id: str = "", system_id: str = "") -> str:
    """Какого рода событие. Пусто — незнакомое.

    Сообщения разделены по чату: письмо поддержки и уведомление площадки
    выглядят как обычное сообщение, но читаются иначе. Продавцу важно
    отличить «покупатель спрашивает» от «поддержка ответила по спору», и
    отключать их он тоже захочет по отдельности.
    """
    name = KINDS.get(type(event).__name__, "")

    if name != "message":
        return name

    where = chat_kind(event, support_id, system_id)

    if where == CHAT_SUPPORT:
        return "support"

    if where == CHAT_SYSTEM:
        return "system"

    return "message"


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


def chat_kind(event, support_id: str = "", system_id: str = "") -> str:
    """Какой это чат: "SUPPORT", "NOTIFICATIONS" или обычный.

    Тип приходит перечислением, но сюда попадает и строка, и число — у
    разных версий библиотеки по-разному. Поэтому читаем терпимо: не узнали
    — считаем обычным чатом, и сообщение всё равно дойдёт.

    Запасной путь — по номеру чата: площадка отдаёт их вместе с кабинетом
    (`support_chat_id`, `system_chat_id`), и это опознание не зависит от
    того, как назван тип в очередной версии библиотеки. Оно и надёжнее:
    номер у чата поддержки один и тот же всегда.
    """
    chat = getattr(event, "chat", None)
    chat_id = _text(getattr(chat, "id", ""))

    if chat_id and chat_id == _text(support_id):
        return CHAT_SUPPORT

    if chat_id and chat_id == _text(system_id):
        return CHAT_SYSTEM

    kind = getattr(chat, "type", None)
    name = _text(getattr(kind, "name", "")) or _text(kind)

    if name.upper().endswith(CHAT_SUPPORT):
        return CHAT_SUPPORT

    if name.upper().endswith(CHAT_SYSTEM):
        return CHAT_SYSTEM

    # Число — на случай, если тип пришёл сырым: PM 0, уведомления 1,
    # поддержка 2.
    if isinstance(kind, int) and not isinstance(kind, bool):
        return {1: CHAT_SYSTEM, 2: CHAT_SUPPORT}.get(kind, "")

    return ""


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


def describe(event, me_id: str = "", enabled=None, support_id: str = "",
             system_id: str = "") -> str:
    """Событие → текст для владельца. Пусто — писать не о чем."""
    kind = kind_of(event, support_id, system_id)

    if not kind or kind not in (DEFAULT if enabled is None else enabled):
        return ""

    if kind in ("message", "support", "system"):
        return _message(event, me_id, kind)

    deal = getattr(event, "deal", None)

    if deal is None:
        return ""

    # Сделка, где покупатель — мы сами, к торговле отношения не имеет.
    #
    # Но проверяем строго: и что покупатель — мы, И что продавец — не мы.
    # Иначе достаточно одной ошибки в том, кого площадка кладёт в поле
    # `user`, чтобы бот молча проглотил ВСЕ продажи, — а выглядело бы это
    # ровно как «уведомления не приходят», без единой жалобы в журнале.
    # Лишнее уведомление дешевле пропущенной покупки.
    if _mine(deal, me_id) and not _mine(getattr(deal, "item", None), me_id):
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
        return _review(deal, what, who, link)

    if kind == "sent":
        return f"📦 Отправлено: «{what}»{link}"

    return ""


# Как подписать сообщение, смотря откуда оно пришло.
MESSAGE_MARK = {
    "message": "💬",
    "support": "🛟 Поддержка",
    "system": "🔔 Площадка",
}


def _review(deal, what: str, who: str, link: str) -> str:
    """Отзыв: оценка и текст.

    Без них уведомление сообщает лишь «отзыв есть», а продавцу надо знать,
    хороший он или нет — и идти читать в кабинет ради одной звезды обидно.
    """
    review = getattr(deal, "review", None)
    rating = getattr(review, "rating", None)
    lines = ["⭐ Новый отзыв по «" + what + "»"]

    try:
        stars = int(rating)
    except (TypeError, ValueError):
        stars = 0

    if 1 <= stars <= 5:
        lines[0] = f"{'⭐' * stars} Отзыв по «{what}»"

    lines.append(f"От: {who}")
    body = _text(getattr(review, "text", ""))

    if body:
        if len(body) > TEXT_LIMIT:
            body = body[:TEXT_LIMIT] + "…"

        lines.append(f"\n{body}")

    return "\n".join(lines) + link


def _message(event, me_id: str, kind: str = "message") -> str:
    """Сообщение в чате.

    Свои не показываем: продавец знает, что сам только что ответил.

    Служебные тоже: «{{ITEM_PAID}}» — это не письмо покупателя, а метка
    площадки, из которой библиотека делает событие о покупке. Дошла она до
    нас текстом — значит сделку прочитать не вышло, и пересылать метку
    владельцу бессмысленно: он увидит «{{ITEM_PAID}}» и не поймёт ничего.
    """
    message = getattr(event, "message", None)

    if message is None or _mine(message, me_id):
        return ""

    body = _text(getattr(message, "text", ""))

    if SYSTEM_TEXT.match(body):
        return ""

    images = getattr(message, "images", None) or []

    if not body and images:
        body = "(картинка)"

    if not body:
        return ""

    if len(body) > TEXT_LIMIT:
        body = body[:TEXT_LIMIT] + "…"

    mark = MESSAGE_MARK.get(kind, "💬")

    # У поддержки и площадки имя отправителя мало что добавляет: важнее,
    # что это не покупатель. У обычного чата — наоборот, имя и есть
    # главное: продавец по нему узнаёт, кто пишет.
    if kind == "message":
        return f"{mark} {_who(message)}:\n{body}"

    who = _who(message)
    signed = f"{mark} ({who})" if who != "покупатель" else mark

    return f"{signed}:\n{body}"


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

    def is_new(self, event) -> bool:
        """Новое ли это событие. НЕ запоминает — только смотрит.

        Разделено с запоминанием нарочно. Запомнив событие до отправки, мы
        теряем его насовсем, если телеграм в этот миг не ответил: повтор
        от площадки придёт, а бот сочтёт его показанным и промолчит.
        Поэтому помечаем только то, что действительно ушло.

        Событие без опознавателя считаем новым: пропустить настоящую
        покупку хуже, чем показать её дважды.
        """
        key = identity(event)

        return not key or key not in self.ids

    def remember(self, event) -> None:
        """Запомнить показанное."""
        key = identity(event)

        if key:
            self.add(key)

    def fresh(self, event) -> bool:
        """Новое ли событие, с запоминанием сразу. Оставлено для тех, кому
        доставка не важна: у уведомлений она важна, там `is_new`."""
        if not self.is_new(event):
            return False

        self.remember(event)

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
