"""Тесты копии живого объявления. Сети не требуют.

Шаблон запоминается при создании товара — а то, что заведено раньше бота
или в кабинете на сайте, шаблона не имеет. Здесь проверяется разбор такого
объявления в черновик: сети и телеграма нет, чтобы проверять это тестами,
а не живыми товарами.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import copyitem                                                 # noqa: E402
import wizard                                                   # noqa: E402


class Node:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def item(**changes):
    base = dict(
        id="i1", name="1000 Robux Global", price=700,
        description="Регион кода: GL\nНоминал: 1000\n\nКоды сразу",
        game=Node(id="g1", name="Roblox"),
        category=Node(id="c1", name="Игровая валюта"),
        obtaining_type=Node(id="o1", name="Без входа"),
        attributes={"a1": "Global", "a2": "Robux"},
        data_fields=[Node(id="f1", label="Комментарий", required=False,
                          value="без входа")],
        attachments=[Node(url="https://cdn/1.jpg"),
                     Node(url="https://cdn/2.jpg")],
    )
    base.update(changes)

    return Node(**base)


class PlanTest(unittest.TestCase):
    def test_everything_needed_is_taken(self):
        plan, gaps = copyitem.plan(item())

        self.assertEqual(gaps, [])
        self.assertEqual(plan["name"], "1000 Robux Global")
        self.assertEqual(plan["price"], 700)
        self.assertEqual(plan["category"], {"id": "c1",
                                            "name": "Игровая валюта"})
        self.assertEqual(plan["obtaining"]["id"], "o1")
        self.assertEqual(len(plan["photos"]), 2)

    def test_the_region_and_nominal_are_read_out_of_the_description(self):
        """Их бот поставит заново — но знать их надо заранее, иначе копия
        уйдёт без региона и выдача по ней встанет."""
        plan, _ = copyitem.plan(item())

        self.assertEqual(plan["region"], "GL")
        self.assertEqual(plan["nominal"], 1000)

    def test_the_nominal_comes_from_the_name_when_the_description_is_silent(self):
        plan, _ = copyitem.plan(item(description="просто текст"))

        self.assertEqual(plan["nominal"], 1000)

    def test_attributes_become_a_list_the_draft_understands(self):
        """Площадка отдаёт их словарём «поле → значение» — тем же самым,
        который принимает обратно."""
        plan, _ = copyitem.plan(item())
        draft = wizard.Draft()
        draft.options = plan["options"]

        self.assertEqual(draft.attributes(), {"a1": "Global", "a2": "Robux"})

    def test_a_filled_attribute_is_not_asked_about_again(self):
        plan, _ = copyitem.plan(item())
        draft = wizard.Draft()
        draft.game = plan["game"]
        draft.category = plan["category"]
        draft.obtaining = plan["obtaining"]
        draft.options = plan["options"]

        self.assertFalse(draft.step.startswith(wizard.OPTION))

    def test_data_fields_keep_their_answers(self):
        plan, _ = copyitem.plan(item())

        self.assertEqual(plan["fields"][0]["label"], "Комментарий")
        self.assertEqual(plan["fields"][0]["value"], "без входа")

    def test_an_empty_attribute_is_dropped(self):
        plan, _ = copyitem.plan(item(attributes={"a1": "Global", "a2": ""}))

        self.assertEqual([o["field"] for o in plan["options"]], ["a1"])


class GapsTest(unittest.TestCase):
    """Подставить недостающее нельзя: товар не там, где хотели, дороже
    несозданного."""

    def test_no_category_is_a_gap(self):
        _, gaps = copyitem.plan(item(category=None))

        self.assertIn("категория", gaps)

    def test_no_pictures_is_a_gap(self):
        """Без них площадка товар не примет."""
        _, gaps = copyitem.plan(item(attachments=[]))

        self.assertIn("картинки", gaps)

    def test_no_price_is_a_gap(self):
        _, gaps = copyitem.plan(item(price=0))

        self.assertIn("цена", gaps)

    def test_an_entity_without_an_id_is_not_taken(self):
        _, gaps = copyitem.plan(item(obtaining_type=Node(name="Без входа")))

        self.assertIn("способ получения", gaps)


class DescriptionTest(unittest.TestCase):
    """Две строки региона в одном описании — тихая денежная ошибка:
    движок прочитает не ту и купит не тот номинал."""

    def test_the_copied_description_gets_exactly_one_region_line(self):
        plan, _ = copyitem.plan(item())
        draft = wizard.Draft()
        draft.region = plan["region"]
        draft.nominal = plan["nominal"]
        body, _ = wizard.accept_description(
            "Регион кода: GL\nНоминал: 1000\n\nКоды сразу")
        draft.description = body
        text = wizard.description_for(draft)

        self.assertEqual(text.count("Регион кода:"), 1)
        self.assertEqual(text.count("Номинал:"), 1)
        self.assertIn("Коды сразу", text)


if __name__ == "__main__":
    unittest.main()
