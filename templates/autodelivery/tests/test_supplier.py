"""Тесты клиента поставщика: конверт, покупка, коды. Сети не требуют.

Тут проходит вся дорога денег, и до сих пор её не проверял никто: тесты
движка подставляли упрощённого поставщика, а настоящий клиент оставался
без единой проверки. Ровно на таком шве уже пряталась беда с
уведомлениями.

Поддельный HTTP отвечает так, как отвечает AppRoute на самом деле:
двухсотый ответ почти всегда, а получилось ли — решает `statusCode` внутри
тела. Коды приходят замазанными, пока не спросишь `unhide=true`.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

import supplier as sup                                          # noqa: E402


class Response:
    """Ответ поставщика в том виде, в каком его видит requests."""

    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)

        return self._body


def envelope(code=0, data=None, message="", trace="tr-1", errors=None,
             error_code=""):
    """Конверт поставщика: HTTP тут ни при чём, решает statusCode."""
    body = {"statusCode": code, "statusMessage": message, "traceId": trace}

    if data is not None:
        body["data"] = data

    if errors:
        body["errors"] = errors

    if error_code:
        body["errorCode"] = error_code

    return body


class FakeHttp:
    """Подставная сессия requests. Помнит, о чём её просили."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.proxies = {}

    def request(self, method, url, headers=None, params=None, json=None,
                timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "params": params or {}, "body": json or {}})

        if not self.answers:
            raise AssertionError(f"лишний запрос: {method} {url}")

        answer = self.answers.pop(0)

        return answer(self.calls[-1]) if callable(answer) else answer


def client(answers, **kw):
    got = sup.ApprouteSupplier(api_key="ключ", **kw)
    got.session = FakeHttp(answers)

    return got


# ---------------------------------------------------------------------------
# Конверт
# ---------------------------------------------------------------------------

class EnvelopeTest(unittest.TestCase):
    """HTTP 200 ничего не значит: получилось ли, решает statusCode."""

    def test_ok_returns_the_data(self):
        got = client([Response(envelope(0, {"а": 1}))]).item("s", "i")

        self.assertEqual(got, {"а": 1})

    def test_accepted_is_a_success_even_without_data(self):
        """ACCEPTED значит «заказ принят, код будет позже». Считать его
        отказом — значит купить второй раз то, что уже куплено."""
        got = client([Response(envelope(1))]).place("d1", "ref-1")

        self.assertTrue(got["ok"])

    def test_idempotency_replay_is_a_success(self):
        """«Такой заказ уже был, отдаю прежний результат» — это ровно то,
        ради чего ссылка и заводилась. Прочесть его отказом значит
        потерять уже купленный код."""
        data = {"status": "SUCCESS", "items": [{"code": "AAAA-BBBB"}]}
        got = client([Response(envelope(2, data))]).place("d1", "ref-1")

        self.assertTrue(got["ok"])
        self.assertEqual(got["codes"], ["AAAA-BBBB"])

    def test_forbidden_names_the_missing_right(self):
        """Без orders:write ключ покупает, но кода не отдаёт."""
        with self.assertRaises(sup.SupplierError) as caught:
            client([Response(envelope(5), 200)]).item("s", "i")

        self.assertIn("orders:write", str(caught.exception))

    def test_out_of_stock_is_told_plainly(self):
        with self.assertRaises(sup.SupplierError) as caught:
            client([Response(envelope(9))]).item("s", "i")

        self.assertIn("наличии", str(caught.exception))

    def test_the_trace_id_is_kept(self):
        """По нему поставщик находит запрос у себя; выброшенный, он
        превращает разбор в переписку."""
        with self.assertRaises(sup.SupplierError) as caught:
            client([Response(envelope(3, trace="tr-777"))]).item("s", "i")

        self.assertIn("tr-777", str(caught.exception))

    def test_a_success_with_complaints_is_treated_as_a_refusal(self):
        """Ответ, противоречащий сам себе. Решаем в сторону «денег не
        потратили»: ошибиться так дешевле."""
        body = envelope(0, {"items": []}, errors=[{"field": "denominationId"}])

        with self.assertRaises(sup.SupplierError):
            client([Response(body)]).item("s", "i")

    def test_someone_elses_answer_is_not_mistaken_for_success(self):
        """Без statusCode это отвечали не они — прокси, заглушка, что
        угодно. Принять такое за успех значит решить, что код куплен."""
        with self.assertRaises(sup.SupplierError) as caught:
            client([Response({"ok": True})]).item("s", "i")

        self.assertIn("statusCode", str(caught.exception))

    def test_html_instead_of_json_is_a_refusal_not_a_crash(self):
        class Html(Response):
            def json(self):
                raise ValueError("не json")

        with self.assertRaises(sup.SupplierError) as caught:
            client([Html("<html>", 200)]).item("s", "i")

        self.assertIn("JSON", str(caught.exception))


