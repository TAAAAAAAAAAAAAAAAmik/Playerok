"""
Создание товара запросами к API — то же, что делает мастер в браузере,
но напрямую через GraphQL.

Соответствие шагам мастера (см. docs/PRODUCT_CREATION.md):

    1. Выберите раздел товаров → games          → gameId
    2. Выберите категорию      → GamePage       → gameCategoryId
    3. Способ передачи         → obtainingTypes → obtainingTypeId
    4. Характеристики          → options категории → attributes
    5. Данные товара           → dataFields (только ITEM_DATA)
    6. Фото                    → файлы-вложения createItem
    7. О товаре + Цена         → createItem (черновик)
    8. Выберите сервис         → itemPriorityStatuses
    9. Публикация              → publishItem

Пользователь называет игру, категорию и характеристики так же, как они
подписаны на сайте, — сопоставление по названиям делает этот модуль.
"""
from __future__ import annotations

import logging
import mimetypes
import os

import playerok_client as api
from selenium_creator import ProductDraft, StepResult

logger = logging.getLogger(__name__)


class ApiCreationError(RuntimeError):
    """Не удалось создать товар через API."""


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _match(items: list[dict], wanted: str, *fields: str) -> dict | None:
    """
    Ищет элемент, у которого одно из полей совпадает с искомым названием.
    Сначала точное совпадение, потом вхождение — названия на сайте бывают
    с эмодзи и приписками вроде «Звезды 🏷».
    """
    target = _norm(wanted)
    for exact in (True, False):
        for item in items:
            for field in fields:
                value = _norm(item.get(field) or "")
                if not value:
                    continue
                if value == target if exact else (target in value or value in target):
                    return item
    return None


def _read_image(path: str) -> tuple[str, bytes, str]:
    with open(path, "rb") as f:
        content = f.read()
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return os.path.basename(path), content, mime


