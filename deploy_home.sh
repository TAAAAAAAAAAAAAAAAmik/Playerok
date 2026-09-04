#!/usr/bin/env bash
# Установка бота без прав root — целиком в домашнюю папку.
#
# Нужна, когда sudo отвечает «may not run sudo»: тогда ни apt, ни служба
# systemd недоступны. Всё, на что здесь опираемся, есть в любой Ubuntu и
# работает от обычного пользователя:
#
#   * код       — тарболлом с GitHub (git ставить нечем);
#   * venv      — своим Python, системные пакеты не трогаем;
#   * Chrome    — .deb распаковывается dpkg-deb'ом в домашнюю папку, туда же
#                 идут его библиотеки, скачанные apt-get download (он root
#                 не требует, читает уже готовые списки пакетов);
#   * автозапуск — cron: @reboot поднимает бота, а проверка раз в 5 минут
#                 возвращает его, если он упал. Это замена systemd.
#
# Запуск:  bash deploy_home.sh
set -euo pipefail

REPO="TAAAAAAAAAAAAAAAAmik/Playerok"
BRANCH="claude/product-creation-lsfuo0"
ROOT="${PLAYEROK_HOME:-$HOME/playerok-bot}"
SYSROOT="$ROOT/sysroot"          # сюда распаковывается Chrome со своими библиотеками
LIBS="$SYSROOT/usr/lib/x86_64-linux-gnu:$SYSROOT/usr/lib:$SYSROOT/lib/x86_64-linux-gnu"

say() { printf '\n── %s %s\n' "$1" "$(printf '─%.0s' $(seq 1 $((56 - ${#1}))))"; }

ask() {
    local prompt="$1" current="${2:-}" required="${3:-0}" answer=""
    if [ -n "$current" ]; then
        echo "  $prompt — уже задано, оставляю." >&2
        printf '%s' "$current"
        return
    fi
    # Спрашиваем терминал напрямую: скрипт могли запустить через конвейер, и
    # обычный read съел бы не ответ, а сам скрипт. Наличие терминала проверяем
    # попыткой открыть его — /dev/tty есть и там, где открыть его нельзя.
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
    # Переменная окружения главнее: так установку можно пройти без вопросов,
    # передав значения одной строкой, и не хранить их в репозитории.
    local from_env="${!1:-}"
    if [ -n "$from_env" ]; then
        printf '%s' "$from_env"
        return
    fi
    [ -f "$ROOT/.env" ] || return 0
    sed -n "s/^$1=//p" "$ROOT/.env" | tail -1
}

# ── Код ───────────────────────────────────────────────────────────────────────
say "Код бота"
mkdir -p "$ROOT"
if [ -d "$ROOT/.git" ] && command -v git > /dev/null 2>&1; then
    git -C "$ROOT" pull --ff-only
else
    # Тарболлом, потому что git на сервере может быть не установлен, а поставить
    # его нечем. --strip-components=1 снимает верхнюю папку из архива.
    echo "  Качаю $BRANCH…"
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
        | tar xz --strip-components=1 -C "$ROOT"
fi
cd "$ROOT"
echo "  Код в $ROOT"

# ── Python ────────────────────────────────────────────────────────────────────
say "Python-окружение"
if [ ! -x "$ROOT/venv/bin/python" ]; then
    if ! python3 -m venv venv 2> /dev/null; then
        # Без пакета python3-venv модуль ensurepip недоступен, а поставить его
        # нечем. Тогда делаем окружение без pip и приносим pip отдельно.
        echo "  ensurepip недоступен — создаю окружение и ставлю pip вручную"
        python3 -m venv --without-pip venv
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        ./venv/bin/python /tmp/get-pip.py --quiet
        rm -f /tmp/get-pip.py
    fi
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
echo "  Зависимости поставлены: $(./venv/bin/python -V)"

# ── Chrome ────────────────────────────────────────────────────────────────────
say "Chrome"
CHROME=""
for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$candidate" > /dev/null 2>&1; then
        CHROME="$(command -v "$candidate")"
        echo "  Уже установлен: $CHROME"
        break
    fi
done

if [ -z "$CHROME" ] && [ -x "$SYSROOT/opt/google/chrome/google-chrome" ]; then
    CHROME="$SYSROOT/opt/google/chrome/google-chrome"
    echo "  Уже распакован: $CHROME"
fi

# Отдельными функциями, чтобы их неудача не роняла установку: бот полезен и
# без Chrome, а вызов под `if` отключает для них выход по ошибке.
fetch_chrome() {
    mkdir -p "$SYSROOT" "$ROOT/.debs"
    cd "$ROOT/.debs"
    curl -fsSL -o chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb || return 1
    dpkg-deb -x chrome.deb "$SYSROOT"
    cd "$ROOT"
    rm -rf "$ROOT/.debs"
    [ -x "$SYSROOT/opt/google/chrome/google-chrome" ]
}

