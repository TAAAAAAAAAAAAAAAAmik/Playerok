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
import copyitem                                               # noqa: E402
import grouping                                               # noqa: E402
import statepath                                              # noqa: E402
import oneshot                                                # noqa: E402
import pricing                                                # noqa: E402
import series                                                 # noqa: E402
import vary                                                   # noqa: E402
import bump                                                   # noqa: E402
from bump import Ledger                                       # noqa: E402
import wizard                                                 # noqa: E402
from accounts import AccountStore                             # noqa: E402
from auth import open_account, sign_in                        # noqa: E402
from cards import CARDS, card_by_slug                         # noqa: E402
from catalog import (card_for_title, denominations_for,       # noqa: E402
                     nominal_from_title, nominal_shown, render,
                     shown_number)
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
         ("📋 Копия с витрины", "копировать")],
        [("📊 Серия номиналов", "серия"),
         ("📄 Черновики", "черновики")],
        [("🏷 Правила копий", "копии"),
         ("⚙️ Автовыдача", "настройки")],
        [("🔑 Проверить сессию", "проверить"),
         ("👤 Аккаунт", "аккаунт")]]

# Команды в меню Telegram — та кнопка слева от поля ввода. Без неё их
# надо помнить и набирать вслепую.
COMMANDS = [
    ("start", "Меню"),
    ("new", "Новый товар"),
    ("blank", "Одним сообщением"),
    ("tpl", "Создать из шаблона"),
    ("series", "Серия номиналов из шаблона"),
    ("drafts", "Черновики"),
    ("copy", "Копия живого объявления"),
    ("copies", "Правила копий: предел одинаковых"),
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
COPIES_WORDS = ("копии", "правила копий", "копии и цены", "/copies")
COPY_WORDS = ("копировать", "копия", "копия с витрины", "/copy")
SERIES_WORDS = ("серия", "серия номиналов", "номиналы", "/series")

# Где лежат шаблоны. Рядом с состоянием выдач: это тоже рабочие данные,
# которые переживают перезапуск и не место им в репозитории.
TEMPLATE_DIR = statepath.in_project(
    os.environ.get("PLAYEROK_TEMPLATES", "state/templates"))

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
PICK_COPY = "cpy:"
PICK_LIVE = "liv:"

# Где лежит состояние выдачи: и настройки, и журнал выданных заказов. На
# кабинет: товары и слова-опознаватели у разных кабинетов разные, а
# смешать журналы двух кабинетов означало бы не выдать оплаченный заказ.
SETTINGS_DIR = statepath.in_project(
    os.environ.get("PLAYEROK_STATE", "state/delivery"))

# User-agent, которым бот входит и работает. Раз сессию выдаём мы сами,
# он наш: площадка сверяет его с тем, при котором сессия выдана, и
# разойтись они не должны.
DEFAULT_UA = os.environ.get(
    "PLAYEROK_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")

# Где живут сохранённые кабинеты.
ACCOUNTS_DIR = statepath.in_project(
    os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts"))

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


def buttons_for(step: str, draft=None):
    """Кнопки под вопрос. Там, где ответ свободный, кнопок нет.

    Название и цену кнопкой не выберешь, но «отмена» нужна на каждом шаге:
    передумать посреди опроса — обычное дело.
    """
    if step == "region":
        return region_buttons(draft)

    if step == "photos":
        return [PHOTOS_DONE]

    if step.startswith(wizard.FIELD) or step == "description":
        # Описание можно не писать — возьмётся типовое. У поля площадки
        # кнопка «пропустить» есть всегда, но обязательное поле её не
        # примет и переспросит.
        return [SKIP]

    return [CANCEL]


def region_buttons(draft=None):
    """Кнопки регионов. Лучше — те, что есть у поставщика для этой карты.

    Ходовые про запас всё равно показываем: каталог может не прочитаться
    (ключа нет, лимит выбран, связь легла), и остаться в такой миг вовсе
    без кнопок хуже, чем с неточными. Любой регион всё равно принимается
    текстом.
    """
    card = card_for_title(CARDS, str(getattr(draft, "name", "") or ""))
    found = supplier_regions(card) if card is not None else []

    if not found:
        return [REGION_BUTTONS, MORE_REGIONS, CANCEL]

    rows = []

    for start in range(0, len(found), 4):
        rows.append([(code, code) for code in found[start:start + 4]])

    rows.append(CANCEL)

    return rows


def regions_of(conf, card) -> list:
    """Какие регионы показывать в настройках карты.

    Те, что есть у поставщика, плюс те, для которых продавец уже задал
    услугу руками: заданную привязку надо показать даже тогда, когда
    номиналы в этом регионе временно кончились — иначе она пропадёт с
    экрана, а работать продолжит.
    """
    found = set(supplier_regions(card))

    for region in REGIONS:
        found.add(region)

    for region in (conf.card(card.slug)["services"] or {}):
        found.add(str(region).upper())

    return sorted(found)


def supplier_regions(card) -> list:
    """Регионы, в которых у поставщика есть эта карта в наличии.

    Показать регион, которого у поставщика нет, — значит дать продавцу
    выставить товар, который выдача не выдаст: узнает он об этом из
    отказа, когда покупатель уже заплатил.
    """
    if not getattr(card, "subcategory", ""):
        return []

    catalog, _ = supplier_catalog()

    if catalog is None:
        return []

    try:
        rows = denominations_for(card, catalog)
    except Exception:                                         # noqa: BLE001
        return []

    return sorted({r.region for r in rows if r.region and r.in_stock > 0})


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
        link.screen(f"Категории прочитать не вышло: {e}", buttons=MENU)
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
        link.screen(f"Способы получения прочитать не вышло: {e}",
                    buttons=MENU)
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
            ANSWER_WAIT, buttons=buttons_for(step, draft))
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


def publish_step(link, account, item_id: str, price: int,
                 nominal: float = 0.0) -> None:
    """Спросить про выставление и выставить, если согласились.

    `nominal` нужен счёту бесплатных объявлений: платное продавец оплатил
    сам, и в лимит одинаковых оно не идёт — иначе мы тратили бы его деньги
    и следом сдвигали цену.
    """
    try:
        statuses = account.get_item_priority_statuses(item_id, price)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Статусы приоритета прочитать не вышло: {e}\n"
                    "Товар остался черновиком, выставьте его в кабинете.",
                    buttons=MENU)
        return

    rows = listing.ordered(statuses or [])

    if not rows:
        link.screen("Статусов приоритета нет — выставить нечем. "
                    "Товар остался черновиком.", buttons=MENU)
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
        link.screen("Оставил черновиком. Выставить можно в кабинете.",
                    buttons=MENU)
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
            buttons=[[(f"💳 Да, списать {shown_number(listing.price_of(chosen))} ₽",
                       listing.CONFIRM_WORD)],
                     [("✖️ Нет, оставить черновиком", "нет")]])

        if not listing.confirmed(str(again.get("text") or "")):
            link.screen("Не подтверждено. Оставил черновиком.",
                        buttons=MENU)
            return

    if nominal and listing.needs_confirmation(chosen):
        # Платное — значит не бесплатное: из счёта убираем.
        ledger_of().forget(nominal, price)

    try:
        account.publish_item(item_id, chosen.id)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Выставить не вышло: {e}\n"
                    "Черновик при этом цел и виден в кабинете.",
                    buttons=MENU)
        return

    link.forget_screen()
    link.say(f"Выставлено: {listing.describe(chosen)}")
    # Экран забыт — значит следующий будет новым сообщением, и меню надо
    # вернуть явно: без него продавцу некуда нажимать.
    link.screen("Готов к следующему.", buttons=MENU)


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
    publish_step(link, account, item_id, draft.price, draft.nominal)

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


