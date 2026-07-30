#!/usr/bin/env bash
# Проверка мастера создания товара одной командой.
#
#   bash run_check.sh <token>
#
# Прописывает токен в .env, прогоняет 9 шагов в браузере и собирает
# скриншоты/HTML в архив, который можно прислать разработчику.
set -uo pipefail

cd "$(dirname "$0")"
TOKEN="${1:-}"

if [ -n "$TOKEN" ]; then
    touch .env
    # затираем прежнее значение, чтобы не копились дубли
    grep -v '^PLAYEROK_COOKIES=' .env > .env.tmp 2>/dev/null || true
    mv .env.tmp .env
    echo "PLAYEROK_COOKIES=token=$TOKEN" >> .env
    echo "✔ Токен записан в .env"
fi

if [ ! -x venv/bin/python ]; then
    echo "✖ Нет venv. Сначала: bash setup.sh"
    exit 1
fi

RUNNER="./venv/bin/python"
if command -v xvfb-run >/dev/null 2>&1; then
    RUNNER="xvfb-run -a ./venv/bin/python"
else
    echo "⚠ xvfb-run не найден, запускаю в headless-режиме"
    export SELENIUM_HEADLESS=1
fi

echo "── Прогон мастера ────────────────────────────────────────────"
set +e
$RUNNER selenium_creator.py 2>&1 | tee last_run.log
STATUS=${PIPESTATUS[0]}
set -e

LATEST="$(ls -1dt debug/*/ 2>/dev/null | head -1)"
if [ -n "$LATEST" ]; then
    ARCHIVE="playerok-debug-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$ARCHIVE" "$LATEST" last_run.log
    echo
    echo "📦 Отчёт: $(pwd)/$ARCHIVE"
    echo "   (скриншоты + HTML шагов + лог; пришлите этот файл)"
    echo
    echo "Шаги, до которых дошло:"
    ls -1 "$LATEST" | sed 's/^/   /'
fi

if [ "$STATUS" -eq 0 ]; then
    echo "✅ Мастер прошёл все 9 шагов."
else
    echo "❌ Мастер остановился, код $STATUS. Последние строки лога:"
    tail -n 15 last_run.log | sed 's/^/   /'
fi
exit "$STATUS"
