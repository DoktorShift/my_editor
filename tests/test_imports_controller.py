# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The imports controller: one owner for the import state of an account.

Nothing is bound, checked or opened without an account in use; leaving
an account stops its checks, pauses its import and closes its window,
and drops answers that arrive for it afterwards; a second process on
the same account only shows the inbox. The window and the panel only
read from it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta
from nostr.imports.feed_list import source_key
from nostr.imports.inbox_store import INBOX, OLDER_POSTS, View, database_path
from nostr.imports_controller import ACCOUNT_CHANGED, QUIT_PAUSED, ImportsController
from tests.imports_fakes import (
    FakeCatalogue,
    FakeFetcher,
    TWO_ITEM_FEED,
    inline_run_blocking,
    make_item,
)
from tests.outbox_fakes import FakeRelayDirectory, settle
from tests.test_imports_subscriptions import (
    FakeRelay,
    FakeScheduler,
    FakeSessionPool,
)
from nostr.imports.subscriptions import FeedSubscriptionStore

PK = "ab" * 32
OTHER = "cd" * 32
FEED = "https://blog.example/feed"
NOSTR_AUTHOR = "npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m"


def profile(pubkey=PK, name="Ada"):
    return SimpleNamespace(user_pubkey=pubkey, bunker_relays=[], display_name=name)


class FakeChecker(QObject):
    checking = Signal(str, bool)
    source_checked = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.started = self.stopped = 0
        self.checked = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def check_now(self, key):
        self.checked.append(key)
        return True

    def is_checking(self, key):
        return False


class FakeWindow(QObject):
    def __init__(self, controller):
        super().__init__()
        self.shown = 0
        self.closed = False

    def show(self):
        self.shown += 1

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def close(self):
        self.closed = True


class Harness:
    def __init__(self, tmp_path, *, fetcher=None, draft_store=None):
        self.checkers = []
        self.windows = []
        self.relay = FakeRelay()
        store = FeedSubscriptionStore(
            session_pool=FakeSessionPool(), relay_pool=None,
            relay_directory=FakeRelayDirectory(), cache_dir=tmp_path / "cache",
            query=self.relay, publisher=self.relay, scheduler=FakeScheduler(),
            clock=lambda: 1_800_000_000)
        self.draft_store = draft_store
        self.controller = ImportsController(
            relay_pool=None, relay_directory=FakeRelayDirectory(), session_pool=None,
            draft_store=draft_store, config_dir=tmp_path, subscription_store=store,
            fetcher=fetcher or FakeFetcher({}), run_blocking=inline_run_blocking,
            checker_factory=self._checker,
            catalogue_factory=lambda inbox: FakeCatalogue(),
            window_factory=self._window)

    def _checker(self, inbox):
        checker = FakeChecker()
        self.checkers.append(checker)
        return checker

    def _window(self, controller):
        window = FakeWindow(controller)
        self.windows.append(window)
        return window


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.controller.account_changed(None)
    settle()


def test_nothing_is_bound_without_an_account(harness, tmp_path):
    assert not harness.controller.bound
    harness.controller.open_window()
    assert harness.windows == []
    assert harness.checkers == []
    assert not (tmp_path / "imports").exists()
    assert harness.controller.counts().inbox == 0
    assert harness.controller.page(View(INBOX)) == []


def test_an_account_opens_its_inbox_and_starts_checking(harness, tmp_path):
    bound = []
    harness.controller.bound_changed.connect(bound.append)
    harness.controller.account_changed(profile())
    settle()
    assert harness.controller.bound
    assert database_path(tmp_path, PK).exists()
    assert harness.checkers[0].started == 1
    assert bound == [True]


def test_sources_follow_the_shared_list(harness):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed(FEED, "Blog")
    harness.controller.subscriptions.add_feed(NOSTR_AUTHOR, "An author")
    sources = {s.key: s for s in harness.controller.sources()}
    assert sources[source_key(FEED)].automatic
    assert not sources[source_key(NOSTR_AUTHOR)].automatic
    harness.controller.subscriptions.remove_feed(FEED)
    assert [s.key for s in harness.controller.sources()] == [source_key(NOSTR_AUTHOR)]


def test_switching_accounts_closes_and_drops_everything_of_the_first(harness, tmp_path):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.open_window()
    first_window, first_checker = harness.windows[0], harness.checkers[0]
    runner = harness.controller.runner
    halted = []
    runner.busy = lambda: True
    runner.halt = halted.append
    harness.controller.account_changed(profile(OTHER))
    settle()
    assert first_window.closed
    assert first_checker.stopped == 1
    assert halted == [ACCOUNT_CHANGED]
    assert database_path(tmp_path, OTHER).exists()
    assert harness.controller.window() is None


