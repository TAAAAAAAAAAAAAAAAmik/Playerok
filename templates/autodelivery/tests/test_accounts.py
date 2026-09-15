"""Тесты хранилища кабинетов. Сети не требуют.

Здесь два дорогих места: в файле лежит доступ к кабинету, а номер
приходит из кнопки и подставляется в путь.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from accounts import AccountStore, valid_id                     # noqa: E402

COOKIES = "__ddg3=a; token=" + "j" * 60
UA = "Mozilla/5.0 (Android 16; Mobile; rv:155.0) Firefox/155.0"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = AccountStore(os.path.join(self.root, "кабинеты"))

    def test_saved_account_comes_back_whole(self):
        aid = self.store.add("Основной", COOKIES, UA)
        saved = self.store.get(aid)

        self.assertEqual(saved.name, "Основной")
        self.assertEqual(saved.cookies, COOKIES)
        self.assertEqual(saved.user_agent, UA)

    def test_file_is_closed_to_others(self):
        """В файле доступ к кабинету: соседям по серверу его не видно."""
        aid = self.store.add("Основной", COOKIES, UA)
        path = os.path.join(self.store.folder, aid, "account.json")

        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_several_accounts_live_side_by_side(self):
        self.store.add("Первый", COOKIES, UA)
        self.store.add("Второй", COOKIES, UA)

        self.assertEqual(len(self.store.all()), 2)

    def test_current_is_remembered(self):
        first = self.store.add("Первый", COOKIES, UA)
        second = self.store.add("Второй", COOKIES, UA)
        self.store.set_current(first)

        self.assertEqual(self.store.current().id, first)

        self.store.set_current(second)

        self.assertEqual(self.store.current().id, second)

    def test_current_falls_back_when_the_marked_one_is_gone(self):
        """Лучше работать не с тем, чем не работать вовсе — имя видно в меню."""
        first = self.store.add("Первый", COOKIES, UA)
        self.store.set_current(first)
        self.store.remove(first)
        self.store.add("Второй", COOKIES, UA)

        self.assertEqual(self.store.current().name, "Второй")

    def test_no_accounts_means_no_current(self):
        self.assertIsNone(self.store.current())

    def test_cookies_can_be_replaced_keeping_the_name(self):
        """Куки протухают чаще всего остального."""
        aid = self.store.add("Основной", COOKIES, UA)
        fresh = "token=" + "n" * 60

        self.assertTrue(self.store.update_cookies(aid, fresh))

        saved = self.store.get(aid)
        self.assertEqual(saved.cookies, fresh)
        self.assertEqual(saved.name, "Основной")
        self.assertEqual(saved.user_agent, UA)

    def test_setting_a_missing_account_as_current_fails(self):
        self.assertFalse(self.store.set_current("deadbeef"))


class DangerousIdTest(unittest.TestCase):
    BAD = ("../../etc", "..", "/etc/passwd", "a/../b", "", None, "DEADBEEF",
           "deadbeef0", "dead beef")

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = AccountStore(os.path.join(self.root, "кабинеты"))
        self.outside = os.path.join(self.root, "постороннее.txt")

        with open(self.outside, "w", encoding="utf-8") as f:
            f.write("важное")

    def test_bad_ids_are_refused(self):
        for bad in self.BAD:
            self.assertFalse(valid_id(bad), bad)
            self.assertIsNone(self.store.get(bad), bad)
            self.assertFalse(self.store.remove(bad), bad)
            self.assertFalse(self.store.update_cookies(bad, "x"), bad)

        self.assertTrue(os.path.exists(self.outside))


if __name__ == "__main__":
    unittest.main()
