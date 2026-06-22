"""Manages the Playerok auth token — loads from disk, saves after login."""
import os
from config import TOKEN_FILE

_token: str = ""


def load_token() -> str:
    global _token
    if not _token and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            _token = f.read().strip()
    return _token


def save_token(token: str):
    global _token
    _token = token
    with open(TOKEN_FILE, "w") as f:
        f.write(token)


def get_token() -> str:
    return _token or load_token()


def clear_token():
    global _token
    _token = ""
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)


def is_authenticated() -> bool:
    return bool(get_token())
