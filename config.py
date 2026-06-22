import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PLAYEROK_TOKEN = os.getenv("PLAYEROK_TOKEN", "")
PLAYEROK_API_URL = "https://playerok.com/graphql"
PLAYEROK_BASE_URL = "https://playerok.com"

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
DB_PATH = "playerok_state.db"
