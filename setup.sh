#!/usr/bin/env bash
# Установка бота на сервер: venv, зависимости, Chrome, Xvfb.
# Запуск:  bash setup.sh
set -euo pipefail

cd "$(dirname "$0")"
APT_GET="apt-get"
if [ "$(id -u)" -ne 0 ]; then
    APT_GET="sudo apt-get"
fi

echo "── Системные пакеты ──────────────────────────────────────────"
$APT_GET update -qq
# python3-venv — иначе PEP 668 не даст ставить пакеты системным pip;
# xvfb — виртуальный дисплей, на сервере нет графики.
$APT_GET install -y -qq python3-venv python3-full xvfb wget ca-certificates

echo "── Виртуальное окружение ─────────────────────────────────────"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "── Google Chrome ─────────────────────────────────────────────"
if command -v google-chrome >/dev/null 2>&1; then
    echo "уже установлен: $(google-chrome --version)"
else
    tmp_deb="$(mktemp --suffix=.deb)"
    wget -qO "$tmp_deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    # apt сам подтянет зависимости пакета
    $APT_GET install -y -qq "$tmp_deb"
    rm -f "$tmp_deb"
    echo "установлен: $(google-chrome --version)"
fi
# chromedriver отдельно ставить не нужно: Selenium Manager скачает
# подходящую версию при первом запуске.

echo
echo "✅ Готово."
echo
echo "Дальше:"
echo "  1. Впишите в .env токен сессии Playerok:"
echo "       PLAYEROK_COOKIES=token=eyJ..."
echo "     (только token — куку DDoS-Guard браузер получит сам с IP сервера)"
echo "  2. Прогон мастера без Telegram:"
echo "       xvfb-run -a ./venv/bin/python selenium_creator.py"
echo "  3. Запуск бота:"
echo "       xvfb-run -a ./venv/bin/python main.py"
