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

АВТОВЫДАЧА. Кнопка «Автовыдача» показывает, что бот умеет выдавать сам:
включена ли карта, по какому слову он узнаёт ваши объявления и какими
услугами поставщика покупает. Настройки у каждого кабинета свои.

ПРОВЕРКА СЕССИИ. Кнопка «Проверить сессию» спрашивает у площадки, кто мы:
этот вызов первым и падает, когда куки протухли. Заодно показывает, не
заблокирован ли кабинет и разрешено ли выставлять товары.

ЧЕРНОВИКИ. Кнопка «Черновики» показывает несозданные объявления кабинета
и выставляет выбранное — с тем же вопросом про платный статус.

ШАБЛОНЫ У КАЖДОГО КАБИНЕТА СВОИ: товары у разных кабинетов разные, и
перемешанные шаблоны означают объявление, созданное не там, где хотели.
Шаблон можно повторить, изменить по одному полю или удалить.

ВХОД. Кабинет добавляется двумя путями: кодом на почту — бот получает
сессию сам, браузер и расширения не нужны — либо куками из браузера, как
раньше. Тем же кодом чинится и кабинет, у которого сессия истекла.

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

import copy
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from envfile import load_env_file                             # noqa: E402
from owner import link_from_env                               # noqa: E402
import listing                                                # noqa: E402
import oneshot                                                # noqa: E402
import pricing                                                # noqa: E402
import series                                                 # noqa: E402
import wizard                                                 # noqa: E402
from accounts import AccountStore                             # noqa: E402
from auth import open_account, sign_in                        # noqa: E402
from cards import CARDS, card_by_slug                         # noqa: E402
from catalog import (card_for_title, denominations_for,       # noqa: E402
                     nominal_from_title, render)
import emailauth                                              # noqa: E402
from alarm import COOKIES_ADVICE                              # noqa: E402
from owner import normalize_cookies                           # noqa: E402
from playerok import is_auth_error                            # noqa: E402
from settings import REGIONS, Settings                        # noqa: E402
from store import STATE_DONE, JsonStore                       # noqa: E402
from templates import TemplateStore, folder_for               # noqa: E402

# Кнопки, которые повторяются. Подписи для человека, значения — те же
# слова, что понимает разбор ответов: нажатие и набранный текст должны
# приходить в один и тот же разбор.
MENU = [[("➕ Новый товар", "новый товар"),
         ("📝 Одним сообщением", "бланк")],
        [("⚡ Из шаблона", "шаблон"),
         ("📊 Серия номиналов", "серия")],
        [("📄 Черновики", "черновики")],
        [("⚙️ Автовыдача", "настройки")],
        [("🔑 Проверить сессию", "проверить")],
        [("👤 Аккаунт", "аккаунт")]]

# Команды в меню Telegram — та кнопка слева от поля ввода. Без неё их
# надо помнить и набирать вслепую.
COMMANDS = [
    ("start", "Меню"),
    ("new", "Новый товар"),
    ("blank", "Одним сообщением"),
    ("tpl", "Создать из шаблона"),
    ("series", "Серия номиналов из шаблона"),
    ("drafts", "Черновики"),
    ("delivery", "Настройки автовыдачи"),
    ("check", "Проверить сессию"),
    ("account", "Кабинеты"),
]
CANCEL = [("✖️ Отмена", "отмена")]
# Ряды кнопок, а не список регионов: коды берём из settings.REGIONS, чтобы
# бот и движок выдачи не разъехались.
#
# Кнопками — ходовые; остальные регионы продавец вводит текстом, и мастер
# понимает их и словом («Турция»), и кодом. Два десятка кнопок на экран
# телефона не помещаются, а торгующему одним регионом они и не нужны.
REGION_BUTTONS = [("🌍 GL", "GL"), ("🇷🇺 RU", "RU"), ("🇺🇸 US", "US")]
MORE_REGIONS = [("🇹🇷 TR", "TR"), ("🇪🇺 EU", "EU"), ("🇦🇷 AR", "AR"),
                ("🇧🇷 BR", "BR")]
SKIP = [("⏭ Пропустить", "пропустить"), ("✖️ Отмена", "отмена")]
PHOTOS_DONE = [("✅ Готово", wizard.DONE_WORD), ("✖️ Отмена", "отмена")]

# Ничего про категории здесь не зашито намеренно. Один зашитый id уже
# привёл к тому, что все товары создавались в чужой категории, и заметить
# это можно было только глазами в кабинете.

# Сколько вариантов показывать кнопками за раз. Больше — и список
# перестаёт помещаться на экране телефона.
MAX_CHOICES = 12

START_WORDS = ("новый товар", "новый", "/new", "/newitem")
MENU_WORDS = ("/start", "старт", "меню", "/menu")
CHECK_WORDS = ("проверить", "проверить сессию", "/check", "сессия")
TEMPLATE_WORDS = ("шаблон", "шаблоны", "из шаблона", "/tpl")
ACCOUNT_WORDS = ("аккаунт", "аккаунты", "кабинет", "/account")
DRAFT_WORDS = ("черновики", "черновик", "/drafts")
SETTINGS_WORDS = ("настройки", "автовыдача", "/delivery")
BLANK_WORDS = ("бланк", "одним сообщением", "одно сообщение", "/blank")
SERIES_WORDS = ("серия", "серия номиналов", "номиналы", "/series")

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
PICK_DRAFT = "drf:"
PICK_WAY = "way:"
PICK_CARD = "crd:"
PICK_SET = "set:"
PICK_SERIES = "ser:"

# Где лежит состояние выдачи: и настройки, и журнал выданных заказов. На
# кабинет: товары и слова-опознаватели у разных кабинетов разные, а
# смешать журналы двух кабинетов означало бы не выдать оплаченный заказ.
SETTINGS_DIR = os.environ.get("PLAYEROK_STATE", "state/delivery")

# User-agent, которым бот входит и работает. Раз сессию выдаём мы сами,
# он наш: площадка сверяет его с тем, при котором сессия выдана, и
# разойтись они не должны.
DEFAULT_UA = os.environ.get(
    "PLAYEROK_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")

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


def screen_text(draft, question: str, complaint: str = "") -> str:
    """Экран диалога: что собрано, что не так и что спрашиваем.

    Всё одним сообщением: диалог из десятка шагов, каждый из которых новое
    сообщение, превращает переписку в простыню, где не найти ни меню, ни
    собственных ответов.
    """
    done = wizard.progress(draft)

    return "\n\n".join(p for p in (done, complaint, question) if p)