def save_from_shop(link, account) -> None:
    """Сделать шаблон из объявления, которое уже стоит на витрине.

    Ничего не создаёт на площадке: товар уже есть, второй такой же сейчас
    не нужен. Нужен шаблон — чтобы потом повторять одним нажатием.
    """
    sample = pick_live_sample(link, account)

    if sample is None:
        return

    store = templates_of()

    try:
        store.save(sample.name, sample.price, sample.region, sample.photos(),
                   description=sample.description, game=sample.game,
                   category=sample.category, obtaining=sample.obtaining,
                   fields=sample.fields, options=sample.options)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Сохранить шаблон не вышло: {e}", buttons=MENU)
        return

    link.screen(f"Шаблон «{sample.name}» сохранён.\n\nТеперь такой товар "
                f"создаётся одним нажатием — «⚡ Из шаблона».", buttons=MENU)


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
        link.screen(f"Сохранить шаблон не вышло: {e}", buttons=MENU)
        return

    link.screen("Шаблон сохранён.", buttons=MENU)


def from_template(link, account) -> None:
    """Список шаблонов кабинета: повторить, изменить или удалить."""
    store = templates_of()
    saved = store.all()

    keys = [[(t.label(), PICK + t.id)] for t in saved]
    # Шаблоны заводились только из товаров, созданных через бота. У
    # продавца, выставившего всё в кабинете на сайте, их нет вовсе — и
    # повторять одним нажатием ему было нечего, хотя объявления у него
    # есть. Отсюда эта кнопка: взять готовое с витрины.
    keys.append([("📥 Взять с витрины", PICK + "shop")])
    keys.append([("✖️ Отмена", "отмена")])

    said = ("Шаблоны этого кабинета:" if saved else
            "Шаблонов пока нет.\n\nШаблон — это объявление, которое "
            "повторяется одним нажатием. Возьмите готовое с витрины или "
            "создайте товар и сохраните его шаблоном.")
    answer = link.ask(said, ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK):
        link.screen("Отменил.", buttons=MENU)
        return

    if text[len(PICK):] == "shop":
        save_from_shop(link, account)
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


# Чередование фраз для повторов из шаблона. Одно на процесс: соседние
# нажатия должны давать разные фразы, а не каждое своё случайное.
ROTATION = vary.Rotation()


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
    draft.nominal = nominal_from_title(template.name, template.price) or 0.0
    draft.region = template.region
    draft.description = template.description
    draft.game = template.game
    draft.category = template.category
    draft.obtaining = template.obtaining
    # Копия полей, а не тот же список: шаблон повторяют много раз, и
    # правка в одном повторе не должна менять сам шаблон.
    draft.fields = copy.deepcopy(template.fields)
    draft.options = copy.deepcopy(template.options)
    draft.photos = photos

    # Свободные поля слегка меняем: площадки не любят объявления,
    # совпадающие до буквы. Что именно вписано — видно в сводке ниже, это
    # не делается втихую.
    #
    # Чередование общее на всё время работы бота: без него два нажатия
    # подряд легко дают одну фразу, а это ровно та копия, которой мы и
    # избегаем.
    ledger = ledger_of()

    if ledger.rules()["vary"]:
        vary.apply(draft.fields, ROTATION)

    live = live_counts(account, template.game, template.category)

    # Четвёртая копия по той же цене — уже не ассортимент. Поднимаем на
    # столько, сколько задано, и говорим об этом: молча изменить цену
    # продавца нельзя.
    draft.price, up = ledger.price_for(draft.nominal, draft.price, live)
    note = (f"\n\nЦена поднята на {up} ₽: таких по {template.price} ₽ уже "
            f"{ledger.count(draft.nominal, template.price, live)}."
            if up else "")

    link.screen(f"Повторяю:\n\n{draft.summary()}{note}\n\nСоздаю черновик…")

    if send_draft(link, account, draft):
        ledger.remember(draft.nominal, draft.price)


PICK_SOURCE = "src:"


class LiveSample:
    """Живое объявление в том виде, в каком его ждёт серия.

    Серия умела строиться только из шаблона, а шаблон запоминается при
    создании товара через бота. У продавца, выставившего всё в кабинете на
    сайте, шаблонов нет вовсе — и серия была ему недоступна, хотя именно
    ему она нужнее всего.
    """

    def __init__(self, plan: dict, photos: list, description: str):
        self.name = plan["name"]
        self.price = plan["price"]
        self.region = plan["region"]
        self.description = description
        self.game = plan["game"]
        self.category = plan["category"]
        self.obtaining = plan["obtaining"]
        self.fields = plan["fields"]
        self.options = plan["options"]
        self._photos = photos

    def photos(self) -> list:
        return list(self._photos)

    def complete(self) -> bool:
        return True


