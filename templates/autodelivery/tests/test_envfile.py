"""Тесты чтения .env. Забытая строка `set -a` не должна выглядеть как
пропавший ключ."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from envfile import load_env_file                                # noqa: E402


class LoadEnvFileTest(unittest.TestCase):
    NAMES = ("ТЕСТ_КЛЮЧ", "ТЕСТ_ВТОРОЙ", "ТЕСТ_ЗАНЯТЫЙ")

    def setUp(self):
        self.saved = {n: os.environ.get(n) for n in self.NAMES}

        for name in self.NAMES:
            os.environ.pop(name, None)

        self.path = os.path.join(tempfile.mkdtemp(), ".env")

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_values_reach_the_environment(self):
        self.write("ТЕСТ_КЛЮЧ=значение\nТЕСТ_ВТОРОЙ=другое\n")

        self.assertEqual(load_env_file(self.path), 2)
        self.assertEqual(os.environ["ТЕСТ_КЛЮЧ"], "значение")

    def test_environment_wins_over_the_file(self):
        """Разовый запуск с другим ключом не должен молча брать старый."""
        os.environ["ТЕСТ_ЗАНЯТЫЙ"] = "из окружения"
        self.write("ТЕСТ_ЗАНЯТЫЙ=из файла\n")
        load_env_file(self.path)

        self.assertEqual(os.environ["ТЕСТ_ЗАНЯТЫЙ"], "из окружения")

    def test_missing_file_is_not_an_error(self):
        """На сервере с systemd файла может не быть вовсе."""
        self.assertEqual(load_env_file("/нет/такого/.env"), 0)

    def test_comments_and_blank_lines_are_skipped(self):
        self.write("# это комментарий\n\nТЕСТ_КЛЮЧ=значение\nмусор без равно\n")

        self.assertEqual(load_env_file(self.path), 1)

    def test_export_prefix_is_tolerated(self):
        self.write("export ТЕСТ_КЛЮЧ=значение\n")
        load_env_file(self.path)

        self.assertEqual(os.environ["ТЕСТ_КЛЮЧ"], "значение")

    def test_quotes_are_stripped(self):
        self.write('ТЕСТ_КЛЮЧ="в кавычках"\n')
        load_env_file(self.path)

        self.assertEqual(os.environ["ТЕСТ_КЛЮЧ"], "в кавычках")

    def test_value_with_equals_signs_survives(self):
        """Куки — это сплошные знаки равенства."""
        self.write("ТЕСТ_КЛЮЧ=token=abc; __ddg3=def\n")
        load_env_file(self.path)

        self.assertEqual(os.environ["ТЕСТ_КЛЮЧ"], "token=abc; __ddg3=def")


if __name__ == "__main__":
    unittest.main()
