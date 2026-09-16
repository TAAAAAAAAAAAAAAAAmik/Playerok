"""Одинаковых объявлений не больше трёх: дальше цена растёт на рубль.

Площадка не любит одинаковые товары, и когда на витрине висит четвёртый
«1000 Robux за 1320 ₽» от того же продавца, это уже не ассортимент. Но и
снимать их незачем: покупатели разные, а объявлений много не бывает.

Поэтому счёт: три объявления одного номинала по одной цене — предел,
четвёртое создаётся на рубль дороже. Если и по новой цене уже три, цена
растёт дальше, пока не найдётся свободная.

ЧТО СЧИТАЕМ. Только бесплатные объявления. Платное продавец оплатил сам —
считать его в тот же лимит значило бы тратить его деньги и следом сдвигать
цену. Поэтому `remember` зовётся там, где известно, как товар выставлен.

СЧЁТ ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК. Без этого после каждого перезапуска бот
начинал бы с нуля и ставил четвёртое, пятое и шестое объявление по одной
цене — то есть ровно то, от чего счёт и заведён.
"""
from __future__ import annotations

# Сколько одинаковых объявлений допускаем по умолчанию.
LIMIT = 3

# На сколько рублей поднимаем цену, когда предел выбран.
STEP = 1

# Пределы настройки. Не придирка: ноль в пределе означал бы, что цена
# растёт у каждого объявления, а нулевой шаг — вечный круг подъёма,
# который ничего не меняет.
MIN_LIMIT, MAX_LIMIT = 1, 50
MIN_STEP, MAX_STEP = 1, 1000

# Сколько раз подряд готовы поднимать. Предохранитель: без него товар,
# у которого уже висит полсотни копий, увёл бы цену далеко от задуманной
# молча.
MAX_STEPS = 20


def _between(value, default: int, low: int, high: int) -> int:
    """Число из настроек в разумных пределах."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(low, min(high, number))


def _key(nominal, price) -> str:
    """Ключ счёта: номинал и цена. Разные номиналы не мешают друг другу."""
    return f"{float(nominal):g}@{int(price)}"


class Ledger:
    """Сколько бесплатных объявлений выставлено на каждую пару.

    Живёт в том же хранилище, что и остальное состояние кабинета:
    отдельный файл стал бы вторым источником правды.
    """

    FIELD = "free_listings"

    def __init__(self, store, card_slug: str = "листинги"):
        self.store = store
        self.slug = card_slug

    # ---------- настройки ----------

    def rules(self) -> dict:
        """Как считать. Умолчания — те же, что были до настроек."""
        conf = self.store.conf(self.slug)

        return {
            # Включено по умолчанию: правило и заводилось ради того, чтобы
            # одинаковых объявлений не копилось само собой.
            "enabled": conf.get("bump_enabled", True) is not False,
            "limit": _between(conf.get("bump_limit"), LIMIT,
                              MIN_LIMIT, MAX_LIMIT),
            "step": _between(conf.get("bump_step"), STEP,
                             MIN_STEP, MAX_STEP),
            # Разнообразие текстовых полей — про то же самое: чтобы копии
            # не были копиями до буквы. Поэтому живёт рядом.
            "vary": conf.get("vary_fields", True) is not False,
        }

    def set_enabled(self, on: bool) -> None:
        self._set("bump_enabled", bool(on))

    def set_vary(self, on: bool) -> None:
        self._set("vary_fields", bool(on))

    def set_limit(self, value) -> None:
        self._set("bump_limit", _between(value, LIMIT, MIN_LIMIT, MAX_LIMIT))

    def set_step(self, value) -> None:
        self._set("bump_step", _between(value, STEP, MIN_STEP, MAX_STEP))

    def _set(self, field: str, value) -> None:
        self.store.conf(self.slug)[field] = value
        self.store.save()

    def reset(self) -> int:
        """Забыть весь счёт → сколько пар забыли.

        Нужно, когда продавец снял объявления руками: счёт про них не
        знает и продолжал бы поднимать цену на пустом месте.
        """
        counts = self._counts()
        was = len(counts)
        counts.clear()
        self.store.save()

        return was

    def _counts(self) -> dict:
        conf = self.store.conf(self.slug)
        counts = conf.get(self.FIELD)

        if not isinstance(counts, dict):
            counts = {}
            conf[self.FIELD] = counts

        return counts

    def pairs(self) -> int:
        """Сколько пар «номинал + цена» под счётом. Для экрана настроек."""
        return len(self._counts())

    def count(self, nominal, price) -> int:
        return int(self._counts().get(_key(nominal, price)) or 0)

    def remember(self, nominal, price) -> None:
        """Записать выставленное бесплатное объявление."""
        counts = self._counts()
        key = _key(nominal, price)
        counts[key] = int(counts.get(key) or 0) + 1
        self.store.save()

    def forget(self, nominal, price) -> None:
        """Забыть объявление: оказалось платным.

        Платное продавец оплатил сам — считать его в тот же лимит значило
        бы тратить его деньги и следом сдвигать цену.
        """
        counts = self._counts()
        key = _key(nominal, price)
        left = int(counts.get(key) or 0) - 1

        if left > 0:
            counts[key] = left
        else:
            counts.pop(key, None)

        self.store.save()

    def price_for(self, nominal, price) -> tuple:
        """Какую цену ставить → (цена, на сколько подняли).

        Поднимаем до первой цены, по которой объявлений меньше предела.
        Ноль во втором значении значит «ничего не меняли» — так вызывающий
        отличает обычный случай от того, о котором надо сказать продавцу.
        """
        price = int(price)
        start = price
        rules = self.rules()

        if not rules["enabled"]:
            return price, 0

        for _ in range(MAX_STEPS):
            if self.count(nominal, price) < rules["limit"]:
                return price, price - start

            price += rules["step"]

        return price, price - start
