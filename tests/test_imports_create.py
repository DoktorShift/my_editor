# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Creating drafts from the Imports window.

The actions apply to the checked posts, or to the open one when none is
checked (Mail's rule). Each source's defaults for new drafts apply
unless the person chose otherwise for this run; before anything is
made, the window says how long an imported draft is kept and how often
a signer app may ask. One import runs at a time.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QPushButton, QWidget

from nostr.imports.feed_list import source_key
from nostr.imports.subscriptions import FeedSubscriptionStore
from nostr.imports_controller import ImportsController
from nostr.ui.flow_layout import FlowLayout
from nostr.ui.imports_actions import EXPIRY_NOTE, create_label, signer_note
from nostr.ui.imports_window import ImportsWindow
from tests.accessibility import unnamed_controls
from tests.imports_fakes import FakeCatalogue, FakeFetcher, inline_run_blocking
from tests.outbox_fakes import FakeRelayDirectory, settle
from tests.test_imports_jobs import FakeItemJob
from tests.test_imports_subscriptions import FakeRelay, FakeScheduler, FakeSessionPool
from tests.test_imports_window import NOW, FakeChecker, Images, fill, JOURNAL, FIELD

PK = "ab" * 32


class Jobs:
    """Item jobs that finish at once, or wait (``hold``) to be played."""

    def __init__(self):
        self.started, self.held, self.kwargs, self.script = [], [], [], {}
        self.resent = []

    def factory(self, **kwargs):
        self.kwargs.append(kwargs)
        return FakeItemJob(self, kwargs["items"], kwargs["is_imported"], kwargs)


def make_controller(tmp_path, jobs, *, signer="remote", catalogue=None,
                    entitled_relays=None):
    relay = FakeRelay()
    store = FeedSubscriptionStore(
        session_pool=FakeSessionPool(), relay_pool=None,
        relay_directory=FakeRelayDirectory(), cache_dir=tmp_path / "cache",
        query=relay, publisher=relay, scheduler=FakeScheduler(), clock=lambda: NOW)
    controller = ImportsController(
        relay_pool=None, relay_directory=FakeRelayDirectory(), session_pool=None,
        config_dir=tmp_path, subscription_store=store, fetcher=FakeFetcher({}),
        images=Images(), run_blocking=inline_run_blocking,
        checker_factory=lambda inbox: FakeChecker(),
        catalogue_factory=lambda inbox: catalogue or FakeCatalogue(),
        item_job_factory=jobs.factory, entitled_relays=entitled_relays)
    controller.account_changed(SimpleNamespace(user_pubkey=PK, bunker_relays=[],
                                               display_name="Ada", signer=signer))
    settle()
    fill(controller)
    return controller


@pytest.fixture
def jobs():
    return Jobs()


@pytest.fixture
def window(tmp_path, jobs):
    controller = make_controller(tmp_path, jobs)
    win = ImportsWindow(controller)
    win.resize(1180, 760)
    win.show()
    settle()
    yield win
    win.close()
    controller.account_changed(None)
    settle()


def bar(win):
    return win.action_bar


class TestTheBar:
    def test_the_open_post_when_none_is_checked(self, window):
        assert window.action_bar.isVisibleTo(window)
        assert bar(window).create.text() == "Create Draft"
        assert bar(window).create.isEnabled()

    def test_the_checked_posts(self, window):
        model = window.posts.model_
        model.set_checked(0, True)
        model.set_checked(1, True)
        assert bar(window).create.text() == "Create 2 Drafts"

    def test_how_long_an_imported_draft_is_kept_is_said_here(self, window):
        assert bar(window).note.text().startswith(EXPIRY_NOTE)
        assert EXPIRY_NOTE == "Imported drafts you don't change are removed after 90 days."

    def test_a_signer_app_may_ask_once_per_draft_and_image(self, window):
        model = window.posts.model_
        model.set_checked(0, True)
        model.set_checked(1, True)
        assert "Your signer may ask up to 2 times" in bar(window).note.text()

    def test_a_key_kept_here_never_asks(self, tmp_path, jobs):
        controller = make_controller(tmp_path, jobs, signer="local")
        win = ImportsWindow(controller)
        assert bar(win).note.text() == EXPIRY_NOTE
        controller.account_changed(None)

    def test_nothing_to_import_no_bar(self, window):
        window.sidebar.select("list", "imported")
        assert not window.action_bar.isVisibleTo(window)


class TestCreating:
    def test_create_makes_drafts_of_the_targets(self, window, jobs):
        model = window.posts.model_
        model.set_checked(0, True)
        model.set_checked(2, True)
        chosen = [model.post(0).title, model.post(2).title]
        bar(window).create.click()
        settle()
        assert sorted(kw["items"][0].title for kw in jobs.kwargs) == sorted(chosen)
        # They are drafts now: Imported, no longer in the Inbox.
        controller = window._controller
        assert controller.counts().imported == 2
        assert model.checked() == []

    def test_each_source_keeps_its_defaults(self, window, jobs):
        controller = window._controller
        controller.subscriptions.set_source_options(JOURNAL, rehost_images=False)
        journal = next(r for r in range(window.posts.model_.rowCount())
                       if window.posts.model_.post(r).source_key == source_key(JOURNAL))
        field = next(r for r in range(window.posts.model_.rowCount())
                     if window.posts.model_.post(r).source_key == source_key(FIELD))
        window.posts.model_.set_checked(journal, True)
        window.posts.model_.set_checked(field, True)
        window.create_drafts()
        settle()
        copies = {kw["items"][0].title: kw["rehost_images"] for kw in jobs.kwargs}
        assert sorted(copies.values()) == [False, True]

    def test_a_choice_for_this_run_applies_to_all(self, window, jobs):
        controller = window._controller
        controller.subscriptions.set_source_options(JOURNAL, rehost_images=False)
        model = window.posts.model_
        for row in range(model.rowCount()):
            model.set_checked(row, True)
        window._show_options()
        popover = window.options_popover
        assert popover.copy.checkState() == Qt.CheckState.PartiallyChecked
        assert not popover.mixed.isHidden()
        popover.copy.click()
        assert popover.copy.checkState() == Qt.CheckState.Checked
        popover.hide()
        window.create_drafts()
        settle()
        assert {kw["rehost_images"] for kw in jobs.kwargs} == {True}

    def test_one_import_at_a_time(self, window, jobs):
        jobs.script = {window.posts.model_.post(0).title: "hold"}
        window.create_drafts()
        settle()
        assert jobs.held
        # The post being imported says so and cannot be chosen again.
        model = window.posts.model_
        assert model.word_for(model.post(0)) == "Importing"
        assert bar(window).create.text() == "Create Draft"
        assert not bar(window).create.isEnabled()
        # Another post waits until this import is done.
        window.posts.setCurrentIndex(model.index(1))
        assert not bar(window).create.isEnabled()
        assert bar(window).create.toolTip() == "Pause the current import before starting another."
        jobs.held[0].play("ok")
        settle()
        assert bar(window).create.isEnabled()

    def test_command_return_creates(self, window, jobs):
        shortcut = QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Return)
        action = next(a for a in window.actions() if a.shortcut() == shortcut)
        action.trigger()
        settle()
        assert len(jobs.kwargs) == 1

    def test_a_window_that_only_shows_creates_nothing(self, tmp_path, jobs):
        controller = make_controller(tmp_path, jobs)
        controller.read_only = True
        win = ImportsWindow(controller)
        win.create_drafts()
        assert jobs.kwargs == []
        assert not bar(win).create.isEnabled()
        controller.account_changed(None)


