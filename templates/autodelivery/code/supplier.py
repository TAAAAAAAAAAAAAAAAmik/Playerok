"""Поставщик кодов AppRoute. Переносится на любую площадку без единой правки.

Это ровно та половина шаблона, которая **не зависит** от маркетплейса:
поставщик не знает и знать не должен, откуда пришёл заказ. Всё, что здесь
написано, проверено живыми вызовами на настоящем ключе.

Пять вещей, каждая из которых стоила денег или дня:

1. **HTTP 200 ничего не значит.** Снаружи ответ почти всегда двухсотый;
   получилось ли — решает `statusCode` ВНУТРИ тела.
2. **Успехов три, а не один.** `0` OK, `1` ACCEPTED (заказ принят, код
   будет позже), `2` IDEMPOTENCY_REPLAY («такой заказ уже был, отдаю
   прежний результат»). Разбор, считающий успехом только `0`, читает
   принятый заказ и законный повтор как отказ — и покупает второй раз.
3. **Ссылка заказа кладётся НАВЕРХ и называется `referenceId`.** Внутри
   позиции такого поля нет вовсе. Положенная не туда, она поставщику не
   видна — значит идемпотентности нет, и повтор после обрыва связи будет
   второй покупкой за свои деньги.
4. **Коды приходят замазанными** (`****9012`), пока не спросишь с
   `unhide=true`. И `unhide` без фильтра `referenceId` отвергается 422.
5. **Белый список IP.** Ключ, работавший вчера, перестаёт приниматься
   после переезда сервера. Лечится прокси с постоянным адресом.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

try:
    import requests
except ImportError:                                   # pragma: no cover
    requests = None


BASE_URL = "https://approute.io/api/v1"
TIMEOUT = 30

# Числовые коды внутри тела. Документация поставщика прямо предупреждает:
# с HTTP они не совпадают.
STATUS_CODES = {
    0: "OK", 1: "ACCEPTED", 2: "IDEMPOTENCY_REPLAY",
    3: "VALIDATION_ERROR", 4: "UNAUTHORIZED", 5: "FORBIDDEN",
    6: "NOT_FOUND", 8: "LIMIT_REACHED", 9: "OUT_OF_STOCK",
    10: "INSUFFICIENT_FUNDS", 11: "UPSTREAM_ERROR",
}
SUCCESS_CODES = (0, 1, 2)           # см. пункт 2 в докстринге модуля
TERMINAL_STATUSES = ("SUCCESS", "PARTIALLY_COMPLETED", "CANCELLED")
RETRYABLE_HTTP = (429, 500, 502, 503, 504)
REFERENCE_MAX = 40                  # требование схемы: 1..40 символов

# Лимиты запросов в минуту — опубликованы поставщиком (`GET /rate-limits`).
RATE_LIMITS = {
    "GET /services": 2,             # каталог целиком — самый дорогой вызов
    "GET /orders": 2,
    "GET /orders?filter": 20,
    "GET /orders?unhide": 5,        # отдельный счётчик
    "POST /orders": 120,
    "GET /services/{id}/items/{id}": 120,
}

_RU = {
    "VALIDATION_ERROR": "поставщик не принял поля запроса",
    "UNAUTHORIZED": "ключ не принят: истёк срок, не тот кабинет "
                    "или наш адрес не в белом списке",
    "FORBIDDEN": "у ключа нет нужного права. Для выдачи кодов обязательно "
                 "orders:write — без него ключ покупает, но кода не отдаёт",
    "NOT_FOUND": "поставщик не нашёл такой номинал",
    "LIMIT_REACHED": "достигнут лимит: запросов или трат по ключу",
    "OUT_OF_STOCK": "номинала нет в наличии",
    "INSUFFICIENT_FUNDS": "не хватает денег на счёте у поставщика",
    "UPSTREAM_ERROR": "поставщик не смог купить у своего источника",
}


# Как поставщик называет сумму и валюту счёта. Полей несколько, потому
# что у него самого они разные в разных ответах.
BALANCE_FIELDS = ("balance", "amount", "value", "sum", "available")
CURRENCY_FIELDS = ("currency", "currencyCode", "code", "name")


def balance_line(accounts) -> str:
    """Счета одной строкой: «USD 0.42 · RUB 0». Пусто — значит не прочли."""
    parts = []

    for row in accounts or []:
        if not isinstance(row, dict):
            continue

        money = None

        for field in BALANCE_FIELDS:
            if row.get(field) is not None:
                money = row[field]
                break

        if money is None:
            continue

        currency = ""

        for field in CURRENCY_FIELDS:
            if row.get(field):
                currency = str(row[field])
                break

        try:
            shown = f"{float(money):.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            shown = str(money)

        parts.append(f"{currency} {shown}".strip())

    return " · ".join(parts)


class SupplierError(Exception):
    def __init__(self, code: str, why: str, http: int = 0, trace: str = ""):
        self.code, self.why, self.http, self.trace = code, why, http, trace
        # traceId печатается в каждом отказе: по нему поставщик находит
        # запрос у себя. Выброшенный, он превращает разбор в переписку.
        super().__init__(f"{why}{f' (traceId {trace})' if trace else ''}")


class Supplier(Protocol):
    """Что движку нужно от поставщика. Тестам хватает поддельного."""

    def services(self) -> Any:
        """Каталог целиком: услуги со вложенными номиналами.

        Разрешён ДВА раза в минуту — это самый дорогой вызов поставщика,
        1263 услуги одним ответом. Поэтому зовётся только через кеш, а
        перед покупкой читается один номинал (120 в минуту), а не каталог.

        Отдаётся как пришло: форму ответа разбирает вызывающий. Клиент не
        знает, что из каталога кому нужно, и приводить её здесь значило бы
        решать это за него.
        """
        return self._call("GET", "/services")

    def item(self, service_id: str, item_id: str) -> dict: ...
    def place(self, denomination_id: str, reference: str,
              quantity: int = 1) -> dict: ...
    def by_reference(self, reference: str) -> dict: ...


def cut_reference(reference: str) -> str:
    """Ссылка в том виде, в каком её увидит поставщик.

    Обрезать надо ОДИНАКОВО при покупке и при поиске: разойдутся на один
    символ — заказ не найдётся, и после обрыва связи мы решим, что покупки
    не было. Поэтому обрезание живёт в одной функции.
    """
    return str(reference or "")[:REFERENCE_MAX]


def order_body(denomination_id: str, quantity: int = 1,
               reference: str = "") -> dict:
    """Тело покупки в той форме, которую поставщик принимает на самом деле.

    Ни SDK (`itemId` наверху), ни openapi (`clientTime` + `reference`) не
    угадали. Сервер требует `ordersType` и позиции списком, а `referenceId`
    — наверху. Позиция ровно одна: схема требует minItems=1, maxItems=1.
    """
    body = {
        "ordersType": "shop",
        "orders": [{"denominationId": str(denomination_id or ""),
                    "quantity": int(quantity or 1)}],
    }
    if reference:
        body["referenceId"] = cut_reference(reference)
    return body


# Где у ответа лежит код. Ищем только под этими именами.
CODE_KEYS = ("pin", "code", "voucher", "serial")

# Куда не заходим вовсе. В ответе полно посторонних «кодов»: валюта USD,
# регион RU, код ошибки. Собрав их наравне с настоящими, бот отправил бы
# покупателю «USD» первой строкой — или, того хуже, «OUT_OF_STOCK» вместо
# кода, отметив заказ выданным.
SKIP_KEYS = frozenset((
    "currency", "region", "country", "locale", "language",
    "error", "errors", "service", "category", "game", "price",
    "meta", "rate", "limits", "provider",
))

# Чем поставщик замазывает коды, пока не спросишь `unhide=true`.
MASK_CHARS = "*•…"

# Самый короткий код, который бывает. Всё короче — это валюта, регион или
# ещё какое-нибудь сокращение, но не то, что покупатель введёт на сайте.
CODE_MIN = 6

# Слова, которые кодом не бывают никогда: имена статусов и исходов. Они
# приходят под теми же ключами и длиннее шести букв — одной длиной их не
# отсечь.
NOT_A_CODE = frozenset(list(STATUS_CODES.values()) + list(TERMINAL_STATUSES) + [
    "IN_PROGRESS", "PENDING", "NEW", "PROCESSING", "FAILED", "ERROR",
    "COMPLETED", "REFUNDED", "EXPIRED", "UNKNOWN",
])


def masked(value: str) -> bool:
    """Замазанный код: «****9012».

    Отправить такое покупателю — это отчёт о выдаче, которой не было.
    """
    return any(ch in str(value or "") for ch in MASK_CHARS)


def looks_like_code(value) -> bool:
    """Похоже ли это на код, который покупатель введёт на сайте."""
    if not isinstance(value, str):
        return False

    text = value.strip()

    if len(text) < CODE_MIN or masked(text):
        return False

    return text.upper() not in NOT_A_CODE


def codes_from(data) -> list[str]:
    """Коды из ответа. Форма отличается у покупки и у поиска по ссылке.

    Отбираем строго: лишний «код» дороже пропущенного. Пропущенный виден
    сразу — движок скажет «ответ без кода» и назовёт ссылку покупки, по
    ней продавец найдёт заказ в кабинете. А лишний уходит покупателю молча
    и выглядит как выданный товар.
    """
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key in CODE_KEYS:
                if looks_like_code(node.get(key)):
                    out.append(node[key].strip())

            for key, val in node.items():
                if str(key).lower() not in SKIP_KEYS:
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    walk(data)
    # порядок сохраняем, дубли убираем
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


class ApprouteSupplier:
    """Клиент на один кабинет. Авторизация — один заголовок."""

    def __init__(self, api_key: str, base_url: str = BASE_URL,
                 proxy: str = "", max_retries: int = 1):
        if requests is None:                          # pragma: no cover
            raise RuntimeError("нужен пакет requests")
        self.api_key = str(api_key or "").strip()
        self.base = (base_url or BASE_URL).rstrip("/")
        self.max_retries = max(0, int(max_retries))
        self.session = requests.Session()
        if proxy:
            # Через прокси обязаны идти И рабочие вызовы, И диагностика.
            # Проба, ходящая мимо, показывает адрес, с которого поставщик
            # нас не видит, — и «ключ не работает» остаётся загадкой.
            self.session.proxies = {"http": proxy, "https": proxy}

    # ---------- транспорт ----------

    def _call(self, method: str, path: str, *, params=None, json_body=None):
        headers = {"X-API-Key": self.api_key,
                   "Accept": "application/json",
                   "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            r = self.session.request(method, self.base + path, headers=headers,
                                     params=params, json=json_body,
                                     timeout=TIMEOUT)
            if r.status_code in RETRYABLE_HTTP and attempt < self.max_retries:
                # Повтор на 429 стоит места в том же лимите, из-за которого
                # отказ и пришёл. Поэтому повтор один, и только если
                # названную поставщиком паузу мы можем выдержать целиком.
                delay = float(r.headers.get("Retry-After") or 0)
                if r.status_code == 429 and delay > 30:
                    pass
                else:
                    time.sleep(min(delay or 1.0, 10.0))
                    continue
            return self._unwrap(r)
        raise SupplierError("", "поставщик не ответил")   # pragma: no cover

    @staticmethod
    def _unwrap(r):
        """Конверт → данные. Успех решает statusCode, а не HTTP."""
        try:
            body = r.json()
        except ValueError:
            raise SupplierError("", f"ответ не в формате JSON "
                                    f"(HTTP {r.status_code})", r.status_code)
        if not isinstance(body, dict):
            raise SupplierError("", f"неожиданный ответ (HTTP {r.status_code})",
                                r.status_code)

        trace = str(body.get("traceId") or "")
        errors = body.get("errors") or []
        raw = body.get("statusCode")
        code = int(raw) if isinstance(raw, (int, str)) and str(raw).lstrip(
            "-").isdigit() else None

        if code is None:
            raise SupplierError("", f"в ответе нет statusCode — отвечали не "
                                    f"они (HTTP {r.status_code})",
                                r.status_code, trace)

        name = STATUS_CODES.get(code, str(code))
        if code in SUCCESS_CODES:
            # Успешный код вместе с жалобами — ответ, противоречащий сам
            # себе. Решаем в сторону «денег не потратили»: ошибиться так
            # дешевле, а есть ли код, выдача всё равно проверит.
            if errors or str(body.get("errorCode") or "").strip():
                raise SupplierError(name, "поставщик ответил успехом, но с "
                                          "жалобами по полям — считаем "
                                          "отказом", r.status_code, trace)
            # `data` при ACCEPTED пустой законно: кода ещё нет.
            return body.get("data")

        said = str(body.get("statusMessage") or "").strip()
        raise SupplierError(name, _RU.get(name) or said or
                            f"поставщик отказал (код {code})",
                            r.status_code, trace)

    # ---------- то, что нужно выдаче ----------

    def services(self) -> Any:
        """Каталог целиком: услуги со вложенными номиналами.

        Разрешён ДВА раза в минуту — это самый дорогой вызов поставщика,
        1263 услуги одним ответом. Поэтому зовётся только через кеш, а
        перед покупкой читается один номинал (120 в минуту), а не каталог.

        Отдаётся как пришло: форму ответа разбирает вызывающий. Клиент не
        знает, что из каталога кому нужно, и приводить её здесь значило бы
        решать это за него.
        """
        return self._call("GET", "/services")

    def accounts(self) -> list:
        """Счета кабинета: сколько денег и в какой валюте.

        Нужны ровно для одного: сказать продавцу «не хватает денег» вместе
        с тем, СКОЛЬКО их. Отказ поставщика этого не говорит, а без числа
        продавец идёт смотреть в кабинет — то есть делает работу, которую
        бот мог сделать за него.

        Живой ответ отдал три счёта: USD, RUB, EUR. Цены каталога в USD.
        """
        data = self._call("GET", "/accounts")

        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]

        if isinstance(data, dict):
            for key in ("accounts", "items", "data", "results"):
                found = data.get(key)

                if isinstance(found, list):
                    return [row for row in found if isinstance(row, dict)]

            # Одиночный счёт тоже бывает — отдаём списком, чтобы у
            # вызывающего была одна форма на все случаи.
            return [data] if data else []

        return []

    def item(self, service_id: str, item_id: str) -> dict:
        """Цена и остаток ОДНОГО номинала. Замена сухому прогону.

        Сухого прогона для магазина не существует: `checkOnly` разрешён
        только при `ordersType=dtu`, а коды — это `voucher`. Зато этот
        вызов разрешён 120 раз в минуту против 2 у каталога, поэтому
        зовётся под каждую покупку.
        """
        data = self._call("GET", f"/services/{service_id}/items/{item_id}")
        return data if isinstance(data, dict) else {}

    def place(self, denomination_id: str, reference: str,
              quantity: int = 1) -> dict:
        """Купить → факты, а не текст.

        Пустые `codes` при `ok` — не поломка: покупка законно отвечает
        `IN_PROGRESS`, и код надо дождаться опросом.
        """
        try:
            data = self._call("POST", "/orders", json_body=order_body(
                denomination_id, quantity, reference))
        except SupplierError as e:
            return {"ok": False, "why": str(e), "status": "", "codes": [],
                    "trace": e.trace}
        node = data if isinstance(data, dict) else {}
        return {"ok": True, "why": "",
                "status": str(node.get("status") or "").upper(),
                "codes": codes_from(node),
                "order_id": str(node.get("orderId") or node.get("id") or ""),
                "trace": ""}

    def by_reference(self, reference: str) -> dict:
        """Чем кончился заказ с этой ссылкой — и коды, если они уже есть.

        `unhide=true` ОБЯЗАТЕЛЕН: без него коды приходят замазанными, и
        отправка такого «кода» покупателю была бы отчётом о выдаче, которой
        не было. Фильтр тоже обязателен — `unhide` без него даёт 422.

        Побочное действие: поставщик помечает коды полученными. Поэтому
        звать «просто посмотреть» из диагностики нельзя — только на пути
        настоящей выдачи.
        """
        try:
            data = self._call("GET", "/orders",
                              params={"referenceId": cut_reference(reference),
                                      "unhide": "true"})
        except SupplierError as e:
            return {"ok": False, "why": str(e), "found": False,
                    "status": "", "codes": []}
        node = data if isinstance(data, dict) else {}
        page = node.get("page")
        rows = (page.get("items") if isinstance(page, dict) else None) \
            or node.get("items") or []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        return {"ok": True, "why": "", "found": bool(rows),
                "status": str(first.get("status")
                              or node.get("status") or "").upper(),
                "codes": codes_from(node)}
