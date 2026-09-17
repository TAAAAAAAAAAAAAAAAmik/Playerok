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

    python3 watch.py        # ключи берутся из .env рядом

Куки можно и не задавать: если настроены TELEGRAM_BOT_TOKEN и
TELEGRAM_OWNER_ID, бот попросит их в телеграме — и там же попросит новые,
когда прежние истекут.

Когда увиденное совпадёт с ожидаемым — можно запускать боевой цикл.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

from auth import open_account, sign_in, user_agent_from_env  # noqa: E402
from envfile import load_env_file                             # noqa: E402
from catalog import (nominal_for, pick_card, shown_number,    # noqa: E402
                     region_from_description)
from owner import renew_cookies                              # noqa: E402
from playerok import (PAID_STATUSES, PlayerokMarketplace,     # noqa: E402
                      is_auth_error)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("watch")

# Товары берём из общей настройки, чтобы наблюдение и боевой цикл смотрели
# на один и тот же список, а не на два разных.
from cards import CARDS                                        # noqa: E402


async def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    account, store, link = sign_in()
    market = PlayerokMarketplace(account)

    log.info("Наблюдение. Покупок не будет — поставщик не вызывается вовсе.")
    log.info("Оплаченными считаем статусы: %s", ", ".join(sorted(PAID_STATUSES)))

    try:
        orders = await market.paid_orders()
    except Exception as e:                                     # noqa: BLE001
        log.error("Не удалось прочитать заказы: %s", e)

        if not (is_auth_error(e) and link):
            raise SystemExit(
                "Проверьте куки и user-agent. Если площадка просит сбавить "
                "темп — подождите и повторите.")

        # Куки протухли, а спросить есть у кого — спрашиваем и пробуем ещё
        # раз. Один раз: если и новые не подошли, дело не в них.
        log.info("Куки не приняты. Спрашиваю новые в телеграме.")
        cookies = renew_cookies(
            store, link,
            "Площадка не приняла вход: куки истекли.")

        if not cookies:
            raise SystemExit("Новых куки не пришло — останавливаюсь.")

        market = PlayerokMarketplace(
            open_account(cookies, user_agent_from_env()))
        orders = await market.paid_orders()

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

        # Считаем товар включённым: наблюдение должно показать, признает
        # ли движок заказ своим. С пустой настройкой подбор отвергает всё,
        # и «не наш» говорило бы лишь о самом наблюдении.
        card = pick_card(CARDS, order.title, lambda slug: {"enabled": True})

        if card is None:
            log.info("  наш товар: не наш — движок пропустит")
            continue

        log.info("  наш товар: %s", card.title)

        region = region_from_description(order.description)
        value, why_value = nominal_for(order.title, order.description,
                                       getattr(order, "amount", None))

        log.info("  регион из описания: %s", region or "НЕ ПРОЧИТАН")
        log.info("  номинал: %s", shown_number(value) if value else "НЕ ПРОЧИТАН")

        if not region:
            log.warning(
                "  ⚠ регион не прочитан — движок остановится и попросит "
                "дописать в описание строку вида «Регион кода: US»")

        if not value:
            log.warning("  ⚠ %s — движок остановится", why_value)

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
