"""Тесты разбора каталога поставщика. Сети не требуют.

Здесь ошибка стоит денег дважды: пропущенный номинал оставляет заказ без
выдачи, а придуманный покупает не то на настоящие деньги.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from catalog import (UNITS, Card, denominations_for,            # noqa: E402
                     denominations_from, find_service, items_of,
                     is_card_order, match_denomination, matches_service,
                     nominal_by_unit, nominal_for,
                     nominal_from_description, numbers_by_unit,
                     numbers_in, region_in, stop_words, whose,
                     nominal_from_title, render, shown_number,
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


class NominalFromDescriptionTest(unittest.TestCase):
    """Номинал берётся из описания — там он сказан, а не угадан.

    Строку «Номинал: …» пишет в описание сам бот при создании товара, как
    и строку региона. Из названия номинал приходится угадывать по самому
    крупному числу, и на «Roblox Gift Card 10 USD (1000 Robux)» такая
    догадка ошибается.
    """

    def test_a_marked_line_is_read(self):
        self.assertEqual(
            nominal_from_description("Регион кода: GL\nНоминал: 1000"), 1000)

    def test_other_wordings_are_understood(self):
        for line in ("Количество: 800", "Кол-во: 800", "Сумма: 800",
                     "amount 800", "НОМИНАЛ — 800"):
            self.assertEqual(nominal_from_description(line), 800, line)

    def test_stray_numbers_are_not_mistaken_for_it(self):
        """В описании чисел полно: срок действия, год, размер скидки. Взяв
        самое крупное, бот потратил бы деньги на случайность."""
        text = ("Код действует до 2030 года. Скидка 5%. "
                "Поддержка 24/7, отвечаем за 15 минут.")

        self.assertIsNone(nominal_from_description(text))

    def test_the_description_answers_when_the_title_is_silent(self):
        got, why = nominal_for("Робуксы недорого", "Номинал: 1000")

        self.assertEqual(why, "")
        self.assertEqual(got, 1000)

    def test_a_stray_year_in_the_title_stops_the_bot_instead_of_buying_it(self):
        """«Роблокс код 2024» раньше означало попытку купить номинал 2024.
        Теперь описание с ним спорит, и бот останавливается, а не тратит."""
        got, why = nominal_for("Роблокс код 2024", "Номинал: 1000")

        self.assertIsNone(got)
        self.assertIn("2024", why)

    def test_the_title_still_works_when_the_description_is_silent(self):
        self.assertEqual(nominal_for("Roblox 1000 Robux", "просто текст")[0],
                         1000)

    def test_a_disagreement_is_refused_not_resolved(self):
        """Бывает от копии соседнего объявления. Купить по описанию значит
        недодать, купить по названию — переплатить за продавца. Ни то ни
        другое не наше решение."""
        got, why = nominal_for("Roblox 1000 Robux", "Номинал: 800")

        self.assertIsNone(got)
        self.assertIn("1000", why)
        self.assertIn("800", why)

    def test_agreement_is_not_a_disagreement(self):
        self.assertEqual(nominal_for("Roblox 1000 Robux", "Номинал: 1000"),
                         (1000, ""))

    def test_neither_source_says_it(self):
        got, why = nominal_for("Робуксы дёшево", "Активация: roblox.com")

        self.assertIsNone(got)
        self.assertIn("Номинал", why)


class BigNumberTest(unittest.TestCase):
    """С миллиона обычное %g переходит на экспоненту — и это не косметика.

    «1e+06» уходит в название товара и в строку «Номинал: …» описания, а
    оттуда выдача читает его обратно. Разбирается оно как ЕДИНИЦА: бот
    купил бы номинал 1 вместо миллиона, на настоящие деньги.
    """

    def test_a_million_is_written_out_in_full(self):
        self.assertEqual(shown_number(1000000), "1000000")
        self.assertEqual(shown_number(1500000), "1500000")
        self.assertEqual(shown_number(10000000), "10000000")

    def test_small_numbers_are_unchanged(self):
        self.assertEqual(shown_number(100), "100")
        self.assertEqual(shown_number(22500), "22500")

    def test_a_whole_number_has_no_tail(self):
        """«1000.0» в названии товара выглядит небрежностью."""
        self.assertEqual(shown_number(1000.0), "1000")

    def test_fractions_survive(self):
        """У денежных карт номиналы дробные: 9.99 USD."""
        self.assertEqual(shown_number(9.99), "9.99")
        self.assertEqual(shown_number(0.5), "0.5")

    def test_nonsense_gives_nothing_not_a_crash(self):
        self.assertEqual(shown_number("много"), "")
        self.assertEqual(shown_number(None), "")

    def test_what_we_write_is_what_we_read_back(self):
        """Круг замкнулся: как написали в описание, так и прочитали."""
        for value in (100, 1000, 22500, 1000000, 10000000):
            line = f"Номинал: {shown_number(value)}"

            self.assertEqual(nominal_from_description(line), value, line)


class MoneyCardTest(unittest.TestCase):
    """Денежные карты — не робуксы, и разбор под них не был готов.

    У робуксов номинал больше цены, и «самое крупное число» случайно
    совпадало с верным. У Apple наоборот: номинал 10, цена 900.
    """

    def test_the_price_written_in_the_name_is_not_the_nominal(self):
        """Продавцы денежных карт пишут её прямо в названии."""
        self.assertEqual(
            nominal_from_title("Apple Gift Card 10$ за 900 рублей", 900), 10)
        self.assertEqual(
            nominal_from_title("Xbox 25 USD — всего 2100₽", 2100), 25)

    def test_without_the_price_the_old_guess_still_applies(self):
        """Цена известна не всегда: у чужих объявлений её может не быть."""
        self.assertEqual(
            nominal_from_title("Roblox 1000 Robux"), 1000)

    def test_a_nominal_equal_to_the_price_survives(self):
        """«Steam 500 ₽» за 500 ₽ — это всё ещё номинал 500. Выбросив
        единственное число, мы остались бы вовсе без номинала."""
        self.assertEqual(nominal_from_title("Steam 500 ₽", 500), 500)

    def test_the_robux_case_is_unchanged(self):
        self.assertEqual(nominal_from_title("Roblox 1000 Robux", 700), 1000)

    def test_the_currency_follows_the_region_not_the_card(self):
        """«Steam 500» в России — рубли, в США доллары, а карта одна и та
        же. Раньше знак стоял в карте, и российский Steam получал описание
        «Код пополнения Steam на 500$»."""
        from cards import card_by_slug

        steam = card_by_slug("steam")

        self.assertIn("500₽", render("{номинал}", steam, 500, "RU"))
        self.assertIn("20$", render("{номинал}", steam, 20, "US"))
        self.assertIn("100₺", render("{номинал}", steam, 100, "TR"))

    def test_a_unit_card_ignores_the_region(self):
        """Робуксы остаются робуксами и в России, и в мире."""
        from cards import card_by_slug

        robux = card_by_slug("robux")

        self.assertIn("1000 Robux", render("{номинал}", robux, 1000, "RU"))
        self.assertIn("1000 Robux", render("{номинал}", robux, 1000, "GL"))

    def test_an_unknown_region_gets_no_sign_at_all(self):
        """Лучше без знака, чем с неверным."""
        from cards import card_by_slug

        got = render("{номинал}", card_by_slug("apple"), 10, "ZZ")

        self.assertEqual(got, "10")


class NominalByUnitTest(unittest.TestCase):
    """Номинал из описания по единице: «50 робуксов» → 50.

    Спасает объявления, у которых в названии числа нет вовсе —
    «🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎», — а номинал написан в описании
    обычными словами. Помеченной строки «Номинал: 50» у таких объявлений
    нет и взяться ей неоткуда: их делали руками.
    """

    ROBUX = Card(slug="robux", title="Roblox", emoji="🎮",
                 keywords=("robux", "робукс", "роблокс"), measure="Robux",
                 unit=UNITS, measure_words=("робукс", "r$"),
                 subcategory="Roblox Gift Cards", activation="")
    APPLE = Card(slug="apple", title="Apple", emoji="🍎",
                 keywords=("apple",), measure="$",
                 subcategory="Apple Gift Cards", activation="")

    NAMELESS = "🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎"

    def value(self, text, card=None, title="", price=None):
        return nominal_for(title or self.NAMELESS, text, price,
                           card or self.ROBUX)

    def test_the_seller_writes_it_in_his_own_words(self):
        self.assertEqual(self.value("Вы получаете 50 робуксов")[0], 50)

    def test_the_unit_in_english_works_too(self):
        self.assertEqual(self.value("50 ROBUX моментально")[0], 50)

    def test_the_declension_does_not_matter(self):
        """По-русски единица склоняется: робукс, робукса, робуксов."""
        for text in ("1 робукс", "2 робукса", "400 робуксов"):
            self.assertIsNotNone(self.value(text)[0], text)

    def test_a_space_inside_the_number_is_not_a_second_number(self):
        self.assertEqual(self.value("1 000 робуксов сразу")[0], 1000)

    def test_the_unit_must_start_a_word(self):
        """«микроробуксы» — не робуксы, и число рядом с ними не номинал."""
        self.assertIsNone(self.value("Не более 5 микроробуксов")[0])

    def test_other_numbers_are_left_alone(self):
        """Год, срок, проценты и телефон — не номиналы."""
        text = ("Код действует до 2030 года. Скидка 5%. Поддержка 24/7, "
                "отвечаем за 15 минут.")

        self.assertIsNone(self.value(text)[0])

    def test_a_range_is_refused_not_guessed(self):
        """«от 50 до 10000 робуксов»: единица стоит рядом только с верхней
        границей, и взять её значит купить самый дорогой номинал."""
        got, why = self.value("Пополнение от 50 до 10000 робуксов")

        self.assertIsNone(got)
        self.assertIn("диапазон", why)

    def test_a_dash_range_is_refused_too(self):
        self.assertIsNone(self.value("50–10000 робуксов")[0])

    def test_two_different_nominals_are_refused(self):
        got, why = self.value("Дарю 50 робуксов сверху к 400 Robux")

        self.assertIsNone(got)
        self.assertIn("несколько", why)

    def test_the_same_nominal_twice_is_not_a_conflict(self):
        self.assertEqual(self.value("400 Robux. Код на 400 робуксов.")[0], 400)

    def test_money_cards_read_their_currency(self):
        got, _ = self.value("Карта на 10$ для App Store", self.APPLE,
                            title="Apple Gift Card", price=900)

        self.assertEqual(got, 10)

    def test_the_currency_may_stand_before_the_number(self):
        got, _ = self.value("Номинал $25", self.APPLE,
                            title="Apple Gift Card")

        self.assertEqual(got, 25)

    def test_the_marked_line_still_wins(self):
        """Сказанное прямо сильнее прочитанного между строк."""
        got, _ = self.value("Номинал: 400. Бонусом 50 робуксов сверху.")

        self.assertEqual(got, 400)

    def test_the_title_still_wins_over_the_description(self):
        """Работающие объявления новый разбор не трогает."""
        got, _ = self.value("Пополнение от 50 до 10000 робуксов",
                            title="Roblox 1000 Robux", price=999)

        self.assertEqual(got, 1000)

    def test_without_a_card_nothing_changes(self):
        """Единицы не знаем — и не выдумываем."""
        got, why = nominal_for(self.NAMELESS, "50 робуксов", 119)

        self.assertIsNone(got)
        self.assertIn("не найден", why)

    def test_the_advice_does_not_demand_a_thousand(self):
        """Старый текст советовал «Номинал: 1000» на выдаче в 50 робуксов
        и читался как требование именно тысячи."""
        _, why = self.value("Просто текст без чисел")

        self.assertNotIn("1000", why)
        self.assertIn("Номинал", why)

    def test_the_refusal_shows_what_was_read(self):
        """Продавец смотрит в своё описание, видит там число и не понимает,
        чем оно боту не угодило."""
        _, why = self.value("Код действует до 2030 года. Поддержка 24/7.")

        self.assertIn("2030", why)
        self.assertIn("единицей", why)

    def test_numbers_by_unit_finds_them_in_order(self):
        self.assertEqual(numbers_by_unit("50 робуксов и 400 robux",
                                         ("robux", "робукс")), [50.0, 400.0])

    def test_nothing_to_read_is_not_a_refusal(self):
        self.assertEqual(nominal_by_unit("", self.ROBUX), (None, ""))


class NumberAcrossLinesTest(unittest.TestCase):
    """Число не склеивается через перевод строки.

    Живое описание продавца:

        Регион кода: GL
        Номинал: 50

        50 робуксов
        🎮 Пополнение 50 ROBUX для аккаунта Roblox (GLOBAL).

    Пробел в разряде числа («1 000») задавался через `\\s`, а оно ловит и
    перевод строки. «Номинал: 50» плюс следующая строка читались как одно
    число «50\\n\\n50», float на нём падал — и строка «Номинал: 50» не
    читалась ВООБЩЕ. Продавец видел «номинал не найден», глядя на номинал,
    написанный в карточке прямым текстом.
    """

    ROBUX = Card(slug="robux", title="Roblox", emoji="🎮",
                 keywords=("robux", "робукс"), measure="Robux", unit=UNITS,
                 measure_words=("робукс",), subcategory="Roblox Gift Cards",
                 activation="")

    LIVE = ("Регион кода: GL\n"
            "Номинал: 50\n"
            "\n"
            "50 робуксов\n"
            "🎮 Пополнение 50 ROBUX для аккаунта Roblox (GLOBAL).\n"
            "\n"
            "⚡ Моментальная выдача кода после покупки.\n"
            "🕐 Поддержка и выдача 24/7.")

    def test_the_live_description_is_read(self):
        got, why = nominal_for("🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎", self.LIVE,
                               119, self.ROBUX)

        self.assertEqual(got, 50)
        self.assertEqual(why, "")

    def test_the_marked_line_survives_the_next_line(self):
        self.assertEqual(nominal_from_description("Номинал: 50\n\n50 робуксов"),
                         50)

    def test_two_lines_are_two_numbers(self):
        self.assertEqual(numbers_in("50\n400"), [50.0, 400.0])

    def test_a_thousand_separator_still_works(self):
        """Ради него `\\s` там и стоял: «1 000» — одно число."""
        self.assertEqual(nominal_from_description("Номинал: 1 000"), 1000)

    def test_a_non_breaking_space_works_too(self):
        self.assertEqual(numbers_by_unit("50\u00a0робуксов", ("робукс",)),
                         [50.0])

    def test_the_unit_must_stand_on_the_same_line(self):
        """«24\nробуксы начисляются» — это не «24 робукса»."""
        self.assertEqual(numbers_by_unit("выдача 24\nробуксы начисляются",
                                         ("робукс",)), [])


class StopWordTest(unittest.TestCase):
    """Слово-исключение: «это не мой товар, что бы ты там ни узнал».

    Один и тот же робукс продают двумя способами: «80 РОБУКСОВ
    промокодом» — это код, «80 РОБУКСОВ через геймпасс» — выдача внутри
    игры. Названия почти совпадают, а выдать по второму код первого
    значит купить не то, что продано.
    """

    CARD = Card(slug="robux", title="Roblox", emoji="🎮",
                keywords=("robux", "робукс"), measure="Robux", unit=UNITS,
                subcategory="Roblox Gift Cards", activation="")

    def test_a_stop_word_rejects_the_order(self):
        self.assertFalse(is_card_order(self.CARD, "80 РОБУКСОВ ГЕЙМПАСС",
                                       stop="геймпасс"))

    def test_without_it_the_order_is_ours(self):
        self.assertTrue(is_card_order(self.CARD, "80 РОБУКСОВ ГЕЙМПАСС"))

    def test_it_is_stronger_than_the_keyword(self):
        """Продавец сказал «не мой» — спорить не с чем."""
        self.assertFalse(is_card_order(self.CARD, "80 РОБУКСОВ ГЕЙМПАСС",
                                       keyword="робукс", stop="геймпасс"))

    def test_several_words_are_separated_by_commas(self):
        for title in ("робукс аккаунт", "робукс геймпасс"):
            self.assertFalse(is_card_order(self.CARD, title,
                                           stop="геймпасс, аккаунт"), title)

    def test_case_and_spaces_do_not_matter(self):
        self.assertFalse(is_card_order(self.CARD, "80 Робуксов ГеймПасс",
                                       stop="  ГЕЙМПАСС  "))

    def test_an_empty_stop_changes_nothing(self):
        self.assertTrue(is_card_order(self.CARD, "80 робуксов", stop="  "))

    def test_the_words_are_split(self):
        self.assertEqual(stop_words("геймпасс, аккаунт; пакет"),
                         ("геймпасс", "аккаунт", "пакет"))


class WhoseTest(unittest.TestCase):
    """Кому достанется заказ и ПОЧЕМУ. Ответ идёт в письма и на экраны."""

    CARD = Card(slug="robux", title="Roblox Gift Cards", emoji="🎮",
                keywords=("robux", "робукс"), measure="Robux", unit=UNITS,
                subcategory="Roblox Gift Cards", activation="")

    def conf(self, **fields):
        base = {"enabled": True}
        base.update(fields)

        return lambda slug: base

    def test_ours_is_ours(self):
        card, why = whose([self.CARD], "80 робуксов", self.conf())

        self.assertIs(card, self.CARD)
        self.assertEqual(why, "мой товар")

    def test_a_disabled_card_is_named_as_disabled(self):
        card, why = whose([self.CARD], "80 робуксов",
                          self.conf(enabled=False))

        self.assertIs(card, self.CARD)
        self.assertIn("выключена", why)

    def test_a_keyword_that_does_not_match_is_explained(self):
        card, why = whose([self.CARD], "80 робуксов",
                          self.conf(keyword="промокод"))

        self.assertIsNone(card)
        self.assertIn("промокод", why)

    def test_a_stop_word_is_explained(self):
        card, why = whose([self.CARD], "80 робуксов геймпасс",
                          self.conf(stop="геймпасс"))

        self.assertIsNone(card)
        self.assertIn("геймпасс", why)

    def test_a_foreign_order_is_foreign(self):
        card, why = whose([self.CARD], "🏆 ПОПУЛЯРНЫЙ ПАКЕТ • 3 LVL",
                          self.conf())

        self.assertIsNone(card)
        self.assertEqual(why, "не мой товар")


class RegionSpellingTest(unittest.TestCase):
    """Регион, который не прочитался, кнопкой не появится.

    Продавец видел «не все регионы» у Apple и Xbox: у этих карт поставщик
    называет регион словом по-английски — «Apple Gift Cards Turkey», — а
    разбор знал только коды и русские названия.
    """

    def test_a_country_written_in_english(self):
        self.assertEqual(region_in("Apple Gift Cards Turkey"), "TR")

    def test_a_two_word_country(self):
        self.assertEqual(region_in("Xbox Gift Card Saudi Arabia"), "SA")

    def test_the_longer_name_wins(self):
        """«SOUTH KOREA» — это KR, а не «KOREA неизвестно»."""
        self.assertEqual(region_in("Gift Card South Korea"), "KR")

    def test_a_code_in_brackets(self):
        self.assertEqual(region_in("Apple Gift Card (Global)"), "GL")

    def test_an_english_preposition_is_not_a_country(self):
        """«in» — это предлог, а не Индия. Коды пишут заглавными."""
        self.assertEqual(region_in("Apple Gift Card in Turkey"), "TR")

    def test_a_lowercase_code_is_ignored(self):
        self.assertEqual(region_in("apple gift card it works"), "")

    def test_an_uppercase_code_still_works(self):
        self.assertEqual(region_in("Apple Gift Cards US"), "US")

    def test_nothing_to_read_is_empty(self):
        self.assertEqual(region_in("Apple Gift Card 10 USD"), "")

    def test_the_field_is_stronger_than_the_name(self):
        """Сказанное полем сильнее угаданного из названия."""
        got = region_of_service({"name": "Apple Gift Card",
                                 "country": "Turkey"})

        self.assertEqual(got, "TR")

    def test_a_country_code_field_is_understood(self):
        self.assertEqual(region_of_service({"name": "Apple",
                                            "countryCode": "ae"}), "AE")

    def test_the_name_still_works_without_a_field(self):
        self.assertEqual(region_of_service({"name": "Apple Gift Cards TR"}),
                         "TR")


if __name__ == "__main__":
    unittest.main()
