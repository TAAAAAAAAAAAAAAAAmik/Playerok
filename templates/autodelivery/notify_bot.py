"""Уведомления с площадки в телеграм: покупки, сообщения, проблемы, отзывы.

Слушает события площадки и пересказывает их владельцу. Событиями, а не
опросом: покупатель не должен ждать минуту, пока бот заметит его вопрос.

    python3 notify_bot.py

Запускается рядом с остальными и не мешает им: только ПИШЕТ в телеграм, но
не читает. Читать может лишь один — Telegram отдаёт каждое сообщение
единственному опрашивающему.

Что показывать, можно сузить:

    PLAYEROK_NOTICES=buy,problem python3 notify_bot.py

Виды: buy — покупка, message — сообщение в чате, confirmed — заказ
подтверждён, problem — проблема, resolved — проблема решена, review —
отзыв, refund — возврат, sent — товар отправлен.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "code"))

import notices                                                # noqa: E402
from alarm import Alarm, COOKIES_ADVICE                       # noqa: E402
from auth import sign_in                                      # noqa: E402
from playerok import is_auth_error                            # noqa: E402
from envfile import load_env_file                             # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notify")

# Сколько ждать перед новой попыткой, если слушатель оборвался. Растёт,
# чтобы при долгой недоступности площадки не стучаться каждую секунду.
PAUSE = 5
MAX_PAUSE = 120

SEEN_FILE = os.environ.get("PLAYEROK_SEEN", "state/seen.json")


def wanted() -> tuple:
    """Какие уведомления показывать."""
    raw = os.environ.get("PLAYEROK_NOTICES", "").strip()

    if not raw:
        return notices.DEFAULT

    return tuple(part.strip() for part in raw.split(",") if part.strip())


def main() -> None:
    load_env_file(os.path.join(os.path.dirname(__file__), ".env"))
    account, _store, link = sign_in()

    if link is None:
        raise SystemExit(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID — "
            "уведомлять некого.")

    try:
        from playerokapi.listener.listener import EventListener
    except ImportError as e:
        # Почти наверняка не вся библиотека, а ровно её слушатель. В
        # setup.py стоит packages=find_packages(), а у папки
        # playerokapi/listener нет __init__.py — и она не попадает в
        # установку. Соседние enums.py и types.py ставятся, потому что они
        # файлы, а не папки.
        #
        # Прежнее «не установлена библиотека playerokapi» уводило в
        # сторону: библиотека стоит, на ней работает всё остальное.
        here = os.path.dirname(os.path.abspath(__file__))
        raise SystemExit(
            f"Слушатель событий не импортируется: {e}\n\n"
            f"Это известная недоделка библиотеки: папку "
            f"playerokapi/listener она не устанавливает. Лечится одной "
            f"командой:\n"
            f"  python3 {here}/fix_listener.py")

    me = str(getattr(account, "id", "") or "")
    show = wanted()

    log.info("Слушаю площадку. Показываю: %s", ", ".join(show))
    link.say("👂 Слежу за площадкой: покупки, сообщения, отзывы, проблемы.")

    seen = notices.Seen(SEEN_FILE)

    # Слушатель создаётся ОДИН раз и переживает обрывы. Внутри у него своя
    # память о показанном, и создавая его заново на каждом переподключении,
    # мы стирали её — после любого сбоя сети недавние события приходили
    # повторно, и выглядело это как бот, который дублирует сообщения.
    # Номера служебных чатов площадка отдаёт вместе с кабинетом. По ним
    # письмо поддержки узнаётся надёжнее, чем по типу чата: тип у разных
    # версий библиотеки называется по-разному, а номер один и тот же.
    pump(EventListener(account), link, seen, me, show,
         alarm=Alarm(link, "Уведомления"),
         support_id=str(getattr(account, "support_chat_id", "") or ""),
         system_id=str(getattr(account, "system_chat_id", "") or ""))


def pump(listener, link, seen, me: str, show, alarm=None,
         sleeper=time.sleep, support_id: str = "",
         system_id: str = "") -> None:
    """Вечный цикл: событие площадки → сообщение владельцу.

    Вынесен из `main` не ради красоты. Здесь живёт всё, что может пойти не
    так — повторы, обрывы, несостоявшаяся отправка, — и ровно это до сих
    пор не проверялось ничем. Отсюда и месяцы без уведомлений.
    """
    pause = PAUSE

    while True:
        try:
            for event in listener.listen():
                # Дожили до события — значит связь есть.
                pause = PAUSE

                if alarm is not None:
                    alarm.working()

                if not seen.is_new(event):
                    continue

                text = notices.describe(event, me, show,
                                        support_id, system_id)

                if not text:
                    # Разобрали и решили молчать — но запомнить надо, иначе
                    # будем разбирать это же при каждом переподключении.
                    seen.remember(event)
                    continue

                log.info("%s", text.splitlines()[0])

                if link.say(text):
                    seen.remember(event)
                else:
                    # Не ушло — не помечаем. Площадка присылает недавние
                    # события заново при переподключении, и там мы
                    # попробуем ещё раз. Молча потерять покупку нельзя.
                    log.error("не отправилось в телеграм, попробую позже: %s",
                              text.splitlines()[0])
        except KeyboardInterrupt:
            raise
        except Exception as e:                                # noqa: BLE001
            # Обрыв слушателя — обычное дело: сеть, перезапуск площадки.
            # Про такое молчим: поток уведомлений не должен превращаться в
            # отчёт о своей сети.
            #
            # А вот отказ во входе молчанием не отделаешься: сам он не
            # пройдёт, и пока продавец не пришлёт куки, уведомлений не
            # будет вовсе. Об этом говорим — один раз.
            if alarm is not None and is_auth_error(e):
                alarm.broken("площадка не приняла вход", COOKIES_ADVICE)

            log.error("слушатель оборвался: %s, продолжу через %ss", e, pause)
            sleeper(pause)
            pause = min(pause * 2, MAX_PAUSE)


if __name__ == "__main__":
    main()
