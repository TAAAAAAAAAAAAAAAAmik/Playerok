"""Тесты шаблонов товаров. Сети не требуют.

Главное здесь — номер шаблона приходит из кнопки, то есть снаружи, и
подставляется в путь к файлу. Без проверки это дыра.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from templates import TemplateStore, extension, valid_id        # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"...bytes..."
JPG = b"\xff\xd8\xff" + b"...bytes..."


class SaveAndReadTest(unittest.TestCase):
    def setUp(self):
        self.store = TemplateStore(os.path.join(tempfile.mkdtemp(), "шаблоны"))

    def test_saved_template_comes_back_whole(self):
        tid = self.store.save("80 Robux", 149, "GL", [PNG, JPG])
        template = self.store.get(tid)

        self.assertEqual(template.name, "80 Robux")
        self.assertEqual(template.price, 149)
        self.assertEqual(template.region, "GL")
        self.assertEqual(template.photos(), [PNG, JPG])

    def test_pictures_are_copied_not_linked(self):
        """Товар продадут или снимут — шаблон должен работать и потом."""
        tid = self.store.save("x", 1, "GL", [PNG])
        files = os.listdir(os.path.join(self.store.folder, tid))

        self.assertIn("photo-1.png", files)

    def test_picture_type_is_guessed_from_content(self):
        self.assertEqual(extension(PNG), "png")
        self.assertEqual(extension(JPG), "jpg")
        self.assertEqual(extension(b"RIFF????WEBP..."), "webp")
        self.assertEqual(extension(b"unknown-format"), "bin")

    def test_missing_template_is_none_not_a_crash(self):
        self.assertIsNone(self.store.get("deadbeef"))

    def test_empty_store_lists_nothing(self):
        self.assertEqual(self.store.all(), [])

    def test_newest_template_comes_first(self):
        old = self.store.save("старый", 1, "GL", [PNG])
        new = self.store.save("новый", 2, "RU", [PNG])
        # Время пишется из time.time(); на быстрой машине оно может совпасть.
        card = self.store.get(old)
        card_path = os.path.join(card.folder, "template.json")
        import json
        data = json.load(open(card_path, encoding="utf-8"))
        data["at"] = data["at"] - 100
        json.dump(data, open(card_path, "w", encoding="utf-8"))

        self.assertEqual([t.id for t in self.store.all()], [new, old])

    def test_lost_picture_does_not_block_the_template(self):
        """Объявление с одной картинкой лучше, чем отказ."""
        tid = self.store.save("x", 1, "GL", [PNG, JPG])
        template = self.store.get(tid)
        os.unlink(os.path.join(template.folder, template.files[0]))

        self.assertEqual(self.store.get(tid).photos(), [JPG])

    def test_removing_works(self):
        tid = self.store.save("x", 1, "GL", [PNG])

        self.assertTrue(self.store.remove(tid))
        self.assertIsNone(self.store.get(tid))

    def test_label_shows_what_matters_for_the_button(self):
        tid = self.store.save("80 Robux", 149, "GL", [PNG])
        label = self.store.get(tid).label()

        self.assertIn("80 Robux", label)
        self.assertIn("149", label)
        self.assertIn("GL", label)


class DangerousIdTest(unittest.TestCase):
    """Номер приходит из кнопки. Если он попадёт в путь как есть —
    прочитать и стереть можно что угодно на диске."""

    BAD = ("../../etc", "..", "/etc/passwd", "a/../b", "", None,
           "ЗАГЛУШКА", "deadbeef0", "DEADBEEF", "dead beef")

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = TemplateStore(os.path.join(self.root, "шаблоны"))
        self.outside = os.path.join(self.root, "постороннее.txt")

        with open(self.outside, "w", encoding="utf-8") as f:
            f.write("важное")

    def test_bad_ids_are_refused(self):
        for bad in self.BAD:
            self.assertFalse(valid_id(bad), bad)

    def test_reading_by_a_bad_id_gives_nothing(self):
        for bad in self.BAD:
            self.assertIsNone(self.store.get(bad), bad)

    def test_removing_by_a_bad_id_deletes_nothing(self):
        for bad in self.BAD:
            self.assertFalse(self.store.remove(bad), bad)

        self.assertTrue(os.path.exists(self.outside))

    def test_saving_never_takes_an_id_from_outside(self):
        """Номер выдаём мы сами, и он всегда наш."""
        tid = self.store.save("x", 1, "GL", [PNG])

        self.assertTrue(valid_id(tid))

    def test_our_own_id_is_accepted(self):
        self.assertTrue(valid_id("0123abcd"))


if __name__ == "__main__":
    unittest.main()
