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
STEPS = ("name", "price", "region", "description", "comment", "photos")

QUESTIONS = {
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
    "comment": ("Комментарий к товару — необязательное поле площадки.\n\n"
                "Короткая пометка для карточки. Если не нужен — "
                "«пропустить»."),
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

COMMENT_LIMIT = 500

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
        self.name = ""
        self.price = 0
        self.region = ""
        # None — ещё не спрашивали, "" — спросили и пропустили. Разница
        # важна: иначе пропуск означал бы вечный повтор вопроса.
        self.description = None
        self.comment = None
        self.photos: list = []

    @property
    def step(self) -> str:
        """Какой шаг сейчас. Пусто — значит всё собрано."""
        if not self.name:
            return "name"

        if not self.price:
            return "price"

        if not self.region:
            return "region"

        if self.description is None:
            return "description"

        if self.comment is None:
            return "comment"

        if not self.photos:
            return "photos"

        return ""

    def summary(self) -> str:
        return (f"Название: {self.name}\n"
                f"Цена: {self.price} ₽\n"
                f"Регион: {self.region}\n"
                f"Комментарий: {self.comment or '—'}\n"
                f"Фотографий: {len(self.photos)}")


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


def accept_comment(text: str) -> tuple[str, str]:
    """→ (комментарий, причина отказа). Поле необязательное."""
    if skipped(text):
        return "", ""

    body = " ".join(str(text or "").split())

    if len(body) > COMMENT_LIMIT:
        return "", (f"Слишком длинно: {len(body)} знаков при "
                    f"{COMMENT_LIMIT} допустимых.")

    return body, ""


ACCEPT = {"name": accept_name, "price": accept_price, "region": accept_region,
          "description": accept_description, "comment": accept_comment}


def question_for(draft: Draft) -> str:
    """Что спросить сейчас. Пусто — спрашивать нечего."""
    step = draft.step

    return QUESTIONS.get(step, "") if step else ""


def apply(draft: Draft, text: str) -> str:
    """Принять текстовый ответ на текущий шаг. → причина отказа или пусто.

    Шаг с фотографиями сюда не приходит: картинки принимает вызывающий,
    ему же решать, что делать с присланным файлом.
    """
    step = draft.step
    accept = ACCEPT.get(step)

    if accept is None:
        return ""

    value, why = accept(text)

    if why:
        return why

    setattr(draft, step, value)

    return ""


def description_for(draft: Draft) -> str:
    """Описание товара для площадки.

    Первая строка — не оформление: из неё движок выдачи читает регион.
    Уберёте её — бот при оплате остановится и код не купит. Поэтому её
    ставим мы, а не продавец, и в его тексте такие строки вырезаны.
    """
    tail = draft.description if draft.description else DEFAULT_TAIL

    return f"Регион кода: {draft.region}\n\n{tail}"
