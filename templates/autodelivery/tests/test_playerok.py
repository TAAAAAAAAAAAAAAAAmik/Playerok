"""Тесты адаптера PlayerOK на поддельном аккаунте. Сети не требуют.

Проверяется то, что на этой площадке стоит денег: отбор оплаченных сделок,
дочитывание страниц, чат отдельно от номера заказа и перевод ошибок.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from playerok import PlayerokMarketplace, _explain             # noqa: E402


class FakeStatus:
    """Подделка Enum библиотеки: у неё есть .name, как у настоящего."""

    def __init__(self, name: str):
        self.name = name


class FakeItem:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class FakeChat:
    def __init__(self, chat_id: str):
        self.id = chat_id


class FakeUser:
    def __init__(self, username: str):
        self.username = username


class FakeDeal:
    def __init__(self, deal_id, status, title="", description="",
                 chat_id="", buyer=""):
        self.id = deal_id
        self.status = FakeStatus(status)
        self.item = FakeItem(title, description)
        self.chat = FakeChat(chat_id) if chat_id else None
        self.user = FakeUser(buyer)
        self.transaction = None


class FakePage:
    def __init__(self, deals, has_next_page=False, end_cursor=None):
        self.deals = deals
        self.has_next_page = has_next_page
        self.end_cursor = end_cursor


class FakeAccount:
    """Поддельный аккаунт площадки: отдаёт заранее заданные страницы.

    Умеет и одиночную сделку: у настоящей площадки список и одна сделка —
    разные запросы с разными наборами полей, и чата в списке нет.
    """

    def __init__(self, pages=None, send_error=None, single=None):
        self.pages = pages or [FakePage([])]
        self.send_error = send_error
        self.single = single or {}
        self.calls = []
        self.single_calls = []
        self.sent = []

    def get_deal(self, deal_id):
        self.single_calls.append(deal_id)

        if deal_id not in self.single:
            raise RuntimeError("нет такой сделки")

        return self.single[deal_id]

    def get_deals(self, direction=None, count=24, after_cursor=None):
        self.calls.append(after_cursor)
        index = 0

        if after_cursor is not None:
            for position, page in enumerate(self.pages):
                if page.end_cursor == after_cursor:
                    index = position + 1
                    break

        return self.pages[index] if index < len(self.pages) else FakePage([])

    def send_message(self, chat_id, text):
        if self.send_error is not None:
            raise self.send_error

        self.sent.append((chat_id, text))
        return object()


def run(coro):
    return asyncio.run(coro)


class PaidStatusTest(unittest.TestCase):
    """Самый дорогой метод: ошибка здесь отдаёт коды бесплатно."""

    def setUp(self):
        self.market = PlayerokMarketplace(FakeAccount())

    def test_money_received_state_is_paid(self):
        # Деньги у продавца, товар ещё не отдан.
        self.assertTrue(self.market.is_paid("PAID"))

    def test_pending_is_deliberately_not_paid(self):
        """PENDING выглядит оплаченным, но достоверности нет.

        Вторая независимая библиотека берёт только PAID. Пока живой заказ
        не показал обратного, лишний статус здесь опаснее недостающего:
        он отдаёт коды бесплатно и необратимо.
        """
        self.assertFalse(self.market.is_paid("PENDING"))

    def test_already_delivered_states_are_not_paid(self):
        # Товар уже отдан — выдавать второй раз нельзя.
        self.assertFalse(self.market.is_paid("SENT"))
        self.assertFalse(self.market.is_paid("CONFIRMED"))
        self.assertFalse(self.market.is_paid("CONFIRMED_AUTOMATICALLY"))

    def test_rolled_back_is_not_paid(self):
        # Деньги вернули покупателю.
        self.assertFalse(self.market.is_paid("ROLLED_BACK"))

    def test_unknown_status_is_not_paid(self):
        # Незнакомый статус — не повод отдавать товар.
        self.assertFalse(self.market.is_paid("SOMETHING_NEW"))
        self.assertFalse(self.market.is_paid(""))
        self.assertFalse(self.market.is_paid(None))

    def test_status_enum_is_read_by_name(self):
        # Библиотека отдаёт Enum, а не строку.
        self.assertTrue(self.market.is_paid(FakeStatus("PAID")))
        self.assertFalse(self.market.is_paid(FakeStatus("CONFIRMED")))

    def test_status_case_and_spaces_do_not_matter(self):
        self.assertTrue(self.market.is_paid(" paid "))


class PaidOrdersTest(unittest.TestCase):
    def test_only_paid_deals_are_returned(self):
        account = FakeAccount([FakePage([
            FakeDeal("1", "PAID", chat_id="c1"),
            FakeDeal("2", "CONFIRMED", chat_id="c2"),
            FakeDeal("3", "PENDING", chat_id="c3"),   # не оплачен для нас
            FakeDeal("4", "ROLLED_BACK", chat_id="c4"),
        ])])
        orders = run(PlayerokMarketplace(account).paid_orders())

        self.assertEqual([o.id for o in orders], ["1"])

    def test_all_pages_are_read(self):
        """Первая страница — это не «все»."""
        account = FakeAccount([
            FakePage([FakeDeal("1", "PAID", chat_id="c1")], True, "cur1"),
            FakePage([FakeDeal("2", "PAID", chat_id="c2")], True, "cur2"),
            FakePage([FakeDeal("3", "PAID", chat_id="c3")], False, None),
        ])
        orders = run(PlayerokMarketplace(account).paid_orders())

        self.assertEqual([o.id for o in orders], ["1", "2", "3"])
        self.assertEqual(account.calls, [None, "cur1", "cur2"])

    def test_paging_stops_when_platform_never_ends(self):
        """Площадка всегда говорит «есть ещё» — не зацикливаемся."""
        endless = [FakePage([FakeDeal("x", "PAID", chat_id="c")], True, "same")
                   for _ in range(200)]
        orders = run(PlayerokMarketplace(FakeAccount(endless)).paid_orders())

        self.assertLess(len(orders), 200)

    def test_deal_without_id_is_skipped(self):
        account = FakeAccount([FakePage([
            FakeDeal("", "PAID", chat_id="c1"),
            FakeDeal("2", "PAID", chat_id="c2"),
        ])])
        orders = run(PlayerokMarketplace(account).paid_orders())

        self.assertEqual([o.id for o in orders], ["2"])


class ChatIsReadSeparatelyTest(unittest.TestCase):
    """Найдено на живом кабинете: в ответе на «список сделок» поля chat нет,
    и заказ приходит с пустым чатом — то есть код отправить некуда."""

    def test_missing_chat_is_fetched_from_the_single_deal(self):
        account = FakeAccount(
            [FakePage([FakeDeal("d1", "PAID")])],          # в списке чата нет
            single={"d1": FakeDeal("d1", "PAID", chat_id="chat-9")},
        )
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.chat_id, "chat-9")
        self.assertEqual(account.single_calls, ["d1"])

    def test_chat_from_the_list_is_not_refetched(self):
        """Лишний запрос на каждую сделку стоил бы темпа опроса."""
        account = FakeAccount([FakePage([FakeDeal("d1", "PAID", chat_id="c1")])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.chat_id, "c1")
        self.assertEqual(account.single_calls, [])

    def test_only_the_chat_is_taken_from_the_second_answer(self):
        """Остальное уже прочитано из списка; доверять второму ответу
        больше первого без причины незачем."""
        account = FakeAccount(
            [FakePage([FakeDeal("d1", "PAID", title="из списка")])],
            single={"d1": FakeDeal("d1", "PAID", title="из сделки",
                                   chat_id="chat-9")},
        )
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.title, "из списка")
        self.assertEqual(order.chat_id, "chat-9")

    def test_unreachable_deal_leaves_the_order_without_a_chat(self):
        """Заказ остаётся на ручную выдачу — это видно и чинится."""
        account = FakeAccount([FakePage([FakeDeal("d1", "PAID")])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.chat_id, "")

    def test_unpaid_deals_are_not_refetched(self):
        """Дочитываем только отобранные: их единицы."""
        account = FakeAccount([FakePage([
            FakeDeal("d1", "CONFIRMED"),
            FakeDeal("d2", "ROLLED_BACK"),
        ])])
        run(PlayerokMarketplace(account).paid_orders())

        self.assertEqual(account.single_calls, [])


class OrderMappingTest(unittest.TestCase):
    def test_chat_id_is_not_the_order_id(self):
        """Ключевой пункт: номер чата у сделки свой."""
        account = FakeAccount([FakePage([
            FakeDeal("order-77", "PAID", chat_id="chat-42"),
        ])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.id, "order-77")
        self.assertEqual(order.chat_id, "chat-42")
        self.assertNotEqual(order.chat_id, order.id)

    def test_title_and_description_come_from_the_item(self):
        """Из названия читается номинал, из описания — регион."""
        account = FakeAccount([FakePage([
            FakeDeal("1", "PAID", title="Steam 10 USD",
                     description="Регион кода: US", chat_id="c1"),
        ])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.title, "Steam 10 USD")
        self.assertEqual(order.description, "Регион кода: US")

    def test_missing_chat_becomes_empty_not_the_order_id(self):
        """Подставить номер заказа вместо чата — значит отправить код в никуда.

        Здесь сделка недоступна и поштучно, так что чат остаётся пустым —
        и это честнее, чем правдоподобная подстановка.
        """
        account = FakeAccount([FakePage([FakeDeal("1", "PAID")])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.chat_id, "")

    def test_buyer_is_kept_for_the_log(self):
        account = FakeAccount([FakePage([
            FakeDeal("1", "PAID", chat_id="c1", buyer="gulnara"),
        ])])
        order = run(PlayerokMarketplace(account).paid_orders())[0]

        self.assertEqual(order.buyer, "gulnara")


class GetOrderTest(unittest.TestCase):
    def test_found_through_pages_when_single_fetch_is_absent(self):
        account = FakeAccount([
            FakePage([FakeDeal("1", "PAID", chat_id="c1")], True, "cur1"),
            FakePage([FakeDeal("2", "CONFIRMED", chat_id="c2")], False, None),
        ])
        order = run(PlayerokMarketplace(account).get_order("2"))

        # Возобновление ищет заказ любого статуса: решает движок, не адаптер.
        self.assertIsNotNone(order)
        self.assertEqual(order.status, "CONFIRMED")

    def test_missing_order_returns_none(self):
        account = FakeAccount([FakePage([FakeDeal("1", "PAID", chat_id="c1")])])

        self.assertIsNone(run(PlayerokMarketplace(account).get_order("404")))

    def test_empty_id_returns_none(self):
        account = FakeAccount()

        self.assertIsNone(run(PlayerokMarketplace(account).get_order("")))


class SendMessageTest(unittest.TestCase):
    def test_message_reaches_the_chat(self):
        account = FakeAccount()
        sent, why = run(PlayerokMarketplace(account).send_message("chat-1", "код"))

        self.assertTrue(sent)
        self.assertEqual(account.sent, [("chat-1", "код")])
        self.assertTrue(why)

    def test_empty_chat_is_refused_before_the_call(self):
        account = FakeAccount()
        sent, why = run(PlayerokMarketplace(account).send_message("", "код"))

        self.assertFalse(sent)
        self.assertEqual(account.sent, [])
        self.assertIn("чат", why.lower())

    def test_failure_explains_itself_in_russian(self):
        account = FakeAccount(send_error=RuntimeError("HTTP 401 Unauthorized"))
        sent, why = run(PlayerokMarketplace(account).send_message("c1", "код"))

        self.assertFalse(sent)
        self.assertIn("куки", why.lower())


class ExplainTest(unittest.TestCase):
    """Продавец читает журнал: голый код ошибки — это «разбирайся сам»."""

    def test_known_failures_are_translated(self):
        self.assertIn("куки", _explain(RuntimeError("403 Forbidden")).lower())
        self.assertIn("темп", _explain(RuntimeError("429 Too Many Requests")).lower())
        self.assertIn("вовремя", _explain(RuntimeError("Read timed out")).lower())
        self.assertIn("закрыта", _explain(RuntimeError("404 not found")).lower())

    def test_unknown_failure_is_shown_but_labelled(self):
        why = _explain(RuntimeError("странная ошибка"))

        self.assertIn("площадка", why.lower())
        self.assertIn("странная ошибка", why)

    def test_silent_failure_still_says_something(self):
        self.assertTrue(_explain(RuntimeError("")).strip())


class ProtocolTest(unittest.TestCase):
    def test_adapter_satisfies_the_engine_protocol(self):
        from marketplace import Marketplace

        self.assertIsInstance(PlayerokMarketplace(FakeAccount()), Marketplace)

    def test_account_is_required(self):
        with self.assertRaises(ValueError):
            PlayerokMarketplace(None)

    def test_order_url_is_empty_for_empty_id(self):
        market = PlayerokMarketplace(FakeAccount())

        self.assertEqual(market.order_url(""), "")
        self.assertIn("playerok.com", market.order_url("42"))


if __name__ == "__main__":
    unittest.main()
