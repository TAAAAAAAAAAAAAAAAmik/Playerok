"""Тесты бота создания товаров. Ни телеграма, ни площадки — обе поддельные.

Проверяется сборка: доходит ли ответ до площадки, попадают ли в товар
картинки из шаблона и что происходит, когда шаблон испорчен.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import item_bot                                                 # noqa: E402
import wizard                                                   # noqa: E402
from accounts import AccountStore                               # noqa: E402
import vary                                                     # noqa: E402
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
        self.last_buttons = None

    def _delete(self, message):
        self.deleted.append(message)
        return True

    def say(self, text, buttons=None):
        self.said.append(text)
        return True

    def screen(self, text, buttons=None):
        """Экран: в жизни переписывает одно сообщение, здесь — просто
        запоминаем, чтобы тесты видели сказанное.

        Кнопки запоминаем тоже: экран без них — это тупик, из которого
        продавцу некуда нажимать.
        """
        self.said.append(text)
        self.screens.append(text)
        self.last_buttons = buttons
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
        link = FakeLink(["Основной", item_bot.PICK_WAY + "cookies",
                         self.COOKIES, "пропустить"])
        got = item_bot.add_account(link, self.store, "прежний")
        saved = self.store.all()[0]

        self.assertEqual(saved.name, "Основной")
        self.assertEqual(saved.cookies, self.COOKIES)
        self.assertEqual(self.store.current().id, saved.id)
        self.assertTrue(got.startswith("аккаунт:"))

    def test_cookie_message_is_deleted(self):
        """В переписке остался бы доступ к кабинету."""
        link = FakeLink(["Основной", item_bot.PICK_WAY + "cookies",
                         self.COOKIES, "пропустить"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(len(link.deleted), 1)

    def test_junk_instead_of_cookies_saves_nothing(self):
        link = FakeLink(["Основной", item_bot.PICK_WAY + "cookies", "ок"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all(), [])

    def test_cancel_saves_nothing(self):
        item_bot.add_account(FakeLink(["отмена"]), self.store, "прежний")

        self.assertEqual(self.store.all(), [])

    def test_json_export_is_accepted_as_cookies(self):
        """С телефона куки достают расширением — оно отдаёт JSON."""
        import json as js
        export = js.dumps([{"name": "token", "value": "j" * 60}])
        link = FakeLink(["Основной", item_bot.PICK_WAY + "cookies", export,
                         "пропустить"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all()[0].cookies, "token=" + "j" * 60)


class CheckSessionTest(unittest.TestCase):
    """Проверка сессии должна отвечать на «почему бот не работает» — и
    заодно на то, о чём площадка молчит до худшего момента."""

    class Me:
        def __init__(self, **kw):
            self.username = kw.get("username", "tamik")
            self.is_blocked = kw.get("is_blocked", False)
            self.is_blocked_for = kw.get("is_blocked_for", "")
            self.can_publish_items = kw.get("can_publish_items", True)
            self.unread_chats_counter = kw.get("unread", 0)

    class Acc:
        def __init__(self, me=None, error=None):
            self.me, self.error = me, error

        def get(self):
            if self.error:
                raise self.error

            return self.me

    class Unauthorized(Exception):
        def __str__(self):
            return "Не удалось подключиться к аккаунту Playerok."

    def setUp(self):
        item_bot.ACCOUNTS_DIR = os.path.join(tempfile.mkdtemp(), "кабинеты")

    def test_live_session_is_reported(self):
        link = FakeLink()
        item_bot.check_session(link, self.Acc(self.Me()))

        self.assertIn("живая", link.said[-1])
        self.assertIn("tamik", link.said[-1])

    def test_expired_session_explains_what_to_do(self):
        link = FakeLink()
        item_bot.check_session(link, self.Acc(error=self.Unauthorized()))

        self.assertIn("не действует", link.said[-1])
        self.assertIn("Аккаунт", link.said[-1])

    def test_network_trouble_is_not_blamed_on_cookies(self):
        """Иначе продавец пойдёт доставать куки там, где просто оборвалась
        связь."""
        link = FakeLink()
        item_bot.check_session(link, self.Acc(error=OSError("сеть упала")))

        self.assertIn("обрыв связи", link.said[-1])

    def test_blocked_cabinet_is_shown(self):
        """Иначе это выглядит как «бот сломался»."""
        link = FakeLink()
        item_bot.check_session(link, self.Acc(
            self.Me(is_blocked=True, is_blocked_for="жалобы")))

        self.assertIn("заблокирован", link.said[-1])
        self.assertIn("жалобы", link.said[-1])

    def test_publishing_ban_is_shown(self):
        link = FakeLink()
        item_bot.check_session(link, self.Acc(
            self.Me(can_publish_items=False)))

        self.assertIn("не разрешает выставлять", link.said[-1])

    def test_unread_chats_are_mentioned(self):
        link = FakeLink()
        item_bot.check_session(link, self.Acc(self.Me(unread=3)))

        self.assertIn("3", link.said[-1])

    def test_without_a_cabinet_it_says_so(self):
        link = FakeLink()
        item_bot.check_session(link, None)

        self.assertIn("не выбран", link.said[-1])


class EmailLoginTest(unittest.TestCase):
    """Вход по коду на почту: сессию получаем сами, браузер не нужен."""

    COOKIES = "token=" + "j" * 60

    def setUp(self):
        self.folder = os.path.join(tempfile.mkdtemp(), "кабинеты")
        item_bot.ACCOUNTS_DIR = self.folder
        self.store = AccountStore(self.folder)
        self.sent = []
        self.saved_open = item_bot.open_account
        self.saved_send = item_bot.emailauth.send_code
        self.saved_confirm = item_bot.emailauth.confirm
        item_bot.open_account = lambda c, u: f"аккаунт:{c[-4:]}"

    def tearDown(self):
        item_bot.open_account = self.saved_open
        item_bot.emailauth.send_code = self.saved_send
        item_bot.emailauth.confirm = self.saved_confirm

    def works(self):
        def send(email, ua, session=None):
            self.sent.append((email, ua))
            return True, ""

        item_bot.emailauth.send_code = send
        item_bot.emailauth.confirm = \
            lambda email, code, ua, session=None: (self.COOKIES, "")

    def test_account_is_created_without_any_cookies_from_the_user(self):
        self.works()
        link = FakeLink(["Основной", item_bot.PICK_WAY + "mail",
                         "seller@example.com", "123456"])
        got = item_bot.add_account(link, self.store, "прежний")
        saved = self.store.all()[0]

        self.assertEqual(saved.name, "Основной")
        self.assertEqual(saved.cookies, self.COOKIES)
        self.assertTrue(got.startswith("аккаунт:"))

    def test_the_same_user_agent_is_used_for_login_and_work(self):
        """Площадка сверяет его с тем, при котором сессия выдана."""
        self.works()
        link = FakeLink(["Основной", item_bot.PICK_WAY + "mail",
                         "seller@example.com", "123456"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.sent[0][1], self.store.all()[0].user_agent)

    def test_refused_code_saves_nothing_and_says_why(self):
        item_bot.emailauth.send_code = \
            lambda email, ua, session=None: (True, "")
        item_bot.emailauth.confirm = \
            lambda email, code, ua, session=None: ("", "код неверный или уже истёк")

        link = FakeLink(["Основной", item_bot.PICK_WAY + "mail",
                         "seller@example.com", "000000"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all(), [])
        self.assertIn("неверный", link.said[-1])

    def test_unsent_code_does_not_ask_for_it(self):
        """Спрашивать код, которого не отправляли, — издевательство."""
        item_bot.emailauth.send_code = \
            lambda email, ua, session=None: (False, "аккаунта с такой почтой нет")

        link = FakeLink(["Основной", item_bot.PICK_WAY + "mail",
                         "нет@example.com"])
        item_bot.add_account(link, self.store, "прежний")

        self.assertEqual(self.store.all(), [])
        self.assertIn("не отправлен", link.said[-1])

    def test_cancel_at_any_step_saves_nothing(self):
        self.works()

        for answers in (["Основной", "отмена"],
                        ["Основной", item_bot.PICK_WAY + "mail", "отмена"],
                        ["Основной", item_bot.PICK_WAY + "mail",
                         "seller@example.com", "отмена"]):
            item_bot.add_account(FakeLink(answers), self.store, "прежний")

        self.assertEqual(self.store.all(), [])

    def test_expired_cabinet_is_renewed_keeping_its_name(self):
        """Иначе пришлось бы заводить кабинет заново, теряя шаблоны."""
        self.works()
        aid = self.store.add("Основной", "token=" + "o" * 60, "ua")
        saved = self.store.get(aid)
        link = FakeLink(["seller@example.com", "123456"])
        got = item_bot.renew_by_email(link, self.store, saved, "прежний")

        self.assertEqual(self.store.get(aid).cookies, self.COOKIES)
        self.assertEqual(self.store.get(aid).name, "Основной")
        self.assertTrue(got.startswith("аккаунт:"))


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
        # Пометка продавца на месте — её не выбрасывают, а дополняют:
        # он её для чего-то ставил.
        self.assertTrue(sent["data_fields"][0].value.startswith("пометка"))
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


class SettingsMenuTest(unittest.TestCase):
    """«Автовыдача» — кнопка, за которой продавец включает выдачу кодов.

    Она однажды падала на первом же обращении (NameError), и снаружи это
    выглядело как «не грузит»: нажал — тишина.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")

    def buttons(self, keys):
        """Все подписи кнопок экрана — раскладка кладёт их по две в ряд."""
        return [name for row in keys for name, _ in row]

    def test_menu_lists_every_card_we_can_deliver(self):
        link = FakeLink(["отмена"])
        item_bot.settings_menu(link)
        question, keys = link.asked[0]
        names = self.buttons(keys)

        self.assertIn("Автовыдача", question)

        for card in item_bot.CARDS:
            self.assertTrue(any(card.title in name for name in names),
                            f"{card.title} не показан")

    def test_card_opens_and_shows_whether_delivery_is_on(self):
        slug = item_bot.CARDS[0].slug
        link = FakeLink([item_bot.PICK_CARD + slug, "отмена"])
        item_bot.settings_menu(link)

        self.assertTrue(any("Выдача: выключена" in said for said in link.said))

    def test_card_screen_says_whether_nominals_were_measured(self):
        measured = next(c for c in item_bot.CARDS if c.measured)
        unmeasured = next(c for c in item_bot.CARDS if not c.measured)

        link = FakeLink([item_bot.PICK_CARD + measured.slug, "отмена"])
        item_bot.settings_menu(link)
        self.assertTrue(any("мерен" in said for said in link.said))

        link = FakeLink([item_bot.PICK_CARD + unmeasured.slug, "отмена"])
        item_bot.settings_menu(link)
        self.assertTrue(any("не проверяли" in said for said in link.said))

    def test_switching_delivery_on_is_saved(self):
        slug = item_bot.CARDS[0].slug
        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "on", "отмена"])
        item_bot.settings_menu(link)

        self.assertTrue(item_bot.settings_of().card(slug)["enabled"])

    def test_every_setting_has_its_own_button(self):
        slug = item_bot.CARDS[0].slug
        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "cfg", "отмена", "отмена"])
        item_bot.settings_menu(link)
        screen = next(keys for question, keys in link.asked
                      if "настройки" in question)

        for _, label, _ in item_bot.FIELDS:
            self.assertIn(label, self.buttons(screen))

    def test_a_setting_is_saved(self):
        slug = item_bot.CARDS[0].slug
        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "cfg",
                         item_bot.PICK_SET + "greeting",
                         "Принял заказ, код будет через минуту",
                         "отмена", "отмена"])
        item_bot.settings_menu(link)

        self.assertEqual(item_bot.settings_of().card(slug)["greeting"],
                         "Принял заказ, код будет через минуту")

    def test_a_dot_clears_a_setting_instead_of_storing_a_dot(self):
        """Точка-текстом в `keyword` — это карта, переставшая узнавать свои
        заказы и забирающая чужие: точка есть в любом названии."""
        slug = item_bot.CARDS[0].slug
        conf = item_bot.settings_of()
        conf.set_keyword(slug, "эпл")

        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "cfg",
                         item_bot.PICK_SET + "keyword", ".",
                         "отмена", "отмена"])
        item_bot.settings_menu(link)

        self.assertEqual(item_bot.settings_of().card(slug)["keyword"], "")

    def test_skipping_leaves_the_setting_alone(self):
        slug = item_bot.CARDS[0].slug
        item_bot.settings_of().set_note(slug, "было")

        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "cfg",
                         item_bot.PICK_SET + "note", "пропустить",
                         "отмена", "отмена"])
        item_bot.settings_menu(link)

        self.assertEqual(item_bot.settings_of().card(slug)["note"], "было")

    def test_service_id_is_saved_for_the_region(self):
        slug = item_bot.CARDS[0].slug
        region = item_bot.REGIONS[0]
        link = FakeLink([item_bot.PICK_CARD + slug,
                         item_bot.PICK_SET + "cfg",
                         item_bot.PICK_SET + "svc",
                         item_bot.PICK_SET + "svc" + region,
                         "abc-123", "отмена", "отмена", "отмена"])
        item_bot.settings_menu(link)

        self.assertEqual(item_bot.settings_of().service_id(slug, region),
                         "abc-123")

    def test_settings_are_kept_per_account(self):
        accounts = AccountStore(item_bot.ACCOUNTS_DIR)
        first = accounts.add("Первый", "token=" + "a" * 60, "ua")
        second = accounts.add("Второй", "token=" + "b" * 60, "ua")
        slug = item_bot.CARDS[0].slug

        accounts.set_current(first)
        item_bot.settings_of().set_enabled(slug, True)

        accounts.set_current(second)

        self.assertFalse(item_bot.settings_of().card(slug)["enabled"])


