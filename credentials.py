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


def _normalize(raw: str) -> str:
    """
    Приводит присланное к виду «token=…». Из браузера копируют по-разному:
    голое значение куки, пару `token=…`, целую строку Cookie с несколькими
    парами — принимаем всё.
    """
    raw = " ".join(str(raw).split())
    if not raw:
        return ""
    if "=" in raw:
        return raw
    return f"token={raw}"


def save(raw: str) -> str:
    """Запоминает куку. Возвращает то, что записано."""
    value = _normalize(raw)
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
