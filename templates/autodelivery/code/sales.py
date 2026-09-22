"""Продажи по сделкам площадки: что продано, что уже можно забрать.

Журнал выдач знает только то, что выдал бот. А продавец спрашивает про
ВСЕ продажи — и про те, что он отдал руками, и про те, что были до бота.
Ответ на это есть только у площадки, в списке её сделок.

Деньги на площадке живут в трёх состояниях, и путать их нельзя:

* **подтверждено** — покупатель принял товар, деньги продавца;
* **ждёт подтверждения** — оплачено, но покупатель ещё не подтвердил. Это
  не «уже заработано»: сделку могут откатить;
* **возвращено** — денег нет и не будет.

Поэтому «продано» считается отдельно от «можно забрать». Отчёт, в котором
они сложены, обещает деньги, которых может не быть.
"""
from __future__ import annotations

# Статусы площадки. Регистр и приставки убираются перед сравнением:
# библиотека отдаёт их то строкой, то перечислением.
RELEASED = ("CONFIRMED", "CONFIRMED_AUTOMATICALLY", "COMPLETED")
WAITING = ("PAID", "SENT", "PENDING")
REFUNDED = ("ROLLED_BACK", "CANCELLED", "REFUNDED")


def status_of(value) -> str:
    """Статус сделки одним словом, как его писать в сравнении."""
    name = getattr(value, "name", value)

    return str(name or "").strip().upper()


class Money:
    """Итог по одному товару: сколько продано и в каком состоянии."""

    # Сколько сделок помнить поимённо. Экран телефона, а не отчётность:
    # продавцу нужно узнать свои продажи, а не перечитать их все.
    KEEP = 8

    def __init__(self, label: str = ""):
        self.label = label      # как называется эта кучка на экране
        self.count = 0          # сделок, принёсших деньги
        self.sold = 0.0         # продано всего, ₽
        self.released = 0.0     # подтверждено — можно забирать
        self.waiting = 0.0      # ждёт подтверждения покупателем
        self.refunded = 0.0     # возвращено покупателю
        self.refunds = 0        # сколько таких сделок
        self.unknown = 0        # сделок без суммы
        self.last: list = []    # свежие сделки: (название, статус, сумма)
        self.names: dict = {}   # название → сколько раз продано

    def add(self, status: str, amount, title: str = "") -> None:
        state = status_of(status)

        if len(self.last) < self.KEEP:
            self.last.append((str(title or ""), state, amount))

        try:
            money = float(amount)
        except (TypeError, ValueError):
            money = None

        if state in REFUNDED:
            self.refunds += 1

            if money is not None:
                self.refunded += money

            return

        if state not in RELEASED and state not in WAITING:
            # Незнакомый статус — не деньги. Придумывать ему место в
            # отчёте о деньгах нельзя.
            return

        self.count += 1
        name = " ".join(str(title or "").split())

        if name:
            self.names[name] = self.names.get(name, 0) + 1

        if money is None:
            self.unknown += 1
            return

        self.sold += money

        if state in RELEASED:
            self.released += money
        else:
            self.waiting += money


def by_card(rows, card_of) -> dict:
    """Сделки → {ключ товара: Money}. Чужие складываются в "".

    `card_of` — как узнать товар по названию сделки. Передаётся снаружи,
    потому что узнавание живёт в одном месте на весь проект.
    """
    out: dict = {}

    for row in rows:
        title, status, amount, _game = row_of(row)
        card = card_of(str(title or ""))
        key = getattr(card, "slug", "") or ""
        out.setdefault(key, Money()).add(status, amount, title)

    return out


def row_of(row) -> tuple:
    """Строка сделки в общем виде: (название, статус, сумма, игра).

    Читаем и короткую тройку, и четвёрку с игрой: строки приходят из двух
    мест — от площадки и из тестов, — и требовать от обоих одного размера
    значит чинить их каждый раз, когда прибавится поле.
    """
    row = tuple(row)

    if len(row) >= 4:
        return row[0], row[1], row[2], str(row[3] or "")

    return row[0], row[1], row[2], ""


def by_group(rows, card_of, head_of, game_of=None) -> dict:
    """Сделки → {ключ: Money} по КАТЕГОРИИ ТОВАРА.

    Что считать категорией, решают три источника по убыванию точности:

    1. **игра и категория площадки** — «Roblox · Промокоды», «ChatGPT».
       Это то, как продавец и сам делит свой товар, и берётся оно у
       площадки, а не угадывается;
    2. **наша карта** — когда игру площадка не отдала, но название
       узнаётся: про свои карты бот знает и закупку, и выдачу;
    3. **начало названия** — последний рубеж, когда не известно ничего.

    Раньше первого источника не было вовсе, и «Roblox промокодом» и
    «Roblox геймпассом» оказывались в разных кучках по эмодзи в названии,
    а два похожих названия одной игры — в одной.
    """
    out: dict = {}

    for row in rows:
        title, status, amount, game = row_of(row)

        if not game and game_of is not None:
            game = str(game_of(row) or "")

        if game:
            key, label = "и:" + game.lower(), game
        else:
            card = card_of(str(title or ""))

            if card is not None:
                key = "к:" + str(getattr(card, "slug", ""))
                label = f"{getattr(card, 'emoji', '')} " \
                        f"{getattr(card, 'title', '')}".strip()
            else:
                label = head_of(str(title or "")) or "Прочее"
                key = "н:" + label.lower()

        out.setdefault(key, Money(label)).add(status, amount, title)

    return out


def rows_of(found):
    """Итоги списком, как бы их ни передали: словарём или списком."""
    return list(found.values()) if hasattr(found, "values") else list(found)


def total_of(found) -> Money:
    """Сложить всё в один итог."""
    whole = Money()

    for one in rows_of(found):
        whole.count += one.count
        whole.sold += one.sold
        whole.released += one.released
        whole.waiting += one.waiting
        whole.refunded += one.refunded
        whole.refunds += one.refunds
        whole.unknown += one.unknown

    return whole


def shares(found: dict, money: float, field: str = "released") -> list:
    """Разложить сумму по категориям в долях их продаж.

    → [(ключ, Money, доля 0..1, сколько приходится)], крупные первыми.

    Зачем это нужно и почему это ПРИКИДКА. Площадка держит деньги общей
    кучей: сколько из доступного к выводу пришло с Xbox, а сколько с
    аккаунтов, она не говорит — у неё просто одна сумма. Разложить её
    можно только по доле подтверждённых продаж, и это честная оценка, а
    не факт. Поэтому доля считается тут, а называется прикидкой там, где
    показывается.

    Если продаж нет вовсе, делить нечего: возвращается пустой список, а
    не деление на ноль и не «поровну».
    """
    pairs = [(getattr(one, "label", "") or "", one)
             for one in rows_of(found)]
    rows = [(key, one, float(getattr(one, field, 0.0) or 0.0))
            for key, one in pairs]
    whole = sum(value for _, _, value in rows)

    if whole <= 0:
        return []

    out = []

    for key, one, value in rows:
        if value <= 0:
            continue

        share = value / whole
        out.append((key, one, share, share * float(money or 0.0)))

    out.sort(key=lambda row: -row[3])

    return out
