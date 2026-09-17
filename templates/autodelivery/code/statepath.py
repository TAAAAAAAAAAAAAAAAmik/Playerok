"""Где лежит состояние выдачи. Один ответ на весь проект.

Здесь и настройки («что включено», «каким словом узнавать своё
объявление», «какой услугой поставщика покупать»), и журнал выдач —
номера уже выданных заказов и незаконченные покупки.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Путь считался в двух местах по-разному, и
считался по-разному годами не заметно: телеграм-бот писал настройки в
`state/delivery/<кабинет>.json`, а движок выдачи читал
`state/seller-1.json`. Продавец включал карту, бот отвечал «включено»,
doctor подтверждал «включена» — а движок смотрел в другой файл, видел
пустоту и молча пропускал каждый оплаченный заказ. Ни ошибки, ни строчки
в журнале: `pick_card` просто не признавал товар своим.

Поэтому путь теперь один и считается тут. Добавляете новое место, где
нужно состояние, — зовите отсюда, а не собирайте путь заново.

ПРО СТАРЫЙ ФАЙЛ. Он мог накопить номера выданных заказов, и потерять их
нельзя: движок узнаёт по ним, что заказ уже отдан. Начав с пустого
списка, бот купил бы те же коды второй раз, за настоящие деньги.
Поэтому старый файл не удаляется, а вливается в нынешний — один раз, с
объединением журналов, — и лишь потом отодвигается в сторону.
"""
from __future__ import annotations

import copy
import json
import os

from store import DELIVERED_MAX, LOG_MAX, STATE_DONE

# Папка проекта. Пути считаем от неё, а не от текущей: боты запускаются и
# сторожем, и руками, и из крона, и «state/…» в этих случаях указывает в
# разные места.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Как файл состояния назывался, пока движок ходил своей дорогой.
LEGACY_NAME = "seller-1.json"

# Куда отодвигаем старый файл после переноса. Не удаляем: в нём деньги —
# номера выданных заказов, и если перенос вышел кривым, вернуть их надо
# откуда-то.
MOVED_SUFFIX = ".перенесён"


def in_project(path: str) -> str:
    """Путь от папки проекта, если он не задан от корня.

    «state/…» из сторожа, из крона и набранное руками из домашней папки —
    это три разных места. Состояние там раздваивается тихо: бот пишет в
    одно, выдача читает другое, и оба выглядят исправными.
    """
    return path if os.path.isabs(path) else os.path.join(HERE, path)


# Короткое имя для своих: снаружи читается хуже, внутри — привычнее.
_abs = in_project


def state_dir() -> str:
    """Папка состояния выдачи."""
    return _abs(os.environ.get("PLAYEROK_STATE", "state/delivery"))


def accounts_dir() -> str:
    """Папка сохранённых кабинетов."""
    return _abs(os.environ.get("PLAYEROK_ACCOUNTS", "state/accounts"))


def current_id() -> str:
    """Кабинет, которым сейчас работаем."""
    from accounts import AccountStore

    try:
        current = AccountStore(accounts_dir()).current()
    except Exception:                                         # noqa: BLE001
        current = None

    return current.id if current else "default"


def settings_file(account_id: str = "", state: str = "") -> str:
    """Файл состояния кабинета. Тот самый, единственный.

    `state` — папка, если она уже посчитана вызывающим. Относительную
    приводим к папке проекта: «state/delivery» из крона и из сторожа
    указывает в разные места, и состояние тогда раздваивается так же
    тихо, как раздвоилось однажды.
    """
    where = _abs(state) if state else state_dir()

    return os.path.join(where, f"{account_id or current_id()}.json")


def legacy_file() -> str:
    """Старый файл движка — если он ещё лежит на диске."""
    return _abs(os.path.join("state", LEGACY_NAME))


# ---------------------------------------------------------------------------
# Перенос старого файла
# ---------------------------------------------------------------------------


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


def _delivered(*lists) -> list:
    """Объединение номеров выданных заказов. Порядок не теряем."""
    out: list = []
    seen: set = set()

    for rows in lists:
        for value in rows or []:
            key = str(value)

            if key in seen:
                continue

            seen.add(key)
            out.append(key)

    return out[-DELIVERED_MAX:]


def _better(first: dict, second: dict) -> dict:
    """Из двух записей об одном заказе — та, которой можно верить.

    Доведённая до конца сильнее незаконченной: иначе возобновление взялось
    бы доделывать уже выданное. При равенстве — свежая по времени.
    """
    done = [e for e in (first, second) if str(e.get("state")) == STATE_DONE]

    if len(done) == 1:
        return done[0]

    return first if float(first.get("at") or 0) >= float(
        second.get("at") or 0) else second


def _log(*lists) -> list:
    """Объединение журналов: по одной записи на заказ, свежие первыми."""
    best: dict = {}

    for rows in lists:
        for entry in rows or []:
            if not isinstance(entry, dict):
                continue

            key = str(entry.get("order") or "")

            if not key:
                continue

            best[key] = _better(best[key], entry) if key in best else entry

    rows = sorted(best.values(), key=lambda e: float(e.get("at") or 0),
                  reverse=True)

    return rows[:LOG_MAX]


def merged(target: dict, legacy: dict) -> dict:
    """Нынешнее состояние + старое. → что записать.

    Настройки берём из НЫНЕШНЕГО: их только что задавал продавец в боте, и
    старый файл о них ничего нового не знает. А журналы объединяем: там
    деньги.
    """
    out = copy.deepcopy(legacy)
    out.update({k: v for k, v in target.items() if k != "cards"})
    cards = out.setdefault("cards", {})

    if not isinstance(cards, dict):
        cards = out["cards"] = {}

    for slug, fresh in (target.get("cards") or {}).items():
        if not isinstance(fresh, dict):
            continue

        old = cards.get(slug)
        old = old if isinstance(old, dict) else {}
        card = dict(old)
        card.update({k: v for k, v in fresh.items()
                     if k not in ("delivered", "log")})
        card["delivered"] = _delivered(old.get("delivered"),
                                       fresh.get("delivered"))
        card["log"] = _log(old.get("log"), fresh.get("log"))
        cards[slug] = card

    # Карты, которые есть только в старом файле, остаются как были: это
    # журнал по товару, который продавец в боте ещё не настраивал.
    for slug, old in list(cards.items()):
        if isinstance(old, dict):
            old.setdefault("delivered", [])
            old.setdefault("log", [])

    return out


def absorb_legacy(target: str = "", legacy: str = "") -> str:
    """Влить старый файл движка в нынешний. → куда его отодвинули.

    Пустая строка — вливать было нечего. Зовётся при запуске движка, а не
    по расписанию: перенос должен случиться ровно один раз и до первой
    выдачи.
    """
    target = target or settings_file()
    legacy = legacy or legacy_file()

    if not os.path.exists(legacy):
        return ""

    old = _read(legacy)

    if not old:
        # Пустой или битый — сливать нечего, но и трогать не будем: пусть
        # лежит, разберётся человек.
        return ""

    data = merged(_read(target), old)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp, target)

    moved = legacy + MOVED_SUFFIX
    os.replace(legacy, moved)

    return moved


def open_store(account_id: str = ""):
    """Хранилище состояния выдачи, с перенесённым старым файлом."""
    from store import JsonStore

    path = settings_file(account_id)
    absorb_legacy(path)

    return JsonStore(path)
