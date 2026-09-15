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


class StepsTest(unittest.TestCase):
    def test_order_puts_cheap_to_redo_first(self):
        """Передумал на названии — не потратил время на картинки."""
        draft = wizard.Draft()

        self.assertEqual(draft.step, "name")
        draft.name = "80 Robux"
        self.assertEqual(draft.step, "price")
        draft.price = 100
        self.assertEqual(draft.step, "region")
        draft.region = "GL"
        self.assertEqual(draft.step, "photos")
        draft.photos.append(b"png-bytes")
        self.assertEqual(draft.step, "")

    def test_every_step_has_a_question(self):
        for step in wizard.STEPS:
            self.assertTrue(wizard.QUESTIONS.get(step, "").strip(), step)

    def test_finished_draft_is_asked_nothing(self):
        draft = wizard.Draft()
        draft.name, draft.price, draft.region = "x", 1, "GL"
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

    def test_unknown_region_is_refused(self):
        """По этой букве бот выбирает, что покупать у поставщика."""
        value, why = wizard.accept_region("EU")

        self.assertEqual(value, "")
        self.assertIn("GL", why)


class ApplyTest(unittest.TestCase):
    def test_answer_moves_the_draft_forward(self):
        draft = wizard.Draft()

        self.assertEqual(wizard.apply(draft, "80 Robux"), "")
        self.assertEqual(draft.name, "80 Robux")
        self.assertEqual(draft.step, "price")

    def test_bad_answer_keeps_the_step(self):
        draft = wizard.Draft()
        draft.name = "x"

        self.assertTrue(wizard.apply(draft, "дорого"))
        self.assertEqual(draft.step, "price")
        self.assertEqual(draft.price, 0)


class WordsTest(unittest.TestCase):
    def test_cancel_is_understood_loosely(self):
        self.assertTrue(wizard.cancelled("отмена"))
        self.assertTrue(wizard.cancelled("  СТОП "))
        self.assertFalse(wizard.cancelled("80 Robux"))

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
