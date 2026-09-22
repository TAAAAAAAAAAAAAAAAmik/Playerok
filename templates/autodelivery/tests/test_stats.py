"""Тесты подсчёта денег. Сети не требуют.

Отчёт о деньгах врать не имеет права: по нему принимают решения. Поэтому
проверяется не «считает ли», а КАК он ведёт себя там, где посчитать
нельзя: незаконченная покупка, сделка без суммы, закупка в долларах при
продаже в рублях.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import stats                                                   # noqa: E402


def entry(paid=900, price=3.0, state="выдан", currency="USD", done_at=1000.0,
          **extra):
    row = {"order": "o1", "state": state, "paid": paid, "price": price,
           "currency": currency, "done_at": done_at, "at": done_at - 60}
    row.update(extra)

    return row


class Store:
    """Хранилище-пустышка: журнал на карту."""

    def __init__(self, logs):
        self.logs = logs

    def conf(self, slug):
        return {"log": self.logs.get(slug, [])}


class Card:
    def __init__(self, slug, title):
        self.slug, self.title = slug, title


XBOX = Card("xbox", "Xbox")
ROBUX = Card("robux", "Roblox")


class SumTest(unittest.TestCase):
    def test_sales_and_costs_add_up(self):
        one = stats.Sum()
        one.add(entry(paid=900, price=3.1))
        one.add(entry(paid=1200, price=4.0))

        self.assertEqual(one.count, 2)
        self.assertEqual(one.revenue, 2100)
        self.assertAlmostEqual(one.cost["USD"], 7.1)

    def test_the_average_check_ignores_what_it_does_not_know(self):
        """Иначе неизвестная сумма считалась бы нулём и занижала чек."""
        one = stats.Sum()
        one.add(entry(paid=1000))
        one.add(entry(paid=None))

        self.assertEqual(one.average, 1000)
        self.assertEqual(one.unknown_price, 1)

    def test_a_missing_sum_is_not_a_zero(self):
        one = stats.Sum()
        one.add(entry(paid=None))

        self.assertEqual(one.revenue, 0)
        self.assertEqual(one.unknown_price, 1)

    def test_profit_needs_a_rate(self):
        """Сложить рубли с долларами без курса нельзя, а выдумать курс в
        отчёте о деньгах — значит подменить его догадкой."""
        one = stats.Sum()
        one.add(entry(paid=900, price=3.0))

        self.assertIsNone(one.profit(0))
        self.assertEqual(one.profit(100), 900 - 300)

    def test_rubles_at_the_supplier_need_no_rate(self):
        one = stats.Sum()
        one.add(entry(paid=900, price=400, currency="RUB"))

        self.assertEqual(one.profit(0), 500)

    def test_the_margin_is_a_share_of_the_revenue(self):
        one = stats.Sum()
        one.add(entry(paid=1000, price=5.0))

        self.assertAlmostEqual(stats.margin(one, 100), 50.0)

    def test_no_revenue_no_margin(self):
        self.assertIsNone(stats.margin(stats.Sum(), 100))


class SoldTest(unittest.TestCase):
    def test_only_delivered_counts(self):
        """Незаконченная покупка — не продажа: деньги могли не списаться,
        код мог не уйти."""
        rows = [entry(), entry(state="покупаем"), entry(state="отказ")]

        self.assertEqual(len(stats.sold(rows)), 1)

    def test_rubbish_in_the_journal_is_skipped(self):
        self.assertEqual(stats.sold([None, "текст", entry()]).__len__(), 1)


class ByCardTest(unittest.TestCase):
    def setUp(self):
        self.store = Store({
            "xbox": [entry(paid=900, done_at=1000),
                     entry(paid=1200, done_at=2000)],
            "robux": [entry(paid=119, done_at=500)],
        })

    def test_every_card_is_counted(self):
        got = dict((card.slug, one) for card, one
                   in stats.by_card(self.store, [XBOX, ROBUX]))

        self.assertEqual(got["xbox"].count, 2)
        self.assertEqual(got["robux"].revenue, 119)

    def test_the_biggest_goes_first(self):
        got = stats.by_card(self.store, [ROBUX, XBOX])

        self.assertIs(got[0][0], XBOX)

    def test_a_card_without_sales_is_not_shown(self):
        got = stats.by_card(Store({}), [XBOX])

        self.assertEqual(got, [])

    def test_a_period_cuts_the_old_ones_off(self):
        got = stats.by_card(self.store, [XBOX, ROBUX], since=1500)
        whole = stats.total_of(got)

        self.assertEqual(whole.count, 1)
        self.assertEqual(whole.revenue, 1200)

    def test_the_time_of_delivery_wins_over_the_time_of_intent(self):
        """Покупка могла висеть ночь, дожидаясь денег на счёте."""
        store = Store({"xbox": [entry(at=100, done_at=9000)]})
        got = stats.by_card(store, [XBOX], since=5000)

        self.assertEqual(len(got), 1)

    def test_an_old_entry_without_the_delivery_time_still_counts(self):
        row = entry()
        del row["done_at"]
        got = stats.by_card(Store({"xbox": [row]}), [XBOX])

        self.assertEqual(got[0][1].count, 1)


class TotalTest(unittest.TestCase):
    def test_cards_add_up(self):
        store = Store({"xbox": [entry(paid=900, price=3.0)],
                       "robux": [entry(paid=100, price=1.0)]})
        whole = stats.total_of(stats.by_card(store, [XBOX, ROBUX]))

        self.assertEqual(whole.count, 2)
        self.assertEqual(whole.revenue, 1000)
        self.assertEqual(whole.cost["USD"], 4.0)

    def test_currencies_are_kept_apart(self):
        store = Store({"xbox": [entry(price=3.0, currency="USD")],
                       "robux": [entry(price=400, currency="RUB")]})
        whole = stats.total_of(stats.by_card(store, [XBOX, ROBUX]))

        self.assertEqual(sorted(whole.cost), ["RUB", "USD"])


class MoneyTest(unittest.TestCase):
    def test_it_reads_like_money(self):
        self.assertEqual(stats.money(12480), "12 480 ₽")

    def test_kopecks_are_rounded(self):
        self.assertEqual(stats.money(99.6), "100 ₽")

    def test_nothing_is_zero(self):
        self.assertEqual(stats.money(None), "0 ₽")

    def test_costs_carry_their_currency(self):
        got = stats.cost_line({"USD": 38.4, "RUB": 1200})

        self.assertIn("38.4 $", got)
        self.assertIn("1200 ₽", got)


class ReviewsTest(unittest.TestCase):
    class Review:
        def __init__(self, rating, text="", who=""):
            self.rating, self.text = rating, text
            self.creator = type("U", (), {"username": who})()

    def test_the_average_is_counted(self):
        got = stats.reviews_of([self.Review(5), self.Review(4)])

        self.assertEqual(got.average, 4.5)
        self.assertEqual(got.count, 2)

    def test_bad_ones_are_counted_apart(self):
        """Именно они стоят денег."""
        got = stats.reviews_of([self.Review(5), self.Review(3),
                                self.Review(1)])

        self.assertEqual(got.bad, 2)

    def test_the_platform_total_wins_over_the_page(self):
        got = stats.reviews_of([self.Review(5)], total=41)

        self.assertEqual(got.total, 41)

    def test_without_a_total_the_page_is_the_total(self):
        self.assertEqual(stats.reviews_of([self.Review(5)]).total, 1)

    def test_the_last_ones_are_kept_with_their_text(self):
        got = stats.reviews_of([self.Review(5, "Спасибо!", "vasya")])

        self.assertEqual(got.last[0], (5, "Спасибо!", "vasya"))

    def test_only_a_few_are_kept(self):
        rows = [self.Review(5) for _ in range(10)]

        self.assertEqual(len(stats.reviews_of(rows, shown=3).last), 3)

    def test_a_review_without_a_rating_is_skipped(self):
        self.assertEqual(stats.reviews_of([self.Review(None)]).count, 0)

    def test_stars_are_drawn(self):
        self.assertEqual(stats.stars(4), "★★★★☆")
        self.assertEqual(stats.stars(4.4), "★★★★☆")
        self.assertEqual(stats.stars("нет"), "")


class UnknownRevenueTest(unittest.TestCase):
    """Известную закупку нельзя вычитать из неизвестной выручки.

    Живой случай: у одиннадцати выдач сумма сделки не записана (их делали
    до того, как бот стал её запоминать), а цена закупки записана всегда.
    Отчёт показал «продано 0 ₽ · профит −2 131 ₽» — убыток, которого не
    было.
    """

    def old(self, price=2.3):
        """Старая выдача: закупка известна, сумма сделки — нет."""
        row = entry(price=price)
        del row["paid"]

        return row

    def test_profit_is_not_counted_at_all(self):
        one = stats.Sum()
        one.add(self.old())

        self.assertIsNone(one.profit(84))

    def test_the_revenue_is_not_called_zero(self):
        one = stats.Sum()
        one.add(self.old())

        self.assertEqual(stats.revenue_line(one), "сумма не записана")

    def test_the_reason_is_named(self):
        one = stats.Sum()
        one.add(self.old())

        self.assertIn("не из чего", stats.profit_line(one, 84))

    def test_the_known_ones_are_counted_alone(self):
        """Одна выдача с суммой — профит по ней, а не по всем."""
        one = stats.Sum()
        one.add(self.old())
        one.add(entry(paid=299, price=2.3))

        self.assertAlmostEqual(one.profit(100), 299 - 230)
        self.assertEqual(one.pairs, 1)

    def test_it_says_how_many_it_counted(self):
        one = stats.Sum()
        one.add(self.old())
        one.add(entry(paid=299, price=2.3))

        self.assertIn("по 1 из 2", stats.profit_line(one, 100))

    def test_the_margin_is_counted_from_the_same_revenue(self):
        """Делить прибыль по части выдач на выручку по всем — значит
        занизить её без предупреждения."""
        one = stats.Sum()
        one.add(self.old())
        one.add(entry(paid=200, price=1.0))

        self.assertAlmostEqual(stats.margin(one, 100), 50.0)

    def test_the_cost_is_still_shown_in_full(self):
        """Деньги потрачены — про них молчать нельзя."""
        one = stats.Sum()
        one.add(self.old(price=2.3))
        one.add(entry(paid=299, price=2.3))

        self.assertAlmostEqual(one.cost["USD"], 4.6)

    def test_totals_keep_the_pairs_apart(self):
        store = Store({"xbox": [self.old(), entry(paid=900, price=3.0)],
                       "robux": [self.old()]})
        whole = stats.total_of(stats.by_card(store, [XBOX, ROBUX]))

        self.assertEqual(whole.count, 3)
        self.assertEqual(whole.pairs, 1)
        self.assertEqual(whole.profit(100), 900 - 300)

    def test_a_cost_without_a_price_is_not_a_pair_either(self):
        row = entry(paid=900)
        del row["price"]
        one = stats.Sum()
        one.add(row)

        self.assertIsNone(one.profit(100))
        self.assertEqual(one.unknown_cost, 1)


if __name__ == "__main__":
    unittest.main()
