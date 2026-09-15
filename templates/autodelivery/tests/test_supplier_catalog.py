"""Тесты разбора каталога поставщика. Сети не требуют.

Здесь ошибка стоит денег дважды: пропущенный номинал оставляет заказ без
выдачи, а придуманный покупает не то на настоящие деньги.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from catalog import (denominations_from, find_service,          # noqa: E402
                     items_of, match_denomination)


class ShapeTest(unittest.TestCase):
    """Имена полей у поставщиков разные и меняются между версиями."""

    def test_items_are_found_under_any_known_name(self):
        for field in ("items", "denominations", "nominals", "values"):
            got = items_of({field: [{"id": "i1"}]})

            self.assertEqual(len(got), 1, field)

    def test_unknown_shape_gives_nothing_not_a_crash(self):
        self.assertEqual(items_of({}), [])
        self.assertEqual(items_of(None), [])
        self.assertEqual(items_of({"items": "не список"}), [])

    def test_service_is_found_in_any_wrapper(self):
        service = {"id": "svc-1", "items": []}

        for catalog in ([service],
                        {"services": [service]},
                        {"data": {"items": [service]}},
                        {"page": {"items": [service]}}):
            self.assertIsNotNone(find_service(catalog, "svc-1"), catalog)

    def test_missing_service_is_none(self):
        self.assertIsNone(find_service({"services": []}, "svc-1"))
        self.assertIsNone(find_service({"services": [{"id": "x"}]}, ""))


class DenominationTest(unittest.TestCase):
    def service(self, *items):
        return {"id": "svc-1", "items": list(items)}

    def test_numeric_field_is_used_when_present(self):
        rows = denominations_from(
            self.service({"id": "i1", "value": 1000, "price": 9.9,
                          "inStock": 5}), region="GL")

        self.assertEqual(rows[0].value, 1000)
        self.assertEqual(rows[0].price, 9.9)
        self.assertEqual(rows[0].in_stock, 5)
        self.assertEqual(rows[0].region, "GL")

    def test_value_is_read_from_the_name_when_there_is_no_field(self):
        """У части услуг число живёт только в названии — иначе такой
        номинал не нашёлся бы никогда."""
        rows = denominations_from(
            self.service({"id": "i1", "name": "Roblox 100 Robux (Global)"}))

        self.assertEqual(rows[0].value, 100)

    def test_entry_without_a_value_is_skipped(self):
        """Подставить догадку значило бы купить не то за настоящие деньги."""
        rows = denominations_from(
            self.service({"id": "i1", "name": "подарочная карта"}))

        self.assertEqual(rows, [])

    def test_entry_without_an_id_is_skipped(self):
        """Без номера покупать нечего."""
        rows = denominations_from(self.service({"value": 100}))

        self.assertEqual(rows, [])

    def test_negative_stock_counts_as_none(self):
        """Иначе такой номинал выглядел бы доступным."""
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "inStock": -3}))

        self.assertEqual(rows[0].in_stock, 0)

    def test_stock_field_is_found_under_any_name(self):
        for field in ("inStock", "stock", "quantity", "available", "count"):
            rows = denominations_from(
                self.service({"id": "i1", "value": 100, field: 7}))

            self.assertEqual(rows[0].in_stock, 7, field)

    def test_price_written_with_a_comma_is_understood(self):
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "price": "1,25"}))

        self.assertEqual(rows[0].price, 1.25)

    def test_broken_price_does_not_lose_the_denomination(self):
        """Цена нужна для выбора дешёвого, но без неё номинал всё ещё
        годен — потерять его хуже."""
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "price": "бесплатно"}))

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].price)


class TogetherTest(unittest.TestCase):
    """Разобранный каталог должен годиться подбору номинала как есть."""

    CATALOG = {"services": [{"id": "svc-gl", "items": [
        {"id": "i1", "name": "Roblox 80 Robux", "price": 1.0, "inStock": 4},
        {"id": "i2", "name": "Roblox 100 Robux", "price": 1.2, "inStock": 0},
        {"id": "i3", "name": "Roblox 100 Robux", "price": 1.1, "inStock": 9},
    ]}]}

    def rows(self):
        return denominations_from(find_service(self.CATALOG, "svc-gl"),
                                  "svc-gl", "GL")

    def test_exact_denomination_is_found(self):
        got, why = match_denomination(self.rows(), "GL", 80)

        self.assertEqual(why, "")
        self.assertEqual(got.item_id, "i1")

    def test_out_of_stock_copy_is_skipped_for_the_live_one(self):
        got, _ = match_denomination(self.rows(), "GL", 100)

        self.assertEqual(got.item_id, "i3")

    def test_missing_denomination_lists_what_there_is(self):
        got, why = match_denomination(self.rows(), "GL", 500)

        self.assertIsNone(got)
        self.assertIn("80", why)


if __name__ == "__main__":
    unittest.main()
