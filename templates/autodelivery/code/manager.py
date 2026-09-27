"""Менеджер: ответить покупателю сам и позвать продавца, когда ответить нечем.

Покупатель пишет в чат раньше, чем продавец берёт телефон в руки: «а где
промокод?», «как активировать?», «сколько ждать?». Ответ на три четверти
таких вопросов один и тот же, и стоит он минуты продавца — а покупателю
эта минута кажется часом, потому что деньги он уже отдал.

Менеджер отвечает вместо продавца на то, что повторяется, и зовёт его на
то, что не повторяется. Разделение тут важнее самих ответов: бот, который
берётся отвечать на всё, однажды отвечает бодрым шаблоном на «вы меня
обманули» — и стоит это дороже, чем сотня неотвеченных «спасибо».

Чего он не делает:

* **не отвечает на жалобы.** Жалоба — это всегда продавец. Шаблон в ответ
  на «где мои деньги» превращает недовольство в спор, а спор в возврат;
* **не отвечает дважды подряд.** Если на тот же вопрос ответ уже ушёл
  минуту назад, второй не нужен: покупатель его читает, а не игнорирует;
* **не отвечает в чаты поддержки и площадки.** Там на том конце не
  покупатель, и автоответ выглядит как неисправность;
* **молчит, пока его не включили.** Ответы уходят настоящим людям от
  имени продавца, и включить их продавец должен сам, прочитав.

Здесь только решения и тексты, без сети: что ответить, кого позвать и
когда промолчать. Проверить это можно без единой живой продажи.
"""
from __future__ import annotations

import re
import time

# Пробелы внутри слов — только горизонтальные: перенос строки разделяет
# фразы, и «не пришёл\nкод» не должно склеиваться в одну.
_H = r"[ \t   ]"

# Не чаще раза в это время в один и тот же чат. Покупатель пишет три
# сообщения подряд («привет», «оплатил», «где код») — это один разговор, а
# не три, и три одинаковых ответа на них выглядят поломкой.
GAP = 90.0

# Сколько ответов в сутки на один чат. Дальше молчим: если после пяти
# ответов разговор продолжается, значит отвечать надо не шаблоном.
PER_DAY = 5

# Сколько разговоров помнить. Память живёт в состоянии, и без предела она
# растёт вместе с числом покупателей.
KEEP_TALKS = 400

# Слова, по которым сообщение читается как жалоба. Здесь нарочно широко:
# лишний раз позвать продавца дешевле, чем не позвать.
COMPLAINT = (
    "обман", "кинул", "кидал", "скам", "развод", "мошен", "жалоб",
    "арбитраж", "спор", "поддержк", "верните", "верни деньги",
    "возврат", "не работает", "нерабоч", "не приш", "не получил",
    "не выдал", "жду уже", "обещал", "отмен", "полиц", "суд",
)

# Слова, по которым сообщение читается как «вопрос про выдачу». На них
# менеджер отвечает сам, но только пока код ещё едет: после отказа выдачи
# отвечать «ещё минутку» — это врать.
WAITING = ("где", "когда", "сколько ждать", "долго", "жду")

DEFAULT = {
    # Выключен. Ответы уходят живым людям, и включает их продавец.
    "on": False,
    # Приветствие на покупку: чем отвечаем сразу после оплаты.
    "hello": {"on": False, "text": ""},
    # Ответы по слову в сообщении покупателя: [{"word": ..., "text": ...}].
    "faq": [],
    # Ответы по слову в названии товара — для приветствия и для вопросов,
    # на которые общий ответ не подошёл.
    "rules": [],
    # Звать ли продавца на жалобы и по каким своим словам.
    "complaint": {"on": True, "words": []},
    # Напоминание о заказах, которые давно ждут.
    "remind": {"on": False, "hours": 24},
    # Чаты, в которые менеджер не пишет вовсе.
    "mute": [],
}


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _low(value) -> str:
    """Строка для сравнения: регистр, пробелы и «ё» не важны.

    «Ё» приводится к «е» нарочно: половина покупателей пишет «не пришел»,
    половина «не пришёл», и правило продавца должно ловить обе. Спор из-за
    одной буквы — самая обидная из причин промолчать.
    """
    low = _text(value).lower().replace("ё", "е")

    return re.sub(rf"{_H}+", " ", low).strip()


def settings_of(shared: dict) -> dict:
    """Настройки менеджера с умолчаниями поверх сохранённого.

    Умолчания подкладываются при каждом чтении, а не однажды при записи:
    настройки переживают обновление бота, и новая возможность должна
    появляться у тех, кто настроил всё полгода назад, — а не ронять их
    менеджера отсутствующим ключом.
    """
    found = shared.get("manager")

    if not isinstance(found, dict):
        found = shared["manager"] = {}

    for key, value in DEFAULT.items():
        if key not in found:
            found[key] = _copy(value)
            continue

        if isinstance(value, dict) and isinstance(found[key], dict):
            for inner, default in value.items():
                found[key].setdefault(inner, _copy(default))

    return found


