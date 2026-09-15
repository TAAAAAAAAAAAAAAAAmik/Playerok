"""Тесты сообщений о поломке. Сети не требуют.

Здесь важны обе крайности. Молчащий бот при протухших куках крутится
вхолостую, а продавец думает, что всё работает. Говорящий на каждой
попытке превращает поток в спам за час, и спам выключают вместе с важным.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from alarm import COOKIES_ADVICE, Alarm                         # noqa: E402
from playerok import is_auth_error                              # noqa: E402


class FakeLink:
    def __init__(self):
        self.said = []

    def say(self, text, buttons=None):
        self.said.append(text)
        return True


class UnauthorizedError(Exception):
    """Как это выглядит у библиотеки: текст по-русски, кодов нет."""

    def __str__(self):
        return ("Не удалось подключиться к аккаунту Playerok. "
                "Может вы указали неверный token?")


class AuthDetectionTest(unittest.TestCase):
    """Проверка по тексту молча пропускала самый частый отказ во входе:
    у исключения библиотеки нет ни кода, ни слова unauthorized."""

    def test_library_exception_is_recognised(self):
        self.assertTrue(is_auth_error(UnauthorizedError()))

    def test_http_codes_are_recognised(self):
        self.assertTrue(is_auth_error(Exception("HTTP 401 Unauthorized")))
        self.assertTrue(is_auth_error(Exception("403 Forbidden")))

    def test_network_trouble_is_not_an_auth_error(self):
        """Иначе бот просил бы куки при каждом обрыве связи."""
        self.assertFalse(is_auth_error(Exception("Connection reset by peer")))
        self.assertFalse(is_auth_error(Exception("Read timed out")))

    def test_marketplace_refusals_are_not_auth_errors(self):
        self.assertFalse(is_auth_error(Exception("OUT_OF_STOCK")))
        self.assertFalse(is_auth_error(Exception("429 Too Many Requests")))


class AlarmTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.link = FakeLink()
        self.alarm = Alarm(self.link, "Уведомления", remind_after=100,
                           clock=lambda: self.now[0])

    def test_first_failure_is_reported(self):
        self.assertTrue(self.alarm.broken("вход не принят", COOKIES_ADVICE))
        self.assertIn("не работает", self.link.said[0])
        self.assertIn("Аккаунт", self.link.said[0])

    def test_repeats_are_silent(self):
        """Сообщение на каждой попытке — это спам за час."""
        self.alarm.broken("вход не принят")

        for _ in range(50):
            self.assertFalse(self.alarm.broken("вход не принят"))

        self.assertEqual(len(self.link.said), 1)

    def test_long_trouble_is_reminded_about(self):
        """Иначе про стоящую торговлю забудут."""
        self.alarm.broken("вход не принят")
        self.now[0] += 200

        self.assertTrue(self.alarm.broken("вход не принят"))

    def test_recovery_is_reported_once(self):
        self.alarm.broken("вход не принят")

        self.assertTrue(self.alarm.working())
        self.assertFalse(self.alarm.working())
        self.assertIn("снова работает", self.link.said[-1])

    def test_recovery_without_trouble_says_nothing(self):
        """Бот, который просто работает, не должен об этом рассказывать."""
        self.assertFalse(self.alarm.working())
        self.assertEqual(self.link.said, [])

    def test_trouble_after_recovery_is_reported_again(self):
        self.alarm.broken("вход не принят")
        self.alarm.working()

        self.assertTrue(self.alarm.broken("вход не принят"))

    def test_works_without_a_link(self):
        """Бот без телеграма не должен падать на попытке пожаловаться."""
        quiet = Alarm(None, "Что-то")

        self.assertTrue(quiet.broken("беда"))
        self.assertTrue(quiet.working())


if __name__ == "__main__":
    unittest.main()
