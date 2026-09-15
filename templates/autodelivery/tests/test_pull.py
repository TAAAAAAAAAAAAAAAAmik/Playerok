"""Тесты обновления кода без git. Сети не требуют — архив подделан.

Проверяется то, что здесь стоит дорого: журнал выдач и ключи переживают
обновление, а архив не может записать файл мимо каталога установки.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pull                                                     # noqa: E402


def github_style_archive(folder: str, files: dict) -> str:
    """Архив в том виде, в каком его отдаёт GitHub: всё в одной папке со
    случайным именем, внутри — весь репозиторий."""
    root = os.path.join(folder, "src", "repo-abc123")

    for name, text in files.items():
        path = os.path.join(root, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    archive = os.path.join(folder, "update.tar.gz")

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname="repo-abc123")

    return archive


class LayOverTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.install = os.path.join(self.folder, "install")
        os.makedirs(os.path.join(self.install, "state"))

        self.write(".env", "TELEGRAM_BOT_TOKEN=секрет\n")
        self.write("state/seller-1.json", '{"выдано": ["заказ-7"]}\n')
        self.write("watch.py", "старый watch\n")

        self.archive = github_style_archive(self.folder, {
            "templates/autodelivery/watch.py": "новый watch\n",
            "templates/autodelivery/code/owner.py": "новый owner\n",
            "templates/autodelivery/.env": "TELEGRAM_BOT_TOKEN=чужой\n",
            "templates/autodelivery/state/seller-1.json": '{"из": "архива"}\n',
            "README.md": "не наше\n",
        })

    def write(self, name, text):
        path = os.path.join(self.install, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, name):
        with open(os.path.join(self.install, *name.split("/")),
                  encoding="utf-8") as f:
            return f.read()

    def update(self):
        work = tempfile.mkdtemp()
        shutil.copy(self.archive, os.path.join(work, "update.tar.gz"))
        source = pull.unpack(os.path.join(work, "update.tar.gz"), work)

        return pull.lay_over(source, self.install)

    def test_code_is_replaced(self):
        changed = self.update()

        self.assertEqual(self.read("watch.py"), "новый watch\n")
        self.assertIn("watch.py", changed)
        self.assertIn(os.path.join("code", "owner.py"), changed)

    def test_journal_of_delivered_orders_survives(self):
        """Затереть журнал значит купить те же коды второй раз."""
        self.update()

        self.assertEqual(self.read("state/seller-1.json"),
                         '{"выдано": ["заказ-7"]}\n')

    def test_secrets_survive(self):
        self.update()

        self.assertEqual(self.read(".env"), "TELEGRAM_BOT_TOKEN=секрет\n")

    def test_second_run_changes_nothing(self):
        self.update()

        self.assertEqual(self.update(), [])

    def test_rest_of_the_repository_is_not_dragged_in(self):
        self.update()

        self.assertFalse(os.path.exists(os.path.join(self.install, "README.md")))


class MaliciousArchiveTest(unittest.TestCase):
    """Архив приходит из сети: имена в нём — не наши данные."""

    def archive_with(self, name: str) -> tuple[str, str]:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "update.tar.gz")

        with tarfile.open(path, "w:gz") as tar:
            data = b"vred\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        return folder, path

    def test_path_escaping_upwards_is_refused(self):
        folder, path = self.archive_with("../../угнали.txt")

        with self.assertRaises(SystemExit) as stop:
            pull.unpack(path, folder)

        self.assertIn("наружу", str(stop.exception))
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(os.path.dirname(folder)), "угнали.txt")))

    def test_absolute_path_is_refused(self):
        folder, path = self.archive_with("/tmp/угнали-тестом.txt")

        with self.assertRaises(SystemExit) as stop:
            pull.unpack(path, folder)

        self.assertIn("наружу", str(stop.exception))
        self.assertFalse(os.path.exists("/tmp/угнали-тестом.txt"))

    def test_archive_without_our_folder_is_refused(self):
        folder = tempfile.mkdtemp()
        archive = github_style_archive(folder, {"README.md": "пусто\n"})
        work = tempfile.mkdtemp()
        shutil.copy(archive, os.path.join(work, "update.tar.gz"))

        with self.assertRaises(SystemExit) as stop:
            pull.unpack(os.path.join(work, "update.tar.gz"), work)

        self.assertIn("templates/autodelivery", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