class TestController:
    def test_defaults_same_and_mixed(self, window):
        controller = window._controller
        posts = [window.posts.model_.post(r) for r in range(window.posts.model_.rowCount())]
        assert controller.defaults_for(posts) == (True, True)
        controller.subscriptions.set_source_options(FIELD, fetch_full_text=False)
        assert controller.defaults_for(posts) == (True, None)
        assert controller.defaults_for([]) == (True, True)

    def test_signer_prompts(self, window):
        controller = window._controller
        post = window.posts.model_.post(0)
        with_images = post.__class__(**{**post.__dict__, "image_count": 3})
        assert controller.signer_prompts([with_images], copy_images=True) == 4
        assert controller.signer_prompts([with_images], copy_images=False) == 1


def test_the_bar_and_its_options_have_names(window):
    window._show_options()
    assert unnamed_controls(window.action_bar) == []
    assert unnamed_controls(window.options_popover) == []
    window.options_popover.hide()


def test_words():
    assert create_label(0) == "Create Draft"
    assert create_label(1) == "Create Draft"
    assert create_label(5) == "Create 5 Drafts"
    assert signer_note(0) == ""
    assert signer_note(1).startswith("Your signer may ask once.")


def test_the_expiry_sentence_is_said_in_one_place():
    # Q-11: said once, where drafts are created.
    root = pathlib.Path(__file__).resolve().parent.parent
    places = [path for path in root.rglob("*.py")
              if ".venv" not in path.parts and "tests" not in path.parts
              and "removed after 90 days" in path.read_text(encoding="utf-8")]
    assert [p.name for p in places] == ["imports_actions.py"]