def test_the_same_account_again_changes_nothing(harness):
    harness.controller.account_changed(profile())
    settle()
    inbox = harness.controller.inbox
    harness.controller.account_changed(profile(name="Ada Lovelace"))
    assert harness.controller.inbox is inbox
    assert harness.controller.profile.display_name == "Ada Lovelace"


def test_no_account_unbinds(harness):
    harness.controller.account_changed(profile())
    settle()
    counts = []
    harness.controller.inbox_count_changed.connect(counts.append)
    harness.controller.account_changed(None)
    assert not harness.controller.bound
    assert harness.checkers[0].stopped == 1
    assert counts[-1] == 0


def test_quitting_pauses_the_import_and_stops_checking(harness):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.open_window()
    halted = []
    harness.controller.runner.busy = lambda: True
    harness.controller.runner.halt = halted.append
    harness.controller.flush()
    assert halted == [QUIT_PAUSED]
    assert harness.checkers[0].stopped == 1
    assert harness.windows[0].closed


def test_a_second_process_only_shows_the_inbox(tmp_path):
    first = Harness(tmp_path)
    second = Harness(tmp_path)
    first.controller.account_changed(profile())
    second.controller.account_changed(profile())
    settle()
    assert not first.controller.read_only
    assert second.controller.read_only
    assert second.checkers[0].started == 0
    assert not second.controller.check_now(source_key(FEED))
    # The first keeps the inbox in step; the second only reads it.
    first.controller.subscriptions.add_feed(FEED, "Blog")
    assert [s.key for s in second.controller.sources()] == [source_key(FEED)]
    second.controller.subscriptions.add_feed(NOSTR_AUTHOR, "An author")
    assert [s.key for s in second.controller.sources()] == [source_key(FEED)]
    first.controller.account_changed(None)
    second.controller.account_changed(None)
    settle()


def test_drafts_make_posts_imported(tmp_path):
    drafts = DraftStore()
    harness = Harness(tmp_path, draft_store=drafts)
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed(FEED, "Blog")
    inbox = harness.controller.inbox
    item = make_item("Post", guid="g-1")
    inbox.ingest(source_key(FEED), [item])
    d_tag = harness.controller.page(View(OLDER_POSTS))[0].d_tag
    drafts.bind_profile(PK)
    drafts.upsert_skeleton(DraftWrapMeta(d_tag, 30023, "e1", PK, 100, None, "ct"))
    settle()
    assert harness.controller.counts().imported == 1
    assert harness.controller.page(View(OLDER_POSTS)) == []
    harness.controller.account_changed(None)
    settle()


def test_pages_continue_after_the_last_post(harness):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed(FEED, "Blog")
    harness.controller.inbox.ingest(
        source_key(FEED), [make_item(f"P{i}", guid=f"g{i}", published_at=i)
                           for i in range(60)])
    first = harness.controller.page(View(OLDER_POSTS))
    second = harness.controller.page(View(OLDER_POSTS), after=first[-1])
    assert len(first) == 50 and len(second) == 10
    assert not {p.key for p in first} & {p.key for p in second}


def test_the_article_is_the_one_an_import_makes(harness):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed(FEED, "Blog")
    harness.controller.inbox.ingest(source_key(FEED), [make_item(
        "Hello", guid="g", link="https://blog.example/hello", published_at=123,
        content_html="<p>Hi <b>there</b></p><script>alert(1)</script>",
        categories=("Bitcoin",), image="https://blog.example/c.png")])
    post = harness.controller.page(View(OLDER_POSTS))[0]
    out = {}
    harness.controller.prepare_article(post, on_ready=lambda md, art: out.update(md=md, art=art),
                                       on_failed=lambda reason: out.update(failed=reason))
    assert "alert" not in out["md"]
    assert "Originally published at" in out["md"]
    article = out["art"]
    assert (article.title, article.author, article.published_at) == ("Hello", "Ada", 123)
    assert article.image == "https://blog.example/c.png"
    assert tuple(article.tags) == ("bitcoin",)


def test_a_source_read_when_opened(tmp_path):
    fetcher = FakeFetcher({"https://example.com/feed": ("ok", TWO_ITEM_FEED)})
    harness = Harness(tmp_path, fetcher=fetcher)
    harness.controller.account_changed(profile())
    settle()
    collection = harness.controller.read_source("https://example.com/feed")
    assert not collection.loading and not collection.error
    assert [p.title for p in collection.posts()] == ["Second", "First"]
    failing = harness.controller.read_source("https://missing.example/feed")
    assert failing.error
    harness.controller.account_changed(None)
    settle()


