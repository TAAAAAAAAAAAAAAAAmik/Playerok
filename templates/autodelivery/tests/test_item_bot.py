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
from catalog import nominal_for                                 # noqa: E402
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
        d.nominal = 80
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


class ManyOptionsTest(unittest.TestCase):
    """Характеристика с тремя десятками вариантов.

    «Валюта» у Xbox — это все страны магазина. Показывались первые
    двенадцать, остальные обрезались молча: продавец искал Турцию и США, а
    список кончался на Индии — по алфавиту. Выглядело как «бот не умеет
    такие регионы», хотя он просто не дорисовал список.
    """

    # Как их отдаёт площадка: подпись со страной и валютой.
    COUNTRIES = ["Аргентина (ARS)", "Австрия (EUR)", "Бразилия (BRL)",
                 "Бельгия (EUR)", "Великобритания (GBP)", "Венгрия (HUF)",
                 "Вьетнам (VND)", "Германия (EUR)", "Гонконг (HKD)",
                 "Европа (EUR)", "Израиль (ILS)", "Индия (INR)",
                 "Испания (EUR)", "Италия (EUR)", "Канада (CAD)",
                 "Мексика (MXN)", "Норвегия (NOK)", "Польша (PLN)",
                 "Саудовская Аравия (SAR)", "США (USD)", "Турция (TRY)",
                 "Швеция (SEK)", "Япония (JPY)"]

    def draft(self):
        d = draft()
        d.nominal = 80
        d.options = [{"field": "currency", "group": "Валюта", "value": None,
                      "choices": [{"label": name, "value": name[-4:-1]}
                                  for name in self.COUNTRIES]}]

        return d

    def labels(self, link, which=-1):
        return [name for row in link.asked[which][1] for name, _ in row]

    def test_the_first_page_is_not_the_whole_list(self):
        """Обрезать молча — значит соврать про умения площадки."""
        d = self.draft()
        link = FakeLink(["отмена"])
        item_bot.choose_option(link, None, d)
        names = self.labels(link)

        self.assertIn("Аргентина (ARS)", names)
        self.assertNotIn("Турция (TRY)", names)
        self.assertTrue(any("Далее" in n for n in names), names)

    def test_the_pages_are_counted(self):
        link = FakeLink(["отмена"])
        item_bot.choose_option(link, None, self.draft())

        self.assertIn("Страница 1 из", link.asked[0][0])

    def test_turkey_is_reachable_by_paging(self):
        d = self.draft()
        link = FakeLink([item_bot.PICK_OPTION + "p1",
                         item_bot.PICK_OPTION + "p2", "отмена"])
        item_bot.choose_option(link, None, d)

        self.assertIn("Турция (TRY)", self.labels(link))

    def test_every_choice_is_reachable(self):
        seen = []
        page = 0

        while True:
            link = FakeLink(["отмена"])
            d = self.draft()
            # Листаем до нужной страницы и смотрим, что на ней.
            item_bot.choose_option(link, None, d, page)
            names = self.labels(link)
            seen += [n for n in names if n in self.COUNTRIES]

            if not any("Далее" in n for n in names):
                break

            page += 1

        self.assertEqual(sorted(set(seen)), sorted(self.COUNTRIES))

    def test_a_single_choice_is_not_a_question(self):
        """Площадка отдаёт «Сумма пополнения» одной кнопкой с тем же
        названием. Спрашивать про неё — требовать нажатия там, где ответ
        один, а продавец в это время набирает сумму и получает «„1" среди
        вариантов нет»."""
        d = draft()
        d.nominal = 80
        d.options = [{"field": "amount", "group": "Сумма пополнения",
                      "value": None,
                      "choices": [{"label": "Сумма пополнения",
                                   "value": "any"}]}]
        link = FakeLink([])

        self.assertTrue(item_bot.choose_option(link, None, d))
        self.assertEqual(link.asked, [])
        self.assertEqual(d.attributes(), {"amount": "any"})

    def test_a_single_empty_choice_is_a_number_to_type(self):
        """Площадка так отдаёт «Сумма пополнения»: одна строка без
        значения. Отправить её кнопкой — получить «атрибуты имеют
        некорректные значения»."""
        d = draft()
        d.nominal = 80
        d.options = [{"field": "amount", "group": "Сумма", "value": None,
                      "choices": [{"label": "Сумма", "value": None}]}]
        item_bot.choose_option(FakeLink(["250"]), None, d)

        self.assertEqual(d.attributes(), {"amount": 250})

    def test_two_choices_are_still_asked(self):
        d = draft()
        d.nominal = 80
        d.options = [{"field": "platform", "group": "Платформа",
                      "value": None,
                      "choices": [{"label": "ПК", "value": "PC"},
                                  {"label": "Телефон", "value": "MOBILE"}]}]
        link = FakeLink(["отмена"])
        item_bot.choose_option(link, None, d)

        self.assertEqual(len(link.asked), 1)

    def test_a_wrong_word_shows_what_is_chosen_here(self):
        d = self.draft()
        link = FakeLink(["1", "турция"])
        item_bot.choose_option(link, None, d)

        self.assertIn("Здесь выбирают из списка", link.asked[-1][0])
        self.assertIn("Аргентина", link.asked[-1][0])

    def test_a_typed_word_picks_the_country(self):
        """«турция» набрать быстрее, чем долистать до буквы Т."""
        d = self.draft()
        link = FakeLink(["турция"])

        self.assertTrue(item_bot.choose_option(link, None, d))
        self.assertEqual(d.options[0]["chosen"], "Турция (TRY)")

    def test_the_currency_code_works_too(self):
        d = self.draft()
        link = FakeLink(["usd"])
        item_bot.choose_option(link, None, d)

        self.assertEqual(d.options[0]["chosen"], "США (USD)")

    def test_several_matches_are_offered(self):
        d = self.draft()
        link = FakeLink(["eur", "отмена"])
        item_bot.choose_option(link, None, d)
        names = self.labels(link)

        self.assertIn("Австрия (EUR)", names)
        self.assertNotIn("Турция (TRY)", names)

    def test_an_unknown_word_does_not_close_the_list(self):
        """Раньше любой набранный текст означал «отменил»."""
        d = self.draft()
        link = FakeLink(["чепуха", "турция"])
        item_bot.choose_option(link, None, d)

        self.assertIn("среди вариантов нет", link.asked[-1][0])
        self.assertEqual(d.options[0]["chosen"], "Турция (TRY)")

    def test_cancel_still_cancels(self):
        d = self.draft()
        link = FakeLink(["отмена"])

        self.assertFalse(item_bot.choose_option(link, None, d))

    def test_a_short_list_has_no_paging(self):
        """Две кнопки листать незачем."""
        d = draft()
        d.nominal = 80
        d.options = [{"field": "platform", "group": "Платформа",
                      "value": None,
                      "choices": [{"label": "ПК", "value": "PC"},
                                  {"label": "Телефон", "value": "MOBILE"}]}]
        link = FakeLink(["отмена"])
        item_bot.choose_option(link, None, d)

        self.assertNotIn("Страница", link.asked[0][0])


class ChooseTest(unittest.TestCase):
    """Общий выбор: игры, категории, способы получения.

    Обрезался он молча по двенадцать, и тринадцатая категория для
    продавца просто не существовала: он видел конец списка и решал, что
    бот её не умеет.
    """

    ROWS = [(f"c{n}", f"Категория {n}") for n in range(25)]

    def labels(self, link, which=-1):
        return [name for row in link.asked[which][1] for name, _ in row]

    def test_the_whole_list_is_reachable(self):
        seen = []
        link = FakeLink(["выб:стр1", "выб:стр2", "отмена"])
        item_bot.choose(link, "Что?", self.ROWS, "выб:")

        for number in range(3):
            seen += [n for n in self.labels(link, number)
                     if n.startswith("Категория")]

        self.assertEqual(sorted(set(seen)), sorted(n for _, n in self.ROWS))

    def test_the_pages_are_counted(self):
        link = FakeLink(["отмена"])
        item_bot.choose(link, "Что?", self.ROWS, "выб:")

        self.assertIn("Страница 1 из 3", link.asked[0][0])

    def test_a_short_list_is_not_paged(self):
        link = FakeLink(["отмена"])
        item_bot.choose(link, "Что?", self.ROWS[:3], "выб:")

        self.assertNotIn("Страница", link.asked[0][0])

    def test_a_button_still_picks(self):
        link = FakeLink(["выб:c7"])
        got = item_bot.choose(link, "Что?", self.ROWS, "выб:")

        self.assertEqual(got, {"id": "c7", "name": "Категория 7"})

    def test_a_typed_word_picks_when_it_is_the_only_one(self):
        link = FakeLink(["категория 17"])
        got = item_bot.choose(link, "Что?", self.ROWS, "выб:")

        self.assertEqual(got["id"], "c17")

    def test_several_matches_are_offered(self):
        link = FakeLink(["категория 1", "выб:c13"])
        got = item_bot.choose(link, "Что?", self.ROWS, "выб:")

        self.assertEqual(got["id"], "c13")

    def test_an_unknown_word_does_not_cancel(self):
        link = FakeLink(["чепуха", "выб:c1"])
        got = item_bot.choose(link, "Что?", self.ROWS, "выб:")

        self.assertIn("в списке нет", link.asked[-1][0])
        self.assertEqual(got["id"], "c1")

    def test_cancel_cancels(self):
        link = FakeLink(["отмена"])

        self.assertIsNone(item_bot.choose(link, "Что?", self.ROWS, "выб:"))

    def test_thirteen_templates_are_all_reachable(self):
        """Тринадцатый шаблон было не достать вовсе."""
        rows = [(f"t{n}", f"Шаблон {n}") for n in range(13)]
        link = FakeLink(["выб:стр1", "отмена"])
        item_bot.choose(link, "Что?", rows, "выб:")
        names = [name for row in link.asked[-1][1] for name, _ in row]

        self.assertIn("Шаблон 12", names)

    def test_an_empty_list_is_none(self):
        self.assertIsNone(item_bot.choose(FakeLink(), "Что?", [], "выб:"))


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


class MuteMenuTest(unittest.TestCase):
    """⛔ Глушка: остановить выдачу и разобрать отложенные заказы.

    Нужна она ровно для одного: чтобы бот не купил код по заказу, который
    продавец уже закрыл руками. По сделке этого не видно — площадка держит
    её в «оплачено», пока покупатель не подтвердит.
    """

    MINE = "🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎"
    GAMEPASS = "🔴 80 РОБУКСОВ 🔴 АВТОВЫДАЧА ГЕЙМПАСС"
    ALIEN = "🏆 ПОПУЛЯРНЫЙ ПАКЕТ • 50.000.000₽ • 3 LVL"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")

    def hold(self, on=True, keyword="автовыдача", **rows):
        """Кладём заказы на паузу.

        Слово-опознаватель по умолчанию «автовыдача» — ровно как у
        продавца: в названии его объявления нет ни «robux», ни «роблокс»,
        и без своего слова бот не узнал бы даже свой товар.
        """
        conf = item_bot.settings_of()

        if on:
            conf.set_enabled("robux", True)
            conf.set_keyword("robux", keyword)

        held = conf.store.shared().setdefault("held", {})

        for order_id, title in rows.items():
            held[order_id] = {"title": title, "at": 0}

        conf.store.save()

        return conf

    def buttons(self, keys):
        return [name for row in keys for name, _ in row]

    def test_the_settings_screen_counts_what_is_on_hold(self):
        self.hold(o1=self.MINE, o2=self.MINE)
        link = FakeLink(["отмена"])
        item_bot.settings_menu(link)
        names = self.buttons(link.asked[0][1])

        self.assertTrue(any("2 заказа на паузе" in n for n in names), names)

    def test_the_held_orders_are_named(self):
        self.hold(o1=self.MINE)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("ПРОМОКОДОМ", link.asked[0][0])

    def test_our_order_is_marked_as_ours(self):
        self.hold(o1=self.MINE)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("✅", link.asked[0][0])
        self.assertIn("моих — 1", link.asked[0][0])

    def test_a_gamepass_order_is_not_ours(self):
        """Тот же робукс, но выдача через геймпасс — другой товар, и код
        подарочной карты по нему покупать нельзя."""
        self.hold(o1=self.GAMEPASS)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("➖", link.asked[0][0])
        self.assertIn("моих — 0", link.asked[0][0])

    def test_a_foreign_order_is_explained(self):
        self.hold(o1=self.ALIEN)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("не мой товар", link.asked[0][0])

    def test_a_keyword_that_does_not_match_is_explained(self):
        """Иначе продавец не поймёт, почему его же товар «не мой»."""
        self.hold(keyword="промокод", o1="🔴 80 РОБУКСОВ 🔴 АВТОВЫДАЧА")
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("промокод", link.asked[0][0])

    def test_the_word_promo_separates_the_two_ways_of_selling(self):
        """У продавца оба объявления про робуксы и оба «автовыдача».
        Отличает их только слово «промокодом» — по нему и делим."""
        self.hold(keyword="промокод", o1=self.MINE,
                  o2="🔴 80 РОБУКСОВ 🔴 💫 АВТОВЫДАЧА 💫")
        link = FakeLink(["глушка:выдать"])
        item_bot.mute_menu(link)

        self.assertEqual(list(item_bot.settings_of().held()), ["o2"])

    def test_a_stop_word_of_the_seller_wins_over_the_keyword(self):
        conf = self.hold(o1=self.MINE)
        conf.set_stop("robux", "промокод")
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("моих — 0", link.asked[0][0])

    def test_only_our_orders_are_released(self):
        """Чужие бот всё равно не выдаст, и держать их в очереди — значит
        заставлять продавца ждать выдачи, которой не будет."""
        conf = self.hold(o1=self.MINE, o2=self.GAMEPASS, o3=self.ALIEN)
        link = FakeLink(["глушка:выдать"])
        item_bot.mute_menu(link)
        left = item_bot.settings_of().held()

        self.assertEqual(sorted(left), ["o2", "o3"])
        self.assertIn("Снял с паузы: 1 заказ", link.said[-1])

    def test_the_rest_is_explained_not_silently_kept(self):
        self.hold(o1=self.MINE, o2=self.ALIEN)
        link = FakeLink(["глушка:выдать"])
        item_bot.mute_menu(link)

        self.assertIn("оставил на паузе", link.said[-1])

    def test_closing_them_means_never(self):
        """«Считать закрытыми» — это «код уже у покупателя»."""
        self.hold(o1=self.MINE)
        link = FakeLink(["глушка:закрыть"])
        item_bot.mute_menu(link)
        conf = item_bot.settings_of()

        self.assertEqual(conf.held(), {})
        self.assertTrue(any("o1" in (conf.store.conf(c.slug).get("closed") or [])
                            for c in item_bot.CARDS))

    def test_the_two_answers_are_not_the_same_button(self):
        """Цена ошибки в разные стороны разная — решает продавец."""
        self.hold(o1=self.MINE)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)
        names = self.buttons(link.asked[0][1])

        self.assertTrue(any("Выдать мои" in n for n in names), names)
        self.assertIn("🚫 Считать закрытыми", names)

    def test_every_order_has_its_own_button(self):
        """В списке разные товары, и решение по ним разное."""
        self.hold(o1=self.MINE, o2=self.ALIEN)
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)
        values = [value for row in link.asked[0][1] for _, value in row]

        self.assertIn(item_bot.PICK_HELD + "o1", values)
        self.assertIn(item_bot.PICK_HELD + "o2", values)

    def test_one_order_can_be_released_alone(self):
        self.hold(o1=self.MINE, o2=self.MINE)
        link = FakeLink([item_bot.PICK_HELD + "o1", "один:выдать"])
        item_bot.mute_menu(link)

        self.assertEqual(list(item_bot.settings_of().held()), ["o2"])

    def test_one_order_can_be_closed_alone(self):
        self.hold(o1=self.MINE, o2=self.MINE)
        link = FakeLink([item_bot.PICK_HELD + "o2", "один:закрыть"])
        item_bot.mute_menu(link)
        conf = item_bot.settings_of()

        self.assertEqual(list(conf.held()), ["o1"])
        self.assertIn("o2", conf.store.conf("robux").get("closed") or [])

    def test_the_single_order_screen_shows_the_verdict(self):
        self.hold(o1=self.GAMEPASS)
        link = FakeLink([item_bot.PICK_HELD + "o1", "отмена"])
        item_bot.mute_menu(link)

        # Второй экран — про один заказ; «Назад» с него снова открывает
        # список, поэтому смотрим именно на второй, а не на последний.
        self.assertIn("Заказ o1", link.asked[1][0])
        self.assertIn("не мой", link.asked[1][0])

    def test_a_vanished_order_is_said_plainly(self):
        self.hold(o1=self.MINE)
        link = FakeLink([item_bot.PICK_HELD + "нет-такого"])
        item_bot.mute_menu(link)

        self.assertIn("уже нет", link.said[-1])

    def test_the_whole_delivery_can_be_stopped(self):
        link = FakeLink(["глушка:выкл"])
        item_bot.mute_menu(link)

        self.assertTrue(item_bot.settings_of().paused())

    def test_and_started_again(self):
        conf = item_bot.settings_of()
        conf.set_paused(True)
        link = FakeLink(["глушка:вкл"])
        item_bot.mute_menu(link)

        self.assertFalse(item_bot.settings_of().paused())

    def test_a_stopped_delivery_is_visible_from_the_settings_screen(self):
        item_bot.settings_of().set_paused(True)
        link = FakeLink(["отмена"])
        item_bot.settings_menu(link)
        names = self.buttons(link.asked[0][1])

        self.assertTrue(any("выдача остановлена" in n.lower() for n in names),
                        names)

    def test_nothing_on_hold_is_said_plainly(self):
        link = FakeLink(["отмена"])
        item_bot.mute_menu(link)

        self.assertIn("Отложенных заказов нет", link.asked[0][0])

    def test_the_screen_is_reachable_from_the_settings(self):
        link = FakeLink(["глушка", "отмена"])
        item_bot.settings_menu(link)

        self.assertIn("Глушка", link.asked[-1][0])


