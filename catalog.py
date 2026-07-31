"""
Каталог идентификаторов Playerok.

Справочники по API недоступны: `SellGames` отвечает «Access denied» даже с
правильным хэшем и куками. Но их прекрасно получает сам фронт — и мы можем
подслушать его ответы, пока бот идёт по мастеру в браузере.

Так каталог наполняется сам собой при обычном создании товара: игра,
категория, способ передачи, характеристики и поля запоминаются вместе с их
идентификаторами. Дальше товар можно создавать уже запросами — `createItem`
и `publishItem` работают от лица пользователя и в справочниках не нуждаются.

Файл: .playerok_catalog.json
"""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

CATALOG_FILE = os.getenv("PLAYEROK_CATALOG_FILE", ".playerok_catalog.json")

EMPTY: dict = {
    "games": {},         # имя игры → {id, slug}
    "categories": {},    # id игры → {имя категории: {id}}
    "obtaining": {},     # id категории → {имя способа: {id}}
    "options": {},       # id категории → [{label, field, value}]
    "data_fields": {},   # "idКатегории|idСпособа" → [{id, label, type}]
}


def load() -> dict:
    if not os.path.exists(CATALOG_FILE):
        return json.loads(json.dumps(EMPTY))
    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Не смог прочитать %s: %s", CATALOG_FILE, e)
        return json.loads(json.dumps(EMPTY))

    catalog = json.loads(json.dumps(EMPTY))
    catalog.update({k: v for k, v in saved.items() if k in EMPTY})
    return catalog


def save(catalog: dict):
    catalog["updated_at"] = time.time()
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


# ── Поиск ─────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _match(mapping: dict, wanted: str) -> dict | None:
    """Ищет по имени: сначала точно, потом по вхождению (эмодзи в названиях)."""
    target = _norm(wanted)
    for name, value in mapping.items():
        if _norm(name) == target:
            return value
    for name, value in mapping.items():
        name_n = _norm(name)
        if target in name_n or name_n in target:
            return value
    return None


def find_game(name: str) -> dict | None:
    return _match(load().get("games", {}), name)


def find_category(game_id: str, name: str) -> dict | None:
    return _match(load().get("categories", {}).get(game_id, {}), name)


def find_obtaining(category_id: str, name: str) -> dict | None:
    return _match(load().get("obtaining", {}).get(category_id, {}), name)


def options(category_id: str) -> list[dict]:
    return load().get("options", {}).get(category_id, [])


def data_fields(category_id: str, obtaining_id: str) -> list[dict]:
    return load().get("data_fields", {}).get(f"{category_id}|{obtaining_id}", [])


def resolve(game: str, category: str, obtaining: str) -> dict:
    """
    Идентификаторы по названиям — то, что нужно `createItem`.
    Пустой словарь означает, что этой связки в каталоге ещё нет.
    """
    found_game = find_game(game)
    if not found_game:
        return {}

    found_category = find_category(found_game["id"], category)
    if not found_category:
        return {}

    result = {"game_id": found_game["id"], "category_id": found_category["id"]}
    if obtaining:
        found_obtaining = find_obtaining(found_category["id"], obtaining)
        if not found_obtaining:
            return {}
        result["obtaining_id"] = found_obtaining["id"]
    return result


# ── Сбор из браузера ──────────────────────────────────────────────────────────

def _nodes(payload) -> list[dict]:
    """Узлы из ответа-соединения: {edges: [{node: {...}}]}."""
    if not isinstance(payload, dict):
        return []
    edges = payload.get("edges")
    if isinstance(edges, list):
        return [e.get("node") for e in edges if isinstance(e, dict) and e.get("node")]
    return []


