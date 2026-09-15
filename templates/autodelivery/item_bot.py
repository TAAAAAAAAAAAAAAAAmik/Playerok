"""Создание товара разговором в Telegram: спрашивает и выставляет.

Запускается на сервере и ждёт. Вы пишете боту «новый товар», он
спрашивает название, цену, регион и фотографии, показывает, что вышло, и
создаёт черновик. Потом спрашивает, выставлять ли и как.

    python3 item_bot.py

ЧТО БОТ ПРИНИМАЕТ. Только от владельца — чужой chat_id выбрасывается
молча. Команд ровно две: «новый товар» и «отмена». Всё остальное имеет
смысл только как ответ на заданный вопрос.

ПРО ДЕНЬГИ. Черновик бесплатен. Выставление со статусом приоритета —
платное, и бот спрашивает об этом отдельно, показывая цены. Платный
переспрашивается словом. Молча он не потратит ничего.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import sign_in                                      # noqa: E402
from envfile import load_env_file                             # noqa: E402
import listing                                                # noqa: E402
import wizard                                                 # noqa: E402

# Товар создаём в той категории, что разведана для кодов Roblox.
CATEGORY_ID = os.environ.get(
    "PLAYEROK_CATEGORY_ID", "1ecc48ce-53cd-6f00-70d9-f8db195c837a")

# «Без входа в аккаунт»: мы выдаём код, а не заходим в чужой аккаунт.
OBTAINING_TYPE_ID = os.environ.get(
    "PLAYEROK_OBTAINING_TYPE_ID", "1f094822-b7a2-6590-385d-cadb2ec7b130")

START_WORDS = ("новый товар", "новый", "/new", "/newitem")

# Сколько ждать ответа на один вопрос. Полчаса: продавец может отвлечься,
# и бот не должен ронять начатое из-за этого.
ANSWER_WAIT = 1800


class Field:
    """Поле с данными в том виде, в каком его ждёт библиотека."""

    def __init__(self, id, value):                            # noqa: A002
        self.id = id
        self.value = value


def photo_id(message: dict) -> str:
    """Самый крупный размер присланной фотографии, или пусто.

    Telegram отдаёт несколько размеров одной картинки. Берём последний —
    он самый большой, а объявление с мутной картинкой хуже продаётся.
    """
    sizes = message.get("photo") or []

    if isinstance(sizes, list) and sizes:
        return str(sizes[-1].get("file_id") or "")

    # Картинку можно прислать и файлом — тогда Telegram не сжимает её.
    document = message.get("document") or {}

    if str(document.get("mime_type") or "").startswith("image/"):
        return str(document.get("file_id") or "")

    return ""


def collect(link, draft: wizard.Draft) -> bool:
    """Пройти опрос. → дошли ли до конца."""
    while True:
        step = draft.step

        if not step:
            return True

        message = link.ask(wizard.question_for(draft), ANSWER_WAIT)

        if not message:
            link.say("Не дождался ответа. Начнём заново, когда будете "
                     "готовы: напишите «новый товар».")
            return False

        text = str(message.get("text") or "")

        if wizard.cancelled(text):
            link.say("Отменил. Ничего не создано.")
            return False

        if step != "photos":
            why = wizard.apply(draft, text)

            if why:
                link.say(why)

            continue

        if not collect_photos(link, draft, message):
            return False


def collect_photos(link, draft: wizard.Draft, message: dict) -> bool:
    """Собрать фотографии, пока не скажут «готово». → продолжать ли."""
    while True:
        text = str(message.get("text") or "")

        if wizard.cancelled(text):
            link.say("Отменил. Ничего не создано.")
            return False

        if wizard.enough_photos(text):
            if draft.photos:
                return True

            link.say("Пока ни одной фотографии. Хотя бы одна обязательна — "
                     "без картинок площадка товар не принимает.")
        else:
            file_id = photo_id(message)

            if not file_id:
                link.say("Это не фотография. Пришлите картинку, а когда "
                         f"хватит — напишите «{wizard.DONE_WORD}».")
            else:
                data = link.download(file_id)

                if not data:
                    link.say("Забрать картинку не вышло. Пришлите ещё раз.")
                else:
                    draft.photos.append(data)
                    link.say(f"Принял. Всего: {len(draft.photos)}. "
                             f"Ещё или «{wizard.DONE_WORD}»?")

        message = link.wait_answer(ANSWER_WAIT)

        if not message:
            link.say("Не дождался. Начнём заново: «новый товар».")
            return False


def publish_step(link, account, item_id: str, price: int) -> None:
    """Спросить про выставление и выставить, если согласились."""
    try:
        statuses = account.get_item_priority_statuses(item_id, price)
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Статусы приоритета прочитать не вышло: {e}\n"
                 "Товар остался черновиком, выставьте его в кабинете.")
        return

    rows = listing.ordered(statuses or [])

    if not rows:
        link.say("Статусов приоритета нет — выставить нечем. "
                 "Товар остался черновиком.")
        return

    lines = [f"{n}. {listing.describe(s)}" for n, s in enumerate(rows, 1)]
    answer = link.ask("Как выставляем?\n\n" + "\n".join(lines)
                      + "\n\n0. не выставлять, оставить черновиком",
                      ANSWER_WAIT)
    chosen = listing.pick(rows, str(answer.get("text") or ""))

    if chosen is None:
        link.say("Оставил черновиком. Выставить можно в кабинете.")
        return

    if listing.needs_confirmation(chosen):
        # Платный статус переспрашиваем словом: промах по номеру не должен
        # стоить денег.
        again = link.ask(
            f"Это платно: {listing.describe(chosen)}\n"
            "Сумма спишется с баланса площадки.\n\n"
            f"Напишите «{listing.CONFIRM_WORD}», если согласны.", ANSWER_WAIT)

        if not listing.confirmed(str(again.get("text") or "")):
            link.say("Не подтверждено. Оставил черновиком.")
            return

    try:
        account.publish_item(item_id, chosen.id)
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Выставить не вышло: {e}\n"
                 "Черновик при этом цел и виден в кабинете.")
        return

    link.say(f"Выставлено: {listing.describe(chosen)}")


def make_item(link, account) -> None:
    """Один проход: опрос, черновик, выставление."""
    draft = wizard.Draft()
    link.say("Создаём товар. В любой момент — «отмена».")

    if not collect(link, draft):
        return

    link.say("Проверьте:\n\n" + draft.summary()
             + "\n\n" + wizard.description_for(draft)
             + "\n\nСоздаю черновик…")

    try:
        item = account.create_item(
            game_category_id=CATEGORY_ID,
            obtaining_type_id=OBTAINING_TYPE_ID,
            name=draft.name,
            price=draft.price,
            description=wizard.description_for(draft),
            options={},
            data_fields=[],
            attachments=list(draft.photos),
        )
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Создать не вышло: {e}\n"
                 "Ничего не потрачено. Попробуем ещё раз: «новый товар».")
        return

    link.say(f"Черновик создан.\nhttps://playerok.com/products/{item.id}")
    publish_step(link, account, item.id, draft.price)


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    account, _store, link = sign_in()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID — "
            "разговаривать не с кем.")

    print("Жду в телеграме. Напишите боту «новый товар».")
    link.say("Готов. Напишите «новый товар», когда захотите добавить "
             "объявление.")

    while True:
        message = link.wait_answer(3600)
        text = str(message.get("text") or "").strip().lower()

        if not text:
            continue

        if text in START_WORDS:
            make_item(link, account)
            link.say("Готов к следующему: «новый товар».")
        elif not wizard.cancelled(text):
            # Молчать нельзя: продавец решит, что бот умер.
            link.say("Напишите «новый товар», чтобы добавить объявление.")

        time.sleep(0.2)


if __name__ == "__main__":
    main()
