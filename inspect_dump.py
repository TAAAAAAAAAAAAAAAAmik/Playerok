"""
Компактная сводка по HTML-дампам из debug/ — чтобы не пересылать архив.

    python3 inspect_dump.py            # последний прогон
    python3 inspect_dump.py debug/20260730_164237
    python3 inspect_dump.py debug/20260730_164237 sell   # только про /sell

Только stdlib, venv не нужен.
"""
import glob
import os
import re
import sys

TAG_RE = re.compile(r"<[^>]+>")
PLACEHOLDER_PATTERN = r'placeholder="([^"]+)"'
DROP_RE = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.S | re.I)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()


def find_all(pattern: str, html: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for match in re.findall(pattern, html, re.S | re.I):
        value = text_of(match if isinstance(match, str) else match[0])
        if value and value not in seen and len(value) < 60:
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def report(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    html = DROP_RE.sub(" ", raw)

    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    body = text_of(html)

    print(f"\n══ {os.path.basename(path)}  ({len(raw) // 1024} КБ)")
    print(f"  title: {title.group(1).strip() if title else '—'}")
    print(f"  заголовки: {find_all(r'<h[1-4][^>]*>(.*?)</h[1-4]>', html, 6)}")
    print(f"  кнопки: {find_all(r'<button[^>]*>(.*?)</button>', html, 10)}")
    placeholders = find_all(PLACEHOLDER_PATTERN, html, 8)
    print(f"  placeholder: {placeholders}")
    links = []
    for href in re.findall(r'href="(/[^"#]*)"', html):
        if href not in links:
            links.append(href)
    print(f"  ссылки: {links[:14]}")
    print(f"  текст: {body[:280]}")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    needle = sys.argv[2] if len(sys.argv) > 2 else ""

    if not target:
        runs = sorted(glob.glob("debug/*/"), reverse=True)
        if not runs:
            print("Нет каталога debug/ — сначала прогоните run_check.sh")
            return 1
        target = runs[0]

    print(f"Прогон: {target}")

    routes_file = os.path.join(target, "routes.txt")
    if os.path.exists(routes_file):
        with open(routes_file, encoding="utf-8") as f:
            routes = [r.strip() for r in f if r.strip()]
        print(f"\n══ Маршруты сайта ({len(routes)}):")
        print("  " + ", ".join(routes))

    dumps = sorted(glob.glob(os.path.join(target, "*.html")))
    if needle:
        dumps = [d for d in dumps if needle in os.path.basename(d)]
    for dump in dumps:
        report(dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