def absorb(catalog: dict, operation: str, variables: dict, data: dict) -> bool:
    """
    Раскладывает один ответ фронта по каталогу. Возвращает True, если
    что-то новое добавилось.
    """
    if not isinstance(data, dict):
        return False
    changed = False

    def payload(*names):
        for name in names:
            if data.get(name) is not None:
                return data[name]
        values = [v for v in data.values() if v is not None]
        return values[0] if len(values) == 1 else None

    if operation in ("SellGames", "games"):
        for node in _nodes(payload("sellGames", "games")):
            if node.get("id") and node.get("name"):
                catalog["games"][node["name"]] = {
                    "id": node["id"], "slug": node.get("slug", "")
                }
                changed = True

    elif operation in ("gameWithCategories", "Game", "GamePage"):
        game = payload("gameWithCategories", "game") or {}
        game_id = game.get("id")
        if game_id:
            if game.get("name"):
                catalog["games"][game["name"]] = {
                    "id": game_id, "slug": game.get("slug", "")
                }
            bucket = catalog["categories"].setdefault(game_id, {})
            for category in game.get("categories") or []:
                if category.get("id") and category.get("name"):
                    bucket[category["name"]] = {"id": category["id"]}
                    changed = True

    elif operation == "gameCategoryObtainingTypes":
        category_id = (variables.get("filter") or {}).get("gameCategoryId")
        if category_id:
            bucket = catalog["obtaining"].setdefault(category_id, {})
            for node in _nodes(payload("gameCategoryObtainingTypes")):
                if node.get("id") and node.get("name"):
                    bucket[node["name"]] = {"id": node["id"]}
                    changed = True

    elif operation == "gameCategoryOptions":
        category_id = variables.get("id")
        found = payload("gameCategoryOptions", "gameCategory")
        if isinstance(found, dict):
            found = found.get("options") or []
        if category_id and isinstance(found, list) and found:
            catalog["options"][category_id] = [
                {
                    "label": o.get("label"),
                    "value": o.get("value"),
                    "field": o.get("field"),
                }
                for o in found
                if isinstance(o, dict)
            ]
            changed = True

    elif operation == "gameCategoryDataFields":
        filters = variables.get("filter") or {}
        key = f"{filters.get('gameCategoryId')}|{filters.get('obtainingTypeId')}"
        nodes = _nodes(payload("gameCategoryDataFields"))
        if nodes:
            catalog["data_fields"][key] = [
                {"id": n.get("id"), "label": n.get("label"), "type": n.get("type")}
                for n in nodes
            ]
            changed = True

    elif operation in ("GamePageCategory", "gameCategory"):
        category = payload("gameCategory") or {}
        if category.get("id") and category.get("options"):
            catalog["options"][category["id"]] = [
                {
                    "label": o.get("label"),
                    "value": o.get("value"),
                    "field": o.get("field"),
                }
                for o in category["options"]
            ]
            changed = True

    return changed


def harvest(browser) -> int:
    """
    Вычерпывает журнал сети браузера и запоминает ответы фронта.
    Возвращает число обновлённых разделов каталога.
    """
    try:
        entries = browser.driver.get_log("performance")
    except Exception as e:
        logger.warning("Журнал сети недоступен: %s", e)
        return 0

    requests: dict[str, tuple[str, dict]] = {}
    responses: list[str] = []

    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, ValueError):
            continue

        method = message.get("method")
        params = message.get("params", {})
        request_id = params.get("requestId")
        if not request_id:
            continue

        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            if "/graphql" not in (request.get("url") or ""):
                continue
            operation, variables = _describe(request)
            if operation:
                requests[request_id] = (operation, variables)

        elif method == "Network.loadingFinished" and request_id in requests:
            responses.append(request_id)

    catalog = load()
    updated = 0
    for request_id in responses:
        operation, variables = requests[request_id]
        try:
            body = browser.driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_id}
            )
            data = json.loads(body.get("body") or "{}").get("data") or {}
        except Exception:
            continue  # тело уже вытеснено из кэша — не страшно

        if absorb(catalog, operation, variables, data):
            updated += 1

    if updated:
        save(catalog)
        logger.info("Каталог пополнен: %s ответов фронта", updated)
    return updated


def _describe(request: dict) -> tuple[str, dict]:
    """Имя операции и переменные запроса — из тела или из строки адреса."""
    from urllib.parse import parse_qs, urlparse

    def unpack(source: dict) -> tuple[str, dict]:
        operation = source.get("operationName") or ""
        variables = source.get("variables")
        if isinstance(variables, str):
            try:
                variables = json.loads(variables)
            except ValueError:
                variables = {}
        return operation, variables if isinstance(variables, dict) else {}

    if request.get("postData"):
        try:
            return unpack(json.loads(request["postData"]))
        except ValueError:
            pass

    query = parse_qs(urlparse(request.get("url", "")).query)
    return unpack({k: v[0] for k, v in query.items()})
