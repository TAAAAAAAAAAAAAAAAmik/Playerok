import asyncio
import json
import logging
from typing import Optional
from config import PLAYEROK_API_URL, PLAYEROK_BASE_URL
import config
import auth
import transport

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
    ...Viewer
    __typename
  }
}

fragment Viewer on User {
  id
  username
  email
  role
  hasFrozenBalance
  supportChatId
  systemChatId
  unreadChatsCounter
  isBlocked
  isBlockedFor
  isFundsProtectionActive
  createdAt
  lastItemCreatedAt
  hasConfirmedPhoneNumber
  canPublishItems
  chosenVerifiedCard {
    ...MinimalUserBankCard
    __typename
  }
  balance {
    value
    __typename
  }
  profile {
    id
    avatarURL
    testimonialCounter
    __typename
  }
  __typename
}

fragment MinimalUserBankCard on UserBankCard {
  id
  cardFirstSix
  cardLastFour
  cardType
  isChosen
  __typename
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

# sha256-хэши persisted-запросов. Меняются при каждой сборке фронта, поэтому
# это лишь запасные значения: актуальные снимает query_sniffer прямо с сайта.
FALLBACK_QUERIES = {
    "deals": "591b0e6d036c2120c8f95b97dbfdf5635df3747cd901f4895e009935229417ef",
    "games": "5de9b3240c148579c82e2310a30b4aad5462884fd1abf93dd3c43d1f5ef14d85",
    "GamePage": "4775f8630a3e234c50537e68649043ac32a40b0370b0f1fb2dc314500ef6202d",
    "GamePageCategory": "7759f743651176ddad6afefb5f2e889ec9984cae08a015281879cd61e94bdb60",
    "gameCategoryObtainingTypes": "15b0991414821528251930b4c8161c299eb39882fd635dd5adb1a81fb0570aea",
    "gameCategoryDataFields": "6fdadfb9b05880ce2d307a1412bc4f2e383683061c281e2b65a93f7266ea4a49",
    "itemPriorityStatuses": "b922220c6f979537e1b99de6af8f5c13727daeff66727f679f07f986ce1c025a",
    # Снято с сайта: характеристики категории запрашиваются отдельно.
    "gameCategoryOptions": "ffa5a575b990f54411c60edc07558d7ee27fa60f32b08f2a0af68dd2d31ebb25",
    "gameWithCategories": "7ee7e0cd62afcd98278ff5ece1b6e2de37353323d9c08d1dfa9e0a079ec1af16",
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

async def _gql(
    query: str,
    variables: Optional[dict] = None,
    operation: Optional[str] = None,
) -> dict:
    """POST-запрос к /graphql. Транспорт синхронный, уводим его в поток."""
    payload: dict = {"query": query}
    if operation:
        payload["operationName"] = operation
    if variables:
        payload["variables"] = variables

    return await asyncio.to_thread(transport.request, "post", json=payload)


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
_last_error_log: float = 0.0


def _log_error_rarely(message: str, error: Exception):
    """Опрос идёт каждые 30 секунд — не заваливаем журнал одной и той же ошибкой."""
    global _last_error_log
    import time

    if time.time() - _last_error_log > 300:
        logger.error("%s: %s", message, error)
        _last_error_log = time.time()
    else:
        logger.debug("%s: %s", message, error)


async def fetch_viewer() -> dict:
    """Данные текущего аккаунта: {id, username, email}."""
    data = await _gql(VIEWER_QUERY, operation="viewer")
    viewer = data.get("viewer")
    if not viewer:
        raise RuntimeError("Не авторизованы: сервер не вернул viewer.")
    return viewer


def _user_id_from_token() -> str:
    """
    ID аккаунта из JWT: в полезной нагрузке токена он лежит в `sub`.
    Это избавляет от лишнего запроса viewer на каждом старте.
    """
    import base64

    token = ""
    for chunk in config.PLAYEROK_COOKIES.split(";"):
        if chunk.strip().startswith("token="):
            token = chunk.split("=", 1)[1].strip()
    token = token or auth.get_token()
    if not token or token.count(".") != 2:
        return ""

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)  # base64url без выравнивания
    try:
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub", "")
    except Exception as e:
        logger.debug("Не разобрал токен: %s", e)
        return ""


async def _get_viewer_id() -> str:
    """ID аккаунта — обязательный фильтр в запросе сделок."""
    global _viewer_id
    if not _viewer_id:
        _viewer_id = _user_id_from_token()
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
                "pagination": {"first": count},
                "filter": {"userId": user_id, "direction": direction},
                "showForbiddenImage": True,
            },
        )
        edges = (data.get("deals") or {}).get("edges") or []
        return [edge["node"] for edge in edges if edge.get("node")]
    except Exception as e:
        _log_error_rarely("fetch_deals error", e)
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

# Фронт со временем переименовывает операции: страница игры сейчас
# запрашивается как Game, раньше — как GamePage. Пробуем варианты по порядку.
OPERATION_ALIASES = {
    # Список игр в мастере продажи; прежнее имя games сервер уже не принимает.
    "games": ["SellGames", "games"],
    # Игра с категориями: в мастере это gameWithCategories, на странице — Game.
    "GamePage": ["gameWithCategories", "Game", "GamePage"],
    "GamePageCategory": ["GamePageCategory", "gameCategory"],
    "deals": ["deals", "Deals"],
}


