"""Тесты бота создания товаров. Ни телеграма, ни площадки — обе поддельные.

Проверяется сборка: доходит ли ответ до площадки, попадают ли в товар
картинки из шаблона и что происходит, когда шаблон испорчен.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import item_bot                                                 # noqa: E402
import wizard                                                   # noqa: E402
from templates import TemplateStore                             # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


class FakeLink:
    """Телеграм-пустышка: отвечает заранее заготовленным."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.said = []
        self.asked = []

    def say(self, text, buttons=None):
        self.said.append(text)
        return True

    def ask(self, question, wait_seconds=0, buttons=None):
        self.asked.append((question, buttons))
        self.said.append(question)

        return {"text": self.answers.pop(0)} if self.answers else {}

    def wait_answer(self, wait_seconds=0):
        return {"text": self.answers.pop(0)} if self.answers else {}


class FakeItem:
    id = "item-1"


class FakeAccount:
    """Площадка-пустышка: запоминает, с чем её позвали."""

    def __init__(self, fail=False):
        self.created = []
        self.fail = fail

    def create_item(self, **kw):
        if self.fail:
            raise RuntimeError("площадка отказала")

        self.created.append(kw)
        return FakeItem()

    def get_item_priority_statuses(self, item_id, price):
        return []          # выставлять нечем — черновик и остаётся


def draft(name="80 Robux", price=149, region="GL", photos=(PNG,)):
    d = wizard.Draft()
    d.name, d.price, d.region = name, price, region
    d.photos = list(photos)
    return d


class SendDraftTest(unittest.TestCase):
    def test_answers_reach_the_marketplace(self):
        account = FakeAccount()
        link = FakeLink()

        self.assertTrue(item_bot.send_draft(link, account, draft()))

        sent = account.created[0]
        self.assertEqual(sent["name"], "80 Robux")
        self.assertEqual(sent["price"], 149)
        self.assertEqual(sent["attachments"], [PNG])

    def test_region_reaches_the_description(self):
        """Из этой строки движок выдачи потом читает регион."""
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft(region="RU"))

        self.assertIn("Регион кода: RU", account.created[0]["description"])

    def test_refusal_is_reported_and_nothing_is_lost(self):
        link = FakeLink()

        self.assertFalse(item_bot.send_draft(link, FakeAccount(fail=True),
                                             draft()))
        self.assertTrue(any("не вышло" in t for t in link.said))


class TemplateFlowTest(unittest.TestCase):
    def setUp(self):
        self.folder = os.path.join(tempfile.mkdtemp(), "шаблоны")
        self.saved = os.environ.get("PLAYEROK_TEMPLATES")
        item_bot.TEMPLATE_DIR = self.folder
        self.store = TemplateStore(self.folder)

    def tearDown(self):
        if self.saved:
            os.environ["PLAYEROK_TEMPLATES"] = self.saved

    def test_saving_keeps_everything_needed(self):
        link = FakeLink(["да"])
        item_bot.offer_template(link, draft())
        template = self.store.all()[0]

        self.assertEqual(template.name, "80 Robux")
        self.assertEqual(template.price, 149)
        self.assertEqual(template.region, "GL")
        self.assertEqual(template.photos(), [PNG])

    def test_refusing_saves_nothing(self):
        item_bot.offer_template(FakeLink(["нет"]), draft())

        self.assertEqual(self.store.all(), [])

    def test_one_press_recreates_the_same_item(self):
        """То, ради чего всё это: нажал — и объявление такое же."""
        tid = self.store.save("80 Robux", 149, "GL", [PNG])
        account = FakeAccount()
        item_bot.from_template(FakeLink([item_bot.PICK + tid]), account)

        sent = account.created[0]
        self.assertEqual(sent["name"], "80 Robux")
        self.assertEqual(sent["price"], 149)
        self.assertEqual(sent["attachments"], [PNG])
        self.assertIn("Регион кода: GL", sent["description"])

    def test_no_templates_is_explained_not_silent(self):
        link = FakeLink()
        account = FakeAccount()
        item_bot.from_template(link, account)

        self.assertEqual(account.created, [])
        self.assertTrue(any("шаблон" in t.lower() for t in link.said))

    def test_cancel_creates_nothing(self):
        self.store.save("x", 1, "GL", [PNG])
        account = FakeAccount()
        item_bot.from_template(FakeLink(["отмена"]), account)

        self.assertEqual(account.created, [])

    def test_foreign_id_creates_nothing(self):
        """Значение кнопки приходит снаружи."""
        self.store.save("x", 1, "GL", [PNG])
        account = FakeAccount()

        for bad in ("tpl:../../etc", "tpl:", "tpl:deadbeef", "что попало"):
            item_bot.from_template(FakeLink([bad]), account)

        self.assertEqual(account.created, [])

    def test_template_without_pictures_is_refused(self):
        """Без картинок площадка товар не примет — лучше сказать сразу."""
        tid = self.store.save("x", 1, "GL", [PNG])
        template = self.store.get(tid)
        os.unlink(os.path.join(template.folder, template.files[0]))

        link = FakeLink([item_bot.PICK + tid])
        account = FakeAccount()
        item_bot.from_template(link, account)

        self.assertEqual(account.created, [])
        self.assertTrue(any("картинк" in t.lower() for t in link.said))


if __name__ == "__main__":
    unittest.main()
