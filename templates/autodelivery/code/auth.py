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
import shutil
from typing import Any

from accounts import AccountStore
from owner import CookieStore, cookies_now, link_from_env

# Где лежат присланные куки. Рядом с состоянием выдач, а не в коде.
COOKIE_FILE = os.environ.get("PLAYEROK_COOKIE_FILE", "state/cookies.json")

# Где живут сохранённые кабинеты. Если есть хоть один — вход идёт через
# него, а не через одиночные куки: иначе переключение аккаунта ничего бы
# не меняло.
ACCOUNTS_DIR = os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts")


def user_agent_from_env() -> str:
    user_agent = os.environ.get("PLAYEROK_UA", "").strip()

    if not user_agent:
        raise SystemExit(
            "Нет PLAYEROK_UA. Нужен тот же user-agent, что у браузера, из "
            "которого взяты куки: иначе площадка сочтёт вход чужим.")

    return user_agent


def ensure_library_cert(package_dir: str = "", bundle: str = "") -> bool:
    """Положить набор корневых сертификатов туда, где библиотека его ищет.

    Заплатка чужой ошибки. playerokapi читает cacert.pem из своей папки, но
    в setup.py у неё нет ни package_data, ни include_package_data — pip
    ставит только .py, и первый же вход падает с FileNotFoundError.

    Файл этот не особенный: обычная копия набора certifi, просто более
    старого выпуска (150 корней против нынешних 137). Берём certifi — он
    приходит вместе с requests, поддерживается и не содержит просроченных
    корней.

    Возвращает True, если файл на месте или положен сейчас.
    """
    if not package_dir:
        try:
            import playerokapi
        except ImportError:
            return False

        where = getattr(playerokapi, "__file__", None)

        if not where:
            return False

        package_dir = os.path.dirname(os.path.abspath(where))

    target = os.path.join(package_dir, "cacert.pem")

    if os.path.exists(target):
        return True

    if not bundle:
        try:
            import certifi
        except ImportError:
            return False

        bundle = certifi.where()

    try:
        shutil.copyfile(bundle, target)
    except OSError:
        return False

    return True


def open_account(cookies: str, user_agent: str):
    """Аккаунт площадки по готовым кукам.

    Куки и user-agent не живут в коде: положить их туда значило бы выложить
    доступ к кабинету продавца в репозиторий.
    """
    try:
        from playerokapi.account import Account
    except ModuleNotFoundError as e:
        # Различаем два случая: библиотеки нет вовсе и она есть, но
        # разваливается при загрузке. Лечатся они по-разному, а свалить их
        # в одно «не установлена» значит отправить ставить уже стоящее.
        if (e.name or "").split(".")[0] != "playerokapi":
            raise SystemExit(
                f"Библиотека playerokapi установлена, но ей не хватает "
                f"зависимости: {e.name}. Поставьте заново:\n"
                "    python3 -m pip install --user --break-system-packages "
                "-r requirements.txt")

        raise SystemExit(
            "Не установлена библиотека playerokapi. Поставьте:\n"
            "    python3 -m pip install --user --break-system-packages "
            "-r requirements.txt")
    except ImportError as e:
        raise SystemExit(
            f"Библиотека playerokapi установлена, но не загружается: {e}\n"
            "Полную причину покажет:\n"
            "    python3 -c \"import playerokapi\"")

    if not ensure_library_cert():
        raise SystemExit(
            "Библиотеке playerokapi не хватает файла cacert.pem, и положить "
            "его не вышло. Поставьте certifi:\n"
            "    python3 -m pip install --user --break-system-packages certifi")

    # Передаём строку куки целиком: библиотека разберёт её на пары сама.
    # Отдельный параметр token ждёт только JWT, и подсунуть ему всю строку
    # значит молча остаться неавторизованным.
    return Account(cookies=cookies, user_agent=user_agent).get()


def sign_in(session: Any = None):
    """Войти в кабинет.

    Возвращает (аккаунт, хранилище куки, связь с владельцем). Связь может
    быть None — бот без телеграма работать обязан: телеграм здесь способ
    обновить куки, а не условие выдачи кодов.

    Если сохранён хотя бы один кабинет, входим в текущий из них. Иначе —
    прежним путём, одиночными куками: так продолжают работать установки,
    где кабинет один и ничего переключать не нужно.

    `session` — чем ходить в Telegram. По умолчанию requests; параметр нужен
    тестам, чтобы проверить вход целиком, не выходя в сеть.
    """
    link = link_from_env(session)
    saved = AccountStore(ACCOUNTS_DIR).current()

    if saved is not None:
        return open_account(saved.cookies,
                            saved.user_agent or user_agent_from_env()), \
            CookieStore(COOKIE_FILE), link

    user_agent = user_agent_from_env()
    store = CookieStore(COOKIE_FILE)
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
