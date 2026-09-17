"""Какие товары умеем выдавать.

Один список на всё: и наблюдение, и боевой цикл, и экран настроек смотрят
сюда, чтобы не разъехались.

Главное поле карты — `subcategory`, ТОЧНОЕ имя подкатегории у поставщика.
Не слово в названии: слово «xbox» на живом каталоге находит 47 услуг, а
гифт-карт среди них 16. Остальное — подписки Game Pass, ключи игр, аккаунты
и `Roblox Wallet Code | XBox`, то есть товар соседней карты. Отбор по слову
увёл бы его, и покупатель получил бы код Roblox вместо карты Xbox.

Номера услуг поставщика здесь не нужны: они свои на каждый регион, их
десятки, и переписывать их руками с телефона продавец не должен. Поле
`services` осталось ручной привязкой на случай, когда отбор по
подкатегории почему-то не подходит.

Поле `measured` — дата, когда разбор номиналов семейства проверяли на живом
каталоге. Пусто значит «не проверяли», а не «не работает»; продавцу это
видно на экране карты, и до включения такую карту стоит прогнать вручную.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from catalog import MONEY, UNITS, Card                        # noqa: E402


def _service(env_name: str) -> str:
    """ID услуги из окружения.

    В коде их не держим: у разных продавцов они разные, а менять их проще
    настройкой, чем выкатом.
    """
    return os.environ.get(env_name, "").strip()


CARDS = [
    Card(
        slug="robux",
        title="Roblox Gift Cards",
        emoji="🎮",
        keywords=("robux", "робукс", "роблокс", "roblox"),
        measure="Robux",
        unit=UNITS,
        # Как это пишут в описаниях. «Роблокс» сюда не входит нарочно:
        # это игра, а не единица, и «Roblox 2024» дал бы номинал 2024.
        measure_words=("робукс", "robuxes", "робаксов", "робакс", "r$"),
        subcategory="Roblox Gift Cards",
        activation="Активируйте код на roblox.com/redeem.",
        pitch="Код на робуксы. Логин покупателя не нужен — он вводит код "
              "сам на roblox.com/redeem.",
        measured="20.08",
        ad_title="Roblox {номинал} — код, моментально",
        ad_text="Код на {номинал}. Регион: {регион}.\n"
                "Активация: roblox.com/redeem.\n"
                "Выдача автоматическая, сразу после оплаты.",
        services={
            # Глобальные коды заметно выгоднее российских: 0.0099 USD за
            # робукс на номинале 10000 против 0.0139–0.0156 у любого RU.
            # Разница почти в полтора раза — это выбор закупочной цены, а
            # не мелочь настройки.
            "GL": _service("APPROUTE_SERVICE_ROBUX_GL"),
            "RU": _service("APPROUTE_SERVICE_ROBUX_RU"),
        },
    ),
    Card(
        slug="apple",
        title="Apple",
        emoji="🍎",
        keywords=("apple", "эпл", "эппл", "айтюнс", "itunes", "app store"),
        measure="$",
        subcategory="Apple Gift Cards",
        activation="Активируйте код в App Store: «Аккаунт» → «Погасить "
                   "подарочную карту».",
        pitch="Пополняет Apple ID: App Store, iCloud, подписки. Логин "
              "покупателя не нужен — код он вводит сам.",
        measured="20.08",
        ad_title="Apple Gift Card {номинал} {регион} — код",
        ad_text="Код Apple Gift Card {номинал}. Регион: {регион}.\n"
                "Активация: App Store → Аккаунт → Погасить подарочную "
                "карту.\nВыдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="psn",
        title="PlayStation Store",
        emoji="🎯",
        keywords=("psn", "playstation", "плейстейшн", "плейстешн", "пс стор",
                  "ps store"),
        measure="$",
        subcategory="PlayStation Gift Cards",
        activation="Активируйте код в PlayStation Store: «Активировать "
                   "код».",
        pitch="Пополняет кошелёк PlayStation Store. Регион кода и регион "
              "аккаунта должны совпадать.",
        measured="20.08",
        ad_title="PSN {номинал} {регион} — код",
        ad_text="Код PlayStation Store {номинал}. Регион: {регион}.\n"
                "Активация: PS Store → Активировать код.\n"
                "Регион кода и аккаунта должны совпадать.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="xbox",
        title="Xbox",
        emoji="🟩",
        keywords=("xbox", "иксбокс", "хбокс", "икс бокс"),
        measure="$",
        subcategory="Xbox Gift Cards",
        # Подписки Game Pass живут в той же выдаче поставщика, но это
        # другой товар: на них у карты нет ни номинала, ни активации.
        # «Xbox Game Pass Ultimate 1 месяц» узнаётся по слову «xbox», а
        # номинал из названия — 1: бот купил бы гифт-карту на доллар.
        # Написаний несколько: у поставщика английское, у продавца своё.
        name_must_not_have=("game pass", "gamepass", "game-pass",
                            "гейм пасс", "геймпасс", "гейм-пасс",
                            "гейпасс"),
        activation="Активируйте код на xbox.com/redeem.",
        pitch="Пополняет кошелёк Microsoft: игры, Xbox и Windows Store. "
              "Не Game Pass — это подписка, отдельный товар.",
        measured="20.08",
        ad_title="Xbox Gift Card {номинал} {регион} — код",
        ad_text="Код Xbox {номинал}. Регион: {регион}.\n"
                "Активация: xbox.com/redeem.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="steam",
        title="Steam",
        emoji="💨",
        keywords=("steam", "стим"),
        measure="$",
        subcategory="Steam Wallet Gift Cards",
        activation="Активируйте код в Steam: «Игры» → «Активировать через "
                   "Steam».",
        pitch="Пополняет кошелёк Steam. Валюта кода и валюта аккаунта "
              "должны совпадать, иначе Steam код не примет.",
        measured="20.08",
        ad_title="Steam {номинал} {регион} — код пополнения",
        ad_text="Код пополнения Steam на {номинал}. Регион: {регион}.\n"
                "Активация: Steam → Игры → Активировать через Steam.\n"
                "Валюта кода и кошелька должны совпадать.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="amazon",
        title="Amazon",
        emoji="📦",
        keywords=("amazon", "амазон"),
        measure="$",
        subcategory="Amazon Gift Cards",
        activation="Активируйте код на amazon.com/redeem.",
        pitch="Пополняет баланс Amazon. Код действует только в магазине "
              "своей страны.",
        measured="20.08",
        ad_title="Amazon Gift Card {номинал} {регион} — код",
        ad_text="Код Amazon {номинал}. Регион: {регион}.\n"
                "Активация: amazon.com/redeem (магазин своей страны).\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="razer",
        title="Razer Gold",
        emoji="🐍",
        keywords=("razer", "рейзер", "разер", "razer gold"),
        measure="$",
        subcategory="Razer Gold Gift Cards",
        activation="Активируйте код на gold.razer.com.",
        pitch="Внутренняя валюта Razer Gold: ею платят в сотнях игр, где "
              "нет прямого пополнения.",
        measured="20.08",
        ad_title="Razer Gold {номинал} — код",
        ad_text="Код Razer Gold на {номинал}. Регион: {регион}.\n"
                "Активация: gold.razer.com.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="airbnb",
        title="Airbnb",
        emoji="🏠",
        keywords=("airbnb", "эйрбиэнби", "эирбнб"),
        measure="$",
        subcategory="Airbnb Gift Card",
        activation="Активируйте код на airbnb.com/gift.",
        pitch="Оплачивает жильё на Airbnb. Код привязывается к аккаунту и "
              "тратится при бронировании.",
        ad_title="Airbnb Gift Card {номинал} — код",
        ad_text="Код Airbnb на {номинал}. Регион: {регион}.\n"
                "Активация: airbnb.com/gift.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="eneba",
        title="Eneba",
        emoji="🛒",
        keywords=("eneba", "энеба"),
        measure="$",
        subcategory="Eneba Gift Card",
        activation="Активируйте код в кошельке Eneba.",
        pitch="Пополняет кошелёк Eneba — магазина ключей и игровой валюты.",
        ad_title="Eneba {номинал} — код пополнения",
        ad_text="Код пополнения Eneba на {номинал}. Регион: {регион}.\n"
                "Активация: кошелёк Eneba.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="twitch",
        title="Twitch",
        emoji="💜",
        keywords=("twitch", "твич"),
        measure="$",
        subcategory="Twitch Gift Card",
        activation="Активируйте код на twitch.tv/redeem.",
        pitch="Оплачивает подписки на стримеров и биты. Код зачисляется на "
              "аккаунт Twitch.",
        ad_title="Twitch {номинал} — код",
        ad_text="Код Twitch на {номинал}. Регион: {регион}.\n"
                "Активация: twitch.tv/redeem.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="meta_quest",
        title="Meta Quest",
        emoji="🥽",
        keywords=("meta quest", "мета квест", "oculus", "окулус", "квест"),
        measure="$",
        subcategory="Meta Quest Gift Card",
        activation="Активируйте код в магазине Meta Quest.",
        pitch="Оплачивает игры и приложения для шлемов Meta Quest.",
        ad_title="Meta Quest {номинал} — код",
        ad_text="Код Meta Quest на {номинал}. Регион: {регион}.\n"
                "Активация: магазин Meta Quest.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="battlenet",
        title="Battle.net",
        emoji="⚔️",
        keywords=("battle.net", "battlenet", "баттлнет", "батлнет",
                  "blizzard", "близзард"),
        measure="$",
        subcategory="Battle.net Gift Card",
        activation="Активируйте код на battle.net в разделе «Пополнить "
                   "баланс».",
        pitch="Пополняет баланс Battle.net: игры Blizzard, внутриигровая "
              "валюта, подписки.",
        ad_title="Battle.net {номинал} — код пополнения",
        ad_text="Код Battle.net на {номинал}. Регион: {регион}.\n"
                "Активация: battle.net → Пополнить баланс.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
    Card(
        slug="ozon",
        title="OZON.ru",
        emoji="🔵",
        keywords=("ozon", "озон"),
        measure="₽",
        subcategory="OZON.ru Gift Card",
        activation="Активируйте код в личном кабинете OZON: «Сертификаты».",
        pitch="Подарочный сертификат OZON. Тратится на товары маркетплейса, "
              "на баланс не выводится.",
        ad_title="Сертификат OZON {номинал} — код",
        ad_text="Подарочный сертификат OZON на {номинал}.\n"
                "Активация: личный кабинет OZON → Сертификаты.\n"
                "Выдача автоматическая, сразу после оплаты.",
    ),
]

# Обращение по slug — экранам и движку оно нужно чаще, чем перебор.
BY_SLUG = {card.slug: card for card in CARDS}


def card_by_slug(slug: str) -> Card | None:
    return BY_SLUG.get(str(slug or ""))
