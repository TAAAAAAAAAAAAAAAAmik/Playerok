"""Тесты восстановления как целого. Ни площадки, ни сети — подделки.

Главное здесь: попытка ровно одна. Повтор каждые две минуты копит
черновики, шлёт одно и то же сообщение и в худшем случае плодит
пересозданные товары.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import restore                                                  # noqa: E402
import restore_bot                                              # noqa: E402


class Status:
    def __init__(self, price):
        self.id, self.price, self.name = f"st-{price}", price, "статус"
        self.period = 30


class Item:
    def __init__(self, item_id="i-1", name="80 Robux",
                 may_be_published=True):
        self.id = item_id
        self.name = name
        self.may_be_published = may_be_published
        self.raw_price = 149


class Account:
    """Площадка-пустышка: считает, сколько раз её дёрнули."""

    def __init__(self, item=None, readable=True, statuses=None,
                 publish_fails=False, publish_error=None):
        self.item = item or Item()
        self.readable = readable
        self.statuses = statuses if statuses is not None else [Status(0)]
        self.publish_fails = publish_fails
        self.publish_error = publish_error
        self.reads = 0
        self.published = []

    def get_item(self, id=None):                               # noqa: A002
        self.reads += 1

        if not self.readable:
            raise RuntimeError("товар не прочитался")

        return self.item

    def get_item_priority_statuses(self, item_id, price):
        return self.statuses

    def publish_item(self, item_id, status_id):
        if self.publish_error:
            raise RuntimeError(self.publish_error)

        if self.publish_fails:
            raise RuntimeError("площадка отказала")

        self.published.append(item_id)


class OneAttemptTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state", "restored.json")
        self.handled = restore.Handled(self.path)

    def test_successful_restore_is_not_repeated(self):
        account = Account()
        first = restore_bot.handle(account, Item(), self.handled)
        second = restore_bot.handle(account, Item(), self.handled)

        self.assertIn("выставлен заново", first)
        self.assertIsNone(second)
        self.assertEqual(account.published, ["i-1"])

    def test_failed_restore_is_not_retried(self):
        """То, ради чего всё это: одна попытка."""
        account = Account(publish_fails=True)
        first = restore_bot.handle(account, Item(), self.handled)

        self.assertIn("не вышло", first)

        for _ in range(5):
            self.assertIsNone(restore_bot.handle(account, Item(),
                                                 self.handled))

    def test_failure_says_it_will_not_try_again(self):
        """Иначе продавец будет ждать, что бот справится сам."""
        account = Account(publish_fails=True)
        told = restore_bot.handle(account, Item(), self.handled)

        self.assertIn("Черновики", told)

    def test_unreadable_item_is_not_pestered_forever(self):
        """Прежде такой товар дёргался каждые две минуты, и молча."""
        account = Account(readable=False)
        told = restore_bot.handle(account, Item(), self.handled)

        self.assertIn("не прочитался", told)
        self.assertEqual(account.reads, 1)

        restore_bot.handle(account, Item(), self.handled)

        self.assertEqual(account.reads, 1)

    def test_paid_only_statuses_do_not_spend_money(self):
        """Восстановление идёт само — платный статус тут покупать нельзя."""
        account = Account(statuses=[Status(99)])
        told = restore_bot.handle(account, Item(), self.handled)

        self.assertEqual(account.published, [])
        self.assertIn("вручную", told)

    def test_memory_survives_a_restart(self):
        account = Account(publish_fails=True)
        restore_bot.handle(account, Item(), self.handled)

        self.assertIsNone(restore_bot.handle(account, Item(),
                                             restore.Handled(self.path)))


class TooFastTest(unittest.TestCase):
    """«Слишком много попыток» — не отказ, а просьба подождать. Записать
    такое в вечные неудачи значит бросить товар из-за минутной заминки."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state", "restored.json")
        self.handled = restore.Handled(self.path)

    def test_rate_limit_is_recognised(self):
        self.assertTrue(restore.try_later(
            "Слишком много попыток, пожалуйста, попробуйте повторить запрос позже"))
        self.assertTrue(restore.try_later("429 Too Many Requests"))
        self.assertTrue(restore.try_later("площадка просит сбавить темп"))

    def test_real_refusals_are_not_mistaken_for_it(self):
        """Иначе бот вечно ходил бы по кругу с тем, что не починится."""
        self.assertFalse(restore.try_later("бесплатного статуса нет"))
        self.assertFalse(restore.try_later("товар не найден"))
        self.assertFalse(restore.try_later(""))

    def test_item_is_not_written_off_after_a_rate_limit(self):
        account = Account(publish_error="Слишком много попыток")

        with self.assertRaises(restore_bot.TooFast):
            restore_bot.handle(account, Item(), self.handled)

        self.assertNotIn("i-1", self.handled)

    def test_the_item_is_restored_on_a_later_pass(self):
        """То, ради чего всё это: заминка не должна стоить товара."""
        account = Account(publish_error="Слишком много попыток")

        with self.assertRaises(restore_bot.TooFast):
            restore_bot.handle(account, Item(), self.handled)

        account.publish_error = None
        told = restore_bot.handle(account, Item(), self.handled)

        self.assertIn("выставлен заново", told)
        self.assertEqual(account.published, ["i-1"])

    def test_a_genuine_failure_is_still_one_attempt(self):
        account = Account(publish_fails=True)
        restore_bot.handle(account, Item(), self.handled)

        self.assertIn("i-1", self.handled)
        self.assertIsNone(restore_bot.handle(account, Item(), self.handled))


