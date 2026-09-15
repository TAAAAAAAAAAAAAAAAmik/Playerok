"""Адаптер площадки PlayerOK.

Транспорт — неофициальная библиотека `playerokapi` (alleexxeeyy/PlayerokAPI),
собранная реверс-инжинирингом. Своих GraphQL-запросов мы не пишем: повторять
чужой реверс — это тот же риск, только без сопровождения.

ОТКУДА ВЗЯТЫ ОТВЕТЫ НА ДЕВЯТЬ ВОПРОСОВ
──────────────────────────────────────
Источник — исходники библиотеки, а не документация площадки (открытой
документации у PlayerOK нет). Это лучше догадок, но хуже проверки руками:
библиотека неофициальная и может отстать от площадки.

1. Авторизация — куки `__ddg3` и `token` (JWT) плюс свой user-agent.
   Origin и referer библиотека выставляет сама.

2. Опрос. Вебхуков у площадки нет. Период здесь не зашит: его задаёт
   вызывающий (см. example_bot.py). Библиотека берёт по 24 сделки за
   страницу — это её значение по умолчанию, не наше ограничение.

3. ОПЛАЧЕНО — только PAID. ПРОВЕРЕНО НА ЖИВОМ ЗАКАЗЕ 15.09.2026: сделка
   в статусе PAID висела с полученными деньгами и неотданным товаром —
   ровно то, чего мы от этого статуса ждём.
   SENT, CONFIRMED и CONFIRMED_AUTOMATICALLY означают, что товар уже
   отдан, ROLLED_BACK — что деньги вернули. Про PENDING см. ниже: он
   выглядит оплаченным, но в список намеренно не включён.

4. Пагинация курсором: `{"first": count, "after": after_cursor}`, в ответе
   `hasNextPage` и курсор. Дочитываем все страницы (см. paid_orders).

5. Номер чата НЕ равен номеру заказа: у сделки отдельное поле `chat` со
   своим id. Подтверждено типом ItemDeal.
   ⚠️ И его НЕТ в ответе на «список сделок» — проверено на живом кабинете:
   заказ приходит с пустым чатом, то есть код отправить некуда. Запросы
   «список» и «одна сделка» у площадки закреплённые, с разными наборами
   полей. Поэтому чат дочитывается отдельно (см. _with_chat).

6. Про запись в закрытый заказ достоверных данных нет. Поэтому ошибку
   отправки мы не переводим в совет «ответьте вручную» — движок и так
   сохранит код и повторит попытку.

7. Описание товара — в `deal.item`. Если описания нет, движок скажет
   продавцу дописать регион в карточку товара.

8. Что площадка отвечает при превышении темпа — неизвестно. Поэтому любая
   ошибка получения списка поднимается наверх, а частоту опроса задаёт
   вызывающий; отступ вдвое делается там же.

9. Идемпотентности у отправки нет. Поэтому движок пишет факт отправки в
   журнал ДО неё и повторно код не шлёт.

ЧТО ОСТАЛОСЬ ПРОВЕРИТЬ РУКАМИ
─────────────────────────────
* ответ площадки при превышении темпа (пункт 8);
* адрес заказа в кабинете (order_url) — формат ссылки не подтверждён.

Пункт 3 проверен на живом заказе и больше догадкой не является.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from marketplace import Order

# Статусы, при которых деньги у продавца, а товар ещё не отдан.
#
# Только PAID, и это выбор в сторону осторожности. PENDING описан как
# «сделка в ожидании отправки товара» и выглядит оплаченным, но вторая
# независимая библиотека (playerok-requests-api) в запросе «актуальные
# оплаченные сделки» фильтрует ровно по ["PAID"] и PENDING не берёт.
# Две реализации расходятся — значит достоверности нет.
#
# Ошибиться можно в две стороны, и цена у них разная: лишний статус здесь
# отдаёт коды бесплатно и необратимо, недостающий — всего лишь оставляет
# заказ на ручную выдачу, что видно и чинится. Поэтому берём меньшее.
#
# PAID проверен на живом заказе 15.09.2026 и означает именно это. PENDING
# на живом заказе пока не встречался, так что про него достоверности
# по-прежнему нет — если окажется, что это честное «оплачено, ждём
# отправки», добавьте его сюда и допишите тест.
PAID_STATUSES = frozenset({"PAID"})

# Сколько сделок просить за раз. 24 — значение самой библиотеки.
PAGE_SIZE = 24

# Потолок страниц за один проход. Защита от бесконечного цикла, если
# площадка когда-нибудь начнёт отдавать hasNextPage всегда истинным.
MAX_PAGES = 40


def _status_name(status: Any) -> str:
    """Имя статуса строкой, чем бы он ни пришёл.

    Библиотека отдаёт Enum, но сравнивать мы будем по имени: числовые
    значения enum'а — её внутреннее дело и могут перенумероваться.
    """
    if status is None:
        return ""

    return str(getattr(status, "name", status) or "").strip().upper()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class PlayerokMarketplace:
    """Площадка PlayerOK для движка выдачи.

    Логики выдачи здесь нет и быть не должно: задача адаптера — привести
    ответ площадки к `Order` и отправить сообщение.
    """

    name = "playerok"

    def __init__(self, account: Any, page_size: int = PAGE_SIZE):
        """`account` — готовый `playerokapi.account.Account`.

        Аккаунт создаётся снаружи: у него свои куки и user-agent, и держать
        их здесь означало бы тащить в адаптер хранение секретов.
        """
        if account is None:
            raise ValueError("Нужен экземпляр playerokapi Account")

        self.account = account
        self.page_size = int(page_size) or PAGE_SIZE

    # ------------------------------------------------------------------
    # Чтение заказов
    # ------------------------------------------------------------------

    async def paid_orders(self) -> list[Order]:
        """Оплаченные продажи, все страницы.

        Первая страница — это не «все»: на исходной площадке чтение только
        первой молча теряло половину заказов.
        """
        orders: list[Order] = []
        cursor: str | None = None

        for _ in range(MAX_PAGES):
            page = await self._page(cursor)
            deals = list(getattr(page, "deals", None) or [])

            for deal in deals:
                order = self._to_order(deal)

                if order is not None and self.is_paid(order.status):
                    orders.append(order)

            cursor = self._next_cursor(page)

            if not cursor:
                break

        # Чат дочитываем после отбора: у списка и у одиночной сделки на
        # площадке РАЗНЫЕ наборы полей, и в списке чата нет вовсе.
        return [await self._with_chat(order) for order in orders]

    async def _with_chat(self, order: Order) -> Order:
        """Заказ с номером чата, дочитанным отдельным запросом.

        Проверено на живом кабинете: «список сделок» возвращает сделку без
        поля chat, и заказ приходит с пустым чатом — то есть выдавать код
        некуда. Одиночный запрос сделки это поле отдаёт.

        Дочитываем только оплаченные, уже отобранные: их единицы, а лишний
        запрос на каждую сделку подряд стоил бы темпа опроса.
        """
        if order.chat_id:
            return order

        get_deal = getattr(self.account, "get_deal", None)

        if not callable(get_deal):
            return order

        try:
            deal = await _run(get_deal, order.id)
        except Exception:                                  # noqa: BLE001
            # Не достучались — отдаём как есть. Движок скажет «нет чата»
            # и оставит заказ на ручную выдачу, а это видно и чинится.
            return order

        full = self._to_order(deal) if deal is not None else None

        if full is None or not full.chat_id:
            return order

        # Берём ТОЛЬКО чат: остальное уже прочитано из списка, и подменять
        # его целиком значит доверять второму ответу больше первого без
        # причины.
        return replace(order, chat_id=full.chat_id)

    async def get_order(self, order_id: str) -> Order | None:
        """Одна сделка по номеру — для возобновления оборванной выдачи."""
        wanted = _text(order_id)

        if not wanted:
            return None

        get_deal = getattr(self.account, "get_deal", None)

        if callable(get_deal):
            try:
                deal = await _run(get_deal, wanted)
            except Exception:                              # noqa: BLE001
                deal = None

            if deal is not None:
                return self._to_order(deal)

        # Запасной путь: библиотека может не уметь брать сделку поштучно.
        # Листаем страницы, пока не найдём нужную.
        cursor: str | None = None

        for _ in range(MAX_PAGES):
            page = await self._page(cursor)

            for deal in list(getattr(page, "deals", None) or []):
                order = self._to_order(deal)

                if order is not None and order.id == wanted:
                    return order

            cursor = self._next_cursor(page)

            if not cursor:
                break

        return None

    # ------------------------------------------------------------------
    # Отправка
    # ------------------------------------------------------------------

    async def send_message(self, chat_id: str, text: str) -> tuple[bool, str]:
        """Написать покупателю → (отправилось, причина по-русски)."""
        chat = _text(chat_id)

        if not chat:
            return False, "у сделки нет чата — писать покупателю некуда"

        try:
            await _run(self.account.send_message, chat, text)
        except Exception as e:                             # noqa: BLE001
            return False, _explain(e)

        return True, "отправлено"

    async def mark_sent(self, order_id: str) -> tuple[bool, str]:
        """Пометить сделку отправленной → (получилось, причина по-русски).

        Без этой отметки сделка остаётся оплаченной: покупатель код видит,
        а деньги у площадки не разблокированы. Найдено по чужому боту
        (playerok-universal), который после выдачи делает ровно это.
        """
        wanted = _text(order_id)

        if not wanted:
            return False, "нет номера заказа"

        update = getattr(self.account, "update_deal", None)

        if not callable(update):
            return False, "библиотека не умеет менять статус сделки"

        sent = _status_sent()

        if sent is None:
            return False, "в библиотеке не нашёлся статус «отправлено»"

        try:
            await _run(update, wanted, sent)
        except Exception as e:                             # noqa: BLE001
            return False, _explain(e)

        return True, "сделка помечена отправленной"

    # ------------------------------------------------------------------
    # Справки
    # ------------------------------------------------------------------

    def is_paid(self, status: str) -> bool:
        """Означает ли статус, что деньги у продавца, а товар ещё не отдан."""
        return _status_name(status) in PAID_STATUSES

    def order_url(self, order_id: str) -> str:
        """Ссылка на сделку в кабинете.

        Формат не подтверждён живым заказом, поэтому при сомнениях лучше
        вернуть пустую строку, чем вести продавца по битой ссылке.
        """
        wanted = _text(order_id)

        return f"https://playerok.com/deal/{wanted}" if wanted else ""

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    async def _page(self, cursor: str | None):
        """Страница продаж. Статусы фильтруем и здесь, и после разбора:
        площадка может не понять фильтр, и тогда отсев сделает is_paid.
        """
        return await _run(
            lambda: self.account.get_deals(
                direction=_direction_out(),
                count=self.page_size,
                after_cursor=cursor,
            )
        )

    @staticmethod
    def _next_cursor(page: Any) -> str | None:
        """Курсор следующей страницы или None, если страница последняя."""
        if not getattr(page, "has_next_page", False):
            return None

        cursor = getattr(page, "end_cursor", None) or getattr(page, "cursor", None)

        return _text(cursor) or None

    def _to_order(self, deal: Any) -> Order | None:
        """Сделка площадки → заказ в терминах движка."""
        order_id = _text(getattr(deal, "id", ""))

        if not order_id:
            return None

        item = getattr(deal, "item", None)
        chat = getattr(deal, "chat", None)
        user = getattr(deal, "user", None)

        return Order(
            id=order_id,
            title=_text(getattr(item, "name", None) or getattr(item, "title", None)),
            status=_status_name(getattr(deal, "status", None)),
            # Чат — отдельная сущность со своим id, номеру заказа он не равен.
            chat_id=_text(getattr(chat, "id", None)),
            description=_text(getattr(item, "description", None)),
            buyer=_text(getattr(user, "username", None) or getattr(user, "name", None)),
            amount=_amount(deal),
            raw={"deal": deal},
        )


def _status_sent():
    """Статус «продавец подтвердил выполнение сделки».

    Импортируется лениво по той же причине, что и направление сделок:
    модуль должен читаться и без установленной библиотеки.
    """
    try:
        from playerokapi.enums import ItemDealStatuses
    except ImportError:
        return None

    return ItemDealStatuses.SENT


def _direction_out():
    """Только продажи: покупки продавца нас не касаются.

    Enum импортируется лениво, чтобы модуль читался и без установленной
    библиотеки — например в тестах на поддельной площадке.
    """
    try:
        from playerokapi.enums import ItemDealDirections
    except ImportError:
        return None

    return ItemDealDirections.OUT


def _amount(deal: Any) -> float | None:
    """Сумма сделки, если площадка её отдала."""
    transaction = getattr(deal, "transaction", None)
    value = getattr(transaction, "value", None) or getattr(transaction, "amount", None)

    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Как библиотека называет свои исключения входа. Смотреть надо на имя
# класса, а не только на текст: у playerokapi.exceptions.UnauthorizedError
# сообщение целиком по-русски — «Не удалось подключиться к аккаунту
# Playerok» — и ни кода, ни слова unauthorized в нём нет. Проверка по
# тексту молча пропускала САМЫЙ ЧАСТЫЙ случай отказа во входе.
AUTH_ERRORS = ("unauthorizederror", "forbiddenerror", "notinitiatederror")

# Куски текста, по которым отказ во входе узнаётся, когда исключение
# пришло не от библиотеки, а прямо от площадки.
AUTH_WORDS = ("401", "403", "unauthor", "forbidden",
              "не удалось подключиться к аккаунту",
              "неверный token", "неверный токен")


def is_auth_error(error: Any) -> bool:
    """Отказ именно во входе, а не любая ошибка площадки.

    Отдельная проверка нужна тем, кто по ней принимает решение — например
    просит у владельца новые куки. Разбирать русскую фразу из _explain для
    этого нельзя: текст пишется человеку и меняется вместе с формулировкой.
    """
    name = type(error).__name__.lower()

    if name in AUTH_ERRORS:
        return True

    low = str(error).lower()

    return any(word in low for word in AUTH_WORDS)


def _explain(error: Exception) -> str:
    """Ошибка площадки — человеческой фразой.

    Голый код ошибки на экране продавца означает «разбирайся сам», поэтому
    переводим то, что узнаём, а незнакомое показываем как есть, но с
    пояснением, что это ответ площадки.
    """
    text = str(error).strip()
    low = text.lower()

    if is_auth_error(error):
        return ("площадка не приняла вход: истекли куки продавца, нужно "
                "войти заново")

    if "429" in low or "too many" in low or "rate" in low:
        return "площадка просит сбавить темп — повторим позже"

    if "timeout" in low or "timed out" in low:
        return "площадка не ответила вовремя"

    if "not found" in low or "404" in low:
        return "чат не найден: возможно, сделка закрыта и писать в неё нельзя"

    return f"площадка отказала: {text[:150]}" if text else "площадка отказала без объяснения"


async def _run(fn, *args):
    """Синхронный вызов библиотеки — в поток, чтобы не держать цикл."""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))
