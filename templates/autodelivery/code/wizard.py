"""Диалог создания товара: какие вопросы и что считать ответом.

Здесь только правила, без телеграма и без площадки — чтобы их можно было
проверить тестами, а не живыми объявлениями.

Порядок вопросов не случаен. Сначала дёшево исправимое (название, цена),
потом фотографии: если продавец передумает на первом шаге, он не потратит
время на отправку картинок.
"""
from __future__ import annotations

# Сколько шагов и в каком порядке.
STEPS = ("name", "price", "region", "photos")

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
    "photos": ("Пришлите фотографии товара. Можно несколько — по одной.\n\n"
               "Когда хватит, напишите «готово».\n"
               "Хотя бы одна обязательна: без картинок площадка товар не "
               "принимает."),
}

DONE_WORD = "готово"
CANCEL_WORDS = ("отмена", "стоп", "хватит")

# Площадка режет длинные названия, и лучше сказать об этом сразу, чем
# получить обрезанное объявление.
NAME_LIMIT = 120

REGIONS = ("GL", "RU")


class Draft:
    """Что уже собрано. Обычная копилка ответов."""

    def __init__(self):
        self.name = ""
        self.price = 0
        self.region = ""
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

        if not self.photos:
            return "photos"

        return ""

    def summary(self) -> str:
        return (f"Название: {self.name}\n"
                f"Цена: {self.price} ₽\n"
                f"Регион: {self.region}\n"
                f"Фотографий: {len(self.photos)}")


def cancelled(text: str) -> bool:
    return str(text or "").strip().lower() in CANCEL_WORDS


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


ACCEPT = {"name": accept_name, "price": accept_price, "region": accept_region}


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
    Уберёте её — бот при оплате остановится и код не купит.
    """
    return (f"Регион кода: {draft.region}\n"
            "\n"
            "Код приходит в чат сразу после оплаты.\n"
            "Активация: roblox.com/redeem")
