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
from accounts import AccountStore                               # noqa: E402
from templates import TemplateStore, folder_for                 # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


class FakeLink:
    """Телеграм-пустышка: отвечает заранее заготовленным."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.said = []
        self.asked = []
        self.screens = []
        self.deleted = []

    def _delete(self, message):
        self.deleted.append(message)
        return True

    def say(self, text, buttons=None):
        self.said.append(text)
        return True

    def screen(self, text, buttons=None):
        """Экран: в жизни переписывает одно сообщение, здесь — просто
        запоминаем, чтобы тесты видели сказанное."""
        self.said.append(text)
        self.screens.append(text)
        return True

    def forget_screen(self):
        self.screens.append(None)

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


GAME = {"id": "g1", "name": "Roblox"}
CATEGORY = {"id": "c1", "name": "Игровая валюта"}
OBTAINING = {"id": "o1", "name": "Без входа в аккаунт"}


def draft(name="80 Robux", price=149, region="GL", photos=(PNG,),
          description="Коды сразу.", comment="быстро"):
    d = wizard.Draft()
    d.game, d.category, d.obtaining = GAME, CATEGORY, OBTAINING
    d.name, d.price, d.region = name, price, region
    d.description = description
    d.fields = [{"id": "f1", "label": "Комментарий", "required": False,
                 "value": comment}]
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

    def test_seller_description_reaches_the_marketplace(self):
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft(description="Мой текст."))

        self.assertIn("Мой текст.", account.created[0]["description"])

    def test_chosen_category_is_used_not_a_hardcoded_one(self):
        """Зашитый id уже приводил к товарам в чужой категории."""
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft())

        self.assertEqual(account.created[0]["game_category_id"], "c1")
        self.assertEqual(account.created[0]["obtaining_type_id"], "o1")

    def test_filled_field_goes_to_the_marketplace(self):
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft(comment="пометка"))
        fields = account.created[0]["data_fields"]

        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].value, "пометка")
        self.assertEqual(fields[0].id, "f1")

    def test_empty_field_is_not_sent(self):
        """Необязательное поле не надо слать пустым."""
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft(comment=""))

        self.assertEqual(account.created[0]["data_fields"], [])

    def test_refusal_is_reported_and_nothing_is_lost(self):
        link = FakeLink()

        self.assertFalse(item_bot.send_draft(link, FakeAccount(fail=True),
                                             draft()))
        self.assertTrue(any("не вышло" in t for t in link.said))


class OptionsTest(unittest.TestCase):
    """Площадка отвечает «заполните все обязательные характеристики», не
    уточняя какие. Поэтому спрашиваем все и шлём выбранное."""

    class Row:
        def __init__(self, field, group, label, value):
            self.field, self.group = field, group
            self.label, self.value = label, value

    class Acc:
        def __init__(self, rows):
            self.rows = rows

        def get_game_category(self, id=None):
            return type("C", (), {"options": self.rows})()

    def test_options_are_grouped_by_field(self):
        rows = [self.Row("platform", "Платформа", "ПК", "PC"),
                self.Row("platform", "Платформа", "Телефон", "MOBILE"),
                self.Row("kind", "Вид", "Код", "CODE")]
        groups = item_bot.category_options(self.Acc(rows), "c1")

        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]["choices"]), 2)
        self.assertEqual(groups[0]["group"], "Платформа")

    def test_unreadable_options_are_not_a_crash(self):
        class Broken:
            def get_game_category(self, id=None):
                raise RuntimeError("площадка молчит")

        self.assertEqual(item_bot.category_options(Broken(), "c1"), [])

    def test_choice_reaches_the_marketplace(self):
        d = draft()
        d.options = [{"field": "platform", "group": "Платформа",
                      "value": None,
                      "choices": [{"label": "ПК", "value": "PC"},
                                  {"label": "Телефон", "value": "MOBILE"}]}]
        link = FakeLink([item_bot.PICK_OPTION + "1"])

        self.assertTrue(item_bot.choose_option(link, None, d))
        self.assertEqual(d.attributes(), {"platform": "MOBILE"})

        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, d)

        self.assertEqual(account.created[0]["options"],
                         {"platform": "MOBILE"})

    def test_empty_attributes_are_not_sent_as_chosen(self):
        account = FakeAccount()
        item_bot.send_draft(FakeLink(), account, draft())

        self.assertEqual(account.created[0]["options"], {})


class AccountsTest(unittest.TestCase):
    """Библиотека площадки держит аккаунт синглтоном, поэтому переключение
    заменяет единственный кабинет, а не заводит второй."""

    COOKIES = "token=" + "j" * 60
    UA = "Mozilla/5.0 тестовый"

    def setUp(self):
        self.folder = os.path.join(tempfile.mkdtemp(), "кабинеты")
        item_bot.ACCOUNTS_DIR = self.folder
        self.store = AccountStore(self.folder)
        self.opened = []

        def fake_open(cookies, user_agent):
            self.opened.append((cookies, user_agent))
            return f"аккаунт:{cookies[-4:]}"

        self.saved_open = item_bot.open_account
        item_bot.open_account = fake_open

    def tearDown(self):
        item_bot.open_account = self.saved_open

    def test_switching_opens_the_saved_cookies(self):
        aid = self.store.add("Второй", self.COOKIES, self.UA)
        link = FakeLink()
        got = item_bot.switch_account(link, self.store, aid, "прежний")

        self.assertEqual(self.opened, [(self.COOKIES, self.UA)])
        self.assertEqual(got, "аккаунт:" + self.COOKIES[-4:])
        self.assertEqual(self.store.current().id, aid)

    def test_failed_login_keeps_the_previous_cabinet(self):
        """Остаться без кабинета хуже, чем остаться на прежнем."""
        def broken(cookies, user_agent):
            raise RuntimeError("куки протухли")

        item_bot.open_account = broken
        aid = self.store.add("Второй", self.COOKIES, self.UA)
        link = FakeLink()

        self.assertEqual(
            item_bot.switch_account(link, self.store, aid, "прежний"),
            "прежний")
        self.assertTrue(any("не вышло" in t for t in link.said))

    def test_expired_cookies_can_be_replaced_without_losing_the_cabinet(self):
        """Иначе пришлось бы заводить кабинет заново, теряя имя."""
        calls = []

        def picky(cookies, user_agent):
            calls.append(cookies)

            if len(calls) == 1:
                raise RuntimeError("куки протухли")

            return "аккаунт:новый"

        item_bot.open_account = picky
        aid = self.store.add("Основной", self.COOKIES, self.UA)
        fresh = "token=" + "n" * 60
        link = FakeLink([item_bot.PICK_FIX + aid, fresh])
        got = item_bot.switch_account(link, self.store, aid, "прежний")

        self.assertEqual(got, "аккаунт:новый")
        self.assertEqual(self.store.get(aid).cookies, fresh)
        self.assertEqual(self.store.get(aid).name, "Основной")

    def test_refusing_to_send_cookies_keeps_everything(self):
        def broken(cookies, user_agent):
            raise RuntimeError("куки протухли")

        item_bot.open_account = broken
        aid = self.store.add("Основной", self.COOKIES, self.UA)
        link = FakeLink(["отмена"])

        self.assertEqual(
            item_bot.switch_account(link, self.store, aid, "прежний"),
            "прежний")
        self.assertEqual(self.store.get(aid).cookies, self.COOKIES)

    def test_bad_cookies_do_not_stop_the_bot(self):
        """Починка живёт в меню бота: упав, он запер бы её за собой."""
        def broken():
            raise RuntimeError("Не удалось подключиться к аккаунту Playerok")

        saved = item_bot.sign_in
        item_bot.sign_in = broken
        link = FakeLink()

        try:
            self.assertIsNone(item_bot.try_sign_in(link))
            self.assertTrue(any("Аккаунт" in t for t in link.said))
        finally:
            item_bot.sign_in = saved

    def test_missing_account_keeps_the_previous_one(self):
        link = FakeLink()

        self.assertEqual(
            item_bot.switch_account(link, self.store, "deadbeef", "прежний"),
            "прежний")

    def test_adding_stores_and_switches(self):
        link = FakeLink(["Основной", self.COOKIES, "пропустить"])
        got = item_bot.add_account(link, self.store, "прежний")
        saved = self.store.all()[0]

        self.assertEqual(saved.name, "Основной")
        self.assertEqual(saved.cookies, self.COOKIES)
        self.assertEqual(self.store.current().id, saved.id)
        self.assertTrue(got.startswith("аккаунт:"))

    def test_cookie_message_is_deleted(self):
        """В переписке остался бы доступ к кабинету."""
        link = FakeLink(["Основной", self.COOKIES, "пропустить"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(len(link.deleted), 1)

    def test_junk_instead_of_cookies_saves_nothing(self):
        link = FakeLink(["Основной", "ок"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all(), [])

    def test_cancel_saves_nothing(self):
        item_bot.add_account(FakeLink(["отмена"]), self.store, "прежний")

        self.assertEqual(self.store.all(), [])

    def test_json_export_is_accepted_as_cookies(self):
        """С телефона куки достают расширением — оно отдаёт JSON."""
        import json as js
        export = js.dumps([{"name": "token", "value": "j" * 60}])
        link = FakeLink(["Основной", export, "пропустить"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all()[0].cookies, "token=" + "j" * 60)


class TemplateFlowTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.store = TemplateStore(folder_for(item_bot.TEMPLATE_DIR, ""))

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

    def test_template_keeps_everything_needed_to_repeat(self):
        item_bot.offer_template(FakeLink(["да"]),
                                draft(description="Мой текст.",
                                      comment="пометка"))
        template = self.store.all()[0]

        self.assertEqual(template.description, "Мой текст.")
        self.assertEqual(template.category, CATEGORY)
        self.assertEqual(template.obtaining, OBTAINING)
        self.assertEqual(template.fields[0]["value"], "пометка")
        self.assertTrue(template.complete())

    def test_old_template_without_a_category_is_refused(self):
        """Повторять его нечем: товар ушёл бы не туда."""
        tid = self.store.save("старый", 1, "GL", [PNG])
        link = FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"])
        account = FakeAccount()
        item_bot.from_template(link, account)

        self.assertEqual(account.created, [])
        self.assertTrue(any("категор" in t.lower() for t in link.said))

    def test_one_press_recreates_the_same_item(self):
        """То, ради чего всё это: нажал — и объявление такое же."""
        tid = self.store.save("80 Robux", 149, "GL", [PNG],
                              description="Мой текст.", game=GAME,
                              category=CATEGORY, obtaining=OBTAINING,
                              fields=[{"id": "f1", "label": "Комментарий",
                                       "required": False, "value": "пометка"}])
        account = FakeAccount()
        item_bot.from_template(
            FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"]),
            account)

        sent = account.created[0]
        self.assertEqual(sent["name"], "80 Robux")
        self.assertEqual(sent["price"], 149)
        self.assertEqual(sent["attachments"], [PNG])
        self.assertIn("Регион кода: GL", sent["description"])
        self.assertIn("Мой текст.", sent["description"])
        self.assertEqual(sent["data_fields"][0].value, "пометка")
        self.assertEqual(sent["game_category_id"], "c1")

    def test_no_templates_is_explained_not_silent(self):
        link = FakeLink()
        account = FakeAccount()
        item_bot.from_template(link, account)

        self.assertEqual(account.created, [])
        self.assertTrue(any("шаблон" in t.lower() for t in link.said))

    def test_cancel_creates_nothing(self):
        self.store.save("x", 1, "GL", [PNG], category=CATEGORY, obtaining=OBTAINING)
        account = FakeAccount()
        item_bot.from_template(FakeLink(["отмена"]), account)

        self.assertEqual(account.created, [])

    def test_foreign_id_creates_nothing(self):
        """Значение кнопки приходит снаружи."""
        self.store.save("x", 1, "GL", [PNG], category=CATEGORY, obtaining=OBTAINING)
        account = FakeAccount()

        for bad in ("tpl:../../etc", "tpl:", "tpl:deadbeef", "что попало"):
            item_bot.from_template(
                FakeLink([bad, item_bot.PICK_ACT + "make"]), account)

        self.assertEqual(account.created, [])

    def test_template_without_pictures_is_refused(self):
        """Без картинок площадка товар не примет — лучше сказать сразу."""
        tid = self.store.save("x", 1, "GL", [PNG], category=CATEGORY,
                              obtaining=OBTAINING)
        template = self.store.get(tid)
        os.unlink(os.path.join(template.folder, template.files[0]))

        link = FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"])
        account = FakeAccount()
        item_bot.from_template(link, account)

        self.assertEqual(account.created, [])
        self.assertTrue(any("картинк" in t.lower() for t in link.said))


class TemplateEditTest(unittest.TestCase):
    """Шаблон живёт долго, а цены и тексты меняются."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.store = TemplateStore(folder_for(item_bot.TEMPLATE_DIR, ""))
        self.tid = self.store.save("80 Robux", 149, "GL", [PNG],
                                   description="Старый текст.",
                                   category=CATEGORY, obtaining=OBTAINING)

    def edit(self, *answers):
        item_bot.edit_template(FakeLink(list(answers)), self.store, self.tid)

        return self.store.get(self.tid)

    def test_price_can_be_changed(self):
        self.assertEqual(self.edit(item_bot.PICK_ACT + "price", "199").price,
                         199)

    def test_name_can_be_changed(self):
        got = self.edit(item_bot.PICK_ACT + "name", "100 Robux")

        self.assertEqual(got.name, "100 Robux")

    def test_region_can_be_changed(self):
        self.assertEqual(self.edit(item_bot.PICK_ACT + "region", "RU").region,
                         "RU")

    def test_description_can_be_changed(self):
        got = self.edit(item_bot.PICK_ACT + "description", "Новый текст.")

        self.assertIn("Новый текст.", got.description)

    def test_other_fields_survive_an_edit(self):
        """Правят по одному полю; переписать целиком — потерять остальное."""
        got = self.edit(item_bot.PICK_ACT + "price", "199")

        self.assertEqual(got.name, "80 Robux")
        self.assertEqual(got.category, CATEGORY)
        self.assertEqual(got.photos(), [PNG])

    def test_bad_value_changes_nothing(self):
        got = self.edit(item_bot.PICK_ACT + "price", "дорого")

        self.assertEqual(got.price, 149)

    def test_cancel_changes_nothing(self):
        got = self.edit("отмена")

        self.assertEqual(got.price, 149)

    def test_photos_are_replaced_wholesale(self):
        """Дописывать к старым значит копить мусор."""
        item_bot.edit_photos(FakeLink(["готово"]), self.store, self.tid)
        self.assertEqual(self.store.get(self.tid).photos(), [PNG])

    def test_deleting_asks_first(self):
        template = self.store.get(self.tid)
        item_bot.drop_template(FakeLink(["отмена"]), self.store, template)

        self.assertIsNotNone(self.store.get(self.tid))

    def test_confirmed_deletion_removes_it(self):
        template = self.store.get(self.tid)
        item_bot.drop_template(FakeLink(["да"]), self.store, template)

        self.assertIsNone(self.store.get(self.tid))


class TemplatesPerAccountTest(unittest.TestCase):
    """Перемешанные шаблоны — это объявление, созданное не в том кабинете."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.accounts = AccountStore(item_bot.ACCOUNTS_DIR)

    def test_each_account_sees_only_its_own(self):
        first = self.accounts.add("Первый", "token=" + "a" * 60, "ua")
        second = self.accounts.add("Второй", "token=" + "b" * 60, "ua")

        self.accounts.set_current(first)
        item_bot.templates_of().save("Товар первого", 1, "GL", [PNG])

        self.accounts.set_current(second)
        item_bot.templates_of().save("Товар второго", 2, "RU", [PNG])

        self.accounts.set_current(first)
        names = [t.name for t in item_bot.templates_of().all()]

        self.assertEqual(names, ["Товар первого"])

    def test_without_accounts_templates_still_work(self):
        item_bot.templates_of().save("Без кабинета", 1, "GL", [PNG])

        self.assertEqual(len(item_bot.templates_of().all()), 1)


if __name__ == "__main__":
    unittest.main()
