"""Тесты объявления одним сообщением. Сети не требуют.

Главная опасность разбора — тихо понять не то. Пропущенная строка не
должна сдвигать остальные, а непонятый ключ обязан дойти до продавца:
молча выброшенная строка — это условие, которого покупатель не увидит.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import oneshot                                                  # noqa: E402
import wizard                                                   # noqa: E402

WHOLE = """Название: Roblox 1000 Robux
Цена: 700
Номинал: 1000
Регион: GL
Описание: Код приходит сразу.
Активация: roblox.com/redeem
Комментарий: без входа в аккаунт"""


class ParseTest(unittest.TestCase):
    def test_every_field_is_read(self):
        values = oneshot.parse(WHOLE, labels=["Комментарий"])

        self.assertEqual(values["name"], "Roblox 1000 Robux")
        self.assertEqual(values["price"], "700")
        self.assertEqual(values["nominal"], "1000")
        self.assertEqual(values["region"], "GL")
        self.assertEqual(values["field:Комментарий"], "без входа в аккаунт")

    def test_a_description_may_span_several_lines(self):
        values = oneshot.parse(WHOLE, labels=["Комментарий"])

        self.assertIn("Код приходит сразу.", values["description"])
        self.assertIn("roblox.com/redeem", values["description"])

    def test_order_does_not_matter(self):
        """Ключи для того и нужны: продавец пишет как ему удобно."""
        values = oneshot.parse("Цена: 700\nНазвание: Робуксы")

        self.assertEqual(values["name"], "Робуксы")
        self.assertEqual(values["price"], "700")

    def test_a_missing_line_does_not_shift_the_others(self):
        """Ради этого разбор и сделан по ключам, а не по порядку строк: там
        пропуск сдвигал всё, что ниже, и цена вставала на место номинала —
        бот покупал у поставщика не то."""
        values = oneshot.parse("Название: Робуксы\nНоминал: 1000")

        self.assertEqual(values["nominal"], "1000")
        self.assertNotIn("price", values)

    def test_synonyms_are_understood(self):
        for word in ("Количество", "Кол-во", "Сумма"):
            values = oneshot.parse(f"{word}: 800")

            self.assertEqual(values["nominal"], "800", word)

    def test_a_colon_in_the_description_does_not_end_it(self):
        """Обычное описание полно двоеточий: «Активация: …», «Скидка: …».
        Начни их считать ключами — и описание развалится на куски."""
        text = ("Описание: Код приходит сразу.\n"
                "Активация: roblox.com/redeem\n"
                "Скидка: 5% на второй")
        values = oneshot.parse(text)

        self.assertIn("roblox.com/redeem", values["description"])
        self.assertIn("5% на второй", values["description"])

    def test_a_typo_in_a_key_lands_in_the_text_not_in_the_price(self):
        """Так и задумано: пропавшую цену поймает `missing` и скажет о ней,
        а «Цна: 700», принятое за цену, не поймал бы никто."""
        values = oneshot.parse("Описание: текст\nЦна: 700")

        self.assertNotIn("price", values)
        self.assertIn("Цна: 700", values["description"])

    def test_a_line_without_a_colon_before_any_key_is_ignored(self):
        values = oneshot.parse("просто заголовок\nНазвание: X")

        self.assertEqual(values["name"], "X")

    def test_case_does_not_matter(self):
        values = oneshot.parse("НАЗВАНИЕ: X\nцена: 5")

        self.assertEqual(values["name"], "X")
        self.assertEqual(values["price"], "5")

    def test_a_colon_inside_a_value_is_kept(self):
        values = oneshot.parse("Описание: Активация: roblox.com/redeem")

        self.assertIn("roblox.com/redeem", values["description"])


class BlankTest(unittest.TestCase):
    def test_the_form_lists_our_fields_and_the_marketplaces(self):
        form = oneshot.blank(labels=["Комментарий", "Промокод"])

        for field in oneshot.ORDER:
            self.assertIn(oneshot.KEYS[field][0] + ":", form)

        self.assertIn("Комментарий:", form)
        self.assertIn("Промокод:", form)

    def test_the_form_can_be_parsed_back(self):
        """Заготовка — это то, что продавец пришлёт обратно. Если её
        собственный разбор не понимает, понимать нечего."""
        form = oneshot.blank(labels=["Комментарий"])
        values = oneshot.parse(form, labels=["Комментарий"])

        self.assertIn("name", values)
        self.assertIn("field:Комментарий", values)


class MissingTest(unittest.TestCase):
    def test_name_and_price_are_required(self):
        self.assertEqual(oneshot.missing({}), ["Название", "Цена"])

    def test_a_required_marketplace_field_is_required_too(self):
        fields = [{"id": "f1", "label": "Комментарий", "required": True}]
        gaps = oneshot.missing({"name": "X", "price": "5"}, fields)

        self.assertEqual(gaps, ["Комментарий"])

    def test_an_optional_field_is_not_missed(self):
        fields = [{"id": "f1", "label": "Промокод", "required": False}]

        self.assertEqual(oneshot.missing({"name": "X", "price": "5"},
                                         fields), [])


class AcceptOneTest(unittest.TestCase):
    """Проверки те же, что в пошаговом опросе.

    Иначе через быстрый путь в товар попало бы то, что опрос отверг бы:
    цена с копейками, чужой регион.
    """

    def draft(self):
        d = wizard.Draft()
        d.game = d.category = d.obtaining = {"id": "1", "name": "x"}
        return d

    def test_a_good_value_is_applied(self):
        d = self.draft()

        self.assertEqual(wizard.accept_one(d, "price", "700"), "")
        self.assertEqual(d.price, 700)

    def test_kopecks_are_refused_here_too(self):
        d = self.draft()

        self.assertTrue(wizard.accept_one(d, "price", "700,50"))
        self.assertEqual(d.price, 0)

    def test_an_unknown_region_is_refused_here_too(self):
        d = self.draft()

        self.assertTrue(wizard.accept_one(d, "region", "ZZ"))

    def test_the_nominal_comes_from_the_name(self):
        d = self.draft()
        wizard.accept_one(d, "name", "Roblox 1000 Robux")

        self.assertEqual(d.nominal, 1000)

    def test_an_explicit_nominal_is_not_overwritten_by_the_name(self):
        """Строка «Номинал» могла прийти раньше названия — порядок строк
        в сообщении любой."""
        d = self.draft()
        wizard.accept_one(d, "nominal", "800")
        wizard.accept_one(d, "name", "Roblox Gift Card 10 USD")

        self.assertEqual(d.nominal, 800)


if __name__ == "__main__":
    unittest.main()
