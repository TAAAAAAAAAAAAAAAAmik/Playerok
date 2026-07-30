# Запуск на сервере

## Установка

```bash
cd /opt/playerok-bot
git pull
bash setup.sh
```

Скрипт ставит `python3-venv`, `xvfb`, Google Chrome, создаёт `venv/` и
зависимости. Chromedriver отдельно не нужен — Selenium Manager скачает
подходящий под установленный Chrome при первом запуске.

### Почему venv обязателен

На Debian 12+/Ubuntu 24.04 системный pip закрыт (PEP 668):

```
error: externally-managed-environment
```

Ставить пакеты нужно в venv: `./venv/bin/pip install -r requirements.txt`.
Флаг `--break-system-packages` использовать не стоит — он ломает системный
Python. И `python` в системе нет, только `python3`; внутри venv — `venv/bin/python`.

## Куки Playerok на сервере

В `.env` достаточно **одного токена**:

```
PLAYEROK_COOKIES=token=eyJhbGciOiJIUzI1NiIs...
```

Куку DDoS-Guard (`__ddg5_`) со своего компьютера копировать **не надо**: она
привязана к IP и User-Agent, а у сервера IP другой, и она сразу станет
недействительной. Браузер, запущенный на сервере, пройдёт проверку сам и получит
свою куку — поэтому браузерный сценарий на сервере работает, а «голые» запросы с
чужой кукой нет.

Где взять `token`: DevTools вашего браузера → Application → Cookies →
`https://playerok.com` → значение `token`.

## Графики на сервере нет

Chrome нужен дисплей. Два варианта:

```bash
# 1) виртуальный дисплей (надёжнее: DDoS-Guard строже к headless)
xvfb-run -a ./venv/bin/python main.py

# 2) headless-режим
SELENIUM_HEADLESS=1 ./venv/bin/python main.py
```

## systemd

В юните укажите python из venv и запуск через `xvfb-run`:

```ini
[Service]
WorkingDirectory=/opt/playerok-bot
ExecStart=/usr/bin/xvfb-run -a /opt/playerok-bot/venv/bin/python /opt/playerok-bot/main.py
Restart=always
```

После правки:

```bash
systemctl daemon-reload
systemctl restart playerok-bot
journalctl -u playerok-bot -f
```

## Проверка мастера без Telegram

```bash
xvfb-run -a ./venv/bin/python selenium_creator.py
```

Скриншоты и HTML каждого шага — в `debug/<дата_время>/`.
