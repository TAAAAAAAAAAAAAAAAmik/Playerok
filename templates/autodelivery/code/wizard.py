"""Диалог создания товара: какие вопросы и что считать ответом.

Здесь только правила, без телеграма и без площадки — чтобы их можно было
проверить тестами, а не живыми объявлениями.

Порядок вопросов не случаен. Сначала дёшево исправимое (название, цена),
потом фотографии: если продавец передумает на первом шаге, он не потратит
время на отправку картинок.
"""
from __future__ import annotations

import re

from catalog import (REGION_ALIASES, REGION_CODES,
                     nominal_from_title, normalize_region,
                     shown_number)

# Сколько шагов и в каком порядке.
# Выбор игры и категории идёт первым: от категории зависит и способ
# получения, и какие поля площадка потребует заполнить. Спрашивать их
# после названия значит однажды выбросить уже написанное.
STEPS = ("game", "category", "obtaining", "options", "name", "price",
         "nominal", "region", "description", "photos")

# Приставка у шага, который спрашивает поле с данными. Полей у разных
# категорий разное число, поэтому шаг не постоянный, а собирается из id.
FIELD = "field:"

# Приставка у шага, который спрашивает характеристику товара. Площадка
# называет их атрибутами и часть требует обязательно — с пустыми она
# отвечает «заполните все обязательные характеристики».
OPTION = "option:"

QUESTIONS = {
    "game": ("Для какой игры или приложения товар?\n\n"
             "Напишите название или его часть — покажу, что нашлось."),
    "category": "Какая категория?",
    # Название характеристики приходит с площадки; этот текст — запасной,
    # на случай если она не назовёт группу.
    "options": "Характеристика товара.",
    "obtaining": ("Как покупатель получает товар?\n\n"
                  "Для кодов это «без входа в аккаунт»: вы отдаёте код, а "
                  "в чужой аккаунт не заходите."),
    "name": ("Название товара.\n\n"
             "Так его увидит покупатель в списке. Номинал лучше писать "
             "числом — из него бот потом поймёт, сколько покупать."),
    "price": ("Цена в рублях. Только число.\n\n"
              "Это то, что заплатит покупатель."),
    "nominal": ("Сколько покупатель получит? Только число.\n\n"
                "Для робуксов — их количество, для гифт-карты — номинал.\n\n"
                "В названии товара числа не нашлось, поэтому спрашиваю: по "
                "нему бот выберет, что покупать у поставщика. Впишу его в "
                "описание строкой «Номинал: …», и дальше буду читать "
                "оттуда."),
    "region": ("Регион кода.\n\n"
               "GL — глобальный, RU — российский. Остальные кнопками или "
               "текстом: US, TR, SA, HK, SG, MX — любой из тех, что "
               "бывают у гифт-карт.\n\n"
               "По нему бот выберет, что покупать у поставщика, так что "
               "ошибка здесь означает покупку не того: код чужого региона "
               "покупатель не активирует."),
    "description": ("Описание товара.\n\n"
                    "Что покупатель прочитает на странице: что он получит, "
                    "как быстро, как активировать.\n\n"
                    "Строки про регион и номинал дописывать не нужно — я "
                    "поставлю их сам, и по ним бот потом выберет, что "
                    "покупать. Если напишете свои, я их уберу, чтобы они не "
                    "спорили."),
    "photos": ("Пришлите фотографии товара. Можно несколько — по одной.\n\n"
               "Когда хватит, напишите «готово».\n"
               "Хотя бы одна обязательна: без картинок площадка товар не "
               "принимает."),
}

DONE_WORD = "готово"
CANCEL_WORDS = ("отмена", "стоп", "хватит")
SKIP_WORDS = ("пропустить", "пропуск", "-", "нет")

# Площадка режет длинные названия, и лучше сказать об этом сразу, чем
# получить обрезанное объявление.
NAME_LIMIT = 120

# Описание длиннее площадка тоже не примет целиком.
DESCRIPTION_LIMIT = 2000

FIELD_LIMIT = 500

# Текст, которым описание заполняется, если продавец его пропустил.
DEFAULT_TAIL = ("Код приходит в чат сразу после оплаты.\n"
                "Активация: roblox.com/redeem")

# Своя строка про регион в описании продавца: её надо убрать, иначе в
# описании окажутся две, и движок прочитает не ту. Ошибка тихая и
# денежная — купится не тот товар.
REGION_LINE = re.compile(r"^\s*(?:регион|region)\b.*$",
                         re.IGNORECASE | re.MULTILINE)

# То же и для номинала: две строки в одном описании — тихая денежная
# ошибка, движок прочитает не ту и купит не то.
NOMINAL_LINE = re.compile(
    r"^\s*(?:номинал|количество|кол-во|сумма|nominal|amount)\b.*$",
    re.IGNORECASE | re.MULTILINE)

