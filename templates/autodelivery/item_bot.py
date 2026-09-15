"""Создание товара разговором в Telegram: спрашивает и выставляет.

Запускается на сервере и ждёт. Вы пишете боту «новый товар», он
спрашивает название, цену, регион и фотографии, показывает, что вышло, и
создаёт черновик. Потом спрашивает, выставлять ли и как.

    python3 item_bot.py

ЧТО СПРАШИВАЕТСЯ. Игра, категория, способ получения, характеристики,
название, цена,
регион, описание, поля площадки и фотографии. Ничего не зашито: категории
у разных игр разные, а один зашитый id уже приводил к тому, что товары
создавались не там, где нужно.

ШАБЛОНЫ У КАЖДОГО КАБИНЕТА СВОИ: товары у разных кабинетов разные, и
перемешанные шаблоны означают объявление, созданное не там, где хотели.
Шаблон можно повторить, изменить по одному полю или удалить.

КАБИНЕТЫ. Их может быть несколько: кнопка «Аккаунт» показывает список и
переключает. Библиотека площадки держит аккаунт синглтоном, поэтому два
кабинета одновременно в одном процессе жить не могут — только по очереди.

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

from envfile import load_env_file                             # noqa: E402
from owner import link_from_env                               # noqa: E402
import listing                                                # noqa: E402
import wizard                                                 # noqa: E402
from accounts import AccountStore                             # noqa: E402
from auth import open_account, sign_in                        # noqa: E402
from owner import normalize_cookies                           # noqa: E402
from templates import TemplateStore, folder_for               # noqa: E402

# Кнопки, которые повторяются. Подписи для человека, значения — те же
# слова, что понимает разбор ответов: нажатие и набранный текст должны
# приходить в один и тот же разбор.
MENU = [[("➕ Новый товар", "новый товар")],
        [("⚡ Из шаблона", "шаблон")],
        [("👤 Аккаунт", "аккаунт")]]
CANCEL = [("✖️ Отмена", "отмена")]
REGIONS = [("🌍 GL — глобальный", "GL"), ("🇷🇺 RU — российский", "RU")]
SKIP = [("⏭ Пропустить", "пропустить"), ("✖️ Отмена", "отмена")]
PHOTOS_DONE = [("✅ Готово", wizard.DONE_WORD), ("✖️ Отмена", "отмена")]

# Ничего про категории здесь не зашито намеренно. Один зашитый id уже
# привёл к тому, что все товары создавались в чужой категории, и заметить
# это можно было только глазами в кабинете.

# Сколько вариантов показывать кнопками за раз. Больше — и список
# перестаёт помещаться на экране телефона.
MAX_CHOICES = 12

START_WORDS = ("новый товар", "новый", "/new", "/newitem")
TEMPLATE_WORDS = ("шаблон", "шаблоны", "из шаблона", "/tpl")
ACCOUNT_WORDS = ("аккаунт", "аккаунты", "кабинет", "/account")

# Где лежат шаблоны. Рядом с состоянием выдач: это тоже рабочие данные,
# которые переживают перезапуск и не место им в репозитории.
TEMPLATE_DIR = os.environ.get("PLAYEROK_TEMPLATES", "state/templates")

# Приставка у значения кнопки шаблона. Нужна, чтобы номер шаблона нельзя
# было спутать с ответом на другой вопрос.
PICK = "tpl:"

# Приставки у кнопок выбора на площадке: чтобы нажатие нельзя было
# спутать с ответом на другой вопрос.
PICK_GAME = "game:"
PICK_CATEGORY = "cat:"
PICK_OBTAINING = "obt:"
PICK_OPTION = "opt:"
PICK_ACCOUNT = "acc:"
PICK_FIX = "fix:"
PICK_ACT = "act:"

# Где живут сохранённые кабинеты.
ACCOUNTS_DIR = os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts")

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

    if step.startswith(wizard.FIELD) or step == "description":
        # Описание можно не писать — возьмётся типовое. У поля площадки
        # кнопка «пропустить» есть всегда, но обязательное поле её не
        # примет и переспросит.
        return [SKIP]

    return [CANCEL]


def choose(link, question, rows, prefix, wait=None):
    """Показать варианты кнопками и вернуть выбранный. → (id, имя) или None.

    Значение кнопки — приставка плюс id, чтобы нажатие нельзя было принять
    за ответ на другой вопрос.
    """
    rows = list(rows)[:MAX_CHOICES]

    if not rows:
        return None

    keys = [[(name, prefix + str(value))] for value, name in rows]
    keys.append([("✖️ Отмена", "отмена")])
    answer = link.ask(question, wait or ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(prefix):
        return None

    chosen = text[len(prefix):]

    for value, name in rows:
        if str(value) == chosen:
            return {"id": str(value), "name": name}

    return None


def choose_game(link, account, draft) -> bool:
    """Спросить игру и найти её у площадки. → продолжать ли."""
    answer = link.ask(wizard.question_for(draft), ANSWER_WAIT, buttons=[CANCEL])
    search = str(answer.get("text") or "").strip()

    if not search or wizard.cancelled(search):
        return False

    try:
        page = account.get_games(name=search, count=MAX_CHOICES)
        games = list(getattr(page, "games", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Поиск не удался: {e}")
        return True

    if not games:
        link.say(f"По запросу «{search}» ничего не нашлось. Попробуйте "
                 "короче.")
        return True

    chosen = choose(link, "Что из этого?",
                    [(g.id, g.name) for g in games], PICK_GAME)

    if chosen is None:
        link.say("Отменил.", buttons=MENU)
        return False

    draft.game = chosen

    return True


def choose_category(link, account, draft) -> bool:
    """Спросить категорию выбранной игры. → продолжать ли."""
    try:
        game = account.get_game(id=draft.game["id"])
        rows = list(getattr(game, "categories", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Категории прочитать не вышло: {e}")
        return False

    if not rows:
        link.say("У этой игры нет категорий — товар создать негде.",
                 buttons=MENU)
        return False

    chosen = choose(link, wizard.question_for(draft),
                    [(c.id, c.name) for c in rows], PICK_CATEGORY)

    if chosen is None:
        link.say("Отменил.", buttons=MENU)
        return False

    draft.category = chosen

    return True


def choose_obtaining(link, account, draft) -> bool:
    """Спросить способ получения и узнать поля категории."""
    try:
        page = account.get_game_category_obtaining_types(
            draft.category["id"], count=MAX_CHOICES)
        rows = list(getattr(page, "obtaining_types", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Способы получения прочитать не вышло: {e}")
        return False

    if not rows:
        link.say("У этой категории нет способов получения.", buttons=MENU)
        return False

    chosen = choose(link, wizard.question_for(draft),
                    [(o.id, o.name) for o in rows], PICK_OBTAINING)

    if chosen is None:
        link.say("Отменил.", buttons=MENU)
        return False

    draft.obtaining = chosen
    draft.fields = item_fields(account, draft.category["id"], chosen["id"])
    draft.options = category_options(account, draft.category["id"])

    return True


def category_options(account, category_id: str) -> list:
    """Характеристики категории, сгруппированные по полю.

    Площадка называет их атрибутами и часть требует обязательно: с пустыми
    она отвечает «заполните все обязательные характеристики», не уточняя
    какие. Поэтому спрашиваем все — лишний вопрос дешевле отказа.

    Группируем по `field`, потому что именно оно уходит в запрос, а
    значений у одного поля бывает много.
    """
    try:
        category = account.get_game_category(id=category_id)
        rows = list(getattr(category, "options", None) or [])
    except Exception:                                         # noqa: BLE001
        return []

    groups: dict = {}

    for row in rows:
        field = str(getattr(row, "field", "") or "")

        if not field:
            continue

        group = groups.setdefault(field, {
            "field": field,
            "group": str(getattr(row, "group", "") or field),
            "choices": [],
            "value": None,
        })
        group["choices"].append({
            "label": str(getattr(row, "label", "") or "—"),
            "value": getattr(row, "value", None),
        })

    return list(groups.values())


def choose_option(link, account, draft) -> bool:
    """Спросить одну характеристику. → продолжать ли."""
    step = draft.step
    option = draft.option(step[len(wizard.OPTION):])

    if option is None:
        return True

    choices = option.get("choices") or []

    if not choices:
        # Спрашивать нечего — считаем незаполненной и идём дальше.
        option["value"] = ""
        return True

    keys = [[(c["label"], PICK_OPTION + str(number))]
            for number, c in enumerate(choices[:MAX_CHOICES])]
    keys.append([("✖️ Отмена", "отмена")])
    title = option.get("group") or wizard.QUESTIONS["options"]
    answer = link.ask(f"{title}?" if not title.endswith(".") else title,
                      ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_OPTION):
        link.say("Отменил.", buttons=MENU)
        return False

    number = text[len(PICK_OPTION):]

    if not number.isdigit() or int(number) >= len(choices):
        link.say("Не понял выбор.")
        return True

    picked = choices[int(number)]
    option["value"] = picked["value"]
    option["chosen"] = picked["label"]

    return True


def item_fields(account, category_id: str, obtaining_id: str) -> list:
    """Поля, которые заполняет ПРОДАВЕЦ при создании товара.

    Только ITEM_DATA. Поля OBTAINING_DATA вводит покупатель при оформлении,
    и заполнять их за него — верный способ получить отказ.
    """
    try:
        page = account.get_game_category_data_fields(
            category_id, obtaining_id, count=24)
        rows = list(getattr(page, "data_fields", None) or [])
    except Exception:                                         # noqa: BLE001
        return []

    fields = []

    for row in rows:
        kind = str(getattr(getattr(row, "type", None), "name", "") or "")

        if kind != "ITEM_DATA":
            continue

        fields.append({"id": str(row.id),
                       "label": str(getattr(row, "label", "") or "Поле"),
                       "required": bool(getattr(row, "required", False)),
                       "value": None})

    return fields


CHOOSE = {}          # заполняется ниже, когда функции определены


def collect(link, account, draft: wizard.Draft) -> bool:
    """Пройти опрос. → дошли ли до конца."""
    complaint = ""

    while True:
        step = draft.step

        if not step:
            return True

        # Замечание и вопрос — одним сообщением, а не двумя. Каждый лишний
        # обмен с Telegram это задержка на ровном месте, и в переписке от
        # них рябит.
        if step.startswith(wizard.OPTION):
            if not choose_option(link, account, draft):
                return False

            continue

        if step in CHOOSE:
            if not CHOOSE[step](link, account, draft):
                return False

            continue

        question = wizard.question_for(draft)
        message = link.ask(f"{complaint}\n\n{question}" if complaint
                           else question,
                           ANSWER_WAIT, buttons=buttons_for(step))
        complaint = ""

        if not message:
            link.say("Не дождался ответа. Начнём заново, когда будете "
                     "готовы.", buttons=MENU)
            return False

        text = str(message.get("text") or "")

        if wizard.cancelled(text):
            link.say("Отменил. Ничего не создано.")
            return False

        if step != "photos":
            complaint = wizard.apply(draft, text)
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

    if not collect(link, account, draft):
        return

    link.say("Проверьте:\n\n" + draft.summary()
             + "\n\n— описание —\n" + wizard.description_for(draft)
             + "\n\nСоздаю черновик…")

    if not send_draft(link, account, draft):
        return

    offer_template(link, draft)


def send_draft(link, account, draft: wizard.Draft) -> bool:
    """Создать черновик и спросить про выставление. → получилось ли."""
    fields = [Field(f["id"], f["value"]) for f in draft.filled_fields()]

    try:
        item = account.create_item(
            game_category_id=draft.category["id"],
            obtaining_type_id=draft.obtaining["id"],
            name=draft.name,
            price=draft.price,
            description=wizard.description_for(draft),
            options=draft.attributes(),
            data_fields=fields,
            attachments=list(draft.photos),
        )
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Создать не вышло: {e}\n"
                 "Ничего не потрачено. Попробуем ещё раз.", buttons=MENU)
        return False

    link.say(f"Черновик создан.\nhttps://playerok.com/products/{item.id}")
    publish_step(link, account, item.id, draft.price)

    return True


def templates_of(account_id: str = "") -> TemplateStore:
    """Шаблоны текущего кабинета.

    Папка на кабинет, а не общая: у разных кабинетов разные товары, и
    перемешанные шаблоны означают объявление, созданное не там, где хотели.
    """
    if not account_id:
        current = AccountStore(ACCOUNTS_DIR).current()
        account_id = current.id if current else ""

    return TemplateStore(folder_for(TEMPLATE_DIR, account_id))


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

    store = templates_of()

    try:
        store.save(draft.name, draft.price, draft.region, draft.photos,
                   description=draft.description,
                   game=draft.game, category=draft.category,
                   obtaining=draft.obtaining, fields=draft.fields,
                   options=draft.options)
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Сохранить шаблон не вышло: {e}")
        return

    link.say("Шаблон сохранён.")


def from_template(link, account) -> None:
    """Список шаблонов кабинета: повторить, изменить или удалить."""
    store = templates_of()
    saved = store.all()

    if not saved:
        link.say("У этого кабинета шаблонов пока нет. Создайте товар и "
                 "сохраните его шаблоном — дальше он будет создаваться "
                 "одним нажатием.", buttons=MENU)
        return

    keys = [[(t.label(), PICK + t.id)] for t in saved]
    keys.append([("✖️ Отмена", "отмена")])
    answer = link.ask("Шаблоны этого кабинета:", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK):
        link.say("Отменил.", buttons=MENU)
        return

    template_id = text[len(PICK):]
    template = store.get(template_id)

    if template is None:
        link.say("Такого шаблона больше нет.", buttons=MENU)
        return

    answer = link.ask(
        f"{template.label()}\n\nЧто делаем?", ANSWER_WAIT,
        buttons=[[("⚡ Создать товар", PICK_ACT + "make")],
                 [("✏️ Изменить", PICK_ACT + "edit")],
                 [("🗑 Удалить", PICK_ACT + "drop")],
                 [("✖️ Назад", "отмена")]])
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_ACT):
        link.say("Отменил.", buttons=MENU)
        return

    what = what[len(PICK_ACT):]

    if what == "drop":
        drop_template(link, store, template)
        return

    if what == "edit":
        edit_template(link, store, template_id)
        return

    make_from_template(link, account, store, template_id)


def drop_template(link, store, template) -> None:
    """Удалить шаблон, переспросив: восстановить его нечем."""
    answer = link.ask(
        f"Удалить «{template.label()}»?\n\n"
        "Вместе с картинками. Восстановить будет нечем.", ANSWER_WAIT,
        buttons=[[("🗑 Да, удалить", "да")], [("✖️ Нет", "отмена")]])

    if str(answer.get("text") or "").strip().lower() != "да":
        link.say("Оставил.", buttons=MENU)
        return

    if store.remove(template.id):
        link.say("Удалил.", buttons=MENU)
    else:
        link.say("Удалить не вышло.", buttons=MENU)


def edit_template(link, store, template_id: str) -> None:
    """Поправить одно поле шаблона."""
    template = store.get(template_id)

    if template is None:
        link.say("Такого шаблона больше нет.", buttons=MENU)
        return

    answer = link.ask(
        "Что меняем?", ANSWER_WAIT,
        buttons=[[("📝 Название", PICK_ACT + "name")],
                 [("💰 Цену", PICK_ACT + "price")],
                 [("🌍 Регион", PICK_ACT + "region")],
                 [("📄 Описание", PICK_ACT + "description")],
                 [("🖼 Фотографии", PICK_ACT + "photos")],
                 [("✖️ Назад", "отмена")]])
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_ACT):
        link.say("Отменил.", buttons=MENU)
        return

    what = what[len(PICK_ACT):]

    if what == "photos":
        edit_photos(link, store, template_id)
        return

    if what == "region":
        answer = link.ask("Новый регион:", ANSWER_WAIT,
                          buttons=[REGIONS, CANCEL])
        value, why = wizard.accept_region(str(answer.get("text") or ""))
    elif what == "price":
        answer = link.ask("Новая цена:", ANSWER_WAIT, buttons=[CANCEL])
        value, why = wizard.accept_price(str(answer.get("text") or ""))
    elif what == "name":
        answer = link.ask("Новое название:", ANSWER_WAIT, buttons=[CANCEL])
        value, why = wizard.accept_name(str(answer.get("text") or ""))
    else:
        answer = link.ask("Новое описание:", ANSWER_WAIT, buttons=[SKIP])
        value, why = wizard.accept_description(str(answer.get("text") or ""))

    if wizard.cancelled(str(answer.get("text") or "")):
        link.say("Отменил.", buttons=MENU)
        return

    if why:
        link.say(why + "\n\nОставил как было.", buttons=MENU)
        return

    if store.update(template_id, **{what: value}):
        link.say("Поправил.", buttons=MENU)
    else:
        link.say("Сохранить не вышло.", buttons=MENU)


def edit_photos(link, store, template_id: str) -> None:
    """Заменить картинки шаблона целиком."""
    draft = wizard.Draft()
    link.say("Пришлите новые фотографии — они заменят прежние целиком.",
             buttons=[PHOTOS_DONE])
    message = link.wait_answer(ANSWER_WAIT)

    if not message:
        link.say("Не дождался.", buttons=MENU)
        return

    if not collect_photos(link, draft, message):
        return

    if store.set_photos(template_id, draft.photos):
        link.say(f"Заменил. Теперь фотографий: {len(draft.photos)}.",
                 buttons=MENU)
    else:
        link.say("Сохранить не вышло.", buttons=MENU)


def make_from_template(link, account, store, template_id: str) -> None:
    """Повторить сохранённое объявление одним нажатием."""
    template = store.get(template_id)

    if template is None:
        link.say("Такого шаблона больше нет.", buttons=MENU)
        return

    if not template.complete():
        # Шаблоны, сохранённые до того, как бот научился спрашивать
        # категорию, повторять нечем: товар ушёл бы не туда.
        link.say("Этот шаблон сохранён до того, как бот стал спрашивать "
                 "категорию, и повторить его нечем — создайте товар заново "
                 "и сохраните шаблон ещё раз.", buttons=MENU)
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
    draft.description = template.description
    draft.game = template.game
    draft.category = template.category
    draft.obtaining = template.obtaining
    draft.fields = template.fields
    draft.options = template.options
    draft.photos = photos

    link.say(f"Повторяю:\n\n{draft.summary()}\n\nСоздаю черновик…")
    send_draft(link, account, draft)


def accounts_menu(link, account):
    """Показать кабинеты и переключить. → аккаунт для дальнейшей работы.

    Возвращает либо новый аккаунт, либо прежний: отказаться от
    переключения не должно означать остаться без кабинета.
    """
    store = AccountStore(ACCOUNTS_DIR)
    saved = store.all()
    current = store.current()

    keys = [[(("✅ " if current and a.id == current.id else "") + a.label(),
              PICK_ACCOUNT + a.id)] for a in saved]
    keys.append([("➕ Добавить кабинет", "добавить")])
    keys.append([("✖️ Назад", "отмена")])

    where = f"Сейчас: {current.name}\n\n" if current else ""
    answer = link.ask(where + "Кабинеты:", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if text == "добавить":
        return add_account(link, store, account)

    if not text.startswith(PICK_ACCOUNT):
        return account

    return switch_account(link, store, text[len(PICK_ACCOUNT):], account)


def switch_account(link, store, account_id: str, account):
    """Переключиться на сохранённый кабинет."""
    saved = store.get(account_id)

    if saved is None:
        link.say("Такого кабинета больше нет.", buttons=MENU)
        return account

    try:
        # Библиотека держит аккаунт синглтоном: этот вызов не создаёт
        # второй кабинет, а переписывает единственный. Поэтому переключение
        # именно заменяет, и две ссылки на разные кабинеты держать нельзя.
        fresh = open_account(saved.cookies,
                             saved.user_agent or os.environ.get(
                                 "PLAYEROK_UA", ""))
    except Exception as e:                                    # noqa: BLE001
        return offer_new_cookies(link, store, saved, account, e)

    store.set_current(account_id)
    link.say(f"Переключился: {saved.name}", buttons=MENU)

    return fresh


def offer_new_cookies(link, store, saved, account, why):
    """Куки кабинета не приняли — предложить прислать свежие.

    Без этого единственным выходом было бы завести кабинет заново, потеряв
    имя и порядок. А протухают куки чаще всего остального.
    """
    answer = link.ask(
        f"Войти в «{saved.name}» не вышло: {why}\n\n"
        "Обычно это значит, что куки протухли. Пришлёте свежие?",
        ANSWER_WAIT,
        buttons=[[("🔑 Прислать куки", PICK_FIX + saved.id)],
                 [("✖️ Не сейчас", "отмена")]])

    if not str(answer.get("text") or "").startswith(PICK_FIX):
        link.say("Оставил как есть.", buttons=MENU)
        return account

    answer = link.ask(
        f"Куки кабинета «{saved.name}» — строка, JSON или токен. "
        "Сообщение удалю сразу после прочтения.", ANSWER_WAIT,
        buttons=[CANCEL])
    cookies = normalize_cookies(str(answer.get("text") or ""))

    if not cookies:
        link.say("Это не похоже на куки. Оставил как есть.", buttons=MENU)
        return account

    if not answer.get("from_button"):
        link._delete(answer)

    store.update_cookies(saved.id, cookies)

    try:
        fresh = open_account(cookies, saved.user_agent
                             or os.environ.get("PLAYEROK_UA", ""))
    except Exception as e:                                    # noqa: BLE001
        link.say(f"И эти не подошли: {e}\n"
                 "Проверьте, что куки из того же браузера, чей user-agent "
                 "указан у кабинета.", buttons=MENU)
        return account

    store.set_current(saved.id)
    link.say(f"Готово, кабинет «{saved.name}» снова работает.", buttons=MENU)

    return fresh


def add_account(link, store, account):
    """Завести новый кабинет: имя, куки, user-agent."""
    answer = link.ask("Как назвать кабинет? Это имя увидите только вы.",
                      ANSWER_WAIT, buttons=[CANCEL])
    name = " ".join(str(answer.get("text") or "").split())

    if not name or wizard.cancelled(name):
        link.say("Отменил.", buttons=MENU)
        return account

    answer = link.ask(
        "Пришлите куки этого кабинета — строку «token=...», выгрузку "
        "расширения в JSON или сам токен. Сообщение я удалю сразу после "
        "прочтения.", ANSWER_WAIT, buttons=[CANCEL])
    raw = str(answer.get("text") or "")

    if wizard.cancelled(raw):
        link.say("Отменил.", buttons=MENU)
        return account

    cookies = normalize_cookies(raw)

    if not cookies:
        link.say("Это не похоже на куки. Начнём заново.", buttons=MENU)
        return account

    # Стираем сразу: в переписке остался бы доступ к кабинету.
    if not answer.get("from_button"):
        link._delete(answer)

    answer = link.ask(
        "User-agent браузера, из которого взяты эти куки.\n\n"
        "Площадка сверяет его с тем, при котором куки выданы: не совпадёт "
        "— вход сочтут чужим. Чтобы взять тот же, что сейчас — "
        "«пропустить».", ANSWER_WAIT, buttons=[SKIP])
    typed = str(answer.get("text") or "").strip()
    user_agent = ("" if wizard.skipped(typed) or wizard.cancelled(typed)
                  else typed)

    account_id = store.add(name, cookies,
                           user_agent or os.environ.get("PLAYEROK_UA", ""))
    link.say(f"Кабинет «{name}» добавлен.")

    return switch_account(link, store, account_id, account)


def try_sign_in(link):
    """Войти, не роняя бота. → аккаунт или None.

    Негодные куки НЕ должны останавливать бота: починить кабинет можно
    только из его же меню, и падение на старте запирало бы починку за тем,
    что сломалось. Продавцу осталась бы только консоль сервера — то есть
    ровно то, от чего мы уходили.
    """
    try:
        account, _store, _link = sign_in()
        return account
    except SystemExit as e:
        link.say(f"Войти в кабинет не вышло.\n\n{e}", buttons=MENU)
    except Exception as e:                                    # noqa: BLE001
        link.say(f"Войти в кабинет не вышло: {e}\n\n"
                 "Скорее всего протухли куки. Откройте «Аккаунт» и "
                 "пришлите свежие или переключитесь на другой кабинет.",
                 buttons=MENU)

    return None


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    link = link_from_env()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID — "
            "разговаривать не с кем.")

    account = try_sign_in(link)
    where = AccountStore(ACCOUNTS_DIR).current()

    if account is not None and where is not None:
        link.say(f"Кабинет: {where.name}")

    print("Жду в телеграме. Напишите боту «новый товар».")

    if account is not None:
        link.say("Готов.", buttons=MENU)

    while True:
        message = link.wait_answer(3600)
        text = str(message.get("text") or "").strip().lower()

        if not text:
            continue

        if text in START_WORDS or text in TEMPLATE_WORDS:
            if account is None:
                link.say("Сначала нужен рабочий кабинет: откройте "
                         "«Аккаунт».", buttons=MENU)
                continue

        if text in START_WORDS:
            make_item(link, account)
            link.say("Готов к следующему.", buttons=MENU)
        elif text in TEMPLATE_WORDS:
            from_template(link, account)
            link.say("Готов к следующему.", buttons=MENU)
        elif text in ACCOUNT_WORDS:
            account = accounts_menu(link, account)
        elif not wizard.cancelled(text):
            # Молчать нельзя: продавец решит, что бот умер.
            link.say("Что делаем?", buttons=MENU)

        time.sleep(0.2)


CHOOSE.update({"game": choose_game, "category": choose_category,
               "obtaining": choose_obtaining})


if __name__ == "__main__":
    main()
