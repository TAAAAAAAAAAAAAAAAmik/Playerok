"""Тесты подбора слова-опознавателя. Сети не требуют.

Слово-опознаватель — самая тихая из настроек: ошибся, и бот либо проходит
мимо своих заказов, либо забирает чужие. Оба раза правильное слово было
видно с витрины.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import setup                                                   # noqa: E402
from catalog import region_in                                  # noqa: E402

PROMO = ["🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎",
         "🍎50 РОБУКСОВ ПРОМОКОДОМ",
         "ПРОМОКОДОМ 1000 робуксов"]
GAMEPASS = ["🔴 80 РОБУКСОВ 🔴 💫 АВТОВЫДАЧА 💫",
            "🔴 400 РОБУКСОВ 🔴 АВТОВЫДАЧА"]


class KeywordsTest(unittest.TestCase):
    def test_the_word_of_our_listings_wins(self):
        got = setup.keywords(PROMO, GAMEPASS)

        self.assertEqual(got[0][0], "промокодом")

    def test_a_word_of_the_others_is_never_offered(self):
        """«Автовыдача» есть и там и там — по ней бот забрал бы чужой
        товар, проданный через геймпасс."""
        words = [word for word, _ in setup.keywords(PROMO, GAMEPASS)]

        self.assertNotIn("автовыдача", words)

    def test_it_says_how_many_are_covered(self):
        self.assertEqual(setup.keywords(PROMO, GAMEPASS)[0][1], 3)

    def test_the_cards_own_word_is_preferred_when_equal(self):
        """«xbox» про товар, а «try» про его оформление: регион в названии
        поменяют, и слово перестанет совпадать."""
        got = setup.keywords(["Xbox Gift Card 100 TRY",
                              "Xbox Gift Card 500 TRY"],
                             ["Apple Gift Card 10 USD"],
                             extra=("xbox",), prefer=("xbox",))

        self.assertEqual(got[0][0], "xbox")

    def test_common_words_are_not_offered(self):
        """«Код», «быстро», «моментально» есть у всех и ничего не
        отличают."""
        got = setup.keywords(["Xbox код быстро"], ["Apple карта"])

        self.assertNotIn("код", [w for w, _ in got])
        self.assertNotIn("быстро", [w for w, _ in got])

    def test_numbers_are_not_words(self):
        got = [w for w, _ in setup.keywords(["Xbox 100 TRY"], [])]

        self.assertNotIn("100", got)

    def test_nothing_in_common_gives_nothing(self):
        """Лучше признать, что слова нет, чем выдать опасное."""
        self.assertEqual(setup.keywords(["Xbox карта"], ["Xbox карта"]), [])

    def test_no_listings_at_all(self):
        self.assertEqual(setup.keywords([], []), [])

    def test_the_cards_word_works_without_listings(self):
        got = setup.keywords(["Xbox Gift Card"], [], extra=("xbox",))

        self.assertIn("xbox", [w for w, _ in got])

    def test_several_options_are_offered(self):
        got = setup.keywords(["Steam Турция 500", "Steam Турция 1000"], [])

        self.assertGreater(len(got), 1)
        self.assertLessEqual(len(got), setup.SHOWN)


class CoveredTest(unittest.TestCase):
    def test_it_shows_what_falls_under_the_word(self):
        got = setup.covered(PROMO + GAMEPASS, "промокодом")

        self.assertEqual(len(got), 3)

    def test_case_does_not_matter(self):
        self.assertEqual(len(setup.covered(["ПРОМОКОДОМ"], "промокодом")), 1)


class RegionTest(unittest.TestCase):
    def test_the_most_frequent_region_wins(self):
        names = ["Xbox Gift Card 100 TR", "Xbox Gift Card 500 TR",
                 "Xbox Gift Card 10 US"]

        self.assertEqual(setup.region_of(names, region_in), "TR")

    def test_nothing_readable_is_empty(self):
        self.assertEqual(setup.region_of(["Просто товар"], region_in), "")

    def test_no_names_at_all(self):
        self.assertEqual(setup.region_of([], region_in), "")


class NoListingsYetTest(unittest.TestCase):
    """Карту включают и до первого объявления: их только собираются
    выставлять, а слово нужно уже сейчас."""

    def test_the_cards_word_is_offered(self):
        got = setup.keywords([], ["Роблокс промокодом"], extra=("xbox",),
                             prefer=("xbox",))

        self.assertEqual(got[0][0], "xbox")

    def test_it_covers_nothing_yet_and_says_so(self):
        self.assertEqual(setup.keywords([], [], extra=("xbox",))[0][1], 0)

    def test_a_word_of_the_others_is_still_forbidden(self):
        got = setup.keywords([], ["Xbox Game Pass"], extra=("xbox",))

        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