# Регионы, которые бот принимает от продавца. Ровно те же, которые он
# умеет узнавать в названии услуги поставщика, — иначе выходит нелепость:
# номиналы в регионе SA у поставщика есть, бот их видит, а пометить ими
# товар продавец не может.
#
# Так и было: мастер принимал шестнадцать регионов из шестидесяти, и
# ходовые для гифт-карт — SA, HK, SG, MX, KW, QA — оставались недоступны.
#
# «UK» сюда не попадает намеренно: разбор приводит его к «GB», и держать
# оба значило бы иметь один регион под двумя именами.
REGIONS = tuple(sorted((set(REGION_ALIASES.values())
                        | set(REGION_CODES)) - {"UK"}))

# Что предлагаем кнопками. Остальные регионы вводятся текстом — кнопок на
# два десятка регионов на экране телефона не помещается.
COMMON_REGIONS = ("GL", "RU", "US", "TR", "EU", "AR", "BR")


class Draft:
    """Что уже собрано. Обычная копилка ответов."""

    def __init__(self):
        # Выбранное на площадке: {"id": ..., "name": ...} или None.
        self.game = None
        self.category = None
        self.obtaining = None
        # Характеристики категории: что предлагает площадка и что выбрано.
        self.options: list = []
        # Поля с данными выбранной категории: что спросить и что ответили.
        self.fields: list = []
        self.name = ""
        self.price = 0
        self.region = ""
        # Сколько покупать. Обычно берётся из названия молча — спрашивать
        # то, что уже знаешь, значит лишнее нажатие на каждом товаре. Ноль
        # означает «в названии числа не нашлось», и тогда спросим.
        self.nominal = 0.0
        # None — ещё не спрашивали, "" — спросили и пропустили. Разница
        # важна: иначе пропуск означал бы вечный повтор вопроса.
        self.description = None
        self.photos: list = []
        # Описание по умолчанию — от узнанной карты: у Apple своя
        # активация, у Steam своя. Одно на всех давало бы объявлению Apple
        # строку «Активация: roblox.com/redeem».
        self.tail = ""

    @property
    def step(self) -> str:
        """Какой шаг сейчас. Пусто — значит всё собрано."""
        if not self.game:
            return "game"

        if not self.category:
            return "category"

        if not self.obtaining:
            return "obtaining"

        # Характеристики спрашиваем сразу после категории: их состав от неё
        # и зависит, а каждая — это одно нажатие.
        for option in self.options:
            if option.get("value") is None:
                return OPTION + str(option.get("field"))

        if not self.name:
            return "name"

        if not self.price:
            return "price"

        if not self.nominal:
            return "nominal"

        if not self.region:
            return "region"

        if self.description is None:
            return "description"

        # Поля площадки: их состав известен только после выбора способа
        # получения, поэтому шаги на них появляются по ходу.
        for field in self.fields:
            if field.get("value") is None:
                return FIELD + str(field.get("id"))

        if not self.photos:
            return "photos"

        return ""

    def option(self, field: str):
        """Характеристика по имени поля, или None."""
        for option in self.options:
            if str(option.get("field")) == str(field):
                return option

        return None

    def attributes(self) -> dict:
        """Характеристики так, как их ждёт площадка: поле → значение."""
        return {str(o["field"]): o["value"] for o in self.options
                if o.get("value") not in (None, "")}

    def field(self, field_id: str):
        """Описание поля по его id, или None."""
        for field in self.fields:
            if str(field.get("id")) == str(field_id):
                return field

        return None

    def filled_fields(self) -> list:
        """Поля, которые есть что отправлять."""
        return [f for f in self.fields if f.get("value")]

    def summary(self) -> str:
        lines = [f"Игра: {(self.game or {}).get('name', '')}",
                 f"Категория: {(self.category or {}).get('name', '')}",
                 f"Получение: {(self.obtaining or {}).get('name', '')}"]

        for option in self.options:
            if option.get("value") not in (None, ""):
                lines.append(f"{option.get('group') or 'Характеристика'}: "
                             f"{option.get('chosen') or option['value']}")

        lines += [f"Название: {self.name}",
                  f"Цена: {self.price} ₽"]

        # Номинал показываем всегда, даже когда его нет: по нему бот
        # покупает у поставщика, и пустое место здесь продавец должен
        # заметить до того, как товар уйдёт на витрину.
        lines.append(f"Номинал: {shown_number(self.nominal)}"
                     if self.nominal
                     else "Номинал: — не понял, автовыдача работать не будет")
        lines.append(f"Регион: {self.region}")

        for field in self.filled_fields():
            lines.append(f"{field.get('label') or 'Поле'}: {field['value']}")

        lines.append(f"Фотографий: {len(self.photos)}")

        return "\n".join(lines)


