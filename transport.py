"""
Транспорт запросов к playerok.com.

Прямые запросы обычным HTTP-клиентом Playerok отбивает пятисоткой: перед API
стоит DDoS-Guard, который смотрит на TLS-отпечаток клиента и на куку `__ddg*`,
привязанную к IP и User-Agent. Поэтому:

* запросы идут через `curl_cffi` с `impersonate="chrome"` — отпечаток как у
  настоящего браузера;
* куки берутся из браузерной сессии: Selenium один раз открывает сайт, проходит
  проверку и отдаёт куки, которые складываются в COOKIES_FILE и переиспользуются;
* если сервер снова отвечает 403/500, куки обновляются браузером и запрос
  повторяется один раз.

Если `curl_cffi` не установлен, используется httpx — работать будет только
там, где защита не срабатывает.
"""
from __future__ import annotations

import json
import logging
import os
import time

import config

logger = logging.getLogger(__name__)

COOKIES_FILE = os.getenv("PLAYEROK_COOKIES_FILE", ".playerok_cookies.json")
# Куки моложе этого времени считаем годными и браузер не дёргаем.
COOKIES_TTL = int(os.getenv("PLAYEROK_COOKIES_TTL", "3600"))

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # пакет опционален
    curl_requests = None


class TransportError(RuntimeError):
    pass


# ── Куки ──────────────────────────────────────────────────────────────────────

def _parse_cookie_string(raw: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for chunk in raw.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            jar[key.strip()] = value.strip()
    return jar


def load_cookies() -> dict[str, str]:
    """Куки из файла, дополненные тем, что задано в .env."""
    jar: dict[str, str] = {}
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            jar.update(saved.get("cookies", {}))
        except (OSError, ValueError) as e:
            logger.warning("Не смог прочитать %s: %s", COOKIES_FILE, e)

    # Токен из .env главнее: это то, что пользователь задал руками.
    jar.update(_parse_cookie_string(config.PLAYEROK_COOKIES))
    return jar


def save_cookies(jar: dict[str, str]):
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump({"saved_at": time.time(), "cookies": jar}, f)


def cookies_age() -> float:
    """Сколько секунд назад куки обновляли браузером (inf — никогда)."""
    if not os.path.exists(COOKIES_FILE):
        return float("inf")
    try:
        with open(COOKIES_FILE, encoding="utf-8") as f:
            return time.time() - json.load(f).get("saved_at", 0)
    except (OSError, ValueError):
        return float("inf")


# Запуск браузера дорогой, а опрос идёт каждые POLL_INTERVAL секунд —
# поэтому между попытками обновить куки держим паузу.
REFRESH_COOLDOWN = int(os.getenv("PLAYEROK_REFRESH_COOLDOWN", "300"))
_last_refresh = 0.0


def refresh_cookies() -> dict[str, str]:
    """
    Открывает Playerok браузером, чтобы получить свежую куку DDoS-Guard,
    и складывает все куки сессии в файл.
    """
    global _last_refresh
    if time.time() - _last_refresh < REFRESH_COOLDOWN:
        raise TransportError(
            f"Куки обновляли меньше {REFRESH_COOLDOWN} с назад — жду, "
            "чтобы не поднимать браузер на каждый запрос"
        )
    _last_refresh = time.time()

    from selenium_creator import PlayerokBrowser  # импорт здесь: браузер нужен редко

    logger.info("Обновляю куки через браузер…")
    with PlayerokBrowser() as browser:
        browser.authorize()
        jar = {c["name"]: c["value"] for c in browser.driver.get_cookies()}

    jar.update(_parse_cookie_string(config.PLAYEROK_COOKIES))
    save_cookies(jar)
    logger.info("Куки обновлены: %s", ", ".join(sorted(jar)))
    return jar


# ── Запросы ───────────────────────────────────────────────────────────────────

def _request(method: str, cookies: dict[str, str], **kwargs):
    headers = {
        "accept": "*/*",
        "content-type": kwargs.pop("content_type", "application/json"),
        "origin": config.PLAYEROK_BASE_URL,
        "referer": config.PLAYEROK_BASE_URL + "/",
        "user-agent": config.SELENIUM_USER_AGENT,
    }
    if headers["content-type"] is None:
        headers.pop("content-type")

    if curl_requests is not None:
        return curl_requests.request(
            method.upper(),
            config.PLAYEROK_API_URL,
            headers=headers,
            cookies=cookies,
            impersonate="chrome",
            timeout=60,
            **kwargs,
        )

    import httpx

    logger.warning("curl_cffi не установлен — запрос пойдёт httpx, защита может его отбить")
    with httpx.Client(headers=headers, cookies=cookies, timeout=60.0) as client:
        return client.request(method.upper(), config.PLAYEROK_API_URL, **kwargs)


def request(method: str, *, retry: bool = True, **kwargs) -> dict:
    """
    Запрос к /graphql. При отказе защиты обновляет куки браузером и повторяет.
    Возвращает разобранный JSON, поднимает TransportError на ошибках GraphQL.
    """
    cookies = load_cookies()
    if not cookies or cookies_age() > COOKIES_TTL:
        try:
            cookies = refresh_cookies()
        except Exception as e:
            logger.warning("Не смог обновить куки браузером: %s", e)

    resp = _request(method, cookies, **kwargs)

    if resp.status_code in (403, 500, 503) and retry:
        logger.info("Ответ %s — похоже на защиту, обновляю куки", resp.status_code)
        try:
            cookies = refresh_cookies()
        except Exception as e:
            raise TransportError(f"Защита Playerok не пройдена: {e}") from e
        resp = _request(method, cookies, **kwargs)

    if resp.status_code >= 400:
        raise TransportError(f"HTTP {resp.status_code} от {config.PLAYEROK_API_URL}")

    try:
        data = resp.json()
    except ValueError as e:
        raise TransportError(f"Ответ не JSON: {e}") from e

    if "errors" in data:
        raise TransportError("; ".join(e.get("message", str(e)) for e in data["errors"]))

    return data.get("data") or {}
