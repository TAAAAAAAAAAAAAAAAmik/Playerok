"""Поднятие объявлений: когда, какое и за какие деньги.

Объявление тонет. На площадке, где в категории три сотни одинаковых
промокодов, вчерашнее объявление не видит никто, и продавец каждый день
руками поднимает десяток — или не поднимает, и продаёт вдвое меньше.

Это делается само. Но поднятие на площадке бывает платным, а цикл,
который работает сам и тратит деньги без спроса, — худшее, что можно
придумать. Поэтому здесь три замка, и снимает их продавец руками:

1. **по умолчанию берётся только бесплатный статус.** Платный — лишь
   когда разрешён отдельно;
2. **потолок трат на сутки.** Больше него бот не потратит, даже если
   разрешение есть: ошибка в расписании не должна стоить месячной
   выручки;
3. **предел цены одного поднятия.** Площадка может назвать любую цену, и
   согласие «до 20 ₽» не должно означать согласие на 500 ₽.

Решения здесь, без сети: что поднимать, каким статусом и хватает ли на
это денег. Сами вызовы площадки живут в restore_bot.
"""
from __future__ import annotations

import time

# Умолчания. Выключено, раз в восемь часов, платное запрещено.
DEFAULT = {
    "on": False,
    # Через сколько часов поднимать один и тот же товар.
    "every": 8,
    # Разрешено ли тратить деньги на поднятие.
    "paid": False,
    # Потолок трат в сутки, ₽. Ноль — платное не покупаем вовсе.
    "limit": 0,
    # Предел цены одного поднятия, ₽. Ноль — любая цена запрещена.
    "max_price": 0,
    # Сколько потрачено сегодня и какой сегодня день.
    "spent": 0.0,
    "day": "",
    # Когда какой товар поднимали: {номер товара: время}.
    "last": {},
    # Сколько поднятий сделано всего — чтобы было что показать.
    "count": 0,
}

# Сколько товаров поднимать за один проход. Каждое поднятие — два запроса
# к площадке, а она просит сбавлять темп быстрее, чем кажется.
AT_ONCE = 5

# Сколько товаров помнить по времени последнего поднятия.
KEEP = 500


class Approved:
    """«На витрине» так, как этот статус нужен библиотеке.

    Своё имя, а не импорт перечисления: библиотека кладёт в запрос ровно
    `status.name`, а импорт тянул бы её целиком туда, где она не нужна, —
    в тесты и в телеграм-бота. Имя же у статуса одно, и меняться ему
    незачем.
    """

    name = "APPROVED"


def settings_of(shared: dict) -> dict:
    """Настройки поднятия с умолчаниями поверх сохранённого."""
    found = shared.get("boost")

    if not isinstance(found, dict):
        found = shared["boost"] = {}

    for key, value in DEFAULT.items():
        if key not in found:
            found[key] = dict(value) if isinstance(value, dict) else value

    return found


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def new_day(conf: dict, now: float) -> None:
    """Сменились сутки — обнулить счётчик трат.

    Считается по местному дню, а не по «прошло 24 часа»: продавец мыслит
    днями, и «сегодня потрачено 40 ₽» должно значить именно сегодня.
    """
    today = _day(now)

    if conf.get("day") != today:
        conf["day"] = today
        conf["spent"] = 0.0


def left_today(conf: dict, now: float) -> float:
    """Сколько ещё можно потратить сегодня, ₽."""
    new_day(conf, now)

    return max(0.0, float(conf.get("limit") or 0) - float(conf.get("spent") or 0))


def due(items, conf: dict, now: float, at_once: int = AT_ONCE) -> list:
    """Какие товары пора поднимать. → список товаров, свежие последними.

    «Пора» — это «с прошлого поднятия прошло больше заданного». Товар, о
    котором мы ничего не помним, поднимается первым: скорее всего он
    лежит с прошлой недели.
    """
    every = float(conf.get("every") or 8) * 3600.0
    last = conf.get("last") or {}
    out = []

    for item in items or []:
        item_id = str(getattr(item, "id", "") or "")

        if not item_id:
            continue

        was = float(last.get(item_id) or 0)

        if now - was >= every:
            out.append((was, item))

    # Давно не поднятые — первыми: они ниже всех и в выдаче площадки.
    out.sort(key=lambda pair: pair[0])

    return [item for _, item in out[:max(1, int(at_once))]]


def pick(statuses, conf: dict, now: float):
    """Каким статусом поднимать. → (статус или None, причина отказа).

    Причина возвращается словами: она уходит продавцу, и по ней видно,
    почему объявления не поднимаются, — «платное не разрешено» и «кончился
    потолок» лечатся по-разному.
    """
    import listing

    rows = listing.ordered(statuses or [])

    if not rows:
        return None, "площадка не предложила ни одного статуса"

    for status in rows:
        if listing.is_free(status):
            return status, ""

    if not conf.get("paid"):
        return None, ("бесплатного поднятия нет, а платное не разрешено — "
                      "включите его в боте, если готовы платить")

    cheapest = rows[0]
    price = listing.price_of(cheapest)
    top = float(conf.get("max_price") or 0)

    if price > top:
        return None, (f"самое дешёвое поднятие стоит {_money(price)}, а вы "
                      f"разрешили до {_money(top)}")

    left = left_today(conf, now)

    if price > left:
        return None, (f"на сегодня осталось {_money(left)} из "
                      f"{_money(conf.get('limit'))} — поднимать не буду")

    return cheapest, ""


def _money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0 ₽"

    whole = int(round(number))

    return f"{whole:,} ₽".replace(",", " ")


def spend(conf: dict, price, now: float) -> None:
    """Записать трату. Без этого потолок не потолок."""
    new_day(conf, now)

    try:
        conf["spent"] = float(conf.get("spent") or 0) + float(price or 0)
    except (TypeError, ValueError):
        pass


def remember(conf: dict, item_id: str, now: float, keep: int = KEEP) -> None:
    """Запомнить, что этот товар только что подняли."""
    item_id = str(item_id or "")

    if not item_id:
        return

    last = conf.setdefault("last", {})
    last[item_id] = now
    conf["count"] = int(conf.get("count") or 0) + 1

    if len(last) > keep:
        old = sorted(last.items(), key=lambda pair: pair[1])

        for key, _ in old[:len(last) - keep]:
            last.pop(key, None)


def report(done: list, spent: float, failed=()) -> str:
    """Что сказать продавцу после прохода. Пусто — говорить не о чем.

    Молчим, когда всё бесплатно и получилось: поднятие идёт каждые
    несколько часов, и письмо о каждом превратит уведомления в шум. Зато
    о потраченных деньгах и об отказах говорим всегда.
    """
    lines = []

    if spent:
        lines.append(f"⬆️ Поднял {len(done)} — потрачено {_money(spent)}")

    for name, why in failed:
        lines.append(f"⚠️ «{name}» поднять не вышло: {why}")

    return "\n".join(lines)
