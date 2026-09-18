"""Кто из ботов сейчас работает.

Боты запускаются по отдельности и падают тоже по отдельности. Самый
дорогой случай — когда жив notify_bot и мёртв delivery_bot: продавцу
приходит «💰 Покупка», покупатель пишет «а где промокод», а выдачи нет и
не будет. И ни одной жалобы: жаловаться некому, тот, кто должен был бы,
не запущен.

Поэтому проверка живости лежит отдельно и зовётся оттуда, где о ней
вспомнят: из уведомлений, из экрана проверки в боте и из doctor.py.
"""
from __future__ import annotations

import os
import subprocess

# Как сторож запускает ботов. По этой строке процесс и узнаётся; короткий
# шаблон вроде «delivery_bot» совпал бы и с редактором, открывшим файл.
PATTERN = "python3 -u {name}.py"

# Сколько ждём pgrep. Он мгновенный, но висящий вызов не должен держать
# ни уведомления, ни экран бота.
TIMEOUT = 10


def running(name: str) -> bool | None:
    """Работает ли бот. True/False, None — проверить не вышло.

    None — не «нет»: на урезанной системе pgrep может отсутствовать вовсе,
    и сказать «бот не работает» там значило бы соврать.
    """
    try:
        found = subprocess.run(["pgrep", "-f", PATTERN.format(name=name)],
                               capture_output=True, text=True,
                               timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None

    return bool(found.stdout.strip())


def delivery_running() -> bool | None:
    """Работает ли автовыдача."""
    return running("delivery_bot")


def start_hint(name: str = "delivery_bot") -> str:
    """Чем поднять бота — командой, которую наберут целиком."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return f"sh {here}/run_bot.sh {name}.py"
