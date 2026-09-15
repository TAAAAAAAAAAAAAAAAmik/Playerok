"""Тесты разбора каталога поставщика. Сети не требуют.

Форма ответа `GET /services` живым вызовом не подтверждена, а у этого
поставщика она уже дважды расходилась с его же документацией. Поэтому
разбор терпимый, а тесты закрепляют, насколько именно.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from supplier_ids import rows, text_of                          # noqa: E402


class RowsTest(unittest.TestCase):
    SERVICE = {"id": "svc-1", "name": "Roblox Global"}

    def test_bare_list(self):
        self.assertEqual(rows([self.SERVICE]), [self.SERVICE])

    def test_wrapped_in_services(self):
        self.assertEqual(rows({"services": [self.SERVICE]}), [self.SERVICE])

    def test_wrapped_in_data_items(self):
        self.assertEqual(rows({"data": {"items": [self.SERVICE]}}),
                         [self.SERVICE])

    def test_wrapped_in_page_items(self):
        """Так завёрнут ответ на чтение заказов — каталог может так же."""
        self.assertEqual(rows({"page": {"items": [self.SERVICE]}}),
                         [self.SERVICE])

    def test_unknown_shape_gives_empty_not_a_crash(self):
        """Скрипт должен сказать «не нашёл», а не упасть на продавце."""
        self.assertEqual(rows({"нечто": 5}), [])
        self.assertEqual(rows(None), [])
        self.assertEqual(rows("строка"), [])

    def test_empty_list_is_not_mistaken_for_absence(self):
        self.assertEqual(rows({"services": []}), [])


class TextOfTest(unittest.TestCase):
    def test_all_plain_values_are_searchable(self):
        got = text_of({"id": "svc-1", "name": "Roblox", "price": 10}).lower()

        self.assertIn("roblox", got)
        self.assertIn("10", got)

    def test_nested_values_do_not_break_the_search(self):
        """У услуги есть вложенные номиналы — они не должны ронять разбор."""
        got = text_of({"name": "Roblox", "items": [{"id": "x"}]})

        self.assertIn("Roblox", got)


if __name__ == "__main__":
    unittest.main()
