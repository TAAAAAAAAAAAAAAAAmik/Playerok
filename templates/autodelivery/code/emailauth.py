"""Вход по коду на почту: получить сессию, не открывая браузер.

Зачем. До сих пор куки доставали расширением из браузера — на телефоне это
мучение, а при входе во второй кабинет площадка гасит сессию первого.
Здесь бот получает сессию сам: просит код на почту и меняет его на куки.

ОТКУДА ВЗЯТ ПУТЬ. Из открытого SDK th1ks/playerok-api. Официальной
документации у площадки нет, так что это чужой реверс-инжиниринг — лучше
догадок, но хуже проверки руками:

    POST /auth/send-otp     {"email": "..."}
    POST /auth/confirm-otp  {"email": "...", "otpCode": "123456"}

Сессия приходит в заголовке Set-Cookie ответа на второй запрос.

ЧЕГО ЗДЕСЬ НЕТ. Двухфакторной проверки. Площадка сообщает, что она нужна
(requiresTwoFactor), но как её пройти — неизвестно: в SDK этого нет тоже.
Если второй фактор включён, вход по коду не завершится, и об этом надо
сказать прямо, а не оставлять человека гадать.

ПРО USER-AGENT. Раз сессию выдаём мы сами, то и user-agent наш: площадка
сверяет его с тем, при котором сессия выдана. Поэтому один и тот же и при
входе, и при работе — иначе вход сочтут чужим.
"""
from __future__ import annotations

import json
import re

BASE = "https://bff.playerok.com/rest-api/public"

TIMEOUT = 30

# Проверка нарочно грубая: задача — отсеять опечатку вроде «почта» или
# пустой строки, а не спорить с почтовыми стандартами.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE = re.compile(r"^\d{6}$")

# Какие куки нас интересуют. Остальное площадка ставит для своих нужд, и
# тащить их в кабинет незачем.
WANTED = ("token", "__ddg1_", "__ddg2_", "__ddg3", "__ddg5_", "__ddg8_",
          "__ddg9_", "__ddg10_")


def valid_email(value: str) -> bool:
    return bool(EMAIL.match(str(value or "").strip()))


def valid_code(value: str) -> bool:
    return bool(CODE.match(str(value or "").strip()))


def _headers(user_agent: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://playerok.com",
        "Referer": "https://playerok.com/",
        "User-Agent": user_agent,
    }


def _explain(response) -> str:
    """Отказ площадки — человеческой фразой."""
    code = getattr(response, "status_code", 0)

    if code == 429:
        wait = str(getattr(response, "headers", {}).get("Retry-After") or "")

        return ("площадка просит сбавить темп"
                + (f", подождите {wait} с" if wait else ", подождите минуту"))

    if code in (400, 422):
        return "площадка не приняла запрос: проверьте почту"

    if code in (401, 403):
        return "код неверный или уже истёк"

    if code == 404:
        return "аккаунта с такой почтой нет"

    body = str(getattr(response, "text", "") or "")[:200]

    return f"площадка ответила {code}" + (f": {body}" if body else "")


def _json(response):
    """Тело ответа или None, если это не JSON.

    Не-JSON здесь обычно означает страницу защиты от ботов: она приходит
    вместо ответа и выглядит как поломка неизвестно чего.
    """
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def send_code(email: str, user_agent: str, session=None) -> tuple[bool, str]:
    """Попросить площадку прислать код на почту. → (получилось, причина)."""
    email = str(email or "").strip()

    if not valid_email(email):
        return False, "это не похоже на почту"

    if session is None:
        import requests

        session = requests.Session()

    try:
        response = session.post(f"{BASE}/auth/send-otp",
                                json={"email": email},
                                headers=_headers(user_agent), timeout=TIMEOUT)
    except Exception as e:                                 # noqa: BLE001
        return False, f"не достучался до площадки: {e}"

    if getattr(response, "status_code", 0) >= 400:
        return False, _explain(response)

    return True, ""


def confirm(email: str, code: str, user_agent: str,
            session=None) -> tuple[str, str]:
    """Обменять код на сессию. → (строка куки, причина отказа).

    Пустая строка куки и пустая причина не бывают одновременно: либо есть
    сессия, либо есть что сказать.
    """
    email = str(email or "").strip()
    code = str(code or "").strip()

    if not valid_email(email):
        return "", "это не похоже на почту"

    if not valid_code(code):
        return "", "код — это шесть цифр из письма"

    if session is None:
        import requests

        session = requests.Session()

    try:
        response = session.post(f"{BASE}/auth/confirm-otp",
                                json={"email": email, "otpCode": code},
                                headers=_headers(user_agent), timeout=TIMEOUT)
    except Exception as e:                                 # noqa: BLE001
        return "", f"не достучался до площадки: {e}"

    if getattr(response, "status_code", 0) >= 400:
        return "", _explain(response)

    body = _json(response)

    if body is None:
        return "", ("площадка ответила не по делу — похоже на защиту от "
                    "ботов. Попробуйте позже или войдите куками")

    if body.get("requiresTwoFactor"):
        return "", ("на аккаунте включена двухфакторная проверка, а её "
                    "бот проходить не умеет. Отключите её в настройках "
                    "площадки или войдите куками из браузера")

    cookies = cookies_from(response, session)

    if not cookies:
        return "", ("код принят, но сессию площадка не выдала. Так бывает, "
                    "когда вход нужно подтвердить в браузере")

    return cookies, ""


def cookies_from(response, session=None) -> str:
    """Строка куки из ответа. Пусто — сессии нет.

    Смотрим и в ответ, и в хранилище сессии: площадка может поставить куку
    не на последнем шаге, а раньше — например пропуск защиты от ботов.
    """
    found: dict = {}

    for source in (getattr(session, "cookies", None),
                   getattr(response, "cookies", None)):
        try:
            for name, value in dict(source or {}).items():
                found[str(name)] = str(value)
        except (TypeError, ValueError):
            continue

    if "token" not in found:
        return ""

    # Порядок: token первым — по нему куки и опознаются дальше.
    order = [n for n in WANTED if n in found]
    order += [n for n in found if n not in order]

    return "; ".join(f"{name}={found[name]}" for name in order)