fetch_chrome_libs() {
    mkdir -p "$SYSROOT" "$ROOT/.debs"
    cd "$ROOT/.debs"
    # Библиотеки Chrome: на «минимизированной» Ubuntu их нет, а поставить
    # системно нечем. apt-get download работает от пользователя — он лишь
    # скачивает файлы, ничего не устанавливая.
    #
    # Скачиваем по одному пакету: одно несуществующее имя валит всю команду,
    # а имена разъезжаются между выпусками (в Ubuntu 24.04 многие получили
    # суффикс t64). Лишние просто не найдутся, и это не беда.
    #
    # Ядро системы — libc6, libstdc++6, libgcc-s1 — намеренно не трогаем:
    # подменять их своими копиями опаснее, чем оставить системные.
    for pkg in \
        libnss3 libnspr4 libgbm1 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxrender1 \
        libx11-6 libx11-xcb1 libxcb1 libxext6 libxi6 libxtst6 libxcursor1 \
        libxinerama1 libxshmfence1 libxcb-dri3-0 libxcb-present0 \
        libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxau6 libxdmcp6 \
        libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libcairo-gobject2 \
        libpixman-1-0 libfontconfig1 libfreetype6 libharfbuzz0b \
        libgraphite2-3 libthai0 libdatrie1 libfribidi0 libpng16-16 \
        libgdk-pixbuf-2.0-0 libepoxy0 libexpat1 libdbus-1-3 \
        libwayland-client0 libwayland-cursor0 libwayland-egl1 \
        libbrotlidec1 libbrotlicommon1 \
        libasound2t64 libasound2 libasound2-data \
        libatk1.0-0t64 libatk1.0-0 libatk-bridge2.0-0t64 libatk-bridge2.0-0 \
        libcups2t64 libcups2 libatspi2.0-0t64 libatspi2.0-0 \
        libglib2.0-0t64 libglib2.0-0
    do
        apt-get download "$pkg" > /dev/null 2>&1 || true
    done

    local count=0
    for deb in *.deb; do
        [ -e "$deb" ] || continue
        dpkg-deb -x "$deb" "$SYSROOT" && count=$((count + 1))
    done
    cd "$ROOT"
    rm -rf "$ROOT/.debs"
    echo "  Библиотек распаковано: $count"
    [ "$count" -gt 0 ]
}

chrome_runs() {
    [ -n "$CHROME" ] || return 1
    LD_LIBRARY_PATH="$LIBS" "$CHROME" --headless=new --no-sandbox \
        --disable-gpu --dump-dom about:blank > /dev/null 2>&1
}

if [ -z "$CHROME" ]; then
    echo "  Ставлю в домашнюю папку (без root)…"
    if fetch_chrome; then
        CHROME="$SYSROOT/opt/google/chrome/google-chrome"
    else
        cd "$ROOT"
        rm -rf "$ROOT/.debs"
        echo "  ⚠️  Скачать Chrome не вышло."
    fi
fi

# Проверяем не наличие файла, а способность запуститься: библиотек может не
# хватать, и узнать об этом лучше сейчас, чем при первом /create. Не пошёл —
# доносим библиотеки и пробуем ещё раз: при повторном запуске скрипта Chrome
# уже на месте, и без этого шага список библиотек никогда бы не обновился.
CHROME_OK=0
if chrome_runs; then
    CHROME_OK=1
    echo "  Chrome запускается: $CHROME"
elif [ -n "$CHROME" ] && [ "$CHROME" = "$SYSROOT/opt/google/chrome/google-chrome" ]; then
    echo "  Не запустился — доношу библиотеки…"
    fetch_chrome_libs || cd "$ROOT"
    rm -rf "$ROOT/.debs"
    if chrome_runs; then
        CHROME_OK=1
        echo "  Chrome запускается: $CHROME"
    fi
fi

if [ "$CHROME_OK" = 0 ]; then
    echo "  ⚠️  Chrome не работает. Бот запустится, мониторинг и /delete будут"
    echo "      в порядке, а /create не сможет наполнить каталог с нуля."
    if [ -x "$SYSROOT/opt/google/chrome/chrome" ]; then
        # ldd натравливаем на сам бинарник: google-chrome рядом — это
        # скрипт-обёртка, и про её зависимости ldd ничего не скажет.
        echo "      Не хватает библиотек:"
        LD_LIBRARY_PATH="$LIBS" ldd "$SYSROOT/opt/google/chrome/chrome" 2> /dev/null \
            | sed -n 's/^\s*\(\S*\) => not found/        \1/p' | sort -u
        echo "      Полный список — bash $ROOT/chrome_check.sh"
        cat > "$ROOT/chrome_check.sh" <<CHECK
