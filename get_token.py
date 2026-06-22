"""
Run this script once to get your Playerok auth token.
Usage: python get_token.py
"""
import httpx
import json

API_URL = "https://playerok.com/graphql"

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) {
    token
    user {
      id
      username
    }
  }
}
"""

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://playerok.com",
    "Referer": "https://playerok.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def main():
    email = input("Playerok email: ").strip()
    password = input("Playerok password: ").strip()

    payload = {
        "query": LOGIN_MUTATION,
        "variables": {
            "input": {"email": email, "password": password}
        },
    }

    resp = httpx.post(API_URL, json=payload, headers=HEADERS)
    data = resp.json()

    if "errors" in data:
        print("Ошибка:", json.dumps(data["errors"], ensure_ascii=False, indent=2))
        return

    login_data = data.get("data", {}).get("login", {})
    token = login_data.get("token")
    user = login_data.get("user", {})

    if token:
        print(f"\n✅ Авторизован как: {user.get('username')}")
        print(f"\nДобавь в .env файл:")
        print(f"PLAYEROK_TOKEN={token}")
    else:
        print("Не удалось получить токен. Ответ:", json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
