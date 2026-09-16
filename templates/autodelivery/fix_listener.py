"""Доставить в playerokapi часть, которую она не устанавливает.

    python3 fix_listener.py

ЧТО СЛОМАНО. В `setup.py` библиотеки стоит `packages=find_packages()`, а
`find_packages` берёт только те папки, где есть `__init__.py`. У папки
`playerokapi/listener` его нет — и она не попадает в установку вовсе.
Соседние `enums.py`, `types.py`, `parser.py` устанавливаются, потому что
они файлы, а не папки; отсюда и путаница.

Как это выглядело: уведомления не приходили никогда, а notify_bot писал
«Не установлена библиотека playerokapi» — хотя библиотека стоит и всё
остальное на ней работает. Не хватало ровно двух файлов.

ЧТО ДЕЛАЕМ. Скачиваем `listener.py` и `events.py` из того же репозитория и
той же ветки, что закреплена в requirements.txt, кладём рядом с остальной
библиотекой и дописываем `__init__.py`.

Это заплатка чужой ошибки, и она честно названа заплаткой: при следующей
переустановке библиотеки её надо будет наложить снова. Скрипт можно звать
повторно — он ничего не ломает и молча уходит, если всё уже на месте.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

# Та же ветка, что и в requirements.txt: транспорт закреплён намеренно, и
# заплатка не должна тащить другую версию.
RAW = "https://raw.githubusercontent.com/alleexxeeyy/PlayerokAPI/main"

# Что именно не доехало.
FILES = ("listener.py", "events.py")


def package_dir() -> str:
    """Где лежит установленная playerokapi."""
    try:
        import playerokapi
    except ImportError as e:
        raise SystemExit(
            f"playerokapi не установлена вовсе: {e}\n"
            "Сначала: python3 -m pip install -r requirements.txt")

    path = os.path.dirname(os.path.abspath(playerokapi.__file__))

    if not os.path.isdir(path):
        raise SystemExit(f"Не нашёл папку библиотеки: {path}")

    return path


def works() -> tuple:
    """Идёт ли импорт слушателя → (идёт, чего не хватило).

    Причину возвращаем, а не глотаем: «не идёт» бывает от двух разных бед,
    и лечатся они по-разному. Не хватает самого слушателя — это наш
    случай, кладём файлы. Не хватает чего-то ещё (websocket, например) —
    класть файлы бесполезно, надо ставить зависимость.
    """
    try:
        from playerokapi.listener.listener import EventListener  # noqa: F401
    except Exception as e:                                    # noqa: BLE001
        return False, str(e)

    return True, ""


def fetch(name: str) -> bytes:
    url = f"{RAW}/playerokapi/listener/{name}"

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub ответил {e.code} на {name}: {e.reason}")
    except Exception as e:                                    # noqa: BLE001
        raise SystemExit(f"Не удалось скачать {name}: {e}")


def main() -> None:
    fine, _ = works()

    if fine:
        print("✅ Слушатель событий уже на месте — чинить нечего.")
        return

    where = os.path.join(package_dir(), "listener")
    print(f"Кладу недостающее в {where}")

    try:
        os.makedirs(where, exist_ok=True)
    except OSError as e:
        raise SystemExit(
            f"Не могу создать папку: {e}\n"
            "Похоже, библиотека установлена не в домашнюю папку. Тогда: "
            "python3 -m pip install --user -r requirements.txt")

    # __init__.py — то самое, из-за отсутствия которого папка и не попала
    # в установку. Пишем первым: без него остальное не импортируется.
    files = {"__init__.py": b""}

    for name in FILES:
        files[name] = fetch(name)
        print(f"  скачал {name}: {len(files[name])} байт")

    for name, data in files.items():
        with open(os.path.join(where, name), "wb") as f:
            f.write(data)

    fine, why = works()

    if not fine:
        # Наши два файла легли — значит не хватает чего-то ещё. Чаще всего
        # это websocket-client: слушатель работает вебсокетом, и без него
        # не поможет никакая доукомплектовка.
        missing = why.split("'")[1] if "'" in why else ""
        hint = ""

        if "websocket" in why:
            hint = ("\nСтавится так:\n"
                    "  python3 -m pip install --user websocket-client==1.8.0")
        elif missing:
            hint = (f"\nСтавится так:\n"
                    f"  python3 -m pip install --user {missing}")

        raise SystemExit(
            f"Файлы слушателя положил, но импорт всё равно не идёт: "
            f"{why}{hint}")

    print("✅ Готово. Теперь можно поднимать уведомления:")
    print(f"   sh {os.path.dirname(os.path.abspath(__file__))}"
          f"/run_bot.sh notify_bot.py")


if __name__ == "__main__":
    main()
