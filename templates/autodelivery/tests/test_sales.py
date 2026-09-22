"""Тесты разбора сделок площадки. Сети не требуют.

Деньги на площадке живут в трёх состояниях, и путать их дорого:
«подтверждено» — уже ваши, «ждёт подтверждения» — ещё нет, сделку могут
откатить, «возвращено» — их нет и не будет.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import sales                                                   # noqa: E402


class Card:
    def __init__(self, slug):
        self.slug = slug


def card_of(name: str):
    low = str(name).lower()

    if "xbox" in low:
        return Card("xbox")

    if "робукс" in low or "robux" in low:
        return Card("robux")

    return None


class MoneyTest(unittest.TestCase):
    def test_confirmed_money_can_be_taken(self):
        one = sales.Money()
        one.add("CONFIRMED", 900)

        self.assertEqual(one.released, 900)
        self.assertEqual(one.waiting, 0)
        self.assertEqual(one.sold, 900)

    def test_paid_is_not_yet_yours(self):
        """Покупатель не подтвердил — сделку могут откатить."""
        one = sales.Money()
        one.add("PAID", 3000)

        self.assertEqual(one.waiting, 3000)
        self.assertEqual(one.released, 0)

    def test_an_automatic_confirmation_is_a_confirmation(self):
        one = sales.Money()
        one.add("CONFIRMED_AUTOMATICALLY", 500)

        self.assertEqual(one.released, 500)

    def test_a_refund_is_not_a_sale(self):
        one = sales.Money()
        one.add("ROLLED_BACK", 900)

        self.assertEqual(one.sold, 0)
        self.assertEqual(one.refunded, 900)
        self.assertEqual(one.refunds, 1)

    def test_an_unknown_status_is_not_money(self):
        """Придумывать ему место в отчёте о деньгах нельзя."""
        one = sales.Money()
        one.add("СТРАННОЕ", 100)

        self.assertEqual(one.count, 0)
        self.assertEqual(one.sold, 0)

    def test_a_deal_without_a_sum_is_counted_but_not_added(self):
        one = sales.Money()
        one.add("CONFIRMED", None)

        self.assertEqual(one.count, 1)
        self.assertEqual(one.sold, 0)
        self.assertEqual(one.unknown, 1)

    def test_the_status_may_come_as_an_enum(self):
        one = sales.Money()
        one.add(type("S", (), {"name": "CONFIRMED"})(), 100)

        self.assertEqual(one.released, 100)

    def test_the_case_does_not_matter(self):
        one = sales.Money()
        one.add("confirmed", 100)

        self.assertEqual(one.released, 100)


class ByCardTest(unittest.TestCase):
    ROWS = [("Xbox Gift Card 100 TR", "CONFIRMED", 900),
            ("Xbox Gift Card 500 TR", "PAID", 3000),
            ("🥳 ПРОМОКОДОМ робуксы", "CONFIRMED", 119),
            ("🏆 ПОПУЛЯРНЫЙ ПАКЕТ 3 LVL", "CONFIRMED", 2500)]

    def test_each_card_gets_its_own(self):
        got = sales.by_card(self.ROWS, card_of)

        self.assertEqual(got["xbox"].sold, 3900)
        self.assertEqual(got["robux"].sold, 119)

    def test_strangers_are_kept_apart(self):
        """Чужие товары — тоже деньги продавца, но не наши карты."""
        got = sales.by_card(self.ROWS, card_of)

        self.assertEqual(got[""].sold, 2500)

    def test_released_and_waiting_do_not_mix(self):
        got = sales.by_card(self.ROWS, card_of)

        self.assertEqual(got["xbox"].released, 900)
        self.assertEqual(got["xbox"].waiting, 3000)

    def test_the_total_adds_everything(self):
        whole = sales.total_of(sales.by_card(self.ROWS, card_of))

        self.assertEqual(whole.sold, 6519)
        self.assertEqual(whole.count, 4)

    def test_nothing_at_all(self):
        self.assertEqual(sales.by_card([], card_of), {})


if __name__ == "__main__":
    unittest.main()
