"""Проверка связи с владельцем. Ничего не покупает и не читает площадку.

Отвечает на три вопроса, которые иначе выясняются только в бою:

    1. Живой ли токен бота.
    2. Тот ли это бот, что вы завели.
    3. Дойдёт ли сообщение до вас — а это отдельный вопрос: пока вы не
       написали боту первым, Telegram не даёт ботам писать людям, и
       первая же просьба о куки уйдёт в никуда.

    set -a && . /etc/autodelivery.env && set +a
    python3 check_telegram.py

Запускать на том же сервере, где живёт бот: проверяется в том числе, что
у сервера вообще есть доступ к api.telegram.org.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

import requests                                               # noqa: E402

from owner import link_from_env                              # noqa: E402


def main() -> None:
    link = link_from_env()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID. Без них бот "
            "работает, но новые куки придётся вписывать на сервере руками.")

    try:
        me = link.whoami()
    except requests.RequestException as e:
        # Сеть и отказ Telegram — разные беды с разным лечением, и валить
        # их в одно «не получилось» значит отправить чинить не то.
        raise SystemExit(
            f"До api.telegram.org не достучаться: {e}\n"
            "Дело не в токене. Проверьте, что с сервера есть выход в "
            "интернет и Telegram не закрыт у провайдера.")
    except Exception as e:                                   # noqa: BLE001
        raise SystemExit(
            f"Telegram не принял токен: {e}\n"
            "Проверьте TELEGRAM_BOT_TOKEN — он выдаётся @BotFather целиком, "
            "вместе с числом до двоеточия.")

    print(f"Бот: @{me.get('username')} ({me.get('first_name')})")

    if not link.say("Связь работает. Отсюда я попрошу новые куки, "
                    "когда площадка перестанет принимать прежние."):
        raise SystemExit(
            f"Токен живой, но сообщение владельцу {link.owner_id} не ушло.\n"
            "Две обычные причины:\n"
            "  1. Вы не написали боту первым. Откройте его в Telegram и "
            "нажмите «Запустить» — до этого боты писать людям не могут.\n"
            "  2. В TELEGRAM_OWNER_ID не тот номер. Нужен ваш числовой id, "
            "а не имя пользователя.")

    print("Сообщение отправлено — проверьте, что оно пришло именно вам.")
    print("Если пришло, обновлять куки можно из телефона: бот попросит их "
          "сам, отвечать нужно строкой из браузера.")


if __name__ == "__main__":
    main()
