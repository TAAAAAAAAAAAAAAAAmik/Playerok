"""Выставление товара: выбор статуса приоритета.

Здесь только решения, без вызовов площадки — чтобы их можно было проверить
тестами, а не живыми публикациями.

Почему это отдельный модуль. Публикация со статусом приоритета ПЛАТНАЯ:
деньги списываются с баланса продавца. В чужом боте это место обставлено
перехватом ошибки — пробуем бесплатно, не вышло, платим. Мы делаем
наоборот: показываем цены и спрашиваем. Молчаливое списание — худший вид
сюрприза, даже когда сумма маленькая.
"""
from __future__ import annotations

from typing import Any

# Сколько запрашивать подтверждение словом, а не номером. Любая платная
# публикация: цена тут маленькая, но списание есть списание.
CONFIRM_WORD = "да"


# Отличать «цены нет» от «цена ноль» приходится явно: обычное `or 0`
# превращает отсутствующую цену в бесплатную, то есть ровно в тот случай,
# когда бот тратит деньги молча.
NO_PRICE = object()


def price_of(status: Any) -> float:
    """Цена статуса числом. Неизвестное считаем платным.

    Именно так, а не наоборот: принять платный статус за бесплатный значит
    списать деньги молча, а бесплатный за платный — всего лишь лишний
    вопрос продавцу.
    """
    raw = getattr(status, "price", NO_PRICE)

    if raw is NO_PRICE or raw is None or isinstance(raw, bool):
        return 1.0

    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def is_free(status: Any) -> bool:
    return price_of(status) <= 0


def describe(status: Any) -> str:
    """Строка для продавца: что это за статус и почём."""
    name = str(getattr(status, "name", "") or "без названия")
    days = getattr(status, "period", None)
    price = price_of(status)
    cost = "бесплатно" if price <= 0 else f"{price:g} ₽"
    period = f", {days} дн." if days else ""

    return f"{name} — {cost}{period}"


def ordered(statuses: list) -> list:
    """Статусы для показа: бесплатные первыми, дальше по цене.

    Порядок не косметика: первым в списке стоит то, что чаще всего и
    нужно, и промахнуться номером в пользу платного труднее.
    """
    return sorted(statuses or [], key=price_of)


def pick(statuses: list, answer: str) -> Any:
    """Что выбрал продавец → статус или None.

    Принимаем номер из показанного списка. Пустой ответ, мусор и выход за
    границы — это None, то есть «не публикуем». Угадывать намерение в
    месте, где списываются деньги, нельзя.
    """
    rows = ordered(statuses)
    text = str(answer or "").strip()

    if not text.isdigit():
        return None

    number = int(text)

    if not 1 <= number <= len(rows):
        return None

    return rows[number - 1]


def needs_confirmation(status: Any) -> bool:
    """Нужно ли спросить ещё раз, словом."""
    return not is_free(status)


def confirmed(answer: str) -> bool:
    """Согласие — только явное слово. Enter согласием не считается."""
    return str(answer or "").strip().lower() == CONFIRM_WORD