class RegionNoteTest(unittest.TestCase):
    """Список регионов: что показано, что означает и как дойти до
    остальных.

    Продавец видел дюжину кнопок и решал, что других регионов бот не
    умеет. Умеет он все шестьдесят — часть на следующих страницах.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.saved = item_bot._CATALOG.copy()
        item_bot._CATALOG.update({"raw": None, "at": 0})
        # Без ключа каталог даже не запрашивается — в тестах сети нет.
        self.key = os.environ.pop("APPROUTE_KEY", None)

    def tearDown(self):
        item_bot._CATALOG.update(self.saved)

        if self.key is not None:
            os.environ["APPROUTE_KEY"] = self.key

    def stock(self, *regions):
        """Каталог поставщика в кеше: сети это не требует."""
        os.environ["APPROUTE_KEY"] = "ключ"
        item_bot._CATALOG.update({
            "raw": {"services": [
                {"id": f"s{n}", "name": f"Apple Gift Cards {code}",
                 "subcategoryName": "Apple Gift Cards",
                 "items": [{"id": f"d{n}", "name": "10 USD", "inStock": 5}]}
                for n, code in enumerate(regions)]},
            "at": time.time()})

    def note(self, name="Apple Gift Card 10$", page=0):
        return item_bot.region_note(draft(name=name), page)

    def buttons(self, page=0, name="Apple Gift Card 10$"):
        keys = item_bot.region_buttons(draft(name=name), page)

        return [label for row in keys for label, _ in row]

    def test_any_region_can_be_typed(self):
        self.assertIn("вписать код", self.note().lower())

    def test_every_region_is_reachable(self):
        """Шестьдесят кодов не влезают на экран — значит листаем."""
        codes = [code for code, _ in item_bot.region_choices(draft())]

        self.assertEqual(sorted(codes), sorted(wizard.REGIONS))

    def test_the_supplier_regions_go_first(self):
        self.stock("TR", "SA")
        codes = [code for code, ok in item_bot.region_choices(
            draft(name="Apple Gift Card 10$")) if ok]

        self.assertEqual(codes, ["SA", "TR"])

    def test_they_are_marked_on_the_buttons(self):
        self.stock("TR")

        self.assertIn("✅ TR", self.buttons())

    def test_the_mark_is_explained(self):
        self.stock("TR")

        self.assertIn("есть у поставщика", self.note())

    def test_there_is_a_next_button(self):
        self.assertTrue(any("Далее" in name for name in self.buttons()),
                        self.buttons())

    def test_the_first_page_has_no_back_button(self):
        self.assertFalse(any("Назад" in name for name in self.buttons()))

    def test_the_second_page_has_both(self):
        names = self.buttons(page=1)

        self.assertTrue(any("Назад" in n for n in names), names)
        self.assertTrue(any("Далее" in n for n in names), names)

    def test_the_last_page_has_no_next(self):
        last = (len(wizard.REGIONS) - 1) // item_bot.REGIONS_PAGE
        names = self.buttons(page=last)

        self.assertFalse(any("Далее" in n for n in names), names)

    def test_the_pages_are_counted_in_the_note(self):
        self.assertIn("Страница 1 из", self.note())

    def test_paging_does_not_lose_regions(self):
        seen = []
        page = 0

        while True:
            keys = item_bot.region_buttons(draft(), page)
            seen += [value for row in keys for _, value in row
                     if value in wizard.REGIONS]

            if not any("Далее" in label for row in keys for label, _ in row):
                break

            page += 1

        self.assertEqual(sorted(set(seen)), sorted(wizard.REGIONS))

    def test_paging_is_not_an_answer(self):
        """«Далее» принималось бы за выбор региона, и мастер пошёл бы
        дальше со строкой «рег:1» вместо страны."""
        link = FakeLink([item_bot.PICK_REGION + "1",
                         item_bot.PICK_REGION + "2", "TR"])
        answer = item_bot.ask_region(link, "Новый регион:")

        self.assertEqual(answer.get("text"), "TR")
        self.assertEqual(len(link.asked), 3)

    def test_the_page_moves_with_the_button(self):
        link = FakeLink([item_bot.PICK_REGION + "1", "TR"])
        item_bot.ask_region(link, "Новый регион:")

        self.assertIn("Страница 2 из", link.asked[1][0])

    def test_an_unreadable_catalog_is_explained(self):
        """Иначе кнопки выглядят как весь список умений бота."""
        self.assertIn("не прочитался", self.note())


class RegionInTheWizardTest(unittest.TestCase):
    """Листание страниц внутри самого опроса."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.key = os.environ.pop("APPROUTE_KEY", None)
        self.saved = item_bot._CATALOG.copy()
        item_bot._CATALOG.update({"raw": None, "at": 0})

    def tearDown(self):
        item_bot._CATALOG.update(self.saved)

        if self.key is not None:
            os.environ["APPROUTE_KEY"] = self.key

    def ready(self):
        """Черновик, которому осталось спросить только регион."""
        d = wizard.Draft()
        d.game, d.category, d.obtaining = GAME, CATEGORY, OBTAINING
        d.name, d.price = "Apple Gift Card 10$", 900
        d.description = "текст"
        d.nominal = 10
        d.photos = [PNG]
        d.fields = []

        return d

    def test_the_wizard_asks_for_the_region(self):
        draft_ = self.ready()
        link = FakeLink(["отмена"])
        item_bot.collect(link, None, draft_)

        self.assertIn("Регион", link.asked[0][0])

    def test_paging_keeps_the_question(self):
        """Нажатие «Далее» не должно стать ответом на вопрос о регионе."""
        draft_ = self.ready()
        link = FakeLink([item_bot.PICK_REGION + "1", "TR"])
        item_bot.collect(link, None, draft_)

        self.assertEqual(draft_.region, "TR")
        self.assertEqual(len(link.asked), 2)

    def test_the_second_page_is_shown(self):
        draft_ = self.ready()
        link = FakeLink([item_bot.PICK_REGION + "1", "TR"])
        item_bot.collect(link, None, draft_)

        self.assertIn("Страница 2 из", link.asked[1][0])


