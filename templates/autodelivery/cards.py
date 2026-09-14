"""Какие товары умеем выдавать.

Один список на всё: и наблюдение, и боевой цикл смотрят сюда, чтобы не
разъехались.

ID услуг у поставщика надо подставить свои — они видны в кабинете AppRoute
(`GET /services`). Пока стоят заглушки, движок честно скажет, что номинал не
найден, и ничего не купит.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from catalog import Card                                       # noqa: E402


def _service(env_name: str) -> str:
    """ID услуги из окружения.

    В коде их не держим: у разных продавцов они разные, а менять их проще
    настройкой, чем выкатом.
    """
    return os.environ.get(env_name, "").strip()


CARDS = [
    Card(
        slug="robux",
        title="Roblox",
        emoji="🎮",
        keywords=("robux", "робукс", "роблокс"),
        measure="робуксов",
        activation="Активируйте код на roblox.com/redeem.",
        services={
            # Глобальные коды заметно выгоднее российских: 0.0099 USD за
            # робукс на номинале 10000 против 0.0139–0.0156 у любого RU.
            "GL": _service("APPROUTE_SERVICE_ROBUX_GL"),
            "RU": _service("APPROUTE_SERVICE_ROBUX_RU"),
        },
    ),
]
