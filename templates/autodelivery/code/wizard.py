"""Диалог создания товара: какие вопросы и что считать ответом.

Здесь только правила, без телеграма и без площадки — чтобы их можно было
проверить тестами, а не живыми объявлениями.

Порядок вопросов не случаен. Сначала дёшево исправимое (название, цена),
потом фотографии: если продавец передумает на первом шаге, он не потратит
время на отправку картинок.
"""
from __future__ import annotations

import re

# Сколько шагов и в каком порядке.
# Выбор игры и категории идёт первым: от категории зависит и способ
# получения, и какие поля площадка потребует заполнить. Спрашивать их
# после названия значит однажды выбросить уже написанное.
STEPS = ("game", "category", "obtaining", "name", "price", "region",
         "description", "photos")

# Приставка у шага, который спрашивает поле с данными. Полей у разных
# категорий разное число, поэтому шаг не постоянный, а собирается из id.
FIELD = "field:"

QUESTIONS = {
    "game": ("Для какой игры или приложения товар?\n\n"
             "Напишите название или его часть — покажу, что нашлось."),
    "category": "Какая категория?",
    "obtaining": ("Как покупатель получает товар?\n\n"
                  "Для кодов это «без входа в аккаунт»: вы отдаёте код, а "
                  "в чужой аккаунт не заходите."),
    "name": ("Название товара.\n\n"
             "Так его увидит покупатель в списке. Номинал лучше писать "
             "числом — из него бот потом поймёт, сколько покупать."),
    "price": ("Цена в рублях. Только число.\n\n"
              "Это то, что заплатит покупатель."),
    "region": ("Регион кода: GL или RU.\n\n"
               "GL — глобальные коды, RU — российские. По этой букве бот "
               "выберет, что покупать у поставщика, так что ошибка здесь "
               "означает покупку не того."),
    "description": ("Описание товара.\n\n"
                    "Что покупатель прочитает на странице: что он получит, "
                    "как быстро, как активировать.\n\n"
                    "Строку про регион дописывать не нужно — я поставлю её "
                    "сам, и по ней бот потом выберет, что покупать. Если "
                    "напишете свою, я её уберу, чтобы они не спорили."),
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

REGIONS = ("GL", "RU")


class Draft:
    """Что уже собрано. Обычная копилка ответов."""

    def __init__(self):
        # Выбранное на площадке: {"id": ..., "name": ...} или None.
        self.game = None
        self.category = None
        self.obtaining = None
        # Поля с данными выбранной категории: что спросить и что ответили.
        self.fields: list = []
        self.name = ""
        self.price = 0
        self.region = ""
        # None — ещё не спрашивали, "" — спросили и пропустили. Разница
        # важна: иначе пропуск означал бы вечный повтор вопроса.
        self.description = None
        self.photos: list = []

    @property
    def step(self) -> str:
        """Какой шаг сейчас. Пусто — значит всё собрано."""
        if not self.game:
            return "game"

        if not self.category:
            return "category"

        if not self.obtaining:
            return "obtaining"

        if not self.name:
            return "name"

        if not self.price:
            return "price"

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
                 f"Получение: {(self.obtaining or {}).get('name', '')}",
                 f"Название: {self.name}",
                 f"Цена: {self.price} ₽",
                 f"Регион: {self.region}"]

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


def accept_region(text: str) -> tuple[str, str]:
    """→ (регион, причина отказа)."""
    region = str(text or "").strip().upper()

    if region in REGIONS:
        return region, ""

    return "", f"Не понял. Напишите {' или '.join(REGIONS)}."


def accept_description(text: str) -> tuple[str, str]:
    """→ (описание, причина отказа). Пропуск даёт текст по умолчанию.

    Строки про регион из текста продавца вырезаются: свою мы поставим
    первой, а две разные строки в одном описании — это тихая денежная
    ошибка, движок прочитает не ту и купит не тот товар.
    """
    if skipped(text):
        return DEFAULT_TAIL, ""

    body = REGION_LINE.sub("", str(text or "")).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)

    if not body:
        return DEFAULT_TAIL, ""

    if len(body) > DESCRIPTION_LIMIT:
        return "", (f"Слишком длинно: {len(body)} знаков при "
                    f"{DESCRIPTION_LIMIT} допустимых.")

    return body, ""


ACCEPT = {"name": accept_name, "price": accept_price, "region": accept_region,
          "description": accept_description}


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

    Первая строка — не оформление: из неё движок выдачи читает регион.
    Уберёте её — бот при оплате остановится и код не купит. Поэтому её
    ставим мы, а не продавец, и в его тексте такие строки вырезаны.
    """
    tail = draft.description if draft.description else DEFAULT_TAIL

    return f"Регион кода: {draft.region}\n\n{tail}"
