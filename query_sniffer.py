"""
Снимает актуальные sha256-хэши persisted-запросов Playerok.

Фронт Playerok отправляет запросы в формате Automatic Persisted Queries: вместо
текста запроса уходит его хэш. Хэши меняются при каждой сборке фронта, и чужой
или устаревший хэш сервер отвергает — например, `games` отвечает
«Access denied». Поэтому хэши не зашиваем, а подсматриваем: открываем сайт
браузером, читаем журнал сети и достаём пары operationName → sha256Hash.

    python query_sniffer.py            # обойти страницы и обновить файл
    python query_sniffer.py telegram   # заодно зайти на страницу игры

Результат складывается в QUERIES_FILE и подхватывается playerok_client.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from urllib.parse import parse_qs, urlparse

import config

logger = logging.getLogger(__name__)

QUERIES_FILE = os.getenv("PLAYEROK_QUERIES_FILE", ".playerok_queries.json")

# Страницы, на которых фронт успевает дёрнуть интересующие нас операции.
DEFAULT_PAGES = ["/", "/profile"]


def load_operations() -> dict[str, dict]:
    """
    Снятые операции: {имя: {"hash": ..., "variables": {...}}}.
    Переменные — образец того, что шлёт фронт: по ним видно, каких аргументов
    ждёт операция, когда она переименовалась и параметры поменялись.
    """
    if not os.path.exists(QUERIES_FILE):
        return {}
    try:
        with open(QUERIES_FILE, encoding="utf-8") as f:
            saved = json.load(f).get("hashes", {})
    except (OSError, ValueError) as e:
        logger.warning("Не смог прочитать %s: %s", QUERIES_FILE, e)
        return {}

    # Файлы старого формата хранили просто строку с хэшем.
    return {
        name: (value if isinstance(value, dict) else {"hash": value, "variables": {}})
        for name, value in saved.items()
    }


def load_hashes() -> dict[str, str]:
    """Только хэши — так их читает playerok_client."""
    return {name: op["hash"] for name, op in load_operations().items() if op.get("hash")}


def save_hashes(operations: dict):
    with open(QUERIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"saved_at": time.time(), "hashes": operations}, f,
                  indent=2, ensure_ascii=False)


def _extract(payload: dict) -> tuple[str, dict] | None:
    """Из запроса достаёт (operationName, {hash, variables}), если они есть."""
    operation = payload.get("operationName")
    extensions = payload.get("extensions")
    variables = payload.get("variables")
    for field in ("extensions", "variables"):
        value = extensions if field == "extensions" else variables
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if field == "extensions":
                extensions = parsed
            else:
                variables = parsed

    if not operation or not isinstance(extensions, dict):
        return None
    sha = (extensions.get("persistedQuery") or {}).get("sha256Hash")
    if not sha:
        return None
    return operation, {"hash": sha, "variables": variables if isinstance(variables, dict) else {}}


def _from_url(url: str) -> tuple[str, str] | None:
    query = parse_qs(urlparse(url).query)
    if not query:
        return None
    return _extract({k: v[0] for k, v in query.items()})


def _drain_log(browser, hashes: dict[str, str]):
    """Вычерпывает журнал сети и докладывает найденные операции."""
    for entry in browser.driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, ValueError):
            continue
        if message.get("method") != "Network.requestWillBeSent":
            continue

        request = message.get("params", {}).get("request", {})
        if "/graphql" not in (request.get("url") or ""):
            continue

        found = _from_url(request["url"])
        if not found and request.get("postData"):
            try:
                found = _extract(json.loads(request["postData"]))
            except ValueError:
                found = None
        if found:
            hashes[found[0]] = found[1]


def collect(browser, pages: list[str] | None = None) -> dict[str, str]:
    """
    Обходит страницы и собирает хэши из журнала сети. Браузер должен быть
    уже запущен и авторизован.
    """
    hashes: dict[str, str] = {}

    for page in pages or DEFAULT_PAGES:
        url = config.PLAYEROK_BASE_URL + page
        logger.info("Смотрю запросы на %s", url)
        try:
            browser.driver.get(url)
        except Exception as e:
            logger.warning("Не открылась %s: %s", url, e)
            continue
        browser._wait_for_render()
        time.sleep(3)  # фронт догружает данные уже после отрисовки
        _drain_log(browser, hashes)

    return hashes


def collect_from_wizard(browser, game: str = "Telegram",
                        category: str = "", obtaining: str = "") -> dict:
    """
    Часть операций фронт запрашивает только внутри мастера создания товара:
    список игр (`games`), способы передачи, поля данных. Открываем мастер и
    проходим первые шаги, снимая запросы по дороге.
    """
    hashes: dict[str, str] = {}
    try:
        browser._open_wizard()
        _drain_log(browser, hashes)

        search = browser._find_input("Поиск игр и приложений") or browser._find_input("Поиск")
        if search:
            search.send_keys(game)
            time.sleep(2.5)
            _drain_log(browser, hashes)

        # Выбор игры открывает категории, категория — способы передачи,
        # способ — поля данных. Идём по цепочке, снимая запросы.
        if browser._click_text(game, timeout=8, required=False):
            time.sleep(3)
            _drain_log(browser, hashes)

            if category and browser._click_text(category, timeout=8, required=False):
                browser._click_next(required=False, timeout=5)
                time.sleep(3)
                _drain_log(browser, hashes)

                if obtaining and browser._click_text(obtaining, timeout=8, required=False):
                    browser._click_next(required=False, timeout=5)
                    time.sleep(3)
                    _drain_log(browser, hashes)
    except Exception as e:
        logger.warning("Мастер прошёл не полностью: %s", e)
        _drain_log(browser, hashes)

    return hashes


def refresh(pages: list[str] | None = None, wizard: bool = True,
            game: str = "Telegram", category: str = "", obtaining: str = "") -> dict:
    """Открывает браузер, собирает хэши и дописывает их в файл."""
    from selenium_creator import PlayerokBrowser

    with PlayerokBrowser() as browser:
        browser.authorize()
        fresh = collect(browser, pages)
        if wizard:
            logger.info("Открываю мастер создания — там живут остальные операции")
            fresh.update(collect_from_wizard(browser, game, category, obtaining))

    if not fresh:
        raise RuntimeError("Не поймал ни одного persisted-запроса")

    operations = load_operations()
    operations.update(fresh)
    save_hashes(operations)
    logger.info("Снято операций: %s", ", ".join(sorted(fresh)))
    return operations


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # python query_sniffer.py [slug игры] [категория] [способ передачи]
    args = sys.argv[1:]
    slug = args[0] if args else "telegram"
    pages = DEFAULT_PAGES + [f"/{slug}"]

    operations = refresh(
        pages,
        game=args[1] if len(args) > 1 else slug.capitalize(),
        category=args[2] if len(args) > 2 else "",
        obtaining=args[3] if len(args) > 3 else "",
    )

    for name, op in sorted(operations.items()):
        print(f"\n{name}\n  hash: {op['hash']}")
        if op.get("variables"):
            print(f"  vars: {json.dumps(op['variables'], ensure_ascii=False)[:220]}")
    print(f"\nСохранено в {QUERIES_FILE}")