def _copy(value):
    if isinstance(value, dict):
        return dict(value)

    if isinstance(value, list):
        return list(value)

    return value


def talks_of(shared: dict) -> dict:
    """Память разговоров: {чат: {"at": когда, "count": сколько, "day": день}}."""
    found = shared.get("talks")

    if not isinstance(found, dict):
        found = shared["talks"] = {}

    return found


def is_complaint(text: str, extra=()) -> bool:
    """Это жалоба? Тогда отвечает продавец, а не бот."""
    low = _low(text)

    if not low:
        return False

    words = list(COMPLAINT) + [_low(w) for w in extra or () if _low(w)]

    return any(word in low for word in words)


def about_waiting(text: str) -> bool:
    """Покупатель спрашивает «где» и «когда»?"""
    low = _low(text)

    return bool(low) and any(word in low for word in WAITING)


def matching(rules, where: str) -> str:
    """Первое правило, чьё слово встретилось. → ответ или пусто.

    Первое, а не самое точное: порядок правил задаёт продавец, и это его
    способ сказать, что важнее. Переставить их он может сам, а угадать за
    него «точность» — значит однажды ответить не тем.
    """
    low = _low(where)

    if not low:
        return ""

    for rule in rules or []:
        word = _low((rule or {}).get("word"))

        if word and word in low:
            return _text((rule or {}).get("text"))

    return ""


def answer_for(text: str, title: str, conf: dict) -> str:
    """Что ответить покупателю. Пусто — молчим и зовём продавца.

    Сначала по тому, что спросили, и лишь потом по тому, что купили:
    вопрос «как активировать» у всех товаров разный, а «спасибо» —
    одинаковое, и общий ответ на него ничего не портит.
    """
    if is_complaint(text, (conf.get("complaint") or {}).get("words")):
        return ""

    return (matching(conf.get("faq"), text)
            or matching(conf.get("rules"), title))


def hello_for(title: str, conf: dict) -> str:
    """Чем поздороваться после покупки. Пусто — молчим."""
    hello = conf.get("hello") or {}

    if not hello.get("on"):
        return ""

    return matching(conf.get("rules"), title) or _text(hello.get("text"))


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def may_talk(talks: dict, chat_id: str, now: float,
             gap: float = GAP, per_day: int = PER_DAY) -> str:
    """Можно ли писать в этот чат. → причина отказа или пусто.

    Причина возвращается словами, а не «нет»: она уходит в журнал, и по
    ней видно, почему покупателю никто не ответил.
    """
    chat_id = _text(chat_id)

    if not chat_id:
        return "чат не опознан"

    was = talks.get(chat_id) or {}

    if now - float(was.get("at") or 0) < gap:
        return "только что уже отвечали"

    if was.get("day") == _day(now) and int(was.get("count") or 0) >= per_day:
        return f"за сутки уже {per_day} ответов — дальше отвечает продавец"

    return ""


def talked(talks: dict, chat_id: str, now: float, keep: int = KEEP_TALKS) -> None:
    """Запомнить, что в этот чат мы только что написали."""
    chat_id = _text(chat_id)

    if not chat_id:
        return

    was = talks.get(chat_id) or {}
    today = _day(now)
    talks[chat_id] = {
        "at": now,
        "day": today,
        "count": (int(was.get("count") or 0) + 1
                  if was.get("day") == today else 1),
    }

    if len(talks) > keep:
        # Выкидываем самые давние: свежие разговоры и есть те, где
        # ограничение имеет смысл.
        old = sorted(talks.items(), key=lambda pair: pair[1].get("at") or 0)

        for chat, _ in old[:len(talks) - keep]:
            talks.pop(chat, None)


def muted(conf: dict, chat_id: str) -> bool:
    """Этот чат продавец велел не трогать?"""
    chat_id = _text(chat_id)

    return bool(chat_id) and chat_id in [
        _text(one) for one in conf.get("mute") or []]


def alarm_text(who: str, what: str, body: str, link: str = "") -> str:
    """Письмо продавцу о жалобе. Коротко и с самой жалобой внутри."""
    lines = ["⚠️ ЖАЛОБА — отвечать надо вам, я молчу.", ""]

    if what:
        lines.append(f"Товар: «{what}»")

    lines.append(f"Покупатель: {who or 'покупатель'}")
    lines += ["", f"«{body}»"]

    if link:
        lines += ["", link]

    return "\n".join(lines)


def _node(*nodes):
    """Первый непустой объект из цепочки."""
    for node in nodes:
        if node is not None:
            return node

    return None


def chat_of(event) -> str:
    """Номер чата события. Пусто — писать некуда."""
    chat = getattr(event, "chat", None)
    deal = _node(getattr(getattr(event, "message", None), "deal", None),
                 getattr(event, "deal", None))

    return (_text(getattr(chat, "id", ""))
            or _text(getattr(getattr(deal, "chat", None), "id", "")))