def cancelled(text: str) -> bool:
    return str(text or "").strip().lower() in CANCEL_WORDS


def skipped(text: str) -> bool:
    return str(text or "").strip().lower() in SKIP_WORDS


def enough_photos(text: str) -> bool:
    return str(text or "").strip().lower() == DONE_WORD


def accept_name(text: str) -> tuple[str, str]:
    """→ (название, причина отказа). Пустая причина — принято."""
    name = " ".join(str(text or "").split())

    if not name:
        return "", "Название пустое. Напишите, как назвать товар."

    if len(name) > NAME_LIMIT:
        return "", (f"Слишком длинно: {len(name)} знаков при "
                    f"{NAME_LIMIT} допустимых. Площадка обрежет — "
                    f"лучше сократить самим.")

    return name, ""


def accept_price(text: str) -> tuple[int, str]:
    """→ (цена, причина отказа).

    Дробную цену не принимаем молчаливым округлением: продавец должен
    увидеть ту цену, которую назначил.
    """
    raw = " ".join(str(text or "").split()).replace(",", ".")

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0, "Это не число. Напишите цену цифрами, например 149."

    if value != int(value):
        return 0, "Цена должна быть целой, без копеек."

    value = int(value)

    if value <= 0:
        return 0, "Цена должна быть больше нуля."

    return value, ""


def accept_nominal(text: str) -> tuple[float, str]:
    """→ (номинал, причина отказа)."""
    clean = " ".join(str(text or "").strip().split())
    clean = clean.replace("\u00a0", "").replace(" ", "").replace(",", ".")

    try:
        value = float(clean)
    except ValueError:
        return 0.0, ("Не понял. Напишите только число — сколько покупатель "
                     "получит: 1000, 800, 10.")

    if value <= 0:
        return 0.0, "Номинал должен быть больше нуля."

    return value, ""


def accept_number(text: str, allow_zero: bool = False) -> tuple[float, str]:
    """Просто число: курс, наценка. → (число, причина отказа).

    Дробное принимаем: курс редко бывает целым. А вот пустое и словами —
    нет: из «примерно сотня» цены не посчитать, а молча взять ноль значит
    выставить товар по закупке.
    """
    clean = " ".join(str(text or "").strip().split())
    clean = clean.replace("\u00a0", "").replace(" ", "").replace(
        ",", ".").replace("%", "")

    try:
        value = float(clean)
    except ValueError:
        return 0.0, f"Не понял «{str(text).strip()}». Напишите число."

    if value < 0:
        return 0.0, "Отрицательное не подойдёт."

    if value == 0 and not allow_zero:
        return 0.0, "Ноль не подойдёт."

    return value, ""


def accept_region(text: str) -> tuple[str, str]:
    """→ (регион, причина отказа).

    Понимаем и код, и слово: продавец пишет «Россия» так же часто, как
    «RU», а отказ на понятном ответе — это экран, спорящий с человеком.
    """
    region = normalize_region(text)

    if region in REGIONS:
        return region, ""

    return "", (f"Не понял «{str(text).strip()}». Напишите код региона: "
                f"{', '.join(COMMON_REGIONS)} — или другой, если у "
                f"поставщика он есть.")


def accept_description(text: str) -> tuple[str, str]:
    """→ (описание, причина отказа). Пропуск даёт текст по умолчанию.

    Строки про регион из текста продавца вырезаются: свою мы поставим
    первой, а две разные строки в одном описании — это тихая денежная
    ошибка, движок прочитает не ту и купит не тот товар.
    """
    if skipped(text):
        return "", ""

    body = REGION_LINE.sub("", str(text or ""))
    body = NOMINAL_LINE.sub("", body).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)

    if not body:
        return "", ""

    if len(body) > DESCRIPTION_LIMIT:
        return "", (f"Слишком длинно: {len(body)} знаков при "
                    f"{DESCRIPTION_LIMIT} допустимых.")

    return body, ""


ACCEPT = {"name": accept_name, "price": accept_price, "region": accept_region,
          "nominal": accept_nominal, "description": accept_description}


