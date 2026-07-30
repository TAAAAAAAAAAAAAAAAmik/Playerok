"""Async wrapper around PlayerokAPI.

Playerok's GraphQL endpoint only accepts persisted queries — the frontend sends
an operation name plus a sha256 hash, never the query text — so the operations
cannot be hand-written. PlayerokAPI ships those hashes and the TLS
fingerprinting needed to get past DDoS-Guard. It is synchronous, so every call
is handed to a worker thread to keep the bot's event loop responsive.
"""
import asyncio
import logging
import shutil
from pathlib import Path

import certifi
import playerokapi.account as playerokapi_account
from playerokapi.account import Account

import auth
import config

logger = logging.getLogger(__name__)

_account: Account | None = None


def _ensure_cacert():
    """Provide the CA bundle PlayerokAPI expects inside its own package.

    Account.__init__ unconditionally copies <package>/cacert.pem to a temp path
    and hands it to curl_cffi as `verify`, but its pip build ships no such data
    file — so construction fails with FileNotFoundError before any request is
    made. Seed it from certifi; disabling verification is not an option.
    """
    target = Path(playerokapi_account.__file__).with_name("cacert.pem")
    if target.exists():
        return
    try:
        shutil.copyfile(certifi.where(), target)
    except OSError as e:
        raise RuntimeError(
            f"Не удалось создать {target}: {e}. "
            f"Скопируйте туда бандл сертификатов вручную: "
            f"cp {certifi.where()} {target}"
        ) from e
    logger.info("Создан %s из certifi — pip-сборка PlayerokAPI его не содержит", target)


def _build(token: str, ddg5: str, user_agent: str) -> Account:
    """Construct and initialise an Account. Blocking — call in a thread."""
    _ensure_cacert()
    return Account(token=token, ddg5=ddg5, user_agent=user_agent).get()


async def login(token: str, ddg5: str = "", user_agent: str = "") -> dict:
    """Validate credentials by fetching the account, then cache the session.

    Raises whatever PlayerokAPI raises when the token is rejected.
    """
    global _account
    account = await asyncio.to_thread(_build, token, ddg5, user_agent)
    _account = account
    return {
        "id": account.id,
        "username": account.username or "",
        "email": account.email or "",
    }


async def get_account() -> Account:
    """Return the cached session, restoring it from disk on first use."""
    global _account
    if _account is None:
        creds = auth.get()
        if not creds.get("token"):
            raise RuntimeError("Нет токена Playerok — выполните /login")
        _account = await asyncio.to_thread(
            _build,
            creds["token"],
            creds.get("ddg5", ""),
            creds.get("user_agent", ""),
        )
    return _account


def reset():
    """Forget the cached session — used on /logout and after auth errors."""
    global _account
    _account = None


async def fetch_deals(count: int | None = None) -> list:
    """Return the most recent deals, newest first. Raises on failure.

    Errors deliberately propagate: a silent empty list would make an expired
    token look exactly like "no new purchases".
    """
    account = await get_account()
    limit = count or config.DEAL_FETCH_COUNT

    def _call():
        return account.get_deals(count=limit)

    page = await asyncio.to_thread(_call)
    return list(page.deals or [])


async def get_account_id() -> str:
    return (await get_account()).id
