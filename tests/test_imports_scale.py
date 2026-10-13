# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Many posts stay quick: checking an archive's posts and moving through
the list must not cost per checked post (CP2 review N2: two database
queries a checked post on every update made one key press 312 ms at
2,000 checked posts, and Select All at 20,000 took 24 s).

The bounds are generous (a slow test machine passes them by far); what
they catch is the cost growing with the number of checked posts again.
"""

from __future__ import annotations

import time

import pytest

from nostr.imports.feed_list import source_key
from nostr.imports.inbox_store import OLDER_POSTS, View
from nostr.ui.imports_window import ImportsWindow
from tests.imports_fakes import make_item
from tests.outbox_fakes import settle
from tests.test_imports_window import NOW, controller_for
from tests.widget_lifetime import delete_new_windows

POSTS = 2_000
FEED = "https://archive.example/feed"


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


@pytest.fixture
def window(tmp_path):
    controller = controller_for(tmp_path)
    controller.subscriptions.add_feed(FEED, "An archive")
    for start in range(0, POSTS, 500):      # an answer keeps its newest 500
        controller.inbox.ingest(source_key(FEED), [
            make_item(f"Post {n}", guid=f"g{n}", published_at=NOW - 86400 - n)
            for n in range(start, start + 500)])       # all Older Posts
    controller._announce()
    win = ImportsWindow(controller, load_settings=lambda: {},
                        save_settings=lambda value: None)
    win.show()
    settle()
    win.show_view("list", OLDER_POSTS)
    model = win.posts.model_
    while model.canFetchMore():
        model.fetchMore()
    settle()
    yield win
    win.close()
    controller.account_changed(None)
    settle()


def slowest(action, times=5) -> float:
    worst = 0.0
    for _ in range(times):
        started = time.perf_counter()
        action()
        worst = max(worst, time.perf_counter() - started)
    return worst


def test_two_thousand_checked_posts_stay_quick(window):
    model = window.posts.model_
    assert model.rowCount() == POSTS
    started = time.perf_counter()
    window.select_all.click()           # check every post
    select_all = time.perf_counter() - started
    assert len(model.checked()) == POSTS
    assert str(POSTS) in window.action_bar.create.text().replace(",", "")
    update = slowest(window._update_selection_band)
    key = slowest(lambda: model.set_checked(5, not model.is_checked(model.post(5))))
    assert update < 0.1, f"an update took {update * 1000:.0f} ms"
    assert key < 0.1, f"a check took {key * 1000:.0f} ms"
    assert select_all < 1.0, f"Select All took {select_all * 1000:.0f} ms"


def test_a_finished_check_does_not_freeze_the_window(window):
    window.select_all.click()
    controller = window._controller
    started = time.perf_counter()
    controller._announce()              # what a finished check says
    settle()
    took = time.perf_counter() - started
    assert took < 1.0, f"a finished check took {took * 1000:.0f} ms"
    assert controller.page(View(OLDER_POSTS))


def test_following_a_long_list_is_one_change(tmp_path, monkeypatch):
    """CP2 review L-d: Follow All synced the whole list once per source
    (3.8 s for 400 on the UI thread)."""
    from nostr.imports.sources.opml import OpmlDocument, OpmlFeed
    controller = controller_for(tmp_path)
    synced = []
    real = controller.inbox.sync_sources

    def counted(*args, **kwargs):
        synced.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(controller.inbox, "sync_sources", counted)
    document = OpmlDocument(title="A list", feeds=tuple(
        OpmlFeed(f"https://site{n}.example/feed", f"Site {n}") for n in range(400)))
    started = time.perf_counter()
    assert controller.follow_all(document) == (400, 0, 0)
    took = time.perf_counter() - started
    assert len(synced) == 1
    assert len(controller.sources()) == 400
    assert took < 1.0, f"following 400 sources took {took * 1000:.0f} ms"
    controller.account_changed(None)


def test_progress_reads_no_stored_posts(tmp_path):
    """CP2 review L-d: every progress step read every row's stored post
    again (68 ms a step for a 2,000-post import)."""
    from nostr.imports.inbox_store import JobRow
    controller = controller_for(tmp_path)
    snapshot = {"guid": "g", "title": "A post", "content_html": "<p>" + "x" * 5000 + "</p>"}
    controller.inbox.create_job(label="A big import", source_type="inbox", rows=[
        JobRow(index=n, d_tag=f"rss-{n:016x}", title=f"Post {n}", snapshot=snapshot)
        for n in range(2000)])
    (light,) = controller.inbox.jobs(payloads=False)
    assert all(row.snapshot is None and row.signed_event is None for row in light.rows)
    started = time.perf_counter()
    busy, failed = controller.posts_in_progress()
    controller.activity()
    took = time.perf_counter() - started
    assert len(busy) == 2000 and not failed
    assert took < 0.25, f"a progress step took {took * 1000:.0f} ms"
    controller.account_changed(None)
