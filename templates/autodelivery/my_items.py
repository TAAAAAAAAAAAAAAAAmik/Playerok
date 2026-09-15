"""Мои товары на площадке: ID, названия, статусы.

Нужен, чтобы взять ID уже выставленного товара — с него new_item.py
копирует картинки, без которых создать новый не получится.

    python3 my_items.py                # выставленные
    python3 my_items.py черновики      # черновики
    python3 my_items.py всё            # всё, что есть

Только читает.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import sign_in                                      # noqa: E402
from envfile import load_env_file                             # noqa: E402

# Что показывать по слову. Имена статусов — из библиотеки.
WHAT = {
    "": ["APPROVED"],
    "выставленные": ["APPROVED"],
    "черновики": ["DRAFT"],
    "проданные": ["SOLD"],
    "всё": ["APPROVED", "DRAFT", "SOLD", "PENDING_APPROVAL",
            "PENDING_MODERATION", "DECLINED", "EXPIRED", "BLOCKED"],
}


def statuses_by_name(names: list):
    try:
        from playerokapi.enums import ItemStatuses
    except ImportError:
        raise SystemExit("Не установлена библиотека playerokapi.")

    return [ItemStatuses[n] for n in names if n in ItemStatuses.__members__]


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    word = (sys.argv[1].lower() if len(sys.argv) > 1 else "")

    if word not in WHAT:
        raise SystemExit(
            f"Не знаю «{word}». Можно: {', '.join(k for k in WHAT if k)}")

    account, _store, _link = sign_in()
    wanted = statuses_by_name(WHAT[word])
    cursor = None
    total = 0

    # Листаем страницы: первая — это не «все», и на этом уже обжигались
    # с заказами.
    for _ in range(20):
        try:
            page = account.get_my_items(statuses=wanted, count=24,
                                        after_cursor=cursor)
        except Exception as e:                                # noqa: BLE001
            raise SystemExit(f"Товары прочитать не вышло: {e}")

        items = list(getattr(page, "items", None) or [])

        for item in items:
            total += 1
            status = getattr(getattr(item, "status", None), "name", "?")
            price = getattr(item, "price", None)
            print(f"\n{item.name}")
            print(f"  id     : {item.id}")
            print(f"  статус : {status}")

            if price is not None:
                print(f"  цена   : {price}")

        info = getattr(page, "page_info", None)

        if not getattr(info, "has_next_page", False):
            break

        cursor = getattr(info, "end_cursor", None)

        if not cursor:
            break

    if not total:
        print("Ничего не нашлось.")
        return

    print(f"\nВсего: {total}")
    print("\nЧтобы взять с товара картинки для нового, добавьте в описание:")
    print('  "copy_images_from": "<id отсюда>"')


if __name__ == "__main__":
    main()
