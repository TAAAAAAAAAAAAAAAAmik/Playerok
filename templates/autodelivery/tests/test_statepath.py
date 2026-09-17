"""Тесты общего пути состояния. Сети не требуют.

Проверяется то, из-за чего автовыдача молчала: бот и движок должны
смотреть в ОДИН файл, а старый файл движка — влиться в него, не потеряв
номера выданных заказов.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import statepath                                               # noqa: E402
from store import STATE_DONE, STATE_WAIT_CODE                  # noqa: E402


def write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return path


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PathTest(unittest.TestCase):
    def setUp(self):
        # Папку состояния другие тесты уводят в свои временные — считаем
        # путь на чистом окружении, иначе проверяем не формулу, а соседа.
        self.saved = os.environ.pop("PLAYEROK_STATE", None)

    def tearDown(self):
        if self.saved is not None:
            os.environ["PLAYEROK_STATE"] = self.saved

    def test_the_path_is_absolute(self):
        """«state/delivery» из крона и из сторожа — разные места."""
        self.assertTrue(os.path.isabs(statepath.settings_file()))

    def test_it_lives_next_to_the_project(self):
        self.assertTrue(statepath.settings_file().startswith(statepath.HERE))

    def test_a_given_folder_is_respected(self):
        got = statepath.settings_file("тест", "/tmp/выдача")

        self.assertEqual(got, os.path.join("/tmp/выдача", "тест.json"))

    def test_a_relative_folder_is_tied_to_the_project(self):
        got = statepath.settings_file("тест", "своё")

        self.assertEqual(got, os.path.join(statepath.HERE, "своё",
                                           "тест.json"))

    def test_without_accounts_the_name_is_default(self):
        old = os.environ.get("PLAYEROK_ACCOUNTS")
        os.environ["PLAYEROK_ACCOUNTS"] = tempfile.mkdtemp()

        try:
            self.assertEqual(statepath.current_id(), "default")
        finally:
            if old is None:
                del os.environ["PLAYEROK_ACCOUNTS"]
            else:
                os.environ["PLAYEROK_ACCOUNTS"] = old


class OneFileTest(unittest.TestCase):
    """Бот и движок считают путь по одной формуле.

    Ради этого всё и затевалось: разные формулы означали «в боте
    включено, а движок не выдаёт» — молча, без единой строчки в журнале.
    """

    def source(self, name):
        with open(os.path.join(statepath.HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_the_same_folder_gives_the_same_file(self):
        """Формула одна: папка кабинета плюс его имя."""
        where = os.environ.get("PLAYEROK_STATE", "state/delivery")

        self.assertEqual(statepath.settings_file("кабинет-7", where),
                         statepath.settings_file("кабинет-7"))

    def test_the_engine_takes_the_file_from_here(self):
        """Своего пути у движка быть не должно — он уже разъезжался."""
        source = self.source("example_bot.py")

        self.assertIn("statepath.open_store()", source)
        self.assertNotIn("JsonStore(", source)

    def test_the_bot_takes_the_file_from_here_too(self):
        self.assertIn("statepath.settings_file(", self.source("item_bot.py"))


class AbsorbTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = os.path.join(self.root, "выдача", "кабинет.json")
        self.legacy = os.path.join(self.root, "seller-1.json")

    def test_nothing_to_absorb_is_not_a_problem(self):
        self.assertEqual(statepath.absorb_legacy(self.target, self.legacy), "")

    def test_the_delivered_orders_survive(self):
        """Потерять их значит купить те же коды второй раз."""
        write(self.target, {"cards": {"roblox": {"delivered": ["b"]}}})
        write(self.legacy, {"cards": {"roblox": {"delivered": ["a"]}}})

        statepath.absorb_legacy(self.target, self.legacy)

        got = read(self.target)["cards"]["roblox"]["delivered"]

        self.assertEqual(sorted(got), ["a", "b"])

    def test_the_settings_of_the_bot_win(self):
        """Их продавец задавал только что, старый файл о них не знает."""
        write(self.target, {"cards": {"roblox": {"enabled": True,
                                                 "keyword": "автовыдача"}}})
        write(self.legacy, {"cards": {"roblox": {"enabled": False,
                                                 "keyword": "старое"}}})

        statepath.absorb_legacy(self.target, self.legacy)
        card = read(self.target)["cards"]["roblox"]

        self.assertTrue(card["enabled"])
        self.assertEqual(card["keyword"], "автовыдача")

    def test_a_card_only_the_engine_knew_is_kept(self):
        write(self.target, {"cards": {"roblox": {"enabled": True}}})
        write(self.legacy, {"cards": {"xbox": {"delivered": ["z"]}}})

        statepath.absorb_legacy(self.target, self.legacy)
        cards = read(self.target)["cards"]

        self.assertEqual(cards["xbox"]["delivered"], ["z"])
        self.assertTrue(cards["roblox"]["enabled"])

    def test_an_unfinished_purchase_comes_along(self):
        write(self.target, {"cards": {"roblox": {}}})
        write(self.legacy, {"cards": {"roblox": {"log": [
            {"order": "17", "state": STATE_WAIT_CODE, "at": 5}]}}})

        statepath.absorb_legacy(self.target, self.legacy)
        log = read(self.target)["cards"]["roblox"]["log"]

        self.assertEqual(log[0]["order"], "17")

    def test_a_finished_entry_beats_an_unfinished_one(self):
        """Иначе возобновление возьмётся доделывать уже выданное."""
        write(self.target, {"cards": {"roblox": {"log": [
            {"order": "17", "state": STATE_DONE, "at": 1}]}}})
        write(self.legacy, {"cards": {"roblox": {"log": [
            {"order": "17", "state": STATE_WAIT_CODE, "at": 9}]}}})

        statepath.absorb_legacy(self.target, self.legacy)
        log = read(self.target)["cards"]["roblox"]["log"]

        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["state"], STATE_DONE)

    def test_the_newest_entries_go_first(self):
        write(self.target, {"cards": {"roblox": {"log": [
            {"order": "1", "state": STATE_WAIT_CODE, "at": 1}]}}})
        write(self.legacy, {"cards": {"roblox": {"log": [
            {"order": "2", "state": STATE_WAIT_CODE, "at": 50}]}}})

        statepath.absorb_legacy(self.target, self.legacy)
        log = read(self.target)["cards"]["roblox"]["log"]

        self.assertEqual([e["order"] for e in log], ["2", "1"])

    def test_the_old_file_is_moved_aside_not_deleted(self):
        """В нём деньги: если перенос вышел кривым, вернуть их надо
        откуда-то."""
        write(self.legacy, {"cards": {"roblox": {"delivered": ["a"]}}})

        moved = statepath.absorb_legacy(self.target, self.legacy)

        self.assertFalse(os.path.exists(self.legacy))
        self.assertTrue(os.path.exists(moved))

    def test_the_second_run_does_nothing(self):
        write(self.legacy, {"cards": {"roblox": {"delivered": ["a"]}}})
        statepath.absorb_legacy(self.target, self.legacy)

        self.assertEqual(statepath.absorb_legacy(self.target, self.legacy), "")
        self.assertEqual(read(self.target)["cards"]["roblox"]["delivered"],
                         ["a"])

    def test_a_broken_old_file_is_left_alone(self):
        os.makedirs(os.path.dirname(self.legacy) or ".", exist_ok=True)

        with open(self.legacy, "w", encoding="utf-8") as f:
            f.write("{это не json")

        self.assertEqual(statepath.absorb_legacy(self.target, self.legacy), "")
        self.assertTrue(os.path.exists(self.legacy))

    def test_a_missing_target_is_created(self):
        write(self.legacy, {"cards": {"roblox": {"delivered": ["a"]}}})
        statepath.absorb_legacy(self.target, self.legacy)

        self.assertEqual(read(self.target)["cards"]["roblox"]["delivered"],
                         ["a"])

    def test_the_closed_orders_survive_too(self):
        """Закрытый заказ, потерявший отметку, покупается заново."""
        write(self.target, {"cards": {"roblox": {"closed": ["b"]}}})
        write(self.legacy, {"cards": {"roblox": {"closed": ["a"]}}})

        statepath.absorb_legacy(self.target, self.legacy)
        got = read(self.target)["cards"]["roblox"]["closed"]

        self.assertEqual(sorted(got), ["a", "b"])

    def test_the_orders_on_hold_survive(self):
        write(self.target, {"shared": {"held": {"1": {"title": "а"}}}})
        write(self.legacy, {"shared": {"held": {"2": {"title": "б"}}}})

        statepath.absorb_legacy(self.target, self.legacy)
        held = read(self.target)["shared"]["held"]

        self.assertEqual(sorted(held), ["1", "2"])

    def test_the_first_look_does_not_happen_twice(self):
        """Иначе бот придержал бы всё ещё раз — уже после разбора."""
        write(self.target, {"shared": {"started": False}})
        write(self.legacy, {"shared": {"started": True}})

        statepath.absorb_legacy(self.target, self.legacy)

        self.assertTrue(read(self.target)["shared"]["started"])

    def test_the_mute_switch_of_the_bot_wins(self):
        write(self.target, {"shared": {"paused": True}})
        write(self.legacy, {"shared": {"paused": False}})

        statepath.absorb_legacy(self.target, self.legacy)

        self.assertTrue(read(self.target)["shared"]["paused"])

    def test_the_store_reads_what_was_absorbed(self):
        write(self.legacy, {"cards": {"roblox": {"delivered": ["a"]}}})
        statepath.absorb_legacy(self.target, self.legacy)

        from store import JsonStore

        self.assertEqual(JsonStore(self.target).conf("roblox")["delivered"],
                         ["a"])


if __name__ == "__main__":
    unittest.main()
