"""Тесты разбиения списка объявлений. Сети не требуют.

Продавец с полусотней объявлений выбирает из списка, где половина строк
выглядит одинаково, а на экране телефона видно первые тридцать знаков.
Такой список не помогает выбрать, а мешает.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import grouping                                                 # noqa: E402


class Item:
    def __init__(self, name, price=100):
        self.name, self.price = name, price


HIT = "🏆 ХИТ КАТЕГОРИИ • 25.000.000₽ • 3 LVL — 2219 уровень"
FAST = "🚀 БЫСТРАЯ ПОКУПКА • 30.000.000₽ • 3 LVL — 2219 уровень"
PROMO = "🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎"


class HeadTest(unittest.TestCase):
    """До первого разделителя обычно стоит то, чем товар и отличается."""

    def test_the_part_before_the_separator_is_taken(self):
        self.assertEqual(grouping.head(HIT), "🏆 ХИТ КАТЕГОРИИ")
        self.assertEqual(grouping.head(FAST), "🚀 БЫСТРАЯ ПОКУПКА")

    def test_any_usual_separator_works(self):
        for line in ("Робуксы | 1000", "Робуксы — 1000", "Робуксы · 1000",
                     "Робуксы / 1000", "Робуксы - 1000"):
            self.assertEqual(grouping.head(line), "Робуксы", line)

    def test_a_name_without_separators_is_kept(self):
        self.assertEqual(grouping.head(PROMO), PROMO)

    def test_a_very_long_head_is_trimmed(self):
        """Иначе подпись кучки не влезет в кнопку."""
        self.assertLessEqual(len(grouping.head("я" * 200)),
                             grouping.LABEL_MAX)

    def test_an_empty_name_does_not_break_it(self):
        self.assertEqual(grouping.head(""), "Без названия")
        self.assertEqual(grouping.head(None), "Без названия")


class GroupsTest(unittest.TestCase):
    def setUp(self):
        self.items = ([Item(HIT)] * 4 + [Item(FAST)] * 8 + [Item(PROMO)]
                      + [Item("1000 Robux Global")])

    def test_the_same_head_goes_into_one_pile(self):
        found = dict(grouping.groups(self.items))

        self.assertEqual(len(found["🏆 ХИТ КАТЕГОРИИ"]), 4)
        self.assertEqual(len(found["🚀 БЫСТРАЯ ПОКУПКА"]), 8)

    def test_the_biggest_pile_comes_first(self):
        """Продавец чаще ищет то, чего у него много."""
        labels = [label for label, _ in grouping.groups(self.items)]

        self.assertEqual(labels[0], "🚀 БЫСТРАЯ ПОКУПКА")
        self.assertEqual(labels[1], "🏆 ХИТ КАТЕГОРИИ")

    def test_nothing_is_lost(self):
        total = sum(len(rows) for _, rows in grouping.groups(self.items))

        self.assertEqual(total, len(self.items))

    def test_order_inside_a_pile_is_kept(self):
        """Он пришёл от площадки, где свежие идут первыми."""
        items = [Item(HIT, price=n) for n in range(5)]
        _, rows = grouping.groups(items)[0]

        self.assertEqual([i.price for i in rows], [0, 1, 2, 3, 4])


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.items = [Item(HIT), Item(FAST), Item(PROMO)]

    def test_a_word_finds_what_contains_it(self):
        got = grouping.matching(self.items, "промокодом")

        self.assertEqual([i.name for i in got], [PROMO])

    def test_case_does_not_matter(self):
        self.assertEqual(len(grouping.matching(self.items, "БЫСТРАЯ")), 1)

    def test_an_empty_word_keeps_everything(self):
        self.assertEqual(len(grouping.matching(self.items, "")), 3)


class PageTest(unittest.TestCase):
    """Первая страница — это не «все»: на заказах мы уже обжигались."""

    def setUp(self):
        self.items = list(range(25))

    def test_a_page_is_the_right_size(self):
        shown, more, total = grouping.page(self.items, 0, 12)

        self.assertEqual(shown, list(range(12)))
        self.assertTrue(more)
        self.assertEqual(total, 3)

    def test_the_last_page_says_there_is_no_more(self):
        shown, more, _ = grouping.page(self.items, 2, 12)

        self.assertEqual(shown, [24])
        self.assertFalse(more)

    def test_a_page_past_the_end_shows_the_last_one(self):
        shown, more, _ = grouping.page(self.items, 99, 12)

        self.assertEqual(shown, [24])
        self.assertFalse(more)

    def test_an_empty_list_is_one_empty_page(self):
        shown, more, total = grouping.page([], 0, 12)

        self.assertEqual(shown, [])
        self.assertFalse(more)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