def series_menu(link, account) -> None:
    """Серия объявлений: остальные номиналы одного и того же товара."""
    saved = templates_of().all()

    keys = [[("📋 С витрины", PICK_SOURCE + "live")]]

    if saved:
        keys.append([("⚡ Из шаблона", PICK_SOURCE + "tpl")])

    keys.append([("✖️ Назад", "отмена")])

    answer = link.ask(
        "Серия — это сразу все номиналы одного товара.\n\n"
        "С какого объявления её делать?", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_SOURCE):
        link.screen("Отменил.", buttons=MENU)
        return

    if text[len(PICK_SOURCE):] == "tpl":
        from_template_series(link, account)
        return

    sample = pick_live_sample(link, account)

    if sample is not None:
        make_series(link, account, sample)


def from_template_series(link, account) -> None:
    """Серия из сохранённого шаблона."""
    store = templates_of()
    saved = store.all()

    if not saved:
        link.screen("Шаблонов пока нет.", buttons=MENU)
        return

    keys = [[(t.label(), PICK_SERIES + t.id)] for t in saved[:MAX_CHOICES]]
    keys.append([("✖️ Назад", "отмена")])
    answer = link.ask("С какого шаблона делаем серию?", ANSWER_WAIT,
                      buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_SERIES):
        link.screen("Отменил.", buttons=MENU)
        return

    template = store.get(text[len(PICK_SERIES):])

    if template is None:
        link.screen("Такого шаблона больше нет.", buttons=MENU)
        return

    if not template.complete():
        link.screen("Этот шаблон сохранён до того, как бот стал спрашивать "
                    "категорию, и повторить его нечем.", buttons=MENU)
        return

    make_series(link, account, template)


def pick_live_sample(link, account):
    """Выбрать объявление с витрины и прочитать его целиком. → образец."""
    item_id = choose_live_item(link, account)

    if item_id is None:
        return None

    link.screen("Читаю объявление…")

    try:
        item = account.get_item(item_id)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Объявление прочитать не вышло: {e}", buttons=MENU)
        return None

    plan, gaps = copyitem.plan(item)

    if gaps:
        link.screen(f"Взять за образец не выйдет — площадка не отдала: "
                    f"{', '.join(gaps)}.", buttons=MENU)
        return None

    link.screen(f"Скачиваю картинки ({len(plan['photos'])})…")
    photos = fetch_photos(plan["photos"])

    if not photos:
        link.screen("Картинки скачать не вышло — без них товар не создать.",
                    buttons=MENU)
        return None

    # Описание чистим так же, как при копии: строки про регион и номинал
    # бот поставит свои, а две одинаковые строки в одном описании движок
    # выдачи прочитает неверно.
    body, why = wizard.accept_description(
        str(getattr(item, "description", "") or ""))

    return LiveSample(plan, photos, "" if why else body)


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


def make_series(link, account, template) -> None:
    """Собрать номиналы с ценами, показать план, создать по согласию.

    `template` — образец: сохранённый шаблон или живое объявление с
    витрины. Серии всё равно, откуда он взялся, лишь бы отдавал название,
    цену, категорию и картинки.
    """
    photos = template.photos()

    if not photos:
        link.screen("У шаблона пропали картинки — без них товар не создать.",
                    buttons=MENU)
        return

    old = nominal_from_title(template.name, template.price)
    pattern = series.pattern_from(template.name, old) if old else ""

    if not pattern:
        # Числа в названии нет — значит подставлять номинал некуда. Это не
        # повод отказывать: у продавца названия бывают какие угодно
        # («🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА»), и спросить образец дешевле, чем
        # заставлять его переименовывать товар ради нашего разбора.
        pattern = ask_pattern(link, template)

        if pattern is None:
            return

    card = card_for_title(CARDS, template.name) \
        or card_for_title(CARDS, str((template.game or {}).get("name") or ""))

    text = ask_series_rows(link, template, card, old)

    if text is None:
        return

    rows, bad = series.parse(text)
    jobs, refused = series.plan(pattern, template.description, old, rows)

    if not jobs:
        link.screen("Создавать нечего.\n\n"
                    + "\n".join(bad + refused), buttons=MENU)
        return

    raised = apply_bump(jobs, live_counts(account, template.game,
                                         template.category))
    count = plural(len(jobs), "объявление", "объявления", "объявлений")
    lines = [f"Создам {count}:", ""]
    lines += [f"• {j['name']} — {j['price']} ₽"
              + (f" (+{j['bump']} ₽ — столько же уже висит)"
                 if j.get("bump") else "")
              for j in jobs]

    if raised:
        many = plural(raised, "объявления", "объявлений", "объявлений")
        lines += ["", f"Цена поднята у {many}: столько же бесплатных "
                      f"с той же ценой уже висит."]

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


def ask_pattern(link, template):
    """Название-образец с местом под номинал. → образец или None.

    Спрашиваем, а не выдумываем: номинал бывает и в начале, и в середине,
    и создать десяток объявлений с неверным названием дороже, чем задать
    один вопрос.
    """
    guess = series.suggest_pattern(template.name)
    answer = link.ask(
        f"В названии «{template.name}» числа нет — подставлять номинал "
        f"некуда.\n\n"
        f"Пришлите название-образец, поставив {series.SLOT} туда, где "
        f"должно быть число. Например:\n\n{guess}\n\n"
        f"Или нажмите «Так и сделать» — возьму этот вариант.",
        ANSWER_WAIT,
        buttons=[[("🏷 Так и сделать", "так")], CANCEL])
    text = str(answer.get("text") or "").strip()

    if not text or wizard.cancelled(text):
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return None

    pattern = guess if text.lower() == "так" else text

    if not series.usable(pattern):
        link.screen(
            f"В образце «{pattern}» нет места под номинал. Поставьте "
            f"{series.SLOT} туда, где должно стоять число — иначе все "
            f"объявления получат одно и то же название.", buttons=MENU)
        return None

    return pattern


