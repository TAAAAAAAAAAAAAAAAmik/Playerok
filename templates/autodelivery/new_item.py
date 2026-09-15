"""Создание товара на площадке: черновик, затем выставление по согласию.

Товар описывается файлом, а не вопросами в терминале: описание удобнее
готовить заранее и повторять, а набирать длинные тексты на телефоне —
мучение.

    python3 new_item.py --образец > robux-80.json   # заготовка
    python3 new_item.py robux-80.json               # создать ЧЕРНОВИК
    python3 new_item.py robux-80.json --выставить   # создать и выставить

ID категории и способа получения берутся из catalog_ids.py.

КАРТИНКА ОБЯЗАТЕЛЬНА, и не по требованию площадки, а из-за ошибки в
библиотеке: без файлов она отправляет запрос обычным JSON вместо
multipart, площадка не находит в нём текста запроса и отвечает
«GraphQL operations must contain a non-empty query». Поэтому либо укажите
файлы в attachments, либо скопируйте картинки с уже выставленного товара
полем copy_images_from — так проще всего, загружать ничего не надо.

ПРО ДЕНЬГИ. Создание черновика бесплатно. Выставление — платное, если
выбрать платный статус приоритета: сумма списывается с баланса площадки.
Поэтому бот показывает статусы с ценами и СПРАШИВАЕТ, а за платный
переспрашивает словом. Молча он не потратит ничего.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import sign_in                                      # noqa: E402
from envfile import load_env_file                             # noqa: E402
import listing                                                # noqa: E402

EXAMPLE = {
    "category_id": "ID категории из catalog_ids.py",
    "obtaining_type_id": "ID способа получения оттуда же",
    "name": "80 Robux",
    "price": 100,
    "description": "Регион кода: GL\nАктивируйте код на roblox.com/redeem.",
    "attributes": {},
    "data_fields": [],
    "attachments": [],
    "copy_images_from": "ID уже выставленного товара — с него возьмём картинки",
}

REQUIRED = ("category_id", "obtaining_type_id", "name", "price")


class Field:
    """Поле с данными в том виде, в каком его ждёт библиотека: id и value."""

    def __init__(self, id: str, value):                       # noqa: A002
        self.id = id
        self.value = value


def read_item(path: str) -> dict:
    """Описание товара из файла, с понятными отказами."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise SystemExit(f"Не открыть {path}: {e}")
    except ValueError as e:
        raise SystemExit(
            f"{path} — это не JSON: {e}\n"
            "Заготовку даст: python3 new_item.py --образец")

    if not isinstance(data, dict):
        raise SystemExit(f"{path} должен описывать один товар объектом.")

    missing = [k for k in REQUIRED if not str(data.get(k) or "").strip()]

    if missing:
        raise SystemExit(
            f"В {path} не хватает: {', '.join(missing)}.\n"
            "ID категории и способа получения берутся из catalog_ids.py")

    try:
        data["price"] = int(data["price"])
    except (TypeError, ValueError):
        raise SystemExit("Цена должна быть числом.")

    if data["price"] <= 0:
        raise SystemExit("Цена должна быть больше нуля.")

    return data


def borrow_images(account, item_id: str) -> list:
    """Картинки с уже выставленного товара → байты.

    Так их проще всего добыть: ничего не надо загружать на сервер, а
    оформление у однотипных товаров всё равно общее.
    """
    try:
        source = account.get_item(id=str(item_id))
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"Товар {item_id} прочитать не вышло: {e}")

    urls = [getattr(att, "url", "") for att in
            (getattr(source, "attachments", None) or [])]
    urls = [u for u in urls if u]

    if not urls:
        raise SystemExit(
            f"У товара {item_id} нет картинок — копировать нечего.")

    images = []

    for url in urls:
        try:
            images.append(account.download_file(url))
        except Exception as e:                                # noqa: BLE001
            print(f"  картинку не забрал: {e}")

    if not images:
        raise SystemExit("Ни одной картинки скачать не вышло.")

    print(f"Взял картинок с товара {item_id}: {len(images)}")

    return images


