"""Поднятие объявлений: когда, каким статусом и за какие деньги.

Здесь проверяется прежде всего то, что бот НЕ тратит. Цикл, который
работает сам и списывает деньги без спроса, — худшее, что можно
придумать, и все три замка (только бесплатное, потолок на сутки, предел
цены) стоят именно поэтому.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import boost                                                    # noqa: E402


class Status:
    def __init__(self, id, price, name="Премиум", period=7):   # noqa: A002
        self.id, self.price = id, price
        self.name, self.period = name, period


class Item:
    def __init__(self, id, name="товар", price=149):           # noqa: A002
        self.id, self.name, self.price = id, name, price


FREE = Status("s0", 0, "Обычный")
CHEAP = Status("s1", 15)
DEAR = Status("s2", 300)

NOW = 1_700_000_000.0


class SettingsTest(unittest.TestCase):
    def test_it_starts_switched_off(self):
        self.assertFalse(boost.settings_of({})["on"])

    def test_paid_is_forbidden_by_default(self):
        conf = boost.settings_of({})

        self.assertFalse(conf["paid"])
        self.assertEqual(conf["limit"], 0)
        self.assertEqual(conf["max_price"], 0)

    def test_what_was_set_up_survives_an_update(self):
        conf = boost.settings_of({"boost": {"on": True, "every": 3}})

        self.assertTrue(conf["on"])
        self.assertEqual(conf["every"], 3)
        self.assertIn("limit", conf)


class PickTest(unittest.TestCase):
    def conf(self, **kw):
        conf = boost.settings_of({})
        conf.update(kw)

        return conf

    def test_the_free_one_is_taken_without_questions(self):
        status, why = boost.pick([DEAR, FREE], self.conf(), NOW)

        self.assertIs(status, FREE)
        self.assertEqual(why, "")

    def test_a_paid_one_is_refused_while_it_is_not_allowed(self):
        status, why = boost.pick([CHEAP], self.conf(), NOW)

        self.assertIsNone(status)
        self.assertIn("не разрешено", why)

    def test_an_allowed_paid_one_is_taken(self):
        conf = self.conf(paid=True, limit=100, max_price=20)
        status, why = boost.pick([DEAR, CHEAP], conf, NOW)

        self.assertIs(status, CHEAP)

    def test_too_dear_is_refused_by_name(self):
        """Согласие «до 20 ₽» — это не согласие на 300 ₽."""
        conf = self.conf(paid=True, limit=1000, max_price=20)
        status, why = boost.pick([DEAR], conf, NOW)

        self.assertIsNone(status)
        self.assertIn("300", why)
        self.assertIn("20", why)

    def test_the_daily_ceiling_stops_it(self):
        conf = self.conf(paid=True, limit=100, max_price=50,
                         spent=95.0, day=time.strftime(
                             "%Y-%m-%d", time.localtime(NOW)))
        status, why = boost.pick([CHEAP], conf, NOW)

        self.assertIsNone(status)
        self.assertIn("осталось", why)

    def test_a_new_day_opens_the_ceiling_again(self):
        conf = self.conf(paid=True, limit=100, max_price=50,
                         spent=95.0, day="2000-01-01")
        status, why = boost.pick([CHEAP], conf, NOW)

        self.assertIs(status, CHEAP)
        self.assertEqual(conf["spent"], 0.0)

    def test_no_statuses_at_all_is_said_plainly(self):
        status, why = boost.pick([], self.conf(), NOW)

        self.assertIsNone(status)
        self.assertIn("не предложила", why)

    def test_a_status_without_a_price_counts_as_paid(self):
        """Принять платный за бесплатный значит списать деньги молча."""
        nameless = Status("s9", None)
        status, why = boost.pick([nameless], self.conf(), NOW)

        self.assertIsNone(status)


class DueTest(unittest.TestCase):
    def conf(self, every=8, last=None):
        conf = boost.settings_of({})
        conf["every"] = every
        conf["last"] = dict(last or {})

        return conf

    def test_an_unknown_item_goes_first(self):
        conf = self.conf(last={"i2": NOW - 100})
        rows = boost.due([Item("i2"), Item("i1")], conf, NOW)

        self.assertEqual([i.id for i in rows], ["i1"])

    def test_a_freshly_lifted_one_is_left_alone(self):
        conf = self.conf(every=8, last={"i1": NOW - 3600})

        self.assertEqual(boost.due([Item("i1")], conf, NOW), [])

    def test_after_the_pause_it_comes_back(self):
        conf = self.conf(every=8, last={"i1": NOW - 9 * 3600})

        self.assertEqual(len(boost.due([Item("i1")], conf, NOW)), 1)

    def test_the_oldest_are_lifted_first(self):
        conf = self.conf(every=1, last={"i1": NOW - 2 * 3600,
                                        "i2": NOW - 90 * 3600})
        rows = boost.due([Item("i1"), Item("i2")], conf, NOW)

        self.assertEqual([i.id for i in rows], ["i2", "i1"])

    def test_not_more_than_a_handful_at_once(self):
        """Каждое поднятие — два запроса, а площадка просит сбавлять темп
        быстрее, чем кажется."""
        items = [Item(f"i{n}") for n in range(30)]
        rows = boost.due(items, self.conf(), NOW)

        self.assertEqual(len(rows), boost.AT_ONCE)


class MemoryTest(unittest.TestCase):
    def test_spending_is_written_down(self):
        conf = boost.settings_of({})
        boost.spend(conf, 15, NOW)
        boost.spend(conf, 15, NOW)

        self.assertEqual(conf["spent"], 30.0)

    def test_a_lift_is_remembered(self):
        conf = boost.settings_of({})
        boost.remember(conf, "i1", NOW)

        self.assertEqual(conf["last"]["i1"], NOW)
        self.assertEqual(conf["count"], 1)

    def test_old_items_are_forgotten(self):
        conf = boost.settings_of({})

        for number in range(boost.KEEP + 10):
            boost.remember(conf, f"i{number}", NOW + number)

        self.assertLessEqual(len(conf["last"]), boost.KEEP)

    def test_left_today_counts_what_is_left(self):
        conf = boost.settings_of({})
        conf.update({"limit": 100, "spent": 40.0,
                     "day": time.strftime("%Y-%m-%d", time.localtime(NOW))})

        self.assertEqual(boost.left_today(conf, NOW), 60.0)


class ReportTest(unittest.TestCase):
    def test_free_and_successful_is_said_silently(self):
        """Поднятие идёт каждые несколько часов — письмо о каждом
        превратит уведомления в шум."""
        self.assertEqual(boost.report(["товар"], 0.0), "")

    def test_money_is_always_named(self):
        said = boost.report(["товар", "второй"], 30.0)

        self.assertIn("30 ₽", said)
        self.assertIn("2", said)

    def test_a_refusal_is_named_too(self):
        said = boost.report([], 0.0, [("80 робуксов", "платное не разрешено")])

        self.assertIn("80 робуксов", said)
        self.assertIn("не разрешено", said)


if __name__ == "__main__":
    unittest.main()