def progress(draft: Draft) -> str:
    """Что уже собрано — коротко, для экрана диалога.

    Диалог живёт в одном переписываемом сообщении, и без этого списка
    введённое исчезало бы с каждым новым вопросом: продавец не видел бы ни
    что уже ответил, ни где ошибся.
    """
    lines = []

    for label, value in (
            ("Игра", (draft.game or {}).get("name")),
            ("Категория", (draft.category or {}).get("name")),
            ("Получение", (draft.obtaining or {}).get("name"))):
        if value:
            lines.append(f"✓ {label}: {value}")

    for option in draft.options:
        if option.get("value") not in (None, ""):
            lines.append(f"✓ {option.get('group') or 'Характеристика'}: "
                         f"{option.get('chosen') or option['value']}")

    if draft.name:
        lines.append(f"✓ Название: {draft.name}")

    if draft.price:
        lines.append(f"✓ Цена: {draft.price} ₽")

    if draft.nominal:
        lines.append(f"✓ Номинал: {shown_number(draft.nominal)}")

    if draft.region:
        lines.append(f"✓ Регион: {draft.region}")

    if draft.description is not None:
        first = (draft.description or draft.tail
                 or DEFAULT_TAIL).splitlines()[0]
        lines.append(f"✓ Описание: {first[:40]}"
                     + ("…" if len(first) > 40 else ""))

    for field in draft.fields:
        if field.get("value") is not None:
            shown = field["value"] or "—"
            lines.append(f"✓ {field.get('label') or 'Поле'}: {shown}")

    if draft.photos:
        lines.append(f"✓ Фотографий: {len(draft.photos)}")

    return "\n".join(lines)


def question_for(draft: Draft) -> str:
    """Что спросить сейчас. Пусто — спрашивать нечего."""
    step = draft.step

    if not step:
        return ""

    if step.startswith(FIELD):
        field = draft.field(step[len(FIELD):]) or {}
        label = field.get("label") or "Поле"

        if field.get("required"):
            return (f"{label}.\n\n"
                    "Это поле площадка требует заполнить — без него товар "
                    "не примут.")

        return (f"{label}.\n\n"
                "Поле необязательное. Если не нужно — «пропустить».")

    return QUESTIONS.get(step, "")


def apply(draft: Draft, text: str) -> str:
    """Принять текстовый ответ на текущий шаг. → причина отказа или пусто.

    Шаги с выбором на площадке и с фотографиями сюда не приходят: там
    ответом служит нажатие или файл, и разбирает их вызывающий.
    """
    step = draft.step

    if step.startswith(FIELD):
        return apply_field(draft, step[len(FIELD):], text)

    accept = ACCEPT.get(step)

    if accept is None:
        return ""

    value, why = accept(text)

    if why:
        return why

    setattr(draft, step, value)

    if step == "name":
        # Номинал обычно виден прямо в названии — берём молча. Спрашивать
        # то, что уже знаешь, значит лишнее нажатие на каждом товаре;
        # шаг «nominal» появится, только если число не нашлось.
        draft.nominal = nominal_from_title(value) or 0.0

    return ""


def accept_one(draft: Draft, field: str, text: str) -> str:
    """Принять одно поле по имени, не глядя на текущий шаг.

    Нужно разбору «одним сообщением»: там поля приходят все сразу и в
    любом порядке, а `apply` умеет только следующий по очереди шаг.

    Проверки те же самые — иначе через этот путь в товар попало бы то,
    что пошаговый опрос отверг бы: цена с копейками, чужой регион.
    """
    accept = ACCEPT.get(field)

    if accept is None:
        return ""

    value, why = accept(text)

    if why:
        return why

    setattr(draft, field, value)

    if field == "name" and not draft.nominal:
        # Номинал из названия — как и в пошаговом опросе. «Не затирать
        # заданное» важно: строка «Номинал» могла прийти раньше названия.
        draft.nominal = nominal_from_title(value) or 0.0

    return ""


def apply_field(draft: Draft, field_id: str, text: str) -> str:
    """Принять ответ на поле площадки. → причина отказа или пусто."""
    field = draft.field(field_id)

    if field is None:
        return ""

    if skipped(text):
        if field.get("required"):
            return ("Это поле обязательное — без него площадка товар не "
                    "примет. Напишите значение.")

        field["value"] = ""
        return ""

    value = " ".join(str(text or "").split())

    if not value:
        if field.get("required"):
            return "Пусто. Это поле площадка требует заполнить."

        field["value"] = ""
        return ""

    if len(value) > FIELD_LIMIT:
        return (f"Слишком длинно: {len(value)} знаков при "
                f"{FIELD_LIMIT} допустимых.")

    field["value"] = value

    return ""


def description_for(draft: Draft) -> str:
    """Описание товара для площадки.

    Первые строки — не оформление: из них движок выдачи читает регион и
    номинал. Уберёте их — бот при оплате остановится и код не купит.
    Поэтому ставим их мы, а не продавец, и в его тексте такие строки
    вырезаны: две разные строки номинала в одном описании — это тихая
    денежная ошибка.

    Номинал в описании надёжнее номинала в названии: здесь он СКАЗАН, а в
    названии его приходится угадывать по самому крупному числу.
    """
    tail = draft.description or draft.tail or DEFAULT_TAIL
    head = [f"Регион кода: {draft.region}"]

    if draft.nominal:
        head.append(f"Номинал: {shown_number(draft.nominal)}")

    return "\n".join(head) + f"\n\n{tail}"
