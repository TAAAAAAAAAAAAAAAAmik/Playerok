"""Тесты входа в кабинет. Ни сети, ни библиотеки площадки — обе поддельные.

Здесь проверяется не разбор куки (это test_owner.py), а сборка: откуда бот
берёт куки при запуске, что делает, когда их нет, и переживает ли он
отсутствие телеграма.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from test_owner import COOKIES, FakeTelegram, OWNER, update      # noqa: E402


class FakeAccount:
    """Подделка playerokapi.account.Account: помнит, с чем её создали."""

    created = []

    def __init__(self, cookies="", user_agent="", **kw):
        self.cookies = cookies
        self.user_agent = user_agent
        FakeAccount.created.append((cookies, user_agent))

    def get(self):
        return self


def install_fake_library():
    """Подсунуть поддельную библиотеку площадки вместо настоящей."""
    package = types.ModuleType("playerokapi")
    module = types.ModuleType("playerokapi.account")
    module.Account = FakeAccount
    package.account = module
    sys.modules["playerokapi"] = package
    sys.modules["playerokapi.account"] = module


class SignInTest(unittest.TestCase):
    ENV = ("PLAYEROK_COOKIES", "PLAYEROK_UA", "PLAYEROK_COOKIE_FILE",
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")

    def setUp(self):
        install_fake_library()
        FakeAccount.created = []
        self.saved = {k: os.environ.get(k) for k in self.ENV}

        for key in self.ENV:
            os.environ.pop(key, None)

        self.path = os.path.join(tempfile.mkdtemp(), "cookies.json")
        os.environ["PLAYEROK_COOKIE_FILE"] = self.path
        os.environ["PLAYEROK_UA"] = "Mozilla/5.0 (наш браузер)"

        # auth читает путь к файлу при импорте — перечитываем его заново.
        sys.modules.pop("auth", None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        sys.modules.pop("auth", None)

    def test_cookies_from_environment_open_the_cabinet(self):
        os.environ["PLAYEROK_COOKIES"] = COOKIES

        import auth
        account, store, link = auth.sign_in()

        self.assertEqual(FakeAccount.created, [(COOKIES, os.environ["PLAYEROK_UA"])])
        self.assertIsNone(link)
        self.assertIs(account.get(), account)

    def test_owner_is_asked_when_there_are_no_cookies_at_all(self):
        """То, ради чего всё это: куки спрашиваются в телеграме."""
        telegram = FakeTelegram([[], [update(1, COOKIES)]])
        os.environ["TELEGRAM_BOT_TOKEN"] = "bot-token"
        os.environ["TELEGRAM_OWNER_ID"] = OWNER

        import auth
        account, store, link = auth.sign_in(session=telegram)

        self.assertEqual(FakeAccount.created[0][0], COOKIES)
        # Присланное сохранено: после перезапуска спрашивать снова не нужно.
        self.assertEqual(store.load(), COOKIES)

    def test_saved_cookies_survive_a_restart(self):
        os.environ["PLAYEROK_COOKIES"] = COOKIES

        import auth
        auth.CookieStore(self.path).save("token=" + "n" * 60)
        auth.sign_in()

        # На диске — присланное через телеграм, оно свежее окружения.
        self.assertEqual(FakeAccount.created[0][0], "token=" + "n" * 60)

    def test_missing_user_agent_stops_with_an_explanation(self):
        os.environ["PLAYEROK_COOKIES"] = COOKIES
        os.environ.pop("PLAYEROK_UA")

        import auth

        with self.assertRaises(SystemExit) as stop:
            auth.sign_in()

        self.assertIn("PLAYEROK_UA", str(stop.exception))

    def test_no_cookies_and_no_telegram_says_both_ways_out(self):
        import auth

        with self.assertRaises(SystemExit) as stop:
            auth.sign_in()

        message = str(stop.exception)

        self.assertIn("PLAYEROK_COOKIES", message)
        self.assertIn("TELEGRAM_BOT_TOKEN", message)

    def test_missing_library_is_named_with_the_cure(self):
        os.environ["PLAYEROK_COOKIES"] = COOKIES
        sys.modules["playerokapi"] = None
        sys.modules.pop("playerokapi.account", None)

        import auth

        try:
            with self.assertRaises(SystemExit) as stop:
                auth.sign_in()

            self.assertIn("requirements.txt", str(stop.exception))
        finally:
            install_fake_library()


if __name__ == "__main__":
    unittest.main()
