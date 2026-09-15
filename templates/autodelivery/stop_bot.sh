#!/bin/sh
# Остановить бота — ВСЕ его копии, а не только записанную в pid-файл.
#
#   ./stop_bot.sh                 остановить бота создания товаров
#   ./stop_bot.sh notify_bot.py   остановить уведомления
#   ./stop_bot.sh --насовсем      ещё и убрать автозапуск
#
# Почему все копии. Запущенный руками «python3 item_bot.py» в pid-файл не
# попадает, и прежняя версия его не трогала. Два бота при этом воруют друг
# у друга сообщения: ответ достаётся то одному, то другому, и тот, кто не
# в курсе диалога, показывает главное меню. Со стороны это выглядит как
# «бот лагает», и перезапуск делает только хуже — копий становится больше.

set -u

HERE=$(CD=$(dirname "$0"); cd "$CD" && pwd)

WHAT=${1:-item_bot.py}

case "$WHAT" in
    --*) WHAT=item_bot.py ;;
esac

NAME=$(basename "$WHAT" .py)
PIDFILE="$HERE/state/$NAME.pid"
KILLED=0

kill_pid() {
    # Сторожа убиваем вместе с его ботом: убить только бота значит
    # получить его обратно через пять секунд.
    kill "$1" 2>/dev/null || true
    pkill -P "$1" 2>/dev/null || true
    KILLED=$(( KILLED + 1 ))
}

# Сначала тот, кого знаем по имени.
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null || echo "")

    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill_pid "$PID"
    fi

    rm -f "$PIDFILE"
fi

# Потом всех остальных: сторожей и самих ботов, как бы их ни запускали.
#
# Искать по куску командной строки нельзя: под «python3 item_bot.py»
# попадает и ваша собственная оболочка, если вы когда-то набирали эту
# команду — а убивать чужой терминал недопустимо. Поэтому каждого
# найденного проверяем по /proc: первый аргумент должен быть python, а
# последний — именно наш файл.
ours() {
    # Процесс может исчезнуть между поиском и чтением: без защиты
    # оборвавшееся чтение роняет весь скрипт из-за строгого режима.
    ARGS=$( { tr '\0' '\n' < "/proc/$1/cmdline"; } 2>/dev/null || true )
    [ -n "$ARGS" ] || return 1

    FIRST=$(printf '%s\n' "$ARGS" | head -1)
    LAST=$(printf '%s\n' "$ARGS" | grep -v '^$' | tail -1)

    case "$FIRST" in
        *python*) ;;
        *) return 1 ;;
    esac

    case "$LAST" in
        *"$NAME.py") return 0 ;;
        *) return 1 ;;
    esac
}

sweep() {
    for PID in $(pgrep -f "$NAME" 2>/dev/null || true); do
        [ "$PID" = "$$" ] && continue
        [ "$PID" = "$PPID" ] && continue
        kill -0 "$PID" 2>/dev/null || continue

        if ours "$PID"; then
            [ "${1:-}" = "-9" ] && kill -9 "$PID" 2>/dev/null || kill_pid "$PID"
        fi
    done

    # Сторож ищется отдельно и по полному пути: короткий шаблон совпадает
    # с чем угодно, где эта строка вообще встречается.
    for PID in $(pgrep -f "$HERE/keep_bot.sh $WHAT" 2>/dev/null || true); do
        [ "$PID" = "$$" ] && continue
        kill -0 "$PID" 2>/dev/null || continue
        [ "${1:-}" = "-9" ] && kill -9 "$PID" 2>/dev/null || kill_pid "$PID"
    done
}

sweep
sleep 1
sweep -9

if [ "$KILLED" -eq 0 ]; then
    echo "$NAME: не работал"
else
    echo "$NAME: остановлен (копий было $KILLED)"
fi

if [ "${1:-}" = "--насовсем" ] || [ "${2:-}" = "--насовсем" ]; then
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
