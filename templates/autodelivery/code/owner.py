"""Связь с владельцем через Telegram и хранение куки площадки.

Куки PlayerOK протухают, и до сих пор их можно было обновить только зайдя
на сервер. Теперь бот сам пишет владельцу «куки перестали работать,
пришли новые», ждёт ответ и продолжает — без SSH.

БЕЗОПАСНОСТЬ, ЧТО ЗДЕСЬ СДЕЛАНО И ЧЕГО НЕ СДЕЛАТЬ
──────────────────────────────────────────────────
* Принимаем сообщения ТОЛЬКО от владельца: чужой chat_id молча
  игнорируется. Иначе любой, кто найдёт бота, подсунет свои куки и
  получит выдачу от вашего имени.
* Сообщение с куками бот удаляет из переписки сразу после прочтения:
  в истории Telegram оно иначе останется навсегда.
* На диск куки ложатся с правами 600 — их читает только владелец файла.
* Токен самого бота и номер владельца берутся из окружения, а не из кода.

Модуль умеет три вещи: сообщить владельцу, получить от него куки и
дождаться ответа на вопрос. Разбора команд здесь нет — кто и о чём
спрашивает, решает вызывающий, а сюда приходят только сообщения от
владельца. Чем меньше бот принимает снаружи, тем меньше у него
поверхности.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any

import requests

API = "https://api.telegram.org"

# Запас времени на саму сеть. К нему прибавляется срок, который метод
# Telegram держит ответ (см. _call).
TIMEOUT = 20

# Сколько ждать ответа владельца. Полчаса: он может спать, а бот всё это
# время не должен ни падать, ни крутить пустой цикл.
WAIT_SECONDS = 1800

# Долгий опрос: Telegram держит соединение до этого срока, если сообщений
# нет. Дешевле и быстрее, чем дёргать его в цикле.
LONG_POLL = 25


class OwnerLink:
    """Переписка с владельцем. Всё, что бот может сказать и спросить."""

    def __init__(self, bot_token: str, owner_id: str, session: Any = None):
        if not bot_token or not owner_id:
            raise ValueError("Нужны токен бота и номер владельца")

        self.bot_token = str(bot_token)
        self.owner_id = str(owner_id)
        self.session = session or requests
        self._offset: int | None = None

    def _call(self, method: str, wait: int = 0, **params) -> Any:
        """Вызов Telegram. `wait` — сколько метод сам будет держать ответ.

        Своё ожидание сети всегда длиннее: с http-таймаутом короче долгого
        опроса каждый запрос обрывался бы по нашей же вине, и сообщения
        владельца приходили бы через раз.
        """
        payload = {k: v for k, v in params.items() if v is not None}

        response = self.session.post(
            f"{API}/bot{self.bot_token}/{method}",
            json=payload, timeout=TIMEOUT + wait,
        )
        data = response.json()

        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(str(data.get("description") or data)[:200])

        result = data.get("result")

        return result if result is not None else {}

    def whoami(self) -> dict:
        """Кто этот бот по мнению Telegram.

        Нужно для самопроверки: ответ подтверждает, что токен живой и
        принадлежит именно тому боту, которого вы завели.
        """
        return self._call("getMe")

    def say(self, text: str, buttons=None) -> bool:
        """Сообщить владельцу, при желании с кнопками.

        Молчание бота дороже неудачной отправки, поэтому ошибка здесь не
        поднимается наверх.
        """
        try:
            self._call("sendMessage", chat_id=self.owner_id, text=text,
                       reply_markup=keyboard(buttons))
            return True
        except Exception:                                  # noqa: BLE001
            return False

    def ask_cookies(self, why: str, wait_seconds: float = WAIT_SECONDS) -> str:
        """Попросить новые куки и дождаться ответа.

        Возвращает строку куки или пустую строку, если владелец не ответил.
        """
        self._drain()
        self.say(
            f"{why}\n\n"
            "Пришлите куки из браузера, где вы вошли продавцом — целиком, "
            "как скопировали. Подойдёт строка «token=...», выгрузка "
            "расширения в JSON или сам токен из куки token. Сообщение я "
            "удалю сразу после прочтения."
        )

        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            messages = self._wait(deadline)

            for message in messages:
                text = str(message.get("text") or "").strip()
                cookies = normalize_cookies(text)

                if not cookies:
                    # Отвечаем раньше, чем стираем: иначе сообщение просто
                    # исчезнет, и владелец не поймёт, что было не так.
                    self.say(
                        "Это не похоже на куки. Подойдёт любое из трёх: "
                        "строка «token=...; __ddg3=...», выгрузка расширения "
                        "в JSON или сам токен из куки token — длинная строка "
                        "из трёх частей через точку."
                    )
                    # Стираем и неудачную попытку. Бот не принимает ничего,
                    # кроме куки, значит присланное было попыткой их
                    # передать — пусть неполной. Оставить её в переписке
                    # значит оставить там кусок ключа навсегда.
                    self._delete(message)
                    continue

                removed = self._delete(message)
                self.say(
                    "Куки приняты." if removed else
                    "Куки приняты, но удалить сообщение не вышло — сотрите "
                    "его сами, оно останется в истории."
                )
                return cookies

        return ""

    def _wait(self, deadline: float) -> list[dict]:
        """Сообщения от владельца, не крутя пустой цикл.

        Настоящий Telegram держит опрос LONG_POLL секунд и сам задаёт темп.
        Но если он ответит сразу, цикл без паузы выжрал бы процессор на всё
        время ожидания.
        """
        messages = self._updates()

        if not messages:
            time.sleep(max(0.0, min(1.0, deadline - time.time())))

        return messages

    def ask(self, question: str, wait_seconds: float = WAIT_SECONDS,
            buttons=None) -> dict:
        """Спросить и дождаться ответа. → сообщение целиком или {}.

        Сообщение, а не текст: в ответ может прийти фотография, а у неё
        текста нет вовсе. Нажатие кнопки приходит сюда же и выглядит как
        обычный текст — иначе разбор ответов пришлось бы держать в двух
        видах, и они бы разошлись.
        """
        self.say(question, buttons)

        return self.wait_answer(wait_seconds)

    def wait_answer(self, wait_seconds: float = WAIT_SECONDS) -> dict:
        """Дождаться следующего сообщения владельца. → сообщение или {}."""
        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            for message in self._wait(deadline):
                return message

        return {}

    def download(self, file_id: str) -> bytes:
        """Забрать файл, присланный владельцем. → байты или пусто.

        Двумя шагами: сначала у Telegram спрашивается путь, потом файл
        берётся по другому адресу — не тому, по которому идут вызовы API.
        """
        try:
            path = str((self._call("getFile", file_id=file_id) or {})
                       .get("file_path") or "")
        except Exception:                                  # noqa: BLE001
            return b""

        if not path:
            return b""

        try:
            response = self.session.get(
                f"{API}/file/bot{self.bot_token}/{path}", timeout=TIMEOUT * 3)
            return response.content or b""
        except Exception:                                  # noqa: BLE001
            return b""

    def _updates(self) -> list[dict]:
        """Новые сообщения от владельца. Чужие отбрасываются молча."""
        try:
            result = self._call(
                "getUpdates", wait=LONG_POLL, offset=self._offset,
                timeout=LONG_POLL,
                allowed_updates=["message", "callback_query"],
            )
        except Exception:                                  # noqa: BLE001
            time.sleep(3)
            return []

        messages = []

        for update in result if isinstance(result, list) else []:
            self._offset = int(update.get("update_id", 0)) + 1
            message = self._as_message(update)

            if not message:
                continue

            chat_id = str((message.get("chat") or {}).get("id") or "")

            # Единственная проверка, которая здесь по-настоящему важна.
            if chat_id == self.owner_id:
                messages.append(message)

        return messages

    def _as_message(self, update: dict) -> dict:
        """Обновление → сообщение. Нажатие кнопки выглядит как текст.

        Так весь разбор ответов остаётся один: иначе пришлось бы держать
        две ветки на каждый вопрос, и однажды они разошлись бы.
        """
        press = update.get("callback_query")

        if not press:
            return update.get("message") or {}

        # Убрать «часики» на кнопке. Не вышло — не беда, ответ уже принят.
        try:
            self._call("answerCallbackQuery",
                       callback_query_id=press.get("id"))
        except Exception:                                  # noqa: BLE001
            pass

        shown = press.get("message") or {}

        return {
            "text": str(press.get("data") or ""),
            "chat": shown.get("chat") or {},
            "message_id": shown.get("message_id"),
            "from_button": True,
        }

    def _drain(self) -> None:
        """Сбросить накопленные сообщения.

        Без этого ответом на свежий вопрос стало бы старое сообщение,
        присланное когда-то раньше, — и бот взял бы протухшие куки.
        """
        try:
            result = self._call("getUpdates", offset=-1, timeout=0)
        except Exception:                                  # noqa: BLE001
            return

        for update in result if isinstance(result, list) else []:
            self._offset = int(update.get("update_id", 0)) + 1

    def _delete(self, message: dict) -> bool:
        try:
            self._call("deleteMessage", chat_id=self.owner_id,
                       message_id=message.get("message_id"))
            return True
        except Exception:                                  # noqa: BLE001
            return False


def keyboard(buttons):
    """Кнопки → разметка Telegram, или None, если кнопок нет.

    Плоский список пар — один ряд: так просят кнопки вида «GL / RU».
    Столбик задаётся явно списком рядов — например для списка статусов,
    где подписи длинные.
    """
    if not buttons:
        return None

    # Плоский список — один ряд. Столбик задаётся явно списком рядов:
    # угадывать за вызывающего, где перенос, значит однажды угадать не так.
    first = buttons[0]
    rows = buttons if isinstance(first, list) else [list(buttons)]

    return {"inline_keyboard": [
        [{"text": str(label), "callback_data": str(data)[:64]}
         for label, data in row]
        for row in rows if row
    ]}


def normalize_cookies(text: str) -> str:
    """Строка куки из того, что прислал владелец, или пустая строка.

    Принимаем три вида, потому что их три в жизни:

    * строка `name=value; name2=value2` — как копируется из инструментов
      разработчика на компьютере;
    * JSON-выгрузка расширения вроде Cookie-Editor — так куки достают с
      телефона, где инструментов разработчика нет;
    * голый JWT — то, что лежит в самой куке `token` и что расширение
      показывает на экране крупнее всего. Продавец копирует именно его,
      и отвергать это значит спорить с очевидным.

    Второе не прихоть: у владельца может не быть компьютера вовсе, и
    требовать переделки JSON в строку руками на телефоне значит требовать
    компьютера окольным путём.

    Проверка нарочно грубая: задача — отсеять «ок» и «привет», а не
    валидировать формат. Строгая проверка отвергала бы рабочие куки при
    каждом изменении на площадке.
    """
    value = str(text or "").strip()

    if not value:
        return ""

    if _looks_like_jwt(value):
        # Библиотека разбирает строку куки в словарь, так что
        # "token=<jwt>" даёт ровно то же, что отдельный параметр token.
        return f"token={value}"

    pairs = _from_json(value)

    if pairs is not None:
        # Точку с запятой в конце не ставим: библиотека разбирает и так, а
        # лишний пустой элемент некоторым разборщикам не нравится.
        value = "; ".join(f"{name}={cookie}" for name, cookie in pairs)

    return value if "token=" in value and len(value) > 40 else ""


# Три части из букв, цифр, дефиса и подчёркивания через точку — это
# base64url, каким записывают JWT. Знака равенства в нём не бывает, и
# по его отсутствию JWT отличается от строки куки.
JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _looks_like_jwt(text: str) -> bool:
    return len(text) > 40 and bool(JWT.match(text))


def _from_json(text: str):
    """Пары имя-значение из выгрузки расширения, или None, если это не она.

    None и пустой список — разные ответы: первое значит «это не JSON,
    разбирай как строку», второе — «JSON, но пустой».
    """
    if text[0] not in "[{":
        return None

    try:
        data = json.loads(text)
    except ValueError:
        return None

    if isinstance(data, dict):
        # Простой вид {"token": "...", "__ddg3": "..."}.
        return [(str(k), str(v)) for k, v in data.items()]

    if not isinstance(data, list):
        return None

    pairs = []

    for item in data:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        cookie = item.get("value")

        if name and cookie is not None:
            pairs.append((name, str(cookie)))

    return pairs


def looks_like_cookies(text: str) -> bool:
    """Похоже ли присланное на куки — в любом из принимаемых видов."""
    return bool(normalize_cookies(text))


class CookieStore:
    """Куки площадки на диске. Права 600 — читает только владелец файла."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> str:
        try:
            with open(self.path, encoding="utf-8") as f:
                return str(json.load(f).get("cookies") or "").strip()
        except (OSError, ValueError):
            return ""

    def save(self, cookies: str) -> None:
        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")

        try:
            os.fchmod(fd, 0o600)

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"cookies": cookies, "at": time.time()}, f)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def link_from_env(session: Any = None) -> OwnerLink | None:
    """Связь с владельцем из окружения или None, если она не настроена.

    Бот без телеграма работать обязан: связь — это удобство обновления
    куки, а не условие выдачи кодов.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner = os.environ.get("TELEGRAM_OWNER_ID", "").strip()

    if not token or not owner:
        return None

    return OwnerLink(token, owner, session=session)


def cookies_now(store: CookieStore, link: OwnerLink | None,
                why: str = "", ask: bool = True) -> str:
    """Актуальные куки: с диска, из окружения или спросив владельца.

    Порядок именно такой. Присланное через телеграм лежит на диске и всегда
    свежее того, что было в окружении при выкате, — иначе после каждого
    перезапуска бот возвращался бы к протухшей строке из systemd.
    """
    saved = store.load()

    if saved:
        return saved

    from_env = os.environ.get("PLAYEROK_COOKIES", "").strip()

    if from_env:
        return from_env

    if not (ask and link):
        return ""

    return renew_cookies(store, link, why or "Куки площадки не заданы.")


def renew_cookies(store: CookieStore, link: OwnerLink, why: str,
                  wait_seconds: float = WAIT_SECONDS) -> str:
    """Спросить у владельца новые куки и сохранить их.

    Пустая строка означает, что владелец не ответил. Сохранять её нельзя:
    затёрли бы последние рабочие куки ничем.
    """
    cookies = link.ask_cookies(why, wait_seconds)

    if cookies:
        store.save(cookies)

    return cookies