class RetryTest(unittest.TestCase):
    """Разрыв и «слишком часто» — не то же самое, что отказ."""

    def setUp(self):
        self.slept = []
        self.was = sup.time.sleep
        sup.time.sleep = self.slept.append

    def tearDown(self):
        sup.time.sleep = self.was

    def test_a_server_hiccup_is_retried_once(self):
        got = client([Response({}, 502), Response(envelope(0, {"а": 1}))])

        self.assertEqual(got.item("s", "i"), {"а": 1})
        self.assertEqual(len(got.session.calls), 2)

    def test_a_long_pause_is_not_waited_out(self):
        """Повтор на 429 стоит места в том же лимите, из-за которого отказ
        и пришёл. Ждать полминуты, держа поток, — хуже, чем сказать.
        """
        answers = [Response(envelope(8), 429, {"Retry-After": "120"})]

        with self.assertRaises(sup.SupplierError):
            client(answers).item("s", "i")

        self.assertEqual(self.slept, [])

    def test_a_short_pause_is_waited_out(self):
        got = client([Response({}, 429, {"Retry-After": "2"}),
                      Response(envelope(0, {"а": 1}))])

        self.assertEqual(got.item("s", "i"), {"а": 1})
        self.assertEqual(self.slept, [2.0])


# ---------------------------------------------------------------------------
# Покупка
# ---------------------------------------------------------------------------

class PurchaseTest(unittest.TestCase):
    """Форма тела выяснялась живыми вызовами: ни SDK, ни openapi не
    угадали."""

    def body_of(self, reference="pk-robux-100"):
        got = client([Response(envelope(0, {"items": [{"code": "X"}]}))])
        got.place("den-1", reference)

        return got.session.calls[0]

    def test_the_reference_goes_on_top_and_is_called_referenceId(self):
        """Внутри позиции такого поля нет вовсе. Положенная не туда, она
        поставщику не видна — значит идемпотентности нет, и повтор после
        обрыва будет второй покупкой за свои деньги."""
        body = self.body_of()["body"]

        self.assertEqual(body["referenceId"], "pk-robux-100")
        self.assertNotIn("referenceId", body["orders"][0])

    def test_the_shape_is_the_one_the_server_accepts(self):
        body = self.body_of()["body"]

        self.assertEqual(body["ordersType"], "shop")
        self.assertEqual(len(body["orders"]), 1)
        self.assertEqual(body["orders"][0]["denominationId"], "den-1")
        self.assertEqual(body["orders"][0]["quantity"], 1)

    def test_a_long_reference_is_cut_the_same_way_everywhere(self):
        """Разойдутся на один символ — заказ не найдётся, и после обрыва
        связи мы решим, что покупки не было."""
        long = "p" * 60
        sent = self.body_of(long)["body"]["referenceId"]
        looked = sup.cut_reference(long)

        self.assertEqual(sent, looked)
        self.assertEqual(len(sent), sup.REFERENCE_MAX)

    def test_a_refusal_does_not_raise_but_reports(self):
        """Движок должен решать сам: поднятая наверх ошибка обрывает
        проход по остальным заказам."""
        got = client([Response(envelope(9))]).place("d1", "ref-1")

        self.assertFalse(got["ok"])
        self.assertIn("наличии", got["why"])

    def test_the_key_goes_in_the_header(self):
        headers = self.body_of()["headers"]

        self.assertEqual(headers["X-API-Key"], "ключ")


# ---------------------------------------------------------------------------
# Получение кодов
# ---------------------------------------------------------------------------

