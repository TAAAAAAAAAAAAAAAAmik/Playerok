"""Сквозной тест уведомлений: событие площадки → сообщение в телеграм.

Ни сети, ни площадки: подставляются слушатель и связь с владельцем. Зато
проверяется весь путь целиком — то, что до сих пор не проверялось ничем, и
именно поэтому уведомления не работали месяцами.

Классы событий здесь — двойники настоящих: имена и поля списаны с
`playerokapi/listener/events.py` и `types.py`. Разбор опирается на имя
класса, так что двойник для него неотличим от настоящего.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notices                                                  # noqa: E402


# ---------------------------------------------------------------------------
# Двойники площадки
# ---------------------------------------------------------------------------

class ChatTypes:
    """Настоящие значения ChatTypes: PM 0, уведомления 1, поддержка 2."""

    PM = type("E", (), {"name": "PM", "value": 0})()
    NOTIFICATIONS = type("E", (), {"name": "NOTIFICATIONS", "value": 1})()
    SUPPORT = type("E", (), {"name": "SUPPORT", "value": 2})()


class User:
    def __init__(self, id="", username=""):                # noqa: A002
        self.id, self.username = id, username


class Item:
    def __init__(self, name="", user=None):
        self.name, self.user = name, user


class Review:
    def __init__(self, text="", rating=0):
        self.text, self.rating = text, rating


class Deal:
    def __init__(self, id="", user=None, item=None, review=None,        # noqa
                 status_description=None):
        self.id, self.user, self.item = id, user, item
        self.review = review
        self.status_description = status_description


class Message:
    def __init__(self, id="", text="", user=None, images=None):         # noqa
        self.id, self.text, self.user = id, text, user
        self.images = images or []


class Chat:
    def __init__(self, id="", type=None):                  # noqa: A002
        self.id, self.type = id, type


def _event(name, **fields):
    """Событие с именем, как у настоящего класса библиотеки."""
    return type(name, (), fields)()


def message_event(message, chat):
    return _event("NewMessageEvent", message=message, chat=chat)


def deal_event(name, deal, chat):
    return _event(name, deal=deal, chat=chat)


# ---------------------------------------------------------------------------
# Двойники бота
# ---------------------------------------------------------------------------

class FakeLink:
    """Связь с владельцем. Может «ломаться» — это тоже проверяется."""

    def __init__(self, broken_until=0):
        self.sent = []
        self.broken_until = broken_until

    def say(self, text, buttons=None):
        if len(self.sent) < self.broken_until:
            self.sent.append(None)      # попытка была, но не ушла
            return False

        self.sent.append(text)
        return True


class FakeListener:
    """Слушатель площадки: отдаёт заготовленные события и обрывается."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def listen(self, *args, **kwargs):
        self.calls += 1

        if not self.rounds:
            raise StopTest()

        for event in self.rounds.pop(0):
            yield event

        raise ConnectionError("связь оборвалась")


class StopTest(BaseException):
    """Кончились заготовленные круги — пора выходить из вечного цикла."""


ME = "me-1"
BUYER = User(id="u-9", username="vasya")
SELLER = User(id=ME, username="Tamik791021")
SUPPORT = User(id="s-1", username="Anna")
ITEM = Item(name="1000 Robux", user=SELLER)
PM = Chat(id="c1", type=ChatTypes.PM)
SUP = Chat(id="c2", type=ChatTypes.SUPPORT)
NOTIF = Chat(id="c3", type=ChatTypes.NOTIFICATIONS)


def run(rounds, link=None, seen_path="", support_id="", system_id=""):
    """Прогнать цикл уведомлений до конца заготовленных кругов."""
    import notify_bot

    link = link or FakeLink()
    listener = FakeListener(rounds)
    seen = notices.Seen(seen_path)

    try:
        notify_bot.pump(listener, link, seen, ME, notices.DEFAULT,
                        sleeper=lambda seconds: None,
                        support_id=support_id, system_id=system_id)
    except StopTest:
        pass

    return link, seen


