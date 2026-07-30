# Playerok Monitor Bot

Telegram-бот, который следит за аккаунтом Playerok и присылает уведомления
о новых покупках и жалобах.

- Вход в Playerok — по email и одноразовому коду, прямо в чате с ботом (`/login`).
- Опрос GraphQL API каждые 30 секунд (настраивается через `POLL_INTERVAL`).
- Дедупликация через SQLite, так что одно событие не приходит дважды.

## Команды бота

| Команда | Что делает |
|---|---|
| `/start` | Приветствие и текущий статус |
| `/login` | Войти в аккаунт Playerok (email → код из письма) |
| `/status` | Статус мониторинга и счётчики обработанных событий |
| `/check` | Проверить Playerok прямо сейчас, не дожидаясь цикла |
| `/logout` | Выйти из аккаунта и остановить мониторинг |

## Установка на сервер

### Через Termius с телефона

1. **Добавьте хост.** В Termius → `Hosts` → `+` → `New Host`:
   - `Address` — IP сервера
   - `Username` — `root`
   - `Password` — пароль от сервера

   Сохраните и нажмите на хост, чтобы подключиться. При первом входе Termius
   спросит про отпечаток ключа — примите его.

2. **Запустите установщик.** Вставьте в терминал одной строкой, подставив
   токен от [@BotFather](https://t.me/BotFather):

   ```sh
   curl -sSL https://raw.githubusercontent.com/TAAAAAAAAAAAAAAAAmik/Playerok/claude/what-do-we-have-zd93b3/deploy/bootstrap.sh | bash -s -- ВАШ_ТОКЕН_БОТА
   ```

   Команду удобно сохранить в Termius как *Snippet* (`Snippets` → `+`), чтобы
   не набирать её вручную и переиспользовать при переустановке.

3. **Напишите боту.** Скрипт дойдёт до шага «Определяю chat_id» и будет ждать
   до 3 минут — откройте бота в Telegram и отправьте `/start`. Свой `chat_id`
   он подхватит сам.

4. **Войдите в Playerok.** Когда скрипт напишет «бот запущен», отправьте боту
   `/login`, затем email и код из письма.

Установщик берёт на себя всё остальное: пакеты, клонирование репозитория,
venv с зависимостями, `.env` с правами `600` и systemd-юнит с автозапуском.
Повторный запуск безопасен — код обновится, а `.env` и сохранённый токен
Playerok останутся.

### Вручную

```sh
apt-get update && apt-get install -y git python3 python3-venv
git clone -b claude/what-do-we-have-zd93b3 \
    https://github.com/TAAAAAAAAAAAAAAAAmik/Playerok.git /opt/playerok-bot
cd /opt/playerok-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # укажите токен и chat_id
cp deploy/playerok-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now playerok-bot
```

## Управление

```sh
systemctl status playerok-bot     # состояние
systemctl restart playerok-bot    # перезапуск
journalctl -u playerok-bot -f     # живой лог
```

## Переменные окружения

Файл `.env` в каталоге приложения (шаблон — `.env.example`):

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | Токен от @BotFather |
| `TELEGRAM_CHAT_ID` | да | Чат, куда приходят уведомления |
| `POLL_INTERVAL` | нет | Интервал опроса в секундах, по умолчанию `30` |

Токен Playerok здесь не нужен — он сохраняется в `.playerok_token` после
`/login`.

## Что стоит знать

- **Первый запуск шлёт пачку сообщений.** База пустая, поэтому все последние
  сделки и жалобы (до 20 каждых) прилетят как «новые».
- **Схема GraphQL не проверена** против живого API Playerok. Если названия
  запросов в `playerok_client.py` не совпадут с реальными, `/login` упадёт с
  ошибкой от сервера — смотрите `journalctl`.
- **Команды бота не ограничены по пользователю.** Любой, кто найдёт бота,
  может вызвать `/login` или `/logout`. `TELEGRAM_CHAT_ID` используется только
  для отправки уведомлений, но не для проверки входящих команд.
- **Ошибки опроса не видны в чате.** `fetch_orders` и `fetch_complaints`
  логируют исключение и возвращают пустой список, так что истёкший токен
  выглядит как «новых покупок нет».
