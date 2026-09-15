"""Цены серии: посчитать от закупки и собрать список для правки.

Номиналы у поставщика уже есть — переписывать их руками незачем. Что
действительно решает продавец, это цена, и только её он и должен вводить.

Два пути, и оба заканчиваются одним и тем же списком «номинал = цена»:

* заполнить руками — бот печатает список номиналов с пустыми ценами;
* посчитать от закупки — курс и наценка, а дальше правка вручную.

ПОСЧИТАННОЕ — ПРЕДЛОЖЕНИЕ, А НЕ РЕШЕНИЕ. Курс меняется, комиссия площадки
в него не входит, и округление у каждого своё. Поэтому счёт даёт тот же
редактируемый список, а не сразу объявления.
"""
from __future__ import annotations

import math


def sheet(rows) -> str:
    """Список для правки: «номинал = цена», по строке на номинал.

    `rows` — пары (номинал, цена) или просто номиналы. Пустая цена значит
    «продавец впишет сам»: подставлять туда ноль нельзя, ноль — это
    настоящая цена, которую площадка примет.
    """
    lines = []

    for row in rows:
        if isinstance(row, (tuple, list)):
            nominal, price = row[0], row[1]
        else:
            nominal, price = row, None

        shown = "" if price in (None, "") else f"{int(round(float(price)))}"
        lines.append(f"{float(nominal):g} = {shown}".rstrip())

    return "\n".join(lines)


def suggest(costs, rate: float, markup: float, step: int = 1) -> list:
    """Закупка в долларах → цена в рублях. → пары (номинал, цена).

    `costs` — пары (номинал, закупка в USD). `markup` — наценка в
    процентах: 40 значит «дороже закупки в 1.4 раза».

    Округляем ВВЕРХ до `step`. Вниз — значит местами продавать ниже
    задуманного, а разницу он заметит не сразу: она прячется в копейках на
    каждом заказе.
    """
    rate = float(rate)
    factor = 1.0 + float(markup) / 100.0
    step = max(1, int(step))
    rows = []

    for nominal, cost in costs:
        if cost in (None, ""):
            # Без закупки считать не от чего. Пропускать нельзя — номинал
            # исчез бы из списка молча; отдаём с пустой ценой.
            rows.append((float(nominal), None))
            continue

        roubles = float(cost) * rate * factor
        rows.append((float(nominal), int(math.ceil(roubles / step) * step)))

    return rows


def costs_from(denominations) -> list:
    """Номиналы поставщика → пары (номинал, закупка).

    Один и тот же номинал бывает у нескольких услуг: берём самый дешёвый —
    тот, по которому выдача и будет покупать.
    """
    best: dict = {}

    for row in denominations:
        value = float(getattr(row, "value", 0) or 0)

        if value <= 0:
            continue

        price = getattr(row, "price", None)
        stock = int(getattr(row, "in_stock", 0) or 0)

        if stock <= 0:
            # Номинала нет в наличии — объявление по нему бот выдать не
            # сможет, а покупатель заплатит и будет ждать.
            continue

        known = best.get(value)

        if known is None or (price is not None
                             and (known is None or price < known)):
            best[value] = price

    return sorted(best.items())