class EveryKindArrivesTest(unittest.TestCase):
    """Всё, что продавец просил: покупки, чаты, отзывы, проблемы,
    поддержка."""

    def setUp(self):
        self.deal = Deal(id="d1", user=BUYER, item=ITEM,
                         status_description="код не подошёл",
                         review=Review(text="быстро, спасибо", rating=5))

    def sent(self, event):
        link, _ = run([[event]])

        return [t for t in link.sent if t]

    def test_a_purchase_arrives(self):
        got = self.sent(deal_event("NewDealEvent", self.deal, PM))

        self.assertEqual(len(got), 1)
        self.assertIn("Покупка", got[0])
        self.assertIn("1000 Robux", got[0])
        self.assertIn("vasya", got[0])

    def test_a_chat_message_arrives(self):
        got = self.sent(message_event(
            Message(id="m1", text="Где мой код?", user=BUYER), PM))

        self.assertIn("Где мой код?", got[0])
        self.assertIn("vasya", got[0])

    def test_a_support_message_is_marked_as_support(self):
        """Письмо поддержки нельзя путать с вопросом покупателя: по нему
        идут споры, и отвечать на него надо иначе."""
        got = self.sent(message_event(
            Message(id="m2", text="Разбираемся по спору", user=SUPPORT), SUP))

        self.assertIn("Поддержка", got[0])
        self.assertIn("Разбираемся по спору", got[0])

    def test_a_platform_notice_is_marked_as_the_platform(self):
        got = self.sent(message_event(
            Message(id="m3", text="Товар одобрен", user=None), NOTIF))

        self.assertIn("Площадка", got[0])
        self.assertIn("Товар одобрен", got[0])

    def test_a_review_shows_its_rating_and_text(self):
        """Иначе уведомление сообщает лишь «отзыв есть», а идти в кабинет
        ради одной звезды обидно."""
        got = self.sent(deal_event("NewReviewEvent", self.deal, PM))

        self.assertIn("⭐⭐⭐⭐⭐", got[0])
        self.assertIn("быстро, спасибо", got[0])

    def test_a_problem_shows_what_is_wrong(self):
        got = self.sent(deal_event("DealHasProblemEvent", self.deal, PM))

        self.assertIn("Проблема", got[0])
        self.assertIn("код не подошёл", got[0])

    def test_a_resolved_problem_arrives(self):
        got = self.sent(deal_event("DealProblemResolvedEvent", self.deal, PM))

        self.assertIn("решена", got[0])

    def test_a_confirmed_order_arrives(self):
        got = self.sent(deal_event("DealConfirmedEvent", self.deal, PM))

        self.assertIn("подтверждён", got[0])

    def test_a_refund_arrives(self):
        got = self.sent(deal_event("DealRolledBackEvent", self.deal, PM))

        self.assertIn("Возврат", got[0])


class SupportByIdTest(unittest.TestCase):
    """Поддержка узнаётся и по номеру чата, а не только по типу.

    Тип у разных версий библиотеки зовётся по-разному, а номер площадка
    отдаёт вместе с кабинетом, и он всегда один и тот же.
    """

    def test_a_plain_looking_chat_with_the_support_id_is_support(self):
        plain = Chat(id="sup-777", type=ChatTypes.PM)
        link, _ = run([[message_event(
            Message(id="m1", text="Разбираемся", user=SUPPORT), plain)]],
            support_id="sup-777")

        self.assertIn("Поддержка", [t for t in link.sent if t][0])

    def test_a_plain_looking_chat_with_the_system_id_is_the_platform(self):
        plain = Chat(id="sys-777", type=ChatTypes.PM)
        link, _ = run([[message_event(
            Message(id="m1", text="Товар одобрен", user=None), plain)]],
            system_id="sys-777")

        self.assertIn("Площадка", [t for t in link.sent if t][0])

    def test_an_ordinary_chat_stays_ordinary(self):
        link, _ = run([[message_event(
            Message(id="m1", text="Где код?", user=BUYER), PM)]],
            support_id="sup-777", system_id="sys-777")
        got = [t for t in link.sent if t][0]

        self.assertNotIn("Поддержка", got)
        self.assertIn("vasya", got)


class SilenceTest(unittest.TestCase):
    """О чём бот молчать обязан."""

    def sent(self, event):
        link, _ = run([[event]])

        return [t for t in link.sent if t]

    def test_our_own_reply_is_not_echoed_back(self):
        self.assertEqual(self.sent(message_event(
            Message(id="m1", text="Сейчас выдам", user=SELLER), PM)), [])

    def test_a_system_marker_is_not_forwarded(self):
        """«{{ITEM_PAID}}» — метка площадки, из которой библиотека делает
        событие о покупке. Дошла текстом — значит сделку прочитать не
        вышло; переслать её владельцу значит прислать ему абракадабру."""
        for marker in ("{{ITEM_PAID}}", "{{ITEM_SENT}}",
                       "{{DEAL_CONFIRMED}}", "{{DEAL_HAS_PROBLEM}}",
                       "{{DEAL_PROBLEM_RESOLVED}}", "{{DEAL_ROLLED_BACK}}"):
            self.assertEqual(self.sent(message_event(
                Message(id="m", text=marker, user=BUYER), PM)), [], marker)

    def test_a_found_chat_is_not_an_event(self):
        """При запуске площадка отдаёт два десятка чатов подряд. Сообщить
        о каждом — значит утопить настоящие уведомления."""
        self.assertEqual(self.sent(_event("ChatInitializedEvent", chat=PM)),
                         [])

    def test_our_own_purchase_elsewhere_is_not_a_sale(self):
        mine = Deal(id="d9", user=SELLER,
                    item=Item(name="чужой товар", user=BUYER))

        self.assertEqual(self.sent(deal_event("NewDealEvent", mine, PM)), [])

    def test_a_sale_is_never_swallowed_by_the_self_check(self):
        """Если площадка однажды положит в `user` продавца, а не
        покупателя, строгая проверка проглотила бы ВСЕ продажи — и это
        выглядело бы как «уведомления не приходят», без жалоб в журнале."""
        odd = Deal(id="d8", user=SELLER, item=ITEM)

        self.assertEqual(len(self.sent(deal_event("NewDealEvent", odd, PM))),
                         1)