def buttons_for(step: str):
    """Кнопки под вопрос. Там, где ответ свободный, кнопок нет.

    Название и цену кнопкой не выберешь, но «отмена» нужна на каждом шаге:
    передумать посреди опроса — обычное дело.
    """
    if step == "region":
        return [REGION_BUTTONS, MORE_REGIONS, CANCEL]

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
    answer = link.ask(screen_text(draft, wizard.question_for(draft)),
                      ANSWER_WAIT, buttons=[CANCEL])
    search = str(answer.get("text") or "").strip()

    if not search or wizard.cancelled(search):
        return False

    try:
        page = account.get_games(name=search, count=MAX_CHOICES)
        games = list(getattr(page, "games", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Поиск не удался: {e}")
        return True

    if not games:
        link.screen(f"По запросу «{search}» ничего не нашлось. Попробуйте "
                 "короче.")
        return True

    chosen = choose(link, screen_text(draft, "Что из этого?"),
                    [(g.id, g.name) for g in games], PICK_GAME)

    if chosen is None:
        link.screen("Отменил.", buttons=MENU)
        return False

    draft.game = chosen

    return True


def choose_category(link, account, draft) -> bool:
    """Спросить категорию выбранной игры. → продолжать ли."""
    try:
        game = account.get_game(id=draft.game["id"])
        rows = list(getattr(game, "categories", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Категории прочитать не вышло: {e}")
        return False

    if not rows:
        link.screen("У этой игры нет категорий — товар создать негде.",
                 buttons=MENU)
        return False

    chosen = choose(link, screen_text(draft, wizard.question_for(draft)),
                    [(c.id, c.name) for c in rows], PICK_CATEGORY)

    if chosen is None:
        link.screen("Отменил.", buttons=MENU)
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
        link.screen(f"Способы получения прочитать не вышло: {e}")
        return False

    if not rows:
        link.screen("У этой категории нет способов получения.", buttons=MENU)
        return False

    chosen = choose(link, screen_text(draft, wizard.question_for(draft)),
                    [(o.id, o.name) for o in rows], PICK_OBTAINING)

    if chosen is None:
        link.screen("Отменил.", buttons=MENU)
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
    answer = link.ask(screen_text(draft, f"{title}?" if not title.endswith(".")
                                  else title), ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_OPTION):
        link.screen("Отменил.", buttons=MENU)
        return False

    number = text[len(PICK_OPTION):]

    if not number.isdigit() or int(number) >= len(choices):
        link.screen("Не понял выбор.")
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

        message = link.ask(
            screen_text(draft, wizard.question_for(draft), complaint),
            ANSWER_WAIT, buttons=buttons_for(step))
        complaint = ""

        if not message:
            link.screen("Не дождался ответа. Начнём заново, когда будете "
                     "готовы.", buttons=MENU)
            return False

        text = str(message.get("text") or "")

        if wizard.cancelled(text):
            link.screen("Отменил. Ничего не создано.", buttons=MENU)
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
            link.screen("Отменил. Ничего не создано.", buttons=MENU)
            return False

        if wizard.enough_photos(text):
            if draft.photos:
                return True

            link.screen(screen_text(
                draft, wizard.question_for(draft),
                "Пока ни одной фотографии. Хотя бы одна обязательна — без "
                "картинок площадка товар не принимает."),
                buttons=[PHOTOS_DONE])
        else:
            file_id = photo_id(message)

            if not file_id:
                link.screen(screen_text(
                    draft, wizard.question_for(draft),
                    "Это не фотография. Пришлите картинку, а когда хватит "
                    "— нажмите «Готово»."), buttons=[PHOTOS_DONE])
            else:
                data = link.download(file_id)

                if not data:
                    link.screen(screen_text(
                        draft, wizard.question_for(draft),
                        "Забрать картинку не вышло. Пришлите ещё раз."),
                        buttons=[PHOTOS_DONE])
                else:
                    draft.photos.append(data)
                    link.screen(screen_text(
                        draft, "Ещё одну или заканчиваем?"),
                        buttons=[PHOTOS_DONE])

        message = link.wait_answer(ANSWER_WAIT)

        if not message:
            link.screen("Не дождался. Начнём заново.", buttons=MENU)
            return False

        if not message.get("from_button"):
            # Присланная фотография тоже уезжает: экран должен оставаться
            # последним, иначе кнопки теряются в середине переписки.
            link._delete(message)


def publish_step(link, account, item_id: str, price: int) -> None:
    """Спросить про выставление и выставить, если согласились."""
    try:
        statuses = account.get_item_priority_statuses(item_id, price)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Статусы приоритета прочитать не вышло: {e}\n"
                 "Товар остался черновиком, выставьте его в кабинете.")
        return

    rows = listing.ordered(statuses or [])

    if not rows:
        link.screen("Статусов приоритета нет — выставить нечем. "
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
        link.screen("Оставил черновиком. Выставить можно в кабинете.")
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
            link.screen("Не подтверждено. Оставил черновиком.")
            return

    try:
        account.publish_item(item_id, chosen.id)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Выставить не вышло: {e}\n"
                 "Черновик при этом цел и виден в кабинете.")
        return

    link.forget_screen()
    link.say(f"Выставлено: {listing.describe(chosen)}")


def make_item(link, account) -> None:
    """Один проход: опрос, черновик, выставление."""
    draft = wizard.Draft()
    link.screen("Создаём товар. В любой момент — «Отмена».")

    if not collect(link, account, draft):
        return

    apply_card_template(draft)

    link.screen("Проверьте:\n\n" + draft.summary()
             + "\n\n— описание —\n" + wizard.description_for(draft)
             + "\n\nСоздаю черновик…")

    if not send_draft(link, account, draft):
        return

    offer_template(link, draft)


def apply_card_template(draft: wizard.Draft) -> None:
    """Подставить описание узнанной карты, если продавец своё не писал.

    Узнаём карту по названию товара — тому же, по которому потом узнаёт
    заказ выдача. Одно описание на все карты давало бы объявлению Apple
    строку «Активация: roblox.com/redeem»: у каждой карты активация своя.

    Заготовка продавца сильнее нашей: он её для того и задавал.
    """
    card = card_for_title(CARDS, draft.name)

    if card is None:
        return

    saved = settings_of().card(card.slug)
    template = saved.get("ad_text") or card.ad_text or card.activation

    draft.tail = render(template, card, draft.nominal,
                        draft.region, draft.price)


def make_blank(link, account) -> None:
    """Товар одним сообщением: заготовка → ответ → картинки → черновик.

    Выбор на площадке всё равно кнопками: игру и категорию надо искать в
    её справочнике, а состав полей известен только после категории — из
    неё и собирается заготовка. Зато дальше один обмен вместо шести.
    """
    draft = wizard.Draft()
    link.screen("Создаём одним сообщением. Сначала — куда.")

    for step in ("game", "category", "obtaining"):
        if not CHOOSE[step](link, account, draft):
            return

    # Характеристики кнопками: площадка предлагает свой список, и вписать
    # их текстом нельзя — она принимает только свои значения.
    while draft.step.startswith(wizard.OPTION):
        if not choose_option(link, account, draft):
            return

    labels = [str(f.get("label") or "") for f in draft.fields]
    # Карту узнаём по игре, а не по категории: категория называется
    # «Игровая валюта», и такое имя не скажет ничего.
    card = card_for_title(CARDS, str(draft.game.get("name") or ""))
    form = oneshot.blank(labels, card)

    # Заготовка отдельным сообщением: её продавец копирует целиком, а
    # экран под ней перепишется следующим вопросом.
    link.forget_screen()
    link.say(form)

    answer = link.ask(
        "Скопируйте это сообщение, впишите своё и пришлите одним ответом.\n\n"
        "Порядок строк любой, лишние можно удалить. Описание можно в "
        "несколько строк — всё, что ниже «Описание:», попадёт в него.",
        ANSWER_WAIT, buttons=[CANCEL])
    text = str(answer.get("text") or "")

    if not text.strip() or wizard.cancelled(text):
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return

    if not apply_blank(link, draft, text):
        return

    if not ask_photos(link, draft):
        return

    apply_card_template(draft)
    link.screen("Проверьте:\n\n" + draft.summary()
                + "\n\n— описание —\n" + wizard.description_for(draft)
                + "\n\nСоздаю черновик…")

    if send_draft(link, account, draft):
        offer_template(link, draft)


def apply_blank(link, draft: wizard.Draft, text: str) -> bool:
    """Разложить присланное по черновику. → можно ли продолжать."""
    labels = [str(f.get("label") or "") for f in draft.fields]
    values = oneshot.parse(text, labels)
    complaints = []

    for key, value in values.items():
        if key.startswith("field:"):
            field = next((f for f in draft.fields
                          if str(f.get("label") or "") == key[6:]), None)

            if field is not None:
                field["value"] = " ".join(str(value).split())

            continue

        why = wizard.accept_one(draft, key, value)

        if why:
            complaints.append(f"{oneshot.KEYS[key][0]}: {why}")

    # Пустые необязательные поля надо закрыть, иначе мастер будет их ждать.
    for field in draft.fields:
        if field.get("value") is None and not field.get("required"):
            field["value"] = ""

    if draft.description is None:
        draft.description = ""

    gaps = oneshot.missing(values, draft.fields)

    if gaps:
        complaints.append("не хватает: " + ", ".join(gaps))

    if not draft.nominal:
        # Не отказ: номинал нужен выдаче, а не площадке. Но сказать надо —
        # без него бот при оплате остановится, и узнает продавец об этом,
        # когда покупатель уже заплатит.
        complaints.append(
            "номинала нет ни в названии, ни строкой «Номинал» — "
            "автовыдача по такому товару работать не будет")

    if complaints:
        # Показываем всё разом, а не по одной: исправлять придётся в том же
        # сообщении, и знать надо про все промахи сразу.
        answer = link.ask(
            "Прочитал так:\n\n" + draft.summary() + "\n\n⚠️ "
            + "\n⚠️ ".join(complaints)
            + "\n\nПришлите исправленное сообщение целиком — или "
              "«продолжить», если так и задумано.",
            ANSWER_WAIT,
            buttons=[[("▶️ Продолжить", "продолжить")], CANCEL])
        again = str(answer.get("text") or "")

        if wizard.cancelled(again):
            link.screen("Отменил. Ничего не создано.", buttons=MENU)
            return False

        if again.strip().lower() != "продолжить":
            return apply_blank(link, draft, again)

        if oneshot.missing(values, draft.fields):
            link.screen("Без названия и цены товар не создать.", buttons=MENU)
            return False

    return True


def ask_photos(link, draft: wizard.Draft) -> bool:
    """Собрать картинки. → продолжать ли."""
    message = link.ask(
        "Теперь фотографии — по одной. Когда хватит, нажмите «Готово».\n\n"
        "Хотя бы одна обязательна: без картинок площадка товар не "
        "принимает.", ANSWER_WAIT, buttons=[PHOTOS_DONE])

    if not message:
        link.screen("Не дождался. Начнём заново, когда будете готовы.",
                    buttons=MENU)
        return False

    return collect_photos(link, draft, message)


def send_draft(link, account, draft: wizard.Draft) -> bool:
    """Создать черновик и спросить про выставление. → получилось ли."""
    item_id, why = create_item(account, draft)

    if not item_id:
        # Отказ во входе лечится не повтором, а свежими куками — и сказать
        # об этом надо прямо здесь, иначе продавец будет жать «ещё раз».
        if is_auth_error_text(why):
            link.screen(f"Создать не вышло: площадка не приняла вход.\n\n"
                        f"{COOKIES_ADVICE}", buttons=MENU)
        else:
            link.screen(f"Создать не вышло: {why}\n"
                        "Ничего не потрачено. Попробуем ещё раз.",
                        buttons=MENU)

        return False

    # Ссылка на товар остаётся в переписке отдельным сообщением: её
    # открывают потом, а экран к тому времени перепишется.
    link.forget_screen()
    link.say(f"Черновик создан.\nhttps://playerok.com/products/{item_id}")
    publish_step(link, account, item_id, draft.price)

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
        link.screen(f"Сохранить шаблон не вышло: {e}")
        return

    link.screen("Шаблон сохранён.")


def from_template(link, account) -> None:
    """Список шаблонов кабинета: повторить, изменить или удалить."""
    store = templates_of()
    saved = store.all()

    if not saved:
        link.screen("У этого кабинета шаблонов пока нет. Создайте товар и "
                 "сохраните его шаблоном — дальше он будет создаваться "
                 "одним нажатием.", buttons=MENU)
        return

    keys = [[(t.label(), PICK + t.id)] for t in saved]
    keys.append([("✖️ Отмена", "отмена")])
    answer = link.ask("Шаблоны этого кабинета:", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK):
        link.screen("Отменил.", buttons=MENU)
        return

    template_id = text[len(PICK):]
    template = store.get(template_id)

    if template is None:
        link.screen("Такого шаблона больше нет.", buttons=MENU)
        return

    answer = link.ask(
        f"{template.label()}\n\nЧто делаем?", ANSWER_WAIT,
        buttons=[[("⚡ Создать товар", PICK_ACT + "make")],
                 [("✏️ Изменить", PICK_ACT + "edit")],
                 [("🗑 Удалить", PICK_ACT + "drop")],
                 [("✖️ Назад", "отмена")]])
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_ACT):
        link.screen("Отменил.", buttons=MENU)
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
        link.screen("Оставил.", buttons=MENU)
        return

    if store.remove(template.id):
        link.screen("Удалил.", buttons=MENU)
    else:
        link.screen("Удалить не вышло.", buttons=MENU)


def edit_template(link, store, template_id: str) -> None:
    """Поправить одно поле шаблона."""
    template = store.get(template_id)

    if template is None:
        link.screen("Такого шаблона больше нет.", buttons=MENU)
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
        link.screen("Отменил.", buttons=MENU)
        return

    what = what[len(PICK_ACT):]

    if what == "photos":
        edit_photos(link, store, template_id)
        return

    if what == "region":
        answer = link.ask("Новый регион:", ANSWER_WAIT,
                          buttons=[REGION_BUTTONS, MORE_REGIONS,
                                   CANCEL])
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
        link.screen("Отменил.", buttons=MENU)
        return

    if why:
        link.screen(why + "\n\nОставил как было.", buttons=MENU)
        return

    if store.update(template_id, **{what: value}):
        link.screen("Поправил.", buttons=MENU)
    else:
        link.screen("Сохранить не вышло.", buttons=MENU)


def edit_photos(link, store, template_id: str) -> None:
    """Заменить картинки шаблона целиком."""
    draft = wizard.Draft()
    link.screen("Пришлите новые фотографии — они заменят прежние целиком.",
             buttons=[PHOTOS_DONE])
    message = link.wait_answer(ANSWER_WAIT)

    if not message:
        link.screen("Не дождался.", buttons=MENU)
        return

    if not collect_photos(link, draft, message):
        return

    if store.set_photos(template_id, draft.photos):
        link.screen(f"Заменил. Теперь фотографий: {len(draft.photos)}.",
                 buttons=MENU)
    else:
        link.screen("Сохранить не вышло.", buttons=MENU)


def make_from_template(link, account, store, template_id: str) -> None:
    """Повторить сохранённое объявление одним нажатием."""
    template = store.get(template_id)

    if template is None:
        link.screen("Такого шаблона больше нет.", buttons=MENU)
        return

    if not template.complete():
        # Шаблоны, сохранённые до того, как бот научился спрашивать
        # категорию, повторять нечем: товар ушёл бы не туда.
        link.screen("Этот шаблон сохранён до того, как бот стал спрашивать "
                 "категорию, и повторить его нечем — создайте товар заново "
                 "и сохраните шаблон ещё раз.", buttons=MENU)
        return

    photos = template.photos()

    if not photos:
        link.screen("У шаблона пропали картинки — без них товар не создать. "
                 "Соберите объявление заново.", buttons=MENU)
        return

    draft = wizard.Draft()
    draft.name = template.name
    draft.price = template.price
    draft.nominal = nominal_from_title(template.name) or 0.0
    draft.region = template.region
    draft.description = template.description
    draft.game = template.game
    draft.category = template.category
    draft.obtaining = template.obtaining
    draft.fields = template.fields
    draft.options = template.options
    draft.photos = photos

    link.screen(f"Повторяю:\n\n{draft.summary()}\n\nСоздаю черновик…")
    send_draft(link, account, draft)


def series_menu(link, account) -> None:
    """Серия объявлений из одного шаблона: остальные номиналы."""
    store = templates_of()
    saved = store.all()

    if not saved:
        link.screen(
            "Шаблонов пока нет.\n\nСоздайте одно объявление на любой "
            "номинал, сохраните его шаблоном — и отсюда я выставлю "
            "остальные номиналы, меняя только число и цену.", buttons=MENU)
        return

    keys = [[(t.label(), PICK_SERIES + t.id)] for t in saved[:MAX_CHOICES]]
    keys.append([("✖️ Назад", "отмена")])
    answer = link.ask("С какого объявления делаем серию?", ANSWER_WAIT,
                      buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_SERIES):
        link.screen("Отменил.", buttons=MENU)
        return

    make_series(link, account, store, text[len(PICK_SERIES):])


# Каталог поставщика читается не чаще двух раз в минуту, а ответ — больше
# тысячи услуг. Держим прочитанное на два экрана подряд: продавец, нажавший
# «взять номиналы» дважды, иначе выберет лимит сам себе.
_CATALOG: dict = {"at": 0.0, "raw": None}
CATALOG_TTL = 120.0


def supplier_catalog():
    """Каталог поставщика → (каталог, причина отказа)."""
    key = os.environ.get("APPROUTE_KEY", "").strip()

    if not key:
        return None, ("в .env нет APPROUTE_KEY — это ключ из кабинета "
                      "AppRoute. Без него номиналы взять неоткуда")

    if _CATALOG["raw"] is not None \
            and time.time() - _CATALOG["at"] < CATALOG_TTL:
        return _CATALOG["raw"], ""

    try:
        from supplier import ApprouteSupplier
    except ImportError as e:                                  # noqa: BLE001
        return None, f"не подключается клиент поставщика: {e}"

    try:
        raw = ApprouteSupplier(
            api_key=key,
            proxy=os.environ.get("APPROUTE_PROXY", "")).services()
    except Exception as e:                                    # noqa: BLE001
        return None, str(e)

    _CATALOG["raw"], _CATALOG["at"] = raw, time.time()

    return raw, ""


def supplier_nominals(link, card, region: str):
    """Номиналы карты у поставщика → пары (номинал, закупка) или None."""
    link.screen("Читаю каталог поставщика…")
    catalog, why = supplier_catalog()

    if catalog is None:
        link.screen(f"Каталог не прочитан: {why}", buttons=MENU)
        return None

    rows = [r for r in denominations_for(card, catalog)
            if not r.region or not region or r.region == region.upper()]
    costs = pricing.costs_from(rows)

    if not costs:
        link.screen(
            f"У поставщика нет номиналов «{card.title}» в наличии"
            + (f" для региона {region}" if region else "")
            + ".\n\nПроверьте подкатегорию на экране карты: "
              f"«{card.subcategory}».", buttons=MENU)
        return None

    return costs


def make_series(link, account, store, template_id: str) -> None:
    """Собрать номиналы с ценами, показать план, создать по согласию."""
    template = store.get(template_id)

    if template is None:
        link.screen("Такого шаблона больше нет.", buttons=MENU)
        return

    if not template.complete():
        link.screen("Этот шаблон сохранён до того, как бот стал спрашивать "
                    "категорию, и повторить его нечем.", buttons=MENU)
        return

    photos = template.photos()

    if not photos:
        link.screen("У шаблона пропали картинки — без них товар не создать.",
                    buttons=MENU)
        return

    old = nominal_from_title(template.name)

    if not old:
        link.screen(
            f"В названии «{template.name}» нет числа, а серия строится "
            f"заменой числа на новое. Переименуйте шаблон так, чтобы "
            f"номинал был в названии.", buttons=MENU)
        return

    card = card_for_title(CARDS, template.name) \
        or card_for_title(CARDS, str((template.game or {}).get("name") or ""))

    text = ask_series_rows(link, template, card, old)

    if text is None:
        return

    rows, bad = series.parse(text)
    jobs, refused = series.plan(template.name, template.description, old, rows)

    if not jobs:
        link.screen("Создавать нечего.\n\n"
                    + "\n".join(bad + refused), buttons=MENU)
        return

    count = plural(len(jobs), "объявление", "объявления", "объявлений")
    lines = [f"Создам {count}:", ""]
    lines += [f"• {j['name']} — {j['price']} ₽" for j in jobs]

    if bad or refused:
        # Пропущенное показываем здесь же: молча пропустить строку значит
        # оставить продавца без объявления, которого он ждал.
        lines += ["", "Пропущу:"] + [f"• {b}" for b in bad + refused]

    answer = link.ask("\n".join(lines) + "\n\nСоздаём?", ANSWER_WAIT,
                      buttons=[[("✅ Да, создать", "да")], CANCEL])

    if str(answer.get("text") or "").strip().lower() != "да":
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return

    run_series(link, account, template, photos, jobs)


def ask_series_rows(link, template, card, old: float):
    """Получить строки «номинал = цена». → текст или None, если отменили.

    Номиналы у поставщика уже перечислены — переписывать их руками
    незачем. Решает продавец только цену, её одну и вводит.
    """
    head = (f"Образец: «{template.name}» — номинал {old:g}, цена "
            f"{template.price} ₽.\n\n")
    keys = [[("📥 Взять номиналы у поставщика", "взять")],
            [("✍️ Вписать самому", "сам")], CANCEL]

    if card is None:
        # Карта не узнана — брать номиналы неоткуда: подкатегория живёт в
        # карте. Остаётся ручной ввод, и незачем предлагать несбыточное.
        answer = link.ask(
            head + "Пришлите номиналы с ценами, по одному в строке:\n\n"
                   "200 = 140\n400 = 280\n800 = 560",
            ANSWER_WAIT, buttons=[CANCEL])

        return _series_text(link, answer)

    answer = link.ask(head + "Откуда взять номиналы?", ANSWER_WAIT,
                      buttons=keys)
    chosen = str(answer.get("text") or "").strip().lower()

    if wizard.cancelled(chosen):
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return None

    if chosen != "взять":
        answer = link.ask(
            "Пришлите номиналы с ценами, по одному в строке:\n\n"
            "200 = 140\n400 = 280\n800 = 560", ANSWER_WAIT, buttons=[CANCEL])

        return _series_text(link, answer)

    costs = supplier_nominals(link, card, template.region)

    if costs is None:
        return None

    return ask_prices(link, card, template, costs)


def ask_prices(link, card, template, costs: list):
    """Показать номиналы поставщика и получить цены. → текст или None."""
    answer = link.ask(
        f"У поставщика {plural(len(costs), 'номинал', 'номинала', 'номиналов')}"
        f" «{card.title}»"
        + (f" ({template.region})" if template.region else "")
        + ".\n\nЦены посчитать от закупки или впишете сами?",
        ANSWER_WAIT,
        buttons=[[("💱 Посчитать от закупки", "посчитать")],
                 [("✍️ Вписать цены самому", "сам")], CANCEL])
    chosen = str(answer.get("text") or "").strip().lower()

    if wizard.cancelled(chosen):
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return None

    rows = [(nominal, None) for nominal, _ in costs]

    if chosen == "посчитать":
        rows = ask_markup(link, costs)

        if rows is None:
            return None

    # Список отдельным сообщением: продавец копирует его целиком, правит у
    # себя и присылает обратно. Экран под ним перепишется вопросом.
    link.forget_screen()
    link.say(pricing.sheet(rows))

    answer = link.ask(
        "Скопируйте список, поставьте свои цены в рублях и пришлите "
        "обратно.\n\n"
        "Лишние номиналы удалите — объявления по ним не появятся.",
        ANSWER_WAIT, buttons=[CANCEL])

    return _series_text(link, answer)


def ask_markup(link, costs: list):
    """Курс и наценка → посчитанные цены. → пары или None."""
    answer = link.ask(
        "Курс рубля к доллару — сколько рублей за 1 $.\n\n"
        "Закупка у поставщика в долларах, а цена на витрине в рублях. "
        "Курс нигде не сохраняется: он меняется, и вчерашний хуже, чем "
        "спрошенный сегодня.", ANSWER_WAIT, buttons=[CANCEL])
    rate, why = wizard.accept_number(str(answer.get("text") or ""))

    if why:
        link.screen(f"{why}\n\nНачнём сначала, когда будете готовы.",
                    buttons=MENU)
        return None

    answer = link.ask(
        "Наценка в процентах.\n\n"
        "40 значит «дороже закупки в 1.4 раза». Комиссия площадки сюда не "
        "входит — заложите её сами.", ANSWER_WAIT, buttons=[CANCEL])
    markup, why = wizard.accept_number(str(answer.get("text") or ""),
                                       allow_zero=True)

    if why:
        link.screen(f"{why}\n\nНачнём сначала, когда будете готовы.",
                    buttons=MENU)
        return None

    # Округляем вверх до десятки: цены вида «537 ₽» на витрине выглядят
    # случайными, а вниз округлять — это продавать дешевле задуманного.
    return pricing.suggest(costs, rate, markup, step=10)


def _series_text(link, answer):
    """Ответ со списком → текст или None, если отменили."""
    text = str(answer.get("text") or "")

    if not text.strip() or wizard.cancelled(text):
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return None

    return text


def run_series(link, account, template, photos, jobs) -> None:
    """Создать объявления по плану, показывая ход одним экраном."""
    done, failed = [], []

    for number, job in enumerate(jobs, start=1):
        link.screen(f"Создаю {number} из {len(jobs)}: {job['name']}…")

        draft = wizard.Draft()
        draft.name = job["name"]
        draft.price = job["price"]
        draft.nominal = job["nominal"]
        draft.region = template.region
        draft.description = job["description"]
        draft.game = template.game
        draft.category = template.category
        draft.obtaining = template.obtaining
        # Своя копия полей и характеристик на каждое объявление: один
        # список на все означал бы, что правка в третьем меняет первое.
        draft.fields = copy.deepcopy(template.fields)
        draft.options = copy.deepcopy(template.options)
        draft.photos = list(photos)

        item_id, why = create_item(account, draft)

        if item_id:
            done.append((job["name"], item_id))
        else:
            failed.append(f"{job['name']}: {why}")

            if is_auth_error_text(why):
                # Дальше пойдут те же отказы: площадка не приняла вход, и
                # каждая следующая попытка — это минута ожидания впустую.
                failed.append("остальные не пробовал — сначала вход")
                break

    lines = [f"Готово: {len(done)} из {len(jobs)}.", ""]
    lines += [f"✅ {name}\nhttps://playerok.com/products/{item_id}"
              for name, item_id in done]

    if failed:
        lines += ["", "Не получилось:"] + [f"⚠️ {f}" for f in failed]

    # Ссылки отдельным сообщением: их открывают потом, а экран перепишется.
    link.forget_screen()
    link.say("\n".join(lines))
    link.screen("Черновики созданы. Выставить их — «📄 Черновики».",
                buttons=MENU)


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 объявление», «2 объявления», «5 объявлений»."""
    tail, hundred = count % 10, count % 100

    if tail == 1 and hundred != 11:
        word = one
    elif 2 <= tail <= 4 and not 12 <= hundred <= 14:
        word = few
    else:
        word = many

    return f"{count} {word}"


def create_item(account, draft: wizard.Draft):
    """Создать черновик → (номер товара, причина отказа)."""
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
        return "", str(e)

    return str(item.id), ""


def is_auth_error_text(why: str) -> bool:
    """Отказ во входе, узнанный по тексту уже пойманной ошибки."""
    return is_auth_error(Exception(str(why)))


def settings_of() -> Settings:
    """Настройки автовыдачи текущего кабинета.

    Поверх того самого файла состояния, который читает движок выдачи: свой
    файл настроек стал бы вторым источником правды, и получилось бы
    «поменял в боте, а выдача по-старому».
    """
    current = AccountStore(ACCOUNTS_DIR).current()
    name = current.id if current else "default"

    return Settings(JsonStore(os.path.join(SETTINGS_DIR, f"{name}.json")))


def settings_menu(link) -> None:
    """Что бот умеет выдавать сам. Список карт."""
    conf = settings_of()
    keys = []
    row = []

    for card in CARDS:
        ok, why = conf.ready(card.slug, card)
        mark = "✅" if ok and not why else ("⚠️" if ok else "⛔")
        row.append((f"{mark} {card.emoji} {card.title}",
                    PICK_CARD + card.slug))

        # По две в ряд: тринадцать кнопок в столбик не помещаются на экран
        # телефона, а листать список настроек продавцу незачем.
        if len(row) == 2:
            keys.append(row)
            row = []

    if row:
        keys.append(row)

    keys.append([("✖️ Назад", "отмена")])
    answer = link.ask(
        "Автовыдача кодов.\n\n"
        "✅ готово · ⚠️ настроено не до конца · ⛔ выключено\n\n"
        "Выключенная карта не тратит ни копейки: бот к ней не подходит.",
        ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "")

    if not text.startswith(PICK_CARD):
        link.screen("Отменил.", buttons=MENU)
        return

    card_menu(link, conf, text[len(PICK_CARD):])


def card_menu(link, conf, slug: str) -> None:
    """Экран одной карты: включить, настроить, посмотреть журнал."""
    card = card_by_slug(slug)

    if card is None:
        link.screen("Такого товара нет.", buttons=MENU)
        return

    saved = conf.card(slug)
    ok, why = conf.ready(slug, card)
    lines = [f"{card.emoji} {card.title}"]

    if card.pitch:
        lines += ["", card.pitch]

    lines += ["", f"Выдача: {'включена' if saved['enabled'] else 'выключена'}"]

    if card.subcategory:
        lines.append(f"Ищем у поставщика: {card.subcategory}")

    # Честность про разбор номиналов. «Мерено» значит, что все названия
    # семейства разобрались на живом каталоге, а не «похоже, разберутся».
    lines.append("Разбор номиналов: "
                 + (f"мерен {card.measured}" if card.measured
                    else "не проверяли — прогоните вручную до включения"))

    if why:
        lines += ["", f"⚠️ {why}"]

    keys = [[(("⏸ Выключить" if saved["enabled"] else "▶️ Включить"),
              PICK_SET + "on"),
             ("⚙️ Настройки", PICK_SET + "cfg")],
            [("📜 Журнал выдач", PICK_SET + "log"),
             ("✖️ Назад", "отмена")]]

    answer = link.ask("\n".join(lines), ANSWER_WAIT, buttons=keys)
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_SET):
        link.screen("Готово.", buttons=MENU)
        return

    what = what[len(PICK_SET):]

    if what == "on":
        conf.set_enabled(slug, not saved["enabled"])
    elif what == "cfg":
        card_settings(link, conf, card)
        return
    elif what == "log":
        card_log(link, conf, card)
        return

    card_menu(link, conf, slug)


# Настройки карты: что спрашиваем и куда кладём ответ. Порядок — тот же,
# что на экране.
FIELDS = [
    ("region", "🌐 Регион", "set_region"),
    ("keyword", "🔤 Слово-опознаватель", "set_keyword"),
    ("greeting", "💬 Автоответ", "set_greeting"),
    ("note", "📝 Заметка", "set_note"),
    ("ad_title", "🏷 Название товара", "set_ad_title"),
    ("ad_text", "📄 Описание товара", "set_ad_text"),
]

# Подсказки. Каждая написана после того, как продавец понял настройку
# неправильно, — поэтому объяснение начинается с того, ГДЕ бот смотрит, и
# показывает пример на его же товаре.
HINTS = {
    "region": (
        "Регион кода — две буквы, как у поставщика (US, TR, AE…).\n\n"
        "Можно не задавать: тогда бот возьмёт регион из описания товара, "
        "где сам его и пишет. Настройка — запасная, для товаров, "
        "заведённых до того, как регион стали писать в описании."),
    "keyword": (
        "Бот смотрит на НАЗВАНИЕ ОБЪЯВЛЕНИЯ на витрине и решает, этой ли "
        "карте отдать заказ.\n\n"
        "Например, слово «эпл»:\n"
        "   ✅ «Эпл гифт карта 10$» — заберёт\n"
        "   ❌ «Apple Gift Card 10$» — пропустит, слова нет\n\n"
        "Пока слово не задано, бот узнаёт заказ по обычным написаниям "
        "названия карты. Задавать своё стоит, только если у вас есть другие "
        "товары с тем же словом — скажем, аккаунты, — и они попадают в "
        "выдачу по ошибке."),
    "greeting": (
        "Уходит в чат заказа сразу, как заказ взят в работу — ДО кода.\n\n"
        "Нужен не всегда: обычно код приходит через секунды, и «принял "
        "заказ» следом за ним выглядит лишним. Но у долгих номиналов "
        "поставщик отвечает «принято, код будет позже», и ожидание тянется "
        "минутами — вот там молчание похоже на поломку."),
    "note": "Заметка уйдёт покупателю вместе с кодом.",
    "ad_title": (
        "Заготовка названия для мастера создания товара.\n\n"
        "Подстановки: {номинал}, {регион}, {цена}, {карта}."),
    "ad_text": (
        "Заготовка описания для мастера создания товара.\n\n"
        "Подстановки: {номинал}, {регион}, {цена}, {карта}.\n\n"
        "⚠️ Ссылки площадка не принимает — отказ придёт на последнем шаге "
        "мастера."),
}


def card_settings(link, conf, card) -> None:
    """Шесть настроек карты. Точка очищает любую из них."""
    saved = conf.card(card.slug)
    lines = [f"{card.emoji} {card.title} — настройки", ""]

    for key, label, _ in FIELDS:
        value = saved.get(key) or ""
        lines.append(f"{label}: {shorten(value) if value else '— не задано'}")

    lines += ["", "Точка «.» очищает любое поле."]

    keys = []
    row = []

    for key, label, _ in FIELDS:
        row.append((label, PICK_SET + key))

        if len(row) == 2:
            keys.append(row)
            row = []

    if row:
        keys.append(row)

    keys.append([("🧾 Услуги вручную", PICK_SET + "svc"),
                 ("✖️ Назад", "отмена")])

    answer = link.ask("\n".join(lines), ANSWER_WAIT, buttons=keys)
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_SET):
        card_menu(link, conf, card.slug)
        return

    what = what[len(PICK_SET):]

    if what == "svc":
        services_menu(link, conf, card)
        return

    field = next((f for f in FIELDS if f[0] == what), None)

    if field is None:
        card_settings(link, conf, card)
        return

    key, label, setter = field
    answer = link.ask(
        f"{label}\n\n{HINTS[key]}\n\n"
        f"Сейчас: {shorten(saved.get(key) or '') or '— не задано'}",
        ANSWER_WAIT, buttons=[SKIP])
    value = str(answer.get("text") or "")

    # «Пропустить» и «отмена» — это «ничего не менять». Очистка — точка, и
    # только она: иначе выйти из экрана, не тронув настройку, было бы нечем.
    if not wizard.cancelled(value) and not wizard.skipped(value):
        getattr(conf, setter)(card.slug, value)

    card_settings(link, conf, card)


def services_menu(link, conf, card) -> None:
    """Ручная привязка карты к услугам поставщика.

    Обычно она не нужна: бот находит услуги по подкатегории сам. Экран
    остаётся на случай, когда поставщик переименовал подкатегорию или
    продавцу нужна конкретная услуга, а не всё семейство.
    """
    lines = [f"{card.emoji} {card.title} — услуги поставщика", ""]

    if card.subcategory:
        lines += [f"Обычно не нужно: бот сам берёт всё из подкатегории "
                  f"«{card.subcategory}».",
                  "Заданная здесь услуга сильнее — бот возьмёт только её.",
                  ""]

    for region in REGIONS:
        got = conf.service_id(card.slug, region)
        lines.append(f"{region}: {got or '— не задана'}")

    keys = [[(f"🧾 Услуга {region}", PICK_SET + "svc" + region)]
            for region in REGIONS]
    keys.append([("✖️ Назад", "отмена")])

    answer = link.ask("\n".join(lines), ANSWER_WAIT, buttons=keys)
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_SET + "svc"):
        card_settings(link, conf, card)
        return

    region = what[len(PICK_SET) + 3:]
    answer = link.ask(
        f"Номер услуги поставщика для региона {region}.\n\n"
        "Его показывает supplier_ids.py на сервере. Точка «.» уберёт "
        "привязку — бот вернётся к поиску по подкатегории.",
        ANSWER_WAIT, buttons=[SKIP])
    value = str(answer.get("text") or "")

    if not wizard.cancelled(value) and not wizard.skipped(value):
        conf.set_service(card.slug, region,
                         "" if value.strip() == "." else value)

    services_menu(link, conf, card)


def card_log(link, conf, card) -> None:
    """Последние выдачи карты — с причиной отказа, если он был."""
    rows = conf.store.conf(card.slug).get("log") or []
    lines = [f"{card.emoji} {card.title} — журнал выдач", ""]

    if not rows:
        lines.append("Пока пусто: по этой карте бот ещё ничего не выдавал.")

    for entry in rows[:10]:
        if not isinstance(entry, dict):
            continue

        mark = "✅" if entry.get("state") == STATE_DONE else "⚠️"
        line = (f"{mark} заказ {entry.get('order')} · "
                f"{entry.get('nominal') or '?'} · {entry.get('state')}")

        if entry.get("why"):
            line += f"\n    {entry['why']}"

        lines.append(line)

    link.ask("\n".join(lines), ANSWER_WAIT,
             buttons=[[("✖️ Назад", "отмена")]])
    card_menu(link, conf, card.slug)


def shorten(text: str, limit: int = 40) -> str:
    """Значение настройки для списка: длинное описание его не распирает."""
    text = " ".join(str(text or "").split())

    return text if len(text) <= limit else text[:limit - 1] + "…"


def drafts_menu(link, account) -> None:
    """Показать черновики кабинета и выставить выбранный.

    Черновик остаётся после каждого созданного товара и после неудачного
    выставления. Без этого списка добраться до него можно было только из
    кабинета на сайте.
    """
    try:
        from playerokapi.enums import ItemStatuses
    except ImportError:
        link.screen("Не установлена библиотека playerokapi.", buttons=MENU)
        return

    try:
        page = account.get_my_items(statuses=[ItemStatuses.DRAFT], count=24)
        drafts = list(getattr(page, "items", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Черновики прочитать не вышло: {e}", buttons=MENU)
        return

    if not drafts:
        link.screen("Черновиков нет.", buttons=MENU)
        return

    keys = [[(f"{d.name} — {getattr(d, 'price', '?')} ₽",
              PICK_DRAFT + str(d.id))] for d in drafts[:MAX_CHOICES]]
    keys.append([("✖️ Назад", "отмена")])
    answer = link.ask("Черновики:", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_DRAFT):
        link.screen("Отменил.", buttons=MENU)
        return

    draft_id = text[len(PICK_DRAFT):]
    chosen = next((d for d in drafts if str(d.id) == draft_id), None)

    if chosen is None:
        link.screen("Такого черновика больше нет.", buttons=MENU)
        return

    price = getattr(chosen, "raw_price", None) or getattr(chosen, "price", 0)
    publish_step(link, account, draft_id, price)


def check_session(link, account) -> None:
    """Проверить, жива ли сессия кабинета.

    Спрашиваем у площадки, кто мы: этот вызов и падает первым, когда куки
    протухли. Заодно он говорит то, что иначе выясняется в худший момент —
    не заблокирован ли кабинет и разрешено ли выставлять товары.
    """
    if account is None:
        link.screen("Кабинет не выбран. Откройте «👤 Аккаунт».", buttons=MENU)
        return

    link.screen("Спрашиваю площадку…")

    try:
        me = account.get()
    except Exception as e:                                    # noqa: BLE001
        if is_auth_error(e):
            link.screen(f"🔴 Сессия не действует.\n\n{COOKIES_ADVICE}",
                        buttons=MENU)
        else:
            link.screen(f"Проверить не вышло: {e}\n\n"
                        "Похоже на обрыв связи, а не на протухшие куки — "
                        "попробуйте ещё раз.", buttons=MENU)

        return

    where = AccountStore(ACCOUNTS_DIR).current()
    lines = ["🟢 Сессия живая."]

    if where is not None:
        lines.append(f"Кабинет: {where.name}")

    if getattr(me, "username", ""):
        lines.append(f"Продавец: {me.username}")

    # Про это молчат, пока не упрёшься: заблокированный кабинет и запрет
    # публикации выглядят как «бот сломался».
    if getattr(me, "is_blocked", False):
        lines.append("⛔ Кабинет заблокирован"
                     + (f": {me.is_blocked_for}"
                        if getattr(me, "is_blocked_for", "") else ""))

    if getattr(me, "can_publish_items", None) is False:
        lines.append("⚠️ Площадка сейчас не разрешает выставлять товары")

    unread = getattr(me, "unread_chats_counter", 0) or 0

    if unread:
        lines.append(f"💬 Непрочитанных чатов: {unread}")

    link.screen("\n".join(lines), buttons=MENU)


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
        link.screen("Такого кабинета больше нет.", buttons=MENU)
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
    link.screen(f"Переключился: {saved.name}", buttons=MENU)

    return fresh


def offer_new_cookies(link, store, saved, account, why):
    """Куки кабинета не приняли — предложить прислать свежие.

    Без этого единственным выходом было бы завести кабинет заново, потеряв
    имя и порядок. А протухают куки чаще всего остального.
    """
    answer = link.ask(
        f"Войти в «{saved.name}» не вышло: {why}\n\n"
        "Обычно это значит, что сессия истекла. Как чиним?",
        ANSWER_WAIT,
        buttons=[[("📧 Кодом на почту", PICK_WAY + "mail")],
                 [("🍪 Прислать куки", PICK_FIX + saved.id)],
                 [("✖️ Не сейчас", "отмена")]])
    chosen = str(answer.get("text") or "")

    if chosen == PICK_WAY + "mail":
        return renew_by_email(link, store, saved, account)

    if not chosen.startswith(PICK_FIX):
        link.screen("Оставил как есть.", buttons=MENU)
        return account

    answer = link.ask(
        f"Куки кабинета «{saved.name}» — строка, JSON или токен. "
        "Сообщение удалю сразу после прочтения.", ANSWER_WAIT,
        buttons=[CANCEL])
    cookies = normalize_cookies(str(answer.get("text") or ""))

    if not cookies:
        link.screen("Это не похоже на куки. Оставил как есть.", buttons=MENU)
        return account

    if not answer.get("from_button"):
        link._delete(answer)

    store.update_cookies(saved.id, cookies)

    try:
        fresh = open_account(cookies, saved.user_agent
                             or os.environ.get("PLAYEROK_UA", ""))
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"И эти не подошли: {e}\n"
                 "Проверьте, что куки из того же браузера, чей user-agent "
                 "указан у кабинета.", buttons=MENU)
        return account

    store.set_current(saved.id)
    link.screen(f"Готово, кабинет «{saved.name}» снова работает.", buttons=MENU)

    return fresh


def renew_by_email(link, store, saved, account):
    """Обновить сессию кабинета кодом на почту, сохранив имя и шаблоны."""
    answer = link.ask(f"Почта кабинета «{saved.name}»:", ANSWER_WAIT,
                      buttons=[CANCEL])
    email = str(answer.get("text") or "").strip()

    if wizard.cancelled(email):
        link.screen("Оставил как есть.", buttons=MENU)
        return account

    user_agent = saved.user_agent or DEFAULT_UA
    link.screen(f"Прошу код для {email}…")
    sent, why = emailauth.send_code(email, user_agent)

    if not sent:
        link.screen(f"Код не отправлен: {why}", buttons=MENU)
        return account

    answer = link.ask(f"Код отправлен на {email}. Введите шесть цифр.",
                      ANSWER_WAIT, buttons=[CANCEL])
    code = str(answer.get("text") or "").strip()

    if wizard.cancelled(code):
        link.screen("Оставил как есть.", buttons=MENU)
        return account

    link.screen("Проверяю код…")
    cookies, why = emailauth.confirm(email, code, user_agent)

    if not cookies:
        link.screen(f"Войти не вышло: {why}", buttons=MENU)
        return account

    store.update_cookies(saved.id, cookies)

    try:
        fresh = open_account(cookies, user_agent)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Сессия получена, но кабинет не открылся: {e}",
                    buttons=MENU)
        return account

    store.set_current(saved.id)
    link.screen(f"Готово, «{saved.name}» снова работает.", buttons=MENU)

    return fresh


def add_account(link, store, account):
    """Завести новый кабинет: почтой или куками."""
    answer = link.ask("Как назвать кабинет? Это имя увидите только вы.",
                      ANSWER_WAIT, buttons=[CANCEL])
    name = " ".join(str(answer.get("text") or "").split())

    if not name or wizard.cancelled(name):
        link.screen("Отменил.", buttons=MENU)
        return account

    answer = link.ask(
        f"Кабинет «{name}».\n\nКак входим?",
        ANSWER_WAIT,
        buttons=[[("📧 Кодом на почту", PICK_WAY + "mail")],
                 [("🍪 Куками из браузера", PICK_WAY + "cookies")],
                 [("✖️ Отмена", "отмена")]])
    way = str(answer.get("text") or "")

    if way == PICK_WAY + "mail":
        return add_by_email(link, store, account, name)

    if way != PICK_WAY + "cookies":
        link.screen("Отменил.", buttons=MENU)
        return account

    return add_by_cookies(link, store, account, name)


def add_by_email(link, store, account, name: str):
    """Вход по коду на почту: сессию получаем сами, браузер не нужен."""
    answer = link.ask(
        "Почта аккаунта на площадке.\n\n"
        "Пришлю на неё код — его и введёте. Куки доставать не придётся.",
        ANSWER_WAIT, buttons=[CANCEL])
    email = str(answer.get("text") or "").strip()

    if wizard.cancelled(email):
        link.screen("Отменил.", buttons=MENU)
        return account

    link.screen(f"Прошу код для {email}…")
    sent, why = emailauth.send_code(email, DEFAULT_UA)

    if not sent:
        link.screen(f"Код не отправлен: {why}", buttons=MENU)
        return account

    answer = link.ask(
        f"Код отправлен на {email}.\n\n"
        "Введите шесть цифр из письма. Письмо может идти пару минут и "
        "попасть в спам.", ANSWER_WAIT, buttons=[CANCEL])
    code = str(answer.get("text") or "").strip()

    if wizard.cancelled(code):
        link.screen("Отменил.", buttons=MENU)
        return account

    link.screen("Проверяю код…")
    cookies, why = emailauth.confirm(email, code, DEFAULT_UA)

    if not cookies:
        link.screen(f"Войти не вышло: {why}", buttons=MENU)
        return account

    # User-agent сохраняем тот же, которым входили: площадка сверяет его с
    # тем, при котором сессия выдана.
    account_id = store.add(name, cookies, DEFAULT_UA)
    link.screen(f"Кабинет «{name}» добавлен.")

    return switch_account(link, store, account_id, account)


def add_by_cookies(link, store, account, name: str):
    """Старый путь: куки из браузера."""
    answer = link.ask(
        "Пришлите куки этого кабинета — строку «token=...», выгрузку "
        "расширения в JSON или сам токен. Сообщение я удалю сразу после "
        "прочтения.", ANSWER_WAIT, buttons=[CANCEL])
    raw = str(answer.get("text") or "")

    if wizard.cancelled(raw):
        link.screen("Отменил.", buttons=MENU)
        return account

    cookies = normalize_cookies(raw)

    if not cookies:
        link.screen("Это не похоже на куки. Начнём заново.", buttons=MENU)
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

    account_id = store.add(name, cookies, user_agent or DEFAULT_UA)
    link.screen(f"Кабинет «{name}» добавлен.")

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
        link.screen(f"Войти в кабинет не вышло.\n\n{e}", buttons=MENU)
    except Exception as e:                                    # noqa: BLE001
        if is_auth_error(e):
            link.screen(f"🔴 Вход в кабинет не принят.\n\n{COOKIES_ADVICE}",
                     buttons=MENU)
        else:
            link.screen(f"Войти в кабинет не вышло: {e}\n\n"
                     "Откройте «Аккаунт», чтобы прислать свежие куки или "
                     "переключиться на другой кабинет.", buttons=MENU)

    return None


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    link = link_from_env()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID — "
            "разговаривать не с кем.")

    link.set_commands(COMMANDS)
    account = try_sign_in(link)
    where = AccountStore(ACCOUNTS_DIR).current()

    if account is not None and where is not None:
        link.screen(f"Кабинет: {where.name}")

    print("Жду в телеграме. Напишите боту «новый товар».")

    if account is not None:
        link.screen("Готов.", buttons=MENU)

    while True:
        message = link.wait_answer(3600)
        text = str(message.get("text") or "").strip().lower()

        if not text:
            continue

        account = serve_one(link, account, text)
        time.sleep(0.2)


def serve_one(link, account, text: str):
    """Одна команда под защитой. → аккаунт для дальнейшей работы.

    Ошибка в обработчике не должна уносить бота целиком: сторож его, конечно,
    поднимет, но продавец за это время видит только «не грузит» — кнопка
    нажата, ответа нет — и теряет начатое. Лучше сказать словами и работать
    дальше.
    """
    try:
        return handle_command(link, account, text)
    except Exception as e:                                    # noqa: BLE001
        log_error(e)
        link.forget_screen()
        link.screen(f"Что-то пошло не так: {e}\n\n"
                    "Бот жив, можно продолжать.", buttons=MENU)

        return account


def log_error(e) -> None:
    """В журнал — полностью, с местом, где случилось."""
    import traceback

    print("ОШИБКА:", e)
    traceback.print_exc()


def handle_command(link, account, text: str):
    """Разобрать одну команду. → аккаунт для дальнейшей работы."""
    if text in MENU_WORDS:
        # «Меню» должно открываться всегда — даже когда кабинета нет и
        # делать больше нечего.
        link.forget_screen()
        link.screen("Что делаем?", buttons=MENU)
        return account

    if text in START_WORDS or text in TEMPLATE_WORDS \
            or text in DRAFT_WORDS or text in CHECK_WORDS \
            or text in BLANK_WORDS or text in SERIES_WORDS:
        if account is None:
            link.screen("Сначала нужен рабочий кабинет: откройте "
                        "«Аккаунт».", buttons=MENU)
            return account

    if text in START_WORDS:
        make_item(link, account)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in BLANK_WORDS:
        make_blank(link, account)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in SERIES_WORDS:
        series_menu(link, account)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in TEMPLATE_WORDS:
        from_template(link, account)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in DRAFT_WORDS:
        drafts_menu(link, account)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in SETTINGS_WORDS:
        settings_menu(link)
        link.screen("Готов к следующему.", buttons=MENU)
    elif text in CHECK_WORDS:
        check_session(link, account)
    elif text in ACCOUNT_WORDS:
        account = accounts_menu(link, account)
    elif not wizard.cancelled(text):
        # Молчать нельзя: продавец решит, что бот умер.
        link.screen("Что делаем?", buttons=MENU)

    return account


CHOOSE.update({"game": choose_game, "category": choose_category,
               "obtaining": choose_obtaining})


if __name__ == "__main__":
    main()
