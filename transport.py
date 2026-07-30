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
import threading
import time
from typing import Optional
from urllib.parse import urlencode

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


def save_cookies(jar: dict[str, str], user_agent: str = ""):
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"saved_at": time.time(), "cookies": jar, "user_agent": user_agent}, f
        )


def load_user_agent() -> str:
    """
    User-Agent той сессии, в которой получены куки. Кука DDoS-Guard привязана
    к UA: запрос с другим User-Agent защита не примет.
    """
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, encoding="utf-8") as f:
                saved = json.load(f).get("user_agent")
            if saved:
                return saved
        except (OSError, ValueError):
            pass
    return (
        config.SELENIUM_MOBILE_USER_AGENT
        if config.SELENIUM_MOBILE
        else config.SELENIUM_USER_AGENT
    )


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
        user_agent = browser.driver.execute_script("return navigator.userAgent")

    jar.update(_parse_cookie_string(config.PLAYEROK_COOKIES))
    save_cookies(jar, user_agent)
    logger.info("Куки обновлены (UA %s): %s", user_agent[:40], ", ".join(sorted(jar)))
    return jar


# ── Запросы ───────────────────────────────────────────────────────────────────

def _request(method: str, cookies: dict[str, str], **kwargs):
    headers = {
        "accept": "*/*",
        "content-type": kwargs.pop("content_type", "application/json"),
        "origin": config.PLAYEROK_BASE_URL,
        "referer": config.PLAYEROK_BASE_URL + "/",
        "user-agent": load_user_agent(),
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


# ── Запрос из самого браузера ─────────────────────────────────────────────────
#
# Самый надёжный путь: fetch выполняется на открытой странице Playerok, так что
# куки, User-Agent и TLS-отпечаток совпадают по определению. Браузер держим
# один на процесс — поднимать его на каждый запрос слишком дорого.

_browser = None
_browser_lock = threading.Lock()

FETCH_SCRIPT = """
const cb = arguments[arguments.length - 1];
const [url, method, body] = arguments;
fetch(url, {
  method: method,
  headers: body ? {'content-type': 'application/json'} : {},
  body: body || undefined,
  credentials: 'include',
})
  .then(r => r.text().then(t => cb({status: r.status, body: t})))
  .catch(e => cb({status: 0, body: String(e)}));
"""


def _browser_instance():
    global _browser
    from selenium_creator import PlayerokBrowser

    if _browser is None or _browser.driver is None:
        browser = PlayerokBrowser()
        browser.start()
        browser.authorize()
        _browser = browser
    return _browser


def browser_request(method: str, json_body: Optional[dict] = None,
                    params: Optional[dict] = None) -> dict:
    """GraphQL-запрос через открытую страницу Playerok."""
    global _browser

    url = config.PLAYEROK_API_URL
    if params:
        url += "?" + urlencode(params)

    with _browser_lock:
        try:
            browser = _browser_instance()
        except Exception as e:
            _browser = None
            raise TransportError(f"Браузер не поднялся: {e}") from e

        try:
            result = browser.driver.execute_async_script(
                FETCH_SCRIPT,
                url,
                method.upper(),
                json.dumps(json_body) if json_body else None,
            )
        except Exception as e:
            # Браузер мог отвалиться — уронить его, чтобы следующий раз поднялся заново.
            try:
                browser.stop()
            finally:
                _browser = None
            raise TransportError(f"Запрос через браузер не удался: {e}") from e

    status = (result or {}).get("status", 0)
    body = (result or {}).get("body", "")
    if status >= 400 or status == 0:
        operation = (json_body or params or {}).get("operationName", "?")
        raise TransportError(f"Браузер получил {status} на {operation}: {body[:200]}")

    try:
        data = json.loads(body)
    except ValueError as e:
        raise TransportError(f"Ответ браузера не JSON: {e}") from e

    if "errors" in data:
        raise TransportError("; ".join(e.get("message", str(e)) for e in data["errors"]))
    return data.get("data") or {}


def _operation_name(kwargs: dict) -> str:
    """Имя операции — чтобы в логах было видно, какой запрос упал."""
    for source in (kwargs.get("json"), kwargs.get("params")):
        if isinstance(source, dict) and source.get("operationName"):
            return source["operationName"]
    return "?"


def request(method: str, *, retry: bool = True, **kwargs) -> dict:
    """
    Запрос к /graphql. При отказе защиты обновляет куки браузером и повторяет,
    а если и это не помогло — выполняет запрос прямо в браузере.
    Возвращает данные GraphQL, поднимает TransportError на ошибках.
    """
    operation = _operation_name(kwargs)

    if config.PLAYEROK_BROWSER_TRANSPORT:
        return browser_request(method, kwargs.get("json"), kwargs.get("params"))

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
            resp = _request(method, cookies, **kwargs)
        except Exception as e:
            logger.info("Обновить куки не вышло (%s) — пробую запрос из браузера", e)
            return browser_request(method, kwargs.get("json"), kwargs.get("params"))

    if resp.status_code >= 400:
        # Свежие куки не помогли: значит дело не в них — идём через браузер.
        logger.info(
            "HTTP %s на операции %s даже со свежими куками — иду через браузер",
            resp.status_code,
            operation,
        )
        return browser_request(method, kwargs.get("json"), kwargs.get("params"))

    try:
        data = resp.json()
    except ValueError as e:
        raise TransportError(f"Ответ не JSON: {e}") from e

    if "errors" in data:
        raise TransportError(
            f"{operation}: "
            + "; ".join(e.get("message", str(e)) for e in data["errors"])
        )

    return data.get("data") or {}
