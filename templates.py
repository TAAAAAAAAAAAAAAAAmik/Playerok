"""
Шаблоны созданных товаров.

Playerok разрешает держать бесплатно ограниченное число объявлений, и
одинаковые описания площадка не любит. Поэтому каждый успешно созданный
товар сохраняется как шаблон, а при повторном создании бот берёт его
целиком и меняет комментарий покупателю — так копия не выглядит дублем.

Файл: .playerok_templates.json (в .gitignore, лежит рядом с ботом).
"""
from __future__ import annotations

import json
import logging
import os
import random
import time

logger = logging.getLogger(__name__)

TEMPLATES_FILE = os.getenv("PLAYEROK_TEMPLATES_FILE", ".playerok_templates.json")
MAX_TEMPLATES = int(os.getenv("PLAYEROK_MAX_TEMPLATES", "30"))

# Из чего собирается новый комментарий: смысл один, формулировки разные.
GREETINGS = [
    "Здравствуйте!",
    "Добрый день!",
    "Приветствую!",
    "Привет!",
    "Рад видеть!",
]

BODIES = [
    "Напишу вам в Telegram сразу после оформления заказа.",
    "После оплаты свяжусь с вами в Telegram.",
    "Как только заказ оформлен — пишу вам в ТГ.",
    "Свяжусь с вами в Telegram по указанному @username.",
    "После оформления заказа жду ваш @username в чате.",
]

ENDINGS = [
    "Выдача моментальная.",
    "Отправлю в течение нескольких минут.",
    "Всё сделаю быстро.",
    "Обычно укладываюсь в пару минут.",
    "На связи весь день.",
]


def _load() -> list[dict]:
    if not os.path.exists(TEMPLATES_FILE):
        return []
    try:
        with open(TEMPLATES_FILE, encoding="utf-8") as f:
            return json.load(f).get("templates", [])
    except (OSError, ValueError) as e:
        logger.warning("Не смог прочитать %s: %s", TEMPLATES_FILE, e)
        return []


def _save(templates: list[dict]):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump({"templates": templates[-MAX_TEMPLATES:]}, f,
                  ensure_ascii=False, indent=2)


def all_templates() -> list[dict]:
    """Сохранённые шаблоны, свежие сверху."""
    return list(reversed(_load()))


def save(data: dict) -> dict:
    """
    Запоминает созданный товар как шаблон. Возвращает сохранённую запись.
    Одинаковые названия не плодим — обновляем существующую.
    """
    template = {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "price": data.get("price", 0),
        "discount": data.get("discount", 0),
        "game": data.get("game", ""),
        "category": data.get("category", ""),
        "obtaining": data.get("obtaining", ""),
        "attributes": list(data.get("attributes", [])),
        "data_fields": dict(data.get("data_fields", {})),
        "images": [p for p in data.get("images", []) if os.path.exists(p)],
        "saved_at": time.time(),
    }

    templates = [t for t in _load() if t.get("name") != template["name"]]
    templates.append(template)
    _save(templates)
    logger.info("Шаблон сохранён: %s", template["name"])
    return template


def remove(name: str) -> bool:
    templates = _load()
    left = [t for t in templates if t.get("name") != name]
    if len(left) == len(templates):
        return False
    _save(left)
    return True


def vary_comment(original: str = "") -> str:
    """
    Собирает новый комментарий: смысл прежний, формулировка другая.
    Так копии не выглядят дублями и не попадают под фильтры площадки.
    """
    parts = [random.choice(GREETINGS), random.choice(BODIES), random.choice(ENDINGS)]
    fresh = " ".join(parts)

    # Если получилось слово в слово как было — пересобираем ещё раз.
    if fresh.strip() == (original or "").strip():
        parts[1] = random.choice([b for b in BODIES if b != parts[1]])
        fresh = " ".join(parts)
    return fresh


def vary_data_fields(fields: dict[str, str]) -> dict[str, str]:
    """Меняет комментарий среди полей товара, остальные оставляет как есть."""
    updated = dict(fields)
    for label, value in fields.items():
        if "коммент" in label.casefold():
            updated[label] = vary_comment(value)
    # Поля с комментарием может не быть вовсе — тогда ничего не трогаем.
    return updated
