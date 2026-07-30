"""
Живой сеанс мастера создания товара.

Списки игр, категорий и способов передачи Playerok отдаёт только своему
фронту: операция SellGames отвечает «Access denied» даже с правильным хэшем
и куками. Зато сам мастер в браузере проходится целиком — это проверено на
живом аккаунте.

Поэтому диалог в Telegram работает как зеркало мастера: бот держит его
открытым, читает оттуда пункты для кнопок и теми же кликами доводит товар
до публикации. Порядок шагов повторяет мастер, так что подписи полей
(«Комментарий» и т.п.) берутся прямо с текущего экрана.

Сеанс один на процесс, брошенный закрывается по таймауту бездействия.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from selenium.webdriver.common.by import By

from selenium_creator import (
    STEP_TITLES,
    CreationError,
    PlayerokBrowser,
    StepResult,
)

logger = logging.getLogger(__name__)

# Строки интерфейса, которые не являются пунктами списка.
NOISE = {
    "далее", "продолжить", "дальше", "готово", "сохранить", "назад", "отмена",
    "поиск", "поиск игр и приложений", "выберите игру", "платеж 10%",
    "выберите раздел товаров", "выберите категорию", "способ передачи",
    "характеристики", "о товаре", "цена", "данные товара", "выберите сервис",
    "игры", "мобильные игры", "фото", "количество", "доход", "цена товара",
    "название товара", "описание товара", "комментарий",
}

IDLE_TIMEOUT = int(os.getenv("WIZARD_IDLE_TIMEOUT", "900"))


class WizardSession:
    """Один открытый мастер: отдаёт списки для кнопок и принимает выбор."""

    _instance: "WizardSession | None" = None
    _lock = threading.RLock()

    def __init__(self):
        self.browser: PlayerokBrowser | None = None
        self.touched = 0.0
        self.step = 0
        # Выбранное мастер показывает шапкой над списком — эти строки
        # не пункты, и в кнопки они попадать не должны.
        self.chosen: list[str] = []

    # ── Жизненный цикл ────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "WizardSession":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def alive(self) -> bool:
        return bool(
            self.browser
            and self.browser.driver
            and time.time() - self.touched < IDLE_TIMEOUT
        )

    def close(self):
        with self._lock:
            if self.browser:
                try:
                    self.browser.stop()
                except Exception as e:
                    logger.warning("Браузер не закрылся: %s", e)
            self.browser = None
            self.step = 0

    def _open(self):
        """Поднимает браузер и открывает мастер на первом шаге."""
        self.close()
        browser = PlayerokBrowser()
        browser.start()
        browser.authorize()
        browser._open_wizard()
        self.browser = browser
        self.step = 1
        self.chosen = []
        self.touched = time.time()

    def _require(self):
        if not self.alive():
            raise CreationError("Сеанс мастера закрылся — начните заново: /create")
        self.touched = time.time()

    # ── Чтение экрана ─────────────────────────────────────────────────────

    # Пункты берём из листовых элементов, а не из innerText страницы: чипы
    # характеристик стоят в строку, и текстом они склеиваются в одну строку.
    LEAF_TEXTS = """
    const out = [], taken = [];
    for (const el of document.querySelectorAll('button, a, div, span, li, label, p')) {
      if (el.offsetParent === null) continue;              // невидимое
      const text = (el.innerText || '').trim();
      if (!text || text.length > 60) continue;             // контейнеры и абзацы
      // Пункт списка — это элемент без вложенных пунктов. Один дочерний
      // элемент с текстом допустим: так размечают эмодзи рядом с названием.
      const textChildren = Array.from(el.children)
        .filter(c => (c.innerText || '').trim()).length;
      if (textChildren > 1) continue;
      if (taken.some(t => t.contains(el))) continue;       // уже внутри взятого
      out.push(text);
      taken.push(el);
    }
    return out;
    """

    def _visible_lines(self) -> list[str]:
        """Видимые подписи пунктов текущего экрана мастера."""
        try:
            raw_lines = self.browser.driver.execute_script(self.LEAF_TEXTS) or []
        except Exception as e:
            logger.warning("Не смог прочитать экран мастера: %s", e)
            raw_lines = []

        if not raw_lines:  # запасной путь, если разметка неожиданная
            text = self.browser.driver.execute_script(
                "return document.body ? document.body.innerText : ''"
            ) or ""
            raw_lines = text.splitlines()

        lines: list[str] = []
        for raw in raw_lines:
            line = " ".join(str(raw).split())
            if not line or line.casefold() in NOISE:
                continue
            # Служебное: счётчики вида «1/10» и длинные абзацы инструкций.
            if len(line) > 60 or line.replace("/", "").isdigit():
                continue
            if line not in lines:
                lines.append(line)
        return lines

    def _items(self) -> list[dict]:
        """Пункты текущего шага без шапки с уже выбранным."""
        return [
            {"name": line}
            for line in self._visible_lines()
            if line not in self.chosen
        ]

    def _field_labels(self) -> list[str]:
        """Подписи полей ввода на текущем экране."""
        labels = []
        for element in self.browser.driver.find_elements(
            By.CSS_SELECTOR, "input, textarea"
        ):
            try:
                if not element.is_displayed() or element.get_attribute("type") == "file":
                    continue
                label = (
                    element.get_attribute("placeholder")
                    or element.get_attribute("name")
                    or ""
                ).strip()
            except Exception:
                continue
            if label and label not in labels:
                labels.append(label)
        return labels

    # ── Шаги 1–4: выбор кнопками ──────────────────────────────────────────

    def games(self, search: str = "") -> list[dict]:
        """Шаг 1: список игр. `search` фильтрует прямо в мастере."""
        with self._lock:
            if not self.alive() or self.step != 1:
                self._open()

            if search:
                field = (
                    self.browser._find_input("Поиск игр и приложений")
                    or self.browser._find_input("Поиск")
                )
                if field:
                    field.clear()
                    field.send_keys(search)
                    time.sleep(2)

            self.touched = time.time()
            return self._items()

    def pick_game(self, name: str) -> list[dict]:
        """Выбирает игру, возвращает категории (шаг 2)."""
        with self._lock:
            self._require()
            self._click(name, "игру")
            self.browser._wait_title(STEP_TITLES[2], timeout=15)
            self.step = 2
            return self._items()

    def pick_category(self, name: str) -> list[dict]:
        """Выбирает категорию, возвращает способы передачи (шаг 3)."""
        with self._lock:
            self._require()
            self._click(name, "категорию")
            self.browser._click_next(required=False, timeout=8)
            self.browser._wait_title(STEP_TITLES[3], timeout=15)
            self.step = 3
            return self._items()

    def pick_obtaining(self, name: str) -> list[dict]:
        """Выбирает способ передачи, возвращает характеристики (шаг 4)."""
        with self._lock:
            self._require()
            self._click(name, "способ передачи")
            self.browser._click_next(required=False, timeout=8)
            time.sleep(2)
            self.step = 4

            # У части категорий характеристик нет — мастер сразу уходит к фото.
            if not self.browser._wait_title(STEP_TITLES[4], timeout=8, required=False):
                return []
            return self._items()

    def pick_attribute(self, name: str):
        """Отмечает характеристику на шаге 4."""
        with self._lock:
            self._require()
            self._click(name, "характеристику")

    def _click(self, name: str, what: str):
        if not self.browser._click_text(name, timeout=self.browser.timeout, required=False):
            raise CreationError(f"Не нашёл {what} «{name}» на экране мастера")
        # Выбранное уходит в шапку — запоминаем, чтобы не показать его кнопкой.
        if name not in self.chosen:
            self.chosen.append(name)

    # ── Шаги 5–8: данные товара ───────────────────────────────────────────

    def upload_images(self, paths: list[str]) -> str:
        """Шаг 5: фотографии."""
        with self._lock:
            self._require()
            if self.step == 4:
                self.browser._click_next(required=False, timeout=8)
            detail = self.browser.step5_upload_images(paths)
            self.step = 5
            return detail

    def fill_about(self, name: str, description: str) -> str:
        """Шаг 6: название и описание."""
        with self._lock:
            self._require()
            detail = self.browser.step6_fill_about(name, description)
            self.step = 6
            return detail

    def fill_price(self, price: int) -> list[str]:
        """Шаг 7: цена. Возвращает подписи полей следующего шага."""
        with self._lock:
            self._require()
            self.browser.step7_fill_price(price)
            self.step = 7
            self.browser._wait_title(STEP_TITLES[8], timeout=15, required=False)
            return self._field_labels()

    def fill_data_fields(self, values: dict[str, str]) -> list[dict]:
        """Шаг 8: данные товара. Возвращает варианты размещения (шаг 9)."""
        with self._lock:
            self._require()
            self.browser.step8_fill_data_fields(values)
            self.step = 8
            self.browser._wait_title(STEP_TITLES[9], timeout=20)
            return self._items()

    def publish(self, placement: str) -> str:
        """Шаг 9: размещение и публикация."""
        with self._lock:
            self._require()
            detail = self.browser.step9_publish(placement)
            self.step = 9
            return detail

    def snapshot(self, number: int, title: str) -> str | None:
        return self.browser.snapshot(number, title) if self.alive() else None


def step_result(number: int, title: str, detail: str, session: WizardSession) -> StepResult:
    """Готовый StepResult со скриншотом — для отправки прогресса в чат."""
    return StepResult(number, title, True, detail, session.snapshot(number, title))