def _unwrap(data: dict, *keys: str):
    """
    Достаёт полезную нагрузку ответа. Имена корневых полей у переименованных
    операций разные (game / gameWithCategories и т.п.), поэтому пробуем
    известные ключи, а если не подошли — берём единственное значение.
    """
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    values = [v for v in data.values() if v is not None]
    return values[0] if len(values) == 1 else None


def _resolve_operation(operation: str) -> tuple[str, str]:
    """
    Имя операции и её хэш. Снятое с сайта имеет приоритет над запасным:
    имя обязано соответствовать хэшу, иначе сервер отвергнет запрос.
    """
    import query_sniffer

    sniffed = query_sniffer.load_hashes()
    for name in OPERATION_ALIASES.get(operation, [operation]):
        if name in sniffed:
            return name, sniffed[name]

    if operation in FALLBACK_QUERIES:
        return operation, FALLBACK_QUERIES[operation]
    raise RuntimeError(
        f"Нет хэша для операции {operation}. Снимите его: python query_sniffer.py"
    )


async def _persisted(operation: str, variables: dict, retry: bool = True) -> dict:
    """
    Persisted-операция. Фронт шлёт такие GET-ом, но GET без content-type
    Apollo блокирует как возможный CSRF, поэтому шлём POST с JSON-телом: для APQ
    это равнозначно, а заголовок content-type снимает вопрос защиты.
    """
    name, sha256 = _resolve_operation(operation)
    payload = {
        "operationName": name,
        "variables": variables,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
    }

    try:
        return await asyncio.to_thread(transport.request, "post", json=payload)
    except Exception as e:
        # Устаревший хэш сервер отвергает («Access denied», PersistedQueryNotFound).
        # Снимаем актуальные с сайта браузером и повторяем — один раз.
        if not retry or not _looks_like_stale_hash(e):
            raise
        logger.info("Операция %s отклонена (%s) — обновляю хэши с сайта", operation, e)
        import query_sniffer

        await asyncio.to_thread(query_sniffer.refresh)
        return await _persisted(operation, variables, retry=False)


def _looks_like_stale_hash(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in ("access denied", "forbidden", "persistedquerynotfound", "not found")
    )


async def search_games(name: str = "", count: int = 24) -> list[dict]:
    """
    Игры и приложения, доступные для продажи. Без имени — первые `count`,
    с именем — поиск. Возвращает [{id, name, slug}, ...].
    """
    # Формат снят с самого фронта: фильтр знает name, а поля type у него нет.
    variables = {"pagination": {"first": count}}
    if name:
        variables["filter"] = {"name": name}

    data = await _persisted("games", variables)
    games = _unwrap(data, "sellGames", "games") or {}
    edges = games.get("edges") or []
    return [edge["node"] for edge in edges if edge.get("node")]


async def fetch_game(slug: str = "", game_id: str = "") -> dict:
    """
    Игра со списком категорий. В мастере это `gameWithCategories` и она
    принимает только id; старая `Game`/`GamePage` понимала ещё и slug.
    """
    # Аргументы зависят от того, какую операцию использует текущий фронт:
    # gameWithCategories знает только id, Game — только slug.
    name, _ = _resolve_operation("GamePage")
    if name == "gameWithCategories":
        variables = {"id": game_id}
    elif name == "Game":
        variables = {"slug": slug}
    else:
        variables = {"id": game_id or None, "slug": slug or None}

    data = await _persisted("GamePage", variables)
    game = _unwrap(data, "gameWithCategories", "game")
    if not game:
        raise RuntimeError(f"Игра не найдена: {slug or game_id}")
    return game


async def fetch_category_options(category_id: str) -> list[dict]:
    """
    Характеристики категории. Фронт запрашивает их отдельной операцией
    `gameCategoryOptions`, а не вместе с категорией.
    """
    data = await _persisted("gameCategoryOptions", {"id": category_id})
    options = _unwrap(data, "gameCategoryOptions", "gameCategory") or []
    if isinstance(options, dict):  # иногда приходит объект категории
        options = options.get("options") or []
    return options


async def fetch_category(
    category_id: str = "", game_id: str = "", slug: str = ""
) -> dict:
    """
    Полные данные категории, включая options — из них собираются
    атрибуты товара. Берётся по id либо по связке game_id + slug.
    """
    if category_id:
        variables = {"id": category_id}
    else:
        variables = {"gameId": game_id or None, "slug": slug or None}

    data = await _persisted("GamePageCategory", variables)
    category = _unwrap(data, "gameCategory", "gamePageCategory")
    if not category:
        raise RuntimeError(f"Категория не найдена: {slug or category_id}")
    return category


async def fetch_obtaining_types(game_category_id: str, count: int = 24) -> list[dict]:
    """Способы получения товара внутри категории."""
    data = await _persisted(
        "gameCategoryObtainingTypes",
        {
            "pagination": {"first": count},
            "filter": {"gameCategoryId": game_category_id},
        },
    )
    edges = (_unwrap(data, "gameCategoryObtainingTypes") or {}).get("edges") or []
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
            "pagination": {"first": count},
            "filter": {
                "gameCategoryId": game_category_id,
                "obtainingTypeId": obtaining_type_id,
            },
        },
    )
    edges = (_unwrap(data, "gameCategoryDataFields") or {}).get("edges") or []
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

    body, content_type = transport.encode_multipart(
        {"operations": json.dumps(operations), "map": json.dumps(file_map)}, files
    )
    data = await asyncio.to_thread(
        transport.request, "post", data=body, content_type=content_type
    )

    item = data.get("createItem")
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
