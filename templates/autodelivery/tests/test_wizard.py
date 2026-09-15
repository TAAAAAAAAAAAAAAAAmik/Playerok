"""Тесты диалога создания товара. Сети не требуют.

Проверяется то, что стоит денег и репутации: цена принимается только
понятная, регион — только известный, и без фотографии дело не идёт.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wizard                                                   # noqa: E402
from item_bot import photo_id                                   # noqa: E402


GAME = {"id": "g1", "name": "Roblox"}
CATEGORY = {"id": "c1", "name": "Игровая валюта"}
OBTAINING = {"id": "o1", "name": "Без входа"}


def started() -> wizard.Draft:
    """Черновик, у которого выбор на площадке уже сделан."""
    draft = wizard.Draft()
    draft.game, draft.category, draft.obtaining = GAME, CATEGORY, OBTAINING

    return draft


class StepsTest(unittest.TestCase):
    def test_marketplace_choices_come_first(self):
        """От категории зависят и способ получения, и поля. Спрашивать её
        после названия значит однажды выбросить написанное."""
        draft = wizard.Draft()

        self.assertEqual(draft.step, "game")
        draft.game = GAME
        self.assertEqual(draft.step, "category")
        draft.category = CATEGORY
        self.assertEqual(draft.step, "obtaining")
        draft.obtaining = OBTAINING
        self.assertEqual(draft.step, "name")

    def test_order_puts_cheap_to_redo_first(self):
        """Передумал на названии — не потратил время на картинки."""
        draft = started()

        self.assertEqual(draft.step, "name")
        draft.name = "80 Robux"
        self.assertEqual(draft.step, "price")
        draft.price = 100
        self.assertEqual(draft.step, "region")
        draft.region = "GL"
        self.assertEqual(draft.step, "description")
        draft.description = "Мои коды лучшие."
        self.assertEqual(draft.step, "photos")
        draft.photos.append(b"png-bytes")
        self.assertEqual(draft.step, "")

    def test_every_step_has_a_question(self):
        for step in wizard.STEPS:
            self.assertTrue(wizard.QUESTIONS.get(step, "").strip(), step)

    def test_skipped_is_not_the_same_as_unanswered(self):
        """Иначе пропуск означал бы вечный повтор одного вопроса."""
        draft = started()
        draft.name, draft.price, draft.region = "x", 1, "GL"
        draft.description = ""

        self.assertEqual(draft.step, "photos")

    def test_marketplace_fields_are_asked_after_description(self):
        """Состав полей известен только после выбора способа получения."""
        draft = started()
        draft.name, draft.price, draft.region = "x", 1, "GL"
        draft.description = ""
        draft.fields = [{"id": "f1", "label": "Комментарий",
                         "required": False, "value": None}]

        self.assertEqual(draft.step, wizard.FIELD + "f1")

    def test_finished_draft_is_asked_nothing(self):
        draft = started()
        draft.name, draft.price, draft.region = "x", 1, "GL"
        draft.description = "текст"
        draft.photos.append(b"png")

        self.assertEqual(wizard.question_for(draft), "")


class NameTest(unittest.TestCase):
    def test_spaces_are_tidied(self):
        name, why = wizard.accept_name("  80   Robux  ")

        self.assertEqual(name, "80 Robux")
        self.assertEqual(why, "")

    def test_empty_is_refused(self):
        self.assertTrue(wizard.accept_name("   ")[1])

    def test_too_long_is_refused_with_the_numbers(self):
        """Площадка обрежет молча — лучше сказать заранее."""
        _, why = wizard.accept_name("я" * (wizard.NAME_LIMIT + 1))

        self.assertIn(str(wizard.NAME_LIMIT), why)


class PriceTest(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(wizard.accept_price("149"), (149, ""))

    def test_spaces_do_not_matter(self):
        self.assertEqual(wizard.accept_price(" 149 ")[0], 149)

    def test_words_are_refused(self):
        self.assertTrue(wizard.accept_price("сто сорок девять")[1])
        self.assertTrue(wizard.accept_price("")[1])

    def test_kopecks_are_refused_not_rounded(self):
        """Продавец должен увидеть ту цену, которую назначил."""
        value, why = wizard.accept_price("149,50")

        self.assertEqual(value, 0)
        self.assertIn("целой", why)

    def test_whole_number_written_with_a_comma_passes(self):
        self.assertEqual(wizard.accept_price("149,00")[0], 149)

    def test_zero_and_negative_are_refused(self):
        self.assertTrue(wizard.accept_price("0")[1])
        self.assertTrue(wizard.accept_price("-5")[1])


class RegionTest(unittest.TestCase):
    def test_known_regions_pass_in_any_case(self):
        self.assertEqual(wizard.accept_region("gl"), ("GL", ""))
        self.assertEqual(wizard.accept_region(" RU "), ("RU", ""))

    def test_a_seller_is_not_stuck_with_two_regions(self):
        """Торгующий и глобальными кодами, и российскими, и турецкими не
        должен упираться в список из двух: у гифт-карт регионов много."""
        for region in ("US", "TR", "EU", "AR", "BR", "AE"):
            self.assertEqual(wizard.accept_region(region), (region, ""))

    def test_a_region_written_as_a_word_is_understood(self):
        """Продавец пишет «Россия» так же часто, как «RU», а отказ на
        понятном ответе — это экран, спорящий с человеком."""
        self.assertEqual(wizard.accept_region("Россия"), ("RU", ""))
        self.assertEqual(wizard.accept_region("глобал"), ("GL", ""))
        self.assertEqual(wizard.accept_region("Турция"), ("TR", ""))

    def test_unknown_region_is_refused(self):
        """Список закрыт нарочно: принять любые две буквы значило бы
        записать в товар регион, которого у поставщика нет, — и узнал бы об
        этом продавец из отказа, когда покупатель уже заплатил."""
        value, why = wizard.accept_region("ZZ")

        self.assertEqual(value, "")
        self.assertIn("GL", why)


class ApplyTest(unittest.TestCase):
    def test_answer_moves_the_draft_forward(self):
        draft = started()

        self.assertEqual(wizard.apply(draft, "80 Robux"), "")
        self.assertEqual(draft.name, "80 Robux")
        self.assertEqual(draft.step, "price")

    def test_bad_answer_keeps_the_step(self):
        draft = started()
        draft.name = "x"

        self.assertTrue(wizard.apply(draft, "дорого"))
        self.assertEqual(draft.step, "price")
        self.assertEqual(draft.price, 0)


class DescriptionInputTest(unittest.TestCase):
    def test_seller_text_is_kept(self):
        body, why = wizard.accept_description("Коды сразу. Активация быстрая.")

        self.assertEqual(why, "")
        self.assertIn("Коды сразу", body)

    def test_skip_leaves_the_description_to_the_card(self):
        """Пропуск не подставляет текст сам: описание по умолчанию берётся
        от узнанной карты, и одно на всех дало бы объявлению Apple строку
        «Активация: roblox.com/redeem»."""
        self.assertEqual(wizard.accept_description("пропустить")[0], "")

    def test_empty_text_is_the_same_as_a_skip(self):
        self.assertEqual(wizard.accept_description("   ")[0], "")

    def test_the_cards_text_is_used_when_the_seller_skipped(self):
        draft = wizard.Draft()
        draft.region = "US"
        draft.description = ""
        draft.tail = "Активация: App Store → Погасить подарочную карту."

        self.assertIn("App Store", wizard.description_for(draft))
        self.assertNotIn("roblox", wizard.description_for(draft).lower())

    def test_the_sellers_own_text_wins_over_the_cards(self):
        draft = wizard.Draft()
        draft.region = "US"
        draft.description = "Мой текст"
        draft.tail = "Заготовка карты"

        self.assertIn("Мой текст", wizard.description_for(draft))
        self.assertNotIn("Заготовка", wizard.description_for(draft))

    def test_without_a_card_the_old_default_still_applies(self):
        draft = wizard.Draft()
        draft.region = "GL"
        draft.description = ""

        self.assertIn(wizard.DEFAULT_TAIL, wizard.description_for(draft))

    def test_own_region_line_is_cut_out(self):
        """Две разные строки региона — тихая денежная ошибка: движок
        прочитает не ту и купит не тот товар."""
        body, _ = wizard.accept_description(
            "Регион кода: RU\nМои коды лучшие.")

        self.assertNotIn("RU", body)
        self.assertIn("Мои коды лучшие.", body)

    def test_region_line_in_any_case_and_language(self):
        for line in ("регион кода: RU", "РЕГИОН: ru", "Region code: RU"):
            body, _ = wizard.accept_description(f"{line}\nтекст")

            self.assertNotIn("RU", body.upper(), line)

    def test_too_long_is_refused_with_the_numbers(self):
        _, why = wizard.accept_description("я" * (wizard.DESCRIPTION_LIMIT + 1))

        self.assertIn(str(wizard.DESCRIPTION_LIMIT), why)


class MarketplaceFieldTest(unittest.TestCase):
    """Поля у разных категорий разные, и обязательность тоже."""

    def field(self, required=False):
        draft = started()
        draft.name, draft.price, draft.region = "x", 1, "GL"
        draft.description = ""
        draft.fields = [{"id": "f1", "label": "Комментарий",
                         "required": required, "value": None}]

        return draft

    def test_optional_field_can_be_skipped(self):
        draft = self.field()

        self.assertEqual(wizard.apply(draft, "пропустить"), "")
        self.assertEqual(draft.fields[0]["value"], "")
        self.assertEqual(draft.step, "photos")

    def test_required_field_cannot_be_skipped(self):
        """Без него площадка товар не примет — лучше упереться здесь."""
        draft = self.field(required=True)
        why = wizard.apply(draft, "пропустить")

        self.assertIn("обязательное", why)
        self.assertEqual(draft.step, wizard.FIELD + "f1")

    def test_value_is_tidied(self):
        draft = self.field()
        wizard.apply(draft, "  быстрая   выдача ")

        self.assertEqual(draft.fields[0]["value"], "быстрая выдача")

    def test_required_question_says_it_is_required(self):
        self.assertIn("требует", wizard.question_for(self.field(True)))

    def test_optional_question_offers_to_skip(self):
        self.assertIn("пропустить", wizard.question_for(self.field()))

    def test_only_filled_fields_are_sent(self):
        draft = self.field()
        wizard.apply(draft, "пропустить")

        self.assertEqual(draft.filled_fields(), [])


class FinalDescriptionTest(unittest.TestCase):
    """Собранное описание должен понять и покупатель, и движок."""

    def build(self, region, text):
        draft = started()
        draft.region = region
        draft.description = text

        return wizard.description_for(draft)

    def test_chosen_region_wins_over_the_typed_one(self):
        from catalog import region_from_description

        body, _ = wizard.accept_description("Регион кода: RU\nтекст")

        self.assertEqual(region_from_description(self.build("GL", body)), "GL")

    def test_seller_text_is_below_the_region_line(self):
        built = self.build("GL", "Мой текст.")

        self.assertTrue(built.startswith("Регион кода: GL"))
        self.assertIn("Мой текст.", built)

    def test_skipped_description_still_says_something_useful(self):
        self.assertIn("чат", self.build("GL", wizard.DEFAULT_TAIL))


class WordsTest(unittest.TestCase):
    def test_cancel_is_understood_loosely(self):
        self.assertTrue(wizard.cancelled("отмена"))
        self.assertTrue(wizard.cancelled("  СТОП "))
        self.assertFalse(wizard.cancelled("80 Robux"))

    def test_skip_is_understood_loosely(self):
        self.assertTrue(wizard.skipped("пропустить"))
        self.assertTrue(wizard.skipped(" НЕТ "))
        self.assertTrue(wizard.skipped("-"))
        self.assertFalse(wizard.skipped("мой текст"))

    def test_done_only_by_the_word(self):
        self.assertTrue(wizard.enough_photos("готово"))
        self.assertTrue(wizard.enough_photos(" ГОТОВО "))
        self.assertFalse(wizard.enough_photos("готовы"))
        self.assertFalse(wizard.enough_photos(""))


class DescriptionTest(unittest.TestCase):
    """Первая строка описания — не оформление: из неё движок выдачи читает
    регион, и без неё бот при оплате не купит код."""

    def test_region_line_is_first(self):
        draft = wizard.Draft()
        draft.region = "GL"

        self.assertTrue(
            wizard.description_for(draft).startswith("Регион кода: GL"))

    def test_engine_reads_the_region_back(self):
        from catalog import region_from_description

        draft = wizard.Draft()
        draft.region = "RU"

        self.assertEqual(
            region_from_description(wizard.description_for(draft)), "RU")

    def test_engine_reads_the_nominal_from_the_name(self):
        from catalog import nominal_from_title

        name, _ = wizard.accept_name("80 Robux | Быстро")

        self.assertEqual(nominal_from_title(name), 80)


class PhotoTest(unittest.TestCase):
    """Telegram отдаёт одну картинку в нескольких размерах."""

    def test_the_largest_size_is_taken(self):
        """Мутная картинка хуже продаётся, а Telegram кладёт крупную
        последней."""
        message = {"photo": [{"file_id": "мелкая"},
                             {"file_id": "средняя"},
                             {"file_id": "крупная"}]}

        self.assertEqual(photo_id(message), "крупная")

    def test_image_sent_as_a_file_is_accepted(self):
        """Файлом картинка приходит несжатой — это даже лучше."""
        message = {"document": {"file_id": "файл", "mime_type": "image/png"}}

        self.assertEqual(photo_id(message), "файл")

    def test_other_files_are_not_pictures(self):
        message = {"document": {"file_id": "док", "mime_type": "application/pdf"}}

        self.assertEqual(photo_id(message), "")

    def test_plain_text_is_not_a_picture(self):
        self.assertEqual(photo_id({"text": "готово"}), "")
        self.assertEqual(photo_id({}), "")

    def test_empty_photo_list_is_not_a_crash(self):
        self.assertEqual(photo_id({"photo": []}), "")


if __name__ == "__main__":
    unittest.main()
