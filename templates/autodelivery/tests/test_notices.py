"""Тесты уведомлений. Сети не требуют — события поддельные.

Главное здесь не тексты, а что бот не превращается в шум: свои действия не
пересказывает, незнакомое молчит, длинное режет.
"""
from __future__ import annotations

import os
import sys
import tempfile
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


class IdentityTest(unittest.TestCase):
    """Что считать тем же самым событием."""

    def test_message_is_known_by_its_number(self):
        one = event("NewMessageEvent", message=Message())
        one.message.id = "m-1"

        self.assertEqual(notices.identity(one), "message:m-1")

    def test_deal_events_differ_by_kind(self):
        """Одна сделка проходит покупку, подтверждение и отзыв — это разные
        события, а два «подтверждено» подряд уже повтор."""
        deal = Deal("d-1")
        buy = notices.identity(event("ItemPaidEvent", deal=deal))
        done = notices.identity(event("DealConfirmedEvent", deal=deal))

        self.assertNotEqual(buy, done)
        self.assertIn("d-1", buy)

    def test_unknown_event_has_no_identity(self):
        self.assertEqual(notices.identity(event("ЧтоТоНовое")), "")


class SeenTest(unittest.TestCase):
    """Память слушателя живёт внутри него и исчезает при обрыве связи.
    Без своей бот показывал бы недавнее заново после каждого сбоя."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state", "seen.json")
        self.seen = notices.Seen(self.path)

    def message(self, number):
        one = event("NewMessageEvent", message=Message())
        one.message.id = number

        return one

    def test_first_time_is_fresh(self):
        self.assertTrue(self.seen.fresh(self.message("m-1")))

    def test_second_time_is_not(self):
        self.seen.fresh(self.message("m-1"))

        self.assertFalse(self.seen.fresh(self.message("m-1")))

    def test_different_events_do_not_shadow_each_other(self):
        self.seen.fresh(self.message("m-1"))

        self.assertTrue(self.seen.fresh(self.message("m-2")))

    def test_memory_survives_a_restart(self):
        """Сторож перезапускает бота при каждом сбое."""
        self.seen.fresh(self.message("m-1"))

        self.assertFalse(notices.Seen(self.path).fresh(self.message("m-1")))

    def test_event_without_identity_is_let_through(self):
        """Пропустить настоящую покупку хуже, чем показать её дважды."""
        odd = event("ItemPaidEvent", deal=Deal(id=""))

        self.assertTrue(self.seen.fresh(odd))
        self.assertTrue(self.seen.fresh(odd))

    def test_memory_does_not_grow_forever(self):
        small = notices.Seen(self.path, limit=3)

        for number in range(10):
            small.fresh(self.message(f"m-{number}"))

        self.assertEqual(len(small.ids), 3)
        self.assertFalse(small.fresh(self.message("m-9")))
        self.assertTrue(small.fresh(self.message("m-0")))

    def test_broken_file_is_not_a_crash(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{сломано")

        self.assertEqual(notices.Seen(self.path).ids, [])

    def test_without_a_file_it_still_works_in_memory(self):
        memory = notices.Seen("")
        memory.fresh(self.message("m-1"))

        self.assertFalse(memory.fresh(self.message("m-1")))


if __name__ == "__main__":
    unittest.main()
