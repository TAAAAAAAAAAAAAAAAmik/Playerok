"""
Кука сессии Playerok, заданная через бота.

Раньше её знали только из `.env`, а значит менять токен приходилось на
сервере: файл правится руками, служба перезапускается. Токен живёт не вечно
и слетает при смене IP, поэтому это самая частая правка — и самая неудобная.

Здесь кука лежит в отдельном файле и перечитывается при каждом запросе, так
что присланная боту в чат заменяет прежнюю сразу, без перезапуска.
Значение из `.env` остаётся запасным: оно годится как начальное.

Файл: .playerok_cookie
"""
from __future__ import annotations

import logging
import os

import config

logger = logging.getLogger(__name__)

COOKIE_FILE = os.getenv("PLAYEROK_COOKIE_FILE", ".playerok_cookie")


def _from_json(raw: str) -> str | None:
    """
    Значение куки `token` из выгрузки расширения вроде Cookie Editor: там
    массив объектов с полями name и value, иногда объект имя→значение.
    None означает, что это вообще не JSON.
    """
    import json

    try:
        data = json.loads(raw)
    except ValueError:
        return None

    if isinstance(data, dict):
        # Либо {"token": "..."}, либо один объект куки {"name": ..., "value": ...}
        if data.get("name") and "value" in data:
            data = [data]
        else:
            for key, value in data.items():
                if str(key).strip().casefold() == "token":
                    return str(value).strip()
            return ""

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip().casefold() == "token":
                return str(item.get("value", "")).strip()
    return ""


def normalize(raw: str) -> str:
    """
    Приводит присланное к виду «token=…». Копируют по-разному: голое значение
    куки, пару `token=…`, целую строку Cookie, выгрузку расширения в JSON —
    принимаем всё.

    Из нескольких кук берём только `token`. Остальные с чужого компьютера не
    просто бесполезны, а вредны: `__ddg*` привязана к IP и User-Agent, и с
    сервера, у которого другой адрес, она сразу делает сессию недействительной.
    Свою куку защиты сервер получает сам.
    """
    raw = str(raw).strip()
    if not raw:
        return ""

    from_json = _from_json(raw)
    if from_json is not None:
        return f"token={from_json}" if from_json else ""

    raw = " ".join(raw.split())
    if "=" not in raw:
        return f"token={raw}"

    for chunk in raw.split(";"):
        name, _, value = chunk.partition("=")
        if name.strip().casefold() == "token":
            return f"token={value.strip()}"

    # Пара вида «что-то=…», но не token — пусть решает вызывающий.
    return ""


def save(raw: str) -> str:
    """Запоминает куку. Возвращает то, что записано."""
    value = normalize(raw)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(value)
    os.chmod(COOKIE_FILE, 0o600)  # в файле сессия аккаунта — чужим не читать
    logger.info("Кука Playerok обновлена через бота")
    return value


def load() -> str:
    """Кука из файла; пустая строка, если её не задавали."""
    if not os.path.exists(COOKIE_FILE):
        return ""
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        logger.warning("Не смог прочитать %s: %s", COOKIE_FILE, e)
        return ""


def cookie_string() -> str:
    """Что подставлять в запросы: присланное боту главнее, чем из `.env`."""
    return load() or config.PLAYEROK_COOKIES


def token() -> str:
    """Значение самой куки `token` — по нему видно, задана ли сессия."""
    for chunk in cookie_string().split(";"):
        name, _, value = chunk.partition("=")
        if name.strip() == "token":
            return value.strip()
    return ""


def clear():
    if os.path.exists(COOKIE_FILE):
        os.remove(COOKIE_FILE)
