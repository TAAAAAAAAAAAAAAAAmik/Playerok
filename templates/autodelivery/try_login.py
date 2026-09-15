"""Проба входа по коду: показать, что площадка отвечает на самом деле.

Бот сказал «код отправлен», а письмо не пришло — значит площадка ответила
не то, что мы поняли. Этот скрипт делает тот же запрос и печатает ответ
целиком: адрес, код состояния, заголовки и тело.

    python3 try_login.py почта@пример.ru

Сам по себе он ничего не сохраняет и ничего не меняет — только спрашивает.
Второй шаг, обмен кода на сессию, делается отдельно:

    python3 try_login.py почта@пример.ru 123456

Показанное можно присылать как есть: ни куки, ни токен в ответ на первый
запрос не приходят, а во втором они прячутся — печатаются только имена.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

import emailauth                                              # noqa: E402
from envfile import load_env_file                             # noqa: E402


def show(response) -> None:
    print(f"  код состояния: {response.status_code}")

    interesting = ("content-type", "retry-after", "server",
                   "x-ratelimit-remaining", "set-cookie")

    for name, value in dict(getattr(response, "headers", {}) or {}).items():
        if name.lower() in interesting:
            if name.lower() == "set-cookie":
                # Значения не печатаем: это доступ к кабинету.
                names = [part.split("=")[0].strip()
                         for part in str(value).split(",")]
                print(f"  куки в ответе: {', '.join(names)}")
            else:
                print(f"  {name}: {value}")

    body = (response.text or "")[:600]
    print("  тело ответа:")
    print("   " + (body.replace("\n", "\n   ") if body else "(пусто)"))


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    import requests

    email = sys.argv[1].strip()
    code = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    # «123456» стоит в примерах, и подставить его целиком вместо цифр из
    # письма проще простого — на этом уже спотыкались.
    if code == "123456":
        raise SystemExit(
            "123456 — это пример из подсказки, а не ваш код.\n"
            "Возьмите шесть цифр из письма и подставьте их.")
    user_agent = os.environ.get(
        "PLAYEROK_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")

    session = requests.Session()
    path = "/auth/confirm-otp" if code else "/auth/send-otp"
    payload = {"email": email}

    if code:
        payload["otpCode"] = code

    print(f"Запрос: POST {emailauth.BASE}{path}")
    print(f"Тело:   {json.dumps(payload, ensure_ascii=False)}")
    print(f"User-Agent: {user_agent[:60]}…")
    print()

    try:
        response = session.post(f"{emailauth.BASE}{path}", json=payload,
                                headers=emailauth._headers(user_agent),
                                timeout=emailauth.TIMEOUT)
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"Не достучался до площадки: {e}")

    print("Ответ площадки:")
    show(response)
    print()

    if code:
        cookies = emailauth.cookies_from(response, session)
        print("Сессия получена:" if cookies else "Сессии в ответе нет.")

        if cookies:
            print("  куки:", ", ".join(c.split("=")[0]
                                       for c in cookies.split("; ")))
    else:
        ok, why = emailauth._accepted(emailauth._json(response) or {})
        print("Наш разбор: запрос принят" if ok else f"Наш разбор: {why}")


if __name__ == "__main__":
    main()
