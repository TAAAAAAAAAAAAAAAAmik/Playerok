"""Минимальная сборка: как всё соединяется.

Адаптер площадки уже написан (code/playerok.py), но каталог поставщика —
ещё нет: метод Catalog.__call__ ниже возвращает пустой список. Пока он
пуст, движок честно скажет «номинал не найден» и ничего не купит.

Это и есть последний шаг до боевого запуска: прочитать каталог AppRoute и
привести его к Denomination. Формат ответа — в docs/03_SUPPLIER.md.

Перед первым запуском обязательно прогнать watch.py: он показывает живые
сделки и ничего не покупает.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from cards import CARDS                           # noqa: E402
from catalog import Denomination                  # noqa: E402
from delivery import DeliveryEngine               # noqa: E402
from playerok import PlayerokMarketplace          # noqa: E402
from store import JsonStore                       # noqa: E402
from supplier import ApprouteSupplier             # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# Период опроса заказов. Выясняется у площадки, а не угадывается: слишком
# часто — просьба сбавить темп, слишком редко — покупатель ждёт.
PERIOD = 60.0

class Catalog:
    """Каталог поставщика с кешем.

    Кеш не для скорости, а по необходимости: `GET /services` разрешён
    **2 раза в минуту**. Без кеша два оплаченных заказа подряд означают два
    тяжёлых чтения, и всё это время опрос заказов стоит.
    """

    TTL = 120.0

    def __init__(self, supplier: ApprouteSupplier):
        self.supplier = supplier
        self._at = 0.0
        self._rows: dict[tuple[str, str], list[Denomination]] = {}

    def __call__(self, card: Card, region: str) -> list[Denomination]:
        import time
        key = (card.slug, region.upper())
        if time.time() - self._at < self.TTL and key in self._rows:
            return self._rows[key]

        service_id = card.services.get(region.upper(), "")
        if not service_id:
            return []

        # TODO: здесь читается каталог поставщика и приводится к Denomination.
        #       Остаток и цену берём оттуда же — но перед покупкой движок
        #       перечитает номинал отдельно, потому что кеш может устареть.
        rows: list[Denomination] = []
        self._rows[key] = rows
        self._at = time.time()
        return rows


async def notify(text: str) -> None:
    """Сообщить продавцу. В боевом боте — отправка в Telegram."""
    print("[продавцу]", text)


async def main() -> None:
    from playerokapi.account import Account

    market = PlayerokMarketplace(
        Account(token=os.environ["PLAYEROK_TOKEN"],
                user_agent=os.environ["PLAYEROK_UA"]).get())
    supplier = ApprouteSupplier(
        api_key=os.environ["APPROUTE_KEY"],
        # Прокси с ПОСТОЯННЫМ адресом: у поставщика белый список IP, а адрес
        # сервера меняется при каждом выкате.
        proxy=os.environ.get("APPROUTE_PROXY", ""),
    )
    store = JsonStore("state/seller-1.json")
    engine = DeliveryEngine(
        market=market, supplier=supplier, store=store, cards=CARDS,
        notify=notify, catalog_of=Catalog(supplier),
        # Префикс ссылки покупки. У каждой площадки СВОЙ: ссылка уникальна в
        # пределах кабинета поставщика, а кабинет один на все площадки.
        reference_prefix="pk",
    )

    seen: set[str] = set()
    while True:
        try:
            for order in await market.paid_orders():
                if order.id in seen:
                    continue
                seen.add(order.id)
                await engine.on_paid_order(order)

            # Каждый проход, а не только при старте: обрыв случается чаще
            # всего от обычного выката, и без этого вызова деньги останутся
            # потраченными, а покупатель — без кода.
            await engine.resume_unfinished()
        except Exception as e:                       # noqa: BLE001
            # Исключение не убивает цикл: один упавший заказ не должен
            # уносить с собой остальные.
            logging.error("проход не удался: %s", e)
        await asyncio.sleep(PERIOD)


if __name__ == "__main__":
    asyncio.run(main())
