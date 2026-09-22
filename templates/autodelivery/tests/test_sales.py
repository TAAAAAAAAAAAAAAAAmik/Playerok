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


class SharesTest(unittest.TestCase):
    """Разложить сумму по категориям — прикидка, и она так и называется.

    Площадка держит деньги общей кучей: сколько из доступного к выводу
    пришло с Xbox, а сколько с аккаунтов, она не говорит.
    """

    def found(self):
        rows = [("Xbox 100", "CONFIRMED", 3000),
                ("Xbox 500", "PAID", 5000),
                ("🏆 ПАКЕТ", "CONFIRMED", 1000)]

        def card_with_label(name):
            card = card_of(name)

            if card is None:
                return None

            card.emoji, card.title = "", "Xbox"

            return card

        return sales.by_group(rows, card_with_label, lambda name: "Прочее")

    def test_the_sum_is_split_by_confirmed_sales(self):
        got = sales.shares(self.found(), 12000)
        shares = dict((one.label, round(money)) for _, one, _, money in got)

        self.assertEqual(shares["Xbox"], 9000)
        self.assertEqual(shares["Прочее"], 3000)

    def test_the_shares_add_up_to_the_whole(self):
        got = sales.shares(self.found(), 12000)

        self.assertAlmostEqual(sum(money for _, _, _, money in got), 12000)

    def test_the_biggest_goes_first(self):
        got = sales.shares(self.found(), 12000)

        self.assertEqual(got[0][1].label, "Xbox")

    def test_waiting_can_be_split_too(self):
        got = sales.shares(self.found(), 5000, "waiting")

        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1].label, "Xbox")

    def test_nothing_confirmed_gives_nothing(self):
        """Делить нечего — не делим. Ни на ноль, ни поровну."""
        rows = [("Xbox 100", "PAID", 1000)]
        found = sales.by_group(rows, card_of, lambda name: "Прочее")

        self.assertEqual(sales.shares(found, 500), [])

    def test_no_sales_at_all(self):
        self.assertEqual(sales.shares({}, 1000), [])
        self.assertEqual(sales.shares([], 1000), [])

    def test_zero_to_split_still_shows_the_shares(self):
        """Доли нужны и сами по себе: по ним видно, чем торгуешь."""
        got = sales.shares(self.found(), 0)

        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0][2], 0.75)


if __name__ == "__main__":
    unittest.main()
