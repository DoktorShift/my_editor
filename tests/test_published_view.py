# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Published view (nostr/ui/published_view.py): its states, its rows,
its actions and its keys.

What must hold: every state says what it is in words (no account,
loading, nothing published yet, could not be read with Try Again); a row
says its title, Article or Note, and when it first went out, also to a
screen reader; Return opens (an article for editing, a note in the
browser); Delete asks first, with Cancel as the default button and the
owner's wording; a deletion no relay took offers Try Again; the Menu key
opens the menu of the current row; every control has a name.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import alerts  # noqa: E402
from nostr.published import LOADING, PAGE_SIZE, PublishedList  # noqa: E402
from nostr.ui import published_view as pv  # noqa: E402
from nostr.ui.published_view import META_ROLE, PublishedView  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    NOW, FakeClient, FakeRelayDirectory, FakeSessionPool, PK, Profile, signed,
)
from tests.published_fakes import Relays  # noqa: E402

R1, R2 = "wss://one.example", "wss://two.example"
REAL_ALERT = alerts.Alert          # kept before the tests stand in for it


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication(sys.argv)


class AnsweringAlert:
    """Stands in for alerts.Alert: records what was asked, answers from a
    list (a value, or a function of what was asked that returns one)."""

    asked = []
    answers = []

    def __init__(self, parent, *, title, message="", buttons=(), caution=False,
                 details="", checkbox="", is_dark=None):
        self.record = {"title": title, "message": message, "buttons": list(buttons),
                       "caution": caution, "details": details}
        AnsweringAlert.asked.append(self.record)

    def run(self):
        answer = AnsweringAlert.answers.pop(0)
        return answer(self.record) if callable(answer) else answer


@pytest.fixture(autouse=True)
def answered_alerts(monkeypatch):
    AnsweringAlert.asked, AnsweringAlert.answers = [], []
    monkeypatch.setattr(alerts, "Alert", AnsweringAlert)
    return AnsweringAlert


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(pv.QDesktopServices, "openUrl", lambda url: urls.append(url.toString()))
    return urls


def note(text, *, at=NOW - 1000):
    return signed(1, [["client", "MyEditor"]], text, created_at=at)


def article(d, title, *, at=NOW - 2000):
    return signed(30023, [["d", d], ["title", title]], "Body", created_at=at)


def make(*stored, refuse_publish=(), down=()):
    relays = Relays(refuse_publish=refuse_publish)
    relays.keep(R1, *stored)
    relays.down = set(down)
    model = PublishedList(relay_pool=relays,
                          relay_directory=FakeRelayDirectory({PK: [R1, R2]}),
                          session_pool=FakeSessionPool(FakeClient()), clock=lambda: NOW)
    view = PublishedView(model, is_dark=False)
    view.resize(320, 480)
    said = []
    view.status_message.connect(said.append)
    edited = []
    view.open_requested.connect(edited.append)
    return view, model, relays, said, edited


def shown(view):
    """What the view shows now: the list's rows, or the placeholder's words."""
    if view._stack.currentWidget() is view._list:
        return [view._rows.data(view._rows.index(row)) for row in range(view._rows.rowCount())]
    return (view._empty_title.text() if view._empty_title.isVisibleTo(view) else "",
            view._empty_body.text(), view._busy.isVisibleTo(view),
            view._empty_action.isVisibleTo(view))


def select(view, row):
    view._list.setCurrentIndex(view._rows.index(row))


# -- states --------------------------------------------------------------------------------

def test_no_account():
    view, _model, _relays, _said, _edited = make()
    assert shown(view) == ("No account in use", "Choose an account in the Nostr menu to see "
                           "what you published.", False, False)
    assert unnamed_controls(view) == []


def test_loading_then_nothing_published_yet():
    view, model, relays, _said, _edited = make()
    view.set_account(Profile())
    assert model.state == LOADING
    assert shown(view) == ("", "Looking for your notes and articles…", True, False)
    relays.run()
    assert shown(view) == ("Nothing published yet",
                           "Notes and articles you publish appear here.", False, False)
    assert unnamed_controls(view) == []


def test_could_not_be_read_offers_try_again():
    view, model, relays, _said, _edited = make(note("hello"), down=(R1, R2))
    view.set_account(Profile())
    relays.run()
    assert shown(view) == ("Couldn’t load what you published",
                           "Check your internet connection, then try again.", False, True)
    assert unnamed_controls(view) == []
    relays.down = set()
    view._empty_action.click()
    assert model.state == LOADING
    relays.run()
    assert shown(view) == ["hello"]


