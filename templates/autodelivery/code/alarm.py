"""Сообщать владельцу о поломке — один раз, и о починке тоже.

Боты, которые только пишут в телеграм, при протухших куках крутятся
вхолостую: площадка отказывает, они молча ждут и пробуют снова. Продавец
при этом думает, что всё работает, — пока не заметит, что заказы стоят.

Но и обратная крайность плоха. Сообщение на каждой неудачной попытке
превращает поток в спам за час, а спам выключают вместе с важным.

Поэтому: сказали один раз — и молчим, пока не заработает. Заработало —
сказали один раз и снова молчим.
"""
from __future__ import annotations

# Сколько ждать, прежде чем напомнить о неустранённой поломке. Сутки:
# достаточно редко, чтобы не быть спамом, и достаточно часто, чтобы
# продавец не забыл про стоящую торговлю.
REMIND_AFTER = 24 * 3600


class Alarm:
    """Состояние одной беды: сломано или работает."""

    def __init__(self, link, what: str, remind_after: float = REMIND_AFTER,
                 clock=None):
        self.link = link
        self.what = what
        self.remind_after = remind_after
        self.clock = clock or __import__("time").time
        self.broken_since = 0.0
        self.told_at = 0.0

    def broken(self, why: str, advice: str = "") -> bool:
        """Сообщить о поломке, если ещё не сообщали. → сказали ли сейчас."""
        now = self.clock()

        if self.broken_since and now - self.told_at < self.remind_after:
            return False

        again = bool(self.broken_since)
        self.broken_since = self.broken_since or now
        self.told_at = now

        if self.link:
            hours = int((now - self.broken_since) // 3600)
            self.link.say(
                (f"🔴 {self.what} не работает"
                 + (f" уже {hours} ч" if again and hours else "")
                 + f":\n{why}")
                + (f"\n\n{advice}" if advice else ""))

        return True

    def working(self) -> bool:
        """Сообщить, что снова работает, если было сломано."""
        if not self.broken_since:
            return False

        self.broken_since = 0.0
        self.told_at = 0.0

        if self.link:
            self.link.say(f"🟢 {self.what} снова работает.")

        return True


# Что советовать при отказе во входе. Один текст на всех ботов: разные
# советы об одном и том же путают больше, чем помогают.
COOKIES_ADVICE = ("Куки кабинета протухли. Откройте «👤 Аккаунт» в боте "
                  "создания товаров, выберите кабинет и пришлите свежие. "
                  "Куки берутся из браузера, где вы вошли продавцом.")
