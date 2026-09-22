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

    for title, status, amount in rows:
        card = card_of(str(title or ""))
        key = getattr(card, "slug", "") or ""
        out.setdefault(key, Money()).add(status, amount, title)

    return out


def by_group(rows, card_of, head_of) -> dict:
    """Сделки → {ключ: Money} по категориям, а не только по нашим картам.

    Наши карты — своей кучкой: про них бот знает и закупку, и выдачу.
    Остальное группируется по началу названия: продавец торгует не только
    кодами, и «сколько принесли аккаунты» — такой же законный вопрос, как
    «сколько принёс Xbox». Разбор названий тот же, что в списках товаров:
    два разных однажды разошлись бы, и одна и та же продажа попадала бы в
    разные кучки на разных экранах.
    """
    out: dict = {}

    for title, status, amount in rows:
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


def total_of(found: dict) -> Money:
    """Сложить всё в один итог."""
    whole = Money()

    for one in found.values():
        whole.count += one.count
        whole.sold += one.sold
        whole.released += one.released
        whole.waiting += one.waiting
        whole.refunded += one.refunded
        whole.refunds += one.refunds
        whole.unknown += one.unknown

    return whole
