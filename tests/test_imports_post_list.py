# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The list of posts: pages, checks, words and keys.

A long list loads a page at a time as it scrolls; checks survive a
reload as long as their posts can still be chosen; the word after a
title says what became of a post unless the list already says it; and
the keys are Mail's, Finder's and Reminders': Up and Down move, Space
checks, Command-A or Ctrl+A checks all, Escape unchecks, Delete or
Backspace asks to skip. A click on a check box checks without opening.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from nostr.imports.inbox_store import DRAFTED, NEW, SKIPPED
from nostr.imports.workspace import INBOX_ORIGIN, Post
from nostr.ui.imports_post_list import PostDelegate, PostList, PostListModel
from tests.outbox_fakes import settle
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


def post(n, state=NEW, image=""):
    return Post(key=f"k{n:03d}", origin=INBOX_ORIGIN, source_key="s",
                source_title="Field notes", source_url="https://s.example/feed",
                d_tag=f"rss-{n:03d}", title=f"Post {n}", excerpt="An excerpt of the post",
                image=image, link="", author="", published_at=1000 - n, found_at=1000,
                read_minutes=3, image_count=2, state=state)


class Pages:
    """A stored list served a page at a time, like the controller does."""

    def __init__(self, posts, size=50):
        self.posts = list(posts)
        self.size = size
        self.asked = []

    def __call__(self, after):
        self.asked.append(after.key if after else None)
        start = 0 if after is None else next(
            i for i, p in enumerate(self.posts) if p.key == after.key) + 1
        return self.posts[start:start + self.size]


class Images(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.requested = []

    def image(self, url):
        return None

    def request(self, url, size=None, *, urgent=False):
        self.requested.append(url)


@pytest.fixture
def view():
    widget = PostList(images=Images())
    widget.resize(420, 600)
    widget.show()
    yield widget
    widget.close()


class TestPaging:
    def test_a_page_at_a_time(self):
        model = PostListModel()
        pages = Pages([post(n) for n in range(120)])
        model.show(pages, paged=True)
        assert model.rowCount() == 50
        assert model.canFetchMore()
        model.fetchMore()
        model.fetchMore()
        assert model.rowCount() == 120
        assert not model.canFetchMore()
        assert pages.asked == [None, "k049", "k099"]

    def test_a_held_list_comes_whole(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1), post(2)], paged=False)
        assert model.rowCount() == 2
        assert not model.canFetchMore()

    def test_scrolling_to_the_end_loads_more(self, view):
        pages = Pages([post(n) for n in range(80)])
        view.model_.show(pages, paged=True)
        view.scrollToBottom()
        settle()
        assert view.model_.rowCount() == 80


class TestChecks:
    def test_check_uncheck_and_the_three_states(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1), post(2)], paged=False)
        assert model.check_state() == Qt.CheckState.Unchecked
        model.toggle(0)
        assert [p.key for p in model.checked()] == ["k001"]
        assert model.check_state() == Qt.CheckState.PartiallyChecked
        model.check_all()
        assert model.check_state() == Qt.CheckState.Checked
        model.clear_checks()
        assert model.checked() == []

    def test_what_cannot_be_imported_cannot_be_checked(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1, DRAFTED), post(2)], paged=False)
        model.set_checked(0, True)
        assert model.checked() == []
        model.set_progress(busy=["rss-002"], failed=[])
        model.set_checked(1, True)
        assert model.checked() == []
        model.check_all()
        assert model.checked() == []

    def test_check_all_loads_the_whole_list(self):
        model = PostListModel()
        model.show(Pages([post(n) for n in range(70)]), paged=True)
        model.check_all()
        assert len(model.checked()) == 70

    def test_a_reload_keeps_checks_that_still_apply(self):
        posts = [post(1), post(2), post(3)]
        model = PostListModel()
        model.show(lambda after: [] if after else list(posts), paged=False)
        model.check_all()
        posts[1] = replace(posts[1], state=DRAFTED)     # imported meanwhile
        del posts[2]                                     # gone
        model.reload()
        assert [p.key for p in model.checked()] == ["k001"]


