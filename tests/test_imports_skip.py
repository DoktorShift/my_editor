# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Skip, with Undo (the owner's decision Q-12).

Skip sets posts aside on this computer: the button, or Delete or
Backspace in the list. A message says how many, with Undo; Edit > Undo
(Command-Z or Ctrl+Z) undoes it too, unless the text being typed has
something to undo. In the Skipped list the button brings posts back.
The app's menu bar acts on the Imports window while it is in front.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from main_window import MainWindow
from nostr.imports.inbox_store import SKIPPED_POSTS
from nostr.ui.imports_sidebar import FILE, LIST
from nostr.ui.imports_window import ImportsWindow
from tests.outbox_fakes import settle
from tests.test_imports_create import Jobs, make_controller


@pytest.fixture
def window(tmp_path):
    controller = make_controller(tmp_path, Jobs())
    win = ImportsWindow(controller)
    win.resize(1180, 760)
    win.show()
    settle()
    yield win
    win.close()
    controller.account_changed(None)
    settle()


def titles(win):
    model = win.posts.model_
    return [model.post(r).title for r in range(model.rowCount())]


def undo_action(win):
    undo = QKeySequence(QKeySequence.StandardKey.Undo)
    return next(a for a in win.actions() if a.shortcut() == undo)


class TestSkip:
    def test_the_button_skips_the_open_post_and_says_so(self, window):
        first = titles(window)[0]
        assert window.action_bar.skip.text() == "Skip"
        window.action_bar.skip.click()
        assert first not in titles(window)
        assert window._controller.counts().skipped == 1
        assert window.banner.label.text() == "Skipped 1 post."
        assert window.banner.action.text() == "Undo"

    def test_undo_in_the_message(self, window):
        first = titles(window)[0]
        window.action_bar.skip.click()
        window.banner.action.click()
        assert first in titles(window)
        assert window._controller.counts().skipped == 0
        assert not window.banner.isVisibleTo(window)

    def test_delete_skips_the_checked_posts(self, window):
        model = window.posts.model_
        model.set_checked(0, True)
        model.set_checked(1, True)
        window.posts.setFocus()
        QTest.keyClick(window.posts, Qt.Key.Key_Delete)
        assert window._controller.counts().skipped == 2
        assert window.banner.label.text() == "Skipped 2 posts."
        assert model.checked() == []

    def test_edit_undo_undoes_the_skip(self, window):
        before = titles(window)
        window.posts.setFocus()
        QTest.keyClick(window.posts, Qt.Key.Key_Backspace)
        assert window.can_undo() and window.undo_text() == "Undo Skip"
        undo_action(window).trigger()
        assert titles(window) == before
        assert not window.can_undo()

    def test_undo_while_typing_undoes_the_typing(self, window):
        window.posts.setFocus()
        QTest.keyClick(window.posts, Qt.Key.Key_Delete)
        window.search.setFocus()
        QTest.keyClicks(window.search, "ab")
        assert window.undo_text() == "Undo"
        undo_action(window).trigger()
        assert window.search.text() != "ab"
        # The skip is still there to undo from the list.
        assert window._controller.counts().skipped == 1

    def test_restore_in_the_skipped_list(self, window):
        window.action_bar.skip.click()
        window.sidebar.select(LIST, SKIPPED_POSTS)
        assert window.action_bar.skip.text() == "Restore"
        window.action_bar.skip.click()
        assert window._controller.counts().skipped == 0
        assert window.banner.label.text() == "Restored 1 post."

    def test_files_are_not_skipped(self, window):
        from tests.imports_fakes import make_item
        collection = window._controller.open_posts(kind="file", label="blog.xml",
                                                   items=[make_item("One", guid="g1")])
        window.show_view(FILE, collection.id)
        assert window.action_bar.skip.isHidden()

    def test_a_window_that_only_shows_skips_nothing(self, window):
        window._controller.read_only = True
        window.skip_or_restore()
        assert window._controller.counts().skipped == 0


class TestTheMenuBar:
    """The app's menu bar acts on the Imports window while it is in front."""

    def stand_in(self, window):
        return SimpleNamespace(
            _imports_in_front=lambda: window, current_editor=lambda: None,
            current_pdf_viewer=lambda: None)

    def test_edit_undo(self, window):
        window.posts.setFocus()
        QTest.keyClick(window.posts, Qt.Key.Key_Delete)
        MainWindow._undo(self.stand_in(window))
        assert window._controller.counts().skipped == 0

    def test_edit_select_all_and_delete(self, window, monkeypatch):
        import main_window
        monkeypatch.setattr(main_window.QApplication, "focusWidget",
                            staticmethod(lambda: window.posts))
        MainWindow._edit_focused(self.stand_in(window), "select_all")
        assert len(window.posts.model_.checked()) == window.posts.model_.rowCount()
        MainWindow._edit_focused(self.stand_in(window), "delete")
        assert window._controller.counts().inbox == 0

    def test_find_and_close(self, window):
        closed = []
        window.close = lambda: closed.append(True)
        stand_in = self.stand_in(window)
        MainWindow._show_find(stand_in)
        assert window.focusWidget() is window.search
        MainWindow._close_current_tab(stand_in)
        assert closed == [True]

    def test_the_undo_item_says_what_it_undoes(self, window):
        from PySide6.QtGui import QAction
        stand_in = self.stand_in(window)
        stand_in.act_undo, stand_in.act_redo = QAction("Undo"), QAction("Redo")
        window.posts.setFocus()
        QTest.keyClick(window.posts, Qt.Key.Key_Delete)
        MainWindow._update_undo_redo_buttons(stand_in)
        assert stand_in.act_undo.text() == "Undo Skip"
        assert stand_in.act_undo.isEnabled()
        window.undo_skip()
        MainWindow._update_undo_redo_buttons(stand_in)
        assert stand_in.act_undo.text() == "Undo"
        assert not stand_in.act_undo.isEnabled()
