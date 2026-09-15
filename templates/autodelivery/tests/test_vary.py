"""Тесты разнообразия полей. Сети не требуют.

Смысл в том, чтобы копии не совпадали до буквы. Но поле бывает видно
покупателю, и «разнообразие» там легко превращается в обман — за этим
здесь и следим.
"""
from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import vary                                                     # noqa: E402


class WhichFieldsTest(unittest.TestCase):
    def test_free_text_fields_are_varied(self):
        for label in ("Комментарий", "Комментарий к товару", "Промокод",
                      "Заметка", "comment"):
            self.assertTrue(vary.varied(label), label)

    def test_fields_with_fixed_values_are_not(self):
        """Площадка принимает в них только свои значения — вписав туда
        фразу, мы получили бы отказ на последнем шаге мастера."""
        for label in ("Регион", "Платформа", "Способ получения", ""):
            self.assertFalse(vary.varied(label), label)


class PromoTest(unittest.TestCase):
    """Про «Промокод» отдельно — там ошибка стоит доверия покупателя."""

    def test_it_says_nothing_about_whether_a_promo_exists(self):
        """Продавец может торговать именно «через промокод» — так и
        называется его объявление. Бодрое «промокода нет» спорило бы с его
        же витриной."""
        for phrase in vary.PROMO_PHRASES:
            self.assertNotIn("промокод", phrase.lower())

    def test_it_never_looks_like_a_code(self):
        """Случайная строка читается как код на скидку, которого нет:
        покупатель попробует применить, не выйдет — и это уже не
        разнообразие, а обман."""
        for _ in range(50):
            value = vary.value_for("Промокод")

            # У кода нет пробелов и точки в конце, у фразы — есть.
            self.assertIn(" ", value)
            self.assertTrue(value.endswith("."))

    def test_a_comment_gets_a_phrase_from_its_own_pool(self):
        """Два поля одного товара не должны получить одну фразу."""
        value = vary.value_for("Комментарий")

        self.assertIn(value, vary.PHRASES)
        self.assertNotIn(value, vary.PROMO_PHRASES)


class KeepingTest(unittest.TestCase):
    def test_the_sellers_own_text_is_not_thrown_away(self):
        """Он его для чего-то ставил."""
        value = vary.value_for("Комментарий", "моя пометка")

        self.assertTrue(value.startswith("моя пометка"))
        self.assertGreater(len(value), len("моя пометка"))

    def test_the_same_phrase_is_not_added_twice(self):
        once = vary.value_for("Комментарий", "")
        twice = vary.value_for("Комментарий", once)

        self.assertEqual(twice.count(once), 1)


class RotationTest(unittest.TestCase):
    """Простой выбор наугад повторяется — а повтор и есть то, чего
    избегаем."""

    def test_a_whole_deck_comes_out_without_repeats(self):
        rotation = vary.Rotation(random.Random(7))
        got = [rotation.next(vary.PHRASES) for _ in range(len(vary.PHRASES))]

        self.assertEqual(len(set(got)), len(vary.PHRASES))

    def test_no_repeat_across_the_seam_between_decks(self):
        """На стыке колод повтор подряд заметнее всего."""
        for seed in range(20):
            rotation = vary.Rotation(random.Random(seed))
            got = [rotation.next(vary.PHRASES)
                   for _ in range(len(vary.PHRASES) * 3)]

            for before, after in zip(got, got[1:]):
                self.assertNotEqual(before, after, seed)

    def test_two_pools_do_not_share_a_deck(self):
        rotation = vary.Rotation(random.Random(1))
        rotation.next(vary.PHRASES)

        self.assertIn(rotation.next(vary.PROMO_PHRASES), vary.PROMO_PHRASES)


class ApplyTest(unittest.TestCase):
    def test_only_matching_fields_are_touched(self):
        fields = [{"id": "1", "label": "Комментарий", "value": ""},
                  {"id": "2", "label": "Регион", "value": "GL"}]
        touched = vary.apply(fields)

        self.assertEqual(touched, ["Комментарий"])
        self.assertEqual(fields[1]["value"], "GL")

    def test_the_list_is_edited_in_place(self):
        """Это тот самый список, который уйдёт на площадку: подменив его
        целиком, потеряли бы всё остальное, что в нём заполнено."""
        fields = [{"id": "1", "label": "Комментарий", "value": "",
                   "required": False}]
        vary.apply(fields)

        self.assertIn("required", fields[0])
        self.assertTrue(fields[0]["value"])


if __name__ == "__main__":
    unittest.main()
