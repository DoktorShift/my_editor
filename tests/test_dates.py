# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how a day and a time are written (i18n.format_day, format_moment).

What must hold:

  A day is written the language's own way, from its locale: "January 6,
  2027" in English, "6. Januar 2027" in German, never a padded day from one
  fixed pattern for every language ("06 January 2027", what the Drafts
  panel and the Published view showed in English). No weekday: a date in
  a row or a tooltip does not need one.

  A list row can have the month shortened ("Jan 6, 2027"); a time comes
  with the day, the language's way ("2:05 PM", "14:05").

  The Drafts panel's and the Published view's helpers, and the membership
  window's statutes date, go through these, so every window writes a day
  the same way.
"""

import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QDateTime, QTime  # noqa: E402

import i18n  # noqa: E402

DAY = datetime.date(2027, 1, 6)
MOMENT = QDateTime(QDate(2027, 1, 6), QTime(14, 5))


@pytest.fixture
def language():
    """Install a language for one test, and English again after it."""
    def install(code):
        i18n.install(code)
    yield install
    i18n.install(i18n.ENGLISH)


def test_english_writes_the_day_its_own_way(language):
    language("en")
    assert i18n.format_day(DAY) == "January 6, 2027"
    assert i18n.format_day(DAY, short_month=True) == "Jan 6, 2027"
    assert i18n.format_moment(MOMENT) == "January 6, 2027 at 2:05 PM" or \
        i18n.format_moment(MOMENT) == "January 6, 2027 at 2:05 PM"


def test_german_writes_the_day_its_own_way(language):
    language("de")
    assert i18n.format_day(DAY) == "6. Januar 2027"
    assert i18n.format_moment(MOMENT) == "6. Januar 2027 um 14:05"


@pytest.mark.parametrize("code", ["en", "de", "pseudo"])
def test_no_padded_day_and_no_weekday(language, code):
    language(code)
    shown = i18n.format_day(DAY)
    assert "06" not in shown and "Wednesday" not in shown and "Mittwoch" not in shown
    assert "2027" in shown


def test_the_windows_write_a_day_through_it(language):
    language("en")
    from nostr.ui import drafts_common
    from nostr.ui.membership_window import format_date
    stamp = QDateTime(QDate(2027, 1, 6), QTime(12, 0)).toSecsSinceEpoch()
    assert drafts_common.format_absolute_date(stamp) == "January 6, 2027"
    assert drafts_common.format_short_date(stamp) == "Jan 6, 2027"
    assert drafts_common.format_absolute_time(stamp).startswith("January 6, 2027 at ")
    assert format_date("2024-04-20") == "April 20, 2024"
    assert format_date("not a date") == "not a date"
