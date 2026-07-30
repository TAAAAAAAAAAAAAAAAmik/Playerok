import json
import httpx
import logging
from typing import Optional
from config import PLAYEROK_API_URL, PLAYEROK_BASE_URL
import config
import auth

logger = logging.getLogger(__name__)

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": PLAYEROK_BASE_URL,
    "Referer": PLAYEROK_BASE_URL + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ── Auth mutations ────────────────────────────────────────────────────────────

SEND_CODE_MUTATION = """
mutation sendConfirmationCode($input: SendCodeInput!) {
  sendConfirmationCode(input: $input) {
    success
  }
}
"""

LOGIN_MUTATION = """
mutation signIn($input: SignInInput!) {
  signIn(input: $input) {
    token
    user {
      id
      username
      email
    }
  }
}
"""

# ── Data queries ──────────────────────────────────────────────────────────────

VIEWER_QUERY = """
query viewer {
  viewer {
    id
    username
    email
  }
}
"""

MY_ITEMS_QUERY = """
query GetMyItems($pagination: OffsetPaginationInput) {
  myItems(pagination: $pagination) {
    list {
      id
      slug
      name
      status
      createdAt
      price {
        value
        currency { symbol }
      }
    }
    pageInfo {
      total
    }
  }
}
"""

# ── Создание товара: этап 2 (запросы вместо браузера) ─────────────────────────
#
# Схема снята с рабочей реализации https://github.com/alleexxeeyy/PlayerokAPI.
# Чтение идёт persisted-запросами (GET с sha256Hash), запись — обычными
# мутациями. Пока бот создаёт товар через Selenium (selenium_creator.py):
# сначала проверяем сценарий в браузере, потом переводим сюда.

# sha256-хэши persisted-запросов Playerok (меняются при обновлении фронта).
PERSISTED_QUERIES = {
    "deals": "591b0e6d036c2120c8f95b97dbfdf5635df3747cd901f4895e009935229417ef",
    "games": "5de9b3240c148579c82e2310a30b4aad5462884fd1abf93dd3c43d1f5ef14d85",
    "GamePage": "4775f8630a3e234c50537e68649043ac32a40b0370b0f1fb2dc314500ef6202d",
    "GamePageCategory": "7759f743651176ddad6afefb5f2e889ec9984cae08a015281879cd61e94bdb60",
    "gameCategoryObtainingTypes": "15b0991414821528251930b4c8161c299eb39882fd635dd5adb1a81fb0570aea",
    "gameCategoryDataFields": "6fdadfb9b05880ce2d307a1412bc4f2e383683061c281e2b65a93f7266ea4a49",
    "itemPriorityStatuses": "b922220c6f979537e1b99de6af8f5c13727daeff66727f679f07f986ce1c025a",
}

# createItem принимает файлы отдельным аргументом $attachments (multipart-спека
# GraphQL: operations + map + пронумерованные файлы).
CREATE_ITEM_MUTATION = """
mutation createItem($input: CreateItemInput!, $attachments: [Upload!]!) {
  createItem(input: $input, attachments: $attachments) {
    ... on MyItem {
      id
      slug
      name
      price
      status
    }
  }
}
"""

