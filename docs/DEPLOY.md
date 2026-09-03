# Запуск на сервере

## С нуля — две команды

```bash
sudo mkdir -p /opt/playerok-bot && sudo chown $USER:$USER /opt/playerok-bot
git clone -b claude/product-creation-lsfuo0 \
  https://github.com/TAAAAAAAAAAAAAAAAmik/Playerok.git /opt/playerok-bot

cd /opt/playerok-bot && bash deploy.sh
```

`deploy.sh` ставит окружение, спрашивает три значения (токен Telegram-бота,
ваш chat id, токен Playerok), пишет `.env` с правами 600, ставит службу и
запускает её. Если служба не поднялась, скрипт сам покажет журнал.

Повторный запуск безопасен: уже заполненные значения он не переспрашивает, так
что `git pull && bash deploy.sh` работает и как обновление. Каталог может быть
любым — путь в юнит подставляется настоящий, не только `/opt/playerok-bot`.

## Без прав root

Если `sudo -ln` отвечает «Sorry, user … may not run sudo», ни `apt`, ни служба
systemd недоступны. Тогда бот ставится целиком в домашнюю папку:

```bash
curl -fsSL https://raw.githubusercontent.com/TAAAAAAAAAAAAAAAAmik/Playerok/claude/product-creation-lsfuo0/deploy_home.sh -o deploy_home.sh
bash deploy_home.sh
```

Чем этот путь отличается от обычного:

| | с root | без root |
|---|---|---|
| код | `git clone` | тарболл с GitHub (`curl`), git не нужен |
| Chrome | `apt install` | `.deb` распаковывается `dpkg-deb -x` в `~/playerok-bot/sysroot`, библиотеки — `apt-get download` (root не требует) |
| дисплей | Xvfb | его нечем поставить, поэтому `SELENIUM_HEADLESS=1` |
| автозапуск | systemd | cron: `@reboot` плюс проверка каждые 5 минут |

Управление: `~/playerok-bot/run.sh` — запустить, `stop.sh` — остановить,
`tail -f ~/playerok-bot/bot.log` — журнал. Повторный запуск `deploy_home.sh`
служит обновлением и ничего не переспрашивает.

Две оговорки. DDoS-Guard строже к headless-браузеру, чем к обычному, поэтому
обновление куки может срываться чаще — токен в `.env` тогда придётся обновлять
руками. И если библиотек для Chrome в системе не окажется, скрипт скажет об
этом прямо: бот запустится, мониторинг и `/delete` будут работать, а `/create`
не сможет наполнить каталог с нуля.

## Установка по шагам

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

Готовый юнит лежит в репозитории — установить и перезапустить:

```bash
cp systemd/playerok-bot.service /etc/systemd/system/playerok-bot.service
systemctl daemon-reload
systemctl restart playerok-bot
journalctl -u playerok-bot -f
```

Главное в нём: python берётся из `venv` (системный не видит зависимостей
из-за PEP 668), а запуск идёт через `xvfb-run` — иначе Chrome не стартует
и команда `/create` молча не сработает.

Проверить, что реально запущено:

```bash
systemctl cat playerok-bot | grep -E "ExecStart|WorkingDirectory"
systemctl status playerok-bot --no-pager
```

После каждого `git pull` бот нужно перезапускать: старый процесс продолжает
работать с прежним кодом, поэтому новые команды в нём не появляются.

## Проверка мастера без Telegram

```bash
xvfb-run -a ./venv/bin/python selenium_creator.py
```

Скриншоты и HTML каждого шага — в `debug/<дата_время>/`.