def test_reading_a_source_notes_when_without_asking_the_signer(tmp_path):
    fetcher = FakeFetcher({"https://example.com/feed": ("ok", TWO_ITEM_FEED)})
    harness = Harness(tmp_path, fetcher=fetcher)
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed("https://example.com/feed", "Example")
    harness.controller.subscriptions.flush()
    settle()
    sent = len(harness.relay.published)
    harness.controller.read_source("https://example.com/feed")
    assert harness.controller.subscriptions.get("https://example.com/feed").last_fetched_at
    settle()
    assert len(harness.relay.published) == sent
    harness.controller.account_changed(None)
    settle()


def test_an_answer_for_the_account_left_is_dropped(tmp_path):
    pending = []

    class SlowFetcher(FakeFetcher):
        def fetch(self, url, *, on_success, on_failure):
            pending.append(lambda: on_success(TWO_ITEM_FEED))

    harness = Harness(tmp_path, fetcher=SlowFetcher({}))
    harness.controller.account_changed(profile())
    settle()
    collection = harness.controller.read_source("https://example.com/feed")
    harness.controller.account_changed(profile(OTHER))
    settle()
    pending.pop()()
    assert collection.items == []
    assert harness.controller.collection(collection.id) is None
    harness.controller.account_changed(None)
    settle()


def test_check_now_checks_a_feed_and_reads_any_other_source(tmp_path):
    harness = Harness(tmp_path)
    harness.controller.account_changed(profile())
    settle()
    harness.controller.subscriptions.add_feed(FEED, "Blog")
    harness.controller.subscriptions.add_feed(NOSTR_AUTHOR, "An author")
    assert harness.controller.check_now(source_key(FEED))
    assert harness.checkers[0].checked == [source_key(FEED)]
    assert harness.controller.check_now(source_key(NOSTR_AUTHOR))
    assert harness.checkers[0].checked == [source_key(FEED)]
    assert harness.controller.collection("m-" + source_key(NOSTR_AUTHOR)) is not None
    harness.controller.account_changed(None)
    settle()


def test_the_window_is_made_once(harness):
    harness.controller.account_changed(profile())
    settle()
    harness.controller.open_window()
    harness.controller.open_window()
    assert len(harness.windows) == 1
    assert harness.windows[0].shown == 2


class SilentRelay(FakeRelay):
    """Relays that do not answer yet: questions wait until ``answer``."""

    def __init__(self):
        super().__init__()
        self.waiting = []

    def latest(self, relays, filters, on_done):
        self.queries.append((list(relays), filters))
        self.waiting.append((filters, on_done))

    def answer(self):
        while self.waiting:
            filters, on_done = self.waiting.pop(0)
            on_done(self.stored.get(filters[0]["#d"][0]))


def test_a_lost_copy_of_the_list_never_empties_the_inbox(tmp_path):
    """Review M7: binding with the list's copy on this computer gone
    removed every source with its posts and skips before any relay was
    asked. Until the list is known, nothing is removed."""
    from nostr.imports.constants import FEED_LIST_DTAG
    from tests.outbox_fakes import PK as LIST_PK
    relay = SilentRelay()
    relay.put(FEED_LIST_DTAG, {"feeds": [{"url": FEED, "title": "Blog"}]})
    store = FeedSubscriptionStore(
        session_pool=FakeSessionPool(), relay_pool=None,
        relay_directory=FakeRelayDirectory(), cache_dir=tmp_path / "cache",
        query=relay, publisher=relay, scheduler=FakeScheduler(),
        clock=lambda: 1_800_000_000)
    controller = ImportsController(
        relay_pool=None, relay_directory=FakeRelayDirectory(), session_pool=None,
        config_dir=tmp_path, subscription_store=store, fetcher=FakeFetcher({}),
        run_blocking=inline_run_blocking, checker_factory=lambda inbox: FakeChecker(),
        catalogue_factory=lambda inbox: FakeCatalogue())
    account = profile(LIST_PK)
    controller.account_changed(account)
    settle()
    relay.answer()
    settle()
    assert [s.url for s in controller.sources()] == [FEED]
    inbox = controller.inbox
    inbox.ingest(source_key(FEED), [make_item("One", guid="g1"), make_item("Two", guid="g2")])
    post = controller.page(View(OLDER_POSTS))[0]
    inbox.skip(post.source_key, post.d_tag, post.revision)
    controller.account_changed(None)
    settle()
    for cached in (tmp_path / "cache").iterdir():
        cached.unlink()
    controller.account_changed(account)
    settle()
    assert relay.waiting            # the relays have not answered yet
    assert [s.url for s in controller.sources()] == [FEED]
    assert controller.counts().skipped == 1
    assert controller.counts().older == 1
    # The relays answer with the list: still there, nothing lost.
    relay.answer()
    settle()
    assert [s.url for s in controller.sources()] == [FEED]
    assert controller.counts().skipped == 1
    controller.account_changed(None)
