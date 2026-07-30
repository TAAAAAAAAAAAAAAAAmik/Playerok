#!/usr/bin/env python3
"""Recover Playerok's real GraphQL operations from its frontend bundles.

Introspection is disabled on the endpoint and every guessed mutation name is
rejected with GRAPHQL_VALIDATION_FAILED, so the schema has to come from the
client instead. Playerok's frontend is a Next.js app, which ships its GraphQL
documents as plain strings inside the JS chunks — this script downloads those
chunks and extracts the operations the site itself sends.

Run on the server:

    /opt/playerok-bot/.venv/bin/python /opt/playerok-bot/tools/extract_schema.py

A compact report goes to the terminal; everything found is written to
/tmp/playerok_schema.log.
"""
import re
import sys
from collections import OrderedDict

import httpx

BASE_URL = "https://playerok.com"
LOG_PATH = "/tmp/playerok_schema.log"

# Keep the download bounded — a Next.js build can ship hundreds of chunks.
MAX_CHUNKS = 80
MAX_TOTAL_BYTES = 40 * 1024 * 1024

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

AUTH_WORDS = ("code", "confirm", "sign", "login", "auth", "token", "session", "otp", "email")
DATA_WORDS = ("deal", "order", "complaint", "purchase", "sale", "transaction")

_log = []


def out(line=""):
    print(line)
    _log.append(str(line))


def note(line):
    _log.append(str(line))


def get(url, timeout=30.0):
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as c:
            r = c.get(url)
        return r.status_code, r.text
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ── Step 1: is introspection really closed everywhere? ────────────────────────

def probe_type_introspection():
    """__schema may be blocked while __type is still answered."""
    out("=" * 62)
    out("1. Пробую __type в обход __schema")
    out("=" * 62)

    found = {}
    for type_name in ("Mutation", "Query"):
        query = '{__type(name:"%s"){fields{name}}}' % type_name
        try:
            with httpx.Client(headers={**HEADERS, "Content-Type": "application/json"},
                              timeout=30.0) as c:
                r = c.post(BASE_URL + "/graphql", json={"query": query})
            body = r.text
        except Exception as e:
            out(f"   {type_name}: запрос не удался — {type(e).__name__}: {e}")
            continue

        note(f"--- __type({type_name}) HTTP {r.status_code} ---")
        note(body)

        names = re.findall(r'"name":"([A-Za-z0-9_]+)"', body)
        # Drop the echoed type name itself.
        names = [n for n in names if n != type_name]
        if names:
            found[type_name] = sorted(set(names))
            out(f"   {type_name}: получено {len(set(names))} полей — introspection ЧАСТИЧНО открыта!")
        else:
            snippet = " ".join(body.split())[:200]
            out(f"   {type_name}: закрыто ({snippet})")
    return found


# ── Step 2: mine the frontend bundles ────────────────────────────────────────

def collect_chunk_urls():
    """Find JS chunk URLs referenced by the site's HTML entry points."""
    urls = OrderedDict()
    html_endpoints = set()
    for page in ("/", "/login", "/profile"):
        status, html = get(BASE_URL + page)
        if status != 200:
            note(f"page {page}: HTTP {status}")
            continue
        note(f"--- HTML {page} ({len(html)} bytes) ---")
        for m in re.findall(r'(?:src|href)="(/_next/static/[^"]+\.js)"', html):
            urls[BASE_URL + m] = None
        for m in re.findall(r'"(/_next/static/[^"]+?\.js)"', html):
            urls[BASE_URL + m] = None
        # The endpoint itself may not be /graphql at all.
        html_endpoints.update(re.findall(r'https?://[A-Za-z0-9_.\-]+/graphql[a-z/]*', html))
    for m in sorted(html_endpoints):
        out(f"   В HTML найден GraphQL-URL: {m}")
    return list(urls)


def extract_document(text, start):
    """Return the operation text starting at `start`, cut at its closing brace.

    Bundles concatenate documents, so a fixed-length slice drags in unrelated
    code; following the brace depth keeps each document intact and complete.
    """
    depth = 0
    for i, ch in enumerate(text[start:start + 4000]):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:start + i + 1]
    return text[start:start + 1200]


