"""Чаты площадки так, как их читает продавец с телефона.

Ни сети, ни площадки: на вход объекты, на выход строки. Проверяется то,
из-за чего список чатов был бы бесполезен: чужое имя вместо своего,
переписка задом наперёд и полотно вместо строки.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import chats                                                    # noqa: E402

ME = "me-1"


class User:
    def __init__(self, id="", username=""):                    # noqa: A002
        self.id, self.username = id, username


class Item:
    def __init__(self, name=""):
        self.name = name


class Deal:
    def __init__(self, item=None):
        self.item = item


class Message:
    def __init__(self, text="", user=None, created_at="", images=None):
        self.text, self.user = text, user
        self.created_at, self.images = created_at, images or []


class Chat:
    def __init__(self, id="c1", users=None, unread=0, deals=None,  # noqa
                 last=None):
        self.id = id
        self.users = users or []
        self.unread_messages_counter = unread
        self.deals = deals or []
        self.last_message = last


BUYER = User("u-9", "vasya")
SELLER = User(ME, "Tamik791021")


class WhoTest(unittest.TestCase):
    def test_we_are_crossed_out_of_the_list(self):
        """Иначе половина списка — собственный ник продавца."""
        chat = Chat(users=[SELLER, BUYER])

        self.assertEqual(chats.who_of(chat, ME), "vasya")

    def test_without_users_it_says_buyer(self):
        self.assertEqual(chats.who_of(Chat(), ME), "покупатель")

    def test_an_unknown_me_does_not_hide_anyone(self):
        chat = Chat(users=[BUYER])

        self.assertEqual(chats.who_of(chat, ""), "vasya")


class LineTest(unittest.TestCase):
    def chat(self, **kw):
        return Chat(users=[SELLER, BUYER],
                    deals=[Deal(Item("80 РОБУКСОВ ПРОМОКОДОМ"))],
                    last=Message("как активировать?", BUYER), **kw)

    def test_the_line_says_who_what_and_the_last_word(self):
        said = chats.line_of(self.chat(), ME)

        self.assertIn("vasya", said)
        self.assertIn("80 РОБУКСОВ", said)
        self.assertIn("как активировать", said)

    def test_unread_is_marked(self):
        self.assertTrue(chats.line_of(self.chat(unread=3), ME).startswith("🔴"))
        self.assertTrue(chats.line_of(self.chat(), ME).startswith("▫️"))

    def test_the_button_carries_the_count(self):
        said = chats.label_of(self.chat(unread=3), ME)

        self.assertIn("3", said)
        self.assertIn("vasya", said)

    def test_a_long_line_stays_a_line(self):
        chat = Chat(users=[BUYER],
                    last=Message("а" * 500, BUYER))

        self.assertLess(len(chats.line_of(chat, ME)), 120)

    def test_a_picture_is_named(self):
        chat = Chat(users=[BUYER], last=Message("", BUYER, images=["a.jpg"]))

        self.assertIn("картинка", chats.line_of(chat, ME))


class TalkTest(unittest.TestCase):
    def test_the_newest_message_ends_up_last(self):
        """Площадка отдаёт свежие первыми — в переписке это читается
        задом наперёд."""
        rows = [Message("третье", BUYER), Message("второе", SELLER),
                Message("первое", BUYER)]
        talk = chats.talk_of(rows, ME)

        self.assertIn("первое", talk[0])
        self.assertIn("третье", talk[-1])

    def test_our_own_words_are_signed_as_ours(self):
        talk = chats.talk_of([Message("вот код", SELLER)], ME)

        self.assertTrue(talk[0].startswith("Я"))

    def test_the_buyer_is_named(self):
        talk = chats.talk_of([Message("привет", BUYER)], ME)

        self.assertTrue(talk[0].startswith("vasya"))

    def test_empty_messages_are_skipped(self):
        talk = chats.talk_of([Message("", BUYER), Message("привет", BUYER)],
                             ME)

        self.assertEqual(len(talk), 1)

    def test_only_the_asked_number_is_shown(self):
        rows = [Message(f"строка {n}", BUYER) for n in range(30)]

        self.assertEqual(len(chats.talk_of(rows, ME, limit=5)), 5)


class WhenTest(unittest.TestCase):
    def test_today_shows_only_the_time(self):
        today = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        said = chats.when_of(Message("привет", BUYER, created_at=today))

        self.assertEqual(len(said), 5)
        self.assertIn(":", said)

    def test_another_day_shows_the_date(self):
        said = chats.when_of(Message("привет", BUYER,
                                     created_at="2024-09-22T14:03:00"))

        self.assertEqual(said, "22.09 14:03")

    def test_an_unknown_format_is_shown_as_it_came(self):
        """Чужой формат честнее выдуманного."""
        said = chats.when_of(Message("привет", BUYER, created_at="вчера"))

        self.assertEqual(said, "вчера")

    def test_no_date_is_no_line(self):
        self.assertEqual(chats.when_of(Message("привет", BUYER)), "")


if __name__ == "__main__":
    unittest.main()
