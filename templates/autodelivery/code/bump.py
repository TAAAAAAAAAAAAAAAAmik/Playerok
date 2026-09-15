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

# Сколько одинаковых объявлений допускаем.
LIMIT = 3

# На сколько рублей поднимаем цену, когда предел выбран.
STEP = 1

# Сколько раз подряд готовы поднимать. Предохранитель: без него товар,
# у которого уже висит полсотни копий, увёл бы цену далеко от задуманной
# молча.
MAX_STEPS = 20


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

    def _counts(self) -> dict:
        conf = self.store.conf(self.slug)
        counts = conf.get(self.FIELD)

        if not isinstance(counts, dict):
            counts = {}
            conf[self.FIELD] = counts

        return counts

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

        for _ in range(MAX_STEPS):
            if self.count(nominal, price) < LIMIT:
                return price, price - start

            price += STEP

        return price, price - start
