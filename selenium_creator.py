"""
Создание товара на Playerok через Selenium (браузерная автоматизация).

Почему браузер, а не запросы: playerok.com стоит за DDoS-Guard, и для прямых
запросов нужна живая кука `__ddg*`, привязанная к IP и User-Agent. Браузер
получает её сам. Это временный этап — после проверки логика переносится
на GraphQL-запросы (см. docs/PRODUCT_CREATION.md, раздел «Этап 2»).

Мастер — это модальное окно, а не отдельная страница: он открывается кнопкой
«Выставить товар» в профиле или «Продать» в нижней навигации, и адрес при этом
не меняется. Шагов ровно 9, они подписаны в заголовке окна:

    1. Выберите раздел товаров   — игра/приложение, есть поиск
    2. Выберите категорию        — список категорий игры
    3. Способ передачи           — как покупатель получит товар
    4. Характеристики            — чипы-атрибуты категории
    5. Фото 1/10                 — изображения товара
    6. О товаре                  — название и описание
    7. Цена                      — цена товара (рядом считается доход)
    8. Данные товара             — комментарий и т.п., кнопка «Сохранить»
    9. Выберите сервис           — Премиум / Обычный (бесплатно) / Выставить позже

Внимание: на девятом шаге по умолчанию отмечен платный «Премиум», поэтому
кнопка публикации нажимается только после явного выбора варианта и проверки,
что в её тексте нет цены.

Каждый шаг сохраняет скриншот и HTML-дамп в DEBUG_DIR — по ним подгоняются
селекторы, если вёрстка Playerok изменится.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

logger = logging.getLogger(__name__)

# Кнопки «дальше» на разных шагах мастера называются по-разному.
NEXT_BUTTON_TEXTS = ["Далее", "Продолжить", "Дальше", "Готово"]

# Чем открывается мастер: кнопка в профиле и пункт нижней навигации.
OPEN_WIZARD_TEXTS = ["Выставить товар", "Продать", "Создать товар"]

# Заголовки шагов — по ним понимаем, что мастер доехал до нужного экрана.
STEP_TITLES = {
    1: "Выберите раздел товаров",
    2: "Выберите категорию",
    3: "Способ передачи",
    4: "Характеристики",
    5: "Фото",
    6: "О товаре",
    7: "Цена",
    8: "Данные товара",
    9: "Выберите сервис",
}

# Варианты размещения на девятом шаге.
PLACEMENT_LABELS = {
    "free": ["Обычный", "Бесплатно"],
    "premium": ["Премиум"],
    "later": ["Выставить позже"],
}

CLICKABLE_TAGS = (
    "self::button or self::a or self::div or self::span or self::li "
    "or self::p or self::label"
)


class CreationError(RuntimeError):
    """Не удалось пройти шаг мастера."""


class NotAuthenticated(CreationError):
    """Браузер не авторизован на Playerok."""


@dataclass
class ProductDraft:
    """Данные товара, которые вводит пользователь бота."""

    game: str                 # «Telegram» — шаг 1
    category: str             # «Звезды» — шаг 2
    obtaining_type: str       # «По @username» — шаг 3
    name: str                 # шаг 6
    description: str          # шаг 6
    price: int                # шаг 7
    # Чипы на шаге «Характеристики», например ["100 звёзд"].
    attributes: list[str] = field(default_factory=list)
    # Поля шага «Данные товара», например {"Комментарий": "..."}.
    data_fields: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    # Размещение на девятом шаге: free — бесплатно, premium — платно,
    # later — оставить в черновике («Выставить позже»).
    placement: str = "free"
    # Идентификаторы, если их уже выбрали кнопками: тогда искать по
    # названиям не нужно. Браузерный мастер их не использует.
    game_id: str = ""
    category_id: str = ""
    obtaining_type_id: str = ""
    # Готовые атрибуты {field: value} и поля [{fieldId, value}].
    attribute_values: dict[str, str] = field(default_factory=dict)
    data_field_values: list[dict] = field(default_factory=list)


@dataclass
class StepResult:
    number: int
    title: str
    ok: bool
    detail: str = ""
    screenshot: str | None = None


class PlayerokBrowser:
    """Обёртка над Selenium с шагами мастера создания товара."""

    def __init__(self, headless: bool | None = None):
        self.headless = config.SELENIUM_HEADLESS if headless is None else headless
        self.driver: webdriver.Chrome | None = None
        self.timeout = config.SELENIUM_TIMEOUT
        self._run_dir = ""

    # ── Жизненный цикл ────────────────────────────────────────────────────

    def start(self):
        opts = Options()
        if self.headless:
            # DDoS-Guard заметно строже к headless — по умолчанию выключено.
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        if config.SELENIUM_MOBILE:
            opts.add_argument("--window-size=412,915")
            opts.add_experimental_option(
                "mobileEmulation",
                {
                    "deviceMetrics": {"width": 412, "height": 915, "pixelRatio": 2.6},
                    "userAgent": config.SELENIUM_MOBILE_USER_AGENT,
                },
            )
        else:
            opts.add_argument("--window-size=1440,1000")
            opts.add_argument(f"--user-agent={config.SELENIUM_USER_AGENT}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        # Журнал сети нужен, чтобы подсмотреть запросы фронта к /graphql
        # и снять актуальные хэши persisted-операций.
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # Постоянный профиль: авторизация и куки DDoS-Guard живут между запусками.
        if config.SELENIUM_PROFILE_DIR:
            os.makedirs(config.SELENIUM_PROFILE_DIR, exist_ok=True)
            opts.add_argument(f"--user-data-dir={config.SELENIUM_PROFILE_DIR}")
        if config.CHROME_BINARY:
            opts.binary_location = config.CHROME_BINARY

        service = Service(
            executable_path=config.CHROMEDRIVER_PATH or None,
            service_args=config.CHROMEDRIVER_ARGS or None,
        )
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.set_page_load_timeout(60)
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        if config.SELENIUM_MOBILE:
            # mobileEmulation задаёт User-Agent, но в headless не всегда меняет
            # ширину viewport — дожимаем метрики напрямую.
            self.driver.execute_cdp_cmd(
                "Emulation.setDeviceMetricsOverride",
                {"width": 412, "height": 915, "deviceScaleFactor": 2.6, "mobile": True},
            )

        self._run_dir = os.path.join(
            config.DEBUG_DIR, datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(self._run_dir, exist_ok=True)
        logger.info("Selenium запущен, отладочные файлы: %s", self._run_dir)

    def stop(self):
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            self.driver = None

    def __enter__(self) -> "PlayerokBrowser":
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ── Авторизация ───────────────────────────────────────────────────────

    def authorize(self):
        """
        Открывает Playerok и подставляет куки сессии.

        Куки берутся из PLAYEROK_COOKIES (строка вида `token=...; __ddg5_=...`).
        Если их нет, рассчитываем на сохранённый профиль браузера: пользователь
        логинится руками один раз, дальше сессия переиспользуется.
        """
        self.driver.get(config.PLAYEROK_BASE_URL)
        self._sleep(2)

        if config.PLAYEROK_COOKIES:
            for chunk in config.PLAYEROK_COOKIES.split(";"):
                if "=" not in chunk:
                    continue
                key, value = chunk.split("=", 1)
                try:
                    self.driver.add_cookie(
                        {
                            "name": key.strip(),
                            "value": value.strip(),
                            "domain": ".playerok.com",
                            "path": "/",
                        }
                    )
                except WebDriverException as e:
                    logger.warning("Не удалось поставить куку %s: %s", key.strip(), e)
            self.driver.get(config.PLAYEROK_BASE_URL)
            self._sleep(2)

        if not self.is_authenticated():
            raise NotAuthenticated(
                "Браузер не авторизован на Playerok. Укажите PLAYEROK_COOKIES "
                "в .env или войдите вручную в профиле браузера "
                f"({config.SELENIUM_PROFILE_DIR})."
            )

    def is_authenticated(self) -> bool:
        """Признак входа: на странице нет кнопки «Войти», есть ссылка на профиль."""
        if self._find_by_text("Войти", timeout=3):
            return False
        return bool(
            self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/profile']")
            or self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings']")
        )

    # ── Служебное ─────────────────────────────────────────────────────────

    def _sleep(self, seconds: float):
        time.sleep(seconds)

    def _wait(self, timeout: int | None = None) -> WebDriverWait:
        return WebDriverWait(self.driver, timeout or self.timeout)

    def _xpath_by_text(self, text: str, exact: bool = False) -> str:
        safe = text.replace('"', '\\"')
        if exact:
            return f'//*[{CLICKABLE_TAGS}][normalize-space(text())="{safe}"]'
        return f'//*[{CLICKABLE_TAGS}][contains(normalize-space(text()), "{safe}")]'

    def _find_by_text(self, text: str, timeout: int = 5, exact: bool = False):
        """Первый видимый элемент с таким текстом или None."""
        safe = text.replace('"', '\\"')
        # Пункты списков у Playerok состоят из нескольких узлов (текст + эмодзи),
        # поэтому кроме text() смотрим и на весь текст элемента целиком.
        variants = [
            self._xpath_by_text(text, exact=True),
            f'//*[{CLICKABLE_TAGS}][normalize-space(.)="{safe}"]',
            self._xpath_by_text(text),
        ]
        if exact:
            variants = variants[:2]

        deadline = time.time() + timeout
        while time.time() < deadline:
            for xpath in variants:
                for el in self.driver.find_elements(By.XPATH, xpath):
                    if el.is_displayed():
                        return el
            time.sleep(0.4)
        return None

    def _page_text(self) -> str:
        try:
            return self.driver.execute_script(
                "return document.body ? document.body.innerText : '';"
            ) or ""
        except WebDriverException:
            return ""

    def _wait_for_render(self, timeout: int = 12) -> int:
        """
        Ждёт, пока Next.js дорисует страницу на клиенте: сразу после `get()`
        в body пусто, и проверять содержимое бессмысленно.
        Возвращает длину текста страницы.
        """
        deadline = time.time() + timeout
        length = 0
        while time.time() < deadline:
            length = len(self._page_text())
            if length > 200:
                return length
            time.sleep(0.5)
        return length

    def _wait_title(self, title: str, timeout: int | None = None, required: bool = True) -> bool:
        """Ждёт, пока мастер покажет шаг с таким заголовком."""
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            if title in self._page_text():
                return True
            time.sleep(0.5)
        if required:
            raise CreationError(f"Мастер не дошёл до шага «{title}»")
        logger.info("Шаг «%s» не появился — вероятно, его нет в этой категории", title)
        return False

    def _click_text(self, text: str, timeout: int | None = None, required: bool = True):
        el = self._find_by_text(text, timeout=timeout or self.timeout)
        if not el:
            if required:
                raise CreationError(f"Не нашёл кликабельный элемент «{text}»")
            return False
        return self._click(el)

    def _click(self, el) -> bool:
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", el
            )
            self._sleep(0.3)
            el.click()
        except WebDriverException:
            # Оверлеи Playerok часто перехватывают клик — жмём через JS.
            self.driver.execute_script("arguments[0].click();", el)
        self._sleep(1)
        return True

    @staticmethod
    def _is_disabled(el) -> bool:
        return bool(
            el.get_attribute("disabled")
            or el.get_attribute("aria-disabled") == "true"
            or not el.is_enabled()
        )

    def _find_next_button(self):
        """Кнопка перехода и признак её активности: (элемент, активна)."""
        found = None
        for text in NEXT_BUTTON_TEXTS:
            el = self._find_by_text(text, timeout=1)
            if not el:
                continue
            if not self._is_disabled(el):
                return el, True
            found = el
        return found, False

    def _click_next(self, required: bool = True, timeout: int | None = None) -> bool:
        # Кнопка становится активной не сразу: сайт ждёт загрузки файлов
        # и валидации полей — поэтому крутим цикл, а не проверяем один раз.
        deadline = time.time() + (timeout or self.timeout)
        disabled = None
        while time.time() < deadline:
            el, enabled = self._find_next_button()
            if el and enabled:
                return self._click(el)
            disabled = disabled or el
            time.sleep(0.5)

        if not required:
            return False
        if disabled:
            raise CreationError(
                f"Кнопка «{disabled.text.strip() or 'Далее'}» осталась неактивной — "
                "похоже, шаг заполнен не полностью"
            )
        raise CreationError("Не нашёл кнопку перехода к следующему шагу")

    def _find_input(self, label: str):
        """
        Ищет поле ввода по placeholder, name, aria-label или подписи рядом.
        Возвращает элемент input/textarea или None.
        """
        safe = label.replace('"', '\\"')
        candidates = [
            f'//input[contains(@placeholder, "{safe}")]',
            f'//textarea[contains(@placeholder, "{safe}")]',
            f'//input[contains(@aria-label, "{safe}")]',
            f'//textarea[contains(@aria-label, "{safe}")]',
            f'//input[contains(@name, "{safe}")]',
            # Подпись рядом с полем: label → вложенный или соседний input.
            f'//label[contains(normalize-space(.), "{safe}")]//input',
            f'//label[contains(normalize-space(.), "{safe}")]//textarea',
            f'//label[contains(normalize-space(.), "{safe}")]/following::input[1]',
            f'//label[contains(normalize-space(.), "{safe}")]/following::textarea[1]',
            f'//*[normalize-space(text())="{safe}"]/following::input[1]',
            f'//*[normalize-space(text())="{safe}"]/following::textarea[1]',
        ]
        for xpath in candidates:
            for el in self.driver.find_elements(By.XPATH, xpath):
                if el.is_displayed() and el.is_enabled():
                    return el
        return None

    # React не замечает value, выставленное напрямую, — нужен нативный сеттер
    # и события input/change, иначе форма считает поле пустым.
    SET_VALUE = """
    const [el, value] = arguments;
    const proto = el.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    """

    def _set_value(self, el, value: str):
        self.driver.execute_script(self.SET_VALUE, el, str(value))

    def _fill(self, label: str, value: str, required: bool = True) -> bool:
        el = self._find_input(label)
        if not el:
            if required:
                raise CreationError(f"Не нашёл поле «{label}»")
            logger.warning("Поле «%s» не найдено, пропускаю", label)
            return False

        value = str(value)
        try:
            el.clear()
        except WebDriverException:
            pass

        # Печатать посимвольно нельзя по двум причинам: ChromeDriver не умеет
        # эмодзи («only supports characters in the BMP»), а описание в пару
        # абзацев набирается десятки секунд. Ставим значение сразу.
        self._set_value(el, value)
        self._sleep(0.3)

        try:
            filled = el.get_attribute("value") or ""
        except WebDriverException:
            return True

        if filled.strip() == value.strip():
            return True

        # Поле не приняло значение — пробуем ввод с клавиатуры, но только
        # если в тексте нет символов вне BMP, иначе драйвер откажет.
        if all(ord(ch) <= 0xFFFF for ch in value):
            el.send_keys(value)
            self._sleep(0.3)
            return True

        raise CreationError(
            f"Поле «{label}» не приняло текст — возможно, из-за эмодзи в нём"
        )

    def _fill_number(self, label: str, value: int, required: bool = True) -> int:
        """
        Заполняет числовое поле и проверяет результат. Поле цены на сайте с
        форматированием («14 914 ₽»), поэтому сверяем именно цифры, а не
        строку, и не дописываем к тому, что там уже было.
        """
        el = self._find_input(label)
        if not el:
            if required:
                raise CreationError(f"Не нашёл поле «{label}»")
            return -1

        def digits(raw: str) -> str:
            return "".join(ch for ch in (raw or "") if ch.isdigit())

        for attempt in range(2):
            # Чистим тремя способами: React иногда игнорирует clear().
            try:
                el.clear()
            except WebDriverException:
                pass
            self._set_value(el, "")
            try:
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.DELETE)
            except WebDriverException:
                pass

            if attempt == 0:
                self._set_value(el, str(value))
            else:
                el.send_keys(str(value))  # цифры печатаются без проблем
            self._sleep(0.5)

            actual = digits(el.get_attribute("value"))
            if actual == str(value):
                return value
            logger.warning(
                "Поле «%s»: ожидал %s, в поле %s — пробую ещё раз",
                label, value, actual or "пусто",
            )

        raise CreationError(
            f"Поле «{label}» приняло {digits(el.get_attribute('value')) or 'пусто'} "
            f"вместо {value}"
        )

    def _pick_from_list(self, value: str, search_label: str = "Поиск") -> bool:
        """
        Выбор элемента из списка (игра, категория, способ получения).
        Сначала пробует поиск, затем прямой клик по названию.
        """
        search = self._find_input(search_label) or self._find_input("Найти")
        if search:
            try:
                search.clear()
            except WebDriverException:
                pass
            search.send_keys(value)
            self._sleep(1.5)

        el = self._find_by_text(value, timeout=self.timeout)
        if not el:
            raise CreationError(f"Не нашёл «{value}» в списке")
        return self._click(el)

    def snapshot(self, step: int, title: str) -> str | None:
        """Скриншот + HTML-дамп шага. Возвращает путь к скриншоту."""
        if not self.driver or not self._run_dir:
            return None
        slug = f"{step:02d}_{title.replace(' ', '_').replace('/', '-')}"
        png = os.path.join(self._run_dir, f"{slug}.png")
        html = os.path.join(self._run_dir, f"{slug}.html")
        try:
            self.driver.save_screenshot(png)
            with open(html, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            return png
        except WebDriverException as e:
            logger.warning("Не смог сохранить снимок шага %s: %s", step, e)
            return None

    # ── Шаг 1: раздел товаров (игра/приложение) ───────────────────────────

    def _discover_routes(self) -> list[str]:
        """
        Маршруты фронта из build-манифеста Next.js — пишем их в routes.txt.
        Мастер это модальное окно, так что для навигации они не нужны, но
        помогают, если Playerok когда-нибудь вынесет его на отдельный адрес.
        """
        try:
            routes = self.driver.execute_script(
                "const m = window.__BUILD_MANIFEST || self.__BUILD_MANIFEST;"
                "return m && m.sortedPages ? m.sortedPages : [];"
            )
        except WebDriverException as e:
            logger.warning("Не смог прочитать build-манифест: %s", e)
            return []

        routes = [r for r in (routes or []) if isinstance(r, str)]
        if routes and self._run_dir:
            with open(os.path.join(self._run_dir, "routes.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(routes))
        return routes

    def _open_wizard(self) -> str:
        """
        Открывает мастер. Это модалка: кнопка «Выставить товар» в профиле или
        «Продать» в нижней навигации. Адрес страницы при этом не меняется.
        """
        for page in ("/profile", "/"):
            self.driver.get(config.PLAYEROK_BASE_URL + page)
            self._wait_for_render()
            self._discover_routes()

            for text in OPEN_WIZARD_TEXTS:
                if not self._click_text(text, timeout=3, required=False):
                    continue
                if self._wait_title(STEP_TITLES[1], timeout=10, required=False):
                    return f"Мастер открыт кнопкой «{text}» на {page}"
                logger.info("Кнопка «%s» на %s мастер не открыла", text, page)

        raise CreationError(
            "Не нашёл, чем открыть мастер. Ожидались кнопки: "
            + ", ".join(OPEN_WIZARD_TEXTS)
        )

    def step1_select_game(self, game: str):
        detail = self._open_wizard()

        search = self._find_input("Поиск игр и приложений") or self._find_input("Поиск")
        if search:
            search.send_keys(game)
            self._sleep(1.5)

        el = self._find_by_text(game, timeout=self.timeout)
        if not el:
            raise CreationError(f"Не нашёл «{game}» в списке игр и приложений")
        self._click(el)
        # Выбор игры сам переводит на следующий шаг, кнопки «Далее» здесь нет.
        return f"{detail}; игра: {game}"

    # ── Шаг 2: категория ──────────────────────────────────────────────────

    def step2_select_category(self, category: str):
        self._wait_title(STEP_TITLES[2])
        el = self._find_by_text(category, timeout=self.timeout)
        if not el:
            raise CreationError(f"Не нашёл категорию «{category}»")
        self._click(el)
        self._click_next()
        return f"Категория: {category}"

    # ── Шаг 3: способ передачи ────────────────────────────────────────────

    def step3_select_obtaining_type(self, obtaining_type: str):
        self._wait_title(STEP_TITLES[3])
        el = self._find_by_text(obtaining_type, timeout=self.timeout)
        if not el:
            raise CreationError(f"Не нашёл способ передачи «{obtaining_type}»")
        self._click(el)
        self._click_next()
        return f"Способ передачи: {obtaining_type}"

    # ── Шаг 4: характеристики ─────────────────────────────────────────────

    def step4_fill_attributes(self, attributes: list[str]):
        # У некоторых категорий характеристик нет — шаг пропускается сайтом.
        if not self._wait_title(STEP_TITLES[4], timeout=8, required=False):
            return "Шага «Характеристики» нет"

        chosen = []
        for value in attributes:
            el = self._find_by_text(value, timeout=5)
            if el:
                self._click(el)
                chosen.append(value)
            else:
                logger.warning("Характеристика «%s» не найдена", value)

        self._click_next()
        return "Характеристики: " + (", ".join(chosen) if chosen else "не заданы")

    # ── Шаг 5: фото ───────────────────────────────────────────────────────

    def step5_upload_images(self, images: list[str]):
        self._wait_title(STEP_TITLES[5], required=False)

        if not images:
            # Без фото «Далее» на этом шаге остаётся серой — проверим и скажем прямо.
            if not self._click_next(required=False, timeout=5):
                raise CreationError(
                    "Фото обязательно: без изображения кнопка «Далее» неактивна"
                )
            return "Изображений загружено: 0"

        uploaded = 0
        file_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        if not file_inputs:
            raise CreationError("Не нашёл поле загрузки изображений")

        target = file_inputs[0]
        # input[type=file] скрыт стилями — иначе send_keys не сработает.
        self.driver.execute_script(
            "arguments[0].style.display='block';"
            "arguments[0].style.visibility='visible';"
            "arguments[0].style.opacity=1;"
            "arguments[0].style.height='1px';arguments[0].style.width='1px';",
            target,
        )
        for path in images:
            if not os.path.exists(path):
                logger.warning("Файл %s не найден", path)
                continue
            target.send_keys(os.path.abspath(path))
            uploaded += 1
            self._sleep(2.5)

        if not uploaded:
            raise CreationError("Ни один из указанных файлов не найден на диске")

        # Загрузка идёт на сервер — «Далее» оживает только после неё.
        self._click_next(timeout=max(self.timeout, 30))
        return f"Изображений загружено: {uploaded}"

    # ── Шаг 6: о товаре ───────────────────────────────────────────────────

    def step6_fill_about(self, name: str, description: str):
        self._wait_title(STEP_TITLES[6])
        if not self._fill("Название товара", name, required=False):
            self._fill("Название", name)
        if not self._fill("Описание товара", description, required=False):
            self._fill("Описание", description)
        self._click_next()
        return f"Название: {name}"

    # ── Шаг 7: цена ───────────────────────────────────────────────────────

    def step7_fill_price(self, price: int, discount: int = 0):
        self._wait_title(STEP_TITLES[7])
        # Рядом есть поле «Доход» — заполнять нужно именно «Цена товара».
        if self._fill_number("Цена товара", price, required=False) < 0:
            self._fill_number("Цена", price)

        applied = self.set_discount(discount) if discount else False
        self._click_next()

        detail = f"Цена: {price} ₽"
        if discount:
            detail += f", скидка {discount}%" if applied else ", скидку задать не вышло"
        return detail

    def set_discount(self, percent: int) -> bool:
        """
        Ставит скидку, если мастер её предлагает. Поле называется по-разному
        («Скидка», «Скидка, %»), а у части категорий его нет вовсе — тогда
        возвращаем False, не роняя шаг.
        """
        for label in ("Скидка", "Скидк", "Discount"):
            if self._fill_number(label, percent, required=False) >= 0:
                logger.info("Скидка %s%% задана полем «%s»", percent, label)
                return True

        # Иногда скидка включается переключателем, а поле появляется после.
        if self._click_text("Скидка", timeout=3, required=False):
            self._sleep(1)
            if self._fill_number("Скидка", percent, required=False) >= 0:
                logger.info("Скидка %s%% задана после включения переключателя", percent)
                return True

        logger.warning("На шаге «Цена» нет поля скидки — оставляю цену как есть")
        return False

    # ── Шаг 8: данные товара ──────────────────────────────────────────────

    def step8_fill_data_fields(self, data_fields: dict[str, str]):
        self._wait_title(STEP_TITLES[8])

        filled = []
        for label, value in data_fields.items():
            if self._fill(label, value, required=False):
                filled.append(label)
            else:
                logger.warning("Поле данных «%s» не найдено", label)

        # Здесь кнопка называется «Сохранить», а не «Далее».
        if not self._click_text("Сохранить", timeout=5, required=False):
            self._click_next()
        return "Заполнены поля: " + (", ".join(filled) if filled else "нет")

    # ── Шаг 9: выбор сервиса и публикация ─────────────────────────────────

    def _publish_button(self, placement: str):
        """
        Кнопка внизу девятого шага. Текст зависит от варианта размещения, и
        похожих кнопок на экране несколько: «Выставить бесплатно», «Выставить
        Премиум за 19 ₽», «Сохранить». Слово «Выставить» есть даже у пункта
        «Выставить позже», поэтому подбираем кнопку строго под выбор.
        """
        buttons = []
        for el in self.driver.find_elements(By.XPATH, "//button | //a[@role='button']"):
            try:
                if not el.is_displayed():
                    continue
                label = " ".join((el.text or "").split())
            except WebDriverException:
                continue
            if label:
                buttons.append((el, label))

        def paid(label: str) -> bool:
            return "₽" in label or "премиум" in label.casefold()

        for el, label in buttons:
            low = label.casefold()
            if placement == "later":
                if "сохранить" in low or "позже" in low:
                    return el, label
            elif placement == "premium":
                if "выставить" in low and paid(label):
                    return el, label
            else:  # бесплатное размещение
                if "выставить" in low and "позже" not in low and not paid(label):
                    return el, label

        # Ничего подходящего: показываем, что было на экране, — это сразу
        # объясняет, почему товар ушёл не туда.
        raise CreationError(
            f"Не нашёл кнопку для размещения «{placement}». На экране: "
            + "; ".join(label for _, label in buttons[:6])
        )

    def _row_of(self, element):
        """
        Строка списка, в которой лежит найденный текст. Радио-кружок стоит
        у правого края строки, а не рядом с подписью, поэтому кликать надо
        по строке целиком.
        """
        try:
            return self.driver.execute_script(
                """
                let el = arguments[0];
                for (let i = 0; i < 4 && el.parentElement; i++) {
                  el = el.parentElement;
                  if (el.clientWidth > window.innerWidth * 0.6) return el;
                }
                return arguments[0];
                """,
                element,
            )
        except WebDriverException:
            return element

    def _placement_ready(self, placement: str) -> bool:
        """Проверяет, что кнопка внизу соответствует выбранному размещению."""
        try:
            _, label = self._publish_button(placement)
            return bool(label)
        except CreationError:
            return False

    def _select_placement(self, placement: str) -> bool:
        """
        Отмечает вариант размещения. Пробуем по-разному: клик по подписи,
        по строке целиком и по её правому краю, где стоит переключатель.
        Успех проверяем по кнопке внизу — её текст меняется вслед за выбором.
        """
        for label in PLACEMENT_LABELS.get(placement, PLACEMENT_LABELS["free"]):
            element = self._find_by_text(label, timeout=4)
            if not element:
                continue

            row = self._row_of(element)
            for target in (element, row):
                self._click(target)
                self._sleep(1)
                if self._placement_ready(placement):
                    logger.info("Размещение «%s» выбрано по «%s»", placement, label)
                    return True

            # Переключатель у правого края строки — жмём по координатам.
            try:
                width = row.size["width"]
                ActionChains(self.driver).move_to_element_with_offset(
                    row, max(width // 2 - 20, 1), 0
                ).click().perform()
                self._sleep(1)
                if self._placement_ready(placement):
                    logger.info("Размещение «%s» выбрано переключателем", placement)
                    return True
            except WebDriverException as e:
                logger.debug("Клик по краю строки не удался: %s", e)

        return False

    def _confirm_dialog(self) -> bool:
        """Подтверждает всплывающий вопрос, если сайт его показал."""
        for text in ("Подтвердить", "Да, выставить", "Выставить", "Да", "Продолжить"):
            element = self._find_by_text(text, timeout=2)
            if element:
                self._click(element)
                logger.info("Подтвердил диалог кнопкой «%s»", text)
                return True
        return False

    def step9_publish(self, placement: str = "free"):
        self._wait_title(STEP_TITLES[9])

        # По умолчанию отмечен платный «Премиум» — переключаем явно.
        if not self._select_placement(placement):
            raise CreationError(
                f"Не удалось выбрать размещение «{placement}»: кнопка внизу "
                "не сменилась. Возможно, вариант недоступен для этого товара."
            )

        button, label = self._publish_button(placement)
        if placement != "premium" and ("₽" in label or "Премиум" in label):
            raise CreationError(
                f"Кнопка публикации осталась платной: «{label}» — прерываю, "
                "чтобы не списать деньги."
            )

        # Жмём и, если ничего не изменилось, пробуем ещё раз другим способом:
        # клик мог не дойти из-за анимации или всплывающего подтверждения.
        for attempt in range(3):
            if attempt == 0:
                self._click(button)
            elif attempt == 1:
                self.driver.execute_script("arguments[0].click();", button)
            else:
                try:
                    ActionChains(self.driver).move_to_element(button).click().perform()
                except WebDriverException:
                    pass

            if self._wait_gone(button, timeout=8):
                self._sleep(3)  # даём сайту дорисовать карточку товара
                return f"Размещение «{placement}», нажато «{label}»"

            if self._confirm_dialog() and self._wait_gone(button, timeout=8):
                self._sleep(3)
                return f"Размещение «{placement}», подтверждено после «{label}»"

            logger.warning("Кнопка «%s» не сработала, попытка %s", label, attempt + 2)

        visible = ", ".join(
            text for text in self._visible_button_texts()[:6]
        )
        raise CreationError(
            f"Нажал «{label}» трижды, но мастер не сдвинулся. Кнопки на экране: "
            f"{visible}"
        )

    def _visible_button_texts(self) -> list[str]:
        texts = []
        for el in self.driver.find_elements(By.XPATH, "//button | //a[@role='button']"):
            try:
                if el.is_displayed() and el.text.strip():
                    texts.append(" ".join(el.text.split()))
            except WebDriverException:
                continue
        return texts

    def _wait_gone(self, element, timeout: int = 15) -> bool:
        """Ждёт, пока элемент исчезнет: пропадёт из DOM или скроется."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if not element.is_displayed():
                    return True
            except WebDriverException:
                return True  # элемента больше нет в DOM — то, что нужно
            time.sleep(0.5)
        return False

    # ── Полный проход ─────────────────────────────────────────────────────

    def create_product(self, draft: ProductDraft, on_step=None) -> list[StepResult]:
        """
        Проходит все 9 шагов. `on_step(StepResult)` вызывается после каждого —
        бот через него шлёт прогресс в Telegram.
        """
        steps = [
            ("Раздел товаров", lambda: self.step1_select_game(draft.game)),
            ("Категория", lambda: self.step2_select_category(draft.category)),
            (
                "Способ передачи",
                lambda: self.step3_select_obtaining_type(draft.obtaining_type),
            ),
            ("Характеристики", lambda: self.step4_fill_attributes(draft.attributes)),
            ("Фото", lambda: self.step5_upload_images(draft.images)),
            ("О товаре", lambda: self.step6_fill_about(draft.name, draft.description)),
            ("Цена", lambda: self.step7_fill_price(draft.price)),
            ("Данные товара", lambda: self.step8_fill_data_fields(draft.data_fields)),
            ("Выберите сервис", lambda: self.step9_publish(draft.placement)),
        ]

        results: list[StepResult] = []
        for number, (title, action) in enumerate(steps, start=1):
            try:
                detail = action()
                result = StepResult(number, title, True, detail, self.snapshot(number, title))
            except Exception as e:  # шаг не прошёл — дальше идти бессмысленно
                logger.exception("Шаг %s (%s) упал", number, title)
                result = StepResult(
                    number, title, False, str(e), self.snapshot(number, title)
                )
                results.append(result)
                if on_step:
                    on_step(result)
                raise CreationError(f"Шаг {number} «{title}»: {e}") from e

            results.append(result)
            if on_step:
                on_step(result)

        return results



