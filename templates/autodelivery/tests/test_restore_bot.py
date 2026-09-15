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
                 publish_fails=False):
        self.item = item or Item()
        self.readable = readable
        self.statuses = statuses if statuses is not None else [Status(0)]
        self.publish_fails = publish_fails
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


if __name__ == "__main__":
    unittest.main()
