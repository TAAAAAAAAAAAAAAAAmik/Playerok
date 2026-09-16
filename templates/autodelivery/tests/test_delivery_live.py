"""Выдача целиком: настоящий клиент AppRoute внутри настоящего движка.

Отличие от `test_delivery.py` в том, что там поставщик поддельный — он
отвечает так, как нам удобно. Здесь поддельный только HTTP, а конверт,
разбор кодов и идемпотентность работают настоящие, как у AppRoute.

Ровно на таком шве — «каждая половина проверена, стык между ними нет» —
уже пряталась беда с уведомлениями, которые не приходили месяцами.

Как отвечает AppRoute: HTTP почти всегда 200, получилось ли — решает
`statusCode` внутри тела. Успехов три: 0 OK, 1 ACCEPTED (код будет
позже), 2 IDEMPOTENCY_REPLAY (такой заказ уже был). Коды приходят
замазанными, пока не спросишь `unhide=true`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import delivery                                                 # noqa: E402
import supplier as sup                                          # noqa: E402
from catalog import Card, Denomination                          # noqa: E402
from delivery import DeliveryEngine                             # noqa: E402
from playerok import Order                                      # noqa: E402
from store import JsonStore, STATE_DONE                          # noqa: E402

CARD = Card(slug="robux", title="Roblox Gift Cards", emoji="🎮",
            keywords=("robux", "роблокс"), measure="Robux",
            subcategory="Roblox Gift Cards",
            activation="Активируйте код на roblox.com/redeem.")

REAL_CODE = "ABCDE-FGHIJ-KLMNO"


# ---------------------------------------------------------------------------
# Поддельный HTTP, отвечающий как AppRoute
# ---------------------------------------------------------------------------

class Approute:
    """Кабинет поставщика: помнит заказы по ссылке, как настоящий.

    Идемпотентность здесь не декорация: из-за неё повтор после обрыва
    связи не становится вторым списанием, и проверить её надо на том же
    коде, который пойдёт в бой.
    """

    def __init__(self, in_stock=5, price=1.23, code=REAL_CODE,
                 accepted=False, masked=False, refuse=0, refuse_item=0,
                 ready_after=1):
        self.in_stock, self.price, self.code = in_stock, price, code
        self.accepted = accepted        # покупка отвечает «принято, ждите»
        self.masked = masked            # покупка отдаёт замазанный код
        self.refuse = refuse            # statusCode отказа на покупке
        self.refuse_item = refuse_item  # statusCode отказа на номинале
        # Через сколько опросов принятый заказ станет готовым. У живого
        # поставщика код появляется не мгновенно.
        self.ready_after = ready_after
        self.polls: dict = {}
        self.orders: dict = {}          # ссылка → что купили
        self.purchases: list = []       # каждое НАСТОЯЩЕЕ списание
        self.calls: list = []
        self.proxies = {}

    # -- транспорт --

    def request(self, method, url, headers=None, params=None, json=None,
                timeout=None):
        path = url.split("/api/v1", 1)[-1]
        self.calls.append((method, path, params or {}, json or {}))

        if method == "GET" and path == "/services":
            return self._services()

        if method == "GET" and "/items/" in path:
            return self._item()

        if method == "POST" and path == "/orders":
            return self._place(json or {})

        if method == "GET" and path == "/orders":
            return self._orders(params or {})

        raise AssertionError(f"неожиданный запрос: {method} {path}")

    # -- ответы --

    def _wrap(self, code=0, data=None):
        body = {"statusCode": code, "statusMessage": "", "traceId": "tr-1"}

        if data is not None:
            body["data"] = data

        return Reply(body)

    def _services(self):
        return self._wrap(0, {"services": [{
            "id": "svc-gl",
            "name": "Roblox Gift Cards Global",
            "subcategoryName": "Roblox Gift Cards",
            "items": [{"id": "den-1000", "value": 1000,
                       "inStock": self.in_stock, "price": self.price}],
        }]})

    def _item(self):
        if self.refuse_item:
            return self._wrap(self.refuse_item)

        return self._wrap(0, {"inStock": self.in_stock, "price": self.price})

    def _place(self, body):
        if self.refuse:
            return self._wrap(self.refuse)

        reference = str(body.get("referenceId") or "")

        # Та же ссылка — тот же результат и НИ ОДНОГО нового списания.
        if reference in self.orders:
            return self._wrap(2, self.orders[reference])

        self.purchases.append(reference)

        if self.accepted:
            self.orders[reference] = {"status": "IN_PROGRESS", "items": []}

            return self._wrap(1)

        shown = "****" + self.code[-4:] if self.masked else self.code
        self.orders[reference] = {"status": "SUCCESS",
                                  "items": [{"code": self.code}]}

        return self._wrap(0, {"status": "SUCCESS",
                              "items": [{"code": shown}]})

    def _orders(self, params):
        # Настоящий поставщик отвергает `unhide` без фильтра — 422.
        if params.get("unhide") and not params.get("referenceId"):
            return self._wrap(3)

        reference = str(params.get("referenceId") or "")
        found = self.orders.get(reference)

        if found is None:
            return self._wrap(0, {"page": {"items": []}})

        # Принятый заказ доходит до готового не сразу.
        if found["status"] == "IN_PROGRESS":
            self.polls[reference] = self.polls.get(reference, 0) + 1

            if self.polls[reference] >= self.ready_after:
                found = {"status": "SUCCESS",
                         "items": [{"code": self.code}]}
                self.orders[reference] = found

        # Без `unhide` коды замазаны. С ним — целиком.
        items = []

        for row in found.get("items") or []:
            code = row.get("code", "")
            items.append({"code": code if params.get("unhide")
                          else "****" + code[-4:]})

        return self._wrap(0, {"page": {"items": [
            {"status": found["status"], "items": items}]}})


class Reply:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.headers = body, status_code, {}

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# Поддельная площадка
# ---------------------------------------------------------------------------

class Market:
    name = "playerok"

    def __init__(self, orders, send_ok=True):
        self.orders = {o.id: o for o in orders}
        self.sent: list = []
        self.marked: list = []
        self.send_ok = send_ok

    async def paid_orders(self):
        return [o for o in self.orders.values() if self.is_paid(o.status)]

    async def get_order(self, order_id):
        return self.orders.get(str(order_id))

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

        return self.send_ok, "" if self.send_ok else "чат закрыт"

    async def mark_sent(self, order_id):
        self.marked.append(str(order_id))

        return True, "отмечено"

    def is_paid(self, status):
        return str(status).lower() in ("paid", "оплачен")

    def order_url(self, order_id):
        return f"https://playerok.com/deal/{order_id}"


def catalog_of(card, region):
    return [Denomination(service_id="svc-gl", item_id="den-1000", value=1000,
                         title="1000 Robux", price=1.2, in_stock=5,
                         region=region or "GL")]


class Base(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = JsonStore(os.path.join(self.tmp, "state.json"))
        self.store.conf("robux")["enabled"] = True
        self.notes: list = []
        self._steps = delivery.POLL_STEPS
        delivery.POLL_STEPS = (0, 0, 0)

    def tearDown(self):
        delivery.POLL_STEPS = self._steps

    async def notify(self, text):
        self.notes.append(text)

    def build(self, approute, order=None, send_ok=True):
        """Настоящий клиент поставщика на поддельном HTTP."""
        client = sup.ApprouteSupplier(api_key="ключ")
        client.session = approute
        market = Market([order or self.order()], send_ok=send_ok)
        engine = DeliveryEngine(market, client, self.store, [CARD],
                                self.notify, catalog_of,
                                reference_prefix="pk")
        self.market = market

        return engine, market

    async def pass_once(self, engine):
        """Один проход по оплаченным заказам — как в боевом цикле."""
        for order in await self.market.paid_orders():
            await engine.on_paid_order(order)

    @staticmethod
    def order(oid="777", status="paid"):
        return Order(id=oid, title="Roblox 1000 Robux", status=status,
                     chat_id="chat-1", description="Регион кода: GL")

    def entry(self, order_id="777"):
        for row in self.store.conf("robux").get("log") or []:
            if str(row.get("order")) == order_id:
                return row

        return {}


# ---------------------------------------------------------------------------

class HappyPathTest(Base):
    """Оплачен — куплен — отправлен — отмечен."""

    async def test_the_buyer_gets_the_code(self):
        engine, market = self.build(Approute())
        await self.pass_once(engine)

        self.assertEqual(len(market.sent), 1)
        chat, text = market.sent[0]
        self.assertEqual(chat, "chat-1")
        self.assertIn(REAL_CODE, text)
        self.assertIn("roblox.com/redeem", text)

    async def test_the_order_is_marked_delivered(self):
        engine, market = self.build(Approute())
        await self.pass_once(engine)

        self.assertEqual(market.marked, ["777"])
        self.assertEqual(self.entry()["state"], STATE_DONE)
        self.assertIn("777", self.store.conf("robux")["delivered"])

    async def test_the_nominal_is_re_read_before_paying(self):
        """Замена сухому прогону, которого для магазина не существует:
        каталог кешируется, и «в наличии» в нём может быть вчерашним."""
        approute = Approute()
        engine, _ = self.build(approute)
        await self.pass_once(engine)
        paths = [path for _, path, _, _ in approute.calls]

        self.assertIn("/services/svc-gl/items/den-1000", paths)
        self.assertLess(paths.index("/services/svc-gl/items/den-1000"),
                        paths.index("/orders"))

    async def test_money_is_spent_exactly_once(self):
        approute = Approute()
        engine, _ = self.build(approute)
        await self.pass_once(engine)

        self.assertEqual(len(approute.purchases), 1)


class AcceptedTest(Base):
    """«Принято, код будет позже» — законный ответ, а не отказ."""

    async def test_the_code_is_waited_for_and_delivered(self):
        engine, market = self.build(Approute(accepted=True))
        await self.pass_once(engine)

        self.assertTrue(market.sent, "код так и не ушёл покупателю")
        self.assertIn(REAL_CODE, market.sent[0][1])

    async def test_waiting_does_not_buy_a_second_time(self):
        approute = Approute(accepted=True)
        engine, _ = self.build(approute)
        await self.pass_once(engine)

        self.assertEqual(len(approute.purchases), 1)


class MaskedCodeTest(Base):
    """Покупка отдала «****KLMNO» — за настоящим надо идти с `unhide`."""

    async def test_the_masked_code_is_never_sent_to_the_buyer(self):
        """Отправить замазанный — это отчёт о выдаче, которой не было."""
        engine, market = self.build(Approute(masked=True))
        await self.pass_once(engine)

        for _, text in market.sent:
            self.assertNotIn("****", text)

    async def test_the_real_code_is_fetched_and_delivered(self):
        engine, market = self.build(Approute(masked=True))
        await self.pass_once(engine)

        self.assertTrue(market.sent, "код так и не ушёл покупателю")
        self.assertIn(REAL_CODE, market.sent[0][1])

    async def test_unhide_is_asked_for_with_a_filter(self):
        """Без фильтра поставщик отвергает `unhide` четыреста двадцать
        вторым."""
        approute = Approute(masked=True)
        engine, _ = self.build(approute)
        await self.pass_once(engine)
        asked = [params for method, path, params, _ in approute.calls
                 if method == "GET" and path == "/orders"]

        self.assertTrue(asked)

        for params in asked:
            self.assertEqual(params.get("unhide"), "true")
            self.assertTrue(params.get("referenceId"))


class IdempotencyTest(Base):
    """Повтор после обрыва не должен стать вторым списанием."""

    async def test_a_second_pass_does_not_buy_again(self):
        approute = Approute()
        engine, market = self.build(approute)
        await self.pass_once(engine)

        # Журнал выдач потерян — худший случай: бот считает заказ новым.
        self.store.conf("robux")["delivered"] = []
        self.store.conf("robux")["log"] = []
        self.store.save()

        await self.pass_once(engine)

        self.assertEqual(len(approute.purchases), 1)
        self.assertIn(REAL_CODE, market.sent[-1][1])

    async def test_the_reference_is_the_same_both_times(self):
        approute = Approute()
        engine, _ = self.build(approute)
        await self.pass_once(engine)
        first = self.entry()["reference"]

        self.store.conf("robux")["delivered"] = []
        self.store.conf("robux")["log"] = []
        self.store.save()
        await self.pass_once(engine)

        self.assertEqual(self.entry()["reference"], first)
        self.assertEqual(approute.purchases, [first])


class RefusalTest(Base):
    """Отказ поставщика доходит до продавца — своими словами."""

    async def test_no_right_to_buy_is_named_plainly(self):
        """Без orders:write ключ покупает, но кода не отдаёт."""
        engine, market = self.build(Approute(refuse=5))
        await self.pass_once(engine)

        self.assertEqual(market.sent, [])
        self.assertTrue(any("orders:write" in n for n in self.notes))

    async def test_no_money_at_the_supplier_is_named_plainly(self):
        engine, _ = self.build(Approute(refuse=10))
        await self.pass_once(engine)

        self.assertTrue(any("не хватает денег" in n for n in self.notes))

    async def test_a_nominal_that_ran_out_is_not_bought(self):
        approute = Approute(in_stock=0)
        engine, market = self.build(approute)
        await self.pass_once(engine)

        self.assertEqual(approute.purchases, [])
        self.assertEqual(market.sent, [])
        self.assertTrue(any("кончил" in n for n in self.notes))

    async def test_a_refused_order_is_not_marked_delivered(self):
        engine, market = self.build(Approute(refuse=9))
        await self.pass_once(engine)

        self.assertEqual(market.marked, [])
        self.assertNotIn("777", self.store.conf("robux")["delivered"])


class BoughtButNotSentTest(Base):
    """Код куплен, чат закрыт — самое дорогое из состояний."""

    async def test_the_code_is_written_down_before_sending(self):
        engine, _ = self.build(Approute(), send_ok=False)
        await self.pass_once(engine)

        self.assertEqual(self.entry()["codes"], [REAL_CODE])

    async def test_the_seller_is_told_the_code_itself(self):
        """Деньги списаны, покупатель без кода. Молчать тут нельзя."""
        engine, _ = self.build(Approute(), send_ok=False)
        await self.pass_once(engine)

        self.assertTrue(any(REAL_CODE in n for n in self.notes))

    async def test_it_is_not_bought_again_on_the_next_pass(self):
        approute = Approute()
        engine, _ = self.build(approute, send_ok=False)
        await self.pass_once(engine)
        await self.pass_once(engine)

        self.assertEqual(len(approute.purchases), 1)


class RetryTest(Base):
    """Случайный сбой не должен навсегда оставлять оплаченный заказ."""

    async def test_a_hiccup_heals_itself_on_the_next_pass(self):
        """Поставщик моргнул — и заказ должен пройти следующим проходом,
        а не ждать перезапуска бота."""
        approute = Approute(refuse_item=11)      # UPSTREAM_ERROR
        engine, market = self.build(approute)
        await self.pass_once(engine)

        self.assertEqual(market.sent, [])

        approute.refuse_item = 0                 # поставщик очнулся
        await self.pass_once(engine)

        self.assertIn(REAL_CODE, market.sent[0][1])

    async def test_a_nominal_that_came_back_in_stock_is_delivered(self):
        approute = Approute(in_stock=0)
        engine, market = self.build(approute)
        await self.pass_once(engine)
        approute.in_stock = 5
        await self.pass_once(engine)

        self.assertTrue(market.sent)
        self.assertEqual(len(approute.purchases), 1)

    async def test_the_same_refusal_is_told_once_not_every_pass(self):
        """Одно объявление без региона засыпало бы продавца одинаковыми
        сообщениями до конца дня."""
        engine, _ = self.build(Approute(in_stock=0))

        for _ in range(5):
            await self.pass_once(engine)

        self.assertEqual(len(self.notes), 1)

    async def test_a_new_reason_is_told_again(self):
        """Причина изменилась — это уже новость."""
        approute = Approute(in_stock=0)
        engine, _ = self.build(approute)
        await self.pass_once(engine)

        approute.in_stock = 5
        approute.refuse = 10                     # кончились деньги
        await self.pass_once(engine)

        self.assertEqual(len(self.notes), 2)
        self.assertIn("кончил", self.notes[0])
        self.assertIn("не хватает денег", self.notes[1])

    async def test_a_delivered_order_is_never_touched_again(self):
        approute = Approute()
        engine, market = self.build(approute)
        await self.pass_once(engine)
        await self.pass_once(engine)
        await self.pass_once(engine)

        self.assertEqual(len(market.sent), 1)
        self.assertEqual(len(approute.purchases), 1)


class GarbageCodeTest(Base):
    """Посторонний «код» из ответа не должен уйти покупателю."""

    async def test_a_currency_is_not_delivered_as_a_code(self):
        class WithCurrency(Approute):
            def _place(self, body):
                reference = str(body.get("referenceId") or "")
                self.purchases.append(reference)
                self.orders[reference] = {"status": "SUCCESS",
                                          "items": [{"code": self.code}]}

                return self._wrap(0, {
                    "currency": {"code": "USD"},
                    "region": {"code": "RU"},
                    "status": "SUCCESS",
                    "items": [{"code": self.code}]})

        engine, market = self.build(WithCurrency())
        await self.pass_once(engine)
        text = market.sent[0][1]

        self.assertIn(REAL_CODE, text)
        self.assertNotIn("USD", text)
        self.assertNotIn("\nRU\n", text)

    async def test_a_refusal_word_is_never_delivered_as_a_code(self):
        """Худший случай: покупатель получает «OUT_OF_STOCK» вместо кода,
        а заказ отмечается выданным."""
        class OnlyStatus(Approute):
            def _place(self, body):
                reference = str(body.get("referenceId") or "")
                self.purchases.append(reference)
                self.orders[reference] = {"status": "CANCELLED", "items": []}

                return self._wrap(0, {"status": "CANCELLED",
                                      "items": [{"code": "OUT_OF_STOCK"}]})

        engine, market = self.build(OnlyStatus())
        await self.pass_once(engine)

        self.assertEqual(market.sent, [])
        self.assertEqual(market.marked, [])


class RealCatalogTest(Base):
    """Настоящий адаптер каталога вместо заготовленного списка.

    Последний непроверенный стык: отбор услуги по подкатегории, кеш и
    ручная привязка живут в `Catalog`, а тесты выше подставляли готовые
    номиналы и этот кусок обходили.
    """

    def build_real(self, approute, pinned=""):
        import example_bot
        from settings import Settings

        client = sup.ApprouteSupplier(api_key="ключ")
        client.session = approute
        conf = Settings(self.store)

        if pinned:
            conf.set_service("robux", "GL", pinned)

        market = Market([self.order()])
        engine = DeliveryEngine(market, client, self.store, [CARD],
                                self.notify,
                                example_bot.Catalog(client, conf),
                                reference_prefix="pk")
        self.market = market

        return engine, market

    async def test_the_nominal_is_found_by_subcategory(self):
        """Номера услуг вписывать вручную больше не нужно — бот находит их
        в каталоге сам."""
        approute = Approute()
        engine, market = self.build_real(approute)
        await self.pass_once(engine)

        self.assertTrue(market.sent, "код не ушёл: номинал не нашёлся")
        self.assertIn(REAL_CODE, market.sent[0][1])

    async def test_the_catalog_is_read_once_for_the_whole_pass(self):
        """`GET /services` разрешён два раза в минуту: без кеша два
        оплаченных заказа подряд выбирают лимит, и третий покупатель ждёт
        минуту ни за что."""
        approute = Approute()
        engine, _ = self.build_real(approute)
        await self.pass_once(engine)
        await self.pass_once(engine)
        reads = [p for m, p, _, _ in approute.calls
                 if m == "GET" and p == "/services"]

        self.assertEqual(len(reads), 1)

    async def test_a_stale_pinned_service_stops_the_delivery_loudly(self):
        """Привязка сильнее поиска по подкатегории. Устаревшая означает
        «номинал не найден» на оплаченном заказе — и сказать об этом надо,
        а не промолчать."""
        engine, market = self.build_real(Approute(), pinned="svc-которой-нет")
        await self.pass_once(engine)

        self.assertEqual(market.sent, [])
        self.assertTrue(self.notes, "продавцу не сказали ни слова")


if __name__ == "__main__":
    unittest.main()
