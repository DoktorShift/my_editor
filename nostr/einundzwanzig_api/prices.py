# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The membership fee in sats, Swiss francs and euros.

The association states its fee in one currency (``/config``), and the
invoice it issues states the exact sats. People still want to see the
fee the way they think about money, so the window shows it in sats
first, then CHF, then EUR. The association's own amount is always shown
exactly; every other amount is converted with today's Bitcoin price and
marked as approximate.

The price comes from mempool.space's public price endpoint, asked once
while the membership window is open, with no account and a plain app
name attached (mempool.space sees the computer's address, as any web
request does). When it cannot be reached, the window shows the
association's amount alone.

Amounts are written the way the app's language writes numbers:
``21,000 sats`` in English, ``21.000 Sats`` in German.

:func:`parse_prices`, :func:`fee_amounts` and :func:`format_fee` are pure;
:class:`PriceLookup` is the one network call.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import QLocale, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import i18n
from i18n import _

PRICES_URL: str = "https://mempool.space/api/v1/prices"
PRICES_TIMEOUT_MS: int = 8_000
_MAX_PRICES_BYTES: int = 16 * 1024
# A plain name: the price is the same for everyone, and the request says
# nothing about why it was asked.
_USER_AGENT = b"MyEditor"

SATS = "SATS"
CHF = "CHF"
EUR = "EUR"
# The order the fee is shown in: sats, then francs, then euros.
DISPLAY_ORDER: Tuple[str, ...] = (SATS, CHF, EUR)
SATS_PER_BTC: int = 100_000_000


@dataclass(frozen=True)
class Prices:
    """What one bitcoin costs, in each currency the fee is shown in."""

    chf: float
    eur: float

    def per_btc(self, currency: str) -> Optional[float]:
        return {CHF: self.chf, EUR: self.eur}.get(currency)


def _currency(value: str) -> str:
    unit = (value or "").strip().upper()
    return SATS if unit in ("SAT", "SATS") else unit


def _positive_price(obj: dict, key: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} is not a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} is not a usable price")
    return value


def parse_prices(data: Any) -> Prices:
    """mempool.space's ``{"CHF": 71340, "EUR": 76470, ...}``. Raises
    ValueError when either price is missing or not a positive number."""
    if not isinstance(data, dict):
        raise ValueError("prices is not an object")
    return Prices(chf=_positive_price(data, CHF), eur=_positive_price(data, EUR))


def fee_amounts(amount: float, currency: str,
                prices: Optional[Prices]) -> List[Tuple[str, float, bool]]:
    """The fee in every currency that can be shown, in display order.

    Each entry is ``(currency, amount, exact)``. The association's own
    currency is exact; the others are converted and only present when
    ``prices`` is known. A currency the price list does not cover (say,
    a fee in USD) is shown as stated, alone.
    """
    unit = _currency(currency)
    stated = (unit, float(amount), True)
    if unit not in DISPLAY_ORDER or prices is None or amount <= 0:
        return [stated]
    if unit == SATS:
        btc = amount / SATS_PER_BTC
    else:
        btc = amount / prices.per_btc(unit)
    shown = []
    for other in DISPLAY_ORDER:
        if other == unit:
            shown.append(stated)
        elif other == SATS:
            shown.append((SATS, btc * SATS_PER_BTC, False))
        else:
            shown.append((other, btc * prices.per_btc(other), False))
    return shown


def _round_sats(value: float, exact: bool) -> int:
    if exact:
        return int(round(value))
    # A converted amount moves with the price; to the hundred is honest.
    return int(round(value / 100.0)) * 100 if value >= 1000 else int(round(value))


def display_locale() -> QLocale:
    """The language's way of writing numbers and dates. The pseudo
    language writes them as English does."""
    language = i18n.language()
    return QLocale(i18n.ENGLISH if language == i18n.PSEUDO else language)


def format_number(value: float, decimals: int = 0) -> str:
    """``21,000`` in English, ``21.000`` in German."""
    return display_locale().toString(float(value), "f", decimals)


def format_one(currency: str, amount: float, exact: bool = True) -> str:
    """``21,000 sats``, ``21 CHF`` or ``about 22 EUR``."""
    unit = _currency(currency)
    if unit == SATS:
        text = _("{amount} sats").format(amount=format_number(_round_sats(amount, exact)))
    elif exact and float(amount).is_integer():
        text = f"{format_number(amount)} {unit}".strip()
    elif not exact and amount >= 1:
        text = f"{format_number(round(amount))} {unit}".strip()
    else:
        text = f"{format_number(amount, 2)} {unit}".strip()
    return text if exact else _("about {amount}").format(amount=text)


def format_fee(amount: float, currency: str, prices: Optional[Prices]) -> Tuple[str, str]:
    """The fee as ``(first, rest)``: the sats amount (or the stated
    amount, when sats cannot be worked out), and the other currencies
    joined, in display order. ``rest`` is empty when there is nothing
    more to show."""
    parts = [format_one(unit, value, exact)
             for unit, value, exact in fee_amounts(amount, currency, prices)]
    return parts[0], ", ".join(parts[1:])


class PriceLookup(QObject):
    """Asks mempool.space once for today's Bitcoin price.

    ``lookup(on_done)`` calls ``on_done(Prices)`` or ``on_done(None)``
    exactly once, never synchronously. ``cancel()`` drops an answer that
    has not arrived yet. ``nam`` is the seam for tests.
    """

    def __init__(self, *, nam: Optional[QNetworkAccessManager] = None,
                 url: str = PRICES_URL, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._nam = nam or QNetworkAccessManager(self)
        self._url = url
        self._generation = 0

    def cancel(self) -> None:
        self._generation += 1

    def lookup(self, on_done: Callable[[Optional[Prices]], None]) -> None:
        generation = self._generation
        request = QNetworkRequest(QUrl(self._url))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setTransferTimeout(PRICES_TIMEOUT_MS)
        reply = self._nam.get(request)
        if reply is None:
            QTimer.singleShot(0, lambda: on_done(None) if generation == self._generation
                              else None)
            return

        def finished() -> None:
            prices = None
            try:
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    raw = bytes(reply.readAll())
                    if len(raw) <= _MAX_PRICES_BYTES:
                        prices = parse_prices(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                prices = None
            finally:
                reply.deleteLater()
            if generation == self._generation:
                on_done(prices)

        reply.finished.connect(finished)
