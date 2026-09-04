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
import uuid
from typing import Optional
from urllib.parse import urlencode

import config
import credentials

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

    # Кука, заданная пользователем, главнее сохранённой: он мог прислать
    # боту свежую взамен слетевшей.
    jar.update(_parse_cookie_string(credentials.cookie_string()))
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

    jar.update(_parse_cookie_string(credentials.cookie_string()))
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
        # Apollo режет «простые» запросы как возможный CSRF; этот заголовок
        # переводит запрос в разряд требующих preflight и снимает блокировку.
        "apollo-require-preflight": "true",
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


def encode_multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
) -> tuple[bytes, str]:
    """
    Собирает тело multipart/form-data вручную: curl_cffi не принимает files=,
    а ручная сборка одинаково работает и с ним, и с httpx.
    Возвращает (тело, значение content-type).
    """
    boundary = "----playerok" + uuid.uuid4().hex
    body = bytearray()

    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += str(value).encode() + b"\r\n"

    for name, (filename, content, content_type) in files.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        ).encode()
        body += content + b"\r\n"

    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


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
  headers: Object.assign(
    {'apollo-require-preflight': 'true'},
    body ? {'content-type': 'application/json'} : {}
  ),
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
        raise TransportError(describe_errors(data["errors"]))
    return data.get("data") or {}


def describe_errors(errors: list) -> str:
    """
    Ошибки GraphQL одной строкой. Playerok часто отвечает обезличенным
    «Something went wrong», и вся полезная часть лежит рядом — в `extensions`
    (код, а иногда и поле ввода) и в `path`. Без них разбираться не в чем,
    поэтому вытаскиваем всё, что есть.
    """
    parts = []
    for error in errors:
        if not isinstance(error, dict):
            parts.append(str(error))
            continue

        text = error.get("message") or "без текста"
        extensions = error.get("extensions") or {}
        details = []
        for key in ("code", "field", "argumentName", "errorCode", "reason"):
            if extensions.get(key):
                details.append(f"{key}={extensions[key]}")
        if error.get("path"):
            details.append("path=" + ".".join(str(p) for p in error["path"]))
        # Сообщения валидатора складывают подробности во вложенный список.
        for nested in extensions.get("errors") or []:
            if isinstance(nested, dict) and nested.get("message"):
                details.append(str(nested["message"]))

        parts.append(text + (f" ({', '.join(details)})" if details else ""))
    return "; ".join(parts)


def _graphql_error(resp) -> str:
    """
    Текст ошибки GraphQL, если сервер её вернул. Пустая строка означает,
    что ответ не от GraphQL — то есть в дело вмешалась защита.
    """
    body = (resp.text or "")[:2000]
    if not body.lstrip().startswith("{"):
        return ""
    try:
        data = json.loads(resp.text)
    except ValueError:
        return ""
    errors = data.get("errors")
    if not errors:
        return ""
    return describe_errors(errors)


def _operation_name(kwargs: dict) -> str:
    """
    Имя операции — чтобы в логах и в сообщении об ошибке было видно, какой
    запрос упал. У multipart-запросов (createItem с картинками) тело собрано
    руками, поэтому имя достаём из его части `operations`.
    """
    for source in (kwargs.get("json"), kwargs.get("params")):
        if isinstance(source, dict) and source.get("operationName"):
            return source["operationName"]

    body = kwargs.get("data")
    if isinstance(body, (bytes, bytearray)):
        marker = b'name="operations"\r\n\r\n'
        start = body.find(marker)
        if start != -1:
            start += len(marker)
            end = body.find(b"\r\n--", start)
            try:
                operations = json.loads(bytes(body[start:end]).decode("utf-8"))
                if operations.get("operationName"):
                    return operations["operationName"]
            except (ValueError, UnicodeDecodeError):
                pass
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

    # Защита отдаёт HTML или пустоту, а сервер GraphQL — JSON с errors даже
    # при 4xx. Различаем: обновлять куки во втором случае бессмысленно.
    graphql_error = _graphql_error(resp)
    if graphql_error:
        raise TransportError(f"{operation}: {graphql_error}")

    if resp.status_code in (403, 500, 503) and retry:
        logger.info(
            "Ответ %s на %s без тела GraphQL — похоже на защиту, обновляю куки",
            resp.status_code, operation,
        )
        try:
            cookies = refresh_cookies()
            resp = _request(method, cookies, **kwargs)
        except Exception as e:
            logger.info("Обновить куки не вышло (%s) — пробую запрос из браузера", e)
            return browser_request(method, kwargs.get("json"), kwargs.get("params"))

    if resp.status_code >= 400 and not kwargs.get("data"):
        # Свежие куки не помогли: значит дело не в них — идём через браузер.
        logger.info(
            "HTTP %s на операции %s даже со свежими куками — иду через браузер",
            resp.status_code,
            operation,
        )
        return browser_request(method, kwargs.get("json"), kwargs.get("params"))

    if resp.status_code >= 400:
        logger.warning("Тело ответа %s: %s", resp.status_code, (resp.text or "")[:300])
        raise TransportError(
            f"HTTP {resp.status_code} на операции {operation}: {(resp.text or '')[:200]}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise TransportError(f"Ответ не JSON: {e}") from e

    if "errors" in data:
        raise TransportError(f"{operation}: " + describe_errors(data["errors"]))

    return data.get("data") or {}
