"""Тесты доктора. Сети не требуют.

Главное здесь — что доктор отвечает ТЕМ ЖЕ правилом, что и выдача.
Диагностика, разошедшаяся с делом, хуже её отсутствия: после «бот не
узнаёт ваш товар» беду будут искать не там, где она лежит.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import doctor                                                   # noqa: E402
from cards import CARDS                                         # noqa: E402
from catalog import is_card_order, pick_card                    # noqa: E402
from settings import Settings                                   # noqa: E402
from store import JsonStore                                     # noqa: E402


class Item:
    def __init__(self, name, description=""):
        self.name, self.description = name, description


class Page:
    def __init__(self, items):
        self.items = items


class Account:
    def __init__(self, items):
        self._items = items

    def get_my_items(self, **kw):
        return Page(self._items)


class WhoseTest(unittest.TestCase):
    """Кому достанется заказ — доктор и движок обязаны отвечать одно."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.conf = Settings(JsonStore(os.path.join(self.root, "s.json")))

    def whose(self, title):
        return doctor._whose(CARDS, is_card_order, self.conf, title)

    def engine_whose(self, title):
        """Как это делает движок: pick_card по включённым картам."""
        for card in CARDS:
            self.conf.set_enabled(card.slug, True)

        return pick_card(CARDS, title, lambda slug: self.conf.store.conf(slug))

    def test_both_agree_on_an_ordinary_name(self):
        title = "Roblox 1000 Robux Global"

        self.assertEqual(self.whose(title).slug, "robux")
        self.assertEqual(self.engine_whose(title).slug, "robux")

    def test_both_agree_when_the_seller_set_a_keyword(self):
        """Название без единого знакомого слова — ровно тот случай, из-за
        которого выдача молчала: «🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА» не содержит
        ни «robux», ни «роблокс»."""
        title = "🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎 100"

        self.assertIsNone(self.whose(title))

        self.conf.set_keyword("robux", "автовыдача")

        self.assertEqual(self.whose(title).slug, "robux")
        self.assertEqual(self.engine_whose(title).slug, "robux")

    def test_a_keyword_means_only_it(self):
        """Обратная сторона настройки, и продавец должен увидеть её до
        того, как потеряет продажу."""
        self.conf.set_keyword("robux", "автовыдача")

        self.assertIsNone(self.whose("Roblox 1000 Robux"))

    def test_a_foreign_product_stays_foreign(self):
        self.assertIsNone(self.whose("Битки в Radmir RP 1кк"))


class ListingsTest(unittest.TestCase):
    """Что доктор скажет про живые объявления продавца."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.environ["PLAYEROK_STATE"] = os.path.join(self.root, "выдача")
        os.environ["PLAYEROK_ACCOUNTS"] = os.path.join(self.root, "кабинеты")
        doctor.problems.clear()

    def check(self, items):
        doctor.problems.clear()
        doctor.listings_check(Account(items))

        return [text for text, _ in doctor.problems]

    def test_an_unrecognised_listing_is_named_not_skipped(self):
        """Молчание тут и есть беда: бот просто не смотрит на такой заказ,
        а выглядит это как «автовыдача не работает»."""
        said = self.check([Item("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА", "Коды сразу")])

        self.assertTrue(any("НЕ узнаёт" in t for t in said))

    def test_a_listing_without_a_nominal_is_a_problem(self):
        doctor._settings().set_keyword("robux", "автовыдача")
        doctor._settings().set_region("robux", "GL")
        said = self.check([Item("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА", "Коды сразу")])

        self.assertTrue(any("Номинал" in t for t in said))

    def test_a_listing_without_a_region_is_a_problem(self):
        said = self.check([Item("Roblox 1000 Robux", "Коды сразу")])

        self.assertTrue(any("регион" in t for t in said))

    def test_a_good_listing_is_not_a_problem(self):
        said = self.check([Item("Roblox 1000 Robux",
                                "Регион кода: GL\nНоминал: 1000")])

        self.assertEqual(said, [])

    def test_the_fallback_region_setting_counts(self):
        """Запасной регион карты — законный путь для старых объявлений."""
        doctor._settings().set_region("robux", "GL")
        said = self.check([Item("Roblox 1000 Robux", "Коды сразу")])

        self.assertEqual(said, [])

    def test_nothing_of_ours_at_all_is_said_plainly(self):
        said = self.check([Item("Битки в Radmir RP", "текст")])

        self.assertTrue(any("выдавать нечего" in t for t in said))


if __name__ == "__main__":
    unittest.main()
