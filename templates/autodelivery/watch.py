"""Режим наблюдения: смотрим на живые заказы и НИЧЕГО не покупаем.

Зачем он нужен. Единственный пункт, ошибка в котором отдаёт коды бесплатно,
— какие статусы площадки означают «оплачено». Ответ у нас взят из исходников
неофициальной библиотеки, а не с живого заказа, и проверять его надо до
первой покупки, а не после.

Этот скрипт даёт проверить его безопасно: он ходит в настоящий кабинет
настоящими ключами, показывает реальные сделки с их статусами и пишет, что
СДЕЛАЛ БЫ движок с каждой. Поставщика он не трогает вовсе — ни покупки, ни
чтения заказов по ссылке (у последнего есть побочное действие: поставщик
помечает коды полученными).

    PLAYEROK_COOKIES=... PLAYEROK_UA=... python3 watch.py

Когда увиденное совпадёт с ожидаемым — можно запускать боевой цикл.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from catalog import (nominal_from_title, pick_card,           # noqa: E402
                     region_from_description)
from playerok import PAID_STATUSES, PlayerokMarketplace       # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("watch")

# Товары берём из общей настройки, чтобы наблюдение и боевой цикл смотрели
# на один и тот же список, а не на два разных.
from cards import CARDS                                        # noqa: E402


def account_from_env():
    """Аккаунт площадки из переменных окружения.

    Куки и user-agent живут только в окружении: положить их в код значило бы
    выложить доступ к кабинету продавца в репозиторий.
    """
    cookies = os.environ.get("PLAYEROK_COOKIES", "").strip()
    user_agent = os.environ.get("PLAYEROK_UA", "").strip()

    if not cookies:
        raise SystemExit(
            "Нет PLAYEROK_COOKIES. Это строка куки целиком из браузера, где "
            "вы вошли продавцом — со всем содержимым, включая token и куку "
            "защиты от DDoS-Guard. Библиотека разберёт её сама.")

    if not user_agent:
        raise SystemExit(
            "Нет PLAYEROK_UA. Нужен тот же user-agent, что у браузера, из "
            "которого взяты куки: иначе площадка сочтёт вход чужим.")

    try:
        from playerokapi.account import Account
    except ImportError:
        raise SystemExit(
            "Не установлена библиотека playerokapi. "
            "Поставьте: pip install -r requirements.txt")

    # Передаём строку куки целиком: библиотека разберёт её на пары сама.
    # Отдельный параметр token ждёт только JWT, и подсунуть ему всю строку
    # значит молча остаться неавторизованным.
    return Account(cookies=cookies, user_agent=user_agent).get()


async def main() -> None:
    market = PlayerokMarketplace(account_from_env())

    log.info("Наблюдение. Покупок не будет — поставщик не вызывается вовсе.")
    log.info("Оплаченными считаем статусы: %s", ", ".join(sorted(PAID_STATUSES)))

    try:
        orders = await market.paid_orders()
    except Exception as e:                                     # noqa: BLE001
        log.error("Не удалось прочитать заказы: %s", e)
        raise SystemExit(
            "Проверьте куки и user-agent. Если площадка просит сбавить темп "
            "— подождите и повторите.")

    if not orders:
        log.info("Оплаченных сделок сейчас нет.")
        log.info(
            "Это ожидаемо, если у вас нет неотданных заказов. Чтобы "
            "проверить по-настоящему, оформите себе тестовый заказ на самом "
            "дешёвом номинале и запустите снова.")
        return

    log.info("Оплаченных сделок: %d", len(orders))

    for order in orders:
        log.info("─" * 60)
        log.info("заказ %s — «%s»", order.id, order.title or "без названия")
        log.info("  статус площадки: %s", order.status)
        log.info("  чат: %s", order.chat_id or "НЕТ — код отправить некуда")
        log.info("  покупатель: %s", order.buyer or "не указан")

        if not order.chat_id:
            log.warning("  ⚠ без чата выдача невозможна")

        card = pick_card(CARDS, order.title, lambda slug: {})

        if card is None:
            log.info("  наш товар: не наш — движок пропустит")
            continue

        log.info("  наш товар: %s", card.title)

        region = region_from_description(order.description)
        value = nominal_from_title(order.title)

        log.info("  регион из описания: %s", region or "НЕ ПРОЧИТАН")
        log.info("  номинал из названия: %s", value if value else "НЕ ПРОЧИТАН")

        if not region:
            log.warning(
                "  ⚠ регион не прочитан — движок остановится и попросит "
                "дописать в описание строку вида «Регион кода: US»")

        if not value:
            log.warning(
                "  ⚠ номинал не прочитан из названия — движок остановится")

        if region and value:
            log.info("  → движок купил бы %s %s (%s) и отправил код в чат %s",
                     value, card.measure, region, order.chat_id)

    log.info("─" * 60)
    log.info("Сверьте статусы выше с тем, что показывает кабинет.")
    log.info("Если в списке оказалась сделка, по которой товар уже отдан или "
             "деньги возвращены — НЕ запускайте боевой цикл: список "
             "оплаченных статусов в code/playerok.py надо исправить.")


if __name__ == "__main__":
    asyncio.run(main())
