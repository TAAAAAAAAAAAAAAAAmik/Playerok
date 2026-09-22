"""Сколько продано, сколько потрачено и сколько на этом заработано.

Считается по журналу выдач — тому самому, который движок пишет ради
надёжности. В нём уже есть всё нужное: что продали, за сколько (сумма
сделки с площадки), почём купили у поставщика и когда.

ТРИ ПРАВИЛА, и все три про честность цифр.

**Считаем только ВЫДАННОЕ.** Незаконченная покупка — это не продажа:
деньги могли не списаться, код мог не уйти. Отчёт, в котором «заработано»
включает несостоявшееся, хуже отсутствия отчёта: по нему принимают
решения.

**Валюты не смешиваем.** Продажа в рублях, закупка у поставщика в
долларах. Сложить их без курса нельзя, а выдумать курс — значит
подменить отчёт о деньгах догадкой. Поэтому закупка складывается ПО
ВАЛЮТАМ, а профит в рублях появляется, только когда продавец сам назвал
курс.

**Чего не знаем — не показываем нулём.** Сделка без суммы (старая запись,
площадка не отдала) не прибавляет к продажам ноль, а считается отдельно и
называется: «у 3 записей суммы нет». Ноль в отчёте о деньгах читается как
факт.
"""
from __future__ import annotations

import time

DAY = 86400.0

# Что считаем выданным. Ровно то же слово пишет движок.
DONE = "выдан"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def when(entry: dict) -> float:
    """Когда выдали. Время выдачи, а не намерения.

    Покупка могла висеть ночь, дожидаясь денег на счёте, и «за сегодня» по
    времени намерения показало бы вчерашнее.
    """
    return _number(entry.get("done_at")) or _number(entry.get("at")) or 0.0


def sold(entries: list) -> list:
    """Только выданные записи журнала."""
    return [e for e in entries or []
            if isinstance(e, dict) and str(e.get("state") or "") == DONE]


class Sum:
    """Итог по одному товару или по всем сразу."""

    def __init__(self):
        self.count = 0                 # сколько выдано
        self.revenue = 0.0             # продано, ₽
        self.cost: dict = {}           # закупка по валютам
        self.unknown_price = 0         # записей без суммы сделки
        self.unknown_cost = 0          # записей без цены закупки
        self.first = 0.0               # когда была первая выдача
        self.last = 0.0                # когда последняя

    def add(self, entry: dict) -> None:
        self.count += 1
        paid = _number(entry.get("paid"))

        if paid is None:
            self.unknown_price += 1
        else:
            self.revenue += paid

        cost = _number(entry.get("price"))

        if cost is None:
            self.unknown_cost += 1
        else:
            money = str(entry.get("currency") or "USD").upper()
            self.cost[money] = self.cost.get(money, 0.0) + cost

        at = when(entry)

        if at:
            self.first = min(self.first or at, at)
            self.last = max(self.last, at)

    @property
    def average(self) -> float:
        """Средний чек. Только по записям, где сумма известна."""
        known = self.count - self.unknown_price

        return self.revenue / known if known else 0.0

    def cost_in_rubles(self, rate: float) -> float | None:
        """Закупка в рублях по курсу продавца. None — курс не назван.

        Рублёвая часть закупки в пересчёте не нуждается и прибавляется как
        есть: у поставщика бывают и рублёвые счета.
        """
        rubles = self.cost.get("RUB", 0.0) + self.cost.get("RUR", 0.0)
        other = sum(value for money, value in self.cost.items()
                    if money not in ("RUB", "RUR"))

        if other and not rate:
            return None

        return rubles + other * rate

    def profit(self, rate: float) -> float | None:
        """Профит в рублях. None — пока не с чем сравнивать."""
        spent = self.cost_in_rubles(rate)

        return None if spent is None else self.revenue - spent


