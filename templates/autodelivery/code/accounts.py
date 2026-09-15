"""Несколько аккаунтов площадки и переключение между ними.

Продавец может держать не один кабинет. Куки у каждого свои, и
user-agent тоже свой: площадка сверяет его с тем, при котором куки
выданы.

ВАЖНОЕ ОГРАНИЧЕНИЕ БИБЛИОТЕКИ. `playerokapi.Account` — синглтон: второй
вызов возвращает ТОТ ЖЕ объект, переписав в нём поля. Значит два кабинета
одновременно в одном процессе жить не могут, и держать на них две ссылки
бессмысленно — обе покажут последний. Отсюда и устройство: «текущий»
аккаунт ровно один, переключение его заменяет.

БЕЗОПАСНОСТЬ. Куки — это доступ к кабинету, поэтому файл пишется с
правами 600. Номер аккаунта приходит из кнопки, то есть снаружи, и
проверяется перед тем, как попасть в путь к файлу.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import time

ID = re.compile(r"^[0-9a-f]{8}$")
CARD = "account.json"
CURRENT = "current"


def new_id() -> str:
    return secrets.token_hex(4)


def valid_id(value: str) -> bool:
    return bool(ID.match(str(value or "")))


class Saved:
    """Сохранённый кабинет."""

    def __init__(self, id: str, card: dict):                 # noqa: A002
        self.id = id
        self.name = str(card.get("name") or "без названия")
        self.cookies = str(card.get("cookies") or "")
        self.user_agent = str(card.get("user_agent") or "")
        self.at = float(card.get("at") or 0)

    def label(self) -> str:
        return self.name


class AccountStore:
    """Кабинеты на диске: папка на каждый плюс отметка о текущем."""

    def __init__(self, folder: str):
        self.folder = folder

    # ---------- чтение ----------

    def get(self, account_id: str):
        if not valid_id(account_id):
            return None

        path = os.path.join(self.folder, account_id, CARD)

        try:
            with open(path, encoding="utf-8") as f:
                return Saved(account_id, json.load(f))
        except (OSError, ValueError):
            return None

    def all(self) -> list:
        """Все кабинеты, свежие первыми."""
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []

        found = [a for a in (self.get(n) for n in names if valid_id(n)) if a]

        return sorted(found, key=lambda a: a.at, reverse=True)

    def current(self):
        """Текущий кабинет или None.

        Если отметка указывает на удалённый — возвращаем первый из
        оставшихся: лучше работать не с тем, чем не работать вовсе, и
        продавец увидит имя в меню.
        """
        marked = ""

        try:
            with open(os.path.join(self.folder, CURRENT),
                      encoding="utf-8") as f:
                marked = f.read().strip()
        except OSError:
            pass

        found = self.get(marked) if marked else None

        if found is not None:
            return found

        saved = self.all()

        return saved[0] if saved else None

    # ---------- запись ----------

    def add(self, name: str, cookies: str, user_agent: str) -> str:
        """Сохранить кабинет. → номер."""
        account_id = new_id()
        path = os.path.join(self.folder, account_id)
        os.makedirs(path, exist_ok=True)
        self._write(os.path.join(path, CARD), json.dumps(
            {"name": name, "cookies": cookies, "user_agent": user_agent,
             "at": time.time()}, ensure_ascii=False))

        return account_id

    def update_cookies(self, account_id: str, cookies: str) -> bool:
        """Заменить куки кабинета — они протухают чаще всего остального."""
        saved = self.get(account_id)

        if saved is None:
            return False

        path = os.path.join(self.folder, account_id, CARD)
        self._write(path, json.dumps(
            {"name": saved.name, "cookies": cookies,
             "user_agent": saved.user_agent, "at": saved.at},
            ensure_ascii=False))

        return True

    def set_current(self, account_id: str) -> bool:
        if self.get(account_id) is None:
            return False

        os.makedirs(self.folder, exist_ok=True)
        self._write(os.path.join(self.folder, CURRENT), account_id)

        return True

    def remove(self, account_id: str) -> bool:
        if not valid_id(account_id):
            return False

        try:
            shutil.rmtree(os.path.join(self.folder, account_id))
            return True
        except OSError:
            return False

    @staticmethod
    def _write(path: str, text: str) -> None:
        """Запись с правами 600 и атомарной заменой.

        Права обязательны: в файле лежит доступ к кабинету. Атомарность —
        чтобы обрыв не оставил половину файла вместо рабочих куки.
        """
        folder = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")

        try:
            os.fchmod(fd, 0o600)

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
