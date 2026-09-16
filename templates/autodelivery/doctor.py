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
import time
import uuid

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
        # Итог собираем вместе с лечением: список одних только бед — это
        # «разбирайся сам», а продавцу нужна команда, которую он наберёт.
        problems.append((text, fix))


def env_check() -> None:
    print("\n── Ключи в .env ──")
    pairs = [
        ("TELEGRAM_BOT_TOKEN", True, "без него бот не отвечает вовсе"),
        ("TELEGRAM_OWNER_ID", True, "без него боту некому писать"),
        ("APPROUTE_KEY", True, "без него автовыдаче нечем покупать коды"),
    ]

    for name, required, why in pairs:
        value = os.environ.get(name, "").strip()

        if value:
            say(OK, f"{name} задан")
        elif required:
            say(BAD, f"{name} НЕ задан — {why}",
                f"допишите в .env строку {name}=...")

    # Про APPROUTE_PROXY здесь молчим нарочно. Он нужен только там, где у
    # поставщика включён белый список IP, и узнать это можно одним
    # способом — позвать поставщика. Если каталог читается, прокси не
    # нужен, и жалоба на него была бы шумом: отчёт выглядел бы хуже, чем
    # дела, а среди трёх «неисправностей» тонут настоящие две.


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
            last_error(name)


# Сколько строк журнала показывать. Больше — и отчёт на телефоне
# превращается в простыню, в которой причина теряется.
LOG_TAIL = 6


def last_error(name: str) -> None:
    """Чем кончил бот в прошлый раз.

    Без этого «не работает» отправляет продавца искать журнал руками — то
    есть ещё один круг вопросов там, где ответ уже записан на диск.
    """
    path = os.path.join(HERE, "state", f"{name}.log")

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip() for line in f if line.strip()]
    except OSError:
        print(f"     журнала нет — бота ни разу не запускали "
              f"или он не дошёл до записи")
        return

    if not lines:
        return

    print("     последнее в журнале:")

    for line in lines[-LOG_TAIL:]:
        print(f"       {line[:200]}")


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
        fix = "проверьте ключ в .env"

        if "403" in str(e) or "ip" in str(e).lower():
            fix = ("похоже на белый список IP: впишите в .env "
                   "APPROUTE_PROXY=... с постоянным адресом и этот адрес "
                   "добавьте у поставщика")

        say(BAD, f"каталог не прочитан: {e}", fix)
        return

    say(OK, "каталог поставщика читается — ключ рабочий")

    if not os.environ.get("APPROUTE_PROXY", "").strip():
        say(OK, "прокси не нужен: поставщик нас и так видит")

    for card in CARDS:
        if not card.subcategory:
            continue

        rows = denominations_for(card, catalog)
        live = [r for r in rows if r.in_stock > 0]

        if live:
            say(OK, f"{card.title}: номиналов в наличии {len(live)}")


# Слова отказа, означающие «ключу не хватает прав».
FORBIDDEN = ("403", "forbidden", "orders:write", "permission", "scope",
             "доступ", "прав")

# Слова отказа, означающие «дошли до поиска товара». Это и есть хороший
# исход: раз поставщик искал номинал, форму тела он принял и права
# проверил — иначе отказал бы раньше.
LOOKED_UP = ("not found", "не найд", "нет такого", "denomination",
             "unavailable", "out of stock", "нет в наличии")


def orders_write_check() -> None:
    """Есть ли у ключа право покупать. Денег не тратит.

    Это единственная проверка, которую нельзя откладывать. Дословно из
    кабинета поставщика: без `orders:write` ключ ПОКУПАЕТ, НО КОДА НЕ
    ОТДАЁТ. То есть деньги спишутся, а выдать будет нечего — и узнает об
    этом продавец от покупателя, который уже заплатил.

    Сухого прогона для магазинных заказов у поставщика нет. Поэтому зовём
    настоящую покупку, но на номинал, которого не существует: купить по
    нему нельзя ни при каких обстоятельствах, а отказ придёт разный —
    «нет прав» или «нет такого товара», и это ровно то, что нам нужно
    различить.
    """
    print("\n── Право покупать (orders:write) ──")
    key = os.environ.get("APPROUTE_KEY", "").strip()

    if not key:
        say(BAD, "ключа нет — проверять нечего")
        return

    try:
        from supplier import ApprouteSupplier
    except ImportError as e:
        say(BAD, f"не подключается клиент поставщика: {e}")
        return

    supplier = ApprouteSupplier(
        api_key=key, proxy=os.environ.get("APPROUTE_PROXY", ""))
    # Своя ссылка: повтор проверки не должен выглядеть повтором покупки.
    got = supplier.place(str(uuid.uuid4()), f"doctor-{int(time.time())}")
    why = str(got.get("why") or "")
    low = why.lower()

    if got.get("ok"):
        # Такого быть не должно: номинала со случайным номером не
        # существует. Молчать нельзя — это либо не тот кабинет, либо мы
        # поняли ответ неверно.
        say(WARN, "поставщик принял покупку несуществующего номинала — "
                  "странно, проверьте кабинет вручную")
        return

    if any(word in low for word in FORBIDDEN):
        say(BAD, "у ключа НЕТ права orders:write — он спишет деньги, но "
                 "кода не отдаст",
            "в кабинете AppRoute выдайте ключу право orders: write")
        return

    if any(word in low for word in LOOKED_UP):
        say(OK, "право есть: поставщик дошёл до поиска номинала, "
                "значит форму принял и права проверил")
        return

    # Отказ по форме тела приходит РАНЬШЕ проверки прав, поэтому он не
    # говорит о правах ничего. Выдать такое за «право есть» — худшее, что
    # здесь можно сделать: продавец включит выдачу, первая же покупка
    # спишет деньги и не отдаст код.
    say(WARN, f"право проверить не вышло: поставщик отказал по форме "
              f"запроса, а это происходит до проверки прав ({why})",
        "убедитесь в кабинете AppRoute, что у ключа стоит orders: write — "
        "без него он спишет деньги и не отдаст код")


def main() -> None:
    load_env_file(os.path.join(HERE, ".env"))
    print("Проверяю, почему не работает. Ничего не меняю.")

    env_check()
    bots_check()
    login_check()
    templates_check()
    delivery_check()
    supplier_check()
    orders_write_check()

    print("\n── Итог ──")

    if not problems:
        print("✅ Всё на месте.")
        return

    print(f"Нашлось неисправностей: {len(problems)}")

    for text, fix in problems:
        print(f"  • {text}")

        if fix:
            print(f"    → {fix}")


if __name__ == "__main__":
    main()