def by_card(store, cards, since: float = 0.0) -> list:
    """Итоги по каждому товару. → [(карта, Sum)], крупные первыми.

    `since` — с какого времени считать. Ноль значит «за всё время».
    """
    out = []

    for card in cards:
        total = Sum()

        for entry in sold(store.conf(card.slug).get("log") or []):
            if since and when(entry) < since:
                continue

            total.add(entry)

        if total.count:
            out.append((card, total))

    out.sort(key=lambda pair: -pair[1].revenue)

    return out


def total_of(rows: list) -> Sum:
    """Сложить итоги нескольких товаров в один."""
    whole = Sum()

    for _, one in rows:
        whole.count += one.count
        whole.revenue += one.revenue
        whole.unknown_price += one.unknown_price
        whole.unknown_cost += one.unknown_cost
        whole.first = min(whole.first or one.first, one.first or whole.first)
        whole.last = max(whole.last, one.last)

        for money, value in one.cost.items():
            whole.cost[money] = whole.cost.get(money, 0.0) + value

    return whole


def periods(now: float | None = None) -> dict:
    """С каких пор считать «сегодня», «неделю», «месяц»."""
    now = time.time() if now is None else now

    return {"сегодня": now - DAY, "за 7 дней": now - 7 * DAY,
            "за 30 дней": now - 30 * DAY, "всего": 0.0}


def money(value) -> str:
    """Рубли так, как их читают: «12 480 ₽», а не «12480.0»."""
    number = _number(value) or 0.0
    whole = f"{int(round(number)):,}".replace(",", " ")

    return f"{whole} ₽"


def cost_line(cost: dict) -> str:
    """Закупка по валютам одной строкой: «38.4 $ · 1 200 ₽»."""
    signs = {"USD": "$", "RUB": "₽", "RUR": "₽", "EUR": "€"}
    parts = []

    for name in sorted(cost):
        value = cost[name]
        shown = f"{value:.2f}".rstrip("0").rstrip(".")
        parts.append(f"{shown} {signs.get(name, name)}")

    return " · ".join(parts)


def margin(one: Sum, rate: float) -> float | None:
    """Доля профита в выручке, процентами. None — не посчитать."""
    got = one.profit(rate)

    if got is None or not one.revenue:
        return None

    return got / one.revenue * 100.0


# ---------------------------------------------------------------------------
# Отзывы
# ---------------------------------------------------------------------------

class Reviews:
    """Итог по отзывам: сколько, какая оценка, что пишут."""

    def __init__(self):
        self.count = 0
        self.total = 0            # сколько всего у площадки, включая нечитанные
        self.rating = 0.0
        self.spread: dict = {}    # оценка → сколько
        self.last: list = []      # свежие: (оценка, текст, кто)

    @property
    def average(self) -> float:
        return self.rating / self.count if self.count else 0.0

    @property
    def bad(self) -> int:
        """Сколько отзывов ниже четвёрки. Именно они стоят денег."""
        return sum(n for mark, n in self.spread.items() if mark <= 3)


def reviews_of(rows, total: int = 0, shown: int = 3) -> Reviews:
    """Отзывы площадки → итог. `rows` — объекты с rating/text/creator."""
    out = Reviews()
    out.total = int(total or 0)

    for row in rows or []:
        mark = _number(getattr(row, "rating", None))

        if mark is None:
            continue

        mark = int(mark)
        out.count += 1
        out.rating += mark
        out.spread[mark] = out.spread.get(mark, 0) + 1

        if len(out.last) < shown:
            who = getattr(getattr(row, "creator", None), "username", "")
            text = " ".join(str(getattr(row, "text", "") or "").split())
            out.last.append((mark, text, str(who or "")))

    if not out.total:
        out.total = out.count

    return out


def stars(mark) -> str:
    """Оценка звёздами: их видно быстрее, чем цифру."""
    try:
        count = max(0, min(5, int(round(float(mark)))))
    except (TypeError, ValueError):
        return ""

    return "★" * count + "☆" * (5 - count)
