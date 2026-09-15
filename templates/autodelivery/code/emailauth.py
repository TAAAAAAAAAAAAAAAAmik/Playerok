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


# Что площадка говорит своими словами и что это значит. Показывать
# продавцу «otp_code_mismatch» — всё равно что не сказать ничего.
SAID = {
    "otp_code_mismatch": "код не совпал",
    "otp_code_expired": "код истёк, запросите новый",
    "otp_code_not_found": "для этой почты код не запрашивали",
    "too_many_requests": "слишком часто, подождите",
    "user_not_found": "аккаунта с такой почтой нет",
    "email_not_confirmed": "почта на площадке не подтверждена",
}


def translate(said: str) -> str:
    """Слова площадки по-человечески, если знаем их."""
    key = str(said or "").strip().lower()

    return SAID.get(key, said)


def _reason(response) -> str:
    """Что площадка сказала в теле — её словами.

    Выбрасывать это нельзя: именно здесь она называет поле, которое ей не
    понравилось. Без него отказ выглядит как «что-то не так», и искать
    причину приходится вслепую.
    """
    body = _json(response)

    if isinstance(body, dict):
        for field in ("message", "error", "detail", "errorMessage"):
            value = body.get(field)

            if isinstance(value, str) and value.strip():
                return translate(value.strip())[:200]

            if isinstance(value, list) and value:
                return translate(str(value[0]))[:200]

    return str(getattr(response, "text", "") or "").strip()[:200]


def _explain(response, step: str = "") -> str:
    """Отказ площадки — человеческой фразой.

    `step` меняет совет: на отправке виновата обычно почта, на
    подтверждении — код. Один текст на оба случая уводил бы в сторону.
    """
    code = getattr(response, "status_code", 0)
    said = _reason(response)
    tail = f" Площадка: {said}" if said else ""

    if code == 429:
        wait = str(getattr(response, "headers", {}).get("Retry-After") or "")

        return ("площадка просит сбавить темп"
                + (f", подождите {wait} с" if wait else ", подождите минуту"))

    if code in (400, 422):
        if step == "confirm":
            # Площадка различает «не тот код» и «неверный запрос». Первое
            # чинится вводом настоящих цифр, второе — нашим кодом, и
            # смешивать их значит искать не там.
            if said and "не совпал" in said:
                return ("код не совпал. Введите цифры из ПОСЛЕДНЕГО письма: "
                        "каждый новый запрос кода отменяет предыдущий")

            return "код не подошёл." + tail

        return "площадка не приняла запрос: проверьте почту" + tail

    if code in (401, 403):
        return "код неверный или уже истёк." + tail

    if code == 404:
        return ("аккаунта с такой почтой нет" if step != "confirm"
                else "площадка не нашла этот запрос кода") + tail

    return f"площадка ответила {code}." + tail


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
        return False, _explain(response, "send")

    # Двухсотый ответ ещё не значит, что письмо ушло. Площадка может
    # ответить страницей защиты от ботов или своей ошибкой в теле — и
    # сказать «код отправлен», когда его не отправляли, значит отправить
    # человека ждать письма, которого не будет.
    body = _json(response)

    if body is None:
        return False, ("площадка ответила не по делу — похоже на защиту от "
                       "ботов. Попробуйте позже или войдите куками")

    return _accepted(body)


def _accepted(body) -> tuple[bool, str]:
    """Признала ли площадка запрос по телу ответа.

    Тело у этой ручки не описано нигде, поэтому разбираем терпимо: явный
    отказ ищем по known полям, а всё остальное считаем согласием — иначе
    бот отказывался бы работать при малейшем изменении формата.
    """
    if not isinstance(body, dict):
        return True, ""

    for field in ("error", "message", "detail", "errorMessage"):
        value = body.get(field)

        if isinstance(value, str) and value.strip():
            return False, f"площадка ответила: {value.strip()[:150]}"

    if body.get("success") is False or body.get("ok") is False:
        return False, "площадка не приняла запрос, но не сказала почему"

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
        return "", _explain(response, "confirm")

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
