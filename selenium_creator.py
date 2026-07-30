"""
Создание товара на Playerok через Selenium (браузерная автоматизация).

Почему браузер, а не запросы: playerok.com стоит за DDoS-Guard, и для прямых
запросов нужна живая кука `__ddg*`, привязанная к IP и User-Agent. Браузер
получает её сам. Это временный этап — после проверки логика переносится
на GraphQL-запросы (см. docs/PRODUCT_CREATION.md, раздел «Этап 2»).

Мастер создания товара на сайте состоит из 9 шагов:

    1. Открыть страницу создания товара
    2. Выбрать игру / приложение
    3. Выбрать категорию товара
    4. Выбрать способ получения (obtaining type)
    5. Заполнить опции (атрибуты) категории
    6. Указать название и описание
    7. Заполнить данные товара (поля, которые получит покупатель)
    8. Указать цену и загрузить изображения
    9. Опубликовать (выбрать статус приоритета)

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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

logger = logging.getLogger(__name__)

# Кнопки «дальше» на разных шагах мастера называются по-разному.
NEXT_BUTTON_TEXTS = ["Далее", "Продолжить", "Дальше", "Готово", "Сохранить"]
PUBLISH_BUTTON_TEXTS = ["Опубликовать", "Выставить на продажу", "Разместить"]

# Кандидаты URL страницы создания товара (сайт периодически меняет роутинг).
CREATE_URL_CANDIDATES = [
    "/products/new",
    "/items/new",
    "/create",
    "/sell",
]

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

    game: str
    category: str
    obtaining_type: str
    name: str
    description: str
    price: int
    options: dict[str, str] = field(default_factory=dict)
    data_fields: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    # Статус приоритета при публикации; "free" — бесплатное размещение.
    priority: str = "free"


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
        deadline = time.time() + timeout
        while time.time() < deadline:
            for xpath in (self._xpath_by_text(text, exact=True), self._xpath_by_text(text)):
                for el in self.driver.find_elements(By.XPATH, xpath):
                    if el.is_displayed():
                        return el
                if exact:
                    break
            time.sleep(0.4)
        return None

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

    def _click_next(self, required: bool = True) -> bool:
        for text in NEXT_BUTTON_TEXTS:
            el = self._find_by_text(text, timeout=2)
            if el and el.is_enabled():
                return self._click(el)
        if required:
            raise CreationError("Не нашёл кнопку перехода к следующему шагу")
        return False

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

    def _fill(self, label: str, value: str, required: bool = True) -> bool:
        el = self._find_input(label)
        if not el:
            if required:
                raise CreationError(f"Не нашёл поле «{label}»")
            logger.warning("Поле «%s» не найдено, пропускаю", label)
            return False
        try:
            el.clear()
        except WebDriverException:
            el.send_keys(Keys.CONTROL, "a")
        el.send_keys(str(value))
        self._sleep(0.4)
        return True

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

    # ── Шаг 1: страница создания ──────────────────────────────────────────

    def _discover_routes(self) -> list[str]:
        """
        Маршруты фронта из build-манифеста Next.js. Playerok собран на Next,
        и клиент держит список всех страниц в `window.__BUILD_MANIFEST` —
        оттуда адрес мастера берётся точно, без угадывания.
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

    def _create_route_candidates(self) -> list[str]:
        """Маршруты, похожие на мастер создания товара, — самые точные первыми."""
        keywords = ("new", "create", "sell", "add")
        # Страницы, которые попадают под keywords, но мастером не являются.
        noise = (
            "giveaway", "terms", "rules", "agreement", "policy", "privacy",
            "faq", "support", "about", "news", "blog", "wallet", "deposit",
            "withdraw", "login", "signin", "signup", "register", "settings",
        )

        def score(route: str) -> int:
            r = route.lower()
            tail = r.rstrip("/").rsplit("/", 1)[-1]
            if r.rstrip("/") in ("/sell", "/products/new", "/items/new", "/create"):
                return 0
            if tail in ("new", "create", "add"):
                return 1
            if "sell" in r and "seller" not in r:
                return 2
            return 3

        routes = self._discover_routes()
        found = [
            r
            for r in routes
            # страницы с [param] пропускаем — туда нужен конкретный id
            if "[" not in r
            and any(k in r.lower() for k in keywords)
            and not any(n in r.lower() for n in noise)
        ]
        found.sort(key=score)
        return found + [c for c in CREATE_URL_CANDIDATES if c not in found]

    def _wait_for_render(self, timeout: int = 12) -> int:
        """
        Ждёт, пока Next.js дорисует страницу на клиенте: сразу после `get()`
        в body пусто, и проверять содержимое бессмысленно.
        Возвращает длину текста страницы.
        """
        deadline = time.time() + timeout
        length = 0
        while time.time() < deadline:
            try:
                length = self.driver.execute_script(
                    "return (document.body && document.body.innerText || '').length;"
                )
            except WebDriverException:
                length = 0
            if length and length > 200:
                return length
            time.sleep(0.5)
        return length

    def _looks_like_wizard(self) -> bool:
        markers = (
            "Выберите игру",
            "Выбор игры",
            "Выберите категорию",
            "Выберите приложение",
            "Что продаём",
            "Что вы продаёте",
            "Создание товара",
            "Новый товар",
            "Новое объявление",
        )
        return any(self._find_by_text(m, timeout=1) for m in markers)

    def step1_open_create_page(self):
        self.driver.get(config.PLAYEROK_BASE_URL)
        self._sleep(2.5)

        candidates = self._create_route_candidates()
        logger.info("Кандидаты адреса мастера: %s", candidates)

        for path in candidates:
            url = config.PLAYEROK_BASE_URL + path
            try:
                self.driver.get(url)
            except TimeoutException:
                continue
            length = self._wait_for_render()
            logger.info("%s — текста на странице: %s символов", url, length)
            # Снимок каждой проверенной страницы: если мастер не опознан,
            # по дампам сразу видно, как страница выглядит на самом деле.
            self.snapshot(1, f"try{path.replace('/', '-')}")
            if self._looks_like_wizard():
                return f"Открыт {url}"

        # Фолбэк: кнопка «Продать» на главной.
        self.driver.get(config.PLAYEROK_BASE_URL)
        self._sleep(2)
        for text in ("Продать", "Создать товар", "Разместить объявление"):
            if self._click_text(text, timeout=3, required=False):
                self._sleep(2.5)
                if self._looks_like_wizard():
                    return f"Перешёл по кнопке «{text}» → {self.driver.current_url}"

        raise CreationError(
            "Не нашёл страницу создания товара. Проверенные адреса: "
            + ", ".join(candidates)
            + f". Полный список маршрутов сайта — в {self._run_dir}/routes.txt"
        )

    # ── Шаг 2: игра ───────────────────────────────────────────────────────

    def step2_select_game(self, game: str):
        self._pick_from_list(game, search_label="Поиск")
        self._click_next(required=False)
        return f"Игра: {game}"

    # ── Шаг 3: категория ──────────────────────────────────────────────────

    def step3_select_category(self, category: str):
        self._pick_from_list(category, search_label="Поиск")
        self._click_next(required=False)
        return f"Категория: {category}"

    # ── Шаг 4: способ получения ───────────────────────────────────────────

    def step4_select_obtaining_type(self, obtaining_type: str):
        self._pick_from_list(obtaining_type, search_label="Поиск")
        self._click_next(required=False)
        return f"Способ получения: {obtaining_type}"

    # ── Шаг 5: опции (атрибуты) ───────────────────────────────────────────

    def step5_fill_options(self, options: dict[str, str]):
        if not options:
            self._click_next(required=False)
            return "Опций нет — пропускаю"

        filled = []
        for label, value in options.items():
            # Опция может быть кнопкой-чипом либо полем ввода.
            el = self._find_by_text(value, timeout=3)
            if el:
                self._click(el)
                filled.append(f"{label}={value}")
                continue
            if self._fill(label, value, required=False):
                filled.append(f"{label}={value}")
            else:
                logger.warning("Опция «%s» не найдена", label)

        self._click_next(required=False)
        return "Опции: " + (", ".join(filled) if filled else "не заданы")

    # ── Шаг 6: название и описание ────────────────────────────────────────

    def step6_fill_main_info(self, name: str, description: str):
        if not self._fill("Название", name, required=False):
            self._fill("Заголовок", name)
        if not self._fill("Описание", description, required=False):
            logger.warning("Поле описания не найдено")
        self._click_next(required=False)
        return f"Название: {name}"

    # ── Шаг 7: данные товара ──────────────────────────────────────────────

    def step7_fill_data_fields(self, data_fields: dict[str, str]):
        if not data_fields:
            self._click_next(required=False)
            return "Данные товара не заданы"

        filled = []
        for label, value in data_fields.items():
            if self._fill(label, value, required=False):
                filled.append(label)
            else:
                logger.warning("Поле данных «%s» не найдено", label)
        self._click_next(required=False)
        return "Заполнены поля: " + (", ".join(filled) if filled else "нет")

    # ── Шаг 8: цена и изображения ─────────────────────────────────────────

    def step8_price_and_images(self, price: int, images: list[str]):
        if not self._fill("Цена", str(price), required=False):
            self._fill("Стоимость", str(price))

        uploaded = 0
        if images:
            file_inputs = [
                el
                for el in self.driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
            ]
            if not file_inputs:
                logger.warning("Поле загрузки изображений не найдено")
            else:
                target = file_inputs[0]
                # input[type=file] часто скрыт стилями — делаем его видимым.
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
                    self._sleep(2)

        self._click_next(required=False)
        return f"Цена: {price} ₽, изображений загружено: {uploaded}"

    # ── Шаг 9: публикация ─────────────────────────────────────────────────

    def step9_publish(self, priority: str = "free"):
        if priority == "free":
            # Бесплатный вариант размещения подписан по-разному.
            for text in ("Бесплатно", "Обычное", "Стандартное", "0 ₽"):
                if self._click_text(text, timeout=3, required=False):
                    break

        published = False
        for text in PUBLISH_BUTTON_TEXTS:
            if self._click_text(text, timeout=4, required=False):
                published = True
                break
        if not published:
            self._click_next()

        self._sleep(4)
        url = self.driver.current_url
        return f"Готово. Текущий адрес: {url}"

    # ── Полный проход ─────────────────────────────────────────────────────

    def create_product(self, draft: ProductDraft, on_step=None) -> list[StepResult]:
        """
        Проходит все 9 шагов. `on_step(StepResult)` вызывается после каждого —
        бот через него шлёт прогресс в Telegram.
        """
        steps = [
            ("Страница создания товара", lambda: self.step1_open_create_page()),
            ("Выбор игры", lambda: self.step2_select_game(draft.game)),
            ("Выбор категории", lambda: self.step3_select_category(draft.category)),
            (
                "Способ получения",
                lambda: self.step4_select_obtaining_type(draft.obtaining_type),
            ),
            ("Опции товара", lambda: self.step5_fill_options(draft.options)),
            (
                "Название и описание",
                lambda: self.step6_fill_main_info(draft.name, draft.description),
            ),
            ("Данные товара", lambda: self.step7_fill_data_fields(draft.data_fields)),
            (
                "Цена и изображения",
                lambda: self.step8_price_and_images(draft.price, draft.images),
            ),
            ("Публикация", lambda: self.step9_publish(draft.priority)),
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


def create_product(draft: ProductDraft, on_step=None) -> list[StepResult]:
    """Синхронная точка входа: запускает браузер, создаёт товар, закрывает браузер."""
    with PlayerokBrowser() as browser:
        browser.authorize()
        return browser.create_product(draft, on_step=on_step)


if __name__ == "__main__":
    # Ручная проверка: python selenium_creator.py
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    demo = ProductDraft(
        game="Telegram",
        category="Подарки (NFT)",
        obtaining_type="Подарок",
        name='Подарок "❤️ Сердце" (13 ⭐)',
        description="Выдача без захода на ваш аккаунт.",
        price=90,
        data_fields={"Комментарий": "Напишу вам в ТГ после оформления заказа"},
    )
    for step in create_product(demo, on_step=lambda s: print(f"[{s.number}/9] {s.title}: {s.detail}")):
        print(step)
