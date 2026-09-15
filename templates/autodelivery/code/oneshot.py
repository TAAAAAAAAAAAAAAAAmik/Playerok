"""Объявление одним сообщением: разбор «Ключ: значение».

Опрос по шагам хорош, когда товар первый. Когда он двадцатый, десять
обменов с ботом — это минуты на ровном месте, и продавец начинает
торопиться там, где ошибка стоит денег.

Поэтому есть второй путь: бот печатает заготовку под выбранную категорию,
продавец правит её у себя и присылает одним сообщением.

ПОЧЕМУ С КЛЮЧАМИ, А НЕ ПО ПОРЯДКУ СТРОК. Порядок казался короче, но
пропущенная строка сдвигает всё, что ниже: цена встаёт на место номинала,
и бот покупает у поставщика не то. Ключ пропуск переживает — не понял,
так и скажет, а не подставит соседнее.

Описание бывает в несколько строк, поэтому строка без ключа продолжает
предыдущее значение. Отсюда правило: ключ узнаётся ТОЛЬКО в начале
строки.
"""
from __future__ import annotations

import re

# Ключи, которые понимаем сами. Первое написание — то, что бот печатает в
# заготовке; остальные на случай, если продавец пишет по-своему.
KEYS = {
    "name": ("Название", "Имя", "Товар", "Name", "Title"),
    "price": ("Цена", "Стоимость", "Price"),
    "nominal": ("Номинал", "Количество", "Кол-во", "Сумма", "Amount"),
    "region": ("Регион", "Region"),
    "description": ("Описание", "Описание товара", "Description"),
}

# Порядок в заготовке: сначала то, что бот проверит строже всего.
ORDER = ("name", "price", "nominal", "region", "description")

# Строка вида «Ключ: значение». Двоеточие обязательно: без него любая
# строка описания, начинающаяся со слова «Цена», стала бы новым полем.
LINE = re.compile(r"^\s*([^:\n]{1,40}?)\s*:\s*(.*)$")


def _known() -> dict:
    """Написание ключа → имя поля."""
    found = {}

    for field, names in KEYS.items():
        for name in names:
            found[name.lower()] = field

    return found


def parse(text: str, labels=()) -> dict:
    """Разобрать сообщение → значения полей.

    `labels` — названия полей самой площадки («Комментарий», «Промокод»):
    их состав зависит от категории, поэтому они приходят снаружи, а не
    записаны здесь.

    НОВОЕ ПОЛЕ НАЧИНАЕТ ТОЛЬКО ЗНАКОМЫЙ КЛЮЧ. Всё остальное — продолжение
    предыдущего значения, даже если в строке есть двоеточие. Иначе обычное
    описание разваливалось бы на куски: «Активация: roblox.com/redeem» —
    это текст для покупателя, а не поле товара.

    Опечатка в ключе из-за этого попадает в описание, а не в цену. Так и
    задумано: пропавшую цену поймает `missing` и скажет об этом, а вот
    «Цна: 700», принятое за цену, никто бы не поймал.
    """
    known = _known()
    by_label = {str(label).strip().lower(): str(label)
                for label in labels if str(label).strip()}

    values: dict = {}
    current = None

    for raw in str(text or "").splitlines():
        match = LINE.match(raw)
        field = None

        if match is not None:
            low = match.group(1).strip().lower()

            if low in known:
                field = known[low]
            elif low in by_label:
                field = "field:" + by_label[low]

        if field is None:
            # Продолжение. До первого ключа продолжать нечего: такую строку
            # пропускаем, иначе заголовок вроде «Мой товар» попал бы в
            # название вторым куском.
            if current is not None and raw.strip():
                values[current] = (values[current] + "\n" + raw).strip()

            continue

        current = field
        values[field] = match.group(2).strip()

    return values


def blank(labels=(), card=None) -> str:
    """Заготовка, которую продавец копирует и правит.

    Поля площадки идут после наших: их состав зависит от категории, и
    показывать их вперемешку значило бы, что при смене категории заготовка
    меняется целиком.
    """
    lines = []

    for field in ORDER:
        name = KEYS[field][0]
        lines.append(f"{name}: {_hint(field, card)}")

    for label in labels:
        if str(label).strip():
            lines.append(f"{label}: ")

    return "\n".join(lines)


def _hint(field: str, card=None) -> str:
    """Пример значения — на товаре продавца, а не абстрактный."""
    if card is None:
        return ""

    if field == "name":
        return card.ad_title.replace("{номинал}", "1000").replace(
            "{регион}", "GL") if card.ad_title else ""

    return ""


def missing(values: dict, fields=()) -> list:
    """Чего не хватает, чтобы создать товар. Список названий."""
    gaps = []

    for field in ("name", "price"):
        if not str(values.get(field) or "").strip():
            gaps.append(KEYS[field][0])

    for field in fields:
        if not field.get("required"):
            continue

        label = str(field.get("label") or "")

        if not str(values.get("field:" + label) or "").strip():
            gaps.append(label)

    return gaps
