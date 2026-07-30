"""Stores the Playerok session credentials.

Playerok has no public login mutation — the frontend authenticates with a JWT
kept in the `token` cookie — so the bot is handed that value directly instead
of signing in with an email and a one-time code. `__ddg5_` is DDoS-Guard's
cookie; it is only needed once plain requests start getting challenged.
"""
import json
import os

from config import TOKEN_FILE

_creds: dict = {}


def load() -> dict:
    global _creds
    if not _creds and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            raw = f.read().strip()
        if raw:
            try:
                _creds = json.loads(raw)
            except json.JSONDecodeError:
                # Earlier versions stored a bare token on a single line.
                _creds = {"token": raw}
    return _creds


def save(token: str, ddg5: str = "", user_agent: str = ""):
    global _creds
    _creds = {"token": token, "ddg5": ddg5, "user_agent": user_agent}
    with open(TOKEN_FILE, "w") as f:
        json.dump(_creds, f)
    # The file holds a live session credential — keep it owner-only.
    os.chmod(TOKEN_FILE, 0o600)


def get() -> dict:
    return _creds or load()


def get_token() -> str:
    return get().get("token", "")


def clear():
    global _creds
    _creds = {}
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)


def is_authenticated() -> bool:
    return bool(get_token())
