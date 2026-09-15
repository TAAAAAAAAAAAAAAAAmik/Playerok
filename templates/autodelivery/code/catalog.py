"""Что и на сколько покупать: номинал из названия товара, регион из описания.

Тут живут две задачи, обе — чистые функции без сети. Именно поэтому они
покрываются тестами напрямую, и именно поэтому ошибка в них ловится до
того, как спишутся деньги.

Главное правило раздела: **не угадывать**. Не нашли номинал — отказ с
причиной, а не «возьмём похожий». Купленный не тот номинал — это деньги
продавца и недовольный покупатель, а не мелкая неточность.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# Три вида номинала. От вида зависит, как номинал показывается покупателю и
# как читается из названия товара.
MONEY = "money"      # номинал — деньги: «10$»
UNITS = "units"      # номинал — штуки: «1000 робуксов»
PERIOD = "period"    # номинал — срок подписки


@dataclass
class Card:
    """Вид товара, который умеем выдавать."""
    slug: str                       # "robux", "steam"
    title: str                      # "Roblox Gift Cards"
    emoji: str = "🎁"
    # По каким словам узнаём заказ этого вида в названии товара.
    keywords: tuple[str, ...] = ()
    # Как называется единица номинала: "робуксов", "₽", "$".
    measure: str = ""
    unit: str = MONEY               # MONEY / UNITS / PERIOD
    # Что написать покупателю вместе с кодом.
    activation: str = "Активируйте код на официальном сайте."
    # Услуги поставщика, где искать номиналы: {регион: service_id}.
    # Ручная привязка — на случай, когда отбор по подкатегории не подходит.
    services: dict[str, str] = field(default_factory=dict)

    # ТОЧНОЕ имя подкатегории у поставщика. Главное поле отбора: см.
    # `matches_service` — по слову брать нельзя.
    subcategory: str = ""
    # Уточнение словом, когда одна подкатегория кормит два вида товара
    # (у Nintendo это карты и подписки). Одна строка или несколько: у
    # поставщика название английское, а продавец пишет своё по-русски, и
    # одного написания на оба случая не хватает.
    name_must_have: str | tuple[str, ...] = ""
    name_must_not_have: str | tuple[str, ...] = ()

    # Одна строка про товар для экрана настроек — про товар, а не про
    # доходы: «покупают чаще всего» проверить нельзя, а «код пополняет
    # Apple ID» покупатель проверит сам.
    pitch: str = ""
    # Дата, когда разбор номиналов этого семейства проверяли на живом
    # каталоге. Пусто — значит не проверяли, и продавцу это видно.
    measured: str = ""

    # Заготовки для мастера создания товара.
    ad_title: str = ""
    ad_text: str = ""


# ---------------------------------------------------------------------------
# Узнавание заказа
# ---------------------------------------------------------------------------

def is_card_order(card: Card, title: str, keyword: str = "") -> bool:
    """Наш ли это заказ.

    Своё слово продавца (`keyword`) означает «только оно»: он задал его,
    чтобы отделить свои товары от чужих, и подмешивать к нему наши догадки
    значит отменять его решение.

    А вот `name_must_not_have` сильнее и слова продавца, потому что оно про
    другое: это слово означает «здесь другой товар». «Xbox Game Pass
    Ultimate 1 месяц» узнаётся по слову «xbox», номинал из названия — 1, и
    бот купил бы гифт-карту на доллар вместо подписки. Деньги продавца
    списаны, покупатель без подписки.
    """
    text = " ".join(str(title or "").lower().split())

    if any(w in text for w in _spellings(card.name_must_not_have)):
        return False

    if keyword.strip():
        return keyword.strip().lower() in text

    return any(k.lower() in text for k in card.keywords)


def card_for_title(cards: list[Card], title: str) -> Card | None:
    """Какая карта узнаёт это название. Без оглядки на настройки.

    Отличие от `pick_card` в том, что выдача сюда не смотрит: это подсказка
    продавцу, пока он создаёт товар, а не решение потратить его деньги.
    Поэтому выключенные карты тоже считаются — иначе заготовка описания
    появлялась бы только после включения выдачи, то есть позже, чем нужна.
    """
    for card in cards:
        if is_card_order(card, title):
            return card

    return None


def render(template: str, card: Card | None = None, nominal="",
           region: str = "", price="") -> str:
    """Подставить значения в заготовку названия или описания.

    Незаполненное не оставляем в фигурных скобках: «Apple {регион}» на
    витрине выглядит поломкой магазина, а не пропуском настройки.
    """
    values = {
        "{номинал}": _shown_nominal(card, nominal),
        "{регион}": str(region or ""),
        "{цена}": "" if price in (None, "") else f"{price} ₽",
        "{карта}": card.title if card else "",
    }
    text = str(template or "")

    for key, value in values.items():
        text = text.replace(key, value)

    # Пустая подстановка оставляет после себя двойные пробелы и висящие
    # разделители — их убираем, иначе заготовка выглядит небрежной.
    return re.sub(r"[ \t]{2,}", " ", text).strip(" -—·,").strip()


def _shown_nominal(card: Card | None, nominal) -> str:
    """Номинал так, как его читает покупатель: «10$», «1000 Robux»."""
    if nominal in (None, ""):
        return ""

    value = f"{float(nominal):g}" if isinstance(nominal, (int, float)) \
        else str(nominal)
    measure = card.measure if card else ""

    if not measure:
        return value

    # Валютный знак пишется вплотную, название единицы — через пробел:
    # «10$», но «1000 Robux».
    return f"{value}{measure}" if len(measure) == 1 else f"{value} {measure}"


def pick_card(cards: list[Card], title: str, conf_of) -> Card | None:
    """Кому достанется заказ. ПЕРВАЯ признавшая забирает.

    Название вида «Apple или Xbox» признали бы обе, и без остановки на
    первой бот купил бы два кода на один оплаченный заказ.
    """
    for card in cards:
        conf = conf_of(card.slug) or {}
        if not conf.get("enabled"):
            continue
        if is_card_order(card, title, str(conf.get("keyword") or "")):
            return card
    return None


# ---------------------------------------------------------------------------
# Номинал из названия
# ---------------------------------------------------------------------------

_NUM = re.compile(r"(\d[\d\s  ]*(?:[.,]\d+)?)")


def nominal_from_title(title: str) -> float | None:
    """Число из названия товара: «Roblox 1000 Robux» → 1000.

    Берётся САМОЕ КРУПНОЕ число, а не первое. В названиях попадаются
    «Roblox Gift Card 10 USD (1000 Robux)» и «Steam 500 ₽ — скидка 5%»:
    первое число там бывает и годом, и процентом, и версией.
    """
    best: float | None = None
    for raw in _NUM.findall(str(title or "")):
        clean = raw.replace(" ", "").replace(" ", "").replace(
            " ", "").replace(",", ".")
        try:
            val = float(clean)
        except ValueError:
            continue
        if best is None or val > best:
            best = val
    return best


# ---------------------------------------------------------------------------
# Регион из описания
# ---------------------------------------------------------------------------

_REGION = re.compile(
    r"(?:регион|region)[^\wа-яё]{0,4}(?:кода|code)?[^\wа-яё]{0,4}"
    r"([A-Za-zА-Яа-яЁё]{2,12})", re.I)

# Глобальный и российский коды невзаимозаменяемы: выдать не тот регион —
# это возврат, а не мелочь.
_ALIASES = {
    "GLOBAL": "GL", "ГЛОБАЛ": "GL", "ГЛОБАЛЬНЫЙ": "GL", "GL": "GL",
    "РОССИЯ": "RU", "РФ": "RU", "RU": "RU", "RUS": "RU",
    "США": "US", "US": "US", "USA": "US",
    "ТУРЦИЯ": "TR", "TR": "TR", "ЕВРОПА": "EU", "EU": "EU",
}


def region_from_description(text: str) -> str:
    """Регион кода из описания товара: «Регион кода: US» → "US".

    Читается ИЗ ОПИСАНИЯ, а не из настроек плагина, потому что у продавца
    товаров много и регионы у них разные. Настройка остаётся запасным
    вариантом — для товаров, заведённых до того, как регион стали писать.
    """
    m = _REGION.search(str(text or ""))
    if not m:
        return ""
    word = m.group(1).strip().upper()
    return _ALIASES.get(word, word if word.isascii() and len(word) <= 3 else "")


# ---------------------------------------------------------------------------
# Подбор номинала в каталоге поставщика
# ---------------------------------------------------------------------------

@dataclass
class Denomination:
    """Номинал у поставщика."""
    service_id: str
    item_id: str            # он же denominationId при покупке
    value: float            # 1000
    title: str              # "1000 Robux (Global)"
    price: float | None = None
    in_stock: int = 0
    region: str = ""


def match_denomination(rows: list[Denomination], region: str,
                       want: float | None) -> tuple[Denomination | None, str]:
    """Найти ТОЧНЫЙ номинал → (номинал, причина отказа).

    Три правила, каждое против потери денег:

    * **регион сначала.** Правильный номинал чужого региона хуже, чем
      отказ: покупатель не активирует код и откроет спор;
    * **точное совпадение.** «Ближайший» номинал — это либо недодать, либо
      переплатить за продавца. Ни то ни другое он не просил;
    * **остаток проверяется здесь и ещё раз перед покупкой.** Каталог
      кешируется, и «есть в наличии» в нём может быть вчерашним.
    """
    if want is None:
        return None, ("в названии товара нет числа — непонятно, какой "
                      "номинал покупать")
    same_region = [r for r in rows if not region or not r.region
                   or r.region.upper() == region.upper()]
    if not same_region:
        return None, f"у поставщика нет номиналов региона {region}"

    exact = [r for r in same_region if abs(r.value - want) < 1e-9]
    if not exact:
        near = ", ".join(str(int(r.value)) for r in sorted(
            same_region, key=lambda r: r.value)[:8])
        return None, (f"номинала {want:g} у поставщика нет. Есть: {near}. "
                      f"Подбирать похожий бот не станет — это чужие деньги")

    live = [r for r in exact if r.in_stock > 0]
    if not live:
        return None, f"номинал {want:g} есть в каталоге, но его нет в наличии"

    # Дешевле — лучше: номинал один и тот же, разница только в закупке.
    live.sort(key=lambda r: (r.price if r.price is not None else 1e9))
    return live[0], ""


def order_reference(prefix: str, card_slug: str, order_id: str) -> str:
    """Ссылка покупки — вся защита от двойного списания.

    Считается ОДИН раз и хранится в записи журнала. Пересчитывать её при
    повторе нельзя: изменится хоть один символ — поставщик не узнает заказ,
    ответит не `IDEMPOTENCY_REPLAY`, а новой покупкой, и деньги спишутся
    второй раз.

    Префикс отделяет площадки друг от друга: ссылка уникальна в пределах
    кабинета поставщика, а кабинет один на все площадки продавца.
    """
    return f"{prefix}-{card_slug}-{order_id}"[:40]


# ---------------------------------------------------------------------------
# Каталог поставщика → номиналы
# ---------------------------------------------------------------------------

# Где у услуги может лежать список номиналов. Имена разные у разных
# поставщиков и меняются между версиями, а ошибиться здесь значит получить
# «номинала нет» на полном каталоге.
ITEM_FIELDS = ("items", "denominations", "nominals", "values")

# Где лежит число, которое мы считаем номиналом. Если ни одного нет —
# читаем его из названия, как делаем с товарами площадки.
VALUE_FIELDS = ("value", "denomination", "nominal", "amount", "faceValue")

NAME_FIELDS = ("name", "title", "label", "denominationName")
STOCK_FIELDS = ("inStock", "stock", "quantity", "available", "count")
PRICE_FIELDS = ("price", "cost", "amountUsd", "priceUsd")


def _first(node: dict, fields, default=None):
    for field in fields:
        if field in node and node[field] not in (None, ""):
            return node[field]

    return default


def _number(value):
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def items_of(service: dict) -> list:
    """Номиналы услуги, как бы поставщик их ни назвал."""
    if not isinstance(service, dict):
        return []

    found = _first(service, ITEM_FIELDS)

    return [row for row in (found or []) if isinstance(row, dict)]


def denominations_from(service: dict, service_id: str = "",
                       region: str = "") -> list:
    """Услуга поставщика → номиналы в терминах движка.

    Номинал берётся из числового поля, а если его нет — из названия. Это
    не роскошь: у части услуг число живёт только в названии вроде
    «Roblox 1000 Robux (Global)», и без разбора названия такой номинал не
    найдётся никогда.

    Записи без номинала пропускаются молча: подставить догадку значило бы
    купить не то на настоящие деньги.
    """
    service_id = str(service_id or _first(service, ("id", "serviceId"), ""))
    rows = []

    for item in items_of(service):
        item_id = str(_first(item, ("id", "itemId", "denominationId"), ""))

        if not item_id:
            continue

        title = str(_first(item, NAME_FIELDS, "") or "")
        value = _number(_first(item, VALUE_FIELDS))

        if value is None:
            value = nominal_from_title(title)

        if value is None:
            continue

        stock = _number(_first(item, STOCK_FIELDS, 0)) or 0

        rows.append(Denomination(
            service_id=service_id,
            item_id=item_id,
            value=value,
            title=title or f"{value:g}",
            price=_number(_first(item, PRICE_FIELDS)),
            # Отрицательный остаток встречается: считаем его нулём, иначе
            # такой номинал выглядел бы доступным.
            in_stock=max(0, int(stock)),
            region=region.upper(),
        ))

    return rows


# Где поставщик держит имя подкатегории. Имя поля у разных версий API
# разное, а промах здесь означает «подкатегория не совпала ни разу», то
# есть карта не найдёт ни одной услуги и молча ничего не выдаст.
SUBCATEGORY_FIELDS = ("subcategoryName", "subCategoryName", "subcategory",
                      "subCategory", "categoryName")

# Регионы, которые узнаём в названии услуги. Список закрытый нарочно:
# «любые две заглавные буквы» поймали бы и «PS», и «GB» в «10 GB», и товар
# уехал бы в чужой регион. Лучше не узнать регион, чем узнать неверный.
REGION_CODES = frozenset((
    "US", "RU", "EU", "GB", "UK", "TR", "AE", "SA", "KW", "QA", "IN", "BR",
    "CA", "AU", "NZ", "JP", "KR", "CN", "HK", "TW", "SG", "MY", "TH", "ID",
    "PH", "VN", "MX", "AR", "CL", "CO", "PE", "DE", "FR", "IT", "ES", "PT",
    "NL", "BE", "AT", "CH", "SE", "NO", "DK", "FI", "IE", "PL", "CZ", "HU",
    "RO", "GR", "IL", "ZA", "NG", "EG", "UA", "KZ", "BY", "AM", "GE", "AZ",
))

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{2,12}")


def _spellings(value) -> tuple:
    """Написания уточняющего слова — одно или несколько."""
    if not value:
        return ()

    if isinstance(value, str):
        return (value.lower(),)

    return tuple(str(v).lower() for v in value if str(v).strip())


def matches_service(card: Card, service: dict) -> bool:
    """Наша ли это услуга поставщика.

    Отбор идёт по ТОЧНОМУ имени подкатегории, а не по слову в названии, и
    это не придирка. На живом каталоге слово «xbox» находит 47 услуг, а
    гифт-карт среди них 16: остальное — подписки Game Pass, ключи игр,
    аккаунты и `Roblox Wallet Code | XBox`, то есть товар другой карты.
    Подбор по слову увёл бы его, и покупатель получил бы код Roblox вместо
    карты Xbox — на свои деньги.
    """
    if not isinstance(service, dict) or not card.subcategory:
        return False

    if str(_first(service, SUBCATEGORY_FIELDS, "") or "") != card.subcategory:
        return False

    low = str(_first(service, NAME_FIELDS, "") or "").lower()

    wanted = _spellings(card.name_must_have)

    if wanted and not any(w in low for w in wanted):
        return False

    if any(w in low for w in _spellings(card.name_must_not_have)):
        return False

    return True


def region_of_service(service: dict) -> str:
    """Регион из названия услуги: «Apple Gift Cards US» → "US".

    Не нашли — пусто, и это нормально: номинал без региона подойдёт любому
    региону, а выдуманный регион отсёк бы верный номинал.
    """
    name = str(_first(service, NAME_FIELDS, "") or "")

    for word in _WORD.findall(name):
        code = word.upper()

        if code in _ALIASES:
            return _ALIASES[code]

        if code in REGION_CODES:
            return "GB" if code == "UK" else code

    return ""


def services_for(card: Card, catalog) -> list:
    """Услуги поставщика, принадлежащие карте."""
    return [s for s in _services(catalog) if matches_service(card, s)]


def denominations_for(card: Card, catalog, region: str = "") -> list:
    """Номиналы карты во всём каталоге поставщика.

    Заменяет ручную привязку «карта → номер услуги»: номера у поставщика
    свои на каждый регион, их десятки, и переписывать их руками с телефона
    продавец не должен. Подкатегория же одна и меняется редко.

    Регион берётся из названия услуги. Когда его там нет, номиналы
    остаются без региона — и подойдут любому: отбор по региону делает
    `match_denomination`, и пустой регион он считает подходящим.
    """
    rows = []

    for service in services_for(card, catalog):
        service_id = str(_first(service, ("id", "serviceId"), "") or "")

        if not service_id:
            continue

        rows.extend(denominations_from(service, service_id,
                                       region_of_service(service)))

    if region:
        want = region.upper()
        rows = [r for r in rows if not r.region or r.region == want]

    return rows


def find_service(catalog, service_id: str):
    """Услуга по её номеру в каталоге поставщика, или None."""
    wanted = str(service_id or "").strip()

    if not wanted:
        return None

    for service in _services(catalog):
        if str(_first(service, ("id", "serviceId"), "")) == wanted:
            return service

    return None


def _services(catalog) -> list:
    """Список услуг, как бы поставщик его ни завернул."""
    if isinstance(catalog, list):
        return [s for s in catalog if isinstance(s, dict)]

    if not isinstance(catalog, dict):
        return []

    for key in ("services", "items", "data", "results"):
        found = catalog.get(key)

        if isinstance(found, list):
            return [s for s in found if isinstance(s, dict)]

        if isinstance(found, dict):
            deeper = _services(found)

            if deeper:
                return deeper

    page = catalog.get("page")

    return _services(page) if isinstance(page, dict) else []
