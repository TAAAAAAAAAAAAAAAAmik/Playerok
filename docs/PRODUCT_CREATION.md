# Создание товара на Playerok

## Что выяснено про API

Разбор рабочей неофициальной библиотеки
[alleexxeeyy/PlayerokAPI](https://github.com/alleexxeeyy/PlayerokAPI).

Ключевое:

* Эндпоинт один — `POST/GET https://playerok.com/graphql`.
* **Авторизация — по кукам, а не по заголовку `Authorization`**: `token` (JWT) плюс
  кука DDoS-Guard `__ddg5_`/`__ddg3`. Кука умирает при смене IP, User-Agent или
  TLS-отпечатка. Из-за этого «голые» запросы часто ловят проверку бота, а браузер
  проходит её сам — поэтому первый этап делаем на Selenium.
* Чтение справочников идёт **persisted-запросами**: GET с `operationName`,
  `variables` и `extensions.persistedQuery.sha256Hash`. Хэши меняются при
  обновлении фронта; актуальные лежат в `playerok_client.PERSISTED_QUERIES`.
* Запись (`createItem`, `publishItem`) — обычные мутации с полным текстом запроса.
* `createItem` принимает картинки отдельным аргументом `$attachments: [Upload!]!`
  по multipart-спеке GraphQL (`operations` + `map` + пронумерованные файлы).
* Созданный товар попадает **в черновик**. На продажу его выставляет `publishItem`
  с `priorityStatuses: [id]`; бесплатному размещению соответствует статус с
  `price == 0` из `itemPriorityStatuses`.

## 9 шагов мастера

Мастер — **модальное окно**, а не отдельная страница: открывается кнопкой
«Выставить товар» в профиле или «Продать» в нижней навигации, адрес при этом не
меняется. Заголовки шагов видны в шапке окна и служат маркерами в коде
(`STEP_TITLES`).

| № | Заголовок в UI | Что делаем | Что в API |
|---|----------------|------------|-----------|
| 1 | Выберите раздел товаров | поиск «Поиск игр и приложений», клик по игре — сразу переводит дальше | `games` → `game.id` |
| 2 | Выберите категорию | клик по категории, «Далее» | `GamePageCategory` → `category.id` |
| 3 | Способ передачи | клик по варианту, «Далее» | `gameCategoryObtainingTypes` → `obtainingTypeId` |
| 4 | Характеристики | чипы категории, «Далее» | `input.attributes` |
| 5 | Фото 1/10 | загрузка файлов, «Далее» | `$attachments` |
| 6 | О товаре | «Название товара», «Описание товара», «Далее» | `input.name`, `input.description` |
| 7 | Цена | «Цена товара» (рядом «Доход»), «Далее» | `input.price` |
| 8 | Данные товара | «Комментарий» и др., кнопка **«Сохранить»** | `input.dataFields` (только `ITEM_DATA`) |
| 9 | Выберите сервис | Премиум / Обычный (бесплатно) / Выставить позже | `itemPriorityStatuses` → `publishItem` |

⚠️ На девятом шаге **по умолчанию отмечен платный «Премиум»** (19 ₽ на момент
разбора). Код сначала кликает нужный вариант, затем проверяет текст кнопки: если
в нём остались «₽» или «Премиум», а размещение выбрано бесплатное — публикация
прерывается с ошибкой, чтобы не списать деньги. Вариант `later`
(«Выставить позже») оставляет товар черновиком — им удобно проверять сценарий.

Поля с типом `OBTAINING_DATA` заполнять не нужно — их вводит покупатель при заказе.

## Этап 1 — Selenium (сейчас)

Код: `selenium_creator.py`, диалог бота: `product_flow.py`, команда `/create`.

Настройка в `.env`:

```
PLAYEROK_COOKIES=token=eyJ...
SELENIUM_HEADLESS=0
SELENIUM_PROFILE_DIR=.chrome-profile
CHROME_BINARY=
CHROMEDRIVER_PATH=
CHROMEDRIVER_ARGS=
SELENIUM_MOBILE=0
```

`SELENIUM_MOBILE=1` переводит браузер в мобильный режим (Pixel 7, 412×915,
мобильный User-Agent и тач) — у Playerok мобильная вёрстка отличается от
десктопной, и селекторы нужно подгонять под ту, что видит пользователь.

Токен берётся из DevTools браузера (Application → Cookies → playerok.com → `token`).
Куку DDoS-Guard копировать не нужно — она привязана к IP, браузер получит свою.
Альтернатива — один раз войти руками в профиле `SELENIUM_PROFILE_DIR`, дальше
сессия переиспользуется.

Запуск на сервере (venv, Chrome, Xvfb, systemd) — см. [DEPLOY.md](DEPLOY.md).

Запуск без Telegram, для проверки сценария:

```bash
bash run_check.sh <token>      # запишет токен в .env, прогонит мастер,
                               # соберёт скриншоты и лог в tar.gz
```

Адрес страницы мастера не зашит: шаг 1 читает build-манифест Next.js
(`window.__BUILD_MANIFEST.sortedPages`) и берёт оттуда маршруты, похожие на
создание товара. Полный список маршрутов сайта сохраняется в
`debug/<дата_время>/routes.txt`.

Каждый шаг сохраняет скриншот и HTML-дамп в `debug/<дата_время>/` — по ним
подгоняются селекторы, если вёрстка Playerok изменилась. Бот присылает эти же
скриншоты в чат по ходу выполнения.

Селекторы намеренно текстовые (ищем по подписи, placeholder, `aria-label`), потому
что классы у Playerok сгенерированы и меняются при каждой сборке фронта.

### Что проверено

* Все 9 шагов прогнаны на макете, повторяющем реальный мастер (мобильная
  вёрстка, headless Chromium): открытие модалки из профиля, поиск и выбор игры,
  категория, способ передачи, чипы характеристик, загрузка файла,
  название/описание, цена, комментарий и «Сохранить», выбор размещения.
* Проверены оба безопасных варианта девятого шага: `free` жмёт «Выставить
  бесплатно», `later` — «Сохранить» (черновик).
* Проверена защита от платной кнопки: если вариант размещения не переключился,
  шаг падает и ничего не нажимает.
* На живом сайте мастер не прогонялся — из контейнера разработки playerok.com
  недоступен (политика сети отвечает 403 на CONNECT). Структура шагов снята со
  скриншотов реального мастера.

## Этап 2 — перевод на запросы

Функции уже написаны в `playerok_client.py` (`search_games`,
`fetch_obtaining_types`, `fetch_data_fields`, `create_item`,
`fetch_priority_statuses`, `publish_item`), но к боту не подключены. Порядок
вызовов повторяет таблицу выше:

```python
games = await search_games("Telegram")
types_ = await fetch_obtaining_types(category_id)
fields = await fetch_data_fields(category_id, obtaining_type_id)
item = await create_item(
    game_category_id=category_id,
    obtaining_type_id=obtaining_type_id,
    name="...", price=90, description="...",
    attributes={"color": "heart"},
    data_fields=[{"fieldId": f["id"], "value": "..."}],
    attachments=[("banner.png", data, "image/png")],
)
statuses = await fetch_priority_statuses(item["id"], item["price"])
free = next(s for s in statuses if s["price"] == 0)
await publish_item(item["id"], free["id"])
```

Переключаем после того, как браузерный сценарий подтверждён на живом аккаунте.