PUBLISH_ITEM_MUTATION = """
mutation publishItem($input: PublishItemInput!) {
  publishItem(input: $input) {
    ... on MyItem {
      id
      slug
      name
      price
      status
    }
  }
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_headers() -> dict:
    headers = dict(BASE_HEADERS)
    token = auth.get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Playerok авторизует по кукам (token + __ddg*), а не по заголовку.
    # Кука DDoS-Guard привязана к IP и User-Agent — берите её из браузера.
    if config.PLAYEROK_COOKIES:
        headers["Cookie"] = config.PLAYEROK_COOKIES
    elif token:
        headers["Cookie"] = f"token={token}"
    return headers


async def _gql(
    query: str,
    variables: Optional[dict] = None,
    token_override: Optional[str] = None,
) -> dict:
    headers = dict(BASE_HEADERS)
    t = token_override or auth.get_token()
    if t:
        headers["Authorization"] = f"Bearer {t}"

    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        resp = await client.post(PLAYEROK_API_URL, json=payload)
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        msgs = [e.get("message", str(e)) for e in data["errors"]]
        raise RuntimeError("; ".join(msgs))

    return data.get("data", {})


# ── Auth ──────────────────────────────────────────────────────────────────────

async def request_auth_code(email: str) -> bool:
    """Ask Playerok to send an OTP code to the given email."""
    data = await _gql(SEND_CODE_MUTATION, {"input": {"email": email}})
    return bool(data.get("sendConfirmationCode", {}).get("success"))


async def login_with_code(email: str, code: str) -> dict:
    """
    Verify the OTP code and get a session token.
    Returns {"token": str, "username": str} on success.
    Raises RuntimeError on failure.
    """
    data = await _gql(LOGIN_MUTATION, {"input": {"email": email, "code": code}})
    sign_in = data.get("signIn", {})
    token = sign_in.get("token")
    if not token:
        raise RuntimeError("Токен не получен. Проверьте код и попробуйте снова.")
    user = sign_in.get("user", {})
    return {"token": token, "username": user.get("username", "")}


# ── Data fetching ─────────────────────────────────────────────────────────────

_viewer_id: str = ""


async def fetch_viewer() -> dict:
    """Данные текущего аккаунта: {id, username, email}."""
    data = await _gql(VIEWER_QUERY)
    viewer = data.get("viewer")
    if not viewer:
        raise RuntimeError("Не авторизованы: сервер не вернул viewer.")
    return viewer


async def _get_viewer_id() -> str:
    """ID аккаунта — обязательный фильтр в запросе сделок."""
    global _viewer_id
    if not _viewer_id:
        _viewer_id = (await fetch_viewer()).get("id", "")
    return _viewer_id


async def fetch_deals(count: int = 20, direction: str = "OUT") -> list[dict]:
    """
    Сделки аккаунта. direction="OUT" — продажи (то, что купили у вас).
    Возвращает список узлов сделок.
    """
    try:
        user_id = await _get_viewer_id()
        data = await _persisted(
            "deals",
            {
                "pagination": {"first": count, "after": None},
                "filter": {"userId": user_id, "direction": direction, "status": None},
                "showForbiddenImage": True,
            },
        )
        edges = (data.get("deals") or {}).get("edges") or []
        return [edge["node"] for edge in edges if edge.get("node")]
    except Exception as e:
        logger.error("fetch_deals error: %s", e)
        return []


async def fetch_orders(limit: int = 20) -> list[dict]:
    """Новые покупки — все продажи аккаунта."""
    return await fetch_deals(count=limit)


async def fetch_complaints(limit: int = 20) -> list[dict]:
    """
    Проблемные сделки. Отдельной сущности «жалоба» в API нет: покупатель
    сообщает о проблеме по сделке, и у неё поднимается флаг hasProblem.
    """
    deals = await fetch_deals(count=limit)
    return [d for d in deals if d.get("hasProblem")]


async def fetch_my_items(limit: int = 10) -> list[dict]:
    try:
        data = await _gql(MY_ITEMS_QUERY, {"pagination": {"limit": limit, "offset": 0}})
        return data.get("myItems", {}).get("list", [])
    except Exception as e:
        logger.error("fetch_my_items error: %s", e)
        return []


# ── Создание товара: этап 2 (запросы вместо браузера) ─────────────────────────

async def _persisted(operation: str, variables: dict) -> dict:
    """GET-запрос persisted-операции (так фронт Playerok читает справочники)."""
    payload = {
        "operationName": operation,
        "variables": json.dumps(variables),
        "extensions": json.dumps({
            "persistedQuery": {
                "version": 1,
                "sha256Hash": PERSISTED_QUERIES[operation],
            }
        }),
    }

    async with httpx.AsyncClient(headers=_make_headers(), timeout=30.0) as client:
        resp = await client.get(PLAYEROK_API_URL, params=payload)
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        raise RuntimeError("; ".join(e.get("message", str(e)) for e in data["errors"]))
    return data.get("data", {})


async def search_games(name: str, count: int = 24) -> list[dict]:
    """Игры и приложения по названию. Возвращает [{id, name, slug}, ...]."""
    data = await _persisted(
        "games",
        {"pagination": {"first": count, "after": None}, "filter": {"name": name, "type": None}},
    )
    edges = (data.get("games") or {}).get("edges") or []
    return [edge["node"] for edge in edges if edge.get("node")]


async def fetch_obtaining_types(game_category_id: str, count: int = 24) -> list[dict]:
    """Способы получения товара внутри категории."""
    data = await _persisted(
        "gameCategoryObtainingTypes",
        {
            "pagination": {"first": count, "after": None},
            "filter": {"gameCategoryId": game_category_id},
        },
    )
    edges = (data.get("gameCategoryObtainingTypes") or {}).get("edges") or []
    return [edge["node"] for edge in edges if edge.get("node")]


async def fetch_data_fields(
    game_category_id: str, obtaining_type_id: str, count: int = 24
) -> list[dict]:
    """
    Поля с данными категории. Заполнять нужно только те, у которых
    type == "ITEM_DATA" — поля OBTAINING_DATA заполняет покупатель.
    """
    data = await _persisted(
        "gameCategoryDataFields",
        {
            "pagination": {"first": count, "after": None},
            "filter": {
                "gameCategoryId": game_category_id,
                "obtainingTypeId": obtaining_type_id,
                "type": None,
            },
        },
    )
    edges = (data.get("gameCategoryDataFields") or {}).get("edges") or []
    return [edge["node"] for edge in edges if edge.get("node")]


async def fetch_priority_statuses(item_id: str, price: int) -> list[dict]:
    """Статусы приоритета для публикации (у бесплатного price == 0)."""
    data = await _persisted(
        "itemPriorityStatuses", {"itemId": item_id, "price": int(price)}
    )
    return data.get("itemPriorityStatuses") or []


async def create_item(
    game_category_id: str,
    obtaining_type_id: str,
    name: str,
    price: int,
    description: str,
    attributes: Optional[dict] = None,
    data_fields: Optional[list[dict]] = None,
    attachments: Optional[list[tuple[str, bytes, str]]] = None,
) -> dict:
    """
    Создаёт товар (попадает в черновик, на продажу его выставляет publish_item).

    attributes:  {field: value} — выбранные опции категории.
    data_fields: [{"fieldId": ..., "value": ...}] — только поля ITEM_DATA.
    attachments: [(имя файла, байты, mime-тип), ...] — картинки товара.
    """
    attachments = attachments or []

    operations = {
        "operationName": "createItem",
        "query": CREATE_ITEM_MUTATION,
        "variables": {
            "input": {
                "gameCategoryId": game_category_id,
                "obtainingTypeId": obtaining_type_id,
                "name": name,
                "price": int(price),
                "description": description,
                "attributes": attributes or {},
                "dataFields": data_fields or [],
            },
            "attachments": [None] * len(attachments),
        },
    }

    files = {}
    file_map = {}
    for i, (filename, content, content_type) in enumerate(attachments, start=1):
        files[str(i)] = (filename, content, content_type)
        file_map[str(i)] = [f"variables.attachments.{i - 1}"]

    headers = _make_headers()
    # Content-Type multipart с boundary проставит httpx.
    headers.pop("Content-Type", None)

    async with httpx.AsyncClient(headers=headers, timeout=120.0) as client:
        resp = await client.post(
            PLAYEROK_API_URL,
            data={"operations": json.dumps(operations), "map": json.dumps(file_map)},
            files=files or {"": ("", b"")},
        )
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        raise RuntimeError("; ".join(e.get("message", str(e)) for e in data["errors"]))

    item = (data.get("data") or {}).get("createItem")
    if not item or not item.get("id"):
        raise RuntimeError("Сервер не вернул созданный товар.")
    return item


async def publish_item(
    item_id: str, priority_status_id: str, transaction_provider_id: str = "LOCAL"
) -> dict:
    """Выставляет созданный товар на продажу."""
    data = await _gql(
        PUBLISH_ITEM_MUTATION,
        {
            "input": {
                "itemId": item_id,
                "priorityStatuses": [priority_status_id],
                "transactionProviderId": transaction_provider_id,
            }
        },
    )
    item = data.get("publishItem")
    if not item or not item.get("id"):
        raise RuntimeError("Не удалось опубликовать товар.")
    return item
