"""Тесты входа по коду на почту. Сети не требуют — площадка поддельная.

Путь взят из чужого SDK и живым вызовом не проверен, поэтому здесь
проверяется не «работает ли вход», а что бот честно скажет о каждом
возможном отказе и не соврёт про сессию, которой нет.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import emailauth                                                # noqa: E402

UA = "Mozilla/5.0 (Android 16; Mobile) Firefox/155.0"
TOKEN = "eyJ" + "a" * 60
GOOD = "seller@example.com"


class Response:
    def __init__(self, status=200, body=None, cookies=None, text="",
                 headers=None):
        self.status_code = status
        self._body = body
        self.cookies = cookies or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("не JSON")

        return self._body


class Platform:
    """Поддельная площадка: отвечает заготовленным и помнит запросы."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.cookies = {}

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append((url, json or {}, headers or {}))

        return self.answers.pop(0) if self.answers else Response()


class ValidationTest(unittest.TestCase):
    def test_email_is_checked_before_the_network(self):
        platform = Platform()
        ok, why = emailauth.send_code("почта", UA, platform)

        self.assertFalse(ok)
        self.assertEqual(platform.calls, [])
        self.assertIn("почт", why)

    def test_code_must_be_six_digits(self):
        platform = Platform()
        cookies, why = emailauth.confirm(GOOD, "123", UA, platform)

        self.assertEqual(cookies, "")
        self.assertIn("шесть цифр", why)
        self.assertEqual(platform.calls, [])

    def test_valid_shapes_pass(self):
        self.assertTrue(emailauth.valid_email(GOOD))
        self.assertFalse(emailauth.valid_email("a@b"))
        self.assertTrue(emailauth.valid_code("123456"))
        self.assertFalse(emailauth.valid_code("12345a"))


class SendTest(unittest.TestCase):
    def test_code_is_requested_at_the_right_address(self):
        platform = Platform(Response(200, {}))
        ok, _ = emailauth.send_code(GOOD, UA, platform)
        url, body, headers = platform.calls[0]

        self.assertTrue(ok)
        self.assertTrue(url.endswith("/auth/send-otp"))
        self.assertEqual(body, {"email": GOOD})
        self.assertEqual(headers["User-Agent"], UA)

    def test_rate_limit_says_how_long_to_wait(self):
        platform = Platform(Response(429, headers={"Retry-After": "60"}))
        ok, why = emailauth.send_code(GOOD, UA, platform)

        self.assertFalse(ok)
        self.assertIn("60", why)

    def test_unknown_email_is_named_as_such(self):
        platform = Platform(Response(404))
        ok, why = emailauth.send_code(GOOD, UA, platform)

        self.assertFalse(ok)
        self.assertIn("нет", why)

    def test_network_trouble_is_not_blamed_on_the_email(self):
        class Broken:
            def post(self, *a, **kw):
                raise OSError("сеть недоступна")

        ok, why = emailauth.send_code(GOOD, UA, Broken())

        self.assertFalse(ok)
        self.assertIn("не достучался", why)


class ConfirmTest(unittest.TestCase):
    def test_session_comes_back_as_a_cookie_string(self):
        platform = Platform(Response(200, {"requiresTwoFactor": False},
                                     cookies={"token": TOKEN,
                                              "__ddg3": "abc"}))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(why, "")
        self.assertTrue(cookies.startswith(f"token={TOKEN}"))
        self.assertIn("__ddg3=abc", cookies)

    def test_the_result_is_what_the_adapter_expects(self):
        """Строка должна годиться туда же, куда идут куки из браузера."""
        from owner import normalize_cookies

        platform = Platform(Response(200, {}, cookies={"token": TOKEN}))
        cookies, _ = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(normalize_cookies(cookies), cookies)

    def test_wrong_code_is_named_plainly(self):
        platform = Platform(Response(401))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(cookies, "")
        self.assertIn("неверный", why)

    def test_two_factor_is_admitted_not_hidden(self):
        """Молчать тут — оставить человека гадать, почему не вышло."""
        platform = Platform(Response(200, {"requiresTwoFactor": True}))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(cookies, "")
        self.assertIn("двухфакторн", why)
        self.assertIn("куками", why)

    def test_bot_protection_page_is_recognised(self):
        """Не-JSON здесь обычно страница защиты, а не поломка неизвестно
        чего."""
        platform = Platform(Response(200, None, text="<html>проверка</html>"))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(cookies, "")
        self.assertIn("защиту от ботов", why.replace("защита", "защиту"))

    def test_code_accepted_but_no_session_is_not_called_success(self):
        """Соврать про сессию хуже, чем отказать."""
        platform = Platform(Response(200, {"requiresTwoFactor": False}))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(cookies, "")
        self.assertIn("сессию площадка не выдала", why)

    def test_cookies_without_a_token_are_not_a_session(self):
        platform = Platform(Response(200, {}, cookies={"__ddg3": "abc"}))
        cookies, why = emailauth.confirm(GOOD, "123456", UA, platform)

        self.assertEqual(cookies, "")
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
