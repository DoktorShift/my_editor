# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Imports window: sources, their posts, and the open one.

The window is built on a real controller and a real inbox (in a
temporary folder); the network, the signer and the checks are faked.
It shows what the controller holds and remembers how it was left.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from nostr.imports.feed_list import source_key
from nostr.imports.inbox_store import INBOX, OLDER_POSTS, SKIPPED_POSTS
from nostr.imports.subscriptions import FeedSubscriptionStore
from nostr.imports_controller import ImportsController
from nostr.ui.imports_sidebar import HEADER, LIST, SOURCE
from nostr.ui.imports_window import ImportsWindow, freshness
from tests.imports_fakes import FakeCatalogue, FakeFetcher, TWO_ITEM_FEED, inline_run_blocking
from tests.imports_fakes import make_item
from tests.outbox_fakes import FakeRelayDirectory, settle
from tests.test_imports_subscriptions import FakeRelay, FakeScheduler, FakeSessionPool

PK = "ab" * 32
# The inbox dates posts by the real clock, so the posts here are too.
NOW = int(time.time())
JOURNAL = "https://journal.example/feed"
FIELD = "https://field.example/rss.xml"
AUTHOR = "https://example.com/feed"      # read when opened in these tests


class FakeChecker(QObject):
    checking = Signal(str, bool)
    source_checked = Signal(str, int)

    def start(self):
        pass

    def stop(self):
        pass

    def check_now(self, key):
        return False

    def is_checking(self, key):
        return False


class Images(QObject):
    ready = Signal(str)

    def image(self, url):
        return None

    def request(self, url):
        pass


def controller_for(tmp_path, *, manual=()):
    relay = FakeRelay()
    store = FeedSubscriptionStore(
        session_pool=FakeSessionPool(), relay_pool=None,
        relay_directory=FakeRelayDirectory(), cache_dir=tmp_path / "cache",
        query=relay, publisher=relay, scheduler=FakeScheduler(), clock=lambda: NOW)
    fetcher = FakeFetcher({AUTHOR: ("ok", TWO_ITEM_FEED)})
    controller = ImportsController(
        relay_pool=None, relay_directory=FakeRelayDirectory(), session_pool=None,
        config_dir=tmp_path, subscription_store=store, fetcher=fetcher, images=Images(),
        run_blocking=inline_run_blocking, checker_factory=lambda inbox: FakeChecker(),
        catalogue_factory=lambda inbox: FakeCatalogue())
    controller.account_changed(SimpleNamespace(user_pubkey=PK, bunker_relays=[],
                                               display_name="Ada"))
    settle()
    return controller


def fill(controller):
    """Two automatic sources with posts, one failing, one read when opened."""
    subscriptions, inbox = controller.subscriptions, controller.inbox
    subscriptions.add_feed(FIELD, "field notes")
    subscriptions.add_feed(JOURNAL, "A thoughtful journal")
    inbox._db.execute("UPDATE sources SET created_at = ?", (NOW - 30 * 86400,))
    inbox._db.commit()
    for url, titles in ((JOURNAL, ("Self-custody", "Small blogs")), (FIELD, ("Lisbon",))):
        inbox.ingest(source_key(url), [make_item("Archive", guid=f"{url}-old",
                                                 published_at=NOW - 60 * 86400)])
        inbox._db.execute("UPDATE sources SET last_checked = 1")
        inbox.ingest(source_key(url), [make_item(t, guid=f"{url}-{t}",
                                                 published_at=NOW - 3600 * (i + 1))
                                       for i, t in enumerate(titles)])
    inbox.record_failure(source_key(FIELD), "Couldn't reach that URL: Host not found.")
    controller._announce()


@pytest.fixture
def window(tmp_path):
    controller = controller_for(tmp_path)
    fill(controller)
    saved = {}
    win = ImportsWindow(controller, load_settings=lambda: {},
                        save_settings=lambda value: saved.update(value))
    win.saved = saved
    win.show()
    settle()
    yield win
    win.close()
    controller.account_changed(None)
    settle()


def entries(win):
    return win.sidebar.model_.entries()


def titles(win):
    return [win.posts.model_.post(r).title for r in range(win.posts.model_.rowCount())]


class TestSidebar:
    def test_the_lists_then_the_sources_sorted_by_title(self, window):
        rows = [(e.kind, e.title) for e in entries(window)]
        assert rows[:4] == [(LIST, "Inbox"), (LIST, "Older Posts"), (LIST, "Imported"),
                            (LIST, "Skipped")]
        assert rows[4:] == [(HEADER, "Sources"), (SOURCE, "A thoughtful journal"),
                            (SOURCE, "field notes")]

    def test_counts(self, window):
        counts = {e.key: e.count for e in entries(window) if e.kind == LIST}
        assert counts == {INBOX: 3, OLDER_POSTS: 2, "imported": 0, SKIPPED_POSTS: 0}
        inbox = entries(window)[0]
        assert inbox.emphasized

    def test_a_failed_source_says_why_and_when_it_is_tried_again(self, window):
        field = next(e for e in entries(window) if e.title == "field notes")
        assert field.failed
        assert "Host not found" in field.tooltip
        assert "Next try in" in field.tooltip

    def test_no_sources_no_header(self, tmp_path):
        controller = controller_for(tmp_path)
        win = ImportsWindow(controller)
        assert [e.kind for e in entries(win)] == [LIST] * 4
        assert win.placeholder_title.text() == "No sources yet"
        controller.account_changed(None)

    def test_header_rows_cannot_be_chosen_and_the_keys_pass_over_them(self, window):
        window.sidebar.setFocus()
        window.sidebar.select(LIST, SKIPPED_POSTS)
        QTest.keyClick(window.sidebar, Qt.Key.Key_Down)
        assert window.sidebar.chosen().kind == SOURCE


