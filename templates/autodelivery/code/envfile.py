"""Чтение .env рядом со скриптом.

На боевом сервере переменные раздаёт systemd через EnvironmentFile, и
тогда этот модуль ничего не делает. Но руками скрипты запускают из обычной
оболочки, где переменных нет, и требовать перед каждым запуском помнить

    set -a && . ./.env && set +a

значит однажды забыть — и получить «нет ключа» там, где ключ есть.

Уже заданные переменные не трогаем: окружение главнее файла, иначе
разовый запуск с другим ключом молча брал бы старый.
"""
from __future__ import annotations

import os


def load_env_file(path: str) -> int:
    """Прочитать файл в окружение. Возвращает, сколько переменных добавлено.

    Отсутствие файла — не ошибка: на сервере с systemd его может не быть.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return 0

    added = 0

    for line in lines:
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()

        # Кавычки ставит человек, а площадке они не нужны.
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if name and name not in os.environ:
            os.environ[name] = value
            added += 1

    return added
