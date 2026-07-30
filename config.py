import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PLAYEROK_BASE_URL = "https://playerok.com"

# Формат ссылки на лот подтвердить не удалось: библиотека их не строит, а сайт
# закрыт для проверки из окружения разработки. Если ссылки в уведомлениях ведут
# на 404 — поправьте шаблон здесь или через переменную ITEM_URL_TEMPLATE.
ITEM_URL_TEMPLATE = os.getenv("ITEM_URL_TEMPLATE", PLAYEROK_BASE_URL + "/products/{slug}")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

# Playerok отдаёт не больше 24 сделок за один запрос.
DEAL_FETCH_COUNT = min(int(os.getenv("DEAL_FETCH_COUNT", "24")), 24)

# На первом запуске база пустая, поэтому все существующие сделки выглядели бы
# новыми. По умолчанию первый проход только заполняет базу, без уведомлений.
NOTIFY_ON_FIRST_RUN = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() in ("1", "true", "yes")

DB_PATH = "playerok_state.db"
TOKEN_FILE = ".playerok_token"