if __name__ == "__main__":
    unittest.main()


class BoostPassTest(unittest.TestCase):
    """Проход поднятия: что поднимается, что не поднимается и где
    останавливаются деньги."""

    class Store:
        def __init__(self, conf=None):
            self.data = {"boost": conf or {}}
            self.saves = 0

        def shared(self):
            return self.data

        def save(self):
            self.saves += 1

    class Account:
        def __init__(self, statuses=None, items=None, fails=""):
            self.statuses = statuses if statuses is not None else [Status(0)]
            self.items = items
            self.fails = fails
            self.lifted = []

        def get_my_items(self, statuses=None, count=24, **kw):
            rows = self.items

            if rows is None:
                rows = [Item("i1", "80 робуксов"), Item("i2", "100 робуксов")]

            return type("P", (), {"items": rows})()

        def get_item_priority_statuses(self, item_id, price):
            return self.statuses

        def increase_item_priority_status(self, item_id, status_id, **kw):
            if self.fails:
                raise RuntimeError(self.fails)

            self.lifted.append((item_id, status_id))

    NOW = 1_700_000_000.0

    def test_nothing_happens_while_it_is_off(self):
        account = self.Account()
        said = restore_bot.boost_pass(account, self.Store({"on": False}),
                                      now=self.NOW)

        self.assertEqual(account.lifted, [])
        self.assertEqual(said, "")

    def test_the_free_status_lifts_everything_silently(self):
        """Поднятие идёт каждые несколько часов — письмо о каждом стало бы
        шумом, в котором потеряется важное."""
        account = self.Account()
        said = restore_bot.boost_pass(account, self.Store({"on": True}),
                                      now=self.NOW)

        self.assertEqual(len(account.lifted), 2)
        self.assertEqual(said, "")

    def test_a_paid_status_is_not_bought_by_itself(self):
        account = self.Account(statuses=[Status(300)])
        store = self.Store({"on": True})
        said = restore_bot.boost_pass(account, store, now=self.NOW)

        self.assertEqual(account.lifted, [])
        self.assertIn("не разрешено", said)

    def test_the_refusal_is_said_once_not_per_item(self):
        account = self.Account(statuses=[Status(300)])
        said = restore_bot.boost_pass(account, self.Store({"on": True}),
                                      now=self.NOW)

        self.assertEqual(said.count("не разрешено"), 1)

    def test_an_allowed_paid_status_is_bought_within_the_ceiling(self):
        account = self.Account(statuses=[Status(15)])
        store = self.Store({"on": True, "paid": True, "limit": 20,
                            "max_price": 20})
        said = restore_bot.boost_pass(account, store, now=self.NOW)

        # Второго не будет: 15 + 15 не помещается в потолок 20 ₽.
        self.assertEqual(len(account.lifted), 1)
        self.assertIn("15 ₽", said)

    def test_what_was_lifted_is_remembered(self):
        account = self.Account()
        store = self.Store({"on": True})
        restore_bot.boost_pass(account, store, now=self.NOW)
        again = restore_bot.boost_pass(account, store, now=self.NOW + 60)

        self.assertEqual(len(account.lifted), 2)
        self.assertEqual(again, "")
        self.assertTrue(store.saves)

    def test_a_refusal_of_the_marketplace_is_named(self):
        account = self.Account(fails="площадка отказала")
        said = restore_bot.boost_pass(account, self.Store({"on": True}),
                                      now=self.NOW)

        self.assertIn("отказала", said)

    def test_too_fast_stops_the_pass(self):
        """Площадка просит сбавить темп — это не отказ, а просьба."""
        account = self.Account(fails="Слишком много попыток")

        with self.assertRaises(restore_bot.TooFast):
            restore_bot.boost_pass(account, self.Store({"on": True}),
                                   now=self.NOW)

    def test_an_empty_showcase_is_not_a_crash(self):
        account = self.Account(items=[])
        said = restore_bot.boost_pass(account, self.Store({"on": True}),
                                      now=self.NOW)

        self.assertEqual(said, "")