class BrokenCommandTest(unittest.TestCase):
    """Упавший обработчик не должен уносить бота вместе с собой."""

    def test_the_seller_is_told_instead_of_silence(self):
        link = FakeLink()
        broken = item_bot.handle_command

        def explode(*args, **kwargs):
            raise RuntimeError("внутри всё сломалось")

        item_bot.handle_command = explode
        try:
            item_bot.serve_one(link, "кабинет", "настройки")
        finally:
            item_bot.handle_command = broken

        self.assertTrue(any("внутри всё сломалось" in said
                            for said in link.said))

    def test_the_account_survives_the_error(self):
        link = FakeLink()
        broken = item_bot.handle_command

        def explode(*args, **kwargs):
            raise RuntimeError("бум")

        item_bot.handle_command = explode
        try:
            kept = item_bot.serve_one(link, "кабинет", "настройки")
        finally:
            item_bot.handle_command = broken

        self.assertEqual(kept, "кабинет")

    def test_a_working_command_is_untouched(self):
        link = FakeLink()
        kept = item_bot.serve_one(link, "кабинет", "меню")

        self.assertEqual(kept, "кабинет")
        self.assertTrue(any("Что делаем?" in said for said in link.said))


class CardTemplateTest(unittest.TestCase):
    """Описание берётся от узнанной карты, а не одно на все тринадцать."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")

    def draft(self, name, region="US", price=900, nominal=10):
        d = wizard.Draft()
        d.name = name
        d.region = region
        d.price = price
        d.nominal = nominal
        d.description = ""
        return d

    def test_an_apple_listing_does_not_mention_roblox(self):
        d = self.draft("Apple Gift Card 10$")
        item_bot.apply_card_template(d)
        text = wizard.description_for(d)

        self.assertIn("App Store", text)
        self.assertNotIn("roblox", text.lower())

    def test_a_roblox_listing_keeps_its_own_activation(self):
        d = self.draft("Roblox 1000 Robux", region="GL")
        item_bot.apply_card_template(d)

        self.assertIn("roblox.com/redeem", wizard.description_for(d))

    def test_the_sellers_own_template_wins(self):
        item_bot.settings_of().set_ad_text("apple", "Мой текст про {номинал}")
        d = self.draft("Apple Gift Card 10$")
        item_bot.apply_card_template(d)

        self.assertIn("Мой текст про 10$", wizard.description_for(d))

    def test_an_unknown_name_changes_nothing(self):
        d = self.draft("Валюта в какой-то игре")
        item_bot.apply_card_template(d)

        self.assertEqual(d.tail, "")

    def test_what_the_seller_wrote_is_never_overwritten(self):
        d = self.draft("Apple Gift Card 10$")
        d.description = "Свои условия"
        item_bot.apply_card_template(d)

        self.assertIn("Свои условия", wizard.description_for(d))


class CardRegistryTest(unittest.TestCase):
    """Реестр карт: опечатка здесь тихо выключает целое семейство."""

    def setUp(self):
        # Свой каталог настроек: иначе проверка «по умолчанию выключено»
        # зависела бы от того, включил ли карту соседний тест.
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")

    def test_slugs_are_unique(self):
        slugs = [c.slug for c in item_bot.CARDS]

        self.assertEqual(len(slugs), len(set(slugs)))

    def test_every_card_can_be_found_and_delivered(self):
        for card in item_bot.CARDS:
            self.assertTrue(card.subcategory, f"{card.slug}: нет подкатегории")
            self.assertTrue(card.keywords, f"{card.slug}: нет слов")
            self.assertTrue(card.activation, f"{card.slug}: нет активации")
            self.assertTrue(card.title, f"{card.slug}: нет названия")

    def test_every_card_is_recognised_by_its_own_title(self):
        """Иначе продавец, назвавший товар как карту, не получит ни
        заготовки, ни выдачи."""
        for card in item_bot.CARDS:
            got = item_bot.card_for_title(item_bot.CARDS,
                                          f"{card.title} 10")

            self.assertIsNotNone(got, card.slug)

    def test_delivery_is_off_for_every_card_by_default(self):
        """Включённая карта тратит деньги продавца у поставщика."""
        conf = item_bot.settings_of()

        for card in item_bot.CARDS:
            self.assertFalse(conf.card(card.slug)["enabled"], card.slug)


class SeriesFromSupplierTest(unittest.TestCase):
    """Номиналы берутся у поставщика — продавец вводит только цены."""

    CATALOG = {"services": [
        {"id": "s-gl", "name": "Roblox Gift Cards Global",
         "subcategoryName": "Roblox Gift Cards",
         "items": [{"id": "i100", "value": 100, "inStock": 9, "price": 1.1},
                   {"id": "i400", "value": 400, "inStock": 9, "price": 4.0},
                   {"id": "i800", "value": 800, "inStock": 0, "price": 7.9}]},
        {"id": "s-ru", "name": "Roblox Gift Cards RU",
         "subcategoryName": "Roblox Gift Cards",
         "items": [{"id": "r700", "value": 700, "inStock": 9, "price": 9.9}]},
    ]}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        os.environ["APPROUTE_KEY"] = "ключ"
        item_bot._CATALOG["raw"] = self.CATALOG
        item_bot._CATALOG["at"] = time.time()

    def tearDown(self):
        item_bot._CATALOG["raw"] = None
        os.environ.pop("APPROUTE_KEY", None)

    def template(self, name="100 Robux Global", region="GL", game="Roblox"):
        return item_bot.templates_of().save(
            name, 150, region, [PNG], description="Выдаём 100 робуксов",
            game={"id": "g", "name": game},
            category={"id": "c", "name": "Валюта"},
            obtaining={"id": "o", "name": "Без входа"})

    def test_the_supplier_nominals_are_offered_for_pricing(self):
        link = FakeLink([item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertIn("100 =", sheet)
        self.assertIn("400 =", sheet)

    def test_what_is_out_of_stock_is_not_offered(self):
        """Объявление по такому номиналу бот выдать не сможет, а покупатель
        заплатит и будет ждать."""
        link = FakeLink([item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertNotIn("800", sheet)

    def test_another_regions_nominals_are_not_offered(self):
        """Код чужого региона покупатель не активирует — объявление по
        нему стало бы спором, а не продажей."""
        link = FakeLink([item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertNotIn("700", sheet)

    def test_prices_can_be_counted_from_the_purchase(self):
        link = FakeLink([item_bot.PICK_SERIES + self.template(),
                         "взять", "посчитать", "100", "40", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        # 1.1 $ × 100 ₽ × 1.4 = 154 → вверх до десятки.
        self.assertIn("100 = 160", sheet)
        self.assertIn("400 = 560", sheet)

    def test_without_a_key_the_seller_is_told_why(self):
        os.environ.pop("APPROUTE_KEY", None)
        item_bot._CATALOG["raw"] = None
        link = FakeLink([item_bot.PICK_SERIES + self.template(), "взять"])
        item_bot.series_menu(link, "кабинет")

        self.assertIn("APPROUTE_KEY", link.said[-1])

    def test_a_name_without_a_number_is_asked_about_not_refused(self):
        """У продавца названия бывают какие угодно — «🥳ПРОМОКОДОМ🥳
        АВТОВЫДАЧА». Заставлять его переименовывать товар ради нашего
        разбора дороже, чем задать один вопрос."""
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SERIES + tid, "так",
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any("подставлять номинал некуда" in t
                            for t in link.said))
        self.assertTrue(any(t.startswith("100 =") for t in link.said))

    def test_the_offered_pattern_builds_real_names(self):
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SERIES + tid, "так", "сам",
                         "400 = 540", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any("400 🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА" in t
                            for t in link.said))

    def test_a_pattern_without_a_place_for_the_nominal_is_refused(self):
        """Десяток объявлений с одинаковым названием — мусор на витрине,
        который потом снимать руками."""
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SERIES + tid, "Просто название"])
        item_bot.series_menu(link, "кабинет")

        self.assertIn("нет места под номинал", link.said[-1])
        self.assertEqual(link.last_buttons, item_bot.MENU)

    def test_the_card_is_recognised_by_the_game_when_the_name_is_odd(self):
        """Продавец назвал товар по-своему — но игра в шаблоне записана, и
        по ней карта узнаётся."""
        link = FakeLink([item_bot.PICK_SERIES + self.template("Валюта 100"),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any(t.startswith("100 =") for t in link.said))

    def test_an_unknown_card_still_allows_typing_by_hand(self):
        """Подкатегория живёт в карте: не узнав её, брать номиналы неоткуда
        — и предлагать несбыточное незачем."""
        link = FakeLink([item_bot.PICK_SERIES + self.template(
            "Валюта 100", game="Arizona RP"), "отмена"])
        item_bot.series_menu(link, "кабинет")
        asked = [q for q, _ in link.asked]

        self.assertFalse(any("Откуда взять" in q for q in asked))
        self.assertTrue(any("по одному в строке" in q for q in asked))


class LastWordTest(unittest.TestCase):
    """Команда обязана закончиться экраном с меню.

    Дважды одна и та же беда. Сначала обработчик дописывал «Готов к
    следующему» поверх — а экран переписывает ТО ЖЕ сообщение, и последнее
    слово стиралось: «Шаблонов пока нет» показывалось и тут же исчезало.
    Со стороны это выглядело как «нажал — сообщение сразу убралось».
    Потом оказалось, что часть ответов и вовсе оставалась без кнопок, и
    продавцу было некуда нажимать.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")

    def run_command(self, word, answers=()):
        link = FakeLink(list(answers))
        item_bot.handle_command(link, "кабинет", word)

        return link

    def test_an_empty_template_list_stays_on_screen(self):
        """Ровно тот случай, с которого всё началось."""
        link = self.run_command("серия")

        self.assertIn("Шаблонов пока нет", link.said[-1])
        self.assertEqual(link.last_buttons, item_bot.MENU)

    def test_no_command_ends_without_buttons(self):
        for word in ("шаблон", "серия", "настройки", "меню"):
            link = self.run_command(word, ["отмена", "отмена"])

            self.assertIsNotNone(link.last_buttons, word)

    def test_the_handler_does_not_write_over_its_own_answer(self):
        """Приписка поверх — это стёртый ответ, а не вежливость."""
        link = self.run_command("серия")

        self.assertNotIn("Готов к следующему", link.said[-1])


