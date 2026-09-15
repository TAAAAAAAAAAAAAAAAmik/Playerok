"""Тесты выбора статуса приоритета. Сети не требуют.

Публикация со статусом приоритета платная: сумма списывается с баланса.
Поэтому проверяется не «удобно ли», а «может ли бот потратить деньги без
явного согласия».
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import listing                                                  # noqa: E402


class FakeStatus:
    def __init__(self, name, price, period=None):
        self.name = name
        self.price = price
        self.period = period


FREE = FakeStatus("Обычный", 0, 30)
CHEAP = FakeStatus("Продвижение", 15, 7)
PREMIUM = FakeStatus("Премиум", 99, 30)


class PriceTest(unittest.TestCase):
    def test_zero_is_free(self):
        self.assertTrue(listing.is_free(FREE))

    def test_any_price_is_not_free(self):
        self.assertFalse(listing.is_free(CHEAP))
        self.assertFalse(listing.is_free(PREMIUM))

    def test_unknown_price_counts_as_paid(self):
        """Принять платный за бесплатный — списать молча. Наоборот — всего
        лишь лишний вопрос."""
        self.assertFalse(listing.is_free(FakeStatus("Странный", "неизвестно")))
        self.assertFalse(listing.is_free(object()))

    def test_missing_price_field_counts_as_paid(self):
        self.assertFalse(listing.is_free(FakeStatus("Без цены", None)))


class OrderTest(unittest.TestCase):
    def test_free_comes_first(self):
        """Промахнуться номером в пользу платного должно быть труднее."""
        rows = listing.ordered([PREMIUM, CHEAP, FREE])

        self.assertIs(rows[0], FREE)
        self.assertIs(rows[-1], PREMIUM)

    def test_empty_list_is_not_a_crash(self):
        self.assertEqual(listing.ordered([]), [])
        self.assertEqual(listing.ordered(None), [])


class PickTest(unittest.TestCase):
    ROWS = [PREMIUM, CHEAP, FREE]          # нарочно вперемешку

    def test_number_picks_from_the_shown_order(self):
        self.assertIs(listing.pick(self.ROWS, "1"), FREE)
        self.assertIs(listing.pick(self.ROWS, "3"), PREMIUM)

    def test_zero_means_do_not_publish(self):
        self.assertIsNone(listing.pick(self.ROWS, "0"))

    def test_empty_answer_publishes_nothing(self):
        """Enter согласием не считается."""
        self.assertIsNone(listing.pick(self.ROWS, ""))
        self.assertIsNone(listing.pick(self.ROWS, "   "))

    def test_junk_publishes_nothing(self):
        """Угадывать намерение там, где списываются деньги, нельзя."""
        self.assertIsNone(listing.pick(self.ROWS, "да"))
        self.assertIsNone(listing.pick(self.ROWS, "премиум"))
        self.assertIsNone(listing.pick(self.ROWS, "1.5"))

    def test_out_of_range_publishes_nothing(self):
        self.assertIsNone(listing.pick(self.ROWS, "4"))
        self.assertIsNone(listing.pick(self.ROWS, "99"))


class ConfirmationTest(unittest.TestCase):
    def test_paid_status_asks_again(self):
        self.assertTrue(listing.needs_confirmation(CHEAP))
        self.assertTrue(listing.needs_confirmation(PREMIUM))

    def test_free_status_does_not(self):
        self.assertFalse(listing.needs_confirmation(FREE))

    def test_only_the_word_counts_as_yes(self):
        self.assertTrue(listing.confirmed("да"))
        self.assertTrue(listing.confirmed("  ДА  "))

    def test_everything_else_is_no(self):
        self.assertFalse(listing.confirmed(""))
        self.assertFalse(listing.confirmed("y"))
        self.assertFalse(listing.confirmed("yes"))
        self.assertFalse(listing.confirmed("ага"))
        self.assertFalse(listing.confirmed("нет"))


class DescribeTest(unittest.TestCase):
    """Продавец решает по этой строке — цена в ней обязана быть видна."""

    def test_free_says_so(self):
        self.assertIn("бесплатно", listing.describe(FREE))

    def test_paid_shows_the_price(self):
        self.assertIn("99", listing.describe(PREMIUM))
        self.assertIn("₽", listing.describe(PREMIUM))

    def test_period_is_shown_when_known(self):
        self.assertIn("30", listing.describe(FREE))

    def test_nameless_status_still_reads(self):
        self.assertTrue(listing.describe(FakeStatus("", 0)).strip())


if __name__ == "__main__":
    unittest.main()