class HealthMenuTest(unittest.TestCase):
    """🩺 Проверка выдачи прямо в телеграме.

    Всё то же умеет doctor.py, но он в терминале, а продавец работает с
    телефона. Диагностика, до которой не дотянуться, — не диагностика:
    покупка висела без кода сутками ровно поэтому.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.key = os.environ.pop("APPROUTE_KEY", None)
        self.saved = item_bot._CATALOG.copy()
        item_bot._CATALOG.update({"raw": None, "at": 0})
        self.live = item_bot.alive.delivery_running
        item_bot.alive.delivery_running = lambda: True

    def tearDown(self):
        item_bot.alive.delivery_running = self.live
        item_bot._CATALOG.update(self.saved)

        if self.key is not None:
            os.environ["APPROUTE_KEY"] = self.key

    def report(self):
        link = FakeLink([])
        item_bot.health_menu(link, None)

        return link.said[-1]

    def test_a_dead_delivery_is_the_first_thing_said(self):
        """При мёртвом процессе остальное неважно: кода не будет."""
        item_bot.alive.delivery_running = lambda: False
        said = self.report()

        self.assertIn("НЕ запущена", said)
        self.assertIn("run_bot.sh", said)

    def test_a_live_delivery_is_said_too(self):
        self.assertIn("✅ Автовыдача запущена", self.report())

    def test_an_uncheckable_process_is_not_called_dead(self):
        """На урезанной системе pgrep может не быть вовсе."""
        item_bot.alive.delivery_running = lambda: None
        said = self.report()

        self.assertIn("проверить не вышло", said)
        self.assertNotIn("НЕ запущена", said)

    def test_the_mute_is_named(self):
        item_bot.settings_of().set_paused(True)

        self.assertIn("Глушка включена", self.report())

    def test_held_orders_are_named(self):
        conf = item_bot.settings_of()
        conf.store.shared().setdefault("held", {})["o1"] = {"title": "т",
                                                            "at": 0}
        conf.store.save()

        self.assertIn("На паузе заказов: 1", self.report())

    def test_a_missing_supplier_key_is_named(self):
        self.assertIn("Ключа поставщика нет", self.report())

    def test_no_enabled_card_is_named(self):
        self.assertIn("Не включена ни одна карта", self.report())

    def test_the_keyword_is_shown(self):
        """По нему бот и решает, его ли это заказ, — а ошибка в нём
        выглядит как «выдача не работает»."""
        conf = item_bot.settings_of()
        conf.set_enabled("robux", True)
        conf.set_keyword("robux", "промокод")
        said = self.report()

        self.assertIn("промокод", said)

    def test_the_stop_word_is_shown_too(self):
        conf = item_bot.settings_of()
        conf.set_enabled("robux", True)
        conf.set_stop("robux", "геймпасс")

        self.assertIn("геймпасс", self.report())

    def test_an_empty_journal_points_at_the_keyword(self):
        self.assertIn("не признал их своими", self.report())

    def test_the_last_deliveries_are_listed(self):
        conf = item_bot.settings_of()
        conf.store.conf("robux").setdefault("log", []).append(
            {"order": "1f1b2ad0-5c99", "state": "выдан", "at": 100})
        conf.store.save()
        said = self.report()

        self.assertIn("1f1b2ad0", said)
        self.assertIn("выдан", said)

    def test_a_refusal_shows_its_reason(self):
        conf = item_bot.settings_of()
        conf.store.conf("robux").setdefault("log", []).append(
            {"order": "77", "state": "отказ", "at": 100,
             "why": "номинал не найден"})
        conf.store.save()

        self.assertIn("номинал не найден", self.report())

    def with_balance(self, money, why=""):
        """Отчёт с подставленным балансом и ключом поставщика."""
        saved = item_bot.supplier_balance
        catalog = item_bot._CATALOG.copy()
        os.environ["APPROUTE_KEY"] = "ключ"
        item_bot.supplier_balance = lambda: (money, why)
        item_bot._CATALOG.update({"raw": {"services": []},
                                  "at": time.time()})

        try:
            return self.report()
        finally:
            item_bot.supplier_balance = saved
            item_bot._CATALOG.update(catalog)
            os.environ.pop("APPROUTE_KEY", None)

    def test_the_balance_is_shown(self):
        """Пустой счёт останавливает выдачу так же наглухо, как выключенная
        карта, и узнать об этом лучше до того, как покупатель заплатит."""
        self.assertIn("USD 0.42", self.with_balance("USD 0.42 · RUB 0"))

    def test_an_unreadable_balance_is_not_a_disaster(self):
        said = self.with_balance("", "связь оборвалась")

        self.assertIn("Баланс прочитать не вышло", said)

    def test_the_cure_is_collected_at_the_end(self):
        item_bot.alive.delivery_running = lambda: False

        self.assertIn("Что сделать:", self.report())

    def test_it_is_reachable_by_the_button(self):
        link = FakeLink(["проверка"])
        item_bot.handle_command(link, None, "проверка")

        self.assertIn("Проверка выдачи", link.said[-1])


class XboxGiftCardTest(unittest.TestCase):
    """Создание гифт-карты Xbox целиком — от вопроса до проверки выдачи.

    У Xbox всё то, на чём спотыкались по очереди: площадка спрашивает
    страну своим вопросом, номинал считается в валюте региона, а рядом в
    каталоге поставщика лежит Game Pass — другой товар с тем же словом.
    """

    CATALOG = {"services": [
        {"id": "xbox-tr", "name": "Xbox Gift Cards Turkey",
         "subcategoryName": "Xbox Gift Cards",
         "items": [{"id": "d-100", "value": 100, "inStock": 7, "price": 3.1},
                   {"id": "d-500", "value": 500, "inStock": 2, "price": 15.0}]},
        {"id": "xbox-us", "name": "Xbox Gift Cards US",
         "subcategoryName": "Xbox Gift Cards",
         "items": [{"id": "d-10", "value": 10, "inStock": 5, "price": 9.6}]},
        {"id": "pass-tr", "name": "Xbox Game Pass Ultimate Turkey",
         "subcategoryName": "Xbox Game Pass",
         "items": [{"id": "p-1", "value": 1, "inStock": 9, "price": 5.0}]},
    ]}

    # Так площадка спрашивает страну у Xbox: «Валюта?» с тремя десятками
    # стран, каждая со своей валютой в скобках.
    CURRENCY = {"field": "currency", "group": "Валюта", "value": None,
                "choices": [{"label": "Аргентина (ARS)", "value": "ARS"},
                            {"label": "США (USD)", "value": "USD"},
                            {"label": "Турция (TRY)", "value": "TRY"},
                            {"label": "Европа (EUR)", "value": "EUR"}]}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        self.key = os.environ.get("APPROUTE_KEY")
        os.environ["APPROUTE_KEY"] = "ключ"
        self.saved = item_bot._CATALOG.copy()
        item_bot._CATALOG.update({"raw": self.CATALOG, "at": time.time()})

    def tearDown(self):
        item_bot._CATALOG.update(self.saved)

        if self.key is None:
            os.environ.pop("APPROUTE_KEY", None)
        else:
            os.environ["APPROUTE_KEY"] = self.key

    def draft(self, name="Xbox Gift Card 100 TRY", region="TR", nominal=100,
              options=None):
        d = wizard.Draft()
        d.game = {"id": "g", "name": "Xbox"}
        d.category = {"id": "c", "name": "Пополнение"}
        d.obtaining = {"id": "o", "name": "Без входа в аккаунт"}
        d.name, d.price, d.region, d.nominal = name, 900, region, nominal
        d.description = ""
        d.options = [dict(o) for o in (options or [])]

        return d

    def on(self, keyword=""):
        conf = item_bot.settings_of()
        conf.set_enabled("xbox", True)

        if keyword:
            conf.set_keyword("xbox", keyword)

        return conf

    # ---------- характеристика площадки ----------

    def test_the_country_is_filled_from_the_region(self):
        """Продавец уже назвал регион — спрашивать то же во второй раз
        значит однажды получить два разных ответа."""
        d = self.draft(options=[self.CURRENCY])
        item_bot.fill_region_options(d)

        self.assertEqual(d.options[0]["value"], "TRY")
        self.assertEqual(d.options[0]["chosen"], "Турция (TRY)")

    def test_filling_it_removes_the_question(self):
        d = self.draft(options=[self.CURRENCY])
        item_bot.fill_region_options(d)

        self.assertFalse(d.step.startswith(wizard.OPTION))

    def test_another_region_gets_another_country(self):
        d = self.draft(region="US", options=[self.CURRENCY])
        item_bot.fill_region_options(d)

        self.assertEqual(d.options[0]["chosen"], "США (USD)")

    def test_an_unknown_region_is_still_asked(self):
        """Выдумывать за продавца нельзя: это его товар."""
        d = self.draft(region="KZ", options=[self.CURRENCY])
        item_bot.fill_region_options(d)

        self.assertIsNone(d.options[0]["value"])
        self.assertTrue(d.step.startswith(wizard.OPTION))

    def test_a_server_option_is_left_alone(self):
        """У аккаунтов «сервер» — не страна, и регион кода туда не годится."""
        d = self.draft(options=[{"field": "server", "group": "Сервер",
                                 "value": None,
                                 "choices": [{"label": "Турция",
                                              "value": "TR"}]}])
        item_bot.fill_region_options(d)

        self.assertIsNone(d.options[0]["value"])

    def test_an_answered_option_is_not_touched(self):
        option = dict(self.CURRENCY, value="ARS", chosen="Аргентина (ARS)")
        d = self.draft(options=[option])
        item_bot.fill_region_options(d)

        self.assertEqual(d.options[0]["value"], "ARS")

    # ---------- номинал в валюте региона ----------

    def test_the_nominal_is_counted_in_the_region_currency(self):
        """Xbox в Турции продаётся в лирах. Продавец, думающий в долларах,
        выставит номинал впятеро крупнее, чем собирался."""
        note = item_bot.nominal_note(self.draft())

        self.assertIn("TR", note)
        self.assertIn("₺", note)
        self.assertIn("не в рублях", note.lower())

    def test_robux_are_counted_in_robux(self):
        d = self.draft(name="Roblox 1000 Robux", region="GL")

        self.assertIn("Robux", item_bot.nominal_note(d))

    # ---------- проверка выдачи до публикации ----------

    def test_a_deliverable_card_is_confirmed(self):
        self.on()
        lines, trouble = item_bot.delivery_preview(self.draft())

        self.assertFalse(trouble)
        self.assertIn("Выдача сможет", lines[0])
        self.assertIn("7 шт", lines[0])

    def test_a_nominal_the_supplier_does_not_have_is_named(self):
        self.on()
        lines, trouble = item_bot.delivery_preview(
            self.draft(name="Xbox Gift Card 250 TRY", nominal=250))

        self.assertTrue(trouble)
        self.assertIn("нет", " ".join(lines))

    def test_a_region_the_supplier_does_not_have_is_named(self):
        self.on()
        lines, trouble = item_bot.delivery_preview(
            self.draft(name="Xbox Gift Card 100", region="BR", nominal=100))

        self.assertTrue(trouble)

    def test_a_disabled_card_is_named(self):
        lines, trouble = item_bot.delivery_preview(self.draft())

        self.assertIn("выключена", " ".join(lines))

    def test_a_keyword_that_misses_the_name_is_named(self):
        """Самая тихая беда: товар создан, выдача проходит мимо."""
        self.on(keyword="промокод")
        lines, trouble = item_bot.delivery_preview(self.draft())

        self.assertIn("пройдёт мимо", " ".join(lines))

    def test_an_unknown_card_is_named(self):
        lines, trouble = item_bot.delivery_preview(
            self.draft(name="Просто товар без слов"))

        self.assertTrue(trouble)
        self.assertIn("не узнаю карту", " ".join(lines))

    def test_a_missing_nominal_is_named(self):
        lines, trouble = item_bot.delivery_preview(self.draft(nominal=None))

        self.assertTrue(trouble)
        self.assertIn("Номинала нет", " ".join(lines))

    # ---------- Game Pass рядом в каталоге ----------

    def test_game_pass_is_not_taken_for_a_gift_card(self):
        """Он лежит в соседней подкатегории и стоит дешевле — перепутать
        значит продать подписку вместо карты."""
        self.on()
        lines, trouble = item_bot.delivery_preview(
            self.draft(name="Xbox Gift Card 1 TRY", nominal=1))

        self.assertTrue(trouble)

    def test_a_game_pass_listing_is_not_ours(self):
        from catalog import is_card_order

        card = item_bot.card_by_slug("xbox")

        self.assertFalse(is_card_order(card, "Xbox Game Pass Ultimate 1 мес"))
        self.assertTrue(is_card_order(card, "Xbox Gift Card 100 TRY"))

    # ---------- описание ----------

    def test_the_description_says_the_region_and_the_nominal(self):
        """Их читает выдача — без них она встанет на оплаченном заказе."""
        d = self.draft()
        item_bot.apply_card_template(d)
        text = wizard.description_for(d)

        self.assertIn("Регион кода: TR", text)
        self.assertIn("Номинал: 100", text)

    def test_the_description_warns_about_the_account_region(self):
        """Код TR на аккаунте US не активируется — это возврат."""
        d = self.draft()
        item_bot.apply_card_template(d)
        text = wizard.description_for(d)

        self.assertIn("xbox.com/redeem", text)
        self.assertIn("регион", text.lower())

    def test_the_description_is_not_about_roblox(self):
        d = self.draft()
        item_bot.apply_card_template(d)

        self.assertNotIn("roblox", wizard.description_for(d).lower())

    def test_what_we_wrote_the_delivery_reads_back(self):
        """Круг замыкается: что написали — то выдача и купит."""
        from catalog import nominal_for, region_from_description

        d = self.draft()
        item_bot.apply_card_template(d)
        text = wizard.description_for(d)
        value, why = nominal_for(d.name, text, d.price,
                                 item_bot.card_by_slug("xbox"))

        self.assertEqual(region_from_description(text), "TR")
        self.assertEqual(value, 100)
        self.assertEqual(why, "")


    def test_the_activation_comes_from_the_game_when_the_name_is_odd(self):
        """«🎮ПОПОЛНЕНИЕ КОШЕЛЬКА 100₺» — карта та же, активация та же."""
        d = self.draft(name="🎮ПОПОЛНЕНИЕ КОШЕЛЬКА 100₺")
        item_bot.apply_card_template(d)

        self.assertIn("xbox.com/redeem", wizard.description_for(d))

    def test_but_the_delivery_will_not_recognise_it(self):
        """И об этом продавцу говорят до создания, а не после продажи."""
        self.on()
        lines, trouble = item_bot.delivery_preview(
            self.draft(name="🎮ПОПОЛНЕНИЕ КОШЕЛЬКА 100₺"))

        self.assertTrue(trouble)
        self.assertIn("не узнаю карту", " ".join(lines))

    def test_the_name_hint_shows_an_example_in_the_region_currency(self):
        note = item_bot.name_note(self.draft(name=""))

        self.assertIn("Xbox Gift Card", note)
        self.assertIn("₺", note)
        self.assertIn("не цена в рублях", note)

    def test_the_robux_hint_is_not_about_currency(self):
        d = self.draft(name="", region="GL")
        d.game = {"id": "g", "name": "Roblox"}

        self.assertIn("номинал", item_bot.name_note(d).lower())

    # ---------- спор характеристики с регионом ----------

    def test_a_country_that_argues_with_the_region_is_named(self):
        """В объявлении «США», в описании «Регион кода: TR» — покупатель
        поверит объявлению, а бот купит по описанию."""
        option = dict(self.CURRENCY, value="USD", chosen="США (USD)")
        lines = item_bot.region_conflicts(self.draft(options=[option]))

        self.assertTrue(lines)
        self.assertIn("спорит с регионом", lines[0])

    def test_a_matching_country_is_not_a_conflict(self):
        option = dict(self.CURRENCY, value="TRY", chosen="Турция (TRY)")

        self.assertEqual(
            item_bot.region_conflicts(self.draft(options=[option])), [])

    def test_the_conflict_stops_the_quiet_creation(self):
        self.on()
        option = dict(self.CURRENCY, value="USD", chosen="США (USD)")
        _, trouble = item_bot.delivery_preview(self.draft(options=[option]))

        self.assertTrue(trouble)

    def test_an_option_that_is_not_about_region_never_argues(self):
        option = {"field": "server", "group": "Сервер", "value": "TR",
                  "chosen": "Турция", "choices": []}

        self.assertEqual(
            item_bot.region_conflicts(self.draft(options=[option])), [])

    # ---------- подтверждение перед созданием ----------

    def test_a_good_card_is_created_without_extra_questions(self):
        self.on()
        link = FakeLink([])
        account = FakeAccount()

        self.assertTrue(item_bot.confirm_and_create(link, account,
                                                    self.draft()))
        self.assertEqual(link.asked, [])
        self.assertEqual(len(account.created), 1)

    def test_a_bad_one_is_asked_about_first(self):
        self.on()
        link = FakeLink(["да"])
        account = FakeAccount()
        item_bot.confirm_and_create(link, account,
                                    self.draft(name="Xbox 250 TRY",
                                               nominal=250))

        self.assertIn("Создавать такой товар?", link.asked[0][0])
        self.assertEqual(len(account.created), 1)

    def test_a_refusal_creates_nothing(self):
        self.on()
        link = FakeLink(["отмена"])
        account = FakeAccount()

        self.assertFalse(item_bot.confirm_and_create(
            link, account, self.draft(name="Xbox 250 TRY", nominal=250)))
        self.assertEqual(account.created, [])



class AmountOptionTest(unittest.TestCase):
    """«Сумма пополнения»: характеристика, куда вписывают число.

    Площадка отдаёт её одной строкой — подпись равна названию самой
    характеристики, значения нет, зато есть разброс min/max. Бот показывал
    её кнопкой и отправлял пустое значение, а площадка отвечала «один или
    более атрибутов имеют некорректные значения» — не говоря какой.
    """

    def option(self, limit=None, group="Сумма пополнения", value=None,
               label=None):
        return {"field": "amount", "group": group, "value": None,
                "kind": "", "limit": limit,
                "choices": [{"label": label or group, "value": value}]}

    def draft(self, nominal=100, options=None):
        d = draft()
        d.nominal = nominal
        d.options = [dict(o) for o in (options or [self.option()])]

        return d

    # ---------- узнавание ----------

    def test_a_limit_means_a_number(self):
        self.assertTrue(item_bot.input_option(
            self.option(limit={"min": 1, "max": 1000})))

    def test_a_single_choice_without_a_value_means_a_number(self):
        self.assertTrue(item_bot.input_option(self.option()))

    def test_a_single_real_choice_is_still_a_choice(self):
        self.assertFalse(item_bot.input_option(self.option(value="any")))

    def test_a_list_is_never_a_number(self):
        option = self.option()
        option["choices"] = [{"label": "ПК", "value": "PC"},
                             {"label": "Телефон", "value": "MOBILE"}]

        self.assertFalse(item_bot.input_option(option))

    # ---------- автоподстановка номинала ----------

    def test_the_nominal_fills_it_without_asking(self):
        """Сумма пополнения гифт-карты — это и есть её номинал."""
        d = self.draft()
        item_bot.fill_amount_options(d)

        self.assertEqual(d.attributes(), {"amount": 100})
        self.assertFalse(d.step.startswith(wizard.OPTION))

    def test_it_is_sent_as_a_number_not_a_word(self):
        d = self.draft()
        item_bot.fill_amount_options(d)

        self.assertIsInstance(d.attributes()["amount"], int)

    def test_a_nominal_outside_the_limit_is_not_pushed_in(self):
        """Молча подставить запрещённое — значит получить отказ на
        создании и не понять, откуда он."""
        d = self.draft(options=[self.option(limit={"min": 10, "max": 50})])
        item_bot.fill_amount_options(d)

        self.assertEqual(d.attributes(), {})

    def test_an_option_that_is_not_about_amount_is_left_alone(self):
        option = dict(self.option(group="Ключ активации"), field="key")
        d = self.draft(options=[option])
        item_bot.fill_amount_options(d)

        self.assertEqual(d.attributes(), {})

    def test_without_a_nominal_nothing_is_filled(self):
        d = self.draft(nominal=None)
        item_bot.fill_amount_options(d)

        self.assertEqual(d.attributes(), {})

    # ---------- когда всё же спрашиваем ----------

    def test_a_typed_number_is_accepted(self):
        d = self.draft(options=[self.option(group="Ключ")])
        item_bot.choose_option(FakeLink(["250"]), None, d)

        self.assertEqual(d.attributes(), {"amount": 250})

    def test_the_limit_is_told_to_the_seller(self):
        d = self.draft(options=[self.option(group="Ключ",
                                            limit={"min": 5, "max": 50})])
        link = FakeLink(["10"])
        item_bot.choose_option(link, None, d)

        self.assertIn("от 5 до 50", link.asked[0][0])

    def test_a_value_outside_the_limit_is_refused(self):
        d = self.draft(options=[self.option(group="Ключ",
                                            limit={"min": 5, "max": 50})])
        link = FakeLink(["500", "10"])
        item_bot.choose_option(link, None, d)

        self.assertEqual(d.attributes(), {"amount": 10})
        self.assertIn("не подходит под разброс", link.asked[-1][0])

    def test_a_word_instead_of_a_number_is_refused(self):
        d = self.draft(options=[self.option(group="Ключ")])
        link = FakeLink(["много", "7"])
        item_bot.choose_option(link, None, d)

        self.assertEqual(d.attributes(), {"amount": 7})
        self.assertIn("не число", link.asked[-1][0])

    def test_the_nominal_is_offered_by_a_button(self):
        d = self.draft(options=[self.option(group="Ключ")])
        link = FakeLink([item_bot.PICK_OPTION + "ном"])
        item_bot.choose_option(link, None, d)

        self.assertEqual(d.attributes(), {"amount": 100})

    def test_a_comma_is_a_dot(self):
        d = self.draft(options=[self.option(group="Ключ")])
        item_bot.choose_option(FakeLink(["9,99"]), None, d)

        self.assertEqual(d.attributes(), {"amount": 9.99})

    def test_cancel_cancels(self):
        d = self.draft(options=[self.option(group="Ключ")])

        self.assertFalse(item_bot.choose_option(FakeLink(["отмена"]), None, d))


class AttributeRetryTest(unittest.TestCase):
    """Отказ «атрибуты имеют некорректные значения» — не приговор.

    Какого типа площадка ждёт число, из её ответа не видно. Создание
    ничего не тратит, поэтому бот пробует вторую форму сам, а не
    возвращает продавцу загадку, которую тот всё равно не починит.
    """

    class Picky:
        """Площадка, принимающая числа только строками."""

        def __init__(self, wants=str):
            self.wants = wants
            self.tries = []

        def create_item(self, **kw):
            self.tries.append(kw["options"])

            for value in kw["options"].values():
                if not isinstance(value, self.wants):
                    raise RuntimeError("Один или более атрибутов товара "
                                       "имеют некорректные значения")

            return FakeItem()

        def get_item_priority_statuses(self, item_id, price):
            return []

    def draft(self, value):
        d = draft()
        d.options = [{"field": "amount", "group": "Сумма", "value": value,
                      "chosen": "100", "choices": []}]

        return d

    def test_a_number_is_retried_as_a_word(self):
        market = self.Picky(wants=str)
        item_id, why = item_bot.create_item(market, self.draft(100))

        self.assertEqual(item_id, "item-1")
        self.assertEqual(market.tries, [{"amount": 100}, {"amount": "100"}])

    def test_a_word_is_retried_as_a_number(self):
        market = self.Picky(wants=int)
        item_id, why = item_bot.create_item(market, self.draft("100"))

        self.assertEqual(item_id, "item-1")
        self.assertEqual(market.tries[-1], {"amount": 100})

    def test_the_first_reason_is_the_one_told(self):
        """Вторая попытка была нашей догадкой — жаловаться на неё
        продавцу незачем."""
        class Never(self.Picky):
            def create_item(self, **kw):
                self.tries.append(kw["options"])

                raise RuntimeError("Один или более атрибутов товара имеют "
                                   "некорректные значения")

        market = Never()
        item_id, why = item_bot.create_item(market, self.draft(100))

        self.assertEqual(item_id, "")
        self.assertIn("некорректные значения", why)
        self.assertEqual(len(market.tries), 2)

    def test_other_refusals_are_not_retried(self):
        """Повторять отказ во входе бессмысленно — он сам не пройдёт."""
        class Closed:
            def __init__(self):
                self.tries = []

            def create_item(self, **kw):
                self.tries.append(kw["options"])

                raise RuntimeError("Не удалось подключиться к аккаунту")

        market = Closed()
        item_bot.create_item(market, self.draft(100))

        self.assertEqual(len(market.tries), 1)

    def test_nothing_to_flip_is_not_retried(self):
        class Closed:
            def __init__(self):
                self.tries = []

            def create_item(self, **kw):
                self.tries.append(kw["options"])

                raise RuntimeError("атрибуты некорректны")

        market = Closed()
        d = draft()
        d.options = [{"field": "region", "value": "Global", "chosen": "Global",
                      "choices": []}]
        item_bot.create_item(market, d)

        self.assertEqual(len(market.tries), 1)

    def test_the_seller_sees_what_was_sent(self):
        """«Некорректные значения» без списка — загадка без ответа."""
        said = item_bot.what_we_sent(
            self.draft(100), "Один или более атрибутов имеют некорректные "
                             "значения")

        self.assertIn("Сумма", said)
        self.assertIn("100", said)

    def test_other_refusals_say_nothing_extra(self):
        self.assertEqual(item_bot.what_we_sent(self.draft(100),
                                               "куки истекли"), "")


class TuneCardTest(unittest.TestCase):
    """🚀 «Настроить выдачу»: включить карту по тому, что уже на витрине.

    Три настройки решают, будет ли выдача работать, и две из них продавцу
    не видны. Обе уже стоили молчаливой выдачи: сначала слова не было
    вовсе, потом оно совпало с чужим товаром.
    """

    CATALOG = {"services": [
        {"id": "xbox-tr", "name": "Xbox Gift Cards Turkey",
         "subcategoryName": "Xbox Gift Cards",
         "items": [{"id": "d1", "value": 100, "inStock": 5, "price": 3.0}]},
        {"id": "xbox-us", "name": "Xbox Gift Cards US",
         "subcategoryName": "Xbox Gift Cards",
         "items": [{"id": "d2", "value": 10, "inStock": 5, "price": 9.6}]},
    ]}

    NAMES = ["Xbox Gift Card 100 TR — промокодом",
             "Xbox Gift Card 500 TR — промокодом",
             "🔴 80 РОБУКСОВ 🔴 АВТОВЫДАЧА",
             "🏆 ПОПУЛЯРНЫЙ ПАКЕТ • 3 LVL"]

    class Market:
        def __init__(self, names):
            self.names = names

        def get_my_items(self, count=24, after_cursor=None, **kw):
            rows = [type("I", (), {"id": f"i{n}", "name": name,
                                   "price": 100 + n})()
                    for n, name in enumerate(self.names)]

            return type("P", (), {
                "items": rows,
                "page_info": type("PI", (), {"has_next_page": False,
                                             "end_cursor": "0"})()})()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.forget_items()
        self.key = os.environ.get("APPROUTE_KEY")
        os.environ["APPROUTE_KEY"] = "ключ"
        self.saved = item_bot._CATALOG.copy()
        item_bot._CATALOG.update({"raw": self.CATALOG, "at": time.time()})
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot._CATALOG.update(self.saved)
        item_bot.forget_items()

        if self.key is None:
            os.environ.pop("APPROUTE_KEY", None)
        else:
            os.environ["APPROUTE_KEY"] = self.key

    def tune(self, answers, names=None, slug="xbox"):
        link = FakeLink(list(answers))
        conf = item_bot.settings_of()
        item_bot.tune_card(link, self.Market(names or self.NAMES), conf,
                           item_bot.card_by_slug(slug))

        return link, item_bot.settings_of()

    def buttons(self, link, which=0):
        return [name for row in link.asked[which][1] for name, _ in row]

    def test_the_supplier_regions_are_shown(self):
        link, _ = self.tune(["отмена"])

        self.assertIn("TR", link.asked[0][0])
        self.assertIn("US", link.asked[0][0])

    def test_our_listings_are_counted(self):
        link, _ = self.tune(["отмена"])

        self.assertIn("Похожих на Xbox: 2", link.asked[0][0])

    def test_a_word_is_offered(self):
        link, _ = self.tune(["отмена"])

        self.assertTrue(any("xbox" in n.lower() for n in self.buttons(link)),
                        self.buttons(link))

    def test_the_word_is_not_one_of_the_others(self):
        """«Автовыдача» есть у геймпасса — по ней бот забрал бы чужое."""
        link, _ = self.tune(["отмена"])

        self.assertNotIn("автовыдача", link.asked[0][0].lower())

    def test_pressing_it_turns_the_delivery_on(self):
        link, conf = self.tune([item_bot.PICK_TUNE + "w:xbox"])

        self.assertTrue(conf.card("xbox")["enabled"])
        self.assertEqual(conf.card("xbox")["keyword"], "xbox")

    def test_the_region_is_filled_from_the_listings(self):
        link, conf = self.tune([item_bot.PICK_TUNE + "w:xbox"])

        self.assertEqual(conf.card("xbox")["region"], "TR")

    def test_it_says_how_many_listings_are_covered(self):
        link, _ = self.tune([item_bot.PICK_TUNE + "w:xbox"])

        self.assertIn("объявлений: 2", link.said[-1])

    def test_a_dangerous_word_is_shown_with_its_catch(self):
        """Продавец должен увидеть чужой товар до первой продажи, а не
        после списания."""
        link, _ = self.tune([item_bot.PICK_TUNE + "w:автовыдача"])

        self.assertIn("не похожие на Xbox", link.said[-1])

    def test_a_word_can_be_typed(self):
        link, conf = self.tune([item_bot.PICK_TUNE + "сам", "промокодом"])

        self.assertEqual(conf.card("xbox")["keyword"], "промокодом")

    def test_nothing_is_saved_on_cancel(self):
        link, conf = self.tune([item_bot.PICK_TUNE + "сам", "отмена"])

        self.assertFalse(conf.card("xbox")["enabled"])

    def test_an_empty_supplier_is_named(self):
        item_bot._CATALOG.update({"raw": {"services": []},
                                  "at": time.time()})
        link, _ = self.tune(["отмена"])

        self.assertIn("У поставщика ничего не нашлось", link.asked[0][0])

    def test_no_listings_still_offers_the_cards_own_word(self):
        link, _ = self.tune(["отмена"], names=["🏆 ПАКЕТ • 3 LVL"])

        self.assertTrue(any("xbox" in n.lower() for n in self.buttons(link)),
                        self.buttons(link))

    def test_apple_is_tuned_the_same_way(self):
        names = ["Apple Gift Card 10 USD — моментально",
                 "Apple Gift Card 25 USD — моментально",
                 "🔴 80 РОБУКСОВ 🔴 АВТОВЫДАЧА"]
        link, conf = self.tune([item_bot.PICK_TUNE + "w:apple"], names,
                               slug="apple")

        self.assertTrue(conf.card("apple")["enabled"])
        self.assertEqual(conf.card("apple")["keyword"], "apple")

    def test_without_an_account_it_says_so(self):
        link = FakeLink([])
        item_bot.tune_card(link, None, item_bot.settings_of(),
                           item_bot.card_by_slug("xbox"))

        self.assertIn("кабинет", link.said[-1])

    def test_the_button_is_on_the_card_screen(self):
        link = FakeLink([item_bot.PICK_CARD + "xbox", "отмена"])
        item_bot.settings_menu(link, self.Market(self.NAMES))
        names = [n for row in link.asked[-1][1] for n, _ in row]

        self.assertIn("🚀 Настроить выдачу", names)


class StatsMenuTest(unittest.TestCase):
    """📊 Статистика: сводка и разделы за кнопками.

    Отчёт о деньгах врать не имеет права: по нему продавец решает, что
    закупать и почём продавать. А всё сразу на одном экране не читается —
    поэтому сводка короткая, а подробности за кнопками.
    """

    class Balance:
        value, frozen, available = 18400, 0, 18400
        withdrawable, pending_income = 12300, 6100

    class Account:
        DEALS = [("Xbox Gift Card 100 TR", "CONFIRMED", 900),
                 ("Xbox Gift Card 500 TR", "PAID", 3000),
                 ("🏆 ПОПУЛЯРНЫЙ ПАКЕТ • 3 LVL", "CONFIRMED", 2500),
                 ("Xbox Gift Card 100 TR", "ROLLED_BACK", 900)]

        def __init__(self, balance=None, reviews=None, deals=None):
            self.profile = type("P", (), {"balance": balance})()
            self.reviews = reviews
            self.deals = self.DEALS if deals is None else deals

        def get(self):
            return self.profile

        def get_deals(self, direction=None, count=24, after_cursor=None):
            rows = [type("D", (), {
                "item": type("I", (), {"name": name})(),
                "status": type("S", (), {"name": status})(),
                "transaction": type("T", (), {"value": amount})()})()
                for name, status, amount in self.deals]

            return type("P", (), {"deals": rows, "has_next_page": False,
                                  "end_cursor": None})()

        def get_my_reviews(self, count=24, **kw):
            if self.reviews is None:
                raise RuntimeError("площадка молчит")

            return type("P", (), {"reviews": self.reviews,
                                  "total_count": len(self.reviews)})()

    class Review:
        def __init__(self, rating, text="", who="покупатель"):
            self.rating, self.text = rating, text
            self.creator = type("U", (), {"username": who})()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.forget_deals()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_deals()

    def account(self, reviews=None, deals=None, balance=True):
        return self.Account(self.Balance() if balance else None, reviews,
                            deals)

    def sold(self, rate=95, rows=((900, 3.1), (1200, 4.0))):
        conf = item_bot.settings_of()
        conf.set_rate(rate)
        log = conf.store.conf("xbox").setdefault("log", [])
        now = time.time()

        for number, (paid, price) in enumerate(rows):
            log.append({"order": f"o{number}", "state": "выдан", "paid": paid,
                        "price": price, "currency": "USD", "done_at": now,
                        "at": now})

        conf.store.save()

        return conf

    def screen(self, answers, account=None, start="stats_menu"):
        link = FakeLink(list(answers))
        getattr(item_bot, start)(link, account or self.account())

        return link

    def asked(self, answers, **kw):
        """Текст ПОСЛЕДНЕГО показанного экрана."""
        link = self.screen(answers, **kw)

        return link.asked[-1][0]

    def buttons(self, link, which=-1):
        return [name for row in link.asked[which][1] for name, _ in row]

    # ---------- сводка ----------

    def test_the_summary_is_short(self):
        """Всё сразу не читается: на первом экране — ответы одной строкой."""
        said = self.asked(["отмена"])

        self.assertLess(len(said.splitlines()), 12, said)

    def test_what_can_be_withdrawn_is_first(self):
        said = self.asked(["отмена"])

        self.assertIn("Можно вывести: 12 300 ₽", said)

    def test_the_sections_are_buttons(self):
        link = self.screen(["отмена"])
        names = self.buttons(link, 0)

        for want in ("💸 Что вывести", "💰 Деньги", "🧾 Продажи по категориям",
                     "💹 Профит", "⭐ Отзывы"):
            self.assertIn(want, names)

    def test_a_missing_balance_is_said_plainly(self):
        said = self.asked(["отмена"], account=self.account(balance=False))

        self.assertIn("Баланс площадка не отдала", said)

    def test_refreshing_re_reads_the_deals(self):
        """Иначе кнопки листались бы по вчерашним числам."""
        account = self.account()
        link = FakeLink([item_bot.PICK_STAT + "fresh", "отмена"])
        item_bot.stats_menu(link, account)

        self.assertIn("📊 Статистика", link.asked[-1][0])

    # ---------- деньги ----------

    def test_money_screen_explains_every_number(self):
        said = self.asked([item_bot.PICK_STAT + "money", "отмена"])

        self.assertIn("Можно вывести сейчас", said)
        self.assertIn("эти деньги уже ваши", said)
        self.assertIn("покупатели ещё не подтвердили", said)

    def test_money_screen_splits_released_and_waiting(self):
        said = self.asked([item_bot.PICK_STAT + "money", "отмена"])

        self.assertIn("подтверждено: 3 400 ₽", said)
        self.assertIn("ждёт подтверждения: 3 000 ₽", said)

    def test_money_screen_warns_that_waiting_is_not_yours(self):
        said = self.asked([item_bot.PICK_STAT + "money", "отмена"])

        self.assertIn("ещё не ваши деньги", said)

    # ---------- продажи по категориям ----------

    def test_sales_are_split_by_category(self):
        said = self.asked([item_bot.PICK_STAT + "sales", "отмена"])

        self.assertIn("Xbox", said)
        self.assertIn("3 900 ₽", said)

    def test_other_goods_are_a_category_too(self):
        """Это тоже деньги продавца, просто не наши карты."""
        said = self.asked([item_bot.PICK_STAT + "sales", "отмена"])

        self.assertIn("ПОПУЛЯРНЫЙ ПАКЕТ", said)
        self.assertIn("2 500 ₽", said)

    def test_every_category_is_a_button(self):
        link = self.screen([item_bot.PICK_STAT + "sales", "отмена"])
        values = [value for row in link.asked[-1][1] for _, value in row]

        self.assertTrue(any(v.startswith(item_bot.PICK_STAT + "g")
                            for v in values), values)

    def test_the_button_values_fit_the_telegram_limit(self):
        """У Telegram на значение кнопки 64 БАЙТА, а не знака. Длинное
        значение отвергает всю клавиатуру — со стороны это выглядит как
        «кнопки не нажимаются»."""
        link = self.screen([item_bot.PICK_STAT + "sales", "отмена"])

        for question, keys in link.asked:
            for row in keys or []:
                for _, value in row:
                    self.assertLessEqual(len(str(value).encode()), 64,
                                         value)

    def test_the_share_of_each_category_is_shown(self):
        said = self.asked([item_bot.PICK_STAT + "sales", "отмена"])

        self.assertIn("%", said)

    def test_a_category_opens_its_own_screen(self):
        said = self.asked([item_bot.PICK_STAT + "sales",
                           item_bot.PICK_STAT + "g0", "отмена"])

        self.assertIn("Можно забрать: 900 ₽", said)
        self.assertIn("Ждёт подтверждения: 3 000 ₽", said)

    def test_a_category_shows_what_was_selling(self):
        said = self.asked([item_bot.PICK_STAT + "sales",
                           item_bot.PICK_STAT + "g0", "отмена"])

        self.assertIn("Что продавалось", said)
        self.assertIn("Xbox Gift Card 100 TR", said)

    def test_a_category_shows_refunds(self):
        said = self.asked([item_bot.PICK_STAT + "sales",
                           item_bot.PICK_STAT + "g0", "отмена"])

        self.assertIn("Возвраты: 900 ₽", said)

    def test_a_card_category_shows_the_profit(self):
        self.sold()
        said = self.asked([item_bot.PICK_STAT + "sales",
                           item_bot.PICK_STAT + "g0", "отмена"])

        self.assertIn("Закупка: 7.1 $", said)
        self.assertIn("Профит 1 426 ₽", said)

    def test_a_foreign_category_promises_no_profit(self):
        """Закупку чужого товара бот не знает и выдумывать не станет."""
        said = self.asked([item_bot.PICK_STAT + "sales",
                           item_bot.PICK_STAT + "g1", "отмена"])

        self.assertIn("ПОПУЛЯРНЫЙ ПАКЕТ", said)
        self.assertNotIn("Профит", said)

    def test_a_vanished_category_is_said_plainly(self):
        said = self.screen([item_bot.PICK_STAT + "sales",
                            item_bot.PICK_STAT + "g99"]).said[-1]

        self.assertIn("нет", said)

    def test_unreadable_deals_do_not_break_the_report(self):
        class Broken(self.Account):
            def get_deals(self, **kw):
                raise RuntimeError("слишком много попыток")

        said = self.asked(["отмена"], account=Broken(self.Balance()))

        self.assertIn("Сделки прочитать не вышло", said)
        self.assertIn("Можно вывести", said)

    # ---------- что можно вывести ----------

    def test_the_withdrawable_is_split_by_category(self):
        said = self.asked([item_bot.PICK_STAT + "withdraw", "отмена"])

        self.assertIn("Всего можно вывести: 12 300 ₽", said)
        self.assertIn("Xbox", said)
        self.assertIn("≈", said)

    def test_the_split_is_called_an_estimate(self):
        """Площадка держит деньги общей кучей и по товарам их не делит."""
        said = self.asked([item_bot.PICK_STAT + "withdraw", "отмена"])

        self.assertIn("прикидка", said)
        self.assertIn("общей кучей", said)

    def test_the_waiting_money_is_split_too(self):
        said = self.asked([item_bot.PICK_STAT + "withdraw", "отмена"])

        self.assertIn("Ждёт подтверждения покупателями", said)
        self.assertIn("3 000 ₽", said)

    def test_the_confirmed_sum_is_shown_beside_the_share(self):
        said = self.asked([item_bot.PICK_STAT + "withdraw", "отмена"])

        self.assertIn("подтверждено 900 ₽", said)

    def test_the_category_list_shows_what_can_be_taken(self):
        said = self.asked([item_bot.PICK_STAT + "sales", "отмена"])

        self.assertIn("можно забрать 900 ₽", said)
        self.assertIn("ждёт 3 000 ₽", said)

    def test_without_a_balance_it_says_so(self):
        said = self.asked([item_bot.PICK_STAT + "withdraw", "отмена"],
                          account=self.account(balance=False))

        self.assertIn("не сказала", said)

    def test_the_money_screen_leads_here(self):
        said = self.asked([item_bot.PICK_STAT + "money",
                           item_bot.PICK_STAT + "withdraw", "отмена"])

        self.assertIn("Что можно вывести", said)

    # ---------- профит ----------

    def test_profit_is_counted_by_periods(self):
        self.sold()
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("сегодня:", said)
        self.assertIn("всего:", said)
        self.assertIn("профит 1 426 ₽", said)

    def test_profit_is_counted_by_card(self):
        self.sold()
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("Xbox", said)
        self.assertIn("чек", said)

    def test_without_a_rate_the_profit_is_not_invented(self):
        """Сложить рубли с долларами без курса нельзя."""
        self.sold(rate=0)
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("Курс доллара не задан", said)
        self.assertNotIn("профит 1", said)

    def test_the_cost_is_always_shown(self):
        self.sold()
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("закупка 7.1 $", said)

    def test_nothing_delivered_is_said_plainly(self):
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("не выдал ни одного кода", said)

    def test_entries_without_a_sum_are_named(self):
        """Ноль в отчёте о деньгах читается как факт."""
        conf = self.sold()
        conf.store.conf("xbox")["log"].append(
            {"order": "old", "state": "выдан", "price": 3.0,
             "currency": "USD", "at": time.time()})
        conf.store.save()
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertIn("сумма сделки не записана", said)

    def test_old_deliveries_do_not_show_a_loss(self):
        """Живой случай: у 11 выдач суммы нет, закупка есть — и отчёт
        показывал «профит −2 131 ₽»."""
        conf = item_bot.settings_of()
        conf.set_rate(84)
        log = conf.store.conf("robux").setdefault("log", [])

        for number in range(11):
            log.append({"order": f"old{number}", "state": "выдан",
                        "price": 2.3, "currency": "USD", "at": time.time(),
                        "done_at": time.time()})

        conf.store.save()
        said = self.asked([item_bot.PICK_STAT + "profit", "отмена"])

        self.assertNotIn("-", said)
        self.assertIn("не из чего считать", said)
        self.assertIn("сумма не записана", said)

    def test_the_missing_sums_can_be_fetched(self):
        """Площадка помнит сделки — дочитать дешевле, чем вычеркнуть."""
        conf = item_bot.settings_of()
        log = conf.store.conf("robux").setdefault("log", [])
        log.append({"order": "d-1", "state": "выдан", "price": 2.3,
                    "currency": "USD", "at": time.time(),
                    "done_at": time.time()})
        conf.store.save()

        class Remembers(self.Account):
            def get_deal(self, deal_id):
                return type("D", (), {
                    "id": deal_id,
                    "item": type("I", (), {"name": "Робуксы",
                                           "description": ""})(),
                    "status": type("S", (), {"name": "CONFIRMED"})(),
                    "chat": type("C", (), {"id": "c1"})(),
                    "user": type("U", (), {"username": "vasya"})(),
                    "transaction": type("T", (), {"value": 299})()})()

        link = FakeLink([item_bot.PICK_STAT + "profit",
                         item_bot.PICK_STAT + "fill"])
        item_bot.stats_menu(link, Remembers(self.Balance()))
        entry = item_bot.settings_of().store.conf("robux")["log"][0]

        self.assertEqual(entry["paid"], 299)
        self.assertIn("Дочитал сумм: 1", link.said[-1])

    def test_nothing_to_fetch_is_said_plainly(self):
        self.sold()
        link = FakeLink([item_bot.PICK_STAT + "profit",
                         item_bot.PICK_STAT + "fill"])
        item_bot.stats_menu(link, self.account())

        self.assertIn("Дочитывать нечего", link.said[-1])

    def test_a_deal_the_platform_forgot_is_counted_apart(self):
        conf = item_bot.settings_of()
        conf.store.conf("robux").setdefault("log", []).append(
            {"order": "gone", "state": "выдан", "price": 2.3,
             "currency": "USD", "at": time.time(), "done_at": time.time()})
        conf.store.save()

        class Forgot(self.Account):
            def get_deal(self, deal_id):
                raise RuntimeError("нет такой сделки")

            def get_deals(self, direction=None, count=24, after_cursor=None):
                return type("P", (), {"deals": [], "has_next_page": False,
                                      "end_cursor": None})()

        link = FakeLink([item_bot.PICK_STAT + "profit",
                         item_bot.PICK_STAT + "fill"])
        item_bot.stats_menu(link, Forgot(self.Balance()))

        self.assertIn("Не отдала площадка: 1", link.said[-1])

    # ---------- отзывы ----------

    def test_reviews_are_summed_up(self):
        said = self.asked([item_bot.PICK_STAT + "reviews", "отмена"],
                          account=self.account([self.Review(5, "Спасибо"),
                                                self.Review(4)]))

        self.assertIn("4.50 из 5", said)

    def test_the_spread_of_marks_is_shown(self):
        said = self.asked([item_bot.PICK_STAT + "reviews", "отмена"],
                          account=self.account([self.Review(5),
                                                self.Review(3)]))

        self.assertIn("★★★★★ 1", said)
        self.assertIn("★★★☆☆ 1", said)

    def test_bad_reviews_are_called_out(self):
        said = self.asked([item_bot.PICK_STAT + "reviews", "отмена"],
                          account=self.account([self.Review(5),
                                                self.Review(2, "Долго")]))

        self.assertIn("Ниже четвёрки: 1", said)

    def test_the_last_reviews_are_quoted(self):
        said = self.asked([item_bot.PICK_STAT + "reviews", "отмена"],
                          account=self.account([self.Review(5, "Всё быстро",
                                                            "vasya")]))

        self.assertIn("Всё быстро", said)
        self.assertIn("vasya", said)

    def test_no_reviews_is_not_an_error(self):
        said = self.screen([item_bot.PICK_STAT + "reviews"],
                           account=self.account([])).said[-1]

        self.assertIn("Отзывов пока нет", said)

    def test_unreadable_reviews_do_not_break_the_summary(self):
        said = self.asked(["отмена"])

        self.assertIn("📊 Статистика", said)

    # ---------- возврат и курс ----------

    def test_every_screen_can_go_back(self):
        for where in ("money", "sales", "profit", "reviews"):
            link = self.screen([item_bot.PICK_STAT + where,
                                item_bot.PICK_STAT + "back", "отмена"],
                               account=self.account([self.Review(5)]))

            self.assertIn("📊 Статистика", link.asked[-1][0], where)

    def test_the_rate_can_be_set(self):
        self.screen([item_bot.PICK_STAT + "rate", "95"])

        self.assertEqual(item_bot.settings_of().rate(), 95)

    def test_a_word_instead_of_a_rate_is_refused(self):
        link = self.screen([item_bot.PICK_STAT + "rate", "много"])

        self.assertEqual(item_bot.settings_of().rate(), 0)
        self.assertIn("не число", link.said[-1])

    def test_without_an_account_it_says_so(self):
        link = FakeLink([])
        item_bot.stats_menu(link, None)

        self.assertIn("кабинет", link.said[-1])

    def test_it_is_reachable_by_the_button(self):
        link = FakeLink(["отмена"])
        item_bot.handle_command(link, self.account(), "статистика")

        self.assertTrue(any("📊 Статистика" in said for said in link.said),
                        link.said)


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
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertIn("100 =", sheet)
        self.assertIn("400 =", sheet)

    def test_what_is_out_of_stock_is_not_offered(self):
        """Объявление по такому номиналу бот выдать не сможет, а покупатель
        заплатит и будет ждать."""
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertNotIn("800", sheet)

    def test_another_regions_nominals_are_not_offered(self):
        """Код чужого региона покупатель не активирует — объявление по
        нему стало бы спором, а не продажей."""
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertNotIn("700", sheet)

    def test_prices_can_be_counted_from_the_purchase(self):
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(),
                         "взять", "посчитать", "100", "40", "отмена"])
        item_bot.series_menu(link, "кабинет")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        # 1.1 $ × 100 ₽ × 1.4 = 154 → вверх до десятки.
        self.assertIn("100 = 160", sheet)
        self.assertIn("400 = 560", sheet)

    def test_without_a_key_the_seller_is_told_why(self):
        os.environ.pop("APPROUTE_KEY", None)
        item_bot._CATALOG["raw"] = None
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(), "взять"])
        item_bot.series_menu(link, "кабинет")

        self.assertIn("APPROUTE_KEY", link.said[-1])

    def test_a_name_without_a_number_is_asked_about_not_refused(self):
        """У продавца названия бывают какие угодно — «🥳ПРОМОКОДОМ🥳
        АВТОВЫДАЧА». Заставлять его переименовывать товар ради нашего
        разбора дороже, чем задать один вопрос."""
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + tid, "так",
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any("подставлять номинал некуда" in t
                            for t in link.said))
        self.assertTrue(any(t.startswith("100 =") for t in link.said))

    def test_the_offered_pattern_builds_real_names(self):
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + tid, "так", "сам",
                         "400 = 540", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any("400 🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА" in t
                            for t in link.said))

    def test_a_pattern_without_a_place_for_the_nominal_is_refused(self):
        """Десяток объявлений с одинаковым названием — мусор на витрине,
        который потом снимать руками."""
        tid = self.template("🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА")
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + tid, "Просто название"])
        item_bot.series_menu(link, "кабинет")

        self.assertIn("нет места под номинал", link.said[-1])
        self.assertEqual(link.last_buttons, item_bot.MENU)

    def test_the_card_is_recognised_by_the_game_when_the_name_is_odd(self):
        """Продавец назвал товар по-своему — но игра в шаблоне записана, и
        по ней карта узнаётся."""
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template("Валюта 100"),
                         "взять", "сам", "отмена"])
        item_bot.series_menu(link, "кабинет")

        self.assertTrue(any(t.startswith("100 =") for t in link.said))

    def test_an_unknown_card_still_allows_typing_by_hand(self):
        """Подкатегория живёт в карте: не узнав её, брать номиналы неоткуда
        — и предлагать несбыточное незачем."""
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.template(
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

    def test_an_empty_answer_still_leaves_a_screen_with_buttons(self):
        """Ровно тот случай, с которого всё началось: ответ показывался и
        тут же затирался, и нажимать было некуда."""
        link = self.run_command("серия")

        self.assertTrue(link.said)
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
        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + tid, "сам", rows, "да"])
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

        link = FakeLink([item_bot.PICK_SOURCE + "tpl",
                         item_bot.PICK_SERIES + self.tid, "сам",
                         "400 = 540", "отмена"])
        item_bot.series_menu(link, FakeAccount())
        plan = next(t for t in link.said if "Создам" in t)

        self.assertIn("541", plan)
        self.assertIn("поднята", plan)


class CopiesMenuTest(unittest.TestCase):
    """Экран «Копии и цены»: правило должно настраиваться, а не быть
    зашитым — у каждого продавца свой ассортимент и свои цены."""

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

        for _ in range(times):
            item_bot.from_template(
                FakeLink([item_bot.PICK + self.tid,
                          item_bot.PICK_ACT + "make"]), account)

        return [c["price"] for c in account.created]

    def test_the_screen_shows_the_current_rule(self):
        link = FakeLink(["отмена"])
        item_bot.copies_menu(link)

        self.assertIn("Одинаковых допускаем: 3", link.said[0])
        self.assertIn("Дальше дороже на: 1 ₽", link.said[0])

    def test_the_limit_can_be_changed_and_is_obeyed(self):
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "limit", "5",
                                       "отмена"]))

        self.assertEqual(self.press(6), [1320] * 5 + [1321])

    def test_the_step_can_be_changed_and_is_obeyed(self):
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "step", "50",
                                       "отмена"]))

        self.assertEqual(self.press(4)[-1], 1370)

    def test_it_can_be_switched_off_entirely(self):
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "on", "отмена"]))

        self.assertEqual(self.press(5), [1320] * 5)

    def test_varying_the_fields_can_be_switched_off(self):
        tid = item_bot.templates_of().save(
            "500 Robux", 700, "GL", [PNG], description="Коды",
            game=GAME, category=CATEGORY, obtaining=OBTAINING,
            fields=[{"id": "f1", "label": "Комментарий",
                     "required": False, "value": ""}])
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "vary", "отмена"]))
        account = FakeAccount()
        item_bot.from_template(
            FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"]),
            account)

        # Пустое поле на площадку не уходит вовсе — и выдумывать в него
        # фразу бот перестал.
        sent = [f.value for f in account.created[0]["data_fields"]]

        self.assertEqual(sent, [])

    def test_a_word_instead_of_a_number_is_refused(self):
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "limit",
                                       "много", "отмена"]))

        self.assertEqual(item_bot.ledger_of().rules()["limit"], 3)

    def test_resetting_asks_first(self):
        item_bot.ledger_of().remember(1000, 1320)
        link = FakeLink([item_bot.PICK_COPY + "reset", "отмена", "отмена"])
        item_bot.copies_menu(link)

        self.assertEqual(item_bot.ledger_of().pairs(), 1)

    def test_a_confirmed_reset_clears_the_count(self):
        item_bot.ledger_of().remember(1000, 1320)
        item_bot.copies_menu(FakeLink([item_bot.PICK_COPY + "reset", "да"]))

        self.assertEqual(item_bot.ledger_of().pairs(), 0)


class CopyFromMarketTest(unittest.TestCase):
    """Список для копии: все объявления, разложенные по кучкам."""

    HIT = "🏆 ХИТ КАТЕГОРИИ • 25.000.000₽ • 3 LVL"
    FAST = "🚀 БЫСТРАЯ ПОКУПКА • 30.000.000₽ • 3 LVL"

    class Market:
        """Площадка отдаёт по 24 за раз — и это не «все»."""

        def __init__(self, items, limit_after=0):
            self.items = items
            self.pages = 0
            # С какой страницы площадка просит сбавить темп.
            self.limit_after = limit_after

        def get_my_items(self, count=24, after_cursor=None, **kw):
            self.pages += 1

            if self.limit_after and self.pages > self.limit_after:
                raise RuntimeError("Слишком много попыток, пожалуйста, "
                                   "попробуйте повторить запрос позже")

            start = int(after_cursor or 0)
            chunk = self.items[start:start + count]
            nxt = start + count

            return type("P", (), {
                "items": chunk,
                "page_info": type("I", (), {
                    "has_next_page": nxt < len(self.items),
                    "end_cursor": str(nxt)})(),
            })()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        # Витрина помнится между нажатиями — но не между проверками.
        item_bot.forget_items()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_items()

    def market(self, hits=14, fasts=22):
        names = [self.HIT] * hits + [self.FAST] * fasts
        items = [type("I", (), {"id": f"i{n}", "name": nm, "price": 100 + n})()
                 for n, nm in enumerate(names)]

        return self.Market(items)

    def test_every_page_is_read_not_just_the_first(self):
        """Продавец, не нашедший своего объявления, решит, что бот его не
        видит."""
        market = self.market()
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, market)

        self.assertGreater(market.pages, 1)
        self.assertIn("36", link.said[-2] if len(link.said) > 1
                      else link.said[-1])

    def test_listings_are_split_into_piles(self):
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.market())
        names = [n for row in link.asked[-1][1] for n, _ in row]

        self.assertTrue(any("ХИТ КАТЕГОРИИ" in n for n in names))
        self.assertTrue(any("БЫСТРАЯ ПОКУПКА" in n for n in names))

    def test_the_biggest_pile_comes_first(self):
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.market())
        first = link.asked[-1][1][0][0][0]

        self.assertIn("БЫСТРАЯ ПОКУПКА", first)
        self.assertIn("22", first)

    def test_a_pile_is_shown_page_by_page(self):
        link = FakeLink(["все", item_bot.PICK_GROUP + "0", "отмена"])
        item_bot.copy_live(link, self.market())

        self.assertIn("страница 1 из 2", link.said[-2])

    def test_the_next_page_shows_the_rest(self):
        link = FakeLink(["все", item_bot.PICK_GROUP + "0",
                         item_bot.PICK_PAGE + "1", "отмена"])
        item_bot.copy_live(link, self.market())

        self.assertIn("страница 2 из 2", link.said[-2])

    def test_a_word_finds_the_listing(self):
        link = FakeLink(["все", item_bot.PICK_GROUP + "find", "хит",
                         "отмена"])
        item_bot.copy_live(link, self.market())

        self.assertIn("«хит»", link.said[-2])

    def test_one_kind_of_listing_needs_no_piles(self):
        """Делить нечего — сразу список, лишний экран только мешает."""
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.market(hits=3, fasts=0))

        self.assertIn("Какое повторить?", link.said[-2])

    def test_no_listings_at_all_is_explained(self):
        link = FakeLink(["все"])
        item_bot.copy_live(link, self.Market([]))

        self.assertIn("не нашлось", link.said[-1])


class RegionButtonsTest(unittest.TestCase):
    """Кнопки региона: первыми те, что есть у поставщика.

    Показать регион, которого у поставщика нет, и промолчать об этом —
    значит дать продавцу выставить товар, который выдача не выдаст. А
    спрятать его совсем — соврать про умения бота: принимает он все
    шестьдесят. Поэтому показываем все, но доступные помечены ✅ и идут
    первыми.
    """

    CATALOG = {"services": [
        {"id": "s1", "name": "Apple Gift Cards US",
         "subcategoryName": "Apple Gift Cards",
         "items": [{"id": "i1", "value": 10, "inStock": 5, "price": 1.0}]},
        {"id": "s2", "name": "Apple Gift Cards SA",
         "subcategoryName": "Apple Gift Cards",
         "items": [{"id": "i2", "value": 50, "inStock": 5, "price": 1.0}]},
        {"id": "s3", "name": "Apple Gift Cards MX",
         "subcategoryName": "Apple Gift Cards",
         "items": [{"id": "i3", "value": 200, "inStock": 0, "price": 1.0}]},
    ]}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        os.environ["APPROUTE_KEY"] = "ключ"
        item_bot._CATALOG["raw"] = self.CATALOG
        item_bot._CATALOG["at"] = time.time()

    def tearDown(self):
        item_bot._CATALOG["raw"] = None
        os.environ.pop("APPROUTE_KEY", None)

    def codes(self, name):
        draft = wizard.Draft()
        draft.name = name

        return [value for row in item_bot.buttons_for("region", draft)
                for _, value in row]

    def test_the_suppliers_regions_are_offered(self):
        got = self.codes("Apple Gift Card 10$")

        self.assertIn("US", got)
        self.assertIn("SA", got)

    def test_a_region_that_is_out_of_stock_is_not_marked(self):
        """Он доступен для выбора, но без ✅: у поставщика его сейчас нет."""
        draft_ = wizard.Draft()
        draft_.name = "Apple Gift Card 10$"
        rows = dict(item_bot.region_choices(draft_))

        self.assertFalse(rows["MX"])
        self.assertTrue(rows["US"])

    def test_the_marked_ones_come_first(self):
        got = self.codes("Apple Gift Card 10$")

        self.assertLess(got.index("SA"), got.index("GL"))

    def test_an_unknown_product_gets_the_usual_buttons(self):
        """Остаться вовсе без кнопок хуже, чем с неточными."""
        got = self.codes("Битки в Radmir RP")

        self.assertIn("GL", got)
        self.assertIn("RU", got)

    def test_without_a_catalog_the_usual_buttons_are_shown(self):
        item_bot._CATALOG["raw"] = None
        os.environ.pop("APPROUTE_KEY", None)
        got = self.codes("Apple Gift Card 10$")

        self.assertIn("GL", got)

    def test_the_settings_screen_knows_more_than_two_regions(self):
        """Привязать услугу для US или SA было нельзя вовсе, хотя номиналы
        там есть, — а именно за этим на такой экран и приходят."""
        conf = item_bot.settings_of()
        card = item_bot.card_by_slug("apple")

        self.assertIn("SA", item_bot.regions_of(conf, card))
        self.assertIn("US", item_bot.regions_of(conf, card))

    def test_a_pinned_region_stays_visible_even_without_stock(self):
        """Иначе привязка пропадёт с экрана, а работать продолжит."""
        conf = item_bot.settings_of()
        conf.set_service("apple", "HK", "svc-hk")

        self.assertIn("HK", item_bot.regions_of(conf,
                                                item_bot.card_by_slug("apple")))


class TooOftenTest(unittest.TestCase):
    """Площадка считает частые запросы и просит сбавить темп.

    Читать двадцать страниц подряд — верный способ нарваться: так и
    вышло, и вместо списка продавец увидел «слишком много попыток».
    """

    Market = CopyFromMarketTest.Market

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_items()

    def market(self, count=60, limit_after=0):
        items = [type("I", (), {"id": f"i{n}", "name": f"Товар {n}",
                                "price": 100})()
                 for n in range(count)]

        return self.Market(items, limit_after=limit_after)

    def test_what_was_read_is_kept_when_the_limit_hits(self):
        """Половина списка полезнее отказа: нужное объявление скорее
        всего в ней."""
        items, whole = item_bot.my_items(self.market(limit_after=2))

        self.assertEqual(len(items), 48)
        self.assertFalse(whole)

    def test_an_incomplete_list_is_said_to_be_incomplete(self):
        """Молча показать половину — значит дать решить, что остального
        нет вовсе."""
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.market(limit_after=2))

        self.assertTrue(any("неполный" in q for q, _ in link.asked))

    def test_the_warning_reaches_a_single_pile_too(self):
        """Кучка может быть одна — тогда экрана с кучками продавец не
        увидит вовсе, и предупреждение до него не дойдёт."""
        items = [type("I", (), {"id": f"i{n}", "name": "🚀 БЫСТРАЯ ПОКУПКА",
                                "price": 100})() for n in range(60)]
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.Market(items, limit_after=2))

        self.assertIn("неполный", link.asked[-1][0])

    def test_a_limit_on_the_very_first_page_is_explained(self):
        """Тут прочитанного нет вовсе — и это не поломка, а просьба
        подождать."""
        link = FakeLink(["все"])
        item_bot.copy_live(link, self.market(limit_after=0.5))

        self.assertIn("сбавить темп", link.said[-1])

    def test_a_full_read_is_not_called_incomplete(self):
        link = FakeLink(["все", "отмена"])
        item_bot.copy_live(link, self.market())

        self.assertNotIn("неполный", link.asked[0][0])

    def test_the_shop_is_read_once_not_on_every_press(self):
        """Перечитывать витрину на каждое нажатие значит выбирать лимит
        площадки собственными руками."""
        market = self.market()
        item_bot.copy_live(FakeLink(["все", "отмена"]), market)
        was = market.pages
        item_bot.copy_live(FakeLink(["все", "отмена"]), market)

        self.assertEqual(market.pages, was)

    def test_another_account_does_not_see_the_previous_shop(self):
        """Показать чужие объявления — значит дать скопировать не тот
        товар не в тот магазин."""
        first = self.market(count=5)
        item_bot.copy_live(FakeLink(["все", "отмена"]), first)

        item_bot.forget_items()
        second = self.market(count=30)
        item_bot.copy_live(FakeLink(["все", "отмена"]), second)

        self.assertGreater(second.pages, 0)


class ByCategoryTest(unittest.TestCase):
    """Отбор по настоящей категории, а не по началу названия.

    У продавца с пятью сотнями объявлений начало названия — это
    «РЕКОМЕНДУЕМ» и «ЛУЧШАЯ ЦЕНА», то есть приставки, а не категории.
    Настоящую категорию площадка в списке товаров не отдаёт, зато умеет
    по ней отбирать — этим и пользуемся.
    """

    class Market:
        BY_CATEGORY = {
            "c-robux": 83,
            "c-acc": 12,
        }

        def __init__(self):
            self.asked = []

        def _rows(self, key, count):
            return [type("I", (), {"id": f"{key}-{n}",
                                   "name": f"🚀 БЫСТРАЯ ПОКУПКА {n}",
                                   "price": 100 + n})()
                    for n in range(count)]

        def get_games(self, name="", count=12):
            self.asked.append(("games", name))

            return type("P", (), {"games": [
                type("G", (), {"id": "g-roblox", "name": "Roblox"})(),
                type("G", (), {"id": "g-az", "name": "Arizona RP"})()]})()

        def get_game(self, id=None):                       # noqa: A002
            self.asked.append(("game", id))

            return type("G", (), {"categories": [
                type("C", (), {"id": "c-robux", "name": "Робуксы"})(),
                type("C", (), {"id": "c-acc", "name": "Аккаунты"})()]})()

        def get_my_items(self, count=24, after_cursor=None,
                         category_id=None, game_id=None, **kw):
            self.asked.append(("items", category_id or game_id or "всё"))
            key = category_id or game_id or "всё"
            rows = self._rows(key, self.BY_CATEGORY.get(category_id, 95))
            start = int(after_cursor or 0)

            return type("P", (), {
                "items": rows[start:start + count],
                "page_info": type("I", (), {
                    "has_next_page": start + count < len(rows),
                    "end_cursor": str(start + count)})(),
            })()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_items()

    def pick(self, *answers):
        market = self.Market()
        link = FakeLink(list(answers))
        item_bot.copy_live(link, market)

        return link, market

    def test_the_game_is_asked_first(self):
        link, _ = self.pick("отмена")

        self.assertIn("игры", link.asked[0][0])

    def test_the_games_categories_are_offered(self):
        link, _ = self.pick("роблокс", item_bot.PICK_GAME + "g-roblox",
                            "отмена")
        names = [n for row in link.asked[-1][1] for n, _ in row]

        self.assertIn("Робуксы", names)
        self.assertIn("Аккаунты", names)

    def test_the_platform_does_the_filtering(self):
        """Это не только точнее — это ещё и меньше страниц, то есть меньше
        поводов услышать «слишком много попыток»."""
        _, market = self.pick("роблокс", item_bot.PICK_GAME + "g-roblox",
                              item_bot.PICK_CAT + "c-acc", "отмена")
        asked = [what for kind, what in market.asked if kind == "items"]

        self.assertTrue(asked)
        self.assertTrue(all(what == "c-acc" for what in asked))

    def test_only_that_categorys_listings_are_shown(self):
        link, _ = self.pick("роблокс", item_bot.PICK_GAME + "g-roblox",
                            item_bot.PICK_CAT + "c-acc",
                            item_bot.PICK_NOM + "all", "отмена")

        # Двенадцать объявлений — это ровно одна страница, и подписи про
        # страницы тогда нет.
        self.assertIn("Аккаунты", link.asked[-1][0])
        self.assertEqual(len(link.asked[-1][1]) - 1, 12)

    def test_all_categories_of_a_game_can_be_taken(self):
        _, market = self.pick("роблокс", item_bot.PICK_GAME + "g-roblox",
                              item_bot.PICK_CAT + "все", "отмена")
        asked = [what for kind, what in market.asked if kind == "items"]

        self.assertTrue(all(what == "g-roblox" for what in asked))

    def test_everything_at_once_is_still_possible(self):
        """Продавцу с десятком объявлений отбор только мешает."""
        _, market = self.pick("все", "отмена")
        asked = [what for kind, what in market.asked if kind == "items"]

        self.assertTrue(asked)
        self.assertTrue(all(what == "всё" for what in asked))

    def test_nothing_in_the_category_is_explained(self):
        market = self.Market()
        market.BY_CATEGORY = dict(market.BY_CATEGORY, **{"c-acc": 0})
        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-acc"])
        item_bot.copy_live(link, market)

        self.assertIn("не нашлось", link.said[-1])

    def test_a_game_that_is_not_found_is_said_plainly(self):
        class Empty(self.Market):
            def get_games(self, name="", count=12):
                return type("P", (), {"games": []})()

        link = FakeLink(["чепуха"])
        item_bot.copy_live(link, Empty())

        self.assertIn("ничего не нашлось", link.said[-1])


class NominalPilesTest(unittest.TestCase):
    """Внутри категории объявления делятся по номиналу.

    У продавца все объявления категории названы одинаково —
    «🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎», — и плоский список из таких строк
    выбирать не помогает: отличает их номинал, а не название.
    """

    NAME = "🥳ПРОМОКОДОМ🥳😎 АВТОВЫДАЧА😎"

    class Market:
        # Номинал живёт в названии: так его пишет и сам продавец.
        ROWS = [("80 РОБУКСОВ", 119), ("80 РОБУКСОВ", 125),
                ("400 РОБУКСОВ", 358), ("1000 РОБУКСОВ", 899),
                ("1000 РОБУКСОВ", 915), ("1000 РОБУКСОВ", 930)]

        def get_games(self, name="", count=12):
            return type("P", (), {"games": [
                type("G", (), {"id": "g-roblox", "name": "Roblox"})()]})()

        def get_game(self, id=None):                       # noqa: A002
            return type("G", (), {"categories": [
                type("C", (), {"id": "c-robux", "name": "Робуксы"})()]})()

        def get_my_items(self, count=24, after_cursor=None, **kw):
            rows = [type("I", (), {"id": f"i{n}",
                                   "name": f"🍎{what} · Roblox",
                                   "price": price})()
                    for n, (what, price) in enumerate(self.ROWS)]

            return type("P", (), {
                "items": rows,
                "page_info": type("I", (), {"has_next_page": False,
                                            "end_cursor": "0"})()})()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_items()

    def choose(self, *answers):
        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-robux"] + list(answers))
        chosen = item_bot.choose_live_item(link, self.Market())

        return link, chosen

    def names(self, link):
        return [n for row in link.asked[-1][1] for n, _ in row]

    def test_the_piles_are_nominals_not_listings(self):
        link, _ = self.choose("отмена")
        names = self.names(link)

        self.assertIn("номинал", link.asked[-1][0].lower())
        self.assertTrue(any(n.startswith("80") for n in names), names)
        self.assertTrue(any(n.startswith("400") for n in names), names)
        self.assertTrue(any(n.startswith("1000") for n in names), names)

    def test_a_pile_says_how_many_are_in_it(self):
        link, _ = self.choose("отмена")
        row = next(n for n in self.names(link) if n.startswith("1000"))

        self.assertIn("3 объявления", row)

    def test_the_smallest_nominal_goes_first(self):
        """Номиналы продавец помнит подряд, а не по размеру кучки."""
        link, _ = self.choose("отмена")

        self.assertTrue(self.names(link)[0].startswith("80"))

    def test_the_measure_is_shown_with_the_number(self):
        link, _ = self.choose("отмена")

        self.assertTrue(any("Robux" in n for n in self.names(link)),
                        self.names(link))

    def test_picking_a_nominal_shows_only_its_listings(self):
        link, _ = self.choose(item_bot.PICK_NOM + "0", "отмена")
        prices = [n.split(" ")[0] for n in self.names(link)
                  if n[:1].isdigit()]

        self.assertEqual(sorted(prices), ["119", "125"])

    def test_the_chosen_listing_is_the_answer(self):
        link, chosen = self.choose(item_bot.PICK_NOM + "1",
                                   item_bot.PICK_LIVE + "i2")

        self.assertEqual(chosen, "i2")

    def test_everything_at_once_is_still_one_press_away(self):
        link, _ = self.choose(item_bot.PICK_NOM + "all", "отмена")

        self.assertEqual(len([n for n in self.names(link)
                              if n[:1].isdigit()]), 6)

    def test_a_single_nominal_is_not_split_at_all(self):
        """Лишний экран с одной кнопкой — это не помощь."""
        class One(self.Market):
            ROWS = [("80 РОБУКСОВ", 119), ("80 РОБУКСОВ", 125)]

        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-robux", "отмена"])
        item_bot.choose_live_item(link, One())

        self.assertNotIn("номинал", link.asked[-1][0].lower())

    def test_listings_without_a_nominal_are_not_dropped(self):
        class Mixed(self.Market):
            ROWS = [("80 РОБУКСОВ", 119), ("ПРОМОКОДОМ АВТОВЫДАЧА", 499)]

        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-robux", "отмена"])
        item_bot.choose_live_item(link, Mixed())
        names = [n for row in link.asked[-1][1] for n, _ in row]

        self.assertTrue(any("Без номинала" in n for n in names), names)

    def test_the_price_in_the_name_is_not_taken_for_a_nominal(self):
        """«Apple Gift Card 10$ за 900 рублей» — это десятка, не девятьсот."""
        class Money(self.Market):
            ROWS = [("Apple Gift Card 10 USD за 900 рублей", 900),
                    ("Apple Gift Card 25 USD за 2200 рублей", 2200)]

        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-robux", "отмена"])
        item_bot.choose_live_item(link, Money())
        names = [n for row in link.asked[-1][1] for n, _ in row]

        self.assertTrue(any(n.startswith("10") for n in names), names)
        self.assertFalse(any(n.startswith("900") for n in names), names)

    def test_many_nominals_are_paged_not_cut(self):
        class Many(self.Market):
            ROWS = [(f"{n * 100} РОБУКСОВ", n * 10) for n in range(1, 20)]

        link = FakeLink(["роблокс", item_bot.PICK_GAME + "g-roblox",
                         item_bot.PICK_CAT + "c-robux",
                         item_bot.PICK_NOM + "p1", "отмена"])
        item_bot.choose_live_item(link, Many())

        self.assertIn("страница 2", link.asked[-1][0])


class LiveShopCountTest(unittest.TestCase):
    """Бот считает и то, что выставлено без него."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause = item_bot.PAGE_PAUSE
        item_bot.PAGE_PAUSE = 0

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.forget_items()

    def market(self, count=84, price=179, name="1000 Robux"):
        rows = [type("I", (), {"id": f"i{n}", "name": name, "price": price})()
                for n in range(count)]

        class Market(FakeAccount):
            asked = []

            def get_my_items(self, count=24, after_cursor=None, **kw):
                Market.asked.append(kw.get("category_id")
                                    or kw.get("game_id") or "всё")
                start = int(after_cursor or 0)

                return type("P", (), {
                    "items": rows[start:start + count],
                    "page_info": type("I", (), {
                        "has_next_page": start + count < len(rows),
                        "end_cursor": str(start + count)})(),
                })()

        Market.asked = []

        return Market()

    def template(self, price=179, name="1000 Robux"):
        return item_bot.templates_of().save(
            name, price, "GL", [PNG], description="Коды",
            game={"id": "g1", "name": "Roblox"},
            category={"id": "c1", "name": "Робуксы"},
            obtaining=OBTAINING)

    def test_a_crowded_shop_raises_the_price_at_once(self):
        """Восемьдесят четыре одинаковых уже висят, а бот не создавал ни
        одного — раньше он спокойно добавил бы ещё три."""
        market = self.market(84)
        item_bot.from_template(
            FakeLink([item_bot.PICK + self.template(),
                      item_bot.PICK_ACT + "make"]), market)

        self.assertEqual(market.created[0]["price"], 180)

    def test_an_empty_shop_keeps_the_price(self):
        market = self.market(0)
        item_bot.from_template(
            FakeLink([item_bot.PICK + self.template(),
                      item_bot.PICK_ACT + "make"]), market)

        self.assertEqual(market.created[0]["price"], 179)

    def test_only_the_same_category_is_read(self):
        """Витрина бывает на пять сотен товаров: читать её целиком ради
        одного нажатия значит заставить продавца ждать, да ещё и нарваться
        на «слишком много попыток»."""
        market = self.market(5)
        item_bot.from_template(
            FakeLink([item_bot.PICK + self.template(),
                      item_bot.PICK_ACT + "make"]), market)

        self.assertEqual(set(type(market).asked), {"c1"})

    def test_a_different_nominal_on_the_shop_does_not_interfere(self):
        market = self.market(84, name="400 Robux")
        item_bot.from_template(
            FakeLink([item_bot.PICK + self.template(name="1000 Robux"),
                      item_bot.PICK_ACT + "make"]), market)

        self.assertEqual(market.created[0]["price"], 179)

    def test_the_seller_is_told_how_many_there_already_are(self):
        market = self.market(84)
        link = FakeLink([item_bot.PICK + self.template(),
                         item_bot.PICK_ACT + "make"])
        item_bot.from_template(link, market)
        said = next(t for t in link.said if "Повторяю" in t)

        self.assertIn("уже 84", said)

    def test_an_unreadable_shop_does_not_block_creating(self):
        """Считать по своему счёту хуже, чем по витрине, но лучше, чем не
        создать товар вовсе."""
        class Broken(FakeAccount):
            def get_my_items(self, **kw):
                raise RuntimeError("площадка не ответила")

        market = Broken()
        item_bot.from_template(
            FakeLink([item_bot.PICK + self.template(),
                      item_bot.PICK_ACT + "make"]), market)

        self.assertEqual(market.created[0]["price"], 179)


