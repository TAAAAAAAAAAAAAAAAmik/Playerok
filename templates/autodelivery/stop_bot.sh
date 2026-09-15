#!/bin/sh
# Остановить бота и не дать cron поднять его снова сразу.
#
#   ./stop_bot.sh          остановить
#   ./stop_bot.sh --насовсем  остановить и убрать из cron

set -eu

HERE=$(CD=$(dirname "$0"); cd "$CD" && pwd)
PIDFILE="$HERE/state/item_bot.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null || echo "")

    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "остановлен ($PID)"
    else
        echo "уже не работал"
    fi

    rm -f "$PIDFILE"
else
    echo "не найден"
fi

if [ "${1:-}" = "--насовсем" ]; then
    # Иначе cron поднимет его через минуту.
    crontab -l 2>/dev/null | grep -v "run_bot.sh" | crontab - || true
    echo "убран из cron"
fi