class VariedFieldsTest(unittest.TestCase):
    """Копии не должны совпадать до буквы.

    Шаблон повторяет товар дословно, а площадки не любят одинаковые
    объявления: десяток товаров с совпадающими полями выглядит накруткой,
    даже когда это просто разные номиналы одного кода.
    """

    CATALOG = {"services": [
        {"id": "s", "name": "Roblox Gift Cards Global",
         "subcategoryName": "Roblox Gift Cards",
         "items": [{"id": f"i{v}", "value": v, "inStock": 9, "price": 1.0}
                   for v in (100, 200, 400, 800)]}]}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot._CATALOG["raw"] = self.CATALOG
        item_bot._CATALOG["at"] = time.time()

    def tearDown(self):
        item_bot._CATALOG["raw"] = None

    def series(self, rows="200 = 140\n400 = 280\n800 = 560"):
        tid = item_bot.templates_of().save(
            "100 Robux", 70, "GL", [PNG], description="Коды сразу",
            game=GAME, category=CATEGORY, obtaining=OBTAINING,
            fields=[{"id": "f1", "label": "Комментарий",
                     "required": False, "value": ""},
                    {"id": "f2", "label": "Промокод",
                     "required": False, "value": ""}])
        account = FakeAccount()
        link = FakeLink([item_bot.PICK_SERIES + tid, "сам", rows, "да"])
        item_bot.series_menu(link, account)

        return account.created

    def values(self, created, index=0):
        return [c["data_fields"][index].value for c in created]

    def test_a_batch_does_not_repeat_itself(self):
        created = self.series()

        self.assertEqual(len(created), 3)
        self.assertEqual(len(set(self.values(created))), 3)

    def test_the_promo_field_never_looks_like_a_code(self):
        """Случайная строка там читается как код на скидку, которого нет:
        покупатель попробует применить, не выйдет — и это уже не
        разнообразие, а обман."""
        created = self.series()

        for value in self.values(created, index=1):
            # Не код: у кода нет пробелов и точки в конце.
            self.assertIn(" ", value)
            self.assertTrue(value.endswith("."))
            # И ни слова о том, есть промокод или нет: это решает
            # продавец — его объявление так и называется.
            self.assertNotIn("промокод", value.lower())

    def test_fields_that_are_not_free_text_are_left_alone(self):
        """Площадка принимает в них только свои значения — вписав туда
        фразу, мы получили бы отказ на последнем шаге."""
        fields = [{"id": "f1", "label": "Регион", "value": "GL"}]
        vary.apply(fields)

        self.assertEqual(fields[0]["value"], "GL")

    def test_the_sellers_own_text_survives(self):
        fields = [{"id": "f1", "label": "Комментарий", "value": "моё"}]
        vary.apply(fields)

        self.assertTrue(fields[0]["value"].startswith("моё"))

    def test_two_presses_in_a_row_differ(self):
        """Без общего чередования соседние нажатия легко дают одну фразу —
        а это ровно та копия, которой мы и избегаем."""
        tid = item_bot.templates_of().save(
            "100 Robux", 70, "GL", [PNG], description="Коды сразу",
            game=GAME, category=CATEGORY, obtaining=OBTAINING,
            fields=[{"id": "f1", "label": "Комментарий",
                     "required": False, "value": ""}])
        account = FakeAccount()

        for _ in range(4):
            item_bot.from_template(
                FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"]),
                account)

        got = [c["data_fields"][0].value for c in account.created]

        self.assertEqual(len(set(got)), 4)

    def test_applying_twice_does_not_stack_the_same_phrase(self):
        fields = [{"id": "f1", "label": "Комментарий", "value": ""}]
        vary.apply(fields)
        once = fields[0]["value"]
        fields[0]["value"] = once
        vary.apply(fields)

        self.assertEqual(fields[0]["value"].count(once), 1)


