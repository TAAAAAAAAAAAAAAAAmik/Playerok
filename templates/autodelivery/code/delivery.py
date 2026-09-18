"""Движок выдачи. Маркетплейс-независимый — в этом весь смысл шаблона.

Один путь к деньгам на все входы: новый оплаченный заказ, ручная выдача по
кнопке и возобновление оборванной. Второй путь пришлось бы снабдить тем же
порядком записей, и однажды он бы с ним разъехался — а разъехаться это может
молча, на настоящих деньгах.

ПОРЯДОК ЗАПИСЕЙ — главное в этом файле. Он такой:

    намерение  → записали в журнал, СОХРАНИЛИ
    покупка    → купили
    код        → записали код, СОХРАНИЛИ
    отправка   → отправили покупателю
    выдано     → отметили выданным, СОХРАНИЛИ

Каждое «СОХРАНИЛИ» стоит на своём месте по причине:

* намерение до покупки — иначе обрыв связи оставит покупку без следа, и
  повторить её будет нечем, кроме как вслепую;
* код до отправки — чат может быть закрыт, и купленный код не должен
  остаться никому;
* «выдано» ПОСЛЕ отправки — отметка по факту покупки однажды доложила бы
  о выдаче, которой покупатель не видел.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from catalog import (Card, Denomination, match_denomination,
                     nominal_for, order_reference, pick_card,
                     region_from_description, whose)
from marketplace import Marketplace, Order
from store import (STATE_BUYING, STATE_DONE, STATE_NEW, STATE_SEND_FAILED,
                   STATE_SENDING, STATE_WAIT_CODE, UNFINISHED, Store,
                   find_entry)
from supplier import TERMINAL_STATUSES, Supplier, balance_line

logger = logging.getLogger(__name__)

# Сколько ждать код, если поставщик ответил «принято». Ожидание занимает
# поток, а на нём стоит опрос заказов, поэтому потолок жёсткий.
# Незаконченная запись не теряется: её подберёт `resume_unfinished`.
POLL_STEPS = (12, 12, 15, 20, 30, 30, 30, 45, 60, 60)
POLL_CEILING = 120.0

# Как узнаётся «на счёте не хватает денег». По словам, а не по коду,
# потому что до движка отказ доходит уже текстом — и от поставщика, и от
# площадки, и от чужого клиента, если его однажды подменят.
MONEY_WORDS = ("не хватает денег", "insufficient", "недостаточно средств",
               "недостаточно денег", "не хватает средств")

# Как часто напоминать про пустой счёт. Пока он пуст, беда одна и та же на
# все заказы, и письмо на каждый — это рассылка.
MONEY_EVERY = 600.0


def is_money_problem(why: str) -> bool:
    """Отказ про деньги на счёте у поставщика?"""
    low = " ".join(str(why or "").lower().split())

    return any(word in low for word in MONEY_WORDS)


@dataclass
class Result:
    """Чем кончилась попытка. Факты, а не проза.

    Разбирать собственный текст вместо структурных данных — тихая поломка,
    которая ждёт первой правки формулировки.
    """
    ok: bool
    state: str
    why: str = ""
    codes: tuple[str, ...] = ()


class DeliveryEngine:
    """Выдача кодов по оплаченным заказам.

    Площадка приходит через `Marketplace`, поставщик через `Supplier` —
    оба заменяемы, и тесты гоняют движок на поддельных.
    """

    def __init__(self, market: Marketplace, supplier: Supplier, store: Store,
                 cards: list[Card], notify, catalog_of,
                 reference_prefix: str = "pk"):
        self.market = market
        self.supplier = supplier
        self.store = store
        self.cards = cards
        self.notify = notify              # async (текст) — сообщить продавцу
        self.catalog_of = catalog_of      # (card, region) → [Denomination]
        self.prefix = reference_prefix
        # О какой причине по какому заказу уже говорили. Живёт в процессе:
        # после перезапуска про беду стоит напомнить.
        self._told: set = set()
        # Когда в последний раз говорили про пустой счёт у поставщика.
        # Отдельно от `_told`, потому что беда не про заказ: пока счёт
        # пуст, её видят ВСЕ заказы, и по сообщению на каждый — это
        # рассылка, в которой потеряется сама беда.
        self._money_at = 0.0

    # ------------------------------------------------------------------
    # Входы
    # ------------------------------------------------------------------

    async def on_paid_order(self, order: Order) -> Result | None:
        """Новый оплаченный заказ. Главный вход."""
        card = pick_card(self.cards, order.title,
                         lambda slug: self.store.conf(slug))
        if card is None:
            return None
        return await self.deliver(card, order)

    async def deliver_by_hand(self, card_slug: str, order_id: str) -> Result:
        """Ручная выдача. Идёт ТЕМ ЖЕ путём, а не своим."""
        card = next((c for c in self.cards if c.slug == card_slug), None)
        if card is None:
            return Result(False, "", f"вид «{card_slug}» не заведён")
        order = await self.market.get_order(order_id)
        if order is None:
            return Result(False, "", f"заказ {order_id} не найден на площадке")
        # Руками продавец спросил — руками и ответим, даже если про эту
        # причину уже говорили: он ждёт ответа именно сейчас.
        self._told = {k for k in self._told if k[0] != str(order_id)}

        return await self.deliver(card, order, by_hand=True)

    async def resume_unfinished(self) -> list[Result]:
        """Довести выдачи, оборванные посередине.

        Повтор безопасен ровно потому, что ссылка та же: поставщик узнаёт
        её и отвечает IDEMPOTENCY_REPLAY вместо второй покупки.
        """
        out: list[Result] = []
        for card in self.cards:
            conf = self.store.conf(card.slug)
            delivered = set(str(x) for x in conf.get("delivered") or [])
            stuck = [e for e in list(conf.get("log") or [])
                     if isinstance(e, dict)
                     and str(e.get("state") or "") in UNFINISHED
                     and str(e.get("order") or "")
                     and str(e.get("order")) not in delivered]
            for entry in stuck:
                order_id = str(entry.get("order"))
                chat_id = str(entry.get("chat") or "")
                if not chat_id:
                    # Записи, сделанные до того, как чат стал запоминаться.
                    fresh = await self.market.get_order(order_id)
                    chat_id = (fresh.chat_id if fresh else "") or order_id
                    entry["chat"] = chat_id
                logger.info("Возобновляем выдачу по заказу %s (состояние «%s»)",
                            order_id, entry.get("state"))
                out.append(await self._finish(card, entry, chat_id))
        return out

    # ------------------------------------------------------------------
    # Глушка
    # ------------------------------------------------------------------

    def paused(self) -> bool:
        """Продавец остановил выдачу целиком."""
        return bool(self._shared().get("paused"))

    def held(self, order_id: str) -> bool:
        """Этот заказ поставлен на паузу и трогать его нельзя."""
        return str(order_id) in (self._shared().get("held") or {})

    def hold_old(self, orders) -> list:
        """Первый взгляд на витрину: что висело ДО запуска. → что придержали.

        Самый дорогой случай из всех возможных — не сбой, а успешный
        запуск. Бот поднимается с чистым журналом (новая установка,
        перенос, смена кабинета), видит оплаченные заказы недельной
        давности и бодро покупает коды ко всем. А продавец их давно выдал
        руками: площадка держит сделку в «оплачено», пока покупатель не
        подтвердил, и по ней не видно, отдан товар или нет.

        Поэтому на первом же проходе всё, что уже висит, откладывается в
        сторону. Не отменяется — откладывается: продавец нажмёт «выдать»,
        если эти заказы и правда ждут кода.

        Второй и следующие запуски сюда не попадают: витрину мы уже
        осматривали, и всё новое — действительно новое.
        """
        shared = self._shared()

        if shared.get("started"):
            return []

        held = shared.setdefault("held", {})
        fresh = []

        for order in orders:
            key = str(order.id)

            if key in held:
                continue

            held[key] = {"title": str(order.title or ""), "at": time.time()}
            fresh.append(order)

        shared["started"] = True
        self.store.save()

        if fresh:
            logger.warning("первый запуск: придержал заказов %d", len(fresh))

        return fresh

    def whose(self, title: str) -> tuple:
        """Чей это заказ и почему → (карта или None, пояснение).

        Нужно письмам и экранам: продавец, глядя на отложенные заказы,
        должен видеть, что бот о них думает, ДО того как нажмёт «выдать».
        """
        return whose(self.cards, title, lambda slug: self.store.conf(slug))

    def _shared(self) -> dict:
        """Общий раздел состояния. У чужого хранилища его может не быть."""
        shared = getattr(self.store, "shared", None)

        return shared() if callable(shared) else {}

    # ------------------------------------------------------------------
    # Общий путь
    # ------------------------------------------------------------------

    async def deliver(self, card: Card, order: Order,
                      by_hand: bool = False) -> Result:
        conf = self.store.conf(card.slug)

        # 0. Глушка. Руками продавец может выдать и при ней: он тогда сам
        #    смотрит на заказ, а глушка — про то, чтобы бот не решал.
        if self.paused() and not by_hand:
            return await self._stop(
                card, order.id,
                "выдача остановлена глушкой. Снять: бот → «⚙️ Автовыдача» "
                "→ «⛔ Глушка»", record=False)

        if self.held(order.id) and not by_hand:
            return await self._stop(
                card, order.id,
                "заказ на паузе: он висел ещё до моего запуска, и я не "
                "знаю, выдали его вручную или нет. Бот → «⚙️ Автовыдача» "
                "→ «⛔ Глушка»", record=False)

        # 1. Оплачен ли. «Создан» деньгами не является.
        if not self.market.is_paid(order.status):
            return await self._stop(
                card, order.id,
                f"заказ не оплачен (статус «{order.status}»). Выдавать по "
                f"неоплаченному нельзя")

        # 2. Не выдавали ли уже.
        delivered = conf.setdefault("delivered", [])
        if str(order.id) in [str(x) for x in delivered]:
            return Result(True, STATE_DONE, "уже выдан раньше")

        # 2а. Не закрыт ли иначе: выдан вручную, отменён, возвращён. Это
        #     не то же самое, что «выдан нами», и списки разные нарочно:
        #     в одном наши покупки, в другом — чужие решения.
        if str(order.id) in [str(x) for x in conf.setdefault("closed", [])]:
            return Result(True, STATE_DONE, "заказ закрыт, выдача не нужна")

        # 3. Есть ли незаконченная запись — тогда продолжаем ЕЁ.
        entry = find_entry(conf, order.id)
        if entry is not None and str(entry.get("state")) != STATE_DONE:
            return await self._finish(card, entry, order.chat_id or order.id)

        # 4. Регион: сначала описание товара, потом настройка плагина.
        region = region_from_description(order.description) \
            or str(conf.get("region") or "").upper()
        if not region:
            return await self._stop(
                card, order.id,
                "в описании товара не сказано, какой это регион, и запасной "
                "не задан. Допиши в описание строку вида «Регион кода: US»")

        # 5. Номинал: из описания, где он сказан, иначе из названия, где
        #    его приходится угадывать по самому крупному числу.
        # Цену заказа отдаём разбору: продавцы денежных карт пишут её
        # прямо в названии («Apple 10$ за 900 рублей»), и без неё самым
        # крупным числом окажется она, а не номинал.
        # Карту отдаём тоже: по ней читается «50 робуксов» в описании,
        # когда в названии числа нет вовсе.
        want, why = nominal_for(order.title, order.description,
                                getattr(order, "amount", None), card)

        if want is None:
            return await self._stop(card, order.id, why)

        try:
            rows = await self._catalog(card, region)
        except Exception as e:                       # noqa: BLE001
            return await self._stop(card, order.id,
                                    f"каталог поставщика не прочитан: {e}")
        row, why = match_denomination(rows, region, want)
        if row is None:
            return await self._stop(card, order.id, why)

        # 6. Намерение — в журнал, ДО вызова поставщика.
        entry = {
            "order": str(order.id),
            "card": card.slug,
            "chat": str(order.chat_id or order.id),
            "region": region,
            "value": row.value,
            "title": row.title,
            "denomination": row.item_id,
            "service_id": row.service_id,
            "price": row.price,
            # Ссылка считается ОДИН раз и живёт в записи. Пересчёт при
            # повторе = вторая покупка за свои деньги.
            "reference": order_reference(self.prefix, card.slug, order.id),
            "codes": [],
            "state": STATE_NEW,
            "at": time.time(),
            "by_hand": bool(by_hand),
        }
        conf.setdefault("log", []).insert(0, entry)
        self.store.save()

        # 7. Приветствие покупателю — до кода и ровно один раз за заказ.
        greeting = str(conf.get("greeting") or "").strip()
        if greeting and not entry.get("greeted"):
            # Отметка ставится в записи ДО отправки, а не в памяти: проход
            # может оборваться между отправкой и покупкой, и возобновление
            # поздоровалось бы второй раз.
            entry["greeted"] = True
            self.store.save()
            sent, why = await self.market.send_message(entry["chat"], greeting)
            if not sent:
                entry["greet_error"] = str(why)[:150]
                self.store.save()

        return await self._finish(card, entry, entry["chat"])

    # ------------------------------------------------------------------
    # Покупка и отправка
    # ------------------------------------------------------------------

    async def _finish(self, card: Card, entry: dict, chat_id: str) -> Result:
        """Купить (если ещё не купили), отправить код, отметить выданным."""
        order_id = str(entry.get("order") or "")
        reference = str(entry.get("reference") or "")
        conf = self.store.conf(card.slug)
        codes = [str(c) for c in (entry.get("codes") or []) if c]

        # Заказ уже принят поставщиком — повторять покупку незачем.
        if not codes and entry.get("state") == STATE_WAIT_CODE:
            waited = await self._wait_codes(entry, reference)
            if waited is None:
                return Result(False, STATE_WAIT_CODE,
                              "код ещё не пришёл, доведём следующим проходом")
            if not waited:
                # Терминальный статус без кодов — это отказ. Провалиться
                # отсюда в покупку значит купить ещё раз то, что поставщик
                # уже закончил.
                return await self._stop(card, order_id,
                                        str(entry.get("why") or
                                            "поставщик закончил заказ без кода"),
                                        record=False)
            codes = waited

        if not codes:
            # Последняя проверка перед деньгами: а ждёт ли заказ ещё?
            #
            # Список оплаченных прочитан секунды или минуты назад, и за это
            # время продавец мог выдать код руками, покупатель — подтвердить
            # получение, площадка — вернуть деньги. Покупать по такому
            # заказу значит платить за код, который никому не нужен.
            #
            # Спрашиваем ТОЛЬКО перед покупкой: если код уже куплен,
            # отправить его надо в любом случае — деньги потрачены.
            closed, status = await self._already_closed(order_id)

            if closed:
                self._close(card, order_id)

                return await self._stop(
                    card, order_id,
                    f"заказ уже не ждёт выдачи (статус «{status}»): его "
                    f"закрыли, пока я собирался. Покупать не стал",
                    record=False)

            codes = await self._buy(card, entry, reference)
            if isinstance(codes, Result):
                return codes

        # Код записан ДО отправки: чат может быть закрыт, и купленный код не
        # должен остаться никому.
        entry["codes"] = list(codes)
        entry["state"] = STATE_SENDING
        self.store.save()

        note = str(conf.get("note") or "").strip()
        text = "\n".join(
            [f"Ваш код {card.title} {entry.get('title') or ''}:".strip()]
            + list(codes) + ["", card.activation]
            + ([f"Регион кода: {entry.get('region')}."]
               if entry.get("region") else [])
            + ([note] if note else []))

        sent, err = await self.market.send_message(chat_id, text)
        if not sent:
            entry["state"] = STATE_SEND_FAILED
            entry["why"] = str(err)[:200]
            self.store.save()
            await self.notify(
                f"{card.emoji} {card.title}: код куплен, но не отправлен.\n"
                f"Заказ #{order_id}. Причина: {err}\n"
                f"Код: {', '.join(codes)}\n"
                f"Передай его покупателю сам — второй раз бот покупать "
                f"не станет.")
            return Result(False, STATE_SEND_FAILED, str(err), tuple(codes))

        # Только теперь.
        entry["state"] = STATE_DONE
        delivered = conf.setdefault("delivered", [])
        if str(order_id) not in [str(x) for x in delivered]:
            delivered.append(str(order_id))
        self.store.save()
        logger.info("%s: заказ %s выдан", card.slug, order_id)

        # Пометка идёт ПОСЛЕ записи в журнал: сбой на ней не должен
        # заставить купить код второй раз. Покупатель код уже получил, так
        # что неудача здесь выдачу не отменяет — но деньги по непомеченной
        # сделке у площадки не разблокированы, и продавец должен узнать.
        marked, why = await self.market.mark_sent(order_id)

        if not marked:
            entry["mark_sent_failed"] = str(why)[:200]
            self.store.save()
            logger.warning("%s: заказ %s не помечен отправленным: %s",
                           card.slug, order_id, why)
            await self.notify(
                f"{card.emoji} {card.title}: код выдан, но сделка не "
                f"помечена отправленной.\n"
                f"Заказ #{order_id}. Причина: {why}\n"
                f"Пометь её в кабинете сам — иначе деньги по ней висят.")

        return Result(True, STATE_DONE, codes=tuple(codes))

    async def _buy(self, card: Card, entry: dict, reference: str):
        """Перечитать номинал, купить, дождаться кода. → коды либо Result."""
        order_id = str(entry.get("order") or "")

        # Перечитать номинал перед покупкой — замена сухому прогону,
        # которого для магазина не существует. Каталог кешируется, и
        # «в наличии» в нём может быть вчерашним.
        try:
            fresh = await self._run(self.supplier.item,
                                    str(entry.get("service_id") or ""),
                                    str(entry.get("denomination") or ""))
        except Exception as e:                       # noqa: BLE001
            entry["state"] = STATE_NEW
            entry["why"] = str(e)[:300]
            self.store.save()
            return await self._stop(card, order_id,
                                    f"номинал не перечитан перед покупкой: {e}",
                                    record=False)

        if int((fresh or {}).get("inStock") or 0) <= 0:
            entry["state"] = STATE_NEW
            self.store.save()
            return await self._stop(
                card, order_id,
                "к моменту покупки номинал кончился у поставщика — "
                "денег не потрачено", record=False)

        # Цена запоминается ДО покупки: иначе «почему списалось столько»
        # объяснить будет нечем.
        if fresh.get("price") is not None:
            entry["price"] = fresh["price"]
        entry["state"] = STATE_BUYING
        self.store.save()

        got = await self._run(self.supplier.place,
                              str(entry.get("denomination") or ""), reference)
        if not got.get("ok"):
            entry["state"] = STATE_NEW
            entry["why"] = str(got.get("why"))[:300]
            self.store.save()
            return await self._stop(card, order_id,
                                    f"поставщик отказал: {got.get('why')}",
                                    record=False)

        codes = [str(c) for c in (got.get("codes") or []) if c]
        # Покупка прошла, а годного кода в ответе нет. Причин две, и обе
        # лечатся опросом по ссылке:
        #
        # * IN_PROGRESS — законный ответ: заказ принят, код будет позже;
        # * код пришёл замазанным («****9012»). Поставщик показывает их
        #   целиком только по запросу с `unhide=true`, а его делает как
        #   раз опрос по ссылке.
        #
        # Раньше опрашивали только первый случай, и замазанный код
        # означал бы «ответ без кода» на оплаченном заказе — при том, что
        # код куплен и лежит у поставщика.
        if not codes:
            entry["state"] = STATE_WAIT_CODE
            self.store.save()
            waited = await self._wait_codes(entry, reference)
            if waited is None:
                return Result(False, STATE_WAIT_CODE,
                              "код ещё не пришёл, доведём следующим проходом")
            codes = waited

        if not codes:
            said = str(entry.get("why") or "").strip()
            entry["state"] = STATE_WAIT_CODE
            self.store.save()
            await self.notify(
                f"{card.emoji} {card.title}: ответ без кода.\n"
                f"Заказ #{order_id}. "
                + (f"Причина: {said}\n" if said
                   else "Поставщик не отказал, но кода не прислал.\n")
                + f"Ссылка покупки: {reference}\n"
                f"Деньги могли списаться. Посмотри кабинет по этой ссылке.")
            return Result(False, STATE_WAIT_CODE, said or "ответ без кода")

        return codes

    async def _wait_codes(self, entry: dict, reference: str):
        """Дождаться кода по принятому заказу.

        → коды, [] (терминальный статус без кода = отказ) либо None
        («время вышло, доведём следующим проходом»).
        """
        waited = 0.0
        for step in POLL_STEPS:
            if waited >= POLL_CEILING:
                break
            await asyncio.sleep(step)
            waited += step
            try:
                got = await self._run(self.supplier.by_reference, reference)
            except Exception as e:                   # noqa: BLE001
                logger.info("опрос %s — %s", reference, e)
                continue
            if not got.get("ok"):
                continue
            codes = [str(c) for c in (got.get("codes") or []) if c]
            if codes:
                return codes
            status = str(got.get("status") or "")
            if status in TERMINAL_STATUSES:
                # PARTIALLY_COMPLETED — тоже конец, а не «ещё подождём».
                entry["why"] = f"поставщик закончил заказ статусом {status}"
                self.store.save()
                return []
        return None

    # ------------------------------------------------------------------
    # Служебное
    # ------------------------------------------------------------------

    async def _catalog(self, card: Card, region: str) -> list[Denomination]:
        res = self.catalog_of(card, region)
        return await res if asyncio.iscoroutine(res) else res

    @staticmethod
    async def _run(fn, *args):
        """Синхронный вызов поставщика — в поток, чтобы не держать цикл."""
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: fn(*args))

    async def _already_closed(self, order_id: str) -> tuple:
        """Заказ закрылся, пока мы собирались? → (да/нет, статус).

        Неуверенность решается в пользу покупателя: не дозвонились до
        площадки, сделки не нашли, метода нет — считаем, что заказ ждёт.
        Он был оплачен секунду назад, и оставить человека без кода из-за
        оборванного запроса хуже, чем купить лишний раз.
        """
        get_order = getattr(self.market, "get_order", None)

        if not callable(get_order):
            return False, ""

        try:
            fresh = await get_order(str(order_id))
        except Exception as e:                       # noqa: BLE001
            logger.warning("заказ %s перечитать не вышло: %s", order_id, e)
            return False, ""

        if fresh is None:
            return False, ""

        if self.market.is_paid(fresh.status):
            return False, str(fresh.status)

        return True, str(fresh.status)

    def _close(self, card: Card, order_id: str) -> None:
        """Пометить заказ закрытым: больше к нему не возвращаемся."""
        closed = self.store.conf(card.slug).setdefault("closed", [])

        if str(order_id) not in [str(x) for x in closed]:
            closed.append(str(order_id))
            self.store.save()

    async def _money_alarm(self, why: str) -> None:
        """Сказать про пустой счёт у поставщика — с суммой и не каждый раз.

        Это не поломка бота и не ошибка объявления: деньги кончились, и
        починить может только продавец. Поэтому говорим отдельно от
        «выдача не пошла», называем баланс и сразу — что будет дальше:
        заказы никуда не денутся, бот доведёт их сам, как только деньги
        появятся.
        """
        now = time.time()

        if now - self._money_at < MONEY_EVERY:
            return

        self._money_at = now
        balance = ""

        try:
            accounts = getattr(self.supplier, "accounts", None)

            if callable(accounts):
                balance = balance_line(await self._run(accounts))
        except Exception as e:                       # noqa: BLE001
            logger.warning("баланс поставщика не прочитан: %s", e)

        await self.notify(
            "💸 На счёте у поставщика не хватает денег — коды не покупаются."
            + (f"\n\nСейчас на счетах: {balance}." if balance else "")
            + "\n\nПополните кабинет AppRoute. Оплаченные заказы никуда не "
              "денутся: бот пробует их каждую минуту и выдаст сам, как "
              "только деньги появятся."
            + (f"\n\nОтвет поставщика: {why}" if why else ""))

    async def _stop(self, card: Card, order_id: str, why: str,
                    record: bool = True, once: bool = True) -> Result:
        """Сказать продавцу, почему выдачи не будет.

        «Ничего не произошло» без причины — самая дорогая тишина в такой
        системе: покупатель ждёт оплаченный заказ, а продавец не знает.

        Но и повторять одно и то же каждую минуту нельзя. Заказ, по
        которому выдача не пошла, пробуется снова каждым проходом — иначе
        случайный сбой (каталог не прочитался, поставщик моргнул) навсегда
        оставлял бы оплаченный заказ без кода. А раз пробуется снова, то и
        отказ приходит снова, и одно объявление без региона засыпало бы
        продавца одинаковыми сообщениями до конца дня.
        
        Поэтому про одну и ту же причину по одному и тому же заказу
        говорим один раз. Изменилась причина — скажем снова: это уже
        новость. Память живёт в процессе: после перезапуска стоит
        напомнить.
        """
        if is_money_problem(why):
            # Про деньги говорим своим сообщением и один раз на всех, а не
            # по письму на каждый ждущий заказ.
            await self._money_alarm(why)
            logger.info("%s: заказ %s — %s", card.slug, order_id, why)

            return Result(False, "", why)

        key = (str(order_id), str(why))

        if not once or key not in self._told:
            self._told.add(key)
            await self.notify(f"{card.emoji} {card.title}: выдача не пошла.\n"
                              f"Заказ #{order_id}.\nПричина: {why}")

        logger.info("%s: заказ %s — %s", card.slug, order_id, why)
        return Result(False, "", why)
