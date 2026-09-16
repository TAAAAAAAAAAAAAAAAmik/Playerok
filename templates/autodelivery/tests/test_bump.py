"""Тесты счёта одинаковых объявлений. Сети не требуют.

Счёт трогает цену, а цену продавец назначил сам. Ошибка здесь — это
проданное дешевле задуманного или дороже обещанного.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from bump import LIMIT, STEP, Ledger                            # noqa: E402
from store import JsonStore                                     # noqa: E402


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "s.json")
        self.led = Ledger(JsonStore(self.path))

    def fill(self, times, nominal=1000, price=1320):
        for _ in range(times):
            got, _ = self.led.price_for(nominal, price)
            self.led.remember(nominal, got)

    def test_the_first_three_keep_the_price(self):
        for _ in range(LIMIT):
            self.assertEqual(self.led.price_for(1000, 1320), (1320, 0))
            self.led.remember(1000, 1320)

    def test_the_fourth_costs_a_rouble_more(self):
        self.fill(LIMIT)

        self.assertEqual(self.led.price_for(1000, 1320), (1320 + STEP, STEP))

    def test_the_price_keeps_climbing_when_the_new_one_fills_up(self):
        self.fill(LIMIT * 2)

        price, up = self.led.price_for(1000, 1320)

        self.assertEqual(price, 1320 + STEP * 2)
        self.assertEqual(up, STEP * 2)

    def test_another_nominal_is_counted_separately(self):
        """Иначе робуксы на тысячу двигали бы цену робуксам на сотню."""
        self.fill(LIMIT)

        self.assertEqual(self.led.price_for(400, 1320), (1320, 0))

    def test_another_price_is_counted_separately(self):
        self.fill(LIMIT)

        self.assertEqual(self.led.price_for(1000, 990), (990, 0))

    def test_a_paid_listing_does_not_count(self):
        """Платное продавец оплатил сам — считать его в тот же лимит
        значило бы тратить его деньги и следом сдвигать цену."""
        self.fill(LIMIT)
        self.led.forget(1000, 1320)

        self.assertEqual(self.led.price_for(1000, 1320), (1320, 0))

    def test_forgetting_what_was_never_counted_is_harmless(self):
        self.led.forget(1000, 1320)

        self.assertEqual(self.led.count(1000, 1320), 0)

    def test_the_count_survives_a_restart(self):
        """Без этого после каждого перезапуска бот ставил бы четвёртое,
        пятое и шестое объявление по одной цене — то есть ровно то, от
        чего счёт и заведён."""
        self.fill(LIMIT)
        again = Ledger(JsonStore(self.path))

        self.assertEqual(again.price_for(1000, 1320), (1320 + STEP, STEP))

    def test_the_climb_has_a_limit(self):
        """Товар, у которого уже висит полсотни копий, не должен молча
        уехать по цене далеко от задуманной."""
        for _ in range(500):
            price, _ = self.led.price_for(1000, 1320)
            self.led.remember(1000, price)

        price, up = self.led.price_for(1000, 1320)

        self.assertLess(up, 100)


class RulesTest(unittest.TestCase):
    """Настройки счёта: предел, шаг, выключение."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "s.json")
        self.led = Ledger(JsonStore(self.path))

    def test_defaults_are_what_it_did_before_settings(self):
        rules = self.led.rules()

        self.assertTrue(rules["enabled"])
        self.assertEqual(rules["limit"], LIMIT)
        self.assertEqual(rules["step"], STEP)
        self.assertTrue(rules["vary"])

    def test_a_bigger_limit_is_respected(self):
        self.led.set_limit(5)

        for _ in range(5):
            self.assertEqual(self.led.price_for(1000, 500)[1], 0)
            self.led.remember(1000, 500)

        self.assertEqual(self.led.price_for(1000, 500), (501, 1))

    def test_a_bigger_step_is_respected(self):
        self.led.set_step(10)

        for _ in range(LIMIT):
            self.led.remember(1000, 500)

        self.assertEqual(self.led.price_for(1000, 500), (510, 10))

    def test_switching_it_off_leaves_the_price_alone(self):
        self.led.set_limit(1)
        self.led.remember(1000, 500)
        self.led.set_enabled(False)

        self.assertEqual(self.led.price_for(1000, 500), (500, 0))

    def test_settings_survive_a_restart(self):
        self.led.set_limit(7)
        self.led.set_step(25)

        again = Ledger(JsonStore(self.path))

        self.assertEqual(again.rules()["limit"], 7)
        self.assertEqual(again.rules()["step"], 25)

    def test_nonsense_falls_back_to_the_default(self):
        """Ноль в пределе означал бы, что цена растёт у каждого
        объявления, а нулевой шаг — вечный круг подъёма ни о чём."""
        self.led.set_limit(0)
        self.led.set_step(0)

        self.assertGreaterEqual(self.led.rules()["limit"], 1)
        self.assertGreaterEqual(self.led.rules()["step"], 1)

    def test_a_word_instead_of_a_number_changes_nothing_dangerous(self):
        self.led.set_limit("много")

        self.assertEqual(self.led.rules()["limit"], LIMIT)

    def test_resetting_forgets_the_count_but_not_the_settings(self):
        """Счёт про снятые руками объявления не знает и продолжал бы
        поднимать цену на пустом месте."""
        self.led.set_limit(5)

        for _ in range(3):
            self.led.remember(1000, 500)

        self.assertEqual(self.led.reset(), 1)
        self.assertEqual(self.led.count(1000, 500), 0)
        self.assertEqual(self.led.rules()["limit"], 5)

    def test_pairs_counts_what_is_under_the_ledger(self):
        self.led.remember(1000, 500)
        self.led.remember(1000, 500)
        self.led.remember(400, 200)

        self.assertEqual(self.led.pairs(), 2)


if __name__ == "__main__":
    unittest.main()
