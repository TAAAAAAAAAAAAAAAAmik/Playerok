import httpx
import logging
from typing import Optional
from config import PLAYEROK_API_URL, PLAYEROK_BASE_URL
import auth

logger = logging.getLogger(__name__)

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": PLAYEROK_BASE_URL,
    "Referer": PLAYEROK_BASE_URL + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ── Auth mutations ────────────────────────────────────────────────────────────

SEND_CODE_MUTATION = """
mutation sendConfirmationCode($input: SendCodeInput!) {
  sendConfirmationCode(input: $input) {
    success
  }
}
"""

LOGIN_MUTATION = """
mutation signIn($input: SignInInput!) {
  signIn(input: $input) {
    token
    user {
      id
      username
      email
    }
  }
}
"""

# ── Data queries ──────────────────────────────────────────────────────────────

ORDERS_QUERY = """
query GetMyDeals($pagination: OffsetPaginationInput) {
  myDeals(pagination: $pagination) {
    list {
      id
      status
      statusDescription
      createdAt
      completedAt
      amount
      item {
        id
        name
        slug
        price {
          value
          currency { symbol }
        }
      }
      buyer {
        id
        username
      }
    }
    pageInfo {
      total
    }
  }
}
"""

COMPLAINTS_QUERY = """
query GetMyComplaints($pagination: OffsetPaginationInput) {
  myComplaints(pagination: $pagination) {
    list {
      id
      status
      reason
      createdAt
      deal {
        id
        item {
          id
          name
        }
        buyer {
          id
          username
        }
      }
    }
    pageInfo {
      total
    }
  }
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_headers() -> dict:
    headers = dict(BASE_HEADERS)
    token = auth.get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _gql(
    query: str,
    variables: Optional[dict] = None,
    token_override: Optional[str] = None,
) -> dict:
    headers = dict(BASE_HEADERS)
    t = token_override or auth.get_token()
    if t:
        headers["Authorization"] = f"Bearer {t}"

    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        resp = await client.post(PLAYEROK_API_URL, json=payload)
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        msgs = [e.get("message", str(e)) for e in data["errors"]]
        raise RuntimeError("; ".join(msgs))

    return data.get("data", {})


# ── Auth ──────────────────────────────────────────────────────────────────────

async def request_auth_code(email: str) -> bool:
    """Ask Playerok to send an OTP code to the given email."""
    data = await _gql(SEND_CODE_MUTATION, {"input": {"email": email}})
    return bool(data.get("sendConfirmationCode", {}).get("success"))


async def login_with_code(email: str, code: str) -> dict:
    """
    Verify the OTP code and get a session token.
    Returns {"token": str, "username": str} on success.
    Raises RuntimeError on failure.
    """
    data = await _gql(LOGIN_MUTATION, {"input": {"email": email, "code": code}})
    sign_in = data.get("signIn", {})
    token = sign_in.get("token")
    if not token:
        raise RuntimeError("Токен не получен. Проверьте код и попробуйте снова.")
    user = sign_in.get("user", {})
    return {"token": token, "username": user.get("username", "")}


# ── Data fetching ─────────────────────────────────────────────────────────────

async def fetch_orders(limit: int = 20) -> list[dict]:
    try:
        data = await _gql(ORDERS_QUERY, {"pagination": {"limit": limit, "offset": 0}})
        return data.get("myDeals", {}).get("list", [])
    except Exception as e:
        logger.error("fetch_orders error: %s", e)
        return []


async def fetch_complaints(limit: int = 20) -> list[dict]:
    try:
        data = await _gql(COMPLAINTS_QUERY, {"pagination": {"limit": limit, "offset": 0}})
        return data.get("myComplaints", {}).get("list", [])
    except Exception as e:
        logger.error("fetch_complaints error: %s", e)
        return []
