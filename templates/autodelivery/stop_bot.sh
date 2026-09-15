#!/bin/sh
# Остановить бота и не дать cron поднять его снова сразу.
#
#   ./stop_bot.sh          остановить
#   ./stop_bot.sh --насовсем  остановить и убрать из cron

set -eu

HERE=$(CD=$(dirname "$0"); cd "$CD" && pwd)
WHAT=${1:-item_bot.py}

case "$WHAT" in
    --*) WHAT=item_bot.py ;;
esac

PIDFILE="$HERE/state/$(basename "$WHAT" .py).pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null || echo "")

    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        # Сторож, а за ним и сам бот: убить только бота значит получить
        # его обратно через пять секунд.
        kill "$PID" 2>/dev/null || true
        pkill -P "$PID" 2>/dev/null || true
        echo "остановлен ($PID)"
    else
        echo "уже не работал"
    fi

    rm -f "$PIDFILE"
else
    echo "не найден"
fi

if [ "${1:-}" = "--насовсем" ] || [ "${2:-}" = "--насовсем" ]; then
    # Иначе cron поднимет его через минуту.
    if command -v crontab >/dev/null 2>&1; then
        crontab -l 2>/dev/null | grep -v "run_bot.sh" | crontab - || true
        echo "убран из cron"
    fi

    if [ -f "$HOME/.profile" ]; then
        grep -v "run_bot.sh" "$HOME/.profile" > "$HOME/.profile.tmp" \
            && mv "$HOME/.profile.tmp" "$HOME/.profile"
        echo "убран из ~/.profile"
    fi
fi