class TestLists:
    def test_the_inbox_first_and_its_first_post_open(self, window):
        assert window.list_title.text() == "Inbox"
        assert window.list_subtitle.text() == "3 new posts"
        assert titles(window) == ["Self-custody", "Lisbon", "Small blogs"]
        assert window.article.post is not None

    def test_a_source_shows_its_new_or_older_posts(self, window):
        window.sidebar.select(SOURCE, source_key(JOURNAL))
        assert window.segments.isVisibleTo(window)
        assert sorted(titles(window)) == ["Self-custody", "Small blogs"]
        window.segment_older.click()
        assert titles(window) == ["Archive"]
        assert window.source_menu_button.isVisibleTo(window)

    def test_a_source_read_when_opened(self, tmp_path):
        controller = controller_for(tmp_path)
        controller.subscriptions.add_feed("npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237"
                                          "lmu63q0uf63m", "An author")
        win = ImportsWindow(controller)
        key = source_key("npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m")
        assert win.sidebar.select(SOURCE, key)
        assert not win.segments.isVisibleTo(win)
        # The fake has nothing at that address: the error and Try Again.
        assert win.placeholder_title.text() == "Couldn't read this source"
        assert win.placeholder_button.text() == "Try Again"
        assert win.list_subtitle.text() == "MyEditor reads this source when you open it."
        controller.account_changed(None)

    def test_skipped_says_it_is_this_computer(self, window):
        window.sidebar.select(LIST, SKIPPED_POSTS)
        assert window.list_subtitle.text() == "Posts you skipped on this computer."
        assert window.placeholder_title.text() == "Nothing skipped"

    def test_up_to_date(self, window):
        for row in range(window.posts.model_.rowCount()):
            post = window.posts.model_.post(row)
            window._controller.inbox.skip(post.source_key, post.d_tag, post.revision)
        window._controller._announce()
        assert window.placeholder_title.text() == "You're up to date"


class TestSearch:
    def test_search_filters_and_clears_with_another_list(self, window):
        window.search.setText("lisbon")
        window._search_timer.timeout.emit()
        assert titles(window) == ["Lisbon"]
        window.search.setText("nothing like it")
        window._search_timer.timeout.emit()
        assert window.placeholder_title.text() == "No Results"
        window.sidebar.select(LIST, OLDER_POSTS)
        assert window.search.text() == ""
        assert len(titles(window)) == 2

    def test_find_focuses_the_field_and_escape_goes_back(self, window):
        find = QKeySequence(QKeySequence.StandardKey.Find)
        action = next(a for a in window.actions() if a.shortcut() == find)
        action.trigger()
        assert window.focusWidget() is window.search
        window.search.setText("x")
        QTest.keyClick(window.search, Qt.Key.Key_Escape)
        assert window.search.text() == ""
        assert window.focusWidget() is window.posts


class TestSelection:
    def test_the_band_counts_what_is_selected(self, window):
        model = window.posts.model_
        model.set_checked(0, True)
        assert window.select_all.text() == "1 of 3 selected"
        assert window.select_all.checkState() == Qt.CheckState.PartiallyChecked
        window.select_all.click()
        assert model.checked() == []
        window.select_all.click()
        assert len(model.checked()) == 3
        assert window.select_all.text() == "3 of 3 selected"


class TestLayout:
    def test_a_narrow_window_folds_the_sidebar_away_and_back(self, window):
        window.resize(1200, 700)
        settle()
        assert window.sidebar.isVisible()
        window.resize(800, 700)
        settle()
        assert not window.sidebar.isVisible()
        assert window.act_sidebar.text() == "Show Sidebar"
        window.resize(1200, 700)
        settle()
        assert window.sidebar.isVisible()

    def test_hiding_the_sidebar_is_remembered(self, window):
        window.act_sidebar.trigger()
        assert not window.sidebar.isVisible()
        assert window.saved["sidebar"] is False
        window.act_sidebar.trigger()
        assert window.saved["sidebar"] is True

    def test_the_list_shown_is_remembered_and_restored(self, window, tmp_path):
        window.sidebar.select(SOURCE, source_key(JOURNAL))
        assert window.saved["selection"] == [SOURCE, source_key(JOURNAL)]
        again = ImportsWindow(window._controller, load_settings=lambda: dict(window.saved))
        assert again.sidebar.chosen().key == source_key(JOURNAL)
        assert again.list_title.text() == "A thoughtful journal"
        again.close()

    def test_every_pane_keeps_a_minimum_width(self, window):
        assert not window.splitter.childrenCollapsible()
        assert window.sidebar.minimumWidth() >= 170
        assert window.article.minimumWidth() >= 360


def test_freshness_words():
    source = SimpleNamespace(automatic=True, error="", last_checked=NOW - 120, next_check=0)
    assert freshness(source, now=NOW) == "Checked 2 min ago."
    source.last_checked = NOW - 10
    assert freshness(source, now=NOW) == "Checked just now."
    source.last_checked = 0
    assert freshness(source, now=NOW) == "Not checked yet."
    failed = SimpleNamespace(automatic=True, error="Boom.", last_checked=0,
                             next_check=NOW + 7200)
    assert freshness(failed, now=NOW) == "Boom. Next try in 2 hours."
    manual = SimpleNamespace(automatic=False, error="", last_checked=0, next_check=0)
    assert freshness(manual, now=NOW) == "MyEditor reads this source when you open it."