def ask_series_rows(link, template, card, old: float):
    """Получить строки «номинал = цена». → текст или None, если отменили.

    Номиналы у поставщика уже перечислены — переписывать их руками
    незачем. Решает продавец только цену, её одну и вводит.
    """
    head = f"Образец: «{template.name}»"
    head += f" — номинал {shown_number(old)}" if old else ""
    head += f", цена {template.price} ₽.\n\n"
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


def apply_bump(jobs, live=None) -> int:
    """Поднять цену там, где одинаковых объявлений уже предел. → сколько.

    Считаем ВСЮ партию сразу, а не по одному при создании: продавец должен
    увидеть настоящие цены до того, как согласится, а не узнать о них из
    готовых объявлений.
    """
    ledger = ledger_of()
    raised = 0

    for job in jobs:
        price, up = ledger.price_for(job["nominal"], job["price"], live)
        job["price"], job["bump"] = price, up

        if up:
            raised += 1

    return raised


def run_series(link, account, template, photos, jobs) -> None:
    """Создать объявления по плану, показывая ход одним экраном."""
    done, failed = [], []
    # Одно чередование на всю партию: иначе две соседние копии легко
    # получают одну фразу, а ровно этого мы и избегаем.
    rotation = vary.Rotation()
    vary_on = ledger_of().rules()["vary"]

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

        if vary_on:
            vary.apply(draft.fields, rotation)

        item_id, why = create_item(account, draft)

        if item_id:
            done.append((job["name"], item_id))
            # Записываем сразу: партия идёт подряд, и без этого все её
            # объявления получили бы одну цену.
            ledger_of().remember(job["nominal"], job["price"])
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


def live_counts(account, game=None, category=None) -> dict:
    """Сколько одинаковых объявлений СЕЙЧАС на витрине → {ключ: число}.

    Свой счёт знает только то, что бот создал сам. А продавец мог
    выставить десятки таких же руками или до бота — площадке всё равно,
    чьей рукой они сделаны, и считать надо всё.

    Смотрим ВНУТРИ той же категории, а не по всей витрине. Одинаковые
    объявления по определению лежат в одной категории, зато витрина
    бывает на пять сотен товаров: читать её целиком ради одного нажатия
    значит заставить продавца ждать, да ещё и нарваться на «слишком много
    попыток».

    Не прочиталось — возвращаем пусто. Считать по своему счёту хуже, чем
    по витрине, но лучше, чем не создать товар вовсе.
    """
    if account is None:
        return {}

    try:
        items, _ = my_items(
            account,
            game_id=str((game or {}).get("id") or ""),
            category_id=str((category or {}).get("id") or ""))
    except Exception:                                         # noqa: BLE001
        return {}

    found: dict = {}

    for item in items:
        value = nominal_from_title(str(getattr(item, "name", "") or ""),
                                   getattr(item, "price", None))
        price = int(getattr(item, "price", 0) or 0)

        if not value or not price:
            continue

        where = bump.key(value, price)
        found[where] = found.get(where, 0) + 1

    return found


def ledger_of() -> Ledger:
    """Счёт бесплатных объявлений текущего кабинета.

    В том же хранилище, что настройки выдачи: отдельный файл стал бы
    вторым источником правды о том же кабинете.
    """
    return Ledger(settings_of().store)


def copies_menu(link) -> None:
    """Что делать с одинаковыми объявлениями: предел, шаг, разнообразие."""
    ledger = ledger_of()
    rules = ledger.rules()
    counted = ledger.pairs()

    lines = [
        "🏷 Правила копий",
        "",
        "Это НЕ создание копий, а правила для них: площадка не любит "
        "одинаковые объявления, и бот следит, чтобы их не копилось само "
        "собой.",
        "",
        "Сами копии делаются так:",
        "  📋 Копия с витрины — повторить уже выставленное",
        "  ⚡ Из шаблона — повторить сохранённое",
        "  📊 Серия номиналов — сразу на все номиналы",
        "",
        f"Подъём цены: {'включён' if rules['enabled'] else 'ВЫКЛЮЧЕН'}",
        f"Одинаковых допускаем: {rules['limit']}",
        f"Дальше дороже на: {rules['step']} ₽",
        f"Разнообразить «Комментарий» и «Промокод»: "
        f"{'да' if rules['vary'] else 'нет'}",
    ]

    if rules["enabled"]:
        many = plural(rules["limit"], "объявление", "объявления",
                      "объявлений")
        step = rules["step"]
        lines += ["", f"Сейчас так: {many} одного номинала по одной "
                      f"цене, следующее — на {step} ₽ дороже."]

    lines += ["",
              "Считаются НЕ только созданные ботом: перед каждой копией он "
              "смотрит витрину и берёт большее из двух. Площадке всё равно, "
              "чьей рукой сделано объявление.",
              "",
              f"Свой счёт: пар «номинал + цена» — {counted}"]

    keys = [[(("⛔ Выключить подъём" if rules["enabled"]
               else "✅ Включить подъём"), PICK_COPY + "on")],
            [("🔢 Сколько допускать", PICK_COPY + "limit"),
             ("💰 На сколько дороже", PICK_COPY + "step")],
            [(("🎲 Не разнообразить" if rules["vary"]
               else "🎲 Разнообразить"), PICK_COPY + "vary")],
            [("🧹 Сбросить счёт", PICK_COPY + "reset")],
            [("✖️ Назад", "отмена")]]

    answer = link.ask("\n".join(lines), ANSWER_WAIT, buttons=keys)
    what = str(answer.get("text") or "")

    if not what.startswith(PICK_COPY):
        link.screen("Готово.", buttons=MENU)
        return

    what = what[len(PICK_COPY):]

    if what == "on":
        ledger.set_enabled(not rules["enabled"])
    elif what == "vary":
        ledger.set_vary(not rules["vary"])
    elif what == "limit":
        ask_copy_number(
            link, ledger.set_limit,
            f"Сколько одинаковых объявлений допускать?\n\n"
            f"Считаются объявления одного номинала по одной цене. Когда "
            f"их станет столько, следующее создастся дороже.\n\n"
            f"Сейчас: {rules['limit']}. Можно от {bump.MIN_LIMIT} "
            f"до {bump.MAX_LIMIT}.")
    elif what == "step":
        ask_copy_number(
            link, ledger.set_step,
            f"На сколько рублей поднимать цену?\n\n"
            f"Сейчас: {rules['step']} ₽. Можно от {bump.MIN_STEP} "
            f"до {bump.MAX_STEP}.")
    elif what == "reset":
        confirm_reset(link, ledger)
        return

    copies_menu(link)


