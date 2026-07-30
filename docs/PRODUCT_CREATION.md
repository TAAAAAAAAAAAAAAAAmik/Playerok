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

| № | Шаг | Что в UI | Что в API |
|---|-----|----------|-----------|
| 1 | Страница создания | открыть мастер | — |
| 2 | Игра / приложение | выбор из списка | `games` → `game.id` |
| 3 | Категория | выбор из списка | `GamePageCategory` → `category.id` |
| 4 | Способ получения | выбор из списка | `gameCategoryObtainingTypes` → `obtainingTypeId` |
| 5 | Опции (атрибуты) | чипы/поля категории | `input.attributes` = `{field: value}` |
| 6 | Название и описание | два поля | `input.name`, `input.description` |
| 7 | Данные товара | поля, которые получит покупатель | `gameCategoryDataFields` (только `type == ITEM_DATA`) → `input.dataFields` |
| 8 | Цена и изображения | цена + загрузка файлов | `input.price`, `$attachments` |
| 9 | Публикация | выбор размещения | `itemPriorityStatuses` → `publishItem` |

Поля с типом `OBTAINING_DATA` заполнять не нужно — их вводит покупатель при заказе.

## Этап 1 — Selenium (сейчас)

Код: `selenium_creator.py`, диалог бота: `product_flow.py`, команда `/create`.

Настройка в `.env`:

```
PLAYEROK_COOKIES=token=eyJ...; __ddg5_=...
SELENIUM_HEADLESS=0
SELENIUM_PROFILE_DIR=.chrome-profile
CHROME_BINARY=
CHROMEDRIVER_PATH=
CHROMEDRIVER_ARGS=
```

Куки берутся из DevTools браузера (Application → Cookies → playerok.com) и должны
быть с того же IP и User-Agent, под которыми вы вошли. Альтернатива — один раз
войти руками в профиле `SELENIUM_PROFILE_DIR`, дальше сессия переиспользуется.

Запуск без Telegram, для проверки сценария:

```bash
python selenium_creator.py
```

Каждый шаг сохраняет скриншот и HTML-дамп в `debug/<дата_время>/` — по ним
подгоняются селекторы, если вёрстка Playerok изменилась. Бот присылает эти же
скриншоты в чат по ходу выполнения.

Селекторы намеренно текстовые (ищем по подписи, placeholder, `aria-label`), потому
что классы у Playerok сгенерированы и меняются при каждой сборке фронта.

### Что проверено

* Шаги 2–9 прогнаны на макете мастера (`headless` Chromium): выбор из списков,
  опции-чипы и поля, название/описание, поля данных, цена, загрузка двух файлов,
  выбор бесплатного размещения, кнопка публикации — всё отрабатывает.
* Шаг 1 (адрес страницы мастера) на живом сайте не проверялся — из контейнера
  playerok.com недоступен. В коде перебираются кандидаты
  `/products/new`, `/items/new`, `/create`, `/sell`, затем фолбэк — кнопка
  «Продать» на главной. Если ни один не подошёл, шаг падает с понятной ошибкой,
  а в `debug/` остаётся дамп страницы — по нему поправим за минуту.

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
