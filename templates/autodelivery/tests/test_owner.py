"""Тесты связи с владельцем. Сети не требуют — Telegram поддельный.

Проверяется то, что здесь стоит доступа к кабинету: чужие сообщения не
принимаются, куки не остаются в переписке, старое сообщение не выдаётся за
ответ, и на диск строка ложится закрытой от посторонних.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import owner                                                    # noqa: E402
from owner import (CookieStore, OwnerLink, cookies_now,          # noqa: E402
                   link_from_env, looks_like_cookies, renew_cookies)

OWNER = "555"
STRANGER = "666"

# Строка куки: важно, что длинная и с token= — по этим двум признакам
# бот и отличает её от «ок».
COOKIES = "__ddg3=abc; token=" + "j" * 60 + "; other=1"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeTelegram:
    """Поддельный Telegram: отдаёт заранее разложенные пачки обновлений."""

    def __init__(self, batches=None, fail_delete=False):
        self.batches = list(batches or [])
        self.fail_delete = fail_delete
        self.sent = []
        self.deleted = []
        self.calls = []

    def post(self, url, json=None, timeout=None):        # noqa: A002
        method = url.rsplit("/", 1)[-1]
        params = json or {}
        self.calls.append((method, params, timeout))

        if method == "sendMessage":
            self.sent.append(str(params.get("text") or ""))
            return FakeResponse({"ok": True, "result": {"message_id": 1}})

        if method == "deleteMessage":
            if self.fail_delete:
                return FakeResponse({"ok": False,
                                     "description": "message can't be deleted"})

            self.deleted.append(params.get("message_id"))
            return FakeResponse({"ok": True, "result": True})

        if method == "getUpdates":
            batch = self.batches.pop(0) if self.batches else []
            return FakeResponse({"ok": True, "result": batch})

        return FakeResponse({"ok": True, "result": {}})


def update(update_id, text, chat_id=OWNER, message_id=None):
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id if message_id is not None else update_id,
            "chat": {"id": int(chat_id)},
            "text": text,
        },
    }


def link(telegram):
    return OwnerLink("bot-token", OWNER, session=telegram)


class OwnerOnlyTest(unittest.TestCase):
    """Самая дорогая проверка: чужие куки — это выдача от вашего имени."""

    def test_message_from_a_stranger_is_ignored(self):
        telegram = FakeTelegram([
            [],                                        # _drain: пусто
            [update(1, COOKIES, chat_id=STRANGER)],    # чужой — не наш
            [update(2, COOKIES)],                      # владелец
        ])

        self.assertEqual(link(telegram).ask_cookies("проверка"), COOKIES)

    def test_stranger_alone_yields_nothing(self):
        telegram = FakeTelegram([[], [update(1, COOKIES, chat_id=STRANGER)]])

        self.assertEqual(link(telegram).ask_cookies("проверка", wait_seconds=0.2), "")

    def test_chat_id_compares_as_text_not_as_number(self):
        """Telegram отдаёт число, в окружении — строка. Разные типы."""
        telegram = FakeTelegram([[], [update(1, COOKIES)]])

        self.assertEqual(link(telegram).ask_cookies("проверка"), COOKIES)


class CookiesAreNotLeftInChatTest(unittest.TestCase):
    def test_message_with_cookies_is_deleted(self):
        telegram = FakeTelegram([[], [update(7, COOKIES, message_id=77)]])
        link(telegram).ask_cookies("проверка")

        self.assertEqual(telegram.deleted, [77])

    def test_owner_is_warned_when_deletion_failed(self):
        """Не удалилось — владелец должен узнать, а не думать, что стёрто."""
        telegram = FakeTelegram([[], [update(7, COOKIES)]], fail_delete=True)
        got = link(telegram).ask_cookies("проверка")

        self.assertEqual(got, COOKIES)
        self.assertIn("сотрите", telegram.sent[-1].lower())

    def test_accepted_quietly_confirms_when_deleted(self):
        telegram = FakeTelegram([[], [update(7, COOKIES)]])
        link(telegram).ask_cookies("проверка")

        self.assertIn("приняты", telegram.sent[-1].lower())


class StaleAnswerTest(unittest.TestCase):
    def test_old_messages_are_dropped_before_asking(self):
        """Без сброса ответом на свежий вопрос стало бы старое сообщение."""
        telegram = FakeTelegram([
            [update(41, "куки из прошлого месяца")],   # _drain съедает это
            [update(42, COOKIES)],
        ])
        link(telegram).ask_cookies("проверка")

        drains = [c for c in telegram.calls if c[0] == "getUpdates"]

        self.assertEqual(drains[0][1].get("offset"), -1)
        # После сброса опрос продолжается с номера, следующего за старым.
        self.assertEqual(drains[1][1].get("offset"), 42)

    def test_offset_moves_past_every_seen_update(self):
        telegram = FakeTelegram([[], [update(5, "привет")], [update(9, COOKIES)]])
        link(telegram).ask_cookies("проверка")

        offsets = [c[1].get("offset") for c in telegram.calls
                   if c[0] == "getUpdates"]

        self.assertEqual(offsets[-1], 6)


class ChatterTest(unittest.TestCase):
    def test_greeting_is_not_taken_for_cookies(self):
        self.assertFalse(looks_like_cookies("ок"))
        self.assertFalse(looks_like_cookies("привет"))
        self.assertFalse(looks_like_cookies(""))
        self.assertFalse(looks_like_cookies(None))

    def test_long_text_without_token_is_refused(self):
        self.assertFalse(looks_like_cookies("а" * 200))

    def test_short_string_with_token_is_refused(self):
        self.assertFalse(looks_like_cookies("token=1"))

    def test_real_looking_cookies_pass(self):
        self.assertTrue(looks_like_cookies(COOKIES))
        self.assertTrue(looks_like_cookies("  " + COOKIES + "  "))

    def test_owner_is_told_what_is_wrong_and_asked_again(self):
        telegram = FakeTelegram([[], [update(1, "ок")], [update(2, COOKIES)]])
        got = link(telegram).ask_cookies("проверка")

        self.assertEqual(got, COOKIES)
        self.assertTrue(any("token=" in m for m in telegram.sent))


class NetworkTest(unittest.TestCase):
    def test_broken_telegram_does_not_kill_the_bot(self):
        """Молчание телеграма — не повод падать: коды важнее переписки."""
        class Broken:
            def post(self, *a, **kw):
                raise OSError("сеть недоступна")

        self.assertFalse(link(Broken()).say("что-нибудь"))
        self.assertEqual(link(Broken()).ask_cookies("проверка", wait_seconds=0.2), "")

    def test_long_poll_waits_longer_than_telegram_holds_the_answer(self):
        """Иначе каждый опрос обрывался бы по нашей же вине."""
        telegram = FakeTelegram([[], [update(1, COOKIES)]])
        link(telegram).ask_cookies("проверка")

        polls = [c for c in telegram.calls
                 if c[0] == "getUpdates" and c[1].get("timeout")]

        for _, params, timeout in polls:
            self.assertGreater(timeout, params["timeout"])

    def test_empty_credentials_are_refused_at_construction(self):
        with self.assertRaises(ValueError):
            OwnerLink("", OWNER)

        with self.assertRaises(ValueError):
            OwnerLink("token", "")


class WhoamiTest(unittest.TestCase):
    """Самопроверка: токен живой и бот тот самый."""

    def test_bot_identity_is_returned(self):
        class Telegram(FakeTelegram):
            def post(self, url, json=None, timeout=None):     # noqa: A002
                if url.endswith("getMe"):
                    return FakeResponse(
                        {"ok": True, "result": {"username": "kinetix_bot"}})

                return super().post(url, json=json, timeout=timeout)

        self.assertEqual(link(Telegram()).whoami()["username"], "kinetix_bot")

    def test_refused_token_is_raised_not_swallowed(self):
        """В отличие от say(), здесь молчать нельзя: это и есть проверка."""
        class Telegram(FakeTelegram):
            def post(self, url, json=None, timeout=None):     # noqa: A002
                return FakeResponse({"ok": False, "description": "Unauthorized"})

        with self.assertRaises(RuntimeError) as failure:
            link(Telegram()).whoami()

        self.assertIn("Unauthorized", str(failure.exception))


class CookieStoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, "state", "cookies.json")
        self.store = CookieStore(self.path)

    def test_saved_cookies_are_read_back(self):
        self.store.save(COOKIES)

        self.assertEqual(self.store.load(), COOKIES)

    def test_file_is_closed_to_others(self):
        """Куки — это доступ к кабинету: соседям по серверу их не видно."""
        self.store.save(COOKIES)

        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(self.store.load(), "")

    def test_broken_file_is_not_an_error(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{не json")

        self.assertEqual(self.store.load(), "")

    def test_rewrite_replaces_the_previous_value(self):
        self.store.save("token=" + "a" * 60)
        self.store.save(COOKIES)

        self.assertEqual(self.store.load(), COOKIES)

    def test_time_of_receipt_is_kept(self):
        """По нему видно, когда куки обновляли в последний раз."""
        self.store.save(COOKIES)

        with open(self.path, encoding="utf-8") as f:
            self.assertIn("at", json.load(f))

    def test_no_leftovers_after_saving(self):
        self.store.save(COOKIES)
        folder = os.path.dirname(self.path)

        self.assertEqual(os.listdir(folder), ["cookies.json"])


class CookiesNowTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = CookieStore(os.path.join(self.folder, "cookies.json"))
        self.saved_env = os.environ.get("PLAYEROK_COOKIES")
        os.environ.pop("PLAYEROK_COOKIES", None)

    def tearDown(self):
        if self.saved_env is None:
            os.environ.pop("PLAYEROK_COOKIES", None)
        else:
            os.environ["PLAYEROK_COOKIES"] = self.saved_env

    def test_disk_wins_over_environment(self):
        """Присланное в телеграм свежее строки из systemd, иначе после
        перезапуска бот вернулся бы к протухшим кукам."""
        os.environ["PLAYEROK_COOKIES"] = "token=" + "e" * 60
        self.store.save(COOKIES)

        self.assertEqual(cookies_now(self.store, None), COOKIES)

    def test_environment_is_used_on_first_run(self):
        os.environ["PLAYEROK_COOKIES"] = COOKIES

        self.assertEqual(cookies_now(self.store, None), COOKIES)

    def test_owner_is_asked_when_nothing_is_known(self):
        telegram = FakeTelegram([[], [update(1, COOKIES)]])

        self.assertEqual(cookies_now(self.store, link(telegram)), COOKIES)
        self.assertEqual(self.store.load(), COOKIES)

    def test_without_a_link_absence_is_just_empty(self):
        self.assertEqual(cookies_now(self.store, None), "")

    def test_silence_does_not_erase_the_last_working_cookies(self):
        """Владелец не ответил — на диске должно остаться прежнее."""
        self.store.save(COOKIES)
        telegram = FakeTelegram([[], []])
        got = renew_cookies(self.store, link(telegram), "истекли",
                            wait_seconds=0.2)

        self.assertEqual(got, "")
        self.assertEqual(self.store.load(), COOKIES)

    def test_renewal_overwrites_the_stale_value(self):
        self.store.save("token=" + "o" * 60)
        telegram = FakeTelegram([[], [update(1, COOKIES)]])

        self.assertEqual(renew_cookies(self.store, link(telegram), "истекли"),
                         COOKIES)
        self.assertEqual(self.store.load(), COOKIES)


class LinkFromEnvTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k)
                      for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_absent_settings_are_not_an_error(self):
        """Бот без телеграма обязан работать: связь — удобство, не условие."""
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_OWNER_ID", None)

        self.assertIsNone(link_from_env())

    def test_half_filled_settings_give_nothing(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "t"
        os.environ.pop("TELEGRAM_OWNER_ID", None)

        self.assertIsNone(link_from_env())

    def test_both_settings_give_a_link(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "t"
        os.environ["TELEGRAM_OWNER_ID"] = OWNER

        self.assertIsInstance(link_from_env(), OwnerLink)


if __name__ == "__main__":
    unittest.main()
