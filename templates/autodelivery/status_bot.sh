#!/bin/sh
# Что из ботов работает, и не устарел ли запущенный код.
#
# Две беды, которые отсюда видно сразу:
#   * бот запущен дважды — тогда сообщения задваиваются;
#   * код обновили, а бота не перезапустили — тогда бот ведёт себя
#     по-старому, и это выглядит как «не работает то, что добавили».

set -u

HERE=$(CD=$(dirname "$0"); cd "$CD" && pwd)
NOW=$(date +%s)

for WHAT in item_bot delivery_bot notify_bot restore_bot; do
    FILE="$HERE/$WHAT.py"
    PIDS=$(pgrep -f "python3 -u $WHAT.py" 2>/dev/null || true)
    HAND=$(pgrep -f "python3 $WHAT.py" 2>/dev/null || true)
    ALL=$(printf '%s\n%s\n' "$PIDS" "$HAND" | grep -c '[0-9]' || true)

    if [ "$ALL" -eq 0 ]; then
        echo "$WHAT: не работает"
        continue
    fi

    if [ "$ALL" -gt 1 ]; then
        echo "$WHAT: ЗАПУЩЕН $ALL РАЗ — отсюда задвоенные сообщения"
        echo "   лечится: $HERE/stop_bot.sh $WHAT.py, потом run_bot.sh $WHAT.py"
        continue
    fi

    PID=$(printf '%s\n%s\n' "$PIDS" "$HAND" | grep '[0-9]' | head -1)
    LIVED=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
    STARTED=$(( NOW - ${LIVED:-0} ))
    CHANGED=$(date -r "$FILE" +%s 2>/dev/null || echo 0)

    if [ "$CHANGED" -gt "$STARTED" ]; then
        AGO=$(( (NOW - CHANGED) / 60 ))
        echo "$WHAT: работает, НО КОД ОБНОВЛЁН ${AGO} мин назад — перезапустите"
        echo "   $HERE/stop_bot.sh $WHAT.py && $HERE/run_bot.sh $WHAT.py"
    else
        echo "$WHAT: работает, код свежий"
    fi
done