def make_placeholder_image(path: str, width: int = 900, height: int = 900) -> str:
    """
    Рисует однотонный PNG без сторонних библиотек — нужен для проверки шага
    «Фото»: без изображения мастер не пускает дальше.
    """
    import struct
    import zlib

    row = b"\x00" + bytes((32, 58, 122)) * width  # фильтр 0 + пиксели RGB
    raw = zlib.compress(row * height, 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", raw)
    png += chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(png)
    return path


def create_product(draft: ProductDraft, on_step=None) -> list[StepResult]:
    """Синхронная точка входа: запускает браузер, создаёт товар, закрывает браузер."""
    with PlayerokBrowser() as browser:
        browser.authorize()
        return browser.create_product(draft, on_step=on_step)


if __name__ == "__main__":
    # Ручная проверка: python selenium_creator.py
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # placement="later" — товар остаётся черновиком: ничего не публикуется
    # и не списывается. Для боевого прогона ставьте "free".
    demo = ProductDraft(
        game="Telegram",
        category="Звезды",
        obtaining_type="По @username",
        attributes=["100 звёзд"],
        name="100 звёзд по @username",
        description="Выдача без входа в аккаунт.",
        price=145,
        data_fields={"Комментарий": "Напишу вам в ТГ после оформления заказа"},
        images=[
            next(
                (p for p in ("demo.jpg", "demo.png") if os.path.exists(p)),
                # своей картинки нет — рисуем заглушку, без фото мастер не пустит
                make_placeholder_image("demo.png"),
            )
        ],
        placement="later",
    )
    for step in create_product(demo, on_step=lambda s: print(f"[{s.number}/9] {s.title}: {s.detail}")):
        print(step)