#!/usr/bin/env bash
# Показывает, каких библиотек не хватает Chrome.
LD_LIBRARY_PATH="$LIBS" ldd "$SYSROOT/opt/google/chrome/chrome" | grep 'not found' | sort -u
CHECK
        chmod +x "$ROOT/chrome_check.sh"
    fi
fi

# ── Настройки ─────────────────────────────────────────────────────────────────
say "Настройки (.env)"
BOT_TOKEN="$(ask 'Токен Telegram-бота (от @BotFather)' "$(value_of TELEGRAM_BOT_TOKEN)" 1)"
CHAT_ID="$(ask 'Ваш Telegram chat id (скажет @userinfobot)' "$(value_of TELEGRAM_CHAT_ID)" 1)"
# Токен Playerok здесь не спрашиваем: его присылают боту командой /token,
# он проверяется на живом аккаунте и подхватывается без перезапуска.
PLAYEROK_TOKEN="$(value_of PLAYEROK_COOKIES)"

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "❌ Без токена бота и chat id он не запустится — прерываю." >&2
    exit 1
fi

case "$PLAYEROK_TOKEN" in
    token=*) ;;
    "")      echo "  Токен Playerok спросит сам бот — командой /token в чате." ;;
    *)       PLAYEROK_TOKEN="token=$PLAYEROK_TOKEN" ;;
esac

cat > "$ROOT/.env" <<ENV
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
PLAYEROK_COOKIES=$PLAYEROK_TOKEN
CREATE_MODE=api
PLAYEROK_DISCOUNT=27
# Xvfb ставить нечем, поэтому Chrome идёт headless.
SELENIUM_HEADLESS=1
CHROME_BINARY=$CHROME
ENV
chmod 600 "$ROOT/.env"
echo "  .env записан (доступ только владельцу)"

# ── Запуск ────────────────────────────────────────────────────────────────────
say "Запуск"
cat > "$ROOT/run.sh" <<RUN
#!/usr/bin/env bash
# Поднимает бота, если он ещё не работает. Вызывается из cron и вручную.
cd "$ROOT"
if pgrep -u "\$(id -u)" -f "$ROOT/venv/bin/python .*main.py" > /dev/null; then
    exit 0
fi
export LD_LIBRARY_PATH="$LIBS\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "$ROOT/venv/bin/python" "$ROOT/main.py" >> "$ROOT/bot.log" 2>&1
RUN
chmod +x "$ROOT/run.sh"

cat > "$ROOT/stop.sh" <<STOP
#!/usr/bin/env bash
pkill -u "\$(id -u)" -f "$ROOT/venv/bin/python .*main.py" && echo "Бот остановлен." \\
    || echo "Бот и так не работал."
STOP
chmod +x "$ROOT/stop.sh"

# systemd от пользователя тут обычно недоступен (для него нужен linger, а его
# включает root), поэтому автозапуск вешаем на cron: он есть у пользователя.
if command -v crontab > /dev/null 2>&1; then
    ( crontab -l 2> /dev/null | grep -v "$ROOT/run.sh"
      echo "@reboot $ROOT/run.sh"
      echo "*/5 * * * * $ROOT/run.sh" ) | crontab - && \
        echo "  cron: запуск при перезагрузке и проверка каждые 5 минут"
else
    echo "  ⚠️  crontab недоступен — бота придётся поднимать вручную: $ROOT/run.sh"
fi

"$ROOT/stop.sh" > /dev/null 2>&1 || true
nohup "$ROOT/run.sh" > /dev/null 2>&1 &
sleep 5

echo
if pgrep -u "$(id -u)" -f "$ROOT/venv/bin/python .*main.py" > /dev/null; then
    echo "✅ Бот запущен. Напишите ему /start в Telegram."
    [ "$CHROME_OK" = 0 ] && echo "   (Chrome не работает — /create пока не сможет наполнить каталог)"
else
    # Питон роняет длинную трассировку, а причина — в её последней строке.
    # Показываем сначала её, иначе она теряется среди путей к файлам.
    echo "❌ Бот не поднялся."
    if [ -s "$ROOT/bot.log" ]; then
        echo "   Причина: $(grep -v '^\s' "$ROOT/bot.log" | tail -n 1)"
        echo
        echo "   Подробнее — $ROOT/bot.log, последние строки:"
        tail -n 15 "$ROOT/bot.log"
    else
        echo "   Журнал пуст — похоже, дело в самом запуске."
    fi
    exit 1
fi

echo
echo "Полезное:"
echo "  tail -f $ROOT/bot.log      — журнал"
echo "  $ROOT/stop.sh              — остановить"
echo "  $ROOT/run.sh               — запустить"
echo "  bash $ROOT/deploy_home.sh  — обновить (заново спрашивать ничего не будет)"
