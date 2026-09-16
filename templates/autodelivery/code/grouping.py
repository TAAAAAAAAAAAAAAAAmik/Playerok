"""Разбиение списка объявлений на понятные кучки.

Продавец с полусотней объявлений выбирает из списка, где половина строк
выглядит одинаково: «🏆 ХИТ КАТЕГОРИИ • 25.000.000₽ • 3 LVL — 2219…», и на
экране телефона от них видно первые тридцать знаков. Такой список не
помогает выбрать, а мешает.

Категорию площадка в списке товаров не отдаёт — только в карточке каждого,
а читать полсотни карточек ради одного меню значит заставить продавца
ждать минуту. Зато название продавец придумывает сам, и придумывает его
осмысленно: до первого разделителя обычно стоит то, чем товар и
отличается, — «ХИТ КАТЕГОРИИ», «БЫСТРАЯ ПОКУПКА».

По нему и группируем. Это догадка, и она честно называется догадкой:
кучка всегда показывает, сколько в ней объявлений, а рядом есть «всё
подряд» — на случай, если разбиение вышло бестолковым.
"""
from __future__ import annotations

import re

# Чем продавцы отделяют начало названия от подробностей.
SPLIT = re.compile(r"\s*[•|·—–\-/]\s*")

# Насколько длинной может быть подпись кучки.
LABEL_MAX = 34


def head(name: str) -> str:
    """Начало названия — то, чем товар отличается от соседнего."""
    text = " ".join(str(name or "").split())

    if not text:
        return "Без названия"

    first = SPLIT.split(text, 1)[0].strip()

    # Разделителя нет или он в самом начале — берём название целиком.
    if not first:
        first = text

    return first[:LABEL_MAX].strip() or "Без названия"


def groups(items, name_of=lambda i: getattr(i, "name", "")) -> list:
    """Объявления → [(подпись, [объявления])], крупные кучки первыми.

    Порядок внутри кучки не трогаем: он пришёл от площадки, где свежие
    идут первыми, и продавец ждёт того же.
    """
    found: dict = {}

    for item in items:
        found.setdefault(head(name_of(item)), []).append(item)

    return sorted(found.items(), key=lambda pair: (-len(pair[1]), pair[0]))


def matching(items, word: str, name_of=lambda i: getattr(i, "name", "")):
    """Объявления, в названии которых есть слово. Регистр не важен."""
    word = " ".join(str(word or "").lower().split())

    if not word:
        return list(items)

    return [i for i in items if word in str(name_of(i) or "").lower()]


def page(items, number: int, size: int) -> tuple:
    """Кусок списка → (что показать, есть ли ещё, сколько страниц)."""
    size = max(1, int(size))
    total = (len(items) + size - 1) // size or 1
    number = max(0, min(int(number), total - 1))
    start = number * size

    return items[start:start + size], number + 1 < total, total
