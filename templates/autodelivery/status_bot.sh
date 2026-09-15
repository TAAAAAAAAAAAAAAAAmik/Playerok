#!/bin/sh
# Что из ботов сейчас работает.
#
# Две копии одного бота — частая причина задвоенных сообщений: каждая
# присылает своё. Запущенные через run_bot.sh задвоиться не могут, но
# запущенный руками «python3 notify_bot.py» об этой защите не знает.

set -u

HERE=$(CD=$(dirname "$0"); cd "$CD" && pwd)

for WHAT in item_bot restore_bot notify_bot; do
    GUARDS=$(pgrep -f "$HERE/keep_bot.sh $WHAT" 2>/dev/null | wc -l)
    SELF=$(pgrep -f "python3 -u $WHAT.py" 2>/dev/null | wc -l)
    HAND=$(pgrep -f "python3 $WHAT.py" 2>/dev/null | wc -l)
    TOTAL=$(( SELF + HAND ))

    if [ "$TOTAL" -eq 0 ]; then
        echo "$WHAT: не работает"
    elif [ "$TOTAL" -eq 1 ]; then
        echo "$WHAT: работает (сторожей: $GUARDS)"
    else
        echo "$WHAT: ЗАПУЩЕН $TOTAL РАЗ — отсюда задвоенные сообщения"
        echo "   лишние: pkill -f '$WHAT.py' и потом $HERE/run_bot.sh $WHAT.py"
    fi
done
