# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the panel's lists say when a piece was written, and what a
row shows after its title (nostr/ui/post_list.py).

What must hold, as Notes does it:

  Rows are grouped under Today, Yesterday, Previous 7 Days, Previous 30
  Days, then the months of this year, then years; around midnight and
  New Year a row lands in the right group. A row's date is the time on
  the day itself and the day before, the weekday within a week, the date
  in digits before that, in the language's own way. A note's line 2 is
  the words after its title, never the title again. A section's title
  is part of its first row, so sections add no rows of their own.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QDate, QDateTime, QLocale, QRect, QTime  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem  # noqa: E402

from nostr.ui.post_list import (  # noqa: E402
    ROW_ROLE, PostRow, PostRowDelegate, date_section, list_date, row_height, section_height,
    section_starts, words_after_title,
)

GERMAN = QLocale(QLocale.Language.German, QLocale.Country.Germany)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


def at(year, month, day, hour=12, minute=0) -> int:
    return QDateTime(QDate(year, month, day), QTime(hour, minute)).toSecsSinceEpoch()


NOW = at(2026, 10, 14, 15, 0)          # a Wednesday afternoon


def test_words_after_title_never_repeat_the_title():
    assert words_after_title("Thinking about relays again",
                             "Thinking about relays again Who keeps my drafts?") == \
        "Who keeps my drafts?"
    assert words_after_title("gm.", "gm. Coffee first") == "Coffee first"
    assert words_after_title("One line only", "One line only") == ""
    # An article's summary does not start with its title: kept whole.
    assert words_after_title("Lotus v0.4.5", "What changed since the last release") == \
        "What changed since the last release"
    assert words_after_title("Case", "case insensitive start") == "insensitive start"


@pytest.mark.parametrize("ts, section", [
    (at(2026, 10, 14, 0, 5), "Today"),
    (at(2026, 10, 14, 23, 59), "Today"),
    (at(2026, 10, 15, 9, 0), "Today"),                    # a clock running ahead
    (at(2026, 10, 13, 23, 59), "Yesterday"),
    (at(2026, 10, 12, 8, 0), "Previous 7 Days"),
    (at(2026, 10, 8, 8, 0), "Previous 7 Days"),
    (at(2026, 10, 7, 8, 0), "Previous 30 Days"),
    (at(2026, 9, 15, 8, 0), "Previous 30 Days"),
    (at(2026, 9, 14, 8, 0), "September"),
    (at(2026, 1, 2, 8, 0), "January"),
    (at(2025, 12, 31, 8, 0), "2025"),
])
def test_rows_are_grouped_as_notes_groups_them(ts, section):
    assert date_section(ts, NOW, QLocale(QLocale.Language.English)) == section


def test_new_year_keeps_last_week_together_and_last_year_apart():
    now = at(2027, 1, 2, 10, 0)
    english = QLocale(QLocale.Language.English)
    assert date_section(at(2026, 12, 31, 22, 0), now, english) == "Previous 7 Days"
    assert date_section(at(2026, 12, 10, 22, 0), now, english) == "Previous 30 Days"
    assert date_section(at(2026, 11, 1, 22, 0), now, english) == "2026"


def test_a_row_dates_itself_as_mail_and_notes_do_in_the_language_shown():
    assert list_date(at(2026, 10, 14, 14, 5), NOW, GERMAN) == "14:05"
    assert list_date(at(2026, 10, 13, 21, 40), NOW, GERMAN) == "21:40"
    assert list_date(at(2026, 10, 10, 9, 0), NOW, GERMAN) == "Samstag"
    assert list_date(at(2026, 9, 24, 9, 0), NOW, GERMAN) == "24.09.26"
    english = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
    assert list_date(at(2026, 10, 10, 9, 0), NOW, english) == "Saturday"
    assert list_date(0, NOW, english) == ""


def test_only_the_first_row_of_a_section_carries_its_title():
    times = [at(2026, 10, 14, 10), at(2026, 10, 14, 9), at(2026, 10, 13, 9),
             at(2026, 10, 2, 9), at(2026, 9, 30, 9)]
    assert section_starts(times, NOW, QLocale(QLocale.Language.English)) == [
        "Today", "", "Yesterday", "Previous 30 Days", ""]


def test_a_section_title_makes_its_row_taller_and_a_long_title_gets_a_tooltip():
    view = QListWidget()
    delegate = PostRowDelegate(view, is_dark=True)
    view.setItemDelegate(delegate)
    plain = QListWidgetItem()
    plain.setData(ROW_ROLE, PostRow("Short", kind="note", when="14:05"))
    first = QListWidgetItem()
    first.setData(ROW_ROLE, PostRow("Short", kind="note", section="Today"))
    view.addItem(first)
    view.addItem(plain)
    first_index, plain_index = view.indexFromItem(first), view.indexFromItem(plain)
    option = view.viewOptions() if hasattr(view, "viewOptions") else None
    assert delegate.sizeHint(option, plain_index).height() == row_height()
    assert delegate.sizeHint(option, first_index).height() == row_height() + section_height()
    narrow = QRect(0, 0, 120, row_height())
    long_row = PostRow("A title far too long for a narrow panel to show whole")
    assert delegate.title_is_cut(narrow, long_row)
    assert not delegate.title_is_cut(QRect(0, 0, 900, row_height()), long_row)
