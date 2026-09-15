"""Тесты цен серии. Сети не требуют.

Ошибка здесь ставит неверную цену сразу на десяток объявлений: продавать
ниже задуманного он будет молча, пока не сведёт выручку.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import pricing                                                  # noqa: E402
from catalog import Denomination                                # noqa: E402


def row(value, price, stock=5, region="GL", item_id="i"):
    return Denomination(service_id="s", item_id=item_id, value=value,
                        title=f"{value:g}", price=price, in_stock=stock,
                        region=region)


class SheetTest(unittest.TestCase):
    def test_a_list_to_fill_in(self):
        self.assertEqual(pricing.sheet([100, 200]), "100 =\n200 =")

    def test_prices_are_shown_when_there_are_any(self):
        self.assertEqual(pricing.sheet([(100, 150), (200, 280)]),
                         "100 = 150\n200 = 280")

    def test_an_empty_price_is_not_a_zero(self):
        """Ноль — это настоящая цена, которую площадка примет: товар уйдёт
        даром."""
        self.assertEqual(pricing.sheet([(100, None)]).partition("=")[2],
                         "")

    def test_the_sheet_is_what_the_series_parser_reads_back(self):
        import series

        rows, bad = series.parse(pricing.sheet([(100, 150), (200, 280)]))

        self.assertEqual(rows, [(100, 150), (200, 280)])
        self.assertEqual(bad, [])


class SuggestTest(unittest.TestCase):
    def test_purchase_price_becomes_a_selling_price(self):
        got = pricing.suggest([(100, 1.0)], rate=100, markup=0, step=1)

        self.assertEqual(got, [(100, 100)])

    def test_the_markup_is_applied(self):
        got = pricing.suggest([(100, 1.0)], rate=100, markup=40, step=1)

        self.assertEqual(got, [(100, 140)])

    def test_rounding_goes_up_not_down(self):
        """Вниз — значит местами продавать ниже задуманного, а разницу
        продавец заметит не сразу: она прячется в копейках на заказе."""
        got = pricing.suggest([(100, 1.01)], rate=100, markup=0, step=10)

        self.assertEqual(got, [(100, 110)])

    def test_a_nominal_without_a_purchase_price_keeps_an_empty_price(self):
        """Пропустить его нельзя — номинал исчез бы из списка молча."""
        got = pricing.suggest([(100, None)], rate=100, markup=40)

        self.assertEqual(got, [(100, None)])


class CostsFromTest(unittest.TestCase):
    def test_nominals_are_sorted_and_deduplicated(self):
        got = pricing.costs_from([row(200, 2.0), row(100, 1.0)])

        self.assertEqual([n for n, _ in got], [100, 200])

    def test_the_cheapest_copy_wins(self):
        """Тот же номинал бывает у нескольких услуг, и выдача купит самый
        дешёвый — цену считаем по нему же."""
        got = pricing.costs_from([row(100, 2.0, item_id="a"),
                                  row(100, 1.1, item_id="b")])

        self.assertEqual(got, [(100, 1.1)])

    def test_what_is_out_of_stock_is_not_offered(self):
        """Объявление по такому номиналу бот выдать не сможет, а покупатель
        заплатит и будет ждать."""
        got = pricing.costs_from([row(100, 1.0, stock=0)])

        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
