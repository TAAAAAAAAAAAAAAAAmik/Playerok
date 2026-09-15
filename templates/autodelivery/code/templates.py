"""Шаблоны товаров: сохранить готовое объявление и повторить его.

Продавец выставляет одно и то же по многу раз. Собирать каждый раз
название, цену, регион и фотографии заново — это минуты вместо секунд.

Что лежит в шаблоне. Игра, категория, способ получения, характеристики,
название, цена, регион, описание, поля площадки и сами картинки файлами. Категория
особенно важна: из-за неё шаблон и экономит больше всего нажатий.
Картинки именно копией, а не ссылкой на товар: товар продадут, снимут или
отклонят, а шаблон должен работать и через полгода.

ШАБЛОНЫ У КАЖДОГО КАБИНЕТА СВОИ. Папка на кабинет, а не общая куча:
товары у разных кабинетов разные, и перемешанные шаблоны означают
объявление, созданное не там, где хотели. Путь собирает `folder_for`.

БЕЗОПАСНОСТЬ. Номер шаблона приходит из кнопки, то есть снаружи. Он
проверяется перед тем, как попасть в путь к файлу: без этого «../../» в
номере читало и писало бы что угодно на диске. То же и для номера
кабинета, из которого собирается путь к его шаблонам.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time

# Номер шаблона: только шестнадцатеричные цифры и ровно столько, сколько
# мы сами выдаём. Всё остальное — не наш номер.
ID = re.compile(r"^[0-9a-f]{8}$")

CARD = "template.json"

# По расширению угадывается тип картинки: площадка смотрит на содержимое,
# но человеку, заглянувшему в папку, имя файла говорит больше.
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def new_id() -> str:
    return secrets.token_hex(4)


def valid_id(value: str) -> bool:
    return bool(ID.match(str(value or "")))


# Куда складывать шаблоны, пока кабинетов не заведено ни одного.
NO_ACCOUNT = "default"


def folder_for(root: str, account_id: str = "") -> str:
    """Папка шаблонов кабинета.

    Номер кабинета тоже приходит снаружи, поэтому непроверенный не
    попадает в путь: такие шаблоны просто лягут в общую папку, а не в
    произвольное место на диске.
    """
    return os.path.join(root, account_id if valid_id(account_id)
                        else NO_ACCOUNT)


def extension(data: bytes) -> str:
    for signature, ext in SIGNATURES:
        if data.startswith(signature):
            return ext

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    return "bin"


class Template:
    """Сохранённое объявление."""

    def __init__(self, id: str, folder: str, card: dict):    # noqa: A002
        self.id = id
        self.folder = folder
        self.name = str(card.get("name") or "")
        self.price = int(card.get("price") or 0)
        self.region = str(card.get("region") or "")
        self.description = str(card.get("description") or "")
        self.game = card.get("game") or None
        self.category = card.get("category") or None
        self.obtaining = card.get("obtaining") or None
        self.fields = list(card.get("fields") or [])
        self.options = list(card.get("options") or [])
        self.files = [str(f) for f in (card.get("photos") or [])]
        self.at = float(card.get("at") or 0)

    def photos(self) -> list:
        """Картинки байтами. Пропавшие файлы пропускаем молча —
        объявление с одной картинкой лучше, чем отказ."""
        images = []

        for name in self.files:
            path = os.path.join(self.folder, os.path.basename(name))

            try:
                with open(path, "rb") as f:
                    images.append(f.read())
            except OSError:
                continue

        return images

    def label(self) -> str:
        """Подпись для кнопки."""
        where = (self.category or {}).get("name") or self.region

        return f"{self.name} — {self.price} ₽ ({where})"

    def complete(self) -> bool:
        """Хватает ли шаблона, чтобы создать товар без вопросов.

        Шаблоны, сохранённые до того, как бот научился спрашивать
        категорию, её не содержат — и повторять их нечем.
        """
        return bool((self.category or {}).get("id")
                    and (self.obtaining or {}).get("id"))


class TemplateStore:
    """Шаблоны на диске: папка на каждый."""

    def __init__(self, folder: str):
        self.folder = folder

    def _path(self, template_id: str) -> str:
        """Папка шаблона. Только для проверенного номера."""
        if not valid_id(template_id):
            raise ValueError("не наш номер шаблона")

        return os.path.join(self.folder, template_id)

    def save(self, name: str, price: int, region: str, photos: list,
             description: str = "", game=None, category=None,
             obtaining=None, fields=None, options=None) -> str:
        """Сохранить объявление шаблоном. → номер."""
        template_id = new_id()
        path = self._path(template_id)
        os.makedirs(path, exist_ok=True)
        files = []

        for number, data in enumerate(photos or [], start=1):
            if not data:
                continue

            file_name = f"photo-{number}.{extension(data)}"

            with open(os.path.join(path, file_name), "wb") as f:
                f.write(data)

            files.append(file_name)

        card = {"name": name, "price": int(price), "region": region,
                "description": description or "", "photos": files,
                "game": game, "category": category, "obtaining": obtaining,
                "fields": list(fields or []),
                "options": list(options or []), "at": time.time()}

        with open(os.path.join(path, CARD), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)

        return template_id

    def get(self, template_id: str):
        """Шаблон по номеру или None."""
        if not valid_id(template_id):
            return None

        path = os.path.join(self.folder, template_id)

        try:
            with open(os.path.join(path, CARD), encoding="utf-8") as f:
                return Template(template_id, path, json.load(f))
        except (OSError, ValueError):
            return None

    def all(self) -> list:
        """Все шаблоны, свежие первыми."""
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []

        found = [t for t in (self.get(n) for n in names if valid_id(n)) if t]

        return sorted(found, key=lambda t: t.at, reverse=True)

    def update(self, template_id: str, **changes) -> bool:
        """Поправить сохранённое. → получилось ли.

        Меняем только названное: шаблон правят по одному полю, и переписать
        его целиком значило бы потерять всё остальное при первой же
        опечатке.
        """
        template = self.get(template_id)

        if template is None:
            return False

        path = os.path.join(self.folder, template_id, CARD)

        try:
            with open(path, encoding="utf-8") as f:
                card = json.load(f)
        except (OSError, ValueError):
            return False

        for key, value in changes.items():
            if value is not None:
                card[key] = value

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False)
        except OSError:
            return False

        return True

    def set_photos(self, template_id: str, photos: list) -> bool:
        """Заменить картинки шаблона целиком.

        Именно целиком: дописывать к старым — значит копить мусор, который
        никто не разберёт, а удалять по одной с телефона мучительно.
        """
        template = self.get(template_id)

        if template is None:
            return False

        path = os.path.join(self.folder, template_id)

        for old in template.files:
            try:
                os.unlink(os.path.join(path, os.path.basename(old)))
            except OSError:
                pass

        files = []

        for number, data in enumerate(photos or [], start=1):
            if not data:
                continue

            name = f"photo-{number}.{extension(data)}"

            with open(os.path.join(path, name), "wb") as f:
                f.write(data)

            files.append(name)

        return self.update(template_id, photos=files)

    def remove(self, template_id: str) -> bool:
        """Удалить шаблон. → получилось ли."""
        if not valid_id(template_id):
            return False

        try:
            shutil.rmtree(os.path.join(self.folder, template_id))
            return True
        except OSError:
            return False
