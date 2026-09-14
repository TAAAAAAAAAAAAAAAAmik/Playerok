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

3. ОПЛАЧЕНО — статусы PAID («сделка оплачена») и PENDING («в ожидании
   отправки товара»). SENT, CONFIRMED и CONFIRMED_AUTOMATICALLY означают,
   что товар уже отдан, ROLLED_BACK — что деньги вернули.
   ⚠️ ЭТО ЕДИНСТВЕННЫЙ ПУНКТ, КОТОРЫЙ НАДО ПРОВЕРИТЬ НА СВОЁМ ТЕСТОВОМ
   ЗАКАЗЕ ДО ПЕРВОЙ ЖИВОЙ ПОКУПКИ. Ошибка здесь отдаёт коды бесплатно.

4. Пагинация курсором: `{"first": count, "after": after_cursor}`, в ответе
   `hasNextPage` и курсор. Дочитываем все страницы (см. paid_orders).

5. Номер чата НЕ равен номеру заказа: у сделки отдельное поле `chat` со
   своим id. Подтверждено типом ItemDeal.

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
* пункт 3 на тестовом заказе;
* ответ площадки при превышении темпа (пункт 8);
* адрес заказа в кабинете (order_url) — формат ссылки не подтверждён.
"""
from __future__ import annotations

import asyncio
from typing import Any

from marketplace import Order

# Статусы, при которых деньги у продавца, а товар ещё не отдан.
PAID_STATUSES = frozenset({"PAID", "PENDING"})

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

        return orders

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


def _explain(error: Exception) -> str:
    """Ошибка площадки — человеческой фразой.

    Голый код ошибки на экране продавца означает «разбирайся сам», поэтому
    переводим то, что узнаём, а незнакомое показываем как есть, но с
    пояснением, что это ответ площадки.
    """
    text = str(error).strip()
    low = text.lower()

    if "401" in low or "unauthor" in low or "forbidden" in low or "403" in low:
        return "площадка не приняла вход: истекли куки продавца, нужно войти заново"

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
