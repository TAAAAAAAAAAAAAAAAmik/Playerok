"""Восстановление проданных объявлений: следит и выставляет заново.

Проданный товар уходит из продажи. Этот скрипт замечает такие и выставляет
их обратно, чтобы торговля не останавливалась.

    python3 restore_bot.py

Запускается рядом с item_bot.py и не мешает ему: он только ПИШЕТ в
телеграм, но не читает. Читать оттуда может лишь один — Telegram отдаёт
каждое сообщение единственному опрашивающему, и второй читатель воровал бы
ответы у первого.

ПРО ДЕНЬГИ. Выставление со статусом приоритета платное. Здесь берётся
только бесплатный: если его нет, товар остаётся невосстановленным, а вам
приходит сообщение. Молча тратить деньги в цикле, который работает сам, —
худшее, что можно придумать.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

import restore                                                # noqa: E402
from alarm import Alarm, COOKIES_ADVICE                       # noqa: E402
from auth import sign_in                                      # noqa: E402
from playerok import is_auth_error                            # noqa: E402
from envfile import load_env_file                             # noqa: E402
import statepath                                  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("restore")

# Как часто смотреть. Проданное не горит: минута роли не играет, а частый
# опрос упирается в темп площадки.
PERIOD = float(os.environ.get("PLAYEROK_RESTORE_PERIOD", 120))

# Сколько проданных товаров разбирать за проход.
BATCH = 12

HANDLED_FILE = statepath.in_project(
    os.environ.get("PLAYEROK_RESTORED", "state/restored.json"))

# Насколько замедляться, когда площадка просит подождать, и до какого
# предела. Продолжать долбить в том же темпе — верный способ получить
# ограничение и на всё остальное, включая вход в кабинет.
SLOWDOWN = 3
MAX_PERIOD = 3600


class TooFast(Exception):
    """Площадка просит сбавить темп. Не отказ, а просьба подождать."""


def sold_items(account, count: int = BATCH) -> list:
    """Недавно проданные товары продавца."""
    try:
        from playerokapi.enums import ItemStatuses
    except ImportError:
        raise SystemExit("Не установлена библиотека playerokapi.")

    page = account.get_my_items(statuses=[ItemStatuses.SOLD], count=count)

    return list(getattr(page, "items", None) or [])


def full_item(account, item):
    """Товар целиком: в списке приходит урезанный.

    Та же болезнь, что с чатом у сделок: список и одиночный запрос отдают
    разные наборы полей, а нам нужны и цена, и признак повторной
    публикации.
    """
    try:
        return account.get_item(id=item.id)
    except Exception as e:                                    # noqa: BLE001
        log.warning("товар %s прочитать не вышло: %s", item.id, e)
        return None


def publish_free(account, item_id: str, price) -> tuple[bool, str]:
    """Выставить бесплатным статусом. → (получилось, причина)."""
    try:
        statuses = account.get_item_priority_statuses(item_id, price)
    except Exception as e:                                    # noqa: BLE001
        return False, f"статусы приоритета не прочитались: {e}"

    free = restore.free_status(statuses)

    if free is None:
        return False, ("бесплатного статуса нет — платный сам покупать не "
                       "буду, выставьте вручную")

    try:
        account.publish_item(item_id, free.id)
    except Exception as e:                                    # noqa: BLE001
        return False, str(e)

    return True, ""


def recreate(account, item) -> tuple[bool, str]:
    """Пересоздать товар копией и убрать старый.

    Путь для товаров, которые площадка не даёт выставить повторно.
    """
    attachments = []

    for attachment in (getattr(item, "attachments", None) or []):
        url = getattr(attachment, "url", "")

        if not url:
            continue

        try:
            attachments.append(account.download_file(url))
        except Exception:                                     # noqa: BLE001
            continue

    if not attachments:
        return False, ("не удалось забрать картинки, а без них товар не "
                       "создать")

    fields = [f for f in (getattr(item, "data_fields", None) or [])
              if str(getattr(getattr(f, "type", None), "name", "")) == "ITEM_DATA"]

    try:
        draft = account.create_item(
            game_category_id=item.category.id,
            obtaining_type_id=item.obtaining_type.id,
            name=item.name,
            price=item.raw_price,
            description=getattr(item, "description", "") or "",
            options=getattr(item, "attributes", None) or {},
            data_fields=fields,
            attachments=attachments,
        )
    except Exception as e:                                    # noqa: BLE001
        return False, f"копию создать не вышло: {e}"

    ok, why = publish_free(account, draft.id, item.raw_price)

    if not ok:
        # Иначе на каждой неудачной попытке копится новый черновик.
        try:
            account.remove_item(draft.id)
        except Exception:                                     # noqa: BLE001
            pass

        return False, why

    try:
        account.remove_item(item.id)
    except Exception:                                         # noqa: BLE001
        return True, "выставлено, но старый товар остался — удалите его сами"

    return True, ""


# Что сказать, когда восстановить не вышло. Вторая попытка не делается
# намеренно, и продавец должен об этом знать: иначе он будет ждать, что
# бот справится сам, а товар так и останется непроданным.
MANUAL = ("Больше пробовать не буду — выставьте через «📄 Черновики» "
          "в боте.")


def handle(account, item, handled):
    """Разобрать один проданный товар. → что сказать владельцу, или None.

    Попытка ровно одна. Повторять неудачу каждые две минуты значит копить
    черновики, слать одно и то же сообщение и в худшем случае плодить
    пересозданные товары.
    """
    if item.id in handled:
        return None

    full = full_item(account, item)

    if full is None:
        # Тоже запоминаем: иначе нечитаемый товар бот дёргал бы вечно, и
        # об этом никто бы не узнал — в журнал такое пишется, но журнал не
        # читают.
        handled.add(item.id)

        return (f"⚠️ Проданный товар {item.id} не прочитался — "
                f"восстановить не смог.\n{MANUAL}")

    name = (full.name or "без названия")[:40]
    way = restore.plan(full)

    if way == restore.RECREATE:
        ok, why = recreate(account, full)
        what = "пересоздан"
    else:
        ok, why = publish_free(account, full.id,
                               getattr(full, "raw_price", None)
                               or getattr(full, "price", 0))
        what = "выставлен заново"

    if not ok and restore.try_later(why):
        # Площадка сказала «попробуйте позже». Это не отказ, а просьба
        # сбавить темп: запомнить такое значило бы бросить товар навсегда
        # из-за минутной заминки. Не запоминаем и вернёмся следующим
        # проходом — но уже реже, см. главный цикл.
        log.warning("«%s»: площадка просит подождать (%s)", name, why)

        raise TooFast(why)

    # Запоминаем в любом случае — и удачу, и неудачу. Попытка одна.
    handled.add(item.id)

    if ok:
        log.info("«%s» %s%s", name, what, f" ({why})" if why else "")

        return f"♻️ «{name}» {what}" + (f"\n{why}" if why else "")

    log.warning("«%s» восстановить не вышло: %s", name, why)

    return f"⚠️ «{name}» восстановить не вышло: {why}\n{MANUAL}"


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    account, _store, link = sign_in()
    handled = restore.Handled(HANDLED_FILE)

    alarm = Alarm(link, "Восстановление проданного")

    log.info("Слежу за проданными. Проверяю раз в %.0f с.", PERIOD)
    log.info("Выставляю только бесплатным статусом: платный сам не куплю.")

    period = PERIOD
    slow = Alarm(link, "Восстановление")

    while True:
        try:
            for item in sold_items(account):
                told = handle(account, item, handled)

                if told and link:
                    link.say(told)

            # Проход дошёл до конца — значит вход в кабинет работает, и
            # темп площадку устраивает.
            alarm.working()
            slow.working()

            if period != PERIOD:
                log.info("темп восстановлен: раз в %.0f с", PERIOD)
                period = PERIOD
        except TooFast as e:
            # Замедляемся и пробуем снова позже. Товар не помечен, так что
            # следующий проход вернётся к нему.
            period = min(period * SLOWDOWN, MAX_PERIOD)
            slow.broken(
                f"площадка просит сбавить темп: {e}",
                f"Подожду и попробую снова — теперь раз в "
                f"{int(period // 60)} мин. Ничего не потеряно.")
            log.warning("сбавляю темп до раза в %.0f с", period)
        except Exception as e:                                # noqa: BLE001
            # Один сбойный проход не должен уносить с собой остальные. Но
            # отказ во входе сам не пройдёт: пока куки не обновят,
            # проданное так и будет лежать невыставленным, а бот молча
            # крутиться. Об этом говорим — один раз.
            if is_auth_error(e):
                alarm.broken("площадка не приняла вход", COOKIES_ADVICE)

            log.error("проход не удался: %s", e)

        time.sleep(period)


if __name__ == "__main__":
    main()
