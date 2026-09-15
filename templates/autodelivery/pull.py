"""Обновление кода с GitHub без git.

На сервере git может быть не установлен, а прав поставить его — не быть.
Этот скрипт заменяет `git pull`: скачивает архив ветки через GitHub API и
раскладывает файлы поверх установленных.

    GITHUB_TOKEN=... python3 pull.py

Токен — «fine-grained», только на чтение (Contents: Read-only) и только
этого репозитория. Держать его удобно там же, где остальные секреты:

    GITHUB_TOKEN=github_pat_...

в .env рядом, с правами 600.

Чего скрипт не делает намеренно: не трогает .env и state/. В первом лежат
ключи, во втором — журнал уже выданных заказов, и затереть его означало бы
купить те же коды второй раз.
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

REPO = "TAAAAAAAAAAAAAAAAmik/Playerok"
BRANCH = "claude/github-taxi-project-ho7q97"

# Какая часть репозитория нас касается. Остальное — исходники приложения
# такси, серверу автовыдачи они не нужны.
SUBDIR = "templates/autodelivery"

# Что не перезаписывать ни при каких обстоятельствах.
KEEP = {".env", "state", "__pycache__", ".venv"}

HERE = os.path.dirname(os.path.abspath(__file__))


def download(token: str, into: str) -> str:
    """Архив ветки в файл. Возвращает путь к нему."""
    url = f"https://api.github.com/repos/{REPO}/tarball/{BRANCH}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "autodelivery-pull",
    })
    path = os.path.join(into, "update.tar.gz")

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with open(path, "wb") as f:
                shutil.copyfileobj(response, f)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SystemExit(
                "GitHub не принял токен. Нужен fine-grained токен с правом "
                "Contents: Read-only на этот репозиторий, и он не должен "
                "быть просрочен.")

        if e.code == 404:
            raise SystemExit(
                f"GitHub не нашёл {REPO} или ветку {BRANCH}. Если токен "
                "fine-grained — проверьте, что этот репозиторий добавлен "
                "в список доступных ему.")

        raise SystemExit(f"GitHub ответил {e.code}: {e.reason}")
    except Exception as e:                                   # noqa: BLE001
        raise SystemExit(f"Не удалось скачать обновление: {e}")

    return path


def unpack(archive: str, into: str) -> str:
    """Распаковать и вернуть путь к нашей части репозитория.

    Имена из архива проверяются: путь с «..» или ведущим «/» вылезет за
    пределы каталога и перепишет что угодно на диске.
    """
    with tarfile.open(archive) as tar:
        members = []

        for member in tar.getmembers():
            target = os.path.normpath(os.path.join(into, member.name))

            if not target.startswith(os.path.abspath(into) + os.sep):
                raise SystemExit(
                    f"В архиве путь, ведущий наружу: {member.name}. "
                    "Обновление отменено.")

            members.append(member)

        tar.extractall(into, members)

    # GitHub кладёт всё в одну папку со случайным именем.
    roots = [os.path.join(into, name) for name in os.listdir(into)
             if os.path.isdir(os.path.join(into, name))]

    if len(roots) != 1:
        raise SystemExit("Архив выглядит не так, как ожидалось.")

    source = os.path.join(roots[0], *SUBDIR.split("/"))

    if not os.path.isdir(source):
        raise SystemExit(f"В архиве нет {SUBDIR}.")

    return source


def lay_over(source: str, target: str) -> list[str]:
    """Разложить скачанное поверх установленного. Возвращает что обновилось."""
    changed = []

    for folder, _, files in os.walk(source):
        relative = os.path.relpath(folder, source)
        parts = [] if relative == "." else relative.split(os.sep)

        if parts and parts[0] in KEEP:
            continue

        destination = os.path.join(target, *parts)
        os.makedirs(destination, exist_ok=True)

        for name in files:
            if name in KEEP:
                continue

            old = os.path.join(destination, name)
            new = os.path.join(folder, name)

            if os.path.exists(old) and _same(old, new):
                continue

            shutil.copy2(new, old)
            changed.append(os.path.join(*(parts + [name])))

    return changed


def _same(one: str, other: str) -> bool:
    with open(one, "rb") as a, open(other, "rb") as b:
        return a.read() == b.read()


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not token:
        raise SystemExit(
            "Нет GITHUB_TOKEN. Заведите fine-grained токен на github.com "
            "(Settings → Developer settings → Personal access tokens), дайте "
            "ему Contents: Read-only на этот репозиторий и положите в .env "
            "строкой GITHUB_TOKEN=...")

    with tempfile.TemporaryDirectory() as workspace:
        print(f"Скачиваю {REPO}, ветка {BRANCH}…")
        source = unpack(download(token, workspace), workspace)
        changed = lay_over(source, HERE)

    if not changed:
        print("Уже свежее некуда — ничего не изменилось.")
        return

    print(f"Обновлено файлов: {len(changed)}")

    for name in sorted(changed):
        print("  ", name)

    print("\n.env и state/ не тронуты.")


if __name__ == "__main__":
    main()