class TestFlowLayout:
    def make(self, widths, *, trailing=True):
        host = QWidget()
        layout = FlowLayout(host, spacing=8, trailing=trailing)
        buttons = []
        for width in widths:
            button = QPushButton("x")
            button.setFixedSize(QSize(width, 24))
            layout.addWidget(button)
            buttons.append(button)
        return host, layout, buttons

    def test_one_row_when_it_fits(self):
        _host, layout, _buttons = self.make([80, 90, 120])
        assert [len(row) for row in layout.rows(400)] == [3]
        assert layout.heightForWidth(400) == 24

    def test_wraps_instead_of_widening(self):
        host, layout, buttons = self.make([80, 90, 120])
        assert [len(row) for row in layout.rows(200)] == [2, 1]
        assert layout.heightForWidth(200) == 24 + 8 + 24
        # Its narrowest width is the widest button's, not the sum.
        assert layout.minimumSize().width() == 120

    def test_rows_end_at_the_trailing_edge(self):
        host, layout, buttons = self.make([80, 90])
        layout.setGeometry(host.rect().adjusted(0, 0, 400 - host.width(), 0))
        assert buttons[1].geometry().right() == 399
        assert buttons[0].geometry().right() + 8 + 1 == buttons[1].geometry().left()

    def test_hidden_widgets_take_no_room(self):
        _host, layout, buttons = self.make([80, 90, 120])
        buttons[1].hide()
        assert layout.sizeHint().width() == 80 + 8 + 120


class TestWhatExistsAlready:
    """Importing again never overwrites a draft (T-M3-1): what exists is
    asked first, and nothing of it is signed again."""

    def test_an_imported_post_is_left_alone(self, tmp_path, jobs):
        from nostr.imports.snapshots import identifier_of
        controller = make_controller(tmp_path, jobs)
        win = ImportsWindow(controller)
        model = win.posts.model_
        first, second = model.post(0), model.post(1)
        controller.catalogue.existing = {first.d_tag: "drafted"}
        model.set_checked(0, True)
        model.set_checked(1, True)
        win.create_drafts()
        settle()
        assert [identifier_of(kw["items"][0]) for kw in jobs.kwargs] == [second.d_tag]
        assert win.banner.label.text() == (
            "Import finished. 1 draft created. 1 was already there.")
        controller.account_changed(None)

    def test_no_answer_means_nothing_is_signed(self, tmp_path, jobs):
        catalogue = FakeCatalogue(unavailable="Couldn't check your existing drafts.")
        controller = make_controller(tmp_path, jobs, catalogue=catalogue)
        win = ImportsWindow(controller)
        win.create_drafts()
        settle()
        assert jobs.kwargs == []
        job = controller.activity()
        assert (job.status, job.error) == ("paused", "Couldn't check your existing drafts.")
        win._show_job_card()
        assert "Couldn't check your existing drafts." in win.job_card.problem.text()
        win.job_card.hide()
        controller.account_changed(None)


def test_imported_drafts_go_where_the_editors_drafts_go(tmp_path, jobs):
    # A membership relay is one of the account's private relays: the
    # import hands it to its drafts as the editor does.
    controller = make_controller(tmp_path, jobs,
                                 entitled_relays=lambda: ["wss://members.example"])
    job = controller._make_item_job(items=[], feed_url="", fetch_full_text=True,
                                    rehost_images=False, skip_image_urls=set(),
                                    is_imported=lambda _d: False, parent=None)
    assert job._entitled_relays == ["wss://members.example"]
    controller.account_changed(None)


def test_the_review_shows_what_the_import_copies(window, monkeypatch):
    from nostr.imports import snapshots
    from nostr.ui.image_review_dialog import ImageReviewDialog
    controller = window._controller
    post = window.posts.model_.post(0)
    import dataclasses
    item = dataclasses.replace(controller.item(post),
                               content_html="<p><img src='https://x.example/a.png'></p>")
    shown = []

    def accept(dialog):
        shown.append(sorted(dialog._items))
        dialog._items["https://x.example/a.png"].setCheckState(Qt.CheckState.Unchecked)
        dialog.done(ImageReviewDialog.DialogCode.Accepted)

    monkeypatch.setattr(controller, "item", lambda _post: item)
    monkeypatch.setattr(ImageReviewDialog, "open", accept)
    window._review_images()
    assert shown == [sorted(snapshots.images_to_copy([item]))]
    assert window._skip_images == {"https://x.example/a.png"}
