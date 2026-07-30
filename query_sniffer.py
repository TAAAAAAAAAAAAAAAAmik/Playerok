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


def load_hashes() -> dict[str, str]:
    """Снятые ранее хэши; пустой словарь, если файла нет."""
    if not os.path.exists(QUERIES_FILE):
        return {}
    try:
        with open(QUERIES_FILE, encoding="utf-8") as f:
            return json.load(f).get("hashes", {})
    except (OSError, ValueError) as e:
        logger.warning("Не смог прочитать %s: %s", QUERIES_FILE, e)
        return {}


def save_hashes(hashes: dict[str, str]):
    with open(QUERIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"saved_at": time.time(), "hashes": hashes}, f, indent=2)


def _extract(payload: dict) -> tuple[str, str] | None:
    """Из тела запроса достаёт (operationName, sha256Hash), если они есть."""
    operation = payload.get("operationName")
    extensions = payload.get("extensions")
    if isinstance(extensions, str):
        try:
            extensions = json.loads(extensions)
        except ValueError:
            return None
    if not operation or not isinstance(extensions, dict):
        return None
    sha = (extensions.get("persistedQuery") or {}).get("sha256Hash")
    return (operation, sha) if sha else None


def _from_url(url: str) -> tuple[str, str] | None:
    query = parse_qs(urlparse(url).query)
    if not query:
        return None
    return _extract({k: v[0] for k, v in query.items()})


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

    return hashes


def refresh(pages: list[str] | None = None) -> dict[str, str]:
    """Открывает браузер, собирает хэши и дописывает их в файл."""
    from selenium_creator import PlayerokBrowser

    with PlayerokBrowser() as browser:
        browser.authorize()
        fresh = collect(browser, pages)

    if not fresh:
        raise RuntimeError("Не поймал ни одного persisted-запроса")

    hashes = load_hashes()
    hashes.update(fresh)
    save_hashes(hashes)
    logger.info("Снято операций: %s", ", ".join(sorted(fresh)))
    return hashes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    pages = list(DEFAULT_PAGES)
    for slug in sys.argv[1:]:
        pages.append(f"/{slug}")

    for operation, sha in sorted(refresh(pages).items()):
        print(f"{operation:35} {sha}")
    print(f"\nСохранено в {QUERIES_FILE}")
