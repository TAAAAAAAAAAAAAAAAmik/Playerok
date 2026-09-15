"""Создание товара разговором в Telegram: спрашивает и выставляет.

Запускается на сервере и ждёт. Вы пишете боту «новый товар», он
спрашивает название, цену, регион и фотографии, показывает, что вышло, и
создаёт черновик. Потом спрашивает, выставлять ли и как.

    python3 item_bot.py

ШАБЛОНЫ. Созданное объявление можно сохранить шаблоном, и тогда такой же
товар создаётся одним нажатием: название, цена, регион и фотографии уже
внутри. Картинки хранятся копией, а не ссылкой на товар — товар продадут
или снимут, а шаблон должен работать и через полгода.

ЧТО БОТ ПРИНИМАЕТ. Только от владельца — чужой chat_id выбрасывается
молча. Команд ровно три: «новый товар», «шаблон» и «отмена». Всё остальное имеет
смысл только как ответ на заданный вопрос.

Нажатие кнопки приходит в тот же разбор, что и набранный текст: подпись
кнопки для человека, а значение — то самое слово, которое бот понимает.
Поэтому кнопками можно пользоваться, а можно писать словами — работает и
так и так.

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
from templates import TemplateStore                           # noqa: E402

# Кнопки, которые повторяются. Подписи для человека, значения — те же
# слова, что понимает разбор ответов: нажатие и набранный текст должны
# приходить в один и тот же разбор.
MENU = [[("➕ Новый товар", "новый товар")],
        [("⚡ Из шаблона", "шаблон")]]
CANCEL = [("✖️ Отмена", "отмена")]
REGIONS = [("🌍 GL — глобальный", "GL"), ("🇷🇺 RU — российский", "RU")]
PHOTOS_DONE = [("✅ Готово", wizard.DONE_WORD), ("✖️ Отмена", "отмена")]

# Товар создаём в той категории, что разведана для кодов Roblox.
CATEGORY_ID = os.environ.get(
    "PLAYEROK_CATEGORY_ID", "1ecc48ce-53cd-6f00-70d9-f8db195c837a")

# «Без входа в аккаунт»: мы выдаём код, а не заходим в чужой аккаунт.
OBTAINING_TYPE_ID = os.environ.get(
    "PLAYEROK_OBTAINING_TYPE_ID", "1f094822-b7a2-6590-385d-cadb2ec7b130")

START_WORDS = ("новый товар", "новый", "/new", "/newitem")
TEMPLATE_WORDS = ("шаблон", "шаблоны", "из шаблона", "/tpl")

# Где лежат шаблоны. Рядом с состоянием выдач: это тоже рабочие данные,
# которые переживают перезапуск и не место им в репозитории.
TEMPLATE_DIR = os.environ.get("PLAYEROK_TEMPLATES", "state/templates")

# Приставка у значения кнопки шаблона. Нужна, чтобы номер шаблона нельзя
# было спутать с ответом на другой вопрос.
PICK = "tpl:"

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


def buttons_for(step: str):
    """Кнопки под вопрос. Там, где ответ свободный, кнопок нет.

    Название и цену кнопкой не выберешь, но «отмена» нужна на каждом шаге:
    передумать посреди опроса — обычное дело.
    """
    if step == "region":
        return [REGIONS, CANCEL]

    if step == "photos":
        return [PHOTOS_DONE]

    return [CANCEL]


def collect(link, draft: wizard.Draft) -> bool:
    """Пройти опрос. → дошли ли до конца."""
    while True:
        step = draft.step

        if not step:
            return True

        message = link.ask(wizard.question_for(draft), ANSWER_WAIT,
                           buttons=buttons_for(step))

        if not message:
            link.say("Не дождался ответа. Начнём заново, когда будете "
                     "готовы.", buttons=MENU)
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
                     "без картинок площадка товар не принимает.",
                     buttons=[PHOTOS_DONE])
        else:
            file_id = photo_id(message)

            if not file_id:
                link.say("Это не фотография. Пришлите картинку, а когда "
                         "хватит — нажмите «Готово».",
                         buttons=[PHOTOS_DONE])
            else:
                data = link.download(file_id)

                if not data:
                    link.say("Забрать картинку не вышло. Пришлите ещё раз.",
                             buttons=[PHOTOS_DONE])
                else:
                    draft.photos.append(data)
                    link.say(f"Принял. Всего: {len(draft.photos)}. "
                             f"Ещё одну или заканчиваем?",
                             buttons=[PHOTOS_DONE])

        message = link.wait_answer(ANSWER_WAIT)

        if not message:
            link.say("Не дождался. Начнём заново.", buttons=MENU)
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

    # Столбиком: подписи длинные, в ряд не влезут. Бесплатный сверху —
    # промахнуться в пользу платного должно быть труднее.
    keys = [[(f"{'🆓' if listing.is_free(s) else '💳'} "
              f"{listing.describe(s)}", str(n))]
            for n, s in enumerate(rows, 1)]
    keys.append([("📝 Оставить черновиком", "0")])

    answer = link.ask("Как выставляем?", ANSWER_WAIT, buttons=keys)
    chosen = listing.pick(rows, str(answer.get("text") or ""))

    if chosen is None:
        link.say("Оставил черновиком. Выставить можно в кабинете.")
        return

    if listing.needs_confirmation(chosen):
        # Платный статус переспрашиваем словом: промах по номеру не должен
        # стоить денег.
        # Кнопка подтверждения называет сумму: «да» вслепую слишком
        # легко нажать, а списание настоящее.
        again = link.ask(
            f"Это платно: {listing.describe(chosen)}\n"
            "Сумма спишется с баланса площадки.",
            ANSWER_WAIT,
            buttons=[[(f"💳 Да, списать {listing.price_of(chosen):g} ₽",
                       listing.CONFIRM_WORD)],
                     [("✖️ Нет, оставить черновиком", "нет")]])

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
    link.say("Создаём товар. В любой момент — «Отмена».")

    if not collect(link, draft):
        return

    link.say("Проверьте:\n\n" + draft.summary()
             + "\n\n" + wizard.description_for(draft)
             + "\n\nСоздаю черновик…")

    if not send_draft(link, account, draft):
        return

    offer_template(link, draft)


def send_draft(link, account, draft: wizard.Draft) -> bool:
    """Создать черновик и спросить про выставление. → получилось ли."""
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
                 "Ничего не потрачено. Попробуем ещё раз.", buttons=MENU)
        return False

    link.say(f"Черновик создан.\nhttps://playerok.com/products/{item.id}")
    publish_step(link, account, item.id, draft.price)

    return True


def offer_template(link, draft: wizard.Draft) -> None:
    """Предложить сохранить объявление шаблоном.

    Спрашиваем после создания, а не до: пока товар не создан, неизвестно,
    подойдёт ли он вообще, и шаблон из неудачной попытки только мешал бы.
    """
    answer = link.ask(
        "Сохранить как шаблон? Потом такой же товар создастся одним "
        "нажатием.", ANSWER_WAIT,
        buttons=[[("💾 Сохранить", "да")], [("Не надо", "нет")]])

    if str(answer.get("text") or "").strip().lower() not in ("да", "сохранить"):
        return

    store = TemplateStore(TEMPLATE_DIR)

    try:
        store.save(draft.name, draft.price, draft.region, draft.photos)
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Сохранить шаблон не вышло: {e}")
        return

    link.say("Шаблон сохранён.")


def from_template(link, account) -> None:
    """Повторить сохранённое объявление одним нажатием."""
    store = TemplateStore(TEMPLATE_DIR)
    saved = store.all()

    if not saved:
        link.say("Шаблонов пока нет. Создайте товар и сохраните его "
                 "шаблоном — дальше он будет создаваться одним нажатием.",
                 buttons=MENU)
        return

    keys = [[(t.label(), PICK + t.id)] for t in saved]
    keys.append([("✖️ Отмена", "отмена")])
    answer = link.ask("Какой повторяем?", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK):
        link.say("Отменил.", buttons=MENU)
        return

    template = store.get(text[len(PICK):])

    if template is None:
        link.say("Такого шаблона больше нет.", buttons=MENU)
        return

    photos = template.photos()

    if not photos:
        link.say("У шаблона пропали картинки — без них товар не создать. "
                 "Соберите объявление заново.", buttons=MENU)
        return

    draft = wizard.Draft()
    draft.name = template.name
    draft.price = template.price
    draft.region = template.region
    draft.photos = photos

    link.say(f"Повторяю:\n\n{draft.summary()}\n\nСоздаю черновик…")
    send_draft(link, account, draft)


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    account, _store, link = sign_in()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID — "
            "разговаривать не с кем.")

    print("Жду в телеграме. Напишите боту «новый товар».")
    link.say("Готов.", buttons=MENU)

    while True:
        message = link.wait_answer(3600)
        text = str(message.get("text") or "").strip().lower()

        if not text:
            continue

        if text in START_WORDS:
            make_item(link, account)
            link.say("Готов к следующему.", buttons=MENU)
        elif text in TEMPLATE_WORDS:
            from_template(link, account)
            link.say("Готов к следующему.", buttons=MENU)
        elif not wizard.cancelled(text):
            # Молчать нельзя: продавец решит, что бот умер.
            link.say("Что делаем?", buttons=MENU)

        time.sleep(0.2)


if __name__ == "__main__":
    main()
