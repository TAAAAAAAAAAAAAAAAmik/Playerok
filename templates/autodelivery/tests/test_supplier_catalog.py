"""Тесты разбора каталога поставщика. Сети не требуют.

Здесь ошибка стоит денег дважды: пропущенный номинал оставляет заказ без
выдачи, а придуманный покупает не то на настоящие деньги.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from catalog import (Card, denominations_for,                   # noqa: E402
                     denominations_from, find_service, items_of,
                     is_card_order, match_denomination, matches_service,
                     region_of_service, services_for)


class ShapeTest(unittest.TestCase):
    """Имена полей у поставщиков разные и меняются между версиями."""

    def test_items_are_found_under_any_known_name(self):
        for field in ("items", "denominations", "nominals", "values"):
            got = items_of({field: [{"id": "i1"}]})

            self.assertEqual(len(got), 1, field)

    def test_unknown_shape_gives_nothing_not_a_crash(self):
        self.assertEqual(items_of({}), [])
        self.assertEqual(items_of(None), [])
        self.assertEqual(items_of({"items": "не список"}), [])

    def test_service_is_found_in_any_wrapper(self):
        service = {"id": "svc-1", "items": []}

        for catalog in ([service],
                        {"services": [service]},
                        {"data": {"items": [service]}},
                        {"page": {"items": [service]}}):
            self.assertIsNotNone(find_service(catalog, "svc-1"), catalog)

    def test_missing_service_is_none(self):
        self.assertIsNone(find_service({"services": []}, "svc-1"))
        self.assertIsNone(find_service({"services": [{"id": "x"}]}, ""))


class DenominationTest(unittest.TestCase):
    def service(self, *items):
        return {"id": "svc-1", "items": list(items)}

    def test_numeric_field_is_used_when_present(self):
        rows = denominations_from(
            self.service({"id": "i1", "value": 1000, "price": 9.9,
                          "inStock": 5}), region="GL")

        self.assertEqual(rows[0].value, 1000)
        self.assertEqual(rows[0].price, 9.9)
        self.assertEqual(rows[0].in_stock, 5)
        self.assertEqual(rows[0].region, "GL")

    def test_value_is_read_from_the_name_when_there_is_no_field(self):
        """У части услуг число живёт только в названии — иначе такой
        номинал не нашёлся бы никогда."""
        rows = denominations_from(
            self.service({"id": "i1", "name": "Roblox 100 Robux (Global)"}))

        self.assertEqual(rows[0].value, 100)

    def test_entry_without_a_value_is_skipped(self):
        """Подставить догадку значило бы купить не то за настоящие деньги."""
        rows = denominations_from(
            self.service({"id": "i1", "name": "подарочная карта"}))

        self.assertEqual(rows, [])

    def test_entry_without_an_id_is_skipped(self):
        """Без номера покупать нечего."""
        rows = denominations_from(self.service({"value": 100}))

        self.assertEqual(rows, [])

    def test_negative_stock_counts_as_none(self):
        """Иначе такой номинал выглядел бы доступным."""
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "inStock": -3}))

        self.assertEqual(rows[0].in_stock, 0)

    def test_stock_field_is_found_under_any_name(self):
        for field in ("inStock", "stock", "quantity", "available", "count"):
            rows = denominations_from(
                self.service({"id": "i1", "value": 100, field: 7}))

            self.assertEqual(rows[0].in_stock, 7, field)

    def test_price_written_with_a_comma_is_understood(self):
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "price": "1,25"}))

        self.assertEqual(rows[0].price, 1.25)

    def test_broken_price_does_not_lose_the_denomination(self):
        """Цена нужна для выбора дешёвого, но без неё номинал всё ещё
        годен — потерять его хуже."""
        rows = denominations_from(
            self.service({"id": "i1", "value": 100, "price": "бесплатно"}))

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].price)


class TogetherTest(unittest.TestCase):
    """Разобранный каталог должен годиться подбору номинала как есть."""

    CATALOG = {"services": [{"id": "svc-gl", "items": [
        {"id": "i1", "name": "Roblox 80 Robux", "price": 1.0, "inStock": 4},
        {"id": "i2", "name": "Roblox 100 Robux", "price": 1.2, "inStock": 0},
        {"id": "i3", "name": "Roblox 100 Robux", "price": 1.1, "inStock": 9},
    ]}]}

    def rows(self):
        return denominations_from(find_service(self.CATALOG, "svc-gl"),
                                  "svc-gl", "GL")

    def test_exact_denomination_is_found(self):
        got, why = match_denomination(self.rows(), "GL", 80)

        self.assertEqual(why, "")
        self.assertEqual(got.item_id, "i1")

    def test_out_of_stock_copy_is_skipped_for_the_live_one(self):
        got, _ = match_denomination(self.rows(), "GL", 100)

        self.assertEqual(got.item_id, "i3")

    def test_missing_denomination_lists_what_there_is(self):
        got, why = match_denomination(self.rows(), "GL", 500)

        self.assertIsNone(got)
        self.assertIn("80", why)




# Кусок живого каталога, из-за которого отбор идёт по подкатегории, а не по
# слову: слово «xbox» встречается во всех четырёх записях, а гифт-карта
# среди них одна.
XBOX_LIKE = {"services": [
    {"id": "s-card", "name": "Xbox Gift Card 10 USD",
     "subcategoryName": "Xbox Gift Cards",
     "items": [{"id": "i1", "value": 10, "inStock": 5, "price": 8.4}]},
    {"id": "s-pass", "name": "Xbox Game Pass Ultimate 1 месяц",
     "subcategoryName": "Xbox Gift Cards",
     "items": [{"id": "i2", "value": 1, "inStock": 5, "price": 9.9}]},
    {"id": "s-robux", "name": "Roblox Wallet Code | XBox",
     "subcategoryName": "Roblox Gift Cards",
     "items": [{"id": "i3", "value": 800, "inStock": 5, "price": 7.0}]},
    {"id": "s-acc", "name": "Xbox аккаунт с играми",
     "subcategoryName": "Game Accounts",
     "items": [{"id": "i4", "value": 1, "inStock": 5, "price": 20.0}]},
]}

XBOX = Card(slug="xbox", title="Xbox", subcategory="Xbox Gift Cards",
            name_must_not_have="game pass")
ROBUX = Card(slug="robux", title="Roblox", subcategory="Roblox Gift Cards")


class SubcategoryTest(unittest.TestCase):
    """Отбор услуги: по точной подкатегории, не по слову.

    На живом каталоге слово «xbox» находит 47 услуг, гифт-карт среди них 16.
    Остальное — подписки, ключи игр, аккаунты и `Roblox Wallet Code | XBox`,
    то есть товар соседней карты. Взяв его, бот выдал бы покупателю код
    Roblox вместо карты Xbox — на деньги продавца.
    """

    def test_only_the_gift_card_is_taken(self):
        got = services_for(XBOX, XBOX_LIKE)

        self.assertEqual([s["id"] for s in got], ["s-card"])

    def test_the_neighbours_code_is_not_stolen(self):
        got = services_for(ROBUX, XBOX_LIKE)

        self.assertEqual([s["id"] for s in got], ["s-robux"])

    def test_a_word_in_the_name_is_not_enough(self):
        card = Card(slug="x", title="X", subcategory="Xbox Gift Cards")

        self.assertFalse(matches_service(
            card, {"name": "Xbox Gift Card", "subcategoryName": "Other"}))

    def test_must_not_have_separates_two_plugins_in_one_subcategory(self):
        pass_service = XBOX_LIKE["services"][1]

        self.assertFalse(matches_service(XBOX, pass_service))

    def test_the_veto_also_keeps_the_wrong_ORDER_away(self):
        """Не только услугу поставщика, но и заказ на витрине.

        «Xbox Game Pass Ultimate 1 месяц» узнаётся по слову «xbox», а
        номинал из названия — 1: без запрета бот купил бы гифт-карту на
        доллар вместо подписки. Деньги списаны, покупатель без подписки.
        """
        card = Card(slug="xbox", title="Xbox", keywords=("xbox", "иксбокс"),
                    name_must_not_have=("game pass", "геймпасс"))

        self.assertTrue(is_card_order(card, "Xbox Gift Card 10 USD"))
        self.assertFalse(is_card_order(card, "Xbox Game Pass 1 месяц"))
        self.assertFalse(is_card_order(card, "Иксбокс геймпасс 12 мес"))

    def test_the_veto_outranks_the_sellers_own_keyword(self):
        """Слово продавца отделяет его товары от чужих, а запрет говорит
        о другом: «здесь другой товар». Второе сильнее."""
        card = Card(slug="xbox", title="Xbox",
                    name_must_not_have="game pass")

        self.assertFalse(is_card_order(card, "мой xbox game pass", "мой"))
        self.assertTrue(is_card_order(card, "мой xbox 10$", "мой"))

    def test_several_spellings_of_must_have(self):
        card = Card(slug="n", title="N", subcategory="Nintendo",
                    name_must_have=("gift", "подарочн"))
        shared = {"subcategoryName": "Nintendo"}

        self.assertTrue(matches_service(
            card, dict(shared, name="Nintendo eShop Gift Card")))
        self.assertTrue(matches_service(
            card, dict(shared, name="Nintendo подарочная карта")))
        self.assertFalse(matches_service(
            card, dict(shared, name="Nintendo Switch Online")))

    def test_must_have_narrows_a_shared_subcategory(self):
        card = Card(slug="n", title="N", subcategory="Nintendo",
                    name_must_have="gift")
        shared = {"subcategoryName": "Nintendo"}

        self.assertTrue(matches_service(
            card, dict(shared, name="Nintendo eShop Gift Card")))
        self.assertFalse(matches_service(
            card, dict(shared, name="Nintendo Switch Online 12 месяцев")))

    def test_a_card_without_a_subcategory_matches_nothing(self):
        card = Card(slug="x", title="X")

        self.assertEqual(services_for(card, XBOX_LIKE), [])

    def test_the_subcategory_name_is_read_under_any_known_field(self):
        for field in ("subcategoryName", "subCategoryName", "subcategory"):
            self.assertTrue(
                matches_service(XBOX, {field: "Xbox Gift Cards", "name": "X"}),
                field)


class ServiceRegionTest(unittest.TestCase):
    """Регион берётся из названия услуги — но только знакомый.

    «Любые две заглавные буквы» поймали бы и «PS», и «GB» в «10 GB», и
    номинал уехал бы в чужой регион. Не узнать регион безопаснее: номинал
    без региона подойдёт любому, а выдуманный отсечёт верный.
    """

    def test_a_country_code_is_recognised(self):
        self.assertEqual(
            region_of_service({"name": "Apple Gift Cards US"}), "US")

    def test_a_word_is_recognised_too(self):
        self.assertEqual(
            region_of_service({"name": "Roblox 1000 Robux (Global)"}), "GL")

    def test_uk_is_normalised_to_gb(self):
        self.assertEqual(
            region_of_service({"name": "Steam Wallet UK"}), "GB")

    def test_an_unknown_name_gives_no_region(self):
        self.assertEqual(region_of_service({"name": "Apple Gift Card"}), "")


class DenominationsForTest(unittest.TestCase):
    """Номиналы карты по всему каталогу — замена ручной привязке услуг."""

    def test_nominals_come_from_the_matching_service_only(self):
        rows = denominations_for(XBOX, XBOX_LIKE)

        self.assertEqual([r.item_id for r in rows], ["i1"])
        self.assertEqual(rows[0].service_id, "s-card")

    def test_the_region_is_taken_from_the_service_name(self):
        catalog = {"services": [
            {"id": "s1", "name": "Apple Gift Cards US",
             "subcategoryName": "Apple Gift Cards",
             "items": [{"id": "i1", "value": 10, "inStock": 1}]},
            {"id": "s2", "name": "Apple Gift Cards TR",
             "subcategoryName": "Apple Gift Cards",
             "items": [{"id": "i2", "value": 10, "inStock": 1}]},
        ]}
        card = Card(slug="apple", title="Apple",
                    subcategory="Apple Gift Cards")

        rows = denominations_for(card, catalog)
        got, why = match_denomination(rows, "TR", 10)

        self.assertEqual(why, "")
        self.assertEqual(got.item_id, "i2")

    def test_nominals_without_a_region_suit_any_region(self):
        catalog = {"services": [
            {"id": "s1", "name": "Apple Gift Cards",
             "subcategoryName": "Apple Gift Cards",
             "items": [{"id": "i1", "value": 10, "inStock": 1}]},
        ]}
        card = Card(slug="apple", title="Apple",
                    subcategory="Apple Gift Cards")

        rows = denominations_for(card, catalog)

        self.assertEqual(match_denomination(rows, "TR", 10)[0].item_id, "i1")


# Xbox у продавца, торгующего и глобальными кодами, и российскими. Номинал
# 1000 есть в обоих регионах — в рублях и в тенге, — и цены разные.
TWO_REGIONS = {"services": [
    {"id": "s-ru", "name": "Xbox Gift Cards RU",
     "subcategoryName": "Xbox Gift Cards",
     "items": [{"id": "ru-1000", "value": 1000, "inStock": 9, "price": 11.0}]},
    {"id": "s-kz", "name": "Xbox Gift Cards KZ",
     "subcategoryName": "Xbox Gift Cards",
     "items": [{"id": "kz-1000", "value": 1000, "inStock": 9, "price": 2.1}]},
]}


class TwoRegionsTest(unittest.TestCase):
    """Одна карта, несколько регионов — обычное дело у гифт-карт.

    Регион живёт в объявлении, а не в карте: продавец выставляет и
    глобальные коды, и российские, и это два разных товара на витрине.
    """

    def rows(self, region=""):
        return denominations_for(XBOX, TWO_REGIONS)

    def test_each_region_gets_its_own_nominal(self):
        got, why = match_denomination(self.rows("RU"), "RU", 1000)

        self.assertEqual(why, "")
        self.assertEqual(got.item_id, "ru-1000")

    def test_the_cheaper_region_is_not_substituted(self):
        """Код KZ дешевле впятеро, но покупателю с российским аккаунтом он
        не активируется: это не экономия, а спор и возврат."""
        got, _ = match_denomination(self.rows("RU"), "RU", 1000)

        self.assertNotEqual(got.item_id, "kz-1000")

    def test_a_region_the_supplier_does_not_have_is_refused(self):
        got, why = match_denomination(self.rows("TR"), "TR", 1000)

        self.assertIsNone(got)
        self.assertIn("TR", why)
        self.assertIn("RU", why)

    def test_a_nameless_region_is_refused_not_guessed(self):
        """Когда поставщик не называет регион ни у одной услуги, какая из
        них российская — отсюда не видно. Купить дешёвую значит наугад
        продать код, который покупатель не активирует."""
        nameless = {"services": [
            dict(TWO_REGIONS["services"][0], name="Xbox Gift Cards"),
            dict(TWO_REGIONS["services"][1], name="Xbox Gift Cards"),
        ]}
        got, why = match_denomination(
            denominations_for(XBOX, nameless), "RU", 1000)

        self.assertIsNone(got)
        self.assertIn("вручную", why)

    def test_one_nameless_service_is_not_ambiguous(self):
        """Выбора нет — значит и гадать не о чем."""
        single = {"services": [
            dict(TWO_REGIONS["services"][0], name="Xbox Gift Cards"),
        ]}
        got, why = match_denomination(
            denominations_for(XBOX, single), "RU", 1000)

        self.assertEqual(why, "")
        self.assertEqual(got.item_id, "ru-1000")

    def test_a_named_region_wins_over_a_nameless_one(self):
        """Точный регион сильнее догадки, даже если догадка дешевле."""
        mixed = {"services": [
            TWO_REGIONS["services"][0],
            dict(TWO_REGIONS["services"][1], name="Xbox Gift Cards"),
        ]}
        got, _ = match_denomination(
            denominations_for(XBOX, mixed), "RU", 1000)

        self.assertEqual(got.item_id, "ru-1000")


if __name__ == "__main__":
    unittest.main()
