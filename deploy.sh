#!/usr/bin/env bash
# Разворачивает бота на чистом сервере: окружение, .env, служба systemd.
# Запуск:  bash deploy.sh
#
# Всё, что скрипт спрашивает, — три значения для .env. Уже заполненные
# оставляет как есть, так что повторный запуск безопасен: он же и обновление.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

# Вопросы задаём терминалу напрямую: скрипт могли запустить через конвейер,
# и тогда обычный read съел бы не ответ пользователя, а сам скрипт. Если
# терминала нет вовсе, спрашиваем обычным способом — молчать нельзя.
ask() {
    local prompt="$1" current="${2:-}" required="${3:-0}" answer=""
    if [ -n "$current" ]; then
        echo "  $prompt — уже задано, оставляю." >&2
        printf '%s' "$current"
        return
    fi
    # Терминал проверяем попыткой открыть его: файл /dev/tty есть и там, где
    # открыть его нельзя, поэтому [ -r ] тут обманывает.
    #
    # Пустой ответ переспрашиваем: в мобильных клиентах ввод легко проскакивает
    # мимо, и обиднее всего узнать об этом в самом конце установки.
    local tries=0
    while [ -z "$answer" ] && [ "$tries" -lt 5 ]; do
        tries=$((tries + 1))
        if (exec 3<>/dev/tty) 2>/dev/null; then
            printf '  %s: ' "$prompt" > /dev/tty
            read -r answer < /dev/tty || break
        else
            printf '  %s: ' "$prompt" >&2
            read -r answer || break
        fi
        [ -z "$answer" ] && [ "$required" = 1 ] && echo "  (пусто — повторите)" >&2
        [ "$required" = 1 ] || break
    done
    printf '%s' "$answer"
}

value_of() {
    # Значение переменной из .env, пустая строка — если файла или строки нет.
    [ -f .env ] || return 0
    sed -n "s/^$1=//p" .env | tail -1
}

echo "── Окружение ─────────────────────────────────────────────────"
bash setup.sh

echo
echo "── Настройки (.env) ──────────────────────────────────────────"
BOT_TOKEN="$(ask 'Токен Telegram-бота (от @BotFather)' "$(value_of TELEGRAM_BOT_TOKEN)" 1)"
CHAT_ID="$(ask 'Ваш Telegram chat id (скажет @userinfobot)' "$(value_of TELEGRAM_CHAT_ID)" 1)"
PLAYEROK_TOKEN="$(ask 'Токен Playerok (кука token с сайта)' "$(value_of PLAYEROK_COOKIES)")"

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "❌ Без токена бота и chat id он не запустится — прерываю." >&2
    exit 1
fi

# Куку пишем целиком: пользователь мог ввести и «token=eyJ…», и просто «eyJ…».
case "$PLAYEROK_TOKEN" in
    token=*) ;;
    "")      echo "  ⚠️  Токен Playerok пуст: бот запустится, но /create и /delete"
             echo "      работать не будут — впишите его в .env и перезапустите." ;;
    *)       PLAYEROK_TOKEN="token=$PLAYEROK_TOKEN" ;;
esac

cat > .env <<ENV
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
PLAYEROK_COOKIES=$PLAYEROK_TOKEN
CREATE_MODE=api
PLAYEROK_DISCOUNT=27
ENV
chmod 600 .env
echo "  .env записан (доступ только владельцу)"

echo
echo "── Служба systemd ────────────────────────────────────────────"
# Юнит в репозитории рассчитан на /opt/playerok-bot. Если каталог другой,
# подставляем настоящий, иначе служба не найдёт ни python, ни main.py.
sed "s#/opt/playerok-bot#$ROOT#g" systemd/playerok-bot.service \
    | $SUDO tee /etc/systemd/system/playerok-bot.service > /dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable playerok-bot
# restart, а не start: служба могла уже работать со старым кодом.
$SUDO systemctl restart playerok-bot
sleep 4

echo
if systemctl is-active --quiet playerok-bot; then
    echo "✅ Бот запущен. Напишите ему /start в Telegram."
else
    echo "❌ Служба не поднялась. Что случилось:"
    $SUDO journalctl -u playerok-bot -n 30 --no-pager
    exit 1
fi

echo
echo "Полезное:"
echo "  systemctl status playerok-bot --no-pager"
echo "  journalctl -u playerok-bot -f"
echo "  cd $ROOT && git pull && sudo systemctl restart playerok-bot"