class NoDuplicatesTest(unittest.TestCase):
    """Обрыв связи не должен превращаться в поток повторов."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "seen.json")
        self.deal = Deal(id="d1", user=BUYER, item=ITEM)

    def test_the_same_event_twice_is_sent_once(self):
        event = deal_event("NewDealEvent", self.deal, PM)
        link, _ = run([[event], [event]], seen_path=self.path)

        self.assertEqual(len([t for t in link.sent if t]), 1)

    def test_memory_survives_a_restart(self):
        event = deal_event("NewDealEvent", self.deal, PM)
        run([[event]], seen_path=self.path)
        link, _ = run([[event]], seen_path=self.path)

        self.assertEqual([t for t in link.sent if t], [])

    def test_different_events_on_one_deal_all_arrive(self):
        """Одна сделка проходит покупку, подтверждение и отзыв — это три
        разных события, а не повтор."""
        events = [deal_event(name, self.deal, PM) for name in
                  ("NewDealEvent", "DealConfirmedEvent", "NewReviewEvent")]
        link, _ = run([events], seen_path=self.path)

        self.assertEqual(len([t for t in link.sent if t]), 3)


class DeliveryTest(unittest.TestCase):
    """Сбой телеграма не должен терять уведомление насовсем."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "seen.json")
        self.deal = Deal(id="d1", user=BUYER, item=ITEM)

    def test_a_failed_send_is_retried_on_the_next_round(self):
        """Помечать показанным ДО отправки — значит терять уведомление
        насовсем, если телеграм в этот миг не ответил."""
        event = deal_event("NewDealEvent", self.deal, PM)
        link = FakeLink(broken_until=1)
        run([[event], [event]], link=link, seen_path=self.path)
        got = [t for t in link.sent if t]

        self.assertEqual(len(got), 1)
        self.assertIn("Покупка", got[0])

    def test_what_we_stay_silent_about_is_remembered_anyway(self):
        """Иначе бот разбирал бы одно и то же при каждом обрыве."""
        event = message_event(
            Message(id="m1", text="Сейчас выдам", user=SELLER), PM)
        _, seen = run([[event]], seen_path=self.path)

        self.assertFalse(seen.is_new(event))


class DeliveryWarningTest(unittest.TestCase):
    """Уведомления живы, выдача мертва — самый дорогой случай.

    Продавцу приходит «💰 Покупка», покупатель пишет «а где промокод», а
    кода нет и не будет: сказать об этом должен был бы тот, кто не
    запущен.
    """

    def setUp(self):
        import notify_bot

        self.bot = notify_bot
        self.live = notify_bot.alive.delivery_running
        notify_bot._warned["at"] = 0.0

    def tearDown(self):
        self.bot.alive.delivery_running = self.live
        self.bot._warned["at"] = 0.0

    def dead(self):
        self.bot.alive.delivery_running = lambda: False

    def test_a_purchase_warns_when_delivery_is_dead(self):
        self.dead()
        got = self.bot.delivery_warning("💰 Покупка: «товар»", now=1000)

        self.assertIn("не запущена", got)
        self.assertIn("run_bot.sh", got)

    def test_a_live_delivery_says_nothing(self):
        self.bot.alive.delivery_running = lambda: True

        self.assertEqual(self.bot.delivery_warning("💰 Покупка", now=1000), "")

    def test_an_uncheckable_process_says_nothing(self):
        """Пугать догадкой не будем: pgrep может отсутствовать вовсе."""
        self.bot.alive.delivery_running = lambda: None

        self.assertEqual(self.bot.delivery_warning("💰 Покупка", now=1000), "")

    def test_only_purchases_are_warned_about(self):
        """Сообщение в чате выдачи не ждёт."""
        self.dead()

        self.assertEqual(self.bot.delivery_warning("💬 vasya: привет",
                                                   now=1000), "")

    def test_it_is_not_repeated_on_every_purchase(self):
        """Десять одинаковых предупреждений — шум, в котором потеряется
        сама покупка."""
        self.dead()
        self.bot.delivery_warning("💰 Покупка", now=1000)

        self.assertEqual(self.bot.delivery_warning("💰 Покупка", now=1100), "")

    def test_it_comes_back_later(self):
        self.dead()
        self.bot.delivery_warning("💰 Покупка", now=1000)
        later = self.bot.delivery_warning("💰 Покупка",
                                          now=1000 + self.bot.WARN_EVERY + 1)

        self.assertIn("не запущена", later)


class ReconnectTest(unittest.TestCase):
    """Обрыв — обычное дело, и он не должен останавливать бота."""

    def test_the_loop_survives_a_broken_connection(self):
        deal = Deal(id="d1", user=BUYER, item=ITEM)
        first = deal_event("NewDealEvent", deal, PM)
        second = deal_event("NewDealEvent",
                            Deal(id="d2", user=BUYER, item=ITEM), PM)
        link, _ = run([[first], [second]])

        self.assertEqual(len([t for t in link.sent if t]), 2)


if __name__ == "__main__":
    unittest.main()
