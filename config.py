import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PLAYEROK_API_URL = "https://playerok.com/graphql"
PLAYEROK_BASE_URL = "https://playerok.com"

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
DB_PATH = "playerok_state.db"
TOKEN_FILE = ".playerok_token"

# ── Selenium (создание товара через браузер) ──────────────────────────────────

# Куки авторизованной сессии Playerok: "token=...; __ddg5_=...".
# Нужны именно с того IP и User-Agent, под которыми вы логинились.
PLAYEROK_COOKIES = os.getenv("PLAYEROK_COOKIES", "")

SELENIUM_HEADLESS = os.getenv("SELENIUM_HEADLESS", "0") == "1"
SELENIUM_TIMEOUT = int(os.getenv("SELENIUM_TIMEOUT", "20"))
SELENIUM_PROFILE_DIR = os.getenv("SELENIUM_PROFILE_DIR", os.path.abspath(".chrome-profile"))
SELENIUM_USER_AGENT = os.getenv(
    "SELENIUM_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
)
# Мобильная вёрстка: у Playerok она отличается от десктопной, и селекторы
# нужно подгонять под ту, которую видите вы.
SELENIUM_MOBILE = os.getenv("SELENIUM_MOBILE", "0") == "1"
SELENIUM_MOBILE_USER_AGENT = os.getenv(
    "SELENIUM_MOBILE_USER_AGENT",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36",
)
CHROME_BINARY = os.getenv("CHROME_BINARY", "")
CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH", "")
# Доп. аргументы chromedriver через пробел, например --disable-build-check,
# если версии Chrome и драйвера не совпадают.
CHROMEDRIVER_ARGS = os.getenv("CHROMEDRIVER_ARGS", "").split()

# Чем создавать товар: "api" — прямыми запросами (быстро),
# "browser" — прогоном мастера в Selenium (запасной путь).
CREATE_MODE = os.getenv("CREATE_MODE", "api")

# 1 — все запросы к API выполнять внутри браузера. Медленнее, но куки,
# User-Agent и TLS-отпечаток гарантированно совпадают с браузерной сессией.
PLAYEROK_BROWSER_TRANSPORT = os.getenv("PLAYEROK_BROWSER_TRANSPORT", "0") == "1"

# Скриншоты и HTML-дампы шагов мастера.
DEBUG_DIR = os.getenv("DEBUG_DIR", os.path.abspath("debug"))
# Загруженные из Telegram изображения товара.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.abspath("uploads"))
