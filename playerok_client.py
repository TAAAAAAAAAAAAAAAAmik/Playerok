import httpx
import logging
from typing import Optional
from config import PLAYEROK_API_URL, PLAYEROK_TOKEN, PLAYEROK_BASE_URL

logger = logging.getLogger(__name__)

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": PLAYEROK_BASE_URL,
    "Referer": PLAYEROK_BASE_URL + "/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# GraphQL query for seller's active orders
ORDERS_QUERY = """
query GetMyOrders($pagination: PaginationInput) {
  myDeals(pagination: $pagination) {
    list {
      id
      status
      statusDescription
      createdAt
      completedAt
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
      amount
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# GraphQL query for complaints/disputes
COMPLAINTS_QUERY = """
query GetMyComplaints($pagination: PaginationInput) {
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
      hasNextPage
      endCursor
    }
  }
}
"""

# Alternative query names that Playerok may use
TRANSACTIONS_QUERY = """
query GetSellerTransactions {
  sellerTransactions(first: 20) {
    edges {
      node {
        id
        status
        createdAt
        amount
        currency
        lot {
          id
          title
        }
        buyer {
          id
          login
        }
      }
    }
  }
}
"""


async def _gql(client: httpx.AsyncClient, query: str, variables: Optional[dict] = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = await client.post(PLAYEROK_API_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        logger.warning("GraphQL errors: %s", data["errors"])

    return data.get("data", {})


async def fetch_new_orders(client: httpx.AsyncClient) -> list[dict]:
    """Fetch recent orders from Playerok."""
    try:
        data = await _gql(client, ORDERS_QUERY, {"pagination": {"first": 20}})
        deals = data.get("myDeals", {}).get("list", [])
        return deals
    except Exception as e:
        logger.error("Error fetching orders: %s", e)
        return []


async def fetch_new_complaints(client: httpx.AsyncClient) -> list[dict]:
    """Fetch recent complaints/disputes from Playerok."""
    try:
        data = await _gql(client, COMPLAINTS_QUERY, {"pagination": {"first": 20}})
        complaints = data.get("myComplaints", {}).get("list", [])
        return complaints
    except Exception as e:
        logger.error("Error fetching complaints: %s", e)
        return []


def build_client() -> httpx.AsyncClient:
    headers = dict(HEADERS)
    if PLAYEROK_TOKEN:
        headers["Authorization"] = f"Bearer {PLAYEROK_TOKEN}"
    return httpx.AsyncClient(headers=headers, timeout=30.0)
