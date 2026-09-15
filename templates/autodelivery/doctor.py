"""Почему не работает: одна команда вместо десяти вопросов.

    python3 doctor.py

Проверяет по порядку то, из-за чего автовыдача и боты молчат чаще всего:
ключи в .env, вход в кабинет, запущенные процессы, свежесть кода,
шаблоны, включённые карты и наличие номиналов у поставщика.

Только читает. Ничего не покупает, не создаёт и не правит: скрипт,
который «чинит» на ходу, однажды починит не то.
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "code"))

from envfile import load_env_file                             # noqa: E402

OK, WARN, BAD = "✅", "⚠️ ", "⛔"

# Что должно работать и зачем. Без этого списка «бот не запущен» ничего не
# говорит: продавцу надо знать, чего именно он лишился.
BOTS = {
    "item_bot": "разговор в телеграме: создание товаров и настройки",
    "delivery_bot": "АВТОВЫДАЧА: покупает код и шлёт покупателю",
    "notify_bot": "уведомления: покупки, сообщения, проблемы, отзывы",
}

problems: list = []


def say(mark: str, text: str, fix: str = "") -> None:
    print(f"{mark} {text}")

    if fix:
        print(f"     → {fix}")

    if mark != OK:
        problems.append(text)


def env_check() -> None:
    print("\n── Ключи в .env ──")
    pairs = [
        ("TELEGRAM_BOT_TOKEN", True, "без него бот не отвечает вовсе"),
        ("TELEGRAM_OWNER_ID", True, "без него боту некому писать"),
        ("APPROUTE_KEY", True, "без него автовыдаче нечем покупать коды"),
        ("APPROUTE_PROXY", False,
         "нужен, если у поставщика включён белый список IP"),
    ]

    for name, required, why in pairs:
        value = os.environ.get(name, "").strip()

        if value:
            say(OK, f"{name} задан")
        elif required:
            say(BAD, f"{name} НЕ задан — {why}",
                f"допишите в .env строку {name}=...")
        else:
            say(WARN, f"{name} не задан — {why}")


def running() -> dict:
    """Что из ботов сейчас живо."""
    alive = {}

    for name in BOTS:
        try:
            found = subprocess.run(
                ["pgrep", "-f", f"python3 -u {name}.py"],
                capture_output=True, text=True, timeout=10)
            alive[name] = bool(found.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            alive[name] = None

    return alive


def bots_check() -> None:
    print("\n── Что запущено ──")
    alive = running()

    for name, what in BOTS.items():
        if alive.get(name) is None:
            say(WARN, f"{name}: проверить не вышло (нет pgrep)")
        elif alive[name]:
            say(OK, f"{name} работает — {what}")
        else:
            say(BAD, f"{name} НЕ работает — {what}",
                f"sh {HERE}/run_bot.sh {name}.py")


def login_check():
    """Вход в кабинет. → аккаунт или None."""
    print("\n── Вход в кабинет площадки ──")

    try:
        from auth import sign_in
    except ImportError as e:
        say(BAD, f"не подключается вход: {e}")
        return None

    try:
        account, _, _ = sign_in()
    except SystemExit as e:
        # sign_in останавливает обычный запуск, когда чего-то не хватает.
        # Здесь останавливаться нельзя: доктор для того и нужен, чтобы
        # назвать ВСЕ беды разом, а не первую.
        say(BAD, f"войти не вышло: {e}")
        return None
    except Exception as e:                                    # noqa: BLE001
        say(BAD, f"войти не вышло: {e}",
            "откройте бота, «👤 Аккаунт» → пришлите свежие куки")
        return None

    if account is None:
        say(BAD, "кабинет не открылся",
            "откройте бота, «👤 Аккаунт» → пришлите свежие куки")
        return None

    try:
        say(OK, f"вошли как {account.profile.username}")
    except Exception:                                         # noqa: BLE001
        say(OK, "вошли (имя прочитать не вышло)")

    return account


def templates_check() -> None:
    print("\n── Шаблоны ──")

    try:
        from accounts import AccountStore
        from templates import TemplateStore, folder_for
    except ImportError as e:
        say(BAD, f"не подключаются шаблоны: {e}")
        return

    accounts_dir = os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts")
    root = os.environ.get("PLAYEROK_TEMPLATES", "state/templates")
    current = AccountStore(os.path.join(HERE, accounts_dir)).current()
    name = current.name if current else "без кабинета"
    store = TemplateStore(folder_for(os.path.join(HERE, root),
                                     current.id if current else ""))
    saved = store.all()

    if not saved:
        say(WARN, f"у кабинета «{name}» шаблонов нет",
            "создайте товар и сохраните его шаблоном")
        return

    say(OK, f"кабинет «{name}», шаблонов: {len(saved)}")

    for template in saved:
        if not template.complete():
            say(WARN, f"«{template.name}»: сохранён до того, как бот стал "
                      f"спрашивать категорию — повторить нечем")
        elif not template.photos():
            say(WARN, f"«{template.name}»: пропали картинки — "
                      f"без них товар не создать")
        else:
            say(OK, f"«{template.name}» — {template.price} ₽, повторяется")


def delivery_check() -> None:
    print("\n── Автовыдача: что включено ──")

    try:
        from cards import CARDS
        from accounts import AccountStore
        from settings import Settings
        from store import JsonStore
    except ImportError as e:
        say(BAD, f"не подключаются настройки: {e}")
        return

    accounts_dir = os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts")
    state = os.environ.get("PLAYEROK_STATE", "state/delivery")
    current = AccountStore(os.path.join(HERE, accounts_dir)).current()
    name = current.id if current else "default"
    conf = Settings(JsonStore(os.path.join(HERE, state, f"{name}.json")))
    on = [c for c in CARDS if conf.card(c.slug)["enabled"]]

    if not on:
        say(BAD, "не включена ни одна карта — выдавать бот ничего не будет",
            "бот → «⚙️ Автовыдача» → выберите товар → «▶️ Включить»")
        return

    for card in on:
        say(OK, f"{card.emoji} {card.title} включена")


def supplier_check() -> None:
    print("\n── Поставщик ──")
    key = os.environ.get("APPROUTE_KEY", "").strip()

    if not key:
        say(BAD, "ключа нет — каталог не прочитать")
        return

    try:
        from cards import CARDS
        from catalog import denominations_for
        from supplier import ApprouteSupplier
    except ImportError as e:
        say(BAD, f"не подключается клиент поставщика: {e}")
        return

    try:
        catalog = ApprouteSupplier(
            api_key=key,
            proxy=os.environ.get("APPROUTE_PROXY", "")).services()
    except Exception as e:                                    # noqa: BLE001
        say(BAD, f"каталог не прочитан: {e}",
            "проверьте ключ и белый список IP у поставщика")
        return

    say(OK, "каталог поставщика читается")

    for card in CARDS:
        if not card.subcategory:
            continue

        rows = denominations_for(card, catalog)
        live = [r for r in rows if r.in_stock > 0]

        if live:
            say(OK, f"{card.title}: номиналов в наличии {len(live)}")


def main() -> None:
    load_env_file(os.path.join(HERE, ".env"))
    print("Проверяю, почему не работает. Ничего не меняю.")

    env_check()
    bots_check()
    login_check()
    templates_check()
    delivery_check()
    supplier_check()

    print("\n── Итог ──")

    if not problems:
        print("✅ Всё на месте.")
        return

    print(f"Нашлось неисправностей: {len(problems)}")

    for text in problems:
        print(f"  • {text}")


if __name__ == "__main__":
    main()