class NotOnlyRobuxTest(unittest.TestCase):
    """Создание товара было заточено под робуксы. Проверяем остальных."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")

    def made(self, name, price, region, description="пропустить"):
        draft = wizard.Draft()
        draft.game = draft.category = draft.obtaining = {"id": "1",
                                                         "name": "x"}

        # Порядок ответов — как их спрашивает мастер: регион раньше
        # названия, чтобы бот успел подставить характеристику площадки.
        for answer in (region, name, str(price), description):
            wizard.apply(draft, answer)

        item_bot.apply_card_template(draft)

        return draft, wizard.description_for(draft)

    def test_apple_gets_apple_activation_not_roblox(self):
        _, text = self.made("Apple Gift Card 10$", 900, "US")

        self.assertIn("App Store", text)
        self.assertNotIn("roblox", text.lower())

    def test_xbox_gets_xbox_activation(self):
        _, text = self.made("Xbox Gift Card 25 USD", 2100, "US")

        self.assertIn("xbox.com/redeem", text)
        self.assertNotIn("roblox", text.lower())

    def test_a_russian_steam_card_is_not_priced_in_dollars(self):
        _, text = self.made("Steam 500 ₽", 550, "RU")

        self.assertIn("500₽", text)
        self.assertNotIn("500$", text)

    def test_the_price_in_the_name_does_not_become_the_nominal(self):
        """Иначе бот пошёл бы покупать у поставщика номинал 900."""
        draft, text = self.made("Apple Gift Card 10$ за 900 рублей", 900,
                                "US")

        self.assertEqual(draft.nominal, 10)
        self.assertIn("Номинал: 10", text)

    def test_what_we_wrote_is_what_the_delivery_reads(self):
        for name, price, region, want in [
                ("Apple Gift Card 10$ за 900 рублей", 900, "US", 10),
                ("Xbox Gift Card 25 USD", 2100, "US", 25),
                ("Steam 500 ₽", 550, "RU", 500),
                ("Apple Gift Card 100 TL", 1400, "TR", 100),
                ("Roblox 1000 Robux", 700, "GL", 1000)]:
            _, text = self.made(name, price, region)
            got, why = nominal_for(name, text, price)

            self.assertEqual(got, want, f"{name}: {why}")

    def test_an_unknown_card_is_not_told_to_redeem_on_roblox(self):
        """Неверная подсказка про активацию хуже её отсутствия."""
        _, text = self.made("Валюта в Arizona RP 1000000", 500, "RU")

        self.assertNotIn("roblox", text.lower())


class SeriesFromShopTest(unittest.TestCase):
    """Серия с профиля, а не из того, что создано через бота.

    Серия умела строиться только из шаблона, а шаблон запоминается при
    создании товара через бота. У продавца, выставившего всё в кабинете на
    сайте, шаблонов нет вовсе — и серия была ему недоступна, хотя именно
    ему она нужнее всего.
    """

    CATALOG = {"services": [
        {"id": "s", "name": "Roblox Gift Cards Global",
         "subcategoryName": "Roblox Gift Cards",
         "items": [{"id": f"i{v}", "value": v, "inStock": 9, "price": 1.0}
                   for v in (100, 400, 800)]}]}

    LIVE = "100 🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА"

    class Market(FakeAccount):
        def __init__(self, description="Регион кода: GL\nНоминал: 100"):
            super().__init__()
            self.description = description
            self.read = []

        def get_games(self, name="", count=12):
            return type("P", (), {"games": [
                type("G", (), {"id": "g1", "name": "Roblox"})()]})()

        def get_game(self, id=None):                       # noqa: A002
            return type("G", (), {"categories": [
                type("C", (), {"id": "c1", "name": "Робуксы"})()]})()

        def get_my_items(self, count=24, after_cursor=None, **kw):
            rows = [type("I", (), {
                "id": "live-1",
                "name": SeriesFromShopTest.LIVE,
                "price": 179})()]

            return type("P", (), {
                "items": rows,
                "page_info": type("I", (), {"has_next_page": False,
                                            "end_cursor": None})()})()

        def get_item(self, id):                            # noqa: A002
            self.read.append(id)
            node = type("N", (), {})

            return type("Item", (), {
                "id": "live-1", "name": SeriesFromShopTest.LIVE,
                "price": 179, "description": self.description,
                "game": type("G", (), {"id": "g1", "name": "Roblox"})(),
                "category": type("C", (), {"id": "c1",
                                           "name": "Робуксы"})(),
                "obtaining_type": type("O", (), {"id": "o1",
                                                 "name": "Без входа"})(),
                "attributes": {"a1": "Global"},
                "data_fields": [type("F", (), {
                    "id": "f1", "label": "Комментарий",
                    "required": False, "value": ""})()],
                "attachments": [type("A", (), {
                    "url": "https://cdn/1.jpg"})()],
            })()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause, item_bot.PAGE_PAUSE = item_bot.PAGE_PAUSE, 0
        self._fetch = item_bot.fetch_photos
        item_bot.fetch_photos = lambda urls: [PNG for _ in urls]
        os.environ["APPROUTE_KEY"] = "ключ"
        item_bot._CATALOG["raw"] = self.CATALOG
        item_bot._CATALOG["at"] = time.time()

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.fetch_photos = self._fetch
        item_bot._CATALOG["raw"] = None
        item_bot.forget_items()
        os.environ.pop("APPROUTE_KEY", None)

    def run_series(self, *answers):
        market = self.Market()
        link = FakeLink([item_bot.PICK_SOURCE + "live", "роблокс",
                         item_bot.PICK_GAME + "g1", item_bot.PICK_CAT + "c1",
                         item_bot.PICK_LIVE + "live-1"] + list(answers))
        item_bot.series_menu(link, market)

        return link, market

    def test_a_series_is_built_without_any_template(self):
        _, market = self.run_series("сам", "400 = 540\n800 = 1060", "да")

        self.assertEqual([c["name"] for c in market.created],
                         ["400 🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА",
                          "800 🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА"])
        self.assertEqual([c["price"] for c in market.created], [540, 1060])

    def test_the_shop_is_offered_first(self):
        """Шаблонов у продавца может не быть вовсе."""
        link = FakeLink(["отмена"])
        item_bot.series_menu(link, self.Market())
        names = [n for row in link.asked[0][1] for n, _ in row]

        self.assertIn("📋 С витрины", names)

    def test_templates_are_not_offered_when_there_are_none(self):
        link = FakeLink(["отмена"])
        item_bot.series_menu(link, self.Market())
        names = [n for row in link.asked[0][1] for n, _ in row]

        self.assertFalse(any("шаблон" in n.lower() for n in names))

    def test_a_saved_template_is_still_offered(self):
        item_bot.templates_of().save("100 Robux", 179, "GL", [PNG],
                                     game=GAME, category=CATEGORY,
                                     obtaining=OBTAINING)
        link = FakeLink(["отмена"])
        item_bot.series_menu(link, self.Market())
        names = [n for row in link.asked[0][1] for n, _ in row]

        self.assertTrue(any("шаблон" in n.lower() for n in names))

    def test_the_sample_keeps_its_category_and_attributes(self):
        _, market = self.run_series("сам", "400 = 540", "да")
        sent = market.created[0]

        self.assertEqual(sent["game_category_id"], "c1")
        self.assertEqual(sent["obtaining_type_id"], "o1")
        self.assertEqual(sent["options"], {"a1": "Global"})

    def test_the_description_gets_one_region_line_not_two(self):
        """Строки про регион и номинал бот ставит свои. Скопированные как
        есть, они встали бы вторыми, и движок выдачи прочитал бы не ту."""
        _, market = self.run_series("сам", "400 = 540", "да")
        text = market.created[0]["description"]

        self.assertEqual(text.count("Регион кода:"), 1)
        self.assertEqual(text.count("Номинал:"), 1)
        self.assertIn("Номинал: 400", text)

    def test_the_nominals_come_from_the_supplier(self):
        link, _ = self.run_series("взять", "сам", "400 = 540", "да")
        sheet = next(t for t in link.said if t.startswith("100 ="))

        self.assertIn("400 =", sheet)
        self.assertIn("800 =", sheet)


class TemplateFromShopTest(unittest.TestCase):
    """Шаблон из объявления, которое уже стоит на витрине.

    Шаблоны заводились только из товаров, созданных через бота. У
    продавца, выставившего всё в кабинете на сайте, их нет вовсе — и
    повторять одним нажатием ему было нечего, хотя объявления у него есть.
    """

    Market = SeriesFromShopTest.Market
    LIVE = SeriesFromShopTest.LIVE

    def setUp(self):
        self.root = tempfile.mkdtemp()
        item_bot.TEMPLATE_DIR = os.path.join(self.root, "шаблоны")
        item_bot.ACCOUNTS_DIR = os.path.join(self.root, "кабинеты")
        item_bot.SETTINGS_DIR = os.path.join(self.root, "выдача")
        item_bot.forget_items()
        self._pause, item_bot.PAGE_PAUSE = item_bot.PAGE_PAUSE, 0
        self._fetch = item_bot.fetch_photos
        item_bot.fetch_photos = lambda urls: [PNG for _ in urls]

    def tearDown(self):
        item_bot.PAGE_PAUSE = self._pause
        item_bot.fetch_photos = self._fetch
        item_bot.forget_items()

    def take(self, market=None):
        market = market or self.Market()
        link = FakeLink([item_bot.PICK + "shop", "роблокс",
                         item_bot.PICK_GAME + "g1", item_bot.PICK_CAT + "c1",
                         item_bot.PICK_LIVE + "live-1"])
        item_bot.from_template(link, market)

        return link, market

    def test_an_empty_list_offers_the_shop_instead_of_a_dead_end(self):
        link = FakeLink(["отмена"])
        item_bot.from_template(link, self.Market())
        names = [n for row in link.asked[0][1] for n, _ in row]

        self.assertIn("📥 Взять с витрины", names)

    def test_a_live_listing_becomes_a_template(self):
        self.take()
        saved = item_bot.templates_of().all()

        self.assertEqual([t.name for t in saved], [self.LIVE])

    def test_nothing_is_created_on_the_marketplace(self):
        """Товар уже есть — второй такой же сейчас не нужен."""
        _, market = self.take()

        self.assertEqual(market.created, [])

    def test_the_template_keeps_everything_needed_to_repeat(self):
        self.take()
        template = item_bot.templates_of().all()[0]

        self.assertEqual(template.price, 179)
        self.assertEqual(template.region, "GL")
        self.assertEqual(template.category["id"], "c1")
        self.assertEqual(template.obtaining["id"], "o1")
        self.assertTrue(template.photos())

    def test_the_saved_template_then_repeats_in_one_press(self):
        self.take()
        tid = item_bot.templates_of().all()[0].id
        market = self.Market()
        item_bot.from_template(
            FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"]),
            market)

        self.assertEqual(market.created[0]["name"], self.LIVE)

    def test_the_seller_is_told_where_to_use_it(self):
        link, _ = self.take()

        self.assertIn("одним нажатием", link.said[-1])

    def test_the_description_gets_one_region_line_not_two(self):
        self.take()
        tid = item_bot.templates_of().all()[0].id
        market = self.Market()
        item_bot.from_template(
            FakeLink([item_bot.PICK + tid, item_bot.PICK_ACT + "make"]),
            market)
        text = market.created[0]["description"]

        self.assertEqual(text.count("Регион кода:"), 1)
        self.assertEqual(text.count("Номинал:"), 1)


if __name__ == "__main__":
    unittest.main()
