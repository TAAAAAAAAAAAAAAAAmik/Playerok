"""Тесты уведомлений. Сети не требуют — события поддельные.

Главное здесь не тексты, а что бот не превращается в шум: свои действия не
пересказывает, незнакомое молчит, длинное режет.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import notices                                                  # noqa: E402

ME = "me-1"


class User:
    def __init__(self, id, username):                          # noqa: A002
        self.id, self.username = id, username


class Item:
    def __init__(self, name):
        self.name = name


class Deal:
    def __init__(self, id="d1", user=None, item="80 Robux",
                 status_description=""):                        # noqa: A002
        self.id = id
        self.user = user or User("buyer-1", "gulnara")
        self.item = Item(item)
        self.status_description = status_description


class Message:
    def __init__(self, text="привет", user=None, images=None):
        self.text = text
        self.user = user or User("buyer-1", "gulnara")
        self.images = images or []


def event(name, **fields):
    """Поддельное событие: разбор смотрит на имя класса."""
    return type(name, (), fields)()


class BuyTest(unittest.TestCase):
    def test_purchase_is_reported_with_who_and_what(self):
        text = notices.describe(event("ItemPaidEvent", deal=Deal()), ME)

        self.assertIn("Покупка", text)
        self.assertIn("80 Robux", text)
        self.assertIn("gulnara", text)

    def test_link_to_the_deal_is_included(self):
        text = notices.describe(event("NewDealEvent", deal=Deal("d-77")), ME)

        self.assertIn("d-77", text)

    def test_our_own_purchase_is_not_reported(self):
        """Сделка, где покупатель мы сами, к торговле отношения не имеет."""
        deal = Deal(user=User(ME, "я"))

        self.assertEqual(notices.describe(event("ItemPaidEvent", deal=deal),
                                          ME), "")


class MessageTest(unittest.TestCase):
    def test_buyer_message_is_reported(self):
        text = notices.describe(event("NewMessageEvent",
                                      message=Message("а код когда?")), ME)

        self.assertIn("gulnara", text)
        self.assertIn("а код когда?", text)

    def test_our_own_message_is_not(self):
        """Продавец знает, что сам только что ответил."""
        mine = Message("держите код", user=User(ME, "я"))

        self.assertEqual(notices.describe(event("NewMessageEvent",
                                                message=mine), ME), "")

    def test_long_text_is_cut(self):
        long = Message("я" * 1000)
        text = notices.describe(event("NewMessageEvent", message=long), ME)

        self.assertLess(len(text), 400)
        self.assertIn("…", text)

    def test_picture_without_text_is_still_reported(self):
        picture = Message("", images=[object()])
        text = notices.describe(event("NewMessageEvent", message=picture), ME)

        self.assertIn("картинка", text)

    def test_empty_message_is_silence(self):
        self.assertEqual(notices.describe(
            event("NewMessageEvent", message=Message("")), ME), "")


class DealEventsTest(unittest.TestCase):
    def test_confirmed(self):
        text = notices.describe(event("DealConfirmedEvent", deal=Deal()), ME)

        self.assertIn("подтверждён", text)

    def test_problem_includes_the_reason(self):
        deal = Deal(status_description="код не активируется")
        text = notices.describe(event("DealHasProblemEvent", deal=deal), ME)

        self.assertIn("Проблема", text)
        self.assertIn("код не активируется", text)

    def test_problem_resolved(self):
        text = notices.describe(event("DealProblemResolvedEvent",
                                      deal=Deal()), ME)

        self.assertIn("решена", text)

    def test_review(self):
        text = notices.describe(event("NewReviewEvent", deal=Deal()), ME)

        self.assertIn("отзыв", text.lower())

    def test_refund_is_reported_because_it_is_money(self):
        text = notices.describe(event("DealRolledBackEvent", deal=Deal()), ME)

        self.assertIn("Возврат", text)


class NoiseTest(unittest.TestCase):
    """Шум перестают читать, и тогда теряется важное."""

    def test_unknown_event_is_silence(self):
        self.assertEqual(notices.describe(event("ЧтоТоНовое"), ME), "")
        self.assertEqual(notices.kind_of(event("ЧтоТоНовое")), "")

    def test_event_without_a_deal_is_silence(self):
        self.assertEqual(notices.describe(event("DealConfirmedEvent"), ME), "")

    def test_disabled_kind_is_silence(self):
        text = notices.describe(event("NewMessageEvent", message=Message()),
                                ME, enabled=("buy",))

        self.assertEqual(text, "")

    def test_sent_is_off_by_default(self):
        """Это наше же действие: бот сам помечает сделку отправленной."""
        self.assertNotIn("sent", notices.DEFAULT)
        self.assertEqual(notices.describe(event("ItemSentEvent", deal=Deal()),
                                          ME), "")

    def test_sent_can_be_switched_on(self):
        text = notices.describe(event("ItemSentEvent", deal=Deal()), ME,
                                enabled=("sent",))

        self.assertIn("Отправлено", text)


if __name__ == "__main__":
    unittest.main()
