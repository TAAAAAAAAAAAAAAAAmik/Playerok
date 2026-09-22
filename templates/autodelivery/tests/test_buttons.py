"""Значения кнопок обязаны влезать в лимит Telegram. Сети не требуют.

У Telegram на `callback_data` 64 БАЙТА, а не знака. Кириллица — два байта
на знак, эмодзи — четыре: «🥳промокодом🥳 автовыдача😎» это 31 знак и 63
байта. Одно значение длиннее — и площадка отвергает ВСЮ клавиатуру
целиком, а сообщение не отправляется вовсе.

Со стороны это выглядит как «кнопки не нажимаются»: экран остался
прежним, потому что новый не ушёл. Ни ошибки, ни следа — поэтому и
проверяем отдельным тестом.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import owner                                                   # noqa: E402

LIMIT = 64


class ShortDataTest(unittest.TestCase):
    def test_a_short_value_is_untouched(self):
        self.assertEqual(owner.short_data("ст:g0"), "ст:g0")

    def test_a_long_value_is_cut_by_bytes(self):
        got = owner.short_data("я" * 100)

        self.assertLessEqual(len(got.encode()), LIMIT)

    def test_a_character_is_never_torn_in_half(self):
        """Обрезка посреди символа даёт значение, которое не прочитать."""
        got = owner.short_data("🥳" * 40)

        self.assertEqual(got, "🥳" * 16)
        self.assertLessEqual(len(got.encode()), LIMIT)

    def test_exactly_the_limit_passes(self):
        value = "a" * LIMIT

        self.assertEqual(owner.short_data(value), value)


class KeyboardTest(unittest.TestCase):
    def test_every_value_fits(self):
        rows = [[("подпись", "🥳промокодом🥳 автовыдача😎" * 3)]]
        keys = owner.keyboard(rows)["inline_keyboard"]

        for row in keys:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()),
                                     LIMIT)

    def test_the_label_is_not_cut(self):
        """Подпись видит человек, её длина Telegram не ограничивает так
        строго."""
        long = "очень длинная подпись " * 3
        keys = owner.keyboard([[(long, "x")]])["inline_keyboard"]

        self.assertEqual(keys[0][0]["text"], long)


class MenuTest(unittest.TestCase):
    """Постоянные кнопки бота — те, что ездят в каждом экране."""

    def values(self, rows):
        return [value for row in rows or [] for _, value in row]

    def test_the_main_menu_fits(self):
        import item_bot

        for value in self.values(item_bot.MENU):
            self.assertLessEqual(len(str(value).encode()), LIMIT, value)

    def test_the_prefixes_leave_room(self):
        """Приставка плюс id должны влезать: id у площадки — uuid из 36
        знаков."""
        import item_bot

        uuid = "1f1b251a-e3f3-68b0-3e57-3ba46de2044a"

        for name in dir(item_bot):
            if not name.startswith("PICK"):
                continue

            prefix = getattr(item_bot, name)

            if not isinstance(prefix, str):
                continue

            self.assertLessEqual(len((prefix + uuid).encode()), LIMIT,
                                 f"{name}={prefix!r}")


if __name__ == "__main__":
    unittest.main()
