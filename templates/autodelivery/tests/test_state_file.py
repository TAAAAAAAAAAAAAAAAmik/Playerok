"""Тесты хранилища состояния. Сети не требуют.

В один файл пишут ДВА процесса: бот в телеграме — настройки, выдача —
журнал и номера выданных заказов. Пока каждый писал его целиком «как у
меня в памяти», они затирали друг друга, и обе стороны стоили дорого.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from store import JsonStore                                     # noqa: E402


class TwoProcessesTest(unittest.TestCase):
    """Бот и выдача работают с одним файлом из разных процессов."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state.json")
        # Выдача: один склад на всю жизнь процесса, файл не перечитывает.
        self.delivery = JsonStore(self.path)
        self.delivery.conf("robux")["enabled"] = True
        self.delivery.save()

    def bot(self):
        """Бот: свой склад на каждое нажатие."""
        return JsonStore(self.path)

    def on_disk(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["cards"]["robux"]

    def test_a_setting_is_not_lost_when_the_journal_is_saved(self):
        """Продавец задавал слово-опознаватель, выдача следом сохраняла
        журнал — и слово пропадало. А выдача продолжала не узнавать товар,
        и найти причину было негде."""
        bot = self.bot()
        bot.conf("robux")["keyword"] = "автовыдача"
        bot.save()

        self.delivery.conf("robux").setdefault("delivered", []).append("777")
        self.delivery.save()

        self.assertEqual(self.on_disk()["keyword"], "автовыдача")

    def test_a_delivered_order_is_not_forgotten_when_a_setting_changes(self):
        """Хуже обратная сторона: номер заказа исчезал из выданных, и
        такой заказ покупался и выдавался ВТОРОЙ раз, за деньги
        продавца."""
        self.delivery.conf("robux").setdefault("delivered", []).append("777")
        self.delivery.save()

        bot = self.bot()
        self.delivery.conf("robux")["delivered"].append("888")
        self.delivery.save()

        bot.conf("robux")["note"] = "спасибо"
        bot.save()

        self.assertEqual(self.on_disk()["delivered"], ["777", "888"])

    def test_both_changes_survive_together(self):
        bot = self.bot()
        self.delivery.conf("robux").setdefault("delivered", []).append("777")
        self.delivery.save()

        bot.conf("robux")["note"] = "спасибо"
        bot.save()
        got = self.on_disk()

        self.assertEqual(got["note"], "спасибо")
        self.assertEqual(got["delivered"], ["777"])

    def test_a_journal_entry_stays_editable_after_someone_elses_save(self):
        """Выдача держит ссылку на запись и дописывает её после
        сохранения: код приходит позже покупки. Подменив словарь целиком,
        мы потеряли бы купленный код."""
        entry = {"order": "777", "state": "покупаем"}
        self.delivery.conf("robux").setdefault("log", []).insert(0, entry)
        self.delivery.save()

        bot = self.bot()
        bot.conf("robux")["note"] = "х"
        bot.save()

        entry["codes"] = ["AAAA-BBBB"]
        entry["state"] = "выдан"
        self.delivery.save()

        self.assertEqual(self.on_disk()["log"][0]["codes"], ["AAAA-BBBB"])

    def test_a_deleted_field_does_not_come_back(self):
        bot = self.bot()
        bot.conf("robux")["note"] = "х"
        bot.save()

        other = self.bot()
        other.conf("robux").pop("note")
        other.save()

        self.assertNotIn("note", self.on_disk())

    def test_a_field_added_by_the_other_side_is_picked_up(self):
        other = self.bot()
        other.conf("robux")["greeting"] = "принял заказ"
        other.save()

        self.delivery.conf("robux").setdefault("delivered", []).append("1")
        self.delivery.save()

        self.assertEqual(self.on_disk()["greeting"], "принял заказ")

    def test_a_broken_file_does_not_lose_our_changes(self):
        """Посреди записи падать нельзя: наши изменения дороже чужих."""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{не json")

        self.delivery.conf("robux")["keyword"] = "своё"
        self.delivery.save()

        self.assertEqual(self.on_disk()["keyword"], "своё")

    def test_a_whole_new_card_from_the_other_side_survives(self):
        other = self.bot()
        other.conf("apple")["enabled"] = True
        other.save()

        self.delivery.conf("robux")["keyword"] = "своё"
        self.delivery.save()

        with open(self.path, encoding="utf-8") as f:
            cards = json.load(f)["cards"]

        self.assertTrue(cards["apple"]["enabled"])
        self.assertEqual(cards["robux"]["keyword"], "своё")


if __name__ == "__main__":
    unittest.main()