def ask_copy_number(link, save, question: str) -> None:
    """Спросить число и сохранить. Отказ и отмена ничего не меняют."""
    answer = link.ask(question, ANSWER_WAIT, buttons=[SKIP])
    text = str(answer.get("text") or "")

    if wizard.cancelled(text) or wizard.skipped(text):
        return

    value, why = wizard.accept_number(text)

    if why:
        link.screen(why, buttons=MENU)
        return

    save(value)


def confirm_reset(link, ledger) -> None:
    """Сброс счёта — с подтверждением: обратно его не собрать."""
    counted = ledger.pairs()

    if not counted:
        link.screen("Счёт и так пуст.", buttons=MENU)
        return

    answer = link.ask(
        f"Забыть свой счёт по {plural(counted, 'паре', 'парам', 'парам')} "
        f"«номинал + цена»?\n\n"
        f"Витрину бот считает заново каждый раз, а свой счёт нужен для "
        f"черновиков: они ещё не на витрине, но скоро там будут.\n\n"
        f"Обратно счёт не собрать — бот не знает, что висит на витрине.",
        ANSWER_WAIT,
        buttons=[[("🧹 Да, забыть", "да")], CANCEL])

    if str(answer.get("text") or "").strip().lower() == "да":
        link.screen(f"Забыл {plural(ledger.reset(), 'пару', 'пары', 'пар')}.",
                    buttons=MENU)
        return

    copies_menu(link)


# Сколько объявлений показывать на одной странице списка. Столько же,
# сколько вариантов в других списках: больше не влезает на телефон.
PAGE = MAX_CHOICES

# Сколько страниц готовы прочитать у площадки. Двадцать по двадцать
# четыре — почти пять сотен объявлений: больше не бывает даже у крупного
# продавца, а бесконечный цикл по чужому курсору бывает.
MAX_PAGES = 20

PICK_GROUP = "grp:"
PICK_NOM = "nom:"
PICK_PAGE = "pge:"


# Пауза между страницами списка товаров. Площадка считает частые запросы
# и отвечает «слишком много попыток» — а мы читаем страницы подряд.
PAGE_PAUSE = 0.8

# Сколько держим прочитанный список. Продавец листает страницы и ходит
# назад: перечитывать витрину на каждое нажатие значит выбирать лимит
# площадки собственными руками.
ITEMS_TTL = 120.0

_ITEMS: dict = {"at": 0.0, "rows": None, "whole": True, "key": ""}

# По этим словам узнаём просьбу площадки сбавить темп. Это не поломка:
# то, что уже прочитано, остаётся годным.
TOO_OFTEN = ("слишком много", "too many", "rate limit", "429")


def forget_items() -> None:
    """Забыть прочитанную витрину.

    Зовётся при смене кабинета: объявления у кабинетов разные, и показать
    чужие — значит дать скопировать не тот товар не в тот магазин.
    """
    _ITEMS.update({"at": 0.0, "rows": None, "whole": True, "key": ""})


def is_too_often(e) -> bool:
    text = str(e).lower()

    return any(word in text for word in TOO_OFTEN)


def my_items(account, fresh: bool = False, game_id: str = "",
             category_id: str = "") -> tuple:
    """Выставленные объявления → (список, всё ли прочитано).

    Площадка отдаёт по двадцать четыре за запрос, и первая страница — это
    не «все»: продавец, не нашедший своего объявления, решит, что бот его
    не видит.

    Но и читать двадцать страниц подряд нельзя — площадка отвечает
    «слишком много попыток». Поэтому между страницами пауза, а если
    попросили сбавить темп посреди чтения, отдаём прочитанное и честно
    говорим, что список неполный. Половина списка полезнее отказа: нужное
    объявление скорее всего в ней.
    """
    key = f"{game_id}/{category_id}"

    if not fresh and _ITEMS.get("key") == key \
            and _ITEMS["rows"] is not None \
            and time.time() - _ITEMS["at"] < ITEMS_TTL:
        return list(_ITEMS["rows"]), _ITEMS["whole"]

    try:
        from playerokapi.enums import ItemStatuses
        where = {"statuses": [ItemStatuses.APPROVED]}
    except ImportError:
        where = {}

    # Отбор делает сама площадка. Это не только точнее — это ещё и меньше
    # страниц, а значит меньше поводов услышать «слишком много попыток».
    if category_id:
        where["category_id"] = category_id
    elif game_id:
        where["game_id"] = game_id

    out: list = []
    cursor = None
    whole = True

    for number in range(MAX_PAGES):
        if number:
            time.sleep(PAGE_PAUSE)

        try:
            page = account.get_my_items(count=24, after_cursor=cursor, **where)
        except Exception as e:                                # noqa: BLE001
            if out and is_too_often(e):
                # Уже что-то прочитали — этим и обойдёмся.
                whole = False
                break

            raise

        out.extend(list(getattr(page, "items", None) or []))
        info = getattr(page, "page_info", None)

        if not getattr(info, "has_next_page", False):
            break

        cursor = getattr(info, "end_cursor", None)

        if not cursor:
            break
    else:
        # Страницы кончились раньше, чем наше терпение.
        whole = False

    _ITEMS.update({"at": time.time(), "rows": list(out), "whole": whole,
                   "key": key})

    return out, whole


PICK_CAT = "cat2:"


def copy_live(link, account) -> None:
    """Копия объявления, которое уже стоит на витрине."""
    item_id = choose_live_item(link, account)

    if item_id is not None:
        make_copy(link, account, item_id)


