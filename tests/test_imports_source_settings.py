# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source Settings: one source's name, checks, defaults and Unsubscribe.

What the sheet shows comes from the controller; Done keeps what changed
(the name and the defaults go with the list of sources, whether checks
run stays on this computer), Cancel nothing; Check Now and Unsubscribe
act at once, Unsubscribe after asking.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from nostr.imports.checker import interval_for
from nostr.imports.feed_list import source_key
from nostr.imports.inbox_store import CHECK_INTERVAL, InboxStore
from nostr.ui.imports_sidebar import SOURCE
from nostr.ui.imports_source_settings import SourceSettingsSheet
from nostr.ui.imports_window import ImportsWindow
from tests.accessibility import unnamed_controls
from tests.outbox_fakes import settle
from tests.test_imports_controller import NOSTR_AUTHOR
from tests.test_imports_window import FIELD, JOURNAL, controller_for, entries, fill
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


@pytest.fixture
def controller(tmp_path):
    controller = controller_for(tmp_path)
    fill(controller)
    yield controller
    controller.account_changed(None)
    settle()


def sheet_for(controller, url, **kw):
    sheet = SourceSettingsSheet(controller, source_key(url), **kw)
    sheet.show()
    settle()
    return sheet


def published(controller):
    """What the list of sources says on the relays after a change."""
    store = controller.subscriptions
    store.flush()
    settle()
    return store._query.payload()


