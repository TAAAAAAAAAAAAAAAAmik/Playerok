#!/usr/bin/env python3
"""Diagnose the Playerok GraphQL API.

The login mutations in playerok_client.py were written without access to the
real schema. This script probes the endpoint from a host that can actually
reach playerok.com and reports what the server accepts, so the client can be
corrected against facts instead of guesses.

Run on the server:

    /opt/playerok-bot/.venv/bin/python /opt/playerok-bot/tools/probe_api.py

Nothing is sent to a real mailbox: the send-code probe uses an address on
example.com, and a schema-validation error arrives before any mail is queued.
"""
import json
import sys

import httpx

API_URL = "https://playerok.com/graphql"
BASE_URL = "https://playerok.com"
LOG_PATH = "/tmp/playerok_probe.log"
DUMMY_EMAIL = "probe-no-such-user@example.com"

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Playerok's frontend is an Apollo client; some deployments reject or 500 on
# requests that arrive without these.
APOLLO_HEADERS = {
    "apollographql-client-name": "web",
    "apollographql-client-version": "1.0.0",
    "x-timezone-offset": "-180",
}

_log = []


def out(line=""):
    """Print for the operator and keep a copy for the full log file."""
    print(line)
    _log.append(str(line))


def note(line):
    """Record in the log file only — keeps the terminal output paste-friendly."""
    _log.append(str(line))


def post(payload, headers=None, timeout=30.0):
    hdrs = dict(BASE_HEADERS)
    if headers:
        hdrs.update(headers)
    try:
        with httpx.Client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
            r = c.post(API_URL, json=payload)
        return r.status_code, r.text, dict(r.headers)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}


def short(text, limit=400):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + " …[обрезано]"


def gql_errors(body):
    """Extract GraphQL error messages from a response body, if it is JSON."""
    try:
        data = json.loads(body)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("errors"):
        return [e.get("message", str(e)) for e in data["errors"]]
    return None


def report(label, status, body, headers, show_body=True):
    out(f"\n── {label}")
    out(f"   HTTP: {status}")
    server = headers.get("server") or headers.get("Server")
    if server:
        out(f"   Server: {server}")
    for key in ("cf-ray", "cf-mitigated", "x-served-by"):
        if key in headers:
            out(f"   {key}: {headers[key]}")
    errs = gql_errors(body)
    if errs:
        out("   GraphQL errors:")
        for m in errs:
            out(f"     - {short(m, 300)}")
    elif show_body:
        out(f"   Body: {short(body)}")
    note(f"--- FULL BODY [{label}] ---")
    note(body)


# ── Probes ────────────────────────────────────────────────────────────────────

def probe_reachability():
    out("\n" + "=" * 62)
    out("1. Отвечает ли эндпоинт вообще")
    out("=" * 62)
    status, body, headers = post({"query": "{__typename}"})
    report("query {__typename}", status, body, headers)
    return status


def probe_apollo_headers():
    out("\n" + "=" * 62)
    out("2. Влияют ли заголовки Apollo на результат")
    out("=" * 62)
    payload = {
        "query": "mutation($input: SendCodeInput!){sendConfirmationCode(input:$input){success}}",
        "variables": {"input": {"email": DUMMY_EMAIL}},
    }
    s1, b1, h1 = post(payload)
    report("текущий запрос бота, базовые заголовки", s1, b1, h1)
    s2, b2, h2 = post(payload, APOLLO_HEADERS)
    report("тот же запрос + заголовки Apollo", s2, b2, h2)
    if s1 != s2:
        out(f"\n   ВАЖНО: статус изменился {s1} -> {s2}, дело в заголовках.")
    return s1, s2