def title_of(event) -> str:
    """Название товара, о котором идёт речь. Пусто — не узнали.

    Три места: сделка сообщения, сделка события и сделки самого чата.
    Площадка кладёт товар то туда, то сюда — смотря какое событие, — а для
    правил продавца важно одно: как товар называется.
    """
    message = getattr(event, "message", None)
    deals = [getattr(message, "deal", None), getattr(event, "deal", None)]
    deals += list(getattr(getattr(event, "chat", None), "deals", None) or [])

    for deal in deals:
        item = _node(getattr(deal, "item", None),
                     getattr(message, "item", None))
        name = _text(getattr(item, "name", "")) or _text(
            getattr(item, "title", ""))

        if name:
            return name

    return ""


def who_of(event) -> str:
    """Имя покупателя."""
    user = getattr(_node(getattr(event, "message", None),
                         getattr(event, "deal", None)), "user", None)

    return (_text(getattr(user, "username", ""))
            or _text(getattr(user, "name", "")) or "покупатель")


def body_of(event) -> str:
    """Текст сообщения покупателя."""
    return _text(getattr(getattr(event, "message", None), "text", ""))


def from_us(event, me_id: str) -> bool:
    """Это наше же сообщение?"""
    user = getattr(getattr(event, "message", None), "user", None)
    who = _text(getattr(user, "id", ""))

    return bool(who) and who == _text(me_id)


class Manager:
    """Менеджер на живых событиях: отвечает, зовёт, помнит.

    Сеть сюда приходит двумя вызовами — «напиши в чат» и «скажи
    продавцу», — и обоим разрешено падать: чат может быть закрыт, телеграм
    может молчать. Ни то ни другое не должно ронять слушателя событий:
    уведомления важнее автоответов.
    """

    def __init__(self, store, reply, say, now=time.time):
        self.store = store
        self.reply = reply
        self.say = say
        self.now = now

    def conf(self) -> dict:
        """Настройки СЕЙЧАС: продавец меняет их в боте, не перезапуская нас."""
        return settings_of(self.store.shared())

    def handle(self, event, kind: str, me_id: str = "") -> str:
        """Событие площадки → что сделали. Пусто — ничего не делали.

        Строкой, а не молчанием: она уходит в журнал, и по ней видно,
        почему покупателю ответили или не ответили.
        """
        try:
            return self._handle(event, kind, me_id)
        except Exception as e:                                # noqa: BLE001
            # Менеджер — помощник, а не сторож. Его поломка не должна
            # уносить с собой уведомления, ради которых и работает бот.
            return f"менеджер споткнулся: {e}"

    def _handle(self, event, kind: str, me_id: str) -> str:
        conf = self.conf()

        if not conf.get("on"):
            return ""

        if kind == "buy":
            return self._hello(event, conf)

        if kind != "message":
            return ""

        if from_us(event, me_id):
            return ""

        return self._answer(event, conf)

    def _hello(self, event, conf: dict) -> str:
        text = hello_for(title_of(event), conf)

        if not text:
            return ""

        return self._send(conf, chat_of(event), text, "поздоровался")

    def _answer(self, event, conf: dict) -> str:
        body = body_of(event)

        if not body:
            return ""

        complaint = conf.get("complaint") or {}

        if complaint.get("on", True) and is_complaint(body, complaint.get("words")):
            # Жалобу не отвечаем шаблоном НИКОГДА: недовольный покупатель,
            # которому ответил автомат, идёт не к продавцу, а в спор.
            self._tell(alarm_text(who_of(event), title_of(event), body,
                                  deal_link(event)))

            return "жалоба — позвал продавца"

        text = answer_for(body, title_of(event), conf)

        if not text:
            return ""

        return self._send(conf, chat_of(event), text, "ответил")

    def _send(self, conf: dict, chat_id: str, text: str, what: str) -> str:
        if muted(conf, chat_id):
            return "чат в тишине"

        talks = talks_of(self.store.shared())
        now = self.now()
        why = may_talk(talks, chat_id, now)

        if why:
            return why

        if not self.reply(chat_id, text):
            # Не ушло — и не запоминаем: иначе следующая попытка упрётся в
            # собственное ограничение и покупатель не получит ничего.
            return "написать в чат не вышло"

        talked(talks, chat_id, now)
        self.store.save()

        return what

    def _tell(self, text: str) -> None:
        try:
            self.say(text)
        except Exception:                                     # noqa: BLE001
            pass


DEAL_URL = "https://playerok.com/deal/"


def deal_link(event) -> str:
    """Ссылка на сделку. Пусто — сделки в событии нет."""
    deal = _node(getattr(getattr(event, "message", None), "deal", None),
                 getattr(event, "deal", None))
    deal_id = _text(getattr(deal, "id", ""))

    return f"{DEAL_URL}{deal_id}" if deal_id else ""
