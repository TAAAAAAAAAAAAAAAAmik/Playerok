"""Пробная покупка: единственная честная проверка права orders:write.

    python3 trial.py

ЗАЧЕМ. Дословно из кабинета поставщика: без права `orders:write` ключ
ПОКУПАЕТ, НО КОДА НЕ ОТДАЁТ. Деньги списаны, выдать нечего — и узнаёт об
этом продавец от покупателя, который уже заплатил.

Проверить это без покупки нельзя. Сухого прогона для магазинных заказов у
поставщика не существует: `checkOnly` разрешён только для прямых
пополнений. А отказ на выдуманном номинале приходит по форме запроса — он
о правах не говорит ничего, потому что поля могли проверить раньше прав.

Поэтому проверка одна: купить самый дешёвый номинал по-настоящему.

ЧТО ЭТО СТОИТ. Ровно цену самого дешёвого номинала выбранной карты —
скрипт покажет её и спросит согласия, прежде чем тратить. Код не
пропадает: он ваш, его можно активировать или продать.

ЧЕГО СКРИПТ НЕ ДЕЛАЕТ. Не трогает площадку, не создаёт объявлений, не
отмечает заказы. Только поставщик и только один номинал.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "code"))

from cards import CARDS, card_by_slug                          # noqa: E402
from catalog import denominations_for                          # noqa: E402
from envfile import load_env_file                              # noqa: E402
from supplier import ApprouteSupplier, SupplierError           # noqa: E402

# Слово согласия. Кнопки «да» мало: тут тратятся настоящие деньги, и
# случайный Enter не должен ничего покупать.
YES = "покупаю"

# Ссылка пробной покупки. Своя приставка — чтобы проба никогда не
# столкнулась с боевой выдачей: ссылка уникальна в пределах кабинета
# поставщика, и совпади они, настоящий заказ получил бы пробный код.
PREFIX = "trial"

# Сколько ждать код у принятого заказа.
POLL_STEPS = (5, 5, 10, 10, 15, 15, 20, 30)


def ask(question: str) -> str:
    try:
        return input(question).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def cheapest(supplier, card):
    """Самый дешёвый номинал карты в наличии → (номинал, услуга) или None."""
    print(f"Читаю каталог поставщика (разрешено 2 раза в минуту)…")

    try:
        catalog = supplier.services()
    except SupplierError as e:
        raise SystemExit(f"Каталог не прочитан: {e}")

    rows = [r for r in denominations_for(card, catalog)
            if r.in_stock > 0 and r.price is not None]

    if not rows:
        raise SystemExit(
            f"У поставщика нет номиналов «{card.title}» в наличии. "
            f"Подкатегория, по которой ищем: «{card.subcategory}».")

    rows.sort(key=lambda r: r.price)

    return rows[0]


def wait_code(supplier, reference: str) -> list:
    """Дождаться кода по ссылке. Тот же путь, которым ходит выдача."""
    for step in POLL_STEPS:
        time.sleep(step)
        got = supplier.by_reference(reference)

        if not got.get("ok"):
            print(f"  опрос: {got.get('why')}")
            continue

        if got.get("codes"):
            return list(got["codes"])

        status = str(got.get("status") or "")
        print(f"  статус заказа: {status or 'ещё нет'}")

        if status in ("SUCCESS", "PARTIALLY_COMPLETED", "CANCELLED"):
            return []

    return []


def main() -> None:
    load_env_file(os.path.join(HERE, ".env"))
    key = os.environ.get("APPROUTE_KEY", "").strip()

    if not key:
        raise SystemExit("В .env нет APPROUTE_KEY — покупать нечем.")

    slug = (sys.argv[1] if len(sys.argv) > 1 else "robux").strip()
    card = card_by_slug(slug)

    if card is None:
        names = ", ".join(c.slug for c in CARDS)
        raise SystemExit(f"Нет карты «{slug}». Есть: {names}")

    supplier = ApprouteSupplier(
        api_key=key, proxy=os.environ.get("APPROUTE_PROXY", ""))
    row = cheapest(supplier, card)

    print()
    print(f"Самый дешёвый номинал «{card.title}»:")
    print(f"  {row.title}")
    print(f"  регион : {row.region or 'не указан'}")
    print(f"  цена   : {row.price} USD")
    print(f"  остаток: {row.in_stock}")
    print()
    print("Это НАСТОЯЩАЯ покупка: деньги спишутся со счёта у поставщика.")
    print("Код останется вам — его можно активировать или продать.")
    print()

    if ask(f"Чтобы купить, напишите «{YES}»: ") != YES:
        print("Ничего не куплено.")
        return

    reference = f"{PREFIX}-{card.slug}-{int(time.time())}"
    print(f"\nПокупаю. Ссылка заказа: {reference}")
    got = supplier.place(row.item_id, reference)

    if not got.get("ok"):
        why = str(got.get("why") or "")
        print(f"\n⛔ Поставщик отказал: {why}")

        if "orders:write" in why:
            print("\nЭто и есть та самая беда: у ключа нет права покупать.")
            print("Выдайте ключу orders: write в кабинете AppRoute.")

        print("\nДенег не потрачено.")
        return

    codes = [str(c) for c in (got.get("codes") or []) if c]
    status = str(got.get("status") or "")

    if not codes:
        print(f"Заказ принят (статус {status or 'без статуса'}). Жду код…")
        codes = wait_code(supplier, reference)

    if not codes:
        print("\n⚠️ Кода нет.")
        print("Деньги могли списаться — найдите заказ в кабинете поставщика "
              f"по ссылке {reference}.")
        print("Если заказ там успешен, а кода нет, дело именно в праве "
              "orders:write: ключ покупает, но кода не отдаёт.")
        return

    print("\n✅ Код получен — значит право orders:write есть и выдача "
          "работает целиком:")

    for code in codes:
        print(f"    {code}")

    print(f"\nАктивация: {card.activation}")


if __name__ == "__main__":
    main()
