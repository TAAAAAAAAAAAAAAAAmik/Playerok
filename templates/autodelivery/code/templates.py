"""Шаблоны товаров: сохранить готовое объявление и повторить его.

Продавец выставляет одно и то же по многу раз. Собирать каждый раз
название, цену, регион и фотографии заново — это минуты вместо секунд.

Что лежит в шаблоне. Название, цена, регион и сами картинки файлами.
Картинки именно копией, а не ссылкой на товар: товар продадут, снимут или
отклонят, а шаблон должен работать и через полгода.

БЕЗОПАСНОСТЬ. Номер шаблона приходит из кнопки, то есть снаружи. Он
проверяется перед тем, как попасть в путь к файлу: без этого «../../» в
номере читало и писало бы что угодно на диске.
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
        return f"{self.name} — {self.price} ₽ ({self.region})"


class TemplateStore:
    """Шаблоны на диске: папка на каждый."""

    def __init__(self, folder: str):
        self.folder = folder

    def _path(self, template_id: str) -> str:
        """Папка шаблона. Только для проверенного номера."""
        if not valid_id(template_id):
            raise ValueError("не наш номер шаблона")

        return os.path.join(self.folder, template_id)

    def save(self, name: str, price: int, region: str, photos: list) -> str:
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
                "photos": files, "at": time.time()}

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

    def remove(self, template_id: str) -> bool:
        """Удалить шаблон. → получилось ли."""
        if not valid_id(template_id):
            return False

        try:
            shutil.rmtree(os.path.join(self.folder, template_id))
            return True
        except OSError:
            return False
