"""Менеджер: ответить покупателю сам и позвать продавца, когда ответить нечем.

Ответы уходят настоящим людям от имени продавца, и цена ошибки тут не
«бот сказал глупость», а «бот ответил шаблоном на „вы меня обманули"».
Поэтому проверяется прежде всего то, чего менеджер НЕ делает.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import manager                                                  # noqa: E402


class FakeStore:
    """Хранилище: общий словарь и счётчик записей."""

    def __init__(self, data=None):
        self.data = data if data is not None else {}
        self.saves = 0

    def shared(self) -> dict:
        return self.data

    def save(self) -> None:
        self.saves += 1


class User:
    def __init__(self, id="", username=""):                    # noqa: A002
        self.id, self.username = id, username


class Item:
    def __init__(self, name=""):
        self.name = name


class Deal:
    def __init__(self, id="", item=None, chat=None):           # noqa: A002
        self.id, self.item, self.chat = id, item, chat


class Message:
    def __init__(self, text="", user=None, deal=None):
        self.text, self.user, self.deal = text, user, deal


class Chat:
    def __init__(self, id="", deals=None):                     # noqa: A002
        self.id, self.deals = id, deals or []


def message_event(text, chat_id="c1", who="vasya", who_id="u2", title=""):
    deal = Deal(id="d1", item=Item(title)) if title else None

    return type("NewMessageEvent", (), {
        "message": Message(text, User(who_id, who), deal),
        "chat": Chat(chat_id)})()


def buy_event(title, chat_id="c1"):
    return type("ItemPaidEvent", (), {
        "deal": Deal(id="d1", item=Item(title), chat=Chat(chat_id)),
        "chat": Chat(chat_id)})()


class SettingsTest(unittest.TestCase):
    def test_a_fresh_store_gets_the_whole_shape(self):
        conf = manager.settings_of({})

        self.assertIn("faq", conf)
        self.assertIn("complaint", conf)
        self.assertFalse(conf["on"])

    def test_the_manager_is_off_until_switched_on(self):
        """Ответы уходят живым людям — включает их продавец, прочитав."""
        self.assertFalse(manager.DEFAULT["on"])

    def test_what_was_set_up_survives_an_update(self):
        """Новая возможность не должна ронять того, кто настроил всё
        полгода назад."""
        shared = {"manager": {"on": True, "faq": [{"word": "код"}]}}
        conf = manager.settings_of(shared)

        self.assertTrue(conf["on"])
        self.assertEqual(conf["faq"], [{"word": "код"}])
        self.assertIn("remind", conf)

    def test_an_inner_key_is_filled_in_too(self):
        conf = manager.settings_of({"manager": {"complaint": {"on": False}}})

        self.assertEqual(conf["complaint"], {"on": False, "words": []})


class ComplaintTest(unittest.TestCase):
    def test_the_usual_words_are_caught(self):
        for text in ("Вы меня обманули!", "верните деньги",
                     "код не работает", "буду жаловаться в поддержку",
                     "промокод не пришёл"):
            self.assertTrue(manager.is_complaint(text), text)

    def test_a_calm_question_is_not_a_complaint(self):
        for text in ("здравствуйте", "как активировать код?",
                     "спасибо большое", "а можно ещё один?"):
            self.assertFalse(manager.is_complaint(text), text)

    def test_the_seller_can_add_his_own_words(self):
        self.assertTrue(manager.is_complaint("это фейк", ("фейк",)))

    def test_nothing_is_not_a_complaint(self):
        self.assertFalse(manager.is_complaint(""))


class AnswerTest(unittest.TestCase):
    CONF = {"faq": [{"word": "активир", "text": "Вставьте код в Robux"},
                    {"word": "спасибо", "text": "Пожалуйста!"}],
            "rules": [{"word": "робукс", "text": "Робуксы приходят сразу."}],
            "complaint": {"on": True, "words": []}}

    def test_the_question_is_answered(self):
        self.assertEqual(manager.answer_for("как активировать?", "", self.CONF),
                         "Вставьте код в Robux")

    def test_the_item_answers_when_the_question_did_not(self):
        said = manager.answer_for("привет", "80 РОБУКСОВ", self.CONF)

        self.assertEqual(said, "Робуксы приходят сразу.")

    def test_a_complaint_is_never_answered_by_a_template(self):
        """Недовольный покупатель, которому ответил автомат, идёт не к
        продавцу, а в спор."""
        said = manager.answer_for("обманули, верните деньги", "80 РОБУКСОВ",
                                  self.CONF)

        self.assertEqual(said, "")

    def test_an_unknown_question_stays_unanswered(self):
        self.assertEqual(manager.answer_for("а вы из какого города?", "",
                                            self.CONF), "")

    def test_the_first_rule_wins(self):
        """Порядок правил — это способ продавца сказать, что важнее."""
        conf = {"faq": [{"word": "код", "text": "первый"},
                        {"word": "код", "text": "второй"}]}

        self.assertEqual(manager.answer_for("где код", "", conf), "первый")

    def test_the_case_and_spaces_do_not_matter(self):
        conf = {"faq": [{"word": "Как   Активировать", "text": "вот так"}]}

        self.assertEqual(manager.answer_for("как активировать?", "", conf),
                         "вот так")


class HelloTest(unittest.TestCase):
    def test_nothing_is_said_while_it_is_off(self):
        conf = {"hello": {"on": False, "text": "Спасибо за покупку!"}}

        self.assertEqual(manager.hello_for("80 робуксов", conf), "")

    def test_the_greeting_goes_out_when_it_is_on(self):
        conf = {"hello": {"on": True, "text": "Спасибо за покупку!"}}

        self.assertEqual(manager.hello_for("80 робуксов", conf),
                         "Спасибо за покупку!")

    def test_the_item_rule_is_stronger_than_the_common_one(self):
        conf = {"hello": {"on": True, "text": "Спасибо!"},
                "rules": [{"word": "робукс", "text": "Робуксы уже летят."}]}

        self.assertEqual(manager.hello_for("80 РОБУКСОВ", conf),
                         "Робуксы уже летят.")


class TalkLimitTest(unittest.TestCase):
    """Покупатель пишет три сообщения подряд — это один разговор, а не три."""

    def test_the_first_answer_is_allowed(self):
        self.assertEqual(manager.may_talk({}, "c1", 1000.0), "")

    def test_a_second_answer_right_away_is_not(self):
        talks = {}
        manager.talked(talks, "c1", 1000.0)

        self.assertIn("только что", manager.may_talk(talks, "c1", 1010.0))

    def test_after_the_pause_it_is_allowed_again(self):
        talks = {}
        manager.talked(talks, "c1", 1000.0)

        self.assertEqual(manager.may_talk(talks, "c1", 1000.0 + 120), "")

    def test_the_daily_limit_stops_the_shower(self):
        talks = {}
        now = 1000.0

        for number in range(manager.PER_DAY):
            manager.talked(talks, "c1", now + number * 200)

        why = manager.may_talk(talks, "c1", now + 10000)

        self.assertIn("продавец", why)

    def test_another_chat_is_not_affected(self):
        talks = {}
        manager.talked(talks, "c1", 1000.0)

        self.assertEqual(manager.may_talk(talks, "c2", 1000.0), "")

    def test_a_chat_without_a_number_gets_nothing(self):
        self.assertTrue(manager.may_talk({}, "", 1000.0))

    def test_old_talks_are_forgotten(self):
        talks = {}

        for number in range(manager.KEEP_TALKS + 20):
            manager.talked(talks, f"c{number}", 1000.0 + number)

        self.assertLessEqual(len(talks), manager.KEEP_TALKS)
        self.assertNotIn("c0", talks)


class HandleTest(unittest.TestCase):
    """Живые события: что уходит в чат, что — продавцу, а что никуда."""

    def build(self, conf=None, broken=False):
        store = FakeStore({"manager": conf or {}})
        sent, told = [], []

        def reply(chat_id, text):
            sent.append((chat_id, text))

            return not broken

        man = manager.Manager(store, reply, told.append,
                              now=lambda: 1000.0)

        return man, sent, told, store

    ON = {"on": True,
          "faq": [{"word": "активир", "text": "Вставьте код в Robux"}],
          "hello": {"on": True, "text": "Спасибо за покупку!"}}

    def test_a_silent_manager_answers_nothing(self):
        man, sent, told, _ = self.build({**self.ON, "on": False})
        man.handle(message_event("как активировать?"), "message")

        self.assertEqual(sent, [])

    def test_the_question_is_answered_in_the_chat(self):
        man, sent, _, _ = self.build(self.ON)
        what = man.handle(message_event("как активировать?"), "message")

        self.assertEqual(sent, [("c1", "Вставьте код в Robux")])
        self.assertEqual(what, "ответил")

    def test_a_complaint_calls_the_seller_and_says_nothing(self):
        man, sent, told, _ = self.build(self.ON)
        what = man.handle(message_event("вы обманули, верните деньги"),
                          "message")

        self.assertEqual(sent, [])
        self.assertEqual(len(told), 1)
        self.assertIn("ЖАЛОБА", told[0])
        self.assertIn("верните деньги", told[0])
        self.assertIn("жалоба", what)

    def test_our_own_message_is_not_answered(self):
        """Иначе бот отвечает сам себе, и это никогда не кончается."""
        man, sent, _, _ = self.build(self.ON)
        man.handle(message_event("как активировать?", who_id="me"),
                   "message", me_id="me")

        self.assertEqual(sent, [])

    def test_a_purchase_gets_the_greeting(self):
        man, sent, _, _ = self.build(self.ON)
        man.handle(buy_event("80 робуксов"), "buy")

        self.assertEqual(sent, [("c1", "Спасибо за покупку!")])

    def test_support_and_system_chats_are_left_alone(self):
        man, sent, _, _ = self.build(self.ON)
        man.handle(message_event("как активировать?"), "support")
        man.handle(message_event("как активировать?"), "system")

        self.assertEqual(sent, [])

    def test_a_muted_chat_stays_silent(self):
        man, sent, _, _ = self.build({**self.ON, "mute": ["c1"]})
        what = man.handle(message_event("как активировать?"), "message")

        self.assertEqual(sent, [])
        self.assertIn("тишин", what)

    def test_what_did_not_reach_the_chat_is_not_remembered(self):
        """Иначе следующая попытка упрётся в собственное ограничение, и
        покупатель не получит ничего."""
        man, _, _, store = self.build(self.ON, broken=True)
        man.handle(message_event("как активировать?"), "message")

        self.assertEqual(manager.talks_of(store.shared()), {})
        self.assertEqual(store.saves, 0)

    def test_the_answer_is_remembered_and_saved(self):
        man, _, _, store = self.build(self.ON)
        man.handle(message_event("как активировать?"), "message")

        self.assertIn("c1", manager.talks_of(store.shared()))
        self.assertEqual(store.saves, 1)

    def test_a_broken_manager_does_not_break_the_listener(self):
        """Уведомления важнее автоответов."""
        store = FakeStore({"manager": self.ON})

        def explode(chat_id, text):
            raise RuntimeError("бум")

        man = manager.Manager(store, explode, lambda text: None)
        what = man.handle(message_event("как активировать?"), "message")

        self.assertIn("споткнулся", what)

    def test_the_item_name_reaches_the_rules(self):
        man, sent, _, _ = self.build(
            {"on": True, "rules": [{"word": "робукс",
                                    "text": "Робуксы приходят сразу."}]})
        man.handle(message_event("привет", title="80 РОБУКСОВ"), "message")

        self.assertEqual(sent, [("c1", "Робуксы приходят сразу.")])


class AlarmTextTest(unittest.TestCase):
    def test_the_complaint_itself_is_inside(self):
        said = manager.alarm_text("vasya", "80 робуксов", "обманули",
                                  "https://playerok.com/deal/d1")

        self.assertIn("vasya", said)
        self.assertIn("80 робуксов", said)
        self.assertIn("обманули", said)
        self.assertIn("deal/d1", said)

    def test_it_says_who_answers(self):
        """Иначе продавец ждёт, что бот уже ответил."""
        said = manager.alarm_text("vasya", "", "обманули")

        self.assertIn("я молчу", said)


if __name__ == "__main__":
    unittest.main()


class Order:
    def __init__(self, id, title=""):                          # noqa: A002
        self.id, self.title = id, title


class Card:
    def __init__(self, slug, title):
        self.slug, self.title = slug, title


class ChoresStore(FakeStore):
    """Хранилище с журналом выдач — тем самым, по которому считается
    отчёт за сутки."""

    def __init__(self, data=None, log=None):
        super().__init__(data)
        self.log = {"robux": list(log or [])}

    def conf(self, slug):
        return {"log": self.log.get(slug, [])}


class RemindTest(unittest.TestCase):
    """Выдача молчит только тогда, когда ей нечем выдать, — а покупатель
    всё это время ждёт."""

    def build(self, hours=6, on=True, now=10_000.0):
        store = ChoresStore({"manager": {"on": True,
                                         "remind": {"on": on, "hours": hours}}})
        told = []
        self.clock = {"now": now}
        chores = manager.Chores(store, told.append,
                                now=lambda: self.clock["now"])

        return chores, told, store

    def test_a_fresh_order_is_not_reminded_about(self):
        chores, told, _ = self.build()
        chores.tick([Order("o1", "80 робуксов")])

        self.assertEqual(told, [])

    def test_the_hanging_order_is_named(self):
        chores, told, _ = self.build(hours=6)
        chores.tick([Order("o1", "80 робуксов")])
        self.clock["now"] += 7 * 3600
        chores.tick([Order("o1", "80 робуксов")])

        self.assertEqual(len(told), 1)
        self.assertIn("80 робуксов", told[0])
        self.assertIn("o1", told[0])

    def test_it_is_said_once_and_not_every_minute(self):
        chores, told, _ = self.build(hours=6)
        chores.tick([Order("o1")])
        self.clock["now"] += 7 * 3600

        for _ in range(5):
            chores.tick([Order("o1")])

        self.assertEqual(len(told), 1)

    def test_switched_off_it_says_nothing(self):
        chores, told, _ = self.build(on=False)
        chores.tick([Order("o1")])
        self.clock["now"] += 99 * 3600
        chores.tick([Order("o1")])

        self.assertEqual(told, [])

    def test_a_silent_manager_silences_the_clock_too(self):
        store = ChoresStore({"manager": {"on": False,
                                         "remind": {"on": True, "hours": 1}}})
        told = []
        clock = {"now": 10_000.0}
        chores = manager.Chores(store, told.append, now=lambda: clock["now"])
        chores.tick([Order("o1")])
        clock["now"] += 99 * 3600
        chores.tick([Order("o1")])

        self.assertEqual(told, [])

    def test_a_delivered_order_is_forgotten(self):
        """Память живёт в файле и без уборки растёт вместе с продажами."""
        chores, _, store = self.build()
        chores.tick([Order("o1")])
        chores.tick([])

        self.assertEqual(manager.seen_of(store.shared()), {})

    def test_several_orders_come_in_one_letter(self):
        """Письмо на заказ — это рассылка, а не помощь."""
        chores, told, _ = self.build(hours=6)
        orders = [Order(f"o{n}", f"товар {n}") for n in range(3)]
        chores.tick(orders)
        self.clock["now"] += 7 * 3600
        chores.tick(orders)

        self.assertEqual(len(told), 1)
        self.assertIn("товар 2", told[0])

    def test_a_long_list_is_cut_with_a_count(self):
        chores, told, _ = self.build(hours=6)
        orders = [Order(f"o{n}") for n in range(manager.REMIND_SHOWN + 4)]
        chores.tick(orders)
        self.clock["now"] += 7 * 3600
        chores.tick(orders)

        self.assertIn("и ещё 4", told[0])


class ReportTest(unittest.TestCase):
    """Отчёт за сутки — по журналу выдач, без единого запроса к площадке."""

    def entry(self, at, paid=149.0, cost=0.9):
        import stats

        return {"state": stats.DONE, "done_at": at, "paid": paid,
                "price": cost, "currency": "USD"}

    def build(self, hour=20, on=True, log=(), now=None):
        # 15 часов по местному времени — до отчёта в 20:00.
        now = now if now is not None else self.at_hour(15)
        store = ChoresStore(
            {"manager": {"on": True, "report": {"on": on, "hour": hour,
                                                "day": ""}}},
            log=list(log))
        told = []
        self.clock = {"now": now}
        chores = manager.Chores(store, told.append,
                                cards=[Card("robux", "Robux")],
                                now=lambda: self.clock["now"])

        return chores, told, store

    def at_hour(self, hour: int) -> float:
        """Время сегодняшнего дня в местном часовом поясе."""
        import time as _time

        now = _time.time()
        parts = list(_time.localtime(now))
        parts[3], parts[4], parts[5] = hour, 0, 0

        return _time.mktime(_time.struct_time(tuple(parts)))

    def test_nothing_before_the_hour(self):
        chores, told, _ = self.build(hour=20)
        chores.tick([])

        self.assertEqual(told, [])

    def test_the_report_comes_after_the_hour(self):
        chores, told, _ = self.build(hour=20)
        self.clock["now"] = self.at_hour(21)
        chores.tick([])

        self.assertEqual(len(told), 1)
        self.assertIn("Отчёт за сутки", told[0])

    def test_it_comes_once_a_day(self):
        chores, told, _ = self.build(hour=20)
        self.clock["now"] = self.at_hour(21)
        chores.tick([])
        chores.tick([])

        self.assertEqual(len(told), 1)

    def test_the_numbers_come_from_the_journal(self):
        now = self.at_hour(21)
        chores, told, _ = self.build(
            hour=20, log=[self.entry(now - 3600), self.entry(now - 7200)])
        self.clock["now"] = now
        chores.tick([])

        self.assertIn("Выдано: 2", told[0])
        self.assertIn("298", told[0].replace(" ", " ").replace(" ", " "))

    def test_yesterday_is_not_counted(self):
        now = self.at_hour(21)
        chores, told, _ = self.build(hour=20,
                                     log=[self.entry(now - 3 * 86400)])
        self.clock["now"] = now
        chores.tick([])

        self.assertIn("Выдач не было", told[0])


class MoneyReminderTest(unittest.TestCase):
    class Money:
        def __init__(self, withdrawable):
            self.withdrawable = withdrawable

    def build(self, limit=3000, on=True):
        store = ChoresStore({"manager": {"on": True,
                                         "money": {"on": on, "from": limit}}})
        told = []
        self.clock = {"now": 10_000.0}
        chores = manager.Chores(store, told.append,
                                now=lambda: self.clock["now"])

        return chores, told

    def test_below_the_limit_it_is_silent(self):
        chores, told = self.build(limit=3000)
        chores.tick([], self.Money(500))

        self.assertEqual(told, [])

    def test_above_the_limit_it_says_so_once(self):
        chores, told = self.build(limit=3000)
        chores.tick([], self.Money(4200))
        chores.tick([], self.Money(4300))

        self.assertEqual(len(told), 1)
        self.assertIn("Накопилось", told[0])

    def test_after_a_withdrawal_it_can_say_it_again(self):
        chores, told = self.build(limit=3000)
        chores.tick([], self.Money(4200))
        chores.tick([], self.Money(0))
        chores.tick([], self.Money(5000))

        self.assertEqual(len(told), 2)

    def test_the_balance_is_asked_rarely(self):
        """Лишний запрос в каждом проходе — это запрос каждую минуту."""
        chores, _ = self.build()

        self.assertTrue(chores.wants_money())
        self.assertFalse(chores.wants_money())

        self.clock["now"] += manager.MONEY_EVERY + 1

        self.assertTrue(chores.wants_money())

    def test_switched_off_the_balance_is_not_asked_at_all(self):
        chores, _ = self.build(on=False)

        self.assertFalse(chores.wants_money())


class ChoresAreHarmlessTest(unittest.TestCase):
    def test_a_broken_clock_does_not_break_the_delivery(self):
        class Broken(FakeStore):
            def shared(self):
                raise RuntimeError("бум")

        said = manager.Chores(Broken(), lambda text: None).tick([])

        self.assertTrue(any("споткнулись" in one for one in said))

    def test_a_silent_telegram_is_not_a_crash(self):
        store = ChoresStore({"manager": {"on": True,
                                         "remind": {"on": True, "hours": 1}}})

        def explode(text):
            raise RuntimeError("телеграм молчит")

        clock = {"now": 10_000.0}
        chores = manager.Chores(store, explode, now=lambda: clock["now"])
        chores.tick([Order("o1")])
        clock["now"] += 2 * 3600

        self.assertTrue(chores.tick([Order("o1")]))