class TestWhatItShows:
    def test_a_feed(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        assert sheet.name.text() == "A thoughtful journal"
        assert sheet.title.full_text() == "A thoughtful journal"
        assert sheet.address.text() == JOURNAL
        assert sheet.website.text() == "journal.example"
        assert sheet.status.text().startswith("Checked ")
        assert sheet.automatic.isVisibleTo(sheet) and sheet.automatic.isChecked()
        assert sheet.check_now_button.isVisibleTo(sheet)
        assert sheet.copy_images.isChecked() and sheet.full_text.isChecked()
        assert unnamed_controls(sheet) == []

    def test_a_failed_check_says_why_and_when_again(self, controller):
        sheet = sheet_for(controller, FIELD)
        assert sheet.status.text().startswith("Couldn't reach that URL")
        assert "Next try in" in sheet.status.text()

    def test_a_source_read_when_opened_has_no_checks(self, controller):
        controller.subscriptions.add_feed(NOSTR_AUTHOR, "An author")
        sheet = sheet_for(controller, NOSTR_AUTHOR)
        assert sheet.status.text() == "MyEditor reads this source when you open it."
        assert not sheet.automatic.isVisibleTo(sheet)
        assert not sheet.check_now_button.isVisibleTo(sheet)
        assert not sheet.website.isVisibleTo(sheet)      # no website to open
        assert unnamed_controls(sheet) == []

    def test_the_name_follows_what_is_typed(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        sheet.name.setText("My favourite journal")
        assert sheet.title.full_text() == "My favourite journal"
        sheet.name.clear()      # its own title again (the feed's, else its address)
        assert sheet.title.full_text() in ("A thoughtful journal", JOURNAL)

    def test_the_website_opens(self, controller):
        opened = []
        sheet = sheet_for(controller, JOURNAL, open_url=lambda url: opened.append(url.toString()))
        sheet.website.click()
        assert opened == ["https://journal.example"]


class TestDoneAndCancel:
    def test_done_keeps_the_name_and_defaults_for_every_device(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        sheet.name.setText("Journal")
        sheet.copy_images.setChecked(False)
        sheet.buttons["done"].click()
        settle()
        assert controller.subscriptions.get(JOURNAL).title == "Journal"
        assert controller.source(source_key(JOURNAL)).display_title == "Journal"
        options = controller.subscriptions.options_for(JOURNAL)
        assert (options.rehost_images, options.fetch_full_text) == (False, True)
        payload = published(controller)
        assert payload["sourceOptions"][source_key(JOURNAL)] == {
            "rehostImages": False, "fetchFullText": True}
        assert any(f.get("title") == "Journal" for f in payload["feeds"])

    def test_done_with_nothing_changed_sends_nothing(self, controller):
        """CP3 review L4: Done wrote the defaults the sheet showed back as a
        change, and the list went out again (a signer request)."""
        before = published(controller)
        sent = len(controller.subscriptions._query.published)
        sheet = sheet_for(controller, JOURNAL)
        sheet.buttons["done"].click()
        assert published(controller) == before
        assert len(controller.subscriptions._query.published) == sent

    def test_turning_checks_off_stays_on_this_computer(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        sheet.automatic.setChecked(False)
        sheet.buttons["done"].click()
        source = controller.source(source_key(JOURNAL))
        assert source.paused
        assert all(s.key != source.key for s in controller.inbox.due_sources(10))
        payload = published(controller)
        assert "paused" not in str(payload)
        # And back on.
        sheet = sheet_for(controller, JOURNAL)
        assert not sheet.automatic.isChecked()
        assert sheet.status.text().startswith("Not checked automatically")
        sheet.automatic.setChecked(True)
        sheet.buttons["done"].click()
        assert not controller.source(source_key(JOURNAL)).paused

    def test_cancel_keeps_nothing(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        sheet.name.setText("Something else")
        sheet.automatic.setChecked(False)
        sheet.full_text.setChecked(False)
        sheet.buttons["cancel"].click()
        assert controller.subscriptions.get(JOURNAL).title == "A thoughtful journal"
        assert not controller.source(source_key(JOURNAL)).paused
        assert controller.subscriptions.options_for(JOURNAL).fetch_full_text


class TestCheckNow:
    def test_too_soon_says_when_for_that_minute(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        controller.checker.check_now = lambda key: False
        sheet.check_now()
        assert sheet.status.text() == "You can check again in a minute."
        assert sheet._throttle.interval() == 60_000
        sheet._throttle.stop()
        sheet._throttle.timeout.emit()        # the minute is over
        assert sheet.status.text().startswith("Checked ")

    def test_under_way_says_so_and_waits(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        controller.checker.is_checking = lambda key: True
        controller.sources_changed.emit()
        assert sheet.status.text() == "Checking for new posts…"
        assert not sheet.check_now_button.isEnabled()

    def test_a_source_not_checked_automatically_can_still_be_checked(self, tmp_path):
        store = InboxStore(":memory:", clock=lambda: 1_800_000_000)
        store.sync_sources([("k", "https://a.example/feed", "A", True)])
        store.set_paused("k", True)
        assert store.due_sources(5) == []
        assert not store.claim("k")                 # never on its own
        assert store.claim("k", manual=True)        # but when asked

    def test_sources_with_checks_off_do_not_stretch_the_interval(self):
        """Q-8: above 50 checked sources the interval grows; a source whose
        checks are off adds no checks, so it does not count."""
        from nostr.imports.checker import FeedChecker
        store = InboxStore(":memory:", clock=lambda: 1_800_000_000)
        store.sync_sources([(f"k{i}", f"https://s{i}.example/feed", "", True)
                            for i in range(51)])
        checker = FeedChecker(store=store, fetcher=None)
        assert checker._interval() == interval_for(51) == 2 * CHECK_INTERVAL
        store.set_paused("k0", True)
        assert checker._interval() == interval_for(50) == CHECK_INTERVAL


class TestUnsubscribe:
    def test_it_asks_first(self, controller):
        asked = []
        sheet = sheet_for(controller, JOURNAL, confirm=lambda **kw: asked.append(kw) or False)
        sheet.buttons["unsubscribe"].click()
        assert asked == [dict(title="Unsubscribe from “A thoughtful journal”?",
                              message="Its posts leave Imports. Drafts you already created "
                                      "stay.",
                              action="Unsubscribe")]
        assert controller.is_followed(JOURNAL)
        assert sheet.isVisible()

    def test_yes_unfollows_and_closes(self, controller):
        gone = []
        sheet = sheet_for(controller, JOURNAL, confirm=lambda **kw: True)
        sheet.unsubscribed.connect(gone.append)
        finished = []
        sheet.finished.connect(finished.append)
        sheet.buttons["unsubscribe"].click()
        assert not controller.is_followed(JOURNAL)
        assert controller.source(source_key(JOURNAL)) is None
        assert gone == [source_key(JOURNAL)]
        assert not sheet.isVisible()
        assert len(finished) == 1           # closed once, not twice

    def test_unfollowed_elsewhere_closes_it(self, controller):
        sheet = sheet_for(controller, JOURNAL)
        controller.subscriptions.remove_feed(JOURNAL)
        settle()
        assert not sheet.isVisible()


class TestFromTheWindow:
    @pytest.fixture
    def window(self, controller):
        win = ImportsWindow(controller, load_settings=lambda: {},
                            save_settings=lambda value: None)
        win.show()
        settle()
        yield win
        win.close()

    def test_the_source_menu_opens_it(self, window):
        entry = next(e for e in entries(window) if e.kind == SOURCE and e.url == JOURNAL)
        menu = QMenu()
        menu.triggered.connect(window._on_source_command)
        window._source_actions(menu, entry)
        labels = [a.text() for a in menu.actions() if a.text()]
        assert labels[-2:] == ["Source Settings…", "Unsubscribe…"]
        next(a for a in menu.actions() if a.text() == "Source Settings…").trigger()
        assert isinstance(window.sheet(), SourceSettingsSheet)
        window.sheet().reject()
        settle()
        assert window.sheet() is None

    def test_a_source_with_checks_off_shows_a_pause(self, window, controller):
        controller.save_settings(source_key(JOURNAL), name="", checking=False,
                                 copy_images=True, full_text=True)
        settle()
        entry = next(e for e in entries(window) if e.kind == SOURCE and e.url == JOURNAL)
        assert entry.paused
        model = window.sidebar.model_
        text = model.data(model.index(model.row_of(SOURCE, entry.key)),
                          Qt.ItemDataRole.AccessibleTextRole)
        assert "Not checked automatically" in text
