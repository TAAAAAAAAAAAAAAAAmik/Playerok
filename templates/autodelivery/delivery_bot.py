"""Автовыдача кодов: боевой запуск.

Сам движок и вся сборка живут в `example_bot.py` — он показывает, как всё
соединяется, и запускать можно прямо его. Но имя «example» читается как
«пример, не для дела», и из-за него автовыдачу легко не запустить вовсе:
сторож знал `item_bot`, `notify_bot`, `restore_bot` — и не знал выдачу.
Так и вышло, что бот создавал товары, а коды не выдавал никто.

    sh run_bot.sh delivery_bot.py

Что нужно в .env:

    APPROUTE_KEY=...        ключ кабинета поставщика
    APPROUTE_PROXY=...      если у поставщика белый список IP
    TELEGRAM_BOT_TOKEN=...  чтобы отказы доходили до продавца
    TELEGRAM_OWNER_ID=...

Без ключа поставщика выдавать нечем — скрипт скажет об этом и выйдет, а
не будет молча перезапускаться сторожем каждые несколько секунд.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from envfile import load_env_file                             # noqa: E402

import example_bot                                            # noqa: E402


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    if not os.environ.get("APPROUTE_KEY", "").strip():
        raise SystemExit(
            "Нет APPROUTE_KEY — это ключ кабинета AppRoute, которым "
            "покупаются коды. Без него выдавать нечего.\n"
            "Положите его в .env строкой APPROUTE_KEY=...")

    asyncio.run(example_bot.main())


if __name__ == "__main__":
    main()
