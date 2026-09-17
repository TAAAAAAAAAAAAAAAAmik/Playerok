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
import statepath                                  # noqa: E402
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

        rows = denominations_for(card, catalog)

        if not rows:
            logging.error("в каталоге нет услуг подкатегории «%s» — "
                          "проверьте её имя у поставщика", card.subcategory)

        return rows


# Сколько чужих заказов перечислять поимённо. Дальше — числом: письмо на
# сорок строк продавец не дочитает, а смысл в первых.
STRANGERS_SHOWN = 5


def strangers(orders) -> str:
    """Письмо продавцу про оплаченные заказы, которые бот не признал.

    Раньше про них не говорилось ничего. Движок возвращал None, цикл шёл
    дальше, и со стороны это выглядело как «автовыдача сломана»: заказ
    оплачен, покупатель ждёт, бот молчит — ни ошибки, ни строчки.

    А причина почти всегда одна и лечится в две минуты: товар выключен
    или его название не совпало со словом-опознавателем. Сказать об этом
    — и есть работа.
    """
    rows = list(orders)
    lines = [f"🤔 Оплачено, но я не признал это своим товаром — выдачи не "
             f"было. Заказов: {len(rows)}.", ""]

    for order in rows[:STRANGERS_SHOWN]:
        lines.append(f"  • «{order.title or 'без названия'}» "
                     f"(заказ {order.id})")

    if len(rows) > STRANGERS_SHOWN:
        lines.append(f"  … и ещё {len(rows) - STRANGERS_SHOWN}")

    lines += [
        "",
        "Свои объявления я узнаю по слову в названии. Проверьте в боте:",
        "«⚙️ Автовыдача» → товар → включён ли он и какое стоит "
        "«🔤 Слово-опознаватель».",
        "Слово должно встречаться в названии этих объявлений.",
        "",
        "Пока не признал — выдайте код вручную, покупатель ждёт.",
    ]

    return "\n".join(lines)


def held_orders(orders, whose_of=None) -> str:
    """Письмо про заказы, отложенные при первом запуске.

    Про каждый сказано, что бот о нём думает. Без этого продавец видит
    список из пяти заказов, в котором его товар один, и не понимает,
    собирается ли бот выдать остальные четыре. А не собирается: половина
    из них — другой товар, проданный другим способом.
    """
    rows = list(orders)
    lines = [f"⛔ Глушка: отложил оплаченных заказов — {len(rows)}.", "",
             "Они висели ещё до моего запуска, и по сделке не видно, "
             "выдали их вручную или нет. Покупать коды вслепую я не стал: "
             "это ваши деньги.", ""]
    mine = 0

    for order in rows[:STRANGERS_SHOWN]:
        card, why = whose_of(order.title) if whose_of else (None, "")
        mark = "✅" if card is not None and why == "мой товар" else "➖"

        if mark == "✅":
            mine += 1

        lines.append(f"  {mark} «{order.title or 'без названия'}»"
                     + (f" — {why}" if why and mark != "✅" else ""))
        lines.append(f"     заказ {order.id}")

    if len(rows) > STRANGERS_SHOWN:
        lines.append(f"  … и ещё {len(rows) - STRANGERS_SHOWN}")

    lines += [
        "",
        "✅ — мой товар, выдам код. ➖ — не мой, не трону.",
        "",
        "Если мои и правда ждут кода — бот → «⚙️ Автовыдача» → «⛔ Глушка» "
        "→ «✅ Выдать их».",
        "Если уже выданы — там же «🚫 Считать закрытыми».",
        "",
        "Новых заказов это не касается: их я выдаю сам.",
    ]

    return "\n".join(lines)


async def one_pass(market, engine, seen: set, notify) -> list:
    """Один проход по оплаченным заказам. → непризнанные заказы.

    Отдельной функцией, а не телом цикла: так проход можно прогнать в
    тестах целиком, а бесконечный цикл — нельзя.
    """
    # Чужие копим за проход и говорим о них одним письмом: по письму на
    # заказ — это рассылка, а не помощь.
    unknown = []
    orders = await market.paid_orders()

    # Первый взгляд на витрину: всё, что висело до запуска, откладываем.
    # Продавец мог выдать эти заказы руками — по сделке это не видно.
    held = engine.hold_old(orders)

    if held:
        await notify(held_orders(held, engine.whose))

    for order in orders:
        if order.id in seen or engine.held(order.id):
            continue

        result = await engine.on_paid_order(order)

        if result is None:
            # Движок не признал товар своим. Молчать об этом нельзя: для
            # продавца это и есть «автовыдача не работает».
            logging.warning("заказ %s («%s») не признан своим",
                            order.id, order.title)
            unknown.append(order)

        # Запоминаем ТОЛЬКО доведённое до конца. Пометив заказ до выдачи,
        # мы теряли его насовсем при любом случайном сбое: каталог не
        # прочитался, поставщик моргнул — и оплаченный заказ больше не
        # пробовался до перезапуска бота.
        #
        # Отказ пробуется каждым проходом и однажды проходит сам (номинал
        # вернулся в наличие, связь поднялась). Продавца это не засыпает:
        # про одну и ту же причину движок говорит один раз.
        if result is None or result.ok:
            seen.add(order.id)

    if unknown:
        await notify(strangers(unknown))

    # Каждый проход, а не только при старте: обрыв случается чаще всего от
    # обычного выката, и без этого вызова деньги останутся потраченными, а
    # покупатель — без кода.
    await engine.resume_unfinished()

    return unknown


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
    # Тот же файл, в который пишет телеграм-бот, и считает его путь
    # общий модуль. Раньше путь считался здесь отдельно
    # («state/seller-1.json») — и выдача читала не то, что продавец
    # настраивал: карта включена в боте, а движок видит пустоту и
    # молча пропускает каждый оплаченный заказ.
    store = statepath.open_store()
    logging.info("состояние выдачи: %s", store.path)

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
            await one_pass(market, engine, seen, notify)
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