class CodesTest(unittest.TestCase):
    def test_unhide_and_the_filter_are_both_sent(self):
        """`unhide` без фильтра поставщик отвергает 422, а без `unhide`
        коды приходят замазанными."""
        got = client([Response(envelope(0, {"page": {"items": []}}))])
        got.by_reference("ref-1")
        params = got.session.calls[0]["params"]

        self.assertEqual(params["unhide"], "true")
        self.assertEqual(params["referenceId"], "ref-1")

    def test_codes_are_found_in_the_page(self):
        data = {"page": {"items": [{"status": "SUCCESS",
                                    "vouchers": [{"code": "AAA-BBB"}]}]}}
        got = client([Response(envelope(0, data))]).by_reference("ref-1")

        self.assertEqual(got["codes"], ["AAA-BBB"])
        self.assertEqual(got["status"], "SUCCESS")
        self.assertTrue(got["found"])

    def test_nothing_found_is_not_an_error(self):
        got = client([Response(envelope(0, {"page": {"items": []}}))]
                     ).by_reference("ref-1")

        self.assertTrue(got["ok"])
        self.assertFalse(got["found"])

    def test_a_refusal_is_reported_not_raised(self):
        got = client([Response(envelope(4))]).by_reference("ref-1")

        self.assertFalse(got["ok"])
        self.assertIn("ключ не принят", got["why"])

    def test_the_same_code_twice_is_shown_once(self):
        data = {"items": [{"code": "AAAA-1111"}, {"pin": "AAAA-1111"}]}

        self.assertEqual(sup.codes_from(data), ["AAAA-1111"])

    def test_the_order_of_codes_is_kept(self):
        data = {"items": [{"code": "AAAA-1111"}, {"code": "BBBB-2222"}]}

        self.assertEqual(sup.codes_from(data), ["AAAA-1111", "BBBB-2222"])


class NotACodeTest(unittest.TestCase):
    """Лишний «код» дороже пропущенного.

    Пропущенный виден сразу: движок скажет «ответ без кода» и назовёт
    ссылку покупки. А лишний уходит покупателю молча и выглядит как
    выданный товар.
    """

    def test_a_masked_code_is_not_a_code(self):
        """Поставщик замазывает коды, пока не спросишь `unhide=true`.
        Отправить «****9012» — это отчёт о выдаче, которой не было."""
        self.assertEqual(sup.codes_from({"items": [{"code": "****9012"}]}), [])

    def test_a_currency_is_not_a_code(self):
        """Иначе покупатель получил бы «USD» первой строкой."""
        data = {"currency": {"code": "USD"}, "items": [{"code": "AAAA-BBBB"}]}

        self.assertEqual(sup.codes_from(data), ["AAAA-BBBB"])

    def test_a_region_is_not_a_code(self):
        data = {"region": {"code": "RU"},
                "vouchers": [{"pin": "1111-2222-3333"}]}

        self.assertEqual(sup.codes_from(data), ["1111-2222-3333"])

    def test_a_refusal_is_not_a_code(self):
        """Худший случай: покупатель получает «OUT_OF_STOCK» вместо кода,
        а заказ отмечается выданным."""
        self.assertEqual(
            sup.codes_from({"error": {"code": "OUT_OF_STOCK"}}), [])

    def test_a_status_name_is_not_a_code(self):
        for name in ("SUCCESS", "CANCELLED", "IN_PROGRESS",
                     "PARTIALLY_COMPLETED"):
            self.assertEqual(sup.codes_from({"items": [{"code": name}]}), [],
                             name)

    def test_real_codes_still_pass(self):
        """Отбор не должен оказаться строже жизни: пропущенный код —
        оплаченный заказ без выдачи."""
        for code in ("ABCDE-FGHIJ-KLMNO", "XMXQ4T7PLKD9", "1111-2222-3333",
                     "AAAA-BBBB", "9SZ4KQ7T2M"):
            self.assertEqual(
                sup.codes_from({"items": [{"code": code}]}), [code], code)

    def test_a_code_next_to_its_status_is_found(self):
        data = {"items": [{"status": "SUCCESS", "code": "XMXQ4T7PLKD9"}]}

        self.assertEqual(sup.codes_from(data), ["XMXQ4T7PLKD9"])


if __name__ == "__main__":
    unittest.main()
