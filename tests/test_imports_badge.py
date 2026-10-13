# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""New posts on the app icon (C13, the owner's decision Q-7).

Off by default; one setting, offered once in the Imports window when new
posts first arrive; when on, the app icon carries the Inbox count, and 0
takes it away.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QAction

from nostr.imports.feed_list import source_key
from nostr.ui.imports_badge import OFFER, AppIconBadge
from tests.imports_fakes import make_item
from tests.outbox_fakes import settle
from tests.test_imports_window import controller_for
from tests.widget_lifetime import delete_new_windows

FEED = "https://blog.example/feed"


@pytest.fixture(autouse=True)
def _windows_deleted():
    yield from delete_new_windows()


class Kept:
    """settings.json, for one key."""

    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def load(self):
        return self.value

    def save(self, value):
        self.value = value
        self.saved.append(value)


def with_new_posts(controller, n=2):
    controller.subscriptions.add_feed(FEED, "Blog")
    inbox = controller.inbox
    inbox.ingest(source_key(FEED), [make_item("Archive", guid="old")])     # the baseline
    inbox._db.execute("UPDATE sources SET last_checked = 1")
    inbox.ingest(source_key(FEED), [make_item(f"New {i}", guid=f"g{i}") for i in range(n)])
    controller.inbox_count_changed.emit(controller.counts().inbox)


def badge_for(controller, kept=None):
    shown = []
    kept = kept or Kept()
    badge = AppIconBadge(controller, load=kept.load, save=kept.save, show=shown.append)
    return badge, shown, kept


class TestTheNumber:
    def test_off_by_default(self, tmp_path):
        controller = controller_for(tmp_path)
        badge, shown, kept = badge_for(controller)
        with_new_posts(controller)
        assert not badge.on and shown == [0]
        assert badge.should_offer() and kept.saved == []
        controller.account_changed(None)

    def test_on_it_is_the_inbox_count(self, tmp_path):
        controller = controller_for(tmp_path)
        badge, shown, kept = badge_for(controller)
        with_new_posts(controller, 2)
        badge.set_on(True)
        assert shown[-1] == 2
        assert kept.value == {"on": True, "offered": True}
        controller.inbox_count_changed.emit(1)     # one imported or skipped
        assert shown[-1] == 1
        controller.account_changed(None)           # signed out: nothing on the icon
        assert shown[-1] == 0
        controller_again = controller_for(tmp_path)
        again, shown_again, _ = badge_for(controller_again, kept)     # the next run
        assert again.on and shown_again == [2]
        controller_again.account_changed(None)

    def test_turned_off_it_goes(self, tmp_path):
        controller = controller_for(tmp_path)
        badge, shown, kept = badge_for(controller, Kept({"on": True, "offered": True}))
        with_new_posts(controller, 3)
        assert shown[-1] == 3
        badge.set_on(False)
        assert shown[-1] == 0 and kept.value["on"] is False
        assert not badge.should_offer()            # never offered again
        controller.account_changed(None)


class TestTheOffer:
    def window(self, tmp_path, kept=None):
        from nostr.ui.imports_window import ImportsWindow
        controller = controller_for(tmp_path)
        badge, shown, kept = badge_for(controller, kept)
        window = ImportsWindow(controller, badge=badge)
        window.show()
        settle()
        return window, controller, badge, shown, kept

    def test_offered_once_when_new_posts_arrive(self, tmp_path):
        window, controller, badge, shown, kept = self.window(tmp_path)
        assert window.banner.isHidden()            # nothing new yet
        with_new_posts(controller)
        controller.new_posts.emit(source_key(FEED), 2)
        assert not window.banner.isHidden() and window.banner.label.text() == OFFER
        assert kept.value == {"offered": True}
        window.banner.action.click()
        assert badge.on and shown[-1] == 2
        assert window.banner.isHidden()
        controller.new_posts.emit(source_key(FEED), 1)
        assert window.banner.isHidden()            # once
        window.close()
        controller.account_changed(None)

    def test_not_now_is_closing_it(self, tmp_path):
        window, controller, badge, shown, kept = self.window(tmp_path)
        with_new_posts(controller)
        controller.new_posts.emit(source_key(FEED), 2)
        window.banner.close_button.click()
        assert not badge.on and not badge.should_offer()
        controller.new_posts.emit(source_key(FEED), 1)
        assert window.banner.isHidden()
        window.close()
        controller.account_changed(None)

    def test_posts_that_came_while_it_was_closed_count(self, tmp_path):
        from nostr.ui.imports_window import ImportsWindow
        controller = controller_for(tmp_path)
        with_new_posts(controller)
        badge, _shown, _kept = badge_for(controller)
        window = ImportsWindow(controller, badge=badge)
        window.show()
        settle()
        assert window.banner.label.text() == OFFER
        window.close()
        controller.account_changed(None)

    def test_never_over_another_message(self, tmp_path):
        window, controller, badge, _shown, _kept = self.window(tmp_path)
        window.banner.say("Skipped 1 post.", action="Undo", key="undo")
        with_new_posts(controller)
        controller.new_posts.emit(source_key(FEED), 2)
        assert window.banner.label.text() == "Skipped 1 post."
        assert badge.should_offer()                # still to be offered
        window.close()
        controller.account_changed(None)


def test_the_menu_item_follows_the_setting_without_choosing():
    from types import SimpleNamespace

    from main_window import MainWindow
    action = QAction()
    action.setCheckable(True)
    chosen = []
    action.toggled.connect(chosen.append)
    host = SimpleNamespace(act_imports_badge=action)
    MainWindow._on_imports_badge_changed(host, True)
    assert action.isChecked() and chosen == []


def test_the_system_badge_is_set_on_the_application():
    """QGuiApplication.setBadgeNumber is the application's own (the
    full-window tests found the class's call refused)."""
    from nostr.ui import imports_badge
    imports_badge._system_badge(3)
    imports_badge._system_badge(0)
