"""Тесты настроек автовыдачи. Сети не требуют.

Главное здесь — что бот правит то самое место, откуда читает движок.
Второй источник правды дал бы «поменял в боте, а выдача по-старому»:
ошибку, которую ищут часами.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from catalog import Card, pick_card                             # noqa: E402
from settings import REGIONS, Settings                          # noqa: E402
from store import JsonStore                                     # noqa: E402

CARD = Card(slug="robux", title="Roblox", emoji="🎮",
            keywords=("robux", "робукс"), measure="робуксов",
            activation="", services={})


class Base(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state", "seller.json")
        self.store = JsonStore(self.path)
        self.conf = Settings(self.store)


class DefaultTest(Base):
    def test_unknown_card_is_off(self):
        """Включать выдачу самим значило бы покупать коды на товары,
        которых продавец не настраивал."""
        self.assertFalse(self.conf.card("robux")["enabled"])

        ok, why = self.conf.ready("robux")

        self.assertFalse(ok)
        self.assertIn("выключена", why)

    def test_enabled_without_services_is_not_ready(self):
        self.conf.set_enabled("robux", True)
        ok, why = self.conf.ready("robux")

        self.assertFalse(ok)
        self.assertIn("услуга", why)

    def test_one_region_is_ready_but_warns(self):
        self.conf.set_enabled("robux", True)
        self.conf.set_service("robux", "GL", "svc-gl")
        ok, why = self.conf.ready("robux")

        self.assertTrue(ok)
        self.assertIn("RU", why)

    def test_both_regions_are_fully_ready(self):
        self.conf.set_enabled("robux", True)

        for region in REGIONS:
            self.conf.set_service("robux", region, f"svc-{region}")

        self.assertEqual(self.conf.ready("robux"), (True, ""))


class SameSourceTest(Base):
    """Движок читает настройки из хранилища состояния — туда и пишем."""

    def test_engine_sees_what_the_bot_changed(self):
        self.conf.set_enabled("robux", True)

        self.assertTrue(self.store.conf("robux")["enabled"])

    def test_card_matching_uses_these_settings(self):
        """Ровно так движок и решает, наш ли это заказ."""
        self.assertIsNone(pick_card([CARD], "80 Robux", self.conf.card))

        self.conf.set_enabled("robux", True)

        self.assertIsNotNone(pick_card([CARD], "80 Robux", self.conf.card))

    def test_sellers_own_word_replaces_the_built_in_ones(self):
        """Он задан, чтобы отделить свои товары от чужих похожих."""
        self.conf.set_enabled("robux", True)
        self.conf.set_keyword("robux", "tamik")

        self.assertIsNone(pick_card([CARD], "80 Robux", self.conf.card))
        self.assertIsNotNone(
            pick_card([CARD], "80 Robux от tamik", self.conf.card))

    def test_changes_survive_a_restart(self):
        self.conf.set_enabled("robux", True)
        self.conf.set_service("robux", "GL", "svc-gl")

        again = Settings(JsonStore(self.path))

        self.assertTrue(again.card("robux")["enabled"])
        self.assertEqual(again.service_id("robux", "GL"), "svc-gl")

    def test_journal_is_not_lost_when_settings_change(self):
        """Затереть журнал значит выдать оплаченный заказ второй раз."""
        self.store.conf("robux").setdefault("delivered", []).append("заказ-7")
        self.store.save()

        self.conf.set_enabled("robux", True)
        self.conf.set_keyword("robux", "tamik")

        self.assertEqual(self.store.conf("robux")["delivered"], ["заказ-7"])


class ServiceTest(Base):
    def test_environment_is_used_when_nothing_is_set(self):
        """Установки, настроенные до появления меню, должны работать."""
        os.environ["APPROUTE_SERVICE_ROBUX_GL"] = "из-окружения"

        try:
            self.assertEqual(self.conf.service_id("robux", "GL"),
                             "из-окружения")
        finally:
            os.environ.pop("APPROUTE_SERVICE_ROBUX_GL", None)

    def test_saved_value_wins_over_the_environment(self):
        os.environ["APPROUTE_SERVICE_ROBUX_GL"] = "из-окружения"

        try:
            self.conf.set_service("robux", "GL", "из-настроек")

            self.assertEqual(self.conf.service_id("robux", "GL"),
                             "из-настроек")
        finally:
            os.environ.pop("APPROUTE_SERVICE_ROBUX_GL", None)

    def test_empty_value_removes_the_service(self):
        self.conf.set_service("robux", "GL", "svc-gl")
        self.conf.set_service("robux", "GL", "")

        self.assertEqual(self.conf.service_id("robux", "GL"), "")

    def test_region_case_does_not_matter(self):
        self.conf.set_service("robux", "gl", "svc-gl")

        self.assertEqual(self.conf.service_id("robux", "GL"), "svc-gl")


class ClearingTest(unittest.TestCase):
    """Точка очищает текстовое поле.

    Пустое сообщение Telegram отправить не даёт, и без этого соглашения
    очистить настройку было нечем вовсе. Очистка обязана происходить здесь,
    а не на экране: однажды подсказка обещала её, а обработчик клал точку
    текстом — и `keyword` из одной точки переставал узнавать свою карту,
    забирая чужие заказы: точка есть в любом названии.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.conf = Settings(JsonStore(os.path.join(self.root, "s.json")))

    def test_a_dot_clears_every_text_field(self):
        pairs = [("set_keyword", "keyword"), ("set_greeting", "greeting"),
                 ("set_note", "note"), ("set_ad_title", "ad_title"),
                 ("set_ad_text", "ad_text"), ("set_region", "region")]

        for setter, field in pairs:
            getattr(self.conf, setter)("robux", "было")
            getattr(self.conf, setter)("robux", ".")

            self.assertEqual(self.conf.card("robux")[field], "", field)

    def test_a_dot_inside_a_longer_text_is_kept(self):
        self.conf.set_note("robux", "Код действует 12 мес. с активации")

        self.assertIn("мес.", self.conf.card("robux")["note"])

    def test_the_region_is_stored_uppercase(self):
        self.conf.set_region("robux", "us")

        self.assertEqual(self.conf.card("robux")["region"], "US")


class ReadyTest(unittest.TestCase):
    """Карта с подкатегорией не требует номеров услуг.

    Требовать их значило бы заставлять продавца переписывать с телефона
    десятки чужих UUID — по одному на регион, — тогда как подкатегория
    одна и меняется редко.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.conf = Settings(JsonStore(os.path.join(self.root, "s.json")))

    def test_disabled_is_not_ready_whatever_else_is_set(self):
        card = Card(slug="apple", title="Apple",
                    subcategory="Apple Gift Cards")

        self.assertEqual(self.conf.ready("apple", card)[0], False)

    def test_a_subcategory_is_enough(self):
        card = Card(slug="apple", title="Apple",
                    subcategory="Apple Gift Cards")
        self.conf.set_enabled("apple", True)

        self.assertEqual(self.conf.ready("apple", card), (True, ""))

    def test_without_a_subcategory_a_service_is_still_required(self):
        card = Card(slug="x", title="X")
        self.conf.set_enabled("x", True)
        ok, why = self.conf.ready("x", card)

        self.assertFalse(ok)
        self.assertIn("услуга", why)


if __name__ == "__main__":
    unittest.main()