async def create_product(draft: ProductDraft, on_step=None) -> dict:
    """
    Создаёт товар и, если размещение не «later», выставляет его на продажу.
    Возвращает созданный товар. `on_step(StepResult)` вызывается после
    каждого шага — тем же способом, что и браузерный мастер.
    """
    results: list[StepResult] = []

    def report(number: int, title: str, detail: str):
        result = StepResult(number, title, True, detail)
        results.append(result)
        if on_step:
            on_step(result)

    def fail(number: int, title: str, error: Exception):
        result = StepResult(number, title, False, str(error))
        if on_step:
            on_step(result)
        raise ApiCreationError(f"Шаг {number} «{title}»: {error}") from error

    # 1. Игра
    async def resolve_game() -> dict:
        if draft.game_id:
            return {"id": draft.game_id, "name": draft.game, "slug": ""}
        games = await api.search_games(draft.game)
        game = _match(games, draft.game, "name", "slug")
        if not game:
            raise RuntimeError(
                f"«{draft.game}» не найдена. Похожие: "
                + ", ".join(g.get("name", "?") for g in games[:5])
            )
        return game

    title = "Раздел товаров"
    try:
        game = await resolve_game()
        report(1, title, f"Игра: {game['name']}")
    except Exception as e:
        fail(1, title, e)

    # 2. Категория (полные данные нужны ради options)
    async def resolve_category() -> dict:
        if draft.category_id:
            # Характеристики уже разобраны — категория нужна была только
            # ради options, значит и запрашивать её незачем.
            if draft.attribute_values or not draft.attributes:
                return {"id": draft.category_id, "name": draft.category, "options": []}
            return await api.fetch_category(category_id=draft.category_id)
        game_page = await api.fetch_game(slug=game.get("slug", ""), game_id=game["id"])
        categories = game_page.get("categories") or []
        found = _match(categories, draft.category, "name", "slug")
        if not found:
            raise RuntimeError(
                f"«{draft.category}» не найдена. Есть: "
                + ", ".join(c.get("name", "?") for c in categories[:10])
            )
        return await api.fetch_category(category_id=found["id"])

    title = "Категория"
    try:
        category = await resolve_category()
        report(2, title, f"Категория: {category['name']}")
    except Exception as e:
        fail(2, title, e)

    # 3. Способ передачи
    async def resolve_obtaining() -> dict:
        if draft.obtaining_type_id:
            return {"id": draft.obtaining_type_id, "name": draft.obtaining_type}
        types_ = await api.fetch_obtaining_types(category["id"])
        found = _match(types_, draft.obtaining_type, "name", "slug")
        if not found:
            raise RuntimeError(
                f"«{draft.obtaining_type}» не найден. Есть: "
                + ", ".join(o.get("name", "?") for o in types_)
            )
        return found

    title = "Способ передачи"
    try:
        obtaining = await resolve_obtaining()
        report(3, title, f"Способ передачи: {obtaining['name']}")
    except Exception as e:
        fail(3, title, e)

    # 4. Характеристики → attributes
    async def resolve_attributes() -> dict[str, str]:
        if draft.attribute_values:
            return dict(draft.attribute_values)
        options = category.get("options") or await api.fetch_category_options(category["id"])
        chosen: dict[str, str] = {}
        missing: list[str] = []
        for wanted in draft.attributes:
            option = _match(options, wanted, "label", "value")
            if option and option.get("field"):
                chosen[option["field"]] = option.get("value")
            else:
                missing.append(wanted)
        if missing:
            raise RuntimeError(
                "не нашёл характеристики: "
                + ", ".join(missing)
                + ". Доступны: "
                + ", ".join(str(o.get("label") or o.get("value")) for o in options[:10])
            )
        return chosen

    title = "Характеристики"
    try:
        attributes = await resolve_attributes()
        report(
            4,
            title,
            "Характеристики: "
            + (", ".join(f"{k}={v}" for k, v in attributes.items()) or "нет"),
        )
    except Exception as e:
        fail(4, title, e)

    # 5. Данные товара → dataFields
    async def resolve_data_fields() -> list[dict]:
        if draft.data_field_values:
            return list(draft.data_field_values)
        fields = await api.fetch_data_fields(category["id"], obtaining["id"])
        # Поля OBTAINING_DATA заполняет покупатель при заказе — их не трогаем.
        item_fields = [f for f in fields if f.get("type") == "ITEM_DATA"]
        filled = []
        for label, value in draft.data_fields.items():
            found = _match(item_fields, label, "label")
            if not found:
                raise RuntimeError(
                    f"поле «{label}» не найдено. Есть: "
                    + ", ".join(f.get("label", "?") for f in item_fields)
                )
            filled.append({"fieldId": found["id"], "value": value})

        skipped = [
            f for f in item_fields
            if f.get("id") not in {d["fieldId"] for d in filled}
        ]
        if skipped:
            logger.info(
                "Не заполнены поля данных: %s",
                ", ".join(f.get("label", "?") for f in skipped),
            )
        return filled

    title = "Данные товара"
    try:
        data_fields = await resolve_data_fields()
        report(5, title, f"Полей заполнено: {len(data_fields)}")
    except Exception as e:
        fail(5, title, e)

    # 6. Фото
    title = "Фото"
    try:
        attachments = [_read_image(p) for p in draft.images if os.path.exists(p)]
        if draft.images and not attachments:
            raise RuntimeError("ни один файл изображения не найден на диске")
        report(6, title, f"Изображений: {len(attachments)}")
    except Exception as e:
        fail(6, title, e)

    # 7. Создание черновика
    #
    # Отдельного поля скидки в схеме нет — площадка рисует её сама, когда цена
    # стала ниже прежней (`prevPrice`). Поэтому черновик создаётся по цене «до
    # скидки», а следом updateItem опускает её до той, которую назвал продавец:
    # покупатель видит зачёркнутую старую цену и −27%, а продавец получает ровно
    # свою сумму.
    discount = int(getattr(draft, "discount", 0) or 0)
    list_price = draft.price
    if 0 < discount < 100:
        list_price = round(draft.price / (1 - discount / 100))
        if list_price <= draft.price:  # копеечные цены округлятся в ту же цифру
            discount, list_price = 0, draft.price

    title = "Создание товара"
    try:
        item = await api.create_item(
            game_category_id=category["id"],
            obtaining_type_id=obtaining["id"],
            name=draft.name,
            price=list_price,
            description=draft.description,
            attributes=attributes,
            data_fields=data_fields,
            attachments=attachments,
        )
        detail = f"Черновик создан: {item.get('name')} (id {item['id']})"
        # Сервер сам решает, можно ли выставлять товар. Если нельзя — сказать об
        # этом здесь, а не ловить безымянный отказ на девятом шаге.
        if item.get("mayBePublished") is False:
            detail += (
                f"; выставлять пока нельзя (статус {item.get('status')}"
                + (f": {item['statusDescription']}" if item.get("statusDescription") else "")
                + ")"
            )
        report(7, title, detail)
    except Exception as e:
        fail(7, title, e)

    if discount:
        try:
            item = await api.update_item(item["id"], price=draft.price)
            report(
                7,
                "Скидка",
                f"Цена {list_price} → {draft.price} ₽, скидка {discount}%",
            )
        except Exception as e:
            # Оставить товар по завышенной цене нельзя — это не то, что просили.
            fail(
                7,
                "Скидка",
                RuntimeError(
                    f"{e}. Черновик создан по цене {list_price} ₽ вместо "
                    f"{draft.price} ₽ — удалите его на сайте и повторите "
                    f"с PLAYEROK_DISCOUNT=0"
                ),
            )

    # 8. Статусы приоритета
    title = "Выберите сервис"
    if draft.placement == "later":
        report(8, title, "Оставляем в черновиках — статусы не запрашиваем")
        report(9, "Публикация", "Пропущена: товар сохранён черновиком")
        return item

    try:
        statuses = await api.fetch_priority_statuses(item["id"], draft.price)
        if draft.placement == "free":
            status = next((s for s in statuses if not s.get("price")), None)
            if not status:
                raise RuntimeError(
                    "бесплатного размещения нет, доступны: "
                    + ", ".join(f"{s.get('name')} — {s.get('price')} ₽" for s in statuses)
                )
        else:
            status = next((s for s in statuses if s.get("price")), None)
            if not status:
                raise RuntimeError("платных статусов не предложено")
        report(8, title, f"Размещение: {status.get('name')} ({status.get('price', 0)} ₽)")
    except Exception as e:
        fail(8, title, e)

    # 9. Публикация
    title = "Публикация"
    try:
        published = await api.publish_item(item["id"], status["id"])
        report(9, title, f"Опубликован, статус: {published.get('status')}")
        return published
    except Exception as e:
        fail(9, title, e)


async def publish_free(item: dict) -> dict:
    """
    Выставляет созданный черновик на бесплатное размещение: берёт статус
    приоритета с нулевой ценой и вызывает publishItem.
    """
    statuses = await api.fetch_priority_statuses(item["id"], item.get("price", 0))
    free = next((s for s in statuses if not s.get("price")), None)
    if not free:
        raise ApiCreationError(
            "Бесплатного размещения нет, доступны: "
            + ", ".join(f"{s.get('name')} — {s.get('price')} ₽" for s in statuses)
        )
    return await api.publish_item(item["id"], free["id"])