def test_a_row_says_its_title_what_it_is_and_when_it_first_went_out():
    view, _model, relays, _said, _edited = make(article("essay", "Essay"), note("Hello there"))
    view.set_account(Profile())
    relays.run()
    assert shown(view) == ["Hello there", "Essay"]
    rows = view._rows
    date = pv.format_absolute_date(NOW - 2000)
    assert rows.data(rows.index(1), META_ROLE) == f"Article · {date}"
    assert rows.data(rows.index(0), META_ROLE).startswith("Note · ")
    spoken = rows.data(rows.index(1), Qt.ItemDataRole.AccessibleTextRole)
    assert spoken.startswith("Essay. Article. First published ")
    assert "Essay" in rows.data(rows.index(1), Qt.ItemDataRole.ToolTipRole)
    assert view._list.accessibleName() == "Published"
    assert unnamed_controls(view) == []


def test_an_untitled_item_still_has_a_name():
    view, _model, relays, _said, _edited = make(note("   "))
    view.set_account(Profile())
    relays.run()
    assert shown(view) == ["Untitled"]


def test_load_more_is_the_last_row_and_return_on_it_loads_more():
    notes = [note(f"note {n}", at=NOW - 10 * n) for n in range(PAGE_SIZE + 3)]
    view, model, relays, _said, _edited = make(*notes)
    view.set_account(Profile())
    relays.run()
    rows = view._rows
    assert rows.rowCount() == PAGE_SIZE + 1 and shown(view)[-1] == "Load More Notes"
    select(view, PAGE_SIZE)
    QTest.keyClick(view._list, Qt.Key.Key_Return)
    assert model.loading_more and shown(view)[-1] == "Loading…"
    relays.run()
    assert shown(view) == [f"note {n}" for n in range(PAGE_SIZE + 3)]


def test_load_more_that_failed_says_so_and_stays_the_way_to_try_again():
    notes = [note(f"note {n}", at=NOW - 10 * n) for n in range(PAGE_SIZE + 1)]
    view, model, relays, said, _edited = make(*notes)
    view.set_account(Profile())
    relays.run()
    relays.down = {R1, R2}
    model.load_more()
    relays.run()
    rows = view._rows
    last = rows.index(rows.rowCount() - 1)
    assert rows.data(last) == "Load More Notes"             # the action stays whole
    assert rows.data(last, pv.PROBLEM_ROLE) == "Couldn’t load."
    assert rows.data(last, Qt.ItemDataRole.AccessibleTextRole).endswith(
        "Press Return to try again.")
    assert said[-1] == ("Couldn’t load more notes. Check your internet connection, "
                        "then try again.")
    relays.down = set()
    view._list.setCurrentIndex(last)
    QTest.keyClick(view._list, Qt.Key.Key_Return)
    relays.run()
    assert rows.rowCount() == PAGE_SIZE + 1 and not model.has_more


def test_a_narrow_line_shows_the_date_in_digits():
    view, _model, relays, _said, _edited = make(article("essay", "Essay"))
    view.set_account(Profile())
    relays.run()
    rows = view._rows
    assert rows.data(rows.index(0), pv.SHORT_META_ROLE) == (
        f"Article · {pv.format_short_date(NOW - 2000)}")


# -- actions --------------------------------------------------------------------------------

def test_return_opens_an_article_for_editing_and_a_note_in_the_browser(opened):
    essay = article("essay", "Essay")
    view, _model, relays, _said, edited = make(essay, note("hello"))
    view.set_account(Profile())
    relays.run()
    select(view, 1)
    QTest.keyClick(view._list, Qt.Key.Key_Return)
    assert [item.id for item in edited] == [essay["id"]] and opened == []
    select(view, 0)
    QTest.keyClick(view._list, Qt.Key.Key_Enter)
    assert len(opened) == 1 and opened[0].startswith("https://njump.me/nevent1")


def test_the_menu_of_an_article_and_of_a_note():
    view, _model, relays, _said, _edited = make(article("essay", "Essay"), note("hello"))
    view.set_account(Profile())
    relays.run()
    words = lambda menu: [a.text() for a in menu.actions() if not a.isSeparator()]  # noqa: E731
    article_menu = view.menu_for(view._rows.index(1))
    assert words(article_menu) == ["Edit", "Open in Browser", "Copy Link", "Delete from Nostr…"]
    assert article_menu.defaultAction().text() == "Edit"
    note_menu = view.menu_for(view._rows.index(0))
    assert words(note_menu) == ["Open in Browser", "Copy Link", "Delete from Nostr…"]
    assert note_menu.defaultAction().text() == "Open in Browser"
    delete = note_menu.actions()[-1]
    assert not delete.shortcut().isEmpty()        # the key that does it, shown
    assert view.menu_for(view._rows.index(5)) is None


