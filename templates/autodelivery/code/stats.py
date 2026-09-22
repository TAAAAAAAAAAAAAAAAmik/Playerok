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
        # Профит считается ТОЛЬКО по записям, где известны оба числа.
        #
        # Иначе выходит так: у старых выдач сумма сделки не записана, а
        # цена закупки записана всегда — и «ноль минус закупка» даёт
        # минус две тысячи там, где на самом деле продано на десять.
        # Отчёт о деньгах показывал убыток вместо прибыли.
        self.pairs = 0                 # записей, где известно и то и то
        self.pair_revenue = 0.0        # их выручка
        self.pair_cost: dict = {}      # их закупка по валютам

    def add(self, entry: dict) -> None:
        self.count += 1
        paid = _number(entry.get("paid"))

        if paid is None:
            self.unknown_price += 1
        else:
            self.revenue += paid

        cost = _number(entry.get("price"))
        money = str(entry.get("currency") or "USD").upper()

        if cost is None:
            self.unknown_cost += 1
        else:
            self.cost[money] = self.cost.get(money, 0.0) + cost

        if paid is not None and cost is not None:
            self.pairs += 1
            self.pair_revenue += paid
            self.pair_cost[money] = self.pair_cost.get(money, 0.0) + cost

        at = when(entry)

        if at:
            self.first = min(self.first or at, at)
            self.last = max(self.last, at)

    @property
    def known(self) -> int:
        """По скольким выдачам известна сумма сделки."""
        return self.count - self.unknown_price

    @property
    def average(self) -> float:
        """Средний чек. Только по записям, где сумма известна."""
        return self.revenue / self.known if self.known else 0.0

    def cost_in_rubles(self, rate: float, cost: dict | None = None):
        """Закупка в рублях по курсу продавца. None — курс не назван.

        Рублёвая часть закупки в пересчёте не нуждается и прибавляется как
        есть: у поставщика бывают и рублёвые счета.
        """
        cost = self.cost if cost is None else cost
        rubles = cost.get("RUB", 0.0) + cost.get("RUR", 0.0)
        other = sum(value for money, value in cost.items()
                    if money not in ("RUB", "RUR"))

        if other and not rate:
            return None

        return rubles + other * rate

    def profit(self, rate: float) -> float | None:
        """Профит в рублях. None — считать не из чего.

        Считается по записям, где известны ОБА числа. Смешивать известную
        закупку с неизвестной выручкой нельзя: получится убыток, которого
        не было.
        """
        if not self.pairs:
            return None

        spent = self.cost_in_rubles(rate, self.pair_cost)

        return None if spent is None else self.pair_revenue - spent


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
        whole.pairs += one.pairs
        whole.pair_revenue += one.pair_revenue

        for money, value in one.cost.items():
            whole.cost[money] = whole.cost.get(money, 0.0) + value

        for money, value in one.pair_cost.items():
            whole.pair_cost[money] = whole.pair_cost.get(money, 0.0) + value

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


def revenue_line(one: Sum) -> str:
    """Выручка словами. Ноль показывается нулём, только если он настоящий.

    «0 ₽ за 11 кодов» читается как «продали на ноль», хотя на деле суммы
    просто не записаны. Это разные вещи, и путать их в отчёте о деньгах
    нельзя.
    """
    if one.count and not one.known:
        return "сумма не записана"

    if one.known < one.count:
        return f"{money(one.revenue)} (по {one.known} из {one.count})"

    return money(one.revenue)


def profit_line(one: Sum, rate: float) -> str:
    """Профит словами: с оговоркой, если посчитан не по всем выдачам."""
    got = one.profit(rate)

    if got is None:
        if not one.pairs:
            return "профит: не из чего считать"

        return "профит: курс доллара не задан"

    share = margin(one, rate)
    line = "профит " + money(got)

    if share is not None:
        line += f" ({share:.0f}%)"

    if one.pairs < one.count:
        line += f" — по {one.pairs} из {one.count}"

    return line


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
    """Доля профита в выручке, процентами. None — не посчитать.

    Доля считается от ТОЙ ЖЕ выручки, из которой посчитан профит: делить
    прибыль по части выдач на выручку по всем — значит занизить её без
    предупреждения.
    """
    got = one.profit(rate)

    if got is None or not one.pair_revenue:
        return None

    return got / one.pair_revenue * 100.0


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
