# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selecting several drafts and asking for them to go.

The panel's whole job here is to name the right drafts. It does not
confirm and it does not delete; it says which ones, and the host asks
the question. So what is pinned is the selection: that the list allows
one, that Delete means the selection, and above all that a right-click
on a row outside the selection acts on that row. Getting that last one
wrong turns a stray click into twelve deletions the user did not ask
for, and there is no undo behind it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QListWidget

from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta
from nostr.profiles import Profile
from nostr.ui.drafts_panel import DraftsPanel
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


PK = "a" * 64


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def wrap(identifier, created_at=1700000000):
    return DraftWrapMeta(
        identifier=identifier, inner_kind=1, event_id=identifier + "e",
        pubkey=PK, created_at=created_at, expiration=None, ciphertext="ct",
    )


@pytest.fixture
def panel():
    return build_panel()


def build_panel():
    store = DraftStore()
    store.bind_profile(PK)
    for i in range(5):
        store.upsert_skeleton(wrap(f"d{i}", created_at=1700000000 + i))
        store.set_decrypted(
            f"d{i}", inner={"kind": 1, "content": f"Body {i}", "tags": []})
    p = DraftsPanel()
    p.set_active_profile(Profile(
        user_pubkey=PK, bunker_pubkey="b" * 64,
        bunker_relays=["wss://relay.example"], local_secret_hex="c" * 64,
        display_name="Alice",
    ))
    p.bind_store(store)
    return p


def select(panel, *rows):
    panel._list.clearSelection()
    for row in rows:
        panel._list.item(row).setSelected(True)
    panel._list.setCurrentRow(rows[-1] if rows else -1)


def rows_of(panel):
    return [panel._list.item(r).data(Qt.UserRole)
            for r in range(panel._list.count())]


def emitted(panel):
    out = []
    panel.delete_drafts.connect(lambda ids: out.append(list(ids)))
    return out


# --------------------------------------------------------------------- #
# Selecting more than one                                               #
# --------------------------------------------------------------------- #

def test_the_list_allows_more_than_one_row(panel):
    assert panel._list.selectionMode() == QListWidget.ExtendedSelection


def test_selected_identifiers_follows_the_list_not_the_click_order(panel):
    order = rows_of(panel)
    select(panel, 3, 0, 2)
    # Clicked 3, 0, 2; the answer is in the order the user is looking at.
    assert panel.selected_identifiers() == [order[0], order[2], order[3]]


def test_nothing_selected_is_an_empty_answer(panel):
    panel._list.clearSelection()
    assert panel.selected_identifiers() == []


def test_select_all_reaches_every_row(panel):
    panel._list.selectAll()
    assert len(panel.selected_identifiers()) == 5


# --------------------------------------------------------------------- #
# Delete means the selection                                            #
# --------------------------------------------------------------------- #

def _press(panel, key, target=None):
    """Send a key to the list, which is where the filter lives.

    Reaching the list is what "the list has focus" means at runtime, and
    it is the part an offscreen test can actually reproduce: setFocus on
    a widget that was never shown does not take.
    """
    event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
    QApplication.sendEvent(target if target is not None else panel._list, event)
    return event


def test_the_delete_key_asks_for_the_selected_drafts(panel):
    out = emitted(panel)
    select(panel, 1, 2)
    _press(panel, Qt.Key_Delete)
    assert out == [[rows_of(panel)[1], rows_of(panel)[2]]]


def test_backspace_does_the_same_thing(panel):
    out = emitted(panel)
    select(panel, 0)
    _press(panel, Qt.Key_Backspace)
    assert out == [[rows_of(panel)[0]]]


def test_delete_with_nothing_selected_asks_for_nothing(panel):
    out = emitted(panel)
    panel._list.clearSelection()
    _press(panel, Qt.Key_Delete)
    assert out == []


