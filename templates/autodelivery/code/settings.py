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

# Чем продавец очищает текстовую настройку. Пустое сообщение Telegram
# отправить не даёт, и без этого соглашения очистить поле было нечем вовсе.
#
# Очистка обязана происходить ЗДЕСЬ, а не на экране: однажды подсказка
# обещала очистку точкой, а общий обработчик клал точку текстом — и
# `keyword` из одной точки переставал узнавать свою карту и забирал чужие
# заказы. Экран обещает то, что делает код.
CLEAR = "."


def _text(value) -> str:
    """Текст настройки. Точка — очистка."""
    text = str(value or "").strip()

    return "" if text == CLEAR else text


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
            "region": str(conf.get("region") or "").upper(),
            "keyword": str(conf.get("keyword") or ""),
            "greeting": str(conf.get("greeting") or ""),
            "note": str(conf.get("note") or ""),
            "ad_title": str(conf.get("ad_title") or ""),
            "ad_text": str(conf.get("ad_text") or ""),
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

    def ready(self, slug: str, card=None) -> tuple[bool, str]:
        """Можно ли выдавать по этой карте. → (можно, чего не хватает).

        У карты с подкатегорией номера услуг не нужны: движок находит их
        в каталоге сам. Требовать их значило бы заставлять продавца
        переписывать с телефона десятки чужих UUID — по одному на регион.
        """
        if not self.card(slug)["enabled"]:
            return False, "выдача выключена"

        if card is not None and getattr(card, "subcategory", ""):
            return True, ""

        missing = [r for r in REGIONS if not self.service_id(slug, r)]

        if len(missing) == len(REGIONS):
            return False, "не задана ни одна услуга поставщика"

        if missing:
            return True, f"нет услуги для региона {', '.join(missing)}"

        return True, ""

    # ---------- глушка ----------

    def _shared(self) -> dict:
        shared = getattr(self.store, "shared", None)

        return shared() if callable(shared) else {}

    def paused(self) -> bool:
        """Выдача остановлена целиком."""
        return bool(self._shared().get("paused"))

    def set_paused(self, on: bool) -> None:
        self._shared()["paused"] = bool(on)
        self.store.save()

    def held(self) -> dict:
        """Заказы на паузе: номер → {«title», «at»}."""
        rows = self._shared().get("held")

        return dict(rows) if isinstance(rows, dict) else {}

    def release(self, order_id: str = "") -> int:
        """Снять с паузы. → сколько сняли.

        Снятые заказы выдаются обычным путём: бот купит код и отправит.
        """
        held = self._shared().setdefault("held", {})
        keys = [str(order_id)] if order_id else list(held)
        gone = [k for k in keys if held.pop(k, None) is not None]
        self.store.save()

        return len(gone)

    def close(self, slug: str, order_id: str = "") -> int:
        """Считать заказы закрытыми: выдача по ним больше не нужна.

        Номера ложатся в `closed` карты — тот же список, куда движок
        кладёт заказы, закрывшиеся сами. Снять с паузы и не выдать — не
        одно и то же: снятый выдастся, закрытый не выдастся никогда.
        """
        held = self._shared().setdefault("held", {})
        keys = [str(order_id)] if order_id else list(held)
        closed = self.store.conf(str(slug)).setdefault("closed", [])
        known = {str(x) for x in closed}
        gone = 0

        for key in keys:
            if held.pop(key, None) is None:
                continue

            if key not in known:
                closed.append(key)
                known.add(key)

            gone += 1

        self.store.save()

        return gone

    # ---------- запись ----------

    def set_enabled(self, slug: str, on: bool) -> None:
        self._change(slug, "enabled", bool(on))

    def set_region(self, slug: str, region: str) -> None:
        """Запасной регион карты. Пусто — брать из описания товара."""
        self._change(slug, "region", _text(region).upper())

    def set_keyword(self, slug: str, word: str) -> None:
        self._change(slug, "keyword", " ".join(_text(word).split()))

    def set_greeting(self, slug: str, text: str) -> None:
        self._change(slug, "greeting", _text(text))

    def set_note(self, slug: str, text: str) -> None:
        self._change(slug, "note", _text(text))

    def set_ad_title(self, slug: str, text: str) -> None:
        self._change(slug, "ad_title", _text(text))

    def set_ad_text(self, slug: str, text: str) -> None:
        self._change(slug, "ad_text", _text(text))

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
