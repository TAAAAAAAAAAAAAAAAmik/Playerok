"""Копия живого объявления: взять с витрины то, что уже выставлено.

Шаблон запоминается при создании товара — а объявления, заведённые раньше
бота или в кабинете на сайте, шаблона не имеют. Повторить их было нечем,
хотя всё нужное у площадки есть: категория, характеристики, поля, текст и
картинки.

ЧТО ТУТ ЕСТЬ. Только разбор: объявление площадки → черновик. Ни сети, ни
телеграма, чтобы проверять это тестами, а не живыми товарами.

ОСОБО ПРО ОПИСАНИЕ. В нём уже стоят строки «Регион кода: …» и
«Номинал: …» — их ставит бот при создании. Скопировав описание как есть,
мы получили бы ДВЕ такие строки: свою и чужую. Движок выдачи читает
первую, и две разные строки означают покупку не того номинала. Поэтому
регион и номинал вынимаются, а строки вырезаются — бот поставит их заново.
"""
from __future__ import annotations

from catalog import nominal_for, region_from_description


def _name(node, *fields) -> str:
    for field in fields:
        got = getattr(node, field, None)

        if got not in (None, ""):
            return str(got)

    return ""


def _ref(node) -> dict | None:
    """Ссылка на сущность площадки: {"id": ..., "name": ...}."""
    if node is None:
        return None

    got = _name(node, "id")

    if not got:
        return None

    return {"id": got, "name": _name(node, "name", "label", "slug") or got}


def options_of(item) -> list:
    """Характеристики в том виде, в каком их ждёт создание товара.

    Площадка отдаёт их готовым словарём «поле → значение» — тем же самым,
    который принимает обратно. Разворачиваем его в список: так черновик
    видит, что все характеристики уже выбраны, и не спрашивает про них.
    """
    found = getattr(item, "attributes", None)

    if not isinstance(found, dict):
        return []

    # Название характеристики площадка в товаре не отдаёт — только поле и
    # значение. Показывать продавцу «a1: Global» хуже, чем
    # «Характеристика: Global»: внутреннее имя поля ему ничего не говорит.
    return [{"field": str(field), "value": value, "group": "",
             "chosen": str(value)}
            for field, value in found.items() if value not in (None, "")]


def fields_of(item) -> list:
    """Поля с данными: что спрашивала категория и что было отвечено."""
    out = []

    for field in getattr(item, "data_fields", None) or []:
        field_id = _name(field, "id")

        if not field_id:
            continue

        out.append({
            "id": field_id,
            "label": _name(field, "label") or "Поле",
            "required": bool(getattr(field, "required", False)),
            "value": str(getattr(field, "value", "") or ""),
        })

    return out


def photos_of(item) -> list:
    """Ссылки на картинки объявления."""
    out = []

    for attachment in getattr(item, "attachments", None) or []:
        url = _name(attachment, "url")

        if url:
            out.append(url)

    return out


def plan(item) -> tuple:
    """Объявление площадки → (что положить в черновик, чего не хватает).

    Второе — список того, без чего копию не создать. Возвращается, а не
    угадывается: подставить недостающую категорию нельзя, а создать товар
    не там, где хотели, — дороже, чем не создать вовсе.
    """
    text = str(getattr(item, "description", "") or "")
    name = str(getattr(item, "name", "") or "")
    value, _ = nominal_for(name, text)

    draft = {
        "game": _ref(getattr(item, "game", None)),
        "category": _ref(getattr(item, "category", None)),
        "obtaining": _ref(getattr(item, "obtaining_type", None)),
        "name": name,
        "price": int(getattr(item, "price", 0) or 0),
        "region": region_from_description(text),
        "nominal": float(value) if value else 0.0,
        "options": options_of(item),
        "fields": fields_of(item),
        "photos": photos_of(item),
    }

    gaps = []

    if not draft["name"]:
        gaps.append("название")

    if not draft["price"]:
        gaps.append("цена")

    if not draft["category"]:
        gaps.append("категория")

    if not draft["obtaining"]:
        gaps.append("способ получения")

    if not draft["photos"]:
        gaps.append("картинки")

    return draft, gaps