def test_delete_in_the_search_field_is_an_edit_not_a_deletion(panel):
    # The search field is in this panel too, and Delete there means what
    # it means in every text field.
    out = emitted(panel)
    select(panel, 1)
    _press(panel, Qt.Key_Delete, target=panel._search_edit)
    assert out == []


def test_an_unrelated_key_is_passed_on(panel):
    out = emitted(panel)
    select(panel, 1)
    _press(panel, Qt.Key_A)
    assert out == []


# --------------------------------------------------------------------- #
# The context menu, and the stray click                                 #
# --------------------------------------------------------------------- #

def menu_labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test_the_menu_counts_the_drafts_it_would_delete(panel):
    select(panel, 0, 1, 2)
    menu = panel._build_context_menu(panel._list.item(1))
    assert "Delete 3 Drafts" in menu_labels(menu)


def test_one_selected_draft_is_named_in_the_singular(panel):
    select(panel, 2)
    menu = panel._build_context_menu(panel._list.item(2))
    assert "Delete Draft" in menu_labels(menu)


def test_right_clicking_outside_the_selection_acts_on_that_row_only(panel):
    # The one that matters. A stray right-click on an unselected row must
    # not delete a selection the user made earlier and forgot about.
    out = emitted(panel)
    select(panel, 0, 1, 2)
    target = panel._list.item(4)
    menu = panel._build_context_menu(target)
    assert "Delete Draft" in menu_labels(menu)

    next(a for a in menu.actions() if a.text() == "Delete Draft").trigger()
    assert out == [[rows_of(panel)[4]]]


def test_right_clicking_inside_the_selection_acts_on_all_of_it(panel):
    out = emitted(panel)
    select(panel, 0, 1, 2)
    menu = panel._build_context_menu(panel._list.item(1))
    next(a for a in menu.actions() if a.text().startswith("Delete")).trigger()
    assert out == [rows_of(panel)[0:3]]


def test_the_menu_asks_the_host_rather_than_deleting_anything(panel):
    # The panel holds no signer and no relay pool, so a menu that deleted
    # would be a menu that lied about what it can do.
    out = emitted(panel)
    select(panel, 0)
    menu = panel._build_context_menu(panel._list.item(0))
    next(a for a in menu.actions() if a.text() == "Delete Draft").trigger()
    assert out and panel._store is not None
    # Still there: removing the row is the host's answer, not the menu's.
    assert len(panel._store) == 5


def test_every_row_command_reaches_the_host(panel):
    """The menu's commands are carried on their actions and run by one
    slot of the panel (no closure over the panel on its children)."""
    asked = []
    panel.open_draft.connect(lambda i: asked.append(("open", i)))
    panel.publish_draft.connect(lambda i: asked.append(("publish", i)))
    panel.copy_event_id.connect(lambda i: asked.append(("copy", i)))
    item = panel._list.item(0)
    identifier = rows_of(panel)[0]
    menu = panel._build_context_menu(item)
    for text in ("Open in New Tab", "Publish…", "Copy Event ID"):
        next(a for a in menu.actions() if a.text() == text).trigger()
    assert asked == [("open", identifier), ("publish", identifier),
                     ("copy", identifier + "e")]
    assert QApplication.clipboard().text() == identifier + "e"


def test_a_panel_without_a_parent_is_freed_without_the_collector():
    """Left to the cycle collector, a panel without a parent can crash it
    (Shiboken hands its children back to Python one by one). Nothing the
    panel's children keep may hold the panel, its menus included."""
    import gc
    import weakref
    panel = build_panel()
    panel.add_view("published", "Published", "", QLabel("Published"))
    panel.show_view("published")
    panel.show_view(DraftsPanel.DRAFTS_VIEW)
    panel._build_context_menu(panel._list.item(0))
    ref = weakref.ref(panel)
    gc.collect()
    # As it was afterwards: the test run collects on its own (conftest).
    collecting = gc.isenabled()
    gc.disable()
    try:
        del panel
        assert ref() is None
    finally:
        if collecting:
            gc.enable()