def choose_live_item(link, account):
    """Выбрать объявление с витрины → его номер или None.

    Общий выбор для копии и для серии: обеим нужен образец, и обеим он
    нужен с профиля продавца, а не из того, что он когда-то создавал
    через бота.

    Шаблон запоминается при создании товара — а то, что заведено раньше
    бота или в кабинете на сайте, шаблона не имеет. Повторить такое было
    нечем, хотя всё нужное у площадки есть.

    Сначала спрашиваем игру и категорию. У продавца с пятью сотнями
    объявлений список без отбора бесполезен, а разложить его по настоящим
    категориям бот сам не может: в списке товаров площадка категорию НЕ
    отдаёт, только в карточке каждого — читать пять сотен карточек ради
    одного меню значит заставить ждать минуты.
    
    Зато отбирать по категории умеет сама площадка. Заодно это и меньше
    страниц, то есть меньше поводов услышать «слишком много попыток».
    """
    game = ask_game(link, account)

    if game is None:
        return None

    if game == "все":
        return show_items(link, account)

    category = ask_category(link, account, game)

    if category is None:
        return None

    return show_items(link, account, game=game,
                      category=None if category == "все" else category)


def ask_game(link, account):
    """Игра или приложение → {"id", "name"}, "все" или None.

    Ищем по названию, а не показываем список: игр на площадке тысячи, и
    меню из них не помещается никуда.
    """
    answer = link.ask(
        "Для какой игры или приложения объявление?\n\n"
        "Напишите название или его часть — покажу, что нашлось.\n"
        "Или нажмите «Все подряд», если объявлений немного.",
        ANSWER_WAIT,
        buttons=[[("📄 Все подряд", "все")], CANCEL])
    search = str(answer.get("text") or "").strip()

    if not search or wizard.cancelled(search):
        link.screen("Отменил.", buttons=MENU)
        return None

    if search.lower() == "все":
        return "все"

    try:
        page = account.get_games(name=search, count=MAX_CHOICES)
        games = list(getattr(page, "games", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Поиск не удался: {e}", buttons=MENU)
        return None

    if not games:
        link.screen(f"По запросу «{search}» ничего не нашлось. "
                    f"Попробуйте короче.", buttons=MENU)
        return None

    chosen = choose(link, "Что из этого?",
                    [(g.id, g.name) for g in games], PICK_GAME)

    if chosen is None:
        link.screen("Отменил.", buttons=MENU)

    return chosen


def ask_category(link, account, game):
    """Категория игры → {"id", "name"}, "все" или None."""
    try:
        found = account.get_game(id=game["id"])
        rows = list(getattr(found, "categories", None) or [])
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Категории прочитать не вышло: {e}", buttons=MENU)
        return None

    if not rows:
        # Не беда: покажем всё по игре.
        return "все"

    keys = [[(c.name, PICK_CAT + str(c.id))] for c in rows[:MAX_CHOICES]]
    keys.append([("📄 Все категории", PICK_CAT + "все")])
    keys.append([("✖️ Отмена", "отмена")])

    answer = link.ask(f"{game['name']} — какая категория?", ANSWER_WAIT,
                      buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_CAT):
        link.screen("Отменил.", buttons=MENU)
        return None

    what = text[len(PICK_CAT):]

    if what == "все":
        return "все"

    found = next((c for c in rows if str(c.id) == what), None)

    if found is None:
        link.screen("Такой категории нет.", buttons=MENU)
        return None

    return {"id": str(found.id), "name": str(found.name)}


def show_items(link, account, game=None, category=None):
    """Прочитать отобранные объявления и показать списком. → номер."""
    where = " · ".join(x["name"] for x in (game, category) if isinstance(x, dict))
    link.screen(f"Читаю объявления{' — ' + where if where else ''}…")

    try:
        items, whole = my_items(
            account,
            game_id=game["id"] if isinstance(game, dict) else "",
            category_id=category["id"] if isinstance(category, dict) else "")
    except Exception as e:                                    # noqa: BLE001
        if is_auth_error(e):
            link.screen(f"Площадка не приняла вход.\n\n{COOKIES_ADVICE}",
                        buttons=MENU)
        elif is_too_often(e):
            link.screen("Площадка просит сбавить темп: «слишком много "
                        "попыток».\n\nЭто не поломка — подождите минуту и "
                        "нажмите ещё раз.", buttons=MENU)
        else:
            link.screen(f"Объявления прочитать не вышло: {e}", buttons=MENU)

        return None

    if not items:
        link.screen(f"Объявлений{' — ' + where if where else ''} не "
                    f"нашлось.\n\nБрать за образец можно только то, что уже "
                    f"стоит на витрине.", buttons=MENU)
        return None

    if where:
        # Отбор уже сделан площадкой — делить дальше по названию незачем:
        # внутри одной категории названия у продавца совпадают до знака.
        # Отличает их номинал, по нему и раскладываем.
        return nominals_menu(link, account, items, where, whole=whole)

    return groups_menu(link, account, items, whole)


def groups_menu(link, account, items, whole: bool = True):
    """Кучки объявлений: по началу названия.

    Полсотни строк подряд, где половина выглядит одинаково, выбрать не
    помогают. Категорию площадка в списке не отдаёт, а название продавец
    придумывает сам — и до первого разделителя обычно стоит то, чем товар
    и отличается.
    """
    found = grouping.groups(items)

    if len(found) < 2:
        # Делить нечего — сразу список.
        return items_menu(link, account, items, "Все объявления", whole=whole)

    keys = []

    for number, (label, rows) in enumerate(found):
        many = plural(len(rows), "объявление", "объявления",
                      "объявлений")
        keys.append([(f"{label} — {many}", PICK_GROUP + str(number))])
    keys.append([("🔍 Найти по слову", PICK_GROUP + "find"),
                 ("📄 Все подряд", PICK_GROUP + "all")])
    keys.append([("✖️ Назад", "отмена")])

    said = (f"Ваших объявлений: {len(items)}." if whole
            else f"Прочитал {len(items)} — площадка попросила сбавить темп, "
                 f"так что список неполный. Через минуту нажмите ещё раз, "
                 f"если нужного тут нет.")
    answer = link.ask(f"{said} Разложил по началу названия — так проще "
                      f"найти нужное.", ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "")

    if not text.startswith(PICK_GROUP):
        link.screen("Отменил.", buttons=MENU)
        return None

    what = text[len(PICK_GROUP):]

    if what == "all":
        return items_menu(link, account, items, "Все объявления", whole=whole)

    if what == "find":
        answer = link.ask("Какое слово есть в названии?", ANSWER_WAIT,
                          buttons=[CANCEL])
        word = str(answer.get("text") or "")

        if wizard.cancelled(word) or not word.strip():
            return groups_menu(link, account, items, whole)

        chosen = grouping.matching(items, word)

        if not chosen:
            link.screen(f"По слову «{word}» ничего не нашлось."
                        + ("" if whole else "\n\nСписок неполный: площадка "
                           "просила сбавить темп. Попробуйте через минуту."),
                        buttons=MENU)
            return None

        return items_menu(link, account, chosen, f"Со словом «{word}»",
                          whole=whole)

    if not what.isdigit() or int(what) >= len(found):
        return groups_menu(link, account, items, whole)

    label, rows = found[int(what)]

    return nominals_menu(link, account, rows, label, whole=whole)


def nominal_of_item(item):
    """Номинал объявления по названию. → число или None.

    Описания в списке товаров площадка не отдаёт, так что читаем название
    и отдаём ему цену: в названиях денежных карт цена написана рядом с
    номиналом («Apple Gift Card 10$ за 900 рублей»), и без подсказки
    самым крупным числом оказалась бы она.
    """
    try:
        return nominal_from_title(str(getattr(item, "name", "") or ""),
                                  getattr(item, "price", None))
    except Exception:                                         # noqa: BLE001
        return None


def nominal_label(value, rows) -> str:
    """Подпись кучки: «1000 Robux», «10$», «Без номинала»."""
    if value is None:
        return "Без номинала"

    name = str(getattr(rows[0], "name", "") or "") if rows else ""
    # Регион у чужого объявления не спрошен, и выдумывать его нельзя:
    # знак валюты не того региона врёт про товар. Без региона
    # `nominal_shown` берёт меру самой карты, а для незнакомой — просто
    # число.
    return nominal_shown(card_for_title(CARDS, name), value) \
        or shown_number(value)


def nominals_menu(link, account, items, title: str, number: int = 0,
                  whole: bool = True):
    """Кучки объявлений по номиналу. → номер выбранного или None.

    Внутри одной категории названия у продавца одинаковые: полстраницы
    «🥳ПРОМОКОДОМ🥳 АВТОВЫДАЧА😎» подряд, и отличает их только номинал.
    Показать такой список плоским значит заставить выбирать вслепую.
    """
    found = grouping.nominals(items, nominal_of_item)

    if len(found) < 2:
        # Номинал один или не прочитался ни у кого — делить нечего.
        return items_menu(link, account, items, title, whole=whole)

    # Номиналов у карт бывает больше, чем влезает на экран телефона, —
    # тогда листаем, а не обрезаем: обрезанный список молча прячет
    # половину витрины.
    shown, more, pages = grouping.page(list(enumerate(found)), number, PAGE)
    keys = []

    for place, (value, rows) in shown:
        many = plural(len(rows), "объявление", "объявления", "объявлений")
        keys.append([(f"{nominal_label(value, rows)} — {many}",
                      PICK_NOM + str(place))])

    row = []

    if number > 0:
        row.append(("⬅️ Назад", PICK_NOM + "p" + str(number - 1)))

    if more:
        row.append(("Ещё ➡️", PICK_NOM + "p" + str(number + 1)))

    if row:
        keys.append(row)

    keys.append([("🔍 Найти по слову", PICK_NOM + "find"),
                 ("📄 Все подряд", PICK_NOM + "all")])
    keys.append([("✖️ Отмена", "отмена")])

    warn = ("" if whole else
            "\n\n⚠️ Список неполный: площадка попросила сбавить темп. "
            "Через минуту нажмите ещё раз, если нужного тут нет.")
    leaf = f" — страница {number + 1} из {pages}" if pages > 1 else ""
    answer = link.ask(f"{title} — объявлений {len(items)}{leaf}.\n\nРазложил "
                      f"по номиналам. Какой берём за образец?{warn}",
                      ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if not text.startswith(PICK_NOM):
        link.screen("Отменил.", buttons=MENU)
        return None

    what = text[len(PICK_NOM):]

    if what.startswith("p") and what[1:].isdigit():
        return nominals_menu(link, account, items, title, int(what[1:]), whole)

    if what == "all":
        return items_menu(link, account, items, title, whole=whole)

    if what == "find":
        answer = link.ask("Какое слово есть в названии?", ANSWER_WAIT,
                          buttons=[CANCEL])
        word = str(answer.get("text") or "")

        if wizard.cancelled(word) or not word.strip():
            return nominals_menu(link, account, items, title,
                                 number, whole)

        chosen = grouping.matching(items, word)

        if not chosen:
            link.screen(f"По слову «{word}» ничего не нашлось."
                        + ("" if whole else "\n\nСписок неполный: площадка "
                           "просила сбавить темп. Попробуйте через минуту."),
                        buttons=MENU)
            return None

        return items_menu(link, account, chosen, f"Со словом «{word}»",
                          whole=whole)

    if not what.isdigit() or int(what) >= len(found):
        return nominals_menu(link, account, items, title, number,
                             whole)

    value, rows = found[int(what)]

    return items_menu(link, account, rows,
                      f"{title} · {nominal_label(value, rows)}", whole=whole)


def items_menu(link, account, items, title: str, number: int = 0,
               whole: bool = True):
    """Список объявлений с листанием. → номер выбранного или None."""
    shown, more, total = grouping.page(items, number, PAGE)
    keys = [[(f"{getattr(i, 'price', '?')} ₽ · {grouping.head(i.name)}",
              PICK_LIVE + str(i.id))] for i in shown]
    row = []

    if number > 0:
        row.append(("⬅️ Назад", PICK_PAGE + str(number - 1)))

    if more:
        row.append(("Ещё ➡️", PICK_PAGE + str(number + 1)))

    if row:
        keys.append(row)

    keys.append([("✖️ Отмена", "отмена")])
    where = f" — страница {number + 1} из {total}" if total > 1 else ""
    # Про неполный список говорим и здесь: кучка может быть одна, и тогда
    # экрана с кучками продавец не увидит вовсе. Молча показать половину —
    # значит дать решить, что остального нет.
    warn = ("" if whole else
            "\n\n⚠️ Список неполный: площадка попросила сбавить темп. "
            "Через минуту нажмите ещё раз, если нужного тут нет.")
    answer = link.ask(f"{title}{where}\n\nКакое повторить?{warn}",
                      ANSWER_WAIT, buttons=keys)
    text = str(answer.get("text") or "").strip()

    if text.startswith(PICK_PAGE):
        return items_menu(link, account, items, title,
                          int(text[len(PICK_PAGE):]), whole)

    if not text.startswith(PICK_LIVE):
        link.screen("Отменил.", buttons=MENU)
        return None

    return text[len(PICK_LIVE):]


def make_copy(link, account, item_id: str) -> None:
    """Прочитать объявление целиком и создать такое же."""
    link.screen("Читаю объявление…")

    try:
        item = account.get_item(item_id)
    except Exception as e:                                    # noqa: BLE001
        link.screen(f"Объявление прочитать не вышло: {e}", buttons=MENU)
        return

    plan, gaps = copyitem.plan(item)

    if gaps:
        # Подставить недостающее нельзя: товар не там, где хотели, дороже
        # несозданного.
        link.screen(f"Скопировать не выйдет — площадка не отдала: "
                    f"{', '.join(gaps)}.\n\nСоздайте товар заново через "
                    f"«➕ Новый товар».", buttons=MENU)
        return

    link.screen(f"Скачиваю картинки ({len(plan['photos'])})…")
    photos = fetch_photos(plan["photos"])

    if not photos:
        link.screen("Картинки скачать не вышло — без них площадка товар не "
                    "примет.", buttons=MENU)
        return

    draft = wizard.Draft()
    draft.game = plan["game"]
    draft.category = plan["category"]
    draft.obtaining = plan["obtaining"]
    draft.name = plan["name"]
    draft.price = plan["price"]
    draft.region = plan["region"]
    draft.nominal = plan["nominal"]
    draft.options = plan["options"]
    draft.fields = plan["fields"]
    draft.photos = photos

    # Описание прогоняем тем же разбором, что и набранное руками: он
    # вырезает строки про регион и номинал. Скопированные как есть, они
    # встали бы вторыми рядом с нашими, и движок выдачи прочитал бы не ту.
    body, why = wizard.accept_description(
        str(getattr(item, "description", "") or ""))
    draft.description = "" if why else body

    ledger = ledger_of()

    if ledger.rules()["vary"]:
        vary.apply(draft.fields, ROTATION)

    live = live_counts(account, plan["game"], plan["category"])
    draft.price, up = ledger.price_for(draft.nominal or draft.price,
                                       draft.price, live)
    note = (f"\n\nЦена поднята на {up} ₽: столько же бесплатных по прежней "
            f"цене уже висит." if up else "")

    answer = link.ask(
        f"Повторю это объявление:\n\n{draft.summary()}{note}\n\nСоздаём?",
        ANSWER_WAIT, buttons=[[("✅ Да, создать", "да")], CANCEL])

    if str(answer.get("text") or "").strip().lower() != "да":
        link.screen("Отменил. Ничего не создано.", buttons=MENU)
        return

    link.screen("Создаю черновик…")

    if send_draft(link, account, draft):
        ledger.remember(draft.nominal or draft.price, draft.price)
        offer_template(link, draft)


def fetch_photos(urls) -> list:
    """Скачать картинки объявления. → байты, что получилось.

    Молча пропускаем то, что не скачалось: одна битая ссылка из пяти не
    повод отказываться от копии. А вот ни одной — повод, и об этом скажет
    вызывающий.
    """
    try:
        import requests
    except ImportError:
        return []

    out = []

    for url in urls:
        try:
            answer = requests.get(url, timeout=30,
                                  headers={"User-Agent": DEFAULT_UA})
        except Exception:                                     # noqa: BLE001
            continue

        if answer.status_code == 200 and answer.content:
            out.append(answer.content)

    return out


def settings_of() -> Settings:
    """Настройки автовыдачи текущего кабинета.

    Поверх того самого файла состояния, который читает движок выдачи: свой
    файл настроек стал бы вторым источником правды, и получилось бы
    «поменял в боте, а выдача по-старому».
    """
    current = AccountStore(ACCOUNTS_DIR).current()
    name = current.id if current else "default"
    path = statepath.settings_file(name, SETTINGS_DIR)

    # Старый файл движка вливаем и здесь: продавец может открыть настройки
    # раньше, чем движок успеет запуститься, и тогда он увидел бы журнал
    # без уже выданных заказов.
    statepath.absorb_legacy(path)

    return Settings(JsonStore(path))


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

    # Регионы берём те, что есть у поставщика для этой карты. Держать
    # вечные «GL и RU» значило бы, что привязать услугу для US или SA
    # нельзя вовсе, хотя номиналы там есть, — а именно за этим на такой
    # экран и приходят.
    where = regions_of(conf, card)

    for region in where:
        got = conf.service_id(card.slug, region)
        lines.append(f"{region}: {got or '— не задана'}")

    keys = []
    row = []

    for region in where:
        row.append((f"🧾 {region}", PICK_SET + "svc" + region))

        if len(row) == 3:
            keys.append(row)
            row = []

    if row:
        keys.append(row)

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
    forget_items()
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
            or text in BLANK_WORDS or text in SERIES_WORDS \
            or text in COPY_WORDS:
        if account is None:
            link.screen("Сначала нужен рабочий кабинет: откройте "
                        "«Аккаунт».", buttons=MENU)
            return account

    # Ни одна ветка не дописывает «Готов к следующему» поверх: экран
    # переписывает ТО ЖЕ сообщение, и такая приписка стирала последнее
    # слово обработчика. Со стороны это выглядело как «нажал — сообщение
    # сразу убралось»: «Шаблонов пока нет» показывалось и исчезало.
    # Поэтому каждая ветка сама заканчивает экраном с меню.
    if text in START_WORDS:
        make_item(link, account)
    elif text in BLANK_WORDS:
        make_blank(link, account)
    elif text in SERIES_WORDS:
        series_menu(link, account)
    elif text in TEMPLATE_WORDS:
        from_template(link, account)
    elif text in DRAFT_WORDS:
        drafts_menu(link, account)
    elif text in COPY_WORDS:
        copy_live(link, account)
    elif text in COPIES_WORDS:
        copies_menu(link)
    elif text in SETTINGS_WORDS:
        settings_menu(link)
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
