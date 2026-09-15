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
            bad.append(f"{raw.strip()} — номинал {nominal:g} уже был")
            continue

        seen.add(nominal)
        rows.append((nominal, int(price)))

    return rows, bad


def retitle(name: str, old: float, new: float) -> str:
    """Название под новый номинал. Пусто — подставить не смогли.

    Заменяются только ОТДЕЛЬНО стоящие числа: иначе «100» внутри «1000»
    превратило бы «1000 Robux» в «2000 Robux» при переходе со ста на две
    сотни.
    """
    text = str(name or "")
    pattern = re.compile(rf"(?<!\d){_shown(old)}(?!\d)")

    if not pattern.search(text):
        return ""

    return pattern.sub(_shown(new), text)


def _shown(value: float) -> str:
    return f"{value:g}"


def plan(name: str, description: str, old: float,
         rows: list) -> tuple[list, list]:
    """Что именно создадим → (задания, отказы).

    Задание — словарь с готовыми названием, описанием, номиналом и ценой.
    Отказ — номинал, для которого название собрать не вышло: создавать
    объявление с чужим названием нельзя, а молча пропускать — тем более.
    """
    jobs: list = []
    refused: list = []

    for nominal, price in rows:
        if abs(nominal - old) < 1e-9:
            # Это и есть исходное объявление. Повторять его не надо:
            # получилось бы два одинаковых товара на витрине.
            continue

        title = retitle(name, old, nominal)

        if not title:
            refused.append(
                f"{nominal:g} — в названии «{name}» нет числа {old:g}, "
                f"подставить новое некуда")
            continue

        jobs.append({
            "nominal": nominal,
            "price": price,
            "name": title,
            # В описании то же число меняем так же. Не нашли — не беда:
            # строку «Номинал: …» бот всё равно поставит свою.
            "description": retitle(description, old, nominal) or description,
        })

    return jobs, refused
