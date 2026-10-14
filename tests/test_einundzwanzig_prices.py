# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the membership fee is shown in sats, CHF and EUR.

What must hold:

  The order is always sats, then CHF, then EUR. The association's own
  amount is shown exactly; every converted amount says "about".

  Without a price, or for a currency the price list does not cover, the
  association's amount is shown alone.

  The price lookup answers exactly once, with None for anything it cannot
  use (an error, a body too large, a price that is not a positive number),
  and not at all after it was canceled.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QNetworkReply  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import einundzwanzig_api as e21  # noqa: E402
from tests.membership_fakes import FakeNam, FakeReply  # noqa: E402

PRICES = e21.Prices(chf=100_000.0, eur=110_000.0)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


# -- parsing ---------------------------------------------------------------------

def test_the_price_list_is_read_for_francs_and_euros():
    prices = e21.parse_prices({"time": 1, "USD": 85808, "EUR": 76470, "CHF": 71340})
    assert prices == e21.Prices(chf=71340.0, eur=76470.0)


@pytest.mark.parametrize("data", [
    None, [], {"EUR": 1}, {"CHF": 1}, {"CHF": "71340", "EUR": 1}, {"CHF": 0, "EUR": 1},
    {"CHF": -5, "EUR": 1}, {"CHF": True, "EUR": 1}, {"CHF": float("nan"), "EUR": 1},
    {"CHF": float("inf"), "EUR": 1},
])
def test_a_price_list_without_two_usable_prices_is_refused(data):
    with pytest.raises(ValueError):
        e21.parse_prices(data)


# -- converting ------------------------------------------------------------------

def test_a_fee_in_sats_converts_to_francs_and_euros():
    assert e21.fee_amounts(21_000, "SATS", PRICES) == [
        ("SATS", 21_000.0, True), ("CHF", 21.0, False), ("EUR", pytest.approx(23.1), False)]


def test_a_fee_in_francs_converts_to_sats_and_euros():
    assert e21.fee_amounts(21, "chf", PRICES) == [
        ("SATS", 21_000.0, False), ("CHF", 21.0, True), ("EUR", pytest.approx(23.1), False)]


def test_a_fee_in_euros_keeps_the_display_order():
    units = [unit for unit, _value, _exact in e21.fee_amounts(22, "EUR", PRICES)]
    assert units == ["SATS", "CHF", "EUR"]


def test_without_a_price_only_the_stated_amount_is_shown():
    assert e21.fee_amounts(21, "CHF", None) == [("CHF", 21.0, True)]


def test_a_currency_the_prices_do_not_cover_is_shown_alone():
    assert e21.fee_amounts(25, "USD", PRICES) == [("USD", 25.0, True)]


# -- wording ---------------------------------------------------------------------

@pytest.mark.parametrize("amount, currency, prices, expected", [
    (21_000, "SATS", PRICES, ("21,000 sats", "about 21 CHF, about 23 EUR")),
    (21, "CHF", PRICES, ("about 21,000 sats", "21 CHF, about 23 EUR")),
    (21, "CHF", None, ("21 CHF", "")),
    (21_000, "sat", None, ("21,000 sats", "")),
    # A converted sats amount is rounded to the hundred; the exact one never is.
    (21, "CHF", e21.Prices(chf=71_340.0, eur=76_470.0), ("about 29,400 sats",
                                                         "21 CHF, about 23 EUR")),
    (29_437, "SATS", None, ("29,437 sats", "")),
])
def test_the_fee_reads_sats_first(amount, currency, prices, expected):
    assert e21.format_fee(amount, currency, prices) == expected


def test_small_converted_amounts_keep_cents():
    assert e21.format_one("EUR", 0.42, False) == "about 0.42 EUR"


# -- the lookup ------------------------------------------------------------------

def answers(reply):
    nam = FakeNam([reply])
    lookup = e21.PriceLookup(nam=nam)
    got = []
    lookup.lookup(got.append)
    reply.finished.emit()
    return got, nam


def test_the_lookup_asks_mempool_space_and_answers_once():
    got, nam = answers(FakeReply(body=json.dumps({"CHF": 71340, "EUR": 76470}).encode()))
    assert got == [e21.Prices(chf=71340.0, eur=76470.0)]
    verb, request, _body = nam.calls[0]
    assert verb == "GET" and request.url().toString() == e21.PRICES_URL


@pytest.mark.parametrize("reply", [
    FakeReply(error=QNetworkReply.NetworkError.HostNotFoundError),
    FakeReply(body=b"not json"),
    FakeReply(body=b"\xff\xfe"),
    FakeReply(body=json.dumps({"CHF": 0, "EUR": 1}).encode()),
    FakeReply(body=b"{" + b" " * (32 * 1024) + b"}"),
])
def test_an_unusable_answer_is_no_price(reply):
    got, _nam = answers(reply)
    assert got == [None]


def test_a_canceled_lookup_never_answers():
    reply = FakeReply(body=json.dumps({"CHF": 1, "EUR": 1}).encode())
    lookup = e21.PriceLookup(nam=FakeNam([reply]))
    got = []
    lookup.lookup(got.append)
    lookup.cancel()
    reply.finished.emit()
    assert got == []
