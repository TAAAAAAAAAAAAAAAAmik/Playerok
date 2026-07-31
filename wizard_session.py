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

import contextlib
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
        # Элементы текущего экрана — по индексу из кнопки кликаем по ним.
        self._elements: list = []
        # Что мастер сказал про цену и скидку — показываем это пользователю.
        self.price_detail = ""

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
        """
        Закрывает мастер. Замок здесь не берём: если предыдущий шаг завис,
        браузер надо гасить именно поэтому. Chrome закрываем в отдельном
        потоке — quit() на зависшем драйвере тоже умеет подвисать.
        """
        browser, self.browser = self.browser, None
        self.step = 0
        self.chosen = []
        self._elements = []
        if browser:
            threading.Thread(target=self._quit, args=(browser,), daemon=True).start()

    @staticmethod
    def _quit(browser):
        try:
            browser.stop()
        except Exception as e:
            logger.warning("Браузер не закрылся: %s", e)

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

    @contextlib.contextmanager
    def _guard(self, what: str):
        """
        Замок сеанса с таймаутом. Если предыдущий шаг превысил время ожидания,
        его поток ещё работает с браузером — без таймаута следующий шаг встал
        бы намертво.
        """
        if not self._lock.acquire(timeout=5):
            raise CreationError(
                "Мастер занят предыдущим шагом. Подождите несколько секунд "
                "или начните заново: /cancel, затем /create"
            )
        logger.info("Мастер: %s", what)
        started = time.time()
        try:
            yield
        except Exception as e:
            # Что именно на экране — это половина диагноза, поэтому кладём
            # заголовок и снимок прямо в ошибку и в журнал.
            screen = self._screen_title()
            logger.warning("Мастер: %s — ошибка: %s (экран: %s)", what, e, screen)
            if self.browser:
                self.browser.snapshot(0, f"ошибка_{what}")
            raise CreationError(f"{e} (мастер на экране «{screen}»)") from e
        finally:
            logger.info("Мастер: %s — готово за %.1f с", what, time.time() - started)
            self._lock.release()

    def _screen_title(self) -> str:
        """Заголовок текущего экрана мастера — по нему видно, где он застрял."""
        if not self.browser or not self.browser.driver:
            return "браузер закрыт"
        try:
            text = self.browser._page_text()
        except Exception:
            return "не прочитать"
        for title in STEP_TITLES.values():
            if title in text:
                return title
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return first[:40] or "пусто"

    def _require(self):
        if not self.alive():
            raise CreationError("Сеанс мастера закрылся — начните заново: /create")
        self.touched = time.time()

    # ── Чтение экрана ─────────────────────────────────────────────────────

    # Возвращаем сами элементы, а не текст: пункт мастера бывает блоком с
    # заголовком и пояснением («По @username» + «Передача звезд через …»),
    # и искать его потом по склеенному тексту ненадёжно — кликаем по элементу.
    LEAF_ELEMENTS = """
    const out = [], taken = [];
    const fits = el => {
      if (el.offsetParent === null) return false;              // невидимое
      const text = (el.innerText || '').trim();
      return text.length > 0 && text.length <= 80;             // не контейнер
    };

    // Пункт мастера — кликабельный блок: у него cursor: pointer. Внутренние
    // куски (заголовок, пояснение, эмодзи) отбрасываем, чтобы клик приходился
    // на весь пункт целиком.
    for (const el of document.querySelectorAll('*')) {
      if (!fits(el)) continue;
      if (getComputedStyle(el).cursor !== 'pointer') continue;
      if (taken.some(t => t.contains(el))) continue;
      out.push(el);
      taken.push(el);
    }
    if (out.length >= 2) return out;

    // Запасной путь: разметка без cursor: pointer — берём внешние элементы
    // с коротким текстом и без вложенных пунктов.
    out.length = 0; taken.length = 0;
    for (const el of document.querySelectorAll('button, a, div, span, li, label, p')) {
      if (!fits(el)) continue;
      const textChildren = Array.from(el.children)
        .filter(c => (c.innerText || '').trim()).length;
      if (textChildren > 1) continue;
      if (taken.some(t => t.contains(el))) continue;
      out.push(el);
      taken.push(el);
    }
    return out;
    """

    def _items(self) -> list[dict]:
        """
        Пункты текущего экрана. Элементы запоминаются, чтобы выбор пользователя
        превратился в клик именно по ним, а не в повторный поиск по тексту.
        """
        try:
            elements = self.browser.driver.execute_script(self.LEAF_ELEMENTS) or []
        except Exception as e:
            logger.warning("Не смог прочитать экран мастера: %s", e)
            elements = []

        items: list[dict] = []
        self._elements = []
        seen: set[str] = set()

        for element in elements:
            try:
                text = " ".join((element.text or "").split())
            except Exception:
                continue
            if not text or text.casefold() in NOISE or text in seen:
                continue
            if len(text) > 80 or text.replace("/", "").isdigit():
                continue
            if text in self.chosen:  # шапка с уже выбранным
                continue

            # На кнопку выносим только первую строку: вторая — пояснение.
            title = text.split("\n")[0].strip() if "\n" in text else text
            for part in (element.text or "").splitlines():
                part = part.strip()
                if part:
                    title = part
                    break

            seen.add(text)
            items.append({"name": title[:60], "full": text})
            self._elements.append(element)

        return items

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
        with self._guard(f"список игр {search or ''}".strip()):
            if not self.alive() or self.step != 1:
                self._open()

            if search:
                field = (
                    self.browser._find_input("Поиск игр и приложений")
                    or self.browser._find_input("Поиск")
                )
                if field:
                    field.clear()
                    # ChromeDriver не печатает символы вне BMP — эмодзи из
                    # поискового запроса выбрасываем, они всё равно не помогут.
                    field.send_keys("".join(c for c in search if ord(c) <= 0xFFFF))
                    time.sleep(2)

            self.touched = time.time()
            return self._items()

    def pick_game(self, index: int) -> list[dict]:
        """Выбирает игру по позиции в показанном списке, возвращает категории."""
        with self._guard("выбор игры"):
            self._require()
            self._click_index(index, "игру")
            self.browser._wait_title(STEP_TITLES[2], timeout=15)
            self.step = 2
            return self._items()

    def pick_category(self, index: int) -> list[dict]:
        """Выбирает категорию, возвращает способы передачи (шаг 3)."""
        with self._guard("выбор категории"):
            self._require()
            self._click_index(index, "категорию")
            self.browser._click_next(required=False, timeout=8)
            self.browser._wait_title(STEP_TITLES[3], timeout=15)
            self.step = 3
            return self._items()

    def pick_obtaining(self, index: int) -> list[dict]:
        """Выбирает способ передачи, возвращает характеристики (шаг 4)."""
        with self._guard("выбор способа передачи"):
            self._require()
            self._click_index(index, "способ передачи")
            self.browser._click_next(required=False, timeout=8)
            time.sleep(2)
            self.step = 4

            # У части категорий характеристик нет — мастер сразу уходит к фото.
            if not self.browser._wait_title(STEP_TITLES[4], timeout=8, required=False):
                return []
            return self._items()

    def pick_attribute(self, index: int):
        """Отмечает характеристику на шаге 4."""
        with self._guard("выбор характеристики"):
            self._require()
            self._click_index(index, "характеристику")

    def _click_index(self, index: int, what: str):
        """Кликает по элементу, который показывали пользователю кнопкой."""
        if index >= len(self._elements):
            raise CreationError(f"Экран мастера изменился — выберите {what} заново")

        element = self._elements[index]
        try:
            text = " ".join((element.text or "").split())
        except Exception:
            text = ""

        self.browser._click(element)
        # Выбранное мастер показывает шапкой — запоминаем, чтобы не выводить
        # его пунктом на следующем шаге.
        if text and text not in self.chosen:
            self.chosen.append(text)

    # ── Шаги 5–8: данные товара ───────────────────────────────────────────

    def upload_images(self, paths: list[str]) -> str:
        """Шаг 5: фотографии."""
        with self._guard("загрузка фото"):
            self._require()
            if self.step == 4:
                self.browser._click_next(required=False, timeout=8)
            detail = self.browser.step5_upload_images(paths)
            self.step = 5
            return detail

    def fill_about(self, name: str, description: str) -> str:
        """Шаг 6: название и описание."""
        with self._guard("название и описание"):
            self._require()
            detail = self.browser.step6_fill_about(name, description)
            self.step = 6
            return detail

    def fill_price(self, price: int, discount: int = 0) -> list[str]:
        """Шаг 7: цена и скидка. Возвращает подписи полей следующего шага."""
        with self._guard("цена"):
            self._require()
            self.price_detail = self.browser.step7_fill_price(price, discount)
            self.step = 7
            self.browser._wait_title(STEP_TITLES[8], timeout=15, required=False)
            return self._field_labels()

    def fill_data_fields(self, values: dict[str, str]) -> list[dict]:
        """Шаг 8: данные товара. Возвращает варианты размещения (шаг 9)."""
        with self._guard("данные товара"):
            self._require()
            self.browser.step8_fill_data_fields(values)
            self.step = 8
            self.browser._wait_title(STEP_TITLES[9], timeout=20)
            return self._items()

    def publish(self, placement: str) -> str:
        """Шаг 9: размещение и публикация."""
        with self._guard("публикация"):
            self._require()
            detail = self.browser.step9_publish(placement)
            self.step = 9
            return detail

    def snapshot(self, number: int, title: str) -> str | None:
        return self.browser.snapshot(number, title) if self.alive() else None


def step_result(number: int, title: str, detail: str, session: WizardSession) -> StepResult:
    """Готовый StepResult со скриншотом — для отправки прогресса в чат."""
    return StepResult(number, title, True, detail, session.snapshot(number, title))
