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

Чего здесь нет намеренно: приёма любых команд. Этот модуль умеет ровно
две вещи — сообщить владельцу и получить от него куки. Чем меньше бот
принимает снаружи, тем меньше у него поверхности.
"""
from __future__ import annotations

import json
import os
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

    def say(self, text: str) -> bool:
        """Сообщить владельцу. Молчание бота дороже неудачной отправки,
        поэтому ошибка здесь не поднимается наверх."""
        try:
            self._call("sendMessage", chat_id=self.owner_id, text=text)
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
            "Пришлите строку куки из браузера, где вы вошли продавцом — "
            "целиком, как скопировали. Сообщение я удалю сразу после "
            "прочтения."
        )

        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            messages = self._updates()

            if not messages:
                # Настоящий Telegram держит опрос LONG_POLL секунд и сам
                # задаёт темп. Но если он ответит сразу — пустой цикл
                # выжрал бы процессор на все полчаса ожидания.
                time.sleep(max(0.0, min(1.0, deadline - time.time())))
                continue

            for message in messages:
                text = str(message.get("text") or "").strip()

                if not looks_like_cookies(text):
                    self.say(
                        "Это не похоже на куки: в строке должно быть "
                        "«token=». Пришлите её целиком."
                    )
                    continue

                # Удаляем до того, как что-то ответим: если удаление не
                # выйдет, владелец хотя бы увидит предупреждение рядом.
                removed = self._delete(message)
                self.say(
                    "Куки приняты." if removed else
                    "Куки приняты, но удалить сообщение не вышло — сотрите "
                    "его сами, оно останется в истории."
                )
                return text

        return ""

    def _updates(self) -> list[dict]:
        """Новые сообщения от владельца. Чужие отбрасываются молча."""
        try:
            result = self._call(
                "getUpdates", wait=LONG_POLL, offset=self._offset,
                timeout=LONG_POLL, allowed_updates=["message"],
            )
        except Exception:                                  # noqa: BLE001
            time.sleep(3)
            return []

        messages = []

        for update in result if isinstance(result, list) else []:
            self._offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id") or "")

            # Единственная проверка, которая здесь по-настоящему важна.
            if chat_id == self.owner_id:
                messages.append(message)

        return messages

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


def looks_like_cookies(text: str) -> bool:
    """Похоже ли это на строку куки.

    Проверка нарочно грубая: задача — отсеять «ок» и «привет», а не
    валидировать формат. Строгая проверка отвергала бы рабочие куки при
    каждом изменении на площадке.
    """
    value = str(text or "").strip()

    return "token=" in value and len(value) > 40


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