class TestWords:
    def test_the_list_does_not_repeat_its_own_word(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1, SKIPPED), post(2, DRAFTED)],
                   paged=False, hidden_word="Skipped")
        assert model.word_for(model.post(0)) == ""
        assert model.word_for(model.post(1)) == "Imported"

    def test_importing_and_failed(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1), post(2)], paged=False,
                   hidden_word="New")
        model.set_progress(busy=["rss-001"], failed=["rss-002"])
        assert model.word_for(model.post(0)) == "Importing"
        assert model.word_for(model.post(1)) == "Failed"

    def test_a_screen_reader_hears_the_row_and_its_check(self):
        """Review M11: a checked post was only "selected" in its name, the
        word the open row has too; the check is now the item's state."""
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1)], paged=False)
        index = model.index(0)
        assert model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable
        assert model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
        model.toggle(0)
        text = model.data(index, Qt.ItemDataRole.AccessibleTextRole)
        assert text.startswith("Post 1, Field notes")
        assert "selected" not in text
        assert model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        # Assistive technology checks it the same way.
        assert model.setData(index, Qt.CheckState.Unchecked.value,
                             Qt.ItemDataRole.CheckStateRole)
        assert model.checked() == []

    def test_a_post_that_cannot_be_chosen_is_not_checkable(self):
        model = PostListModel()
        model.show(lambda after: [] if after else [post(1)], paged=False)
        model.set_progress(busy=[model.post(0).d_tag], failed=[])
        assert not model.flags(model.index(0)) & Qt.ItemFlag.ItemIsUserCheckable


class TestKeys:
    def _three(self, view):
        view.model_.show(lambda after: [] if after else [post(1), post(2), post(3)],
                         paged=False)
        view.setFocus()
        view.setCurrentIndex(view.model_.index(0))

    def test_up_down_and_space(self, view):
        self._three(view)
        QTest.keyClick(view, Qt.Key.Key_Down)
        assert view.current_post().key == "k002"
        QTest.keyClick(view, Qt.Key.Key_Space)
        assert [p.key for p in view.model_.checked()] == ["k002"]
        QTest.keyClick(view, Qt.Key.Key_Space)
        assert view.model_.checked() == []

    def test_select_all_checks_every_post_and_escape_unchecks(self, view):
        self._three(view)
        keys = QKeySequence(QKeySequence.StandardKey.SelectAll)[0]
        QTest.keyClick(view, keys.key(), keys.keyboardModifiers())
        assert len(view.model_.checked()) == 3
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert view.model_.checked() == []

    @pytest.mark.parametrize("key", [Qt.Key.Key_Delete, Qt.Key.Key_Backspace])
    def test_delete_and_backspace_ask_to_skip(self, view, key):
        self._three(view)
        asked = []
        view.skip_requested.connect(lambda: asked.append(True))
        QTest.keyClick(view, key)
        assert asked == [True]

    def test_the_open_post_follows_the_current_row(self, view):
        opened = []
        view.open_post.connect(lambda p: opened.append(p.key if p else None))
        self._three(view)
        QTest.keyClick(view, Qt.Key.Key_Down)
        assert opened[-1] == "k002"


class TestMouse:
    def test_a_click_on_the_box_checks_without_opening(self, view):
        view.model_.show(lambda after: [] if after else [post(1), post(2)], paged=False)
        view.setCurrentIndex(view.model_.index(0))
        rect = view.visualRect(view.model_.index(1))
        box = PostDelegate.check_rect(rect).center()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=box)
        assert [p.key for p in view.model_.checked()] == ["k002"]
        assert view.current_post().key == "k001"

    def test_a_click_on_the_row_opens_it(self, view):
        view.model_.show(lambda after: [] if after else [post(1), post(2)], paged=False)
        view.setCurrentIndex(view.model_.index(0))
        rect = view.visualRect(view.model_.index(1))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rect.center().x(), rect.center().y()))
        assert view.current_post().key == "k002"
        assert view.model_.checked() == []


def test_a_cover_is_asked_for_when_its_row_is_drawn(view):
    view.model_.show(lambda after: [] if after else [post(1, image="https://i.example/c.png")],
                     paged=False)
    view.grab()
    assert "https://i.example/c.png" in view.itemDelegate()._images.requested
