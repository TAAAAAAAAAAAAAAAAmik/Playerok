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
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import (open_account, sign_in,           # noqa: E402
                  user_agent_from_env)
from cards import CARDS                           # noqa: E402
from catalog import (Card, Denomination,           # noqa: E402
                     denominations_for, denominations_from, find_service)
from delivery import DeliveryEngine               # noqa: E402
from envfile import load_env_file                 # noqa: E402
from owner import link_from_env, renew_cookies    # noqa: E402
from playerok import PlayerokMarketplace, is_auth_error   # noqa: E402
from settings import Settings                     # noqa: E402
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
    **2 раза в минуту**, а ответ — больше тысячи услуг одним куском. Без
    кеша два оплаченных заказа подряд выбирают лимит, и третий покупатель
    ждёт минуту ни за что.

    Кешируется весь ответ, а не отдельные услуги: он приходит одним куском
    на все услуги сразу, и просить его повторно ради второй карты — тот же
    тяжёлый вызов из того же лимита.

    Три вещи здесь не для красоты:

    * **возраст отдаётся наружу** (`age`). Показать вчерашние остатки, не
      сказав об этом, — то же враньё, что бодрый отчёт о непроверенном;
    * **при отказе отдаётся просроченный каталог**, если он есть: список
      двухминутной давности полезнее пустого экрана;
    * **покупка всё равно перечитывает свой номинал** отдельным запросом
      (`GET /services/{id}/items/{id}`, 120 в минуту) — на нём кеша нет, и
      цена с остатком берутся свежими.
    """

    TTL = 120.0

    def __init__(self, supplier: ApprouteSupplier, conf: Settings):
        self.supplier = supplier
        self.conf = conf
        self._at = 0.0
        self._raw = None
        # Замок на кабинет: два экрана, нажатых подряд, иначе выберут лимит
        # сами себе — каждый своим чтением каталога.
        self._lock = threading.Lock()

    @property
    def age(self) -> float | None:
        """Сколько секунд каталогу. None — не читали ещё ни разу."""
        return None if self._raw is None else time.time() - self._at

    def _catalog(self):
        """Каталог целиком, не чаще чем раз в TTL."""
        with self._lock:
            fresh = self._raw is not None and time.time() - self._at < self.TTL

            if fresh:
                return self._raw

            try:
                self._raw = self.supplier.services()
                self._at = time.time()
            except Exception:
                # Просроченный каталог лучше пустого: остатки в нём могли
                # устареть, но номиналы и номера услуг — вряд ли, а покупка
                # всё равно перечитает свой номинал отдельным запросом.
                if self._raw is None:
                    raise

                logging.warning("каталог поставщика не обновился, работаем "
                                "по списку %.0f с давности", self.age or 0)

            return self._raw

    def __call__(self, card: Card, region: str) -> list[Denomination]:
        try:
            catalog = self._catalog()
        except Exception as e:                           # noqa: BLE001
            logging.error("каталог поставщика не прочитался: %s", e)
            # Пустой список честнее выдумки: движок остановится и скажет
            # «номинал не найден», а не купит не то.
            return []

        # Ручная привязка сильнее отбора по подкатегории: продавец задал её
        # руками, значит у него была причина, и наши догадки её не отменяют.
        service_id = self.conf.service_id(card.slug, region)

        if service_id:
            service = find_service(catalog, service_id)

            if service is None:
                logging.error("услуги %s нет в каталоге поставщика",
                              service_id)
                return []

            return denominations_from(service, service_id, region)

        if not card.subcategory:
            # Ни привязки, ни подкатегории — искать нечем. Молчим: движок
            # скажет об этом понятнее, с названием карты и регионом.
            return []

        rows = denominations_for(card, catalog, region)

        if not rows:
            logging.error("в каталоге нет услуг подкатегории «%s» — "
                          "проверьте её имя у поставщика", card.subcategory)

        return rows


async def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    # Вход в кабинет: куки с диска, из окружения или спросив у владельца в
    # телеграме. Оттуда же берутся новые, когда прежние истекут.
    account, cookie_store, link = sign_in()
    market = PlayerokMarketplace(account)

    async def notify(text: str) -> None:
        """Сообщить продавцу. Телеграм есть — пишем туда, нет — в журнал."""
        if link and await to_thread(link.say, text):
            return

        logging.info("[продавцу] %s", text)

    supplier = ApprouteSupplier(
        api_key=os.environ["APPROUTE_KEY"],
        # Прокси с ПОСТОЯННЫМ адресом: у поставщика белый список IP, а адрес
        # сервера меняется при каждом выкате.
        proxy=os.environ.get("APPROUTE_PROXY", ""),
    )
    store = JsonStore("state/seller-1.json")

    # Настройки поверх того же хранилища: что включено, по какому слову
    # узнавать свои объявления и какими услугами поставщика покупать.
    # Движок читает их оттуда же сам.
    conf = Settings(store)
    engine = DeliveryEngine(
        market=market, supplier=supplier, store=store, cards=CARDS,
        notify=notify, catalog_of=Catalog(supplier, conf),
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

            if is_auth_error(e) and link:
                # Куки истекли. Пока владелец не пришлёт новые, площадка
                # будет отказывать каждый проход, а покупатели — ждать.
                account = await ask_for_new_cookies(cookie_store, link)

                if account is not None:
                    market.account = account

        await asyncio.sleep(PERIOD)


async def ask_for_new_cookies(cookie_store, link):
    """Попросить у владельца новые куки и войти заново.

    Ожидание живёт в отдельном потоке, чтобы не держать цикл asyncio: сам
    проход по заказам всё равно ждёт — с отказанными куками опрашивать
    площадку бессмысленно, она ответит тем же отказом.
    """
    logging.warning("Площадка не приняла вход — прошу новые куки в телеграме")

    cookies = await to_thread(
        renew_cookies, cookie_store, link,
        "Площадка не приняла вход: куки истекли. Выдача кодов стоит.")

    if not cookies:
        logging.error("Новых куки не пришло — пробую прежние дальше")
        return None

    try:
        return open_account(cookies, user_agent_from_env())
    except Exception as e:                           # noqa: BLE001
        logging.error("новые куки не подошли: %s", e)
        return None


async def to_thread(fn, *args):
    """Синхронный вызов — в поток, чтобы не держать цикл заказов."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: fn(*args))


if __name__ == "__main__":
    asyncio.run(main())