def create(account, item: dict):
    """Создать черновик. Денег не стоит."""
    fields = [Field(str(f.get("id")), f.get("value"))
              for f in (item.get("data_fields") or [])
              if isinstance(f, dict) and f.get("id")]

    attachments = list(item.get("attachments") or [])
    source = str(item.get("copy_images_from") or "").strip()

    if not attachments and source:
        attachments = borrow_images(account, source)

    if not attachments:
        raise SystemExit(
            "Нет ни одной картинки, а без них создать товар не получится.\n"
            "Причина — ошибка в библиотеке: без файлов она шлёт запрос "
            "обычным JSON вместо multipart, и площадка не находит в нём "
            "текста запроса.\n"
            "Проще всего скопировать оформление с уже выставленного "
            "товара — добавьте в описание строку:\n"
            '    "copy_images_from": "ID вашего товара"')

    item["attachments"] = attachments

    return account.create_item(
        game_category_id=str(item["category_id"]),
        obtaining_type_id=str(item["obtaining_type_id"]),
        name=str(item["name"]),
        price=int(item["price"]),
        description=str(item.get("description") or ""),
        options=item.get("attributes") or {},
        data_fields=fields,
        attachments=attachments,
    )


def ask_status(account, item_id: str, price: int):
    """Показать статусы приоритета и спросить. → статус или None."""
    try:
        statuses = account.get_item_priority_statuses(item_id, price)
    except Exception as e:                                    # noqa: BLE001
        print(f"\nСтатусы приоритета прочитать не вышло: {e}")
        return None

    rows = listing.ordered(statuses or [])

    if not rows:
        print("\nСтатусов приоритета нет — выставить нечем.")
        return None

    print("\nКак выставляем?\n")

    for number, status in enumerate(rows, start=1):
        print(f"  {number}. {listing.describe(status)}")

    print("\n  0. не выставлять, оставить черновиком")

    chosen = listing.pick(rows, input("\nНомер: "))

    if chosen is None:
        print("Оставляю черновиком.")
        return None

    if listing.needs_confirmation(chosen):
        # Платный статус спрашиваем ещё раз, словом: промах по номеру не
        # должен стоить денег.
        print(f"\nЭто платно: {listing.describe(chosen)}")
        print("Сумма спишется с баланса площадки.")

        if not listing.confirmed(input(f"Напишите «{listing.CONFIRM_WORD}» "
                                       f"для подтверждения: ")):
            print("Не подтверждено. Оставляю черновиком.")
            return None

    return chosen


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    args = sys.argv[1:]

    if not args:
        raise SystemExit(__doc__)

    if "--образец" in args or "--example" in args:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return

    path = args[0]
    publish = "--выставить" in args or "--publish" in args
    item = read_item(path)

    print(f"Товар: {item['name']}")
    print(f"Цена : {item['price']}")
    print("Создаю черновик…")

    account, _store, _link = sign_in()

    try:
        draft = create(account, item)
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(
            f"Создать не вышло: {e}\n"
            "Частые причины: не тот ID категории или способа получения, "
            "либо не заполнены обязательные поля с данными — их показывает "
            "catalog_ids.py")

    print(f"Черновик создан: {draft.id}")
    print(f"  https://playerok.com/products/{draft.id}")

    if not publish:
        print("\nВыставить: python3 new_item.py "
              f"{path} --выставить")
        return

    chosen = ask_status(account, draft.id, int(item["price"]))

    if chosen is None:
        return

    try:
        account.publish_item(draft.id, chosen.id)
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(
            f"Выставить не вышло: {e}\n"
            "Черновик при этом создан и никуда не делся — его видно в "
            "кабинете. Премиум-товары площадка бесплатным статусом "
            "выставить не даёт.")

    print(f"\nВыставлено: {listing.describe(chosen)}")


if __name__ == "__main__":
    main()
