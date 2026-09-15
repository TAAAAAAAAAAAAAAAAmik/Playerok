"""Восстановление проданных объявлений: решения без вызовов площадки.

Проданный товар уходит из продажи. Чтобы торговля не останавливалась, его
нужно выставить заново — и делать это самому по десять раз в день никто не
станет.

Площадка различает два случая, и путать их нельзя:

* `may_be_published` истинно — тот же товар выставляется повторно;
* ложно — повторно нельзя, нужен новый товар копией, а старый удалить.

ПРО ДЕНЬГИ. Выставление со статусом приоритета платное. Восстановление
идёт само, без спроса, поэтому берётся ТОЛЬКО бесплатный статус. Если
бесплатного нет — товар не восстанавливается, а владельцу приходит
сообщение. Молча тратить деньги в цикле, который работает сам, — худшее,
что здесь можно придумать.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

# Что делать с проданным товаром.
PUBLISH = "publish"      # выставить его же повторно
RECREATE = "recreate"    # выставить копию, старый удалить
SKIP = "skip"            # не наш случай


def plan(item) -> str:
    """Каким путём восстанавливать этот товар."""
    if item is None:
        return SKIP

    # None означает «площадка не сказала». Считаем, что выставить можно:
    # ошибка в эту сторону даёт понятный отказ, а в другую — лишний
    # пересозданный товар и удалённый старый.
    may = getattr(item, "may_be_published", None)

    return RECREATE if may is False else PUBLISH


def free_status(statuses: list):
    """Бесплатный статус приоритета, или None.

    Платные здесь не годятся: восстановление идёт само, а списание денег
    без спроса — это не то, чего ждут от автоматики.
    """
    import listing

    for status in listing.ordered(statuses or []):
        if listing.is_free(status):
            return status

    return None


class Handled:
    """Память о том, что уже восстановлено.

    Без неё бот, у которого восстановление сорвалось на полпути, ходил бы
    по кругу: снова видел бы проданный товар, снова пересоздавал копию — и
    наплодил бы их столько, сколько раз успел проснуться.
    """

    def __init__(self, path: str, limit: int = 500):
        self.path = path
        self.limit = limit
        self.ids = self._read()

    def _read(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as f:
                return [str(x) for x in (json.load(f).get("ids") or [])]
        except (OSError, ValueError):
            return []

    def __contains__(self, item_id) -> bool:
        return str(item_id) in self.ids

    def add(self, item_id) -> None:
        """Запомнить. Старое вытесняется: список не должен расти вечно."""
        item_id = str(item_id)

        if item_id in self.ids:
            return

        self.ids.append(item_id)
        self.ids = self.ids[-self.limit:]
        self._write()

    def _write(self) -> None:
        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"ids": self.ids, "at": time.time()}, f)

            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
