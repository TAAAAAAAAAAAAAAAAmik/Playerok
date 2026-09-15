"""Тесты серии объявлений. Сети не требуют.

Серия создаёт сразу десяток товаров, и ошибка размножается вместе с ними:
снять с витрины десять неверных объявлений дороже, чем не создать их.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import series                                                   # noqa: E402


class ParseTest(unittest.TestCase):
    def test_any_separator_works(self):
        """С телефона разделитель набирать неудобно — принимаем как есть."""
        rows, bad = series.parse("100 = 70\n200 - 140\n400:280\n800 560")

        self.assertEqual(rows, [(100, 70), (200, 140), (400, 280),
                                (800, 560)])
        self.assertEqual(bad, [])

    def test_spaces_inside_numbers_do_not_break_it(self):
        rows, _ = series.parse("1 000 = 700")

        self.assertEqual(rows, [(1000, 700)])

    def test_a_line_that_makes_no_sense_is_reported(self):
        """Пропущенная строка — это объявление, которого продавец ждал, а
        его нет. Узнать об этом лучше сразу."""
        rows, bad = series.parse("100 = 70\nещё что-нибудь")

        self.assertEqual(rows, [(100, 70)])
        self.assertEqual(bad, ["ещё что-нибудь"])

    def test_zero_and_negative_are_refused(self):
        rows, bad = series.parse("100 = 0\n0 = 70\n-5 = 70")

        self.assertEqual(rows, [])
        self.assertEqual(len(bad), 3)

    def test_the_same_nominal_twice_is_refused(self):
        """Два одинаковых объявления на витрине, и продавец не поймёт,
        какое из них он правил."""
        rows, bad = series.parse("100 = 70\n100 = 90")

        self.assertEqual(rows, [(100, 70)])
        self.assertIn("уже был", bad[0])

    def test_blank_lines_are_not_complaints(self):
        rows, bad = series.parse("100 = 70\n\n\n200 = 140")

        self.assertEqual(len(rows), 2)
        self.assertEqual(bad, [])


class PatternTest(unittest.TestCase):
    """Название-образец: место под номинал вместо числа."""

    def test_the_number_becomes_a_slot(self):
        self.assertEqual(series.pattern_from("100 Robux (Global)", 100),
                         "{номинал} Robux (Global)")

    def test_a_number_inside_a_bigger_one_is_left_alone(self):
        """Иначе «1000 Robux» при переходе со ста на двести стало бы
        «2000 Robux» — и покупатель получил бы вдвое меньше обещанного."""
        self.assertEqual(series.pattern_from("1000 Robux", 100), "")

    def test_a_name_without_the_number_has_no_pattern(self):
        self.assertEqual(series.pattern_from("Робуксы дёшево", 100), "")

    def test_the_suggested_pattern_puts_the_nominal_first(self):
        """Чаще всего номинал и правда стоит первым, а одно нажатие лучше,
        чем набирать всё название заново с телефона."""
        self.assertEqual(series.suggest_pattern("🥳ПРОМОКОДОМ🥳"),
                         "{номинал} 🥳ПРОМОКОДОМ🥳")

    def test_a_pattern_without_a_slot_is_not_usable(self):
        """Иначе все объявления серии получат одно название."""
        self.assertFalse(series.usable("Просто название"))
        self.assertTrue(series.usable("{номинал} Robux"))

    def test_rendering_puts_the_number_back(self):
        self.assertEqual(series.render("{номинал} Robux", 400), "400 Robux")


class RetitleTest(unittest.TestCase):
    def test_the_number_is_replaced(self):
        self.assertEqual(series.retitle("100 Robux (Global)", 100, 200),
                         "200 Robux (Global)")

    def test_a_number_inside_a_bigger_one_is_left_alone(self):
        self.assertEqual(series.retitle("1000 Robux", 100, 200), "")

    def test_without_the_number_there_is_nothing_to_replace(self):
        self.assertEqual(series.retitle("Робуксы дёшево", 100, 200), "")


class PlanTest(unittest.TestCase):
    def test_each_row_becomes_a_job(self):
        jobs, refused = series.plan(
            "{номинал} Robux", "Выдаём 100 робуксов сразу", 100,
            [(200, 140), (400, 280)])

        self.assertEqual([j["name"] for j in jobs],
                         ["200 Robux", "400 Robux"])
        self.assertEqual([j["price"] for j in jobs], [140, 280])
        self.assertEqual([j["nominal"] for j in jobs], [200, 400])
        self.assertEqual(refused, [])

    def test_the_description_follows_the_number(self):
        jobs, _ = series.plan("{номинал} Robux", "Выдаём 100 робуксов", 100,
                              [(200, 140)])

        self.assertEqual(jobs[0]["description"], "Выдаём 200 робуксов")

    def test_a_description_without_the_number_is_kept_as_is(self):
        jobs, _ = series.plan("{номинал} Robux", "Коды сразу", 100,
                              [(200, 140)])

        self.assertEqual(jobs[0]["description"], "Коды сразу")

    def test_the_original_nominal_is_not_repeated(self):
        """Иначе на витрине оказалось бы два одинаковых товара."""
        jobs, _ = series.plan("{номинал} Robux", "", 100,
                              [(100, 70), (200, 140)])

        self.assertEqual([j["nominal"] for j in jobs], [200])

    def test_without_an_original_nominal_every_row_is_new(self):
        """Название образца числа не содержало — значит и повторять
        нечего: продавец сам решил, какие номиналы выставить."""
        jobs, _ = series.plan("{номинал} Robux 🥳", "", None,
                              [(100, 70), (200, 140)])

        self.assertEqual([j["nominal"] for j in jobs], [100, 200])

    def test_a_pattern_without_a_slot_creates_nothing(self):
        """Десяток объявлений с одинаковым названием — это мусор на
        витрине, который потом снимать руками."""
        jobs, refused = series.plan("Просто название", "", None, [(200, 140)])

        self.assertEqual(jobs, [])
        self.assertIn("номинал", refused[0])


if __name__ == "__main__":
    unittest.main()