class PriceBumpTest(unittest.TestCase):
    """Четвёртая копия по той же цене — уже не ассортимент."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        self.tid = item_bot.templates_of().save(
            "1000 Robux", 1320, "GL", [PNG], description="Коды",
            game=GAME, category=CATEGORY, obtaining=OBTAINING)

    def press(self, times):
        account = FakeAccount()
        screens = []

        for _ in range(times):
            link = FakeLink([item_bot.PICK + self.tid,
                             item_bot.PICK_ACT + "make"])
            item_bot.from_template(link, account)
            screens.append(next(t for t in link.said if "Повторяю" in t))

        return account.created, screens

    def test_the_first_three_keep_the_price(self):
        created, _ = self.press(3)

        self.assertEqual([c["price"] for c in created], [1320] * 3)

    def test_the_fourth_costs_a_rouble_more(self):
        created, _ = self.press(4)

        self.assertEqual(created[-1]["price"], 1321)

    def test_the_seller_is_told_the_price_changed(self):
        """Молча изменить цену продавца нельзя: он её назначил сам."""
        _, screens = self.press(4)

        self.assertIn("поднята", screens[-1])
        self.assertNotIn("поднята", screens[0])

    def test_the_series_shows_raised_prices_before_creating(self):
        """Продавец должен увидеть настоящие цены до того, как согласится,
        а не узнать о них из готовых объявлений."""
        ledger = item_bot.ledger_of()

        for _ in range(3):
            ledger.remember(400, 540)

        link = FakeLink([item_bot.PICK_SERIES + self.tid, "сам",
                         "400 = 540", "отмена"])
        item_bot.series_menu(link, FakeAccount())
        plan = next(t for t in link.said if "Создам" in t)

        self.assertIn("541", plan)
        self.assertIn("поднята", plan)


if __name__ == "__main__":
    unittest.main()