def test_copy_link_puts_the_web_address_on_the_clipboard():
    view, _model, relays, said, _edited = make(article("essay", "Essay"))
    view.set_account(Profile())
    relays.run()
    menu = view.menu_for(view._rows.index(0))
    next(a for a in menu.actions() if a.text() == "Copy Link").trigger()
    assert QGuiApplication.clipboard().text().startswith("https://njump.me/naddr1")
    assert said[-1] == "Link copied."


def test_delete_asks_first_with_the_owners_words_and_cancel_as_the_default(answered_alerts):
    view, model, relays, _said, _edited = make(article("essay", "Essay"))
    view.set_account(Profile())
    relays.run()
    select(view, 0)
    answered_alerts.answers = [False]
    QTest.keyClick(view._list, Qt.Key.Key_Delete)
    (asked,) = answered_alerts.asked
    assert asked["title"] == "Delete “Essay” from Nostr?"
    assert asked["message"] == ("Relays that keep it are asked to remove it. People and apps "
                                "that already saved a copy may still have it. Your draft stays.")
    assert relays.published == [] and len(model.items()) == 1
    # The real alert, built from what the view asked for.
    dialog = REAL_ALERT(None, title=asked["title"], message=asked["message"],
                        buttons=asked["buttons"], caution=asked["caution"])
    assert dialog.buttons["Cancel"].isDefault()
    assert not dialog.buttons["Delete"].isDefault()
    assert dialog.buttons["Delete"].objectName() == "destructive"
    dialog.deleteLater()


def test_confirming_deletes_and_the_row_goes(answered_alerts):
    view, model, relays, said, _edited = make(article("essay", "Essay"), note("kept"))
    view.set_account(Profile())
    relays.run()
    select(view, 1)
    answered_alerts.answers = [True]
    QTest.keyClick(view._list, Qt.Key.Key_Backspace)
    assert view._rows.data(view._rows.index(1), META_ROLE) == "Deleting…"
    relays.run()
    assert shown(view) == ["kept"]
    assert said[-1] == "2 of 2 relays accepted the request to delete “Essay”."
    assert view._list.currentIndex().row() == 0          # the keyboard stays in the list


def test_a_deletion_no_relay_took_offers_try_again(answered_alerts):
    view, model, relays, _said, _edited = make(note("hello"), refuse_publish={R1, R2})
    view.set_account(Profile())
    relays.run()
    select(view, 0)

    def try_again_once_the_relays_are_back(_record):
        relays.publisher.refuse = set()
        return True

    answered_alerts.answers = [True, try_again_once_the_relays_are_back]
    QTest.keyClick(view._list, Qt.Key.Key_Delete)
    relays.run()
    failure = answered_alerts.asked[1]
    assert failure["title"] == "Couldn’t delete “hello”"
    assert failure["message"].startswith("No relay accepted the request.")
    assert [(b.label, b.role) for b in failure["buttons"]] == [
        ("Cancel", alerts.CANCEL), ("Try Again", alerts.DEFAULT)]
    assert R1 in failure["details"]
    relays.run()
    assert shown(view) == ("Nothing published yet",
                           "Notes and articles you publish appear here.", False, False)


def test_a_deletion_left_as_it_is_keeps_the_row(answered_alerts):
    view, model, relays, _said, _edited = make(note("hello"), refuse_publish={R1, R2})
    view.set_account(Profile())
    relays.run()
    select(view, 0)
    answered_alerts.answers = [True, False]         # Delete, then Cancel
    QTest.keyClick(view._list, Qt.Key.Key_Delete)
    relays.run()
    assert shown(view) == ["hello"] and model.deletion_state(model.items()[0].id) == ""


def test_the_menu_key_opens_the_menu_of_the_current_row():
    view, _model, relays, _said, _edited = make(note("one", at=NOW - 1), note("two"))
    view.set_account(Profile())
    relays.run()
    asked = []
    view._list.menu_requested.disconnect()
    view._list.menu_requested.connect(lambda index, _point: asked.append(index.row()))
    select(view, 1)
    QApplication.sendEvent(view._list, QContextMenuEvent(
        QContextMenuEvent.Reason.Keyboard, QPoint(5, 5), view._list.mapToGlobal(QPoint(5, 5))))
    assert asked == [1]


def test_both_themes_build():
    view, _model, relays, _said, _edited = make(note("hello"))
    view.set_account(Profile())
    relays.run()
    view.apply_theme(True)
    assert view.is_dark and "#1E1E1E".lower() in view.styleSheet().lower()
    view.apply_theme(False)
    assert not view.is_dark
