"""Тесты восстановления проданных объявлений. Сети не требуют.

Здесь два дорогих места: платный статус приоритета и цикл, который
работает сам. Ошибка в первом тратит деньги без спроса, во втором —
плодит товары.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import restore                                                  # noqa: E402


class FakeItem:
    def __init__(self, may_be_published=True):
        self.may_be_published = may_be_published


class FakeStatus:
    def __init__(self, name, price):
        self.name, self.price = name, price


class PlanTest(unittest.TestCase):
    def test_republishable_item_is_published_again(self):
        self.assertEqual(restore.plan(FakeItem(True)), restore.PUBLISH)

    def test_item_that_cannot_be_republished_is_recreated(self):
        """Площадка не даёт выставить такие повторно."""
        self.assertEqual(restore.plan(FakeItem(False)), restore.RECREATE)

    def test_silence_means_try_the_cheap_way(self):
        """Ошибка сюда даёт понятный отказ, а в другую сторону — лишний
        товар и удалённый старый."""
        self.assertEqual(restore.plan(FakeItem(None)), restore.PUBLISH)
        self.assertEqual(restore.plan(object()), restore.PUBLISH)

    def test_nothing_is_skipped(self):
        self.assertEqual(restore.plan(None), restore.SKIP)


class FreeStatusTest(unittest.TestCase):
    """Восстановление идёт само — платный статус здесь брать нельзя."""

    def test_free_is_found_among_paid(self):
        rows = [FakeStatus("Премиум", 99), FakeStatus("Обычный", 0)]

        self.assertEqual(restore.free_status(rows).name, "Обычный")

    def test_only_paid_means_nothing(self):
        rows = [FakeStatus("Премиум", 99), FakeStatus("Продвижение", 15)]

        self.assertIsNone(restore.free_status(rows))

    def test_unknown_price_is_not_taken_for_free(self):
        self.assertIsNone(restore.free_status([FakeStatus("Странный", None)]))

    def test_empty_list_means_nothing(self):
        self.assertIsNone(restore.free_status([]))
        self.assertIsNone(restore.free_status(None))


class HandledTest(unittest.TestCase):
    """Без памяти сорвавшееся восстановление ходит по кругу и плодит копии."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state", "restored.json")
        self.handled = restore.Handled(self.path)

    def test_remembered_across_restarts(self):
        self.handled.add("item-1")

        self.assertIn("item-1", restore.Handled(self.path))

    def test_unknown_is_not_remembered(self):
        self.assertNotIn("item-2", self.handled)

    def test_adding_twice_is_harmless(self):
        self.handled.add("item-1")
        self.handled.add("item-1")

        self.assertEqual(self.handled.ids.count("item-1"), 1)

    def test_list_does_not_grow_forever(self):
        small = restore.Handled(self.path, limit=3)

        for number in range(10):
            small.add(f"item-{number}")

        self.assertEqual(len(small.ids), 3)
        self.assertIn("item-9", small)
        self.assertNotIn("item-0", small)

    def test_broken_file_is_not_a_crash(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{сломано")

        self.assertEqual(restore.Handled(self.path).ids, [])


if __name__ == "__main__":
    unittest.main()