def probe_introspection():
    out("\n" + "=" * 62)
    out("3. Introspection — настоящие имена запросов и мутаций")
    out("=" * 62)
    query = """
    query {
      __schema {
        queryType { fields { name } }
        mutationType { fields { name } }
      }
    }
    """
    status, body, headers = post({"query": query}, APOLLO_HEADERS)
    if status != 200:
        report("introspection", status, body, headers)
        out("\n   Introspection недоступна — переходим к шагу 4.")
        return None

    try:
        schema = json.loads(body)["data"]["__schema"]
        queries = sorted(f["name"] for f in (schema["queryType"] or {}).get("fields", []))
        mutations = sorted(f["name"] for f in (schema["mutationType"] or {}).get("fields", []))
    except Exception as e:
        report("introspection (не разобрал ответ)", status, body, headers)
        out(f"   Ошибка разбора: {e}")
        return None

    note(f"ALL QUERIES: {queries}")
    note(f"ALL MUTATIONS: {mutations}")
    out(f"\n   Introspection работает: {len(queries)} запросов, {len(mutations)} мутаций.")
    out("   Полные списки — в " + LOG_PATH)

    keywords = ("code", "sign", "login", "auth", "token", "session", "confirm", "otp")
    hits = [m for m in mutations if any(k in m.lower() for k in keywords)]
    out("\n   Мутации, похожие на авторизацию:")
    for m in hits or ["(ничего не нашлось)"]:
        out(f"     - {m}")

    keywords = ("deal", "order", "complaint", "purchase", "sale", "me", "viewer")
    hits = [q for q in queries if any(k in q.lower() for k in keywords)]
    out("\n   Запросы, похожие на сделки/жалобы:")
    for q in hits[:25] or ["(ничего не нашлось)"]:
        out(f"     - {q}")

    out("\n   Что из этого ожидает наш код:")
    for name, pool in (
        ("sendConfirmationCode", mutations),
        ("signIn", mutations),
        ("myDeals", queries),
        ("myComplaints", queries),
    ):
        out(f"     {'ЕСТЬ ' if name in pool else 'НЕТ  '} {name}")
    return {"queries": queries, "mutations": mutations}


def probe_field_validation():
    """Without introspection, GraphQL validation errors still reveal field names."""
    out("\n" + "=" * 62)
    out("4. Проверка полей через ошибки валидации")
    out("=" * 62)
    candidates = [
        "sendConfirmationCode",
        "sendAuthCode",
        "sendEmailConfirmationCode",
        "requestAuthCode",
        "sendCode",
        "signIn",
        "login",
        "authenticate",
    ]
    for name in candidates:
        status, body, headers = post(
            {"query": "mutation{%s{__typename}}" % name}, APOLLO_HEADERS
        )
        errs = gql_errors(body) or []
        joined = " ".join(errs).lower()
        if "cannot query field" in joined or "unknown field" in joined:
            verdict = "нет такого поля"
        elif errs:
            verdict = "СУЩЕСТВУЕТ (ошибка про аргументы/тип, а не про имя)"
        elif status == 200:
            verdict = "СУЩЕСТВУЕТ (ответ 200)"
        else:
            verdict = f"неясно (HTTP {status})"
        out(f"   {name:32} {verdict}")
        note(f"[{name}] HTTP {status} :: {short(body, 500)}")


def main():
    out("Диагностика Playerok GraphQL API")
    out(f"Эндпоинт: {API_URL}")
    out(f"httpx {httpx.__version__}, Python {sys.version.split()[0]}")

    status = probe_reachability()
    if status is None:
        out("\nДо эндпоинта не удалось дойти — проверьте сеть на сервере.")
    else:
        probe_apollo_headers()
        schema = probe_introspection()
        if schema is None:
            probe_field_validation()

    with open(LOG_PATH, "w") as f:
        f.write("\n".join(_log))

    print("\n" + "=" * 62)
    print(f"Полный лог: {LOG_PATH}")
    print("Пришлите вывод выше — по нему станет ясно, как починить запросы.")
    print("=" * 62)


if __name__ == "__main__":
    main()