OPERATION_RE = re.compile(
    r'\b(query|mutation)\s+([A-Za-z0-9_]+)\s*(?:\([^)]{0,400}\))?\s*\{', re.S
)
# graphql-tag compiled to an AST keeps operation names in this shape.
AST_RE = re.compile(
    r'"operation":"(query|mutation)".{0,200}?"value":"([A-Za-z0-9_]+)"', re.S
)


def mine_chunks(chunk_urls):
    out()
    out("=" * 62)
    out("2. Ищу GraphQL-операции в JS-бандлах сайта")
    out("=" * 62)

    if not chunk_urls:
        out("   Не нашёл ни одного JS-чанка — возможно, HTML не отдался.")
        return {}, set()

    out(f"   Найдено чанков: {len(chunk_urls)}, скачиваю до {MAX_CHUNKS}")

    operations = {}          # name -> (kind, source chunk)
    endpoints = set()
    documents = []           # raw operation texts for auth-related ops
    total = 0
    fetched = 0

    for url in chunk_urls[:MAX_CHUNKS]:
        status, text = get(url, timeout=45.0)
        if status != 200:
            note(f"chunk FAIL {status} {url}")
            continue
        fetched += 1
        total += len(text)

        for kind, name in OPERATION_RE.findall(text):
            operations.setdefault(name, (kind, url.rsplit("/", 1)[-1]))
        for kind, name in AST_RE.findall(text):
            operations.setdefault(name, (kind, url.rsplit("/", 1)[-1]))

        for m in re.findall(r'https?://[A-Za-z0-9_.\-]+/graphql[a-z/]*', text):
            endpoints.add(m)

        # Capture full document text around auth-looking operations.
        for m in OPERATION_RE.finditer(text):
            name = m.group(2)
            if any(w in name.lower() for w in AUTH_WORDS):
                documents.append((name, extract_document(text, m.start())))

        if total > MAX_TOTAL_BYTES:
            out(f"   Достигнут лимит объёма после {fetched} чанков — останавливаюсь.")
            break

    out(f"   Скачано {fetched} чанков, {total // 1024} КБ")
    note(f"ALL OPERATIONS: {sorted(operations)}")

    if endpoints:
        out("\n   Обнаруженные GraphQL-эндпоинты:")
        for e in sorted(endpoints):
            out(f"     {e}")

    muts = sorted(n for n, (k, _) in operations.items() if k == "mutation")
    qs = sorted(n for n, (k, _) in operations.items() if k == "query")
    out(f"\n   Всего операций: {len(muts)} мутаций, {len(qs)} запросов")

    def show(title, names, words):
        hits = [n for n in names if any(w in n.lower() for w in words)]
        out(f"\n   {title}:")
        for n in hits[:30] or ["(не найдено)"]:
            out(f"     {n}")
        return hits

    show("Мутации, связанные с авторизацией", muts, AUTH_WORDS)
    show("Запросы про сделки и жалобы", qs, DATA_WORDS)

    if documents:
        out("\n" + "=" * 62)
        out("3. Тексты найденных документов авторизации")
        out("=" * 62)
        seen = set()
        for name, body in documents:
            if name in seen:
                continue
            seen.add(name)
            compact = " ".join(body.split())
            out(f"\n── {name}")
            out("   " + compact[:700])
            note(f"--- DOCUMENT {name} ---")
            note(body)

    return operations, endpoints


def main():
    out("Извлечение схемы GraphQL из фронтенда Playerok")
    out(f"httpx {httpx.__version__}, Python {sys.version.split()[0]}")
    out()

    partial = probe_type_introspection()
    if partial:
        for type_name, names in partial.items():
            out(f"\n   Поля {type_name} (первые 40):")
            for n in names[:40]:
                out(f"     {n}")
        out(f"\n   Полные списки — в {LOG_PATH}")

    chunk_urls = collect_chunk_urls()
    mine_chunks(chunk_urls)

    with open(LOG_PATH, "w") as f:
        f.write("\n".join(_log))

    print("\n" + "=" * 62)
    print(f"Полный лог: {LOG_PATH}")
    print("=" * 62)


if __name__ == "__main__":
    main()
