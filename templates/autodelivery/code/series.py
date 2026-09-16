"""Серия объявлений: один товар — и такие же на остальные номиналы.

Продавец выставляет одно и то же семейство: 100, 200, 400, 800, 1000
робуксов. Отличаются они номиналом и ценой, всё остальное — категория,
характеристики, картинки, описание — совпадает до буквы.

Поэтому серия строится ИЗ ГОТОВОГО объявления: берём шаблон, меняем в нём
число и цену, остальное не трогаем.

ПОЧЕМУ ПОКАЗЫВАЕМ ДО СОЗДАНИЯ. Подстановка числа в название — догадка:
«100 Robux за 100 рублей» содержит сотню дважды, и заменить надо не обе.
Догадку показываем списком и создаём только после согласия, потому что
снять с витрины десяток неверных объявлений дороже, чем прочитать десять
строк.
"""
from __future__ import annotations

import re

from catalog import shown_number

# «100 = 70», «100 - 70», «100:70», «100 70» — как придётся. Разделитель
# необязателен: с телефона его набирать неудобно.
ROW = re.compile(r"^\s*(\d[\d\s  ]*(?:[.,]\d+)?)\s*(?:[=:\-—]|\s)\s*"
                 r"(\d[\d\s  ]*)\s*$")


def _number(raw: str):
    clean = str(raw).replace(" ", "").replace(" ", "").replace(
        " ", "").replace(",", ".").strip()

    try:
        return float(clean)
    except ValueError:
        return None


def parse(text: str) -> tuple[list, list]:
    """Строки «номинал = цена» → (пары, непонятые строки).

    Непонятое возвращается, а не пропускается: пропущенная строка — это
    объявление, которого продавец ждал, а его нет. Узнать об этом лучше
    сразу, чем через неделю по отсутствию продаж.
    """
    rows: list = []
    bad: list = []
    seen: set = set()

    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue

        match = ROW.match(raw)

        if match is None:
            bad.append(raw.strip())
            continue

        nominal = _number(match.group(1))
        price = _number(match.group(2))

        if nominal is None or price is None or nominal <= 0 or price <= 0:
            bad.append(raw.strip())
            continue

        if nominal in seen:
            # Два разных задания на один номинал — это два одинаковых
            # объявления на витрине, и продавец не поймёт, какое из них он
            # правил.
            bad.append(f"{raw.strip()} — номинал {_shown(nominal)} "
                       f"уже был")
            continue

        seen.add(nominal)
        rows.append((nominal, int(price)))

    return rows, bad


# Место номинала в названии-образце. Продавец ставит его руками, когда в
# названии числа нет: «{номинал} Robux 🥳ПРОМОКОДОМ🥳».
SLOT = "{номинал}"


def retitle(name: str, old: float, new: float) -> str:
    """Название под новый номинал. Пусто — подставить не смогли.

    Заменяются только ОТДЕЛЬНО стоящие числа: иначе «100» внутри «1000»
    превратило бы «1000 Robux» в «2000 Robux» при переходе со ста на две
    сотни.
    """
    pattern = pattern_from(name, old)

    return render(pattern, new) if pattern else ""


def pattern_from(name: str, old: float) -> str:
    """Название → образец с местом под номинал. Пусто — числа в нём нет."""
    text = str(name or "")
    found = re.compile(rf"(?<!\d){_shown(old)}(?!\d)")

    if not found.search(text):
        return ""

    return found.sub(SLOT, text)


def render(pattern: str, nominal: float) -> str:
    """Образец + номинал → название."""
    return str(pattern or "").replace(SLOT, _shown(nominal))


def suggest_pattern(name: str) -> str:
    """Образец для названия без числа: номинал спереди.

    Предложение, а не решение: продавец поправит, если место другое. Но
    чаще всего номинал и правда стоит первым — «1000 Robux …», — и одно
    нажатие лучше, чем набирать всё название заново с телефона.
    """
    name = " ".join(str(name or "").split())

    return f"{SLOT} {name}".strip()


def usable(pattern: str) -> bool:
    """Годится ли образец. Без места под номинал все названия совпадут."""
    return SLOT in str(pattern or "")


def _shown(value: float) -> str:
    """Число целиком: «1000000», а не «1e+06».

    В названии товара экспонента выглядит поломкой магазина, а в описании
    она ещё и читается обратно как единица — бот купил бы не тот номинал.
    """
    return shown_number(value)


def plan(pattern: str, description: str, old, rows: list) -> tuple[list, list]:
    """Что именно создадим → (задания, отказы).

    `pattern` — название с местом под номинал. `old` — номинал образца или
    None, если его в названии не было: тогда повторять нечего и пропускать
    нечего, все строки новые.

    Задание — словарь с готовыми названием, описанием, номиналом и ценой.
    """
    jobs: list = []
    refused: list = []

    if not usable(pattern):
        return [], [f"в образце названия нет места под номинал ({SLOT})"]

    for nominal, price in rows:
        if old is not None and abs(nominal - float(old)) < 1e-9:
            # Это и есть исходное объявление. Повторять его не надо:
            # получилось бы два одинаковых товара на витрине.
            continue

        jobs.append({
            "nominal": nominal,
            "price": price,
            "name": render(pattern, nominal),
            # В описании число меняем так же, если оно там было. Не нашли —
            # не беда: строку «Номинал: …» бот всё равно поставит свою.
            "description": (retitle(description, float(old), nominal)
                            if old is not None else "") or description,
        })

    return jobs, refused
