"""Настройки автовыдачи поверх того самого хранилища, что читает движок.

Почему поверх, а не своим файлом. Движок спрашивает настройки карты у
хранилища состояния — там же, где лежит журнал выданных заказов:
`enabled`, `keyword`, `region`, `greeting`, `note` и рядом `delivered`.
Отдельный файл настроек стал бы вторым источником правды, и получилось бы
«поменял в боте, а выдача работает по-старому» — ошибка, которую ищут
часами.

Что добавлено к тому, что уже было: `services` — номера услуг поставщика
по регионам. Раньше они жили только в переменных окружения, то есть
менялись лишь на сервере, а менять их хочется с телефона.

ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ. Неизвестная карта считается выключенной: включать
выдачу самим значило бы покупать коды на товары, которых продавец не
настраивал.
"""
from __future__ import annotations

import os

# Регионы, для которых держим номера услуг. Тот же список, что понимает
# разбор описания товара.
REGIONS = ("GL", "RU")


class Settings:
    """Настройки карт одного продавца. Пишет туда же, откуда читает движок."""

    def __init__(self, store):
        self.store = store

    # ---------- чтение ----------

    def card(self, slug: str) -> dict:
        """Настройки карты как их видит движок."""
        conf = self.store.conf(str(slug))
        services = conf.get("services")

        return {
            "enabled": bool(conf.get("enabled")),
            "keyword": str(conf.get("keyword") or ""),
            "greeting": str(conf.get("greeting") or ""),
            "note": str(conf.get("note") or ""),
            "services": services if isinstance(services, dict) else {},
        }

    def service_id(self, slug: str, region: str) -> str:
        """Номер услуги поставщика для карты и региона.

        Из настроек, а если там пусто — из окружения: так продолжают
        работать установки, настроенные до появления этого меню.
        """
        region = str(region).upper()
        saved = str(self.card(slug)["services"].get(region) or "")

        if saved:
            return saved

        return os.environ.get(
            f"APPROUTE_SERVICE_{str(slug).upper()}_{region}", "").strip()

    def ready(self, slug: str) -> tuple[bool, str]:
        """Можно ли выдавать по этой карте. → (можно, чего не хватает)."""
        if not self.card(slug)["enabled"]:
            return False, "выдача выключена"

        missing = [r for r in REGIONS if not self.service_id(slug, r)]

        if len(missing) == len(REGIONS):
            return False, "не задана ни одна услуга поставщика"

        if missing:
            return True, f"нет услуги для региона {', '.join(missing)}"

        return True, ""

    # ---------- запись ----------

    def set_enabled(self, slug: str, on: bool) -> None:
        self._change(slug, "enabled", bool(on))

    def set_keyword(self, slug: str, word: str) -> None:
        self._change(slug, "keyword", " ".join(str(word or "").split()))

    def set_greeting(self, slug: str, text: str) -> None:
        self._change(slug, "greeting", str(text or "").strip())

    def set_note(self, slug: str, text: str) -> None:
        self._change(slug, "note", str(text or "").strip())

    def set_service(self, slug: str, region: str, service_id: str) -> None:
        services = dict(self.card(slug)["services"])
        region = str(region).upper()
        value = str(service_id or "").strip()

        if value:
            services[region] = value
        else:
            services.pop(region, None)

        self._change(slug, "services", services)

    def _change(self, slug: str, field: str, value) -> None:
        # Правим тот самый словарь, что отдаёт хранилище: подменять его
        # целиком значило бы потерять журнал выданных заказов.
        self.store.conf(str(slug))[field] = value
        self.store.save()
