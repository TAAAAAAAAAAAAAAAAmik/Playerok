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
from auth import sign_in                                      # noqa: E402
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
    except ImportError:
        raise SystemExit("Не установлена библиотека playerokapi.")

    me = str(getattr(account, "id", "") or "")
    show = wanted()

    log.info("Слушаю площадку. Показываю: %s", ", ".join(show))
    link.say("👂 Слежу за площадкой: покупки, сообщения, отзывы, проблемы.")

    pause = PAUSE
    seen = notices.Seen(SEEN_FILE)

    # Слушатель создаётся ОДИН раз и переживает обрывы. Внутри у него своя
    # память о показанном, и создавая его заново на каждом переподключении,
    # мы стирали её — после любого сбоя сети недавние события приходили
    # повторно, и выглядело это как бот, который дублирует сообщения.
    listener = EventListener(account)

    while True:
        try:
            for event in listener.listen():
                pause = PAUSE          # дожили до события — значит связь есть

                if not seen.fresh(event):
                    continue

                text = notices.describe(event, me, show)

                if text:
                    log.info("%s", text.splitlines()[0])
                    link.say(text)
        except KeyboardInterrupt:
            raise
        except Exception as e:                                # noqa: BLE001
            # Обрыв слушателя — обычное дело: сеть, перезапуск площадки.
            # Молча поднимаемся, но владельцу об этом не пишем: поток
            # уведомлений не должен превращаться в отчёт о своей сети.
            log.error("слушатель оборвался: %s, продолжу через %ss", e, pause)
            time.sleep(pause)
            pause = min(pause * 2, MAX_PAUSE)


if __name__ == "__main__":
    main()
