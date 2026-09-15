"""Вход в кабинет площадки: куки, их источник и обновление.

Куки PlayerOK протухают. Раньше это означало «зайди на сервер и поправь
переменную окружения»; теперь бот просит новые у владельца в телеграме и
кладёт их на диск, откуда берёт при следующем запуске.

Порядок источников — от свежего к старому:

    диск (присланное в телеграм) → окружение → спросить владельца

Именно так, а не наоборот: строка в systemd написана один раз при выкате, а
присланная в телеграм — последняя, про которую известно, что она работала.
Иначе каждый перезапуск возвращал бы бота к протухшим кукам.

Модуль отдельный от watch.py нарочно: watch.py — это скрипт, он настраивает
журнал под себя, и импортировать его из боевого цикла значило бы менять
боевому циклу формат журнала.
"""
from __future__ import annotations

import os
from typing import Any

from owner import CookieStore, cookies_now, link_from_env

# Где лежат присланные куки. Рядом с состоянием выдач, а не в коде.
COOKIE_FILE = os.environ.get("PLAYEROK_COOKIE_FILE", "state/cookies.json")


def user_agent_from_env() -> str:
    user_agent = os.environ.get("PLAYEROK_UA", "").strip()

    if not user_agent:
        raise SystemExit(
            "Нет PLAYEROK_UA. Нужен тот же user-agent, что у браузера, из "
            "которого взяты куки: иначе площадка сочтёт вход чужим.")

    return user_agent


def open_account(cookies: str, user_agent: str):
    """Аккаунт площадки по готовым кукам.

    Куки и user-agent не живут в коде: положить их туда значило бы выложить
    доступ к кабинету продавца в репозиторий.
    """
    try:
        from playerokapi.account import Account
    except ImportError:
        raise SystemExit(
            "Не установлена библиотека playerokapi. "
            "Поставьте: pip install -r requirements.txt")

    # Передаём строку куки целиком: библиотека разберёт её на пары сама.
    # Отдельный параметр token ждёт только JWT, и подсунуть ему всю строку
    # значит молча остаться неавторизованным.
    return Account(cookies=cookies, user_agent=user_agent).get()


def sign_in(session: Any = None):
    """Войти в кабинет.

    Возвращает (аккаунт, хранилище куки, связь с владельцем). Связь может
    быть None — бот без телеграма работать обязан: телеграм здесь способ
    обновить куки, а не условие выдачи кодов.

    `session` — чем ходить в Telegram. По умолчанию requests; параметр нужен
    тестам, чтобы проверить вход целиком, не выходя в сеть.
    """
    user_agent = user_agent_from_env()
    store = CookieStore(COOKIE_FILE)
    link = link_from_env(session)
    cookies = cookies_now(store, link,
                          why="Запускаюсь, но куки площадки не заданы.")

    if not cookies:
        raise SystemExit(
            "Нет куки площадки. Задайте PLAYEROK_COOKIES — это строка куки "
            "целиком из браузера, где вы вошли продавцом, со всем "
            "содержимым, включая token и куку защиты от DDoS-Guard. Либо "
            "настройте TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID, и бот "
            "попросит их у вас сам.")

    return open_account(cookies, user_agent), store, link
