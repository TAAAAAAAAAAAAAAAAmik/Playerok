#!/usr/bin/env bash
#
# Установщик Playerok-бота на чистый Debian/Ubuntu сервер.
#
#   curl -sSL https://raw.githubusercontent.com/TAAAAAAAAAAAAAAAAmik/Playerok/claude/what-do-we-have-zd93b3/deploy/bootstrap.sh \
#     | bash -s -- <TELEGRAM_BOT_TOKEN> [TELEGRAM_CHAT_ID]
#
# Повторный запуск безопасен: код обновляется, .env и сохранённый
# токен Playerok остаются на месте.
#
# Если на сервере уже есть другие боты, скрипт их не тронет: при конфликте
# имени каталога или сервиса он останавливается с подсказкой. Поставить
# рядом вторую копию можно так:
#
#   ... | SERVICE=playerok-bot2 APP_DIR=/opt/playerok-bot2 bash -s -- <TOKEN>

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/TAAAAAAAAAAAAAAAAmik/Playerok.git}"
REPO_BRANCH="${REPO_BRANCH:-claude/what-do-we-have-zd93b3}"
APP_DIR="${APP_DIR:-/opt/playerok-bot}"
SERVICE="${SERVICE:-playerok-bot}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"

BOT_TOKEN="${1:-${TELEGRAM_BOT_TOKEN:-}}"
CHAT_ID="${2:-${TELEGRAM_CHAT_ID:-}}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m !  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m X  %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Нужны права root — запустите через sudo."
command -v systemctl >/dev/null || die "systemd не найден, этот скрипт рассчитан на systemd."

# ── 1. Системные пакеты ───────────────────────────────────────────────────────

log "Устанавливаю системные пакеты"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq git curl python3 python3-venv python3-pip
elif command -v dnf >/dev/null; then
    dnf install -y -q git curl python3 python3-pip
elif command -v yum >/dev/null; then
    yum install -y -q git curl python3 python3-pip
else
    warn "Неизвестный пакетный менеджер — полагаюсь на уже установленные git/python3."
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Нужен Python 3.10+, а установлен $(python3 -V 2>&1)."

# ── 2. Код ────────────────────────────────────────────────────────────────────

# На сервере могут жить другие боты. Ничего не удаляем и не перезаписываем:
# при любом пересечении останавливаемся и предлагаем другие имена.

hint_rename() {
    printf '%s\n' \
        "    Поставить рядом отдельную копию:" \
        "      SERVICE=${SERVICE}2 APP_DIR=${APP_DIR}2 bash bootstrap.sh <TOKEN>"
}

UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/$SERVICE.service}"
if [ -f "$UNIT_FILE" ]; then
    unit_dir="$(sed -n 's/^WorkingDirectory=//p' "$UNIT_FILE" | tail -n1)"
    if [ -n "$unit_dir" ] && [ "$unit_dir" != "$APP_DIR" ]; then
        warn "Сервис $SERVICE уже существует и запускается из $unit_dir."
        hint_rename
        die "Не трогаю чужой сервис."
    fi
fi

if [ -e "$APP_DIR" ] && [ ! -d "$APP_DIR/.git" ]; then
    warn "Каталог $APP_DIR уже занят, и это не клон нашего репозитория."
    hint_rename
    die "Ничего не удаляю."
fi

if [ -d "$APP_DIR/.git" ]; then
    origin="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)"
    case "$origin" in
        *[Pp]layerok*)
            log "Обновляю код в $APP_DIR"
            git -C "$APP_DIR" fetch --depth 1 origin "$REPO_BRANCH"
            git -C "$APP_DIR" checkout -B "$REPO_BRANCH" "origin/$REPO_BRANCH"
            ;;
        *)
            warn "В $APP_DIR лежит другой репозиторий: ${origin:-неизвестный}."
            hint_rename
            die "Ничего не перезаписываю."
            ;;
    esac
else
    log "Клонирую репозиторий в $APP_DIR"
    git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
fi

# ── 3. Виртуальное окружение ──────────────────────────────────────────────────

log "Ставлю Python-зависимости"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── 4. Конфигурация ───────────────────────────────────────────────────────────

ENV_FILE="$APP_DIR/.env"

# Достаём значение из существующего .env, чтобы не терять его при переустановке.
from_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1
}

[ -n "$BOT_TOKEN" ] || BOT_TOKEN="$(from_env TELEGRAM_BOT_TOKEN)"
[ -n "$BOT_TOKEN" ] || die "Не передан токен бота. Запустите: bash bootstrap.sh <TELEGRAM_BOT_TOKEN>"

[ -n "$CHAT_ID" ] || CHAT_ID="$(from_env TELEGRAM_CHAT_ID)"

log "Проверяю токен бота"
BOT_NAME="$(
    curl -sS --max-time 20 "https://api.telegram.org/bot${BOT_TOKEN}/getMe" \
    | python3 -c 'import sys,json
d = json.load(sys.stdin)
if not d.get("ok"):
    sys.exit(1)
print(d["result"].get("username", ""))' 2>/dev/null
)" || die "Telegram отклонил токен. Проверьте его в @BotFather."
echo "    Бот: @${BOT_NAME}"

# chat_id можно узнать только после того, как пользователь напишет боту.
resolve_chat_id() {
    curl -sS --max-time 20 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates" \
    | python3 -c 'import sys,json
d = json.load(sys.stdin)
ids = [
    (u.get("message") or u.get("edited_message") or {}).get("chat", {}).get("id")
    for u in d.get("result", [])
]
ids = [i for i in ids if i]
print(ids[-1] if ids else "")' 2>/dev/null
}

if [ -z "$CHAT_ID" ]; then
    log "Определяю chat_id"
    echo "    Откройте в Telegram @${BOT_NAME} и отправьте любое сообщение (например /start)."
    echo "    Жду до 3 минут..."
    for _ in $(seq 36); do
        CHAT_ID="$(resolve_chat_id)"
        [ -n "$CHAT_ID" ] && break
        sleep 5
    done
    [ -n "$CHAT_ID" ] || die "Так и не увидел от вас сообщения. Напишите боту и запустите скрипт снова."
    echo "    Нашёл chat_id: $CHAT_ID"
fi

log "Записываю $ENV_FILE"
umask 077
cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
POLL_INTERVAL=$POLL_INTERVAL
EOF
chmod 600 "$ENV_FILE"

# ── 5. systemd ────────────────────────────────────────────────────────────────

log "Настраиваю systemd-сервис $SERVICE"
# Шаблон в репозитории всегда называется playerok-bot.service; путь и
# идентификатор в журнале подставляем под фактические APP_DIR и SERVICE.
sed -e "s|/opt/playerok-bot|$APP_DIR|g" \
    -e "s|^SyslogIdentifier=.*|SyslogIdentifier=$SERVICE|" \
    "$APP_DIR/deploy/playerok-bot.service" > "$UNIT_FILE"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"

sleep 3
if systemctl is-active --quiet "$SERVICE"; then
    log "Готово — бот запущен"
else
    warn "Сервис не поднялся. Последние строки лога:"
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    exit 1
fi

cat <<EOF

  Дальше в Telegram:
    /login   — войти в аккаунт Playerok (email + код из письма)
    /status  — статус мониторинга
    /check   — проверить прямо сейчас

  Управление на сервере:
    systemctl status $SERVICE
    systemctl restart $SERVICE
    journalctl -u $SERVICE -f      # живой лог

EOF
