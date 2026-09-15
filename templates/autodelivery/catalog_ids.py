"""Разведка каталога площадки: ID, без которых нельзя создать товар.

Создание товара требует id категории игры, id способа получения и набор
полей с данными. Угадать их невозможно, а в открытой документации у
площадки их нет — читаем у неё самой.

    python3 catalog_ids.py roblox              # игры и их категории
    python3 catalog_ids.py roblox КАТЕГОРИЯ_ID # способы получения и поля

Скрипт ТОЛЬКО ЧИТАЕТ. Ничего не создаёт, не публикует и не тратит денег:
у площадки публикация товара со статусом приоритета платная, и случайная
публикация из разведочного скрипта была бы списанием с баланса.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import sign_in                                      # noqa: E402
from envfile import load_env_file                             # noqa: E402


def show_games(account, search: str) -> None:
    page = account.get_games(name=search, count=24)
    games = list(getattr(page, "games", None) or [])

    if not games:
        print(f"По запросу «{search}» игр не нашлось.")
        print("Попробуйте короче: «roblox», «steam», «brawl».")
        return

    for game in games:
        print(f"\nигра: {game.name}")
        print(f"  id   : {game.id}")
        print(f"  slug : {game.slug}")

        categories = list(getattr(game, "categories", None) or [])

        if not categories:
            # Список игр отдаёт усечённый объект — та же беда, что с чатом
            # у сделок. Дочитываем игру отдельным запросом.
            try:
                categories = list(
                    getattr(account.get_game(id=game.id), "categories", None) or [])
            except Exception as e:                            # noqa: BLE001
                print(f"  категории прочитать не вышло: {e}")
                continue

        if not categories:
            print("  категорий нет")
            continue

        print("  категории:")

        for category in categories:
            print(f"    {category.id}  —  {category.name}")

    print("\nДальше: python3 catalog_ids.py <игра> <ID категории>")


def show_category(account, category_id: str) -> None:
    print(f"категория: {category_id}\n")

    try:
        category = account.get_game_category(id=category_id)
        print(f"название: {category.name}")
    except Exception as e:                                    # noqa: BLE001
        print(f"саму категорию прочитать не вышло: {e}")
        category = None

    options = list(getattr(category, "options", None) or [])

    if options:
        print("\nопции (атрибуты) товара:")

        for option in options:
            print(f"  {option.field} = {option.value!r}   ({option.label})")

    try:
        page = account.get_game_category_obtaining_types(category_id, count=24)
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"Способы получения прочитать не вышло: {e}")

    types_ = list(getattr(page, "obtaining_types", None) or [])

    if not types_:
        print("\nСпособов получения нет — в этой категории товар не создать.")
        return

    for obtaining in types_:
        print(f"\nспособ получения: {obtaining.name}")
        print(f"  id: {obtaining.id}")

        try:
            fields = list(getattr(
                account.get_game_category_data_fields(category_id, obtaining.id,
                                                      count=24),
                "data_fields", None) or [])
        except Exception as e:                                # noqa: BLE001
            print(f"  поля прочитать не вышло: {e}")
            continue

        if not fields:
            print("  полей с данными нет")
            continue

        print("  поля с данными:")

        for field in fields:
            kind = getattr(field.type, "name", field.type)
            mark = "обязательное" if field.required else "необязательное"
            # Заполнять при создании надо ТОЛЬКО ITEM_DATA. Поля
            # OBTAINING_DATA вводит сам покупатель при оформлении.
            whose = "наше" if str(kind) == "ITEM_DATA" else "покупателя"
            print(f"    {field.id}  —  {field.label}  [{kind}, {mark}, {whose}]")


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    account, _store, _link = sign_in()

    if len(sys.argv) >= 3:
        show_category(account, sys.argv[2])
    else:
        show_games(account, sys.argv[1])


if __name__ == "__main__":
    main()
