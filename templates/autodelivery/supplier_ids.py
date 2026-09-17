"""Разведка каталога поставщика: ID услуг и номиналов.

Чтобы движок покупал коды, ему нужны ID услуг AppRoute — те, что
подставляются в APPROUTE_SERVICE_ROBUX_GL и подобные. Взять их можно только
из каталога поставщика.

    python3 supplier_ids.py robux            # услуги и все их номиналы
    python3 supplier_ids.py robux --кратко   # только названия и ID
    python3 supplier_ids.py robux --регионы  # какие регионы бот видит
    python3 supplier_ids.py robux --raw      # плюс сырой ответ в файл

Режим «--регионы» отвечает на вопрос «почему при создании товара не все
регионы». Он берёт карту по её названию (robux, apple, xbox…), проходит по
её услугам у поставщика и показывает, какой регион бот прочитал у каждой.
Услуги, где регион прочитать не вышло, перечислены отдельно: их регионы
кнопками не появятся, потому что бот не знает, что они за регионы.

Кратко — когда услуг много: полный список номиналов на телефон не влезает,
а выбирать услугу всё равно надо по названию.

Скрипт ТОЛЬКО ЧИТАЕТ каталог. Ничего не покупает: ни одного вызова,
списывающего деньги, здесь нет. Чтение заказов (`by_reference`) тоже не
зовётся — у него есть побочное действие, поставщик помечает коды
полученными.

Осторожно с темпом: каталог разрешён ДВА раза в минуту. Если поставщик
попросит сбавить — просто подождите минуту.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from envfile import load_env_file                             # noqa: E402
from supplier import ApprouteSupplier, SupplierError          # noqa: E402

RAW_FILE = "supplier-catalog.json"


def rows(data) -> list:
    """Список услуг, как бы поставщик его ни завернул.

    Форма ответа у каталога не подтверждена живым вызовом, а у этого
    поставщика она уже дважды отличалась от собственной документации.
    Поэтому разбираем терпимо: ищем список там, где он может лежать.
    """
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in ("services", "items", "data", "results"):
        found = data.get(key)

        if isinstance(found, list):
            return found

        if isinstance(found, dict):
            deeper = rows(found)

            if deeper:
                return deeper

    page = data.get("page")

    return rows(page) if isinstance(page, dict) else []


def text_of(node: dict) -> str:
    """Всё текстовое из записи — по нему и ищем слово."""
    return " ".join(str(v) for v in node.values()
                    if isinstance(v, (str, int, float)))


def short(service: dict) -> None:
    """Одна строка на услугу: название, номер и сколько номиналов."""
    name = (service.get("name") or service.get("title")
            or service.get("serviceName") or "без названия")
    items = service.get("items") or service.get("denominations") or []
    count = len(items) if isinstance(items, list) else 0

    print(f"{service.get('id') or service.get('serviceId')}  "
          f"{name}  ({count})")


def show(service: dict) -> None:
    name = (service.get("name") or service.get("title")
            or service.get("serviceName") or "без названия")

    print(f"\nуслуга: {name}")
    print(f"  id  : {service.get('id') or service.get('serviceId')}")

    kind = service.get("type") or service.get("serviceType")

    if kind:
        print(f"  тип : {kind}")

    items = service.get("items") or service.get("denominations") or []

    if not isinstance(items, list) or not items:
        print("  номиналов в ответе нет")
        return

    print(f"  номиналы ({len(items)}):")

    for item in items:
        if not isinstance(item, dict):
            continue

        title = (item.get("name") or item.get("title")
                 or item.get("denomination") or "?")
        print(f"    {item.get('id')}  —  {title}  "
              f"| цена {item.get('price')} {item.get('currency') or ''}"
              f" | остаток {item.get('inStock')}")


def regions(search: str, data) -> bool:
    """Регионы карты глазами бота. → нашлась ли такая карта.

    Печатает ровно то, из чего складываются кнопки регионов в мастере:
    услуга → прочитанный регион → остаток. Если региона нет в списке, то
    и кнопки его не будет, и здесь сразу видно, почему: услуги нет вовсе,
    остаток нулевой или название не разобралось.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from cards import CARDS
    from catalog import (denominations_for, region_of_service,
                         services_for)

    card = next((c for c in CARDS
                 if search in c.slug or search in c.title.lower()), None)

    if card is None:
        print(f"Карты «{search}» нет. Есть: "
              + ", ".join(c.slug for c in CARDS))
        return False

    print(f"\n{card.emoji} {card.title} — подкатегория «{card.subcategory}»")

    found = services_for(card, data)

    if not found:
        print("  услуг этой подкатегории у поставщика не нашлось")
        print("  → проверьте точное имя подкатегории в cards.py")
        return True

    print(f"  услуг: {len(found)}")
    unknown = []

    for service in found:
        name = str(service.get("name") or service.get("title") or "?")
        region = region_of_service(service)

        if not region:
            unknown.append(name)

        print(f"    {region or '??'}  ←  {name}")

    rows = denominations_for(card, data)
    in_stock = sorted({r.region for r in rows if r.region and r.in_stock > 0})
    empty = sorted({r.region for r in rows if r.region} - set(in_stock))

    print(f"\n  кнопками в мастере покажутся ({len(in_stock)}): "
          + (", ".join(in_stock) or "ни одного"))

    if empty:
        print(f"  есть, но всё кончилось: {', '.join(empty)}")

    if unknown:
        print(f"\n  ⚠️  регион не прочитан у {len(unknown)} услуг:")

        for name in unknown[:15]:
            print(f"      {name}")

        print("  → пришлите эти названия — добавим их в разбор регионов")

    return True


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    search = sys.argv[1].lower()
    key = os.environ.get("APPROUTE_KEY", "").strip()

    if not key:
        raise SystemExit(
            "Нет APPROUTE_KEY. Это ключ из кабинета AppRoute; положите его "
            "в .env строкой APPROUTE_KEY=...")

    supplier = ApprouteSupplier(
        api_key=key,
        # Прокси обязателен там, где адрес сервера меняется: у поставщика
        # белый список IP. Диагностика обязана идти тем же путём, что и
        # покупка, иначе «ключ не работает» останется загадкой.
        proxy=os.environ.get("APPROUTE_PROXY", ""),
    )

    print("Читаю каталог поставщика (разрешено 2 раза в минуту)…")

    try:
        data = supplier.services()
    except SupplierError as e:
        raise SystemExit(
            f"Поставщик отказал: {e}\n"
            "Частые причины: ключ не тот, у ключа нет права на чтение "
            "каталога, или адрес сервера не в белом списке — тогда нужен "
            "APPROUTE_PROXY с постоянным адресом.")
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"Не удалось прочитать каталог: {e}")

    if "--raw" in sys.argv:
        with open(RAW_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"Сырой ответ сохранён в {RAW_FILE}")

    services = rows(data)

    if not services:
        print("Каталог прочитан, но список услуг в нём не найден.")
        print("Запустите с --raw и покажите начало файла — разберём форму.")
        return

    print(f"Всего услуг: {len(services)}")

    if "--регионы" in sys.argv or "--regions" in sys.argv:
        regions(search, data)
        return

    found = [s for s in services
             if isinstance(s, dict) and search in text_of(s).lower()]

    if not found:
        print(f"Со словом «{search}» ничего не нашлось.")
        return

    print(f"Со словом «{search}»: {len(found)}")

    brief = "--кратко" in sys.argv or "--short" in sys.argv

    if brief:
        print()

        for service in found:
            short(service)

        print("\nПодробнее по одной услуге: python3 supplier_ids.py "
              "<часть названия>")
        return

    for service in found:
        show(service)


if __name__ == "__main__":
    main()
